"""T1: event attribution from the pre-event same-track temporal stack.

Method (all inputs are pipeline-legal; EMSR927 is never read here):

1. Per slice (track 85, slices 5+6), align the five pre-event compacts
   (2026-06-29, 07-11, 07-23, 08-04, 08-16) onto the 08-16 reference grid.
2. Robust per-pixel statistics of VV in dB: temporal median and MAD.
3. Event signal: z-score of the post-event scene (08-28, already
   co-registered) against the pre-event median/MAD.
4. Control (a) signal: the SAME method with the post scene replaced by
   08-16 (a pre-event date) against the four earlier scenes.
5. Thresholds from the control distribution ONLY (a high percentile of
   the control z over the valid valley-floor domain).
6. Attribution restricted to the valley floor (HAND + slope from the DEM)
   within 2 km of the OSM main stems (Trishuli, Bhote Koshi, Langtang).

Outputs
-------
evaluation/results/event_attribution.json   all numbers + method record
data/stack/trishuli/attribution_common.tif  uint8: 0 none, 1 control,
                                            2 event, 3 both (common grid)
data/stack/trishuli/other_change.tif        old debris minus event layer

Usage
-----
.venv/bin/python evaluation/event_attribution.py --hand-threshold 30 \
    --slope-threshold 15 --control-percentile 99.9
Run with --show-terrain first to print the DEM distributions the
thresholds should be derived from.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from datetime import date
from pathlib import Path

import numpy as np
import rasterio
import rasterio.features
from rasterio.warp import Resampling
from rasterio.warp import reproject as warp_reproject

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from floodsight.detection.temporal_stack import (
    stack_statistics,
    warp_to_reference,
)
from floodsight.preprocessing.s1 import utm_epsg_for_bbox

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("t1_attr")

BBOX = (84.39, 27.68, 85.73, 28.31)  # approved corridor; do not change
STACK_ROOT = REPO / "data/stack/trishuli"
RUN_DIR = REPO / "runs/84.390_27.680_85.730_28.310_2026-08-26_fast"
EPSG = utm_epsg_for_bbox(BBOX)
PIXEL = 10.0  # common-grid spacing (matches the detection stage)
MAIN_STEM_PATTERN = re.compile(r"trishuli|trisuli|bhot[e ]?koshi|langtang", re.IGNORECASE)
MAIN_STEM_BUFFER_M = 2000.0
SLICES = (5, 6)
STACK_DATES = [date(2026, 6, 29), date(2026, 7, 11), date(2026, 7, 23),
               date(2026, 8, 4), date(2026, 8, 16)]
EVENT_POST = date(2026, 8, 28)


def common_grid() -> tuple[rasterio.transform.Affine, int, int]:
    import math

    import pyproj

    tr = pyproj.Transformer.from_crs("EPSG:4326", f"EPSG:{EPSG}", always_xy=True)
    x0, y0 = tr.transform(BBOX[0], BBOX[1])
    x1, y1 = tr.transform(BBOX[2], BBOX[3])
    left = math.floor(min(x0, x1) / PIXEL) * PIXEL
    bottom = math.floor(min(y0, y1) / PIXEL) * PIXEL
    right = math.ceil(max(x0, x1) / PIXEL) * PIXEL
    top = math.ceil(max(y0, y1) / PIXEL) * PIXEL
    width = round((right - left) / PIXEL)
    height = round((top - bottom) / PIXEL)
    return rasterio.transform.from_origin(left, top, PIXEL, PIXEL), width, height


def warp_common(arr, src_transform, src_crs, height, width, dst_transform,
                resampling=Resampling.bilinear, dtype="float32", nodata=None):
    dst = np.zeros((height, width), dtype=dtype)
    warp_reproject(
        source=arr, destination=dst,
        src_transform=src_transform, src_crs=src_crs,
        dst_transform=dst_transform, dst_crs=f"EPSG:{EPSG}",
        src_nodata=nodata, dst_nodata=nodata if nodata is not None else 0,
        resampling=resampling,
    )
    return dst


def main_stem_buffer():
    import geopandas as gpd

    w = gpd.read_file(RUN_DIR / "osm" / "waterways.geojson").to_crs(f"EPSG:{EPSG}")
    cols = ["name", "name:en", "alt_name"]
    m = np.zeros(len(w), dtype=bool)
    for col in cols:
        if col in w.columns:
            m |= w[col].astype(str).str.contains(MAIN_STEM_PATTERN, na=False).values
    stems = w[m]
    if len(stems) == 0:
        raise RuntimeError("no main-stem waterways matched in the OSM snapshot")
    log.info("main stems: %d segments, %.1f km",
             len(stems), stems.geometry.length.sum() / 1000)
    return stems.geometry.union_all().buffer(MAIN_STEM_BUFFER_M)


def build_slice(slice_n: int) -> dict[str, Path]:
    """Align the stack for one slice; compute z for event and control."""
    out = STACK_ROOT / f"slice{slice_n}"
    out.mkdir(parents=True, exist_ok=True)
    compact = {
        d: STACK_ROOT / f"{d.isoformat()}_slice{slice_n}_vvvh_db.tif"
        for d in STACK_DATES + [EVENT_POST]
    }
    missing = [str(p) for p in compact.values() if not p.exists()]
    if missing:
        raise SystemExit(f"missing stack compacts for slice {slice_n}: {missing}")
    ref = compact[date(2026, 8, 16)]
    aligned_dir = out / "aligned"
    aligned_dir.mkdir(exist_ok=True)
    stack_paths = [ref]
    for d in STACK_DATES[:-1]:  # everything except the 08-16 reference
        aligned = aligned_dir / f"{d.isoformat()}_aligned.tif"
        if not aligned.exists():
            warp_to_reference(compact[d], ref, aligned)
        stack_paths.append(aligned)
    # NOTE: order matters for reproducibility - sort by date.
    # The 08-16 reference is first, then the four earlier dates.
    event_stack = [compact[date(2026, 8, 16)]] + [
        aligned_dir / f"{d.isoformat()}_aligned.tif" for d in STACK_DATES[:-1]
    ]
    # Control stack excludes 08-16 entirely: it is the control's "post".
    control_stack = [
        aligned_dir / f"{d.isoformat()}_aligned.tif" for d in STACK_DATES[:-1]
    ]

    results = {}
    for case, stack, post in (
        ("event", event_stack, compact[EVENT_POST]),
        ("control", control_stack, compact[date(2026, 8, 16)]),
    ):
        case_dir = out / case
        z = case_dir / "z_post.tif"
        if z.exists():
            log.info("z exists for slice%d %s", slice_n, case)
            results[case] = z
            continue
        t0 = time.monotonic()
        paths = stack_statistics(
            stack, post, case_dir, band_indexes=(1,),  # VV only (debris rule)
        )
        log.info("slice%d %s stats in %.0fs", slice_n, case,
                 time.monotonic() - t0)
        # keep only z (and a summary of the others, then delete)
        with rasterio.open(paths["median"]) as src:
            med = src.read(1)
        with rasterio.open(paths["mad"]) as src:
            mad = src.read(1)
        summary = {
            "median_db_p50": float(np.nanmedian(med)),
            "mad_db_p50": float(np.nanmedian(mad)),
            "mad_db_p90": float(np.nanpercentile(mad, 90)),
        }
        (case_dir / "stack_summary.json").write_text(json.dumps(summary, indent=2))
        for key in ("median", "mad", "count"):
            paths[key].unlink()
        results[case] = paths["z"]
    return results


def show_terrain() -> None:
    """Print DEM-based distributions used to justify valley thresholds."""
    import geopandas as gpd

    hand_t = STACK_ROOT / "hand30m.tif"
    slope_t = STACK_ROOT / "slope30m.tif"
    if not hand_t.exists():
        raise SystemExit("run scripts/stack/hand_corridor.py first")
    buf = main_stem_buffer()
    with rasterio.open(hand_t) as src:
        hand = src.read(1)
        tr, crs = src.transform, src.crs
    with rasterio.open(slope_t) as src:
        slope = src.read(1)
    # rasterize the 2 km main-stem buffer at 30 m, then shrink by 250 m:
    # the near-river core represents the valley bottom the DEM implies
    near = rasterio.features.rasterize(
        [(buf, 1)], out_shape=hand.shape, transform=tr, fill=0, dtype="uint8"
    )
    core_geom = gpd.GeoSeries([buf], crs=crs).geometry.iloc[0].buffer(-1750)
    core = rasterio.features.rasterize(
        [(core_geom, 1)], out_shape=hand.shape, transform=tr, fill=0, dtype="uint8"
    )
    ok = (near == 1) & np.isfinite(hand) & np.isfinite(slope)
    ok_core = (core == 1) & ok
    rep = {
        "buffer_domain_cells": int(ok.sum()),
        "near_river_hand_p50_p90": [
            round(float(np.percentile(hand[ok], 50)), 1),
            round(float(np.percentile(hand[ok], 90)), 1)],
        "near_river_slope_p50_p90": [
            round(float(np.percentile(slope[ok], 50)), 1),
            round(float(np.percentile(slope[ok], 90)), 1)],
        "core_250m_hand_p50_p90": [
            round(float(np.percentile(hand[ok_core], 50)), 1),
            round(float(np.percentile(hand[ok_core], 90)), 1)] if ok_core.any() else None,
        "corridor_hand_p50": round(float(np.nanpercentile(hand, 50)), 1),
    }
    print(json.dumps(rep, indent=2))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hand-threshold", type=float, required=False)
    ap.add_argument("--slope-threshold", type=float, required=False)
    ap.add_argument("--control-percentile", type=float, default=99.9)
    ap.add_argument("--show-terrain", action="store_true")
    args = ap.parse_args()
    if args.show_terrain:
        show_terrain()
        return
    if args.hand_threshold is None or args.slope_threshold is None:
        raise SystemExit("run with --show-terrain first, then pass both "
                         "--hand-threshold and --slope-threshold (DEM-derived)")
    t_start = time.monotonic()

    # --- per-slice z rasters --------------------------------------------
    zs: dict[int, dict[str, Path]] = {}
    for slice_n in SLICES:
        zs[slice_n] = build_slice(slice_n)

    # --- union onto the corridor common grid -----------------------------
    dst_transform, width, height = common_grid()
    log.info("common grid %dx%d", height, width)

    z_event = np.zeros((height, width), dtype="float32")
    z_ctrl = np.zeros((height, width), dtype="float32")
    valid_e = np.zeros((height, width), dtype=bool)
    valid_c = np.zeros((height, width), dtype=bool)
    attribution = np.zeros((height, width), dtype="uint8")
    for i, slice_n in enumerate(SLICES):
        # event and control z are NOT competing layers: every pixel needs
        # BOTH z values from the SAME slice (the slice that covers it).
        # The first version reused detection's first-come claim per case,
        # which let the event warp claim 99.86% and left control at 0.00%
        # (verified in the log) - a design bug, not a data problem.
        with rasterio.open(zs[slice_n]["event"]) as src:
            z_e = warp_common(src.read(1), src.transform, src.crs,
                              height, width, dst_transform,
                              resampling=Resampling.bilinear)
            ve = np.isfinite(z_e) & (z_e != 0)
        with rasterio.open(zs[slice_n]["control"]) as src:
            z_c = warp_common(src.read(1), src.transform, src.crs,
                              height, width, dst_transform,
                              resampling=Resampling.bilinear)
            vc = np.isfinite(z_c) & (z_c != 0)
        claim = (attribution == 0) & (ve | vc)
        attribution[claim] = i + 1
        z_event[claim] = z_e[claim]
        z_ctrl[claim] = z_c[claim]
        valid_e |= claim & ve
        valid_c |= claim & vc
    valid = valid_e | valid_c
    log.info("valid pixels on common grid: %.2f%% (event %.2f%%, control %.2f%%)",
             100.0 * valid.sum() / valid.size,
             100.0 * valid_e.sum() / valid_e.size,
             100.0 * valid_c.sum() / valid_c.size)

    # --- valley floor: HAND + slope + main-stem buffer -------------------
    buf = main_stem_buffer()
    with rasterio.open(STACK_ROOT / "hand30m.tif") as src:
        hand_c = warp_common(src.read(1), src.transform, src.crs,
                             height, width, dst_transform,
                             resampling=Resampling.bilinear)
    with rasterio.open(STACK_ROOT / "slope30m.tif") as src:
        slope_c = warp_common(src.read(1), src.transform, src.crs,
                              height, width, dst_transform,
                              resampling=Resampling.bilinear)
    from shapely.geometry import box as shapely_box

    minx, maxy = dst_transform * (0, 0)
    maxx, miny = dst_transform * (width, height)
    grid_box = shapely_box(minx, miny, maxx, maxy)
    buf_clip = buf.intersection(grid_box)
    buf_mask = rasterio.features.rasterize(
        [(buf_clip, 1)], out_shape=(height, width),
        transform=dst_transform, fill=0, dtype="uint8") == 1
    valley = (hand_c <= args.hand_threshold) & (slope_c <= args.slope_threshold) \
        & buf_mask
    domain = valley & valid
    log.info("valley-floor domain: %d px (%.1f%% of bbox, %.1f%% of valid)",
             domain.sum(), 100.0 * domain.sum() / domain.size,
             100.0 * domain.sum() / max(valid.sum(), 1))

    # --- threshold from the control distribution only --------------------
    # percentile over the CONTROL-valid domain only: union-valid pixels
    # claimed by the event case alone carry z_ctrl == 0 (outside the
    # control footprint) and would swamp the control distribution with
    # zeros - the first version did exactly that and produced a
    # degenerate ~0 threshold (found in the T1 acceptance check)
    zc = z_ctrl[domain & valid_c]
    zc = zc[np.isfinite(zc)]
    thr = float(np.percentile(zc, args.control_percentile))
    log.info("control z p%s = %.3f (n=%d)", args.control_percentile, thr, len(zc))

    ev_hit = valid_e & valley & (z_event >= thr)
    ct_hit = valid_c & valley & (z_ctrl >= thr)
    px_km2 = (PIXEL * PIXEL) / 1e6
    ev_km2 = float(ev_hit.sum() * px_km2)
    ct_km2 = float(ct_hit.sum() * px_km2)
    ratio = ev_km2 / ct_km2 if ct_km2 > 0 else float("inf")

    # --- old debris layer for the comparison -----------------------------
    import geopandas as gpd
    from rasterio import features as rfeatures

    old_debris = gpd.read_file(RUN_DIR / "debris.geojson").to_crs(f"EPSG:{EPSG}")
    old_mask = rfeatures.rasterize(
        ((geom, 1) for geom in old_debris.geometry),
        out_shape=(height, width), transform=dst_transform,
        fill=0, dtype="uint8") == 1
    old_in_domain = float((old_mask & domain).sum() * px_km2)
    overlap = float((old_mask & ev_hit).sum() * px_km2)
    other_change = float((old_mask & ~ev_hit).sum() * px_km2)

    result = {
        "method": {
            "stack_dates": [d.isoformat() for d in STACK_DATES],
            "event_post": EVENT_POST.isoformat(),
            "control": "post=2026-08-16 vs stack of 4 earlier scenes",
            "band": "VV dB",
            "mad_floor_db": 1.0,
            "threshold_source": f"p{args.control_percentile} of control z over the "
                                "valid valley-floor domain (never from EMSR927)",
            "z_threshold": round(thr, 3),
            "valley_floor": {
                "hand_max_m": args.hand_threshold,
                "slope_max_deg": args.slope_threshold,
                "main_stem_buffer_m": MAIN_STEM_BUFFER_M,
                "main_stem_pattern": MAIN_STEM_PATTERN.pattern,
            },
        },
        "valid_fraction_of_bbox": round(float(valid.sum()) / valid.size, 4),
        "valley_domain_km2": round(float(domain.sum() * px_km2), 3),
        "event_attributed_km2": round(ev_km2, 3),
        "control_area_km2": round(ct_km2, 3),
        "event_control_ratio": round(ratio, 2),
        "attributable": bool(ratio >= 2.0),
        "old_debris_layer": {
            "total_km2": round(float(old_mask.sum() * px_km2), 3),
            "inside_valley_domain_km2": round(old_in_domain, 3),
            "overlapping_event_layer_km2": round(overlap, 3),
            "other_ground_change_km2": round(other_change, 3),
        },
        "control_z_stats": {
            "p50": round(float(np.percentile(zc, 50)), 2),
            "p90": round(float(np.percentile(zc, 90)), 2),
            "p99": round(float(np.percentile(zc, 99)), 2),
            "n": len(zc),
        },
    }
    res_dir = REPO / "evaluation/results"
    res_dir.mkdir(parents=True, exist_ok=True)
    (res_dir / "event_attribution.json").write_text(json.dumps(result, indent=2))

    profile = {"driver": "GTiff", "height": height, "width": width,
               "count": 1, "dtype": "uint8", "crs": f"EPSG:{EPSG}",
               "transform": dst_transform, "compress": "deflate", "nodata": 0}
    both = ev_hit & ct_hit
    attr = np.zeros((height, width), dtype="uint8")
    attr[ct_hit] = 1
    attr[ev_hit] = 2
    attr[both] = 3
    with rasterio.open(STACK_ROOT / "attribution_common.tif", "w", **profile) as dst:
        dst.write(attr, 1)
    with rasterio.open(STACK_ROOT / "other_change.tif", "w", **profile) as dst:
        dst.write((old_mask & ~ev_hit).astype("uint8"), 1)
    np.save(STACK_ROOT / "event_mask_common.npy", ev_hit)
    np.save(STACK_ROOT / "domain_mask_common.npy", domain)

    print(json.dumps(result, indent=2))
    log.info("T1 attribution done in %.0fs", time.monotonic() - t_start)


if __name__ == "__main__":
    main()
