# FloodSight

Satellite flood intelligence for rescue teams. Give FloodSight an area and a
flood date. It uses Sentinel radar and optical imagery to answer three
questions:

1.  **Where did the flood hit?** Flooded and debris-covered areas.
1.  **What was damaged?** Buildings, roads and bridges from OpenStreetMap
    that fall inside the flood extent.
1.  **Who is cut off?** Settlements that lost their road connection to the
    nearest town or hospital.

Built for Track B (Mapping Flood Damage from Space) of the Multimodal AI
Hackathon 2026. The case study is the August 2026 Trishuli flood in Nepal,
checked against Copernicus EMS activation EMSR927.

> **Educational prototype.** FloodSight is not an operational tool. It maps
> the aftermath of a flood from satellite data. It cannot warn about an
> event in advance, and its outputs are estimates. See
> [Limitations](#limitations).

[TOC]

## Status

Update this list as the project progresses.

*   [x] Data acquisition and same-track orbit pairing (verified on real Trishuli pairs; downloads via CDSE authenticated)
*   [ ] Sentinel-1 and Sentinel-2 preprocessing (S1 done via SNAP headless; S2 optical NOT implemented - monsoon cloud made it unusable for the case study)
*   [x] Radar change-detection baseline (log-ratio + Otsu, live-verified; honest 0 standing water for the case-study dates)
*   [x] U-Net training on Kuro Siwo (CPU subset, event-wise splits; held-out flood IoU 0.630 vs baseline 0.554)
*   [x] Debris detection and fusion (evidence layer live: 1.68 km2 on the case study; model stream + agreement warning; optical confirmation not implemented)
*   [x] Damage assessment (buildings, roads, bridges) - 9/127 buildings, 2.95 km roads, 2/373 bridges on the case study
*   [x] Cut-off settlement analysis (16 likely / 7 possibly cut, 260 connected, 59 already isolated; synthetic bridge/loop/hamlet tests)
*   [x] Flood path tracing (bonus) (terrain-based corridor from a real upstream point; verified live)
*   [x] Dashboard (Streamlit + Folium; starts real runs; Playwright-verified)
*   [x] One-page situation report (English and Nepali) (template-only, works with no LLM; numbers validator-checked)
*   [x] Trishuli case study and EMSR927 comparison (one-shot comparison completed after the code freeze: evaluation/results/emsr927/metrics.json; reference acquired via the public dashboard API)
*   [ ] Report, demo video and final attributions

## How it works

```text
(bounding box, flood date)
          |
          v
 1. Acquire      Sentinel-1 pair (same orbit track), Sentinel-2, DEM,
                 OSM snapshot dated before the event
          |
          v
 2. Preprocess   radar calibration + terrain correction, cloud masking,
                 slope and height above drainage, shadow/layover masks
          |
          v
 3. Detect       U-Net flood model + log-ratio baseline + debris evidence
                 --> fused flood and debris map with confidence
          |
          +----------------------+----------------------+
          v                      v                      v
 4a. Damage              4b. Cut-off             4c. Flood path
     OSM overlay             road-graph              DEM flow trace
     (buildings,             connectivity            (bonus)
      roads, bridges)        before vs. after
          |                      |                      |
          +----------------------+----------------------+
                                 v
 5. Results store (single source of truth, with data provenance)
                                 |
                                 v
 6. Dashboard, one-page situation report (EN/NE), grounded assistant
```

Full design, evaluation plan and risks are in
[ARCHITECTURE.md](ARCHITECTURE.md).

### Where the AI is

*   **Flood segmentation model (declared AI component).** A U-Net with a
    pretrained encoder, trained on Kuro Siwo. Input channels are pre- and
    post-event radar (VV, VH), their change, slope and height above drainage.
    Output classes are dry, permanent water and new flood water.
*   **Change-detection baseline.** A classical log-ratio method used as a
    sanity check and a fallback.
*   **Debris detection.** Evidence-based rather than a trained class,
    because the training data has no debris labels.
*   **Situation-report assistant.** Writes the report and answers questions in
    English and Nepali. Numbers come only from the results store; a validator
    rejects any number that is not in it.

### Why several data types

| Data | What it contributes |
|---|---|
| Sentinel-1 radar | Sees through monsoon cloud, main flood signal |
| Sentinel-2 optical | Confirms water and shows surface change when the sky is clear |
| Copernicus DEM | Rules out impossible flood pixels, flow path, terrain correction |
| OpenStreetMap | Turns flooded pixels into buildings, roads, bridges, settlements |

## Data rules

These rules come from the hackathon brief. Breaking them leads to
disqualification, so the code enforces them.

**Allowed as pipeline inputs:** Sentinel-1, Sentinel-2, Copernicus DEM,
OpenStreetMap as it was before the event, and the listed training datasets.

**Not allowed as inputs:** Copernicus EMS, UNOSAT or any other published
damage maps, and any OpenStreetMap edit made after the event. These are used
only to check results.

How the repository enforces this:

*   The OSM snapshot date is set in config and checked at startup. The run
    stops if it is not earlier than the flood date.
*   Reference maps (EMSR927) are loaded only by the `evaluation/` package.
    The `src/` package must never import from `evaluation/` or read the
    reference folder. `tests/test_data_rules.py` checks this.
*   Every run writes a provenance file listing the scenes, dates, orbit
    numbers and OSM snapshot date it used.

## Repository layout

```text
floodsight/
├── README.md
├── ARCHITECTURE.md              # design, evaluation plan, limitations
├── LICENSE
├── pyproject.toml               # package metadata and CLI entry point
├── environment.yml              # conda environment (GDAL-friendly)
├── requirements.txt             # pip fallback
├── .env.example                 # template for credentials (copy to .env)
├── .gitignore
│
├── config/
│   ├── default.yaml             # thresholds, tile size, snapshot date, paths
│   ├── training.yaml            # model and training settings
│   └── regions/
│       └── trishuli.yaml        # case-study bounding box and dates
│
├── src/floodsight/
│   ├── cli.py                   # command-line entry point
│   ├── pipeline.py              # orchestrator, cached resumable stages
│   ├── config.py                # config loading and validation
│   ├── provenance.py            # records inputs used for every run
│   ├── acquisition/
│   │   ├── cdse_client.py       # Copernicus Data Space search and download
│   │   ├── orbit_pairing.py     # same-track pre/post scene selection
│   │   ├── dem.py               # Copernicus DEM tiles
│   │   └── osm_snapshot.py      # dated OSM data (ohsome / pinned query)
│   ├── preprocessing/
│   │   ├── s1.py                # calibration, terrain correction, dB
│   │   ├── s2.py                # cloud masking, water and change indices
│   │   ├── dem_derivatives.py   # slope, height above drainage
│   │   └── masks.py             # shadow, layover, no-data masks
│   ├── detection/
│   │   ├── model.py             # U-Net definition and inference
│   │   ├── tiling.py            # overlapping tile inference
│   │   ├── baseline.py          # log-ratio change detection
│   │   ├── debris.py            # evidence-based debris/change layer
│   │   └── fusion.py            # combine streams, clean up, confidence
│   ├── analysis/
│   │   ├── damage.py            # buildings, roads, bridges vs. flood
│   │   ├── cutoff.py            # road graph, baseline vs. post-flood
│   │   └── flood_path.py        # DEM flow trace and settlements (bonus)
│   ├── reporting/
│   │   ├── results_store.py     # structured results, source of truth
│   │   ├── sitrep.py            # one-page report generator
│   │   ├── assistant.py         # grounded question answering
│   │   ├── validator.py         # rejects numbers not in the store
│   │   ├── glossary_ne.yaml     # fixed Nepali terminology
│   │   └── templates/           # report templates (EN, NE)
│   └── dashboard/
│       └── app.py               # map dashboard
│
├── training/
│   ├── prepare_kurosiwo.py      # build train/val/test splits by event
│   ├── train.py                 # training loop
│   └── export_checkpoint.py     # package best weights with metadata
│
├── evaluation/                  # the ONLY place reference data is read
│   ├── segmentation.py          # IoU, F1 on held-out events
│   ├── emsr927.py               # Trishuli vs. EMSR927 comparison
│   ├── cutoff_review.py         # sensitivity and manual review helpers
│   ├── assistant_tests.py       # question set and number-grounding checks
│   └── reference/               # EMSR927 files (gitignored)
│
├── tests/
│   ├── test_data_rules.py       # snapshot date and import isolation
│   ├── test_orbit_pairing.py
│   ├── test_damage.py
│   ├── test_cutoff.py
│   └── test_validator.py
│
├── scripts/                     # setup and download helpers
├── notebooks/                   # exploration only, never imported by src/
├── docs/
│   ├── figures/                 # diagrams, screenshots
│   └── report/                  # 6-page report sources
│
├── data/                        # gitignored, see "File management"
└── runs/                        # gitignored, one folder per analysis run
```

## File management

### What goes in Git and what does not

| In Git | Not in Git (gitignored) |
|---|---|
| Source code, config, tests, docs | `data/` (downloaded imagery, DEM, OSM, datasets) |
| `.env.example` | `.env` (real credentials) |
| Small figures for the README and report | `runs/` (analysis outputs) |
| Metadata about the model checkpoint | Model weights (`*.pt`) |
| | `evaluation/reference/` (EMSR927 files) |

Model weights are large. Publish them as a release asset or a shared drive
link and document the download in `scripts/`. Do not commit them.

### Data folder

```text
data/
├── raw/            # untouched downloads
│   ├── sentinel1/
│   ├── sentinel2/
│   └── dem/
├── osm/            # dated OSM snapshots, named by snapshot date
├── training/       # Kuro Siwo and Sen1Floods11
├── interim/        # preprocessed rasters, keyed by scene ID
└── cache/          # stage caches, keyed by area + dates + code version
```

Rules:

*   `raw/` is never edited. Everything else can be rebuilt from it.
*   Cache keys include the bounding box, dates and software version, so a
    changed setting never reuses stale results.
*   Keep `training/` separate from analysis data so training samples never
    leak into evaluation.

### Run folders

Each analysis creates `runs/<run_id>/`, where `run_id` combines the area,
flood date and orbit track.

```text
runs/<run_id>/
├── results.json         # all figures and lists (source of truth)
├── provenance.json      # scenes, dates, orbit numbers, OSM snapshot date
├── warnings.json        # missing pair, cloud cover, sparse OSM, etc.
├── flood_water.tif      # flood probability and class
├── debris.tif           # probable debris / change
├── confidence.tif
├── flood.geojson
├── damage.gpkg          # buildings, roads, bridges with status
├── cutoff.gpkg          # settlements with connectivity status
├── flood_path.geojson   # bonus
├── map_preview.png
├── sitrep_en.pdf
└── sitrep_ne.pdf
```

Runs are self-contained, so the dashboard, report and assistant can open any
past run without recomputing.

### Naming and conventions

*   Python code follows PEP 8 with type hints and docstrings. Format with
    `ruff` and `black` before committing.
*   Rasters are GeoTIFF, vectors are GeoPackage or GeoJSON, in EPSG:4326 for
    exchange. Area and length calculations use a local UTM projection.
*   Branches: `main` is always runnable. Use `feature/<topic>` branches and
    merge by pull request. Commit messages are short and in the imperative.
*   Never commit credentials, API keys or reference maps.

## Prerequisites

*   Python 3.10 or newer (developed and install-tested on 3.10.12)
*   A free Copernicus Data Space account
    ([register](https://dataspace.copernicus.eu))
*   Conda or Mamba (recommended, because geospatial libraries such as GDAL are
    easiest to install this way)
*   A CUDA GPU for training (recommended). Inference on small areas works on
    CPU.
*   Optional: ESA SNAP, used as the fallback radar processing path
*   Optional: an Anthropic API key for the situation-report assistant. Without
    it, reports are produced from templates only.
*   Disk space: plan for tens of GB for training data and downloaded scenes.

## Setup

```bash
git clone <your-repo-url> floodsight
cd floodsight

conda env create -f environment.yml
conda activate floodsight
pip install -e .

cp .env.example .env
# edit .env with your credentials (see below)
```

### Environment variables

| Variable | Required | Purpose |
|---|---|---|
| `CDSE_USERNAME` | Yes | Copernicus Data Space login |
| `CDSE_PASSWORD` | Yes | Copernicus Data Space password |
| `ANTHROPIC_API_KEY` | No | Situation-report assistant |
| `SNAP_GPT_PATH` | No | Path to SNAP's `gpt`, for the fallback path |
| `FLOODSIGHT_DATA_DIR` | No | Override the default `data/` location |

### Model weights and training data

1.  Place the trained checkpoint at
    `src/floodsight/detection/checkpoints/best_model.pt`, or train your own
    (see [Training](#training)).
1.  Download Kuro Siwo by following the instructions in its
    [repository](https://github.com/Orion-AI-Lab/KuroSiwo) and place it in
    `data/training/`. Sen1Floods11 is optional
    ([repository](https://github.com/cloudtostreet/Sen1Floods11)).

## Usage

### One-command launcher (recommended)

```bash
./run.sh                  # environment checks + dashboard on http://localhost:8520
./run.sh --port 8501      # different port
./run.sh --run-case-study # also run the Trishuli fast-mode case study first (~30 min;
                          # cached stages are skipped automatically)
```

The launcher verifies the environment (imports, credentials, ESA SNAP, model
checkpoint, completed runs) with honest warnings, then starts the dashboard.
Stop it with Ctrl-C. Demo walkthrough: `docs/demo_script.md`.

### CLI

The commands below describe the intended command-line interface. Adjust them
to match the final implementation.

### Run an analysis

```bash
floodsight run \
    --bbox <lon_min> <lat_min> <lon_max> <lat_max> \
    --date 2026-08-26 \
    --mode full
```

*   `--mode fast`: radar and the trained model only.
*   `--mode full`: adds optical confirmation, debris layer and flood path.
*   Add `--upstream <lon> <lat>` to trace the flood path from a point.
Run the Trishuli case study with its preset (check the bounding box in
`config/regions/trishuli.yaml` before using it):

```bash
floodsight run --region trishuli
```

### Open the dashboard

```bash
floodsight dashboard
```

Enter an area and date, or open an existing run from `runs/`.

### Generate the situation report

```bash
floodsight report --run <run_id> --lang en,ne
```

### Training
	
```bash
floodsight train --config config/training.yaml
```

Splits are made by whole events and regions, not by random tiles, so the
validation score reflects scenes the model has never seen.

### Evaluation

```bash
# held-out events
floodsight evaluate segmentation --checkpoint <path>

## For judges: what to download and run

The trained model checkpoint is NOT in the git repository (*.pt is
gitignored). Download it from the GitHub release and place it where the
pipeline expects it:

    # after cloning:
    mkdir -p src/floodsight/detection/checkpoints
    # download best_model.pt (57 MB) + metrics.json from the release page:
    #   https://github.com/<org>/<repo>/releases/tag/v1-model
    # into src/floodsight/detection/checkpoints/
    uv sync --extra dashboard
    cp .env.example .env   # add your Copernicus Data Space credentials

    # quick demo (single pair, labelled monsoon-baseline change):
    .venv/bin/floodsight run --bbox <lon_min lat_min lon_max lat_max> \
        --date <YYYY-MM-DD> --mode fast --judge

    # full attribution demo (5-scene pre-event stack, event-attributed
    # layer; about 45 minutes on a small area, see evaluation/results/
    # v7_timing_table.json): run the quick mode first, then the
    # attribution scripts under evaluation/ as documented in DECISIONS D66.

    .venv/bin/floodsight dashboard   # dark UI, satellite basemap, port 8520

What works without any download: opening one of the bundled run folders
(runs/ is gitignored too - the three corridor runs are re-creatable with
the commands above; their numbers are recorded in DECISIONS.md).

Model weights hosting plan (V8): GitHub Release "v1-model" with
best_model.pt + metrics.json (57 MB, well under GitHub's 2 GB asset
limit); a second asset for the v2 ImageNet-pretrained checkpoint if it
outperforms v1. No remote push is performed by the agent (the user
supplies the repository URL).

# Judge mode: refuse areas over the configured budget before any download
#   floodsight run --bbox <lon_min lat_min lon_max lat_max> --date <YYYY-MM-DD> --mode fast --judge
# (config judge.max_area_km2; verified fresh 20x20 km run: ~23 min end to
#  end including scene downloads - see DECISIONS D67)

# Trishuli vs. EMSR927 (reference data used for checking only)
floodsight evaluate emsr927 --run <run_id> \
    --reference evaluation/reference/emsr927/

# assistant: question set and number-grounding check
floodsight evaluate assistant --run <run_id>
```

### Tests

```bash
pytest
```

## Outputs

*   **Dashboard:** layered map (flood water, debris, damaged buildings, roads
    and bridges, cut-off settlements, flood path), summary cards with
    confidence ranges, and a status panel showing which scenes were used and
    any warnings.
*   **Situation report:** one page, English and Nepali, generated from the
    results store.
*   **Run folder:** all rasters, vectors and tables in `runs/<run_id>/`.

## Results

Fill this in after evaluation. Report real numbers only.

| Test | Metric | Value |
|---|---|---|
| Held-out events (6 unseen flood events, Kuro Siwo test shard subset) | Flood IoU / F1 (U-Net) | 0.630 / 0.773 (precision 0.981, recall 0.638) |
| Held-out events | Flood IoU / F1 (dark-water baseline) | 0.554 / 0.713 (precision 0.817, recall 0.632) |
| Terrain split | steep-slope pixels in the held-out subset | ~0 valid pixels (all test pixels slope <= 10 deg); recorded, not glossed |
| Trishuli case study (real run) | standing flood water on post-event scenes (+2 d / +5 d) | 0.0 km2 (receded; honest zero, DECISIONS D32) |
| Trishuli case study | probable debris / change | 1.6804 km2 (603 polygons; evidence layer) |
| Trishuli case study | buildings likely / possibly hit | 9 / 127 of 186,869 |
| Trishuli case study | cut-off settlements | 16 likely / 7 possibly / 260 connected / 59 already isolated |
| Assistant test set | ungrounded numbers | 0 (all answers pass the validator) |

Training details (all real, from `src/floodsight/detection/checkpoints/metrics.json`):
CPU-only, 15 epochs x ~70 s on 8 vCPU, 1495 training samples from 19
events (event-wise splits; Kuro Siwo webdataset subset, DECISIONS D35),
best validation flood IoU 0.3229.

## Limitations

The full list is in [ARCHITECTURE.md](ARCHITECTURE.md#limitations). The
essentials:

*   **No early warning.** Sentinel satellites revisit every few days. The
    system maps the aftermath and cannot warn of a glacier collapse minutes
    ahead.
*   **Timing gap.** The first usable post-event image may arrive days after
    the event, so the map shows the state on that date, not the peak.
*   **Same-track rule.** Radar images from different orbit tracks are never
    compared, so some dates have no valid pair.
*   **Terrain and cloud.** Narrow Himalayan gorges are affected by radar
    shadow and layover, and monsoon cloud often blocks optical imagery.
*   **Ice and debris confusion.** Glacier ice, wet snow and mud can resemble
    water or dry land to radar.
*   **Debris is not a trained class.** The training data has no debris
    labels, so the debris layer is evidence-based.
*   **OSM gaps.** Remote villages, tracks and bridges are often unmapped, so
    cut-off results are a lower bound. Population data is unreliable.
*   **Estimates, not confirmation.** A building inside the flood extent is
    "likely affected", not "destroyed". Cut-off status assumes a flooded road
    is impassable.
*   **Flood path is terrain-based.** It is not a hydraulic simulation.
*   **Assistant.** It can only report what is in the results store. Nepali
    output should be reviewed by a native speaker before any real use.

## Responsible use

This disaster is recent and many families are still affected. Do not use
images of victims in demos or reports. FloodSight is an educational
prototype and must not replace official sources such as national authorities
and the Copernicus Emergency Management Service.

## Troubleshooting

| Problem | What to try |
|---|---|
| "No valid same-track pair" | Widen the date window, or choose another area or date. The tool will not compare different tracks. |
| Download is slow or fails | Check Copernicus account quota, retry, and use cached data for the demo. |
| GDAL or rasterio import errors | Use the conda environment instead of plain pip. |
| Run stops at the OSM step | The snapshot date must be earlier than the flood date. Check `config/default.yaml`. |
| Few or no settlements found | OSM coverage may be sparse in that area. Check the warnings in the run folder. |
| Out of memory during inference | Reduce tile size or batch size in `config/default.yaml`. |

## Attribution and citations

Required on every output of this project:

*   Contains modified Copernicus Sentinel data 2026.
*   Produced using Copernicus WorldDEM-30 (c) DLR e.V. 2010-2014 and (c)
    Airbus Defence and Space GmbH 2014-2018 provided under COPERNICUS by the
    European Union and ESA; all rights reserved.
*   (c) OpenStreetMap contributors. OpenStreetMap data is available under the
    Open Database License (ODbL). Any published modified version of the OSM
    data must stay under ODbL.
*   Copernicus EMS data (EMSR927), used only for checking: "European Union,
    Copernicus Emergency Management Service data".

Training data citations:

*   Bountos, N. I. et al. (2024). *Kuro Siwo: 33 billion m² under the water!
    A global multi-temporal satellite dataset for rapid flood mapping.*
    NeurIPS 2024 (Datasets and Benchmarks).
*   Bonafilia, D. et al. (2020). *Sen1Floods11: a georeferenced dataset to
    train and test deep learning flood algorithms for Sentinel-1.* CVPR
    Workshops. (Only if used.)

Please verify the exact titles and details against the original papers
before final submission.

## Acknowledgements

*   Copernicus Data Space Ecosystem for Sentinel and DEM data
*   ohsome API (HeiGIT) for dated OpenStreetMap data
*   The authors of Kuro Siwo and Sen1Floods11

## License

Add a license for the code (for example MIT) in `LICENSE`. Data and model
outputs inherit the licenses of their sources listed above.

## See also

*   [ARCHITECTURE.md](ARCHITECTURE.md)
*   [Copernicus Data Space](https://dataspace.copernicus.eu)
*   [ohsome API](https://api.ohsome.org/v1)
*   [Copernicus EMS mapping](https://mapping.emergency.copernicus.eu)
