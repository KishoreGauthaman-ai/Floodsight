"""Pipeline orchestrator.

Runs the analysis stages in order, writes per-stage status to the run folder
(so the dashboard can show real progress), and fails loudly when a stage is
not implemented yet. Stage results are cached and resumable; the cache key
combines the bounding box, dates, mode and software version so a changed
setting never reuses stale results.

Implemented stages: validate_input, search_sentinel1, pair_orbits,
search_sentinel2 (selection), fetch_dem, fetch_osm_snapshot (Phase 2) and
preprocess (Phase 3: S1 pair download + SNAP terrain correction +
co-registration, DECISIONS D28). detect_flood onward raises
StageNotImplementedError — never a fake result.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__
from .config import (
    ConfigError,
    load_config,
    parse_date,
    runs_dir,
    validate_bbox,
    validate_snapshot_before_event,
)

logger = logging.getLogger(__name__)

STAGES: list[str] = [
    "validate_input",
    "search_sentinel1",
    "pair_orbits",
    "search_sentinel2",
    "fetch_dem",
    "fetch_osm_snapshot",
    "preprocess",
    "detect_flood",
    "assess_damage",
    "analyze_cutoff",
    "trace_flood_path",
    "write_results",
]

STAGE_PHASE: dict[str, str] = {
    "validate_input": "Phase 1 (implemented)",
    "search_sentinel1": "Phase 2 (implemented)",
    "pair_orbits": "Phase 2 (implemented)",
    "search_sentinel2": "Phase 2 (implemented)",
    "fetch_dem": "Phase 2 (implemented)",
    "fetch_osm_snapshot": "Phase 2 (implemented)",
    "preprocess": "Phase 3 (implemented: SNAP chain, D28)",
    "detect_flood": "Phase 4 (baseline implemented; model in Phase 7)",
    "assess_damage": "Phase 5 (implemented)",
    "analyze_cutoff": "Phase 5 (implemented)",
    "trace_flood_path": "Phase 10 (implemented with an upstream point)",
    "write_results": "Phase 5 (implemented)",
}

STATUS_FILE = "stage_status.json"


class PipelineError(Exception):
    """A pipeline stage failed; the run stopped with a clear error."""


class StageNotImplementedError(NotImplementedError):
    """A pipeline stage is not implemented yet. Fails loudly, never fakes."""


def make_run_id(bbox: tuple[float, ...], flood_date: str, mode: str) -> str:
    """Deterministic run id: area + date + mode (orbit track added by stages)."""
    area = f"{bbox[0]:.3f}_{bbox[1]:.3f}_{bbox[2]:.3f}_{bbox[3]:.3f}"
    slug = re.sub(r"[^0-9a-zA-Z._-]+", "_", f"{area}_{flood_date}_{mode}")
    return slug.strip("_")


def initial_status() -> dict[str, str]:
    return {stage: "pending" for stage in STAGES}


def write_stage_status(run_dir: Path, statuses: dict[str, str]) -> Path:
    """Write the stage status file the dashboard reads for live progress."""
    path = run_dir / STATUS_FILE
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(
            {"updated_at": datetime.now(timezone.utc).isoformat(), "stages": statuses},
            fh,
            indent=2,
        )
    return path


def read_stage_status(run_dir: Path) -> dict[str, str]:
    """Read the stage status map for a run folder."""
    path = run_dir / STATUS_FILE
    if not path.is_file():
        raise FileNotFoundError(f"No {STATUS_FILE} in {run_dir}")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)["stages"]


def create_run_dir(cfg: dict[str, Any], run_id: str) -> Path:
    run_dir = runs_dir(cfg) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    write_stage_status(run_dir, initial_status())
    return run_dir


def cache_key(
    bbox: tuple[float, ...], flood_date: str, mode: str, version: str = __version__
) -> str:
    """Content key for cached, resumable stage artifacts.

    Includes the software version so a changed setting or code version
    never reuses stale results.
    """
    payload = json.dumps(
        {"bbox": [round(float(v), 6) for v in bbox], "date": flood_date,
         "mode": mode, "version": version},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


class _Run:
    """Mutable state for one pipeline execution (statuses, warnings, timings)."""

    def __init__(self, run_dir: Path, statuses: dict[str, str]):
        self.run_dir = run_dir
        self.statuses = statuses
        self.warnings: list[dict[str, str]] = []
        self.timings: dict[str, float] = {}
        self._t0 = time.monotonic()

    def done(self, stage: str) -> None:
        self.statuses[stage] = "done"
        self.timings[stage] = round(time.monotonic() - self._t0, 3)
        write_stage_status(self.run_dir, self.statuses)
        self._write_timings()

    def _write_timings(self) -> None:
        with open(self.run_dir / "stage_timings.json", "w", encoding="utf-8") as fh:
            json.dump(self.timings, fh, indent=2)

    def skip_rest(self, reason: str, after: str) -> None:
        seen = False
        for stage in STAGES:
            if stage == after:
                seen = True
                continue
            if seen and self.statuses.get(stage) in ("pending", "running", None):
                self.statuses[stage] = f"skipped ({reason})"
        write_stage_status(self.run_dir, self.statuses)

    def warn(self, code: str, message: str, severity: str = "warning") -> None:
        self.warnings.append({"code": code, "message": message, "severity": severity})
        self._write_warnings()

    def _write_warnings(self) -> None:
        with open(self.run_dir / "warnings.json", "w", encoding="utf-8") as fh:
            json.dump(self.warnings, fh, indent=2)


# --- Stage implementations (Phase 2) ---------------------------------------


def _warn_if_large_bbox(bbox, cfg: dict[str, Any]) -> None:
    """Soft warning when a bbox side exceeds the configured threshold.

    The hard limit (validation.hard_max_bbox_side_deg) raises in
    validate_bbox; this records the softer 1-degree guidance as a warning so
    the user sees the runtime/disk trade-off before committing.
    """
    soft = float(cfg["validation"].get("max_bbox_side_deg", 1.0))
    sides = {"lon": bbox[2] - bbox[0], "lat": bbox[3] - bbox[1]}
    big = {k: round(v, 3) for k, v in sides.items() if v > soft}
    if big:
        logger.warning(
            "Large analysis area (sides %s deg exceed the %.1f deg soft "
            "threshold): radar preprocessing and model inference grow with "
            "area; expect a longer run and more disk use.", big, soft,
        )


def _stage_search_sentinel1(
    bbox: tuple[float, float, float, float], flood_date: str, cfg: dict[str, Any], run: _Run
) -> list:
    from .acquisition import cdse_client, orbit_pairing

    s1_cfg = cfg["acquisition"]["s1"]
    flood = parse_date(flood_date)
    start = flood.fromordinal(flood.toordinal() - int(s1_cfg["search_window_days_pre"]))
    end = flood.fromordinal(flood.toordinal() + int(s1_cfg["search_window_days_post"]))
    products = cdse_client.search_products("SENTINEL-1", bbox, start, end, cfg)
    scenes = [s for s in (orbit_pairing.parse_scene(p) for p in products) if s]
    payload = [
        {
            "product_id": s.product_id, "name": s.name,
            "sensing_time": s.sensing_time.isoformat(),
            "relative_orbit": s.relative_orbit,
            "orbit_direction": s.orbit_direction,
            "slice_number": s.slice_number, "platform": s.platform,
            "online": s.online,
            "footprint": (s.footprint.__geo_interface__
                          if s.footprint is not None else None),
        }
        for s in scenes
    ]
    with open(run.run_dir / "scenes_s1.json", "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    if len(scenes) < 2:
        run.warn("sparse_s1_scenes", f"Only {len(scenes)} usable S1 IW GRD scene(s) found in the search window")
    return scenes


def _stage_pair_orbits(
    scenes: list, flood_date: str, cfg: dict[str, Any], run: _Run, bbox=None
) -> dict[str, Any]:
    """Coverage-aware same-track pair selection (DECISIONS D46).

    Ranks every candidate same-track pair by how much of the bbox its
    footprints cover, then by date proximity. Uses ONE pair when it covers
    >= min_pair_coverage of the bbox; otherwise processes every track pair
    with >= min_track_coverage separately and unions their masks (per-track
    attribution; pixels are never compared across tracks).
    """
    from .acquisition import orbit_pairing

    selection = orbit_pairing.select_pairs(scenes, flood_date, tuple(bbox), cfg)
    payload = {
        "mode": selection["mode"],
        "coverage": round(selection["coverage"], 4),
        "pairs": [
            {
                "track": {"relative_orbit": rp.track[0],
                          "orbit_direction": rp.track[1]},
                "days_before": rp.days_before, "days_after": rp.days_after,
                "coverage": round(rp.coverage, 4),
                "pre": [s.name for s in rp.pre],
                "post": [s.name for s in rp.post],
            }
            for rp in selection["pairs"]
        ],
        "all_candidates": [
            {"track": list(rp.track), "days_before": rp.days_before,
             "days_after": rp.days_after, "coverage": round(rp.coverage, 4)}
            for rp in selection["all_candidates"]
        ],
    }
    with open(run.run_dir / "pair.json", "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    return {"selection": selection, "payload": payload}


def _stage_search_sentinel2(
    bbox: tuple[float, float, float, float], flood_date: str, cfg: dict[str, Any], run: _Run
) -> list[dict[str, Any]]:
    from .acquisition import cdse_client

    s2_cfg = cfg["acquisition"]["s2"]
    flood = parse_date(flood_date)
    start = flood.fromordinal(flood.toordinal() - int(s2_cfg["search_window_days_pre"]))
    end = flood.fromordinal(flood.toordinal() + int(s2_cfg["search_window_days_post"]))
    products = cdse_client.search_products("SENTINEL-2", bbox, start, end, cfg)
    max_cloud = float(s2_cfg["max_cloud_cover_pct"])
    selected: list[dict[str, Any]] = []
    for product in products:
        attrs = cdse_client.attribute_map(product)
        if attrs.get("productType") != s2_cfg["product_type"]:
            continue
        try:
            cloud = float(attrs.get("cloudCover", "nan"))
        except (TypeError, ValueError):
            cloud = float("nan")
        if cloud > max_cloud:
            continue
        selected.append(
            {
                "product_id": product["Id"], "name": product["Name"],
                "sensing_time": product["ContentDate"]["Start"],
                "cloud_cover_pct": None if math.isnan(cloud) else cloud,
            }
        )
    selected.sort(key=lambda s: s["sensing_time"])
    with open(run.run_dir / "scenes_s2.json", "w", encoding="utf-8") as fh:
        json.dump(selected, fh, indent=2)
    if not selected:
        run.warn(
            "optical_unavailable",
            "No Sentinel-2 L2A scene with cloud cover under "
            f"{max_cloud:.0f}% in the window; continuing radar-only.",
        )
    return selected


def _stage_fetch_dem(bbox: tuple[float, float, float, float], cfg: dict[str, Any], run: _Run) -> list[Path]:
    from .acquisition import dem

    try:
        paths = dem.fetch_dem_tiles(bbox, cfg)
    except dem.DEMError as exc:
        run.warn("dem_download_failed", str(exc), severity="error")
        raise
    return paths


def _stage_fetch_osm(
    bbox: tuple[float, float, float, float], flood_date: str, cfg: dict[str, Any], run: _Run,
    region: str | None = None,
) -> tuple[dict[str, Path], dict[str, Any]]:
    from .acquisition import osm_snapshot

    layers, provenance = osm_snapshot.fetch_osm_features(bbox, flood_date, cfg, region)
    run_dir_layers = run.run_dir / "osm"
    run_dir_layers.mkdir(exist_ok=True)
    import shutil

    for path in layers.values():
        shutil.copy2(path, run_dir_layers / path.name)
    return layers, provenance


def _stage_preprocess(
    selection: dict[str, Any], cfg: dict[str, Any], run: _Run, bbox
) -> list[dict[str, Any]]:
    """Download every scene of every selected pair and run SNAP per slice.

    Multi-slice passes are fully supported now (closes K14): each track pass
    may cover the bbox with several slices; slices are matched across the
    pair by slice number (the same slice of the same track is the same ground
    footprint 12 days apart), each matched slice pair is terrain-corrected
    and co-registered independently, and detection later unions the slices.
    The same-track rule holds inside each pair; nothing is ever compared
    across tracks.
    """
    from .acquisition import cdse_client
    from .config import data_dir
    from .preprocessing import s1

    token = cdse_client.get_auth_token(cfg)
    raw_dir = _raw_sentinel_dir(cfg)
    interim = data_dir(cfg) / "interim" / cache_key(tuple(bbox), "s1-prep", __version__)

    slice_results: list[dict[str, Any]] = []
    for rp in selection["pairs"]:
        # Pair-cache check BEFORE any download: the pair folder name needs
        # only scene names, so a run whose terrain-corrected rasters and
        # co-registration already exist skips the (multi-GB) downloads
        # entirely. Verified failure mode: re-opening the completed case
        # study re-downloaded 6.9 GB before hitting the SNAP cache
        # (Round 4 D72).
        pre_scenes = {s.slice_number: s for s in rp.pre}
        post_scenes = {s.slice_number: s for s in rp.post}
        common_slices = sorted(set(pre_scenes) & set(post_scenes))
        pair_dirs: dict[int, Path] = {}
        for sl in common_slices:
            pre_id = pre_scenes[sl].name.split("_")[-1].split(".")[0]
            post_id = post_scenes[sl].name.split("_")[-1].split(".")[0]
            pair_dirs[sl] = interim / (
                f"track{rp.track[0]}_{rp.track[1][:4]}_slice{sl}_{pre_id}_{post_id}")
        zips: dict[str, list[Path]] = {"pre": [], "post": []}
        for label, scenes in (("pre", rp.pre), ("post", rp.post)):
            for sc in scenes:
                sl = sc.slice_number
                sub = pair_dirs.get(sl) if sl is not None else None
                snap_cached = (
                    sub is not None
                    and (sub / "s1_pre_sigma0_tc.tif").is_file()
                    and (sub / "s1_post_coreg.tif").is_file()
                )
                if snap_cached:
                    # paths are placeholders: preprocess_s1_pair returns
                    # the cached rasters without opening the zips
                    zips[label].append(raw_dir / f"{sc.name}.zip")
                    continue
                # ContentLength (size_bytes from the search) must be passed
                # through: without it download_product's partial-file guard
                # silently accepts truncated leftovers from killed runs
                # (verified: the zombie judge run's 430 MB stubs passed as
                # complete and SNAP failed with 'no product reader')
                product = {"Id": sc.product_id, "Name": sc.name,
                           "ContentLength": sc.size_bytes}
                zips[label].append(cdse_client.download_product(product, raw_dir, cfg, token))
        pre_by_slice = {s.slice_number: z for s, z in zip(rp.pre, zips["pre"])}
        post_by_slice = {s.slice_number: z for s, z in zip(rp.post, zips["post"])}
        for sl in common_slices:
            # the scene identity (product hex id) MUST be part of the
            # cache folder: control runs on other dates share the same
            # track/slice and would otherwise silently reuse the event
            # run's SNAP outputs (verified collision)
            pre_id = pre_by_slice[sl].name.split("_")[-1].split(".")[0]
            post_id = post_by_slice[sl].name.split("_")[-1].split(".")[0]
            sub = interim / f"track{rp.track[0]}_{rp.track[1][:4]}_slice{sl}_{pre_id}_{post_id}"
            result = s1.preprocess_s1_pair(
                pre_by_slice[sl], post_by_slice[sl], tuple(bbox), cfg, sub
            )
            slice_results.append({
                "track": rp.track, "slice": sl, "rasters": result,
            })
    if not slice_results:
        raise PipelineError(
            "No matched pre/post slices in the selected pairs; cannot "
            "preprocess (pair selection or slice metadata is inconsistent)."
        )
    stats = _raster_stats(slice_results[0]["rasters"])
    payload = {
        "n_slice_pairs": len(slice_results),
        "tracks": [list(sr["track"]) for sr in slice_results],
        "stats": stats,
        "rasters": [str(sr["rasters"]["pre"]) for sr in slice_results],
    }
    with open(run.run_dir / "preprocessed.json", "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    return slice_results


def _raw_sentinel_dir(cfg: dict[str, Any]) -> Path:
    from .config import data_dir

    d = data_dir(cfg) / "raw" / "sentinel1"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _raster_stats(paths: dict[str, Path]) -> dict[str, Any]:
    """Value ranges + grid identity of the pre/post pair (real numbers)."""
    import numpy as np
    import rasterio

    out: dict[str, Any] = {}
    with rasterio.open(paths["pre"]) as pre:
        out["pre_grid"] = {
            "width": pre.width, "height": pre.height,
            "crs": str(pre.crs),
            "bounds": [round(v, 2) for v in pre.bounds],
        }
        band_stats = []
        for b in range(1, pre.count + 1):
            arr = pre.read(b, out_shape=(1, min(512, pre.height), min(512, pre.width)))
            finite = arr[np.isfinite(arr)]
            band_stats.append(
                {"band": b, "p50": round(float(np.percentile(finite, 50)), 5),
                 "max": round(float(finite.max()), 3)}
            )
        out["pre_bands"] = band_stats
    with rasterio.open(paths["post"]) as post:
        same = (post.width == pre.width and post.height == pre.height
                and post.transform == pre.transform and post.crs == pre.crs)
    out["coregistered_identical_grid"] = bool(same)
    return out


def _stage_detect_flood(
    slice_results: list[dict[str, Any]], cfg: dict[str, Any], run: _Run,
    bbox: tuple[float, float, float, float],
) -> dict[str, Any]:
    """Per-slice detection (baseline + debris + model) unioned on one grid.

    Each (track, slice) pair is detected on its own co-registered grid -
    pixels are never compared across tracks or slices - and the masks are
    reprojected (nearest) onto a common UTM grid over the bbox. Each pixel
    is attributed to the first (best-ranked) track that covers it. The
    ANALYSED AREA is the union of valid footprints intersected with the
    bbox; every downstream statistic is clipped to it, and coverage below
    90% raises a partial_coverage warning.
    """
    import geopandas as gpd
    import numpy as np
    import rasterio
    from rasterio import features
    from rasterio.warp import Resampling
    from rasterio.warp import reproject as warp_reproject
    from shapely.geometry import box as shapely_box

    from .detection import baseline
    from .detection import debris as debris_mod
    from .detection import model as model_mod
    from .preprocessing.s1 import utm_epsg_for_bbox

    epsg = utm_epsg_for_bbox(bbox)
    pixel = float(cfg["preprocessing"]["s1"].get("pixel_spacing_m", 10.0))
    # common grid: bbox in UTM, bounds snapped outward to whole pixels
    import pyproj

    tr = pyproj.Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
    x0, y0 = tr.transform(bbox[0], bbox[1])
    x1, y1 = tr.transform(bbox[2], bbox[3])
    left, bottom = min(x0, x1), min(y0, y1)
    right, top = max(x0, x1), max(y0, y1)
    import math

    left = math.floor(left / pixel) * pixel
    bottom = math.floor(bottom / pixel) * pixel
    right = math.ceil(right / pixel) * pixel
    top = math.ceil(top / pixel) * pixel
    width = round((right - left) / pixel)
    height = round((top - bottom) / pixel)
    common_transform = rasterio.transform.from_origin(left, top, pixel, pixel)

    def to_common(mask: np.ndarray, src_transform, src_crs,
                  dtype: str = "uint8", resampling=Resampling.nearest):
        dst = np.zeros((height, width), dtype=dtype)
        warp_reproject(
            source=mask.astype(dtype), destination=dst,
            src_transform=src_transform, src_crs=src_crs,
            dst_transform=common_transform, dst_crs=f"EPSG:{epsg}",
            resampling=resampling,
        )
        return dst

    n_tracks = len({sr["track"] for sr in slice_results})
    order = []  # unique tracks in ranked order
    for sr in slice_results:
        if sr["track"] not in order:
            order.append(sr["track"])

    flood_union = np.zeros((height, width), dtype=bool)
    debris_union = np.zeros((height, width), dtype=bool)
    model_union = np.zeros((height, width), dtype=bool)
    valid_union = np.zeros((height, width), dtype=bool)
    flood_conf = np.zeros((height, width), dtype="float32")
    debris_conf = np.zeros((height, width), dtype="float32")
    attribution = np.zeros((height, width), dtype="uint8")  # 0 = unprocessed
    per_track_stats = []
    model_payload: dict[str, Any] = {}

    for sr in slice_results:
        rasters = sr["rasters"]
        base = baseline.log_ratio_change(rasters["pre"], rasters["post"], cfg)
        debris_result = debris_mod.debris_evidence(
            rasters["pre"], rasters["post"], _read_waterways(run), cfg
        )
        model_result = None
        try:
            ckpt = model_mod.load_checkpoint()
        except model_mod.ModelError as exc:
            if not any(w["code"] == "model_missing" for w in run.warnings):
                run.warn("model_missing", str(exc))
        else:
            model_result = model_mod.predict(
                rasters["pre"], rasters["post"], cfg, ckpt
            )
        track_idx = order.index(sr["track"]) + 1
        src_transform = base["transform"]
        src_crs = base["crs"]
        b_mask = to_common(base["mask"], src_transform, src_crs)
        d_mask = to_common(debris_result["mask"], src_transform, src_crs)
        v_mask = to_common(base["valid"], src_transform, src_crs)
        claim = (attribution == 0) & (v_mask > 0)
        attribution[claim] = track_idx
        flood_conf[claim] = to_common(base["confidence"], src_transform,
                                       src_crs, dtype="float32",
                                       resampling=Resampling.bilinear)[claim]
        debris_conf[claim] = to_common(debris_result["confidence"], src_transform,
                                       src_crs, dtype="float32",
                                       resampling=Resampling.bilinear)[claim]
        flood_union |= (b_mask > 0) & claim
        debris_union |= (d_mask > 0) & claim
        valid_union |= (v_mask > 0) & claim
        if model_result is not None:
            m_mask = to_common(model_result["mask"], src_transform, src_crs)
            model_union |= (m_mask > 0) & claim
        per_track_stats.append({
            "track": {"relative_orbit": sr["track"][0],
                      "orbit_direction": sr["track"][1]},
            "slice": sr["slice"],
            "flood_area_km2": base["stats"]["flood_area_km2"],
            "debris_area_km2": debris_result["stats"]["area_km2"],
            "model_flood_area_km2": (
                model_result["stats"]["flood_area_km2"]
                if model_result else None),
            "baseline_stats": base["stats"],
            "debris_stats": debris_result["stats"],
        })

    pixel_area_km2 = (pixel / 1000.0) ** 2
    flood_stats = {
        "flood_pixels": int(flood_union.sum()),
        "flood_area_km2": round(float(flood_union.sum()) * pixel_area_km2, 4),
        "debris_pixels": int(debris_union.sum()),
        "debris_area_km2_union": round(float(debris_union.sum()) * pixel_area_km2, 4),
        "model_flood_pixels": int(model_union.sum()),
        "model_flood_area_km2": round(float(model_union.sum()) * pixel_area_km2, 4),
        "valid_pixel_share_of_bbox": round(float(valid_union.mean()), 4),
    }

    # --- analysed area: union of valid footprints, clipped to the bbox
    from shapely.geometry import shape as shapely_shape
    from shapely.ops import unary_union

    # coverage from full-res pixel counts (accurate and fast); the polygon
    # for display/report comes from an 8x-downsampled valid mask because
    # shapes() on the full ~109M-cell mask produced a multi-million-vertex
    # polygon whose simplify() stalled the stage for hours (verified with
    # py-spy, DECISIONS D50).
    bbox_utm = shapely_box(left, bottom, right, top)
    coverage = float(valid_union.sum()) * pixel * pixel / bbox_utm.area
    ds = 8
    # floor division: the outline may drop a <8-cell remainder; the
    # coverage NUMBER comes from the full-res count, not this outline
    h_ds, w_ds = height // ds, width // ds
    v8 = valid_union[: h_ds * ds, : w_ds * ds].reshape(h_ds, ds, w_ds, ds)
    small = (v8.mean(axis=(1, 3)) >= 0.5).astype("uint8")
    ds_transform = common_transform * rasterio.transform.Affine.scale(ds, ds)
    polys = [shapely_shape(g) for g, v in features.shapes(
        small, mask=small > 0, transform=ds_transform) if v == 1]

    analysed_utm = unary_union(polys) if polys else shapely_box(left, bottom, right, top)
    analysed_clipped = analysed_utm.intersection(bbox_utm)
    analysed_gdf = gpd.GeoSeries([analysed_clipped], crs=f"EPSG:{epsg}").to_crs("EPSG:4326")
    simplified = analysed_gdf.iloc[0].simplify(0.0005)
    gpd.GeoSeries([simplified], crs="EPSG:4326").to_file(
        run.run_dir / "analysed_area.geojson", driver="GeoJSON"
    )
    if coverage < float(cfg["acquisition"]["s1"].get("min_pair_coverage", 0.9)):
        run.warn(
            "partial_coverage",
            f"Radar analysis covers only {coverage:.1%} of the bbox "
            "(same-track pairs cannot cover the rest; pixels were never "
            "compared across tracks). All statistics are clipped to the "
            "analysed area; settlements outside it are 'not_analysed'.",
            severity="warning",
        )

    # --- write union rasters + vectors
    mask_path = run.run_dir / "flood_water.tif"
    with rasterio.open(
        mask_path, "w", driver="GTiff", height=height, width=width,
        count=3, dtype="float32", crs=f"EPSG:{epsg}",
        transform=common_transform, compress="deflate",
    ) as dst:
        dst.write(flood_union.astype("float32"), 1)
        dst.write((attribution > 0).astype("float32"), 2)
        dst.write(attribution.astype("float32"), 3)
        dst.set_band_description(1, "flood_water")
        dst.set_band_description(2, "analysed_area")
        dst.set_band_description(3, "track_attribution")

    def vectorize(mask: np.ndarray, name: str,
                  confidence: np.ndarray | None = None) -> gpd.GeoDataFrame:
        if confidence is None:
            confidence = np.ones_like(mask, dtype="float32")
        gdf = baseline.vectorize_flood({
            "mask": mask, "confidence": confidence,
            "transform": common_transform, "crs": f"EPSG:{epsg}",
            "shape": (height, width),
        })
        if len(gdf):
            gdf["area_km2"] = gdf.geometry.area / 1e6
            gdf = gdf.to_crs("EPSG:4326")
        gdf.to_file(run.run_dir / name, driver="GeoJSON")
        return gdf

    flood_gdf = vectorize(flood_union, "flood.geojson", flood_conf)
    debris_gdf = vectorize(debris_union, "debris.geojson", debris_conf)

    model_geojson = run.run_dir / "model_flood.geojson"
    model_stats_out = None
    if model_payload is not None and (attribution > 0).any():
        vectorize(model_union, "model_flood.geojson")
        # model/baseline agreement over the analysed area
        agree = float((flood_union == model_union)[attribution > 0].mean())
        model_stats_out = {
            "flood_area_km2": flood_stats["model_flood_area_km2"],
            "checkpoint_val_iou": (model_mod.load_checkpoint().get("val_flood_iou")
                                   if True else None),
            "model_baseline_agreement_on_analysed": round(agree, 4),
        }
        if agree < 0.5:
            run.warn(
                "model_baseline_disagreement",
                f"Model and baseline flood maps agree on only {agree:.1%} "
                "of analysed pixels; treat results with extra caution.",
            )

    payload = {
        "method": "log_ratio_otsu_baseline + debris_evidence + unet "
                  "(per-slice, union across same-track slices)",
        "mode": "single" if n_tracks == 1 else "union",
        "stats": flood_stats,
        "n_polygons": len(flood_gdf),
        "flood_water_tif": str(mask_path),
        "flood_geojson": str(run.run_dir / "flood.geojson"),
        "debris_n_polygons": len(debris_gdf),
        "debris_tif": str(mask_path),
        "debris_geojson": str(run.run_dir / "debris.geojson"),
        "coverage_fraction": round(coverage, 4),
        "analysed_area_geojson": str(run.run_dir / "analysed_area.geojson"),
        "per_track": per_track_stats,
        "model_stats": model_stats_out,
        "model_n_polygons": None if model_stats_out is None else len(
            gpd.read_file(model_geojson)),
        "model_flood_geojson": str(model_geojson),
    }
    if model_stats_out is None:
        payload.pop("model_stats")
        payload.pop("model_n_polygons")
        payload.pop("model_flood_geojson")
    with open(run.run_dir / "detection.json", "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    return payload


def _read_waterways(run: _Run):
    import geopandas as gpd

    waterways_path = run.run_dir / "osm" / "waterways.geojson"
    if waterways_path.is_file():
        return gpd.read_file(waterways_path)
    return None


def _stage_assess_damage(cfg: dict[str, Any], run: _Run) -> tuple[dict[str, Any], Path]:
    """Intersect flood/debris extents with pre-event OSM buildings/roads/bridges."""
    import geopandas as gpd

    from .analysis import damage

    osm_dir = run.run_dir / "osm"
    flood_path = run.run_dir / "flood.geojson"
    debris_path = run.run_dir / "debris.geojson"
    flood_gdf = gpd.read_file(flood_path) if flood_path.is_file() else None
    debris_gdf = gpd.read_file(debris_path) if debris_path.is_file() else None
    analysed_area = _read_analysed_area(run)
    buildings = gpd.read_file(osm_dir / "buildings.geojson")
    roads = gpd.read_file(osm_dir / "roads.geojson")
    bridges = gpd.read_file(osm_dir / "bridges.geojson")
    results, damage_gdf = damage.assess_damage(
        buildings, roads, bridges, flood_gdf, debris_gdf, cfg, analysed_area
    )
    gpkg = run.run_dir / "damage.gpkg"
    if len(damage_gdf):
        damage_gdf.to_file(gpkg, driver="GPKG")
    with open(run.run_dir / "damage.json", "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2)
    return results, gpkg


def _stage_analyze_cutoff(cfg: dict[str, Any], run: _Run) -> dict[str, Any]:
    """Baseline connectivity + two flood scenarios on the pre-event graph."""
    import geopandas as gpd

    from .analysis import cutoff as cutoff_mod

    osm_dir = run.run_dir / "osm"
    flood_path = run.run_dir / "flood.geojson"
    debris_path = run.run_dir / "debris.geojson"
    flood_gdf = gpd.read_file(flood_path) if flood_path.is_file() else None
    debris_gdf = gpd.read_file(debris_path) if debris_path.is_file() else None
    import pandas as pd

    extent = None
    parts = [g for g in (flood_gdf, debris_gdf) if g is not None and len(g)]
    if parts:
        extent = pd.concat(parts, ignore_index=True)
    settlements = gpd.read_file(osm_dir / "settlements.geojson")
    roads = gpd.read_file(osm_dir / "roads.geojson")
    destinations = gpd.read_file(osm_dir / "destinations.geojson")
    analysed_area = _read_analysed_area(run)
    if len(settlements) == 0:
        run.warn("no_settlements", "No settlements found in the OSM snapshot for this area")
        result = {
            "already_isolated": [], "likely_cut": [], "possibly_cut": [],
            "still_connected_count": 0, "settlements": [],
        }
    else:
        try:
            result = cutoff_mod.analyze_cutoff(
                settlements, roads, destinations, extent, cfg, analysed_area
            )
        except cutoff_mod.CutoffError as exc:
            run.warn("cutoff_failed", str(exc), severity="error")
            raise
    with open(run.run_dir / "cutoff.json", "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2)
    return result


def _read_analysed_area(run: _Run):
    """Load the analysed-area polygon for clipping (or None)."""
    import geopandas as gpd

    path = run.run_dir / "analysed_area.geojson"
    if path.is_file():
        gdf = gpd.read_file(path)
        if len(gdf):
            return gdf
    return None


def _stage_write_results(
    run_dir: Path, damage: dict[str, Any], cutoff: dict[str, Any], run: _Run
) -> Path:
    """Assemble results.json from the run's real artifacts; validate on write."""
    from .reporting import results_store

    with open(run_dir / "provenance.json", encoding="utf-8") as fh:
        provenance = json.load(fh)
    with open(run_dir / "detection.json", encoding="utf-8") as fh:
        detection = json.load(fh)
    payload = results_store.build_results(
        run_dir, provenance, detection, damage, cutoff, run.warnings
    )
    store = results_store.ResultsStore(run_dir)
    return store.write(payload)


# --- Orchestrator -----------------------------------------------------------


def run_pipeline(
    bbox: Any,
    flood_date: str,
    mode: str = "fast",
    upstream: tuple[float, float] | None = None,
    cfg: dict[str, Any] | None = None,
    region: str | None = None,
    stop_after: str | None = None,
) -> Path:
    """Run the full analysis for (bbox, flood_date).

    Implemented stages run for real (network for search, DEM and OSM).
    ``stop_after`` runs the pipeline only through that stage (used by unit
    tests to avoid network calls; the remaining stages are marked
    "skipped (stop_after requested)" — an explicit, visible state).

    Raises on failure with a clear error; the run folder always records the
    real per-stage status and warnings.
    """
    cfg = cfg if cfg is not None else load_config()
    if mode not in ("fast", "full"):
        raise ConfigError(f"mode must be 'fast' or 'full', got {mode!r}")

    validated = validate_bbox(bbox, cfg)
    parse_date(flood_date)
    if upstream is not None:
        lon, lat = (float(v) for v in upstream)
        if not (-180 <= lon <= 180 and -90 <= lat <= 90):
            raise ConfigError(f"Invalid upstream point (lon, lat): {upstream!r}")
        if not (validated[0] <= lon <= validated[2] and validated[1] <= lat <= validated[3]):
            raise ConfigError(f"Upstream point {upstream!r} lies outside the bounding box {validated}")

    # OSM data-rule check BEFORE any artifact is created, so a violating run
    # leaves nothing behind (tested in tests/test_data_rules.py).
    from .config import resolve_osm_snapshot

    osm_region_entry = resolve_osm_snapshot(cfg, flood_date, region)
    snapshot_date = parse_date(osm_region_entry["snapshot_date"])
    validate_snapshot_before_event(snapshot_date, flood_date)

    run_id = make_run_id(validated, flood_date, mode)
    run_dir = create_run_dir(cfg, run_id)
    run = _Run(run_dir, initial_status())

    # soft size warning, recorded in the run's warnings.json so the
    # dashboard and report show it (hard limit raises inside validate_bbox)
    soft = float(cfg["validation"].get("max_bbox_side_deg", 1.0))
    for side_name, side in (("lon", validated[2] - validated[0]),
                            ("lat", validated[3] - validated[1])):
        if side > soft:
            run.warn(
                "large_bbox",
                f"{side_name} side {side:.2f} deg exceeds the {soft:.1f} deg "
                "soft threshold: radar preprocessing and model inference "
                "grow with area; expect a longer run and more disk use.",
            )
    run.done("validate_input")

    if stop_after == "validate_input":
        run.skip_rest("stop_after requested", after="validate_input")
        return run_dir

    # --- search + pair (anonymous; metadata only)
    run.statuses["search_sentinel1"] = "running"
    scenes = _stage_search_sentinel1(validated, flood_date, cfg, run)
    run.done("search_sentinel1")

    from .acquisition import orbit_pairing

    run.statuses["pair_orbits"] = "running"
    try:
        pair = _stage_pair_orbits(scenes, flood_date, cfg, run, tuple(validated))
    except orbit_pairing.NoValidPairError as exc:
        run.warn("no_valid_pair", str(exc), severity="error")
        run.statuses["pair_orbits"] = "failed (no valid same-track pair)"
        run.skip_rest("no valid same-track pair", after="pair_orbits")
        raise
    run.done("pair_orbits")

    run.statuses["search_sentinel2"] = "running"
    _stage_search_sentinel2(validated, flood_date, cfg, run)
    run.done("search_sentinel2")

    if stop_after == "search_sentinel2":
        run.skip_rest("stop_after requested", after="search_sentinel2")
        return run_dir

    run.statuses["fetch_dem"] = "running"
    dem_paths = _stage_fetch_dem(validated, cfg, run)
    run.done("fetch_dem")

    run.statuses["fetch_osm_snapshot"] = "running"
    _osm_layers, osm_provenance = _stage_fetch_osm(validated, flood_date, cfg, run,
                                                   region)
    run.done("fetch_osm_snapshot")

    if stop_after == "fetch_osm_snapshot":
        run.skip_rest("stop_after requested", after="fetch_osm_snapshot")
        return run_dir

    # --- provenance for everything acquired so far
    _write_provenance(
        run_dir, run_id, validated, flood_date, mode, cfg, snapshot_date,
        osm_provenance, pair["selection"], dem_paths,
    )

    # --- preprocess: download every selected scene + SNAP per slice
    run.statuses["preprocess"] = "running"
    slice_results = _stage_preprocess(pair["selection"], cfg, run, validated)
    run.done("preprocess")

    if stop_after == "preprocess":
        run.skip_rest("stop_after requested", after="preprocess")
        return run_dir

    # --- detect: baseline + debris + model per slice, unioned on one grid
    run.statuses["detect_flood"] = "running"
    _stage_detect_flood(slice_results, cfg, run, tuple(validated))
    run.done("detect_flood")

    if stop_after == "detect_flood":
        run.skip_rest("stop_after requested", after="detect_flood")
        return run_dir

    # --- damage + cutoff + results store (Phase 5)
    run.statuses["assess_damage"] = "running"
    damage_results, _gpkg = _stage_assess_damage(cfg, run)
    run.done("assess_damage")

    run.statuses["analyze_cutoff"] = "running"
    cutoff_results = _stage_analyze_cutoff(cfg, run)
    run.done("analyze_cutoff")

    run.statuses["write_results"] = "running"
    results_path = _stage_write_results(run_dir, damage_results, cutoff_results, run)
    run.done("write_results")

    # --- trace_flood_path (Phase 10 bonus; runs only with an upstream point)
    if upstream is None:
        run.statuses["trace_flood_path"] = "skipped (no upstream point)"
        write_stage_status(run_dir, run.statuses)
        logger.info("Run complete through write_results: %s", results_path)
        return run_dir

    run.statuses["trace_flood_path"] = "running"
    import geopandas as gpd

    from .analysis import flood_path as fp_mod
    from .reporting import results_store

    settlements = gpd.read_file(run_dir / "osm" / "settlements.geojson")
    analysed_path = run_dir / "analysed_area.geojson"
    analysed_area = gpd.read_file(analysed_path) if analysed_path.is_file() else None
    if analysed_area is not None and len(analysed_area):
        analysed_geom = analysed_area.geometry.union_all()
        inside = settlements.geometry.within(analysed_geom)
        excluded = int((~inside).sum())
        settlements = settlements[inside]
        if excluded:
            run.warn(
                "flood_path_settlements_clipped",
                f"{excluded} corridor settlements lie outside the analysed "
                "radar area and are excluded from the flood-path list "
                "(not analysed).",
            )
    fp = fp_mod.trace_flood_path(upstream, tuple(validated), settlements, cfg)
    with open(run_dir / "flood_path.json", "w", encoding="utf-8") as fh:
        json.dump(fp, fh, indent=2)
    from shapely.geometry import shape

    gpd.GeoSeries([shape(fp["path_geojson"])], crs="EPSG:4326").to_file(
        run_dir / "flood_path.geojson", driver="GeoJSON"
    )
    payload = results_store.ResultsStore(run_dir).read()
    payload["flood_path"] = fp
    results_store.ResultsStore(run_dir).write(payload)
    run.done("trace_flood_path")
    logger.info("Run complete with flood path: %s", results_path)
    return run_dir


def _write_provenance(
    run_dir: Path, run_id: str, bbox, flood_date: str, mode: str, cfg: dict[str, Any],
    snapshot_date, osm_provenance, selection, dem_paths,
) -> None:
    from .acquisition import dem as dem_mod
    from .provenance import ProvenanceRecord, SceneRecord

    def scene_records(scenes) -> list[SceneRecord]:
        return [
            SceneRecord(
                scene_id=s.product_id, name=s.name,
                sensing_time=s.sensing_time.isoformat(),
                relative_orbit=s.relative_orbit,
                orbit_direction=s.orbit_direction, platform=s.platform,
                product_type=s.product_type,
            )
            for s in scenes
        ]

    pairs = selection["pairs"]
    first = pairs[0]
    record = ProvenanceRecord(
        run_id=run_id, bbox=list(bbox), flood_date=flood_date, mode=mode,
        software_version=__version__,
        created_at=datetime.now(timezone.utc).isoformat(),
        osm_snapshot_date=snapshot_date.isoformat(),
        sentinel1_pre=scene_records(first.pre)[0] if first.pre else None,
        sentinel1_post=scene_records(first.post)[0] if first.post else None,
        dem_source=json.dumps(
            dem_mod.dem_provenance(tuple(bbox), cfg), sort_keys=True
        ),
    )
    payload = record.to_dict()
    payload["osm"] = osm_provenance
    payload["s1_pairs"] = [
        {
            "track": {"relative_orbit": rp.track[0],
                      "orbit_direction": rp.track[1]},
            "days_before": rp.days_before, "days_after": rp.days_after,
            "bbox_coverage": round(rp.coverage, 4),
            "pre": [s.name for s in rp.pre],
            "post": [s.name for s in rp.post],
        }
        for rp in pairs
    ]
    with open(run_dir / "provenance.json", "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
