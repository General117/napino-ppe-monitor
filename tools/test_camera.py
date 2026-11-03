"""Checks the NVR connection (using the dashboard sign-in) and saves a snapshot with the configured
zones drawn on it, to verify the guard-chair / window zones match the camera view.
Output: data\\camera_test.jpg"""
import os
import sys

import cv2

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # embedded Python does not add it
from _common import banner, config, fail, info, ok, tcp_ok  # noqa: E402
from ppe_live.video_source import probe_rtsp  # noqa: E402


def main():
    banner("Camera / NVR connection test")
    cfg = config.load()
    c, n = cfg.camera, cfg.nvr()
    if not n:
        fail("Not signed in to the NVR - sign in on the dashboard (Settings tab) first.")
        return 1
    info(f"NVR {n['ip']}:{c.rtsp_port}   channel {n.get('channel', c.channel)}   user {n['username']}")
    up, err = tcp_ok(n["ip"], c.rtsp_port)
    if not up:
        fail(f"Cannot reach {n['ip']}:{c.rtsp_port} ({err})")
        info("- Is this PC on the factory network (same as the NVR)?")
        info("- Is RTSP enabled on the NVR (Configuration > Network > Advanced > Integration / RTSP port 554)?")
        return 1
    ok(f"NVR reachable on port {c.rtsp_port}")
    got, frame = probe_rtsp(cfg.rtsp_url(), c.rtsp_transport)
    if not got:
        fail("Stream did not open - check the sign-in, channel and the user's live-view permission.")
        return 1
    h, w = frame.shape[:2]
    ok(f"Receiving video: {w} x {h}")
    if (w, h) != (config.FRAME_W, config.FRAME_H):
        frame = cv2.resize(frame, (config.FRAME_W, config.FRAME_H))
    z = cfg.zones
    x1, y1, x2, y2 = z.guard_zone
    cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 200, 255), 2)
    cv2.putText(frame, "guard zone", (x1, y1 - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 200, 255), 2)
    x1, y1, x2, y2 = z.seat_zone
    cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 120, 255), 2)
    rx1, rx2 = z.reflection_x
    cv2.rectangle(frame, (rx1, 0), (rx2, config.FRAME_H - 1), (255, 120, 0), 2)
    cv2.putText(frame, "reflection (ignored)", (rx1 + 6, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 120, 0), 2)
    out = config.DATA_DIR / "camera_test.jpg"
    cv2.imwrite(str(out), frame)
    ok(f"Snapshot saved: {out}")
    info("Check the orange box sits on the guard's chair and the blue band covers the glass window")
    info("on the right wall. If not, adjust [zones] in config\\settings.toml.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
