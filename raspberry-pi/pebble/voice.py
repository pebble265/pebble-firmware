"""Microphone + speaker: listen -> text (STT), text -> speech (TTS).

Blocking functions; the app runs them with asyncio.to_thread().
`level` (0..1) is the live loudness, the face uses it to move the mouth.
"""
from __future__ import annotations

import io
import logging
import shutil
import subprocess
import threading
import time
import wave

import numpy as np

log = logging.getLogger("voice")

RATE = 16000
BLOCK = 480  # 30 ms


def _device(value: str):
    if value == "":
        return None
    return int(value) if value.isdigit() else value


class Voice:
    def __init__(self, cfg):
        self.cfg = cfg
        self.level = 0.0
        self.speaking = False
        self._stop_speaking = threading.Event()
        self._cancel_listen = threading.Event()
        self.available = False
        if not cfg.voice_enabled:
            log.info("voice disabled in config")
            return
        try:
            import sounddevice as sd

            sd.query_devices()
            self.sd = sd
            self.available = True
        except Exception as e:
            log.warning("no audio device: %s", e)
        if not shutil.which("ffmpeg"):
            log.warning("ffmpeg not found: install it (sudo apt install ffmpeg)")

    # ================================================================ listen
    def listen(self, wait_seconds: float = 6.0) -> bytes | None:
        """Records one utterance. Returns 16 kHz mono WAV bytes, or None if nobody spoke."""
        if not self.available:
            return None
        sd = self.sd
        frames: list[np.ndarray] = []
        noise = []
        started = False
        silent_for = 0.0
        t0 = time.monotonic()
        block_s = BLOCK / RATE
        with sd.InputStream(
            samplerate=RATE,
            channels=1,
            dtype="int16",
            blocksize=BLOCK,
            device=_device(self.cfg.input_device),
        ) as stream:
            while True:
                if self._cancel_listen.is_set():
                    self.level = 0
                    return None
                data, _ = stream.read(BLOCK)
                chunk = data[:, 0].copy()
                rms = float(np.sqrt(np.mean(chunk.astype(np.float32) ** 2)))
                self.level = min(1.0, rms / 3000)
                elapsed = time.monotonic() - t0

                if len(noise) < 10:  # first 0.3 s: learn the room noise
                    noise.append(rms)
                    continue
                threshold = max(400.0, float(np.median(noise)) * 3)

                if not started:
                    frames = (frames + [chunk])[-10:]  # keep 0.3 s before speech starts
                    if rms > threshold:
                        started = True
                        speech_start = elapsed
                    elif elapsed > wait_seconds:
                        self.level = 0
                        return None
                    continue

                frames.append(chunk)
                silent_for = silent_for + block_s if rms < threshold * 0.7 else 0.0
                if silent_for >= self.cfg.silence_seconds:
                    break
                if elapsed - speech_start > self.cfg.max_record_seconds:
                    break
        self.level = 0
        audio = np.concatenate(frames)
        if len(audio) < RATE * 0.4:  # under 0.4 s: a click, not speech
            return None
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(audio.tobytes())
        return buf.getvalue()

    def transcribe(self, wav: bytes) -> str:
        """Speech -> text. Empty string when nothing was understood."""
        lang = self.cfg.language
        try:
            if self.cfg.stt == "openai" and self.cfg.openai_key:
                import requests

                r = requests.post(
                    "https://api.openai.com/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {self.cfg.openai_key}"},
                    files={"file": ("speech.wav", wav, "audio/wav")},
                    data={"model": "gpt-4o-mini-transcribe", "language": lang},
                    timeout=30,
                )
                r.raise_for_status()
                return r.json().get("text", "").strip()

            import speech_recognition as sr

            with wave.open(io.BytesIO(wav)) as w:
                raw = w.readframes(w.getnframes())
            audio = sr.AudioData(raw, RATE, 2)
            locale = {"th": "th-TH", "en": "en-US"}.get(lang, lang)
            return sr.Recognizer().recognize_google(audio, language=locale).strip()
        except Exception as e:
            if e.__class__.__name__ != "UnknownValueError":
                log.warning("speech-to-text failed: %s", e)
            return ""

    # ================================================================ speak
    def synth(self, text: str) -> bytes:
        """Text -> compressed audio (mp3)."""
        if self.cfg.tts == "openai" and self.cfg.openai_key:
            import requests

            r = requests.post(
                "https://api.openai.com/v1/audio/speech",
                headers={"Authorization": f"Bearer {self.cfg.openai_key}"},
                json={"model": "gpt-4o-mini-tts", "voice": self.cfg.openai_voice, "input": text, "response_format": "mp3"},
                timeout=60,
            )
            r.raise_for_status()
            return r.content
        from gtts import gTTS

        buf = io.BytesIO()
        gTTS(text, lang=self.cfg.language).write_to_fp(buf)
        return buf.getvalue()

    def speak(self, text: str, rate: float = 1.0) -> None:
        """Says `text` and returns when done (or when stop_speaking() is called)."""
        text = text.strip()
        if not text:
            return
        log.info("say: %s", text)
        if not self.available:
            time.sleep(min(6.0, 0.08 * len(text)))  # no speaker: pretend, so the face still animates
            return
        try:
            mp3 = self.synth(text)
            pcm = self._decode(mp3, rate)
        except Exception as e:
            log.warning("text-to-speech failed: %s", e)
            return
        self._play(pcm, 24000)

    def _decode(self, mp3: bytes, rate: float) -> np.ndarray:
        rate = max(0.5, min(2.0, rate or 1.0))
        out = subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-i", "pipe:0", "-af", f"atempo={rate}",
             "-f", "s16le", "-ac", "1", "-ar", "24000", "pipe:1"],
            input=mp3, capture_output=True, check=True,
        ).stdout
        return np.frombuffer(out, dtype=np.int16)

    def _play(self, pcm: np.ndarray, rate: int):
        sd = self.sd
        self._stop_speaking.clear()
        self.speaking = True
        block = rate // 50  # 20 ms
        try:
            with sd.OutputStream(samplerate=rate, channels=1, dtype="int16",
                                 device=_device(self.cfg.output_device)) as out:
                for i in range(0, len(pcm), block):
                    if self._stop_speaking.is_set():
                        break
                    chunk = pcm[i:i + block]
                    self.level = min(1.0, float(np.sqrt(np.mean(chunk.astype(np.float32) ** 2))) / 6000)
                    out.write(chunk.reshape(-1, 1))
        finally:
            self.level = 0
            self.speaking = False

    def play_tone(self, freq: float = 880, ms: int = 120, volume: float = 0.25):
        """Short beep from the speaker (listening start / stop cue)."""
        if not self.available:
            return
        t = np.arange(int(24000 * ms / 1000)) / 24000
        env = np.minimum(1, np.minimum(t, t[::-1]) * 60)
        tone = (np.sin(2 * np.pi * freq * t) * env * volume * 32767).astype(np.int16)
        try:
            self.sd.play(tone, 24000, device=_device(self.cfg.output_device), blocking=True)
        except Exception:
            pass

    def stop_speaking(self):
        self._stop_speaking.set()

    def cancel_listening(self):
        self._cancel_listen.set()

    def reset_cancel(self):
        """Call when a new conversation starts."""
        self._cancel_listen.clear()
