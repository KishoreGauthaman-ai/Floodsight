"""Copernicus DEM (GLO-30 public) tile fetching for the analysis area.

Verified live (Phase 2, DECISIONS D22): the AWS Open Data bucket
``copernicus-dem-30m`` serves 1-degree COG GeoTIFF tiles anonymously, e.g.::

    https://copernicus-dem-30m.s3.amazonaws.com/
      Copernicus_DSM_COG_10_N28_00_E085_00_DEM/
      Copernicus_DSM_COG_10_N28_00_E085_00_DEM.tif

Tile names encode the south-west corner of a 1x1 degree block:
``N{lat:02d}_00_E{lon:03d}_00`` (or S/W for the southern/western
hemispheres). GLO-30 via the CDSE Process API requires CCM registration
since 2026-08-25, so this anonymous bucket is the demo-safe path. The
required attribution for WorldDEM-30 is unchanged and appears on every
output.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any

import requests

from ..config import data_dir

logger = logging.getLogger(__name__)

USER_AGENT = "FloodSight/0.1 (hackathon research prototype)"


class DEMError(Exception):
    """DEM tile download failed."""


def tile_names_for_bbox(bbox: tuple[float, float, float, float]) -> list[str]:
    """1-degree tile names covering the bbox.

    The bbox is (lon_min, lat_min, lon_max, lat_max); tile names use the
    floor of the corner degrees, e.g. lat 27.8..28.25 -> N27_00, N28_00 and
    lon 85.0..85.6 -> E085_00.
    """
    lon_min, lat_min, lon_max, lat_max = bbox
    lats = range(math.floor(lat_min), math.floor(lat_max) + 1)
    lons = range(math.floor(lon_min), math.floor(lon_max) + 1)
    names = []
    for lat in lats:
        for lon in lons:
            ns = f"N{lat:02d}" if lat >= 0 else f"S{abs(lat):02d}"
            ew = f"E{lon:03d}" if lon >= 0 else f"W{abs(lon):03d}"
            names.append(f"{ns}_00_{ew}_00")
    return names


def fetch_dem_tiles(
    bbox: tuple[float, float, float, float],
    cfg: dict[str, Any],
    download: bool = True,
) -> list[Path]:
    """Ensure all DEM tiles covering the bbox exist under data/raw/dem/.

    Returns local tile paths (existing cache hits first, no re-download).
    Anonymous downloads from the AWS Open Data bucket; resume partial files
    via HTTP Range when the server supports it.
    """
    dem_cfg = cfg["acquisition"]["dem"]
    if dem_cfg.get("provider") != "aws_open_data":
        raise DEMError(f"Unknown DEM provider {dem_cfg.get('provider')!r}")
    bucket = dem_cfg["bucket_url"].rstrip("/")
    tile_dir_pattern = dem_cfg["tile_dir_pattern"]
    tile_file_pattern = dem_cfg["tile_file_pattern"]
    # data/raw/ is never edited; tiles are cached under it (README file management)
    raw_dir = data_dir(cfg) / "raw" / "dem"

    paths: list[Path] = []
    for tile in tile_names_for_bbox(bbox):
        remote = f"{bucket}/{tile_dir_pattern.format(tile=tile)}/{tile_file_pattern.format(tile=tile)}"
        local = raw_dir / tile / tile_file_pattern.format(tile=tile)
        if local.exists():
            paths.append(local)
            continue
        if not download:
            raise DEMError(f"DEM tile {tile} missing locally and download disabled")
        local.parent.mkdir(parents=True, exist_ok=True)
        _download_with_resume(remote, local)
        paths.append(local)
    logger.info("DEM tiles for %s: %s", bbox, [p.parent.name for p in paths])
    return paths


def _download_with_resume(remote: str, local: Path, chunk: int = 1 << 20,
                           timeout: int = 120) -> None:
    """Stream a remote file to local path, resuming a partial file if any."""
    headers = {"User-Agent": USER_AGENT}
    mode = "wb"
    if local.exists():
        have = local.stat().st_size
        headers["Range"] = f"bytes={have}-"
        mode = "ab"
    with requests.get(remote, headers=headers, stream=True, timeout=timeout) as resp:
        if resp.status_code == 416:
            # Range not satisfiable: file already complete
            return
        if resp.status_code not in (200, 206):
            raise DEMError(
                f"DEM download failed: HTTP {resp.status_code} for {remote}"
            )
        with open(local, mode) as fh:
            fh.writelines(resp.iter_content(chunk_size=chunk))
    logger.info("DEM tile downloaded: %s (%s bytes)", local.name, local.stat().st_size)


def dem_provenance(bbox: tuple[float, float, float, float],
                   cfg: dict[str, Any]) -> dict[str, Any]:
    """Provenance entry for the DEM inputs of a run."""
    dem_cfg = cfg["acquisition"]["dem"]
    return {
        "source": "Copernicus DEM GLO-30 Public (AWS Open Data bucket "
                  f"{dem_cfg.get('bucket_url')}, COG tiles)",
        "tiles": tile_names_for_bbox(bbox),
        "attribution": (
            "Produced using Copernicus WorldDEM-30 (c) DLR e.V. 2010-2014 and "
            "(c) Airbus Defence and Space GmbH 2014-2018 provided under "
            "COPERNICUS by the European Union and ESA; all rights reserved."
        ),
    }
