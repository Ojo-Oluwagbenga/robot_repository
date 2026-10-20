#!/usr/bin/env python3
"""
Train the fingertip model (YOLO11n-pose, 2 keypoints: index tip, thumb tip)
and export it to ONNX for the Raspberry Pi.

Run this on your PC (or the Pi if you are patient), not needed for tracking:
  pip install -r requirements-train.txt
  python3 tools/make_dataset.py --labels data/photos/labels.json --labels data/my_frames/labels.json --out data/dataset
  python3 tools/train.py --data data/dataset/data.yaml --epochs 60
The new model is copied to models/hand_tips.onnx.
"""
import argparse
import shutil
from pathlib import Path

from ultralytics import YOLO


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/dataset/data.yaml")
    ap.add_argument("--weights", default="yolo11n-pose.pt",
                    help="start point: yolo11n-pose.pt (fresh) or runs/.../best.pt / models/hand_tips.pt (fine-tune)")
    ap.add_argument("--epochs", type=int, default=60)
    ap.add_argument("--imgsz", type=int, default=320)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--name", default="hand_tips")
    ap.add_argument("--fraction", type=float, default=1.0, help="use only part of the training set per run")
    ap.add_argument("--lr0", type=float, default=None, help="e.g. 0.0005 when fine-tuning an existing model")
    ap.add_argument("--out", default="models")
    a = ap.parse_args()

    model = YOLO(a.weights)
    extra = dict(optimizer="AdamW", lr0=a.lr0) if a.lr0 else {}
    model.train(**extra,
        data=a.data, epochs=a.epochs, imgsz=a.imgsz, batch=a.batch, workers=a.workers,
        device=a.device, name=a.name, fraction=a.fraction, exist_ok=True, patience=20, plots=False,
        # our dataset is already heavily augmented; keep YOLO's own augmentation moderate
        hsv_h=0.01, hsv_s=0.3, hsv_v=0.3, degrees=10, translate=0.1, scale=0.35,
        fliplr=0.5, mosaic=0.3, close_mosaic=5, mixup=0.0,
        pose=16.0, cos_lr=True, warmup_epochs=2,
    )
    best = Path(model.trainer.best)
    print("best weights:", best)

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    shutil.copy(best, out / "hand_tips.pt")
    onnx = YOLO(str(best)).export(format="onnx", imgsz=a.imgsz, opset=12, simplify=True, dynamic=False)
    shutil.copy(onnx, out / "hand_tips.onnx")
    print("exported ->", out / "hand_tips.onnx")


if __name__ == "__main__":
    main()
