"""Settings loading and folder layout.

  <program>\config\settings.toml   technical settings (thresholds, zones, defaults) - edited by developers
  <data>\preferences.json          choices made on the dashboard (alert recipients)
  <data>\access.dat                NVR / email sign-in, encrypted, expiring (see credentials.py)
  <data>\incidents.db              incident log
  <screenshots>\YYYY-MM-DD\*.jpg   incident screenshots
  <logs>\ppe_monitor.log           technical log
The data / screenshots / logs folders are set in settings.toml -> [storage] and by default sit next to
the program folder, so the program can be replaced by a new version without touching any of them.
No password is ever stored in a configuration file.
"""
import json
import os
import re
import threading
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[2]          # ...\live_environment (the program folder)
CONFIG_DIR = ROOT / "config"
MODELS_DIR = ROOT / "models"


def _storage_dir(key, default):
    """Folder from settings.toml [storage]; relative paths are relative to the program folder."""
    try:
        with open(CONFIG_DIR / "settings.toml", "rb") as f:
            value = tomllib.load(f).get("storage", {}).get(key, default)
    except (OSError, tomllib.TOMLDecodeError):
        value = default
    p = Path(value)
    return p if p.is_absolute() else (ROOT / p).resolve()


_test_dir = os.environ.get("PPE_DATA_DIR")          # set by tools/replay_check.py and tests
DATA_DIR = Path(_test_dir) if _test_dir else _storage_dir("data_dir", "../data")
INCIDENT_DIR = DATA_DIR / "screenshots" if _test_dir else _storage_dir("screenshots_dir", "../screenshots")
LOG_DIR = _storage_dir("logs_dir", "../logs")
OUTBOX_DIR = DATA_DIR / "outbox"
DB_PATH = DATA_DIR / "incidents.db"
PREFS_PATH = DATA_DIR / "preferences.json"

FRAME_W, FRAME_H = 1920, 1080                         # every frame is normalised to this canvas
EMAIL_RE = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$")


class Section(dict):
    """dict with attribute access: cfg.camera.channel"""
    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError as e:
            raise AttributeError(f"missing setting '{k}'") from e


class Prefs:
    """Small JSON file of dashboard choices."""

    def __init__(self):
        self.lock = threading.Lock()

    def get(self, key, default=None):
        try:
            return json.loads(PREFS_PATH.read_text(encoding="utf-8")).get(key, default)
        except (OSError, ValueError):
            return default

    def set(self, key, value):
        with self.lock:
            try:
                data = json.loads(PREFS_PATH.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
            data[key] = value
            PREFS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


@dataclass
class Config:
    site: Section
    camera: Section
    processing: Section
    ppe: Section
    lock: Section
    zones: Section
    incidents: Section
    email: Section
    dashboard: Section
    security: Section
    prefs: Prefs = field(default_factory=Prefs)
    creds: object = None                               # CredentialStore, attached in load()

    # ------------------------------------------------------------- alert recipients
    def recipients(self):
        return list(self.prefs.get("recipients", self.email.recipients))

    # ------------------------------------------------------------- sign-in
    def nvr(self):
        """NVR sign-in (ip, port, username, password, channel) or None."""
        return self.creds.get("nvr") if self.creds else None

    def smtp(self):
        """Email sender sign-in (address, password) or None."""
        return self.creds.get("smtp") if self.creds else None

    def rtsp_url(self, nvr=None, channel=None, stream=None, redact=False):
        n = nvr or self.nvr()
        if not n:
            return None
        ch = int(channel if channel is not None else n.get("channel", self.camera.channel))
        sub = 1 if (stream or self.camera.stream) == "main" else 2
        # some NVR / FFmpeg combinations only accept the password as typed (not %-encoded)
        pw = "****" if redact else (n["password"] if n.get("raw_auth") else quote(n["password"], safe=""))
        return (f"rtsp://{quote(n['username'], safe='')}:{pw}@{n['ip']}:{int(n.get('port', self.camera.rtsp_port))}"
                f"/Streaming/Channels/{ch}0{sub}")


def load(override=None) -> Config:
    """settings.toml, optionally with an override file whose sections/keys replace the defaults."""
    from .credentials import CredentialStore
    with open(CONFIG_DIR / "settings.toml", "rb") as f:
        raw = tomllib.load(f)
    if override:
        with open(override, "rb") as f:
            for sec, vals in tomllib.load(f).items():
                raw.setdefault(sec, {}).update(vals)
    sections = {k: Section(raw.get(k, {})) for k in
                ("site", "camera", "processing", "ppe", "lock", "zones", "incidents", "email", "dashboard",
                 "security")}
    fp = sections["camera"].get("file_path")
    if fp and not Path(fp).is_absolute():                 # relative to the live_environment folder
        sections["camera"]["file_path"] = str((ROOT / fp).resolve())
    for d in (DATA_DIR, LOG_DIR, INCIDENT_DIR, OUTBOX_DIR):
        d.mkdir(parents=True, exist_ok=True)
    cfg = Config(**sections)
    cfg.creds = CredentialStore(sections["security"].get("credential_days", 30))
    return cfg
