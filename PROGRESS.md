# PROGRESS.md

Live task and phase status for FloodSight. Re-read at the start of every
session. States: not started, in progress, verified, blocked.

## Current phase

Phases 0-4 are verified/closed. Phase 5 (results store, damage, cut-off) is
next. Phase 7 (Kuro Siwo) and the full case-study archive are BLOCKED on the
EBS disk enlargement that the user was asked for (see the session log
below).

## Session log (2026-10-08, third session — Phase 2 closure)

*   User fixed .env; authentication now works.
*   First download attempt returned HTTP 401: the catalogue `$value` endpoint
    301-redirects to `download.dataspace.copernicus.eu` and `requests` drops
    the Authorization header on cross-host redirects. Verified the download
    host accepts the same Bearer token directly; implemented manual redirect
    following + fresh-restart retries (the server sends no Accept-Ranges).
*   `download_product` (pre DC16): 1,718 MB in 325 s (5.3 MB/s); (post 2C5B):
    1,718 MB in 394 s (4.4 MB/s). Both SAFE zips verified by listing:
    VV+VH measurement GeoTIFFs (~855 MB per band), calibration + noise LUT
    XMLs, annotations, manifest.safe.
*   Disk after downloads: 7.4 GiB free. No expansion needed for Phase 3.

## Session log (2026-10-09 — Phases 6-10)

*   EBS extended (96 GB disk, 30 GiB RAM, 8 vCPU). Track-121 pair re-downloaded
    (case-study archive restored). Kuro Siwo subset downloaded and verified:
    1 train + 1 test GRD shard (~10.9 GB each, byte-exact), 6,000 samples per
    shard, per-sample info.json with scene sources (D35).
*   Dashboard (Phase 6): Streamlit + Folium; starts real CLI runs; live
    stage progress; layers incl. debris + model flood + flood-path corridor;
    Playwright-verified (0 console errors, screenshots in docs/figures/);
    user tunnel on port 8520.
*   Debris evidence layer (Phase 8): brightening > +3 dB AND not-water AND
    gentle slope AND within 1 km of mapped waterways (layer added to the OSM
    extraction); case study tightened from 3.39 to 1.6804 km2 / 603
    polygons with the valley-floor mask (D36/D37).
*   Training (Phase 7): manifest from real shards (24 train events in the
    single train shard -> 19/5 event split, 120/event flood-first cap;
    mask value 3 = no-data verified against valid_mask, D39; DEM sentinel
    -32768 zeroed, D40); CPU training 15 epochs x ~70 s; best val IoU
    0.3229. Held-out TEST (6 events): model IoU 0.630 / F1 0.773 /
    precision 0.981 vs baseline 0.554 / 0.713 (D38; metrics in
    evaluation/results/segmentation_metrics.json). Model wired into the
    pipeline (tiling with averaged overlaps) and live-verified on the real
    pair: 6 flood pixels, 100% baseline agreement (honest zero confirmed).
*   Report + assistant (Phase 9): sitrep EN/NE markdown+PDF (Unicode fonts
    incl. Devanagari; fpdf2 kwarg/new_x fixes; validator-checked numbers);
    `floodsight report` works; assistant lookup Q&A grounded by the
    validator; evaluation/assistant_tests.py + tests/test_validator.py pass
    (rounding-direction-safe validator fix: the store may round TO the text
    number, never the reverse).
*   Flood path (Phase 10): priority-flood + D8 routing implemented and
    unit-tested; real corridor from (85.35, 28.1): 61,094 m, 30 settlements,
    mean buffer 1,032 m. Two real bugs found by live verification and fixed:
    unguarded D8 walk cycled into 29.7 GB OOM (cycle guard added); GLO-30
    grid is EPSG:4326 so the corridor was buffered in degrees (metric UTM
    fix). Folded into results.json (D41).
*   EMSR927 (Phase 11): comparison script ready; the EMS portal requires a
    login, so the reference download is a user action (K16).
*   Test suite: 92 tests pass offline; network integration test passes
    (full pipeline with credentials + SNAP). The CLI dashboard test now
    launches/mocks the real server instead of a stub.

## Session log (2026-10-10 — approved bbox case study)

*   User APPROVED the Trishuli corridor bbox with 7 conditions (DECISIONS D43).
    Pre-condition checks all verified before applying:
    (1) upstream point (85.377, 28.278) is 5 m from the Bhote Koshi
    centreline - on the river, no snapping needed (figure
    docs/figures/bbox_upstream_check.png); (2) true corridor fully inside:
    margins west +3,143 m / south +6,820 m / east +3,521 m / north +3,556 m;
    the 0.02-0.03 deg buffer = 2.0-3.3 km covers OSM centrelines' 10-50 m
    uncertainty and the 100-1000 m valley many times over; a 6.7 km
    namesake tributary ("Bhoti Khola", different valley) lies west and stays
    outside by design; (3) lon-first order asserted by
    tests/test_region_order.py; (4) resources: 4 GLO-30 tiles (87 MB new),
    18 candidate S1 scenes, only the cached track-121 pair needed;
    (5) soft warning at 1.0 deg now recorded in warnings.json (fires for
    this box), hard stop at 2.5 deg tested; (6) no EMSR927 or post-event map
    used for any coordinate (D43).
*   Approved-bbox case study, fast mode + upstream point, REAL numbers:
    total 30 m 5 s (1800 s): preprocess 19 s (cached pair + SNAP interim),
    detect 1468 s, damage 117 s, cutoff 104 s, flood path 84 s. Disk: run
    folder 379 MB, data/interim 3.5 GB total, 26 GB still free.
    flood water 0.0 km2 (honest, D32); probable debris 5.4876 km2; buildings
    144 likely / 434 possible of 869,263; roads inside extent 10.66 km;
    bridges 20 of 1556; cutoff 16 likely / 11 possibly cut / 736 connected /
    104 already isolated; model agrees 100% (0.0001 km2). Flood-path bonus:
    384.6 km terrain corridor from the approved point, 85 settlements along
    it (D8 staircase length - see limitations note in the results section).
*   Soft-warning implementation note: the first approved-box run (32 m 4 s)
    logged the large_bbox warning to console only; the warning is now
    recorded through the run object into warnings.json (tested), and the
    final run (30 m 5 s) shows it there.

## Session log (2026-10-12/13 — validation and fixes round)

*   A (COVERAGE): the user's finding confirmed — the first approved-bbox run
    silently analysed 31.2% (track 121). Live per-track coverage table
    computed; pair selection now ranks by bbox coverage (min_pair_coverage
    0.9, min_track_coverage 0.05); multi-slice passes fully supported
    (matched by slice number, SNAP per slice, union with per-track
    attribution); analysed_area.geojson written; partial_coverage warning
    below 90%; every statistic clipped to the analysed area; settlements
    outside are 'not_analysed'. Tests added (test_coverage_selection.py).
    FINAL run: track 85 ASC (08-16->08-28), coverage 55.9% of bbox,
    runtime 6263 s, run folder 296 MB.
*   Performance bugs found with py-spy and fixed (D50/D52): per-blob
    vectorize loop (unbounded); 109M-cell polygon simplify stall; 4-vs-8
    connectivity mismatch zeroing all confidences; damage sjoin 1-h stall
    (dissolve + shapely.prepare); per-slice SNAP cache collision
    (scene ids now in the folder name); CRS mismatch in the not_analysed
    check; results.json debris key moved; sub-area split units bug.
*   B (FLOOD PATH): old 384.6 km trace had a 4822-cell eastward detour
    (D8 tie-wandering). Root causes found and fixed: DEM voids made inert
    (7,091 zero cells, one corner void attracted 45% of flow); main-stem
    walk by accumulation; nearest-drainage snap. New trace: 189.4 km from
    the approved upstream point, exits west, 189,371 m vs 181.7 km OSM
    centrelines (ratio 1.04). Tests added (test_flood_path.py).
*   C (DEBRIS VALIDITY): debris_validity.py written and run. C1: 8.4% of
    debris within 500 m of the named corridor rivers, 10.4% within 1 km,
    22.8% within 5 km; north/south split 22.3/19.7 km2; slope classes in
    evaluation/results/debris_validity.json; HAND honestly skipped
    (per-cell walk too slow on CPU at 10.9M cells). C4: the 1-pixel model
    artefact is removed by the shared min_blob filter (7 polygons remain,
    nearest ~44 km from the reported location). C5: frozen, config hash
    4d689774634c30d1ce0a5df9cb5699ac7f3ad25f351d77e7d753a109e8fa1a28,
    src hash cd3d08adfbb200d4bc0d2df788f483171f333feac3a1e10990ad0f71fa9b345b.
*   E (DASHBOARD): wide layout, 16:9 map (CSS aspect-ratio), collapsible
    legend generated from the SAME LAYER_STYLE dict as the markers,
    analysed-area outline + coverage % in the status panel, sidebar shows
    the opened run's bbox+date, stray JSON widget hidden. Playwright
    verified at 1920x1080 and 1366x768: no horizontal scrollbar, 0 errors.
*   F (REPORT TEXT): scripts/make_pdf_content.py generates
    pdf_content.txt (2203 words) and pdf_content_sources.txt; every number
    read from result files; validator run against the union of all cited
    files; remaining flags documented as identifiers/citations (EMSR927,
    NeurIPS 2024).
*   Controls (C3): control (a) pre-event launched in the background
    (flood_date=2026-08-10); results pending at freeze time.

## Session log (2026-10-13 — Round 3: map fix, controls, HAND, explanations)

*   MAP FIX (D55): dark CartoDB basemap was invisible because the `{r}`
    retina parameter silently broke folium; removed it. Map now renders
    9/9 tiles at 884x520 (Playwright verified). Height=520 fixes the
    earlier height=0 iframe bug.
*   CONTROL (a) COMPLETED (D54): pre-event pair produced MORE debris
    (54.25 km2) than the event run (41.99 km2). Controls (b) and (c)
    were launched but killed by session cleanup; their numbers are
    NOT YET MEASURED (D57).
*   HAND (D56): the per-cell downstream walk is too slow on CPU at
    corridor scale (10.9M cells; hand() is O(n^2) worst case). The
    terrain evidence comes from the slope-class breakdown and the
    1km waterway buffer; 8.4%/500m distance shares recorded.
*   QUESTION 3a ANSWERED: track 85 slices 5+6 were SNAP-subset to the
    bbox and their union produces 55.9% coverage. Slice 5 covers only
    the northern ~50km (bounds y 3044174-3082694), slice 6 covers the
    full height but the combined valid-pixel union is still 55.9%.
    The remaining 44.1% needs a third slice or a different track.
*   QUESTION 3b ANSWERED: "likely cut" went from 16 to 0 because the
    flood-water layer is 0.0 km2 (no polygons to intersect with) and
    the debris confidences on the larger area do not reach the 0.6
    "likely" threshold for any settlement's road segments. "Possibly
    cut" went from 11 to 106 because the debris layer is now 41.99 km2
    (vs 1.68 on the old small test bbox) and intersects many more
    road segments at the 0.3 "possible" tier. A result of 0 likely
    cut does NOT mean no one is cut off; it means the confidence does
    not reach the "likely" tier - the dashboard reports both tiers
    and the 106 "possibly cut" is the honest answer.


## Session handover (2026-10-13 — read this first when resuming)

### Machine and venv
- AWS EC2 Ubuntu 26.04, 8 vCPU (was 4), 30 GiB RAM, 96 GB disk (~12 GB free)
- No GPU. No git repo (D18: user commits manually).
- venv at `.venv/` (Python 3.14.4, uv 0.12.23). Install: `uv sync --extra dev --extra dashboard --extra graph`
- ESA SNAP at `/home/ubuntu/snap/bin/gpt` (v14.0.0). osmium-tool installed.
- Credentials in `.env` (CDSE username/password; never print).

### Paths
- Repo root: `/home/ubuntu/Floodsight`
- Approved bbox: `[84.39, 27.68, 85.73, 28.31]`; upstream `[85.377, 28.278]`
- Case study run: `runs/84.390_27.680_85.730_28.310_2026-08-26_fast`
- Control (a): `runs/84.390_27.680_85.730_28.310_2026-08-10_fast` (54.25 km2 debris > event 41.99)
- Kuro Siwo subset: `data/training/kurosiwo/` (1 train + 1 test shard)
- Model checkpoint: `src/floodsight/detection/checkpoints/best_model.pt`
- Evaluation metrics: `evaluation/results/segmentation_metrics.json`

### Ports and dashboard
- Dashboard runs on port **8520** (user's SSH tunnel maps this)
- Start: `./run.sh` or `nohup .venv/bin/python -m streamlit run src/floodsight/dashboard/app.py --server.headless true --server.address 0.0.0.0 --server.port 8520 --browser.gatherUsageStats false --global.developmentMode false > /tmp/opencode/streamlit.log 2>&1 &`
- The dashboard uses **CartoDB Dark Matter** tiles (dark basemap). Do NOT use `{r}` retina param in the tile URL.

### Running stages
- Full pipeline: `.venv/bin/python -c "from floodsight.config import load_config; from floodsight.pipeline import run_pipeline; run_pipeline([84.39,27.68,85.73,28.31],'2026-08-26',mode='fast',cfg=load_config(),region='nepal',upstream=(85.377,28.278))"`
- Control (b): `nohup .venv/bin/python scripts/controls/ctrl_b.py > runs/_jobs/ctrl_b.log 2>&1 &`
- Control (c): `nohup .venv/bin/python scripts/controls/ctrl_c.py > runs/_jobs/ctrl_c.log 2>&1 &`
- Tests: `timeout 300 .venv/bin/python -m pytest` (104 offline, 1 network)
- Report content: `timeout 600 .venv/bin/python scripts/make_pdf_content.py`
- Debris validity: `timeout 900 .venv/bin/python evaluation/debris_validity.py --run 84.390_27.680_85.730_28.310_2026-08-26_fast`

### Hashes at freeze (D53)
- Config hash: `4d689774634c30d1ce0a5df9cb5699ac7f3ad25f351d77e7d753a109e8fa1a28`
- Source hash: `cd3d08adfbb200d4bc0d2df788f483171f333feac3a1e10990ad0f71fa9b345b`
- NOTE: code changed after freeze (map fix, CSS rewrite, HAND attempt) — re-hash before the final freeze.

### Background processes
- Dashboard: port 8520, may or may not be running; restart with the command above.
- Control (b): killed by session cleanup; restart from scripts/controls/ctrl_b.py.
- Control (c): never started; run after (b) completes.
- HAND computation: timed out at corridor scale; needs vectorized implementation.

### Key verified numbers (with file paths)
- Coverage 55.9% of bbox: `runs/84.390_..._2026-08-26_fast/results.json` → `flood.coverage_fraction`
- Flood water 0.0 km2: same file → `flood.area_km2_new_water`
- Debris 41.99 km2: same file → `flood.area_km2_probable_debris`
- Buildings 228 likely / 3397 possible: same file → `damage.buildings`
- Cutoff 0 likely / 106 possible / 594 connected / 85 isolated / 87 not-analysed: `runs/84.390_.../cutoff.json`
- Control (a) debris 54.25 km2: `runs/84.390_..._2026-08-10_fast/results.json`
- Model IoU 0.630 / F1 0.773 vs baseline 0.554 / 0.713: `evaluation/results/segmentation_metrics.json`
- Debris distance: 8.4% within 500m, 10.4% within 1km: `evaluation/results/debris_validity.json`
- Training best val IoU 0.3229: `src/floodsight/detection/checkpoints/metrics.json`

### Next steps
See `NEXT_STEPS.md` for the ordered todo list.

## Session log (2026-10-14 — Round 4: attribution, validation, delivery)

*   T2 DONE (D61): hand_fast (nearest-2D drainage via distance_transform_edt)
    added; window validation r=0.629 overall, low-HAND (<=30 m) mean |diff|
    3.35 m, 77% within 5 m; full-grid slow HAND OOMs (23.9 GB, kernel log);
    hand30m/slope30m regenerated in metric UTM after a CRS-labelling bug fix.
*   T3 SOLVED (D58): raw slice coverage 99.9%; analysed 55.7-55.9% after
    terrain masks (43.85% slope>30 deg, 0.37% layover/shadow).
*   T4: control (c) = "no valid same-track pair" (D60); control (b) DONE on
    track 19 DESC 09-05->09-17: debris 30.87 km2, buildings 157/3088,
    cutoff 7/79; control (a) 54.25 km2 (D54). Old method fires on every
    monsoon pair; temporal-stack attribution (T1) is the response.
*   T6 DONE (D63): per-event IoU 0.19-0.77 on test; val model 0.3665 <
    baseline 0.6326 (hard event at 895 m); leakage check clean; ALL test
    events lowland (4-108 m) - NO mountainous held-out event; Himalayan
    generalisation not claimed; encoder trained WITHOUT ImageNet
    pretraining (deviation recorded).
*   T7: judge mode verified (corridor refused at 9,237 km2, box accepted at
    415 km2); judge box [84.9041, 27.8035, 85.1105, 27.9869] APPROVED by
    user; fresh timing run queued after T1 (CPU/disk contention).
*   T8a DONE (D64): EMSR927 products downloaded via the public dashboard API
    (119.7 MB, 6 products, 4 AOIs, all inside the approved bbox); metadata
    only read; freeze + one-shot comparison still pending T1.
*   T10 DONE (code): all six report-generator fixes applied; every number
    reads from results files; not regenerated (per user instruction).
*   Incidents: partial-zip bug fixed (D59); mtime-scoped prune after the
    ctrl_c glob-prune deleted an in-flight zip; CDSE token expiry fixed
    (fresh token per product); a zombie judge-box CLI test truncated
    s1_post_coreg.tif to 0 bytes -> coregister now atomic (tmp+os.replace),
    file regeneration queued; disk pressure managed by pruning the
    ctrl_b SNAP cache (results frozen in runs/).

## Session log (2026-10-14 — Round 4 completed: freeze + one-shot comparison)

*   T5 DONE (D68): event-attributed recompute buildings 8/74 (old 228/3397),
    roads 1.28 km (old 107.63), bridges 19 (old 159), cutoff 0/0 (old 0/106);
    three counts 30 / 4,348 / 769,329. Two py-spy-found performance bugs
    fixed in analysis/damage.py (grid-subdivided clip, STRtree counts).
*   T8b FROZEN (D69): config hash b09870f7...bc1bb, src hash ba55a74a...3c00,
    104 tests pass, lint clean, verified intact after the comparison.
*   T8c ONE-SHOT EMSR927 COMPARISON (D70): reference = mass-movement
    polygons (all AOIs), common area 41.30 km2. Event-attributed layer:
    precision 0.659/0.694/0.762, recall 0.013/0.093/0.343 at 0/30/100 m;
    old layer F1 higher (0.59 vs 0.47 at 100 m) because it is larger.
    Building-damage recall 9/3,807 (0.24%). Disagreement GeoJSONs + 5
    misses + 5 false alarms saved. NO tuning performed after the run.
*   T9 DONE: dashboard verified with Playwright at 1920x1080 and
    1366x768 - 9/9 dark tiles, both renamed layers ("Event-attributed
    debris (temporal stack)" / "Other ground change (monsoon baseline,
    not event-attributed)"), quality badges (analysed area 55.9%, raw
    radar coverage 100%, event/control ratio 4.71, event-attributed
    0.455 km2), 0-likely help text, 0 console errors at both resolutions.
*   T11 delivery checks: no git repo (D18 - user commits manually);
    .gitignore covers data/, runs/, .env, *.pt, evaluation/reference/,
    .venv/; no secrets in any tracked file; EMSR portal credentials in
    .env only (never printed, never used - the public API needed no login).

## Session log (2026-10-14 — dashboard + launcher repair, D72)

*   MAP FIXED: st_folium width=500 default was the "map not showing" bug
    (500 px map in an 884-px frame + legend-forced double layout); now
    full-width 882x560 on Esri World Imagery satellite + dark labels;
    legend = Streamlit chip row (12 chips); layer toggles verified ON.
*   RUN ANALYSIS FIXED: infinite-loop-on-early-failure removed; bounded
    90-min wait; stderr to file; stage chips keyed by stage_status.json
    mtime; cached pairs skip the 6.9 GB re-download; preprocess cached
    end-to-end. Verified live from the UI (preset trishuli run; stage
    chips ticked through validate..preprocess with no downloads).
*   Verified at 1920x1080 and 1366x768 (full-page screenshots
    docs/figures/dashboard_v2_1920.png / dashboard_v2_1366.png),
    0 console errors; Esri tile URLs return 200 image/jpeg.

## Phase status

| Phase | State | Notes |
|---|---|---|
| 0 Recon | verified | |
| 1 Scaffold | verified | 67 offline tests pass on Python 3.14.4 |
| 2 Acquisition | verified/closed | Exit criterion met: pair found AND downloaded; DEM + OSM fetched; provenance written |
| 3 Preprocessing | verified/closed | SNAP 14 headless (D28) + Lee speckle (D33); identical co-registered grids verified on real data; figure docs/figures/phase3_s1_pair_check.png; HAND pending (K13) |
| 4 Baseline detection | verified/closed | Runs end-to-end in the pipeline (flood_water.tif + flood.geojson + detection.json, real stats). Honest case-study result (D32): 0 standing-water pixels on BOTH post-event pairs (+2 d, +5 d); event signature is corridor CHANGE (near-river 9.3% bright >3 dB / 10.1% dark <-3 dB vs 3.6%/3.9% far) -> Phase 8 debris layer |
| 5 Results store, damage, cut-off | verified/closed | results.json validated on a real run: 186,869 buildings / 373 bridges / 342 settlements (59 already isolated, 283 connected, 0 cut - consistent with the 0-flood D32 finding); synthetic tests incl. the mandated bridge/loop/hamlet case pass; cutoff O(N*M) Dijkstra bug found and fixed with a single multi-source path tree |
| 6 Dashboard | verified/closed | Streamlit app verified with Playwright (0 console errors; real cards/map/provenance/footer; screenshot docs/figures/dashboard_verified.png); launches real CLI runs; user tunnel on 8520 |
| 7 Training and model | verified/closed | CPU training done (15 epochs x ~70 s, 1495 samples, 19 events); held-out TEST: model flood IoU 0.630 / F1 0.773 / precision 0.981 vs dark-water baseline IoU 0.554 / F1 0.713 (D38); checkpoint wired into the pipeline and live-verified on the real pair (agrees with the honest 0 standing water; 100% baseline agreement) |
| 8 Fusion/debris | verified/closed for the radar path | debris evidence layer live (D36/D37); optical confirmation deferred (monsoon cloud, S2 unused; recorded honestly) |
| 9 Report and assistant | verified/closed | sitrep EN+NE (md+PDF, Unicode fonts incl. Devanagari; real numbers validator-checked), CLI `floodsight report` works; assistant answers from the store with 0 ungrounded numbers; evaluation/assistant_tests.py all pass |
| 10 Flood path (bonus) | not started | |
| 11 Case study / EMSR927 | not started | Bbox awaiting user review |
| 12 Hardening | in progress | Generality rehearsal DONE (Koshi 2024-09-28, D42); remaining: EMSR927 reference (user), final README/demo polish |

## Session log (2026-10-08, fourth session — Phases 3 + 4)

*   ESA SNAP 14.0.0 installed headless (installer md5-verified, ~1.1 GB,
    unattended install to /home/ubuntu/snap); gpt works on Ubuntu 26.04.
    Full chain verified on the real scenes: Read(zip) -> Subset -> Orbit
    (S1D POEORB auto-download works) -> ThermalNoise -> Calibration ->
    Speckle(Lee 3x3) -> TC(Copernicus 30m, UTM, 10 m) -> BandSelect.
    ~44-52 s per subset-area scene on 4 cores. Radar path logged as D28
    (openEO rejected: basic-auth 404, device flow interactive, client
    credentials experimental). Version bumped 0.1.0 -> 0.1.1 so the
    version-keyed interim cache invalidates honestly after adding the
    speckle node (D33).
*   Pipeline preprocess stage verified on real data: `floodsight run
    --region trishuli` completes through detect_flood and stops honestly at
    assess_damage; preprocessed.json: coregistered_identical_grid=true,
    VV -8.6 dB / VH -20.2 dB / DEM 1993 m / incidence 43.8 deg; total run
    2m29s with fresh SNAP processing.
*   Baseline detector implemented and unit-tested (Otsu verified against
    brute force; blob cleanup, masks, vectorization, confidence).
*   Case-study finding (D32): NO standing flood water on either post-event
    pair (track 121 +5 d, track 85 +2 d; both honest zeros); the event IS
    visible as elevated near-river change -> Phase 8 debris layer.
*   Disk management: track-121 zips (3.4 GB) deleted to fit the track-85
    slice-6 test pair (re-downloadable in ~6 min via the pipeline);
    4.8 GiB free now. The +2 d test used slice 6 alone (footprint verified
    from the catalogue, D34). EBS enlargement requested from the user.

## Phase 2 verification (2026-10-08, live)

Implemented: `acquisition/cdse_client.py` (anonymous search with pagination
and retries; token + download code paths implemented but untested without
credentials), `acquisition/orbit_pairing.py` (same-track pairing by
relativeOrbitNumber + orbitDirection, explicit NoValidPairError with
alternatives), `acquisition/dem.py` (GLO-30 via AWS Open Data, anonymous),
`acquisition/osm_snapshot.py` (dated Geofabrik snapshot + osmium
extract/tags-filter/export pipeline, cached by bbox), pipeline wiring for
the six acquisition stages with per-stage status/timings, warnings.json and
provenance.json.

Commands run and their real output:

*   `.venv/bin/python -m pytest` -> "56 passed" (unit, no network);
    `pytest -m network` -> 1 passed (live Trishuli acquisition).
*   `.venv/bin/floodsight run --region trishuli` -> exit 3 (honest
    preprocess not-implemented) after completing all acquisition stages:
    stage_timings.json shows search_sentinel1 2.473s, search_sentinel2
    9.601s cumulative, DEM and OSM served from cache in ~0.03s; wall time
    9.76 s.
*   Live pair selection (run folder pair.json): track relative orbit 121
    DESCENDING, pre `S1D_IW_GRDH_1SDV_20260819T001036_..._DC16.SAFE`
    (2026-08-19, 7 days before), post
    `S1D_IW_GRDH_1SDV_20260831T001037_..._2C5B.SAFE` (2026-08-31, 5 days
    after) — matches the Phase 0 reconnaissance pair, selected
    automatically.
*   S2 selection: 27 L2A scenes with cloud <= 80% in the window
    (pre-event Aug 7..24, post-event Aug 27..Sep 8; clearest 13.4%).
*   DEM: 2 GLO-30 COG tiles downloaded (N27_00_E085_00 44,874,244 bytes,
    N28_00_E085_00 38,402,839 bytes; byte sizes match the published
    Content-Length exactly).
*   OSM: snapshot `nepal-260701.osm.pbf` (410,781,379 bytes, md5 OK) ->
    real layer extraction for the case-study bbox: buildings.geojson
    45.8 MB, roads.geojson 7.0 MB, bridges 83 KB, settlements 86 KB,
    destinations 8.5 KB.

Measured timings: first full live acquisition (with downloads, OSM extract
computed fresh) completed inside one integration run in roughly 25 s total;
cached re-run 9.76 s wall (see stage_timings.json in the run folder). OSM
snapshot download (410 MB) was not precisely timed; md5 verification passed.

Deviations: psycopg2 removed (D14); OSM via Geofabrik snapshot instead of
ohsome geometry (D19); DEM via AWS Open Data instead of CDSE collection
(D22); S1/S2 downloads at preprocess prep (D23).

## Trishuli bbox derivation (pending user approval)

Derived from pre-event river geometry in the snapshot (method and numbers in
docs/trishuli_bbox_derivation.md): proposed bbox `[84.39, 27.68, 85.73,
28.31]`, upstream point `[85.377, 28.278]` (Bhote Koshi head, Rasuwa).
NOT applied to config until approved.

## Environment facts (measured 2026-10-08, AWS EC2)

*   OS: Ubuntu 26.04.1 LTS, x86_64, 4 vCPU.
*   RAM: 15 GiB total (about 13 GiB available).
*   Disk: 13 GiB free of 38 GiB — training data does NOT fit; user will
    enlarge the EBS volume to 80+ GB on request (ask before large
    downloads).
*   GPU: none. `nvidia-smi` binary exists but no NVIDIA hardware/driver
    (EC2 virtual VGA only). Training is CPU-only unless the user provides a
    GPU instance.
*   Python env: uv 0.12.23 with `.venv` on Python 3.14.4 (DECISIONS D13).
    floodsight installed editable; rasterio 1.5.2, geopandas 1.2.0,
    numpy 2.5.3, shapely 2.2.0, pyproj 3.8.0, pytest 9.1.1, ruff 0.16.10.
    psycopg2-binary was removed per user decision (D14).
*   Not installed (later phases): torch, streamlit, folium, networkx,
    scipy, matplotlib.
*   ESA SNAP: not installed.
*   Network: CDSE catalogue and ohsome API both reachable (HTTP 200).
*   `.env`: absent — the user will add CDSE credentials themselves.
*   git: no repository (D18: user commits manually; do not run git init).

## Environment facts (previous machine, 2026-10-06 — historical)

*   OS: Linux x86_64, 12 CPU cores, Python 3.10.12, pip user-site.
*   GPU: none. RAM: 7.1 GiB. Disk: 43 GiB free. ESA SNAP not installed.
*   All Phase 0 API verification below was performed from that machine.

## Verified external facts (Phase 0)

These were checked against the live services, not assumed:

*   **ohsome API** (api.ohsome.org/v1, version 1.10.4): extract temporal
    extent runs 2007-10-08 to **2026-07-27T09:00Z**. The pre-event snapshot
    date 2026-07-27 from the brief is exactly the latest available, so
    date-pinned OSM queries are feasible.
*   **CDSE OData catalogue** (catalogue.dataspace.copernicus.eu/odata/v1):
    anonymous search works. The live syntax differs from older blog posts:
    filter fields are `Collection/Name`, `ContentDate/Start`,
    `ContentDate/End`, and spatial search is
    `OData.CSC.Intersects(area=geography'SRID=4326;POLYGON((...))')`.
    (Older documented forms `CollectionName`, `ContentDateStart`,
    `footprint` return HTTP 400.)
*   Product attributes include `relativeOrbitNumber`, `orbitDirection`,
    `polarisationChannels`, `platformSerialIdentifier`, `productType` —
    enough for mandatory same-track pairing without filename guessing.
*   **Real Sentinel-1 GRD data exists for the Trishuli corridor around the
    26 Aug 2026 event** (verified by query, 16 products). Examples:
    *   Track relative orbit 121, DESCENDING, ~00:10 UTC: pre-event
        2026-08-19 (`S1D_IW_GRDH_1SDV_20260819T001036_..._DC16`), post-event
        2026-08-31 (`S1D_IW_GRDH_1SDV_20260831T001037_..._2C5B`) — valid
        same-track pair spanning the event.
    *   Another track (12:21 UTC): pre-event 2026-08-16, post-event
        2026-08-28 — also a valid same-track pair.
    *   Products are `S1D` (Sentinel-1D) IW GRDH 1SDV (VV&VH), processor
        004.03. Both plain SAFE and `_COG` duplicates exist.
*   Downloads (Products({Id})/$value) are expected to require
    authentication; no credentials are present yet, so download is
    untested.

## Session log (2026-10-08, second session)

*   `.env` credentials present (values never printed). Token request with the
    officially documented format returns HTTP 400
    `invalid_grant: Invalid user credentials` — credentials rejected
    (typo or 2FA account). BLOCKED; asked the user (KNOWN_ISSUES K2).
*   Python 3.14.4 stack VERIFIED (D26): rasterio 1.5.2 (bundled GDAL 3.12.2,
    PROJ 9.8.1), torch 2.14.1+cpu, torchvision 0.29.1+cpu (must come from
    the pytorch CPU index together; mixing builds breaks C++ ops),
    segmentation-models-pytorch 0.5.0 with a real U-Net forward pass
    (6 input channels, 3 classes). No Python version change needed.
*   Bbox area answer (real computation): proposed bbox
    [84.39, 27.68, 85.73, 28.31] = 9,204 km^2 (132.9 x 71.9 km). The 1-km
    buffered named-river corridor is only 417 km^2 (4.5% of the bbox);
    the minimum rotated rectangle around the river network is 145.3 x 96.4
    km = 4,392 km^2 (the L-shaped Trishuli+Bhote Koshi network inflates any
    single rectangle); a two-box split saves only ~2% (8,989 km^2 total).
*   Radar path reconnaissance (D27): CDSE openEO backend supports
    `sar_backscatter` with elevation_model / local_incidence_angle /
    noise_removal (server-side DEM-based terrain correction), plus
    `SENTINEL1_GRD` and `COPERNICUS_30` collections. Testing on the real
    pair once credentials work; ESA SNAP headless is the fallback.

## Measured timings

None yet (no stage implemented).

## User decisions (2026-10-08) and remaining open questions

Answered by the user:

1.  GPU/training: no GPU; CPU-only training with a documented subset when
    the time comes. User will enlarge the EBS disk to 80+ GB when the agent
    asks (agent must ask before large downloads such as Kuro Siwo).
2.  CDSE credentials: the user will add them to `.env` on this machine
    themselves. The agent must never print their values.
3.  LLM: template-only assistant for now (no API key needed).
4.  License: MIT (LICENSE file created).
5.  git: do NOT run `git init` or commit; the user commits manually.
6.  Nepali reviewer: none yet; keep the native-speaker review note on all
    Nepali output.
7.  OSM source: investigate the ohsome geometry error first (show the
    exact error, try small tiles); if still blocked, use the Geofabrik
    history file filtered to edits on or before 2026-07-27, nothing later;
    log in DECISIONS.md.
8.  Trishuli bbox: the agent derives it from river geometry (NOT from
    EMSR927) and shows it to the user for approval.

Still open:

1.  Kuro Siwo download location and the CPU-friendly training subset
    definition (Phase 7; needs the enlarged disk).
2.  Time remaining before the hackathon / priority when time runs short.
3.  Repository URL for the deliverable (user handles commits manually).
