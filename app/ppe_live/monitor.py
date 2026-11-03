"""The monitor: wires camera -> detection -> tracking -> PPE rules -> overlay / incidents.

Threads
  camera     VideoSource            newest frame from the NVR
  analysis   Monitor._analysis_loop  detection + tracking + PPE at `analysis_fps`
  render     Monitor._render_loop    overlay on every displayed frame -> JPEG for the dashboard
  email      EmailNotifier           sends incident emails
  retention  RetentionJob            deletes old incidents
"""
import logging
import threading
import time
from collections import Counter
from datetime import datetime

import cv2
import numpy as np

from . import overlay
from .config import FRAME_H, FRAME_W
from .incidents import IncidentStore, RetentionJob
from .inference import CoatClassifier, PersonDetector
from .notifier import EmailNotifier, email_ready
from .ppe_rules import PersonRegistry, Scorer
from .tracker import Tracker
from .video_source import VideoSource

log = logging.getLogger("monitor")
RANK = {"none": 0, "partial": 1, "full": 2, "pending": 3}


class Monitor:
    def __init__(self, cfg):
        self.cfg = cfg
        self.source = VideoSource(cfg)
        log.info("Loading models ...")
        self.detector = PersonDetector(cfg)
        self.coat = CoatClassifier(cfg)
        self.tracker = Tracker(high=cfg.processing.detector_conf)
        self.registry = PersonRegistry(cfg)
        self.scorer = Scorer(cfg)
        self.store = IncidentStore()
        self.notifier = EmailNotifier(cfg, self.store)
        self.retention = RetentionJob(self.store, cfg.incidents.retention_days)

        self.scene_lock = threading.Lock()
        self.scene, self.scene_t = [], 0.0           # list of dicts for the overlay
        self.jpeg_lock = threading.Condition()
        self.jpeg, self.jpeg_seq = None, 0
        self.stats = {"analysis_fps": 0.0, "detect_ms": 0.0, "coat_ms": 0.0, "mode": "active"}
        self.viewers, self._viewers_lock = 0, threading.Lock()   # open dashboard video streams
        self.visits_today, self._day = Counter(), datetime.now().date()
        self._finished = []                          # visits waiting for the guard hand-off check
        self._last_stamp = None
        self._prev_small, self._busy_until = None, 0.0   # idle / active analysis rate (motion + people)

    # ================================================================== lifecycle
    def start(self):
        self.source.start()
        self.notifier.start()
        self.retention.start()
        threading.Thread(target=self._guard(self._analysis_loop), name="analysis", daemon=True).start()
        threading.Thread(target=self._guard(self._render_loop), name="render", daemon=True).start()
        return self

    @staticmethod
    def _guard(fn):
        """Restart a loop if it ever raises, so one bad frame never stops monitoring."""
        def run():
            while True:
                try:
                    fn()
                except Exception:                    # noqa: BLE001
                    log.exception("%s crashed - restarting in 2 s", fn.__name__)
                    time.sleep(2)
        return run

    # ================================================================== analysis
    def _analysis_loop(self):
        last_seq, n, t_fps = 0, 0, time.time()
        while True:
            t0 = time.time()
            frame, seq, stamp = self.source.latest()
            if frame is None or seq == last_seq:
                time.sleep(0.01)
                continue
            last_seq = seq
            moving = self.motion(frame)
            self.process(frame, stamp)
            period = self.next_period(moving, stamp)
            n += 1
            if n % 10 == 0:
                now = time.time()
                self.stats["analysis_fps"], t_fps = 10 / (now - t_fps), now
            time.sleep(max(0.0, period - (time.time() - t0)))

    def motion(self, frame):
        """True if the picture changed noticeably since the last analysed frame (cheap 160x90 check)."""
        small = cv2.cvtColor(cv2.resize(frame, (160, 90), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
        prev, self._prev_small = self._prev_small, small
        if prev is None:
            return True
        return float((cv2.absdiff(small, prev) > 18).mean()) > float(self.cfg.processing.get("motion_threshold", 0.004))

    def next_period(self, moving, stamp):
        """Seconds until the next analysis: full rate (analysis_fps) while people or movement are in view
        (and for 3 s after), idle_analysis_fps while the corridor is empty and still."""
        p = self.cfg.processing
        if moving or self.scene or self.registry.lost or self._finished:
            self._busy_until = stamp + 3.0
        busy = stamp < self._busy_until
        self.stats["mode"] = "active" if busy else "idle"
        return 1.0 / float(p.analysis_fps if busy else p.get("idle_analysis_fps", p.analysis_fps))

    def process(self, frame, stamp):
        """Analyse one frame taken at time `stamp` (seconds). Also used by tools/replay_check.py."""
        cfg = self.cfg
        coat_period = 1.0 / float(cfg.processing.coat_checks_per_second)
        judge_h = cfg.ppe.min_judge_height
        dt = 0.0 if self._last_stamp is None else min(stamp - self._last_stamp, 1.0)
        self._last_stamp = stamp

        td = time.time()
        dets = self.detector(frame)
        self.stats["detect_ms"] = 0.8 * self.stats["detect_ms"] + 0.2 * (time.time() - td) * 1000
        tracks = self.tracker.update(dets, stamp)

        visible, coat_jobs = [], []
        for tr in tracks:
            b = tr.box
            p = self.registry.get(tr, stamp)
            self.registry.update_guard(p, b, dt)
            if p.is_guard or self.registry.excluded(b):
                continue
            h = b[3] - b[1]
            probs = None
            if h >= judge_h:
                probs = {}
                for k, v in (("cap", self.scorer.cap(frame, b)), ("feet", self.scorer.floaters(frame, b))):
                    if v is not None:
                        probs[k] = v
                if stamp - p.last_coat_t >= coat_period:
                    coat_jobs.append((p, probs))
                    p.last_coat_t = stamp
            visible.append((tr, p, probs, h))

        if coat_jobs:
            tc = time.time()
            crops = [self.scorer.coat_crop(frame, p.box) for p, _ in coat_jobs]
            for (p, probs), c in zip(coat_jobs, self.coat(crops)):
                probs["coat"] = float(c)
            self.stats["coat_ms"] = 0.8 * self.stats["coat_ms"] + 0.2 * (time.time() - tc) * 1000

        scene = []
        for tr, p, probs, h in visible:
            if probs is not None:
                p.observe(probs, h, stamp)
            st, fl = p.verdict()
            scene.append({"pid": p.pid, "box": tr.box.copy(), "vel": tr.vel.copy(), "status": st,
                          "flags": fl, "locked": p.locked is not None})
            if probs and (p.best is None or h > p.best[0]) and not p.locked:
                p.best = (h, frame, scene, stamp)            # frame arrays are never modified in place
        with self.scene_lock:
            self.scene, self.scene_t = scene, stamp

        # decide finished visits after a short grace period (see PersonRegistry.is_guard_handoff)
        for p, gone in self.registry.pop_finished(stamp, self.tracker.alive_ids()):
            if not p.incident_done:
                p.incident_done = True
                self._finished.append((stamp + PersonRegistry.HANDOFF_SECONDS + 1.0, p))
        due = [p for when, p in self._finished if when <= stamp]
        self._finished = [(w, p) for w, p in self._finished if w > stamp]
        for p in due:
            if self.registry.is_guard_handoff(p):
                log.info("Person #%s ignored: walked to / from the guard's chair", p.pid)
            else:
                self._finish_visit(p)

    def _finish_visit(self, p):
        if p.is_guard:
            return
        inc = self.cfg.incidents
        if p.judged_seconds < inc.min_judged_seconds or p.best is None:
            return
        status, flags = p.visit_verdict()
        today = datetime.now().date()
        if today != self._day:
            self.visits_today, self._day = Counter(), today
        self.visits_today[status] += 1
        if status not in inc.alert_on:
            return
        _, frame, scene, shot_t = p.best
        img = frame.copy()
        people = []
        for s in scene:
            st, fl, lk = (status, flags, False) if s["pid"] == p.pid else (s["status"], s["flags"], s["locked"])
            overlay.draw_person(img, s["box"], st, s["pid"], fl, lk)
            if st != "pending":
                people.append((st, s["pid"], lk))
        overlay.draw_alerts(img, sorted(people, key=lambda x: (x[1] != p.pid, RANK[x[0]])))
        ok, why = email_ready(self.cfg)
        close_n = max(sum(1 for o in obs if o[2] >= inc.min_close_height) for obs in p.visit.values())
        if ok and close_n < int(inc.get("email_min_close_observations", 0)):
            ok, why = False, "far_only"                  # judged only at a distance: log, don't email
        iid = self.store.add(shot_t, self.cfg.site.camera_name, p.pid, status, flags, img,
                             "pending" if ok else why)
        log.info("INCIDENT %s: person #%s %s (coat=%d cap=%d floaters=%d) email=%s",
                 iid, p.pid, status.upper(), *flags, "queued" if ok else why)
        if ok:
            self.notifier.notify()

    # ================================================================== rendering
    def _render_loop(self):
        d = self.cfg.dashboard
        period = 1.0 / float(d.stream_fps)
        out_w = int(d.stream_width)
        out_h = round(out_w * FRAME_H / FRAME_W)
        last_seq = -1
        while True:
            if not self.viewers:                     # nobody has the dashboard open: draw nothing
                time.sleep(0.2)
                continue
            t0 = time.time()
            frame, seq, stamp = self.source.latest()
            if frame is None or not self.source.connected:
                img = self._offline_frame()
            elif seq == last_seq:
                time.sleep(0.005)
                continue
            else:
                last_seq = seq
                img = frame.copy()
                with self.scene_lock:
                    scene, st_t = self.scene, self.scene_t
                ahead = float(np.clip(stamp - st_t, 0.0, 0.4))   # extrapolate boxes to this frame
                for s in scene:
                    overlay.draw_person(img, s["box"] + s["vel"] * ahead, s["status"], s["pid"], s["flags"],
                                        s["locked"])
                alerts = [(s["status"], s["pid"], s["locked"]) for s in scene]
                overlay.draw_alerts(img, sorted(alerts, key=lambda a: RANK[a[0]]))
            small = cv2.resize(img, (out_w, out_h), interpolation=cv2.INTER_AREA)
            ok, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 80])
            if ok:
                with self.jpeg_lock:
                    self.jpeg, self.jpeg_seq = buf.tobytes(), self.jpeg_seq + 1
                    self.jpeg_lock.notify_all()
            time.sleep(max(0.0, period - (time.time() - t0)))

    def _offline_frame(self):
        img = np.full((FRAME_H, FRAME_W, 3), (24, 20, 18), np.uint8)
        msg = "Camera offline - reconnecting..."
        cv2.putText(img, msg, (FRAME_W // 2 - 420, FRAME_H // 2), cv2.FONT_HERSHEY_DUPLEX, 1.6,
                    (150, 150, 150), 2, cv2.LINE_AA)
        cv2.putText(img, self.source.status, (FRAME_W // 2 - 420, FRAME_H // 2 + 60), cv2.FONT_HERSHEY_SIMPLEX,
                    0.9, (110, 110, 110), 1, cv2.LINE_AA)
        time.sleep(0.5)
        return img

    # ================================================================== dashboard API
    def add_viewer(self, delta):
        with self._viewers_lock:
            self.viewers = max(0, self.viewers + delta)

    def wait_jpeg(self, after_seq, timeout=2.0):
        with self.jpeg_lock:
            self.jpeg_lock.wait_for(lambda: self.jpeg_seq != after_seq, timeout)
            return self.jpeg, self.jpeg_seq

    def status(self):
        midnight = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        e = self.cfg.email
        ok, why = email_ready(self.cfg)
        return {
            "site": self.cfg.site.company, "camera": self.cfg.site.camera_name,
            "camera_connected": self.source.connected, "camera_status": self.source.status,
            "camera_fps": round(self.source.fps, 1), "input_size": self.source.input_size,
            "analysis_fps": round(self.stats["analysis_fps"], 1),
            "detect_ms": round(self.stats["detect_ms"]), "coat_ms": round(self.stats["coat_ms"]),
            "device": {"detector": self.detector.device, "coat": self.coat.device},
            "analysis_mode": self.stats["mode"], "decoder": self.source.decoder,
            "visits_today": dict(self.visits_today) if self._day == datetime.now().date() else {},
            "incidents_today": self.store.counts_since(midnight),
            "email": {"state": "ready" if ok else why, "mode": e.mode, "last_error": self.notifier.last_error},
            "time": time.time(),
        }
