"""Fusion of detection streams into the final flood and debris maps.

New flood water = post-event water minus pre-event (permanent) water, so
rivers and lakes are not counted as damage. Applies DEM-based plausibility
constraints and shadow/layover masks, removes tiny blobs, and produces a
per-pixel confidence layer. Strong model-baseline disagreement is recorded
as an uncertainty warning.
"""

from __future__ import annotations


def fuse(*args: object, **kwargs: object) -> object:
    """Combine model, baseline, optical and DEM evidence with confidence."""
    raise NotImplementedError("Fusion is implemented in Phase 8 (basic masks in Phase 4).")
