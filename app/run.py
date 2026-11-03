"""Start the PPE Compliance Monitor:  runtime\\python\\python.exe app\\run.py [--no-browser] [--config FILE]

Runs until Ctrl+C is pressed in its window (or the window is closed).
"""
import argparse
import ctypes
import logging
import os
import signal
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))      # embedded Python does not add it itself

from ppe_live import __version__, config, log as logsetup   # noqa: E402

PRIORITY = {"low": 0x40, "below_normal": 0x4000, "normal": 0x20}   # Windows priority classes


def port_in_use(host, port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1" if host == "0.0.0.0" else host, int(port))) == 0


def be_a_good_neighbour(cfg, log):
    """Lower the process priority so the PC stays responsive, and cap OpenCV's internal threads."""
    level = str(cfg.processing.get("priority", "below_normal")).lower()
    if sys.platform == "win32" and level in PRIORITY:
        k32 = ctypes.windll.kernel32
        k32.SetPriorityClass(k32.GetCurrentProcess(), PRIORITY[level])
    import cv2
    cv2.setNumThreads(int(cfg.processing.get("opencv_threads", 2)))
    log.info("Process priority %s, OpenCV threads %s", level, cv2.getNumThreads())


def main():
    ap = argparse.ArgumentParser(description="Napino Auto PPE Compliance Monitor")
    ap.add_argument("--config", help="override file: its keys replace those in config/settings.toml")
    ap.add_argument("--no-browser", action="store_true", help="do not open the dashboard in a browser")
    args = ap.parse_args()

    logsetup.setup()
    log = logging.getLogger("main")
    cfg = config.load(args.config)
    log.info("PPE Compliance Monitor %s starting - %s / %s", __version__, cfg.site.company, cfg.site.camera_name)
    log.info("Folders: program %s | screenshots %s | data %s | logs %s",
             config.ROOT, config.INCIDENT_DIR, config.DATA_DIR, config.LOG_DIR)

    d = cfg.dashboard
    url = f"http://localhost:{d.port}"
    if port_in_use(d.host, d.port):
        log.error("Port %s is already in use - the monitor is probably already running. Open %s", d.port, url)
        return 3

    be_a_good_neighbour(cfg, log)
    from ppe_live.dashboard import serve
    from ppe_live.monitor import Monitor
    try:
        monitor = Monitor(cfg).start()
    except Exception:
        log.exception("Start-up failed")
        return 2

    # Ctrl+C (and Ctrl+Break) in the console window stop the monitor cleanly
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, signal.default_int_handler)
    if d.open_browser_on_start and not args.no_browser:
        threading.Thread(target=lambda: (time.sleep(3), webbrowser.open(url)), daemon=True).start()
    log.info("Running - dashboard %s - press Ctrl+C in this window to stop", url)
    try:
        serve(monitor, d.host, d.port)
    except KeyboardInterrupt:
        pass
    log.info("Stopped")
    logging.shutdown()
    os._exit(0)                                 # end camera / AI threads immediately


if __name__ == "__main__":
    sys.exit(main())
