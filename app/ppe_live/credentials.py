"""Sign-in details entered on the dashboard (NVR and email sender).

Nothing is written in plain text: the record is encrypted with Windows DPAPI for the current Windows
user, so the file is useless on any other PC or user account. Each sign-in expires after
`credential_days` (settings.toml -> [security]); after that it is deleted and must be entered again.
"""
import ctypes
import json
import logging
import threading
import time
from ctypes import wintypes

from .config import DATA_DIR

log = logging.getLogger("credentials")
STORE = DATA_DIR / "access.dat"
_ENTROPY = b"napino-ppe-monitor/v1"


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


_crypt32 = ctypes.windll.crypt32
_kernel32 = ctypes.windll.kernel32
_kernel32.LocalFree.argtypes = [ctypes.c_void_p]


def _blob(data):
    buf = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def _dpapi(data, encrypt):
    src, _keep1 = _blob(data)
    ent, _keep2 = _blob(_ENTROPY)
    out = _Blob()
    fn = _crypt32.CryptProtectData if encrypt else _crypt32.CryptUnprotectData
    desc = "PPE Monitor" if encrypt else None
    if not fn(ctypes.byref(src), desc, ctypes.byref(ent), None, None, 0x01, ctypes.byref(out)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        _kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


class CredentialStore:
    """kind = "nvr" (fields: ip, port, username, password, channel) or "smtp" (address, password)."""

    def __init__(self, days):
        self.days = float(days)
        self.lock = threading.Lock()

    def _read(self):
        if not STORE.exists():
            return {}
        try:
            return json.loads(_dpapi(STORE.read_bytes(), encrypt=False))
        except Exception:                                 # noqa: BLE001 - other user / corrupted
            log.warning("Stored sign-in could not be read on this account - please sign in again")
            return {}

    def _write(self, data):
        STORE.parent.mkdir(parents=True, exist_ok=True)
        if data:
            STORE.write_bytes(_dpapi(json.dumps(data).encode(), encrypt=True))
        elif STORE.exists():
            STORE.unlink()

    def _expired(self, rec):
        return self.days > 0 and time.time() - rec.get("saved_at", 0) > self.days * 86400

    def get(self, kind):
        """The record (with password) or None if never entered / expired."""
        with self.lock:
            data = self._read()
            rec = data.get(kind)
            if rec and self._expired(rec):
                log.warning("%s sign-in expired after %s days - removed", kind.upper(), self.days)
                del data[kind]
                self._write(data)
                return None
            return rec

    def set(self, kind, **fields):
        with self.lock:
            data = self._read()
            data[kind] = {**fields, "saved_at": time.time()}
            self._write(data)
        log.info("%s sign-in saved (encrypted, valid %s days)", kind.upper(), self.days)

    def clear(self, kind):
        with self.lock:
            data = self._read()
            if data.pop(kind, None) is not None:
                self._write(data)
                log.info("%s signed out", kind.upper())

    def info(self, kind):
        """Public view for the dashboard: never includes the password."""
        rec = self.get(kind)
        if not rec:
            return {"signed_in": False}
        pub = {k: v for k, v in rec.items() if k != "password"}
        pub["signed_in"] = True
        pub["expires_at"] = rec["saved_at"] + self.days * 86400 if self.days > 0 else None
        return pub
