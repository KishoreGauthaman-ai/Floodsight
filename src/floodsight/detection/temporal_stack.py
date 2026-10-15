"""Temporal-stack event attribution (Round 4 T1).

Instead of a single pre/post log-ratio (which the pre-event control showed
detects monsoon-season change, DECISIONS D54), build a same-track stack of
pre-event scenes, compute per-pixel temporal median and MAD of the
terrain-corrected backscatter in dB, and score the post-event scene as a
robust z-score against that spread. The same method run on the pre-event
control (its "post" is the last pre-event scene) gives a null distribution
from which the event threshold is chosen - never from EMSR927.

Grid discipline: all scenes of one slice are warped onto that slice's
reference grid (the pre-event 08-16 terrain-corrected raster), so pixels
are compared within one track and slice only, as required by the radar
rule. Statistics are computed window-by-window; a full slice stack never
fits in memory.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import rasterio
from rasterio.warp import Resampling, reproject

logger = logging.getLogger(__name__)

# Robust spread scaling: MAD * 1.4826 estimates the standard deviation
# for Gaussian noise. The floor stops ultra-stable pixels (flat areas
# under stable conditions) from producing astronomic z-scores from
# sub-dB noise; value recorded in DECISIONS (Round 4 T1).
MAD_TO_SIGMA = 1.4826
DEFAULT_MAD_FLOOR_DB = 1.0
# rasterio window size for windowed statistics (pixels)
WINDOW = 2048


def warp_to_reference(
    src_tif: Path, ref_tif: Path, out_tif: Path, band: int = 1
) -> Path:
    """Warp one band of a compact dB raster onto the reference grid.

    Writes a 1-band float32 GeoTIFF sharing the reference grid exactly so
    that per-pixel stack statistics are valid. Default band 1 = VV (the
    debris evidence rule uses VV only; warping a single band also halves
    the disk footprint of the stack).
    """
    with rasterio.open(ref_tif) as ref:
        ref_transform, ref_crs = ref.transform, ref.crs
        width, height = ref.width, ref.height
    with rasterio.open(src_tif) as src:
        profile = src.profile.copy()
        profile.update(
            transform=ref_transform, crs=ref_crs,
            width=width, height=height, dtype="float32", count=1,
            compress="deflate", nodata=float("nan"),
        )
        with rasterio.open(out_tif, "w", **profile) as dst:
            reproject(
                source=rasterio.band(src, band),
                destination=rasterio.band(dst, 1),
                src_transform=src.transform,
                src_crs=src.crs,
                dst_transform=ref_transform,
                dst_crs=ref_crs,
                resampling=Resampling.bilinear,
                src_nodata=float("nan"),
                dst_nodata=float("nan"),
            )
    return out_tif


def _iter_windows(height: int, width: int, size: int = WINDOW):
    """Yield full-width row bands, NOT a 2-D grid of windows.

    Rationale (verified the hard way): the stack rasters inherit a
    STRIPPED deflate layout from the SNAP compacts. GDAL rewrites a strip
    by APPENDING the new version to the file whenever a window touches
    it; a 2-D window grid rewrites every strip once per column band
    (8x write amplification on slice 6, which crashed into the classic
    4 GB TIFF limit with ~4.7 GB of strip appends for ~594 MB of data).
    Full-width bands touch every strip exactly once.
    """
    for r0 in range(0, height, size):
        r1 = min(r0 + size, height)
        yield rasterio.windows.Window(0, r0, width, r1 - r0)


def stack_statistics(
    stack_paths: list[Path],
    post_path: Path,
    out_dir: Path,
    mad_floor_db: float = DEFAULT_MAD_FLOOR_DB,
    band_indexes: tuple[int, ...] = (1, 2),
    min_valid_scenes: int = 3,
) -> dict[str, Path]:
    """Per-pixel median/MAD of the stack and z-scores of the post scene.

    ``stack_paths`` are rasters already aligned to one common grid (same
    transform/shape); ``post_path`` too. ``band_indexes`` selects which
    raster bands to process (1 = VV, 2 = VH; the debris evidence layer
    uses VV only). Writes, on that same grid:

    * ``median_db.tif``, ``mad_db.tif``  - stack robust statistics
    * ``z_post.tif``  - z-score of the post scene vs the stack
    * ``count.tif``   - number of valid stack scenes per pixel;
      pixels with fewer than ``min_valid_scenes`` valid scenes have NaN
      statistics

    Returns the dict of written paths.
    """
    import contextlib

    out_dir.mkdir(parents=True, exist_ok=True)
    with rasterio.open(stack_paths[0]) as ref:
        profile = ref.profile.copy()
        height, width = ref.height, ref.width
    profile.update(count=len(band_indexes), dtype="float32",
                   compress="deflate", nodata=float("nan"),
                   BIGTIFF="IF_SAFER")

    paths = {
        "median": out_dir / "median_db.tif",
        "mad": out_dir / "mad_db.tif",
        "z": out_dir / "z_post.tif",
        "count": out_dir / "count.tif",
    }
    srcs = [rasterio.open(p) for p in stack_paths]
    post_src = rasterio.open(post_path)
    try:
        dsts = {
            key: rasterio.open(p, "w", **profile) for key, p in paths.items()
        }
        try:
            for win in _iter_windows(height, width):
                # (n_stack, n_bands, h, w) float32; NaN = invalid sample
                block = np.stack(
                    [s.read(indexes=band_indexes, window=win) for s in srcs]
                )
                post = post_src.read(indexes=band_indexes, window=win)
                with np.errstate(invalid="ignore"):
                    count = (
                        np.isfinite(block).sum(axis=0).astype("float32")
                    )
                    enough = count >= min_valid_scenes
                    median = np.full(post.shape, np.nan, dtype="float32")
                    mad = np.full(post.shape, np.nan, dtype="float32")
                    if enough.any():
                        import warnings

                        with warnings.catch_warnings():
                            # per-pixel all-NaN slices are expected where a
                            # pixel has too few valid scenes; not an error
                            warnings.simplefilter("ignore", RuntimeWarning)
                            med = np.nanmedian(
                                np.where(enough, block, np.nan), axis=0
                            )
                            dev = np.abs(block - med[None])
                            mad_v = np.nanmedian(
                                np.where(enough, dev, np.nan), axis=0
                            )
                        median = med.astype("float32")
                        mad = mad_v.astype("float32")
                    sigma = np.maximum(mad * MAD_TO_SIGMA, mad_floor_db)
                    with np.errstate(invalid="ignore"):
                        z = (post - median) / sigma
                    dsts["median"].write(median, window=win)
                    dsts["mad"].write(mad, window=win)
                    dsts["z"].write(z.astype("float32"), window=win)
                    dsts["count"].write(count, window=win)
        finally:
            for d in dsts.values():
                with contextlib.suppress(Exception):
                    d.close()
    finally:
        for s in srcs:
            s.close()
        post_src.close()
    logger.info(
        "stack statistics over %d scenes -> %s", len(stack_paths), out_dir
    )
    return paths
