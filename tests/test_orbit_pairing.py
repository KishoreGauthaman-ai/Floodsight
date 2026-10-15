"""Orbit-pairing tests with synthetic scene metadata (AGENT.md 8.1).

The same-track rule is a hackathon hard rule: scenes from different tracks
(relative orbit or flight direction) must never be paired.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from floodsight.acquisition.orbit_pairing import (
    NoValidPairError,
    SceneInfo,
    group_passes,
    parse_scene,
    select_same_track_pair,
)

FLOOD = "2026-08-26"


def scene(rel_orbit: int, direction: str, day: str, name_suffix: str = "A001",
          slice_number: int = 8, cog: bool = False) -> SceneInfo:
    dt = datetime.fromisoformat(f"{day}T00:10:00+00:00")
    name = f"S1D_IW_GRDH_1SDV_{day.replace('-', '')}T001036_2026{day.replace('-', '')[2:]}_000000_000000_{name_suffix}.SAFE"
    return SceneInfo(
        product_id=f"{day}-{name_suffix}",
        name=name if not cog else name.replace(".SAFE", "_COG.SAFE"),
        sensing_time=dt,
        relative_orbit=rel_orbit,
        orbit_direction=direction,
        product_type="IW_GRDH_1S",
        platform="D",
        slice_number=slice_number,
        is_cog=cog,
    )


def product_dict(rel_orbit: int, direction: str, iso: str, name: str,
                 product_type: str = "IW_GRDH_1S") -> dict:
    return {
        "Id": name, "Name": name,
        "ContentDate": {"Start": iso},
        "ContentLength": 1234, "Online": True,
        "Attributes": [
            {"Name": "relativeOrbitNumber", "Value": rel_orbit},
            {"Name": "orbitDirection", "Value": direction},
            {"Name": "productType", "Value": product_type},
            {"Name": "platformSerialIdentifier", "Value": "D"},
            {"Name": "sliceNumber", "Value": 8},
        ],
    }


# --- parse_scene -------------------------------------------------------------


def test_parse_scene_reads_verified_attributes() -> None:
    p = product_dict(121, "DESCENDING", "2026-08-19T00:10:36Z",
                     "S1D_IW_GRDH_1SDV_20260819T001036.SAFE")
    s = parse_scene(p)
    assert s is not None
    assert s.relative_orbit == 121
    assert s.orbit_direction == "DESCENDING"
    assert s.sensing_time.year == 2026


def test_parse_scene_skips_cog_and_non_iw_grdh() -> None:
    cog = product_dict(121, "DESCENDING", "2026-08-19T00:10:36Z",
                       "S1D_IW_GRDH_1SDV_20260819T001036_COG.SAFE")
    sm = product_dict(121, "DESCENDING", "2026-08-19T00:10:36Z",
                      "S1D_IW_SLC_1SDV_20260819T001036.SAFE",
                      product_type="IW_SLC_1S")
    assert parse_scene(cog) is None
    assert parse_scene(sm) is None


# --- same-track pairing -------------------------------------------------------


def test_valid_pair_selected_from_single_track() -> None:
    scenes = [
        scene(121, "DESCENDING", "2026-08-07"),
        scene(121, "DESCENDING", "2026-08-19"),
        scene(121, "DESCENDING", "2026-08-31"),
        scene(44, "ASCENDING", "2026-08-10"),
        scene(44, "ASCENDING", "2026-08-22"),  # pre-event only on track 44
    ]
    pair = select_same_track_pair(scenes, FLOOD, max_pre_event_days=21, max_post_event_days=14)
    assert pair.track == (121, "DESCENDING")
    assert [s.name for s in pair.pre] and pair.pre[0].sensing_time.date() == date(2026, 8, 19)
    assert pair.post[0].sensing_time.date() == date(2026, 8, 31)
    assert pair.days_before == 7 and pair.days_after == 5


def test_nearest_passes_chosen_on_same_track() -> None:
    scenes = [
        scene(121, "DESCENDING", "2026-08-01"),
        scene(121, "DESCENDING", "2026-08-19"),  # closest pre
        scene(121, "DESCENDING", "2026-09-04"),
        scene(121, "DESCENDING", "2026-08-31"),  # closest post
    ]
    pair = select_same_track_pair(scenes, FLOOD, 21, 14)
    assert pair.pre[0].sensing_time.date() == date(2026, 8, 19)
    assert pair.post[0].sensing_time.date() == date(2026, 8, 31)


def test_cross_track_scenes_are_never_paired() -> None:
    # pre on track 121 only, post on track 44 only -> no pair, explicit error
    scenes = [
        scene(121, "DESCENDING", "2026-08-19"),
        scene(44, "ASCENDING", "2026-08-28"),
    ]
    with pytest.raises(NoValidPairError):
        select_same_track_pair(scenes, FLOOD, 21, 14)


def test_no_post_event_scene_is_explicit() -> None:
    scenes = [
        scene(121, "DESCENDING", "2026-08-12"),
        scene(121, "DESCENDING", "2026-08-24"),
    ]
    with pytest.raises(NoValidPairError, match="alternatives"):
        select_same_track_pair(scenes, FLOOD, 21, 14)


def test_out_of_window_pre_scene_rejected() -> None:
    scenes = [
        scene(121, "DESCENDING", "2026-07-01"),  # 56 days before: outside window
        scene(121, "DESCENDING", "2026-08-31"),
    ]
    with pytest.raises(NoValidPairError):
        select_same_track_pair(scenes, FLOOD, 21, 14)


def test_multi_slice_pass_stays_together() -> None:
    s1 = scene(121, "DESCENDING", "2026-08-19", "DC16", slice_number=8)
    s2 = scene(121, "DESCENDING", "2026-08-19", "B38C", slice_number=9)
    p1 = scene(121, "DESCENDING", "2026-08-31", "2C5B", slice_number=8)
    p2 = scene(121, "DESCENDING", "2026-08-31", "65B9", slice_number=9)
    pair = select_same_track_pair([s1, s2, p1, p2], FLOOD, 21, 14)
    assert len(pair.pre) == 2 and len(pair.post) == 2


def test_best_track_minimizes_worst_gap() -> None:
    # track 121: pre 7d, post 5d (worst 7); track 44: pre 10d, post 2d (worst 10)
    scenes = [
        scene(121, "DESCENDING", "2026-08-19"),
        scene(121, "DESCENDING", "2026-08-31"),
        scene(44, "ASCENDING", "2026-08-16"),
        scene(44, "ASCENDING", "2026-08-28"),
    ]
    pair = select_same_track_pair(scenes, FLOOD, 21, 14)
    assert pair.track == (121, "DESCENDING")


def test_group_passes_clusters_by_track_and_day() -> None:
    scenes = [
        scene(121, "DESCENDING", "2026-08-19", "DC16"),
        scene(121, "DESCENDING", "2026-08-19", "B38C"),
        scene(121, "DESCENDING", "2026-08-31"),
        scene(44, "ASCENDING", "2026-08-28"),
    ]
    passes = group_passes(scenes)
    assert len(passes) == 3
    two_slice = [p for p in passes if p.date == date(2026, 8, 19)]
    assert len(two_slice[0].scenes) == 2
