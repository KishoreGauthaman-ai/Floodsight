"""Sentinel-2 optical preprocessing.

Cloud and shadow masking, water index and surface-change index for the
before/after comparison. Optical confirmation is used only where the sky is
clear.
"""

from __future__ import annotations


def preprocess_s2(*args: object, **kwargs: object) -> object:
    """Mask clouds and compute water / change indices for the S2 pair."""
    raise NotImplementedError("Sentinel-2 preprocessing is implemented in Phase 3.")
