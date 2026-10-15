"""Overlapping tile inference with averaged overlaps.

Predictions run on ``tile_size`` crops with ``tile_overlap`` margins and are
averaged in the overlaps, so no seams appear at tile edges. Georeferencing
is preserved by operating on the source grid and re-assembling in place.
"""

from __future__ import annotations

import numpy as np
import torch


def tile_logits(
    model: torch.nn.Module,
    image: torch.Tensor,
    tile_size: int,
    overlap: int,
) -> torch.Tensor:
    """Full-image logits (C, H, W) from per-tile forward passes.

    ``image`` is (C, H, W), already normalized. Tiles are placed on a grid
    with stride ``tile_size - overlap``; each output pixel accumulates the
    sum of overlapping tile logits and a count, then the sum is divided.
    Edge tiles are clamped to the image bounds.
    """
    _, height, width = image.shape
    if tile_size >= max(height, width):
        with torch.no_grad():
            return model(image[None])[0]
    stride = max(tile_size - overlap, 1)
    n_classes = None
    acc = torch.zeros((1, 1), dtype=torch.float64)  # placeholder, sized below
    counts = np.zeros((height, width), dtype="float64")
    logits_sum: np.ndarray | None = None
    rows = list(range(0, max(height - tile_size, 0) + 1, stride))
    cols = list(range(0, max(width - tile_size, 0) + 1, stride))
    if rows[-1] + tile_size < height:
        rows.append(height - tile_size)
    if cols[-1] + tile_size < width:
        cols.append(width - tile_size)
    for r in rows:
        for c in cols:
            tile = image[:, r:r + tile_size, c:c + tile_size]
            if tile.shape[1] < tile_size or tile.shape[2] < tile_size:
                pad_h, pad_w = tile_size - tile.shape[1], tile_size - tile.shape[2]
                tile = torch.nn.functional.pad(
                    tile, (0, pad_w, 0, pad_h), mode="replicate"
                )
            with torch.no_grad():
                out = model(tile[None])[0]  # (C, tile, tile)
            n_classes = out.shape[0]
            if logits_sum is None:
                logits_sum = np.zeros((n_classes, height, width), dtype="float64")
            h = min(tile_size, height - r)
            w = min(tile_size, width - c)
            logits_sum[:, r:r + h, c:c + w] += out[:, :h, :w].numpy()
            counts[r:r + h, c:c + w] += 1.0
    del acc
    assert logits_sum is not None and n_classes is not None
    averaged = logits_sum / counts[None, :, :]
    return torch.from_numpy(averaged.astype("float32"))
