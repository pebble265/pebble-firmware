"""python -m pebble [--config PATH] [--windowed] [--text] [--sim] [--debug]"""
import argparse
import logging

from . import config
from .app import run_face, run_web


def main():
    ap = argparse.ArgumentParser(prog="pebble", description="pebble(a) robot brain (Raspberry Pi 5)")
    ap.add_argument("--config", help="path to config.toml (default /etc/pebble/config.toml)")
    ap.add_argument("--ui", choices=["web", "face"], default="web",
                    help="web = touch app in the style of the phone app (default), face = robot face")
    ap.add_argument("--no-browser", action="store_true", help="web UI: don't open Chromium (open http://127.0.0.1:8787 yourself)")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--windowed", action="store_true", help="face UI: window instead of full screen")
    ap.add_argument("--text", action="store_true", help="type to Pebbi in the terminal instead of speaking")
    ap.add_argument("--sim", action="store_true", help="no ESP32 attached: print its commands instead")
    ap.add_argument("--no-camera", action="store_true")
    ap.add_argument("--debug", action="store_true")
    a = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if a.debug else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)-10s %(message)s",
        datefmt="%H:%M:%S",
    )
    for noisy in ("engineio", "socketio", "aiohttp", "urllib3", "bleak", "dbus_next"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    cfg = config.load(a.config)
    if a.sim:
        cfg.esp_port = "sim"
    if a.no_camera:
        cfg.camera_enabled = False
    if a.ui == "face":
        run_face(cfg, windowed=a.windowed, text_mode=a.text)
    else:
        run_web(cfg, text_mode=a.text, browser=not a.no_browser, port=a.port)


if __name__ == "__main__":
    main()
