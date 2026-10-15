"""T2b: full-corridor SLOW along-flow HAND for honest validation vs fast.

Run: nohup .venv/bin/python scripts/stack/hand_slow_full.py \
         > runs/_jobs/t2_hand_slow.log 2>&1 &
"""
import logging
import sys
import time
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))
from floodsight.analysis.flood_path import _load_dem_grid
from floodsight.config import load_config
from floodsight.preprocessing.dem_derivatives import (
    fill_depressions,
    flow_accumulation,
    flow_direction,
    hand,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("t2_slow")
BBOX = (84.39, 27.68, 85.73, 28.31)
ACC = 500.0

cfg = load_config()
grid, transform = _load_dem_grid(BBOX, cfg)
clean = np.where(np.isfinite(grid), grid, 1e5)
clean[clean <= 0] = 1e5
filled = fill_depressions(clean)
direction = flow_direction(filled)
acc = flow_accumulation(direction)
t0 = time.monotonic()
slow = hand(filled, direction, acc, acc_threshold=ACC)
log.info("slow hand full grid %s in %.0fs", grid.shape, time.monotonic() - t0)
np.save(REPO / "data/stack/trishuli/hand_slow_full.npy", slow.astype("float32"))
log.info("saved hand_slow_full.npy")
