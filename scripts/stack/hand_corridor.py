"""T2: full-corridor HAND (vectorized) + slope + validation vs slow HAND.

Run:  nohup .venv/bin/python scripts/stack/hand_corridor.py \
          > runs/_jobs/t2_hand.log 2>&1 &

Outputs
-------
data/stack/trishuli/hand30m.tif      HAND at 30 m, corridor grid
data/stack/trishuli/slope30m.tif    slope in degrees, same grid
data/stack/trishuli/hand_meta.json  drainage threshold, grid info
evaluation/results/hand_validation.json  window correlation vs slow hand()
"""

import json
import logging
import sys
import time
from pathlib import Path

import numpy as np
import rasterio

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from floodsight.analysis.flood_path import _load_dem_grid
from floodsight.config import load_config
from floodsight.preprocessing.dem_derivatives import (
    fill_depressions,
    flow_accumulation,
    flow_direction,
    hand,
    hand_fast,
    slope_degrees,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("t2_hand")

BBOX = (84.39, 27.68, 85.73, 28.31)  # approved corridor
ACC_THRESHOLD = 500.0  # cells at 30 m => ~0.45 km2 catchment = drainage line


def main() -> None:
    cfg = load_config()
    out_dir = REPO / "data/stack/trishuli"
    out_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.monotonic()
    grid, transform = _load_dem_grid(BBOX, cfg)
    log.info("DEM loaded %s (native CRS) in %.0fs", grid.shape,
             time.monotonic() - t0)
    # The DEM tiles are geographic (EPSG:4326, ~30 m degree cells). All
    # hydrology and HAND must run in a metric projection: reproject the
    # mosaic to the corridor UTM zone at a true 30 m spacing BEFORE
    # conditioning (the first version wrongly labelled the 4326 grid
    # as 32645 - a CRS metadata bug caught in Round 4 T2).
    import pyproj
    from rasterio.transform import from_origin
    from rasterio.warp import Resampling
    from rasterio.warp import reproject as warp_reproject

    tr4326 = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:32645",
                                         always_xy=True)
    x0, y0 = tr4326.transform(BBOX[0], BBOX[1])
    x1, y1 = tr4326.transform(BBOX[2], BBOX[3])
    import math

    left = math.floor(min(x0, x1) / 30.0) * 30.0
    right = math.ceil(max(x0, x1) / 30.0) * 30.0
    bottom = math.floor(min(y0, y1) / 30.0) * 30.0
    top = math.ceil(max(y0, y1) / 30.0) * 30.0
    width = round((right - left) / 30.0)
    height = round((top - bottom) / 30.0)
    metric_transform = from_origin(left, top, 30.0, 30.0)
    grid_m = np.zeros((height, width), dtype="float64")
    warp_reproject(
        source=np.where(np.isfinite(grid), grid, -9999.0), destination=grid_m,
        src_transform=transform, src_crs="EPSG:4326",
        dst_transform=metric_transform, dst_crs="EPSG:32645",
        src_nodata=-9999.0, dst_nodata=1e5, resampling=Resampling.bilinear,
    )
    log.info("reprojected to EPSG:32645 %s in %.0fs", grid_m.shape,
             time.monotonic() - t0)
    grid = grid_m
    transform = metric_transform
    clean = np.where(np.isfinite(grid) & (grid < 1e4), grid, 1e5)
    clean[clean <= 0] = 1e5  # sea-level sentinels/voids made inert (D48)

    t0 = time.monotonic()
    filled = fill_depressions(clean)
    log.info("fill_depressions in %.0fs", time.monotonic() - t0)
    t0 = time.monotonic()
    direction = flow_direction(filled)
    log.info("flow_direction in %.0fs", time.monotonic() - t0)
    t0 = time.monotonic()
    acc = flow_accumulation(direction)
    log.info("flow_accumulation in %.0fs", time.monotonic() - t0)

    drainage = acc >= ACC_THRESHOLD
    log.info("drainage cells: %d (%.2f%%)", drainage.sum(),
             100.0 * drainage.mean())

    t0 = time.monotonic()
    hand_v, _dist = hand_fast(filled, drainage)
    log.info("hand_fast in %.0fs", time.monotonic() - t0)
    slope = slope_degrees(filled, 30.0)

    profile = {
        "driver": "GTiff", "height": grid.shape[0], "width": grid.shape[1],
        "count": 1, "dtype": "float32", "crs": "EPSG:32645",
        "transform": transform, "compress": "deflate", "nodata": float("nan"),
    }
    for arr, name in ((hand_v, "hand30m.tif"), (slope, "slope30m.tif")):
        with rasterio.open(out_dir / name, "w", **profile) as dst:
            dst.write(arr.astype("float32"), 1)
    meta = {
        "acc_threshold_cells": ACC_THRESHOLD,
        "shape": list(grid.shape),
        "crs": "EPSG:32645",
        "hand_stats": {
            "p50": float(np.nanpercentile(hand_v, 50)),
            "p75": float(np.nanpercentile(hand_v, 75)),
            "p90": float(np.nanpercentile(hand_v, 90)),
            "max": float(np.nanmax(hand_v)),
        },
    }
    (out_dir / "hand_meta.json").write_text(json.dumps(meta, indent=2))
    log.info("HAND stats: %s", meta["hand_stats"])

    # --- validation: slow along-flow HAND on an interior window ---------
    H, W = grid.shape
    r0, c0 = H // 2 - 600, W // 2 - 600
    r1, c1 = r0 + 1200, c0 + 1200
    win = (slice(r0, r1), slice(c0, c1))
    t0 = time.monotonic()
    slow = hand(filled[win], direction[win], acc[win], acc_threshold=ACC_THRESHOLD)
    fast = hand_v[win]
    log.info("slow hand on 1200x1200 window in %.0fs", time.monotonic() - t0)
    m = 100  # interior margin: window-edge sinks bias the slow variant
    inner = (slice(m, -m), slice(m, -m))
    sv, fv = slow[inner], fast[inner]
    ok = np.isfinite(sv) & np.isfinite(fv)
    diff = fv[ok] - sv[ok]
    corr = float(np.corrcoef(fv[ok], sv[ok])[0, 1])
    val = {
        "window": [r0, r1, c0, c1],
        "inner_margin": m,
        "n_compared": int(ok.sum()),
        "pearson_r": round(corr, 4),
        "mean_abs_diff_m": round(float(np.abs(diff).mean()), 3),
        "p50_abs_diff_m": round(float(np.percentile(np.abs(diff), 50)), 3),
        "p95_abs_diff_m": round(float(np.percentile(np.abs(diff), 95)), 3),
        "slow_min": round(float(np.nanmin(sv)), 1),
        "fast_min": round(float(np.nanmin(fv)), 1),
        "note": "fast = nearest drainage cell in 2D (distance_transform_edt); "
                "slow = along-flow walk to first drainage; interior margin "
                "excludes window-edge sinks of the slow variant",
    }
    res_dir = REPO / "evaluation/results"
    res_dir.mkdir(parents=True, exist_ok=True)
    (res_dir / "hand_validation.json").write_text(json.dumps(val, indent=2))
    log.info("HAND validation: r=%s mad=%s m | p95=%s m",
             val["pearson_r"], val["mean_abs_diff_m"], val["p95_abs_diff_m"])
    log.info("T2 DONE")


if __name__ == "__main__":
    main()
