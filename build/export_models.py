"""Re-creates everything in ..\\models from the original PyTorch weights (developer machine only).

Needs a full Python 3.11 with: ultralytics, torch, openai-clip (`clip`), openvino, numpy.

  python build\\export_models.py --yolo C:\\path\\yolo26s.pt [--yolo C:\\path\\yolo26m.pt]
                                 --coat-head ..\\ppe_monitor\\heads.npz

1. Person detector  : Ultralytics YOLO26 (COCO) -> OpenVINO IR FP16, 640x640, NMS-free output [1,300,6]
2. Coat classifier  : OpenAI CLIP ViT-B/32 image encoder (L2-normalised output) -> OpenVINO IR FP16
3. Coat head        : logistic-regression weights trained on labelled crops from the SMT Gallery 2
                      recording (proof of concept: ppe_monitor\\train_heads.py). Saved as coat_head.npz
                      with keys w (512), b (1), scale (12).
"""
import argparse
import shutil
import tempfile
from pathlib import Path

import numpy as np

MODELS = Path(__file__).resolve().parents[1] / "models"


def export_yolo(pt):
    from ultralytics import YOLO
    pt = Path(pt)
    with tempfile.TemporaryDirectory() as tmp:
        shutil.copy(pt, tmp)
        out = Path(YOLO(str(Path(tmp) / pt.name)).export(format="openvino", imgsz=640, half=True, dynamic=False))
        dst = MODELS / "person_detector"
        dst.mkdir(parents=True, exist_ok=True)
        for ext in (".xml", ".bin"):
            shutil.copy(out / f"{pt.stem}{ext}", dst / f"{pt.stem}{ext}")
    print("detector ->", dst)


def export_clip():
    import clip
    import openvino as ov
    import torch

    model, _ = clip.load("ViT-B/32", device="cpu")
    model = model.float().eval()

    class Visual(torch.nn.Module):
        def __init__(self, v):
            super().__init__()
            self.v = v

        def forward(self, x):
            f = self.v(x)
            return f / f.norm(dim=-1, keepdim=True)

    om = ov.convert_model(Visual(model.visual).eval(), example_input=torch.randn(1, 3, 224, 224),
                          input=[(-1, 3, 224, 224)])
    dst = MODELS / "coat_classifier"
    dst.mkdir(parents=True, exist_ok=True)
    ov.save_model(om, str(dst / "clip_vitb32_visual.xml"), compress_to_fp16=True)
    print("coat encoder ->", dst)


def export_head(npz):
    h = np.load(npz)
    np.savez(MODELS / "coat_classifier" / "coat_head.npz", w=h["coat_w"].astype(np.float32),
             b=h["coat_b"].astype(np.float32), scale=np.float32(12))
    print("coat head ->", MODELS / "coat_classifier" / "coat_head.npz")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--yolo", action="append", default=[])
    ap.add_argument("--coat-head")
    ap.add_argument("--skip-clip", action="store_true")
    a = ap.parse_args()
    for pt in a.yolo:
        export_yolo(pt)
    if not a.skip_clip:
        export_clip()
    if a.coat_head:
        export_head(a.coat_head)
