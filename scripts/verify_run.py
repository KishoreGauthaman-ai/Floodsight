"""Check that every output in a run folder sits inside the approved bbox
and that lon/lat were not swapped. Usage: python scripts/verify_run.py runs/<run_id>"""
import json
import sys
from pathlib import Path

import geopandas as gpd
import rasterio
from rasterio.warp import transform_bounds

BBOX = (84.39, 27.68, 85.73, 28.31)  # lon_min, lat_min, lon_max, lat_max
TOL = 0.05                           # degrees of slack at the edges
run = Path(sys.argv[1])

def inside(b):  # b = (minx, miny, maxx, maxy) in lon/lat
    return (b[0] >= BBOX[0]-TOL and b[1] >= BBOX[1]-TOL and
            b[2] <= BBOX[2]+TOL and b[3] <= BBOX[3]+TOL)

def swapped(b):  # does the box fit only if lat/lon are exchanged?
    return inside((b[1], b[0], b[3], b[2])) and not inside(b)

# Vector files
for p in sorted(list(run.glob("*.geojson")) + list(run.glob("*.gpkg"))):
    try:
        import pyogrio
        layers = [l[0] for l in pyogrio.list_layers(p)]
    except (ImportError, RuntimeError, ValueError):
        layers = [None]
    for layer in layers:
        gdf = gpd.read_file(p, layer=layer) if layer else gpd.read_file(p)
        if gdf.empty:
            print(f"EMPTY   {p.name}:{layer}"); continue
        crs = gdf.crs
        b = tuple(gdf.to_crs(4326).total_bounds)
        flag = "OK     " if inside(b) else ("SWAPPED" if swapped(b) else "OUTSIDE")
        print(f"{flag} {p.name}:{layer} crs={crs} n={len(gdf)} "
              f"invalid={int((~gdf.is_valid).sum())} bounds={tuple(round(x,3) for x in b)}")

# Rasters
for p in sorted(run.glob("*.tif")):
    with rasterio.open(p) as src:
        b = transform_bounds(src.crs, "EPSG:4326", *src.bounds)
        flag = "OK     " if inside(b) else "OUTSIDE"
        print(f"{flag} {p.name} crs={src.crs} bounds={tuple(round(x,3) for x in b)}")

# Any lat/lon pairs inside JSON files
def walk(o, hits):
    if isinstance(o, dict):
        lk = next((k for k in o if k.lower() in ("lat", "latitude")), None)
        ok = next((k for k in o if k.lower() in ("lon", "lng", "longitude")), None)
        if lk and ok and isinstance(o[lk], (int, float)) and isinstance(o[ok], (int, float)):
            hits.append((o[ok], o[lk]))
        for v in o.values(): walk(v, hits)
    elif isinstance(o, list):
        for v in o: walk(v, hits)

for p in sorted(run.glob("*.json")):
    hits = []
    walk(json.loads(p.read_text()), hits)
    if hits:
        bad = [h for h in hits if not (BBOX[0]-TOL <= h[0] <= BBOX[2]+TOL and BBOX[1]-TOL <= h[1] <= BBOX[3]+TOL)]
        print(f"{'OK     ' if not bad else 'OUTSIDE'} {p.name}: {len(hits)} lat/lon pairs, {len(bad)} outside bbox")
