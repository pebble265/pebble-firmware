"""Wi-Fi from inside the touch app (no desktop needed) — NetworkManager via `nmcli`.

The kiosk used to close itself so the desktop's Wi-Fi menu could be used, but the Pi's
desktop has no on-screen keyboard and the app only came back after it got online, so it
looked like the app had crashed. Now the screen lists networks, has its own keyboard and
connects here.

Needs: Raspberry Pi OS Bookworm (NetworkManager) and permission for the app's user to
change networks (install.sh adds a polkit rule).
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import time

log = logging.getLogger("wifi")

SCAN_TIMEOUT = 20
CONNECT_TIMEOUT = 45


def _split(line: str) -> list[str]:
    """`nmcli -t` line -> fields (":" separates, "\\:" and "\\\\" are escaped)."""
    out, cur, esc = [], [], False
    for ch in line:
        if esc:
            cur.append(ch)
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == ":":
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur))
    return out


def _is_wrong_password(text: str) -> bool:
    t = text.lower()
    return "secrets were required" in t or "no secrets" in t or "(7)" in t and "secret" in t


def _thai_error(text: str) -> str:
    t = text.lower()
    if _is_wrong_password(text) or "psk: property is invalid" in t:
        return "รหัสผ่าน Wi-Fi ไม่ถูกต้อง"
    if "no network with ssid" in t or "not found" in t:
        return "ไม่พบ Wi-Fi นี้แล้ว ลองค้นหาใหม่"
    if "not authorized" in t or "insufficient privileges" in t or "permission" in t:
        return "แอปยังไม่มีสิทธิ์ตั้งค่า Wi-Fi (รัน install.sh ใหม่ แล้ว reboot)"
    if "timeout" in t or "timed out" in t:
        return "เชื่อมต่อนานเกินไป ลองใหม่อีกครั้ง หรือขยับหุ่นให้ใกล้เราเตอร์"
    if "no wi-fi device" in t or "no suitable device" in t:
        return "ไม่พบการ์ด Wi-Fi ของ Pi"
    return "เชื่อมต่อไม่สำเร็จ ลองใหม่อีกครั้ง"


class Wifi:
    def __init__(self):
        self.available = shutil.which("nmcli") is not None
        self.scanning = False
        self.networks: list[dict] = []
        self.scanned_at = 0.0
        self.busy: str | None = None             # SSID being connected
        self.result: dict | None = None          # {"ok", "msg", "ssid"} of the last attempt
        self.on_connected = None                 # callback after a successful connect

    def view(self) -> dict:
        return {
            "available": self.available,
            "scanning": self.scanning,
            "networks": self.networks,
            "busy": self.busy,
            "result": self.result,
        }

    async def _run(self, *args: str, timeout: float) -> tuple[int, str]:
        proc = await asyncio.create_subprocess_exec(
            "nmcli", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return 124, "timeout"
        return proc.returncode or 0, out.decode("utf-8", "replace").strip()

    async def _known(self) -> set[str]:
        code, out = await self._run("-t", "-f", "NAME,TYPE", "connection", "show", timeout=10)
        if code:
            return set()
        names = set()
        for line in out.splitlines():
            f = _split(line)
            if len(f) >= 2 and f[1] in ("802-11-wireless", "wifi"):
                names.add(f[0])
        return names

    async def scan(self):
        if not self.available:
            self.result = {"ok": False, "msg": "เครื่องนี้ไม่มี NetworkManager (nmcli)", "ssid": ""}
            return
        if self.scanning:
            return
        self.scanning = True
        try:
            known = await self._known()
            code, out = await self._run(
                "-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "device", "wifi", "list", "--rescan", "yes",
                timeout=SCAN_TIMEOUT,
            )
            if code:
                log.warning("scan failed: %s", out[:200])
                self.result = {"ok": False, "msg": _thai_error(out), "ssid": ""}
                return
            best: dict[str, dict] = {}
            for line in out.splitlines():
                f = _split(line)
                if len(f) < 4 or not f[1]:
                    continue  # hidden network
                ssid = f[1]
                try:
                    signal = int(f[2] or 0)
                except ValueError:
                    signal = 0
                net = {
                    "ssid": ssid,
                    "signal": signal,
                    "secure": f[3] not in ("", "--"),
                    "enterprise": "802.1X" in f[3],  # needs a username too: not supported on the screen
                    "security": f[3],
                    "active": f[0].strip() == "*",
                    "known": ssid in known,
                }
                old = best.get(ssid)
                if not old or net["active"] or (signal > old["signal"] and not old["active"]):
                    best[ssid] = net
            self.networks = sorted(best.values(), key=lambda n: (not n["active"], not n["known"], -n["signal"]))[:20]
            self.scanned_at = time.monotonic()
        except Exception as e:
            log.warning("scan: %s", e)
            self.result = {"ok": False, "msg": "ค้นหา Wi-Fi ไม่สำเร็จ", "ssid": ""}
        finally:
            self.scanning = False

    async def _wifi_device(self) -> str:
        code, out = await self._run("-t", "-f", "DEVICE,TYPE", "device", timeout=10)
        for line in out.splitlines() if not code else []:
            f = _split(line)
            if len(f) >= 2 and f[1] == "wifi":
                return f[0]
        return "wlan0"

    def _key_mgmt(self, ssid: str) -> str | None:
        sec = next((n.get("security", "") for n in self.networks if n["ssid"] == ssid), "WPA2")
        if not sec or sec == "--":
            return None
        if "WPA3" in sec and "WPA2" not in sec and "WPA1" not in sec:
            return "sae"
        return "wpa-psk"

    async def _connect_profile(self, ssid: str, password: str) -> tuple[int, str]:
        """Plan B: make the profile ourselves (key-mgmt set explicitly), then bring it up.
        Fixes "802-11-wireless-security.key-mgmt: property is missing" on Bookworm."""
        dev = await self._wifi_device()
        await self._run("connection", "delete", "id", ssid, timeout=10)
        args = ["connection", "add", "type", "wifi", "ifname", dev, "con-name", ssid, "ssid", ssid,
                "connection.autoconnect", "yes"]
        km = self._key_mgmt(ssid)
        if password and km:
            args += ["wifi-sec.key-mgmt", km, "wifi-sec.psk", password]
        code, out = await self._run(*args, timeout=15)
        if code:
            return code, out
        return await self._run("--wait", "35", "connection", "up", "id", ssid, timeout=CONNECT_TIMEOUT)

    async def connect(self, ssid: str, password: str = ""):
        """Join `ssid`. The password is never logged or shown."""
        if not self.available or self.busy or not ssid:
            return
        self.busy = ssid
        self.result = None
        hide = (lambda t: t.replace(password, "***")) if password else (lambda t: t)
        try:
            known = ssid in await self._known()
            if known and not password:
                code, out = await self._run("--wait", "35", "connection", "up", "id", ssid, timeout=CONNECT_TIMEOUT)
            else:
                if known:  # new password for a saved network: replace the old profile
                    await self._run("connection", "delete", "id", ssid, timeout=10)
                args = ["--wait", "35", "device", "wifi", "connect", ssid]
                if password:
                    args += ["password", password]
                code, out = await self._run(*args, timeout=CONNECT_TIMEOUT)
                if code and not _is_wrong_password(out) and "no network with ssid" not in out.lower():
                    log.info("connect to %s: plan A failed (%s), trying plan B", ssid, hide(out)[:160])
                    code, out = await self._connect_profile(ssid, password)
                if code:
                    # don't leave a broken profile that the Pi keeps retrying in the background
                    await self._run("connection", "delete", "id", ssid, timeout=10)
            if code:
                safe = hide(out)
                log.warning("connect to %s failed: %s", ssid, safe[:300])
                detail = next((l.strip() for l in safe.splitlines() if l.strip().lower().startswith("error")), safe.strip())
                self.result = {"ok": False, "msg": _thai_error(safe), "ssid": ssid, "detail": detail[:160]}
            else:
                log.info("connected to Wi-Fi %s", ssid)
                self.result = {"ok": True, "msg": f"เชื่อมต่อ {ssid} แล้ว", "ssid": ssid}
                if self.on_connected:
                    self.on_connected()
                asyncio.create_task(self.scan())
        except Exception as e:
            log.warning("connect: %s", type(e).__name__)
            self.result = {"ok": False, "msg": "เชื่อมต่อไม่สำเร็จ ลองใหม่อีกครั้ง", "ssid": ssid, "detail": type(e).__name__}
        finally:
            self.busy = None
