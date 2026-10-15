"""Data-rule enforcement tests (hackathon rules, AGENT.md section 2.3).

Rules enforced here:
1.  The OSM snapshot date must be strictly earlier than the flood date;
    the pipeline stops with a clear error otherwise.
2.  The src/ package must never import from evaluation/ or read the
    reference data folder (EMSR927 etc.). Reference data is for checking
    only, inside evaluation/.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from floodsight.config import ConfigError, validate_snapshot_before_event
from floodsight.pipeline import run_pipeline

SRC_DIR = Path(__file__).resolve().parents[1] / "src" / "floodsight"
REPO_ROOT = Path(__file__).resolve().parents[1]


# --- Rule 1: OSM snapshot date strictly before the flood date -------------


def test_snapshot_before_flood_is_accepted() -> None:
    assert str(validate_snapshot_before_event("2026-07-27", "2026-08-26")) == "2026-07-27"


@pytest.mark.parametrize("snapshot,flood", [
    ("2026-08-26", "2026-08-26"),  # equal -> violation
    ("2026-08-27", "2026-08-26"),  # after  -> violation
    ("2026-09-01", "2026-08-26"),
])
def test_snapshot_not_before_flood_is_rejected(snapshot: str, flood: str) -> None:
    with pytest.raises(ConfigError, match="DATA RULE VIOLATION"):
        validate_snapshot_before_event(snapshot, flood)


def test_pipeline_enforces_snapshot_rule_at_startup(tmp_path, monkeypatch) -> None:
    """A region snapshot on/after the flood date must stop the run early."""
    import copy

    from floodsight.config import load_config

    monkeypatch.setenv("FLOODSIGHT_RUNS_DIR", str(tmp_path))
    monkeypatch.setenv("FLOODSIGHT_DATA_DIR", str(tmp_path))
    cfg = load_config()
    bad = copy.deepcopy(cfg)
    bad["osm"]["geofabrik"]["regions"]["nepal"]["snapshot_date"] = "2026-08-26"
    with pytest.raises(ConfigError, match="DATA RULE VIOLATION"):
        run_pipeline([85.0, 27.8, 85.6, 28.25], "2026-08-26", mode="fast", cfg=bad)
    # and nothing may have been created for the rejected run
    runs = tmp_path / "runs"
    assert not runs.exists() or list(runs.iterdir()) == []


def test_pipeline_enforces_snapshot_cap_rule(tmp_path, monkeypatch) -> None:
    """A region snapshot newer than the 2026-07-27 cap is rejected even when
    it still precedes the flood (e.g. the 1 Aug 2026 Geofabrik file)."""
    import copy

    from floodsight.config import load_config

    monkeypatch.setenv("FLOODSIGHT_RUNS_DIR", str(tmp_path))
    monkeypatch.setenv("FLOODSIGHT_DATA_DIR", str(tmp_path))
    cfg = load_config()
    bad = copy.deepcopy(cfg)
    bad["osm"]["geofabrik"]["regions"]["nepal"]["snapshot_date"] = "2026-08-01"
    with pytest.raises(ConfigError, match="cap"):
        run_pipeline([85.0, 27.8, 85.6, 28.25], "2026-08-26", mode="fast", cfg=bad)


# --- Rule 2: src/ is isolated from evaluation/ and reference data ----------

IMPORT_RE = re.compile(r"^\s*(from|import)\s+evaluation\b", re.MULTILINE)
REFERENCE_PATH_RE = re.compile(r"evaluation[/.]reference|reference/emsr927", re.IGNORECASE)


def _python_sources() -> list[Path]:
    return sorted(SRC_DIR.rglob("*.py"))


def test_src_has_python_sources() -> None:
    assert len(_python_sources()) > 5, "expected the package to exist"


def test_src_never_imports_evaluation() -> None:
    offenders = [
        p for p in _python_sources()
        if IMPORT_RE.search(p.read_text(encoding="utf-8"))
    ]
    assert not offenders, f"src/ must never import evaluation/: {offenders}"


def test_src_never_reads_reference_data_paths() -> None:
    offenders = [
        p for p in _python_sources()
        if REFERENCE_PATH_RE.search(p.read_text(encoding="utf-8"))
    ]
    assert not offenders, (
        "src/ must never reference evaluation/reference (EMSR927 is for "
        f"checking only): {offenders}"
    )


def test_reference_folder_is_gitignored() -> None:
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "evaluation/reference/" in gitignore
    for entry in ("data/", "runs/", ".env", "*.pt"):
        assert entry in gitignore, f".gitignore must contain {entry!r}"


def test_evaluation_package_exists_separately() -> None:
    evaluation_init = REPO_ROOT / "evaluation" / "__init__.py"
    assert evaluation_init.is_file(), "evaluation/ package must exist separately"
