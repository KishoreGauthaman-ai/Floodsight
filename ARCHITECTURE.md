# FloodSight: System Architecture and Plan

FloodSight turns an area of interest and a flood date into three answers for
rescue teams: where the flood hit, what infrastructure was damaged, and which
settlements are cut off. It is built for Track B of the Multimodal AI
Hackathon 2026 (Mapping Flood Damage from Space) and runs end to end from raw
Sentinel data for any area and date the judges choose.

This document contains the plan, the system architecture and the limitations.
It contains no code.

[TOC]

## Problem and scope

On 26 August 2026 a glacier collapse in northern Nepal sent water, mud and
debris down the Bhote Koshi-Trishuli corridor. Ground sensors, roads, bridges
and phone lines failed. Free satellite data is the only complete source of
information.

The system takes `(bounding box, flood date)` and must answer:

1.  **Where did the flood hit?** Flooded and debris-covered areas, from
    before/after Sentinel-1 radar and Sentinel-2 optical imagery.
1.  **What was damaged?** Pre-event OpenStreetMap buildings, roads and
    bridges intersected with the flood extent.
1.  **Who is cut off?** Settlements that lost their road connection to the
    nearest town or hospital.

Bonus: trace the flood path downstream from any upstream point using
elevation data and list the settlements along it.

Case study: map the August 2026 Trishuli flood and compare against the
Copernicus EMS activation EMSR927 (used for checking only).

## Design principles

*   **Parameterized, never hardcoded.** Every stage takes the bounding box and
    date as input. Nothing is specific to Nepal.
*   **Obey the data rules by construction.** Only Sentinel-1, Sentinel-2,
    Copernicus DEM, pre-event OpenStreetMap and the listed training datasets
    enter the pipeline. EMSR927, UNOSAT and post-event OSM edits are used only
    in the evaluation module, which is isolated from the pipeline.
*   **Measured numbers only.** Every figure shown to a user comes from a
    computed result, never from a language model's memory.
*   **Fail visibly.** When data is missing (no matching orbit pass, cloud
    cover, sparse OSM), the system says so in the output instead of producing
    a confident-looking map.
*   **Live-demo safe.** A cached run exists for the Trishuli case study, and
    the live path is built to finish within a few minutes on a small area.

## Data sources and rules

| Source | Use | Notes |
|---|---|---|
| Sentinel-1 GRD (Copernicus Data Space) | Core flood detection | Radar, sees through monsoon cloud |
| Sentinel-2 L2A (Copernicus Data Space) | Optical confirmation, debris/change | Only when cloud cover allows |
| Copernicus DEM (WorldDEM-30) | Slope, height above drainage, terrain correction, flow path | 30 m resolution |
| OpenStreetMap via ohsome API | Buildings, roads, bridges, settlements, hospitals | Snapshot dated 27 July 2026 (before the event) |
| Kuro Siwo | Primary training data | Cite Bountos et al., 2024 |
| Sen1Floods11 | Optional extra training data | Cite Bonafilia et al., CVPR Workshops 2020 |
| Copernicus EMS EMSR927 | Checking only | Never a pipeline input |

Required attribution on every output (dashboard footer, report, README):

*   "Contains modified Copernicus Sentinel data 2026."
*   "Produced using Copernicus WorldDEM-30 (c) DLR e.V. 2010-2014 and (c)
    Airbus Defence and Space GmbH 2014-2018 provided under COPERNICUS by the
    European Union and ESA; all rights reserved."
*   "(c) OpenStreetMap contributors."
*   Training dataset citations, and the EMSR927 credit line where used for
    checking.

A startup check must assert that the OSM snapshot date is earlier than the
flood date, so a wrong date can never silently break the rules.

## System architecture

The system has seven layers. Data flows downward; the results store is the
single source of truth for the dashboard, the report and the copilot.

### Layer 1: Input and orchestration

*   Input is the bounding box, flood date and an optional upstream point.
*   A job orchestrator runs the stages as cached, resumable steps keyed by the
    bounding box, dates and software version, so a re-run or a judge's repeat
    request does not redo downloads.
*   Progress is reported to the dashboard stage by stage.

### Layer 2: Satellite data acquisition

*   Query the Copernicus Data Space catalogue for Sentinel-1 GRD scenes
    covering the area within a window around the flood date.
*   **Orbit pairing is mandatory.** Group scenes by relative orbit number and
    flight direction. Select a pre-event and a post-event scene from the same
    track (same viewing geometry, normally 12 days apart). Scenes from
    different tracks are never compared pixel by pixel.
*   If no same-track pair exists, the system reports it and offers the
    nearest alternatives rather than forcing a bad comparison.
*   Query Sentinel-2 for the same period and keep only scenes with usable
    cloud-free coverage over the area.
*   Download the Copernicus DEM tiles for the area.

### Layer 3: Preprocessing

**Sentinel-1 (radar).**

*   Apply precise orbit, thermal noise removal, radiometric calibration to
    backscatter, speckle filtering, and terrain correction with the
    Copernicus DEM, then convert to decibels.
*   Terrain correction is critical in the Himalaya, where steep valley walls
    cause layover and shadow.
*   Primary path: server-side processing through the Copernicus Data Space
    processing services (cropped to the area, much faster for a live demo).
    Fallback path: the ESA SNAP graph processing tool for full-scene
    processing, slower but fully controllable.
*   Co-register the pre and post images to the same grid.
*   Compute a shadow/layover mask and slope-based exclusion mask from the DEM
    so unreliable pixels are flagged as "no data" instead of "dry".

**Sentinel-2 (optical).**

*   Cloud and shadow masking, then compute a water index and a bare-surface /
    vegetation-loss index for before/after change.
*   Used to confirm radar detections and to help identify debris and mud
    where the sky is clear.

**DEM derivatives.**

*   Slope and height above nearest drainage (a measure of how high a pixel
    sits above the river). These rule out physically impossible flood pixels
    such as steep slopes and ridges.
*   Hydrologically conditioned DEM (pits filled, flow direction, flow
    accumulation) for the flood-path bonus.

### Layer 4: Flood and debris detection (the AI core)

The flood map comes from three evidence streams combined into one result.

**Stream A: trained segmentation model.**

*   A U-Net with a pretrained encoder, taking stacked channels: pre-event and
    post-event radar (VV and VH), their difference or ratio, slope and height
    above drainage.
*   Trained on Kuro Siwo, which provides before/after Sentinel-1 pairs, DEM,
    slope and flood labels across many events and regions. Sen1Floods11 is an
    optional addition for water-only supervision.
*   Output classes: dry, permanent water, new flood water.
*   Training uses a combined focal and Dice loss because flood pixels are a
    small fraction of any scene. Augmentation includes flips, rotations and
    speckle/noise variation to improve robustness.
*   Inference runs on overlapping tiles with averaged overlaps to avoid seams
    at tile edges.

**Stream B: change-detection baseline.**

*   A classical log-ratio change method with an automatic threshold on the
    radar pair. It is cheap, needs no training, and acts as a sanity check and
    fallback when the model is uncertain or fails on a scene.

**Stream C: debris and mud detection.**

*   Kuro Siwo has no debris class, so debris cannot be claimed as a trained
    model output. It is derived from evidence: persistent backscatter change
    on land that is not open water, loss of surface vegetation in Sentinel-2
    where clear, and position within the valley floor (low height above
    drainage, gentle slope).
*   It is reported as "probable debris/change", clearly separate from "flood
    water", and its accuracy is evaluated separately and honestly.

**Fusion and cleanup.**

*   New flood water is post-event water minus pre-event (permanent) water, so
    rivers and lakes that were always there are not counted as damage.
*   Where Sentinel-2 is clear, it confirms or rejects radar detections.
*   Remove shadow/layover areas and physically impossible pixels using the DEM
    masks, remove tiny isolated blobs, and keep a per-pixel confidence.
*   Output: flood and debris rasters with confidence, and vector polygons.

### Layer 5: Damage and cut-off analysis

**Damage assessment (what was damaged).**

*   Fetch buildings, roads, bridges, hospitals and settlements from the
    ohsome API at the pre-event snapshot date.
*   Intersect with the flood and debris extent:
    *   Buildings: count and footprint area inside the flood or debris
        extent, with a "likely hit" and "possibly hit" split using
        confidence.
    *   Roads: length inside the flood or debris extent, by road class.
    *   Bridges: because a bridge normally crosses permanent water, test it
        against new flood and debris, and against the approaches on either
        side, not against the river itself.
*   Report results as estimates with confidence, never as confirmed damage.

**Cut-off analysis (who is cut off).**

*   Build the road graph from the same pre-event snapshot date (for example by
    pinning the data query to 27 July 2026). Using today's live OSM would
    include post-event edits and break the data rules.
*   Compute a **baseline**: which settlements could reach a town or hospital
    before the event. Settlements already isolated pre-event are reported
    separately, not blamed on the flood.
*   Remove or penalize road segments and bridges affected by the flood or
    debris, using the confidence level to produce two scenarios: likely cut
    (high confidence) and possibly cut (including lower-confidence segments).
*   For each settlement, test whether a path still exists to the nearest town
    or hospital, and report the detour length if one does.
*   Output: list and map of cut-off, at-risk and still-connected settlements,
    with the evidence (which segments failed).

### Layer 6: Flood path tracing (bonus)

*   Given an upstream point, snap it to the nearest drainage line on the
    conditioned DEM and trace the flow path downstream.
*   Buffer the path by a distance that grows with flow accumulation (wider
    valleys further downstream) and list the pre-event settlements inside,
    ordered by distance from the source.
*   The path is a terrain-based hazard corridor, not a hydraulic simulation,
    and is labelled that way.

### Layer 7: Presentation, report and copilot

**Results store.** All metrics, rasters, polygons, settlement lists and the
data provenance (scene IDs, dates, orbit numbers, snapshot date) are written
to one structured store.

**Dashboard.**

*   Map with toggleable layers: flood water, debris, damaged buildings, roads
    and bridges, cut-off settlements, flood path, with a before/after slider.
*   Summary cards for flooded area, buildings, road length, bridges and
    cut-off settlements, each with its confidence range.
*   Clear status panel showing which scenes were used, their dates and any
    warnings (no same-track pair, cloud cover, low OSM coverage).
*   Attribution footer on every view.

**One-page situation report.**

*   Generated from the results store, exportable as PDF.
*   Sections: summary, key figures, critical infrastructure, cut-off
    communities, data sources and dates, limitations note.
*   Available in English and Nepali.

**Situation-report assistant (supporting feature).**

*   Answers rescuers' questions and drafts the report in English and Nepali.
*   Numbers are never generated by the language model. The model calls
    defined lookup functions on the results store, or fills placeholders in a
    template, and a validator rejects any output containing a number that is
    not in the store.
*   The map image is passed to the model so it can describe spatial context,
    but the numbers still come only from the store.
*   Nepali output uses a fixed glossary for technical terms and carries a note
    that it should be reviewed by a native speaker before operational use.

## AI component choice

The hackathon asks for one AI component. The plan is:

*   **Declared component: the flood segmentation model.** It is measurable,
    can be tested on unseen Himalayan scenes, and maps directly to the 25%
    AI criterion.
*   The situation-report assistant is built as a lightweight, grounded
    feature on top of the results store. It improves dashboard usability and
    the report, but the evaluation story and metrics focus on the
    segmentation model.

### How the system is multimodal

*   Sentinel-1 radar and Sentinel-2 optical imagery (different physics,
    complementary strengths).
*   Elevation data (slope, height above drainage) as a physical constraint.
*   Vector map data from OpenStreetMap (buildings, roads, bridges).
*   Text and image (report and assistant) built on the numeric results.

Each modality fills a gap in the others: radar works through cloud but is
confused by shadow and ice, optical is clean but often cloud-blocked, the DEM
removes impossible detections, and OSM turns pixels into infrastructure.

## Evaluation plan

**Segmentation model.**

*   Hold out whole events and regions, not random tiles, so results reflect
    generalization to scenes the model has never seen.
*   Report IoU, F1, precision and recall for flood water, and compare against
    the log-ratio baseline.
*   Test on Himalayan or mountainous scenes if any labelled ones exist in the
    training datasets; if none do, state that clearly in the report.
*   Report performance split by terrain (flat versus steep) and by cloud
    availability.

**Trishuli case study (EMSR927).**

*   After the pipeline output is frozen, compare it against the EMSR927
    reference maps: overlap of flood extent, and agreement on damaged
    buildings and roads where the reference covers them.
*   Explain every large disagreement (different acquisition date, shadow,
    reference covers only part of the area) rather than hiding it.
*   Reference data is loaded only by the evaluation module, never by the
    pipeline.

**Cut-off analysis.**

*   Check on known corridors with a manual review of flagged segments, and
    report how sensitive the result is to the confidence threshold.

**Situation-report assistant.**

*   A fixed set of test questions with expected answers from the results
    store, plus a check that every number in every answer is traceable.

## Deliverables mapping

| Deliverable | Where it comes from |
|---|---|
| Map dashboard | Layer 7 dashboard |
| One-page situation report | Layer 7 report generator (English and Nepali) |
| GitHub repository and README | Pipeline, dashboard, setup and run instructions |
| Report (max 6 pages) with limitations | Architecture, evaluation results and the limitations section below |
| 3-minute demo video | Cached case study, then a live run on a new area |
| Attributions and dataset citations | Footer, README and report |
| EMSR927 comparison | Evaluation module |

## Evaluation criteria coverage

| Criterion | Weight | How it is addressed |
|---|---|---|
| Flood and damage mapping quality | 30% | Same-track radar pairing, terrain correction, trained model plus baseline, DEM masks, optical confirmation, confidence-aware damage |
| AI component | 25% | U-Net trained on Kuro Siwo, held-out event evaluation, comparison with baseline, honest debris handling |
| Cut-off settlement analysis | 20% | Pre-event road graph, baseline connectivity, bridge-aware logic, two confidence scenarios |
| Report, Q&A and honesty about limitations | 15% | Limitations section, data provenance shown in the dashboard, clear metrics |
| Dashboard and report usability | 10% | Layered map, summary cards, status warnings, bilingual one-page report |

## Live-judging readiness

Judges will pick an unseen area and date, so reliability matters more than
sophistication.

*   Keep the first live area small. Offer a "fast mode" using only radar and
    the trained model, and a "full mode" adding optical confirmation and
    path tracing.
*   Pre-cache DEM tiles, the OSM snapshot for the Himalayan region and the
    model weights. Cache Trishuli outputs for the demo.
*   Show the stage-by-stage progress and the data-availability warnings; a
    clear "no valid pair found" message is better than a wrong map.
*   Rehearse on at least two other regions and dates before the event.
*   Check Copernicus Data Space account quotas and download speeds before the
    day, and have a local fallback for the area.

## Team plan

A suggested split for a small team:

1.  Data acquisition, orbit pairing and preprocessing.
1.  Model training, evaluation and the change-detection baseline.
1.  Damage assessment, cut-off graph and flood-path analysis.
1.  Dashboard, report generator, assistant and demo video.

Suggested order of work: data acquisition and preprocessing first, then a
radar-only baseline, then the trained model, then damage and cut-off, then
the dashboard and report, then the Trishuli case study and EMSR927
comparison, then the bonus and polish.

## Limitations

These should appear in the report in plain language. They are real
constraints, not disclaimers.

### Satellite and timing limits

*   **No early warning.** Sentinel satellites revisit an area only every few
    days. The system maps the aftermath; it cannot detect or warn about a
    glacier collapse minutes ahead.
*   **Delay before the first useful image.** The first same-track post-event
    radar scene may arrive days after the event, and the flood may have
    receded or changed by then. Maps show the state on that date, not the
    peak.
*   **Same-track constraint.** Strict same-orbit pairing sometimes leaves no
    valid pair, or only a pair many days apart.
*   **Optical imagery is often unavailable.** Monsoon cloud can block
    Sentinel-2 for weeks, so optical confirmation is frequently missing.

### Radar and terrain limits

*   **Layover and shadow.** Narrow Himalayan gorges are partly invisible or
    distorted in radar. Those pixels are marked "no data", so damage in the
    deepest valleys is likely undercounted.
*   **Ice, snow and wet debris confusion.** Glacier ice, wet snow, mud and
    smooth debris can look like water or like dry land to C-band radar. Some
    misclassification is unavoidable.
*   **Resolution.** At 10 m (radar) and 30 m (DEM), small structures, narrow
    footpaths and small bridges are not individually resolved. Building-level
    results are estimates, not damage confirmation.

### Model limits

*   **Debris is not a trained class.** The training data has water labels but
    no debris labels, so debris detection is evidence-based and has no
    labelled accuracy figure beyond what can be checked against reference
    maps.
*   **Generalization to mountains is uncertain.** Most training events are in
    flatter terrain. Performance on steep valleys may be lower, and the
    report must show how much.
*   **Distribution shift.** New sensors processing versions, seasons or
    snow-covered scenes can reduce accuracy. The baseline method is the
    fallback and the disagreement between model and baseline is shown as an
    uncertainty indicator.

### Map data limits

*   **OSM completeness in remote Nepal.** Many villages, tracks and bridges
    are missing from OSM. Cut-off results are therefore a lower bound: an
    unmapped isolated hamlet will never appear.
*   **Pre-event snapshot is stale by design.** The latest allowed snapshot
    (27 July 2026) is about a month before the event, so recent roads may be
    missing and recent construction may be absent.
*   **Population is unreliable.** OSM population tags are sparse, so affected
    population figures are rough or unavailable and must not be quoted as
    casualty or displacement numbers.
*   **Road graph simplifications.** The system assumes a road intersecting
    the flood is impassable. In reality a road may be passable on one side,
    repaired quickly, or bypassed on foot or by track not in OSM. Roads that
    are not mapped cannot be used for detours.

### Analysis limits

*   **Flood path is terrain-based, not a simulation.** It shows where water
    is likely to follow the valley, not flow depth, speed or arrival time.
*   **Not a casualty or damage-certification tool.** The system detects
    changes and overlaps with mapped features. It cannot confirm a building
    is destroyed or a person is trapped.
*   **Assistant limits.** The assistant can only report what is in the results
    store, and Nepali output needs native-speaker review before any
    operational use.
*   **Processing time.** Full-scene processing can take a long time; the live
    system relies on small areas, caching and server-side processing.

### Responsible use

*   This is an educational prototype, not an operational tool. It must not
    replace official sources such as national authorities and the Copernicus
    Emergency Management Service.
*   The event is recent and families are still affected. Demos and reports
    must not show images of victims, and the wording should stay factual and
    respectful.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| No same-track radar pair for the judges' area and date | Clear warning, nearest-pair options, never an invalid pixel comparison |
| Model fails on a steep, snowy or unseen scene | Baseline fallback, disagreement indicator, confidence layer |
| Live processing too slow | Server-side processing, small areas, caching, fast mode |
| Accidental use of post-event OSM or reference maps | Snapshot date assertion, evaluation module isolated from the pipeline |
| Hallucinated numbers in reports | Lookup functions or templates, numeric validator, test question set |
| Overclaiming damage | Confidence tiers, wording of "estimated" and "possible", limitations on every output |
| API or quota problems during the event | Pre-cached data, local fallback, early quota check |

## See also

*   Challenge brief: Track B, Multimodal AI Hackathon 2026 (IIT Mandi)
*   [Copernicus Data Space](https://dataspace.copernicus.eu)
*   [ohsome API](https://api.ohsome.org/v1)
*   [Kuro Siwo](https://github.com/Orion-AI-Lab/KuroSiwo)
*   [Sen1Floods11](https://github.com/cloudtostreet/Sen1Floods11)
*   [Copernicus EMS mapping](https://mapping.emergency.copernicus.eu)
    (EMSR927, checking only)
