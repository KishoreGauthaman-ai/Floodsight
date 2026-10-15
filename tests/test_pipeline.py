"""Pipeline orchestrator and CLI tests.

Unit tests avoid the network via ``stop_after`` (the pipeline marks the
remaining stages "skipped (stop_after requested)" — an explicit, visible
state). Real end-to-end acquisition tests live in
tests/test_integration_live.py (opt-in, marked ``network``).
"""

from __future__ import annotations

import pytest

from floodsight import __version__
from floodsight.cli import main
from floodsight.config import ConfigError
from floodsight.pipeline import (
    STAGES,
    cache_key,
    make_run_id,
    read_stage_status,
    run_pipeline,
)

BBOX = [85.0, 27.8, 85.6, 28.25]


@pytest.fixture(autouse=True)
def isolated_runs(tmp_path, monkeypatch):
    """Never write into the real runs/ or data/ directories during tests."""
    monkeypatch.setenv("FLOODSIGHT_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("FLOODSIGHT_DATA_DIR", str(tmp_path / "data"))
    return tmp_path / "runs"


# --- run ids and cache keys -------------------------------------------------


def test_run_id_is_deterministic_and_slugged() -> None:
    a = make_run_id(tuple(BBOX), "2026-08-26", "fast")
    b = make_run_id(tuple(BBOX), "2026-08-26", "fast")
    assert a == b
    assert "2026-08-26" in a and "fast" in a
    assert all(ch.isalnum() or ch in "._-" for ch in a)


def test_cache_key_changes_with_version() -> None:
    k1 = cache_key(tuple(BBOX), "2026-08-26", "fast")
    k2 = cache_key(tuple(BBOX), "2026-08-26", "fast", version="0.0.1")
    k3 = cache_key(tuple(BBOX), "2026-08-27", "fast")
    assert k1 != k2 and k1 != k3


def test_software_version_recorded() -> None:
    assert isinstance(__version__, str) and __version__


# --- pipeline behaviour (stop_after; no network) ------------------------------


def test_stop_after_validate_creates_run_folder(isolated_runs) -> None:
    run_dir = run_pipeline(BBOX, "2026-08-26", mode="fast", stop_after="validate_input")
    assert run_dir.is_dir()
    statuses = read_stage_status(run_dir)
    assert statuses["validate_input"] == "done"
    for stage in STAGES[1:]:
        assert statuses[stage].startswith("skipped")


def test_invalid_mode_rejected(isolated_runs) -> None:
    with pytest.raises(ConfigError, match="mode"):
        run_pipeline(BBOX, "2026-08-26", mode="turbo", stop_after="validate_input")


def test_upstream_point_must_be_inside_bbox(isolated_runs) -> None:
    with pytest.raises(ConfigError, match="outside the bounding box"):
        run_pipeline(BBOX, "2026-08-26", mode="full", upstream=(90.0, 30.0),
                     stop_after="validate_input")


def test_bad_date_rejected(isolated_runs) -> None:
    with pytest.raises(ConfigError):
        run_pipeline(BBOX, "not-a-date", stop_after="validate_input")


def test_unknown_osm_region_rejected(isolated_runs) -> None:
    with pytest.raises(ConfigError, match="not in the geofabrik registry"):
        run_pipeline(BBOX, "2026-08-26", stop_after="validate_input", region="atlantis")


# --- CLI ----------------------------------------------------------------------


def test_cli_help_exits_zero() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0


def test_cli_run_invalid_bbox_reports_config_error(capsys) -> None:
    code = main(["run", "--bbox", "85.6", "28.25", "85.0", "27.8", "--date", "2026-08-26"])
    assert code == 2
    assert "Input error" in capsys.readouterr().err


def test_cli_run_unknown_region_reports_config_error(capsys) -> None:
    code = main(["run", "--region", "atlantis"])
    assert code == 2
    assert "Input error" in capsys.readouterr().err


def test_cli_report_missing_run_fails_loudly(capsys) -> None:
    # the report command is implemented: a bad run id must fail with a
    # clear message, never a fake report
    assert main(["report", "--run", "x"]) == 2
    assert "No results.json" in capsys.readouterr().err


def test_cli_train_evaluate_are_stubs(capsys) -> None:
    for argv in ("train", ["evaluate", "segmentation"]):
        argv = [argv] if isinstance(argv, str) else argv
        assert main(argv) == 3
        assert "not implemented" in capsys.readouterr().err.lower()


def test_cli_dashboard_starts_real_streamlit(monkeypatch) -> None:
    from floodsight import cli

    calls = []

    def fake_call(cmd):
        calls.append(cmd)
        return 0

    monkeypatch.setattr(cli.subprocess, "call", fake_call)
    assert main(["dashboard"]) == 0
    assert calls and "streamlit" in " ".join(calls[0])
