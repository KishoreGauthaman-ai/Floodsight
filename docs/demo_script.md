# FloodSight — 3-minute demo script

Prepared from the real, verified state of the system (PROGRESS.md,
DECISIONS.md). Timing marks are targets; the run itself is live.

## Before the demo (already prepared)

- Cached: Trishuli case-study run `runs/85.000_27.800_85.600_28.250_2026-08-26_fast`
  (results.json, flood/debris/model rasters, sitrep EN/NE PDFs), S1 scenes
  for the pair, DEM tiles, OSM snapshot, model checkpoint.
- Dashboard running (`floodsight dashboard`), opened on the cached run.

## 0:00–0:30 — The problem (dashboard, cached run)

"On 26 August 2026 a glacier collapse sent water, mud and debris down the
Trishuli corridor. Rescue teams need three answers from satellites: where
did the flood hit, what was damaged, who is cut off. This is FloodSight."
Show summary cards; explain the honest zero standing water (the flood had
receded before the first usable post-event radar scene — an explicit
limitation, not a failure) and the 1.68 km2 probable debris/change layer
that carries the event signature.

## 0:30–1:15 — One honest number, end to end

Open the provenance panel: same-orbit-track S1 pair (track 121 DESCENDING,
2026-08-19 -> 2026-08-31), pre-event OSM snapshot (2026-07-01, rule-checked
against the flood date), Copernicus GLO-30. Point at the cut-off numbers:
16 likely cut, 7 possibly cut, 260 still connected, 59 already isolated
before the event (never blamed on the flood). Show the map with settlement
colors and the debris polygons along the river.

## 1:15–2:15 — Live run on a judge-chosen area (fast mode)

Enter a fresh small bounding box + date in the sidebar and press
"Run analysis (real data)". Narrate the stage statuses as they turn green:
search -> same-track pairing -> DEM -> dated OSM snapshot -> SNAP terrain
correction -> baseline + U-Net detection -> damage -> cut-off -> results.
Mention the AI component: U-Net trained on Kuro Siwo, held-out flood IoU
0.630 vs baseline 0.554, running here on CPU in the loop.

## 2:15–2:45 — One-page report, bilingual

`floodsight report --run <new run> --lang en,ne`: point at the English PDF
(numbers validator-checked against the results store) and the Nepali PDF
with the fixed glossary and the native-speaker review note.

## 2:45–3:00 — Honest limitations

"No early warning — the satellites revisit every few days. The map shows
the post-event date, not the peak. Debris is evidence-based, not a trained
class. OSM gaps make cut-off a lower bound. Educational prototype — not an
operational tool. Full attribution: Copernicus Sentinel data, WorldDEM-30,
OpenStreetMap contributors."

## Talking points if asked

- Why zero flood water? Both usable post-event scenes (+2 d, +5 d) show the
  flood already receded; we refuse to tune thresholds to manufacture a map
  (DECISIONS D32).
- Model quality? Held-out events: IoU 0.630 / F1 0.773 / precision 0.981;
  beats the classical baseline (0.554 / 0.713) — trained CPU-only on a
  documented Kuro Siwo subset, event-wise splits.
- Data rules? OSM snapshot date enforced at startup; EMSR927 reference
  data lives ONLY in evaluation/ with an import-isolation test.
