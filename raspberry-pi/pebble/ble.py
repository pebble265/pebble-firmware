"""Bluetooth LE pairing service, same UUIDs as the mobile app (lib/robot-protocol.ts).

  Service b7a1e000-…  advertised while the robot has no owner
  INFO    b7a1e001-…  read  JSON {"serial","name","model","color","fw"}
  PAIR    b7a1e002-…  read  6-digit pairing code ("" until the backend gave one)
"""
from __future__ import annotations

import json
import logging

log = logging.getLogger("ble")

SERVICE = "b7a1e000-5e6b-4c3a-9f0e-7065626c6561"
INFO = "b7a1e001-5e6b-4c3a-9f0e-7065626c6561"
PAIR = "b7a1e002-5e6b-4c3a-9f0e-7065626c6561"


class PairingBle:
    def __init__(self, serial: str, model: str, color: str, fw: str):
        self.info = json.dumps(
            {"serial": serial, "name": "pebble(a)", "model": model, "color": color, "fw": fw},
            ensure_ascii=False,
        ).encode()
        self.code = b""
        self.server = None
        self.running = False
        self._failed_at = 0.0

    def set_code(self, code: str):
        self.code = code.encode()
        if self.server:
            try:
                self.server.get_characteristic(PAIR).value = bytearray(self.code)
            except Exception:
                pass

    async def start(self):
        import time

        if self.running or (self._failed_at and time.monotonic() - self._failed_at < 120):
            return
        try:
            from bless import (
                BlessServer,
                GATTAttributePermissions,
                GATTCharacteristicProperties,
            )
        except ImportError:
            import time

            self._failed_at = time.monotonic()
            log.warning("bless not installed: Bluetooth pairing off (type the code instead)")
            return
        try:
            server = BlessServer(name="pebble(a)")
            server.read_request_func = self._read
            await server.add_new_service(SERVICE)
            for uuid, value in ((INFO, self.info), (PAIR, self.code)):
                await server.add_new_characteristic(
                    SERVICE,
                    uuid,
                    GATTCharacteristicProperties.read,
                    bytearray(value),
                    GATTAttributePermissions.readable,
                )
            await server.start()
            self.server = server
            self.running = True
            log.info("advertising pebble(a) for pairing")
        except Exception as e:
            import time

            log.warning("Bluetooth unavailable (%s) — retrying in 2 min; the code is on screen", e)
            self._failed_at = time.monotonic()

    def _read(self, characteristic, **_):
        uuid = str(characteristic.uuid).lower()
        if uuid == PAIR:
            return bytearray(self.code)
        if uuid == INFO:
            return bytearray(self.info)
        return characteristic.value

    async def stop(self):
        if not self.running:
            return
        if self.server:
            try:
                await self.server.stop()
            except Exception:
                pass
        self.server = None
        self.running = False
        log.info("Bluetooth pairing stopped")
