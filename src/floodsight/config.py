"""Configuration loading and validation.

All tunable settings come from YAML files under ``config/``; the code contains
no magic numbers. This module loads the configuration, validates inputs and
fails early with clear messages.

Enforced data rule (hackathon constraint): the OSM snapshot date must be
strictly earlier than the flood date. The check lives in
:func:`validate_snapshot_before_event` and is called at pipeline startup.
"""

from __future__ import annotations

import os
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "config"
DEFAULT_CONFIG_PATH = CONFIG_DIR / "default.yaml"
REGIONS_DIR = CONFIG_DIR / "regions"

# Keys that must exist in the default config for the pipeline to operate.
_REQUIRED_KEYS = (
    "data_dir",
    "runs_dir",
    "osm.snapshot_date_cap",
    "osm.region_default",
    "osm.geofabrik.base_url",
    "validation.hard_max_bbox_side_deg",
    "acquisition.s1.search_window_days_pre",
    "acquisition.s1.search_window_days_post",
)


class ConfigError(Exception):
    """Raised when configuration or user input is invalid."""


def parse_date(value: str | date) -> date:
    """Parse a YYYY-MM-DD string (or pass through a date). Raises ConfigError."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value).strip(), "%Y-%m-%d").date()
    except ValueError as exc:
        raise ConfigError(
            f"Invalid date {value!r}: expected YYYY-MM-DD"
        ) from exc


def _get_nested(cfg: dict[str, Any], dotted_key: str) -> Any:
    node: Any = cfg
    for part in dotted_key.split("."):
        if not isinstance(node, dict) or part not in node:
            raise ConfigError(f"Config key missing: {dotted_key!r}")
        node = node[part]
    return node


def load_config(path: Path | str | None = None) -> dict[str, Any]:
    """Load and validate the default configuration.

    Raises ConfigError with a clear message if the file is missing or a
    required key is absent.
    """
    cfg_path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    if not cfg_path.is_file():
        raise ConfigError(f"Config file not found: {cfg_path}")
    with open(cfg_path, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    if not isinstance(cfg, dict):
        raise ConfigError(f"Config file is empty or not a mapping: {cfg_path}")
    for key in _REQUIRED_KEYS:
        _get_nested(cfg, key)
    return cfg


def load_region(name: str, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Load a region preset from ``config/regions/<name>.yaml``.

    The preset must define ``bbox`` and ``flood_date``. The snapshot-date
    data rule is validated here so a bad preset fails before any download.
    """
    region_path = REGIONS_DIR / f"{name}.yaml"
    if not region_path.is_file():
        raise ConfigError(f"Unknown region {name!r}: no file at {region_path}")
    with open(region_path, encoding="utf-8") as fh:
        region = yaml.safe_load(fh)
    if not isinstance(region, dict):
        raise ConfigError(f"Region file is empty or not a mapping: {region_path}")
    if "bbox" not in region or "flood_date" not in region:
        raise ConfigError(f"Region file {region_path} must define bbox and flood_date")
    cfg = cfg if cfg is not None else load_config()
    validate_bbox(region["bbox"], cfg)
    parse_date(region["flood_date"])
    # Data-rule check against the effective OSM snapshot (region registry)
    resolve_osm_snapshot(cfg, region["flood_date"])
    return region


def validate_bbox(
    bbox: Any, cfg: dict[str, Any]
) -> tuple[float, float, float, float]:
    """Validate a bounding box in (lon_min, lat_min, lon_max, lat_max) order.

    Longitude first, latitude second, everywhere in this project. Raises
    ConfigError on wrong order, out-of-range values or excessive area.
    """
    try:
        lon_min, lat_min, lon_max, lat_max = (float(v) for v in bbox)
    except (TypeError, ValueError) as exc:
        raise ConfigError(
            f"Bounding box must be 4 numbers (lon_min lat_min lon_max lat_max), got {bbox!r}"
        ) from exc
    if not (-180 <= lon_min < lon_max <= 180):
        raise ConfigError(
            f"Invalid bbox longitudes: need -180 <= lon_min < lon_max <= 180, got {bbox!r}"
        )
    if not (-90 <= lat_min < lat_max <= 90):
        raise ConfigError(
            f"Invalid bbox latitudes: need -90 <= lat_min < lat_max <= 90, got {bbox!r}"
        )
    hard_max = float(_get_nested(cfg, "validation.hard_max_bbox_side_deg"))
    for side in (lon_max - lon_min, lat_max - lat_min):
        if side > hard_max:
            raise ConfigError(
                f"Bbox side {side:.2f} deg exceeds the configured hard limit "
                f"{hard_max} deg; choose a smaller area (live-demo memory safety)"
            )
    return lon_min, lat_min, lon_max, lat_max


def validate_snapshot_before_event(
    snapshot: str | date, flood: str | date
) -> date:
    """Data rule: the OSM snapshot date must be strictly earlier than the
    flood date. Returns the snapshot date or raises ConfigError.

    This is the startup check demanded by the hackathon rules: using OSM
    state from after the event would leak post-event edits into the pipeline.
    """
    snap = parse_date(snapshot)
    fld = parse_date(flood)
    if snap >= fld:
        raise ConfigError(
            "DATA RULE VIOLATION: OSM snapshot date "
            f"{snap.isoformat()} must be strictly earlier than the flood date "
            f"{fld.isoformat()}. Post-event OSM edits are not allowed as "
            "pipeline input. Fix the geofabrik region entry in "
            "config/default.yaml."
        )
    return snap


def resolve_osm_snapshot(cfg: dict[str, Any], flood: str | date, region: str | None = None) -> dict[str, Any]:
    """Resolve the OSM snapshot entry for a run and enforce the data rules.

    The effective snapshot is the dated Geofabrik region file (DECISIONS D19).
    Two startup checks (both raise ConfigError):
    1. the region's snapshot_date must be strictly earlier than the flood
       date (no post-event OSM edits as pipeline input);
    2. the region's snapshot_date must not be later than the configured cap
       (osm.snapshot_date_cap, 2026-07-27 per the hackathon brief).

    Returns the region entry dict with keys path, snapshot_date, region.
    """
    region = region or _get_nested(cfg, "osm.region_default")
    regions = _get_nested(cfg, "osm.geofabrik.regions")
    if region not in regions:
        raise ConfigError(
            f"OSM region {region!r} is not in the geofabrik registry "
            f"(config osm.geofabrik.regions). Known: {sorted(regions)}. "
            "Add an entry with a dated snapshot strictly before the flood "
            "date (KNOWN_ISSUES K9b)."
        )
    entry = dict(regions[region])
    entry["region"] = region
    snapshot_date = entry.get("snapshot_date")
    if not snapshot_date:
        raise ConfigError(f"Region {region!r} has no snapshot_date; cannot verify the data rule")
    validate_snapshot_before_event(snapshot_date, flood)
    cap = parse_date(_get_nested(cfg, "osm.snapshot_date_cap"))
    if parse_date(snapshot_date) > cap:
        raise ConfigError(
            f"DATA RULE VIOLATION: OSM snapshot {snapshot_date} for region "
            f"{region!r} is later than the allowed cap {cap.isoformat()} "
            "(osm.snapshot_date_cap, from the hackathon brief)."
        )
    if "path" not in entry:
        raise ConfigError(f"Region {region!r} has no geofabrik path")
    return entry


def data_dir(cfg: dict[str, Any], create: bool = True) -> Path:
    """Resolve the data directory (FLOODSIGHT_DATA_DIR overrides config)."""
    override = os.environ.get("FLOODSIGHT_DATA_DIR", "").strip()
    base = Path(override) if override else Path(_get_nested(cfg, "data_dir"))
    path = base if base.is_absolute() else REPO_ROOT / base
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def runs_dir(cfg: dict[str, Any], create: bool = True) -> Path:
    """Resolve the runs directory (analysis outputs, one folder per run).

    FLOODSIGHT_RUNS_DIR environment variable overrides config (used by tests
    and alternative deployments).
    """
    override = os.environ.get("FLOODSIGHT_RUNS_DIR", "").strip()
    if override:
        path = Path(override)
    else:
        raw = Path(_get_nested(cfg, "runs_dir"))
        path = raw if raw.is_absolute() else REPO_ROOT / raw
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def cdse_credentials() -> tuple[str | None, str | None]:
    """Read Copernicus Data Space credentials from the environment.

    Loads .env if python-dotenv is available. Never logs the values.
    """
    try:
        from dotenv import load_dotenv  # optional at import time

        load_dotenv(REPO_ROOT / ".env")
    except ImportError:
        pass
    user = os.environ.get("CDSE_USERNAME", "").strip() or None
    password = os.environ.get("CDSE_PASSWORD", "").strip() or None
    return user, password
