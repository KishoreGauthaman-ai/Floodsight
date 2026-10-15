"""V7 (Round 5): measure parallel downloads and parallel SNAP for the
judge-mode timing table.

The v1 judge-box run (23.4 min, D67) did NOT include the 5-scene
temporal stack - it was plain fast mode. Full event attribution on a
fresh area needs 6 scenes (5 pre-event + 1 post) per covering slice.
This script measures the two dominant costs with REAL track-85 scenes,
in parallel:

1. CONCURRENT DOWNLOAD: two 1.73 GB GRD products at the same time
   (2026-07-11 slices 5+6 - the same scenes an attribution run needs).
2. CONCURRENT SNAP: both terrain-correction graphs at the same time
   (gpt -q 4 each, 8 vCPU).

Combined with the measured detection (17 s) and stack-statistics costs,
this yields the quick-mode vs full-attribution-mode budget.

Run:  .venv/bin/python scripts/stack/parallel_measure.py
Out:  evaluation/results/v7_parallel_timing.json
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from floodsight.acquisition.cdse_client import (
    download_product,
    get_auth_token,
    search_products,
)
from floodsight.acquisition.orbit_pairing import parse_scene
from floodsight.config import load_config
from floodsight.preprocessing.s1 import (
    build_graph_xml,
)

BBOX = (84.39, 27.68, 85.73, 28.31)
TARGET_DATE = date(2026, 7, 11)
OUT = REPO / "evaluation/results/v7_parallel_timing.json"


def main() -> None:
    cfg = load_config()
    raw = REPO / "data/raw/sentinel1"
    results: dict = {"scenes": [], "phases": {}}

    # ---- find the two products -----------------------------------------
    products = search_products("SENTINEL-1", BBOX,
                               date(2026, 7, 10), date(2026, 7, 12), cfg)
    scenes = [s for s in (parse_scene(p) for p in products) if s]
    wanted = {s.slice_number: (s, p) for s, p in zip(scenes, products)
              if s and s.relative_orbit == 85
              and s.orbit_direction == "ASCENDING"
              and s.sensing_time.date() == TARGET_DATE}
    by_id = {str(p["Id"]): p for p in products}
    token = get_auth_token(cfg)

    # ---- phase 1: two concurrent downloads ------------------------------
    t0 = time.monotonic()
    def dl(item):
        _sl, (scene, _p) = item
        return download_product(by_id[scene.product_id], raw, cfg, token)
    with ThreadPoolExecutor(max_workers=2) as ex:
        zips = list(ex.map(dl, sorted(wanted.items())))
    dl_s = time.monotonic() - t0
    results["phases"]["download_2_concurrent_s"] = round(dl_s, 1)
    results["phases"]["download_bytes"] = sum(z.stat().st_size for z in zips)
    for z, (sl, (scene, _)) in zip(zips, sorted(wanted.items())):
        results["scenes"].append({"slice": sl, "name": scene.name,
                                  "zip_mb": round(z.stat().st_size / 1e6, 1)})
    print(f"parallel download (2 x 1.73 GB): {dl_s:.1f}s "
          f"({sum(z.stat().st_size for z in zips)/1e6/dl_s:.2f} MB/s total)")

    # ---- phase 2: two concurrent SNAP runs -------------------------------
    work = REPO / "data/stack/measure_snap"
    work.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    procs = []
    for z, (sl, (scene, _)) in zip(zips, sorted(wanted.items())):
        graph = build_graph_xml(
            z, BBOX, work / f"tc_slice{sl}.tif", 32645,
            float(cfg["preprocessing"]["s1"].get("pixel_spacing_m", 10.0)),
            int(cfg["preprocessing"]["s1"].get("speckle_window", 3)))
        (work / f"snap{sl}").mkdir(exist_ok=True)
        gpt_graph = work / f"snap{sl}/graph.xml"
        gpt_graph.write_text(graph)
        procs.append(subprocess.Popen(
            [str(Path(cfg["preprocessing"]["s1"]["snap_gpt_path"])),
             gpt_graph.name, "-q", "4"],
            cwd=work / f"snap{sl}"))
    codes = [p.wait() for p in procs]
    snap_s = time.monotonic() - t0
    results["phases"]["snap_2_concurrent_s"] = round(snap_s, 1)
    results["phases"]["snap_exit_codes"] = codes
    print(f"parallel SNAP (2 scenes, -q 4 each): {snap_s:.1f}s, exits {codes}")

    # ---- cleanup: keep the compacts already built earlier; drop the
    # measurement artefacts so the disk does not carry duplicates --------
    for z in zips:
        z.unlink()
    import shutil
    shutil.rmtree(work, ignore_errors=True)
    print("pruned measurement zips + SNAP outputs")

    OUT.write_text(json.dumps(results, indent=2))
    print(f"-> {OUT}")


if __name__ == "__main__":
    main()
