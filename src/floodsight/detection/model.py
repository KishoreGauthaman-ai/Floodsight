"""U-Net flood segmentation: checkpoint loading and inference on real runs.

The declared AI component (AGENT.md section 4). The checkpoint stores the
channel layout, mean/std and class names, so inference uses EXACTLY the
same preprocessing as training (one shared definition — a channel mismatch
would be a bug). Input channels built from the co-registered SNAP rasters:
post VV/VH, pre VV/VH, DEM, slope (from DEM), post-pre VV difference.

Classes: 0 land, 1 permanent water, 2 flood. Class 2 IS new flood water
(the dataset annotates permanent and flood water separately), so no
permanent-water subtraction is applied on top.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
import torch

from ..config import REPO_ROOT
from .baseline import BAND_ELEV, BAND_INCIDENCE, BAND_VV, slope_degrees
from .tiling import tile_logits

logger = logging.getLogger(__name__)

CHECKPOINT_PATH = REPO_ROOT / "src" / "floodsight" / "detection" / "checkpoints" / "best_model.pt"


class ModelError(Exception):
    """Model checkpoint or inference failed."""


def load_checkpoint(path: Path | None = None) -> dict[str, Any]:
    """Load the checkpoint; raises a clear error when weights are absent."""
    path = Path(path) if path else CHECKPOINT_PATH
    if not path.is_file():
        raise ModelError(
            f"No model checkpoint at {path}. The pipeline falls back to the "
            "log-ratio baseline (train with training/train.py to create it)."
        )
    return torch.load(path, weights_only=False)


def build_model_input(pre_tif: Path, post_tif: Path, ckpt: dict[str, Any]):
    """(image, valid_mask, profile) on the pre grid; channels per ckpt."""
    channel_names = list(ckpt["channel_names"])
    with rasterio.open(pre_tif) as pre_src:
        post_vv = None
        pre_vv = pre_src.read(BAND_VV).astype("float32")
        pre_vh = pre_src.read(2).astype("float32")
        elev = pre_src.read(BAND_ELEV).astype("float32")
        inc = pre_src.read(BAND_INCIDENCE).astype("float32")
        profile = {"transform": pre_src.transform, "crs": pre_src.crs,
                   "height": pre_src.height, "width": pre_src.width}
        pixel_m = float(pre_src.transform.a)
    with rasterio.open(post_tif) as post_src:
        post_vv = post_src.read(BAND_VV).astype("float32")
        post_vh = post_src.read(2).astype("float32")

    slope = slope_degrees(elev, pixel_m).astype("float32")
    diff_vv = post_vv - pre_vv
    available = {
        "flood_vv": post_vv, "flood_vh": post_vh,
        "sec1_vv": pre_vv, "sec1_vh": pre_vh,
        "dem": elev, "slope(computed)": slope,
        "diff_vv(post-pre)": diff_vv,
    }
    try:
        layers = [available[name] for name in channel_names]
    except KeyError as exc:
        raise ModelError(
            f"checkpoint channel {exc} cannot be built from the SNAP rasters; "
            "training/inference preprocessing mismatch (AGENT.md 8.3)"
        ) from exc
    image = np.stack(layers)
    mean = np.array(ckpt["channel_mean"], dtype="float32")[:, None, None]
    std = np.array(ckpt["channel_std"], dtype="float32")[:, None, None]
    image = (image - mean) / std

    valid = (
        (elev > 0) & np.isfinite(image).all(axis=0)
        & (inc > 0) & (inc <= 90)
    )
    return image, valid, profile


def predict(
    pre_tif: Path,
    post_tif: Path,
    cfg: dict[str, Any],
    ckpt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Flood segmentation on the pair. Returns mask/confidence/stats.

    Confidence is the softmax probability of the predicted class (per-pixel).
    """
    import segmentation_models_pytorch as smp

    ckpt = ckpt or load_checkpoint()
    model = smp.Unet(
        encoder_name=ckpt["encoder"], encoder_weights=None,
        in_channels=int(ckpt["in_channels"]), classes=3,
    )
    model.load_state_dict(ckpt["model_state"])
    model.eval()

    image, valid, profile = build_model_input(pre_tif, post_tif, ckpt)
    tile = int(cfg["detection"].get("tile_size", 224))
    overlap = int(cfg["detection"].get("tile_overlap", 64))
    logits = tile_logits(model, torch.from_numpy(image), tile, overlap)
    probs = torch.softmax(logits, dim=0).numpy()
    pred = probs.argmax(0)
    confidence = probs.max(0)

    flood = (pred == 2) & valid
    permanent = (pred == 1) & valid
    # remove specks at raster/tile edges with the SAME blob rule as the
    # baseline (a verified 1-pixel tile-edge artefact existed before this
    # fix - DECISIONS D47); confidence follows the cleaned mask
    from .baseline import remove_small_blobs

    min_blob = int(cfg["detection"]["baseline"].get("min_blob_pixels", 20))
    flood = remove_small_blobs(flood, min_blob)
    pixel_area_km2 = (float(profile["transform"].a) / 1000.0) ** 2
    stats = {
        "method": "unet_resnet18_kurosiwo",
        "flood_pixels": int(flood.sum()),
        "flood_area_km2": round(float(flood.sum()) * pixel_area_km2, 4),
        "permanent_water_pixels": int(permanent.sum()),
        "valid_pixel_share": round(float(valid.mean()), 4),
        "checkpoint_val_iou": ckpt.get("val_flood_iou"),
    }
    return {
        "mask": flood,
        "permanent_water": permanent,
        "confidence": confidence,
        "stats": stats,
        "transform": profile["transform"],
        "crs": profile["crs"],
        "shape": (profile["height"], profile["width"]),
    }
