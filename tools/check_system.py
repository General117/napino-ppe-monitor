"""System check: Python runtime, Intel GPU / CPU, model speed on this PC, fonts, disk, settings."""
import os
import platform
import shutil
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # embedded Python does not add it
from _common import banner, config, fail, info, ok  # noqa: E402


def decode_check(cfg):
    """CPU cost of decoding the camera stream with and without the graphics chip -> hw_decode advice."""
    import cv2
    from ppe_live.video_source import HW_NAMES, open_rtsp
    url = cfg.rtsp_url()
    if not url:
        info("video decoding: sign in to the camera on the dashboard to measure (hw_decode advice)")
        return
    cv2.setNumThreads(int(cfg.processing.get("opencv_threads", 2)))
    cost = {}
    for hw in (False, True):
        cap = open_rtsp(url, cfg.camera.rtsp_transport, hw_decode=hw)
        if not cap.isOpened():
            continue
        used = HW_NAMES.get(int(cap.get(cv2.CAP_PROP_HW_ACCELERATION)), "graphics chip")
        cap.read()
        t, n = time.process_time(), 0
        for _ in range(100):
            n += bool(cap.read()[0])
        cap.release()
        if n:
            cost[hw] = (time.process_time() - t) / n * 1000
            ok(f"video decoding on {used}: {cost[hw]:.1f} ms CPU per frame")
    if len(cost) == 2:
        best = cost[True] < cost[False] * 0.85
        info(f"recommended: hw_decode = {'true' if best else 'false'} in config\\settings.toml "
             f"(currently {str(cfg.processing.get('hw_decode', False)).lower()})")


def main():
    banner("System check")
    cfg = config.load()
    ok(f"Python {platform.python_version()} on {platform.platform()}")
    import openvino as ov
    core = ov.Core()
    devs = core.available_devices
    ok(f"OpenVINO {ov.__version__}; devices: {', '.join(devs)}")
    for d in devs:
        try:
            info(f"{d}: {core.get_property(d, 'FULL_DEVICE_NAME')}")
        except Exception:                                     # noqa: BLE001
            pass
    if "GPU" not in devs:
        info("No Intel GPU visible - the monitor will run on CPU (update the Intel graphics driver to enable it).")

    from ppe_live.inference import CoatClassifier, PersonDetector
    det, coat = PersonDetector(cfg), CoatClassifier(cfg)
    frame = (np.random.rand(config.FRAME_H, config.FRAME_W, 3) * 255).astype(np.uint8)
    crop = frame[200:500, 800:950]
    for name, fn in (("person detector", lambda: det(frame)), ("coat classifier", lambda: coat([crop]))):
        fn()
        t = time.time()
        for _ in range(10):
            fn()
        ok(f"{name}: {(time.time() - t) * 100:.0f} ms per run on {det.device if 'person' in name else coat.device}")
    det_ms = 0
    t = time.time()
    for _ in range(10):
        det(frame)
    det_ms = (time.time() - t) * 100
    cap = 1000 / max(det_ms, 1)
    (ok if cap >= cfg.processing.analysis_fps else fail)(
        f"capacity ~{cap:.0f} analyses/s (configured analysis_fps = {cfg.processing.analysis_fps})")
    if cap < cfg.processing.analysis_fps:
        info("Lower [processing] analysis_fps in settings.toml to about " + str(max(2, int(cap * 0.8))))

    fonts = [Path("C:/Windows/Fonts") / f for f in ("segoeui.ttf", "seguisb.ttf", "segmdl2.ttf")]
    missing = [f.name for f in fonts if not f.exists()]
    (fail if missing else ok)("fonts " + ("missing: " + ", ".join(missing) if missing else "OK (Segoe UI)"))
    for label, folder in (("program", config.ROOT), ("screenshots", config.INCIDENT_DIR),
                          ("data", config.DATA_DIR), ("logs", config.LOG_DIR)):
        ok(f"{label:<12} {folder}")
    free = shutil.disk_usage(config.INCIDENT_DIR).free / 1e9
    (ok if free > 5 else fail)(f"free disk space for screenshots {free:.1f} GB")
    for kind, label in (("nvr", "camera (NVR)"), ("smtp", "sender email")):
        i = cfg.creds.info(kind)
        (ok if i["signed_in"] else fail)(f"{label} sign-in: " + ("signed in" if i["signed_in"] else
                                                                  "not signed in - use the dashboard Settings tab"))
    decode_check(cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
