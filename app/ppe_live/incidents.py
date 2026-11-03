"""Incident store (SQLite + JPEG screenshots) with automatic retention clean-up.

Layout:  <data>/incidents.db                    - one row per incident (+ email delivery state)
         <screenshots>/YYYY-MM-DD/<file>.jpg    - annotated screenshot of the moment
"""
import logging
import shutil
import sqlite3
import threading
import time
from datetime import datetime, timedelta

import cv2

from .config import DB_PATH, INCIDENT_DIR

log = logging.getLogger("incidents")

SCHEMA = """
CREATE TABLE IF NOT EXISTS incidents (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            REAL    NOT NULL,          -- unix time of the screenshot
    camera        TEXT    NOT NULL,
    person        INTEGER NOT NULL,          -- on-screen person number (#n)
    status        TEXT    NOT NULL,          -- 'partial' | 'none'
    coat          INTEGER NOT NULL,          -- 1 = item detected
    cap           INTEGER NOT NULL,
    floaters      INTEGER NOT NULL,
    image         TEXT    NOT NULL,          -- path relative to the screenshots folder
    email_status  TEXT    NOT NULL,          -- pending | sent | failed | disabled | not_configured | rate_limited | far_only
    email_attempts INTEGER NOT NULL DEFAULT 0,
    email_error   TEXT,
    email_sent_at REAL
);
CREATE INDEX IF NOT EXISTS ix_incidents_ts ON incidents(ts);
"""


class IncidentStore:
    def __init__(self):
        self.lock = threading.Lock()
        with self._db() as db:
            db.executescript(SCHEMA)

    def _db(self):
        db = sqlite3.connect(DB_PATH, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    # ----------------------------------------------------------------- writes
    def add(self, ts, camera, person, status, flags, image_bgr, email_status):
        day = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
        folder = INCIDENT_DIR / day
        folder.mkdir(parents=True, exist_ok=True)
        name = datetime.fromtimestamp(ts).strftime("%H%M%S") + f"_person{person}_{status}.jpg"
        cv2.imwrite(str(folder / name), image_bgr, [cv2.IMWRITE_JPEG_QUALITY, 90])
        rel = (folder / name).relative_to(INCIDENT_DIR).as_posix()
        with self.lock, self._db() as db:
            cur = db.execute(
                "INSERT INTO incidents (ts, camera, person, status, coat, cap, floaters, image, email_status)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (ts, camera, person, status, int(flags[0]), int(flags[1]), int(flags[2]), rel, email_status))
            return cur.lastrowid

    def set_email(self, iid, status, error=None, attempted=False):
        with self.lock, self._db() as db:
            db.execute("UPDATE incidents SET email_status=?, email_error=?,"
                       " email_attempts=email_attempts+?, email_sent_at=CASE WHEN ?='sent' THEN ? ELSE email_sent_at END"
                       " WHERE id=?", (status, error, int(attempted), status, time.time(), iid))

    # ----------------------------------------------------------------- reads
    def get(self, iid):
        with self._db() as db:
            r = db.execute("SELECT * FROM incidents WHERE id=?", (iid,)).fetchone()
            return dict(r) if r else None

    def recent(self, limit=50):
        with self._db() as db:
            return [dict(r) for r in db.execute("SELECT * FROM incidents ORDER BY ts DESC LIMIT ?", (limit,))]

    def pending_emails(self):
        with self._db() as db:
            return [dict(r) for r in db.execute(
                "SELECT * FROM incidents WHERE email_status IN ('pending','failed') ORDER BY ts")]

    def sent_since(self, ts):
        with self._db() as db:
            return db.execute("SELECT COUNT(*) FROM incidents WHERE email_status='sent' AND email_sent_at>=?",
                              (ts,)).fetchone()[0]

    def counts_since(self, ts):
        with self._db() as db:
            rows = db.execute("SELECT status, COUNT(*) n FROM incidents WHERE ts>=? GROUP BY status", (ts,))
            return {r["status"]: r["n"] for r in rows}

    # ----------------------------------------------------------------- retention
    def purge_older_than(self, days):
        cutoff = datetime.now() - timedelta(days=float(days))
        with self.lock, self._db() as db:
            n = db.execute("DELETE FROM incidents WHERE ts < ?", (cutoff.timestamp(),)).rowcount
        removed = 0
        for d in INCIDENT_DIR.iterdir() if INCIDENT_DIR.exists() else []:
            try:
                if d.is_dir() and datetime.strptime(d.name, "%Y-%m-%d") < cutoff.replace(hour=0, minute=0,
                                                                                         second=0, microsecond=0):
                    shutil.rmtree(d, ignore_errors=True)
                    removed += 1
            except ValueError:
                continue
        if n or removed:
            log.info("Retention: removed %d incident rows and %d day folders older than %s days", n, removed, days)


class RetentionJob(threading.Thread):
    """Runs the clean-up at start-up and then every hour."""

    def __init__(self, store, days):
        super().__init__(name="retention", daemon=True)
        self.store, self.days = store, days

    def run(self):
        while True:
            try:
                self.store.purge_older_than(self.days)
            except Exception:                            # noqa: BLE001 - never let clean-up kill the app
                log.exception("Retention clean-up failed")
            time.sleep(3600)
