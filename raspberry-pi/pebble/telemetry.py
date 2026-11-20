"""ThingsBoard MQTT telemetry (the admin site reads it back)."""
from __future__ import annotations

import json
import logging

log = logging.getLogger("thingsboard")


class Telemetry:
    def __init__(self, host: str, port: int, token: str):
        self.client = None
        if not token:
            log.info("no ThingsBoard token: telemetry off")
            return
        import paho.mqtt.client as mqtt

        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="")
        self.client.username_pw_set(token)
        self.client.reconnect_delay_set(2, 60)
        self.client.on_connect = lambda c, u, f, rc, p: log.info("connected (%s)", rc)
        try:
            self.client.connect_async(host, port, keepalive=60)
            self.client.loop_start()
        except Exception as e:
            log.warning("connect failed: %s", e)

    def send(self, **values):
        if not self.client:
            return
        try:
            self.client.publish("v1/devices/me/telemetry", json.dumps(values), qos=0)
        except Exception as e:
            log.debug("publish failed: %s", e)

    def event(self, name: str, detail: str = ""):
        log.info("event %s %s", name, detail)
        self.send(event=name, **({"detail": detail[:200]} if detail else {}))

    def stop(self):
        if self.client:
            self.client.loop_stop()
            self.client.disconnect()
