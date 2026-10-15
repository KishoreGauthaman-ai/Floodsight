"""DEM tile naming tests (pure; the naming scheme is verified live)."""

from __future__ import annotations

from floodsight.acquisition.dem import tile_names_for_bbox


def test_trishuli_area_tiles() -> None:
    # lon 85.0..85.6, lat 27.8..28.25 -> tiles N27 + N28 of E085
    tiles = tile_names_for_bbox((85.0, 27.8, 85.6, 28.25))
    assert tiles == ["N27_00_E085_00", "N28_00_E085_00"]


def test_exact_degree_boundaries() -> None:
    # lon 84.0..85.0 touches two tiles (floor of 85.0 is 85)
    tiles = tile_names_for_bbox((84.0, 28.0, 85.0, 29.0))
    assert tiles == ["N28_00_E084_00", "N28_00_E085_00", "N29_00_E084_00", "N29_00_E085_00"]


def test_southern_and_western_hemispheres() -> None:
    # lon -1.5..0.5 spans W002, W001, E000; lat -2.5..-1.5 spans S03, S02
    tiles = tile_names_for_bbox((-1.5, -2.5, 0.5, -1.5))
    assert tiles == [
        "S03_00_W002_00", "S03_00_W001_00", "S03_00_E000_00",
        "S02_00_W002_00", "S02_00_W001_00", "S02_00_E000_00",
    ]


def test_large_area_tile_count() -> None:
    # 2x2 degrees -> 9 tiles
    assert len(tile_names_for_bbox((10.0, 20.0, 12.0, 22.0))) == 9
