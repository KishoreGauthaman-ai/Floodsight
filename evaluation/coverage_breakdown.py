"""T3: reproducible coverage/analysed-area breakdown for the case study.

Measures, on the approved corridor, with the real preprocessed rasters:
* raw same-track footprint coverage of the bbox (slices 5+6, track 85);
* how much of the bbox fails each reliability mask at native 10 m:
  no radar data, DEM void, layover/shadow (local incidence), slope>30 deg;
* the resulting analysed-area share (matches the pipeline's 55.9%).

Run:  .venv/bin/python evaluation/coverage_breakdown.py
Output: evaluation/results/coverage_breakdown.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin
from rasterio.warp import Resampling, reproject
from rasterio.windows import Window

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from floodsight.config import REPO_ROOT as FS_ROOT
from floodsight.detection.baseline import slope_degrees

BBOX = (84.39, 27.68, 85.73, 28.31)  # approved corridor
SLOPE_MAX = 30.0
OUT = REPO / "evaluation" / "results" / "coverage_breakdown.json"
CACHE = (FS_ROOT / "data" / "interim" /
         "709f78806df43e49a2abed4b095b810feeafdc3344ad5633ae6f85ef4dc39a68")

SLICES = [
    (5, CACHE / "track85_ASCE_slice5_B057_C523/s1_pre_sigma0_tc.tif",
     CACHE / "track85_ASCE_slice5_B057_C523/s1_post_coreg.tif"),
    (6, CACHE / "track85_ASCE_slice6_C3AB_01B4/s1_pre_sigma0_tc.tif",
     CACHE / "track85_ASCE_slice6_C3AB_01B4/s1_post_coreg.tif"),
]


def main() -> None:
    import math

    import pyproj

    tr = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:32645", always_xy=True)
    x0, y0 = tr.transform(BBOX[0], BBOX[3])
    x1, y1 = tr.transform(BBOX[2], BBOX[1])
    pix = 100.0  # analysis grid: 100 m keeps the union in memory
    left, right = math.floor(x0 / pix) * pix, math.ceil(x1 / pix) * pix
    bottom, top = math.floor(y1 / pix) * pix, math.ceil(y0 / pix) * pix
    w, h = round((right - left) / pix), round((top - bottom) / pix)
    t100 = from_origin(left, top, pix, pix)

    def warp(mask, src_tr):
        dst = np.zeros((h, w), dtype="uint8")
        reproject(mask.astype("uint8"), dst, src_transform=src_tr,
                  src_crs="EPSG:32645", dst_transform=t100,
                  dst_crs="EPSG:32645", resampling=Resampling.nearest)
        return dst > 0

    fails = {k: np.zeros((h, w), dtype=bool)
             for k in ("radar", "dem", "incidence", "slope")}
    seen = np.zeros((h, w), dtype=bool)
    per_slice_raw = {}
    ws = 4096
    for sl, pre, post in SLICES:
        alone = np.zeros((h, w), dtype=bool)
        with rasterio.open(pre) as s, rasterio.open(post) as p:
            for r0 in range(0, s.height, ws):
                for c0 in range(0, s.width, ws):
                    win = Window(c0, r0, min(ws, s.width - c0),
                                 min(ws, s.height - r0))
                    pre_vv = s.read(1, window=win)
                    elev = s.read(3, window=win)
                    inc = s.read(4, window=win)
                    post_vv = p.read(1, window=win)
                    radar_ok = (np.isfinite(pre_vv) & (pre_vv > 0)
                                & np.isfinite(post_vv) & (post_vv > 0))
                    dem_ok = np.isfinite(elev) & (elev > 0)
                    inc_ok = np.isfinite(inc) & (inc > 0) & (inc <= 90)
                    with np.errstate(invalid="ignore"):
                        slope = slope_degrees(
                            np.where(dem_ok, elev, np.nan), 10.0)
                    slope_ok = dem_ok & (slope <= SLOPE_MAX)
                    wtr = s.window_transform(win)
                    r = warp(radar_ok, wtr)
                    alone |= r
                    here = r & ~seen
                    seen |= here
                    fails["radar"] |= here & ~r
                    d, i_, sl_ = warp(dem_ok, wtr), warp(inc_ok, wtr), \
                        warp(slope_ok, wtr)
                    fails["dem"] |= here & r & ~d
                    fails["incidence"] |= here & r & d & ~i_
                    fails["slope"] |= here & r & d & i_ & ~sl_
        per_slice_raw[f"slice{sl}_raw_coverage_pct"] = round(
            float(alone.mean() * 100), 1)

    valid = seen & ~np.logical_or.reduce(list(fails.values()))
    result = {
        "bbox": list(BBOX),
        "analysis_grid": "100 m over the bbox, masks evaluated at native 10 m",
        "slope_max_deg": SLOPE_MAX,
        **per_slice_raw,
        "raw_footprint_coverage_pct": round(float(seen.mean() * 100), 1),
        "fail_no_radar_pct": round(float(fails["radar"].mean() * 100), 3),
        "fail_dem_void_pct": round(float(fails["dem"].mean() * 100), 3),
        "fail_layover_shadow_pct": round(float(fails["incidence"].mean() * 100), 3),
        "fail_slope_gt_30deg_pct": round(float(fails["slope"].mean() * 100), 2),
        "analysed_area_pct": round(float(valid.mean() * 100), 1),
        "note": "the pipeline's coverage_fraction is the analysed-area share "
                "after these reliability masks, not missing satellite data "
                "(DECISIONS D58)",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
