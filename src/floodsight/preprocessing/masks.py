"""Shadow, layover and no-data masks for radar reliability.

Unreliable pixels (radar shadow, layover on steep valley walls, nodata) are
flagged as "no data", never as "dry".
"""

from __future__ import annotations


def compute_reliability_masks(*args: object, **kwargs: object) -> object:
    """Compute shadow / layover / nodata masks from the DEM and orbit."""
    raise NotImplementedError("Reliability masks are implemented in Phase 3.")
