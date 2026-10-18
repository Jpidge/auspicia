"""Command line.

``python -m gridiron_lab ingest --season 2025``

``python -m gridiron_lab features --season 2024 --week 1``
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from gridiron_lab.config import ConfigError, default_config_path
from gridiron_lab.data.errors import FetchError, IngestError
from gridiron_lab.data.fetch import FixtureFetch, HttpFetch
from gridiron_lab.data.ingest import ingest
from gridiron_lab.features import FeatureError, build_features
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
