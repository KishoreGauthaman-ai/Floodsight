"""Live integration tests: real CDSE search, DEM download, OSM extraction,
SNAP preprocessing, baseline detection, damage, cut-off and the results store.

Opt-in (network + disk + SNAP + credentials), never run by default::

    pytest -m network tests/test_integration_live.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from floodsight.config import load_config
from floodsight.pipeline import run_pipeline
from floodsight.reporting.results_store import ResultsStore

pytestmark = pytest.mark.network

BBOX = [85.0, 27.8, 85.6, 28.25]
FLOOD = "2026-08-26"


def test_full_acquisition_run_trishuli(tmp_path, monkeypatch) -> None:
    """Real end-to-end: search -> pair -> S2 -> DEM -> OSM -> SNAP preprocess
    -> baseline detection -> damage -> cut-off -> results.json.

    Full mode stops honestly at trace_flood_path (Phase 10) with results
    complete. Needs credentials in .env and ESA SNAP (DECISIONS D28).
    Slow (minutes) on first run; faster with warm caches.
    """
    monkeypatch.setenv("FLOODSIGHT_RUNS_DIR", str(tmp_path / "runs"))
    cfg = load_config()
    with pytest.raises(NotImplementedError, match="trace_flood_path"):
        run_pipeline(BBOX, FLOOD, mode="full", cfg=cfg)

    run_dir = next(iter((tmp_path / "runs").iterdir()))
    statuses = json.loads((run_dir / "stage_status.json").read_text())["stages"]
    for stage in ("validate_input", "search_sentinel1", "pair_orbits",
                  "search_sentinel2", "fetch_dem", "fetch_osm_snapshot",
                  "preprocess", "detect_flood", "assess_damage",
                  "analyze_cutoff", "write_results"):
        assert statuses[stage] == "done", f"stage {stage} -> {statuses[stage]}"

    pair = json.loads((run_dir / "pair.json").read_text())
    assert pair["track"]["relative_orbit"] > 0
    assert pair["pre"] and pair["post"]

    provenance = json.loads((run_dir / "provenance.json").read_text())
    assert provenance["osm_snapshot_date"] < FLOOD

    prepped = json.loads((run_dir / "preprocessed.json").read_text())
    assert prepped["stats"]["coregistered_identical_grid"] is True
    assert Path(prepped["pre"]).is_file() and Path(prepped["post"]).is_file()

    # results store: valid, complete, every number from a real stage
    results = ResultsStore(run_dir).read()
    assert results["meta"]["flood_date"] == FLOOD
    assert results["inputs"]["sentinel1_pre"]["name"].startswith("S1D_")
    assert isinstance(results["damage"]["buildings"]["total_in_area"], int)
    assert isinstance(results["cutoff"]["still_connected_count"], int)
    assert isinstance(results["flood"]["area_km2_new_water"], (int, float)) or \
        results["flood"]["area_km2_new_water"] is None
