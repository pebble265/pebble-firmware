"""REST client for the pebble backend (robot routes, device token)."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import aiohttp

log = logging.getLogger("api")

TOKEN_REFRESH_SECONDS = 50 * 60  # token lasts 1 h


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status


class Api:
    def __init__(self, base_url: str, serial: str, secret: str):
        self.base = base_url
        self.serial = serial
        self.secret = secret
        self.token: str | None = None
        self.token_at = 0.0
        self.paired = False
        self._session: aiohttp.ClientSession | None = None
        self._login_lock = asyncio.Lock()

    async def session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30),
                headers={"ngrok-skip-browser-warning": "1", "User-Agent": "pebble-pi"},
            )
        return self._session

    async def close(self):
        if self._session:
            await self._session.close()

    # -------------------------------------------------------------- auth
    async def login(self) -> None:
        async with self._login_lock:
            await self._login()

    async def _login(self) -> None:
        if True:
            s = await self.session()
            async with s.post(
                f"{self.base}/robot/auth",
                json={"serialNumber": self.serial, "secret": self.secret},
            ) as r:
                body = await self._body(r)
                if r.status != 200:
                    raise ApiError(r.status, str(body))
            self.token = body["accessToken"]
            self.token_at = time.monotonic()
            self.paired = bool(body.get("paired"))
            log.info("logged in (paired=%s)", self.paired)

    def _stale(self) -> bool:
        return not self.token or time.monotonic() - self.token_at > TOKEN_REFRESH_SECONDS

    async def ensure_token(self) -> str:
        if self._stale():
            async with self._login_lock:
                if self._stale():  # another task may have just logged in
                    await self._login()
        return self.token  # type: ignore[return-value]

    # -------------------------------------------------------------- requests
    async def request(self, method: str, path: str, json: Any = None) -> Any:
        for attempt in range(2):
            token = await self.ensure_token()
            s = await self.session()
            async with s.request(
                method,
                f"{self.base}{path}",
                json=json,
                headers={"Authorization": f"Bearer {token}"},
            ) as r:
                body = await self._body(r)
                if r.status == 401 and attempt == 0:
                    self.token = None  # expired or revoked -> log in again once
                    continue
                if r.status >= 400:
                    msg = body.get("message") if isinstance(body, dict) else body
                    raise ApiError(r.status, str(msg))
                return body
        raise ApiError(401, "unauthorized")

    @staticmethod
    async def _body(r: aiohttp.ClientResponse) -> Any:
        if r.status == 204:
            return None
        try:
            return await r.json(content_type=None)
        except Exception:
            return await r.text()

    # -------------------------------------------------------------- robot routes
    async def pairing_code(self) -> dict:
        return await self.request("POST", "/robot/pairing-code")

    async def heartbeat(self, battery: int, status: str, fw: str) -> dict:
        res = await self.request(
            "POST",
            "/robot/heartbeat",
            {"batteryLevel": max(0, min(100, int(battery))), "status": status, "firmwareVersion": fw[:20]},
        )
        self.paired = bool(res.get("paired"))
        return res

    async def me(self) -> dict:
        res = await self.request("GET", "/robot/me")
        self.paired = bool(res.get("paired"))
        return res

    async def doses_today(self) -> dict:
        return await self.request("GET", "/robot/doses/today")

    async def dispensed(self, dose_id: str) -> dict:
        return await self.request("POST", f"/robot/doses/{dose_id}/dispensed")

    async def take(self, dose_id: str, via: str = "robot_button") -> dict:
        return await self.request("POST", f"/robot/doses/{dose_id}/take", {"via": via})

    async def chat(self, message: str, conversation_id: str | None) -> dict:
        body: dict[str, Any] = {"message": message[:2000]}
        if conversation_id:
            body["conversationId"] = conversation_id
        return await self.request("POST", "/robot/chat/messages", body)

    async def mood(self, score: int) -> dict:
        return await self.request("POST", "/robot/moods", {"score": int(score)})

    async def mood_today(self) -> dict:
        return await self.request("GET", "/robot/moods/today")

    async def end_chat(self, conversation_id: str) -> None:
        await self.request("POST", f"/robot/chat/conversations/{conversation_id}/end")
