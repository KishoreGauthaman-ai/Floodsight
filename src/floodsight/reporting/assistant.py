"""Situation-report assistant (grounded, template-only).

Answers rescuer questions using ONLY lookups on the results store — no LLM
is configured (user decision D16); every answer is checked by the numeric
validator before being returned, and unknown topics get an explicit "not in
the analysis" answer. Never speculates about casualties.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .results_store import ResultsStore
from .validator import validate_numbers_in_text

_INTENTS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"how many settlements .*likely cut|likely cut off", re.IGNORECASE),
     "likely cut settlements"),
    (re.compile(r"how many settlements .*possibly cut|possibly cut off", re.IGNORECASE),
     "possibly cut settlements"),
    (re.compile(r"how many settlements", re.IGNORECASE), "settlement summary"),
    (re.compile(r"how many buildings", re.IGNORECASE), "buildings summary"),
    (re.compile(r"how many (road|roads)|road length|roads", re.IGNORECASE), "roads summary"),
    (re.compile(r"how many (bridge|bridges)|bridges", re.IGNORECASE), "bridges summary"),
    (re.compile(r"flood (area|water)|how much.*flood", re.IGNORECASE), "flood area"),
    (re.compile(r"debris|surface change", re.IGNORECASE), "debris area"),
    (re.compile(r"which scenes|which images|data sources|which satellite", re.IGNORECASE),
     "data sources"),
    (re.compile(r"osm snapshot|snapshot date", re.IGNORECASE), "osm snapshot"),
]


def _lookup(topic: str, results: dict[str, Any]) -> str | None:
    c = results["cutoff"]
    b = results["damage"]["buildings"]
    r = results["damage"]["roads"]
    br = results["damage"]["bridges"]
    f = results["flood"]
    inputs = results["inputs"]
    match topic:
        case "likely cut settlements":
            names = ", ".join(c["likely_cut"][:12]) or "none"
            return (f"{len(c['likely_cut'])} settlements are likely cut off "
                    f"(high-confidence scenario): {names}.")
        case "possibly cut settlements":
            names = ", ".join(c["possibly_cut"][:12]) or "none"
            return (f"{len(c['possibly_cut'])} settlements are possibly cut off "
                    f"(lower-confidence scenario): {names}.")
        case "settlement summary":
            return (f"{len(c['likely_cut'])} likely cut, "
                    f"{len(c['possibly_cut'])} possibly cut, "
                    f"{c['still_connected_count']} still connected, "
                    f"{len(c['already_isolated'])} were already isolated "
                    "before the event (not flood-related).")
        case "buildings summary":
            return (f"{b['likely_hit']} buildings likely hit and "
                    f"{b['possibly_hit']} possibly hit, of {b['total_in_area']} "
                    f"mapped; footprint area {b['footprint_area_km2']} km2.")
        case "roads summary":
            return (f"{r['total_length_km']} km of roads inside the "
                    f"flood/debris extent; by class: "
                    + ", ".join(f"{cls} {km} km" for cls, km in
                                r["length_by_class_km"].items())
                    + ".")
        case "bridges summary":
            return (f"{br['affected']} of {br['total_in_area']} mapped bridges "
                    "are affected.")
        case "flood area":
            return (f"New standing flood water: {f['area_km2_new_water']} km2 "
                    "(detected on the post-event satellite date, not the peak).")
        case "debris area":
            return (f"Probable debris / surface change: "
                    f"{f['area_km2_probable_debris']} km2. This is an "
                    "evidence-based layer, not a trained class.")
        case "data sources":
            pre = inputs.get("sentinel1_pre") or {}
            post = inputs.get("sentinel1_post") or {}
            return (f"Sentinel-1 pair on the same orbit track "
                    f"(relative orbit {pre.get('relative_orbit')}): pre-event "
                    f"{str(pre.get('sensing_time', ''))[:10]}, post-event "
                    f"{str(post.get('sensing_time', ''))[:10]}. DEM: Copernicus "
                    "GLO-30. OSM snapshot: "
                    f"{inputs.get('osm_snapshot_date')} (pre-event).")
        case "osm snapshot":
            return (f"OpenStreetMap snapshot date: {inputs.get('osm_snapshot_date')}. "
                    "It is strictly earlier than the flood date (rule-enforced).")
    return None


def answer_question(question: str, run_dir: Path) -> str:
    """Answer a question strictly from the results store of one run."""
    results = ResultsStore(Path(run_dir)).read()
    lowered = question.lower()
    if re.search(r"casualt|injur|died|death|victim", lowered):
        return ("The analysis does not contain casualty information. "
                "FloodSight maps flood extent and infrastructure estimates only.")
    for pattern, topic in _INTENTS:
        if pattern.search(question):
            answer = _lookup(topic, results)
            if answer is not None:
                ok, ungrounded = validate_numbers_in_text(answer, results)
                if not ok:
                    return ("Refusing to answer: an internal check found "
                            f"ungrounded numbers {ungrounded}.")
                return answer
    return ("That information is not in the analysis. Available topics: "
            "flood area, debris, buildings, roads, bridges, cut-off "
            "settlements, data sources.")
