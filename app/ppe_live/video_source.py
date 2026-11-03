"""Frame grabber: Hikvision NVR RTSP stream (or a video file for testing) on a background thread.

Always keeps only the newest frame so processing never falls behind the live feed.
Frames are normalised to 1920x1080 so every pixel threshold / zone in settings.toml applies.
"""
import logging
import os
import threading
import time

import cv2

from .config import FRAME_H, FRAME_W

log = logging.getLogger("camera")


HW_NAMES = {0: "CPU", 1: "graphics chip", 2: "graphics chip (D3D11)", 4: "graphics chip (Intel QSV)"}


def open_capture(source, hw_decode=True, timeout_ms=10000):
    """Open a stream or file with FFmpeg. With hw_decode the graphics chip decodes the video (much less
    CPU); if that is not possible the stream is opened again with normal CPU decoding."""
    base = [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, timeout_ms, cv2.CAP_PROP_READ_TIMEOUT_MSEC, timeout_ms]
    if hw_decode:
        cap = cv2.VideoCapture(source, cv2.CAP_FFMPEG,
                               base + [cv2.CAP_PROP_HW_ACCELERATION, cv2.VIDEO_ACCELERATION_ANY])
        if cap.isOpened():
            return cap
        cap.release()
    return cv2.VideoCapture(source, cv2.CAP_FFMPEG, base)


def open_rtsp(url, transport="tcp", timeout_ms=10000, hw_decode=True):
    os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = f"rtsp_transport;{transport}"
    return open_capture(url, hw_decode, timeout_ms)


def probe_rtsp(url, transport="tcp"):
    """Open the stream and read one frame. Returns (ok, frame_or_None)."""
    cap = open_rtsp(url, transport)
    try:
        if not cap.isOpened():
            return False, None
        for _ in range(10):
            ok, frame = cap.read()
            if ok:
                return True, frame
        return False, None
    finally:
        cap.release()


class VideoSource:
    def __init__(self, cfg):
        self.cfg = cfg
        self.lock = threading.Lock()
        self.frame = None
        self.seq = 0
        self.stamp = 0.0
        self.connected = False
        self.status = "starting"
        self.input_size = None
        self.decoder = None
        self.fps = 0.0
        self._stop = threading.Event()
        self._reconnect = threading.Event()
        self._thread = threading.Thread(target=self._run, name="camera", daemon=True)

    # ------------------------------------------------------------------ public
    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()

    def reconnect(self):
        """Drop the current stream and reconnect (used after the NVR sign-in changes)."""
        self._reconnect.set()

    def latest(self):
        """(frame, seq, timestamp) of the newest frame, or (None, 0, 0)."""
        with self.lock:
            return self.frame, self.seq, self.stamp

    # ------------------------------------------------------------------ internals
    def _open(self):
        c = self.cfg.camera
        hw = bool(self.cfg.processing.get("hw_decode", True))
        if c.source == "file":
            log.info("Opening video file %s", c.file_path)
            return open_capture(c.file_path, hw), True
        url = self.cfg.rtsp_url()
        if url is None:
            return None, False
        log.info("Connecting to %s", self.cfg.rtsp_url(redact=True))
        return open_rtsp(url, c.rtsp_transport, hw_decode=hw), False

    def _run(self):
        wait = float(self.cfg.camera.reconnect_seconds)
        while not self._stop.is_set():
            self._reconnect.clear()
            cap, is_file = self._open()
            if cap is None:
                self._set_down("NVR sign-in required (Settings)")
                self._reconnect.wait(wait)
                continue
            if not cap.isOpened():
                self._set_down("cannot open stream (check sign-in, channel, RTSP enabled on the NVR)")
                self._reconnect.wait(wait)
                continue
            src_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
            # Recordings are often 50-60 fps: only keep ~25 per second (like the live camera) to save CPU
            skip = max(1, round(src_fps / 25)) if is_file else 1
            period = skip / src_fps if is_file else 0.0
            self.input_size = (int(cap.get(3)), int(cap.get(4)))
            self.decoder = HW_NAMES.get(int(cap.get(cv2.CAP_PROP_HW_ACCELERATION)), "graphics chip")
            log.info("Stream open: %dx%d @ %.1f fps, decoded on %s", *self.input_size, src_fps, self.decoder)
            n, t0, next_t = 0, time.time(), time.time()
            while not self._stop.is_set() and not self._reconnect.is_set():
                for _ in range(skip - 1):
                    cap.grab()
                ok, frame = cap.read()
                if not ok:
                    if is_file and self.cfg.camera.file_loop:
                        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        continue
                    break
                if frame.shape[1] != FRAME_W or frame.shape[0] != FRAME_H:
                    frame = cv2.resize(frame, (FRAME_W, FRAME_H), interpolation=cv2.INTER_LINEAR)
                with self.lock:
                    self.frame, self.seq, self.stamp = frame, self.seq + 1, time.time()
                if not self.connected:
                    self.connected, self.status = True, "live"
                    log.info("Receiving frames")
                n += 1
                if n % 50 == 0:
                    now = time.time()
                    self.fps, t0 = 50 / (now - t0), now
                if period:                                   # play files at their real speed
                    next_t += period
                    d = next_t - time.time()
                    if d > 0:
                        time.sleep(d)
                    else:
                        next_t = time.time()
            cap.release()
            if self._reconnect.is_set():
                continue
            self._set_down("stream interrupted - reconnecting")
            self._reconnect.wait(wait)

    def _set_down(self, why):
        if self.connected or self.status != why:
            log.warning("Camera: %s", why)
        self.connected, self.status = False, why
