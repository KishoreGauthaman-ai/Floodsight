# FloodSight — Report (max 6 pages)

All numbers in this report are real, computed by the system; every figure
cites its source file. [Sections marked (FILL) are completed only with
measured data that exists at submission time.]

## 1. Summary

FloodSight answers three questions for rescue teams from free satellite
data: where did the flood hit, what was damaged, who is cut off. Given a
bounding box and a flood date, the pipeline runs end to end: same-orbit-track
Sentinel-1 pairing, ESA SNAP terrain-corrected preprocessing, a trained
U-Net plus a classical baseline, an evidence-based debris layer, damage
estimation against a pre-event OpenStreetMap snapshot, and a two-scenario
cut-off analysis with detour lengths. The case study is the August 2026
Trishuli flood (Nepal), checked against Copernicus EMS EMSR927 (evaluation
module only).

## 2. System

Seven layers (ARCHITECTURE.md): input/orchestration, acquisition,
preprocessing, detection (AI core), damage + cut-off analysis, results
store, presentation (dashboard + one-page bilingual sitrep + grounded
assistant). Radar processing uses ESA SNAP 14 headless (verified on the
real scenes; openEO rejected for live-demo fragility). OSM data comes from
a dated Geofabrik snapshot (2026-07-01 state), asserted at startup to be
strictly earlier than the flood date and no later than the brief's
2026-07-27 cap. Reference data (EMSR927) is read only by the evaluation
package; an automated test enforces that the pipeline never imports it.

## 3. AI component (25% criterion)

Declared component: the flood segmentation U-Net. Trained on a documented
Kuro Siwo subset (event-wise splits; 19 train / 5 validation / 6 test
events; 1495 training patches), CPU-only (15 epochs x ~70 s).

| Held-out events (6 unseen events) | IoU | F1 | Precision | Recall |
|---|---|---|---|---|
| U-Net (ours) | 0.630 | 0.773 | 0.981 | 0.638 |
| Dark-water baseline | 0.554 | 0.713 | 0.817 | 0.632 |

Source: evaluation/results/segmentation_metrics.json (real run output).
Terrain split: all test pixels are slope <= 10 deg in this subset; the
steep-terrain slice is effectively empty — recorded, not glossed. Training
stability: validation IoU oscillated (best 0.3229, epoch 7); the best
checkpoint is used and the instability is reported (KNOWN_ISSUES K18).

## 4. Trishuli case study (real run)

Run `runs/85.000_27.800_85.600_28.250_2026-08-26_fast` (fast mode, track
121 DESCENDING, pre 2026-08-19 / post 2026-08-31, OSM snapshot 2026-07-01):

- Standing flood water: **0.0 km2** — an honest zero. Both usable
  post-event scenes (+2 d and +5 d) show the flood already receded; the
  trained model independently agrees (100% baseline agreement).
- Probable debris / surface change: **1.6804 km2** (603 polygons) —
  brightening > 3 dB on land, gentle slope, within 1 km of mapped
  waterways (valley-floor mask).
- Damage: **9 likely / 127 possibly hit buildings** of 186,869 mapped;
  **2.95 km of roads** inside the extent; **2 of 373 bridges** affected.
- Cut-off: **16 likely cut / 7 possibly cut / 260 still connected**;
  59 settlements were already isolated before the event (reported
  separately, never blamed on the flood).

## 5. EMSR927 comparison (checking only)

The comparison script is ready (evaluation/emsr927.py). The EMS portal
requires an account login for vector downloads; once the reference package
is placed in evaluation/reference/emsr927/, the script reports flood-extent
IoU, recall of the reference and per-layer overlaps, and every large
disagreement is explained (different acquisition dates, different mapping
rules). [FILL when the reference is available.]

## 6. Limitations (measured evidence)

- No early warning: Sentinel revisit is days; the map shows the state on
  the post-event date, not the peak. Measured: our +2 d and +5 d scenes
  hold zero standing water.
- ~69% of pixels in the case-study area are physically unreliable for
  radar change detection (steep slopes, layover/shadow) — valid_pixel_share
  0.3073 in detection.json.
- Debris is not a trained class; the evidence layer is labelled
  "probable debris / change".
- OSM completeness: 59 settlements were already unreachable before the
  event; unmapped hamlets cannot appear, so cut-off results are a lower
  bound.
- The assistant can only report what is in the results store; Nepali
  output needs native-speaker review.
- Educational prototype, not an operational tool.

## Attribution

Contains modified Copernicus Sentinel data 2026. Produced using Copernicus
WorldDEM-30 (c) DLR e.V. 2010-2014 and (c) Airbus Defence and Space GmbH
2014-2018 provided under COPERNICUS by the European Union and ESA; all
rights reserved. (c) OpenStreetMap contributors. Kuro Siwo: Bountos et al.,
NeurIPS 2024 (Datasets and Benchmarks). EMSR927 (checking only): European
Union, Copernicus Emergency Management Service data.
