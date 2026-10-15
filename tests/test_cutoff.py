"""Cut-off analysis tests with a synthetic road graph (AGENT.md 8.5).

Layout (EPSG:4326, near London so UTM math is well-behaved):

    town A (destination, 0.00,51.50)
      |  \\  (main road with a BRIDGE at 0.010-0.012)
      |   \\____ village B (0.02,51.50)
      |   /
      loop road via (0.00,51.52) -> (0.02,51.52)
    hamlet C at (0.05,51.55) - no road within snap distance
    village D on a dead-end road (0.03,51.53)->(0.035,51.53) - never connected

Flood scenarios:
    polygon P1 (confidence 0.9): covers the bridge segment
    polygon P2 (confidence 0.4): covers the dead-end road (low confidence)
"""

from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import LineString, Point, Polygon

from floodsight.analysis.cutoff import (
    CutoffError,
    analyze_cutoff,
    build_graph,
    snap_points,
)
from floodsight.config import load_config


def _make_inputs():
    roads = gpd.GeoDataFrame(
        {
            "highway": ["primary", "primary", "primary", "primary", "track"],
            "bridge": [None, "yes", None, None, None],
            "name": ["main", "main-bridge", "main", "loop", "deadend"],
            "@id": [1, 2, 3, 4, 5],
        },
        geometry=[
            LineString([(0.0, 51.50), (0.01, 51.50)]),
            LineString([(0.01, 51.50), (0.012, 51.50)]),
            LineString([(0.012, 51.50), (0.02, 51.50)]),
            LineString([(0.0, 51.50), (0.0, 51.52), (0.02, 51.52), (0.02, 51.50)]),
            LineString([(0.03, 51.53), (0.035, 51.53)]),
        ],
        crs="EPSG:4326",
    )
    settlements = gpd.GeoDataFrame(
        {"name": ["village B", "hamlet C", "village D"]},
        geometry=[Point(0.02, 51.5002), Point(0.05, 51.55), Point(0.0345, 51.53)],
        crs="EPSG:4326",
    )
    destinations = gpd.GeoDataFrame(
        {"name": ["town A"], "place": ["town"]},
        geometry=[Point(0.0, 51.50)],
        crs="EPSG:4326",
    )
    flood = gpd.GeoDataFrame(
        {"mean_confidence": [0.9, 0.4]},
        geometry=[
            Polygon([(0.009, 51.495), (0.013, 51.495), (0.013, 51.505), (0.009, 51.505)]),
            Polygon([(0.029, 51.525), (0.036, 51.525), (0.036, 51.535), (0.029, 51.535)]),
        ],
        crs="EPSG:4326",
    )
    return roads, settlements, destinations, flood


CFG = load_config()


def test_graph_built_from_linestrings() -> None:
    roads, *_ = _make_inputs()
    g = build_graph(roads.to_crs(roads.estimate_utm_crs()))
    assert g.number_of_nodes() == 8  # 8 distinct vertices
    assert g.number_of_edges() == 7  # 5 segments minus merged loop overlap


def test_full_scenario_matrix() -> None:
    roads, settlements, destinations, flood = _make_inputs()
    result = analyze_cutoff(settlements, roads, destinations, flood, CFG)
    by_name = {r["name"]: r for r in result["settlements"]}

    b = by_name["village B"]
    # baseline: short path over the bridge; likely scenario: bridge removed,
    # loop intact -> still connected WITH a detour
    assert b["status"] == "still_connected"
    assert b["detour_length_m"] > 0
    # the evidence points at the failed bridge
    assert any(seg["highway"] == "primary" for seg in b["failed_segments"])

    # hamlet C: farther than the 2 km snap limit -> no road mapped, never cut
    c = by_name["hamlet C"]
    assert c["status"] == "no_road_mapped"
    assert c["snap_distance_m"] > 2000

    # village D: on a dead-end road, was never connected -> already isolated
    d = by_name["village D"]
    assert d["status"] == "already_isolated"
    assert "village D" in result["already_isolated"]


def test_possible_scenario_cuts_village_b() -> None:
    roads, settlements, destinations, _ = _make_inputs()
    # low-confidence flood on the north loop edge: village B is cut only in
    # the "possible" tier (bridge already likely-removed -> likely cut in
    # the aggressive test below)
    flood = gpd.GeoDataFrame(
        {"mean_confidence": [0.9, 0.4]},
        geometry=[
            Polygon([(0.009, 51.495), (0.013, 51.495), (0.013, 51.505), (0.009, 51.505)]),
            Polygon([(-0.002, 51.517), (0.022, 51.517), (0.022, 51.523), (-0.002, 51.523)]),
        ],
        crs="EPSG:4326",
    )
    result = analyze_cutoff(settlements, roads, destinations, flood, CFG)
    by_name = {r["name"]: r for r in result["settlements"]}
    assert by_name["village B"]["status"] == "possibly_cut"
    assert "village B" in result["possibly_cut"]


def test_aggressive_tiers_make_village_b_likely_cut() -> None:
    roads, settlements, destinations, _ = _make_inputs()
    flood = gpd.GeoDataFrame(
        {"mean_confidence": [0.9, 0.4]},
        geometry=[
            Polygon([(0.009, 51.495), (0.013, 51.495), (0.013, 51.505), (0.009, 51.505)]),
            Polygon([(-0.002, 51.517), (0.022, 51.517), (0.022, 51.523), (-0.002, 51.523)]),
        ],
        crs="EPSG:4326",
    )
    # lower the tiers so BOTH flood polygons count as "likely" ->
    # bridge AND loop edge removed -> village B is likely cut
    cfg = {**CFG, "detection": {"confidence": {"likely_min": 0.2, "possible_min": 0.1}}}
    result = analyze_cutoff(settlements, roads, destinations, flood, cfg)
    by_name = {r["name"]: r for r in result["settlements"]}
    assert by_name["village B"]["status"] == "likely_cut"
    assert "village B" in result["likely_cut"]


def test_no_flood_keeps_everyone_connected() -> None:
    roads, settlements, destinations, _ = _make_inputs()
    result = analyze_cutoff(settlements, roads, destinations, None, CFG)
    by_name = {r["name"]: r for r in result["settlements"]}
    assert by_name["village B"]["status"] == "still_connected"
    assert by_name["village B"]["detour_length_m"] == 0.0
    assert result["still_connected_count"] == 1


def test_missing_destinations_raise_clearly() -> None:
    roads, settlements, _, flood = _make_inputs()
    empty = settlements.iloc[0:0]
    with pytest.raises(CutoffError, match="destinations"):
        analyze_cutoff(settlements, roads, empty, flood, CFG)


def test_snap_points_respects_limit() -> None:
    roads, *_ = _make_inputs()
    g = build_graph(roads.to_crs(roads.estimate_utm_crs()))
    far = gpd.GeoDataFrame({"n": ["far"]}, geometry=[Point(0.05, 51.55)], crs="EPSG:4326")
    far_m = far.to_crs(roads.estimate_utm_crs())
    snapped = snap_points(far_m, g, max_m=1000.0)
    assert snapped.iloc[0]["graph_node"] is None
