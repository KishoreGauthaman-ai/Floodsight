"""FloodSight command-line interface.

Thin wrappers over the same pipeline functions the dashboard uses: ``run``,
``dashboard``, ``report``, ``train``, ``evaluate``. A command that works in
the CLI but not the dashboard (or the reverse) is a bug (AGENT.md 8.10).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Sequence

from .config import ConfigError, load_config, load_region, runs_dir
from .pipeline import StageNotImplementedError, run_pipeline

# Exit codes: 0 ok, 2 configuration/input error, 3 stage not implemented yet.
EXIT_OK = 0
EXIT_CONFIG_ERROR = 2
EXIT_NOT_IMPLEMENTED = 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="floodsight",
        description=(
            "Satellite flood intelligence: where did the flood hit, what was "
            "damaged, who is cut off. Give it a bounding box and a flood date."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="run an analysis for an area and date")
    area = p_run.add_mutually_exclusive_group(required=True)
    area.add_argument(
        "--bbox",
        nargs=4,
        type=float,
        metavar=("LON_MIN", "LAT_MIN", "LON_MAX", "LAT_MAX"),
        help="analysis area; longitude first",
    )
    area.add_argument("--region", help="region preset from config/regions/")
    p_run.add_argument("--date", help="flood date, YYYY-MM-DD")
    p_run.add_argument(
        "--mode", choices=("fast", "full"), default=None, help="analysis mode"
    )
    p_run.add_argument(
        "--upstream",
        nargs=2,
        type=float,
        metavar=("LON", "LAT"),
        help="upstream point for the flood-path bonus (full mode)",
    )
    p_run.add_argument(
        "--judge",
        action="store_true",
        help="judge mode: enforce the demo area budget from config "
             "(judge.max_area_km2); refuses larger areas before any "
             "download starts",
    )
    p_run.set_defaults(func=_cmd_run)

    p_dash = sub.add_parser("dashboard", help="open the map dashboard")
    p_dash.set_defaults(func=_cmd_dashboard)

    p_report = sub.add_parser("report", help="generate the one-page situation report")
    p_report.add_argument("--run", required=True, help="run id under runs/")
    p_report.add_argument(
        "--lang", default="en,ne", help="comma-separated language codes (en, ne)"
    )
    p_report.set_defaults(func=_cmd_report)

    p_train = sub.add_parser("train", help="train the flood segmentation model")
    p_train.add_argument("--config", default="config/training.yaml")
    p_train.set_defaults(func=_cmd_train)

    p_eval = sub.add_parser("evaluate", help="evaluation suite")
    p_eval.add_argument(
        "what", choices=("segmentation", "emsr927", "assistant")
    )
    p_eval.add_argument("--checkpoint", help="model checkpoint path")
    p_eval.add_argument("--run", help="run id under runs/")
    # The reference folder path is resolved by the evaluation package, never
    # inside src/ (data-rule isolation, enforced by tests/test_data_rules.py).
    p_eval.add_argument(
        "--reference",
        help="EMSR927 reference folder (checking only; evaluation/ resolves the default)",
    )
    p_eval.set_defaults(func=_cmd_evaluate)

    return parser


def _cmd_run(args: argparse.Namespace) -> int:
    cfg = load_config()
    mode = args.mode or cfg.get("mode_default", "fast")
    if args.region:
        region = load_region(args.region, cfg)
        bbox = region["bbox"]
        flood_date = region["flood_date"]
        if args.date:
            flood_date = args.date
        upstream = tuple(region["upstream_point"]) if region.get("upstream_point") else None
    else:
        bbox = args.bbox
        if not args.date:
            raise ConfigError("--date is required when using --bbox")
        flood_date = args.date
        upstream = None
    if args.upstream:
        if upstream is not None:
            upstream = tuple(args.upstream)  # explicit CLI point wins
        else:
            upstream = tuple(args.upstream)
    if args.judge:
        judge_cfg = cfg.get("judge", {})
        max_km2 = float(judge_cfg.get("max_area_km2", 500.0))
        import math

        # 111.32 km per degree of latitude; longitude shrinks by cos(lat)
        width_km = (bbox[2] - bbox[0]) * 111.32 * math.cos(
            math.radians((bbox[1] + bbox[3]) / 2))
        height_km = (bbox[3] - bbox[1]) * 111.32
        area_km2 = width_km * height_km
        if area_km2 > max_km2:
            raise ConfigError(
                f"judge mode: area {area_km2:.0f} km2 exceeds the "
                f"{max_km2:.0f} km2 budget (config judge.max_area_km2). "
                "Judges should pick an area of about 20x20 km; the full "
                "corridor case study takes much longer."
            )
        print(f"judge mode: area {area_km2:.0f} km2 within {max_km2:.0f} km2 budget")
    run_dir = run_pipeline(bbox, flood_date, mode=mode, upstream=upstream, cfg=cfg)
    print(f"Run complete: {run_dir}")
    print(f"  results:    {run_dir / 'results.json'}")
    print(f"  flood map:  {run_dir / 'flood.geojson'}")
    return EXIT_OK


def _cmd_dashboard(args: argparse.Namespace) -> int:
    from pathlib import Path

    app = Path(__file__).resolve().parent / "dashboard" / "app.py"
    print("Starting FloodSight dashboard (http://localhost:8501) ...")
    return subprocess.call([sys.executable, "-m", "streamlit", "run", str(app)])


def _cmd_report(args: argparse.Namespace) -> int:
    from pathlib import Path

    from .reporting.sitrep import generate_sitrep

    langs = tuple(args.lang.split(","))
    cfg = load_config()
    run_dir = Path(args.run)
    if not (run_dir / "results.json").is_file():
        run_dir = runs_dir(cfg) / args.run
    paths = generate_sitrep(run_dir, langs=langs)
    for key, path in paths.items():
        print(f"{key}: {path}")
    return EXIT_OK


def _cmd_train(args: argparse.Namespace) -> int:
    raise StageNotImplementedError(
        "Training is not implemented yet (Phase 7). It requires the Kuro Siwo "
        "dataset, which is not yet downloaded (see KNOWN_ISSUES K3)."
    )


def _cmd_evaluate(args: argparse.Namespace) -> int:
    raise StageNotImplementedError(
        f"Evaluation '{args.what}' is not implemented yet "
        "(Phase 7 for segmentation, Phase 11 for emsr927, Phase 9 for "
        "assistant). Reference data is read only by this evaluation package."
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (ConfigError, FileNotFoundError) as exc:
        print(f"Input error: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    except StageNotImplementedError as exc:
        print(f"Not implemented: {exc}", file=sys.stderr)
        return EXIT_NOT_IMPLEMENTED


if __name__ == "__main__":
    sys.exit(main())
