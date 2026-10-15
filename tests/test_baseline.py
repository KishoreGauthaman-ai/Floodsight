"""Baseline detection unit tests (pure functions, no SNAP or network)."""

from __future__ import annotations

import numpy as np
import pytest

from floodsight.detection.baseline import (
    BaselineError,
    confidence_from_ratio,
    otsu_threshold,
    remove_small_blobs,
    slope_degrees,
    to_db,
)


def test_otsu_on_overlapping_classes() -> None:
    rng = np.random.default_rng(42)
    vals = np.concatenate([rng.normal(-2, 1.0, 10000), rng.normal(2, 1.0, 10000)])
    thr = otsu_threshold(vals)
    assert -0.75 < thr < 0.75, thr


def test_otsu_separates_well_separated_classes() -> None:
    # With a 12-sigma gap the between-class variance is flat across the gap:
    # any threshold inside it is optimal (verified against brute force).
    rng = np.random.default_rng(42)
    vals = np.concatenate([rng.normal(-2, 0.5, 10000), rng.normal(4, 0.5, 5000)])
    thr = otsu_threshold(vals)
    assert -1.0 < thr < 3.0, thr


def test_otsu_rejects_degenerate_input() -> None:
    with pytest.raises(BaselineError):
        otsu_threshold(np.array([1.0, 2.0, np.nan]))


def test_to_db_conversion_and_clip() -> None:
    # 0.01 -> -20 dB; 0 -> NaN; 1.0 -> 0 dB; 100 (20 dB) exceeds db_max=10 -> NaN
    db = to_db(np.array([[0.01, 0.0], [1.0, 100.0]]), -35, 10)
    assert abs(db[0, 0] + 20) < 1e-9
    assert np.isnan(db[0, 1])
    assert abs(db[1, 0]) < 1e-9
    assert np.isnan(db[1, 1])


def test_slope_45_degrees_on_ramp() -> None:
    elev = np.tile(np.arange(100.0), (10, 1))
    assert np.allclose(slope_degrees(elev, pixel_m=1.0)[:, 1:-1], 45.0, atol=1e-6)


def test_remove_small_blobs_keeps_large_components() -> None:
    m = np.zeros((20, 20), bool)
    m[2:7, 2:7] = True
    m[15, 15:17] = True
    assert remove_small_blobs(m, 3).sum() == 25
    assert remove_small_blobs(m, 100).sum() == 0


def test_confidence_clips_to_unit_interval() -> None:
    conf = confidence_from_ratio(np.array([2.0, 5.0, 11.0]), 2.0, 3.0)
    assert np.allclose(conf, [0.0, 1.0, 1.0])
