"""OpenVINO models: person detector (YOLO26) and coat classifier (CLIP ViT-B/32 + linear head).

Models are compiled for the Intel GPU when available; any failure falls back to CPU automatically.
"""
import logging
import os

import cv2
import numpy as np
import openvino as ov
from PIL import Image

from .config import MODELS_DIR

log = logging.getLogger("models")
_core = ov.Core()


def cpu_threads(setting):
    """0 = automatic: half the logical cores (at least 1), so the AI never takes the whole PC."""
    return int(setting) if int(setting) > 0 else max(1, (os.cpu_count() or 4) // 2)


# Run GPU work at low priority / with light CPU polling, so the screen and other programs stay smooth.
_GPU_GENTLE = {"GPU_QUEUE_PRIORITY": "LOW", "GPU_QUEUE_THROTTLE": "LOW"}


def compile_model(xml_path, device, threads, sample_input):
    """Compile on `device`, verify with one inference, fall back to CPU on any error."""
    tried = []
    for dev in ([device, "CPU"] if device != "CPU" else ["CPU"]):
        try:
            conf = {"PERFORMANCE_HINT": "LATENCY"}
            if dev == "CPU":
                conf["INFERENCE_NUM_THREADS"] = cpu_threads(threads)
            if dev == "GPU" and "GPU" not in _core.available_devices:
                raise RuntimeError("no Intel GPU visible to OpenVINO")
            if dev == "GPU":
                try:
                    compiled = _core.compile_model(str(xml_path), dev, {**conf, **_GPU_GENTLE})
                except Exception:                        # noqa: BLE001 - older driver: plain settings
                    compiled = _core.compile_model(str(xml_path), dev, conf)
            else:
                compiled = _core.compile_model(str(xml_path), dev, conf)
            compiled(sample_input)                       # smoke test
            log.info("%s running on %s", xml_path.name, dev)
            return compiled, dev
        except Exception as e:                           # noqa: BLE001 - any driver error -> fallback
            tried.append(f"{dev}: {str(e).splitlines()[0][:120]}")
            log.warning("Could not use %s for %s (%s)", dev, xml_path.name, tried[-1])
    raise RuntimeError("model could not be loaded: " + " | ".join(tried))


class PersonDetector:
    SIZE = 640

    def __init__(self, cfg):
        p = cfg.processing
        xml = MODELS_DIR / "person_detector" / f"{p.detector_model}.xml"
        dummy = np.zeros((1, 3, self.SIZE, self.SIZE), np.float32)
        self.model, self.device = compile_model(xml, p.device, p.cpu_threads, dummy)
        self.conf = float(p.detector_conf)

    def __call__(self, frame, min_conf=0.1):
        """-> array N x 5 (x1, y1, x2, y2, score) of persons, in frame pixels."""
        h, w = frame.shape[:2]
        s = self.SIZE / max(h, w)
        nw, nh = round(w * s), round(h * s)
        top, left = (self.SIZE - nh) // 2, (self.SIZE - nw) // 2
        canvas = np.full((self.SIZE, self.SIZE, 3), 114, np.uint8)
        canvas[top:top + nh, left:left + nw] = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_LINEAR)
        x = cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB).transpose(2, 0, 1)[None].astype(np.float32) / 255.0
        out = self.model(x)[0][0]                        # 300 x 6: x1 y1 x2 y2 score class
        out = out[(out[:, 5] == 0) & (out[:, 4] >= min_conf)]
        if not len(out):
            return np.zeros((0, 5), np.float32)
        b = out[:, :4].copy()
        b[:, [0, 2]] = (b[:, [0, 2]] - left) / s
        b[:, [1, 3]] = (b[:, [1, 3]] - top) / s
        b[:, [0, 2]] = b[:, [0, 2]].clip(0, w - 1)
        b[:, [1, 3]] = b[:, [1, 3]].clip(0, h - 1)
        return np.hstack([b, out[:, 4:5]]).astype(np.float32)


class CoatClassifier:
    """Plain coat (any block colour) vs shirt / t-shirt / patterned shirt / jacket."""
    MEAN = np.array([0.48145466, 0.4578275, 0.40821073], np.float32)
    STD = np.array([0.26862954, 0.26130258, 0.27577711], np.float32)

    def __init__(self, cfg):
        p = cfg.processing
        d = MODELS_DIR / "coat_classifier"
        dummy = np.zeros((1, 3, 224, 224), np.float32)
        self.model, self.device = compile_model(d / "clip_vitb32_visual.xml", p.device, p.cpu_threads, dummy)
        head = np.load(d / "coat_head.npz")
        self.w, self.b, self.scale = head["w"], float(head["b"][0]), float(head["scale"])

    def _prep(self, bgr):
        h, w = bgr.shape[:2]
        s = 224 / min(h, w)
        im = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
        im = im.resize((max(224, round(w * s)), max(224, round(h * s))), Image.BICUBIC)
        a = np.asarray(im, np.float32) / 255.0
        y, x = (a.shape[0] - 224) // 2, (a.shape[1] - 224) // 2
        return ((a[y:y + 224, x:x + 224] - self.MEAN) / self.STD).transpose(2, 0, 1)

    def __call__(self, crops):
        """crops: list of BGR images -> coat probabilities (0..1)."""
        if not crops:
            return np.zeros(0, np.float32)
        x = np.stack([self._prep(c) for c in crops]).astype(np.float32)
        f = self.model(x)[0]
        return 1.0 / (1.0 + np.exp(-self.scale * (f @ self.w + self.b)))
