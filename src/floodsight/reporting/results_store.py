"""Structured results store: the single source of truth for a run.

Implements the data contract from AGENT.md section 9. ``results.json`` is
written once per run from the stage artifacts and validated on write AND on
read — the dashboard, report and assistant must break loudly when a field
is missing, never guess. Unknown values are ``null`` (never 0, never
invented).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

# Required top-level sections and their required keys. Warnings and
# section-specific list payloads are validated for presence only.
_REQUIRED: dict[str, tuple[str, ...]] = {
    "meta": ("run_id", "bbox", "flood_date", "created_at", "mode",
             "software_version", "schema_version"),
    "inputs": ("sentinel1_pre", "sentinel1_post", "sentinel2", "dem_source",
               "osm_snapshot_date", "model_checkpoint"),
    "flood": ("area_km2_new_water", "area_km2_probable_debris",
              "confidence_summary", "raster", "vector"),
    "damage": ("buildings", "roads", "bridges"),
    "cutoff": ("already_isolated", "likely_cut", "possibly_cut",
               "still_connected_count"),
    "warnings": (),
}


class ResultsValidationError(Exception):
    """results.json does not match the contract; consumers must not guess."""


def validate(payload: dict[str, Any]) -> list[str]:
    """Validate a results payload; returns the list of problems (empty=ok)."""
    problems: list[str] = []
    if not isinstance(payload, dict):
        return [f"payload is {type(payload).__name__}, expected dict"]
    for section, keys in _REQUIRED.items():
        if section not in payload:
            problems.append(f"missing section '{section}'")
            continue
        if section == "warnings":
            # contract: a list of {code, message, severity} objects
            if not isinstance(payload[section], list):
                problems.append("section 'warnings' must be a list")
            continue
        if not isinstance(payload[section], dict):
            problems.append(f"section '{section}' is not a mapping")
            continue
        for key in keys:
            if key not in payload[section]:
                problems.append(f"missing key '{section}.{key}'")
    if payload.get("meta", {}).get("schema_version") != SCHEMA_VERSION:
        problems.append(
            f"schema_version is {payload.get('meta', {}).get('schema_version')!r}, "
            f"expected {SCHEMA_VERSION}"
        )
    return problems


class ResultsStore:
    """Read/write access to one run's results.json."""

    def __init__(self, run_dir: Path):
        self.run_dir = Path(run_dir)
        self.path = self.run_dir / "results.json"

    def write(self, payload: dict[str, Any]) -> Path:
        problems = validate(payload)
        if problems:
            raise ResultsValidationError(
                "Refusing to write invalid results: " + "; ".join(problems)
            )
        self.run_dir.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        return self.path

    def read(self) -> dict[str, Any]:
        if not self.path.is_file():
            raise FileNotFoundError(
                f"No results.json in {self.run_dir}; the run may not have "
                "reached the write_results stage."
            )
        with open(self.path, encoding="utf-8") as fh:
            payload = json.load(fh)
        problems = validate(payload)
        if problems:
            raise ResultsValidationError(
                f"results.json is invalid: {'; '.join(problems)}"
            )
        return payload


def build_results(
    run_dir: Path,
    provenance: dict[str, Any],
    detection: dict[str, Any],
    damage: dict[str, Any],
    cutoff: dict[str, Any],
    warnings: list[dict[str, str]],
    meta_extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble a valid results payload from the run's real artifacts.

    Every number comes from the stage payloads; nothing is invented here.
    Sections not yet produced by the pipeline (e.g. debris) are null.
    """
    run_dir = Path(run_dir)
    detection_stats = detection.get("stats", {}) if detection else {}
    payload: dict[str, Any] = {
        "meta": {
            "run_id": provenance.get("run_id"),
            "bbox": provenance.get("bbox"),
            "flood_date": provenance.get("flood_date"),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "mode": provenance.get("mode"),
            "software_version": provenance.get("software_version"),
            "schema_version": SCHEMA_VERSION,
            **(meta_extra or {}),
        },
        "inputs": {
            "sentinel1_pre": provenance.get("sentinel1_pre"),
            "sentinel1_post": provenance.get("sentinel1_post"),
            "sentinel2": provenance.get("sentinel2", []),
            "dem_source": provenance.get("dem_source"),
            "osm_snapshot_date": provenance.get("osm_snapshot_date"),
            "model_checkpoint": provenance.get("model_checkpoint"),
        },
        "flood": {
            "area_km2_new_water": detection_stats.get("flood_area_km2"),
            "area_km2_probable_debris":
                ((detection or {}).get("stats") or {}).get(
                    "debris_area_km2_union",
                    ((detection or {}).get("debris_stats") or {}).get("area_km2")),
            "confidence_summary": {
                "method": detection.get("method"),
                "ratio_threshold_db": detection_stats.get("ratio_threshold_db"),
                "valid_pixel_share": detection_stats.get("valid_pixel_share"),
            },
            "raster": detection.get("flood_water_tif"),
            "vector": detection.get("flood_geojson"),
            "coverage_fraction": detection.get("coverage_fraction"),
            "analysed_area_geojson": detection.get("analysed_area_geojson"),
            "per_track": detection.get("per_track"),
        },
        "damage": damage,
        "cutoff": cutoff,
        "flood_path": None,  # Phase 10 bonus
        "warnings": warnings,
    }
    problems = validate(payload)
    if problems:
        raise ResultsValidationError(
            "Assembled results are invalid: " + "; ".join(problems)
        )
    return payload
