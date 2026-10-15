"""T5: recompute damage and cut-off on EVENT-ATTRIBUTED debris only.

Inputs (all produced by the Round 4 T1 pipeline):
* per-slice event z rasters  data/stack/trishuli/slice{5,6}/event/z_post.tif
* per-slice control z rasters .../control/z_post.tif
* valley-floor domain        data/stack/trishuli/domain_mask_common.npy
* method record              evaluation/results/event_attribution.json

Method (thresholds from the CONTROL distribution only, never EMSR927):
* possible tier: z_event >= p99.9 of control z over the valley domain
* likely tier:   z_event >= p99.99 of control z over the same domain
The percentile tiers are mapped onto the existing confidence scale
(possible -> 0.5, likely -> 0.8) so the UNCHANGED tier code in
analysis/damage.py and analysis/cutoff.py assigns them; the semantics
remain the control percentiles (DECISIONS D66).

Outputs:
* runs/<case>/event_debris.geojson     event-attributed polygons with tier
* evaluation/results/t5_recompute.json before/after table + counts

Run:  .venv/bin/python evaluation/t5_recompute.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio import features
from scipy import ndimage

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "evaluation"))

from event_attribution import EPSG, SLICES, common_grid, warp_common

from floodsight.analysis.cutoff import analyze_cutoff
from floodsight.analysis.damage import assess_damage
from floodsight.config import load_config

STACK = REPO / "data/stack/trishuli"
RUN = REPO / "runs/84.390_27.680_85.730_28.310_2026-08-26_fast"
OUT = REPO / "evaluation/results/t5_recompute.json"
POSSIBLE_CONF = 0.5   # maps onto the (likely_min=0.6, possible_min=0.3) tiers
LIKELY_CONF = 0.8
POSS_PCTL = 99.9
LIKE_PCTL = 99.99


def union_case(case: str):
    """Warp per-slice z rasters of one case onto the common grid."""
    dst_transform, width, height = common_grid()
    z_union = np.zeros((height, width), dtype="float32")
    valid = np.zeros((height, width), dtype=bool)
    attribution = np.zeros((height, width), dtype="uint8")
    for i, slice_n in enumerate(SLICES):
        with rasterio.open(STACK / f"slice{slice_n}" / case / "z_post.tif") as src:
            z = warp_common(src.read(1), src.transform, src.crs,
                            height, width, dst_transform)
        v = np.isfinite(z) & (z != 0)
        claim = (attribution == 0) & v
        attribution[claim] = i + 1
        z_union[claim] = z[claim]
        valid |= claim
    return z_union, valid, dst_transform, height, width


def vectorize_tier(mask: np.ndarray, transform, conf: float):
    """Vectorize one tier mask into polygons with a constant confidence."""
    labeled, n = ndimage.label(mask, structure=np.ones((3, 3), dtype=int))
    if n == 0:
        return gpd.GeoDataFrame(
            {"mean_confidence": [], "geometry": []},
            crs=f"EPSG:{EPSG}", geometry="geometry")
    from shapely.geometry import shape as shp_shape
    geoms = [
        {"mean_confidence": conf, "geometry": shp_shape(geom)}
        for geom, _ in features.shapes(
            mask.astype("uint8"), mask=labeled > 0,
            transform=transform, connectivity=8)
    ]
    gdf = gpd.GeoDataFrame(geoms, geometry="geometry", crs=f"EPSG:{EPSG}")
    return gdf.to_crs("EPSG:4326")


def main() -> None:
    cfg = load_config()
    z_ctrl, valid_c, tr, _h, _w = union_case("control")
    z_event, valid_e, *_ = union_case("event")
    domain = np.load(STACK / "domain_mask_common.npy")

    # thresholds from the CONTROL distribution only (T1 acceptance rule)
    zc = z_ctrl[domain & valid_c]
    zc = zc[np.isfinite(zc)]
    thr_possible = float(np.percentile(zc, POSS_PCTL))
    thr_likely = float(np.percentile(zc, LIKE_PCTL))
    print(f"control p{POSS_PCTL} z = {thr_possible:.3f} (possible tier)")
    print(f"control p{LIKE_PCTL} z = {thr_likely:.3f} (likely tier)")

    ev_domain = domain & valid_e
    mask_possible = ev_domain & (z_event >= thr_possible)
    mask_likely = ev_domain & (z_event >= thr_likely)
    px_km2 = 10.0 * 10.0 / 1e6
    print(f"event-attributed: possible {mask_possible.sum()*px_km2:.2f} km2, "
          f"likely {mask_likely.sum()*px_km2:.2f} km2")

    # polygons: likely tier first, then possible-only
    likely_gdf = vectorize_tier(mask_likely, tr, LIKELY_CONF)
    possible_only = mask_possible & ~mask_likely
    possible_gdf = vectorize_tier(possible_only, tr, POSSIBLE_CONF)
    debris_new = gpd.GeoDataFrame(
        pd.concat([likely_gdf, possible_gdf], ignore_index=True),
        crs="EPSG:4326")
    debris_new["tier"] = ["likely"] * len(likely_gdf) + ["possible"] * len(possible_gdf)
    debris_new.to_file(RUN / "event_debris.geojson", driver="GeoJSON")

    # ---- recompute damage and cut-off with the same analysis code ----
    osm = RUN / "osm"
    buildings = gpd.read_file(osm / "buildings.geojson")
    roads = gpd.read_file(osm / "roads.geojson")
    bridges = gpd.read_file(osm / "bridges.geojson")
    settlements = gpd.read_file(osm / "settlements.geojson")
    destinations = gpd.read_file(osm / "destinations.geojson")
    analysed = gpd.read_file(RUN / "analysed_area.geojson")

    damage_new, _ = assess_damage(
        buildings, roads, bridges, None, debris_new, cfg,
        analysed_area=analysed)
    cutoff_new = analyze_cutoff(
        settlements, roads, destinations, debris_new, cfg,
        analysed_area=analysed)

    # ---- before (monsoon layer) numbers from the frozen run ----------
    old = json.loads((RUN / "results.json").read_text())
    old_damage = old["damage"]
    old_cutoff = old["cutoff"]
    # Three labelled building counts. STRtree against the ORIGINAL small
    # polygons in every case: unioning 16k debris polygons first (the
    # first version) created a multi-million-vertex geometry and the
    # centroid tests crawled; per-polygon tree queries are seconds.
    import shapely

    old_debris = gpd.read_file(RUN / "debris.geojson")
    analysed_m = analysed.to_crs(analysed.estimate_utm_crs())
    clip = analysed_m.geometry.union_all()
    shapely.prepare(clip)
    b_utm = buildings.to_crs(analysed_m.crs)
    cents = shapely.centroid(b_utm.geometry.values)
    in_analysed = shapely.intersects(cents, clip)
    b_in_analysed = b_utm[in_analysed]
    cents_in = cents[in_analysed]

    def _tree_hits(polys_utm) -> np.ndarray:
        tree = shapely.STRtree(np.asarray(polys_utm))
        pairs = tree.query(cents_in, predicate="intersects")
        return np.unique(pairs[1]) if pairs.size else np.array([], dtype=int)

    old_polys = old_debris.to_crs(analysed_m.crs).geometry.values
    b_in_old = b_in_analysed.iloc[_tree_hits(old_polys)]
    new_polys = debris_new.to_crs(analysed_m.crs).geometry.values
    b_in_new = b_in_analysed.iloc[_tree_hits(new_polys)]

    result = {
        "method": {
            "tiers": f"possible = control p{POSS_PCTL} z; likely = control "
                     f"p{LIKE_PCTL} z over the valley-floor domain",
            "thr_possible_z": round(thr_possible, 3),
            "thr_likely_z": round(thr_likely, 3),
            "confidence_mapping": {str(POSSIBLE_CONF): "possible",
                                   str(LIKELY_CONF): "likely"},
        },
        "event_attributed": {
            "area_km2_possible": round(float(mask_possible.sum() * px_km2), 3),
            "area_km2_likely": round(float(mask_likely.sum() * px_km2), 3),
            "n_polygons": len(debris_new),
        },
        "buildings_three_counts": {
            "hit_event_attributed": len(b_in_new),
            "inside_old_monsoon_debris_extent": len(b_in_old),
            "whole_analysed_area": len(b_in_analysed),
        },
        "before_after": {
            "buildings": {
                "old_likely": old_damage["buildings"]["likely_hit"],
                "old_possible": old_damage["buildings"]["possibly_hit"],
                "new_likely": damage_new["buildings"]["likely_hit"],
                "new_possible": damage_new["buildings"]["possibly_hit"],
            },
            "roads_km": {
                "old": old_damage["roads"]["total_length_km"],
                "new": damage_new["roads"]["total_length_km"],
            },
            "bridges": {
                "old_affected": old_damage["bridges"]["affected"],
                "new_affected": damage_new["bridges"]["affected"],
            },
            "cutoff": {
                "old_likely_cut": old_cutoff["likely_cut"],
                "old_possibly_cut": old_cutoff["possibly_cut"],
                "new_likely_cut": cutoff_new["likely_cut"],
                "new_possibly_cut": cutoff_new["possibly_cut"],
            },
        },
        "note_zero_likely": "a zero in the likely tiers means the control-"
                            "derived threshold was not reached; it does NOT "
                            "mean no one is affected - the possible tier and "
                            "the three building counts are the honest answers",
    }
    OUT.write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
    print(f"-> {OUT}")


if __name__ == "__main__":
    main()
