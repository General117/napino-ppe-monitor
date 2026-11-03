"""Camera connection diagnosis: checks the RTSP port and tries the usual Hikvision channel numberings,
showing the NVR's own answer (e.g. 401 Unauthorized, 404 Not Found). The password is asked here and is
NOT stored.

  DIAGNOSE_CAMERA.bat [channel]        (default: channel from the dashboard / settings, e.g. 13)
"""
import getpass
import os
import sys

os.environ["OPENCV_FFMPEG_LOGLEVEL"] = "16"           # show FFmpeg errors (401 / 404 / timeouts)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # embedded Python does not add it
from _common import banner, config, fail, info, ok, tcp_ok  # noqa: E402

import cv2  # noqa: E402
from urllib.parse import quote  # noqa: E402


def try_url(url, transport):
    os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = f"rtsp_transport;{transport}"
    cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG,
                           [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 8000, cv2.CAP_PROP_READ_TIMEOUT_MSEC, 8000])
    try:
        if not cap.isOpened():
            return None
        got, frame = cap.read()
        return frame.shape if got else None
    finally:
        cap.release()


def main():
    banner("Camera connection diagnosis")
    cfg = config.load()
    c = cfg.camera
    ip = cfg.prefs.get("nvr_ip", c.nvr_ip)
    user = cfg.prefs.get("nvr_username", c.username)
    ch = int(sys.argv[1]) if len(sys.argv) > 1 else int(cfg.prefs.get("nvr_channel", c.channel))
    port = int(c.rtsp_port)
    info(f"NVR {ip}   user {user}   channel {ch}   RTSP port {port}")

    for p in (80, port, 8000):
        up, err = tcp_ok(ip, p)
        (ok if up else fail)(f"port {p}: {'open' if up else 'NOT reachable (' + err + ')'}")
    if not tcp_ok(ip, port)[0]:
        info("RTSP port is blocked or RTSP is off on the NVR - nothing else can work until it is open.")
        return 1

    pw = getpass.getpass(f"  NVR password for {user} (not shown, not stored): ")
    creds = [("password %-encoded", f"{quote(user, safe='')}:{quote(pw, safe='')}@{ip}:{port}")]
    if quote(pw, safe="") != pw:
        creds.append(("password as typed", f"{quote(user, safe='')}:{pw}@{ip}:{port}"))
    # few attempts only: Hikvision locks the login for ~30 min after repeated failures
    candidates = [(f"channel {ch} main stream", f"/Streaming/Channels/{ch}01")]
    found = None
    for how, cred in creds:
        for label, path in candidates:
            transport = "tcp"
            print(f"         trying {label}, {how} ...", flush=True)
            shape = try_url(f"rtsp://{cred}{path}", transport)
            if shape:
                ok(f"VIDEO OK: {label}, {how}  ({shape[1]}x{shape[0]})   path {path}")
                found = found or (f"{label}, {how}", path, transport)
                break
        if found:
            break
    print()
    if not found:
        fail("No stream opened. Look at the FFmpeg lines above:")
        info("'401 Unauthorized' = wrong password or the user has no remote live-view right")
        info("'404 Not Found'    = wrong channel number")
        info("no message/timeout = login locked after failed attempts (wait 30-60 min) or RTSP restricted")
        return 1
    label, path, transport = found
    info(f"Use: {label}. Send this result to the developer if it is not 'channel {ch} main stream'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
