"""Logging to console + rotating file logs/ppe_monitor.log."""
import logging
import logging.handlers

from .config import LOG_DIR


def setup(level=logging.INFO):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s  %(levelname)-7s %(name)-10s %(message)s", "%Y-%m-%d %H:%M:%S")
    root = logging.getLogger()
    root.setLevel(level)
    if root.handlers:
        return
    fh = logging.handlers.RotatingFileHandler(LOG_DIR / "ppe_monitor.log", maxBytes=5_000_000,
                                              backupCount=5, encoding="utf-8")
    fh.setFormatter(fmt)
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    root.addHandler(fh)
    root.addHandler(ch)
    for noisy in ("waitress", "werkzeug"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
