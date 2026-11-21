"""Serial link to the ESP32-S3 "MedBot" board — over the RX/TX wires (GPIO14/15, /dev/serial0)
or the USB cable (wheels, pill box, pusher, arm,
16x16 LED face, buzzer, fan, distance sensor, touch sensors).

Protocol: one JSON object per line, 115200 baud — see ../esp32/PROTOCOL.md
Lines that don't start with "{" are the board's own log / test menu and are ignored.

Pi -> ESP32   {"cmd":"move","dir":"forward"|"backward"|"left"|"right"|"stop","speed":0-100}
              {"cmd":"dispense","slot":1,"id":"<doseId>"}   push a pill into the box, slide the box out
              {"cmd":"box","do":"open"|"close"|"toggle"}
              {"cmd":"arm","do":"wave"|"home"}
              {"cmd":"face","mood":"happy"}                 LED panel face for a mood
              {"cmd":"emoji","score":1-7,"ms":6000}         LED panel shows the check-in emoji
              {"cmd":"buzz","mode":"beep"|"double"|"alarm"|"off"}
              {"cmd":"hello"} {"cmd":"status"} {"cmd":"ping"}
ESP32 -> Pi   {"evt":"hello","fw":"medbot-3.0.0","caps":[...]}
              {"evt":"status","battery":70,"box":"closed","dist":420,...}
              {"evt":"box","state":"opening|open|closing|closed","by":"touch|pi|menu|alarm"}
              {"evt":"dispensed","slot":1,"id":"<doseId>","ok":true}
              {"evt":"button","press":"short"}  {"evt":"obstacle","mm":150}
              {"evt":"pong"}  {"evt":"error","msg":"..."}
"""
from __future__ import annotations

import asyncio
import glob
import json
import logging
import threading
import time
from typing import Callable

log = logging.getLogger("esp32")

EventCb = Callable[[dict], None]


class Esp32:
    def __init__(self, port: str, baud: int, on_event: EventCb):
        self.port_setting = port
        self.baud = baud
        self.on_event = on_event
        self.ser = None
        self.sim = port == "sim"
        self.connected = False
        self.fw: str | None = None
        self.caps: set[str] = set()
        self.battery = 100
        self.battery_known = False
        self.charging = False
        self.box = "closed"
        self.dist: int | None = None
        self.fan: bool | None = None
        self.last_seen = 0.0
        self.port: str | None = None          # port in use, e.g. /dev/serial0 (RX/TX) or /dev/ttyACM0 (USB)
        self._next = 0                        # auto mode: which candidate to try next
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._pending: dict[str, asyncio.Future] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    # ------------------------------------------------------------------ setup
    @property
    def link(self) -> str:
        """"uart" (RX/TX wires), "usb", or "" when not connected."""
        if not self.connected or not self.port:
            return ""
        return "usb" if ("ACM" in self.port or "USB" in self.port) else "uart"

    def _candidates(self) -> list[str]:
        if self.port_setting not in ("auto", ""):
            return [self.port_setting]
        # RX/TX wires first (the robot's normal link), then a USB cable
        uart = [p for p in ("/dev/serial0", "/dev/ttyAMA0") if glob.glob(p)][:1]
        return uart + sorted(glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*"))

    def _find_port(self) -> str | None:
        ports = self._candidates()
        if not ports:
            return None
        port = ports[self._next % len(ports)]
        self._next += 1
        return port

    def start(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop
        if self.sim:
            log.info("simulated ESP32 (no hardware)")
            self.connected = True
            self.fw = "sim"
            self.caps = {"wheels", "box", "pusher", "arm", "face", "buzzer"}
            return
        threading.Thread(target=self._reader, name="esp32", daemon=True).start()
        threading.Thread(target=self._keepalive, name="esp32-ping", daemon=True).start()

    def stop(self):
        self.send({"cmd": "move", "dir": "stop"})
        self._stop.set()

    def has(self, cap: str) -> bool:
        return cap in self.caps

    def _keepalive(self):
        """The ESP32 stops the wheels if it hears nothing from the Pi for 6 s."""
        while not self._stop.wait(2):
            if self.ser and self.connected:
                self.send({"cmd": "ping"}, quiet=True)

    def _reader(self):
        import serial  # pyserial

        while not self._stop.is_set():
            port = self._find_port()
            if not port:
                time.sleep(3)
                continue
            try:
                with serial.serial_for_url(port, baudrate=self.baud, timeout=1) as ser:
                    self.ser = ser
                    self.port = port
                    opened = time.monotonic()
                    heard = False
                    log.info("opened %s, waiting for the ESP32…", port)
                    time.sleep(0.5)
                    ser.reset_input_buffer()
                    self.send({"cmd": "hello"}, quiet=True)
                    buf = b""
                    while not self._stop.is_set():
                        chunk = ser.readline()
                        now = time.monotonic()
                        if not heard and now - opened > 6:
                            # nothing answered here: try the next port (auto) or reopen
                            log.debug("no ESP32 on %s", port)
                            break
                        if heard and now - self.last_seen > 15 and self.connected:
                            log.warning("no answer from the ESP32 on %s for 15 s", port)
                            self.connected = False
                        if heard and now - self.last_seen > 30:
                            break  # gone for good: look on the other ports too
                        if not chunk:
                            if not self.connected and int(now - opened) % 2 == 0:
                                self.send({"cmd": "hello"}, quiet=True)
                            continue
                        buf += chunk
                        if not buf.endswith(b"\n"):
                            continue
                        line, buf = buf.decode("utf-8", "replace").strip(), b""
                        if line.startswith("{"):
                            self.last_seen = time.monotonic()
                            if not self.connected:
                                log.info("ESP32 connected on %s (%s)", port, "USB" if "ACM" in port or "USB" in port else "RX/TX")
                                cands = self._candidates()  # if the link drops, try this port again first
                                self._next = cands.index(port) if port in cands else 0
                            heard = True
                            self.connected = True
                            self._handle(line)
                        elif line:
                            log.debug("esp32: %s", line)
            except Exception as e:
                log.warning("serial %s: %s", port, e)
            self.ser = None
            self.connected = False
            time.sleep(1 if len(self._candidates()) > 1 else 3)

    def _handle(self, line: str):
        try:
            evt = json.loads(line)
        except json.JSONDecodeError:
            return
        kind = evt.get("evt")
        if kind == "hello":
            self.fw = str(evt.get("fw", ""))
            self.caps = set(evt.get("caps") or [])
        elif kind == "status":
            if "battery" in evt:
                self.battery = int(evt["battery"])
                self.battery_known = True
            self.charging = bool(evt.get("charging", False))
            self.box = str(evt.get("box", self.box))
            self.dist = evt.get("dist")
            self.fan = evt.get("fan")
        elif kind == "box":
            self.box = str(evt.get("state", self.box))
        elif kind == "dispensed" and evt.get("id") in self._pending:
            fut = self._pending.pop(evt["id"])
            ok = bool(evt.get("ok"))
            if self._loop:
                self._loop.call_soon_threadsafe(lambda: fut.done() or fut.set_result(ok))
        elif kind == "pong":
            return
        if self._loop:
            self._loop.call_soon_threadsafe(self.on_event, evt)

    # ------------------------------------------------------------------ commands
    def send(self, msg: dict, quiet: bool = False) -> bool:
        line = json.dumps(msg, separators=(",", ":"), ensure_ascii=False) + "\n"
        if self.sim or not self.ser:
            if not quiet:
                log.info("-> esp32%s %s", " (sim)" if self.sim else " (not connected)", line.strip())
            return self.sim
        if not quiet:
            log.debug("-> %s", line.strip())
        try:
            with self._lock:
                self.ser.write(line.encode())
            return True
        except Exception as e:
            log.warning("write failed: %s", e)
            return False

    def move(self, direction: str, speed: int = 60):
        self.send({"cmd": "move", "dir": direction, "speed": speed})

    def face(self, mood: str):
        self.send({"cmd": "face", "mood": mood})

    def emoji(self, score: int, ms: int = 6000):
        """Show the mood check-in emoji (1 😭 … 7 🥰) on the LED panel for `ms`, then the normal face."""
        self.send({"cmd": "emoji", "score": int(score), "ms": int(ms)})

    def arm(self, do: str = "wave"):
        self.send({"cmd": "arm", "do": do})

    def box_cmd(self, do: str = "toggle"):
        self.send({"cmd": "box", "do": do})

    def buzz(self, mode: str = "beep"):
        self.send({"cmd": "buzz", "mode": mode})

    def head(self, pan: int, tilt: int):
        if self.has("head"):
            self.send({"cmd": "head", "pan": int(pan), "tilt": int(tilt)})

    async def dispense(self, slot: int, dose_id: str, timeout: float = 20) -> bool:
        """Pushes the pill into the box and slides the box out. True when the ESP32 confirms."""
        if self.sim:
            self.send({"cmd": "dispense", "slot": slot, "id": dose_id})
            await asyncio.sleep(1)
            self.box = "open"
            return True
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[dose_id] = fut
        if not self.send({"cmd": "dispense", "slot": slot, "id": dose_id}):
            self._pending.pop(dose_id, None)
            return False
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            self._pending.pop(dose_id, None)
            return False
