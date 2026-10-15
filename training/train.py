"""Training loop for the flood segmentation U-Net (declared AI component).

Trains segmentation_models_pytorch U-Net (resnet18 encoder) on the Kuro Siwo
subset defined by training/prepare_kurosiwo.py (event-wise splits, DECISIONS
D35). Input channels follow what Kuro Siwo really provides (verified on
disk): flood (post) VV/VH, sec1 (pre) VV/VH, DEM, slope computed from the
DEM, and the post-pre VV difference. Classes: 0 land / 1 permanent water /
2 flood water. Loss: combined focal + Dice (flood pixels are rare).

Everything measured (step time, epoch time, val IoU) is printed AND written
to the metrics file next to the checkpoint — no invented numbers.
"""

from __future__ import annotations

import argparse
import io
import json
import tarfile
import time

import numpy as np
import torch
import torch.nn.functional as F
from torch.utils.data import DataLoader, Dataset

from floodsight.config import REPO_ROOT

SHARD_PATHS = {
    "train": REPO_ROOT / "data" / "training" / "kurosiwo" / "train_GRD_shard-00000.tar",
    "test": REPO_ROOT / "data" / "training" / "kurosiwo" / "test_GRD_shard-00000.tar",
}
MANIFEST = REPO_ROOT / "data" / "training" / "splits_manifest.json"
OUT_DIR = REPO_ROOT / "src" / "floodsight" / "detection" / "checkpoints"
CACHE_DIR = REPO_ROOT / "data" / "training" / "cache"


def slope_from_dem(dem: np.ndarray) -> np.ndarray:
    """Slope in degrees from the DEM patch (central differences).

    Kuro Siwo patches are (1, 224, 224); the gradient is spatial (last 2 dims).
    """
    d = dem.astype("float32")
    if d.ndim == 3:
        d = d[0]
    gy, gx = np.gradient(d)
    return np.degrees(np.arctan(np.hypot(gx, gy)))


def extract_split(split: str, manifest: dict) -> tuple[np.ndarray, np.ndarray]:
    """Extract (X, y) arrays for one split from the shards, cached to disk."""
    cache = CACHE_DIR / f"{split}.npz"
    if cache.is_file():
        data = np.load(cache)
        return data["x"], data["y"]
    rows = [r for r in manifest["samples"] if r["split"] == split]
    xs, ys = [], []
    shards_open: dict[str, tarfile.TarFile] = {}
    try:
        for r in rows:
            shard_key = r["shard"]
            if shard_key not in shards_open:
                shards_open[shard_key] = tarfile.open(SHARD_PATHS[shard_key])  # noqa: SIM115 - closed in finally
            tf = shards_open[shard_key]

            def read(member: str, _tf: tarfile.TarFile = tf, _r: dict = r) -> np.ndarray:
                buf = _tf.extractfile(f"{_r['sample']}.{member}.npy")
                arr = np.load(io.BytesIO(buf.read())).astype("float32")
                return arr[0] if arr.ndim == 3 else arr  # squeeze (1, H, W)

            flood_vv, flood_vh = read("flood_vv"), read("flood_vh")
            sec1_vv, sec1_vh = read("sec1_vv"), read("sec1_vh")
            dem, mask = read("dem"), read("mask")
            # DEM voids arrive as NaN or the -32768 sentinel (verified
            # empirically: 0.74% of subset pixels, p0.1 = -32768); zero them
            # out — those pixels carry the no-data label 3 and are ignored.
            dem = np.nan_to_num(dem, nan=0.0, neginf=0.0, posinf=0.0)
            dem[dem < -500.0] = 0.0
            slope = np.nan_to_num(slope_from_dem(dem), nan=0.0)
            slope[slope < 0] = 0.0
            diff_vv = flood_vv - sec1_vv
            x = np.nan_to_num(
                np.stack([flood_vv, flood_vh, sec1_vv, sec1_vh, dem, slope, diff_vv]),
                nan=0.0,
            )
            xs.append(x)
            ys.append(mask.astype("int64"))
    finally:
        for tf in shards_open.values():
            tf.close()
    X = np.stack(xs)
    y = np.stack(ys)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    np.savez(cache, x=X, y=y)
    return X, y


class KuroDataset(Dataset):
    def __init__(self, x: np.ndarray, y: np.ndarray, mean: np.ndarray,
                 std: np.ndarray, crop: int, train: bool, seed: int = 42):
        self.x, self.y = x, y
        self.mean, self.std = mean, std
        self.crop = crop
        self.train = train
        self.rng = np.random.default_rng(seed)

    def __len__(self) -> int:
        return len(self.x)

    def __getitem__(self, i: int):
        x, y = self.x[i], self.y[i]
        if self.train:
            k = self.rng.integers(0, 4)  # rot90 k times + random flips
            x = np.rot90(x, k, axes=(1, 2)).copy()
            y = np.rot90(y, k).copy()
            if self.rng.random() < 0.5:
                x, y = x[:, :, ::-1].copy(), y[::-1].copy()
            top = self.rng.integers(0, x.shape[1] - self.crop + 1)
            left = self.rng.integers(0, x.shape[2] - self.crop + 1)
            x = x[:, top:top + self.crop, left:left + self.crop]
            y = y[top:top + self.crop, left:left + self.crop]
        x = (x - self.mean[:, None, None]) / self.std[:, None, None]
        return torch.from_numpy(x.astype("float32")), torch.from_numpy(y.astype("int64"))


def focal_dice_loss(logits: torch.Tensor, target: torch.Tensor) -> torch.Tensor:
    """Combined focal + Dice on the flood class (2) plus land/water CE.

    Label 3 is the no-data label (verified empirically: mask==3 coincides
    exactly with valid_mask==0 in the real shards), so pixels labelled 3 are
    ignored everywhere. Flood pixels are a small fraction; the focal term
    upweights hard examples and Dice directly optimizes overlap.
    """
    valid = target != 3
    ce = F.cross_entropy(logits, target, ignore_index=3)
    probs = torch.softmax(logits, dim=1)
    one_hot = F.one_hot(target.clamp_max(2), num_classes=3).permute(0, 3, 1, 2).float()
    pt = (probs * one_hot).sum(1)
    focal = -(1 - pt) ** 2 * torch.log(pt.clamp_min(1e-6))
    focal = (focal * valid).sum() / valid.sum().clamp_min(1)
    flood_prob = probs[:, 2] * valid
    flood_target = ((target == 2).float() * valid)
    inter = (flood_prob * flood_target).sum()
    dice = 1 - (2 * inter + 1) / (flood_prob.sum() + flood_target.sum() + 1)
    return ce + focal + dice


def iou_flood(logits: torch.Tensor, target: torch.Tensor) -> float:
    valid = target != 3
    pred = logits.argmax(1)
    inter = (((pred == 2) & (target == 2)) & valid).sum().item()
    union = (((pred == 2) | (target == 2)) & valid).sum().item()
    return inter / union if union else float("nan")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--epochs", type=int, default=6)
    parser.add_argument("--encoder-weights", default=None,
                        help="'imagenet' to pretrain the encoder (v2); "
                             "None reproduces the frozen v1 run")
    parser.add_argument("--out-tag", default="",
                        help="checkpoint suffix, e.g. '_v2'; the default "
                             "EMPTY tag writes the frozen v1 paths - a v2 "
                             "run MUST pass a tag so v1 is never overwritten")
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--crop", type=int, default=128)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--max-train", type=int, default=0,
                        help="cap train samples (0 = all manifest train rows)")
    args = parser.parse_args()
    torch.manual_seed(args.seed)
    torch.set_num_threads(max(1, torch.get_num_threads()))

    manifest = json.loads(MANIFEST.read_text())
    print("extracting splits (cached after first run) ...", flush=True)
    x_train, y_train = extract_split("train", manifest)
    x_val, y_val = extract_split("validation", manifest)
    if args.max_train:
        x_train, y_train = x_train[:args.max_train], y_train[:args.max_train]
    print(f"train: {len(x_train)} samples | val: {len(x_val)} | "
          f"channels: {x_train.shape[1]} | patch: {x_train.shape[2]}", flush=True)

    mean = x_train.mean(axis=(0, 2, 3))
    std = x_train.std(axis=(0, 2, 3)) + 1e-6
    assert np.isfinite(mean).all() and np.isfinite(std).all(), \
        "channel stats must be finite (NaN handling failed)"
    print("channel mean/std:", [round(float(m), 4) for m in mean],
          [round(float(s), 4) for s in std], flush=True)

    train_ds = KuroDataset(x_train, y_train, mean, std, args.crop, True, args.seed)
    val_ds = KuroDataset(x_val, y_val, mean, std, x_val.shape[-1], False)
    train_dl = DataLoader(train_ds, batch_size=args.batch_size, shuffle=True)
    val_dl = DataLoader(val_ds, batch_size=args.batch_size)

    import segmentation_models_pytorch as smp

    model = smp.Unet(encoder_name="resnet18",
                     encoder_weights=args.encoder_weights,
                     in_channels=x_train.shape[1], classes=3)
    optim = torch.optim.AdamW(model.parameters(), lr=args.lr)

    out_dir = OUT_DIR / args.out_tag.lstrip("_") if args.out_tag else OUT_DIR
    if args.encoder_weights and not args.out_tag:
        raise SystemExit("v2 (pretrained) runs must pass --out-tag so the "
                          "frozen v1 checkpoint is never overwritten")
    out_dir.mkdir(parents=True, exist_ok=True)
    best_iou, history = -1.0, []
    for epoch in range(1, args.epochs + 1):
        model.train()
        t0, losses = time.monotonic(), []
        for step, (xb, yb) in enumerate(train_dl):
            optim.zero_grad()
            loss = focal_dice_loss(model(xb), yb)
            loss.backward()
            optim.step()
            losses.append(loss.item())
            if step == 0:
                print(f"epoch {epoch} first-step time: "
                      f"{time.monotonic() - t0:.1f}s", flush=True)
        train_time = time.monotonic() - t0

        model.eval()
        ious = []
        with torch.no_grad():
            for xb, yb in val_dl:
                ious.append(iou_flood(model(xb), yb))
        val_iou = float(np.nanmean(ious))
        history.append({
            "epoch": epoch,
            "train_loss": round(float(np.mean(losses)), 4),
            "val_flood_iou": round(val_iou, 4),
            "train_seconds": round(train_time, 1),
        })
        print(f"epoch {epoch}: loss={history[-1]['train_loss']} "
              f"val_flood_iou={history[-1]['val_flood_iou']} "
              f"({train_time:.0f}s)", flush=True)
        if val_iou > best_iou:
            best_iou = val_iou
            torch.save(
                {
                    "model_state": model.state_dict(),
                    "encoder": "resnet18",
                    "in_channels": int(x_train.shape[1]),
                    "classes": ["land", "permanent_water", "flood_water"],
                    "channel_mean": mean.tolist(),
                    "channel_std": std.tolist(),
                    "channel_names": manifest["channels"],
                    "val_flood_iou": round(val_iou, 4),
                    "epoch": epoch,
                    "manifest_source": manifest["source"],
                },
                out_dir / "best_model.pt",
            )
            print(f"  saved best (IoU {val_iou:.4f})", flush=True)

    metrics = {
        "best_val_flood_iou": round(best_iou, 4),
        "history": history,
        "train_samples": len(x_train),
        "val_samples": len(x_val),
        "batch_size": args.batch_size,
        "crop": args.crop,
        "epochs": args.epochs,
        "seed": args.seed,
    }
    (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=2))
    print("done. metrics ->", out_dir / "metrics.json", flush=True)


if __name__ == "__main__":
    main()
