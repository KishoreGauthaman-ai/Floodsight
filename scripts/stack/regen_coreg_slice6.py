"""Regenerate the truncated s1_post_coreg.tif (track85 slice6 event pair).

A zombie pipeline run was killed mid-write by coregister's old
non-atomic write, leaving a 0-byte cache raster (Round 4 incident).
coregister is now atomic (tmp + os.replace); this script recreates the
missing output from the intact pre/post TC rasters.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from floodsight.preprocessing.s1 import coregister

PAIR = (REPO / "data/interim"
        / "709f78806df43e49a2abed4b095b810feeafdc3344ad5633ae6f85ef4dc39a68"
        / "track85_ASCE_slice6_C3AB_01B4")

if __name__ == "__main__":
    coregister(PAIR / "s1_post_sigma0_tc.tif",
               PAIR / "s1_pre_sigma0_tc.tif",
               PAIR / "s1_post_coreg.tif")
    print("COREG REGENERATED")
