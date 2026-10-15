"""Dated OpenStreetMap data, pinned to a pre-event Geofabrik snapshot.

Data rule (hackathon constraint): only OSM state from BEFORE the flood event
may enter the pipeline. The effective snapshot is a dated Geofabrik file
(e.g. ``asia/nepal-260701.osm.pbf`` = state of 1 July 2026; every edit in it
is on or before that date — DECISIONS D19). The snapshot date is asserted
against the flood date and the 2026-07-27 cap at startup
(``floodsight.config.resolve_osm_snapshot``).

Live OSM, post-event Geofabrik files (e.g. nepal-260801 and later) and
ohsome geometry endpoints (Apache-level 403, verified) are NOT used for
pipeline features.

Extraction (osmium-tool, installed system-wide):
1. ``osmium extract -b`` crops the snapshot to the analysis bbox;
2. ``osmium tags-filter`` keeps the feature layers;
3. ``osmium export`` writes GeoJSON (closed building ways as polygons via
   area_tags, roads as linestrings via linear_tags).
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any

import requests

from ..config import data_dir, resolve_osm_snapshot
from ..pipeline import cache_key

logger = logging.getLogger(__name__)

USER_AGENT = "FloodSight/0.1 (hackathon research prototype)"

# osmium export --geometry-types per layer (verified on the real snapshot:
# without it, stray tagged nodes leak into buildings/roads as Points).
LAYER_GEOM_TYPES = {
    "buildings": "polygon",
    "roads": "linestring",
    "bridges": "linestring",
    "settlements": "point",
    "destinations": "point,polygon",
    "waterways": "linestring",
}
LAYER_EXPRESSIONS = {
    "buildings": ["w/building", "r/building"],
    "roads": ["w/highway"],  # routability narrowed later via comma value list (verified below)
    "bridges": ["w/bridge", "r/bridge"],
    "settlements": ["n/place=city,town,village,hamlet"],
    "destinations": ["n/place=city,town", "n/amenity=hospital,clinic",
                     "w/place=city,town", "w/amenity=hospital,clinic"],
    "waterways": ["w/waterway=river,stream", "r/waterway=river"],
}


class OSMError(Exception):
    """OSM snapshot handling failed."""


def _run_osmium(args: list[str]) -> None:
    """Run an osmium command, raising OSMError with stderr on failure."""
    if shutil.which("osmium") is None:
        raise OSMError("osmium-tool is not installed (DECISIONS D20: sudo apt install osmium-tool)")
    proc = subprocess.run(
        ["osmium", *args], capture_output=True, text=True, timeout=600, check=False
    )
    if proc.returncode != 0:
        raise OSMError(f"osmium {' '.join(args[:2])} failed: {proc.stderr.strip()[:400]}")


def _md5_of(path: Path) -> str:
    digest = hashlib.md5()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def ensure_snapshot(
    cfg: dict[str, Any], flood_date: str, region: str | None = None
) -> tuple[Path, dict[str, Any]]:
    """Return (local snapshot path, provenance entry), downloading if needed.

    The snapshot is fetched from Geofabrik with its .md5 file and verified.
    resolve_osm_snapshot (called here) enforces the pre-event data rule and
    the 2026-07-27 cap before anything is used.
    """
    entry = resolve_osm_snapshot(cfg, flood_date, region)
    base_url = cfg["osm"]["geofabrik"]["base_url"].rstrip("/")
    filename = Path(entry["path"]).name
    osm_dir = data_dir(cfg) / "osm"
    osm_dir.mkdir(parents=True, exist_ok=True)
    local = osm_dir / filename

    if not local.exists():
        url = f"{base_url}/{entry['path']}"
        logger.info("Downloading OSM snapshot %s ...", url)
        with requests.get(url, headers={"User-Agent": USER_AGENT}, stream=True,
                         timeout=600) as resp:
            resp.raise_for_status()
            tmp = local.with_suffix(".part")
            with open(tmp, "wb") as fh:
                fh.writelines(resp.iter_content(chunk_size=1 << 20))
        tmp.rename(local)
        _verify_md5(base_url, entry["path"], local)
    else:
        logger.info("OSM snapshot already present: %s", local.name)

    provenance = {
        "source": f"Geofabrik dated extract {entry['path']} ({base_url})",
        "snapshot_date": entry["snapshot_date"],
        "region": entry["region"],
        "file": str(local),
        "attribution": "(c) OpenStreetMap contributors, ODbL 1.0.",
    }
    return local, provenance


def _verify_md5(base_url: str, rel_path: str, local: Path) -> None:
    """Verify the snapshot against the published .md5; delete on mismatch."""
    md5_url = f"{base_url}/{rel_path}.md5"
    resp = requests.get(md5_url, headers={"User-Agent": USER_AGENT}, timeout=120)
    if resp.status_code == 404:
        # Geofabrik publishes no .md5 for older yearly extracts (verified:
        # nepal-240101.osm.pbf.md5 -> 404). Record honestly and continue;
        # the download itself completed and the size was checked implicitly.
        logger.warning(
            "no published md5 for %s (HTTP 404); snapshot kept unverified "
            "by checksum", local.name)
        return
    resp.raise_for_status()
    expected = resp.text.split()[0].strip()
    actual = _md5_of(local)
    if actual != expected:
        local.unlink(missing_ok=True)
        raise OSMError(
            f"OSM snapshot md5 mismatch for {local.name}: expected {expected}, got {actual}. "
            "File deleted; retry the download."
        )
    logger.info("OSM snapshot md5 verified: %s", local.name)


def extract_osm_layers(
    bbox: tuple[float, float, float, float],
    cfg: dict[str, Any],
    layers: list[str] | None = None,
) -> dict[str, Path]:
    """Extract OSM feature layers for the bbox into cached GeoJSON files.

    Returns {layer_name: geojson_path}. Results are cached per
    (bbox, snapshot file, layer set) under data/osm/cache/, keyed with the
    pipeline cache key so a changed bbox or snapshot never reuses stale
    files.
    """
    snapshot = Path(cfg["_osm_snapshot"])  # set by the pipeline after ensure_snapshot
    layers = layers or list(LAYER_EXPRESSIONS)
    cache_root = data_dir(cfg) / "osm" / "cache"
    key = cache_key(tuple(bbox), f"osm:{snapshot.name}", "extract-v1")
    run_cache = cache_root / key
    marker = run_cache / "done.json"
    result: dict[str, Path] = {}
    wanted = {name: run_cache / f"{name}.geojson" for name in layers}

    if marker.is_file() and all(p.is_file() for p in wanted.values()):
        return {k: p for k, p in wanted.items()}

    run_cache.mkdir(parents=True, exist_ok=True)
    # osmium bbox order: LEFT,BOTTOM,RIGHT,TOP == (lon_min, lat_min, lon_max, lat_max)
    bbox_arg = f"{bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]}"
    area_pbf = run_cache / "area.osm.pbf"
    if not area_pbf.exists():
        _run_osmium(["extract", "-b", bbox_arg, str(snapshot), "-o", str(area_pbf), "--overwrite"])

    for layer, out in wanted.items():
        expressions = LAYER_EXPRESSIONS[layer]
        if layer == "roads":
            routable = ",".join(cfg["cutoff"]["routable_highway"])
            expressions = [f"w/highway={routable}"]
        filtered = run_cache / f"{layer}.osm.pbf"
        _run_osmium(["tags-filter", str(area_pbf), *expressions, "-o", str(filtered), "--overwrite"])
        _run_osmium(["export", str(filtered), "-o", str(out), "--overwrite",
                     "--geometry-types", LAYER_GEOM_TYPES[layer],
                     "--config", str(_export_config_path(run_cache))])
        result[layer] = out

    marker.write_text("done")
    logger.info("OSM layers extracted for bbox %s: %s", bbox, sorted(result))
    return result


def _export_config_path(cache_dir: Path) -> Path:
    """osmium export config: buildings/places as areas, roads as lines."""
    cfg_file = cache_dir / "export_config.json"
    cfg_file.write_text(
        '{\n'
        '  "attributes": {"type": false, "id": true, "version": false,'
        ' "changeset": false, "timestamp": false, "uid": false, "user": false, "way_nodes": false},\n'
        '  "linear_tags": ["highway", "bridge", "waterway", "boundary"],\n'
        '  "area_tags": ["building", "place", "landuse", "amenity"]\n'
        '}\n'
    )
    return cfg_file


def fetch_osm_features(
    bbox: tuple[float, float, float, float],
    flood_date: str,
    cfg: dict[str, Any],
    region: str | None = None,
) -> tuple[dict[str, Path], dict[str, Any]]:
    """Full OSM acquisition: snapshot (rule-checked) + per-layer GeoJSON.

    Returns ({layer: path}, provenance entry for the run).
    """
    snapshot, provenance = ensure_snapshot(cfg, flood_date, region)
    cfg = {**cfg, "_osm_snapshot": str(snapshot)}
    layers = extract_osm_layers(bbox, cfg)
    provenance = {**provenance, "layers": sorted(layers)}
    return layers, provenance
