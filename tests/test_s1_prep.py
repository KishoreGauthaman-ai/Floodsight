"""Unit tests for the S1 preprocessing pure functions (no SNAP run)."""

from __future__ import annotations

from pathlib import Path

from floodsight.preprocessing.s1 import (
    BAND_ORDER,
    bbox_wkt,
    build_graph_xml,
    utm_epsg_for_bbox,
)


def test_utm_epsg_nepal_is_zone_45_north() -> None:
    assert utm_epsg_for_bbox((84.39, 27.68, 85.73, 28.31)) == 32645


def test_utm_epsg_southern_hemisphere() -> None:
    # lon 18..19 E, lat -34..-33 (Cape Town area) -> zone 34 south
    assert utm_epsg_for_bbox((18.0, -34.5, 19.0, -33.5)) == 32734


def test_bbox_wkt_lon_first() -> None:
    assert bbox_wkt((85.0, 27.8, 85.6, 28.25)) == (
        "POLYGON((85.0 27.8, 85.6 27.8, 85.6 28.25, 85.0 28.25, 85.0 27.8))"
    )


def test_graph_contains_verified_operators_and_band_order() -> None:
    xml = build_graph_xml(
        Path("/data/scene.SAFE.zip"), (85.0, 27.8, 85.6, 28.25),
        Path("/tmp/out.tif"), 32645, 10.0,
    )
    for op in ("Apply-Orbit-File", "ThermalNoiseRemoval", "Calibration",
               "Terrain-Correction", "BandSelect", "Subset"):
        assert f"<operator>{op}</operator>" in xml
    assert "Copernicus 30m Global DEM" in xml
    assert "EPSG:32645" in xml
    assert f"<sourceBands>{','.join(BAND_ORDER)}</sourceBands>" in xml
    # band order is deterministic and physically labeled (D29)
    assert BAND_ORDER == ["Sigma0_VV", "Sigma0_VH", "elevation", "localIncidenceAngle"]


def test_graph_escapes_wkt_specials() -> None:
    xml = build_graph_xml(
        Path("/data/x.zip"), (-1.5, -2.5, 0.5, -1.5), Path("/tmp/o.tif"), 32730, 10.0
    )
    assert "POLYGON((-1.5 -2.5" in xml
