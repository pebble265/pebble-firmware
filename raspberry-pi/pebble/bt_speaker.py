"""The robot as a Bluetooth speaker: a phone pairs with "pebble(a)" and plays any app through it.

Why: Spotify Connect (spotifyd/librespot) is refused the audio keys for newer Spotify
accounts ("audio key error"), so every track is skipped. Bluetooth works with any account
and any music app.

How it works on Raspberry Pi OS Bookworm (desktop):
- BlueZ talks to the phone; PipeWire/WirePlumber (+ libspa-0.2-bluetooth) receives the A2DP
  audio and plays it on the default speaker. Nothing to do for the audio path here.
- This module (D-Bus, system bus, via dbus-next):
  * sets the adapter name/alias to "pebble(a)", powered, pairable
  * registers a pairing agent: pairing is only accepted while "จับคู่มือถือ" was pressed on
    the screen (discoverable for 3 minutes); phones paired before can reconnect any time
  * marks paired phones as trusted (reconnect without asking)
  * reads now playing from the phone (AVRCP, org.bluez.MediaPlayer1) and sends
    play/pause/next/previous back to it
Same interface as spotify.Spotify, so app.py can use either one.
"""
# no "from __future__ import annotations": dbus-next reads the D-Bus types from the annotations
import asyncio
import json
import logging
import re
import time
import urllib.parse
import urllib.request

log = logging.getLogger("bt")

BLUEZ = "org.bluez"
AGENT_PATH = "/pebble/agent"
PAIR_WINDOW_S = 180
A2DP_SINK = "0000110b-0000-1000-8000-00805f9b34fb"
AVRCP_UUIDS = ("0000110c-0000-1000-8000-00805f9b34fb", "0000110e-0000-1000-8000-00805f9b34fb",
               "0000110f-0000-1000-8000-00805f9b34fb")

try:
    from dbus_next import BusType, DBusError, Message, MessageType, Variant
    from dbus_next.aio import MessageBus
    from dbus_next.service import ServiceInterface, method
    HAVE_DBUS = True
except Exception:  # pragma: no cover - not on the Pi
    HAVE_DBUS = False


def _v(x):
    """Unwrap dbus-next Variants (also inside dicts/lists)."""
    if HAVE_DBUS and isinstance(x, Variant):
        return _v(x.value)
    if isinstance(x, dict):
        return {k: _v(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_v(i) for i in x]
    return x


if HAVE_DBUS:
    class _Agent(ServiceInterface):
        """org.bluez.Agent1: "Just Works" pairing, only while the screen's pairing window is open."""

        def __init__(self, owner: "BtSpeaker"):
            super().__init__("org.bluez.Agent1")
            self.owner = owner

        def _allow(self, device: str):
            if not self.owner.pairing_open(device):
                log.info("pairing from %s rejected (pairing window closed)", device)
                raise DBusError("org.bluez.Error.Rejected", "tap จับคู่มือถือ on the robot first")

        @method()
        def Release(self):
            pass

        @method()
        def RequestPinCode(self, device: "o") -> "s":
            self._allow(device)
            return "0000"

        @method()
        def DisplayPinCode(self, device: "o", pincode: "s"):
            pass

        @method()
        def RequestPasskey(self, device: "o") -> "u":
            self._allow(device)
            return 0

        @method()
        def DisplayPasskey(self, device: "o", passkey: "u", entered: "q"):
            pass

        @method()
        def RequestConfirmation(self, device: "o", passkey: "u"):
            self._allow(device)
            self.owner.just_paired(device)

        @method()
        def RequestAuthorization(self, device: "o"):
            self._allow(device)
            self.owner.just_paired(device)

        @method()
        def AuthorizeService(self, device: "o", uuid: "s"):
            if not self.owner.known_device(device):
                self._allow(device)

        @method()
        def Cancel(self):
            pass


class BtSpeaker:
    kind = "bluetooth"

    def __init__(self, name: str = "pebble(a)"):
        self.device_name = name
        self.bus = None
        self.adapter: str | None = None
        self.available = False
        self.error = ""                    # shown on screen when Bluetooth can't start
        self.discoverable_until = 0.0
        self.devices: dict[str, dict] = {}  # path -> Device1 props
        self.connected: str | None = None   # device path playing to us
        self.player: str | None = None      # its MediaPlayer1 path
        self._ok_devices: set[str] = set()  # paired while the window was open
        self._art_key: tuple | None = None
        self._art_cache: dict = {}
        # now playing (same fields as Spotify)
        self.status = "Stopped"
        self.title = self.artist = self.album = ""
        self.art = ""
        self.length_s = self.position_s = 0.0
        self.updated_at = 0.0
        self._paused_by_us = False

    # ------------------------------------------------------------------ D-Bus helpers
    async def _call(self, path: str, interface: str, member: str, signature: str = "", body=None):
        reply = await self.bus.call(Message(destination=BLUEZ, path=path, interface=interface,
                                            member=member, signature=signature, body=body or []))
        if reply.message_type == MessageType.ERROR:
            raise RuntimeError(f"{member}: {reply.error_name} {reply.body}")
        return reply.body

    async def _set(self, path: str, iface: str, prop: str, sig: str, value):
        await self._call(path, "org.freedesktop.DBus.Properties", "Set", "ssv", [iface, prop, Variant(sig, value)])

    async def _objects(self) -> dict:
        body = await self._call("/", "org.freedesktop.DBus.ObjectManager", "GetManagedObjects")
        return _v(body[0])

    # ------------------------------------------------------------------ pairing window
    def pairing_open(self, device: str) -> bool:
        return time.monotonic() < self.discoverable_until or device in self._ok_devices

    def just_paired(self, device: str):
        self._ok_devices.add(device)

    def known_device(self, device: str) -> bool:
        d = self.devices.get(device) or {}
        return bool(d.get("Paired") or d.get("Trusted")) or device in self._ok_devices

    async def pair_mode(self, seconds: int = PAIR_WINDOW_S):
        """Let a new phone find and pair with the robot for a few minutes."""
        if not self.adapter:
            return
        self.discoverable_until = time.monotonic() + seconds
        try:
            await self._set(self.adapter, "org.bluez.Adapter1", "Pairable", "b", True)
            await self._set(self.adapter, "org.bluez.Adapter1", "Discoverable", "b", True)
            log.info("discoverable as %s for %ds", self.device_name, seconds)
        except Exception as e:
            log.warning("discoverable: %s", e)

    async def _end_pair_mode(self):
        self.discoverable_until = 0.0
        try:
            await self._set(self.adapter, "org.bluez.Adapter1", "Discoverable", "b", False)
        except Exception:
            pass

    async def disconnect(self):
        if self.connected:
            try:
                await self._call(self.connected, "org.bluez.Device1", "Disconnect")
            except Exception as e:
                log.warning("disconnect: %s", e)

    # ------------------------------------------------------------------ main loop
    async def _setup(self) -> bool:
        if not HAVE_DBUS:
            self.error = "ไม่มีไลบรารี dbus-next (รัน install.sh ใหม่)"
            return False
        try:
            self.bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
            objs = await self._objects()
        except Exception as e:
            self.error = "เปิด Bluetooth ไม่ได้ (เช็กว่า Pi เปิด Bluetooth อยู่)"
            log.warning("bluetooth: %s", e)
            return False
        adapters = sorted(p for p, ifs in objs.items() if "org.bluez.Adapter1" in ifs)
        if not adapters:
            self.error = "ไม่พบ Bluetooth บน Pi"
            return False
        self.adapter = adapters[0]
        a = "org.bluez.Adapter1"
        for prop, sig, val in (("Powered", "b", True), ("Alias", "s", self.device_name),
                               ("DiscoverableTimeout", "u", 0), ("PairableTimeout", "u", 0),
                               ("Pairable", "b", True), ("Discoverable", "b", False)):
            try:
                await self._set(self.adapter, a, prop, sig, val)
            except Exception as e:
                log.warning("adapter %s: %s", prop, e)
        try:
            self.bus.export(AGENT_PATH, _Agent(self))
            await self._call("/org/bluez", "org.bluez.AgentManager1", "RegisterAgent", "os", [AGENT_PATH, "NoInputNoOutput"])
            await self._call("/org/bluez", "org.bluez.AgentManager1", "RequestDefaultAgent", "o", [AGENT_PATH])
        except Exception as e:
            log.warning("pairing agent: %s", e)
        self.error = ""
        log.info("Bluetooth speaker ready as %s on %s", self.device_name, self.adapter)
        return True

    async def run_forever(self):
        while not await self._setup():
            self.available = True  # show the error on the screen
            await asyncio.sleep(15)
        self.available = True
        try:
            while True:
                try:
                    await self._poll()
                except Exception as e:
                    log.debug("poll: %s", e)
                await asyncio.sleep(1)
        finally:
            self.stop()

    async def _poll(self):
        objs = await self._objects()
        self.devices = {p: ifs["org.bluez.Device1"] for p, ifs in objs.items()
                        if "org.bluez.Device1" in ifs and p.startswith(self.adapter + "/")}
        for path, d in self.devices.items():
            if d.get("Paired") and not d.get("Trusted"):
                try:
                    await self._set(path, "org.bluez.Device1", "Trusted", "b", True)
                    log.info("paired + trusted: %s", d.get("Alias") or d.get("Name"))
                except Exception:
                    pass
        # a connected phone that sends audio (A2DP) or remote control (AVRCP)
        conn = [p for p, d in self.devices.items() if d.get("Connected")
                and (A2DP_SINK in [u.lower() for u in d.get("UUIDs", [])] or
                     any(u in [x.lower() for x in d.get("UUIDs", [])] for u in AVRCP_UUIDS) or d.get("Paired"))]
        was = self.connected
        self.connected = conn[0] if conn else None
        if self.connected != was:
            name = self.device_label()
            log.info("phone %s", f"connected: {name}" if self.connected else "disconnected")
            if self.connected and self.discoverable_until:
                await self._end_pair_mode()  # got one: stop advertising
        if self.discoverable_until and time.monotonic() >= self.discoverable_until:
            await self._end_pair_mode()
        # now playing from the phone
        players = [p for p, ifs in objs.items() if "org.bluez.MediaPlayer1" in ifs
                   and self.connected and p.startswith(self.connected + "/")]
        self.player = players[0] if players else None
        if not self.player:
            self.status, self.title, self.artist, self.album, self.art = "Stopped", "", "", "", ""
            self._art_key = None
            self.length_s = self.position_s = 0.0
            return
        mp = objs[self.player]["org.bluez.MediaPlayer1"]
        track = mp.get("Track") or {}
        st = str(mp.get("Status", "stopped")).lower()
        self.status = {"playing": "Playing", "paused": "Paused"}.get(st, "Stopped")
        self.title = str(track.get("Title", "") or "")
        self.artist = str(track.get("Artist", "") or "")
        self.album = str(track.get("Album", "") or "")
        self.length_s = float(track.get("Duration", 0) or 0) / 1000
        self.position_s = float(mp.get("Position", 0) or 0) / 1000
        self.updated_at = time.monotonic()
        self._update_art()
        if self.status != "Stopped" and not self.title:
            self.title = "กำลังเล่นเพลง"

    # ------------------------------------------------------------------ cover art
    ART_RETRY_S = 30

    def _update_art(self):
        """Bluetooth (AVRCP) gives no picture: look the cover up by song + artist
        (iTunes Search, then Deezer; both free, no key). Failures are retried, not remembered."""
        key = (self.title.strip().lower(), self.artist.strip().lower())
        if key != self._art_key:
            self._art_key = key
            self.art = self._art_cache.get(key, "")
            self._art_next_try = 0.0
        if not key[0] or key in self._art_cache or getattr(self, "_art_busy", False):
            return
        if time.monotonic() < getattr(self, "_art_next_try", 0.0):
            return
        self._art_busy = True
        asyncio.get_running_loop().create_task(self._fetch_art(key, self.title, self.artist))

    async def _fetch_art(self, key: tuple, title: str, artist: str):
        try:
            url, sure = await asyncio.to_thread(self._lookup_art, title, artist)
        finally:
            self._art_busy = False
        if sure:  # found, or every service answered "no such song": remember it
            if len(self._art_cache) > 300:
                self._art_cache.clear()
            self._art_cache[key] = url
        else:     # network trouble: try again a bit later
            self._art_next_try = time.monotonic() + self.ART_RETRY_S
        log.info("cover for %s - %s: %s", artist, title, url or ("not found" if sure else "lookup failed, retry soon"))
        if self._art_key == key and url:
            self.art = url

    @staticmethod
    def _clean_title(title: str) -> str:
        t = re.sub(r"\s*[\(\[][^\)\]]*(feat|ft\.|with|remaster|version|live|edit|mix)[^\)\]]*[\)\]]", "", title, flags=re.I)
        t = re.sub(r"\s+-\s+.*(remaster|version|live|edit|mix|mono|stereo).*$", "", t, flags=re.I)
        return t.strip() or title.strip()

    @staticmethod
    def _get_json(url: str):
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (pebble robot)", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=8) as r:
            return json.load(r)

    @classmethod
    def _lookup_art(cls, title: str, artist: str) -> tuple[str, bool]:
        """-> (cover url or "", sure). sure=False when a service could not be reached."""
        clean = cls._clean_title(title)
        first_artist = re.split(r",|&| feat\.? | ft\.? | x ", artist, flags=re.I)[0].strip()
        want = clean.lower()
        reached = 0
        # 1) iTunes
        for country in ("TH", "US"):
            q = urllib.parse.urlencode({"term": f"{clean} {first_artist}".strip(), "entity": "song", "limit": 8, "country": country})
            try:
                results = cls._get_json(f"https://itunes.apple.com/search?{q}").get("results", [])
                reached += 1
            except Exception as e:
                log.warning("cover (iTunes %s): %s", country, e)
                continue
            if results:
                best = next((x for x in results if str(x.get("trackName", "")).strip().lower().startswith(want)), results[0])
                art = str(best.get("artworkUrl100", "")).replace("http://", "https://", 1)
                if art.startswith("https://"):
                    return re.sub(r"/\d+x\d+(bb)?(-\d+)?\.(jpg|png)$", r"/600x600bb.\3", art), True
        # 2) Deezer
        q = urllib.parse.urlencode({"q": f'track:"{clean}" artist:"{first_artist}"' if first_artist else clean, "limit": 5})
        try:
            data = cls._get_json(f"https://api.deezer.com/search?{q}").get("data", [])
            if not data and first_artist:
                data = cls._get_json("https://api.deezer.com/search?" + urllib.parse.urlencode({"q": f"{clean} {first_artist}", "limit": 5})).get("data", [])
            reached += 1
            for x in data:
                art = str((x.get("album") or {}).get("cover_xl") or (x.get("album") or {}).get("cover_big") or "")
                if art.startswith("https://"):
                    return art, True
        except Exception as e:
            log.warning("cover (Deezer): %s", e)
        return "", reached >= 2

    def device_label(self) -> str:
        d = self.devices.get(self.connected or "") or {}
        return str(d.get("Alias") or d.get("Name") or "มือถือ")

    def stop(self):
        pass  # BlueZ keeps running; nothing to clean up

    # ------------------------------------------------------------------ now playing / controls
    @property
    def playing(self) -> bool:
        return self.status == "Playing"

    def view(self) -> dict | None:
        if not self.available:
            return None
        pos = self.position_s
        if self.playing and self.updated_at:
            pos = min(self.length_s or pos, pos + (time.monotonic() - self.updated_at))
        left = max(0, int(self.discoverable_until - time.monotonic())) if self.discoverable_until else 0
        return {
            "kind": "bluetooth",
            "name": self.device_name,
            "error": self.error,
            "device": self.device_label() if self.connected else "",
            "pairLeft": left,
            "paired": [str(d.get("Alias") or d.get("Name") or "") for d in self.devices.values() if d.get("Paired")][:5],
            "status": self.status,
            "title": self.title,
            "artist": self.artist,
            "album": self.album,
            "art": self.art if self.art.startswith("https://") and self.connected else "",
            "length": round(self.length_s),
            "position": round(pos),
            "controls": bool(self.player),
        }

    async def command(self, what: str):
        member = {"play": "Play", "pause": "Pause", "next": "Next", "prev": "Previous"}.get(what)
        if what == "toggle":
            member = "Pause" if self.playing else "Play"
        if not member or not self.player:
            return
        try:
            await self._call(self.player, "org.bluez.MediaPlayer1", member)
        except Exception as e:
            log.debug("%s: %s", member, e)
        await asyncio.sleep(0.4)
        try:
            await self._poll()
        except Exception:
            pass

    async def duck(self):
        """Pause the phone's music while Pebbi talks (resume() puts it back)."""
        if self.playing:
            self._paused_by_us = True
            await self.command("pause")

    async def resume(self):
        if self._paused_by_us:
            self._paused_by_us = False
            await self.command("play")
