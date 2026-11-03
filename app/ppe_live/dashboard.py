"""Local web dashboard (Flask app served by waitress).

  GET  /                       dashboard (Live + Settings tabs)
  GET  /stream.mjpg            annotated live feed
  GET  /api/status             camera / processing / email state, today's counts
  GET  /api/incidents          recent incidents
  GET  /api/settings           recipients + sign-in state (never any password)
  POST /api/settings/recipients        {"recipients": [...]}
  POST /api/settings/nvr               {"ip", "username", "password", "channel"}  -> verified, then saved
  POST /api/settings/smtp              {"address", "password"}                     -> verified, then saved
  POST /api/settings/<nvr|smtp>/signout
  POST /api/settings/smtp/test         sends one test email to the sender address only
"""
import logging
import time
from pathlib import Path
from urllib.parse import urlparse

import cv2
from flask import Flask, Response, abort, jsonify, request, send_file, send_from_directory

from .config import EMAIL_RE, INCIDENT_DIR
from .notifier import build_message, send, verify_login
from .ppe_rules import ITEM_LABELS, ITEMS
from .video_source import probe_rtsp

log = logging.getLogger("dashboard")
WEB_DIR = Path(__file__).parent / "web"
GOOGLE_APP_PASSWORDS = "https://myaccount.google.com/apppasswords"


def create_app(monitor):
    app = Flask(__name__, static_folder=None)
    cfg = monitor.cfg

    @app.before_request
    def same_origin_only():
        """Settings can only be changed from this dashboard page (blocks other sites in the browser)."""
        if request.method == "POST":
            origin = request.headers.get("Origin") or request.headers.get("Referer") or ""
            if request.headers.get("X-PPE-Dashboard") != "1" or \
                    (origin and urlparse(origin).netloc != request.host):
                abort(403)

    # ------------------------------------------------------------------ pages / feed
    @app.get("/")
    def index():
        return send_from_directory(WEB_DIR, "index.html")

    @app.get("/static/<path:name>")
    def static_files(name):
        return send_from_directory(WEB_DIR, name)

    @app.get("/stream.mjpg")
    def stream():
        def frames():
            seq = -1
            monitor.add_viewer(+1)                  # the overlay video is only drawn while someone watches
            try:
                while True:
                    jpg, seq = monitor.wait_jpeg(seq)
                    if jpg:
                        yield (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(jpg)).encode()
                               + b"\r\n\r\n" + jpg + b"\r\n")
            finally:
                monitor.add_viewer(-1)
        return Response(frames(), mimetype="multipart/x-mixed-replace; boundary=frame",
                        headers={"Cache-Control": "no-store"})

    @app.get("/api/status")
    def status():
        return jsonify(monitor.status())

    @app.get("/api/incidents")
    def incidents():
        limit = min(int(request.args.get("limit", 50)), 500)
        rows = monitor.store.recent(limit)
        for r in rows:
            have = dict(zip(ITEMS, (r["coat"], r["cap"], r["floaters"])))
            r["missing"] = [ITEM_LABELS[a] for a in ITEMS if not have[a]]
            r["image_url"] = f"/incident-image/{r['id']}"
        return jsonify(rows)

    @app.get("/incident-image/<int:iid>")
    def incident_image(iid):
        r = monitor.store.get(iid)
        if not r:
            abort(404)
        p = (INCIDENT_DIR / r["image"]).resolve()
        if INCIDENT_DIR.resolve() not in p.parents or not p.exists():
            abort(404)
        return send_file(p, mimetype="image/jpeg")

    # ------------------------------------------------------------------ settings
    @app.get("/api/settings")
    def get_settings():
        nvr, smtp = cfg.creds.info("nvr"), cfg.creds.info("smtp")
        nvr.setdefault("ip", cfg.prefs.get("nvr_ip", cfg.camera.nvr_ip))
        nvr.setdefault("username", cfg.prefs.get("nvr_username", cfg.camera.username))
        nvr.setdefault("channel", cfg.prefs.get("nvr_channel", cfg.camera.channel))
        nvr["login_url"] = f"http://{nvr['ip']}/doc/page/login.asp"
        smtp.setdefault("address", cfg.prefs.get("sender_address", cfg.email.sender_address))
        smtp["app_password_url"] = GOOGLE_APP_PASSWORDS
        return jsonify({"recipients": cfg.recipients(), "nvr": nvr, "smtp": smtp,
                        "credential_days": cfg.creds.days})

    @app.post("/api/settings/recipients")
    def set_recipients():
        raw = (request.get_json(silent=True) or {}).get("recipients", [])
        rec, seen = [], set()
        for a in raw:
            a = str(a).strip().lower()
            if not EMAIL_RE.match(a):
                return jsonify(ok=False, message=f"Not a valid email address: {a}"), 400
            if a not in seen:
                rec.append(a)
                seen.add(a)
        if not rec:
            return jsonify(ok=False, message="Add at least one recipient."), 400
        cfg.prefs.set("recipients", rec)
        log.info("Alert recipients changed: %s", ", ".join(rec))
        return jsonify(ok=True, recipients=rec)

    @app.post("/api/settings/nvr")
    def set_nvr():
        d = request.get_json(silent=True) or {}
        ip, user, pw = str(d.get("ip", "")).strip(), str(d.get("username", "")).strip(), str(d.get("password", ""))
        try:
            ch = int(d.get("channel") or cfg.camera.channel)
        except ValueError:
            return jsonify(ok=False, message="Channel must be a number."), 400
        if not ip or not user or not pw:
            return jsonify(ok=False, message="Enter the NVR address, user name and password."), 400
        rec = {"ip": ip, "port": int(cfg.camera.rtsp_port), "username": user, "password": pw, "channel": ch}
        ok = False
        for raw in (False, True):                  # %-encoded password first, then as typed
            rec["raw_auth"] = raw
            ok, _ = probe_rtsp(cfg.rtsp_url(nvr=rec), cfg.camera.rtsp_transport)
            if ok:
                break
        if not ok:
            return jsonify(ok=False, message="Could not open the camera stream with these details "
                                              "(check address, user name, password, channel).")
        cfg.creds.set("nvr", **rec)
        cfg.prefs.set("nvr_ip", ip)
        cfg.prefs.set("nvr_username", user)
        cfg.prefs.set("nvr_channel", ch)
        monitor.source.reconnect()
        return jsonify(ok=True, message="Connected")

    @app.post("/api/settings/smtp")
    def set_smtp():
        d = request.get_json(silent=True) or {}
        addr = str(d.get("address", "")).strip().lower()
        pw = "".join(str(d.get("password", "")).split())          # App passwords are shown with spaces
        if not EMAIL_RE.match(addr) or not pw:
            return jsonify(ok=False, message="Enter the sender email address and its App password."), 400
        ok, msg = verify_login(cfg, addr, pw)
        if not ok:
            return jsonify(ok=False, message=msg)
        cfg.creds.set("smtp", address=addr, password=pw)
        cfg.prefs.set("sender_address", addr)
        monitor.notifier.notify()
        return jsonify(ok=True, message="Signed in")

    @app.post("/api/settings/<kind>/signout")
    def signout(kind):
        if kind not in ("nvr", "smtp"):
            abort(404)
        cfg.creds.clear(kind)
        if kind == "nvr":
            monitor.source.reconnect()
        return jsonify(ok=True)

    @app.post("/api/settings/smtp/test")
    def test_email():
        acc = cfg.smtp()
        if not acc:
            return jsonify(ok=False, message="Sign in the sender first.")
        img = monitor.jpeg or cv2.imencode(".jpg", monitor._offline_frame())[1].tobytes()
        inc = {"id": 0, "ts": time.time(), "camera": cfg.site.camera_name, "person": 0,
               "status": "partial", "coat": 1, "cap": 0, "floaters": 1}
        msg = build_message(cfg, inc, img, recipients=[acc["address"]])
        msg.replace_header("Subject", "[TEST] " + msg["Subject"])
        try:
            send(cfg, msg)
        except Exception as ex:                                     # noqa: BLE001
            return jsonify(ok=False, message=f"Sending failed: {type(ex).__name__}")
        if cfg.email.mode == "file":
            return jsonify(ok=False, message="Test mode: email saved to the outbox folder, not sent")
        return jsonify(ok=True, message=f"Test email sent to {acc['address']}")

    return app


def serve(monitor, host, port):
    from waitress import serve as waitress_serve
    log.info("Dashboard on http://%s:%s", "localhost" if host in ("127.0.0.1", "0.0.0.0") else host, port)
    waitress_serve(create_app(monitor), host=host, port=int(port), threads=12, channel_timeout=30,
                   ident="PPE Monitor")
