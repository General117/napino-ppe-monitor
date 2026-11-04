"""PPE reasoning for one camera: exclusions, per-item scoring, smoothing, lock, visit verdict.

Items
  coat     - plain coat of any block colour (CoatClassifier probability)
  cap      - white cap: top-centre of the head is bright and colourless (hair = dark,
             blue hairnets / light-blue caps = saturated, so neither counts)
  floaters - light-blue floaters: blue/violet pixels in the bottom 10 % of the person box
Status: 3 items = "full", 2 = "partial", 0-1 = "none"; "pending" while evidence is insufficient.
"""
from collections import deque

import cv2
import numpy as np

from .config import FRAME_H

ITEMS = ("coat", "cap", "feet")            # "feet" = floaters
ITEM_LABELS = {"coat": "ESD Apron", "cap": "ESD Cap", "feet": "ESD Slippers"}   # names shown to users


def status_of(flags):
    k = sum(flags)
    return "full" if k == 3 else "partial" if k == 2 else "none"


def _sig(x):
    return 1.0 / (1.0 + np.exp(-x))


class Scorer:
    """Pixel rules for cap and floaters (coat comes from the CLIP classifier)."""

    def __init__(self, cfg):
        self.p = cfg.ppe

    def cap(self, frame, b):
        x1, y1, x2, y2 = [int(v) for v in b]
        if y1 <= 6:
            return None                                  # head cut off by the top of the frame
        h, w = y2 - y1, x2 - x1
        patch = frame[y1 + int(.02 * h):y1 + int(.08 * h) + 1, x1 + int(.35 * w):x1 + int(.65 * w) + 1]
        if patch.size == 0:
            return None
        hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV).reshape(-1, 3)
        s, v = np.median(hsv[:, 1]), np.median(hsv[:, 2])
        return float(_sig((self.p.cap_max_saturation - s) / 2.5) * _sig((v - self.p.cap_min_brightness) / 6))

    def floaters(self, frame, b):
        x1, y1, x2, y2 = [int(v) for v in b]
        if y2 >= FRAME_H - 6:
            return None                                  # feet below the frame edge
        h = y2 - y1
        patch = frame[y1 + int(.90 * h):y2, x1:x2]
        if patch.size == 0:
            return None
        hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV).reshape(-1, 3).astype(np.int16)
        p = self.p
        frac = ((hsv[:, 0] >= p.floater_hue_min) & (hsv[:, 0] <= p.floater_hue_max)
                & (hsv[:, 1] >= p.floater_min_saturation) & (hsv[:, 2] >= p.floater_min_brightness)).mean()
        lo = p.floater_min_fraction * 0.375                     # soft ramp around the threshold
        return float(np.clip((frac - lo) / (p.floater_min_fraction * 1.25), 0, 1))

    @staticmethod
    def coat_crop(frame, b):
        x1, y1, x2, y2 = [int(v) for v in b]
        h = y2 - y1
        return frame[y1 + int(.12 * h):y1 + int(.78 * h), x1:x2]


def _centre(b):
    return (b[0] + b[2]) / 2, (b[1] + b[3]) / 2


def _inside(b, z):
    cx, cy = _centre(b)
    return z[0] <= cx <= z[2] and z[1] <= cy <= z[3]


class Person:
    """State of one tracked person during one visit."""

    def __init__(self, pid, track_id, t, cfg):
        self.pid, self.track_id = pid, track_id
        self.first_seen = self.last_seen = t
        self.cfg = cfg
        self.box = None
        self.first_box = None
        self.hist = {a: deque() for a in ITEMS}          # (t, p, weight) inside smoothing window
        self.visit = {a: [] for a in ITEMS}              # (p, weight, height) whole visit
        self.mean = {a: None for a in ITEMS}
        self.flag = {a: False for a in ITEMS}
        self.first_judged = None
        self.n_judged = 0
        self.judged_seconds = 0.0
        self._last_judged_t = None
        self.locked = None
        self._streak = 0.0
        self._last_verdict = None
        self._last_t = t
        self.last_coat_t = -1e9
        self.guard_seconds = 0.0
        self.is_guard = False
        self.best = None                                 # (height, t) of the best judged view
        self.incident_done = False

    # ---------------------------------------------------------------- evidence
    def observe(self, probs, h, t):
        dt = max(0.0, t - self._last_t)
        self._last_t = t
        if self.locked:
            return
        if probs:
            if self.first_judged is None:
                self.first_judged = t
            if self._last_judged_t is not None:
                self.judged_seconds += min(t - self._last_judged_t, 0.5)
            self._last_judged_t = t
            self.n_judged += 1
        w = min(1.0, h / 350.0)
        win = float(self.cfg.ppe.smoothing_seconds)
        for a in ITEMS:
            q = self.hist[a]
            if a in probs:
                q.append((t, probs[a], w))
                self.visit[a].append((probs[a], w, h))
            while q and t - q[0][0] > win:
                q.popleft()
            if q:
                ps = np.array([(p, ww) for _, p, ww in q])
                m = float((ps[:, 0] * ps[:, 1]).sum() / ps[:, 1].sum())
                self.mean[a] = m
                self.flag[a] = m > (0.40 if self.flag[a] else 0.55)       # hysteresis
        if probs and self.cfg.lock.enabled:
            self._update_lock(h, dt)

    def _update_lock(self, h, dt):
        lk = self.cfg.lock
        v = self.verdict()
        confident = all(len(self.hist[a]) >= 3 and self.mean[a] is not None
                        and abs(self.mean[a] - 0.5) >= 0.5 - lk.margin for a in ITEMS)
        if v[0] != "full" or v != self._last_verdict:
            self._streak = 0.0
        elif h >= lk.min_height and confident:
            self._streak += dt
        else:
            self._streak = max(0.0, self._streak - 3 * dt)
        self._last_verdict = v
        if self._streak >= lk.confirm_seconds:
            self.locked = v

    # ---------------------------------------------------------------- verdicts
    def verdict(self):
        """Live verdict shown on screen: (status, flags or None)."""
        if self.locked:
            return self.locked
        if self.first_judged is None or self.n_judged < 3 or \
                self._last_t - self.first_judged < self.cfg.ppe.min_verdict_seconds:
            return "pending", None
        fl = tuple(self.flag[a] for a in ITEMS)
        return status_of(fl), fl

    def visit_verdict(self):
        """Verdict for the whole visit (used for incidents): close-range evidence preferred."""
        if self.locked:
            return self.locked
        close_h = self.cfg.incidents.min_close_height
        flags = []
        for a in ITEMS:
            obs = self.visit[a]
            close = [o for o in obs if o[2] >= close_h]
            use = close if len(close) >= 3 else obs
            if not use:
                flags.append(False)                      # never visible => not confirmed
                continue
            p = np.array([(o[0], o[1]) for o in use])
            flags.append(float((p[:, 0] * p[:, 1]).sum() / p[:, 1].sum()) > 0.5)
        fl = tuple(flags)
        return status_of(fl), fl


class PersonRegistry:
    """Maps tracker ids to Person objects; applies guard / reflection exclusions."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.people = {}
        self.next_pid = 1
        z = cfg.zones
        self.guard_zone, self.seat_zone = z.guard_zone, z.seat_zone
        self.reflection_x = z.reflection_x
        self.guard_events = []          # (time, box): guard appeared (seated) or left the chair
        self.lost = []                  # recently lost people that a new track may continue (stitching)

    # The tracker usually gives the guard a new id when they sit down or stand up (the box shape
    # changes a lot). These events let a walking track that ends/starts at the chair inherit "guard".
    HANDOFF_SECONDS, HANDOFF_PX = 3.0, 260

    def _near_guard_event(self, t0, t1, box):
        cx, cy = _centre(box)
        return any(t0 <= t <= t1 and abs(_centre(b)[0] - cx) < self.HANDOFF_PX and abs(_centre(b)[1] - cy) < self.HANDOFF_PX
                   for t, b in self.guard_events)

    def is_guard_handoff(self, p):
        """True if a guard sat down where, and around when, this (walking) person's track ended."""
        return p.box is not None and self._near_guard_event(p.last_seen - self.HANDOFF_SECONDS,
                                                            p.last_seen + self.HANDOFF_SECONDS, p.box)

    def excluded(self, b):
        h = b[3] - b[1]
        cx, _ = _centre(b)
        return (h < self.cfg.ppe.min_show_height
                or (_inside(b, self.seat_zone) and h <= self.cfg.zones.guard_seated_max_height - 5)
                or self.reflection_x[0] <= cx <= self.reflection_x[1])

    # A tracker can split one walk into several ids (occlusion at the turnstiles, missed detections).
    # A new track that starts where a person was just lost, with a similar size, is the same person.
    STITCH_SECONDS = 2.0

    def _stitch(self, box, t):
        h = box[3] - box[1]
        cx, cy = _centre(box)
        best, best_d = None, None
        for i, (p, t_lost) in enumerate(self.lost):
            ph = p.box[3] - p.box[1]
            px, py = _centre(p.box)
            d = ((px - cx) ** 2 + (py - cy) ** 2) ** 0.5
            if t - t_lost <= self.STITCH_SECONDS and 0.65 <= h / ph <= 1.55 and d <= 0.6 * max(h, ph)                     and (best_d is None or d < best_d):
                best, best_d = i, d
        return None if best is None else self.lost.pop(best)[0]

    def get(self, track, t):
        p = self.people.get(track.id)
        if p is None:
            p = self._stitch(track.box, t)
            if p is not None:
                p.track_id = track.id
            else:
                p = Person(self.next_pid, track.id, t, self.cfg)
                self.next_pid += 1
                p.first_box = track.box.copy()
                # guard just stood up from the chair -> this new track is the guard
                if self._near_guard_event(t - self.HANDOFF_SECONDS, t, p.first_box):
                    p.is_guard = True
            self.people[track.id] = p
        p.last_seen, p.box = t, track.box.copy()
        return p

    def update_guard(self, p, b, dt):
        """A person seated in the guard zone long enough is the guard: hidden from then on."""
        if _inside(b, self.guard_zone) and (b[3] - b[1]) <= self.cfg.zones.guard_seated_max_height:
            p.guard_seconds += dt
            if p.guard_seconds >= self.cfg.zones.guard_confirm_seconds and not p.is_guard:
                p.is_guard = True
                self.guard_events.append((p.first_seen, p.first_box))

    def pop_finished(self, t, alive_ids):
        """People whose visit is over -> [(person, gone)].

        A lost person waits STITCH_SECONDS for a continuation before the visit counts as finished.
        People still in view longer than max_observe_seconds are returned once with gone=False."""
        inc = self.cfg.incidents
        done = []
        for tid, p in list(self.people.items()):
            if tid not in alive_ids or t - p.last_seen > inc.lost_after_seconds:
                del self.people[tid]
                if p.is_guard:
                    self.guard_events.append((p.last_seen, p.box))
                self.lost.append((p, t))
            elif t - p.first_seen > inc.max_observe_seconds and not p.incident_done:
                done.append((p, False))
        keep = []
        for p, t_lost in self.lost:
            (keep if t - t_lost <= self.STITCH_SECONDS else done).append((p, t_lost) if t - t_lost <= self.STITCH_SECONDS else (p, True))
        self.lost = keep
        self.guard_events = [e for e in self.guard_events if t - e[0] < 60]
        return done
