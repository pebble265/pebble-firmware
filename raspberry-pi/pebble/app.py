"""pebble(a) robot brain: ties the backend, screen, voice, camera and ESP32 together.

Two screens are possible:
  web  (default) a touch app in the style of the Pebble phone app, shown full screen
       in Chromium; served by ui_server.py at http://127.0.0.1:8787
  face the old pygame robot face (python -m pebble --ui face)

Threads: the asyncio loop runs everything here; ESP32 serial, camera, MQTT and the
blocking audio calls run in helper threads.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import socket
import threading
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .api import Api, ApiError
from .ble import PairingBle
from .camera import Camera
from .config import VERSION, Config
from .esp32 import Esp32
from .realtime import Realtime
from .telemetry import Telemetry
from .voice import Voice

log = logging.getLogger("pebble")

HEARTBEAT_S = 60
TELEMETRY_S = 30
DOSE_POLL_S = 120
MOOD_POLL_S = 600
MOVE_TIMEOUT_S = 10      # stop the wheels if "stop" never arrives
CHAT_END_AFTER_S = 120   # summarise the conversation after 2 min of quiet
MAX_TURNS = 8            # follow-up turns per conversation
IDLE_SLEEP_S = 10 * 60   # sleepy after 10 min with nobody around

GOODBYE = re.compile(r"(บาย|ลาก่อน|แค่นี้|พอแล้ว|ไปก่อน|ไว้คุยกันใหม่|ราตรีสวัสดิ์|goodbye|bye)", re.I)
MOOD_LABELS = {1: "แย่มาก", 2: "เศร้า", 3: "ไม่ค่อยดี", 4: "เฉย ๆ", 5: "สงบ", 6: "ดี", 7: "ดีมาก"}
THAI_DAYS = ["จันทร์", "อังคาร", "พุธ", "พฤหัสบดี", "ศุกร์", "เสาร์", "อาทิตย์"]
THAI_MONTHS = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]

# Moods shown on the ESP32's 16x16 LED face (see MOOD_FACES in the .ino)
FACE_MOODS = {"idle", "happy", "listening", "thinking", "speaking", "concerned", "alert",
              "pairing", "offline", "sleepy", "boot"}


def local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return "-"


def cpu_temp() -> float | None:
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return round(int(f.read()) / 1000, 1)
    except OSError:
        return None


class DispensedLog:
    """Remembers which doses already dropped a pill, so a restart never drops it twice."""

    def __init__(self):
        base = os.environ.get("STATE_DIRECTORY") or os.path.expanduser("~/.local/state/pebble")
        os.makedirs(base, exist_ok=True)
        self.path = os.path.join(base, "dispensed.json")
        try:
            with open(self.path) as f:
                self.items: dict[str, float] = json.load(f)
        except (OSError, ValueError):
            self.items = {}

    def __contains__(self, dose_id: str) -> bool:
        return dose_id in self.items

    def add(self, dose_id: str):
        cutoff = time.time() - 3 * 86400  # keep 3 days
        self.items = {k: v for k, v in self.items.items() if v > cutoff}
        self.items[dose_id] = time.time()
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.items, f)
        os.replace(tmp, self.path)


def parse_time(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


class Robot:
    def __init__(self, cfg: Config, face=None, text_mode: bool = False):
        self.cfg = cfg
        self.face = face  # pygame face, or None when the web screen is used
        self.text_mode = text_mode
        self.api = Api(cfg.server_url, cfg.serial, cfg.secret)
        self.voice = Voice(cfg)
        self.telemetry = Telemetry(cfg.tb_host, cfg.tb_port, cfg.tb_token)
        self.esp = Esp32(cfg.esp_port, cfg.esp_baud, self._on_esp)
        self.camera = Camera(cfg.camera_enabled, self._on_arrive_thread)
        self.ble = PairingBle(cfg.serial, cfg.model, cfg.color, VERSION)
        self.realtime = Realtime(self.api, self._on_realtime)

        self.loop: asyncio.AbstractEventLoop | None = None
        self.started = time.monotonic()
        self.online = False
        self.profile: dict = {}
        # what the screen shows
        self.activity: str | None = None      # listening / thinking / speaking / happy / concerned
        self.activity_until = 0.0
        self.caption = ""                     # last thing said (subtitle)
        self.pair_code: str | None = None
        self.pair_expires: datetime | None = None
        self.messages: list[dict] = []        # current conversation, for the screen
        self.crisis_contacts: list[dict] | None = None
        self.doses: list[dict] = []           # today's doses (from the backend)
        self.mood_today: int | None = None
        self.alert_hidden_until = 0.0         # "later" on the medicine pop-up
        self.toast: tuple[str, float] | None = None
        # medicine
        self.pending: dict | None = None      # dose waiting to be taken
        self.dose_queue: list[dict] = []
        self.done_doses: set[str] = set()
        self.dispensed = DispensedLog()
        # conversation
        self.talk_lock = asyncio.Lock()
        self.in_conversation = False          # a back-and-forth with Pebbi (not just a reminder)
        self.talk_cancel = False
        self.conversation_id: str | None = None
        self.chat_end_task: asyncio.Task | None = None
        self.typed: asyncio.Queue[str] = asyncio.Queue()
        # misc
        self.last_interaction = time.monotonic()
        self.last_greet = 0.0
        self.move_task: asyncio.Task | None = None
        self.info_until = 0.0
        self._face_last: str | None = None
        self._stop = threading.Event()

    # ================================================================ start / stop
    def run_in_thread(self) -> threading.Thread:
        t = threading.Thread(target=lambda: asyncio.run(self.main()), name="core", daemon=True)
        t.start()
        return t

    def request_stop(self):
        self._stop.set()

    async def main(self, extra_tasks=()):
        self.loop = asyncio.get_running_loop()
        self.esp.start(self.loop)
        self.camera.start()
        self.telemetry.event("boot", VERSION)

        tasks = [
            asyncio.create_task(self._connect_and_heartbeat(), name="heartbeat"),
            asyncio.create_task(self._pairing(), name="pairing"),
            asyncio.create_task(self.realtime.run_forever(), name="realtime"),
            asyncio.create_task(self._dose_poll(), name="doses"),
            asyncio.create_task(self._mood_poll(), name="moods"),
            asyncio.create_task(self._telemetry_loop(), name="telemetry"),
            asyncio.create_task(self._ui_loop(), name="ui"),
            asyncio.create_task(self._head_follow(), name="head"),
            *[asyncio.create_task(c) for c in extra_tasks],
        ]
        if self.text_mode:
            threading.Thread(target=self._stdin_reader, daemon=True).start()
        try:
            while not self._stop.is_set():
                await asyncio.sleep(0.2)
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self.esp.stop()
            self.camera.stop()
            await self.ble.stop()
            if self.conversation_id:
                try:
                    await self.api.end_chat(self.conversation_id)
                except Exception:
                    pass
            await self.api.close()
            self.telemetry.event("shutdown")
            self.telemetry.stop()

    # ================================================================ backend link
    async def _connect_and_heartbeat(self):
        delay = 3
        while True:
            try:
                status = "charging" if self.esp.charging else "online"
                await self.api.heartbeat(self.esp.battery, status, VERSION)
                if not self.online:
                    log.info("backend online")
                self.online = True
                delay = 3
                await asyncio.sleep(HEARTBEAT_S)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                if self.online:
                    log.warning("backend unreachable: %s", e)
                self.online = False
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60)

    async def _refresh_profile(self):
        try:
            me = await self.api.me()
            self.profile = me if me.get("paired") else {}
        except Exception as e:
            log.warning("profile: %s", e)

    def settings(self) -> dict:
        return (self.profile or {}).get("settings") or {}

    def owner_name(self) -> str:
        return ((self.profile or {}).get("owner") or {}).get("displayName") or ""

    def speech_rate(self) -> float:
        try:
            return float(self.settings().get("speechRate") or 1.0)
        except (TypeError, ValueError):
            return 1.0

    def tz(self) -> ZoneInfo:
        try:
            return ZoneInfo(((self.profile or {}).get("owner") or {}).get("timezone") or "Asia/Bangkok")
        except Exception:
            return ZoneInfo("Asia/Bangkok")

    def now_local(self) -> datetime:
        return datetime.now(self.tz())

    def quiet_now(self) -> bool:
        s = self.settings()
        start, end = s.get("quietHoursStart"), s.get("quietHoursEnd")
        if not start or not end:
            return False
        now = self.now_local().strftime("%H:%M")
        return (start <= now < end) if start < end else (now >= start or now < end)

    def show_toast(self, text: str, seconds: float = 2.5):
        self.toast = (text, time.monotonic() + seconds)

    # ================================================================ pairing
    async def _pairing(self):
        """Shows a code + advertises over Bluetooth until someone pairs, then watches for unpairing."""
        was_paired = None
        code_expires = datetime.min.replace(tzinfo=timezone.utc)
        while True:
            try:
                await self.api.ensure_token()
                await self._refresh_profile()
                paired = self.api.paired
                if paired and was_paired is not True:
                    await self.ble.stop()
                    self.pair_code = None
                    self.telemetry.event("paired", self.owner_name())
                    asyncio.create_task(self._check_doses())
                    asyncio.create_task(self._load_mood())
                    asyncio.create_task(self.greet(first_time=was_paired is False))
                elif not paired:
                    if was_paired:
                        self.telemetry.event("unpaired")
                        self.doses, self.mood_today, self.pending = [], None, None
                        self.messages, self.crisis_contacts = [], None
                    if datetime.now(timezone.utc) >= code_expires:
                        try:
                            res = await self.api.pairing_code()
                            self.pair_code = res["code"]
                            self.pair_expires = parse_time(res["expiresAt"])
                            code_expires = self.pair_expires - timedelta(seconds=20)
                            self.ble.set_code(self.pair_code)
                            log.info("pairing code %s", self.pair_code)
                        except ApiError as e:
                            if e.status == 409:
                                await asyncio.sleep(2)
                                continue
                            raise
                    await self.ble.start()
                was_paired = paired
                await asyncio.sleep(5 if not paired else 60)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("pairing loop: %s", e)
                await asyncio.sleep(5)

    # ================================================================ events in
    def _on_esp(self, evt: dict):
        kind = evt.get("evt")
        if kind == "button":
            asyncio.ensure_future(self.on_button(evt.get("press", "short")))
        elif kind == "box":
            asyncio.ensure_future(self.on_box(str(evt.get("state", "")), str(evt.get("by", ""))))
        elif kind == "obstacle":
            self.show_toast("มีสิ่งกีดขวางข้างหน้า หุ่นหยุดแล้ว")
            self.telemetry.event("obstacle", f"{evt.get('mm')} mm")
        elif kind == "error":
            log.warning("esp32 error: %s", evt.get("msg"))
            self.telemetry.event("esp32_error", str(evt.get("msg", "")))
        elif kind == "hello":
            log.info("esp32 firmware %s caps=%s", evt.get("fw"), ",".join(evt.get("caps") or []))
            self._face_last = None  # re-send the face after the board restarts

    async def on_box(self, state: str, by: str):
        """Pill box moved. Closing it by touch after a pill was dispensed = the pill was taken."""
        self.last_interaction = time.monotonic()
        if by == "touch" and state == "closing" and self.pending and self.pending.get("dispensed"):
            log.info("pill box closed by touch: dose taken")
            await self.take_dose()
        elif by == "alarm" and state == "open":
            self.telemetry.event("box_open_too_long")
            if not self.pending:
                await self.say_short("กล่องยายังเปิดอยู่นะ หยิบยาแล้วแตะปิดกล่องด้วยนะ", "concerned")

    async def _on_realtime(self, event: str, data: dict):
        if event == "robot:command":
            await self.on_command(str(data.get("action", "")))
        elif event == "dose:due":
            log.info("dose due %s", data.get("doseId"))
            await self._check_doses(prefer=data.get("doseId"))

    def _on_arrive_thread(self, minutes_away: float):
        if self.loop:
            self.loop.call_soon_threadsafe(lambda: asyncio.ensure_future(self.on_arrive(minutes_away)))

    def _stdin_reader(self):
        print("โหมดทดสอบ: พิมพ์ข้อความแล้ว Enter = คุยกับ Pebbi | /b = กดปุ่ม | /l = กดค้าง | /cmd wake|forward|stop")
        while True:
            try:
                line = input().strip()
            except EOFError:
                return
            if not line or not self.loop:
                continue
            if line in ("/b", "/button"):
                self.loop.call_soon_threadsafe(lambda: asyncio.ensure_future(self.on_button("short")))
            elif line in ("/l", "/long"):
                self.loop.call_soon_threadsafe(lambda: asyncio.ensure_future(self.on_button("long")))
            elif line.startswith("/cmd "):
                act = line.split(maxsplit=1)[1]
                self.loop.call_soon_threadsafe(lambda: asyncio.ensure_future(self.on_command(act)))
            else:
                self.loop.call_soon_threadsafe(lambda: asyncio.ensure_future(self.send_text(line)))

    # ================================================================ touch screen actions
    async def on_ui(self, msg: dict):
        """An action from the touch screen (ui_server)."""
        self.last_interaction = time.monotonic()
        a = msg.get("a")
        if a == "talk":
            if not self.talk_lock.locked() and self.api.paired:
                asyncio.create_task(self.converse(typed=self.text_mode or not self.voice.available))
        elif a == "stop":
            self.stop_talking()
        elif a == "send":
            text = str(msg.get("text", "")).strip()[:2000]
            if text:
                await self.send_text(text)
        elif a == "take":
            if self.pending and self.pending["id"] == msg.get("id"):
                await self.take_dose()
            elif msg.get("id"):
                await self.take_other(str(msg["id"]))
        elif a == "later":
            self.alert_hidden_until = time.monotonic() + self.cfg.remind_every_minutes * 60
        elif a == "mood":
            await self.check_in(int(msg.get("score", 0)))
        elif a == "refresh":
            asyncio.create_task(self._check_doses())
        elif a == "wave":
            self.esp.arm("wave")
            self.set_activity("happy", 2.5)
        elif a == "box":
            self.esp.box_cmd("toggle")
        elif a == "touch":
            pass  # just wakes the screen (last_interaction above)

    async def send_text(self, text: str):
        """A typed message: start a typed conversation, or add to the running one."""
        if self.talk_lock.locked():
            self.typed.put_nowait(text)
        elif self.api.paired:
            asyncio.create_task(self.converse(first_text=text, typed=True))

    def stop_talking(self):
        self.talk_cancel = True
        self.voice.stop_speaking()
        self.voice.cancel_listening()

    async def check_in(self, score: int):
        if not 1 <= score <= 7:
            return
        prev = self.mood_today
        self.mood_today = score
        try:
            await self.api.mood(score)
            self.show_toast(f"บันทึกแล้ว: {MOOD_LABELS[score]}")
            self.telemetry.event("mood_checkin")
        except Exception as e:
            log.warning("mood: %s", e)
            self.mood_today = prev
            self.show_toast("บันทึกไม่สำเร็จ ลองใหม่อีกทีนะ")
            return
        if score <= 2 and not self.talk_lock.locked():
            asyncio.create_task(self.say_short("ขอบคุณที่บอกนะ ถ้าอยากเล่าให้ฟัง แตะคุยกับ Pebbi ได้เลย", "concerned"))
        elif score >= 6 and not self.talk_lock.locked():
            asyncio.create_task(self.say_short("ดีใจด้วยนะ", "happy"))

    async def _load_mood(self):
        try:
            res = await self.api.mood_today()
            self.mood_today = (res.get("today") or {}).get("score")
        except Exception as e:
            log.debug("mood today: %s", e)

    async def _mood_poll(self):
        while True:
            await asyncio.sleep(MOOD_POLL_S)
            if self.api.paired:
                await self._load_mood()

    # ================================================================ button / app commands
    async def on_button(self, press: str):
        self.last_interaction = time.monotonic()
        log.info("button %s", press)
        if press == "long":
            self.info_until = time.monotonic() + 10
            return
        if self.voice.speaking:
            self.voice.stop_speaking()
            return
        if self.pending and press != "double":
            await self.take_dose()
            return
        if not self.api.paired:
            await self.say("เปิดแอป Pebble บนมือถือ แล้วกดเพิ่มหุ่น เพื่อจับคู่กันนะ")
            return
        if not self.talk_lock.locked():
            asyncio.create_task(self.converse())

    async def on_command(self, action: str):
        log.info("app command %s", action)
        self.telemetry.event("cmd", action)
        self.last_interaction = time.monotonic()
        if action == "wake":
            self.set_activity("happy", 3)
            self.esp.buzz("beep")
            self.esp.arm("wave")
            if not self.talk_lock.locked():
                asyncio.create_task(self.converse(opening="มีอะไรให้ช่วยไหม"))
        elif action in ("forward", "backward", "left", "right"):
            self.esp.move(action, 60 if action in ("forward", "backward") else 45)
            if self.move_task:
                self.move_task.cancel()
            self.move_task = asyncio.create_task(self._move_timeout())
        elif action == "stop":
            self.esp.move("stop")
            if self.move_task:
                self.move_task.cancel()
        elif action == "wave":
            self.esp.arm("wave")
            self.set_activity("happy", 2.5)
        elif action == "box":
            self.esp.box_cmd("toggle")

    async def _move_timeout(self):
        await asyncio.sleep(MOVE_TIMEOUT_S)
        log.warning("no stop command for %ss: stopping", MOVE_TIMEOUT_S)
        self.esp.move("stop")

    async def on_arrive(self, minutes_away: float):
        self.last_interaction = time.monotonic()
        if (not self.api.paired or self.quiet_now() or self.talk_lock.locked() or self.pending
                or minutes_away < self.cfg.greet_after_minutes or time.monotonic() - self.last_greet < 3600):
            return
        self.telemetry.event("person_arrived")
        await self.greet()

    # ================================================================ speech
    def set_activity(self, mood: str | None, seconds: float = 0):
        self.activity = mood
        self.activity_until = time.monotonic() + seconds if seconds else 0

    async def say(self, text: str, mood: str = "speaking"):
        self.set_activity(mood)
        self.caption = text
        await asyncio.to_thread(self.voice.speak, text, self.speech_rate())
        self.set_activity(None)

    async def say_short(self, text: str, mood: str = "speaking"):
        if self.talk_lock.locked():
            return
        async with self.talk_lock:
            await self.say(text, mood)
            self.caption = ""

    def greeting_part(self) -> str:
        hour = self.now_local().hour
        return ("อรุณสวัสดิ์" if 5 <= hour < 11 else "สวัสดีตอนบ่าย" if hour < 17
                else "สวัสดีตอนเย็น" if hour < 22 else "ราตรีสวัสดิ์")

    async def greet(self, first_time: bool = False):
        if self.talk_lock.locked():
            return
        async with self.talk_lock:
            self.last_greet = time.monotonic()
            name = self.owner_name()
            if first_time:
                text = self.settings().get("greeting") or f"สวัสดี {name} ยินดีที่ได้รู้จักนะ"
            else:
                part = self.greeting_part()
                text = random.choice([f"{part} {name}", f"{part}นะ {name} วันนี้เป็นยังไงบ้าง"]).strip()
            self.set_activity("happy", 2)
            self.esp.arm("wave")
            await asyncio.sleep(0.6)
            await self.say(text)
            self.caption = ""

    async def _next_utterance(self, wait: float, typed: bool) -> str | None:
        """What the user said next: from the microphone, or typed."""
        if typed or self.text_mode or not self.voice.available:
            self.set_activity(None)
            deadline = time.monotonic() + max(wait, 45)
            while time.monotonic() < deadline and not self.talk_cancel:
                try:
                    return await asyncio.wait_for(self.typed.get(), 0.5)
                except asyncio.TimeoutError:
                    continue
            return None
        self.set_activity("listening")
        await asyncio.to_thread(self.voice.play_tone, 880, 90)
        wav = await asyncio.to_thread(self.voice.listen, wait)
        if not wav or self.talk_cancel:
            return None
        self.set_activity("thinking")
        return await asyncio.to_thread(self.voice.transcribe, wav)

    async def converse(self, opening: str | None = None, first_text: str | None = None, typed: bool = False):
        """One conversation with Pebbi: up to MAX_TURNS back-and-forth turns."""
        if self.talk_lock.locked():
            return
        async with self.talk_lock:
            if self.chat_end_task:
                self.chat_end_task.cancel()
            self.talk_cancel = False
            self.in_conversation = True
            self.voice.reset_cancel()
            misses = 0
            try:
                if opening:
                    self.messages.append({"role": "pebbi", "text": opening})
                    await self.say(opening)
                for turn in range(MAX_TURNS):
                    if self.talk_cancel:
                        break
                    if first_text:
                        text, first_text = first_text, None
                    else:
                        text = await self._next_utterance(8 if turn == 0 else 6, typed)
                    if text is None or self.talk_cancel:
                        break
                    if not text:
                        misses += 1
                        if misses > 2:
                            break
                        await self.say("ขอโทษนะ ไม่ค่อยได้ยิน พูดอีกทีได้ไหม")
                        continue
                    self.last_interaction = time.monotonic()
                    self.messages.append({"role": "user", "text": text})
                    self.set_activity("thinking")
                    try:
                        res = await self.api.chat(text, self.conversation_id)
                    except Exception as e:
                        log.warning("chat failed: %s", e)
                        msg = "ตอนนี้ต่อเน็ตไม่ได้ เดี๋ยวลองใหม่อีกทีนะ"
                        self.messages.append({"role": "pebbi", "text": msg})
                        await self.say(msg, mood="concerned")
                        break
                    self.conversation_id = res.get("conversationId") or self.conversation_id
                    crisis = res.get("crisis")
                    if crisis:
                        self.telemetry.event("crisis_support")
                        self.crisis_contacts = (crisis.get("contacts") or [
                            {"label": "สายด่วนสุขภาพจิต", "phone": "1323"},
                            {"label": "เจ็บป่วยฉุกเฉิน", "phone": "1669"}])[:5]
                    reply = res.get("reply") or ""
                    self.messages.append({"role": "pebbi", "text": reply})
                    self.messages = self.messages[-40:]
                    await self.say(reply, mood="concerned" if crisis else "speaking")
                    if GOODBYE.search(text):
                        break
            finally:
                self.in_conversation = False
                self.set_activity(None)
                self.caption = ""
                self.talk_cancel = False
                while not self.typed.empty():
                    self.typed.get_nowait()
                if self.conversation_id:
                    self.chat_end_task = asyncio.create_task(self._end_chat_later())

    async def _end_chat_later(self):
        await asyncio.sleep(CHAT_END_AFTER_S)
        cid, self.conversation_id = self.conversation_id, None
        self.messages, self.crisis_contacts = [], None
        if cid:
            try:
                await self.api.end_chat(cid)
                log.info("conversation ended")
            except Exception as e:
                log.debug("end chat: %s", e)

    # ================================================================ medicine
    async def _dose_poll(self):
        while True:
            await asyncio.sleep(10 if not self.api.paired else 0)
            if self.api.paired:
                await self._check_doses()
            await asyncio.sleep(DOSE_POLL_S)

    async def _check_doses(self, prefer: str | None = None):
        try:
            data = await self.api.doses_today()
        except Exception as e:
            log.debug("doses: %s", e)
            return
        self.doses = data.get("doses", [])
        now = datetime.now(timezone.utc)
        give_up = timedelta(minutes=self.cfg.give_up_after_minutes)
        due = []
        for d in self.doses:
            if d["status"] != "pending":
                if self.pending and self.pending["id"] == d["id"]:
                    log.info("dose %s confirmed elsewhere (%s)", d["id"], d["status"])
                    await self._finish_dose(spoken=False)
                continue
            at = parse_time(d["scheduledAt"])
            if d["id"] in self.done_doses:
                continue
            if at <= now + timedelta(minutes=1) and (now - at < give_up or d["id"] == prefer):
                due.append(d)
        due.sort(key=lambda d: (d["id"] != prefer, d["scheduledAt"]))
        for d in due:
            if not self.pending or self.pending["id"] != d["id"]:
                if d["id"] not in [q["id"] for q in self.dose_queue]:
                    self.dose_queue.append(d)
        if not self.pending and self.dose_queue:
            await self._start_dose(self.dose_queue.pop(0))

    async def _start_dose(self, dose: dict):
        med = dose.get("medication") or {}
        name = med.get("name") or "ยา"
        self.pending = {**dose, "started": time.monotonic(), "dispensed": False}
        self.alert_hidden_until = 0
        self.last_interaction = time.monotonic()
        self.telemetry.event("dose_due", dose["id"])

        slot = med.get("dispenserSlot")
        if slot and dose["id"] in self.dispensed:
            log.info("dose %s already dispensed before restart", dose["id"])
            self.pending["dispensed"] = True
        elif slot:
            ok = await self.esp.dispense(int(slot), dose["id"])
            if ok:
                self.dispensed.add(dose["id"])
                self.pending["dispensed"] = True
                try:
                    await self.api.dispensed(dose["id"])
                except Exception as e:
                    log.warning("mark dispensed: %s", e)
                self.telemetry.event("dispensed", f"slot {slot}")
            else:
                self.telemetry.event("dispense_failed", f"slot {slot}")
        asyncio.create_task(self._remind_loop(dose["id"], name))

    async def _remind_loop(self, dose_id: str, name: str):
        first = True
        while self.pending and self.pending["id"] == dose_id:
            if time.monotonic() - self.pending["started"] > self.cfg.give_up_after_minutes * 60:
                log.info("dose %s not confirmed, giving up", dose_id)
                self.telemetry.event("dose_unconfirmed", dose_id)
                self.done_doses.add(dose_id)
                await self._finish_dose(spoken=False)
                return
            if self.talk_lock.locked():  # busy talking: remind as soon as it's done
                await asyncio.sleep(2)
                continue
            self.alert_hidden_until = 0  # show the pop-up again
            self.esp.buzz("double")
            who = self.owner_name()
            if first:
                text = f"{who} ถึงเวลากินยา {name} แล้วนะ"
                if self.pending.get("dispensed"):
                    text += " ยาอยู่ในกล่องแล้ว หยิบยาแล้วแตะปิดกล่องได้เลย"
                else:
                    text += " กินเสร็จแล้วแตะ กินแล้ว บนจอนะ"
            else:
                text = f"อย่าลืมกินยา {name} นะ"
            async with self.talk_lock:
                await self.say(text.strip())
                self.caption = ""
            first = False
            await asyncio.sleep(self.cfg.remind_every_minutes * 60)

    async def take_dose(self):
        dose = self.pending
        if not dose:
            return
        if not await self._take(dose["id"]):
            return
        self.done_doses.add(dose["id"])
        await self._finish_dose(spoken=True)

    async def take_other(self, dose_id: str):
        """Tapped "กินแล้ว" on a dose in the list that isn't the one being reminded."""
        if await self._take(dose_id):
            self.done_doses.add(dose_id)
            self.dose_queue = [q for q in self.dose_queue if q["id"] != dose_id]
            self.show_toast("บันทึกแล้ว")
            await self._check_doses()

    async def _take(self, dose_id: str) -> bool:
        try:
            await self.api.take(dose_id, "robot_button")
        except ApiError as e:
            if e.status not in (400, 409):
                self.show_toast("บันทึกไม่สำเร็จ ลองอีกทีนะ")
                return False
        except Exception:
            self.show_toast("ต่อเน็ตไม่ได้ ลองอีกทีนะ")
            return False
        self.telemetry.event("dose_taken", dose_id)
        for d in self.doses:
            if d["id"] == dose_id:
                d["status"] = "taken"
        return True

    async def _finish_dose(self, spoken: bool):
        self.pending = None
        if spoken:
            self.set_activity("happy", 2.5)
            self.esp.arm("wave")
            asyncio.create_task(self.say_short(random.choice(["เก่งมาก กินยาเรียบร้อยแล้ว", "เยี่ยมเลย บันทึกให้แล้วนะ"]), "happy"))
        if self.dose_queue:
            await self._start_dose(self.dose_queue.pop(0))

    # ================================================================ screen state
    def current_mood(self) -> str:
        now = time.monotonic()
        if self.activity and (not self.activity_until or now < self.activity_until):
            return self.activity
        if self.activity_until and now >= self.activity_until:
            self.activity = None
            self.activity_until = 0
        if not self.api.token and not self.online:
            return "offline" if now - self.started > 20 else "boot"
        if not self.online:
            return "offline"
        if not self.api.paired:
            return "pairing"
        if self.pending:
            return "alert"
        if self.quiet_now():
            return "sleepy"
        if not self.camera.present and now - self.last_interaction > IDLE_SLEEP_S:
            return "sleepy"
        return "idle"

    def _local_hhmm(self, iso: str) -> str:
        return parse_time(iso).astimezone(self.tz()).strftime("%H:%M")

    def _dose_view(self, d: dict) -> dict:
        med = d.get("medication") or {}
        due = parse_time(d["scheduledAt"]) <= datetime.now(timezone.utc) + timedelta(minutes=1)
        return {
            "id": d["id"],
            "time": self._local_hhmm(d["scheduledAt"]),
            "name": med.get("name") or "ยา",
            "dosage": med.get("dosage") or "",
            "instructions": med.get("instructions") or "",
            "slot": med.get("dispenserSlot"),
            "status": d["status"],
            "due": due,
        }

    def ui_state(self) -> dict:
        """Everything the touch screen shows (sent to the browser as JSON)."""
        mood = self.current_mood()
        now = self.now_local()
        if not self.api.token and not self.online:
            mode = "offline" if time.monotonic() - self.started > 20 else "boot"
        elif not self.online:
            mode = "offline"
        elif not self.api.paired:
            mode = "pairing"
        else:
            mode = "ready"
        talking = self.in_conversation
        talk_state = "idle"
        if talking:
            talk_state = self.activity if self.activity in ("listening", "thinking", "speaking") else "waiting"
        toast = self.toast[0] if self.toast and time.monotonic() < self.toast[1] else None
        pending = None
        if self.pending and time.monotonic() >= self.alert_hidden_until:
            pending = {**self._dose_view(self.pending), "dispensed": bool(self.pending.get("dispensed"))}
        expires_in = None
        if self.pair_expires:
            expires_in = max(0, int((self.pair_expires - datetime.now(timezone.utc)).total_seconds()))
        return {
            "mode": mode,
            "mood": mood,
            "clock": now.strftime("%H:%M"),
            "date": f"{THAI_DAYS[now.weekday()]} {now.day} {THAI_MONTHS[now.month - 1]}",
            "hour": now.hour,
            "greeting": self.greeting_part(),
            "owner": self.owner_name(),
            "robotName": self.settings().get("robotName") or "Pebble",
            "pair": {"code": self.pair_code, "expiresIn": expires_in} if mode == "pairing" else None,
            "doses": [self._dose_view(d) for d in sorted(self.doses, key=lambda d: d["scheduledAt"])],
            "pending": pending,
            "moodToday": self.mood_today,
            "talk": {
                "active": talking,
                "state": talk_state,
                "typed": self.text_mode or not self.voice.available,
                "messages": self.messages[-20:],
                "caption": self.caption,
                "crisis": self.crisis_contacts,
            },
            "level": round(self.voice.level, 2),
            "sleep": mood == "sleepy" and not talking and not self.pending,
            "idleSeconds": int(time.monotonic() - self.last_interaction),
            "toast": toast,
            "status": {
                "online": self.online and self.realtime.connected,
                "battery": self.esp.battery if self.esp.connected and (self.esp.battery_known or self.esp.sim) else None,
                "charging": self.esp.charging,
                "esp32": self.esp.connected,
                "espFw": self.esp.fw,
                "box": self.esp.box,
                "dist": self.esp.dist,
                "hasBox": self.esp.has("box"),
                "hasArm": self.esp.has("arm"),
                "camera": self.camera.available,
                "person": self.camera.present,
                "voice": self.voice.available,
                "ip": local_ip(),
                "serial": self.cfg.serial,
                "version": VERSION,
                "server": self.cfg.server_url.split("//")[-1],
                "cpuTemp": cpu_temp(),
            },
        }

    async def _ui_loop(self):
        """Face on the ESP32's LED panel, plus the old pygame face if that screen is used."""
        while True:
            mood = self.current_mood()
            face = mood if mood in FACE_MOODS else "idle"
            if face != self._face_last and self.esp.connected:
                self.esp.face(face)
                self._face_last = face
            if self.face is not None:
                self._update_face(mood)
                while self.face.keys:
                    k = self.face.keys.pop(0)
                    if k == "space":
                        await self.on_button("short")
                    elif k == "l":
                        await self.on_button("long")
                    elif k == "d":
                        await self.on_button("double")
            await asyncio.sleep(0.05)

    def _update_face(self, mood: str):
        s = self.face.s
        s.mood = mood
        s.online = self.online and self.realtime.connected
        s.battery = self.esp.battery if self.esp.connected else None
        s.level = self.voice.level
        s.look_x, s.look_y = (self.camera.face_x, self.camera.face_y) if self.camera.present else (0.0, 0.0)
        s.caption = {"offline": "ต่ออินเทอร์เน็ตไม่ได้ กำลังลองใหม่…", "boot": "กำลังเปิดเครื่อง…",
                     "listening": "ฟังอยู่นะ…"}.get(mood, self.caption)
        if mood == "pairing":
            code = self.pair_code
            s.big_text = f"{code[:3]} {code[3:]}" if code else ""
            s.sub_text = "เปิดแอป Pebble แล้วเลือก “เพิ่มหุ่น” หรือพิมพ์รหัสนี้"
            if not code:
                s.caption = "กำลังขอรหัสจับคู่…"
        elif mood == "alert" and self.pending:
            v = self._dose_view(self.pending)
            s.big_text = v["name"] if len(v["name"]) <= 18 else v["name"][:17] + "…"
            detail = " · ".join(x for x in (v["dosage"], v["instructions"]) if x)
            s.sub_text = (detail + "  " if detail else "") + "กดปุ่มบนหัวเมื่อกินแล้ว"
        else:
            s.big_text = s.sub_text = ""
        s.lines = [f"{c.get('label', '')}  {c.get('phone', '')}" for c in (self.crisis_contacts or [])][:4]
        s.info = self._info_text() if time.monotonic() < self.info_until else ""

    def _info_text(self) -> str:
        return "\n".join([
            f"pebble(a) {VERSION} · {self.cfg.serial}",
            f"IP {local_ip()} · ESP32 {self.esp.fw or ('ไม่ได้ต่อ' if not self.esp.connected else '?')}",
            f"server {self.cfg.server_url.split('//')[-1]} · {'online' if self.online else 'offline'}",
        ])

    async def _head_follow(self):
        """Turn the head servo toward the person the camera sees (rate-limited)."""
        last = (90, 90)
        while True:
            await asyncio.sleep(0.4)
            if not self.esp.has("head"):
                continue
            if not self.camera.present:
                target = (90, 90)
            else:
                target = (int(90 + self.camera.face_x * 35), int(90 + self.camera.face_y * 15))
            if abs(target[0] - last[0]) >= 4 or abs(target[1] - last[1]) >= 4:
                self.esp.head(*target)
                last = target

    async def _telemetry_loop(self):
        while True:
            self.telemetry.send(
                battery=self.esp.battery,
                charging=self.esp.charging,
                paired=self.api.paired,
                online=self.online,
                esp32=self.esp.connected,
                person=self.camera.present,
                box=self.esp.box,
                dist=self.esp.dist,
                cpu_temp=cpu_temp(),
                uptime=int(time.monotonic() - self.started),
                mood=self.current_mood(),
            )
            await asyncio.sleep(TELEMETRY_S)


# ==================================================================== entry points
def run_face(cfg: Config, windowed: bool = False, text_mode: bool = False):
    """Old robot-face screen (pygame) on the main thread, brain in a thread."""
    from .face import Face

    if windowed:
        cfg.fullscreen = False
    face = Face(cfg.width, cfg.height, cfg.fullscreen, cfg.fps)
    robot = Robot(cfg, face, text_mode=text_mode)
    core = robot.run_in_thread()
    try:
        while core.is_alive() and face.tick():
            pass
    except KeyboardInterrupt:
        pass
    finally:
        robot.request_stop()
        core.join(timeout=8)
        face.close()
        os._exit(0)


def run_web(cfg: Config, text_mode: bool = False, browser: bool = True, port: int = 8787):
    """Touch app: local web server + Chromium kiosk window."""
    from .ui_server import UiServer

    robot = Robot(cfg, None, text_mode=text_mode)
    server = UiServer(robot, port)

    async def start():
        await server.start()
        if browser:
            await server.open_kiosk()

    try:
        asyncio.run(robot.main(extra_tasks=[start()]))
    except KeyboardInterrupt:
        pass
    finally:
        server.close_kiosk()
        os._exit(0)


def run(cfg: Config, windowed: bool = False, text_mode: bool = False):
    run_face(cfg, windowed, text_mode)
