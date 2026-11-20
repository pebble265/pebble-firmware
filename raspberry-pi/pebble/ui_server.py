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
import subprocess
from pathlib import Path

from aiohttp import WSMsgType, web

log = logging.getLogger("ui")

UI_DIR = Path(__file__).resolve().parent / "ui"
ACTIONS = {"talk", "stop", "send", "take", "later", "mood", "refresh", "touch", "wave", "box"}


class UiServer:
    def __init__(self, robot, port: int = 8787):
        self.robot = robot
        self.port = port
        self.runner: web.AppRunner | None = None
        self.browser: subprocess.Popen | None = None
        self.clients: set[web.WebSocketResponse] = set()

    async def start(self):
        app = web.Application()
        app.router.add_get("/", self._index)
        app.router.add_get("/ws", self._ws)
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

    async def _act(self, data: dict):
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
        self.browser = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        log.info("opened %s in kiosk mode", Path(exe).name)
        asyncio.create_task(self._watch_browser(args))

    async def _watch_browser(self, args):
        """Reopen the screen if the browser is closed or crashes."""
        while True:
            await asyncio.sleep(5)
            if self.browser and self.browser.poll() is not None:
                log.warning("browser exited (%s): reopening", self.browser.returncode)
                await asyncio.sleep(2)
                self.browser = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def close_kiosk(self):
        if self.browser and self.browser.poll() is None:
            self.browser.terminate()
