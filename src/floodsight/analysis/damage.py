"""Damage assessment: intersect flood/debris extents with pre-event OSM.

Estimates, never confirmed damage:
*   Buildings: count and footprint area inside the flood or debris extent,
    split into likely / possible tiers by polygon confidence.
*   Roads: length inside the extent by highway class, computed in a metric
    projection.
*   Bridges: a bridge normally crosses permanent water, so it is tested
    against NEW flood / debris and against its two approach ends — never
    against the permanent river itself (AGENT.md 8.4).
"""

from __future__ import annotations

import logging
from typing import Any

import geopandas as gpd

logger = logging.getLogger(__name__)


class DamageError(Exception):
    """Damage assessment failed."""


def _tier_intersects(
    targets: gpd.GeoDataFrame, extent: gpd.GeoDataFrame, min_confidence: float
) -> set[Any]:
    """Indices of targets intersecting extent polygons at/above the tier.

    The tier polygons are dissolved into ONE geometry first: a direct sjoin
    of hundreds of thousands of buildings against tens of thousands of
    small debris polygons took ~1 h at corridor scale (py-spy verified),
    while a single prepared-geometry intersects test needs seconds
    (DECISIONS D52).
    """
    if extent is None or len(extent) == 0:
        return set()
    strong = extent[extent["mean_confidence"] >= min_confidence]
    if len(strong) == 0:
        return set()
    combined = strong.geometry.union_all()
    import shapely

    shapely.prepare(combined)  # prepared-geometry predicates: repeated
    # intersects tests against one complex multipolygon are ~100x faster
    hits = targets.geometry.intersects(combined)
    return set(targets.index[hits].tolist())


def _ends_buffer(geom, buffer_m: float):
    """Buffer around the first/last vertices of a line (the approaches)."""
    from shapely.geometry import Point

    coords = list(geom.coords) if geom.geom_type == "LineString" else [
        c for part in geom.geoms for c in part.coords
    ]
    if len(coords) < 2:
        return geom.buffer(buffer_m)
    return Point(coords[0]).buffer(buffer_m).union(Point(coords[-1]).buffer(buffer_m))


def assess_damage(
    buildings: gpd.GeoDataFrame,
    roads: gpd.GeoDataFrame,
    bridges: gpd.GeoDataFrame,
    flood_polygons: gpd.GeoDataFrame | None,
    debris_polygons: gpd.GeoDataFrame | None,
    cfg: dict[str, Any],
    analysed_area: gpd.GeoDataFrame | None = None,
) -> tuple[dict[str, Any], gpd.GeoDataFrame]:
    """Returns (damage results section, damage GeoDataFrame for damage.gpkg).

    All inputs in EPSG:4326; lengths/areas computed in the local UTM.
    """
    conf = cfg["detection"]["confidence"]
    approach_m = float(cfg["damage"].get("bridge_approach_buffer_m", 50.0))

    # Clip the OSM features to the ANALYSED area (where radar data exists):
    # features outside it are "not analysed", never counted as unaffected.
    if analysed_area is not None and len(analysed_area):
        # Grid-subdivide the analysed-area polygon before the spatial join:
        # the raw polygon has ~25k vertices and 869k buildings pay O(V) per
        # intersects candidate (40+ min sequential, py-spy verified; sjoin
        # with the full polygon is no better). Cutting it into a 24x24 grid
        # of small pieces makes each candidate test O(100) - seconds total.
        import numpy as np
        import shapely

        clip_geom = analysed_area.geometry.union_all()
        minx, miny, maxx, maxy = clip_geom.bounds
        n = 24
        xs = np.linspace(minx, maxx, n + 1)
        ys = np.linspace(miny, maxy, n + 1)
        pieces = []
        for i in range(n):
            for j in range(n):
                inter = shapely.intersection(
                    clip_geom, shapely.box(xs[i], ys[j], xs[i + 1], ys[j + 1]))
                if not inter.is_empty:
                    pieces.append(inter)
        pieces_gdf = gpd.GeoDataFrame(
            geometry=pieces, crs=analysed_area.crs)

        def _clip(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
            if gdf is None or len(gdf) == 0:
                return gdf
            idx = gpd.sjoin(
                gdf, pieces_gdf[["geometry"]], how="inner",
                predicate="intersects",
            ).index.unique()
            return gdf.loc[idx]

        buildings = _clip(buildings)
        roads = _clip(roads)
        bridges = _clip(bridges)

    crs_metric = buildings.estimate_utm_crs()
    buildings_m = buildings.to_crs(crs_metric)
    roads_m = roads.to_crs(crs_metric)
    bridges_m = bridges.to_crs(crs_metric)

    extents: list[gpd.GeoDataFrame] = []
    for layer in (flood_polygons, debris_polygons):
        if layer is not None and len(layer):
            e = layer.to_crs(crs_metric)
            if "mean_confidence" not in e.columns:
                e = e.assign(mean_confidence=1.0)
            extents.append(e)

    damage_gdf = gpd.GeoDataFrame(
        columns=["osm_id", "feature_type", "class", "name", "status",
                 "mean_confidence", "geometry"],
        crs=crs_metric,
    )
    rows: list[dict[str, Any]] = []

    # --- buildings
    likely_idx = set()
    possible_idx = set()
    for e in extents:
        likely_idx |= _tier_intersects(buildings_m, e, conf["likely_min"])
        possible_idx |= _tier_intersects(buildings_m, e, conf["possible_min"])
    possible_idx -= likely_idx
    for idx in sorted(likely_idx, key=str):
        rows.append({
            "osm_id": buildings_m.loc[idx].get("@id"),
            "feature_type": "building",
            "class": buildings_m.loc[idx].get("building"),
            "name": buildings_m.loc[idx].get("name"),
            "status": "likely_hit", "mean_confidence": None,
            "geometry": buildings_m.loc[idx].geometry,
        })
    for idx in sorted(possible_idx, key=str):
        rows.append({
            "osm_id": buildings_m.loc[idx].get("@id"),
            "feature_type": "building",
            "class": buildings_m.loc[idx].get("building"),
            "name": buildings_m.loc[idx].get("name"),
            "status": "possibly_hit", "mean_confidence": None,
            "geometry": buildings_m.loc[idx].geometry,
        })
    building_area_m2 = sum(
        buildings_m.loc[i].geometry.area for i in list(likely_idx) + list(possible_idx)
    ) if (likely_idx or possible_idx) else 0.0

    # --- roads: length inside the combined extent, by highway class
    road_lengths: dict[str, float] = {}
    roads_affected_m = 0.0
    if extents:
        combined = extents[0].geometry.union_all()
        for e in extents[1:]:
            combined = combined.union(e.geometry.union_all())
        # vectorized intersection (the row-by-row loop took ~1 h at
        # corridor scale with 16k debris polygons - DECISIONS D51)
        clipped_all = roads_m.geometry.intersection(combined)
        keep = ~clipped_all.is_empty
        clipped_all = clipped_all[keep]
        roads_kept = roads_m[keep]
        for cls_value, length in (
            roads_kept.assign(length=clipped_all.length)
            .groupby(roads_kept["highway"].fillna("unknown"))["length"]
            .sum().items()
        ):
            road_lengths[str(cls_value)] = float(length)
            roads_affected_m += float(length)
        for idx, geom in zip(roads_kept.index, clipped_all):
            row = roads_kept.loc[idx]
            rows.append({
                "osm_id": row.get("@id"), "feature_type": "road",
                "class": str(row.get("highway") or "unknown"),
                "name": row.get("name"), "status": "length_inside_extent",
                "mean_confidence": None, "geometry": geom,
            })

    # --- bridges: new flood/debris on the bridge OR at either approach end
    bridge_affected: list[dict[str, Any]] = []
    if extents:
        combined = extents[0].geometry.union_all()
        for e in extents[1:]:
            combined = combined.union(e.geometry.union_all())
        for idx, row in bridges_m.iterrows():
            on_bridge = row.geometry.intersects(combined)
            at_approach = _ends_buffer(row.geometry, approach_m).intersects(combined)
            if on_bridge or at_approach:
                bridge_affected.append({
                    "osm_id": row.get("@id"), "name": row.get("name"),
                    "highway": row.get("highway"),
                    "reason": "on_bridge" if on_bridge else "approach_flooded",
                })
                rows.append({
                    "osm_id": row.get("@id"), "feature_type": "bridge",
                    "class": row.get("highway"), "name": row.get("name"),
                    "status": "likely_affected" if on_bridge else "approach_flooded",
                    "mean_confidence": None, "geometry": row.geometry,
                })

    if rows:
        damage_gdf = gpd.GeoDataFrame(rows, crs=crs_metric)

    results = {
        "buildings": {
            "total_in_area": len(buildings),
            "likely_hit": len(likely_idx),
            "possibly_hit": len(possible_idx),
            "footprint_area_km2": round(building_area_m2 / 1e6, 4),
        },
        "roads": {
            "length_by_class_km": {
                cls: round(v / 1000.0, 2) for cls, v in sorted(road_lengths.items())
            },
            "total_length_km": round(roads_affected_m / 1000.0, 2),
        },
        "bridges": {
            "total_in_area": len(bridges),
            "affected": len(bridge_affected),
            "affected_list": bridge_affected,
        },
    }
    return results, damage_gdf
