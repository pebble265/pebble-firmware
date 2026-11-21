"""Loads config.toml into plain dataclasses."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

VERSION = "pi-1.0.0"

DEFAULT_PATHS = [
    Path(os.environ.get("PEBBLE_CONFIG", "")),
    Path("/etc/pebble/config.toml"),
    Path(__file__).resolve().parent.parent / "config.toml",
]


@dataclass
class Config:
    server_url: str
    serial: str
    secret: str
    bluetooth: bool = False  # old Bluetooth pairing; the app now pairs with the 6-digit code
    model: str = "A3120B"
    color: str = "Space Black"
    esp_port: str = "auto"
    esp_baud: int = 115200
    fullscreen: bool = True
    width: int = 800
    height: int = 480
    fps: int = 30
    voice_enabled: bool = True
    stt: str = "google"
    tts: str = "gtts"
    language: str = "th"
    openai_key: str = ""
    openai_voice: str = "nova"
    input_device: str = ""
    output_device: str = ""
    silence_seconds: float = 1.2
    max_record_seconds: float = 15
    camera_enabled: bool = True
    greet_after_minutes: float = 30
    tb_host: str = "demo.thingsboard.io"
    tb_port: int = 1883
    tb_token: str = ""
    spotify_enabled: bool = True
    spotify_name: str = "pebble(a)"
    remind_every_minutes: float = 5
    give_up_after_minutes: float = 60
    extra: dict = field(default_factory=dict)


def load(path: str | None = None) -> Config:
    candidates = [Path(path)] if path else DEFAULT_PATHS
    for p in candidates:
        if p and str(p) not in ("", ".") and p.is_file():
            with open(p, "rb") as f:
                raw = tomllib.load(f)
            break
    else:
        raise SystemExit(
            "ไม่พบ config.toml — copy config.example.toml ไปที่ /etc/pebble/config.toml แล้วกรอกค่า"
        )

    s, r = raw.get("server", {}), raw.get("robot", {})
    e, d = raw.get("esp32", {}), raw.get("display", {})
    v, c = raw.get("voice", {}), raw.get("camera", {})
    t, p = raw.get("thingsboard", {}), raw.get("dispenser", {})
    sp = raw.get("spotify", {})

    url = str(s.get("url", "")).rstrip("/")
    if not url or not r.get("serial") or not r.get("secret"):
        raise SystemExit("config.toml ต้องมี server.url, robot.serial และ robot.secret")

    return Config(
        server_url=url,
        serial=r["serial"],
        secret=r["secret"],
        model=r.get("model", "A3120B"),
        bluetooth=bool(r.get("bluetooth", False)),
        color=r.get("color", "Space Black"),
        esp_port=str(e.get("port", "auto")),
        esp_baud=int(e.get("baud", 115200)),
        fullscreen=bool(d.get("fullscreen", True)),
        width=int(d.get("width", 800)),
        height=int(d.get("height", 480)),
        fps=int(d.get("fps", 30)),
        voice_enabled=bool(v.get("enabled", True)),
        stt=v.get("stt", "google"),
        tts=v.get("tts", "gtts"),
        language=v.get("language", "th"),
        openai_key=v.get("openai_api_key", "") or os.environ.get("OPENAI_API_KEY", ""),
        openai_voice=v.get("openai_tts_voice", "nova"),
        input_device=str(v.get("input_device", "")),
        output_device=str(v.get("output_device", "")),
        silence_seconds=float(v.get("silence_seconds", 1.2)),
        max_record_seconds=float(v.get("max_record_seconds", 15)),
        camera_enabled=bool(c.get("enabled", True)),
        greet_after_minutes=float(c.get("greet_after_minutes", 30)),
        tb_host=t.get("host", "demo.thingsboard.io"),
        tb_port=int(t.get("port", 1883)),
        tb_token=t.get("token", ""),
        spotify_enabled=bool(sp.get("enabled", True)),
        spotify_name=str(sp.get("device_name", "pebble(a)"))[:40],
        remind_every_minutes=float(p.get("remind_every_minutes", 5)),
        give_up_after_minutes=float(p.get("give_up_after_minutes", 60)),
    )
