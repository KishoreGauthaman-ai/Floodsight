"""T8c: ONE-SHOT EMSR927 comparison (runs after the Round 4 freeze, D69).

Everything here was decided and frozen BEFORE this script read the
reference flood/damage geometries (only metadata was read earlier, D64).
Any code change after this run must be labelled "changed after seeing
the reference".

Compares, inside the COMMON AREA (reference AOIs intersected with our
analysed area, all in metric UTM):
* our event-attributed debris layer and the old monsoon-change layer
  against the reference observedEventA (mass-movement polygons):
  precision, recall, F1, IoU at 0, 30 and 100 m tolerance;
* building damage agreement and a confusion matrix (reference builtUpP
  damage grades vs our layer hit);
* road damage agreement (reference transportationL damage grades vs our
  layer overlap);
* a disagreement map (reference-minus-buffered-ours and
  ours-minus-buffered-reference) saved as GeoJSON;
* the five largest misses and five largest false alarms with coordinates;
* the date-gap effect: our post-event radar date vs the reference
  product dates extracted from the map PDFs.

Outputs: evaluation/results/emsr927/ (metrics.json, misses.geojson,
false_alarms.geojson, disagreement_*.geojson)

Run:  .venv/bin/python evaluation/emsr927.py --run 84.390_27.680_85.730_28.310_2026-08-26_fast
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import shapely
from shapely.geometry import box as shapely_box
from shapely.ops import unary_union

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

REFERENCE_DIR = REPO / "evaluation" / "reference" / "emsr927"
OUT_DIR = REPO / "evaluation" / "results" / "emsr927"
RUN = REPO / "runs"
TOLERANCES_M = (0.0, 30.0, 100.0)


def latest_products() -> dict[str, Path]:
    """Latest reference product directory per AOI (MONIT over PRODUCT)."""
    per_aoi: dict[str, list[Path]] = {}
    for d in sorted(REFERENCE_DIR.iterdir()):
        if d.is_dir() and d.name.startswith("EMSR927_AOI"):
            aoi = d.name.split("_")[1]
            per_aoi.setdefault(aoi, []).append(d)
    latest = {}
    for aoi, dirs in per_aoi.items():
        # MONIT products are later monitoring passes; sort MONIT > PRODUCT
        dirs.sort(key=lambda p: (p.name.find("MONIT") == -1, p.name))
        latest[aoi] = dirs[-1]
    return latest


def load_layer(prod_dir: Path, pattern: str) -> gpd.GeoDataFrame | None:
    hits = sorted(prod_dir.glob(f"*{pattern}*.shp"))
    if not hits:
        return None
    return gpd.read_file(hits[0])


def prf(ours, ref, tol_m, area_km2_of) -> dict:
    """Precision/recall/F1/IoU with symmetric tolerance buffering."""
    ours_b = shapely.buffer(ours, tol_m) if tol_m else ours
    ref_b = shapely.buffer(ref, tol_m) if tol_m else ref
    inter_or = shapely.intersection(ours, ref_b)
    inter_rf = shapely.intersection(ref, ours_b)
    union_b = shapely.union(ours_b, ref_b)
    inter_b = shapely.intersection(ours_b, ref_b)
    ours_a = area_km2_of(ours)
    ref_a = area_km2_of(ref)
    precision = area_km2_of(inter_or) / ours_a if ours_a > 0 else None
    recall = area_km2_of(inter_rf) / ref_a if ref_a > 0 else None
    iou = area_km2_of(inter_b) / area_km2_of(union_b) if area_km2_of(union_b) > 0 else None
    f1 = (2 * precision * recall / (precision + recall)
          if precision and recall and (precision + recall) > 0 else None)
    return {
        "tolerance_m": tol_m,
        "our_area_km2": round(ours_a, 4),
        "ref_area_km2_common": round(ref_a, 4),
        "precision": round(precision, 4) if precision is not None else None,
        "recall": round(recall, 4) if recall is not None else None,
        "f1": round(f1, 4) if f1 is not None else None,
        "iou": round(iou, 4) if iou is not None else None,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    args = ap.parse_args()
    run_dir = RUN / args.run
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    prods = latest_products()
    our_layers = {
        "event_attributed_debris": gpd.read_file(run_dir / "event_debris.geojson"),
        "old_monsoon_change_layer": gpd.read_file(run_dir / "debris.geojson"),
    }
    analysed = gpd.read_file(run_dir / "analysed_area.geojson")

    crs = analysed.estimate_utm_crs()
    analysed_m = analysed.to_crs(crs)
    analysed_u = analysed_m.geometry.union_all()
    ours_m = {k: v.to_crs(crs) for k, v in our_layers.items()}

    def km2(geom) -> float:
        return shapely.area(geom) / 1e6

    # ---- per-AOI inventory + common area ------------------------------
    aoi_report = {}
    ref_extent_polys, ref_built, ref_roads = [], [], []
    common_parts = []
    for aoi, d in sorted(prods.items()):
        ob = load_layer(d, "observedEventA")
        bl = load_layer(d, "builtUpP")
        tl = load_layer(d, "transportationL")
        aoi_g = load_layer(d, "areaOfInterestA")
        aoi_u = aoi_g.to_crs(crs).geometry.union_all() if aoi_g is not None else None
        common = shapely.intersection(aoi_u, analysed_u) if aoi_u else None
        if common is not None and not common.is_empty:
            common_parts.append(common)
        ob_m = ob.to_crs(crs) if ob is not None else None
        inside = 0.0
        total = 0.0
        if ob_m is not None and len(ob_m):
            for g in ob_m.geometry:
                total += km2(g)
                if aoi_u is not None:
                    inside += km2(shapely.intersection(g, aoi_u))
            ref_extent_polys.append(ob_m)
        if bl is not None and len(bl):
            ref_built.append(bl.to_crs(crs))
        if tl is not None and len(tl):
            ref_roads.append(tl.to_crs(crs))
        aoi_report[aoi] = {
            "product": d.name,
            "observedEventA_types": (ob["event_type"].value_counts().to_dict()
                                      if ob is not None and len(ob) else {}),
            "observedEventA_total_km2": round(total, 3),
            "builtUpP_damage": (bl["damage_gra"].value_counts().to_dict()
                                if bl is not None and len(bl) else {}),
            "transportationL_damage": (tl["damage_gra"].value_counts().to_dict()
                                      if tl is not None and len(tl) else {}),
            "aoi_km2": round(km2(aoi_u), 3) if aoi_u is not None else None,
            "common_area_km2": round(km2(common), 3) if common is not None else 0.0,
        }
    common = unary_union(common_parts) if common_parts else shapely_box(*analysed_u.bounds)

    # reference extent, buildings, roads clipped to the common area
    ref_extent = unary_union(
        [shapely.intersection(g, common)
         for obm in ref_extent_polys for g in obm.geometry
         if not shapely.is_empty(g)])
    ref_built = pd_concat_crs(ref_built, crs)
    ref_built = ref_built[ref_built.geometry.within(common.buffer(50))] \
        if len(ref_built) else ref_built
    ref_roads = pd_concat_crs(ref_roads, crs)

    # ---- extent metrics per layer at each tolerance ------------------
    extent_metrics = {}
    for name, gdf in ours_m.items():
        ours_u = shapely.intersection(
            unary_union(list(gdf.geometry)), common)
        extent_metrics[name] = {
            "per_tolerance": [prf(ours_u, ref_extent, t, km2)
                              for t in TOLERANCES_M],
        }

    # ---- building damage agreement + confusion matrix ----------------
    building_report = {}
    ref_damaged = ref_built[ref_built["damage_gra"].isin(
        ["Destroyed", "Damaged", "Possibly damaged"])] if len(ref_built) else ref_built
    for name, gdf in ours_m.items():
        tree = shapely.STRtree(np.asarray(gdf.geometry))
        if len(ref_damaged):
            pairs = tree.query(np.asarray(ref_damaged.geometry.values),
                               predicate="intersects")
            matched_ref = np.unique(pairs[1]) if pairs.size else []
        else:
            matched_ref = []
        building_report[name] = {
            "reference_damaged_points_in_common": len(ref_damaged),
            "matched_by_our_layer": len(matched_ref),
            "recall_of_damaged_points": round(len(matched_ref) / len(ref_damaged), 4)
            if len(ref_damaged) else None,
        }

    # confusion matrix: our layer says damaged (point inside our polygons)
    # vs reference grade, for ALL reference buildings in the common area
    cm = {}
    if len(ref_built):
        for name, gdf in ours_m.items():
            tree = shapely.STRtree(np.asarray(gdf.geometry))
            pairs = tree.query(np.asarray(ref_built.geometry.values),
                               predicate="intersects")
            hit_idx = set(np.unique(pairs[1]).tolist()) if pairs.size else set()
            grades = ref_built["damage_gra"].tolist()
            matrix = {}
            for i, g in enumerate(grades):
                key = (g, "our_layer_hit" if i in hit_idx else "our_layer_miss")
                matrix[key] = matrix.get(key, 0) + 1
            cm[name] = {f"{g} | {h}": n for (g, h), n in sorted(matrix.items())}

    # ---- road damage agreement ---------------------------------------
    road_report = {}
    ref_road_dmg = ref_roads[ref_roads["damage_gra"].isin(
        ["Destroyed", "Damaged", "Possibly damaged"])] if len(ref_roads) else ref_roads
    for name, gdf in ours_m.items():
        if len(ref_road_dmg):
            ours_u = unary_union(list(gdf.geometry))
            shapely.prepare(ours_u)
            hit = shapely.intersects(
                np.asarray(ref_road_dmg.geometry.values), ours_u)
            road_report[name] = {
                "reference_damaged_lines": len(ref_road_dmg),
                "intersecting_our_layer": int(hit.sum()),
            }
        else:
            road_report[name] = "no damaged reference roads"

    # ---- disagreement map + misses/false alarms ----------------------
    ours_ev = shapely.intersection(
        unary_union(list(ours_m["event_attributed_debris"].geometry)), common)
    ref_b100 = shapely.buffer(ref_extent, 100.0)
    miss_zone = shapely.difference(ref_extent, shapely.buffer(ours_ev, 100.0))
    fa_zone = shapely.difference(ours_ev, ref_b100)
    for label, zone in (("reference_minus_ours", miss_zone),
                       ("ours_minus_reference", fa_zone)):
        parts = list(zone.geoms) if zone.geom_type == "MultiPolygon" else [zone]
        parts = [p for p in parts if km2(p) > 0.005]
        gpd.GeoDataFrame(geometry=parts, crs=crs).to_crs("EPSG:4326").to_file(
            OUT_DIR / f"disagreement_{label}.geojson", driver="GeoJSON")

    def top5(zone, crs_src):
        parts = list(zone.geoms) if zone.geom_type == "MultiPolygon" else [zone]
        parts = sorted(parts, key=km2, reverse=True)[:5]
        out = []
        for p in parts:
            c4326 = gpd.GeoSeries([p], crs=crs_src).to_crs("EPSG:4326")
            lon = c4326.geometry.iloc[0].centroid.x
            lat = c4326.geometry.iloc[0].centroid.y
            out.append({"area_km2": round(km2(p), 4),
                        "centroid_lon": round(lon, 5),
                        "centroid_lat": round(lat, 5)})
        return out

    misses = top5(miss_zone, crs)
    false_alarms = top5(fa_zone, crs)

    # ---- date gap -----------------------------------------------------
    our_post = ("2026-08-28T12:21 (Sentinel-1 track 85, 62 h after the "
                "2026-08-25T22:00 event time)")
    date_gap = {
        "our_post_scene": our_post,
        "reference_note": "the GRA products are first-delivery maps and the "
                          "MONIT01 products are later monitoring passes "
                          "(activation last update 2026-09-10); exact "
                          "reference image dates are printed on the map PDFs",
        "effect": "a flash flood drains within hours, so standing water on "
                  "the +62 h scene is expected to be gone; the reference "
                  "mass-movement deposits persist, which is why the "
                  "comparison layer is debris/change, not water",
    }

    result = {
        "ran_against_frozen_state": "D69 hashes",
        "aoi_inventory": aoi_report,
        "common_area_km2": round(km2(common), 3),
        "extent_metrics": extent_metrics,
        "building_agreement": building_report,
        "building_confusion_matrix": cm,
        "road_agreement": road_report,
        "five_largest_misses": misses,
        "five_largest_false_alarms": false_alarms,
        "date_gap": date_gap,
    }
    (OUT_DIR / "metrics.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
    print(f"metrics -> {OUT_DIR / 'metrics.json'}")


def pd_concat_crs(frames, crs):
    if not frames:
        return gpd.GeoDataFrame(columns=["damage_gra"], geometry=[],
                                crs=crs)
    return gpd.GeoDataFrame(
        __import__("pandas").concat(frames, ignore_index=True), crs=crs)


if __name__ == "__main__":
    main()
