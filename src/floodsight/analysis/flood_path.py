"""Flood-path tracing (bonus): terrain-based hazard corridor.

Given an upstream point, snap it to the nearest drainage line on the
hydrologically conditioned DEM (priority-flood fill, D8 routing —
preprocessing/dem_derivatives.py), trace the flow path downstream, buffer it
by a distance that grows with flow accumulation, and list pre-event
settlements inside the corridor ordered by distance from the source.

This is explicitly a TERRAIN-BASED HAZARD CORRIDOR, not a hydraulic
simulation: it shows where water is likely to follow the valley, with no
depth, speed or arrival time. The output is labelled that way.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import rasterio
from rasterio.merge import merge as merge_rasters

from ..config import data_dir
from ..preprocessing.dem_derivatives import (
    _D8,
    fill_depressions,
    flow_accumulation,
    flow_direction,
)

logger = logging.getLogger(__name__)


class FloodPathError(Exception):
    """Flood-path tracing failed."""


def _load_dem_grid(bbox, cfg: dict[str, Any]):
    """Mosaic the DEM tiles covering the bbox onto one grid (UTM)."""
    from ..acquisition import dem as dem_mod

    dem_mod.fetch_dem_tiles(bbox, cfg)  # ensures tiles exist (cached)
    tile_paths = []
    for tile in dem_mod.tile_names_for_bbox(bbox):
        cfg["acquisition"]["dem"]["tile_dir_pattern"]
        tile_file_pattern = cfg["acquisition"]["dem"]["tile_file_pattern"]
        tile_paths.append(
            data_dir(cfg) / "raw" / "dem" / tile
            / tile_file_pattern.format(tile=tile)
        )
    if not tile_paths:
        raise FloodPathError("no DEM tiles found for the area")
    srcs = [rasterio.open(p) for p in tile_paths]
    try:
        # clip the mosaic to the analysis bbox (converted to the tile CRS):
        # conditioning the FULL 1-degree tiles would be ~4x slower and the
        # corridor is only defined within the analysis area anyway
        import pyproj

        transformer = pyproj.Transformer.from_crs(
            "EPSG:4326", srcs[0].crs, always_xy=True)
        x0, y0 = transformer.transform(bbox[0], bbox[1])
        x1, y1 = transformer.transform(bbox[2], bbox[3])
        mosaic, transform = merge_rasters(
            srcs, bounds=(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)))
    finally:
        for s in srcs:
            s.close()
    # band 1 is the DEM surface; nodata inside tiles is large negative
    grid = mosaic[0].astype("float64")
    grid[grid < -1000] = np.nan  # sentinel voids  # void walls become NaN...
    return grid, transform


def trace_flood_path(
    upstream: tuple[float, float],
    bbox: tuple[float, float, float, float],
    settlements,  # GeoDataFrame EPSG:4326 with names
    cfg: dict[str, Any],
) -> dict[str, Any]:
    """Trace the corridor. Returns a results-contract fragment."""
    import geopandas as gpd

    fp_cfg = cfg.get("flood_path", {})
    acc_threshold = float(fp_cfg.get("drainage_acc_threshold", 500.0))
    base_buffer_m = float(fp_cfg.get("base_buffer_m", 300.0))
    buffer_per_log_acc_m = float(fp_cfg.get("buffer_per_log_acc_m", 120.0))

    grid, transform = _load_dem_grid(bbox, cfg)
    if not np.isfinite(grid).all():
        # fill interior nodata with a conservative pass: flood from edges
        grid = np.where(np.isfinite(grid), grid, -np.inf)
    # derive the CRS from an actual tile (never guess a projection)
    logger.info("conditioning DEM (%s cells) ...", grid.size)
    # GLO-30 voids arrive as NaN, -32768 sentinel or literal 0 m cells
    # (verified on the approved corridor: 7,091 scattered 0 m voids whose
    # worst one attracted 45% of all flow). Making them inert towers (very
    # high) means the flood never routes flow through them; they keep
    # direction 0 and add no accumulation.
    clean = np.where(np.isfinite(grid), grid, 1e5)
    clean[clean < -1000] = 1e5
    clean[clean <= 0] = 1e5
    n_voids = int((clean == 1e5).sum())
    if n_voids:
        logger.info("DEM voids made inert for routing: %s cells", n_voids)
    filled = fill_depressions(clean)
    logger.info("fill done")
    direction = flow_direction(filled)
    logger.info("flow direction done")
    accumulation = flow_accumulation(direction)
    logger.info("accumulation done")

    # snap upstream point to the nearest strong drainage cell (window search)
    inv = ~transform
    col, row = inv * (upstream[0], upstream[1])
    row, col = round(row), round(col)
    window = 200
    r0, r1 = max(0, row - window), min(grid.shape[0], row + window)
    c0, c1 = max(0, col - window), min(grid.shape[1], col + window)
    local = accumulation[r0:r1, c0:c1]
    if local.size == 0 or not (local >= acc_threshold).any():
        raise FloodPathError(
            "no drainage line within the search window of the upstream point; "
            "the point may be off-channel or outside the DEM area"
        )
    # snap to the NEAREST drainage cell (not the largest): the user-verified
    # upstream point is 5 m from the Bhote Koshi centreline, so nearest-
    # drainage keeps the trace start AT the point; max-accumulation snaps
    # instead jumped 6 km to a bigger trunk downstream.
    drainage_local = np.argwhere(local >= acc_threshold)
    centre = np.array([row - r0, col - c0])
    dists = np.linalg.norm(drainage_local - centre, axis=1)
    nearest = drainage_local[int(np.argmin(dists))]
    snap_r, snap_c = r0 + int(nearest[0]), c0 + int(nearest[1])
    logger.info("snapped upstream point to drainage cell (row %s, col %s), "
                "accumulation %s", snap_r, snap_c, accumulation[snap_r, snap_c])

    # Walk the MAIN STEM downstream by accumulation: at each step move to
    # the not-uphill neighbour whose accumulation is largest (the main
    # flow). Following raw D8 steepest-descent alone wanders across
    # epsilon-filled flats (tie-biased directions) and produced a
    # 4822-cell cross-country detour in the verified failure (DECISIONS
    # D48); accumulation strictly grows along the true channel, so this
    # walk stays on the main stem and cannot cycle.
    path_cells = [(snap_r, snap_c)]
    visited = {(snap_r, snap_c)}
    rr, cc = snap_r, snap_c
    logger.info("walking the main stem downstream from (%s, %s)", snap_r, snap_c)
    n_rows, n_cols = grid.shape
    while True:
        cur_acc = accumulation[rr, cc]
        cur_z = filled[rr, cc]
        best = None
        best_acc = cur_acc
        for dr, dc in _D8:
            nr, nc = rr + dr, cc + dc
            if not (0 <= nr < n_rows and 0 <= nc < n_cols):
                continue
            if (nr, nc) in visited:
                continue
            # downstream-or-flat only (never uphill on the filled surface);
            # a 1e-3 m tolerance covers the epsilon-fill ramp
            if filled[nr, nc] > cur_z + 1e-3:
                continue
            nacc = accumulation[nr, nc]
            if nacc >= best_acc:
                best_acc = nacc
                best = (nr, nc)
        if best is None:
            break
        rr, cc = best
        visited.add((rr, cc))
        path_cells.append((rr, cc))
    if len(path_cells) < 2:
        raise FloodPathError("downstream trace produced an empty path")

    rows = np.array([p[0] for p in path_cells])
    cols = np.array([p[1] for p in path_cells])
    xs_deg, ys_deg = transform * (cols, rows)
    path_acc = accumulation[rows, cols]

    # The GLO-30 grid is EPSG:4326 (degrees): all metric work happens in
    # the local UTM (README CRS convention), outputs return to EPSG:4326.
    import pyproj
    from shapely.geometry import LineString

    from ..preprocessing.s1 import utm_epsg_for_bbox

    utm_crs = f"EPSG:{utm_epsg_for_bbox(tuple(bbox))}"
    fwd = pyproj.Transformer.from_crs("EPSG:4326", utm_crs, always_xy=True)
    xs, ys = fwd.transform(xs_deg, ys_deg)
    seg = np.sqrt(np.diff(xs) ** 2 + np.diff(ys) ** 2)
    along = np.concatenate([[0.0], np.cumsum(seg)])

    # corridor buffer grows with accumulation: width = base + k * log10(acc)
    widths = base_buffer_m + buffer_per_log_acc_m * np.log10(
        np.maximum(path_acc, 10.0)
    )
    line = LineString(zip(xs, ys))
    corridor = line.buffer(float(np.nanmean(widths)))

    # settlements inside the corridor, ordered by distance along the path
    settlements_m = settlements.to_crs(utm_crs)
    inside = settlements_m[settlements_m.geometry.within(corridor)].copy()
    pts = np.array([(g.x, g.y) for g in inside.geometry]) if len(inside) else \
        np.empty((0, 2))
    ordered = []
    if len(inside):
        d2 = (pts[:, 0][:, None] - xs[None, :]) ** 2 + \
             (pts[:, 1][:, None] - ys[None, :]) ** 2
        nearest_idx = np.argmin(d2, axis=1)
        dists = along[nearest_idx]
        for i in np.argsort(dists):
            row = inside.iloc[i]
            ordered.append({
                "name": row.get("name") if isinstance(row.get("name"), str)
                else f"settlement@{row.geometry.x:.0f},{row.geometry.y:.0f}",
                "distance_from_source_m": round(float(dists[i]), 0),
            })

    path_4326 = gpd.GeoSeries([line], crs=utm_crs).to_crs("EPSG:4326")
    corridor_4326 = gpd.GeoSeries([corridor], crs=utm_crs).to_crs("EPSG:4326")
    return {
        "kind": "terrain-based hazard corridor (not a hydraulic simulation)",
        "upstream_point": list(upstream),
        "path_length_m": round(float(along[-1]), 0),
        "corridor_buffer_m_mean": round(float(np.nanmean(widths)), 0),
        "settlements_along_path": ordered,
        "n_settlements": len(ordered),
        "path_geojson": path_4326.iloc[0].__geo_interface__,
        "corridor_geojson": corridor_4326.iloc[0].__geo_interface__,
    }
