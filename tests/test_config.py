"""Configuration loading and validation tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from floodsight.config import (
    ConfigError,
    data_dir,
    load_config,
    load_region,
    parse_date,
    runs_dir,
    validate_bbox,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def cfg() -> dict:
    return load_config()


def test_default_config_loads(cfg: dict) -> None:
    assert cfg["osm"]["snapshot_date_cap"] == "2026-07-27"
    assert cfg["osm"]["geofabrik"]["regions"]["nepal"]["snapshot_date"] == "2026-07-01"
    assert cfg["mode_default"] in ("fast", "full")


def test_missing_config_file_raises() -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_config("/nonexistent/path.yaml")


def test_parse_date_accepts_iso_and_rejects_garbage() -> None:
    assert str(parse_date("2026-08-26")) == "2026-08-26"
    with pytest.raises(ConfigError):
        parse_date("26/08/2026")


def test_valid_bbox_accepted(cfg: dict) -> None:
    assert validate_bbox([85.0, 27.8, 85.6, 28.25], cfg) == (85.0, 27.8, 85.6, 28.25)


@pytest.mark.parametrize(
    "bbox",
    [
        [85.6, 28.25, 85.0, 27.8],      # reversed order (lon_min > lon_max)
        [85.0, 27.8, 85.0, 28.25],      # zero-width
        [85.0, 28.25, 85.6, 27.8],      # inverted latitude
        [-200.0, 27.8, 85.6, 28.25],    # out of range longitude
        [85.0, 27.8],                   # wrong arity
        ["a", "b", "c", "d"],           # not numbers
    ],
)
def test_invalid_bbox_rejected(bbox, cfg: dict) -> None:
    with pytest.raises(ConfigError):
        validate_bbox(bbox, cfg)


def test_axis_order_is_longitude_first(cfg: dict) -> None:
    """The (lon_min, lat_min, lon_max, lat_max) order must round-trip exactly.

    Note: a fully-swapped box whose four values all fit in both the lon and
    lat ranges (e.g. [27.8, 85.0, 28.25, 85.6]) is geometrically valid and
    cannot be auto-detected; the CLI metavar names and docs enforce the
    order instead (DECISIONS.md).
    """
    bbox = (85.0, 27.8, 85.6, 28.25)
    assert validate_bbox(bbox, cfg) == bbox


def test_oversized_bbox_rejected(cfg: dict) -> None:
    with pytest.raises(ConfigError, match="hard limit"):
        validate_bbox([80.0, 20.0, 90.0, 30.0], cfg)


def test_trishuli_region_loads(cfg: dict) -> None:
    region = load_region("trishuli", cfg)
    assert len(region["bbox"]) == 4
    assert str(parse_date(region["flood_date"])) == "2026-08-26"


def test_unknown_region_rejected(cfg: dict) -> None:
    with pytest.raises(ConfigError, match="Unknown region"):
        load_region("atlantis", cfg)


def test_data_dir_env_override(cfg: dict, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("FLOODSIGHT_DATA_DIR", str(tmp_path / "custom_data"))
    assert data_dir(cfg) == tmp_path / "custom_data"
    assert (tmp_path / "custom_data").is_dir()


def test_runs_dir_env_override(cfg: dict, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("FLOODSIGHT_RUNS_DIR", str(tmp_path / "custom_runs"))
    assert runs_dir(cfg) == tmp_path / "custom_runs"
    assert (tmp_path / "custom_runs").is_dir()


def test_large_bbox_soft_warning_recorded(tmp_path, monkeypatch) -> None:
    """1.0-2.5 deg boxes do NOT stop the run; the warning lands in warnings.json."""
    import json

    from floodsight.pipeline import run_pipeline

    monkeypatch.setenv("FLOODSIGHT_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("FLOODSIGHT_DATA_DIR", str(tmp_path / "data"))
    # approved Trishuli box: lon side 1.34 deg -> soft zone (not a hard stop)
    run_dir = run_pipeline([84.39, 27.68, 85.73, 28.31], "2026-08-26",
                           mode="fast", stop_after="validate_input",
                           region="nepal")
    warnings = json.loads((run_dir / "warnings.json").read_text())
    codes = [w["code"] for w in warnings]
    assert "large_bbox" in codes
    # a small box must NOT record the warning
    run_dir2 = run_pipeline([85.0, 27.8, 85.6, 28.25], "2026-08-26",
                            mode="fast", stop_after="validate_input",
                            region="nepal")
    w2_path = run_dir2 / "warnings.json"
    warnings2 = json.loads(w2_path.read_text()) if w2_path.is_file() else []
    assert all(w["code"] != "large_bbox" for w in warnings2)
