"""Command line.

``python -m gridiron_lab ingest --season 2025``

``python -m gridiron_lab features --season 2024 --week 1``

``python -m gridiron_lab evaluate --season 2018-2025 --test-season 2022-2025 --output results.json``

``python -m gridiron_lab export-dashboard --rosters examples/dashboard/rosters.json
--output site/dashboard.json``
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from gridiron_lab.config import ConfigError, default_config_path, load_config
from gridiron_lab.dashboard import (
    DashboardError,
    export_dashboard,
    lab_block_from_report,
    load_json,
)
from gridiron_lab.data.errors import FetchError, IngestError
from gridiron_lab.data.fetch import FixtureFetch, HttpFetch
from gridiron_lab.data.ingest import ingest
from gridiron_lab.evaluate import EvalError, run_evaluation
from gridiron_lab.features import FeatureError, build_features
from gridiron_lab.lineup import LineupError
from gridiron_lab.logging import configure_logging, get_logger
from gridiron_lab.scoring import ScoringError

logger = get_logger("cli")


def main(argv: list[str] | None = None) -> None:
    sys.exit(run(argv))


def run(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = _parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        code = exc.code
        return int(code) if isinstance(code, int) else 2
    if args.command == "ingest":
        return _run_ingest(args)
    if args.command == "features":
        return _run_features(args)
    if args.command == "evaluate":
        return _run_evaluate(args)
    if args.command == "export-dashboard":
        return _run_export_dashboard(args)
    parser.print_help()
    return 2


def _run_ingest(args: argparse.Namespace) -> int:
    try:
        seasons = parse_seasons(args.seasons)
        data_dir = _data_dir(args.data_dir, args.config)
        fetch: FixtureFetch | HttpFetch
        if args.fixture_dir is not None:
            fetch = FixtureFetch(args.fixture_dir)
        else:
            fetch = HttpFetch(data_dir / "cache")
        ingest(seasons=seasons, data_dir=data_dir, fetch=fetch)
    except ConfigError as exc:
        logger.error("%s", exc)
        return 2
    except FetchError as exc:
        logger.error("%s", exc)
        return 1
    except IngestError as exc:
        logger.error("%s", exc)
        return 1
    except ScoringError as exc:
        logger.error("%s", exc)
        return 1
    except OSError as exc:
        logger.error("Could not write the data directory: %s", exc.strerror or exc)
        return 1
    return 0


def _run_features(args: argparse.Namespace) -> int:
    try:
        data_dir = _data_dir(args.data_dir, args.config)
        rows = build_features(
            args.season,
            args.week,
            args.policy,
            data_dir=data_dir,
            season_type=args.season_type,
        )
    except ConfigError as exc:
        logger.error("%s", exc)
        return 2
    except FeatureError as exc:
        logger.error("%s", exc)
        return 2
    except IngestError as exc:
        logger.error("%s", exc)
        return 1
    except OSError as exc:
        logger.error("Could not read the data directory: %s", exc.strerror or exc)
        return 1
    logger.info("Feature rows %s", len(rows))
    return 0


def _run_evaluate(args: argparse.Namespace) -> int:
    try:
        seasons = parse_seasons(args.seasons)
        test_seasons = parse_seasons(args.test_seasons)
        data_dir = _data_dir(args.data_dir, args.config)
        config = load_config(args.config)
        run_evaluation(
            data_dir=data_dir,
            seasons=seasons,
            test_seasons=test_seasons,
            output=args.output,
            seed=args.seed,
            n_boot=args.bootstrap,
            teams=config.league.teams,
            lineup=config.lineup,
        )
    except ConfigError as exc:
        logger.error("%s", exc)
        return 2
    except EvalError as exc:
        logger.error("%s", exc)
        return 2
    except IngestError as exc:
        logger.error("%s", exc)
        return 2
    except FeatureError as exc:
        logger.error("%s", exc)
        return 2
    except OSError as exc:
        logger.error("Could not write the evaluation file: %s", exc.strerror or exc)
        return 1
    return 0


def _run_export_dashboard(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.config)
        lab = None
        if args.lab_report is not None:
            lab = lab_block_from_report(load_json(args.lab_report))
        export_dashboard(
            config=config,
            rosters_path=args.rosters,
            output=args.output,
            predictions_path=args.predictions,
            lab=lab,
            data_dir=args.data_dir,
        )
    except ConfigError as exc:
        logger.error("%s", exc)
        return 2
    except (DashboardError, LineupError, FeatureError) as exc:
        logger.error("%s", exc)
        return 2
    except OSError as exc:
        logger.error("Could not write the dashboard file: %s", exc.strerror or exc)
        return 1
    logger.info("Wrote dashboard %s", args.output)
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gridiron-lab")
    sub = parser.add_subparsers(dest="command")
    ingest_parser = sub.add_parser("ingest", help="Load NFLverse seasons into Parquet")
    ingest_parser.add_argument(
        "--season",
        action="append",
        dest="seasons",
        required=True,
        help="Season year, or a range such as 2018-2025. Repeat to pass several.",
    )
    ingest_parser.add_argument(
        "--data-dir",
        type=Path,
        help="Directory for Parquet and DuckDB. Defaults to the parent of paths.raw.",
    )
    ingest_parser.add_argument("--config", type=Path, help="Path to gridiron_lab.toml")
    ingest_parser.add_argument(
        "--fixture-dir",
        type=Path,
        help="Read CSV fixtures instead of downloading NFLverse. For tests and local replay.",
    )
    features_parser = sub.add_parser("features", help="Log a Week W feature build; writes nothing")
    features_parser.add_argument("--season", type=int, required=True)
    features_parser.add_argument("--week", type=int, required=True)
    features_parser.add_argument(
        "--policy",
        default="simulated_capture",
        choices=["capture", "simulated_capture"],
    )
    features_parser.add_argument("--season-type", default="REG")
    features_parser.add_argument(
        "--data-dir",
        type=Path,
        help="Directory for Parquet and DuckDB. Defaults to the parent of paths.raw.",
    )
    features_parser.add_argument("--config", type=Path, help="Path to gridiron_lab.toml")
    evaluate_parser = sub.add_parser(
        "evaluate",
        help="Rolling-origin backtest. Writes a JSON results file and no model artifact.",
    )
    evaluate_parser.add_argument(
        "--season",
        action="append",
        dest="seasons",
        required=True,
        help="Seasons to build, including training history. Example: 2018-2025.",
    )
    evaluate_parser.add_argument(
        "--test-season",
        action="append",
        dest="test_seasons",
        required=True,
        help="Seasons to score. Example: 2022-2025.",
    )
    evaluate_parser.add_argument(
        "--data-dir",
        type=Path,
        help="Directory for Parquet and DuckDB. Defaults to the parent of paths.raw.",
    )
    evaluate_parser.add_argument("--config", type=Path, help="Path to gridiron_lab.toml")
    evaluate_parser.add_argument("--output", type=Path, required=True, help="JSON results path")
    evaluate_parser.add_argument("--seed", type=int, default=0)
    evaluate_parser.add_argument(
        "--bootstrap",
        type=int,
        default=1000,
        help="Week resamples for the MAE-difference interval.",
    )
    export_parser = sub.add_parser(
        "export-dashboard",
        help="Write dashboard JSON from a roster file. Does not call Yahoo.",
    )
    export_parser.add_argument("--rosters", type=Path, required=True, help="Roster JSON input")
    export_parser.add_argument("--output", type=Path, required=True, help="Dashboard JSON path")
    export_parser.add_argument(
        "--predictions",
        type=Path,
        help="Optional predictions JSON. Overrides q10, q50, q90, mean, and yahoo.",
    )
    export_parser.add_argument(
        "--lab-report",
        type=Path,
        help=(
            "Optional evaluate report JSON. The Lab block is generated from "
            "the report instead of the published M3.1 backtest."
        ),
    )
    export_parser.add_argument("--config", type=Path, help="Path to gridiron_lab.toml")
    export_parser.add_argument(
        "--data-dir",
        type=Path,
        help=(
            "Point-in-time store for recent_points and snap_share. "
            "Omit to copy those fields from the roster file."
        ),
    )
    return parser


def _data_dir(override: Path | None, config_path: Path | None) -> Path:
    if override is not None:
        return override
    path = config_path if config_path is not None else default_config_path()
    try:
        import tomllib

        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Could not parse {path}") from exc
    except OSError as exc:
        raise ConfigError(f"Could not read {path}") from exc
    paths = raw.get("paths") if isinstance(raw, dict) else None
    raw_value = "data/raw"
    if isinstance(paths, dict) and isinstance(paths.get("raw"), str):
        raw_value = paths["raw"]
    raw_path = Path(raw_value)
    if not raw_path.is_absolute():
        raw_path = path.parent / raw_path
    return raw_path.parent


def parse_seasons(values: list[str]) -> list[int]:
    """Accept ``2025`` and ``2018-2025``, preserving order and dropping repeats."""

    seasons: list[int] = []
    for value in values:
        if re.fullmatch(r"\d{4}", value):
            seasons.append(int(value))
            continue
        if re.fullmatch(r"\d{4}-\d{4}", value):
            start_text, end_text = value.split("-", 1)
            start, end = int(start_text), int(end_text)
            if end < start:
                raise IngestError(f"season range {value} is reversed")
            seasons.extend(range(start, end + 1))
            continue
        raise IngestError(f"season must be YYYY or YYYY-YYYY, got {value}")
    ordered: list[int] = []
    seen: set[int] = set()
    for season in seasons:
        if season in seen:
            continue
        seen.add(season)
        ordered.append(season)
    return ordered
