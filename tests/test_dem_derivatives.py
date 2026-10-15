"""DEM derivative tests on small synthetic grids."""

from __future__ import annotations

import numpy as np

from floodsight.preprocessing.dem_derivatives import (
    fill_depressions,
    flow_accumulation,
    flow_direction,
    hand,
    slope_degrees,
)


def test_pit_is_filled_to_spill_level() -> None:
    dem = np.array([
        [10, 10, 10, 10],
        [10, 4, 10, 10],
        [10, 10, 10, 10],
    ], dtype="float64")
    filled = fill_depressions(dem, eps=1e-3)
    # the pit must rise to the rim of its bowl (epsilon may push it a hair
    # above so the filled surface drains)
    assert 9.0 <= filled[1, 1] <= 10.0 + 1e-2
    assert filled[1, 1] > dem[1, 1]
    # no cell may be lower than in the original DEM
    assert (filled >= dem - 1e-9).all()


def test_inclined_plane_flows_one_direction() -> None:
    dem = np.tile(np.arange(50.0), (20, 1))  # rises toward east -> drains west
    filled = fill_depressions(dem, eps=1e-4)
    d = flow_direction(filled)
    # every interior cell drains west (code 64 in the D8 table)
    assert (d[5:15, 5:40] == 64).all()


def test_accumulation_counts_upstream_cells() -> None:
    dem = np.tile(np.arange(60.0), (30, 1))
    filled = fill_depressions(dem, eps=1e-4)
    d = flow_direction(filled)
    acc = flow_accumulation(d)
    # the plane drains WEST along each row: accumulation at (r, c) is the
    # number of cells east of (and including) c in that row
    assert acc[15, 5] == 55  # 60 - 5 upstream cells
    assert acc[10, 5] == 55
    # the far-west column is the outlet of every row; on this synthetic
    # constant-height edge the epsilon fill also drains along the edge,
    # so the corner can collect more than one row (900 = 60*30/2 observed)
    assert acc[15, 0] >= 60


def test_hand_zero_on_drainage_positive_above() -> None:
    dem = np.tile(np.arange(80.0), (40, 1))  # west-draining slope
    filled = fill_depressions(dem, eps=1e-4)
    d = flow_direction(filled)
    acc = flow_accumulation(d)
    drainage_threshold = 30.0
    h = hand(filled, d, acc, acc_threshold=drainage_threshold)
    drainage = acc >= drainage_threshold
    assert (h[drainage] == 0).all()
    above = ~drainage
    assert (h[above] > 0).all()
    assert np.isfinite(h).all()


def test_slope_matches_baseline_definition() -> None:
    from floodsight.detection.baseline import slope_degrees as ref_slope

    dem = np.tile(np.arange(100.0), (10, 1))
    assert np.allclose(
        slope_degrees(dem, pixel_m=1.0), ref_slope(dem, pixel_m=1.0)
    )
