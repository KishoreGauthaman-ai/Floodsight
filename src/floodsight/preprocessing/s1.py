"""Sentinel-1 radar preprocessing via ESA SNAP headless (gpt).

DECISIONS D28: SNAP 14.0.0 is the chosen radar path (verified end-to-end on
the real Trishuli pair; openEO rejected for the pipeline because its auth
options are interactive or experimental service accounts).

Chain per scene: Read (SAFE zip, no extraction) -> Subset(geoRegion) ->
Apply-Orbit-File (precise, auto-download, continueOnFail) ->
ThermalNoiseRemoval -> Calibration(sigma0) -> Range-Doppler
Terrain-Correction (Copernicus 30m Global DEM, UTM, 10 m, saveDEM +
localIncidenceAngle) -> BandSelect(Sigma0_VV, Sigma0_VH, elevation,
localIncidenceAngle; D29: never rely on alphabetical band order) ->
Write(GeoTIFF).

The pre and post scenes are terrain-corrected independently; the post raster
is then co-registered onto the pre raster's grid with bilinear resampling
(the TC grids differ by up to ~100 m for the same track 12 days apart,
verified on the real pair), so change detection compares exactly aligned
pixels. Output is linear sigma0; dB conversion happens in the detection
module.
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from pathlib import Path
from xml.sax.saxutils import escape

import rasterio
from rasterio.warp import Resampling, reproject

logger = logging.getLogger(__name__)

# Deterministic band order written by the SNAP graph (D29).
BAND_ORDER = ["Sigma0_VV", "Sigma0_VH", "elevation", "localIncidenceAngle"]


class S1PreprocessError(Exception):
    """SNAP preprocessing failed."""


def utm_epsg_for_bbox(bbox: tuple[float, float, float, float]) -> int:
    """UTM WGS84 EPSG code for the bbox center (north: 326xx, south: 327xx)."""
    lon_min, lat_min, lon_max, lat_max = bbox
    zone = int(( (lon_min + lon_max) / 2 + 180) / 6) + 1
    if (lat_min + lat_max) / 2 >= 0:
        return 32600 + zone
    return 32700 + zone


def bbox_wkt(bbox: tuple[float, float, float, float]) -> str:
    """WKT polygon in (lon_min, lat_min, lon_max, lat_max) order."""
    return (
        f"POLYGON(({bbox[0]} {bbox[1]}, {bbox[2]} {bbox[1]}, "
        f"{bbox[2]} {bbox[3]}, {bbox[0]} {bbox[3]}, {bbox[0]} {bbox[1]}))"
    )


def snap_gpt_path(cfg: dict) -> str:
    """Resolve the gpt executable: SNAP_GPT_PATH env, then config."""
    env_path = os.environ.get("SNAP_GPT_PATH", "").strip()
    if env_path:
        return env_path
    configured = cfg["preprocessing"]["s1"].get("snap_gpt_path", "")
    if configured:
        return configured
    raise S1PreprocessError(
        "ESA SNAP gpt path not configured: set SNAP_GPT_PATH in .env or "
        "preprocessing.s1.snap_gpt_path in config (DECISIONS D28)."
    )


def build_graph_xml(
    scene_path: Path,
    bbox: tuple[float, float, float, float],
    out_path: Path,
    epsg: int,
    pixel_spacing_m: float,
    speckle_window: int = 3,
) -> str:
    """SNAP graph XML for the full S1 preprocessing chain (verified on the
    real Trishuli scenes; parameter names checked with `gpt <op> -h`)."""
    region = escape(bbox_wkt(bbox))
    bands = ",".join(BAND_ORDER)
    scene_path = Path(scene_path).resolve()
    out_path = Path(out_path).resolve()
    return f"""<graph id="floodsight-s1-prep">
  <version>1.0</version>

  <node id="Read">
    <operator>Read</operator>
    <parameters>
      <file>{scene_path}</file>
    </parameters>
  </node>

  <node id="Subset">
    <operator>Subset</operator>
    <sources>
      <sourceProduct refid="Read"/>
    </sources>
    <parameters>
      <geoRegion>{region}</geoRegion>
    </parameters>
  </node>

  <node id="Orbit">
    <operator>Apply-Orbit-File</operator>
    <sources>
      <sourceProduct refid="Subset"/>
    </sources>
    <parameters>
      <orbitType>Sentinel Precise (Auto Download)</orbitType>
      <continueOnFail>true</continueOnFail>
    </parameters>
  </node>

  <node id="ThermalNoise">
    <operator>ThermalNoiseRemoval</operator>
    <sources>
      <sourceProduct refid="Orbit"/>
    </sources>
    <parameters>
      <removeThermalNoise>true</removeThermalNoise>
    </parameters>
  </node>

  <node id="Calibration">
    <operator>Calibration</operator>
    <sources>
      <sourceProduct refid="ThermalNoise"/>
    </sources>
    <parameters>
      <outputSigmaBand>true</outputSigmaBand>
      <outputBetaBand>false</outputBetaBand>
      <outputGammaBand>false</outputGammaBand>
    </parameters>
  </node>

  <node id="SpeckleFilter">
    <operator>Speckle-Filter</operator>
    <sources>
      <sourceProduct refid="Calibration"/>
    </sources>
    <parameters>
      <filter>Lee</filter>
      <filterSizeX>{speckle_window}</filterSizeX>
      <filterSizeY>{speckle_window}</filterSizeY>
    </parameters>
  </node>

  <node id="TerrainCorrection">
    <operator>Terrain-Correction</operator>
    <sources>
      <sourceProduct refid="SpeckleFilter"/>
    </sources>
    <parameters>
      <demName>Copernicus 30m Global DEM</demName>
      <demResamplingMethod>BILINEAR_INTERPOLATION</demResamplingMethod>
      <imgResamplingMethod>BILINEAR_INTERPOLATION</imgResamplingMethod>
      <pixelSpacingInMeter>{pixel_spacing_m}</pixelSpacingInMeter>
      <mapProjection>EPSG:{epsg}</mapProjection>
      <saveDEM>true</saveDEM>
      <saveLocalIncidenceAngle>true</saveLocalIncidenceAngle>
      <nodataValueAtSea>true</nodataValueAtSea>
    </parameters>
  </node>

  <node id="BandSelect">
    <operator>BandSelect</operator>
    <sources>
      <sourceProduct refid="TerrainCorrection"/>
    </sources>
    <parameters>
      <sourceBands>{bands}</sourceBands>
    </parameters>
  </node>

  <node id="Write">
    <operator>Write</operator>
    <sources>
      <sourceProduct refid="BandSelect"/>
    </sources>
    <parameters>
      <file>{out_path}</file>
      <formatName>GeoTIFF</formatName>
    </parameters>
  </node>
</graph>
"""


def run_snap_graph(graph_xml: str, cfg: dict, work_dir: Path) -> None:
    """Write the graph to a file and run gpt headless."""
    gpt = snap_gpt_path(cfg)
    if not os.path.isfile(gpt) or not os.access(gpt, os.X_OK):
        raise S1PreprocessError(f"gpt not executable at {gpt!r} (DECISIONS D28)")
    work_dir.mkdir(parents=True, exist_ok=True)
    graph_file = work_dir / "graph.xml"
    graph_file.write_text(graph_xml)
    parallelism = str(cfg["preprocessing"]["s1"].get("snap_parallelism", 4))
    t0 = time.monotonic()
    proc = subprocess.run(
        [gpt, graph_file.name, "-q", parallelism],
        cwd=work_dir, capture_output=True, text=True, timeout=3600, check=False,
    )
    if proc.returncode != 0:
        raise S1PreprocessError(
            f"gpt failed (exit {proc.returncode}): {proc.stderr.strip()[-600:]}"
        )
    logger.info("SNAP graph done in %.0f s", time.monotonic() - t0)


def coregister(
    post_tif: Path, pre_tif: Path, out_path: Path
) -> Path:
    """Reproject the post raster onto the pre raster's exact grid.

    Verified need: same-track TC outputs 12 days apart differ by up to
    ~100 m in origin and a few pixels in size (real pair, 2026-08-19 vs
    2026-08-31). Bilinear resampling; the result shares the pre grid
    exactly, so pixel-wise change detection is valid.
    """
    with rasterio.open(pre_tif) as pre_src:
        pre_transform = pre_src.transform
        pre_crs = pre_src.crs
        width, height = pre_src.width, pre_src.height
        pre_nodata = pre_src.nodata
    with rasterio.open(post_tif) as post_src:
        profile = post_src.profile.copy()
        bands = post_src.count
        profile.update(
            transform=pre_transform, crs=pre_crs,
            width=width, height=height, compress="deflate",
        )
        # atomic write: a killed run must never leave a truncated cache
        # raster behind (verified corruption, Round 4: a zombie pipeline
        # was killed mid-write and left a 0-byte s1_post_coreg.tif)
        tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
        with rasterio.open(tmp_path, "w", **profile) as dst:
            for band in range(1, bands + 1):
                reproject(
                    source=rasterio.band(post_src, band),
                    destination=rasterio.band(dst, band),
                    src_transform=post_src.transform,
                    src_crs=post_src.crs,
                    src_nodata=post_src.nodata,
                    dst_transform=pre_transform,
                    dst_crs=pre_crs,
                    dst_nodata=pre_nodata,
                    resampling=Resampling.bilinear,
                )
        os.replace(tmp_path, out_path)
    logger.info("Co-registered %s onto %s grid -> %s",
                post_tif.name, pre_tif.name, out_path.name)
    return out_path


def preprocess_scene(
    scene_zip: Path,
    bbox: tuple[float, float, float, float],
    out_tif: Path,
    cfg: dict,
    work_dir: Path,
) -> Path:
    """Run the full SNAP chain for one scene; cached by output existence."""
    if out_tif.exists():
        logger.info("Preprocessed raster exists, skipping SNAP: %s", out_tif.name)
        return out_tif
    epsg = utm_epsg_for_bbox(bbox)
    spacing = float(cfg["preprocessing"]["s1"].get("pixel_spacing_m", 10.0))
    speckle = int(cfg["preprocessing"]["s1"].get("speckle_window", 3))
    graph = build_graph_xml(scene_zip, bbox, out_tif, epsg, spacing, speckle)
    run_snap_graph(graph, cfg, work_dir)
    if not out_tif.exists():
        raise S1PreprocessError(f"SNAP produced no output at {out_tif}")
    return out_tif


def preprocess_s1_pair(
    pre_zip: Path,
    post_zip: Path,
    bbox: tuple[float, float, float, float],
    cfg: dict,
    out_dir: Path,
) -> dict[str, Path]:
    """Preprocess both scenes and co-register: the Phase 3 deliverable.

    Returns {"pre": path, "post": path} where "post" is on the pre grid.
    Cached end-to-end: if both terrain-corrected rasters AND the
    co-registration exist, nothing is re-run (a cached corridor pair
    re-ran a multi-minute coregister on every open, Round 4 D72).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    pre_tif = out_dir / "s1_pre_sigma0_tc.tif"
    post_coreg = out_dir / "s1_post_coreg.tif"
    if pre_tif.is_file() and (out_dir / "s1_post_sigma0_tc.tif").is_file() \
            and post_coreg.is_file():
        logger.info("pair fully cached (TC + coregistration): %s", out_dir.name)
        return {"pre": pre_tif, "post": post_coreg}
    pre_tif = preprocess_scene(pre_zip, bbox, pre_tif, cfg,
                               out_dir / "snap_pre")
    post_tc = preprocess_scene(post_zip, bbox, out_dir / "s1_post_sigma0_tc.tif", cfg,
                               out_dir / "snap_post")
    post_coreg = coregister(post_tc, pre_tif, post_coreg)
    return {"pre": pre_tif, "post": post_coreg}
