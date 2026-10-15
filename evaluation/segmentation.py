"""Held-out event evaluation of the flood segmentation model.

Computes flood-class IoU, F1, precision and recall for:
*   the trained U-Net checkpoint (src/floodsight/detection/checkpoints/best_model.pt);
*   a classical dark-water baseline (post-event VV below the dark-water
    threshold AND pre-event VV above it — the same rule the production
    baseline uses), applied per patch.

Metrics are reported overall and split by terrain (steep vs flat via the
DEM slope), as ARCHITECTURE.md requires. Everything written to
evaluation/results/segmentation_metrics.json comes from this run — no
number is copied from anywhere else.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import torch

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT))  # for training.train

from floodsight.config import REPO_ROOT as FS_ROOT
from floodsight.preprocessing.dem_derivatives import slope_degrees
from training.train import (
    KuroDataset,
    extract_split,
)

MANIFEST = FS_ROOT / "data" / "training" / "splits_manifest.json"
CHECKPOINT = FS_ROOT / "src" / "floodsight" / "detection" / "checkpoints" / "best_model.pt"
OUT = REPO_ROOT / "evaluation" / "results" / "segmentation_metrics.json"


def _f1(pr: float, re: float) -> float:
    return 2 * pr * re / (pr + re) if (pr + re) else 0.0


def _metrics(pred: np.ndarray, target: np.ndarray, valid: np.ndarray) -> dict:
    p = (pred == 2) & valid
    t = (target == 2) & valid
    inter = (p & t).sum()
    union = (p | t).sum()
    precision = inter / p.sum() if p.sum() else 0.0
    recall = inter / t.sum() if t.sum() else 0.0
    return {
        "iou": round(float(inter / union), 4) if union else None,
        "f1": round(_f1(precision, recall), 4),
        "precision": round(float(precision), 4),
        "recall": round(float(recall), 4),
    }


def evaluate() -> dict:
    manifest = json.loads(MANIFEST.read_text())
    x_test, y_test = extract_split("test", manifest)
    ckpt = torch.load(CHECKPOINT, weights_only=False)
    mean = np.array(ckpt["channel_mean"], dtype="float32")
    std = np.array(ckpt["channel_std"], dtype="float32")

    import segmentation_models_pytorch as smp

    model = smp.Unet(encoder_name=ckpt["encoder"], encoder_weights=None,
                     in_channels=ckpt["in_channels"], classes=3)
    model.load_state_dict(ckpt["model_state"])
    model.eval()

    ds = KuroDataset(x_test, y_test, mean, std, crop=x_test.shape[-1], train=False)
    model_pred = np.zeros_like(y_test)
    with torch.no_grad():
        for i in range(len(ds)):
            xb, _ = ds[i]
            logits = model(xb[None])
            model_pred[i] = logits.argmax(1).numpy()[0]

    # classical baseline: dark post-event VV that was NOT dark pre-event.
    # The patches are linear backscatter; -17 dB == 10^(-1.7).
    dark = 10 ** (-1.7)
    post_vv = x_test[:, 0]
    pre_vv = x_test[:, 2]
    base_pred = ((post_vv < dark) & (pre_vv >= dark)).astype("int64") * 2

    valid = y_test != 3
    slope = np.stack([slope_degrees(x_test[i, 4], pixel_m=10.0)
                      for i in range(len(x_test))])
    flat = valid & (slope <= 10.0)
    steep = valid & (slope > 10.0)

    results = {
        "checkpoint": str(CHECKPOINT),
        "checkpoint_val_iou": ckpt.get("val_flood_iou"),
        "test_samples": len(x_test),
        "test_events": manifest["events"]["test"],
        "model": {
            "overall": _metrics(model_pred, y_test, valid),
            "flat_slope<=10deg": _metrics(model_pred, y_test, flat),
            "steep_slope>10deg": _metrics(model_pred, y_test, steep),
        },
        "darkwater_baseline": {
            "overall": _metrics(base_pred, y_test, valid),
            "flat_slope<=10deg": _metrics(base_pred, y_test, flat),
            "steep_slope>10deg": _metrics(base_pred, y_test, steep),
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(results, indent=2))
    print(json.dumps(results, indent=2))
    print(f"metrics -> {OUT}")
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    evaluate()
