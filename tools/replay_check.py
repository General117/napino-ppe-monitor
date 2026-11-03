"""Replay a recorded video through the exact live analysis (no dashboard, no emails) and print every
visit verdict and incident. For tuning / regression checks by developers.

  runtime\python\python.exe tools\replay_check.py VIDEO [--start S] [--end S] [--fps N] [--cpu]

Incidents and screenshots go to data_replay\ (not the live data folder).
"""
import argparse
import logging
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("PPE_DATA_DIR", str(ROOT / "data_replay"))   # set PPE_DATA_DIR to keep it elsewhere
sys.path.insert(0, str(ROOT / "app"))

import cv2  # noqa: E402

from ppe_live import config  # noqa: E402
from ppe_live.monitor import Monitor  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--start", type=float, default=0)
    ap.add_argument("--end", type=float, default=None)
    ap.add_argument("--fps", type=float, default=None, help="analysis fps (default: settings.toml)")
    ap.add_argument("--cpu", action="store_true", help="force CPU")
    ap.add_argument("--threads", type=int, default=0)
    ap.add_argument("--model", help="detector model, e.g. yolo26m (default: settings.toml)")
    ap.add_argument("--adaptive", action="store_true",
                    help="use the live idle/active analysis rate (idle_analysis_fps when nothing moves)")
    a = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(name)-9s %(message)s")
    cfg = config.load()
    cfg.email["enabled"], cfg.email["mode"] = True, "file"      # notifier is never started: nothing is sent
    if a.cpu:
        cfg.processing["device"] = "CPU"
    if a.model:
        cfg.processing["detector_model"] = a.model
    if a.threads:
        cfg.processing["cpu_threads"] = a.threads
    fps = a.fps or float(cfg.processing.analysis_fps)
    m = Monitor(cfg)

    cap = cv2.VideoCapture(a.video)
    vfps = cap.get(cv2.CAP_PROP_FPS) or 25
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(a.start * vfps))
    base = time.time() - a.start
    i = int(a.start * vfps)                          # index of the next frame the capture will return
    end = int(a.end * vfps) if a.end else None
    t = a.start                                      # video time of the next analysis
    t0, frame, analysed, idle = time.time(), None, 0, 0
    while True:
        target = int(round(t * vfps))
        if end is not None and target >= end:
            break
        while i < target and cap.grab():
            i += 1
        ok, img = cap.read()
        if not ok:
            break
        i += 1
        frame = img
        if frame.shape[:2] != (config.FRAME_H, config.FRAME_W):
            frame = cv2.resize(frame, (config.FRAME_W, config.FRAME_H))
        moving = m.motion(frame)
        m.process(frame, base + t)
        period = m.next_period(moving, base + t) if a.adaptive else 1.0 / fps
        analysed += 1
        idle += m.stats["mode"] == "idle"
        t += period
    blank = (frame * 0) if frame is not None else None
    for k in range(int(8 * fps)) if blank is not None else []:   # flush: let the last visits finish
        m.process(blank, base + t + k / fps)
    print(f"\nDone in {time.time() - t0:.0f}s. Frames analysed: {analysed} ({idle} at idle rate). "
          f"Visits: {dict(m.visits_today)}")
    for r in reversed(m.store.recent(1000)):
        print(f"  incident {r['id']:3d}  video {r['ts'] - base:7.1f}s  person #{r['person']:<4d} {r['status']:8s}"
              f" coat={r['coat']} cap={r['cap']} floaters={r['floaters']}  email={r['email_status']:9s} {r['image']}")


if __name__ == "__main__":
    main()
