"""Damage assessment tests with synthetic OSM-like features."""

from __future__ import annotations

import geopandas as gpd
from shapely.geometry import LineString, Polygon

from floodsight.analysis.damage import assess_damage
from floodsight.config import load_config

CFG = load_config()


def _inputs():
    # a small town grid near lon 0 / lat 51 (UTM math well-behaved)
    buildings = gpd.GeoDataFrame(
        {"building": ["yes", "yes", "house"], "@id": [10, 11, 12]},
        geometry=[
            Polygon([(0.000, 51.500), (0.001, 51.500), (0.001, 51.001), (0.000, 51.001)]),
            Polygon([(0.004, 51.500), (0.005, 51.500), (0.005, 51.001), (0.004, 51.001)]),
            Polygon([(0.010, 51.500), (0.011, 51.500), (0.011, 51.001), (0.010, 51.001)]),
        ],
        crs="EPSG:4326",
    )
    roads = gpd.GeoDataFrame(
        {"highway": ["primary", "residential"], "@id": [20, 21]},
        geometry=[
            LineString([(0.0, 51.500), (0.012, 51.500)]),
            LineString([(0.010, 51.500), (0.010, 51.505)]),
        ],
        crs="EPSG:4326",
    )
    bridges = gpd.GeoDataFrame(
        {"highway": ["primary"], "bridge": ["yes"], "@id": [30]},
        geometry=[LineString([(0.006, 51.500), (0.008, 51.500)])],
        crs="EPSG:4326",
    )
    flood = gpd.GeoDataFrame(
        {"mean_confidence": [0.9, 0.4]},
        geometry=[
            Polygon([(0.000, 51.4995), (0.006, 51.4995), (0.006, 51.5015), (0.000, 51.5015)]),
            Polygon([(0.008, 51.4995), (0.014, 51.4995), (0.014, 51.5015), (0.008, 51.5015)]),
        ],
        crs="EPSG:4326",
    )
    return buildings, roads, bridges, flood


def test_building_tiers_and_counts() -> None:
    buildings, roads, bridges, flood = _inputs()
    damage, gdf = assess_damage(buildings, roads, bridges, flood, None, CFG)
    b = damage["buildings"]
    assert b["total_in_area"] == 3
    # building 10 sits in the high-confidence polygon -> likely
    # building 11 straddles both polygons -> likely (max tier wins)
    # building 12 sits in the low-confidence polygon -> possible
    assert b["likely_hit"] == 2
    assert b["possibly_hit"] == 1
    assert b["footprint_area_km2"] > 0
    statuses = set(gdf[gdf["feature_type"] == "building"]["status"])
    assert statuses == {"likely_hit", "possibly_hit"}


def test_road_length_inside_extent_by_class() -> None:
    buildings, roads, bridges, flood = _inputs()
    damage, _ = assess_damage(buildings, roads, bridges, flood, None, CFG)
    r = damage["roads"]
    # the primary road is fully inside the two polygons; the residential
    # spur only partially
    assert r["total_length_km"] > 0
    assert set(r["length_by_class_km"]) <= {"primary", "residential"}
    assert r["length_by_class_km"]["primary"] > r["length_by_class_km"].get("residential", 0)


def test_bridge_affected_by_new_flood_on_it() -> None:
    buildings, roads, bridges, flood = _inputs()
    damage, _ = assess_damage(buildings, roads, bridges, flood, None, CFG)
    br = damage["bridges"]
    assert br["total_in_area"] == 1
    assert br["affected"] == 1
    assert br["affected_list"][0]["reason"] == "on_bridge"


def test_bridge_approach_flood_counts() -> None:
    buildings, roads, bridges, _ = _inputs()
    # flood NOT on the bridge but at its west approach end
    approach_flood = gpd.GeoDataFrame(
        {"mean_confidence": [0.9]},
        geometry=[Polygon([(0.0055, 51.4995), (0.0059, 51.4995), (0.0059, 51.5010), (0.0055, 51.5010)])],
        crs="EPSG:4326",
    )
    damage, _ = assess_damage(buildings, roads, bridges, approach_flood, None, CFG)
    assert damage["bridges"]["affected"] == 1
    assert damage["bridges"]["affected_list"][0]["reason"] == "approach_flooded"


def test_empty_flood_means_zero_damage() -> None:
    buildings, roads, bridges, _ = _inputs()
    damage, gdf = assess_damage(buildings, roads, bridges, None, None, CFG)
    assert damage["buildings"]["likely_hit"] == 0
    assert damage["buildings"]["possibly_hit"] == 0
    assert damage["roads"]["total_length_km"] == 0.0
    assert damage["bridges"]["affected"] == 0
    assert len(gdf) == 0
    assert damage["buildings"]["total_in_area"] == 3  # context counts stay real
