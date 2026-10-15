"""Provenance records: what data a run used.

Every run writes ``provenance.json`` listing the Sentinel scenes (IDs, dates,
relative orbit, direction), the DEM source, the OSM snapshot date and the
model checkpoint, so any figure can be traced back to its inputs.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class SceneRecord:
    """One satellite scene used by a run."""

    scene_id: str
    name: str
    sensing_time: str  # ISO 8601
    relative_orbit: int | None = None
    orbit_direction: str | None = None
    platform: str | None = None
    product_type: str | None = None
    local_path: str | None = None


@dataclass
class ProvenanceRecord:
    """Complete input provenance for one analysis run."""

    run_id: str
    bbox: list[float]  # (lon_min, lat_min, lon_max, lat_max)
    flood_date: str  # YYYY-MM-DD
    mode: str
    software_version: str
    created_at: str  # ISO 8601
    osm_snapshot_date: str  # YYYY-MM-DD, must predate flood_date
    sentinel1_pre: SceneRecord | None = None
    sentinel1_post: SceneRecord | None = None
    sentinel2: list[SceneRecord] = field(default_factory=list)
    dem_source: str | None = None
    model_checkpoint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def write(self, run_dir: Path) -> Path:
        """Write provenance.json into the run folder."""
        run_dir.mkdir(parents=True, exist_ok=True)
        path = run_dir / "provenance.json"
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, indent=2)
        return path

    @classmethod
    def read(cls, run_dir: Path) -> ProvenanceRecord:
        """Read and type a provenance.json from a run folder."""
        path = run_dir / "provenance.json"
        if not path.is_file():
            raise FileNotFoundError(f"No provenance.json in {run_dir}")
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
        for key in ("sentinel1_pre", "sentinel1_post"):
            if raw.get(key) is not None:
                raw[key] = SceneRecord(**raw[key])
        raw["sentinel2"] = [SceneRecord(**s) for s in raw.get("sentinel2", [])]
        return cls(**raw)
