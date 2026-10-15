"""V4 (Round 5, v2): steep-terrain honesty - the third cut-off category.

v1 reports settlements as "still connected" when a route exists in the
road graph. But 43.85% of the corridor is masked (slope > 30 deg, D58):
a route that CROSSES masked terrain cannot be verified intact by radar,
so claiming "connected" there overstates certainty. This v2 analysis:

1. Rebuilds the same road graph and routes as v1 (same code path,
   same config).
2. For every settlement v1 called "still connected", tests its actual
   shortest path to the nearest destination: if any edge of that path
   runs outside the analysed area by more than UNASSESSED_MIN_OUTSIDE_M,
   the settlement is re-labelled "unassessed (route crosses unobserved
   terrain)" - never "connected".
3. Adds a clearly-labelled TERRAIN EXPOSURE scenario: roads, bridges and
   settlements inside the flood-path corridor AND the valley-floor
   domain (HAND <= 25 m, slope <= 15 deg). Exposure is a hazard-corridor
   statement, NOT observed damage.

Run:  .venv/bin/python evaluation/v4_terrain.py
Out:  evaluation/results/v4_terrain.json
      runs/<case>/cutoff_v2.json (third category, per-settlement status)
      runs/<case>/terrain_exposure.json (exposure counts + names)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import geopandas as gpd
import networkx as nx
import numpy as np
import shapely

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "evaluation"))

from event_attribution import STACK_ROOT, common_grid

from floodsight.analysis.cutoff import build_graph, snap_points
from floodsight.config import load_config

RUN = REPO / "runs/84.390_27.680_85.730_28.310_2026-08-26_fast"
OUT = REPO / "evaluation/results/v4_terrain.json"
UNASSESSED_MIN_OUTSIDE_M = 5.0  # an edge contributes if >5 m lies outside


def main() -> None:
    cfg = load_config()
    snap_max = float(cfg["cutoff"]["settlement_snap_max_m"])

    cutoff_v1 = json.loads((RUN / "cutoff.json").read_text())
    routes_cached = (RUN / "cutoff_v2.json").is_file()
    settlements = gpd.read_file(RUN / "osm" / "settlements.geojson")
    roads = gpd.read_file(RUN / "osm" / "roads.geojson")
    bridges = gpd.read_file(RUN / "osm" / "bridges.geojson")
    destinations = gpd.read_file(RUN / "osm" / "destinations.geojson")
    analysed = gpd.read_file(RUN / "analysed_area.geojson")

    crs = analysed.estimate_utm_crs()
    roads_m = roads.to_crs(crs)
    analysed_u = analysed.to_crs(crs).geometry.union_all()
    shapely.prepare(analysed_u)

    if routes_cached:
        graph = None; snapped = None; base_paths = {}
    else:
        graph = build_graph(roads_m)
        snapped = snap_points(settlements.to_crs(crs), graph, snap_max)
        dest_snapped = snap_points(destinations.to_crs(crs), graph, snap_max * 5)
        dest_nodes = {n for n in dest_snapped["graph_node"] if n is not None}
        base_paths = nx.multi_source_dijkstra_path(
            graph, {s for s in dest_nodes if s in graph})
    _edge_outside_cache: dict = {}

    # ---- v2 third category over v1 "still_connected" --------------------
    # v1 cutoff settlements are index-aligned with the settlements file
    if not routes_cached:
        assert len(cutoff_v1["settlements"]) == len(settlements) == len(snapped), \
            "settlement alignment broken"
    if routes_cached:
        v2_rows = json.loads((RUN / "cutoff_v2.json").read_text())["per_settlement"]
        print("route assessment loaded from cutoff_v2.json checkpoint")
    else:
        v2_rows = []
    for i, s_v1 in enumerate(cutoff_v1["settlements"] if not routes_cached else []):
        name = s_v1["name"]
        status = s_v1["status"]
        row = {"name": name, "v1_status": status, "v2_status": status,
               "route_outside_m": None}
        if status == "still_connected":
            node = snapped.iloc[i]["graph_node"]
            if node is None or node not in base_paths:
                row["v2_status"] = "unassessed (no route found)"
                v2_rows.append(row)
                continue
            path = base_paths[node]
            outside_m = 0.0
            for a, b in __import__('itertools').pairwise(path):
                if not graph.has_edge(a, b):
                    continue
                key = (a, b) if a < b else (b, a)
                if key in _edge_outside_cache:
                    outside_m += _edge_outside_cache[key]
                    continue
                seg = graph[a][b]["geometry"]
                # prepared predicate first: most edges lie fully inside the
                # analysed area and 'within' is index-accelerated; the
                # expensive difference() runs only for boundary-crossing
                # edges (paths share trunk edges - cache by unique edge)
                if shapely.within(seg, analysed_u):
                    _edge_outside_cache[key] = 0.0
                    continue
                outside = shapely.difference(seg, analysed_u)
                val = float(shapely.length(outside)) if not outside.is_empty else 0.0
                _edge_outside_cache[key] = val
                outside_m += val
            row["route_outside_m"] = round(outside_m, 1)
            row["v2_status"] = ("unassessed (route crosses unobserved terrain)"
                                if outside_m > UNASSESSED_MIN_OUTSIDE_M
                                else "connected (route inside analysed area)")
        v2_rows.append(row)

    counts = {}
    for r in v2_rows:
        counts[r["v2_status"]] = counts.get(r["v2_status"], 0) + 1
    # checkpoint the route assessment immediately (it is the expensive
    # part; the exposure scenario below must not be able to lose it)
    (RUN / "cutoff_v2.json").write_text(json.dumps(
        {"counts": counts, "per_settlement": v2_rows,
         "method": "v2 route-vs-masked-terrain (in progress)"}, indent=2))
    print("route assessment saved:", json.dumps(counts, indent=1))

    # ---- terrain exposure scenario (NOT observed damage) -----------------
    fp = json.loads((RUN / "flood_path.json").read_text())
    from shapely.geometry import shape as shp_shape
    cg = fp["corridor_geojson"]
    corridor = shp_shape(cg.get("geometry", cg))
    corridor_m = gpd.GeoSeries([corridor], crs="EPSG:4326").to_crs(crs).iloc[0]
    domain = np.load(STACK_ROOT / "domain_mask_common.npy")
    tr, width, height = common_grid()

    def in_domain(lon, lat) -> bool:
        import pyproj
        t = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:32645", always_xy=True)
        x, y = t.transform(lon, lat)
        c = int((x - tr.c) / tr.a)
        r_ = int((y - tr.f) / tr.e)
        return (0 <= r_ < height and 0 <= c < width and bool(domain[r_, c]))

    roads_in = float(roads_m.geometry.intersection(corridor_m).length.sum() / 1000)
    br_pts = bridges.to_crs("EPSG:4326").to_crs(crs)
    br_exposed = []
    for _, b in br_pts.iterrows():
        c4326 = bridges.loc[[b.name]].to_crs("EPSG:4326").geometry.iloc[0].centroid
        if corridor_m.intersects(b.geometry) and in_domain(c4326.x, c4326.y):
            br_exposed.append(str(b.get("name") or b.get("@id")))
    s_exposed = []
    for i, s_v1 in enumerate(cutoff_v1["settlements"]):
        geom = settlements.iloc[i].geometry
        c = geom.centroid
        sm = gpd.GeoSeries([geom], crs=settlements.crs).to_crs(crs).iloc[0]
        if corridor_m.intersects(sm) and in_domain(c.x, c.y):
            s_exposed.append(str(s_v1["name"]))
    exposure = {
        "label": "terrain exposure scenario - NOT observed damage",
        "definition": "roads, bridges and settlements inside the flood-path "
                      "corridor AND the valley-floor domain (HAND <= 25 m, "
                      "slope <= 15 deg)",
        "roads_km": round(roads_in, 1),
        "bridges": len(br_exposed),
        "settlements": len(s_exposed),
        "settlement_names": s_exposed,
    }

    result = {
        "method": {
            "v2_of": "v1 cutoff (frozen); same graph, same routing, same "
                     "config - only the route-vs-masked-terrain test is new",
            "unassessed_rule": f"v1 'still connected' settlements whose "
                               f"shortest route runs more than "
                               f"{UNASSESSED_MIN_OUTSIDE_M} m outside the "
                               "analysed area (slope>30 deg / shadow masks)",
            "masked_share_of_bbox": 0.4385,
        },
        "counts_v2": counts,
        "per_settlement": v2_rows,
        "terrain_exposure": exposure,
    }
    OUT.write_text(json.dumps(result, indent=2))
    (RUN / "cutoff_v2.json").write_text(json.dumps(
        {"counts": counts, "per_settlement": v2_rows,
         "method": result["method"]}, indent=2))
    (RUN / "terrain_exposure.json").write_text(json.dumps(exposure, indent=2))
    print(json.dumps({"counts_v2": counts,
                      "terrain_exposure": {k: exposure[k] for k in
                                           ("roads_km", "bridges", "settlements")}},
                     indent=2))
    print(f"-> {OUT}")


if __name__ == "__main__":
    main()
