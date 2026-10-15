"""T6: per-event, terrain-binned held-out evaluation + leakage check.

Extends evaluation/segmentation.py with what Round 4 requires:
* per-event IoU/F1/precision/recall for the model and the dark-water
  baseline, for BOTH the validation and the test split (explains the
  validation 0.32 vs test 0.63 gap from real per-event numbers);
* elevation- and slope-binned metrics (the patches carry DEM and slope
  channels, so no external data is needed);
* per-event terrain characterisation (elevation/slope at flood-labelled
  pixels) to state honestly whether any held-out event is mountainous;
* an explicit leakage check: event-family overlaps between splits.

Run:  .venv/bin/python evaluation/segmentation_by_event.py
Output: evaluation/results/segmentation_by_event.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import torch

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT))

from floodsight.config import REPO_ROOT as FS_ROOT
from training.train import KuroDataset, extract_split

MANIFEST = FS_ROOT / "data" / "training" / "splits_manifest.json"
CHECKPOINT = FS_ROOT / "src" / "floodsight" / "detection" / "checkpoints" / "best_model.pt"
OUT = REPO_ROOT / "evaluation" / "results" / "segmentation_by_event.json"


def _metrics(pred: np.ndarray, target: np.ndarray, sel: np.ndarray) -> dict:
    p = (pred == 2) & sel
    t = (target == 2) & sel
    inter = int((p & t).sum())
    union = int((p | t).sum())
    precision = inter / int(p.sum()) if p.sum() else 0.0
    recall = inter / int(t.sum()) if t.sum() else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {
        "iou": round(inter / union, 4) if union else None,
        "f1": round(f1, 4),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "n_flood_px": int(t.sum()),
    }


def _predict(model, x: np.ndarray, mean, std) -> np.ndarray:
    ds = KuroDataset(x, x, mean, std, crop=x.shape[-1], train=False)
    preds = np.zeros((len(ds), *x.shape[2:]), dtype="int64")
    with torch.no_grad():
        for i in range(len(ds)):
            xb, _ = ds[i]
            preds[i] = model(xb[None]).argmax(1).numpy()[0]
    return preds


def evaluate_split(name: str, manifest: dict, model, mean, std) -> dict:
    x, y = extract_split(name, manifest)
    events = [r["event"] for r in manifest["samples"] if r["split"] == name]
    assert len(events) == len(x), "sample/event order mismatch"
    pred = _predict(model, x, mean, std)

    dark = 10 ** (-1.7)
    base_pred = ((x[:, 0] < dark) & (x[:, 2] >= dark)).astype("int64") * 2

    valid = y != 3
    dem = x[:, 4]      # DEM channel (metres; voids zeroed in extraction)
    slope = x[:, 5]    # slope channel (computed at extraction)
    # per-event overall + terrain characterisation
    per_event = {}
    for ev in sorted(set(events)):
        m = np.array([e == ev for e in events])
        per_event[ev] = {
            "n_samples": int(m.sum()),
            "model": _metrics(pred[m], y[m], valid[m]),
            "baseline": _metrics(base_pred[m], y[m], valid[m]),
            "terrain": {
                "flood_px_median_elev_m": (
                    round(float(np.median(dem[m][valid[m] & (y[m] == 2)])), 1)
                    if (valid[m] & (y[m] == 2)).any() else None),
                "flood_px_p90_elev_m": (
                    round(float(np.percentile(dem[m][valid[m] & (y[m] == 2)], 90)), 1)
                    if (valid[m] & (y[m] == 2)).any() else None),
                "flood_px_median_slope_deg": (
                    round(float(np.median(slope[m][valid[m] & (y[m] == 2)])), 1)
                    if (valid[m] & (y[m] == 2)).any() else None),
                "median_elev_m": round(float(np.median(dem[m][valid[m]])), 1),
                "p90_slope_deg": round(float(np.percentile(slope[m][valid[m]], 90)), 1),
            },
        }
    # terrain bins over the whole split
    bins = {
        "elev<100m": valid & (dem < 100),
        "elev_100_500m": valid & (dem >= 100) & (dem < 500),
        "elev_500_1000m": valid & (dem >= 500) & (dem < 1000),
        "elev>=1000m": valid & (dem >= 1000),
        "slope<=10deg": valid & (slope <= 10),
        "slope_10_30deg": valid & (slope > 10) & (slope <= 30),
        "slope>30deg": valid & (slope > 30),
    }
    binned = {k: {"model": _metrics(pred, y, m), "baseline": _metrics(base_pred, y, m),
                  "n_valid_px": int(m.sum())}
              for k, m in bins.items()}
    return {
        "n_samples": len(x),
        "n_events": len(set(events)),
        "overall_model": _metrics(pred, y, valid),
        "overall_baseline": _metrics(base_pred, y, valid),
        "per_event": per_event,
        "terrain_bins": binned,
    }


def main() -> None:
    manifest = json.loads(MANIFEST.read_text())
    ev = manifest["events"]

    def fam(e: str) -> str:
        return "_".join(e.split("_")[:3])

    leakage = {
        "train∩test_families": sorted({fam(e) for e in ev["train"]} & {fam(e) for e in ev["test"]}),
        "train∩validation_families": sorted({fam(e) for e in ev["train"]} & {fam(e) for e in ev["validation"]}),
        "validation∩test_families": sorted({fam(e) for e in ev["validation"]} & {fam(e) for e in ev["test"]}),
        "event_id_overlaps": sorted(
            (set(ev["train"]) & set(ev["test"]))
            | (set(ev["train"]) & set(ev["validation"]))
            | (set(ev["validation"]) & set(ev["test"]))),
        "test_unique_families": sorted({fam(e) for e in ev["test"]}),
    }

    ckpt = torch.load(CHECKPOINT, weights_only=False)
    mean = np.array(ckpt["channel_mean"], dtype="float32")
    std = np.array(ckpt["channel_std"], dtype="float32")

    import segmentation_models_pytorch as smp

    model = smp.Unet(encoder_name=ckpt["encoder"], encoder_weights=None,
                     in_channels=ckpt["in_channels"], classes=3)
    model.load_state_dict(ckpt["model_state"])
    model.eval()

    results = {
        "checkpoint_val_iou_recorded": ckpt.get("val_flood_iou"),
        "leakage_check": leakage,
        "validation": evaluate_split("validation", manifest, model, mean, std),
        "test": evaluate_split("test", manifest, model, mean, std),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(results, indent=2))
    print(f"metrics -> {OUT}")
    for split in ("validation", "test"):
        s = results[split]
        print(f"\n{split}: model IoU {s['overall_model']['iou']} "
              f"baseline IoU {s['overall_baseline']['iou']}")
        for ev_name, d in s["per_event"].items():
            t = d["terrain"]
            print(f"  {ev_name}: model IoU {d['model']['iou']} "
                  f"(flood px median elev {t['flood_px_median_elev_m']} m, "
                  f"slope {t['flood_px_median_slope_deg']} deg)")


if __name__ == "__main__":
    main()
