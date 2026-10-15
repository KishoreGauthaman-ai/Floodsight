"""V5 (Round 5, time-boxed): optical landslide-scar detection from
Sentinel-2 L2A NDVI change.

Method (v2 optical, labelled; thresholds from NON-EVENT controls only,
never from EMSR927 - the reference is used ONLY as a labelled post-hoc
check at the end):
1. Per scene (T45RUL tile): NDVI = (B08-B04)/(B08+B04) at 10 m and a
   validity mask from the SCL scene-classification band (valid =
   vegetation/bare/water classes 4,5,6; clouds, shadows, snow excluded).
2. Pre-event stack: 2026-08-11 (13.4% cloud), 08-12 (27.9%), 08-22
   (56.7%). Post-event: 2026-09-28 (7.9% - the first near-clear pass,
   +33 days after the event).
3. Per-pixel median/MAD of pre-event NDVI; leave-one-out CONTROL z
   scores (each pre scene vs the other two) give the non-event null;
   the scar threshold is the control's p0.1 (NDVI LOSS tail).
4. Scars = post NDVI z <= threshold on valid pixels, STEEP TERRAIN
   INCLUDED (optical has no slope mask - that is the point of V5).
5. Report the observability honestly: the valid pixel fraction over
   the reference AOIs per scene and for the full stack intersection.
6. POST-HOC EMSR927 check (labelled): P/R/F1 at 0/30/100 m vs the
   reference observedEventA, same machinery as D70/D74.

Run:  .venv/bin/python evaluation/v5_optical.py
Out:  evaluation/results/v5_optical.json
      data/stack/trishuli/ndvi_*.tif (compact per-date rasters)
"""

from __future__ import annotations

import io
import json
import sys
import zipfile
from pathlib import Path

import numpy as np
import rasterio
from rasterio.warp import Resampling
from rasterio.warp import reproject as warp_reproject

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "evaluation"))

import emsr927 as emsr
import geopandas as gpd
import shapely
from shapely.geometry import box as shapely_box

RAW = REPO / "data/raw/sentinel2"
STACK = REPO / "data/stack/trishuli"
OUT = REPO / "evaluation/results/v5_optical.json"

SCENES = [
    {"date": "2026-08-11", "zip": "S2A_MSIL2A_20260811T045241_N0512_R076_T45RUL_20260811T100012.zip", "role": "pre"},
    {"date": "2026-08-12", "zip": "S2C_MSIL2A_20260812T045701_N0512_R119_T45RUL_20260812T100317.zip", "role": "pre"},
    {"date": "2026-08-22", "zip": "S2A_MSIL2A_20260822T045241_N0513_R076_T45RUL_20260822T100012.zip", "role": "pre"},
    {"date": "2026-09-28", "zip": "S2C_MSIL2A_20260928T044701_N0513_R076_T45RUL_20260928T075011.zip", "role": "post"},
]
AOIS = {  # reference AOI bounds from evaluation/results/emsr927_metadata.json
    "AOI01": (85.323, 28.149, 85.348, 28.188),
    "AOI02": (85.357, 28.242, 85.381, 28.280),
    "AOI03": (85.099, 27.857, 85.193, 28.009),
    "AOI05": (84.941, 27.793, 85.109, 27.867),
}
BUFFER_DEG = 0.03
VALID_SCL = (4, 5, 6)


def read_scl(zf: zipfile.ZipFile, scln: str, window_bounds, transform10, shape10):
    with zf.open(scln) as fhs:
        src = rasterio.open(io.BytesIO(fhs.read()))
        dst = np.zeros(shape10, dtype="uint8")
        win = rasterio.windows.from_bounds(
            *window_bounds, transform=src.transform,
            width=src.width, height=src.height).round_offsets().round_lengths()
        data = src.read(1, window=win)
        wtr = src.window_transform(win)
        warp_reproject(
            source=data, destination=dst,
            src_transform=wtr, src_crs=src.crs,
            dst_transform=transform10, dst_crs=src.crs,
            resampling=Resampling.nearest,
        )
        return dst


def main() -> None:
    # analysis window: union of AOI bounds + buffer, in the tile CRS (UTM 45N)
    import pyproj

    tr4326 = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:32645",
                                         always_xy=True)
    minx = miny = 1e9
    maxx = maxy = -1e9
    for b in AOIS.values():
        x0, y0 = tr4326.transform(b[0] - BUFFER_DEG, b[1] - BUFFER_DEG)
        x1, y1 = tr4326.transform(b[2] + BUFFER_DEG, b[3] + BUFFER_DEG)
        minx, miny = min(minx, x0), min(miny, y0)
        maxx, maxy = max(maxx, x1), max(maxy, y1)
    window_bounds = (minx, miny, maxx, maxy)

    ndvi_paths, valid_share = [], {}
    for scene in SCENES:
        out = STACK / f"ndvi_{scene['date']}.tif"
        if not out.is_file():
            with zipfile.ZipFile(RAW / scene["zip"]) as zf:
                b04n = next(n for n in zf.namelist() if n.endswith("_B04_10m.jp2"))
                b08n = next(n for n in zf.namelist() if n.endswith("_B08_10m.jp2"))
                scln = next(n for n in zf.namelist() if n.endswith("_SCL_20m.jp2"))
                with zf.open(b04n) as fh:
                    b04_src = rasterio.open(io.BytesIO(fh.read()))
                    with zf.open(b08n) as fh8:
                        b08_src = rasterio.open(io.BytesIO(fh8.read()))
                        win = rasterio.windows.from_bounds(
                            *window_bounds, transform=b04_src.transform,
                            width=b04_src.width,
                            height=b04_src.height).round_offsets().round_lengths()
                        b04 = b04_src.read(1, window=win).astype("float32")
                        b08 = b08_src.read(1, window=win).astype("float32")
                        transform = b04_src.window_transform(win)
                scl = read_scl(zf, scln, window_bounds, transform, b04.shape)
            with np.errstate(invalid="ignore"):
                ndvi = (b08 - b04) / (b08 + b04)
            valid = np.isin(scl, VALID_SCL) & np.isfinite(ndvi)
            ndvi = np.where(valid, ndvi, np.nan).astype("float32")
            profile = {"driver": "GTiff", "height": b04.shape[0],
                       "width": b04.shape[1], "count": 2,
                       "dtype": "float32", "crs": "EPSG:32645",
                       "transform": transform, "compress": "deflate",
                       "nodata": float("nan")}
            with rasterio.open(out, "w", **profile) as dst:
                dst.write(ndvi, 1)
                dst.write(valid.astype("float32"), 2)
        with rasterio.open(out) as src:
            vl = src.read(2) > 0
            transform = src.transform
        valid_share[scene["date"]] = round(float(vl.mean()), 3)
        ndvi_paths.append(out)
        print(f"{scene['date']} ({scene['role']}): valid {vl.mean():.1%}")

    pre = [p for p, s in zip(ndvi_paths, SCENES) if s["role"] == "pre"]
    post = next(p for p, s in zip(ndvi_paths, SCENES) if s["role"] == "post")
    stack = []
    for p in pre:
        with rasterio.open(p) as src:
            stack.append(src.read(1))
            valid = src.read(2) > 0
    with rasterio.open(post) as src:
        post_nd = src.read(1)
        post_valid = src.read(2) > 0
    arr = np.stack(stack)
    with np.errstate(invalid="ignore"):
        med = np.nanmedian(arr, axis=0)
        mad = np.nanmedian(np.abs(arr - med[None]), axis=0)
    sigma = np.maximum(mad * 1.4826, 0.02)  # NDVI floor 0.02 (sensor noise)
    stack_valid = np.isfinite(med) & (np.sum(np.isfinite(arr), axis=0) >= 2)

    # control z: each pre scene vs the other two (non-event null)
    with np.errstate(invalid="ignore"):
        zs = [(arr[i] - med) / sigma for i in range(len(arr))]
    ctrl_ok = np.stack([stack_valid] * len(arr))
    for i in range(len(arr)):
        others = [j for j in range(len(arr)) if j != i]
        o = arr[others]
        with np.errstate(invalid="ignore"):
            omed = np.nanmedian(o, axis=0)
            omad = np.nanmedian(np.abs(o - omed[None]), axis=0)
        osig = np.maximum(omad * 1.4826, 0.02)
        with np.errstate(invalid="ignore"):
            zs[i] = (arr[i] - omed) / osig
        ctrl_ok[i] = np.isfinite(omed) & np.isfinite(arr[i]) & \
            np.isfinite(zs[i])
    ctrl_vals = np.concatenate([z[ok] for z, ok in zip(zs, ctrl_ok)])
    ctrl_vals = ctrl_vals[np.isfinite(ctrl_vals)]
    thr = float(np.percentile(ctrl_vals, 0.1))  # NDVI-LOSS tail, non-event

    with np.errstate(invalid="ignore"):
        post_z = (post_nd - med) / sigma
    scar = stack_valid & post_valid & (post_z <= thr)
    px_km2 = 1e-4
    aoi_report = {}
    for name, b in AOIS.items():
        x0, y0 = tr4326.transform(b[0], b[1])
        x1, y1 = tr4326.transform(b[2], b[3])
        r0 = int((transform.f - max(y0, y1)) / transform.e)
        r1 = int((transform.f - min(y0, y1)) / transform.e)
        c0 = int((min(x0, x1) - transform.c) / transform.a)
        c1 = int((max(x0, x1) - transform.c) / transform.a)
        r0, r1 = max(0, r0), min(scar.shape[0], max(r1, r0 + 1))
        c0, c1 = max(0, c0), min(scar.shape[1], max(c1, c0 + 1))
        w = scar[r0:r1, c0:c1]
        aoi_report[name] = {
            "scar_km2": round(float(w.sum() * px_km2), 3),
            "observable_fraction": round(float(
                (stack_valid & post_valid)[r0:r1, c0:c1].mean()), 3),
        }

    # POST-HOC EMSR927 check (labelled) - only now is the reference read
    prods = emsr.latest_products()
    crs = gpd.read_file(
        REPO / "runs/84.390_27.680_85.730_28.310_2026-08-26_fast"
        / "analysed_area.geojson").estimate_utm_crs()
    ref_polys, common_parts = [], []
    for aoi, d in sorted(prods.items()):
        ob = emsr.load_layer(d, "observedEventA")
        aoi_g = emsr.load_layer(d, "areaOfInterestA")
        if ob is None or aoi_g is None or not len(ob):
            continue
        aoi_u = aoi_g.to_crs(crs).geometry.union_all()
        # the optical tile only covers part of the corridor: intersect the
        # AOI with the OPTICAL footprint instead of the radar analysed area
        opt_bounds = shapely_box(
            transform.c, transform.f - scar.shape[0] * abs(transform.e),
            transform.c + scar.shape[1] * transform.a, transform.f)
        common = shapely.intersection(aoi_u, opt_bounds)
        if not common.is_empty:
            common_parts.append(common)
        ref_polys.append(ob.to_crs(crs))
    from shapely.ops import unary_union
    common = unary_union(common_parts)
    ref_extent = unary_union(
        [shapely.intersection(g, common)
         for ob in ref_polys for g in ob.geometry
         if not shapely.is_empty(g)])

    def km2(g):
        return shapely.area(g) / 1e6

    # scars as vectors over the reference area
    from rasterio import features
    from scipy import ndimage
    labeled, _ = ndimage.label(scar, structure=np.ones((3, 3), int))
    geoms = [g for g, _ in features.shapes(
        scar.astype("uint8"), mask=labeled > 0,
        transform=transform, connectivity=8)]
    from shapely.geometry import shape as shp_shape
    scars = [shp_shape(g) for g in geoms]
    scars = [s for s in scars if km2(s) > 0.005]
    scars_u = unary_union(scars) if scars else shapely.Point(0, 0).buffer(0)
    prf = [emsr.prf(shapely.intersection(scars_u, common), ref_extent, t, km2)
           for t in (0.0, 30.0, 100.0)]

    result = {
        "method": {
            "stack": [s["date"] for s in SCENES if s["role"] == "pre"],
            "post": "2026-09-28 (+33 days, 7.9% cloud)",
            "index": "NDVI from L2A B08/B04 at 10 m; SCL 4/5/6 valid",
            "threshold_source": "p0.1 of leave-one-out CONTROL z over the "
                                "non-event pre stack (never EMSR927)",
            "threshold_z": round(thr, 3),
            "steep_terrain_included": True,
        },
        "valid_share_per_scene": valid_share,
        "stack_and_post_valid_share": round(
            float((stack_valid & post_valid).mean()), 3),
        "scar_km2_total": round(float(scar.sum() * px_km2), 3),
        "per_aoi": aoi_report,
        "emsr927_post_hoc_check_labelled": {
            "common_area_km2": round(km2(common), 3),
            "reference_km2_in_common": round(km2(ref_extent), 3),
            "per_tolerance": prf,
            "n_scar_polygons": len(scars),
        },
        "time_box_note": "post-event near-event passes were 40-86% cloud; "
                         "the first near-clear pass is +33 days, by which "
                         "time some scars may have vegetated or eroded - "
                         "reported as-is",
    }
    OUT.write_text(json.dumps(result, indent=2))
    print(json.dumps({k: result[k] for k in
                      ("valid_share_per_scene", "scar_km2_total",
                       "per_aoi", "emsr927_post_hoc_check_labelled")},
                     indent=1))
    print(f"-> {OUT}")


if __name__ == "__main__":
    main()
