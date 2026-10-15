# Trishuli case-study bounding box derivation

Status: APPROVED by the user on 2026-10-10 with conditions (DECISIONS D43),
including the pre-condition checks below. Applied to
`config/regions/trishuli.yaml`. Do not change these coordinates without
asking the user first.

Method (per user instruction: derived from river geometry in the PRE-EVENT
OSM snapshot, NOT from EMSR927):

1.  Source: the dated Geofabrik snapshot `nepal-260701.osm.pbf` (OSM state
    of 2026-07-01, md5-verified; DECISIONS D19).
2.  Extracted all OSM ways/relations tagged `waterway=river` in the window
    (lon 84.0-86.5, lat 27.2-28.9) with osmium.
3.  Filtered by name. The corridor rivers present in the snapshot are:
    *   `Trishuli River`, `Trishuli Ganga River`, `Trisuli` —
        extent lon 84.422..85.341, lat 27.741..28.164
    *   `Bhote Koshi` (the Rasuwa/Trishuli-basin one) —
        extent lon 85.341..85.378, lat 28.164..28.278 (head at the
        China border area, lon 85.377, lat 28.278)
    *   `Langtang Khola` — extent lon 85.341..85.694,
        lat 28.151..28.233
4.  Disambiguation: OSM has a SECOND `Bhote Koshi` (Sindhupalchok/Sun Koshi
    basin, lon 85.868..85.955, lat 27.779..27.954) which does NOT connect to
    the Trishuli and is excluded from the corridor.
5.  The named Trishuli's westernmost point (84.422, 27.741) is the area
    where the Trishuli hands over to the Narayani (Devghat confluence) —
    the natural downstream end of the corridor.

## Proposed values

*   **bbox** = `[84.39, 27.68, 85.73, 28.31]`
    (lon 84.39..85.73, lat 27.68..28.31; corridor extent plus a ~0.02-0.03
    deg valley buffer; spans about 147 x 70 km). This exceeds the 1.0-deg
    soft warning threshold in config (the full corridor is intentional for
    the case study) but stays under the 2.5-deg hard limit.
*   **upstream_point** (flood-path bonus, proposed) = `[85.377, 28.278]`
    — the head of the Bhote Koshi (Rasuwa) at the China border area, i.e.
    the top of the corridor.

Trade-off noted honestly: a rectangle over a diagonally running valley also
covers some off-corridor terrain (including the western edge of the
Kathmandu valley), which the damage/cut-off analysis will simply not flag
unless the flood map reaches it.


## Approval checks (2026-10-10, DECISIONS D43)

1.  Upstream point (85.377, 28.278) lies **5 m** from the Bhote Koshi
    centreline (on-river; DEM elevation 1804 m, valley floor). No snapping
    was needed. Figure: `docs/figures/bbox_upstream_check.png`.
2.  The true corridor (Trishuli + Bhote Koshi + Langtang, excluding the
    unrelated "Bhoti Khola" namesake tributary) is fully inside the box:
    margins west +3,143 m, south +6,820 m, east +3,521 m, north +3,556 m.
    The 0.02-0.03 deg derivation buffer equals ~2.0-3.3 km, covering OSM
    centrelines' 10-50 m positional uncertainty and the 100-1000 m valley
    width many times over. The namesake tributary (6.7 km of centreline,
    draining a different valley into the Narayani) stays outside by design.
3.  Longitude-first order is asserted by `tests/test_region_order.py`
    (Nepal bands: lon 79-89, lat 26-31).
4.  Resources for the approved box: 4 GLO-30 DEM tiles (N27/N28 x E084/E085;
    2 cached, 87 MB to download) and 18 candidate S1 GRD scenes in the
    search window (only the selected same-track pair is downloaded).
5.  Soft warning at 1.0 deg fires for this box (lon side 1.34 deg); the run
    proceeds; the hard stop at 2.5 deg is enforced with a clear error.
6.  No EMSR927 or post-event map was used for any coordinate (D25, D43).
