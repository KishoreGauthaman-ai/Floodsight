"""Cut-off settlement analysis on the pre-event road graph.

Baseline connectivity first: settlements that could NOT reach a town or
hospital before the event are reported as "already isolated", never blamed
on the flood. Flood-affected road segments and bridges are then removed in
two confidence scenarios (likely cut / possibly cut), connectivity is
recomputed with detour lengths, and each settlement carries the evidence of
which road segments failed.

The road graph is built from the same dated OSM snapshot as everything else
(DECISIONS D19); live OSM would be a data-rule violation. Lengths are
computed in a local metric UTM CRS; output coordinates are EPSG:4326.
"""

from __future__ import annotations

import logging
import math
from itertools import pairwise
from typing import Any

import geopandas as gpd
import networkx as nx
from shapely.geometry import LineString

logger = logging.getLogger(__name__)


class CutoffError(Exception):
    """Cut-off analysis failed."""


def _utm_crs(gdf: gpd.GeoDataFrame) -> Any:
    return gdf.estimate_utm_crs()


def build_graph(roads_gdf: gpd.GeoDataFrame) -> nx.Graph:
    """Road graph from linestrings; nodes are shared vertices.

    All geometry must already be in a metric CRS (callers reproject first).
    Edge attributes: osm_id, highway, name, bridge, length_m, geometry.
    """
    g = nx.Graph()
    for idx, row in roads_gdf.iterrows():
        geom = row.geometry
        lines = [geom] if isinstance(geom, LineString) else list(geom.geoms)
        for line in lines:
            coords = list(line.coords)
            for a, b in pairwise(coords):
                if a == b:
                    continue
                seg = LineString([a, b])
                length = seg.length
                if length <= 0:
                    continue
                g.add_node(a, x=a[0], y=a[1])
                g.add_node(b, x=b[0], y=b[1])
                if g.has_edge(a, b) and g[a][b]["length_m"] <= length:
                    continue
                g.add_edge(
                    a, b, osm_id=row.get("@id", idx), highway=row.get("highway"),
                    name=row.get("name"), bridge=row.get("bridge"),
                    length_m=length, geometry=seg,
                )
    if g.number_of_nodes() == 0:
        raise CutoffError("Road graph is empty; no routable roads in the area")
    return g


def snap_points(points_gdf: gpd.GeoDataFrame, graph: nx.Graph, max_m: float):
    """Snap point features to the nearest graph node within max_m.

    Returns (snapped_gdf, results) where results rows carry the node id and
    the snap distance; features farther than max_m get node None
    ("no road mapped" — never counted as cut off).
    """
    import numpy as np

    nodes = np.array([(d["x"], d["y"]) for _, d in graph.nodes(data=True)])
    node_ids = list(graph.nodes)
    snapped, dists, node_idx = [], [], []
    for geom in points_gdf.geometry:
        if geom is None or geom.is_empty:
            snapped.append(None); dists.append(None); node_idx.append(None)
            continue
        pt = geom.centroid if geom.geom_type != "Point" else geom
        d = np.hypot(nodes[:, 0] - pt.x, nodes[:, 1] - pt.y)
        i = int(np.argmin(d))
        if d[i] <= max_m:
            snapped.append(node_ids[i]); dists.append(float(d[i])); node_idx.append(i)
        else:
            snapped.append(None); dists.append(float(d[i])); node_idx.append(None)
    out = points_gdf.copy()
    out["graph_node"] = snapped
    out["snap_distance_m"] = dists
    return out


def flood_affected_edges(
    graph: nx.Graph,
    flood_gdf: gpd.GeoDataFrame | None,
    min_confidence: float,
) -> list[tuple]:
    """Edges intersecting flood polygons at or above the confidence tier."""
    if flood_gdf is None or len(flood_gdf) == 0:
        return []
    polys = flood_gdf[flood_gdf["mean_confidence"] >= min_confidence]
    if len(polys) == 0:
        return []
    united = polys.union_all()  # one intersects() call per edge, not per polygon
    return [
        (u, v)
        for u, v, data in graph.edges(data=True)
        if data["geometry"].intersects(united)
    ]


def _multi_source_distances(graph: nx.Graph, sources: list) -> dict:
    """Shortest distance from any source node to every node."""
    return nx.multi_source_dijkstra_path_length(
        graph, {s for s in sources if s in graph}, weight="length_m"
    )


def analyze_cutoff(
    settlements: gpd.GeoDataFrame,     # EPSG:4326, place points with names
    roads: gpd.GeoDataFrame,           # EPSG:4326, highway linestrings
    destinations: gpd.GeoDataFrame,   # EPSG:4326, towns/hospitals/clinics
    flood_polygons: gpd.GeoDataFrame | None,  # EPSG:4326 + mean_confidence
    cfg: dict[str, Any],
    analysed_area: gpd.GeoDataFrame | None = None,
) -> dict[str, Any]:
    """Baseline + two-scenario cut-off analysis. Returns the cutoff section
    of the results contract plus per-settlement details."""
    crs_metric = _utm_crs(settlements)
    snap_max = float(cfg["cutoff"]["settlement_snap_max_m"])
    conf = cfg["detection"]["confidence"]

    roads_m = roads.to_crs(crs_metric)
    graph = build_graph(roads_m)

    settlements_m = settlements.to_crs(crs_metric)
    snapped = snap_points(settlements_m, graph, snap_max)
    dest_m = destinations.to_crs(crs_metric) if len(destinations) else destinations
    if len(dest_m) == 0:
        raise CutoffError(
            "No destinations (towns/hospitals/clinics) in the OSM snapshot "
            "for this area; cannot define cut-off."
        )
    dest_snapped = snap_points(dest_m, graph, snap_max * 5)
    dest_nodes = [n for n in dest_snapped["graph_node"] if n is not None]
    if not dest_nodes:
        raise CutoffError("No destination could be snapped to the road graph")

    base_dist = _multi_source_distances(graph, dest_nodes)

    # flood edge removal per scenario
    flood_m = None
    if flood_polygons is not None and len(flood_polygons):
        cols = flood_polygons.columns.tolist()
        flood_m = flood_polygons.to_crs(crs_metric)
        if "mean_confidence" not in cols and len(flood_m):
            flood_m = flood_m.assign(mean_confidence=1.0)

    scenarios = {}
    for label, threshold in (("likely", conf["likely_min"]),
                             ("possible", conf["possible_min"])):
        g2 = graph.copy()
        affected = []
        if flood_m is not None and len(flood_m):
            affected = flood_affected_edges(g2, flood_m, threshold)
            g2.remove_edges_from(affected)
        dist = _multi_source_distances(g2, dest_nodes)
        scenarios[label] = {"graph": g2, "dist": dist, "removed": len(affected)}

    # ONE Dijkstra from the destination set gives every settlement its
    # original route (per-settlement-per-destination searches are O(N*M)
    # and were too slow on the real road network).
    base_paths = nx.multi_source_dijkstra_path(graph, {s for s in dest_nodes if s in graph})

    # lon/lat for the results contract come from the original EPSG:4326
    # geometries (same row order as the snapped frame)
    lonlat = list(zip(settlements.geometry.x, settlements.geometry.y))

    analysed_geom = None
    if analysed_area is not None and len(analysed_area):
        # the settlements are in the METRIC CRS by this point: transform the
        # analysed polygon (EPSG:4326) to the same CRS before the within
        # test - the previous CRS mismatch marked every settlement
        # 'not_analysed' (verified: 812 of 899 are inside the polygon)
        analysed_geom = analysed_area.geometry.union_all()
        if analysed_area.crs != crs_metric:

            analysed_geom = gpd.GeoSeries(
                [analysed_geom], crs=analysed_area.crs
            ).to_crs(crs_metric).iloc[0]

    results_rows = []
    already_isolated, likely_cut, possibly_cut = [], [], []
    still_connected = 0
    not_analysed = 0

    def _clean(value):
        """NaN/None-safe value for names, populations and JSON payloads."""
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return None
        if isinstance(value, str) and value.strip():
            return value
        if not isinstance(value, str):
            return value
        return None

    for i, (_, row) in enumerate(snapped.iterrows()):
        node = row["graph_node"]
        lon, lat = lonlat[i]
        name = _clean(row.get("name")) or f"settlement@{lon:.4f},{lat:.4f}"
        entry = {
            "name": name,
            "lon": round(float(lon), 6),
            "lat": round(float(lat), 6),
            "population": _clean(row.get("population")),
            "snap_distance_m": round(row["snap_distance_m"], 1)
            if row["snap_distance_m"] is not None else None,
            "status": None, "detour_length_m": None, "failed_segments": None,
        }
        if analysed_geom is not None and not row.geometry.within(analysed_geom):
            entry["status"] = "not_analysed"
            not_analysed += 1
            results_rows.append(entry)
            continue
        if node is None:
            entry["status"] = "no_road_mapped"
            results_rows.append(entry)
            continue
        base_len = base_dist.get(node)
        if base_len is None:
            entry["status"] = "already_isolated"
            already_isolated.append(name)
            results_rows.append(entry)
            continue
        likely_len = scenarios["likely"]["dist"].get(node)
        possible_len = scenarios["possible"]["dist"].get(node)
        evidence = _evidence_from_path(
            base_paths.get(node, []), graph, scenarios["likely"]["graph"]
        )
        # the "possible" scenario removes a superset of the "likely" edges,
        # so cut-in-likely implies cut-in-possible
        if likely_len is None:
            entry["status"] = "likely_cut"
            entry["failed_segments"] = evidence
            likely_cut.append(name)
        elif possible_len is None:
            entry["status"] = "possibly_cut"
            entry["failed_segments"] = evidence
            possibly_cut.append(name)
        else:
            entry["status"] = "still_connected"
            entry["detour_length_m"] = round(likely_len - base_len, 1) if likely_len > base_len else 0.0
            entry["failed_segments"] = evidence  # explains the detour
            still_connected += 1
        results_rows.append(entry)

    return {
        "already_isolated": sorted(already_isolated),
        "likely_cut": sorted(likely_cut),
        "possibly_cut": sorted(possibly_cut),
        "still_connected_count": still_connected,
        "not_analysed_count": not_analysed,
        "settlements": results_rows,
    }


def _evidence_from_path(path, graph, removed_graph) -> list[dict]:
    """Which segments of this settlement's original route are now removed."""
    failed = []
    for a, b in pairwise(path):
        if not removed_graph.has_edge(a, b):
            data = graph[a][b]
            failed.append(
                {"road": data.get("name") or data.get("highway") or "unnamed",
                 "highway": data.get("highway"), "osm_id": data.get("osm_id")}
            )
    return failed
