# NEXT_STEPS.md — Round 5 live todo list (verification and gap closing)

Status: pending | in-progress | blocked | done. One in-progress at a time.
Rules this round: no pdf regeneration; frozen v1 untouched; new methods = "v2" with development story; thresholds only from controls/physics/pre-event data (never EMSR927); judge box needs GeoJSON + map approval.

| # | Task | Status | Acceptance test | Blocked by |
|---|---|---|---|---|
| S1 | Prune runs/ to 3 unique (case study 08-26, control a 08-10, control b 09-09 — the trio the report generator reads live); deleted control (c), judge-box v1, Koshi (all numbers recorded in DECISIONS D60/D67/D42) | DONE | runs/ has exactly 3 run dirs + _jobs | - |
| S2 | Judge box exported for approval: docs/figures/judge_box.geojson + judge_box_map.png (satellite, main stems, 100 settlements, box) | DONE (awaiting user approval) | artifacts on disk; user says approve/reject | user |
| V1 | DONE (D75): circularity acknowledged; LOO controls 0.116-0.200 km2; independent control (d) 0.148 km2; event 0.4545 -> ratios 4.69 / 3.07, attributable holds on every variant | DONE | v1_circularity.json | - |
| V3 | DONE (D76): 82 geometry-touch = 30 centroid-in + 52 edge-clip; 19 bridges = 10 vehicle / 8 foot / 1 untagged, ALL possible-tier via the 50 m approach buffer (0 m direct overlap); median distance to main stems 0.0 m; map: docs/figures/bridges_event_map.png | DONE | v3_counts.json + bridges_event.geojson | - |
| V4 | DONE (D77): 483 of 594 v1-connected settlements re-labelled UNASSESSED (routes cross masked terrain); 111 verifiably connected; exposure: 17 settlements / 1184.2 km roads / 137 bridges (NOT observed damage); both panels verified rendering in the dashboard | DONE | v4_terrain.json + cutoff_v2.json + terrain_exposure.json | - |
| V7 | DONE (D79): parallel download 318.9 s (10.88 MB/s), parallel SNAP 242.3 s measured live; full attribution ~46 min > 30 -> TWO MODES defined: quick (~14 min, pair only, labelled monsoon-baseline change) and full attribution (~46 min) | DONE | v7_parallel_timing.json + v7_timing_table.json | - |
| V2 | DONE (D74/D80): prevalence 28.3%; random baseline F1 0.034; placebo layers score F1 0.56/0.59 (HIGHER than the event layer's 0.47) - EMSR927 F1 is NOT event-specific; only precision separates the event layer (0.76 vs 0.49 random) | DONE | v2_placebo.json | - |
| V6 | AI COMPONENT: (a) check ImageNet weights download + retrain as v2 if possible; (b) IoU per elevation bin with counts, val + test; (d) purge "beats baseline on every metric" claims (report generator already fixed in T10 — verify no other claim remains) | pending | v2 model metrics or honest "download failed"; no stale claims | npz caches intact (D65) |
| V5 | STOPPED per the time-box (D81): joint observability 15.4%, control NDVI null z=-21 (cloud-edge noise), zero scars in the reference AOIs, recall 0 - cloud-free coverage is too poor for a defensible optical layer; machinery (JP2/SCL/control-threshold) built and reusable | DONE (honest stop) | v5_optical.json | - |
| V8 | DONE (D73): README "For judges" section + weights-hosting plan (GitHub Release, 57 MB asset); no git remote/push | DONE | README | - |

## Frozen v1 (untouched): case study results, EMSR927 one-shot comparison (D70), freeze hashes (D69)
## v2 methods this round: everything in evaluation/v1_*, v3_*, v4_*, v2_*, v6_* + cutoff_v2/terrain_exposure artifacts; src/floodsight detection logic unchanged

## Round 5 complete - all tasks V1-V8 done or honestly stopped
- Frozen v1 results untouched; every post-freeze change labelled (D71-D82).
- Awaiting from the user: judge-box approval (docs/figures/judge_box.geojson + judge_box_map.png); git remote URL; the order to regenerate pdf_content.txt.
- Dashboard: port 8520, up, with the v2 panels (unassessed category + terrain exposure).
