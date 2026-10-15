"""Same-track pre/post Sentinel-1 scene selection (mandatory radar rule).

Scenes are grouped by (relative orbit number, flight direction); a valid
pair is a pre-event pass and a post-event pass from the SAME track, never
from different tracks (different tracks view the terrain from different
angles and must never be compared pixel by pixel — hackathon rule).

A single pass may consist of several adjacent slices covering the area
(grouped by acquisition date on the same track). Pairing aligns passes;
slice-level alignment is preserved via the ``sliceNumber`` attribute so the
preprocessing stage can co-register matching slices.

If no valid pair exists within the configured windows, the result says so
explicitly and lists the nearest alternatives; the pipeline then stops
rather than forcing a bad comparison.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from ..config import parse_date


class NoValidPairError(Exception):
    """No same-track pre/post pair exists for the area and date."""


@dataclass
class SceneInfo:
    """One Sentinel-1 scene, parsed from a CDSE OData product dict."""

    product_id: str
    name: str
    sensing_time: datetime
    relative_orbit: int
    orbit_direction: str
    product_type: str
    platform: str
    slice_number: int | None = None
    is_cog: bool = False  # COG duplicates are skipped (DECISIONS D5)
    size_bytes: int | None = None
    online: bool = True
    footprint: Any = None  # shapely geometry of the scene footprint, or None


@dataclass
class PassGroup:
    """All slices acquired over the area during one track pass."""

    track: tuple[int, str]  # (relative_orbit, orbit_direction)
    date: date
    scenes: list[SceneInfo] = field(default_factory=list)


@dataclass
class PairResult:
    """A selected same-track pre/post pair (possibly multi-slice)."""

    track: tuple[int, str]
    pre: list[SceneInfo]
    post: list[SceneInfo]
    days_before: int
    days_after: int

    @property
    def score(self) -> int:
        """Pair preference: minimize the worse of the two gaps."""
        return max(self.days_before, self.days_after)


def parse_scene(product: dict[str, Any]) -> SceneInfo | None:
    """Parse a CDSE product dict into SceneInfo; None if not usable S1 GRD.

    Uses API attributes (relativeOrbitNumber, orbitDirection, productType —
    verified on live products, DECISIONS D7), never filename guessing.
    COG duplicates are skipped in favour of standard SAFE products (D5).
    """
    name = product.get("Name", "")
    attrs = {a["Name"]: a["Value"] for a in product.get("Attributes", [])}
    product_type = attrs.get("productType", "")
    if product_type != "IW_GRDH_1S":
        return None
    if name.endswith("_COG.SAFE"):
        return None
    start = product.get("ContentDate", {}).get("Start")
    if not start or not name:
        return None
    rel_orbit = attrs.get("relativeOrbitNumber")
    direction = attrs.get("orbitDirection")
    if rel_orbit is None or direction is None:
        return None
    sensing = datetime.fromisoformat(start.replace("Z", "+00:00"))
    footprint = None
    gf = product.get("GeoFootprint")
    if gf:
        from shapely.geometry import shape

        try:
            footprint = shape(gf)
        except (TypeError, ValueError, KeyError):
            # malformed footprint must never break the search
            footprint = None
    return SceneInfo(
        product_id=str(product["Id"]),
        name=name,
        sensing_time=sensing,
        relative_orbit=int(rel_orbit),
        orbit_direction=str(direction),
        product_type=product_type,
        platform=str(attrs.get("platformSerialIdentifier", "")),
        slice_number=int(attrs["sliceNumber"]) if attrs.get("sliceNumber") else None,
        is_cog=False,
        size_bytes=product.get("ContentLength"),
        online=bool(product.get("Online", True)),
        footprint=footprint,
    )


def group_passes(scenes: list[SceneInfo]) -> list[PassGroup]:
    """Group scenes into track passes (one track, one acquisition date).

    Slices of the same pass share the track and the acquisition date (a
    datatake is split into slices acquired seconds apart; verified on live
    products: sliceNumber 8/15 etc.).
    """
    passes: dict[tuple[tuple[int, str], date], PassGroup] = {}
    for scene in sorted(scenes, key=lambda s: s.sensing_time):
        key = (scene.relative_orbit, scene.orbit_direction), scene.sensing_time.date()
        if key not in passes:
            passes[key] = PassGroup(track=key[0], date=key[1])
        passes[key].scenes.append(scene)
    return sorted(passes.values(), key=lambda p: p.date)


def select_same_track_pair(
    scenes: list[SceneInfo],
    flood_date: str | date,
    max_pre_event_days: int,
    max_post_event_days: int,
) -> PairResult:
    """Select the best same-track pre/post pair around the flood date.

    Rules:
    *   pre-pass = latest pass strictly before the flood date on the track;
    *   post-pass = earliest pass strictly after the flood date on the track;
    *   both must be within the configured day windows;
    *   among tracks with a valid pair, minimize the worse gap
        (``max(days_before, days_after)``), then the total gap.

    Raises NoValidPairError with the nearest alternatives listed, so the
    pipeline can stop with a clear message instead of forcing a comparison.
    """
    flood = parse_date(flood_date)
    passes = group_passes(list(scenes))
    if not passes:
        raise NoValidPairError(
            "No Sentinel-1 IW GRD scenes found over the area in the search window."
        )

    candidates: list[PairResult] = []
    tracks = sorted({p.track for p in passes})
    for track in tracks:
        track_passes = [p for p in passes if p.track == track]
        pre = [p for p in track_passes if p.date < flood]
        post = [p for p in track_passes if p.date > flood]
        if not pre or not post:
            continue
        pre_pass = max(pre, key=lambda p: p.date)
        post_pass = min(post, key=lambda p: p.date)
        days_before = (flood - pre_pass.date).days
        days_after = (post_pass.date - flood).days
        if days_before > max_pre_event_days or days_after > max_post_event_days:
            continue
        candidates.append(
            PairResult(track=track, pre=pre_pass.scenes, post=post_pass.scenes,
                       days_before=days_before, days_after=days_after)
        )

    if not candidates:
        raise NoValidPairError(_alternatives_message(passes, flood))
    candidates.sort(key=lambda c: (c.score, c.days_before + c.days_after))
    return candidates[0]


def _alternatives_message(passes: list[PassGroup], flood: date) -> str:
    """Human-readable list of the nearest same-track alternatives."""
    lines = [
        "No valid same-track Sentinel-1 pre/post pair exists within the "
        "configured windows around " + flood.isoformat() + ". "
        "Scenes from different tracks are never compared pixel by pixel. "
        "Nearest alternatives:"
    ]
    tracks = sorted({p.track for p in passes})
    for rel_orbit, direction in tracks:
        track_passes = sorted((p for p in passes if p.track == (rel_orbit, direction)),
                              key=lambda p: p.date)
        dates = [p.date.isoformat() for p in track_passes[-4:]]
        lines.append(
            f"  track rel-orbit {rel_orbit} {direction}: pass dates "
            + ", ".join(dates)
        )
    lines.append(
        "Widen acquisition.search_window_days_pre/post in config/default.yaml "
        "or choose another date/area."
    )
    return "\n".join(lines)


def _pass_union(pass_scenes: list[SceneInfo]):
    """Union footprint of all scenes in one track pass (multi-slice aware)."""
    geoms = [s.footprint for s in pass_scenes if s.footprint is not None]
    if not geoms:
        return None
    if len(geoms) == 1:
        return geoms[0]
    from shapely.ops import unary_union

    return unary_union(geoms)


def pair_bbox_coverage(pre_scenes: list[SceneInfo],
                       post_scenes: list[SceneInfo],
                       bbox: tuple[float, float, float, float]) -> float:
    """Fraction of the bbox covered by BOTH dates of a same-track pair.

    The analysed area of a pair is the intersection of what the pre-event
    pass and the post-event pass each cover (both slices of a multi-slice
    pass count), intersected with the bbox. Returns 0.0 when footprints are
    unavailable (never guessed).
    """
    from shapely.geometry import box as shapely_box

    pre_u = _pass_union(pre_scenes)
    post_u = _pass_union(post_scenes)
    if pre_u is None or post_u is None:
        return 0.0
    both = pre_u.intersection(post_u)
    bbox_poly = shapely_box(*bbox)
    covered = both.intersection(bbox_poly)
    return float(covered.area / bbox_poly.area)


@dataclass
class RankedPair:
    """A same-track pair plus its bbox coverage and date gaps."""

    track: tuple[int, str]
    pre: list[SceneInfo]
    post: list[SceneInfo]
    days_before: int
    days_after: int
    coverage: float

    @property
    def date_key(self) -> tuple[int, int, int]:
        """Date preference: smallest worst gap, then fresher post-event
        (flood water recedes), then closer pre-event."""
        return (max(self.days_before, self.days_after), self.days_after,
                self.days_before)


def select_pairs(
    scenes: list[SceneInfo],
    flood_date: str | date,
    bbox: tuple[float, float, float, float],
    cfg: dict[str, Any],
) -> dict[str, Any]:
    """Coverage-aware pair selection (validated against the approved bbox).

    Rules:
    *   Every candidate is a SAME-TRACK pair (relative orbit + direction);
        the radar rule holds inside each pair; pixels are never compared
        across tracks.
    *   Pairs covering at least ``min_pair_coverage`` (default 0.9) of the
        bbox are ranked by date; the best one is used alone ("single").
    *   If NO pair reaches that threshold, every pair with coverage at or
        above ``min_track_coverage`` (default 0.05) is processed separately
        and their masks are unioned, each pixel attributed to one track
        ("union") - the only honest alternative to a silently partial map.
    *   A partial_coverage warning is the caller's responsibility when the
        final coverage stays below the threshold.

    Returns {"mode", "pairs": [RankedPair...], "coverage": best coverage}.
    Raises NoValidPairError when nothing usable exists.
    """
    from ..config import parse_date as _parse

    flood = _parse(flood_date)
    s1_cfg = cfg["acquisition"]["s1"]
    max_pre = int(s1_cfg["max_pre_event_days"])
    max_post = int(s1_cfg["max_post_event_days"])
    min_pair_cov = float(s1_cfg.get("min_pair_coverage", 0.9))
    min_track_cov = float(s1_cfg.get("min_track_coverage", 0.05))

    passes = group_passes(scenes) if scenes else []
    by_track: dict[tuple[int, str], dict[str, list[PassGroup]]] = {}
    for pg in passes:
        if pg.date < flood:
            by_track.setdefault(pg.track, {}).setdefault("pre", []).append(pg)
        elif pg.date > flood:
            by_track.setdefault(pg.track, {}).setdefault("post", []).append(pg)

    ranked: list[RankedPair] = []
    for track, sides in sorted(by_track.items()):
        if not sides.get("pre") or not sides.get("post"):
            continue
        pre_pass = max(sides["pre"], key=lambda p: p.date)
        post_pass = min(sides["post"], key=lambda p: p.date)
        days_before = (flood - pre_pass.date).days
        days_after = (post_pass.date - flood).days
        if days_before > max_pre or days_after > max_post:
            continue
        ranked.append(RankedPair(
            track=track, pre=pre_pass.scenes, post=post_pass.scenes,
            days_before=days_before, days_after=days_after,
            coverage=pair_bbox_coverage(pre_pass.scenes, post_pass.scenes, bbox),
        ))
    if not ranked:
        raise NoValidPairError(_alternatives_message(passes, flood))

    covering = [r for r in ranked if r.coverage >= min_pair_cov]
    if covering:
        covering.sort(key=lambda r: r.date_key)
        chosen = covering
        mode = "single"
        # a single pair only: process the best, but keep the ranking recorded
        chosen = covering[:1] if len(covering) == 1 else [covering[0]]
        if len(covering) > 1:
            chosen = [covering[0]]
    else:
        chosen = [r for r in ranked if r.coverage >= min_track_cov]
        chosen.sort(key=lambda r: (-r.coverage, r.date_key))
        mode = "union"
        if not chosen:
            raise NoValidPairError(
                "No same-track pair covers at least "
                f"{min_track_cov:.0%} of the bbox; refusing a partial "
                "comparison. " + _alternatives_message(passes, flood)
            )
    return {"mode": mode, "pairs": chosen,
            "coverage": max(r.coverage for r in chosen),
            "all_candidates": ranked}
