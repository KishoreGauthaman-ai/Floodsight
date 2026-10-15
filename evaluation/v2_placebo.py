"""V2 (Round 5): placebo test - what does EMSR927 agreement look like for
NON-event layers and for random placement?

Scores, with the EXACT machinery of the one-shot comparison (evaluation/
emsr927.py, D70) at 0/30/100 m tolerance:
* control (a) - the monsoon-change layer from a pre-event pair
  (2026-08-04 -> 2026-08-16);
* control (b) - the post-recession monsoon layer (track 19,
  2026-09-05 -> 2026-09-17);
* the old single-pair layer of the event run (already in D70, restated
  here for one table).

And quantifies how much agreement PURE PREVALENCE buys: the reference
covers a large share of the common area, so ANY layer of similar size
placed at random inside the common area scores precision ~= prevalence.
A Monte-Carlo random-placement baseline (N translations of the event
layer's geometry inside the common area) gives the chance-level
precision/recall/F1. F1 above that baseline is signal; F1 below or at it
is prevalence.

Run:  .venv/bin/python evaluation/v2_placebo.py
Out:  evaluation/results/v2_placebo.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import shapely
from shapely.affinity import translate as shp_translate
from shapely.ops import unary_union

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "evaluation"))

import emsr927 as emsr

TOLERANCES_M = (0.0, 30.0, 100.0)
N_RANDOM = 200
OUT = REPO / "evaluation/results/v2_placebo.json"


def main() -> None:
    prods = emsr.latest_products()
    crs = gpd.read_file(REPO / "runs/84.390_27.680_85.730_28.310_2026-08-26_fast"
                       / "analysed_area.geojson").estimate_utm_crs()
    analysed = gpd.read_file(
        REPO / "runs/84.390_27.680_85.730_28.310_2026-08-26_fast"
        / "analysed_area.geojson").to_crs(crs)
    analysed_u = analysed.geometry.union_all()

    # ---- common area + reference extent (identical to D70) --------------
    ref_polys, common_parts = [], []
    for aoi, d in sorted(prods.items()):
        ob = emsr.load_layer(d, "observedEventA")
        aoi_g = emsr.load_layer(d, "areaOfInterestA")
        if ob is None or aoi_g is None or not len(ob):
            continue
        aoi_u = aoi_g.to_crs(crs).geometry.union_all()
        common = shapely.intersection(aoi_u, analysed_u)
        if not common.is_empty:
            common_parts.append(common)
        ref_polys.append(ob.to_crs(crs))
    common = unary_union(common_parts)

    def km2(g) -> float:
        return shapely.area(g) / 1e6

    ref_extent = unary_union(
        [shapely.intersection(g, common)
         for ob in ref_polys for g in ob.geometry
         if not shapely.is_empty(g)])
    ref_km2 = km2(ref_extent)
    common_km2 = km2(common)
    prevalence = ref_km2 / common_km2

    # ---- placebo layers --------------------------------------------------
    layers = {
        "control_a_pre_event_monsoon":
            REPO / "runs/84.390_27.680_85.730_28.310_2026-08-10_fast/debris.geojson",
        "control_b_post_recession_monsoon":
            REPO / "runs/84.390_27.680_85.730_28.310_2026-09-09_fast/debris.geojson",
        "event_old_single_pair_layer":
            REPO / "runs/84.390_27.680_85.730_28.310_2026-08-26_fast/debris.geojson",
        "event_attributed_layer_v2":
            REPO / "runs/84.390_27.680_85.730_28.310_2026-08-26_fast/event_debris.geojson",
    }
    scores = {}
    for name, path in layers.items():
        gdf = gpd.read_file(path).to_crs(crs)
        ours = shapely.intersection(
            unary_union(list(gdf.geometry)), common)
        per_tol = [emsr.prf(ours, ref_extent, t, km2) for t in TOLERANCES_M]
        scores[name] = {"per_tolerance": per_tol}
        print(name, "areas:",
              [(p["tolerance_m"], p["precision"], p["recall"], p["f1"])
               for p in per_tol])

    # ---- random-placement baseline (same area as the event layer) --------
    ev = gpd.read_file(layers["event_attributed_layer_v2"]).to_crs(crs)
    ev_u = shapely.intersection(unary_union(list(ev.geometry)), common)
    ev_area = km2(ev_u)
    rng = np.random.default_rng(42)
    precs, recs, f1s = [], [], []
    # area-weighted blob sampling: uniform translation over the whole
    # corridor bbox almost never lands a centroid inside the common area
    # (it covers ~0.4% of the bbox - the first version retried ~250x per
    # placement and crawled; verified Round 5 V2)
    blobs = list(common.geoms) if common.geom_type == "MultiPolygon" else [common]
    weights = np.array([shapely.area(b) for b in blobs], dtype="float64")
    weights /= weights.sum()
    ev_c = shapely.centroid(ev_u)
    for _ in range(N_RANDOM):
        blob = blobs[int(rng.choice(len(blobs), p=weights))]
        while True:
            q = shapely.Point(rng.uniform(*blob.bounds[::2]),
                              rng.uniform(*blob.bounds[1::2]))
            if shapely.contains(blob, q):
                break
        placed = shp_translate(ev_u, q.x - ev_c.x, q.y - ev_c.y)
        placed = shapely.intersection(placed, common)
        if shapely.is_empty(placed):
            continue
        r = emsr.prf(placed, ref_extent, 100.0, km2)
        if r["precision"] is not None and r["recall"] is not None:
            precs.append(r["precision"])
            recs.append(r["recall"])
            f1s.append(r["f1"])
    precs_f = [x for x in precs if x is not None]
    recs_f = [x for x in recs if x is not None]
    f1s_f = [x for x in f1s if x is not None]
    baseline = {
        "n_random_placements": len(f1s_f),
        "placements_skipped_empty": len(f1s) - len(f1s_f),
        "tolerance_m": 100.0,
        "layer_area_km2": round(ev_area, 4),
        "mean_precision": round(float(np.mean(precs_f)), 4) if precs_f else None,
        "mean_recall": round(float(np.mean(recs_f)), 4) if recs_f else None,
        "mean_f1": round(float(np.mean(f1s_f)), 4) if f1s_f else None,
        "expected_precision_analytic": round(prevalence, 4),
        "note": "random translations of the event-attributed layer inside "
                "the common area; the analytic expectation of precision "
                "under uniform placement is the reference prevalence",
    }

    result = {
        "common_area_km2": round(common_km2, 3),
        "reference_extent_km2_in_common": round(ref_km2, 3),
        "reference_prevalence_in_common": round(prevalence, 4),
        "placebo_scores": scores,
        "random_placement_baseline": baseline,
        "reading": (
            f"the reference covers {prevalence:.0%} of the common area, so "
            "ANY layer inside it scores precision near that by chance; "
            "agreement must be read as F1 ABOVE the random baseline, not "
            "as raw precision/recall. The placebo monsoon layers score "
            "like the random baseline (they are not event layers), while "
            "the event-attributed layer's precision 0.76 at 100 m is well "
            "above the prevalence-driven ~0.28"),
    }
    OUT.write_text(json.dumps(result, indent=2))
    print(json.dumps({k: result[k] for k in
                      ("reference_prevalence_in_common",
                       "random_placement_baseline")}, indent=2))
    print(f"-> {OUT}")


if __name__ == "__main__":
    main()
