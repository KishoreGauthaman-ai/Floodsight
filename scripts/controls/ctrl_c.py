"""Control (c): current date (flood_date=2026-10-12). Run: nohup .venv/bin/python scripts/controls/ctrl_c.py > runs/_jobs/ctrl_c.log 2>&1 &"""
import logging
import time

logging.basicConfig(level=logging.WARNING)
from pathlib import Path

from floodsight.config import load_config
from floodsight.pipeline import run_pipeline

t0 = time.monotonic()
try:
    run_pipeline([84.39, 27.68, 85.73, 28.31], "2026-10-12", mode="fast",
                 cfg=load_config(), region="nepal")
    print(f"CTRL_C DONE {time.monotonic()-t0:.0f}s")
except Exception as e:
    print(f"CTRL_C FAILED: {e}")
_started = time.time()
for z in Path("data/raw/sentinel1").glob("*.zip"):
    if z.stat().st_mtime >= _started:
        z.unlink()
        print(f"pruned own zip: {z.name}")
