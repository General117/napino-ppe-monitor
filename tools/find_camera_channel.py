"""Tries NVR channels 1..N (low-resolution sub-stream, using the dashboard sign-in) and saves one
snapshot per working channel to data\\channel_scan\\ to identify SMT Gallery 2.

  FIND_CAMERA_CHANNEL.bat [max_channel]      (default 32)"""
import os
import sys

import cv2

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # embedded Python does not add it
from _common import banner, config, fail, info, ok, tcp_ok  # noqa: E402
from ppe_live.video_source import probe_rtsp  # noqa: E402


def main():
    banner("Find the camera channel on the NVR")
    cfg = config.load()
    c, nvr = cfg.camera, cfg.nvr()
    if not nvr:
        fail("Not signed in to the NVR - sign in on the dashboard (Settings tab) first.")
        return 1
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 32
    up, err = tcp_ok(nvr["ip"], c.rtsp_port)
    if not up:
        fail(f"Cannot reach {nvr['ip']}:{c.rtsp_port} ({err})")
        return 1
    out = config.DATA_DIR / "channel_scan"
    out.mkdir(parents=True, exist_ok=True)
    found = []
    for ch in range(1, n + 1):
        got, frame = probe_rtsp(cfg.rtsp_url(channel=ch, stream="sub"), c.rtsp_transport)
        if got:
            p = out / f"channel_{ch:02d}.jpg"
            cv2.imwrite(str(p), frame)
            ok(f"channel {ch:2d}: video OK  -> {p.name}")
            found.append(ch)
        else:
            print(f"         channel {ch:2d}: no video")
    if not found:
        fail("No channel returned video - check the NVR sign-in on the dashboard.")
        return 1
    info(f"Snapshots in {out}. Enter the SMT Gallery 2 channel in the dashboard Settings -> Camera (NVR).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
