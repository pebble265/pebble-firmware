"""Local web server for the touch screen (http://127.0.0.1:8787).

GET /      the app (ui/index.html)
GET /ws    WebSocket: the server pushes the screen state as JSON whenever it changes,
           the page sends actions such as {"a":"talk"} {"a":"take","id":…} {"a":"mood","score":5}
Only listens on localhost: nothing on the network can reach it.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

from aiohttp import WSMsgType, web

log = logging.getLogger("ui")

UI_DIR = Path(__file__).resolve().parent / "ui"
ACTIONS = {"talk", "stop", "send", "take", "later", "mood", "refresh", "touch", "wave", "box", "desktop", "music", "wifiScan", "wifiConnect", "wifiClear", "btPair", "btDisconnect", "volume"}
HIDE_MINUTES = 10  # "ตั้งค่า Wi-Fi": leave the desktop free this long


class UiServer:
    def __init__(self, robot, port: int = 8787):
        self.robot = robot
        self.port = port
        self.runner: web.AppRunner | None = None
        self.browser: subprocess.Popen | None = None
        self.args: list[str] | None = None
        self.hidden_until = 0.0        # kiosk closed on purpose until this time
        self.hidden_offline = False    # it was closed to fix the internet: reopen once online
        self.clients: set[web.WebSocketResponse] = set()

    async def start(self):
        app = web.Application()
        app.router.add_get("/", self._index)
        app.router.add_get("/ws", self._ws)
        app.router.add_get("/show", self._show)  # desktop shortcut: bring the screen back
        app.router.add_static("/static/", UI_DIR, show_index=False)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        await web.TCPSite(self.runner, "127.0.0.1", self.port).start()
        log.info("touch screen at http://127.0.0.1:%s", self.port)

    async def _index(self, _req):
        return web.FileResponse(UI_DIR / "index.html", headers={"Cache-Control": "no-store"})

    async def _ws(self, req):
        ws = web.WebSocketResponse(heartbeat=20)
        await ws.prepare(req)
        self.clients.add(ws)
        sender = asyncio.create_task(self._push(ws))
        try:
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                try:
                    data = json.loads(msg.data)
                except ValueError:
                    continue
                if isinstance(data, dict) and data.get("a") in ACTIONS:
                    asyncio.create_task(self._act(data))
        finally:
            sender.cancel()
            self.clients.discard(ws)
        return ws

    async def _show(self, _req):
        self.show_kiosk()
        return web.Response(text="ok")

    async def _act(self, data: dict):
        if data.get("a") == "desktop":
            self.hide_kiosk()
            return
        try:
            await self.robot.on_ui(data)
        except Exception:
            log.exception("action %s failed", data.get("a"))

    async def _push(self, ws: web.WebSocketResponse):
        last = None
        while not ws.closed:
            try:
                state = json.dumps(self.robot.ui_state(), ensure_ascii=False)
            except Exception:
                log.exception("ui_state failed")
                state = None
            if state and state != last:
                try:
                    await ws.send_str(state)
                except (ConnectionResetError, RuntimeError):
                    return
                last = state
            await asyncio.sleep(0.15)

    # ------------------------------------------------------------------ kiosk browser
    async def open_kiosk(self):
        exe = next((shutil.which(n) for n in ("chromium-browser", "chromium", "google-chrome") if shutil.which(n)), None)
        if not exe:
            log.warning("Chromium not found: open http://127.0.0.1:%s yourself", self.port)
            return
        if not (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")):
            log.warning("no desktop session (WAYLAND_DISPLAY/DISPLAY): not opening the browser")
            return
        profile = Path(os.path.expanduser("~/.config/pebble-kiosk"))
        profile.mkdir(parents=True, exist_ok=True)
        self.profile = profile
        # a kiosk left over from the previous run (app restarted / killed) would take our launch
        # and the watcher would keep relaunching = the screen reloads every few seconds
        self._kill_stale_kiosks()
        args = [
            exe,
            f"--app=http://127.0.0.1:{self.port}/",
            "--kiosk",
            "--noerrdialogs",
            "--disable-infobars",
            "--disable-session-crashed-bubble",
            "--disable-features=Translate,TranslateUI",
            "--overscroll-history-navigation=0",
            "--disable-pinch",
            "--check-for-update-interval=31536000",
            "--password-store=basic",
            f"--user-data-dir={profile}",
        ]
        self.args = args
        self._launch()
        log.info("opened %s in kiosk mode", Path(exe).name)
        asyncio.create_task(self._watch_browser())

    def _kiosk_pids(self) -> list[int]:
        """Chromium processes using our kiosk profile (any of them = the screen is up)."""
        prof = getattr(self, "profile", None)
        if not prof:
            return []
        try:
            out = subprocess.run(["pgrep", "-f", f"chrom[a-z-]*( .*)? --.*user-data-dir={prof}"],
                                 capture_output=True, text=True, timeout=3).stdout
        except Exception:
            return []
        me = os.getpid()
        return [int(p) for p in out.split() if p.isdigit() and int(p) != me]

    @staticmethod
    def _signal(pids: list[int], sig: int):
        for pid in pids:
            try:
                os.kill(pid, sig)
            except OSError:
                pass

    def _kill_stale_kiosks(self):
        pids = self._kiosk_pids()
        if not pids:
            return
        log.info("closing %d leftover kiosk process(es) from the last run", len(pids))
        self._signal(pids, signal.SIGTERM)
        for _ in range(30):
            if not self._kiosk_pids():
                break
            time.sleep(0.1)
        else:
            self._signal(self._kiosk_pids(), signal.SIGKILL)
            time.sleep(0.3)
        for lock in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
            try:
                (self.profile / lock).unlink()
            except OSError:
                pass

    def _launch(self):
        if self.args and (not self.browser or self.browser.poll() is not None):
            self.browser = subprocess.Popen(self.args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    async def _watch_browser(self):
        """Reopen the screen if the browser is closed or crashes (not while hidden on purpose)."""
        while True:
            await asyncio.sleep(3)
            now = time.monotonic()
            if self.hidden_until:
                back_online = self.hidden_offline and self.robot.online
                if now < self.hidden_until and not back_online:
                    continue
                log.info("showing the screen again (%s)", "back online" if back_online else "timeout")
                self.hidden_until = 0
                self._launch()
                continue
            if self.browser and self.browser.poll() is not None:
                if self._kiosk_pids():
                    continue  # our launcher handed over to a Chromium that is still showing the app
                log.warning("browser exited (%s): reopening", self.browser.returncode)
                await asyncio.sleep(2)
                if not self._kiosk_pids():
                    self._launch()

    def hide_kiosk(self, minutes: float = HIDE_MINUTES):
        """Close the full-screen app so the desktop (Wi-Fi menu, keyboard) can be used."""
        log.info("screen hidden for %s min (desktop / Wi-Fi settings)", minutes)
        self.hidden_until = time.monotonic() + minutes * 60
        self.hidden_offline = not self.robot.online
        self.close_kiosk()

    def show_kiosk(self):
        self.hidden_until = 0
        self._launch()

    def close_kiosk(self):
        if self.browser and self.browser.poll() is None:
            self.browser.terminate()
        if getattr(self, "profile", None):
            self._signal(self._kiosk_pids(), signal.SIGTERM)
