#!/usr/bin/env bash
# FloodSight — one-command launcher.
#
# Verifies the environment, then starts the map dashboard (Streamlit) on a
# local port. The dashboard can browse every completed run under runs/ and
# can start NEW real analyses from the sidebar (bbox + date -> live stages).
#
# Usage:
#   ./run.sh                     # checks + dashboard on port 8520
#   ./run.sh --port 8501         # different port
#   ./run.sh --run-case-study    # ALSO run the Trishuli fast-mode case study
#                                # first (~30 min, mostly model inference;
#                                # skips everything already cached)
#   ./run.sh --skip-checks       # start the dashboard immediately
#
# Stop with Ctrl-C.

set -uo pipefail   # no -e: soft warnings must not stop the launcher

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PORT=8520
RUN_CASE_STUDY=0
SKIP_CHECKS=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --port) PORT="${2:?--port needs a value}"; shift 2 ;;
    --run-case-study) RUN_CASE_STUDY=1; shift ;;
    --skip-checks) SKIP_CHECKS=1; shift ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    *) echo "unknown option: $1 (see --help)"; exit 2 ;;
  esac
done

PY=".venv/bin/python"
APP="src/floodsight/dashboard/app.py"

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
info() { printf '  [info] %s\n' "$*"; }
warn() { printf '  \033[33m[warn]\033[0m %s\n' "$*"; }
fail() { printf '  \033[31m[fail]\033[0m %s\n' "$*"; }

echo
bold "FloodSight launcher"
echo "-----------------------------------------------------------"

# --- 1. virtual environment -------------------------------------------------
if [[ ! -x "$PY" ]]; then
  fail "virtual environment not found at .venv/"
  cat <<'EOF'

  First-time setup on this machine:
      uv venv
      uv sync --extra dev --extra dashboard --extra graph
      uv pip install torch --index-url https://download.pytorch.org/whl/cpu
      uv pip install torchvision --index-url https://download.pytorch.org/whl/cpu
      uv pip install segmentation-models-pytorch
      cp .env.example .env        # then add CDSE credentials
      sudo apt-get install -y osmium-tool fonts-noto-core fonts-dejavu-core

  Then re-run ./run.sh
EOF
  exit 1
fi
info "virtual environment: $("$PY" --version) at .venv/"

# --- 2. import checks --------------------------------------------------------
if [[ $SKIP_CHECKS -eq 0 ]]; then
  info "checking imports (floodsight, streamlit, folium, geopandas, rasterio)..."
  if "$PY" - <<'PYEOF' >/dev/null 2>&1
import floodsight, streamlit, folium, geopandas, rasterio  # noqa: F401
PYEOF
  then
    info "imports OK"
  else
    fail "core imports failed; run:  uv sync --extra dev --extra dashboard --extra graph"
    exit 1
  fi

  # .env / credentials
  if [[ -f .env ]]; then
    if grep -qE "^CDSE_USERNAME=.+" .env && grep -qE "^CDSE_PASSWORD=.+" .env; then
      info "CDSE credentials present (.env) - downloads enabled"
    else
      warn ".env exists but CDSE credentials are empty: new analyses cannot download scenes"
    fi
  else
    warn "no .env found: new analyses cannot download scenes (cached runs still open fine)"
  fi

  # ESA SNAP (radar preprocessing)
  if "$PY" -c "from floodsight.config import load_config; \
from floodsight.preprocessing.s1 import snap_gpt_path; \
import os; exit(0 if os.path.isfile(snap_gpt_path(load_config())) else 1)" 2>/dev/null; then
    info "ESA SNAP gpt found - radar preprocessing enabled"
  else
    warn "ESA SNAP not found: new analyses will fail at the preprocess stage"
  fi

  # model checkpoint
  if [[ -f src/floodsight/detection/checkpoints/best_model.pt ]]; then
    info "trained U-Net checkpoint present (flood IoU 0.630 on held-out events)"
  else
    warn "no model checkpoint: the pipeline falls back to the log-ratio baseline"
  fi

  # completed runs to browse
  if compgen -G "runs/*/results.json" > /dev/null; then
    info "completed runs available in the dashboard:"
    for d in runs/*/; do
      [[ -f "$d/results.json" ]] && echo "        - $(basename "$d")"
    done
  else
    warn "no completed runs yet: start one from the dashboard sidebar"
  fi
fi

# --- 3. optional full case-study run ----------------------------------------
if [[ $RUN_CASE_STUDY -eq 1 ]]; then
  echo "-----------------------------------------------------------"
  bold "Running the Trishuli case study (fast mode)"
  echo "  This takes ~30 minutes end to end (mostly U-Net inference on CPU)."
  echo "  Cached stages (scene pair, DEM, OSM snapshot, SNAP outputs) are skipped."
  if "$PY" - <<'PYEOF'
import logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
from floodsight.config import load_config
from floodsight.pipeline import run_pipeline
run_pipeline([84.39, 27.68, 85.73, 28.31], "2026-08-26", mode="fast",
             cfg=load_config(), region="nepal")
print("case study complete")
PYEOF
  then
    info "case study finished - it will appear in the dashboard's run selector"
  else
    warn "case study run failed; the dashboard still starts so you can inspect the run folder"
  fi
fi

# --- 4. port handling ---------------------------------------------------------
# kill a previous FloodSight dashboard on this port (only OUR app.py)
if command -v ss >/dev/null; then
  OLD_PIDS=$(ss -ltnp 2>/dev/null | awk -v p=":$PORT" '$4 ~ p {print $NF}' \
             | grep -oP 'pid=\K[0-9]+' | sort -u || true)
  for pid in $OLD_PIDS; do
    if ps -p "$pid" -o args= 2>/dev/null | grep -q "floodsight/dashboard/app.py"; then
      warn "stopping a previous FloodSight dashboard (pid $pid) on port $PORT"
      kill "$pid" 2>/dev/null || true
      sleep 2
    else
      fail "port $PORT is used by another program (pid $pid); use --port to pick another"
      exit 1
    fi
  done
fi

# --- 5. launch the dashboard --------------------------------------------------
echo "-----------------------------------------------------------"
bold "Starting the dashboard on http://localhost:$PORT"
echo "  (also reachable on your machine's IP / SSH tunnel)"
echo "  Open a run in the sidebar, or start a new analysis with a bbox + date."
echo "  Press Ctrl-C here to stop."
echo

"$PY" -m streamlit run "$APP" \
  --server.headless true \
  --server.address 0.0.0.0 \
  --server.port "$PORT" \
  --browser.gatherUsageStats false \
  --global.developmentMode false &
DASH_PID=$!

trap 'kill "$DASH_PID" 2>/dev/null; echo; bold "dashboard stopped"; exit 0' INT TERM

# wait until it answers
for _ in $(seq 1 40); do
  if curl -s -o /dev/null "http://localhost:$PORT"; then
    echo
    bold "dashboard is live:  http://localhost:$PORT"
    echo "  demo walkthrough: docs/demo_script.md"
    break
  fi
  sleep 1
done

wait "$DASH_PID"
