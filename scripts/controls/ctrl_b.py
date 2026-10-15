"""Control (b): post-recession pair (flood_date=2026-09-09).

Run inside tmux:
  .venv/bin/python scripts/controls/ctrl_b.py 2>&1 | tee runs/_jobs/ctrl_b.log
"""

import logging
import time
from pathlib import Path

logging.basicConfig(level=logging.WARNING)

from floodsight.config import load_config
from floodsight.pipeline import run_pipeline

RAW_DIR = Path("data/raw/sentinel1")


def prune_own_zips(started: float) -> None:
    """Delete only zips this run downloaded (mtime >= start).

    NEVER glob-delete everything: concurrent jobs download into the same
    directory and an unconditional prune deleted another job's in-flight
    zip (verified crash, Round 4 T1).
    """
    for z in RAW_DIR.glob("*.zip"):
        if z.stat().st_mtime >= started:
            z.unlink()
            print(f"pruned own zip: {z.name}")


def main() -> None:
    started = time.time()
    t0 = time.monotonic()
    try:
        run_pipeline([84.39, 27.68, 85.73, 28.31], "2026-09-09", mode="fast",
                     cfg=load_config(), region="nepal")
        print(f"CTRL_B DONE {time.monotonic() - t0:.0f}s")
    except Exception as e:
        print(f"CTRL_B FAILED: {e}")
    prune_own_zips(started)


if __name__ == "__main__":
    main()
