"""Provenance record round-trip tests."""

from __future__ import annotations

from floodsight.provenance import ProvenanceRecord, SceneRecord


def test_provenance_round_trip(tmp_path) -> None:
    record = ProvenanceRecord(
        run_id="test_run",
        bbox=[85.0, 27.8, 85.6, 28.25],
        flood_date="2026-08-26",
        mode="fast",
        software_version="0.1.0",
        created_at="2026-10-06T00:00:00+00:00",
        osm_snapshot_date="2026-07-27",
        sentinel1_pre=SceneRecord(
            scene_id="id-pre",
            name="S1D_PRE.SAFE",
            sensing_time="2026-08-19T00:10:36Z",
            relative_orbit=121,
            orbit_direction="DESCENDING",
        ),
        sentinel2=[
            SceneRecord(scene_id="id-s2", name="S2B.SAFE", sensing_time="2026-08-20T05:00:00Z")
        ],
    )
    record.write(tmp_path)
    loaded = ProvenanceRecord.read(tmp_path)
    assert loaded.to_dict() == record.to_dict()
    assert loaded.sentinel1_pre is not None
    assert loaded.sentinel1_pre.relative_orbit == 121
    assert loaded.sentinel2[0].name == "S2B.SAFE"


def test_provenance_missing_file_raises(tmp_path) -> None:
    import pytest

    with pytest.raises(FileNotFoundError):
        ProvenanceRecord.read(tmp_path)
