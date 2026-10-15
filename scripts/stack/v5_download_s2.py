"""V5 (Round 5, time-boxed): S2 L2A downloads for the optical scar check.

Downloads the T45RUL tile for the pre-event stack (2026-08-11 13.4%,
08-12 27.9%, 08-22 56.7% cloud) and the post-event scene (2026-09-28
7.9% - the first near-clear pass, +33 days after the event). Two
concurrent downloads; zips land in data/raw/sentinel2/ and are pruned
after the NDVI stack is extracted to compact per-date VV-analogue
GeoTIFFs (NDVI + valid mask at 10 m) by v5_optical.py.

Run:  .venv/bin/python scripts/stack/v5_download_s2.py
"""

from __future__ import annotations

import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from floodsight.acquisition.cdse_client import (
    download_product,
    get_auth_token,
)
from floodsight.config import load_config

PRODUCTS = [
    {"id": "94d101c3-8eb1-41a5-bfd0-4321174a6d56",
     "name": "S2A_MSIL2A_20260811T045241_N0512_R076_T45RUL_20260811T100012",
     "date": "2026-08-11", "cloud": 13.37},
    {"id": "d5883e5c-6124-4672-8e68-dd7c84307c8c",
     "name": "S2C_MSIL2A_20260812T045701_N0512_R119_T45RUL_20260812T100317",
     "date": "2026-08-12", "cloud": 27.9},
    {"id": "681bf159-5509-4be1-ac51-6c464803d999",
     "name": "S2A_MSIL2A_20260822T045241_N0513_R076_T45RUL_20260822T100012",
     "date": "2026-08-22", "cloud": 56.7},
    {"id": "a4b6eec2-bab3-4ffd-9930-c2fc82b2fc49",
     "name": "S2C_MSIL2A_20260928T044701_N0513_R076_T45RUL_20260928T075011",
     "date": "2026-09-28", "cloud": 7.86},
]

DEST = REPO / "data/raw/sentinel2"


def main() -> None:
    cfg = load_config()
    DEST.mkdir(parents=True, exist_ok=True)
    token = get_auth_token(cfg)

    def dl(p):
        product = {"Id": p["id"], "Name": p["name"],
                   "ContentLength": None}
        # ContentLength unknown here: rely on the zip being either absent
        # or complete-enough; the extract step validates each zip.
        return download_product(product, DEST, cfg, token)

    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=2) as ex:
        zips = list(ex.map(dl, PRODUCTS))
    print(f"downloaded {len(zips)} S2 tiles in {time.monotonic()-t0:.0f}s")
    for z, p in zip(zips, PRODUCTS):
        print(f"  {p['date']}  {p['cloud']:5.1f}% cloud  {z.name[:60]}"
              f"  {z.stat().st_size/1e9:.2f} GB")


if __name__ == "__main__":
    main()
