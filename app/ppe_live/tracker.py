"""Lightweight multi-person tracker (ByteTrack-style two-stage IoU matching, constant velocity).

Keeps the package free of PyTorch / ultralytics. Good for the handful of people in one corridor.
"""
import itertools

import numpy as np


def iou_matrix(a, b):
    if not len(a) or not len(b):
        return np.zeros((len(a), len(b)), np.float32)
    x1 = np.maximum(a[:, None, 0], b[None, :, 0])
    y1 = np.maximum(a[:, None, 1], b[None, :, 1])
    x2 = np.minimum(a[:, None, 2], b[None, :, 2])
    y2 = np.minimum(a[:, None, 3], b[None, :, 3])
    inter = (x2 - x1).clip(0) * (y2 - y1).clip(0)
    area = lambda r: (r[:, 2] - r[:, 0]) * (r[:, 3] - r[:, 1])  # noqa: E731
    return inter / (area(a)[:, None] + area(b)[None, :] - inter + 1e-6)


def greedy_match(iou, thr):
    pairs, used_r, used_c = [], set(), set()
    for r, c in sorted(zip(*np.nonzero(iou >= thr)), key=lambda rc: -iou[rc]):
        if r not in used_r and c not in used_c:
            pairs.append((r, c))
            used_r.add(r)
            used_c.add(c)
    return pairs


class Track:
    _ids = itertools.count(1)

    def __init__(self, box, score, t):
        self.id = next(Track._ids)
        self.box = np.array(box, np.float32)
        self.vel = np.zeros(4, np.float32)            # px / second for x1 y1 x2 y2
        self.score = score
        self.t = t                                    # time of last detection
        self.hits = 1

    def predict(self, t):
        return self.box + self.vel * min(t - self.t, 0.5)

    def update(self, box, score, t):
        dt = max(t - self.t, 1e-3)
        box = np.array(box, np.float32)
        self.vel = 0.6 * self.vel + 0.4 * (box - self.box) / dt
        self.box, self.score, self.t = box, score, t
        self.hits += 1


class Tracker:
    def __init__(self, high=0.30, low=0.12, new_track=0.40, max_lost_s=1.5, min_hits=2):
        self.high, self.low, self.new_track = high, low, new_track
        self.max_lost_s, self.min_hits = max_lost_s, min_hits
        self.tracks = []

    def update(self, dets, t):
        """dets: N x 5 (x1 y1 x2 y2 score). Returns confirmed tracks seen in this frame."""
        pred = np.array([tr.predict(t) for tr in self.tracks], np.float32).reshape(-1, 4)
        hi, lo = dets[dets[:, 4] >= self.high], dets[(dets[:, 4] < self.high) & (dets[:, 4] >= self.low)]
        seen = []
        m1 = greedy_match(iou_matrix(pred, hi[:, :4]), 0.25)
        for r, c in m1:
            self.tracks[r].update(hi[c, :4], hi[c, 4], t)
            seen.append(self.tracks[r])
        left = [i for i in range(len(self.tracks)) if i not in {r for r, _ in m1}]
        m2 = greedy_match(iou_matrix(pred[left], lo[:, :4]), 0.4)
        for r, c in m2:
            tr = self.tracks[left[r]]
            tr.update(lo[c, :4], lo[c, 4], t)
            seen.append(tr)
        used = {c for _, c in m1}
        # the detector occasionally returns a second, overlapping box for the same person:
        # never start a new track on top of an existing one
        cover = iou_matrix(hi[:, :4], np.array([tr.box for tr in self.tracks], np.float32).reshape(-1, 4))
        for c in range(len(hi)):
            if c not in used and hi[c, 4] >= self.new_track and not (cover.shape[1] and cover[c].max() > 0.6):
                tr = Track(hi[c, :4], hi[c, 4], t)
                self.tracks.append(tr)
                seen.append(tr)
        self.tracks = [tr for tr in self.tracks if t - tr.t <= self.max_lost_s]
        return [tr for tr in seen if tr.hits >= self.min_hits]

    def alive_ids(self):
        return {tr.id for tr in self.tracks}
