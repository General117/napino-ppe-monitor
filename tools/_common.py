"""Shared helpers for the diagnostic tools (run through the .bat files in this folder)."""
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from ppe_live import config  # noqa: E402,F401


def tcp_ok(host, port, timeout=4):
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True, ""
    except OSError as e:
        return False, str(e)


def banner(title):
    print("=" * 78)
    print(" " + title)
    print("=" * 78)


def ok(msg):
    print("  [ OK ] " + msg)


def fail(msg):
    print("  [FAIL] " + msg)


def info(msg):
    print("         " + msg)
