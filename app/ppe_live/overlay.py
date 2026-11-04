"""Overlay drawing: elegant thin boxes, status chips and the top-left PPE alert panel.

Identical look to the proof-of-concept videos. Sprites (labels / alerts) are rendered once with PIL
using Segoe UI and cached, then alpha-blended onto frames with numpy.
"""
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

FONT_DIR = "C:/Windows/Fonts/"
F_SEMI = FONT_DIR + "seguisb.ttf"
F_REG = FONT_DIR + "segoeui.ttf"
F_ICON = FONT_DIR + "segmdl2.ttf"     # Segoe MDL2 Assets (lock glyph U+E72E)
LOCK_GLYPH = "\ue72e"

# Status palette (BGR for OpenCV, RGB for PIL)
STATUS = {
    "full":    {"label": "FULL PPE DETECTED",    "short": "FULL PPE",    "rgb": (46, 204, 113)},
    "partial": {"label": "PARTIAL PPE DETECTED", "short": "PARTIAL PPE", "rgb": (255, 176, 32)},
    "none":    {"label": "NO PPE DETECTED",      "short": "NO PPE",      "rgb": (235, 77, 75)},
    "pending": {"label": "VERIFYING PPE",        "short": "VERIFYING",   "rgb": (180, 188, 198)},
}
ITEMS = ("ESD Apron", "ESD Cap", "ESD Slippers")


def bgr(rgb):
    return (rgb[2], rgb[1], rgb[0])


_cache = {}


def _font(path, size):
    k = (path, size)
    if k not in _cache:
        try:
            _cache[k] = ImageFont.truetype(path, size)
        except OSError:                                  # font missing: fall back, never crash
            _cache[k] = ImageFont.load_default(size)
    return _cache[k]


def _sprite(key, builder):
    """Cache RGBA sprites so PIL only runs once per unique label."""
    if key not in _cache:
        _cache[key] = builder()
    return _cache[key]


def blend(frame, rgba, x, y):
    h, w = rgba.shape[:2]
    H, W = frame.shape[:2]
    x0, y0 = max(x, 0), max(y, 0)
    x1, y1 = min(x + w, W), min(y + h, H)
    if x1 <= x0 or y1 <= y0:
        return
    s = rgba[y0 - y:y1 - y, x0 - x:x1 - x]
    a = s[..., 3:4].astype(np.float32) / 255.0
    roi = frame[y0:y1, x0:x1].astype(np.float32)
    frame[y0:y1, x0:x1] = (roi * (1 - a) + s[..., 2::-1].astype(np.float32) * a).astype(np.uint8)


def _rounded_panel(w, h, r, fill):
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(im).rounded_rectangle([0, 0, w - 1, h - 1], r, fill=fill)
    return im


def chip_sprite(status, pid, flags, locked=False):
    """Small label above a person: '#ID  FULL PPE' + three item ticks (+ lock badge once confirmed)."""
    def build():
        st = STATUS[status]
        f1, f2 = _font(F_SEMI, 17), _font(F_REG, 14)
        title = f"#{pid}  {st['short']}" if pid is not None else st["short"]
        tw = f1.getbbox(title)[2] + (26 if locked else 0)
        parts = []
        if flags is not None:
            for name, ok in zip(ITEMS, flags):
                parts.append((name, ok))
        pw = sum(f2.getbbox(n)[2] + 22 for n, _ in parts)
        w = max(tw, pw) + 24
        h = 30 if not parts else 52
        im = _rounded_panel(w, h, 6, (16, 20, 26, 200))
        d = ImageDraw.Draw(im)
        d.rectangle([0, 6, 3, h - 7], fill=st["rgb"] + (255,))
        d.text((12, 3), title, font=f1, fill=st["rgb"] + (255,))
        if locked:
            d.text((f1.getbbox(title)[2] + 20, 8), LOCK_GLYPH, font=_font(F_ICON, 14), fill=st["rgb"] + (255,))
        x = 12
        for name, ok in parts:
            col = (46, 204, 113, 255) if ok else (235, 77, 75, 255)
            d.ellipse([x, 35, x + 8, 43], fill=col)
            d.text((x + 13, 29), name, font=f2, fill=(225, 230, 236, 255))
            x += f2.getbbox(name)[2] + 22
        return np.array(im)
    return _sprite(("chip", status, pid, flags, locked), build)


def draw_person(frame, box, status, pid, flags, locked=False):
    x1, y1, x2, y2 = [int(v) for v in box]
    col = bgr(STATUS[status]["rgb"])
    H, W = frame.shape[:2]
    x1, x2 = max(0, min(x1, W - 1)), max(0, min(x2, W - 1))
    y1, y2 = max(0, min(y1, H - 1)), max(0, min(y2, H - 1))
    if x2 - x1 < 4 or y2 - y1 < 4:
        return
    # hairline full rectangle, translucent (blended on the box region only, for speed)
    rx1, ry1, rx2, ry2 = max(0, x1 - 2), max(0, y1 - 2), min(W, x2 + 3), min(H, y2 + 3)
    roi = frame[ry1:ry2, rx1:rx2]
    ov = roi.copy()
    cv2.rectangle(ov, (x1 - rx1, y1 - ry1), (x2 - rx1, y2 - ry1), col, 1, cv2.LINE_AA)
    cv2.addWeighted(ov, 0.55, roi, 0.45, 0, roi)
    # corner accents
    L = max(10, int(min(x2 - x1, y2 - y1) * 0.18))
    for (cx, cy, dx, dy) in ((x1, y1, 1, 1), (x2, y1, -1, 1), (x1, y2, 1, -1), (x2, y2, -1, -1)):
        cv2.line(frame, (cx, cy), (cx + dx * L, cy), col, 2, cv2.LINE_AA)
        cv2.line(frame, (cx, cy), (cx, cy + dy * L), col, 2, cv2.LINE_AA)
    sp = chip_sprite(status, pid, flags, locked)
    ty = y1 - sp.shape[0] - 6
    if ty < 70:
        ty = y2 + 6
    blend(frame, sp, x1, ty)


def alert_sprite(status, pid, locked=False):
    def build():
        st = STATUS[status]
        f1, f2 = _font(F_SEMI, 22), _font(F_REG, 15)
        sub = f"Person #{pid}" if pid is not None else "PPE compliance monitor"
        if locked:
            sub += "  \u00b7  Confirmed & locked"
        w = max(f1.getbbox(st["label"])[2], f2.getbbox(sub)[2]) + 66
        h = 62
        im = _rounded_panel(w, h, 8, (14, 18, 24, 215))
        d = ImageDraw.Draw(im)
        c = st["rgb"]
        d.rounded_rectangle([0, 0, 5, h - 1], 3, fill=c + (255,))
        if locked:
            d.text((22, 16), LOCK_GLYPH, font=_font(F_ICON, 18), fill=c + (255,))
        else:
            d.ellipse([22, 18, 40, 36], fill=c + (60,))
            d.ellipse([26, 22, 36, 32], fill=c + (255,))
        d.text((52, 5), st["label"], font=f1, fill=c + (255,))
        d.text((53, 36), sub, font=f2, fill=(170, 178, 190, 255))
        return np.array(im)
    return _sprite(("alert", status, pid, locked), build)


def idle_sprite():
    def build():
        f = _font(F_REG, 16)
        t = "PPE MONITOR  \u00b7  No personnel in view"
        w = f.getbbox(t)[2] + 50
        im = _rounded_panel(w, 36, 8, (14, 18, 24, 170))
        d = ImageDraw.Draw(im)
        d.ellipse([16, 13, 26, 23], fill=(120, 130, 142, 255))
        d.text((34, 7), t, font=f, fill=(185, 192, 202, 255))
        return np.array(im)
    return _sprite(("idle",), build)


def draw_alerts(frame, people, x=24, y=112):
    """people: list of (status, pid[, locked]). Stacked alerts below the NVR timestamp."""
    if not people:
        blend(frame, idle_sprite(), x, y)
        return
    for status, pid, *rest in people[:5]:
        sp = alert_sprite(status, pid, bool(rest and rest[0]))
        blend(frame, sp, x, y)
        y += sp.shape[0] + 8
