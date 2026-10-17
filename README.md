# FloodSight

Satellite flood intelligence for any area and date: **where did the
flood hit, what was damaged, who is cut off.**

> **Educational prototype - NOT an operational tool.** Outputs are
> estimates produced from Sentinel-1 radar, the Copernicus GLO-30 DEM
> and a pre-event OpenStreetMap snapshot. "Likely" and "possibly" are
> confidence tiers, never certainties. This disaster is recent and
> families are affected; the wording everywhere is factual and
> respectful.

## What it answers

1. **Where did the flood hit?** - flood water and event-attributed
   valley-floor change from same-orbit-track radar pairs and a
   pre-event temporal stack.
2. **What was damaged?** - buildings, roads and bridges from the
   pre-event snapshot that overlap the mapped layers, in confidence
   tiers.
3. **Who is cut off?** - settlements whose road connection to the
   nearest town/hospital crosses the mapped layers, with routes that
   cross radar-unreliable terrain reported as *unassessed*, never as
   "connected".

## Results snapshot (Trishuli corridor, flood 2026-08-26)

| Quantity | Value | Caveat |
|---|---|---|
| Standing flood water | 0.0 km2 | honest zero - flood receded before the +62 h scene; flood-water mapping not demonstrated on this event |
| Event-attributed change | 0.455 km2 | ratio 4.69 vs pre-event control, 3.07 vs an independent control; threshold from controls only |
| Other ground change (single pair) | 41.99 km2 | monsoon-season baseline - fires on every date tested (54.25 km2 on a pre-event pair) |
| Analysed area | 55.9% of bbox | 43.85% of the corridor is slope >30 deg and masked as radar-unreliable; raw footprint covers 99.9% |
| Buildings hit (centroid in event layer) | 30 | 82 touch with a footprint edge (8 likely / 74 possible) |
| Roads inside event layer | 1.28 km | old layer: 107.63 km |
| Bridges with approach inside zone | 19 | no bridge structure observed damaged |
| Settlements possibly cut | 106 | 0 likely cut is NOT "nobody cut off"; 483 further settlements are unassessed (routes cross masked terrain) |
| Held-out model IoU | 0.63 (test) / 0.37 (validation) | validation is worse than the classical baseline there; the kept checkpoint's test score is an optimistic selection |

## Quick start for judges

### USE THIS LINK INSTEAD TO SEE VIEW THE DASHBOARD:

it would take 2 mins to load the website..!!
```bash
https://sanyo-terrorist-fax-august.trycloudflare.com
```
### OR 

```bash
./run.sh
```

### OR

```bash
# quick mode: ONE pre/post radar pair, ~14 min on a small box,
# labelled "monsoon-baseline change - NOT event-attributed"
.venv/bin/floodsight run --bbox 84.9041 27.8035 85.1105 27.9869 \
    --date 2026-08-26 --mode fast --judge
```

Measured on that exact 415 km2 judge box: downloads + SNAP
co-registration 22.8 min when uncached (2.8 min once cached), detection
17 s, damage 11 s, cut-off 5 s (D67). Full event attribution (five
pre-event scenes + control-derived threshold) needs the temporal stack
and takes ~46 min (D79) - it is a separate mode, honestly labelled.
`--judge` refuses areas above 500 km2 before any download starts.

Open the dashboard (port 8520) and pick any run:

```bash
.venv/bin/floodsight dashboard
```

## How it works

```
bbox + flood date
  -> validate (OSM snapshot strictly pre-event, area budget)
  -> search Copernicus Data Space, pair SAME relative orbit + direction
  -> SNAP: orbit, thermal noise, calibration, Lee filter, 10 m TC
  -> co-register pairs; union multi-slice passes on one grid
  -> detect: U-Net + log-ratio baseline + temporal-stack attribution
  -> damage: intersect layers with pre-event OSM features (tiers)
  -> cut-off: road-graph connectivity, unassessed category, exposure
  -> results store (schema-validated) -> dashboard / sitreps / assistant
```

1. Input validation refuses bad boxes, post-event OSM and huge areas.
2. Same-track pairing is a hard rule; no cross-track comparison ever.
3. Preprocessing caches per scene pair (co-registration included), so
   cached areas re-open in minutes, not hours.
4. Detection unions slices per track; the analysed area is clipped to
   radar-reliable terrain and every statistic is computed inside it.
5. Damage tiers use mapped-layer overlap; bridges are flagged when
   their approach is inside a flagged zone (approach-buffer rule).
6. Cut-off keeps baseline connectivity (already-isolated before the
   event), two flood scenarios, and *unassessed* for routes crossing
   masked terrain (D77).
7. Everything lands in one run folder; the dashboard, reports and
   assistant read only that store.

## Installation (Ubuntu, as actually used)

- Ubuntu 26.04, 8 vCPU, 30 GB RAM, no GPU. Python 3.14 via `uv`
  (`uv 0.12.23`); `uv sync --extra dashboard --extra dev`.
- ESA SNAP 14.0.0 headless at `/home/ubuntu/snap`; set `SNAP_GPT_PATH`
  in `.env` (see `.env.example`). Java comes with SNAP.
- Credentials in `.env` (never committed): `CDSE_USERNAME`,
  `CDSE_PASSWORD` for https://dataspace.copernicus.eu.
- Model weights are NOT in git (`*.pt` is ignored). Download the v1
  checkpoint (57 MB, SHA-256
  `cb91fa6321fa0b48b7c6183ef646656f4735ffa096ce1087da3d49a213cbbd37`)
  plus `metrics.json` from the GitHub Release
  `<RELEASE_URL_PLACEHOLDER>` into
  `src/floodsight/detection/checkpoints/`.
- Data lands under `data/` automatically (S1 zips, GLO-30 tiles, the
  dated Geofabrik OSM snapshot `nepal-260701.osm.pbf` for Nepal; other
  regions need their own dated snapshot - see KNOWN_ISSUES K9b).

## CLI reference (from `floodsight --help`)

| Command | Purpose | Key flags | Example | Runtime (measured) |
|---|---|---|---|---|
| `run` | full analysis for an area + date | `--bbox` or `--region`, `--date`, `--mode fast/full`, `--upstream LON LAT`, `--judge` | `floodsight run --region trishuli` | quick mode ~14 min fresh (415 km2); corridor (9,237 km2) ~104 min |
| `dashboard` | map UI | none | `floodsight dashboard` | starts in ~10 s |
| `report` | one-page sitrep (en, ne) | `--run RUN`, `--lang en,ne` | `floodsight report --run <id>` | seconds |
| `train` | CLI stub - training runs via `training/train.py` (see below) | `--config` | not via CLI | 15 epochs ~73 s/epoch on 8 vCPU |
| `evaluate` | CLI stub - evaluation runs via `evaluation/*.py` scripts | `--checkpoint`, `--run`, `--reference` | not via CLI | segmentation eval ~2 min |

`run` exits 0 on success, 2 on input/config errors, 3 for unimplemented
stages; a failed stage stops the run with a visible error and dependent
stages are skipped with reasons in `stage_status.json`.

## Dashboard guide

- **Inputs (sidebar):** preset region or custom bbox, flood date, mode;
  "Run analysis" launches the CLI detached with live stage chips.
- **Layers:** event-attributed debris (temporal stack) and other ground
  change (monsoon baseline, relabelled); flood water (baseline + model);
  buildings/roads/bridges hit; settlement statuses; flood-path corridor;
  analysis bbox and analysed-area outline. Satellite imagery (Esri
  World Imagery, no API key) with an Esri labels overlay.
- **Legend:** chip row under the map (same colours as the layers).
- **Panels:** metric cards (water, buildings, roads, cut-off with the
  "0L is not nobody" help text), quality badges (analysed area, raw
  radar coverage, event/control ratio, event-attributed km2), the v2
  steep-terrain panel (connected / possibly cut / unassessed) and the
  terrain-exposure panel labelled NOT observed damage, provenance and
  warnings.

## Run-folder files

| File | Purpose |
|---|---|
| `results.json` | schema-validated store: every number the UI/reports show |
| `provenance.json` | scenes, dates, orbit, OSM snapshot date, model version |
| `stage_status.json`, `stage_timings.json` | live progress + per-stage seconds |
| `warnings.json` | machine-readable warnings (partial coverage, sparse OSM, ...) |
| `flood_water.tif`, `flood.geojson` | baseline flood-water raster + vectors |
| `model_flood.geojson` | U-Net water layer |
| `event_debris.geojson` | event-attributed change (temporal stack, tiers) |
| `debris.geojson` | other ground change (monsoon baseline, relabelled) |
| `analysed_area.geojson` | radar-reliable area outline |
| `damage.gpkg`, `damage.json` | per-feature damage rows + summary |
| `cutoff.json`, `cutoff_v2.json` | v1 cut-off + v2 with the unassessed category |
| `terrain_exposure.json` | exposure scenario (NOT observed damage) |
| `flood_path.geojson`, `flood_path.json` | traced hazard corridor + settlements along it |
| `sitrep_en/ne.md`, `sitrep_en/ne.pdf` | one-page situation reports |
| `osm/` | pre-event snapshot extracts (buildings, roads, bridges, settlements, destinations, waterways) |

## Repository layout

```
config/                  thresholds, region presets (no magic numbers in code)
src/floodsight/
  cli.py                  thin command entry
  pipeline.py             stage orchestration, caching, resumability
  config.py               config + .env loading and validation
  provenance.py           run provenance
  acquisition/             CDSE client, same-track pairing, DEM, OSM snapshot
  preprocessing/          SNAP graphs (s1.py), DEM derivatives (HAND, slope)
  detection/              U-Net, log-ratio baseline, debris, temporal stack
  analysis/               damage, cut-off, flood path
  reporting/              results store, sitreps, validator, assistant
  dashboard/app.py        Streamlit UI (satellite basemap)
training/                 Kuro Siwo prep + CPU training loop
evaluation/               every validation script + results/ (json evidence)
scripts/                  setup, stack prep, controls, measurements
tests/                    104 offline tests incl. data-rules enforcement
docs/                     figures, DESIGN.md, demo script, report bundle
runs/                     one folder per analysis (gitignored)
data/                     downloads + caches (gitignored)
```

## Data rules and compliance

- **Used:** Sentinel-1/2 (Copernicus Data Space), Copernicus GLO-30,
  pre-event OSM only (Geofabrik dated extract 2026-07-01; the snapshot
  date must be strictly earlier than the flood date - enforced at
  startup), Kuro Siwo subset for training.
- **Not used as input:** Copernicus EMS maps (EMSR927), UNOSAT or any
  published damage map. The EMSR927 vectors live only in
  `evaluation/reference/` (gitignored) and are read only by
  `evaluation/` after the code freeze, solely to check results.
- **Enforced in code:** `tests/test_data_rules.py` proves `src/` never
  imports evaluation; same-track pairing refuses cross-track
  comparisons; the OSM date check stops the run otherwise.
- Sentinel-2 was evaluated for this event and stopped (15.4% joint
  cloud-free observability, D81); it contributed nothing to the maps.

## Validation summary

- **Controls:** the single-pair layer fires on every monsoon date
  (54.25 km2 pre-event, 30.87 km2 post-recession); a 2026-10-12 run
  correctly returns "no valid same-track pair". The temporal-stack
  attribution holds against leave-one-out controls (0.116-0.200 km2)
  and an independent pair (0.148 km2): ratios 4.69 / 3.07 (D75).
- **Placebo vs EMSR927:** the non-event monsoon layers score F1
  0.56-0.59 at 100 m - HIGHER than the event layer's 0.47 - so
  reference F1 is not event-specific; only precision (0.76) separates
  event signal; random placement scores 0.03 (D74/D80).
- **EMSR927 one-shot:** the reference is mass-movement mapping, not
  water. Event-layer precision 0.66-0.76, recall 0.01-0.34 at 0-100 m;
  building-damage recall 9 of 3,807 (0.24%). No tuning after (D70).
- **AI:** U-Net v1 kept over an ImageNet v2 on test performance
  (0.63 vs 0.45); validation is worse than the baseline (0.37 vs
  0.63); no held-out event is mountainous (D63/D78).

## Limitations (from measured evidence)

- Flood-water mapping is not demonstrated on this event (0.0 km2).
- 10 m radar change does not grade per-building damage (9/3,807).
- 43.85% of the corridor is masked as unreliable; 483 settlements'
  connectivity is unassessed, not safe.
- The kept model's test score is an optimistic checkpoint selection;
  training was unstable (validation IoU oscillated).
- Himalayan generalisation is unproven (all held-out floods lowland).
- The event-attributed layer is deliberately small (high precision,
  low recall vs the reference).
- Optical confirmation was impossible in the monsoon (stopped).

## Deviations from the original plan

Sentinel-2 stopped (cloud); ohsome blocked -> Geofabrik dated extracts;
GLO-30 fetched via AWS; ResNet-18 instead of ResNet-50 (CPU budget);
template-only assistant (no LLM dependency); ESA SNAP chosen over
server-side openEO; "unassessed" cut-off category added; the
event-attributed temporal-stack layer added after controls exposed the
monsoon baseline. Full table: ARCHITECTURE.md.

## Reproducing the results

```bash
uv sync --extra dashboard --extra dev
cp .env.example .env           # add CDSE credentials, SNAP path
# weights from the GitHub Release -> src/floodsight/detection/checkpoints/
.venv/bin/floodsight run --region trishuli          # case study
.venv/bin/python -m pytest                        # 104 tests
.venv/bin/python evaluation/v1_circularity.py     # attribution stress tests
.venv/bin/python evaluation/emsr927.py --run 84.390_27.680_85.730_28.310_2026-08-26_fast
```

## Troubleshooting

- **"CDSE authentication failed"** - check credentials in `.env`; 2FA
  accounts need a non-2FA login for the password grant.
- **"no valid same-track pair"** - a real result, not an error: no
  same-orbit pre/post scenes exist in the window; widen
  `search_window_days_pre/post` in config or pick another date.
- **SNAP not found** - set `SNAP_GPT_PATH`; SNAP 14 tested.
- **Slow first run** - downloads + SNAP dominate; cached pairs skip both.
- **Dashboard map empty** - the app needs a run folder; start one or
  pick an existing run in the sidebar.

## Attributions, citations, licence, responsible use

Contains modified Copernicus Sentinel data 2026. Produced using
Copernicus WorldDEM-30 (c) DLR e.V. 2014 and (c) Airbus Defence and
Space GmbH 2014-2018 provided under COPERNICUS by the European Union
and ESA; all rights reserved. (c) OpenStreetMap contributors. Esri
World Imagery basemap (c) Esri, Maxar, Earthstar Geographics. Kuro
Siwo: Bountos et al., 2024. European Union, Copernicus Emergency
Management Service data (EMSR927, checking only). Code: MIT licence
(LICENSE).

Outputs are estimates for educational use; never the sole basis for
rescue decisions. Settlements marked *unassessed* must not be treated
as safe. No victim imagery; respectful wording throughout.
