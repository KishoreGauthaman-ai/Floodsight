"""Evaluation package — the ONLY place reference data (EMSR927, UNOSAT) is read.

Isolation contract (tests/test_data_rules.py enforces it):
*   src/floodsight never imports this package and never reads
    evaluation/reference/.
*   Reference data is used to CHECK pipeline results, never to produce them.
"""

__all__ = ["assistant_tests", "cutoff_review", "emsr927", "segmentation"]
