"""Axis-order discipline for the Trishuli region file (AGENT.md 10):
longitude FIRST, latitude SECOND, everywhere. Nepal's numeric ranges make a
swap detectable: lon in [79, 89], lat in [26, 31]."""

from __future__ import annotations

import yaml

from floodsight.config import REPO_ROOT, load_config, load_region, validate_bbox

REGION_PATH = REPO_ROOT / "config" / "regions" / "trishuli.yaml"


def _region_raw() -> dict:
    with open(REGION_PATH, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def test_bbox_is_lon_first() -> None:
    bbox = _region_raw()["bbox"]
    lon_min, lat_min, lon_max, lat_max = bbox
    # Nepal ranges: a lat/lon swap would put values in the wrong band
    assert all(79.0 < v < 89.0 for v in (lon_min, lon_max)), "lon values must be in Nepal's longitude band"
    assert all(26.0 < v < 31.0 for v in (lat_min, lat_max)), "lat values must be in Nepal's latitude band"
    assert lon_min < lon_max and lat_min < lat_max
    # and the pair order is (lon_min, LAT_MIN), (lon_max, LAT_MAX)
    assert bbox == [lon_min, lat_min, lon_max, lat_max]


def test_upstream_point_is_lon_first() -> None:
    up = _region_raw().get("upstream_point")
    if up is None:
        return  # upstream point not configured yet
    lon, lat = up
    assert 79.0 < lon < 89.0, "first value must be the longitude"
    assert 26.0 < lat < 31.0, "second value must be the latitude"
    # the point must sit inside the region bbox
    lon_min, lat_min, lon_max, lat_max = _region_raw()["bbox"]
    assert lon_min <= lon <= lon_max and lat_min <= lat <= lat_max


def test_region_loads_and_round_trips_order() -> None:
    region = load_region("trishuli", load_config())
    stored = region["bbox"]
    validated = validate_bbox(stored, load_config())
    assert validated == tuple(stored)  # order preserved exactly
