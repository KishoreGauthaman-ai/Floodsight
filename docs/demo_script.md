# FloodSight 3-minute demo script (real numbers, timed)

## Pre-demo (done before the clock starts)
- Dashboard open on :8520 (case study run selected).
- Terminal ready with the judge-box quick command.
- Checkpoint + credentials in place (SHA-256 cb91fa63... verified).

## 0:00-0:30 - The problem and the honest headline
"On 26 August 2026 a glacier collapse sent a flood down the Trishuli
corridor. FloodSight answers three questions from free satellite data:
where did it hit, what was damaged, who is cut off. Point at the
dashboard: standing flood water is ZERO - an honest zero, the flood
receded before the first usable radar scene 62 hours later. What
survives is surface change, and we attribute it carefully."

## 0:30-1:10 - Attribution, not wishful thinking (dashboard map)
Show the two layers: "event-attributed debris, 0.455 km2 - red - and
other ground change, 41.99 km2 - orange. A pre-event control pair
produces 54 km2 of the orange layer: it is monsoon change, not event
damage, and we say so on the label. The red layer is 4.69 times the
control at the same control-derived threshold, 3.07 against a second
independent control. Thresholds came from controls only, never from
the reference."

## 1:10-1:50 - Damage and cut-off honesty (panels)
"30 buildings have a centroid inside the event layer - 82 touch it.
19 bridges have an APPROACH inside the flagged zone - no bridge is
observed damaged. 106 settlements are possibly cut. And here is the
part we are proudest of: 483 settlements are marked UNASSESSED because
their road routes cross terrain where radar cannot see - we refuse to
call them connected. Zero likely-cut does NOT mean nobody is cut off."

## 1:50-2:30 - Live run (judge box, quick mode)
Run:
    .venv/bin/floodsight run --bbox 84.9041 27.8035 85.1105 27.9869 \
        --date 2026-08-26 --mode fast --judge
"While it works: the CLI refused the full corridor because --judge
caps the area at 500 km2 before any download. Quick mode is one radar
pair - about 14 minutes with parallel downloads, and it is labelled
monsoon-baseline change. Full event attribution is a separate 46-minute
mode. The stage chips tick live on the dashboard."

## 2:30-3:00 - Validation honesty and close
"Everything here was checked: against Copernicus EMS reference mapping
after a code freeze, no tuning after - our building-damage recall is 9
of 3,807 and we say so; placebo monsoon layers score HIGHER F1 than the
event layer, so only precision separates real signal. Educational
prototype, estimates only - and it never hides what it cannot see."

## Timing notes (measured)
- Judge-box quick mode fresh: 23.4 min; cached pair: minutes. For the
  live demo, pre-open the cached judge-box run and let the fresh run
  tick in the background - do not wait for it on stage.
- Dashboard start: ~10 s. Sitrep generation: seconds.
