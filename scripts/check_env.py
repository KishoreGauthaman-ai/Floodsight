"""Environment report for FloodSight: prints measured facts about the
machine and the installed dependencies. Never prints credential values.

Usage: python scripts/check_env.py
"""

from __future__ import annotations

import importlib
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

CORE = ["yaml", "requests", "dotenv", "numpy", "rasterio", "shapely", "pyproj", "geopandas"]
OPTIONAL = {
    "torch": "model training/inference (Phase 7)",
    "streamlit": "dashboard (Phase 6)",
    "folium": "dashboard maps (Phase 6)",
    "networkx": "cut-off road graph (Phase 5)",
    "scipy": "DEM/flow processing (Phase 3)",
    "matplotlib": "figures and previews",
}


def _have(module: str) -> tuple[bool, str]:
    try:
        mod = importlib.import_module(module)
    except ImportError:
        return False, "-"
    return True, getattr(mod, "__version__", "unknown")


def main() -> int:
    print(f"python: {sys.version.split()[0]} at {sys.executable}")
    snap_gpt = shutil.which("gpt")
    print(f"ESA SNAP gpt: {'found at ' + snap_gpt if snap_gpt else 'NOT installed'}")
    print("\ncore dependencies:")
    for mod in CORE:
        ok, ver = _have(mod)
        print(f"  {mod:12s} {'OK ' + ver if ok else 'MISSING'}")
    print("\noptional dependencies:")
    for mod, purpose in OPTIONAL.items():
        ok, ver = _have(mod)
        print(f"  {mod:12s} {'OK ' + ver if ok else 'missing':16s} ({purpose})")
    env_file = REPO_ROOT / ".env"
    print(f"\n.env file: {'present' if env_file.is_file() else 'absent (copy from .env.example)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
