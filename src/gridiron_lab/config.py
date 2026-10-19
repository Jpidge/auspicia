"""Load ``gridiron_lab.toml``. Secrets stay in the environment, not the file."""

from __future__ import annotations

import math
import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from gridiron_lab.logging import get_logger
from gridiron_lab.scoring import (
    YARD_KEY_TO_ATTR,
    LeagueScoring,
    PointsAllowedTier,
    ScoringError,
    event_rule_keys,
)

logger = get_logger("config")

_SECRET_KEY_NAMES = frozenset(
    {
        "token",
        "access_token",
        "refresh_token",
        "client_secret",
        "password",
        "secret",
        "api_key",
        "authorization",
    }
)
_TOP_LEVEL_KEYS = frozenset(
    {"league", "lineup", "scoring", "paths", "seasons", "models", "dashboard"}
)
_DASHBOARD_KEYS = frozenset({"anonymize_managers"})
_LEAGUE_KEYS = frozenset(
    {
        "name",
        "yahoo_id",
        "teams",
        "format",
        "ppr",
        "fractional",
        "negative_scoring",
        "scoring_version",
        "my_team",
        "my_team_id",
    }
)
_LINEUP_KEYS = frozenset({"qb", "rb", "wr", "te", "flex", "k", "dst", "bench", "ir"})
_SCORING_KEYS = frozenset({"yards_per_point", "events", "points_allowed"})


class ConfigError(Exception):
    """``gridiron_lab.toml`` is missing, unreadable, or does not match the schema."""


@dataclass(frozen=True)
class LeagueInfo:
    name: str
    yahoo_id: int
    teams: int
    format: str
    ppr: bool
    fractional: bool
    negative_scoring: bool
    scoring_version: str
    my_team: str
    my_team_id: int


@dataclass(frozen=True)
class Lineup:
    qb: int
    rb: int
    wr: int
    te: int
    flex: int
    k: int
    dst: int
    bench: int
    ir: int


@dataclass(frozen=True)
class DashboardSettings:
    """Manager-name privacy for the public dashboard export.

    ``anonymize_managers`` replaces each manager display name with
    ``Manager {team_id}`` and drops the roster file's manager string from
    the JSON. Team names are unchanged. Flip this in ``gridiron_lab.toml``.
    """

    anonymize_managers: bool


@dataclass(frozen=True)
class AppConfig:
    path: Path
    league: LeagueInfo
    lineup: Lineup
    scoring: LeagueScoring
    dashboard: DashboardSettings


def default_config_path() -> Path:
    """Find ``gridiron_lab.toml`` from the environment or by walking parents."""

    override = os.environ.get("GRIDIRON_LAB_CONFIG")
    if override:
        return Path(override)
    start = Path(__file__).resolve().parent
    for directory in (start, *start.parents):
        candidate = directory / "gridiron_lab.toml"
        if candidate.is_file():
            return candidate
    raise ConfigError(
        "Could not find gridiron_lab.toml. Set GRIDIRON_LAB_CONFIG or keep the file "
        "at the repository root."
    )


def load_config(path: Path | str | None = None) -> AppConfig:
    """Read and validate the league config. Log one INFO summary on success."""

    config_path = Path(path) if path is not None else default_config_path()
    try:
        config = _load_config(config_path)
    except ConfigError:
        logger.error("Scoring config %s is not usable", config_path)
        raise
    logger.info(
        "Loaded scoring %s · hash %s · %s",
        config.scoring.version,
        config.scoring.table_hash[:12],
        config.league.name,
    )
    return config


def _load_config(path: Path) -> AppConfig:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"Could not read {path}") from exc
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Could not parse {path}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} must be a TOML table")
    _reject_secret_keys(raw, "config")
    _exact_keys(raw, _TOP_LEVEL_KEYS, "config")
    league = _league(raw.get("league"), path)
    lineup = _lineup(raw.get("lineup"), path)
    scoring = _scoring(raw.get("scoring"), league, path)
    dashboard = _dashboard(raw.get("dashboard"), path)
    return AppConfig(
        path=path,
        league=league,
        lineup=lineup,
        scoring=scoring,
        dashboard=dashboard,
    )


def _league(raw: object, path: Path) -> LeagueInfo:
    table = _table(raw, "league", path)
    _exact_keys(table, _LEAGUE_KEYS, "league")
    fractional = _bool(table, "fractional", "league")
    if fractional is not True:
        raise ConfigError("league.fractional must be true; the rules do not define a rounding mode")
    ppr = _bool(table, "ppr", "league")
    return LeagueInfo(
        name=_text(table, "name", "league"),
        yahoo_id=_positive_int(table, "yahoo_id", "league"),
        teams=_positive_int(table, "teams", "league"),
        format=_text(table, "format", "league"),
        ppr=ppr,
        fractional=fractional,
        negative_scoring=_bool(table, "negative_scoring", "league"),
        scoring_version=_text(table, "scoring_version", "league"),
        my_team=_text(table, "my_team", "league"),
        my_team_id=_positive_int(table, "my_team_id", "league"),
    )


def _dashboard(raw: object, path: Path) -> DashboardSettings:
    table = _table(raw, "dashboard", path)
    _exact_keys(table, _DASHBOARD_KEYS, "dashboard")
    return DashboardSettings(
        anonymize_managers=_bool(table, "anonymize_managers", "dashboard"),
    )


def _lineup(raw: object, path: Path) -> Lineup:
    table = _table(raw, "lineup", path)
    _exact_keys(table, _LINEUP_KEYS, "lineup")
    values = {key: _non_negative_int(table, key, "lineup") for key in sorted(_LINEUP_KEYS)}
    return Lineup(**values)


def _scoring(raw: object, league: LeagueInfo, path: Path) -> LeagueScoring:
    table = _table(raw, "scoring", path)
    _exact_keys(table, _SCORING_KEYS, "scoring")
    yards_raw = _table(table.get("yards_per_point"), "scoring.yards_per_point", path)
    events_raw = _table(table.get("events"), "scoring.events", path)
    _exact_keys(yards_raw, set(YARD_KEY_TO_ATTR), "scoring.yards_per_point")
    _exact_keys(events_raw, set(event_rule_keys()), "scoring.events")
    tiers_raw = table.get("points_allowed")
    if not isinstance(tiers_raw, list) or not tiers_raw:
        raise ConfigError("scoring.points_allowed must be a non-empty array of tables")

    kwargs: dict[str, object] = {"version": league.scoring_version}
    for key, attr in YARD_KEY_TO_ATTR.items():
        kwargs[attr] = _positive_int(yards_raw, key, "scoring.yards_per_point")
    for key in event_rule_keys():
        kwargs[key] = _number(events_raw, key, "scoring.events")
    kwargs["points_allowed"] = tuple(
        _tier(item, index, len(tiers_raw) - 1) for index, item in enumerate(tiers_raw)
    )
    try:
        rules = LeagueScoring(**kwargs)  # type: ignore[arg-type]
    except ScoringError as exc:
        raise ConfigError(str(exc)) from exc
    except TypeError as exc:
        raise ConfigError(f"scoring table does not match the engine: {exc}") from exc

    reception = float(rules.reception)
    if league.ppr and reception == 0:
        raise ConfigError("league.ppr is true but reception points are 0")
    if not league.ppr and reception != 0:
        raise ConfigError("league.ppr is false but reception points are not 0")
    has_negative = any(float(getattr(rules, key)) < 0 for key in event_rule_keys()) or any(
        float(tier.points) < 0 for tier in rules.points_allowed
    )
    if has_negative and not league.negative_scoring:
        raise ConfigError(
            "The scoring table contains negative values but league.negative_scoring "
            "is false. This engine does not apply a zero floor."
        )
    return rules


def _tier(raw: object, index: int, last: int) -> PointsAllowedTier:
    label = f"scoring.points_allowed[{index}]"
    if not isinstance(raw, dict):
        raise ConfigError(f"{label} must be a table")
    allowed = {"points"} if index == last and "max_points" not in raw else {"points", "max_points"}
    if index != last and "max_points" not in raw:
        raise ConfigError(f"{label} must set max_points; only the last tier may be open")
    _exact_keys(raw, allowed, label)
    points = _number(raw, "points", label)
    if "max_points" not in raw:
        return PointsAllowedTier(points=points, max_points=None)
    return PointsAllowedTier(
        points=points,
        max_points=_non_negative_int(raw, "max_points", label),
    )


def _reject_secret_keys(raw: object, location: str) -> None:
    if isinstance(raw, dict):
        for key, value in raw.items():
            if not isinstance(key, str):
                continue
            normalized = key.lower()
            if (
                normalized in _SECRET_KEY_NAMES
                or normalized.endswith("_token")
                or normalized.endswith("_secret")
                or normalized.endswith("_password")
            ):
                logger.error("Rejected secret-like config key %s.%s", location, key)
                raise ConfigError(
                    f"Config key {location}.{key} looks like a secret. "
                    "Use environment variables instead of gridiron_lab.toml."
                )
            _reject_secret_keys(value, f"{location}.{key}")
        return
    if isinstance(raw, list):
        for index, item in enumerate(raw):
            _reject_secret_keys(item, f"{location}[{index}]")


def _exact_keys(
    table: Mapping[str, object],
    expected: set[str] | frozenset[str],
    label: str,
) -> None:
    keys = set(table)
    missing = sorted(expected - keys)
    unexpected = sorted(keys - expected)
    if missing or unexpected:
        raise ConfigError(f"{label} keys mismatch: missing {missing}, unexpected {unexpected}")


def _table(raw: object, label: str, path: Path) -> Mapping[str, object]:
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} is missing [{label}]")
    return raw


def _text(table: Mapping[str, object], key: str, label: str) -> str:
    value = table[key]
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{label}.{key} must be a non-empty string")
    return value


def _bool(table: Mapping[str, object], key: str, label: str) -> bool:
    value = table[key]
    if not isinstance(value, bool):
        raise ConfigError(f"{label}.{key} must be true or false")
    return value


def _positive_int(table: Mapping[str, object], key: str, label: str) -> int:
    value = _non_negative_int(table, key, label)
    if value <= 0:
        raise ConfigError(f"{label}.{key} must be a positive integer")
    return value


def _non_negative_int(table: Mapping[str, object], key: str, label: str) -> int:
    value = table[key]
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ConfigError(f"{label}.{key} must be a non-negative integer")
    return value


def _number(table: Mapping[str, object], key: str, label: str) -> float:
    value = table[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{label}.{key} must be a number")
    if isinstance(value, float) and not math.isfinite(value):
        raise ConfigError(f"{label}.{key} must be finite")
    return float(value)
