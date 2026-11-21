"""Spotify Connect: the robot shows up as a speaker ("pebble(a)") in the Spotify app.

- spotifyd (full build: PulseAudio/PipeWire + MPRIS) runs as a child of this app, inside the
  desktop session so it can reach the sound server and the D-Bus session bus.
- Nothing to log in on the robot: pick "pebble(a)" in the Spotify app on a phone on the same
  Wi-Fi (Spotify Premium) and the phone hands the session over (zeroconf).
- Now playing + play/pause/next/previous through MPRIS, using the `playerctl` command.
- Backup for now playing: spotifyd runs spotify_hook.py on every player event and it writes
  ~/.cache/pebble-spotifyd/now.json (used when MPRIS/D-Bus gives nothing). Missing titles are
  looked up from the track id (Spotify oEmbed, no login needed).
- spotifyd's own output goes to ~/.cache/pebble-spotifyd/spotifyd.log
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

log = logging.getLogger("spotify")

PLAYER = "spotifyd"
CACHE = Path(os.path.expanduser("~/.cache/pebble-spotifyd"))
HOOK = Path(__file__).resolve().parent / "spotify_hook.py"
PLAYING_EVENTS = {"playing", "play", "start", "started", "unpaused", "resumed"}
PAUSED_EVENTS = {"paused", "pause"}
STOPPED_EVENTS = {"stopped", "stop", "unavailable", "sessiondisconnected", "session_disconnected", "end_of_track"}
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
        self.mpris = False                  # MPRIS answered last time (controls work)
        self.mpris_name: str | None = None  # e.g. spotifyd.instance1234
        self._oembed: dict[str, dict] = {}   # track id -> {"title", "art"}
        self._log = None
        self._said_no_mpris = False

    # ------------------------------------------------------------------ process
    def _config(self) -> Path:
        cfg_dir = Path(os.path.expanduser("~/.config/pebble-spotifyd"))
        cfg_dir.mkdir(parents=True, exist_ok=True)
        cache = CACHE
        cache.mkdir(parents=True, exist_ok=True)
        path = cfg_dir / "spotifyd.conf"
        name = self.device_name.replace('"', "")
        path.write_text(
            "[global]\n"
            f'device_name = "{name}"\n'
            'device_type = "speaker"\n'
            'backend = "pulseaudio"\n'
            # 320 = Ogg Vorbis 320 (Premium). At 160 some tracks only exist as MP3 here and that
            # decodes to junk -> every track is skipped with no sound ("skipping junk" in the log)
            "bitrate = 320\n"
            "use_mpris = true\n"
            "volume_normalisation = true\n"
            f'cache_path = "{cache}"\n'
            f'on_song_change_hook = "{sys.executable} {HOOK} {cache}"\n'
        )
        return path

    def _launch(self):
        cfg = self._config()
        logf = CACHE / "spotifyd.log"
        try:
            if logf.exists() and logf.stat().st_size > 2_000_000:
                logf.unlink()
            self._log = open(logf, "ab")
        except OSError:
            self._log = None
        self.proc = subprocess.Popen(
            [self.binary, "--no-daemon", "--config-path", str(cfg)],
            stdout=self._log or subprocess.DEVNULL,
            stderr=subprocess.STDOUT if self._log else subprocess.DEVNULL,
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
                await asyncio.to_thread(self._poll)
                await asyncio.sleep(1 if self.status == "Playing" else 2)
        finally:
            self.stop()

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()

    # ------------------------------------------------------------------ now playing
    def _player_name(self) -> str | None:
        """MPRIS name of our spotifyd (newer versions add ".instance<pid>")."""
        try:
            out = subprocess.run(["playerctl", "-l"], capture_output=True, text=True, timeout=3).stdout
        except Exception:
            return None
        names = [n.strip() for n in out.splitlines() if n.strip().startswith(PLAYER)]
        if self.proc:
            mine = [n for n in names if n.endswith(f"instance{self.proc.pid}")]
            if mine:
                return mine[0]
        return names[0] if names else None

    def _poll(self):
        if self.has_playerctl and self._poll_mpris():
            return
        self._poll_hook()

    def _poll_mpris(self) -> bool:
        name = self._player_name()
        self.mpris_name = name
        if not name:
            self.mpris = False
            if not self._said_no_mpris:
                self._said_no_mpris = True
                log.info("no MPRIS player from spotifyd (yet): using its events for now playing")
            return False
        fmt = SEP.join("{{%s}}" % f for f in FIELDS)
        try:
            out = subprocess.run(["playerctl", "-p", name, "metadata", "--format", fmt],
                                 capture_output=True, text=True, timeout=3)
        except Exception:
            return False
        parts = (out.stdout.rstrip("\n").split(SEP) + [""] * len(FIELDS))[: len(FIELDS)]
        status, title, artist, album, art, length, position, _vol = parts
        self.mpris = out.returncode == 0
        if out.returncode != 0 or not title:
            return False  # MPRIS is there but knows no track: try the event file
        if self._said_no_mpris:
            log.info("MPRIS player %s found", name)
            self._said_no_mpris = False
        self.status = status or "Stopped"
        self.title, self.artist, self.album, self.art = title, artist, album, art
        try:
            self.length_s = int(length) / 1e6 if length else 0.0
            self.position_s = int(position) / 1e6 if position else 0.0
        except ValueError:
            self.length_s = self.position_s = 0.0
        self.updated_at = time.monotonic()
        return True

    @staticmethod
    def _track_id(raw: str) -> str:
        m = re.search(r"([A-Za-z0-9]{22})$", raw or "")
        return m.group(1) if m else ""

    def _lookup(self, tid: str) -> dict:
        """Title + cover from the public oEmbed endpoint (cached)."""
        if not tid:
            return {}
        if tid in self._oembed:
            return self._oembed[tid]
        info: dict = {}
        try:
            url = f"https://open.spotify.com/oembed?url=https://open.spotify.com/track/{tid}"
            req = urllib.request.Request(url, headers={"User-Agent": "pebble-pi"})
            with urllib.request.urlopen(req, timeout=4) as r:
                d = json.load(r)
            info = {"title": d.get("title", ""), "art": d.get("thumbnail_url", "")}
        except Exception as e:
            log.debug("oembed %s: %s", tid, e)
        if len(self._oembed) > 200:
            self._oembed.clear()
        self._oembed[tid] = info
        return info

    def _poll_hook(self):
        try:
            d = json.loads((CACHE / "now.json").read_text())
        except Exception:
            self.status, self.title = "Stopped", ""
            return
        events = [e.lower() for e in d.get("_events") or [] if e]
        last = next((e for e in reversed(events) if e in PLAYING_EVENTS | PAUSED_EVENTS | STOPPED_EVENTS), "")
        status = "Playing" if last in PLAYING_EVENTS else "Paused" if last in PAUSED_EVENTS else "Stopped"
        if not last and events:
            status = "Playing"  # only "change"/"load" seen so far: a track is starting
        tid = self._track_id(d.get("TRACK_ID", ""))
        first = lambda *keys: next((str(d[k]) for k in keys if d.get(k)), "")
        title = first("TRACK_NAME", "NAME", "TITLE")
        artist = first("ARTISTS", "ARTIST", "TRACK_ARTISTS").replace("\n", ", ")
        album = first("ALBUM", "TRACK_ALBUM")
        art = first("COVERS", "COVER_URL", "COVER").split("\n")[0]
        if tid and (not title or not art):
            info = self._lookup(tid)
            title = title or info.get("title", "")
            art = art or info.get("art", "")
        try:
            length = float(first("DURATION_MS") or 0) / 1000
            position = float(first("POSITION_MS") or 0) / 1000
        except ValueError:
            length = position = 0.0
        at = float(d.get("_at") or time.time())
        if status == "Playing":
            position += max(0.0, time.time() - at)
        if not title and not tid:
            status = "Stopped"
        self.status, self.title, self.artist, self.album, self.art = status, title or "กำลังเล่นเพลง", artist, album, art
        self.length_s = length
        self.position_s = min(position, length) if length else position
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
            "controls": self.has_playerctl and self.mpris,
        }

    # ------------------------------------------------------------------ controls
    def _ctl(self, *args: str):
        if not self.has_playerctl:
            return
        try:
            subprocess.run(["playerctl", "-p", self.mpris_name or PLAYER, *args], capture_output=True, timeout=3)
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
