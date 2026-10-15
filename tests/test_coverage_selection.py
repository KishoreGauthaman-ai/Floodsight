"""Coverage-aware pair selection tests (synthetic scenes with footprints).

The approved-bbox failure mode (track 121 silently covering only 31.2%)
must never recur: selection must prefer a fully-covering pair, fall back to
a union of partial pairs, and refuse when nothing covers enough.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from shapely.geometry import box

from floodsight.acquisition.orbit_pairing import (
    NoValidPairError,
    RankedPair,
    SceneInfo,
    pair_bbox_coverage,
    select_pairs,
)
from floodsight.config import load_config

CFG = load_config()
BBOX = (84.39, 27.68, 85.73, 28.31)


def scene(track: int, direction: str, iso: str, name: str, slice_no: int,
          lon0: float, lat0: float, lon1: float, lat1: float) -> SceneInfo:
    return SceneInfo(
        product_id=name, name=name,
        sensing_time=datetime.fromisoformat(iso),
        relative_orbit=track, orbit_direction=direction,
        product_type="IW_GRDH_1S", platform="D",
        slice_number=slice_no, footprint=box(lon0, lat0, lon1, lat1),
    )


def test_pair_coverage_of_full_and_partial_footprints() -> None:
    full = [scene(1, "DESC", "2026-08-16T00:00:00+00:00", "f", 8,
                  84.0, 27.0, 86.5, 28.9)]
    part = [scene(2, "ASC", "2026-08-16T00:00:00+00:00", "p", 8,
                  85.2, 27.0, 86.5, 28.9)]
    assert pair_bbox_coverage(full, full, BBOX) == pytest.approx(1.0)
    frac = pair_bbox_coverage(part, part, BBOX)
    assert 0.0 < frac < 0.6  # partial, measured not guessed


def test_single_pair_mode_when_one_track_covers_bbox() -> None:
    scenes = [
        # track A: covers the whole bbox, slightly worse dates
        scene(10, "DESC", "2026-08-16T00:00:00+00:00", "a-pre", 8, 84.0, 27.0, 86.5, 28.9),
        scene(10, "DESC", "2026-08-28T00:00:00+00:00", "a-post", 8, 84.0, 27.0, 86.5, 28.9),
        # track B: covers only the east, better dates -> must NOT win
        scene(20, "ASC", "2026-08-19T00:00:00+00:00", "b-pre", 5, 85.2, 27.0, 86.5, 28.9),
        scene(20, "ASC", "2026-08-31T00:00:00+00:00", "b-post", 5, 85.2, 27.0, 86.5, 28.9),
    ]
    selection = select_pairs(scenes, "2026-08-26", BBOX, CFG)
    assert selection["mode"] == "single"
    assert len(selection["pairs"]) == 1
    assert selection["pairs"][0].track == (10, "DESC")
    assert selection["coverage"] == pytest.approx(1.0)


def test_union_mode_when_no_single_track_covers() -> None:
    west = (84.0, 27.0, 85.1, 28.9)   # covers the west part only
    east = (85.0, 27.0, 86.5, 28.9)   # covers the east part only
    scenes = [
        scene(30, "DESC", "2026-08-16T00:00:00+00:00", "w-pre", 8, *west),
        scene(30, "DESC", "2026-08-28T00:00:00+00:00", "w-post", 8, *west),
        scene(40, "ASC", "2026-08-19T00:00:00+00:00", "e-pre", 8, *east),
        scene(40, "ASC", "2026-08-31T00:00:00+00:00", "e-post", 8, *east),
    ]
    selection = select_pairs(scenes, "2026-08-26", BBOX, CFG)
    assert selection["mode"] == "union"
    assert len(selection["pairs"]) == 2
    # ranked by coverage, descending
    covs = [p.coverage for p in selection["pairs"]]
    assert covs == sorted(covs, reverse=True)
    assert all(cov >= 0.05 for cov in covs)


def test_refusal_when_nothing_covers_enough() -> None:
    tiny = (86.0, 28.5, 86.1, 28.55)  # outside the bbox entirely
    scenes = [
        scene(50, "DESC", "2026-08-16T00:00:00+00:00", "t-pre", 8, *tiny),
        scene(50, "DESC", "2026-08-28T00:00:00+00:00", "t-post", 8, *tiny),
    ]
    with pytest.raises(NoValidPairError):
        select_pairs(scenes, "2026-08-26", BBOX, CFG)


def test_rankedpair_date_key_prefers_fresher_post_event() -> None:
    a = RankedPair(track=(1, "DESC"), pre=[], post=[],
                   days_before=10, days_after=2, coverage=1.0)
    b = RankedPair(track=(2, "ASC"), pre=[], post=[],
                   days_before=2, days_after=10, coverage=1.0)
    # same worst gap (10) -> the pair whose POST scene is fresher wins
    assert a.date_key < b.date_key
