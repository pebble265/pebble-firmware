"""The robot's face on the Pi screen (pygame). Runs on the main thread.

The app changes `face.mood` / `face.caption` / … from other threads; the
render loop just draws whatever is set.

Moods: boot, pairing, idle, happy, listening, thinking, speaking, sleepy,
       concerned, alert (medicine time), offline
"""
from __future__ import annotations

import math
import os
import random
import time
from dataclasses import dataclass, field

import pygame

BG = (11, 11, 15)
EYE = (232, 244, 255)
EYE_GLOW = (120, 190, 255)
ACCENT = (255, 196, 92)
SOFT = (150, 160, 175)
DANGER = (255, 120, 120)

THAI_FONTS = [
    "/usr/share/fonts/truetype/noto/NotoSansThai-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansThaiUI-Regular.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansThai-Regular.ttf",
    "/usr/share/fonts/truetype/tlwg/Loma.ttf",
    "/usr/share/fonts/opentype/tlwg/Loma.otf",
    "/usr/share/fonts/truetype/tlwg/Garuda.ttf",
    "/usr/share/fonts/opentype/tlwg/Garuda.otf",
]
THAI_FONTS_BOLD = [
    "/usr/share/fonts/truetype/noto/NotoSansThai-Bold.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansThai-Bold.ttf",
    "/usr/share/fonts/truetype/tlwg/Loma-Bold.ttf",
    "/usr/share/fonts/opentype/tlwg/Loma-Bold.otf",
]


LATIN_FONTS = [
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
]
LATIN_FONTS_BOLD = [
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]


def _load(paths, size):
    for p in paths:
        if os.path.isfile(p):
            return pygame.font.Font(p, size)
    return pygame.font.Font(None, size)  # pygame's built-in font (Latin only)


def _is_thai(ch: str) -> bool:
    return "\u0e00" <= ch <= "\u0e7f"


class MixedFont:
    """Thai letters with a Thai font, everything else (digits, English) with a Latin font.

    Thai-only fonts such as Noto Sans Thai have no Latin glyphs, so "Pebble" or
    "601 740" would show as empty boxes without this.
    """

    def __init__(self, thai_paths, latin_paths, size):
        self.thai = _load(thai_paths, size)
        self.latin = _load(latin_paths, size)

    def _runs(self, text: str):
        runs: list[tuple[pygame.font.Font, str]] = []
        cur, buf = None, ""
        for ch in text:
            f = self.thai if _is_thai(ch) else (cur if ch == " " and cur else self.latin)
            if f is not cur and buf:
                runs.append((cur, buf))
                buf = ""
            cur = f
            buf += ch
        if buf:
            runs.append((cur, buf))
        return runs

    def size(self, text: str):
        runs = self._runs(text)
        if not runs:
            return 0, self.get_height()
        return sum(f.size(t)[0] for f, t in runs), max(f.get_height() for f, _ in runs)

    def get_height(self):
        return max(self.thai.get_height(), self.latin.get_height())

    def render(self, text: str, aa: bool, color):
        runs = self._runs(text)
        ascent = max(self.thai.get_ascent(), self.latin.get_ascent())
        descent = max(-self.thai.get_descent(), -self.latin.get_descent())
        width = max(1, sum(f.size(t)[0] for f, t in runs))
        surf = pygame.Surface((width, ascent + descent + 2), pygame.SRCALPHA)
        x = 0
        for f, t in runs:
            img = f.render(t, aa, color)
            surf.blit(img, (x, ascent - f.get_ascent()))  # line up the baselines
            x += img.get_width()
        return surf


def _font(paths, size):
    bold = paths is THAI_FONTS_BOLD
    return MixedFont(paths, LATIN_FONTS_BOLD if bold else LATIN_FONTS, size)


@dataclass
class FaceState:
    mood: str = "boot"
    caption: str = ""          # one line under the face (subtitle / hint)
    big_text: str = ""         # pairing code, etc.
    sub_text: str = ""         # small line under big_text
    lines: list[str] = field(default_factory=list)  # e.g. hotline numbers
    look_x: float = 0.0        # -1..1, where the eyes look
    look_y: float = 0.0
    level: float = 0.0         # voice loudness 0..1 (mouth / ring)
    online: bool = False
    battery: int | None = None
    info: str = ""             # status corner (long press)


class Face:
    def __init__(self, width: int, height: int, fullscreen: bool, fps: int = 30):
        os.environ.setdefault("SDL_VIDEO_CENTERED", "1")
        pygame.display.init()
        pygame.font.init()
        flags = pygame.FULLSCREEN if fullscreen else 0
        if fullscreen:
            info = pygame.display.Info()
            width, height = info.current_w or width, info.current_h or height
            pygame.mouse.set_visible(False)
        self.screen = pygame.display.set_mode((width, height), flags)
        pygame.display.set_caption("pebble(a)")
        self.W, self.H = width, height
        self.fps = fps
        self.clock = pygame.time.Clock()
        self.s = FaceState()
        u = min(width, height)
        self.f_caption = _font(THAI_FONTS, max(18, u // 16))
        self.f_small = _font(THAI_FONTS, max(14, u // 26))
        self.f_big = _font(THAI_FONTS_BOLD, max(40, u // 5))
        self.f_title = _font(THAI_FONTS_BOLD, max(22, u // 12))
        # animation state
        self._blink_at = time.monotonic() + 3
        self._blink = 0.0
        self._lx = self._ly = 0.0
        self._wander_at = 0.0
        self._wander = (0.0, 0.0)
        self._t0 = time.monotonic()
        self.keys: list[str] = []  # keyboard events for the simulator (space / l / d / q)

    # ------------------------------------------------------------------ loop
    def tick(self) -> bool:
        """Draw one frame. Returns False when the window was closed."""
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                return False
            if e.type == pygame.KEYDOWN:
                if e.key == pygame.K_ESCAPE:
                    return False
                name = pygame.key.name(e.key)
                self.keys.append(name)
            if e.type == pygame.MOUSEBUTTONDOWN:  # touch screen tap = short press
                self.keys.append("space")
        self.draw()
        pygame.display.flip()
        self.clock.tick(self.fps)
        return True

    def close(self):
        pygame.quit()

    # ------------------------------------------------------------------ drawing
    def draw(self):
        s = self.s
        now = time.monotonic()
        t = now - self._t0
        scr = self.screen
        scr.fill(BG)

        if s.mood in ("pairing", "alert") and s.big_text:
            self._draw_card(t)
            self._status(s)
            return

        # eyes follow the face the camera sees, otherwise wander now and then
        tx, ty = s.look_x, s.look_y
        if abs(tx) < 0.01 and abs(ty) < 0.01:
            if now > self._wander_at:
                self._wander = (random.uniform(-0.5, 0.5), random.uniform(-0.25, 0.25)) if random.random() < 0.6 else (0, 0)
                self._wander_at = now + random.uniform(2, 5)
            tx, ty = self._wander
        if s.mood == "thinking":
            tx, ty = 0.55, -0.6
        self._lx += (tx - self._lx) * 0.15
        self._ly += (ty - self._ly) * 0.15

        # blink
        if now > self._blink_at:
            self._blink = 1.0
            self._blink_at = now + random.uniform(2.5, 6) if s.mood != "sleepy" else now + 1.5
        self._blink = max(0.0, self._blink - 0.18)
        blink = 1 - abs(self._blink * 2 - 1) if self._blink > 0 else 1.0  # 1 open .. 0 closed

        cx, cy = self.W / 2, self.H * (0.42 if (s.caption or s.lines) else 0.47)
        u = min(self.W, self.H)
        ew, eh = u * 0.2, u * 0.32
        gap = u * 0.2
        off_x, off_y = self._lx * u * 0.08, self._ly * u * 0.06

        mood = s.mood
        openness = blink
        if mood == "sleepy":
            openness = min(openness, 0.18 + 0.05 * math.sin(t * 1.5))
        if mood == "offline":
            openness = min(openness, 0.35)
        if mood == "listening":
            eh *= 1.08 + 0.05 * s.level
        if mood == "concerned":
            openness = min(openness, 0.8)

        for side in (-1, 1):
            ex = cx + side * (gap / 2 + ew / 2) + off_x
            ey = cy + off_y
            if mood == "happy":
                self._arc_eye(ex, ey, ew, eh)
            else:
                self._eye(ex, ey, ew, max(6, eh * openness), mood, side)

        # mouth while speaking
        if mood == "speaking":
            mw = u * 0.16
            mh = 6 + s.level * u * 0.12
            r = pygame.Rect(0, 0, mw, mh)
            r.center = (cx + off_x, cy + eh / 2 + u * 0.09 + off_y)
            pygame.draw.rect(self.screen, EYE, r, border_radius=int(mh / 2))

        # listening ring
        if mood == "listening":
            rr = u * 0.46 + s.level * u * 0.06 + 4 * math.sin(t * 4)
            pygame.draw.circle(self.screen, EYE_GLOW, (cx, cy), rr, 3)

        # thinking dots
        if mood == "thinking":
            for i in range(3):
                a = 0.5 + 0.5 * math.sin(t * 5 - i * 0.8)
                col = tuple(int(c * (0.35 + 0.65 * a)) for c in EYE)
                pygame.draw.circle(self.screen, col, (cx - u * 0.06 + i * u * 0.06, cy + eh / 2 + u * 0.1), u * 0.017)

        if mood == "sleepy":
            z = self.f_title.render("z", True, SOFT)
            self.screen.blit(z, (cx + gap / 2 + ew + 10, cy - eh / 2 - 10 - 8 * math.sin(t)))

        self._captions(s)
        self._status(s)

    def _eye(self, x, y, w, h, mood, side):
        r = pygame.Rect(0, 0, w, h)
        r.center = (x, y)
        rad = int(min(w, h) * 0.45)
        glow = pygame.Surface((w + 24, h + 24), pygame.SRCALPHA)
        pygame.draw.rect(glow, (*EYE_GLOW, 22), glow.get_rect(), border_radius=rad + 12)
        self.screen.blit(glow, (r.x - 12, r.y - 12))
        pygame.draw.rect(self.screen, EYE, r, border_radius=rad)
        if mood == "concerned" and h > 20:  # sad: the outer top corner droops
            ox = r.centerx + side * (w / 2 + 14)
            pts = [(ox, r.top - 14), (r.centerx - side * w * 0.15, r.top - 14), (ox, r.top + h * 0.42)]
            pygame.draw.polygon(self.screen, BG, pts)

    def _arc_eye(self, x, y, w, h):
        """Smiling eye: a rounded eye with its lower part cut away (a ^ crescent)."""
        self._eye(x, y, w, h, "idle", 0)
        cover = pygame.Rect(0, 0, w * 1.7, h * 1.1)
        cover.midtop = (x, y - h * 0.08)
        pygame.draw.ellipse(self.screen, BG, cover)

    def _wrap(self, text: str, font, width: int) -> list[str]:
        words = text.split(" ") if " " in text else [text]
        out, cur = [], ""
        for w in words:
            trial = (cur + " " + w).strip()
            if font.size(trial)[0] <= width:
                cur = trial
                continue
            if cur:
                out.append(cur)
            # a very long Thai run without spaces: cut by characters
            while font.size(w)[0] > width and len(w) > 1:
                n = len(w)
                while n > 1 and font.size(w[:n])[0] > width:
                    n -= 1
                out.append(w[:n])
                w = w[n:]
            cur = w
        if cur:
            out.append(cur)
        return out

    def _captions(self, s: FaceState):
        y = self.H * 0.78
        if s.caption:
            for line in self._wrap(s.caption, self.f_caption, int(self.W * 0.88))[-3:]:
                img = self.f_caption.render(line, True, EYE)
                self.screen.blit(img, img.get_rect(center=(self.W / 2, y)))
                y += img.get_height() * 1.05
        for line in s.lines[:4]:
            img = self.f_small.render(line, True, ACCENT)
            self.screen.blit(img, img.get_rect(center=(self.W / 2, y)))
            y += img.get_height() * 1.1

    def _draw_card(self, t):
        """Full-screen text card: pairing code, or medicine time."""
        s = self.s
        title_col = ACCENT if s.mood == "alert" else SOFT
        title = "ถึงเวลากินยาแล้ว" if s.mood == "alert" else "จับคู่กับแอป Pebble"
        y = self.H * 0.16
        img = self.f_title.render(title, True, title_col)
        self.screen.blit(img, img.get_rect(center=(self.W / 2, y)))
        pulse = 0.85 + 0.15 * math.sin(t * 3)
        big = self.f_big.render(s.big_text, True, tuple(int(c * pulse) for c in EYE))
        self.screen.blit(big, big.get_rect(center=(self.W / 2, self.H * 0.45)))
        y = self.H * 0.68
        for line in self._wrap(s.sub_text, self.f_caption, int(self.W * 0.9)):
            img = self.f_caption.render(line, True, SOFT)
            self.screen.blit(img, img.get_rect(center=(self.W / 2, y)))
            y += img.get_height() * 1.1
        if s.caption:
            img = self.f_small.render(s.caption, True, EYE)
            self.screen.blit(img, img.get_rect(center=(self.W / 2, y + 8)))

    def _status(self, s: FaceState):
        pad = 12
        dot = (120, 220, 140) if s.online else DANGER
        pygame.draw.circle(self.screen, dot, (pad + 6, pad + 8), 5)
        if s.battery is not None:
            img = self.f_small.render(f"{s.battery}%", True, SOFT)
            self.screen.blit(img, (self.W - img.get_width() - pad, pad - 2))
        if s.info:
            y = self.H - pad
            for line in reversed(s.info.split("\n")):
                img = self.f_small.render(line, True, SOFT)
                y -= img.get_height()
                self.screen.blit(img, (pad, y))
