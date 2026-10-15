"""Validator unit tests (AGENT.md lists test_validator.py as required)."""

from __future__ import annotations

from floodsight.reporting.validator import (
    collect_store_numbers,
    validate_numbers_in_text,
)

RESULTS = {
    "meta": {"run_id": "run-2026", "flood_date": "2026-08-26",
             "bbox": [85.0, 27.8, 85.6, 28.25]},
    "flood": {"area_km2_new_water": 0.0, "area_km2_probable_debris": 1.6804},
    "damage": {"buildings": {"total_in_area": 186869, "likely_hit": 9,
                             "possibly_hit": 127, "footprint_area_km2": 0.008},
               "bridges": {"affected": 2, "total_in_area": 373}},
    "cutoff": {"likely_cut": ["A", "B"], "possibly_cut": [], "still_connected_count": 260},
    "inputs": {"sentinel1_pre": {"name": "S1D_IW_GRDH_1SDV_20260819T001036.SAFE",
                                 "sensing_time": "2026-08-19T00:10:36Z"},
               "osm_snapshot_date": "2026-07-01"},
    "warnings": [],
}


def test_collects_numbers_from_all_sections() -> None:
    found = collect_store_numbers(RESULTS)
    for expected in ("186869", "9", "127", "373", "2", "260",
                     "1.6804", "2026", "85.6", "27.8"):
        assert expected in found, expected


def test_grounded_text_passes() -> None:
    text = ("2 likely cut settlements, 260 connected, 9 buildings likely "
            "hit, debris 1.6804 km2, scene S1D_IW_GRDH_1SDV_20260819T001036.SAFE "
            "from 2026-08-19, snapshot 2026-07-01, bbox 85.0..85.6.")
    ok, ungrounded = validate_numbers_in_text(text, RESULTS)
    assert ok, ungrounded


def test_invented_numbers_are_rejected() -> None:
    ok, ungrounded = validate_numbers_in_text(
        "47.3 km2 flooded and 999999 buildings hit", RESULTS
    )
    assert not ok
    assert "47.3" in ungrounded and "999999" in ungrounded


def test_rounding_is_allowed_but_not_distortion() -> None:
    # 1.68 rounds to 1.68 exactly; 1.7 is NOT within rounding tolerance of
    # 1.6804 at any precision (differences: 0.0196 > 0.05/2? no: one decimal
    # rounding of 1.6804 is 1.7 -> allowed as safe formatting)
    ok, _ = validate_numbers_in_text("debris 1.68 km2", RESULTS)
    assert ok
    ok, _ = validate_numbers_in_text("debris 1.7 km2", RESULTS)
    assert ok
    ok, ungrounded = validate_numbers_in_text("debris 99.9 km2", RESULTS)
    assert not ok and "99.9" in ungrounded


def test_identifier_numbers_do_not_confuse_the_check() -> None:
    ok, ungrounded = validate_numbers_in_text(
        "scene S1D_IW_GRDH_1SDV_20260819T001036_20260819T001101_DC16.SAFE used", RESULTS
    )
    assert ok, ungrounded


def test_zero_is_grounded_only_when_in_store() -> None:
    ok, _ = validate_numbers_in_text("flood water 0.0 km2", RESULTS)
    assert ok  # 0.0 normalizes to 0 which is in the store (area 0.0)
