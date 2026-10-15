"""Evidence-based debris / change layer (Stream C, ARCHITECTURE.md).

Kuro Siwo has no debris class, so debris is NEVER claimed as a trained
output. The layer combines evidence — labelled "probable debris / change":

1.  Persistent backscatter BRIGHTENING on land: the post-event pixel got
    measurably brighter than pre-event (rough deposits, boulder fields,
    damaged surfaces roughen C-band backscatter). Verified on the real
    Trishuli pair: near-river pixels brighten >3 dB at 2.5x the rate of
    far-from-river pixels (D32).
2.  NOT open water: the post-event pixel is brighter than the dark-water
    threshold (flood water is dark; debris is bright).
3.  Valley-floor position: gentle slope (from the SNAP DEM band) and within
    a configured distance of a mapped river (waterways from the same dated
    OSM snapshot — a proxy for height-above-drainline until HAND is
    implemented, KNOWN_ISSUES K13; documented as such).

Confidence mirrors the baseline: distance of the ratio above the threshold,
clipped to [0, 1]. Everything is config-driven; nothing is hardcoded.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import rasterio

from .baseline import (
    BAND_ELEV,
    BAND_INCIDENCE,
    BAND_VV,
    remove_small_blobs,
    slope_degrees,
    to_db,
)

logger = logging.getLogger(__name__)


class DebrisError(Exception):
    """Debris evidence layer failed."""


def debris_evidence(
    pre_tif,
    post_tif,
    waterways_gdf,
    cfg: dict[str, Any],
) -> dict[str, Any]:
    """Probable-debris/change layer on the co-registered pair.

    Returns the same structure as baseline.log_ratio_change (mask,
    confidence, stats, transform, crs, shape) for the debris layer.
    """
    from rasterio import features

    s1_cfg = cfg["preprocessing"]["s1"]
    dem_cfg = cfg["preprocessing"]["dem"]
    base_cfg = cfg["detection"]["baseline"]
    debris_cfg = cfg["detection"].get("debris", {})
    bright_db = float(debris_cfg.get("bright_change_db", 3.0))
    max_river_m = float(debris_cfg.get("max_river_distance_m", 1000.0))
    min_blob = int(base_cfg.get("min_blob_pixels", 20))

    with rasterio.open(pre_tif) as pre_src:
        pre_vv = pre_src.read(BAND_VV)
        post_path = post_tif  # same grid as pre (co-registered)
        elev = pre_src.read(BAND_ELEV)
        inc = pre_src.read(BAND_INCIDENCE)
        transform, crs = pre_src.transform, pre_src.crs
        pixel_m = float(pre_src.transform.a)
        shape = (pre_src.height, pre_src.width)
    with rasterio.open(post_path) as post_src:
        post_vv = post_src.read(BAND_VV)

    pre_db = to_db(pre_vv, float(s1_cfg["db_min"]), float(s1_cfg["db_max"]))
    post_db = to_db(post_vv, float(s1_cfg["db_min"]), float(s1_cfg["db_max"]))
    ratio = post_db - pre_db

    with np.errstate(invalid="ignore"):
        slope = slope_degrees(elev, pixel_m)
    valid = (
        np.isfinite(pre_db) & np.isfinite(post_db)
        & (elev > 0) & (inc > 0) & (inc <= 90)
        & np.isfinite(slope) & (slope <= float(dem_cfg["slope_max_deg"]))
    )

    # valley floor: within max_distance_m of a mapped waterway
    near_river = np.ones(shape, dtype=bool)
    if waterways_gdf is not None and len(waterways_gdf):
        rivers = waterways_gdf.to_crs(crs)
        buffered = rivers.geometry.buffer(max_river_m)
        burned = features.rasterize(
            ((geom, 1) for geom in buffered.geometry),
            out_shape=shape, transform=transform, fill=0, dtype="uint8",
        )
        near_river = burned.astype(bool)
        logger.info(
            "valley-floor mask: %.1f%% of pixels within %.0f m of mapped waterways",
            100 * near_river.mean(), max_river_m,
        )

    debris = (
        (ratio > bright_db)                      # persistent brightening
        & (post_db >= float(base_cfg.get("dark_water_db", -17.0)))  # not water
        & valid & near_river                     # physically plausible valley floor
    )
    debris = remove_small_blobs(debris, min_blob)

    with np.errstate(invalid="ignore"):
        conf = np.clip((ratio - bright_db) / float(debris_cfg.get("confidence_scale_db", 3.0)), 0, 1)
    confidence = np.where(debris, conf, 0.0)

    pixel_area_km2 = (pixel_m / 1000.0) ** 2
    stats = {
        "debris_pixels": int(debris.sum()),
        "area_km2": round(float(debris.sum()) * pixel_area_km2, 4),
        "bright_change_db": bright_db,
        "max_river_distance_m": max_river_m,
        "valid_pixel_share": round(float(valid.mean()), 4),
        "near_river_share": round(float(near_river.mean()), 4),
    }
    return {
        "mask": debris, "confidence": confidence, "ratio": ratio,
        "transform": transform, "crs": crs, "shape": shape, "stats": stats,
    }
