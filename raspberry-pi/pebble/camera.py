"""Camera: is someone in front of the robot, and where (so the eyes can follow).

Frames stay in memory on the Pi — nothing is saved or uploaded.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

log = logging.getLogger("camera")


class Camera:
    def __init__(self, enabled: bool, on_arrive: Callable[[float], None]):
        self.enabled = enabled
        self.on_arrive = on_arrive  # called with "minutes away" when a face shows up
        self.present = False
        self.face_x = 0.0  # -1 (left) .. 1 (right), from the robot's point of view
        self.face_y = 0.0
        self.last_seen = 0.0
        self.available = False
        self._stop = threading.Event()

    def start(self):
        if not self.enabled:
            return
        threading.Thread(target=self._run, name="camera", daemon=True).start()

    def stop(self):
        self._stop.set()

    def _open(self):
        try:
            from picamera2 import Picamera2  # Raspberry Pi camera module

            cam = Picamera2()
            cam.configure(cam.create_preview_configuration(main={"size": (320, 240), "format": "RGB888"}))
            cam.start()
            return lambda: cam.capture_array(), cam.stop
        except Exception as e:
            log.info("picamera2 not available (%s), trying USB camera", e)
        import cv2

        cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            raise RuntimeError("no camera found")
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 320)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 240)
        return (lambda: cap.read()[1]), cap.release

    def _run(self):
        try:
            import cv2

            grab, close = self._open()
            cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        except Exception as e:
            log.warning("camera off: %s", e)
            return
        self.available = True
        log.info("camera on")
        away_since = time.monotonic()
        try:
            while not self._stop.is_set():
                frame = grab()
                if frame is None:
                    time.sleep(1)
                    continue
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                faces = cascade.detectMultiScale(gray, scaleFactor=1.2, minNeighbors=5, minSize=(40, 40))
                now = time.monotonic()
                if len(faces):
                    x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
                    H, W = gray.shape
                    # mirror x: the camera looks out, the eyes look out
                    self.face_x = -((x + w / 2) / W * 2 - 1)
                    self.face_y = (y + h / 2) / H * 2 - 1
                    if not self.present:
                        self.present = True
                        self.on_arrive((now - away_since) / 60)
                    self.last_seen = now
                elif self.present and now - self.last_seen > 5:
                    self.present = False
                    away_since = self.last_seen
                    self.face_x = self.face_y = 0.0
                time.sleep(0.25)
        finally:
            close()
