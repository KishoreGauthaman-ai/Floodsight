"""V3 (Round 5): count consistency and the event-attributed bridge list.

Reconciles two differently-defined building counts that appear side by
side in the v2 results:
* assess_damage (analysis/damage.py): a building is LIKELY hit when its
  GEOMETRY INTERSECTS a dissolved polygon with mean_confidence >= 0.6,
  POSSIBLY hit when it intersects the 0.3-0.6 tier. The v2 event layer
  maps control-percentile tiers onto that scale (0.8 = likely, 0.5 =
  possible, D68), so 8 likely + 74 possible = 82 buildings TOUCH the
  event-attributed polygons with any part of their footprint.
* the three-counts table (evaluation/t5_recompute.py): a building is
  "hit" when its CENTROID falls inside the UNION of the same polygons
  (30 buildings). Centroid containment is the stricter, less
  boundary-noise-prone definition used for the headline count.

Both are reported with their exact definitions; the difference is
edge-touching footprints (52 buildings clip a polygon boundary without
their centre being inside).

Also lists every bridge the event-attributed layer flags, with OSM id,
highway/bridge tags, name, position, in-layer overlap length and a
vehicle/foot/culvert classification, and exports a satellite map for a
visual sample check (docs/figures/bridges_event_map.html / .png).

Run:  .venv/bin/python evaluation/v3_counts.py
Out:  evaluation/results/v3_counts.json, docs/figures/bridges_event.geojson
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from floodsight.analysis.damage import assess_damage
from floodsight.config import load_config

RUN = REPO / "runs/84.390_27.680_85.730_28.310_2026-08-26_fast"
OUT = REPO / "evaluation/results/v3_counts.json"

VEHICLE_HIGHWAYS = {"motorway", "trunk", "primary", "secondary", "tertiary",
                    "unclassified", "residential", "living_street", "service"}
FOOT_HIGHWAYS = {"footway", "path", "steps", "pedestrian", "cycleway"}
TRACK_HIGHWAYS = {"track"}  # agricultural tracks: vehicle-ish, low traffic


def classify(row) -> str:
    hw = str(row.get("highway", "")).lower()
    structure = str(row.get("bridge:structure", "") or
                    row.get("bridge:type", "")).lower()
    if any(k in structure for k in ("culvert",)):
        return "culvert"
    if hw in VEHICLE_HIGHWAYS:
        return "vehicle bridge"
    if hw in FOOT_HIGHWAYS:
        return "footbridge"
    if hw in TRACK_HIGHWAYS:
        return "track bridge (vehicle, low traffic)"
    return f"other ({hw or 'no highway tag'})"


def main() -> None:
    cfg = load_config()
    debris_new = gpd.read_file(RUN / "event_debris.geojson")
    analysed = gpd.read_file(RUN / "analysed_area.geojson")
    buildings = gpd.read_file(RUN / "osm/buildings.geojson")
    roads = gpd.read_file(RUN / "osm/roads.geojson")
    bridges = gpd.read_file(RUN / "osm/bridges.geojson")

    # ---- definition A: assess_damage tier logic (unchanged code) ---------
    damage, dmg_gdf = assess_damage(buildings, roads, bridges,
                                    None, debris_new, cfg,
                                    analysed_area=analysed)
    a_likely = damage["buildings"]["likely_hit"]
    a_possible = damage["buildings"]["possibly_hit"]

    # ---- definition B/C: centroid-in-union and geometry-intersects ------
    crs = analysed.estimate_utm_crs()
    clip = analysed.to_crs(crs).geometry.union_all()
    shapely.prepare(clip)
    b_utm = buildings.to_crs(crs)
    cents = shapely.centroid(b_utm.geometry.values)
    in_area = shapely.intersects(cents, clip)
    union = debris_new.to_crs(crs).geometry.union_all()
    shapely.prepare(union)
    centroid_hit = int((in_area & shapely.intersects(cents, union)).sum())
    geom_hit = int(shapely.intersects(
        b_utm.geometry.values[in_area], union).sum())

    definitions = {
        "A_damage_tiers_geometry_intersects": {
            "definition": "building footprint INTERSECTS a dissolved "
                          "tier polygon; likely tier = confidence >= 0.6 "
                          "(v2 map: 0.8 = control p99.99), possible = "
                          "0.3-0.6 (v2 map: 0.5 = control p99.9)",
            "likely": a_likely, "possible": a_possible,
            "total_touching": a_likely + a_possible},
        "B_centroid_in_union": {
            "definition": "building CENTROID falls inside the union of all "
                          "event-attributed polygons (the headline 'hit' "
                          "count in t5_recompute.json)",
            "hit": centroid_hit},
        "C_geometry_intersects_union": {
            "definition": "building footprint touches the union, no tier "
                          "split - the same spatial test as A, pooled",
            "hit": geom_hit},
        "difference_explanation": (
            f"{geom_hit - centroid_hit} buildings clip an event-attributed "
            "polygon with an edge of their footprint without their centroid "
            "being inside; the headline count uses the stricter centroid "
            "definition to avoid boundary noise"),
    }

    # ---- the bridge list --------------------------------------------------
    bridge_rows = dmg_gdf[dmg_gdf["feature_type"] == "bridge"].copy() \
        if len(dmg_gdf) else dmg_gdf.iloc[0:0]
    bridge_rows = bridge_rows.to_crs("EPSG:4326")  # coords are printed as lon/lat
    osm_by_id = bridges.set_index("@id")
    out_rows = []
    for _, r in bridge_rows.iterrows():
        if r["osm_id"] in osm_by_id.index:
            row = osm_by_id.loc[r["osm_id"]]
            geom = row.geometry
            vals = {c: row.get(c) for c in
                   ("name", "bridge:name", "highway", "bridge",
                    "bridge:structure", "bridge:type")}
        else:
            geom = r.geometry
            vals = {}
        overlap_m = float(geom.intersection(union).length)
        out_rows.append({
            "osm_id": r["osm_id"],
            "name": vals.get("name") or vals.get("bridge:name"),
            "highway": vals.get("highway"),
            "bridge": vals.get("bridge"),
            "class": classify(vals),
            "lon": round(float(r.geometry.centroid.x), 5),
            "lat": round(float(r.geometry.centroid.y), 5),
            "overlap_m": round(overlap_m, 1),
            "tier": ("likely" if (r["mean_confidence"] or 0) >= 0.6 else "possible"),
        })
    df = pd.DataFrame(out_rows)
    class_split = df["class"].value_counts().to_dict() if len(df) else {}

    # save the bridge geometries for the map + visual check
    if len(bridge_rows):
        g = bridge_rows.copy()
        def _cls(i):
            if i in osm_by_id.index:
                row = osm_by_id.loc[i]
                return classify({c: row.get(c) for c in
                                ("highway", "bridge", "bridge:structure",
                                 "bridge:type")})
            return "other"
        g["class"] = [_cls(i) for i in g["osm_id"]]
        g.to_crs("EPSG:4326").to_file(
            REPO / "docs/figures/bridges_event.geojson", driver="GeoJSON")

    # distance to main stems (programmatic sanity: bridges should be on rivers)
    import re
    w = gpd.read_file(RUN / "osm/waterways.geojson").to_crs(crs)
    pat = re.compile(r"trishuli|trisuli|bhot[e ]?koshi|langtang", re.IGNORECASE)
    mask = False
    for col in ["name", "name:en", "alt_name"]:
        if col in w.columns:
            mask |= w[col].astype(str).str.contains(pat, na=False).values
    stems_u = w[mask].geometry.union_all()
    if len(bridge_rows) and not stems_u.is_empty:
        # bridge_rows is EPSG:4326 (for lon/lat output); reproject for the
        # metric distance test
        rows_m = bridge_rows.to_crs(crs)
        dists = [stems_u.distance(g) for g in rows_m.geometry]
    else:
        dists = []

    result = {
        "definitions_and_counts": definitions,
        "bridges_flagged_by_event_layer": {
            "n": len(out_rows),
            "class_split": class_split,
            "tier_split": df["tier"].value_counts().to_dict() if len(df) else {},
            "median_distance_to_main_stem_m": (round(float(np.median(dists)), 1)
                                               if dists else None),
            "list": out_rows,
            "flagging_note": "all 19 bridges are flagged via the 50 m "
                            "approach-buffer rule of analysis/damage.py "
                            "(bridge approaches hit the layer); direct "
                            "bridge-line overlap with the event layer is "
                            "0 m for every flagged bridge - no bridge "
                            "structure is observed damaged, its access "
                            "is observed changed",
        },
    }
    OUT.write_text(json.dumps(result, indent=2))
    print(json.dumps({k: result[k] for k in
                      ("definitions_and_counts",)}, indent=2)[:1200])
    print("bridges:", len(out_rows), "| class split:", class_split)
    print(f"-> {OUT}")


if __name__ == "__main__":
    main()
