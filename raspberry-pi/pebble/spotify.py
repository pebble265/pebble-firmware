"""Spotify Connect: the robot shows up as a speaker ("pebble(a)") in the Spotify app.

- spotifyd (full build: PulseAudio/PipeWire + MPRIS) runs as a child of this app, inside the
  desktop session so it can reach the sound server and the D-Bus session bus.
- Nothing to log in on the robot: pick "pebble(a)" in the Spotify app on a phone on the same
  Wi-Fi (Spotify Premium) and the phone hands the session over (zeroconf).
- Now playing + play/pause/next/previous through MPRIS, using the `playerctl` command.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path

log = logging.getLogger("spotify")

PLAYER = "spotifyd"
FIELDS = ["status", "title", "artist", "album", "mpris:artUrl", "mpris:length", "position", "volume"]
SEP = "\x1f"


def find_spotifyd() -> str | None:
    for p in ("/opt/pebble/bin/spotifyd", os.path.expanduser("~/.local/bin/spotifyd")):
        if os.access(p, os.X_OK):
            return p
    return shutil.which("spotifyd")


class Spotify:
    def __init__(self, enabled: bool, device_name: str = "pebble(a)"):
        self.enabled = enabled
        self.device_name = device_name
        self.binary = find_spotifyd() if enabled else None
        self.has_playerctl = bool(shutil.which("playerctl"))
        self.proc: subprocess.Popen | None = None
        self.available = False
        # now playing
        self.status = "Stopped"   # Playing / Paused / Stopped
        self.title = ""
        self.artist = ""
        self.album = ""
        self.art = ""
        self.length_s = 0.0
        self.position_s = 0.0
        self.updated_at = 0.0
        self._paused_by_us = False

    # ------------------------------------------------------------------ process
    def _config(self) -> Path:
        cfg_dir = Path(os.path.expanduser("~/.config/pebble-spotifyd"))
        cfg_dir.mkdir(parents=True, exist_ok=True)
        cache = Path(os.path.expanduser("~/.cache/pebble-spotifyd"))
        cache.mkdir(parents=True, exist_ok=True)
        path = cfg_dir / "spotifyd.conf"
        name = self.device_name.replace('"', "")
        path.write_text(
            "[global]\n"
            f'device_name = "{name}"\n'
            'device_type = "speaker"\n'
            'backend = "pulseaudio"\n'
            "bitrate = 160\n"
            "use_mpris = true\n"
            "volume_normalisation = true\n"
            f'cache_path = "{cache}"\n'
        )
        return path

    def _launch(self):
        self.proc = subprocess.Popen(
            [self.binary, "--no-daemon", "--config-path", str(self._config())],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        log.info("spotifyd started as \"%s\"", self.device_name)

    async def run_forever(self):
        if not self.enabled:
            return
        if not self.binary:
            log.warning("spotifyd not installed: Spotify off (run install.sh again)")
            return
        if not self.has_playerctl:
            log.warning("playerctl not installed: no now-playing / controls on screen")
        self.available = True
        self._launch()
        try:
            while True:
                if self.proc and self.proc.poll() is not None:
                    log.warning("spotifyd exited (%s): restarting", self.proc.returncode)
                    await asyncio.sleep(3)
                    self._launch()
                if self.has_playerctl:
                    await asyncio.to_thread(self._poll)
                await asyncio.sleep(1 if self.status == "Playing" else 2)
        finally:
            self.stop()

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()

    # ------------------------------------------------------------------ now playing
    def _poll(self):
        fmt = SEP.join("{{%s}}" % f for f in FIELDS)
        try:
            out = subprocess.run(["playerctl", "-p", PLAYER, "metadata", "--format", fmt],
                                 capture_output=True, text=True, timeout=3)
        except Exception:
            return
        if out.returncode != 0 or not out.stdout.strip():
            self.status, self.title = "Stopped", ""
            return
        parts = (out.stdout.rstrip("\n").split(SEP) + [""] * len(FIELDS))[: len(FIELDS)]
        status, title, artist, album, art, length, position, _vol = parts
        self.status = status or "Stopped"
        self.title, self.artist, self.album, self.art = title, artist, album, art
        try:
            self.length_s = int(length) / 1e6 if length else 0.0
            self.position_s = int(position) / 1e6 if position else 0.0
        except ValueError:
            self.length_s = self.position_s = 0.0
        self.updated_at = time.monotonic()

    @property
    def playing(self) -> bool:
        return self.status == "Playing"

    def view(self) -> dict | None:
        if not self.available:
            return None
        pos = self.position_s
        if self.playing and self.updated_at:
            pos = min(self.length_s or pos, pos + (time.monotonic() - self.updated_at))
        return {
            "name": self.device_name,
            "status": self.status,
            "title": self.title,
            "artist": self.artist,
            "album": self.album,
            "art": self.art if self.art.startswith("https://") else "",
            "length": round(self.length_s),
            "position": round(pos),
            "controls": self.has_playerctl,
        }

    # ------------------------------------------------------------------ controls
    def _ctl(self, *args: str):
        if not self.has_playerctl:
            return
        try:
            subprocess.run(["playerctl", "-p", PLAYER, *args], capture_output=True, timeout=3)
        except Exception as e:
            log.debug("playerctl %s: %s", args, e)

    async def command(self, what: str):
        cmd = {"toggle": "play-pause", "play": "play", "pause": "pause", "next": "next", "prev": "previous"}.get(what)
        if cmd:
            await asyncio.to_thread(self._ctl, cmd)
            await asyncio.sleep(0.4)
            await asyncio.to_thread(self._poll)

    async def duck(self):
        """Pause the music while Pebbi talks or listens (resume() puts it back)."""
        if self.playing:
            self._paused_by_us = True
            await asyncio.to_thread(self._ctl, "pause")
            self.status = "Paused"

    async def resume(self):
        if self._paused_by_us:
            self._paused_by_us = False
            await asyncio.to_thread(self._ctl, "play")
