"""V1 (Round 5): circularity check, leave-one-out and an independent control.

The user's circularity observation is correct and is reported as such: the
v1 control area (0.097 km2) is 0.1% of the 96.46 km2 domain BY CONSTRUCTION,
because the threshold is the control's own p99.9. The v1 event/control ratio
(4.71) therefore measures how much MORE area the event shows above the
control's p99.9 than the control itself does. This script stress-tests
that null:

(a) inventory: which scenes are in the event median/MAD stack, and which
    are in the control stack; whether the control pair's scenes appear.
(b) leave-one-out: recompute the control z with each control-stack scene
    removed (3-scene stacks) and apply the FIXED v1 threshold (z >= 4.679,
    no recalibration). If the control area is stable near 0.1% of the
    domain, the null is not an artifact of one scene.
(c) an independent second pre-event pair: post = 2026-08-04 against the
    stack [06-29, 07-11, 07-23] (all strictly earlier, same track 85,
    same slices). Same fixed threshold.
(d) restate the event/control ratios against every control variant and
    whether "attributable" (ratio >= 2) still holds.

Everything reads only pipeline-legal inputs (S1 scenes, DEM, OSM). The
threshold comes from the v1 control distribution (D66), never EMSR927.

Run:  .venv/bin/python evaluation/v1_circularity.py
Out:  evaluation/results/v1_circularity.json
"""

from __future__ import annotations

import json
import sys
import time
from datetime import date
from pathlib import Path

import numpy as np
import rasterio

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "evaluation"))

from event_attribution import (
    SLICES,
    STACK_ROOT,
    common_grid,
    warp_common,
)

from floodsight.detection.temporal_stack import (
    stack_statistics,
    warp_to_reference,
)

FIXED_THRESHOLD = 4.679  # v1 control p99.9 (D66) - never recalibrated
PX_KM2 = 10.0 * 10.0 / 1e6
OUT = REPO / "evaluation" / "results" / "v1_circularity.json"

COMPACTS = {
    date(2026, 6, 29), date(2026, 7, 11), date(2026, 7, 23),
    date(2026, 8, 4), date(2026, 8, 16), date(2026, 8, 28),
}


def aligned(slice_n: int, d: date) -> Path:
    """Aligned (reference-grid) VV raster for one date+slice, cached."""
    compact = STACK_ROOT / f"{d.isoformat()}_slice{slice_n}_vvvh_db.tif"
    if not compact.is_file():
        raise SystemExit(f"missing compact: {compact}")
    ref = STACK_ROOT / f"2026-08-16_slice{slice_n}_vvvh_db.tif"
    out = STACK_ROOT / f"slice{slice_n}" / "aligned" / f"{d.isoformat()}_aligned.tif"
    if not out.is_file():
        warp_to_reference(compact, ref, out)
    return out


def z_raster(slice_n: int, stack: list[date], post: date, tag: str) -> Path:
    """Stack median/MAD + z for one variant+slice; z returned, temps deleted."""
    out_dir = STACK_ROOT / f"slice{slice_n}" / f"v1_{tag}"
    z = out_dir / "z_post.tif"
    if z.is_file():
        return z
    out_dir.mkdir(parents=True, exist_ok=True)
    stack_paths = [aligned(slice_n, d) for d in stack]
    # the POST must be on the same grid as the stack: use the aligned
    # (reference-grid) version of the post date - raw compacts sit on
    # their own SNAP grids and can differ by a pixel (verified crash:
    # 14657 vs 14658 columns)
    post_path = aligned(slice_n, post)
    paths = stack_statistics(stack_paths, post_path, out_dir, band_indexes=(1,))
    for key in ("median", "mad", "count"):
        paths[key].unlink()
    return paths["z"]


def area_above_threshold(z_paths: dict[int, Path], domain, tr, height, width):
    """Union the per-slice z onto the common grid; area where z >= thr."""
    hit = np.zeros((height, width), dtype=bool)
    valid = np.zeros((height, width), dtype=bool)
    attribution = np.zeros((height, width), dtype="uint8")
    for i, slice_n in enumerate(SLICES):
        with rasterio.open(z_paths[slice_n]) as src:
            z = warp_common(src.read(1), src.transform, src.crs,
                            height, width, tr)
        v = np.isfinite(z) & (z != 0)
        claim = (attribution == 0) & v
        attribution[claim] = i + 1
        valid |= claim
        hit |= claim & (z >= FIXED_THRESHOLD)
    hit_domain = hit & domain & valid
    return float(hit_domain.sum() * PX_KM2), float((domain & valid).sum() * PX_KM2)


def main() -> None:
    t0 = time.monotonic()
    # (a) inventory --------------------------------------------------------
    event_stack = [date(2026, 6, 29), date(2026, 7, 11), date(2026, 7, 23),
                   date(2026, 8, 4), date(2026, 8, 16)]
    ctrl_stack = [date(2026, 6, 29), date(2026, 7, 11), date(2026, 7, 23),
                  date(2026, 8, 4)]
    inventory = {
        "event_stack": [d.isoformat() for d in event_stack],
        "event_post": "2026-08-28",
        "control_a_pair": {"pre": "2026-08-04", "post": "2026-08-16"},
        "control_stack": [d.isoformat() for d in ctrl_stack],
        "control_post_in_control_stack": False,
        "control_post_in_event_stack": True,
        "note": "the control's post scene (08-16) is excluded from the "
                "CONTROL stack by design (D66) but is a legitimate member "
                "of the EVENT pre-event stack",
    }

    domain = np.load(STACK_ROOT / "domain_mask_common.npy")
    tr, width, height = common_grid()
    domain_km2 = float(domain.sum() * PX_KM2)

    # (b) leave-one-out over the control stack ----------------------------
    loo = {}
    for leave_out in ctrl_stack:
        stack = [d for d in ctrl_stack if d != leave_out]
        tag = f"loo_{leave_out.strftime('%m%d')}"
        zp = {s: z_raster(s, stack, date(2026, 8, 16), tag) for s in SLICES}
        km2, valid_km2 = area_above_threshold(zp, domain, tr, height, width)
        loo[leave_out.isoformat()] = {
            "stack": [d.isoformat() for d in stack],
            "area_km2": round(km2, 4),
            "valid_domain_km2": round(valid_km2, 2),
        }
        print(f"LOO leave {leave_out}: {km2:.4f} km2 above {FIXED_THRESHOLD}")

    # (c) independent second pre-event pair: post 08-04 --------------------
    stack_d = [date(2026, 6, 29), date(2026, 7, 11), date(2026, 7, 23)]
    zp = {s: z_raster(s, stack_d, date(2026, 8, 4), "ctrl_d") for s in SLICES}
    ctrl_d_km2, ctrl_d_valid = area_above_threshold(zp, domain, tr, height, width)
    print(f"control (d) post=08-04: {ctrl_d_km2:.4f} km2 above {FIXED_THRESHOLD}")

    # (d) ratios -----------------------------------------------------------
    event_zp = {s: STACK_ROOT / f"slice{s}" / "event" / "z_post.tif"
                for s in SLICES}
    event_km2, _ = area_above_threshold(event_zp, domain, tr, height, width)
    ctrl_a_km2 = 0.097  # v1 measured (D66)
    ratios = {
        "event_km2": round(event_km2, 4),
        "control_a_km2_v1": ctrl_a_km2,
        "event_vs_control_a_ratio": round(event_km2 / ctrl_a_km2, 2),
        "event_vs_control_d_ratio": (round(event_km2 / ctrl_d_km2, 2)
                                     if ctrl_d_km2 > 0 else None),
        "loo_areas_km2": {k: v["area_km2"] for k, v in loo.items()},
        "attributable_vs_control_a": event_km2 / ctrl_a_km2 >= 2,
        "attributable_vs_control_d": (event_km2 / ctrl_d_km2 >= 2
                                      if ctrl_d_km2 > 0 else None),
        "circularity_acknowledged": (
            "the v1 control area equals 0.1% of the domain by construction "
            "of a p99.9 threshold on the control; the ratio measures how "
            "much more area the event shows above that same fixed cutoff"),
    }
    result = {"inventory": inventory, "domain_km2": round(domain_km2, 2),
              "fixed_threshold_z": FIXED_THRESHOLD,
              "leave_one_out": loo, "control_d": {
                  "pair": "2026-06-29/07-11/07-23 stack -> post 2026-08-04",
                  "area_km2": round(ctrl_d_km2, 4),
                  "valid_domain_km2": round(ctrl_d_valid, 2)},
              "ratios": ratios}
    OUT.write_text(json.dumps(result, indent=2))
    print(json.dumps(result["ratios"], indent=2))
    print(f"-> {OUT}  ({time.monotonic()-t0:.0f}s)")


if __name__ == "__main__":
    main()
