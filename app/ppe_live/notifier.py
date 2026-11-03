"""Email alerts for incidents (Gmail / Google Workspace SMTP with an App password).

Sender address + App password come from the dashboard sign-in (credentials.py); recipients from the
dashboard (first address = To, the rest = Cc).

Runs on its own thread: picks up incidents whose email is pending or failed, sends them with the
screenshot inline + attached, retries with back-off, and respects the hourly cap.
mode = "file" writes .eml files to data/outbox instead of sending (for testing).
"""
import logging
import smtplib
import ssl
import threading
import time
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from .config import INCIDENT_DIR, OUTBOX_DIR
from .ppe_rules import ITEM_LABELS, ITEMS

log = logging.getLogger("email")

STATUS_TEXT = {"partial": ("PARTIAL PPE", "#E69A00"), "none": ("NO PPE", "#D64541")}


def email_ready(cfg):
    """(ok, reason). Used when an incident is created to decide its initial email state."""
    e = cfg.email
    if not e.enabled:
        return False, "disabled"
    if e.mode == "smtp" and not cfg.smtp():
        return False, "not_configured"
    if not cfg.recipients():
        return False, "no_recipients"
    return True, "pending"


def build_message(cfg, inc, image_bytes, recipients=None, sender=None):
    e = cfg.email
    recipients = recipients or cfg.recipients()
    sender = sender or (cfg.smtp() or {}).get("address") or e.sender_address
    when = datetime.fromtimestamp(inc["ts"])
    label, colour = STATUS_TEXT.get(inc["status"], (inc["status"].upper(), "#D64541"))
    have = dict(zip(ITEMS, (inc["coat"], inc["cap"], inc["floaters"])))
    missing = [ITEM_LABELS[a] for a in ITEMS if not have[a]]
    present = [ITEM_LABELS[a] for a in ITEMS if have[a]]

    msg = EmailMessage()
    msg["Subject"] = e.subject.format(camera=inc["camera"], time=when.strftime("%d %b %Y %H:%M:%S"))
    msg["From"] = formataddr((e.sender_name, sender))
    msg["To"] = recipients[0]
    if len(recipients) > 1:
        msg["Cc"] = ", ".join(recipients[1:])
    msg["X-PPE-Incident"] = str(inc["id"])

    text = (f"Possible PPE non-compliance captured\n\n"
            f"Site:      {cfg.site.company}\nCamera:    {inc['camera']}\n"
            f"Time:      {when:%d %b %Y, %H:%M:%S}\nPerson:    #{inc['person']}\nStatus:    {label}\n"
            f"Missing:   {', '.join(missing) or '-'}\nDetected:  {', '.join(present) or '-'}\n\n"
            "Screenshot attached. This alert is generated automatically from CCTV and may need manual verification.")
    msg.set_content(text)

    cid = make_msgid(domain="ppe-monitor.local")
    rows = "".join(
        f'<tr><td style="padding:6px 14px 6px 0;color:#6b7280">{k}</td><td style="padding:6px 0;color:#111827">{v}</td></tr>'
        for k, v in (("Site", cfg.site.company), ("Camera", inc["camera"]),
                     ("Time", f"{when:%d %b %Y, %H:%M:%S}"), ("Person", f"#{inc['person']}"),
                     ("Missing", f'<b style="color:{colour}">{", ".join(missing) or "-"}</b>'),
                     ("Detected", ", ".join(present) or "-")))
    html = f"""\
<html><body style="margin:0;background:#f3f4f6;font-family:Segoe UI,Arial,sans-serif">
<table width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:24px">
<table width="720" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:10px;overflow:hidden">
<tr><td style="background:#111827;padding:18px 24px;color:#ffffff;font-size:16px;font-weight:600">
  {cfg.site.company} &middot; PPE Compliance Monitor</td></tr>
<tr><td style="padding:22px 24px 6px 24px">
  <div style="display:inline-block;background:{colour};color:#fff;font-weight:600;font-size:13px;
       padding:5px 12px;border-radius:4px;letter-spacing:.4px">{label} DETECTED</div>
  <h2 style="margin:14px 0 6px 0;font-size:20px;color:#111827">Possible PPE non-compliance captured</h2>
  <table cellpadding="0" cellspacing="0" style="font-size:14px;margin-top:8px">{rows}</table></td></tr>
<tr><td style="padding:16px 24px"><img src="cid:{cid[1:-1]}" width="672" style="width:100%;border-radius:6px"
  alt="Screenshot of the incident"></td></tr>
<tr><td style="padding:4px 24px 22px 24px;font-size:12px;color:#6b7280">
  Required PPE: coat, white cap and blue floaters. This alert is generated automatically from CCTV
  and may need manual verification. Incident ref #{inc['id']}.</td></tr>
</table></td></tr></table></body></html>"""
    msg.add_alternative(html, subtype="html")
    msg.get_payload()[1].add_related(image_bytes, maintype="image", subtype="jpeg", cid=cid)
    msg.add_attachment(image_bytes, maintype="image", subtype="jpeg",
                       filename=f"PPE_incident_{inc['id']}_{when:%Y%m%d_%H%M%S}.jpg")
    return msg


def _smtp_session(cfg, address, password):
    e = cfg.email
    ctx = ssl.create_default_context()
    if int(e.smtp_port) == 465:
        s = smtplib.SMTP_SSL(e.smtp_host, 465, context=ctx, timeout=30)
    else:
        s = smtplib.SMTP(e.smtp_host, int(e.smtp_port), timeout=30)
        s.starttls(context=ctx)
    s.login(address, password)
    return s


def verify_login(cfg, address, password):
    """Sign in to the mail server without sending anything. Returns (ok, message)."""
    e = cfg.email
    try:
        with _smtp_session(cfg, address, password):
            return True, "Signed in"
    except smtplib.SMTPAuthenticationError:
        return False, "The mail server rejected this address / App password."
    except (OSError, smtplib.SMTPException) as ex:
        return False, f"Cannot reach {e.smtp_host}:{e.smtp_port} from this PC ({type(ex).__name__})."


def send(cfg, msg):
    if cfg.email.mode == "file":
        p = OUTBOX_DIR / f"{msg['X-PPE-Incident'] or 'test'}_{int(time.time())}.eml"
        p.write_bytes(bytes(msg))
        return
    acc = cfg.smtp()
    if not acc:
        raise RuntimeError("email sign-in missing or expired")
    with _smtp_session(cfg, acc["address"], acc["password"]) as s:
        s.send_message(msg)


class EmailNotifier(threading.Thread):
    def __init__(self, cfg, store):
        super().__init__(name="email", daemon=True)
        self.cfg, self.store = cfg, store
        self.wake = threading.Event()
        self.last_error = None
        self.last_sent = None

    def notify(self):
        self.wake.set()

    def run(self):
        while True:
            try:
                self._process()
            except Exception:                                    # noqa: BLE001
                log.exception("Email worker error")
            self.wake.wait(15)
            self.wake.clear()

    def _process(self):
        e = self.cfg.email
        for inc in self.store.pending_emails():
            if inc["email_status"] == "failed":
                if inc["email_attempts"] >= int(e.retry_attempts):
                    continue
                backoff = 60 * 2 ** (inc["email_attempts"] - 1)            # 1, 2, 4, 8 ... minutes
                if time.time() - inc["ts"] < backoff:
                    continue
            if self.store.sent_since(time.time() - 3600) >= int(e.max_per_hour):
                self.store.set_email(inc["id"], "rate_limited", "hourly email cap reached")
                log.warning("Incident %s not emailed: hourly cap of %s reached", inc["id"], e.max_per_hour)
                continue
            try:
                img = (INCIDENT_DIR / inc["image"]).read_bytes()
                send(self.cfg, build_message(self.cfg, inc, img))
                self.store.set_email(inc["id"], "sent", attempted=True)
                self.last_sent, self.last_error = time.time(), None
                if e.mode == "file":
                    log.info("Incident %s email saved to data/outbox (test mode - not sent)", inc["id"])
                else:
                    log.info("Incident %s emailed to %s", inc["id"], ", ".join(self.cfg.recipients()))
            except Exception as ex:                              # noqa: BLE001
                self.last_error = f"{type(ex).__name__}: {ex}"
                self.store.set_email(inc["id"], "failed", self.last_error[:300], attempted=True)
                log.warning("Incident %s email failed: %s", inc["id"], self.last_error)
