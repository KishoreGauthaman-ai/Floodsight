"""T1: build the pre-event same-track stack for the Trishuli corridor.

Downloads track-85 ASC GRD scenes for the pre-event dates that are not yet
preprocessed (2026-06-29, 07-11, 07-23), runs the standard SNAP chain per
scene, compacts each terrain-corrected raster to a 2-band (VV, VH) dB
GeoTIFF, and prunes the zip and the large SNAP output immediately to stay
within disk budget. Control dates already in the SNAP cache (08-04, 08-16,
08-28) are compacted from cache without re-downloading.

Run:  nohup .venv/bin/python scripts/stack/prepare_stack.py \
          > runs/_jobs/t1_stack.log 2>&1 &
"""

import json
import logging
import sys
import time
from datetime import date
from pathlib import Path

import numpy as np
import rasterio

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from floodsight.acquisition.cdse_client import (
    download_product,
    get_auth_token,
    search_products,
)
from floodsight.acquisition.orbit_pairing import parse_scene
from floodsight.config import load_config
from floodsight.preprocessing.s1 import preprocess_scene

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("t1_stack")

BBOX = (84.39, 27.68, 85.73, 28.31)  # approved corridor; do not change
STACK_DATES = [date(2026, 6, 29), date(2026, 7, 11), date(2026, 7, 23)]
SLICES = [5, 6]  # the two slices covering the corridor (verified in pair.json)
SNAP_CACHE = (
    REPO / "data/interim/709f78806df43e49a2abed4b095b810feeafdc3344ad5633ae6f85ef4dc39a68"
)
OUT_ROOT = REPO / "data/stack/trishuli"


def to_db_compact(tc_tif: Path, out_tif: Path) -> Path:
    """Convert a SNAP sigma0 TC raster to a compact 2-band dB float32 tif.

    The SNAP graph writes 4 bands in a fixed order (s1.BAND_ORDER):
    Sigma0_VV, Sigma0_VH, elevation, localIncidenceAngle. Only the two
    sigma0 bands are kept, converted to dB.
    """
    with rasterio.open(tc_tif) as src:
        if src.count != 4:
            raise RuntimeError(f"expected 4 SNAP bands, got {src.count} in {tc_tif}")
        data = src.read((1, 2))  # Sigma0_VV, Sigma0_VH (s1.BAND_ORDER)
        profile = src.profile.copy()
    db = 10.0 * np.log10(np.where(data > 0, data, np.nan)).astype("float32")
    profile.update(dtype="float32", count=2, compress="deflate", nodata=float("nan"))
    with rasterio.open(out_tif, "w", **profile) as dst:
        dst.write(db)
    return out_tif


def main() -> None:
    cfg = load_config()
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    raw_dir = REPO / "data/raw/sentinel1"

    # --- 1. compact the dates already in the SNAP cache -----------------
    cache_sources = {
        date(2026, 8, 4): {5: SNAP_CACHE / "track85_ASCE_slice5_CDD6_B057/s1_pre_sigma0_tc.tif",
                           6: SNAP_CACHE / "track85_ASCE_slice6_6F58_C3AB/s1_pre_sigma0_tc.tif"},
        date(2026, 8, 16): {5: SNAP_CACHE / "track85_ASCE_slice5_B057_C523/s1_pre_sigma0_tc.tif",
                            6: SNAP_CACHE / "track85_ASCE_slice6_C3AB_01B4/s1_pre_sigma0_tc.tif"},
        date(2026, 8, 28): {5: SNAP_CACHE / "track85_ASCE_slice5_B057_C523/s1_post_coreg.tif",
                            6: SNAP_CACHE / "track85_ASCE_slice6_C3AB_01B4/s1_post_coreg.tif"},
    }
    for d, per_slice in cache_sources.items():
        for sl, tif in per_slice.items():
            out = OUT_ROOT / f"{d.isoformat()}_slice{sl}_vvvh_db.tif"
            if out.exists():
                log.info("cached compact exists: %s", out.name)
                continue
            log.info("compacting %s", tif)
            to_db_compact(tif, out)

    # --- 2. search + download + SNAP the missing stack dates ------------
    products = search_products("SENTINEL-1", BBOX,
                               date(2026, 6, 20), date(2026, 8, 1), cfg)
    scenes = [s for s in (parse_scene(p) for p in products) if s]
    wanted = {}
    for s in scenes:
        if (s.relative_orbit == 85 and s.orbit_direction == "ASCENDING"
                and s.sensing_time.date() in STACK_DATES and s.slice_number in SLICES):
            wanted[(s.sensing_time.date(), s.slice_number)] = s
    log.info("stack scenes found on CDSE: %d", len(wanted))
    product_by_id = {str(p["Id"]): p for p in products}

    for (d, sl) in sorted(wanted):
        scene = wanted[(d, sl)]
        out_dir = OUT_ROOT / f"{d.isoformat()}_slice{sl}"
        out_dir.mkdir(parents=True, exist_ok=True)
        # FLAT compact name: the event-attribution driver expects
        # {date}_slice{n}_vvvh_db.tif directly under OUT_ROOT
        compact = OUT_ROOT / f"{d.isoformat()}_slice{sl}_vvvh_db.tif"
        if compact.exists():
            log.info("done already: %s", compact)
            continue
        t0 = time.monotonic()
        # fresh token per product: CDSE tokens expire mid-run (verified:
        # HTTP 401 after ~2 h with a reused token)
        token = get_auth_token(cfg)
        zip_path = download_product(product_by_id[scene.product_id], raw_dir,
                                    cfg, token)
        log.info("downloaded %s in %.0fs", zip_path.name, time.monotonic() - t0)
        t0 = time.monotonic()
        tc = preprocess_scene(zip_path, BBOX, out_dir / "s1_sigma0_tc.tif",
                              cfg, out_dir / "snap_work")
        log.info("SNAP done for %s slice%d in %.0fs", d, sl, time.monotonic() - t0)
        to_db_compact(tc, compact)
        tc.unlink()          # stay within disk budget
        zip_path.unlink()
        log.info("pruned %s and %s", tc.name, zip_path.name)

    # --- 3. record the stack inventory -----------------------------------
    inventory = sorted(p.name for p in OUT_ROOT.glob("*_vvvh_db.tif"))
    (OUT_ROOT / "inventory.json").write_text(json.dumps(inventory, indent=2))
    log.info("STACK READY: %d compact rasters", len(inventory))
    for name in inventory:
        log.info("  %s", name)


if __name__ == "__main__":
    main()
