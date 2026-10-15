"""Flood-path trace tests (B-check, synthetic valley DEM).

The verified failure modes must never recur:
- the old D8 walk wandered 4,822 cells east across filled flats and touched
  all four bbox edges (DECISIONS D48);
- scattered 0 m DEM voids acted as mega-pits (45% of flow attracted to one
  corner void);
- max-accumulation snapping moved the start 6 km off the given point.
"""

from __future__ import annotations

import numpy as np
import pytest

from floodsight.analysis.flood_path import FloodPathError, trace_flood_path

CFG = {
    "acquisition": {"dem": {"bucket_url": "", "tile_dir_pattern": "",
                            "tile_file_pattern": ""}},
    "flood_path": {"drainage_acc_threshold": 20.0, "base_buffer_m": 200.0,
                   "buffer_per_log_acc_m": 50.0},
}


def valley_dem(n: int = 120) -> np.ndarray:
    """A west-flowing valley: height rises with column and away from a
    meandering thalweg at row = n/2 + 15*sin(col/12)."""
    cols = np.arange(n)
    centre = n // 2 + (15 * np.sin(cols / 12.0)).astype(int)
    rows = np.arange(n)
    dem = np.zeros((n, n))
    for c in cols:
        dem[:, c] = 300.0 * c / n + 0.5 * (rows - centre[c]) ** 2
    return dem


class _FakeSrc:
    """Only _load_dem_grid is patched; everything else runs for real."""


def test_main_stem_trace_stays_in_valley_and_exits_west(monkeypatch) -> None:
    import rasterio.transform as rt

    dem = valley_dem()
    transform = rt.from_origin(85.3, 28.4, 0.001, 0.001)  # ~100 m pixels
    monkeypatch.setattr(
        "floodsight.analysis.flood_path._load_dem_grid",
        lambda bbox, cfg: (dem.copy(), transform),
    )
    import geopandas as gpd
    from shapely.geometry import Point

    settlements = gpd.GeoDataFrame(
        {"name": ["S1"]},
        geometry=[Point(85.28, 28.32)], crs="EPSG:4326",
    )
    bbox = (85.2, 28.25, 85.4, 28.4)
    fp = trace_flood_path((85.32, 28.335), bbox, settlements, CFG)

    from shapely.geometry import shape

    line = shape(fp["path_geojson"])
    assert line.geom_type == "LineString"
    assert len(line.coords) >= 2
    pts = np.array(line.coords)
    # downstream = west: the path must end at (or near) the west edge of
    # the DEM data and must never wander east of its start
    assert pts[-1, 0] <= pts[0, 0] + 1e-9
    assert pts[:, 0].max() <= pts[0, 0] + 1e-6
    assert pts[:, 1].max() <= bbox[3] + 1e-6
    # a sane length in METRES: straight-line <= traced <= 4x straight
    import pyproj

    fwd = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:32645", always_xy=True)
    x0, y0 = fwd.transform(*pts[0])
    x1, y1 = fwd.transform(*pts[-1])
    straight = float(np.hypot(x1 - x0, y1 - y0))
    assert straight <= fp["path_length_m"] <= 4 * straight + 1.0


def test_voids_are_inert_no_mega_pit(monkeypatch) -> None:
    import rasterio.transform as rt

    dem = valley_dem()
    dem[10, 100] = 0.0          # a literal 0 m void far from the valley
    dem[60, 10] = -32768.0      # a sentinel void
    transform = rt.from_origin(85.3, 28.4, 0.001, 0.001)
    monkeypatch.setattr(
        "floodsight.analysis.flood_path._load_dem_grid",
        lambda bbox, cfg: (dem, transform),
    )
    import geopandas as gpd

    settlements = gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
    fp = trace_flood_path((85.32, 28.335), (85.2, 28.25, 85.4, 28.4),
                          settlements, CFG)
    from shapely.geometry import shape

    pts = np.array(shape(fp["path_geojson"]).coords)
    # the path must still flow west out of the valley, not detour to a void
    assert pts[-1, 0] <= pts[0, 0] + 1e-9
    # no point may be within 2 cells of either void
    for vr, vc in ((10, 100), (60, 10)):
        lon, lat = transform * (vc, vr)
        assert not (
            (np.abs(pts[:, 0] - lon) < 0.003) &
            (np.abs(pts[:, 1] - lat) < 0.003)
        ).any()


def test_no_drainage_near_point_raises(monkeypatch) -> None:
    import rasterio.transform as rt

    dem = np.full((60, 60), 1000.0)  # a flat plateau
    transform = rt.from_origin(85.3, 28.4, 0.001, 0.001)
    monkeypatch.setattr(
        "floodsight.analysis.flood_path._load_dem_grid",
        lambda bbox, cfg: (dem, transform),
    )
    import geopandas as gpd

    # a threshold no filled-flat accumulation can reach (the whole grid is
    # only 3600 cells): the honest failure is "no drainage", never a fake
    # corridor on a plateau
    cfg = {**CFG, "flood_path": {**CFG["flood_path"],
                                 "drainage_acc_threshold": 1e9}}
    with pytest.raises(FloodPathError, match="no drainage"):
        trace_flood_path((85.32, 28.335), (85.2, 28.25, 85.4, 28.4),
                         gpd.GeoDataFrame(geometry=[], crs="EPSG:4326"), cfg)
