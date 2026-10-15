"""Debris validity analysis (C-checks 1-2), computed from a real run folder.

C1: share of debris area within 500 m / 1 km / 5 km of the corridor river
    centrelines, and debris by slope class and by HAND where DEM data allows.
C2: debris + damage + cut-off counts per sub-area (north corridor vs south
    strip) to explain regional jumps.

Every number printed and written to evaluation/results/debris_validity.json
comes from the run's own rasters/vectors and the dated OSM snapshot. Nothing
is guessed; sections that cannot be computed say so.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import geopandas as gpd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

OUT = REPO_ROOT / "evaluation" / "results" / "debris_validity.json"


def load_rivers(bbox) -> gpd.GeoDataFrame:
    rivers = gpd.read_file(
        REPO_ROOT / "data" / "osm" / "bbox_check" / "rivers.geojson"
    )[["name", "geometry"]]
    m = rivers["name"].fillna("").str.contains(
        "Trish|Trisu|Bhote|Bhoti|Langtang", case=False, na=False)
    corr = rivers[m]
    corr = corr[corr.geometry.bounds.maxx < 85.8]
    return corr[~corr["name"].str.strip().isin(["Bhoti Khola"])]


def main(run_dir: Path) -> dict:
    import rasterio
    from shapely.ops import unary_union

    run_dir = Path(run_dir)
    results = {}
    utm = "EPSG:32645"

    debris = gpd.read_file(run_dir / "debris.geojson").to_crs(utm)
    json.loads((run_dir / "detection.json").read_text())
    results["run"] = run_dir.name
    results["debris_area_km2_results_store"] = (
        json.loads((run_dir / "results.json").read_text())
        ["flood"]["area_km2_probable_debris"]
    )
    results["debris_polygons"] = len(debris)

    # --- C1: distance to river centrelines
    bbox = json.loads((run_dir / "results.json").read_text())["meta"]["bbox"]
    rivers = load_rivers(bbox).to_crs(utm)
    river_union = unary_union(rivers.geometry.values)
    if len(debris):
        d = debris.geometry.distance(river_union)
        debris_km2 = float((debris.geometry.area / 1e6).sum())
        results["debris_total_km2_raster_union"] = round(debris_km2, 4)
        for thresh_m in (500, 1000, 5000):
            share = float(debris.geometry[d <= thresh_m].area.sum() / 1e6
                          / debris_km2) if debris_km2 else None
            results[f"share_within_{thresh_m}m_of_rivers"] = round(share, 4)
    else:
        results["debris_total_km2_raster_union"] = 0.0

    # --- C1: by slope class (from the analysed flood_water.tif DEM band)
    tif = run_dir / "flood_water.tif"
    if tif.is_file():
        pass  # the union raster has no DEM band; slope classes come from the
        # per-slice SNAP rasters below if present
    snap_dirs = sorted((REPO_ROOT / "data" / "interim").glob(
        "*/track85_ASC_slice*"))
    slope_bins = {}
    if snap_dirs:
        with rasterio.open(snap_dirs[0] / "s1_pre_sigma0_tc.tif") as src:
            dem = src.read(3).astype("float64")
            from floodsight.detection.baseline import slope_degrees

            slope = slope_degrees(dem, float(src.transform.a))
            tr = src.transform
            src_crs = src.crs
        # rasterize debris polygons onto the SNAP grid
        from rasterio import features

        debris_src = debris.to_crs(src_crs)
        burned = features.rasterize(
            ((g, 1) for g in debris_src.geometry),
            out_shape=slope.shape, transform=tr, fill=0, dtype="uint8"
        )
        for lo, hi, name in ((0, 10, "0-10"), (10, 20, "10-20"),
                             (20, 30, "20-30"), (30, 90, ">30")):
            sel = (burned > 0) & (slope > lo) & (slope <= hi)
            slope_bins[name] = round(float(sel.sum()) * 1e-4, 4)  # 10 m px
        results["debris_km2_by_slope_class"] = slope_bins

    # --- C1: HAND where computable. The hand() per-cell walk on the
    # ~10.9M-cell corridor grid is too slow on this CPU (measured: the
    # trace alone takes ~90 s; HAND visits every cell). Record honestly.
    results["debris_HAND_note"] = (
        "HAND at corridor scale not computed this session: the per-cell "
        "downstream walk is O(n) per cell on 10.9M cells on CPU; the "
        "slope-class breakdown and the 1 km waterway buffer (built into "
        "the debris layer) serve as the terrain-validity evidence instead."
    )
    # --- C2: per sub-area (north corridor vs south strip at lat 27.95).
    # The split MUST be done in degrees: the earlier version compared UTM
    # metres against a latitude-like number and put everything in "north".
    debris_deg = debris.to_crs("EPSG:4326")
    north = debris[debris_deg.centroid.y >= 27.95]
    south = debris[debris_deg.centroid.y < 27.95]
    results["sub_area_split_lat"] = 27.95
    results["debris_km2_north"] = round(float(north.geometry.area.sum() / 1e6), 4)
    results["debris_km2_south"] = round(float(south.geometry.area.sum() / 1e6), 4)

    dmg_path = run_dir / "damage.gpkg"
    if dmg_path.is_file():
        dmg = gpd.read_file(dmg_path).to_crs(utm)
        dmg_deg = dmg.to_crs("EPSG:4326")
        north_d = dmg[dmg_deg.centroid.y >= 27.95]
        south_d = dmg[dmg_deg.centroid.y < 27.95]
        results["damage_features_north"] = len(north_d)
        results["damage_features_south"] = len(south_d)
        b_n = int((north_d["feature_type"] == "building").sum()) if "feature_type" in north_d else None
        b_s = int((south_d["feature_type"] == "building").sum()) if "feature_type" in south_d else None
        results["damaged_buildings_north"] = b_n
        results["damaged_buildings_south"] = b_s

    cutoff = json.loads((run_dir / "cutoff.json").read_text())
    results["cutoff_counts"] = {
        "likely_cut": len(cutoff["likely_cut"]),
        "possibly_cut": len(cutoff["possibly_cut"]),
        "already_isolated": len(cutoff["already_isolated"]),
        "still_connected": cutoff["still_connected_count"],
        "not_analysed": cutoff.get("not_analysed_count"),
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(results, indent=2))
    print(json.dumps(results, indent=2))
    print(f"-> {OUT}")
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True)
    args = parser.parse_args()
    main(REPO_ROOT / "runs" / args.run)
