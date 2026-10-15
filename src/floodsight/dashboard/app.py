"""FloodSight dashboard — dark operational theme.

Dark canvas (#0D0D0F), elevated surfaces (#161618), warm white text
(#E5E5E7), muted steel (#8E8E93). The map uses Esri World Imagery
(satellite — the Himalayan terrain reads clearly on it) with CartoDB
dark-only-labels on top, rendered FULL-WIDTH via st_folium's
use_container_width (the 500 px default was the "map not showing" bug).
The legend is a Streamlit-rendered chip row (NOT a folium figure child —
that produced a shrunken side-by-side double layout). All numbers in
JetBrains Mono with tabular figures. Attribution footer on every view.

Launch: ./run.sh  (or)  streamlit run src/floodsight/dashboard/app.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import streamlit as st

from floodsight.config import REPO_ROOT, load_config, runs_dir
from floodsight.reporting.results_store import ResultsStore, ResultsValidationError

st.set_page_config(page_title="FloodSight", layout="wide")

ATTRIBUTION = (
    "Contains modified Copernicus Sentinel data 2026. | Produced using Copernicus "
    "WorldDEM-30 (c) DLR e.V. 2014 and (c) Airbus Defence and Space GmbH "
    "2014-2018 provided under COPERNICUS by the European Union and ESA; all "
    "rights reserved. | (c) OpenStreetMap contributors. | Esri World Imagery "
    "basemap (c) Esri, Maxar, Earthstar Geographics. | Educational prototype "
    "- NOT an operational tool. Outputs are estimates."
)

# ---------------------------------------------------------------------------
# DESIGN TOKENS — dark operational theme (docs/DESIGN.md, dark variant)
# ---------------------------------------------------------------------------
C = {
    "bg":     "#0D0D0F",
    "card":   "#161618",
    "card_h": "#1E1E20",
    "ink":    "#E5E5E7",
    "steel":  "#8E8E93",
    "border": "#2A2A2C",
    "accent": "#FF4757",
}

LAYER_STYLE = {
    "settlement_likely_cut":    {"color": "#ff4757", "label": "Settlement likely cut off"},
    "settlement_possibly_cut": {"color": "#ffa502", "label": "Settlement possibly cut off"},
    "settlement_already_isolated": {"color": "#747d8c", "label": "Already isolated before event"},
    "settlement_connected":    {"color": "#2ed573", "label": "Connected inside analysed area"},
    "settlement_not_analysed": {"color": "#a55eea", "label": "Not analysed (no coverage)"},
    "settlement_on_flood_path": {"color": "#e17055", "label": "On flood-path corridor"},
    "flood_water":             {"color": "#3742fa", "label": "Flood water (baseline)"},
    "flood_water_model":       {"color": "#a55eea", "label": "Flood water (U-Net model)"},
    "event_debris":            {"color": "#ff6348", "label": "Event-attributed debris (temporal stack)"},
    "debris":                  {"color": "#e17055", "label": "Other ground change (monsoon baseline, not event-attributed)"},
    "damaged_building":        {"color": "#fd79a8", "label": "Building hit"},
    "damaged_road":            {"color": "#d4cd40", "label": "Road in extent"},
    "affected_bridge":        {"color": "#00d2d3", "label": "Bridge affected"},
    "flood_path":              {"color": "#2ed573", "label": "Flood-path corridor"},
    "upstream_point":          {"color": "#ff4757", "label": "Upstream point"},
    "bbox_outline":            {"color": "#E5E5E7", "label": "Analysis bbox"},
    "analysed_area_outline":   {"color": "#9aa4ad", "label": "Analysed area"},
}

# Esri World Imagery — satellite terrain that is actually VISIBLE against a
# dark UI (dark CartoDB tiles rendered rural Nepal as near-black emptiness,
# D55/D72 context). Labels come from CartoDB's dark label-only overlay.
SAT_TILES = ("https://server.arcgisonline.com/ArcGIS/rest/services/"
             "World_Imagery/MapServer/tile/{z}/{y}/{x}")
SAT_ATTR = ("Tiles &copy; Esri &mdash; Source: Esri, Maxar, Earthstar "
            "Geographics, and the GIS User Community")
# Labels: Esri World Boundaries and Places reference layer - no API key,
# same provider family as the imagery (replaces the CARTO label overlay,
# which is key-free but third-party; Round 5 final delivery)
LABEL_TILES = ("https://server.arcgisonline.com/ArcGIS/rest/services/"
              "Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}")
LABEL_ATTR = ("Labels &copy; Esri, Source: Esri, Garmin, and the GIS User "
              "Community")

# ---------------------------------------------------------------------------
# CSS — plain string (never an f-string: <style> braces break f-strings)
# ---------------------------------------------------------------------------
_CSS_TEMPLATE = """
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root {
    --bg:#0D0D0F; --card:#161618; --ink:#E5E5E7; --steel:#8E8E93;
    --br:#2A2A2C;
    --sans:'Geist','Helvetica Neue',sans-serif;
    --mono:'JetBrains Mono','SF Mono',monospace;
}
.stApp{background:var(--bg)!important;color:var(--ink)!important;font-family:var(--sans)!important;}
.stApp::before{display:none!important;}
h1{font-family:var(--sans)!important;font-size:2rem!important;font-weight:700!important;
    letter-spacing:-.03em!important;color:var(--ink)!important;margin-bottom:.2rem!important;
    text-wrap:balance!important;}
h2,h3,h4{font-family:var(--sans)!important;color:var(--ink)!important;letter-spacing:-.015em!important;}
p{color:var(--ink)!important;line-height:1.6!important;}
.stCaption,[data-testid="stCaption"]{color:var(--steel)!important;font-size:.875rem!important;
    line-height:1.55!important;max-width:70ch!important;}
[data-testid="stMetric"]{background:var(--card)!important;border:1px solid var(--br)!important;
    border-radius:10px!important;padding:16px 20px!important;}
[data-testid="stMetricLabel"]{font-family:var(--mono)!important;font-size:.65rem!important;
    text-transform:uppercase!important;letter-spacing:.1em!important;color:var(--steel)!important;margin-bottom:4px!important;}
[data-testid="stMetricValue"]{font-family:var(--mono)!important;font-size:1.55rem!important;
    font-weight:600!important;color:var(--ink)!important;letter-spacing:-.02em!important;
    font-variant-numeric:tabular-nums!important;}
[data-testid="stMetricDelta"],[data-testid="stMetricHelp"]{display:none!important;}
.stButton>button{background:var(--ink)!important;color:var(--bg)!important;
    border:none!important;border-radius:6px!important;font-family:var(--sans)!important;
    font-size:.875rem!important;font-weight:600!important;padding:10px 24px!important;
    box-shadow:none!important;transition:opacity .15s,transform .08s!important;width:100%!important;}
.stButton>button:hover{opacity:.85!important;}
.stButton>button:active{transform:scale(.97)!important;opacity:.75!important;}
[data-testid="stSidebar"]{background:var(--card)!important;border-right:1px solid var(--br)!important;}
[data-testid="stNumberInput"]>div>div>input,
[data-testid="stDateInput"]>div>div>input{background:var(--bg)!important;
    border:1px solid var(--br)!important;border-radius:6px!important;color:var(--ink)!important;
    font-family:var(--mono)!important;font-size:.875rem!important;}
[data-testid="stNumberInput"]>div>div>input:focus{border-color:var(--ink)!important;
    box-shadow:0 0 0 1px var(--ink)!important;}
[data-testid="stWidgetLabel"] p{font-family:var(--mono)!important;font-size:.65rem!important;
    text-transform:uppercase!important;letter-spacing:.1em!important;color:var(--steel)!important;}
[data-testid="stSelectbox"]>div>div{background:var(--bg)!important;border:1px solid var(--br)!important;
    border-radius:6px!important;font-family:var(--mono)!important;}
[data-testid="stExpander"]{border:none!important;border-top:1px solid var(--br)!important;
    border-radius:0!important;background:transparent!important;}
[data-testid="stExpander"]>summary{font-family:var(--mono)!important;font-size:.7rem!important;
    text-transform:uppercase!important;letter-spacing:.08em!important;color:var(--steel)!important;}
[data-testid="stAlert"]{background:var(--card)!important;border:1px solid var(--br)!important;
    border-radius:8px!important;color:var(--ink)!important;}
.fs-badge{display:inline-flex;align-items:center;padding:4px 12px;margin:3px;
    border-radius:9999px;font-family:var(--mono);font-size:.65rem;
    font-weight:500;letter-spacing:.06em;text-transform:uppercase;}
.fs-red{background:rgba(255,71,87,.15);color:#FF6B81;}
.fs-amber{background:rgba(255,159,10,.15);color:#FFB142;}
.fs-green{background:rgba(46,204,113,.15);color:#5CD39C;}
.fs-blue{background:rgba(90,160,255,.15);color:#8CB8FF;}
.fs-purple{background:rgba(168,85,247,.15);color:#C084FC;}
/* map frame: the hero element */
.stMain iframe[title="streamlit_folium.st_folium"]{
    border:1px solid var(--br)!important;border-radius:12px!important;
    box-shadow:0 12px 40px rgba(0,0,0,.45)!important;background:#000!important;}
/* legend chip row (rendered in Streamlit, not inside the map) */
.fs-leg{display:flex;flex-wrap:wrap;gap:6px 14px;padding:10px 2px 2px;}
.fs-leg-item{display:inline-flex;align-items:center;gap:7px;
    font-family:var(--mono);font-size:.66rem;letter-spacing:.03em;color:var(--steel);}
.fs-leg-item i{width:11px;height:11px;border-radius:3px;display:inline-block;
    border:1px solid rgba(255,255,255,.25);flex:none;}
.fs-leg-item.is-on i{box-shadow:0 0 6px 0 currentColor;}
/* run progress card */
.fs-run{font-family:var(--mono);font-size:.72rem;line-height:1.9;color:var(--ink);
    background:var(--card);border:1px solid var(--br);border-radius:10px;
    padding:14px 18px;margin:6px 0 14px;}
.fs-run .s{color:var(--steel);} .fs-run .ok{color:#5CD39C;} .fs-run .go{color:#FFB142;}
[data-testid="stJson"]{display:none!important;}
@keyframes fsIn{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:translateY(0)}}
[data-testid="stMetric"]{animation:fsIn .4s cubic-bezier(.16,1,.3,1) both;}
[data-testid="stMetric"]:nth-of-type(1){animation-delay:0ms}
[data-testid="stMetric"]:nth-of-type(2){animation-delay:60ms}
[data-testid="stMetric"]:nth-of-type(3){animation-delay:120ms}
[data-testid="stMetric"]:nth-of-type(4){animation-delay:180ms}
button:focus-visible,a:focus-visible,input:focus-visible,summary:focus-visible{
    outline:2px solid var(--ink)!important;outline-offset:2px!important;}
[data-testid="stMain"]>.block-container{max-width:1400px!important;
    padding:2rem 3rem 4rem!important;}
@media(max-width:768px){[data-testid="stMain"]>.block-container{padding:1rem 1rem 2rem!important;}}
html{scroll-behavior:smooth;}
#MainMenu,header[data-testid="stHeader"]{background:var(--bg)!important;}
.stApp>[data-testid="stToolbar"]{display:none!important;}
</style>
"""
CSS = _CSS_TEMPLATE


def _badge(text: str, kind: str) -> str:
    return f'<span class="fs-badge fs-{kind}">{text}</span>'


@st.cache_resource
def _cfg() -> dict:
    return load_config()


def list_runs() -> list[Path]:
    root = runs_dir(_cfg(), create=False)
    if not root.is_dir():
        return []
    return sorted(
        (p for p in root.iterdir() if (p / "stage_status.json").is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


# ---------------------------------------------------------------------------
# Run launcher — subprocess with stderr to a log FILE (a full stderr pipe
# can deadlock a chatty child), bounded wait, live stage progress from the
# NEW run's stage_status.json, and a crash-safe exit condition.
# ---------------------------------------------------------------------------
MAX_RUN_S = 5400  # 90 minutes hard bound


def sidebar_new_run() -> None:
    st.markdown("#### New analysis")
    with st.form("run_form", border=False):
        region = st.selectbox("preset", ["-- custom --"] + sorted(
            p.stem for p in (REPO_ROOT / "config" / "regions").glob("*.yaml")))
        if region == "-- custom --":
            c1, c2 = st.columns(2)
            lon_min = c1.number_input("lon min", value=85.0, step=0.05)
            lat_min = c2.number_input("lat min", value=27.8, step=0.05)
            lon_max = c1.number_input("lon max", value=85.6, step=0.05)
            lat_max = c2.number_input("lat max", value=28.25, step=0.05)
            date = st.date_input("flood date").isoformat()
            bbox_args = ["--bbox", str(lon_min), str(lat_min), str(lon_max), str(lat_max)]
        else:
            date = None
            bbox_args = ["--region", region]
        mode = st.selectbox("mode", ["fast", "full"])
        go = st.form_submit_button("Run analysis", use_container_width=True)
    if go:
        cli = [sys.executable, "-m", "floodsight.cli", "run", *bbox_args]
        if date:
            cli += ["--date", date]
        cli += ["--mode", mode]
        log = runs_dir(_cfg(), create=True) / "_jobs"
        log.mkdir(parents=True, exist_ok=True)
        err_file = log / "dashboard_run.log"
        err_fh = open(err_file, "w", encoding="utf-8")  # noqa: SIM115 - child owns it
        proc = subprocess.Popen(
            cli, cwd=REPO_ROOT,
            stdout=subprocess.DEVNULL, stderr=err_fh, text=True,
            # own process group: restarting the dashboard (Ctrl-C in tmux)
            # must not SIGINT a 20-minute analysis mid-detection - that
            # killed a live corridor run inside a torch conv (verified,
            # Round 4 D72)
            start_new_session=True)
        st.session_state["proc"] = proc
        st.session_state["proc_err_file"] = str(err_file)
        st.session_state["proc_cmd"] = " ".join(cli)
        st.session_state["proc_t0"] = time.monotonic()
        st.rerun()


def _stage_progress_html(run_dir: Path | None) -> str:
    lines = []
    if run_dir is not None:
        s = _read_json(run_dir / "stage_status.json") or {}
        for k, v in s.get("stages", {}).items():
            mark = ("&#9679;" if v == "done" else
                    "&#9653;" if v == "running" else "&#9675;")
            cls = ("ok" if v == "done" else
                   "go" if v == "running" else "s")
            lines.append(f'<span class="{cls}">{mark} {k}</span>')
    return ('<div class="fs-run">'
            + " &nbsp;".join(lines[:13]) + "</div>") if lines else ""


def show_live_progress() -> None:
    proc = st.session_state.get("proc")
    if proc is None:
        return
    t0 = st.session_state.get("proc_t0", time.monotonic())
    # the new run is the most recent one created after the process started
    started = time.time() - (time.monotonic() - t0)
    ph = st.empty()
    run_dir: Path | None = None
    while proc.poll() is None and (time.monotonic() - t0) < MAX_RUN_S:
        # match the new run by its stage_status.json mtime (the run dir's
        # own mtime is stale on re-runs that reuse the same folder)
        runs = [r for r in list_runs()
                if (r / "stage_status.json").stat().st_mtime >= started - 5]
        run_dir = runs[0] if runs else None
        ph.markdown(
            f"**Run in progress** &nbsp;`{st.session_state.get('proc_cmd','')}`"
            + _stage_progress_html(run_dir), unsafe_allow_html=True)
        time.sleep(3)
    code = proc.poll()
    if code is None:  # MAX_RUN_S bound hit while still running
        proc.kill()
        code = proc.poll()
    err_tail = ""
    err_path = Path(st.session_state.get("proc_err_file", ""))
    if err_path.is_file():
        err_tail = err_path.read_text(encoding="utf-8", errors="replace")[-600:]
    ok = code == 0
    ph.markdown(
        (f"**Run complete.** &nbsp;`{st.session_state.get('proc_cmd','')}`"
         if ok else
         f"**Run failed** (exit {code}). &nbsp;`{st.session_state.get('proc_cmd','')}`")
        + _stage_progress_html(run_dir)
        + (f'<div class="fs-run s">{err_tail}</div>' if not ok and err_tail else ""),
        unsafe_allow_html=True)
    st.session_state.pop("proc", None)


# ---------------------------------------------------------------------------
# Map — satellite basemap, fit to the analysed data, legend OUTSIDE folium
# ---------------------------------------------------------------------------
def render_map(run_dir: Path, results: dict):
    import folium
    import geopandas as gpd
    from shapely.geometry import box as sbox

    bbox = results["meta"]["bbox"]
    m = folium.Map(
        location=[sum(bbox[1::2]) / 2, sum(bbox[0::2]) / 2],
        zoom_start=9,
        tiles=None,
    )
    folium.TileLayer(SAT_TILES, attr=SAT_ATTR, name="satellite",
                     max_zoom=19).add_to(m)
    folium.TileLayer(LABEL_TILES, attr=LABEL_ATTR, name="labels",
                     max_zoom=19, overlay=True).add_to(m)

    def rd(name):
        p = run_dir / name
        return gpd.read_file(p) if p.is_file() else None

    def add(gdf, key, fill=True, wt=1.5):
        if gdf is None or len(gdf) == 0:
            return
        c = LAYER_STYLE[key]["color"]
        folium.GeoJson(gdf, name=LAYER_STYLE[key]["label"],
            style_function=lambda _,c=c,f=fill,w=wt: {
                "color": c, "fill": f, "fillOpacity": .4 if f else 0, "weight": w},
        ).add_to(m)

    add(gpd.GeoDataFrame(geometry=[sbox(*bbox)], crs="EPSG:4326"),
        "bbox_outline", False, 2)
    add(rd("analysed_area.geojson"), "analysed_area_outline", False, 2)
    add(rd("flood.geojson"), "flood_water")
    add(rd("model_flood.geojson"), "flood_water_model")
    add(rd("event_debris.geojson"), "event_debris")
    add(rd("debris.geojson"), "debris")

    dp = run_dir / "damage.gpkg"
    if dp.is_file():
        d = gpd.read_file(dp).to_crs("EPSG:4326")
        for ft, key in (("building", "damaged_building"), ("road", "damaged_road"),
                        ("bridge", "affected_bridge")):
            sub = d[d["feature_type"] == ft] if "feature_type" in d.columns else d.iloc[0:0]
            add(sub, key)

    add(rd("flood_path.geojson"), "flood_path", False, 2)

    fp = _read_json(run_dir / "flood_path.json")
    if fp:
        lon, lat = fp["upstream_point"]
        folium.Marker([lat, lon], icon=folium.Icon(icon="star", color="red"),
                      tooltip="Upstream point").add_to(m)

    co = _read_json(run_dir / "cutoff.json")
    if co:
        sk = {"likely_cut": "settlement_likely_cut",
              "possibly_cut": "settlement_possibly_cut",
              "already_isolated": "settlement_already_isolated",
              "still_connected": "settlement_connected",
              "no_road_mapped": "settlement_already_isolated",
              "not_analysed": "settlement_not_analysed"}
        onp = {s["name"] for s in (fp or {}).get("settlements_along_path", [])}
        for s in co.get("settlements", []):
            col = LAYER_STYLE["settlement_on_flood_path"]["color"] if s["name"] in onp \
                else LAYER_STYLE[sk.get(s["status"], "settlement_connected")]["color"]
            folium.CircleMarker([s["lat"], s["lon"]], radius=4, color=col,
                fill=True, fill_opacity=.85,
                tooltip=f"{s['name']} - {s['status']}").add_to(m)

    # fit the frame to the analysed data, not a fixed zoom
    m.fit_bounds([[bbox[1], bbox[0]], [bbox[3], bbox[2]]])
    folium.LayerControl(collapsed=False, position="topleft").add_to(m)
    return m


def legend_row(run_dir: Path) -> None:
    """Legend chips rendered by Streamlit (NOT inside the folium figure:
    a figure-root legend child forced folium's double float layout and
    shrank the map to 500 px - the 'map not showing' bug, D72)."""
    import html as h
    present = []
    for key in ("event_debris", "debris", "flood_water", "damaged_building",
                "damaged_road", "affected_bridge", "settlement_likely_cut",
                "settlement_possibly_cut", "settlement_connected",
                "settlement_not_analysed", "flood_path", "bbox_outline"):
        v = LAYER_STYLE[key]
        active = " is-on" if key in ("event_debris", "debris",
                                     "settlement_possibly_cut") else ""
        present.append(
            f'<span class="fs-leg-item{active}" style="color:{v["color"]}">'
            f'<i style="background:{v["color"]}"></i>'
            f'<span style="color:var(--steel)">{h.escape(v["label"])}</span></span>')
    st.markdown('<div class="fs-leg">' + "".join(present) + "</div>",
                unsafe_allow_html=True)


def sidebar_run_info(results: dict, run_dir: Path) -> None:
    bbox = results["meta"]["bbox"]
    st.markdown("#### Opened run")
    st.caption(f"bbox ({bbox[0]:.3f}, {bbox[1]:.3f}) - ({bbox[2]:.3f}, {bbox[3]:.3f})")
    st.caption(f"date {results['meta']['flood_date']} - mode {results['meta']['mode']}")
    cov = (results["flood"] or {}).get("coverage_fraction")
    if cov is not None:
        st.markdown(_badge(f"analysed area {cov:.1%}", "blue"),
                    unsafe_allow_html=True)
        st.caption("share of bbox reliable for radar change detection; "
                   "the rest is steep terrain and shadow, not missing data")
    prov = _read_json(run_dir / "provenance.json") or {}
    pre = prov.get("sentinel1_pre") or {}
    post = prov.get("sentinel1_post") or {}
    st.caption(f"track {pre.get('relative_orbit')} {pre.get('orbit_direction')} | "
               f"{str(pre.get('sensing_time', ''))[:10]} -> {str(post.get('sensing_time', ''))[:10]} | "
               f"OSM {prov.get('osm_snapshot_date')}")


def main() -> None:
    st.markdown(CSS, unsafe_allow_html=True)
    st.title("FloodSight")
    st.caption("Where did the flood hit? What was damaged? Who is cut off? "
               "Real Sentinel-1 radar, Copernicus DEM, pre-event OpenStreetMap.")

    with st.sidebar:
        sidebar_new_run()
        st.divider()
        runs = list_runs()
        selected = st.selectbox("open a run",
            [r.name for r in runs] or ["(no runs)"])

    show_live_progress()

    if not runs or selected == "(no runs)":
        st.markdown(f"""
        <div style="padding:4rem 2.5rem;border:1px solid {C['border']};
             border-radius:12px;background:{C['card']};max-width:580px;margin:2.5rem auto;">
            <p style="font-family:'JetBrains Mono',monospace;font-size:.65rem;
               text-transform:uppercase;letter-spacing:.1em;color:{C['steel']};margin:0 0 .8rem 0;">
               Getting started</p>
            <h2 style="font-size:1.4rem;font-weight:600;letter-spacing:-.02em;
               color:{C['ink']};margin:0 0 .5rem 0;">Start a flood analysis</h2>
            <p style="font-size:.9375rem;line-height:1.6;color:{C['steel']};
               max-width:52ch;margin:0 0 1rem 0;">
               Pick a preset or enter a bounding box and flood date in the
               sidebar, then run. The pipeline downloads real Sentinel-1
               scenes, pairs them on the same orbit track, and builds the
               map, damage estimates and cut-off analysis from raw data.
               The map below renders on Esri satellite imagery so the
               terrain is always visible.</p>
            <p style="font-family:'JetBrains Mono',monospace;font-size:.75rem;
               color:{C['steel']};margin:0;">
               A cached area re-runs in seconds; a fresh 20x20 km area takes
               roughly 25 minutes end to end.</p>
        </div>
        """, unsafe_allow_html=True)
        st.caption(ATTRIBUTION)
        return

    run_dir = next(r for r in runs if r.name == selected)
    try:
        results = ResultsStore(run_dir).read()
    except (FileNotFoundError, ResultsValidationError) as exc:
        st.error(f"No valid results.json: {exc}")
        st.caption(ATTRIBUTION)
        return

    with st.sidebar:
        sidebar_run_info(results, run_dir)

    f = results["flood"]
    b = results["damage"]["buildings"]
    r = results["damage"]["roads"]
    br = results["damage"]["bridges"]
    c = results["cutoff"]
    fa = f.get("area_km2_new_water")
    cov = f.get("coverage_fraction")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("flood water",
              f"{fa if fa is not None else 'n/a'} km\u00b2",
              help="standing water on the post-event radar scene; an honest "
                   "zero means the flash flood had already receded")
    m2.metric("buildings hit", f"{b['likely_hit']} / {b['possibly_hit']}",
              help="likely / possibly hit by the mapped change layers")
    m3.metric("roads in extent", f"{r['total_length_km']} km")
    m4.metric("cut off (likely/possible)",
              f"{len(c['likely_cut'])}L / {len(c['possibly_cut'])}P",
              help="L = likely (high confidence), P = possible (lower "
                   "confidence). 0L does NOT mean no one is cut off; the "
                   "possible tier is the honest answer when the high-"
                   "confidence tier is empty.")

    bh = ""
    bh += _badge(f"area {b['total_in_area']}", "blue")
    bh += _badge(f"bridges {br['affected']}/{br['total_in_area']}", "amber")
    bh += _badge(f"isolated {len(c['already_isolated'])}", "purple")
    bh += _badge(f"connected {c['still_connected_count']}", "green")
    if c.get("not_analysed_count"):
        bh += _badge(f"not analysed {c['not_analysed_count']}", "red")
    if cov is not None:
        bh += _badge(f"analysed area {cov:.1%}", "green" if cov >= .9 else "red")
    cb = _read_json(REPO_ROOT / "evaluation" / "results"
                    / "coverage_breakdown.json") or {}
    if cb.get("raw_footprint_coverage_pct") is not None:
        bh += _badge(
            f"raw radar coverage {cb['raw_footprint_coverage_pct']:.0f}%",
            "green")
    attr = _read_json(REPO_ROOT / "evaluation" / "results"
                      / "event_attribution.json") or {}
    if attr.get("event_control_ratio") is not None:
        ok = attr.get("attributable")
        if ok is not None:
            bh += _badge(
                f"event/control ratio {attr['event_control_ratio']}",
                "green" if ok else "amber")
    if attr.get("event_attributed_km2") is not None:
        bh += _badge(
            f"event-attributed {attr['event_attributed_km2']} km\u00b2",
            "green" if attr.get("attributable") else "amber")
    st.markdown(bh, unsafe_allow_html=True)

    # ---- v2 (Round 5): the third cut-off category + terrain exposure ----
    cv2 = _read_json(run_dir / "cutoff_v2.json")
    if cv2:
        counts = cv2["counts"]
        n_un = sum(v for k, v in counts.items()
                   if k.startswith("unassessed"))
        st.markdown(
            f'<div class="fs-run">'
            f'<span class="s">v2 steep-terrain honesty:</span> '
            f'<span class="ok">{counts.get("connected (route inside analysed area)", 0)}</span> settlements connected with routes fully inside the analysed area, '
            f'<span class="go">{counts.get("possibly_cut", 0)}</span> possibly cut, '
            f'<span class="go">{counts.get("likely_cut", 0)}</span> likely cut, and '
            f'<span class="go">{n_un}</span> <b>unassessed - their routes cross terrain masked as unreliable (slope>30&deg;/shadow), so connectivity cannot be verified</b>'
            f'</div>', unsafe_allow_html=True)
    ex = _read_json(run_dir / "terrain_exposure.json")
    if ex:
        st.markdown(
            f'<div class="fs-run"><span class="s">terrain exposure scenario - '
            f'NOT observed damage:</span> {ex.get("settlements")} settlements, '
            f'{ex.get("roads_km")} km of roads and {ex.get("bridges")} bridges '
            f'lie inside the flood-path valley corridor (HAND&le;25 m, slope&le;15&deg;). '
            f'This is exposure to the hazard corridor, not observed damage.'
            f'</div>', unsafe_allow_html=True)

    # ---- the map, full width, on visible satellite terrain ---------------
    try:
        import streamlit_folium
        streamlit_folium.st_folium(
            render_map(run_dir, results),
            height=560, use_container_width=True, returned_objects=[],
            debug=False)
    except ImportError:
        st.info("Install: uv pip install streamlit-folium")
    legend_row(run_dir)

    with st.expander("Provenance", expanded=False):
        prov = _read_json(run_dir / "provenance.json") or {}
        pre = prov.get("sentinel1_pre") or {}
        post = prov.get("sentinel1_post") or {}
        st.markdown(
            f"Sentinel-1 pair: `{str(pre.get('sensing_time',''))[:10]}` to "
            f"`{str(post.get('sensing_time',''))[:10]}`, track "
            f"{pre.get('relative_orbit')} {pre.get('orbit_direction')}  \n"
            f"OSM snapshot: **{prov.get('osm_snapshot_date','n/a')}** (pre-event)  \n"
            f"DEM: Copernicus GLO-30  \n"
            f"Method: {f['confidence_summary']['method']}")
        if cov is not None:
            st.markdown(f"Analysed area: **{cov:.1%}** of bbox")
        for w in results.get("warnings") or []:
            k = "red" if w.get("severity") == "error" else "amber"
            st.markdown(_badge(w.get("code", "?"), k) + f" {w.get('message','')}",
                        unsafe_allow_html=True)

    st.divider()
    st.caption(ATTRIBUTION)


if __name__ == "__main__":
    main()
