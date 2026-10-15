"""Log-ratio change-detection baseline.

A classical method (no training, no weights): the dB difference of the
speckle-filtered, terrain-corrected pre/post VV pair, thresholded
automatically with Otsu. This is the sanity check and the fallback when the
trained model is missing or fails (AGENT.md 8.3).

Flood definition used by the baseline (all conditions must hold):
1.  the pixel got darker: ratio = post_dB - pre_dB exceeds the Otsu
    threshold of the ratio histogram;
2.  the post-event pixel looks like water: post_dB < dark_water_db;
3.  it was NOT already water before the event: pre_dB >= dark_water_db
    (permanent-water subtraction, so rivers are not counted as flood);
4.  the pixel is physically plausible: valid incidence angle (the SNAP
    local-incidence band flags layover/shadow) and slope below the
    configured maximum (from the SNAP DEM band).

Confidence is a documented, simple function of the ratio distance above
the threshold, scaled by ``confidence_change_scale_db`` and clipped to
[0, 1].
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import rasterio
from scipy import ndimage

logger = logging.getLogger(__name__)

# Band layout written by the SNAP graph (D29): 1=Sigma0_VV, 2=Sigma0_VH,
# 3=elevation, 4=localIncidenceAngle.
BAND_VV = 1
BAND_ELEV = 3
BAND_INCIDENCE = 4


class BaselineError(Exception):
    """Baseline detection failed."""


def to_db(sigma0: np.ndarray, db_min: float, db_max: float) -> np.ndarray:
    """Linear sigma0 -> decibels; non-positive values become NaN."""
    arr = sigma0.astype("float64")
    arr[arr <= 0] = np.nan
    with np.errstate(invalid="ignore"):
        db = 10.0 * np.log10(arr)
    db[(db < db_min) | (db > db_max)] = np.nan
    return db


def otsu_threshold(values: np.ndarray, bins: int = 256) -> float:
    """Otsu's threshold on finite values (maximizing between-class variance).

    Pure numpy; deterministic. Raises BaselineError on degenerate input.
    """
    vals = values[np.isfinite(values)]
    if vals.size < bins:
        raise BaselineError(f"Too few finite values for Otsu: {vals.size}")
    hist, edges = np.histogram(vals, bins=bins)
    hist = hist.astype("float64")
    centers = (edges[:-1] + edges[1:]) / 2
    w1 = hist.cumsum()                      # class-1 weight
    m1 = (hist * centers).cumsum()          # class-1 cumulative sum (count * center)
    total = w1[-1]
    grand = m1[-1]
    w2 = total - w1
    variance12 = np.zeros_like(w1)
    ok = (w1 > 0) & (w2 > 0)
    # between-class variance: (m1*total - grand*w1)^2 / (w1*w2)
    variance12[ok] = (m1[ok] * total - grand * w1[ok]) ** 2 / (w1[ok] * w2[ok])
    idx = int(np.argmax(variance12[:-1]))
    return float(centers[idx])


def slope_degrees(elevation: np.ndarray, pixel_m: float) -> np.ndarray:
    """Slope in degrees from a DEM grid via central differences."""
    dzdy, dzdx = np.gradient(elevation, pixel_m, pixel_m)
    return np.degrees(np.arctan(np.hypot(dzdx, dzdy)))


def remove_small_blobs(mask: np.ndarray, min_pixels: int) -> np.ndarray:
    """Keep only connected components of at least min_pixels (8-connectivity)."""
    labels, n = ndimage.label(mask, structure=np.ones((3, 3), dtype=int))
    if n == 0:
        return mask
    sizes = np.bincount(labels.ravel())
    sizes[0] = 0
    keep = sizes >= min_pixels
    return keep[labels]


def confidence_from_ratio(
    ratio: np.ndarray, threshold: float, scale_db: float
) -> np.ndarray:
    """Distance of the ratio above the threshold, clipped to [0, 1]."""
    with np.errstate(invalid="ignore"):
        conf = (ratio - threshold) / scale_db
    return np.clip(conf, 0.0, 1.0)


def log_ratio_change(
    pre_tif, post_tif, cfg: dict[str, Any]
) -> dict[str, Any]:
    """Full baseline flood detection on the co-registered pair.

    Returns {"mask", "confidence", "ratio", "threshold", "dark_water_db",
    "transform", "crs", "stats"} — all arrays on the pre grid.
    """
    s1_cfg = cfg["preprocessing"]["s1"]
    base_cfg = cfg["detection"]["baseline"]
    dem_cfg = cfg["preprocessing"]["dem"]
    dark_water_db = float(base_cfg.get("dark_water_db", -17.0))
    min_blob = int(base_cfg.get("min_blob_pixels", 20))
    scale_db = float(cfg["detection"].get("confidence_change_scale_db", 3.0))

    with rasterio.open(pre_tif) as pre_src:
        pre_vv = pre_src.read(BAND_VV)
        elev = pre_src.read(BAND_ELEV)
        incidence = pre_src.read(BAND_INCIDENCE)
        transform, crs = pre_src.transform, pre_src.crs
        pixel_m = float(pre_src.transform.a)
        shape = pre_vv.shape
    with rasterio.open(post_tif) as post_src:
        post_vv = post_src.read(BAND_VV)

    pre_db = to_db(pre_vv, float(s1_cfg["db_min"]), float(s1_cfg["db_max"]))
    post_db = to_db(post_vv, float(s1_cfg["db_min"]), float(s1_cfg["db_max"]))

    # physically plausible pixels: real DEM value, sane incidence angle
    # (<=0 or >90 flags layover/shadow areas from the SNAP DEM computation)
    with np.errstate(invalid="ignore"):
        slope = slope_degrees(elev, pixel_m)
    valid = (
        np.isfinite(pre_db) & np.isfinite(post_db)
        & np.isfinite(elev) & (elev > 0)
        & np.isfinite(incidence) & (incidence > 0) & (incidence <= 90)
        & np.isfinite(slope) & (slope <= float(dem_cfg["slope_max_deg"]))
    )
    valid_share = float(valid.mean())
    if valid_share < 0.05:
        raise BaselineError(
            f"Only {valid_share:.1%} of pixels are physically valid; "
            "the area is probably unusable for the baseline."
        )

    ratio = post_db - pre_db
    threshold = otsu_threshold(ratio[valid], int(base_cfg.get("otsu_bins", 256)))
    logger.info("Otsu ratio threshold: %.2f dB over dark-water %.1f dB", threshold, dark_water_db)

    flood = (
        (ratio > threshold)
        & (post_db < dark_water_db)
        & ~(pre_db < dark_water_db)  # permanent-water subtraction
        & valid
    )
    flood = remove_small_blobs(flood, min_blob)

    confidence = np.where(flood, confidence_from_ratio(ratio, threshold, scale_db), 0.0)

    pixel_area_km2 = (pixel_m / 1000.0) ** 2
    stats = {
        "flood_pixels": int(flood.sum()),
        "flood_area_km2": round(float(flood.sum()) * pixel_area_km2, 4),
        "ratio_threshold_db": round(threshold, 3),
        "dark_water_db": dark_water_db,
        "valid_pixel_share": round(valid_share, 4),
        "post_water_pixels": int(((post_db < dark_water_db) & valid).sum()),
        "pre_water_pixels": int(((pre_db < dark_water_db) & valid).sum()),
    }
    return {
        "mask": flood,
        "confidence": confidence,
        "ratio": ratio,
        "valid": valid,
        "threshold": threshold,
        "transform": transform,
        "crs": crs,
        "shape": shape,
        "stats": stats,
    }


def vectorize_flood(result: dict[str, Any], min_confidence: float = 0.0):
    """Polygonize the flood mask; returns a GeoDataFrame in EPSG:4326.

    Areas are computed in the source UTM CRS (metric), then geometries are
    reprojected to EPSG:4326 for exchange (README CRS convention).
    """
    import geopandas as gpd
    from rasterio import features
    from shapely.geometry import shape

    mask = result["mask"] & (result["confidence"] >= min_confidence)
    if not mask.any():
        return gpd.GeoDataFrame(
            {"area_km2": [], "mean_confidence": []},
            geometry=[], crs="EPSG:4326",
        )
    labels, n_blobs = ndimage.label(mask, structure=np.ones((3, 3), dtype=int))
    # SINGLE-PASS vectorization: one features.shapes call over the whole
    # mask. The previous per-blob loop re-scanned the full grid for every
    # blob and was unbounded at corridor scale (verified stuck at 5.5 h on
    # the approved bbox). Per-blob confidence comes from one ndimage.mean.
    polygons, mean_confs = [], []
    for g, v in features.shapes(
        mask.astype("uint8"), mask=mask, transform=result["transform"],
        connectivity=8,  # must match ndimage.label's 8-connectivity or the
        # per-blob confidence assignment below never runs (verified: all
        # confidences came out 0.0 with the default 4-connectivity)
    ):
        if v != 1:
            continue
        polygons.append(shape(g))
        mean_confs.append(0.0)
    if n_blobs and len(polygons) == n_blobs:
        conf = np.asarray(result["confidence"], dtype="float64")
        blob_means = ndimage.mean(conf, labels=labels,
                                  index=range(1, n_blobs + 1))
        mean_confs = [float(m) for m in blob_means]
    gdf = gpd.GeoDataFrame(
        {"mean_confidence": mean_confs}, geometry=polygons, crs=result["crs"],
    )
    gdf["area_km2"] = gdf.geometry.area / 1e6
    return gdf.to_crs("EPSG:4326")
