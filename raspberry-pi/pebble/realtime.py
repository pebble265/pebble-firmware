"""Socket.IO client for ws://<server>/realtime (robot:command, dose:due, robot:paired/unpaired)."""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

import socketio

from .api import Api

log = logging.getLogger("realtime")

Handler = Callable[[str, dict], Awaitable[None]]


class Realtime:
    def __init__(self, api: Api, on_event: Handler):
        self.api = api
        self.on_event = on_event
        self.connected = False
        self.sio = socketio.AsyncClient(reconnection=False, logger=False, engineio_logger=False)
        ns = "/realtime"

        @self.sio.on("connect", namespace=ns)
        async def _connect():
            self.connected = True
            log.info("connected")

        @self.sio.on("disconnect", namespace=ns)
        async def _disconnect(*_):
            self.connected = False
            log.info("disconnected")

        @self.sio.on("ready", namespace=ns)
        async def _ready(data):
            log.info("ready %s", data)

        @self.sio.on("error", namespace=ns)
        async def _error(data):
            log.warning("server error %s", data)
            self.api.token = None  # probably an expired token: log in again

        for event in ("robot:command", "dose:due", "alert", "robot:paired", "robot:unpaired"):
            self.sio.on(event, self._forward(event), namespace=ns)

    def _forward(self, event: str):
        async def handler(data):
            try:
                await self.on_event(event, data or {})
            except Exception:
                log.exception("handler for %s failed", event)

        return handler

    async def run_forever(self):
        """Connects and reconnects (with a fresh token) until cancelled."""
        delay = 2
        while True:
            try:
                token = await self.api.ensure_token()
                await self.sio.connect(
                    self.api.base,
                    namespaces=["/realtime"],
                    auth={"token": token},
                    headers={"ngrok-skip-browser-warning": "1"},
                    transports=["websocket", "polling"],
                    wait_timeout=15,
                )
                delay = 2
                await self.sio.wait()
            except asyncio.CancelledError:
                await self.sio.disconnect()
                raise
            except Exception as e:
                log.warning("connect failed: %s", e)
            self.connected = False
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)
