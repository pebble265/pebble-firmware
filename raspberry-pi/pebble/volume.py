"""Speaker volume of the robot (music, Pebbi's voice, everything): PipeWire `wpctl`,
falling back to ALSA `amixer`. Used by the volume bar on the touch screen."""
from __future__ import annotations

import asyncio
import logging
import re
import shutil

log = logging.getLogger("volume")

SINK = "@DEFAULT_AUDIO_SINK@"
STEP = 5  # percent per + / - tap


class Volume:
    def __init__(self):
        self.tool = "wpctl" if shutil.which("wpctl") else "amixer" if shutil.which("amixer") else None
        self.level = 50      # 0..100
        self.muted = False
        self.ok = False      # read it at least once

    def view(self) -> dict:
        return {"level": self.level, "muted": self.muted, "ok": self.ok and self.tool is not None}

    async def _run(self, *args: str) -> tuple[int, str]:
        try:
            p = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE,
                                                     stderr=asyncio.subprocess.STDOUT)
            out, _ = await asyncio.wait_for(p.communicate(), 4)
            return p.returncode or 0, out.decode("utf-8", "replace")
        except Exception as e:
            return 1, str(e)

    async def refresh(self):
        if self.tool == "wpctl":
            code, out = await self._run("wpctl", "get-volume", SINK)   # "Volume: 0.45 [MUTED]"
            m = re.search(r"Volume:\s*([\d.]+)", out)
            if code == 0 and m:
                self.level = max(0, min(100, round(float(m.group(1)) * 100)))
                self.muted = "MUTED" in out
                self.ok = True
        elif self.tool == "amixer":
            code, out = await self._run("amixer", "get", "Master")      # "[45%] [on]"
            m = re.search(r"\[(\d+)%\]", out)
            if code == 0 and m:
                self.level = int(m.group(1))
                self.muted = "[off]" in out
                self.ok = True

    async def set(self, level: int):
        level = max(0, min(100, int(level)))
        self.level = level  # show it right away
        if self.tool == "wpctl":
            await self._run("wpctl", "set-volume", "-l", "1.0", SINK, f"{level / 100:.2f}")
            if self.muted and level > 0:
                await self._run("wpctl", "set-mute", SINK, "0")
        elif self.tool == "amixer":
            await self._run("amixer", "-q", "set", "Master", f"{level}%", "unmute" if level > 0 else "mute")
        await self.refresh()

    async def change(self, delta: int):
        await self.refresh()
        await self.set(self.level + delta)

    async def toggle_mute(self):
        if self.tool == "wpctl":
            await self._run("wpctl", "set-mute", SINK, "toggle")
        elif self.tool == "amixer":
            await self._run("amixer", "-q", "set", "Master", "toggle")
        await self.refresh()

    async def run_forever(self):
        """Keep the bar in step with changes from elsewhere (e.g. the phone's volume buttons)."""
        if not self.tool:
            log.warning("no wpctl/amixer: volume bar off")
            return
        while True:
            await self.refresh()
            await asyncio.sleep(2)
