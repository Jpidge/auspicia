"""League scoring for Fantasy Football Coalition.

Ported from the previous tool's scoring module and cleaned up:

- rates come from the ``[scoring]`` table in ``gridiron_lab.toml``
- negative totals are kept (there is no zero floor)
- omitted play-level events stay unavailable instead of an observed zero
- a missing points-allowed value is not treated as a shutout

The engine scores stat lines. It does not read play-by-play or weekly feeds.
Canonical field names match the previous scoring module. Unknown keys, such as
``player_id``, are ignored. ``interceptions`` means a pass thrown on offense
and a takeaway on defense; pick the function that matches the unit.

A 40+ touchdown bonus is added on top of the 6-point touchdown. The bonus
count cannot exceed the matching touchdown count.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal

SCORING_VERSION = "ffc_yahoo_v1"

OmitPolicy = Literal["zero", "unavailable"]
StatMode = Literal["yards", "count", "points_allowed"]


class ScoringError(ValueError):
    """A stat line or scoring table cannot be scored as given."""


@dataclass(frozen=True)
class PointsAllowedTier:
    """One DST points-allowed tier.

    ``max_points`` is an inclusive upper bound. ``None`` means the tier has
    no upper bound and must be last.
    """

    points: float
    max_points: int | None = None


@dataclass(frozen=True)
class StatField:
    """One stat-line column and the rule attribute that prices it."""

    name: str
    rule: str
    omit: OmitPolicy
    mode: StatMode
    allow_negative: bool = False
    touchdown: bool = False


OFFENSE_FIELDS: tuple[StatField, ...] = (
    StatField("passing_yards", "passing_yards_per_point", "zero", "yards", allow_negative=True),
    StatField("passing_tds", "passing_touchdown", "zero", "count", touchdown=True),
    StatField("interceptions", "interception", "zero", "count"),
    StatField(
        "passing_tds_40_plus",
        "passing_touchdown_40_plus",
        "unavailable",
        "count",
        touchdown=True,
    ),
    StatField("rushing_yards", "rushing_yards_per_point", "zero", "yards", allow_negative=True),
    StatField("rushing_tds", "rushing_touchdown", "zero", "count", touchdown=True),
    StatField(
        "rushing_tds_40_plus",
        "rushing_touchdown_40_plus",
        "unavailable",
        "count",
        touchdown=True,
    ),
    StatField("receptions", "reception", "zero", "count"),
    StatField(
        "receiving_yards",
        "receiving_yards_per_point",
        "zero",
        "yards",
        allow_negative=True,
    ),
    StatField("receiving_tds", "receiving_touchdown", "zero", "count", touchdown=True),
    StatField(
        "receiving_tds_40_plus",
        "receiving_touchdown_40_plus",
        "unavailable",
        "count",
        touchdown=True,
    ),
    StatField("fumbles_lost", "fumble_lost", "zero", "count"),
    StatField("two_point", "two_point_conversion", "zero", "count"),
    StatField(
        "return_yards",
        "return_yards_per_point",
        "unavailable",
        "yards",
        allow_negative=True,
    ),
    StatField("return_tds", "return_touchdown", "unavailable", "count", touchdown=True),
    StatField(
        "offensive_fumble_return_tds",
        "offensive_fumble_return_touchdown",
        "unavailable",
        "count",
        touchdown=True,
    ),
    StatField("extra_points_made", "extra_point_made", "unavailable", "count"),
    StatField("field_goal_yards_made", "field_goal_yards_per_point", "unavailable", "yards"),
)

DEFENSE_FIELDS: tuple[StatField, ...] = (
    StatField("sacks", "defense_sack", "zero", "count"),
    StatField("interceptions", "defense_interception", "zero", "count"),
    StatField("fumble_recoveries", "defense_fumble_recovery", "zero", "count"),
    StatField("defense_tds", "defense_touchdown", "zero", "count", touchdown=True),
    StatField("safeties", "defense_safety", "zero", "count"),
    StatField("blocked_kicks", "defense_blocked_kick", "unavailable", "count"),
    StatField(
        "return_yards",
        "return_yards_per_point",
        "unavailable",
        "yards",
        allow_negative=True,
    ),
    StatField("return_tds", "defense_return_touchdown", "unavailable", "count", touchdown=True),
    StatField("extra_point_returns", "defense_extra_point_return", "unavailable", "count"),
    StatField("points_allowed", "", "unavailable", "points_allowed"),
)

_LONG_TOUCHDOWN_PAIRS: tuple[tuple[str, str], ...] = (
    ("passing_tds", "passing_tds_40_plus"),
    ("rushing_tds", "rushing_tds_40_plus"),
    ("receiving_tds", "receiving_tds_40_plus"),
)

YARD_KEY_TO_ATTR: dict[str, str] = {
    "passing": "passing_yards_per_point",
    "rushing": "rushing_yards_per_point",
    "receiving": "receiving_yards_per_point",
    "return": "return_yards_per_point",
    "field_goal": "field_goal_yards_per_point",
}


def event_rule_keys() -> tuple[str, ...]:
    """Rule attributes the ``[scoring.events]`` table must contain, in order."""

    seen: list[str] = []
    for spec in (*OFFENSE_FIELDS, *DEFENSE_FIELDS):
        if spec.mode == "count" and spec.rule not in seen:
            seen.append(spec.rule)
    return tuple(seen)


@dataclass(frozen=True)
class LeagueScoring:
    """Rates and DST tiers the engine multiplies. Loaded from config."""

    version: str
    passing_yards_per_point: int
    rushing_yards_per_point: int
    receiving_yards_per_point: int
    return_yards_per_point: int
    field_goal_yards_per_point: int
    passing_touchdown: float
    interception: float
    passing_touchdown_40_plus: float
    rushing_touchdown: float
    rushing_touchdown_40_plus: float
    reception: float
    receiving_touchdown: float
    receiving_touchdown_40_plus: float
    return_touchdown: float
    two_point_conversion: float
    fumble_lost: float
    offensive_fumble_return_touchdown: float
    extra_point_made: float
    defense_sack: float
    defense_interception: float
    defense_fumble_recovery: float
    defense_touchdown: float
    defense_safety: float
    defense_blocked_kick: float
    defense_return_touchdown: float
    defense_extra_point_return: float
    points_allowed: tuple[PointsAllowedTier, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.version, str) or not self.version.strip():
            raise ScoringError("scoring version must be a non-empty string")
        yard_specs = (*OFFENSE_FIELDS, *DEFENSE_FIELDS)
        yard_attrs = {spec.rule for spec in yard_specs if spec.mode == "yards"}
        for name in yard_attrs:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ScoringError(f"{name} must be a positive integer")
        for name in event_rule_keys():
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ScoringError(f"{name} must be a number")
            if not math.isfinite(float(value)):
                raise ScoringError(f"{name} must be finite")
        if not self.points_allowed:
            raise ScoringError("points-allowed tiers are empty")
        previous: int | None = None
        last = len(self.points_allowed) - 1
        for index, tier in enumerate(self.points_allowed):
            if not isinstance(tier, PointsAllowedTier):
                raise ScoringError("points-allowed tiers must be PointsAllowedTier values")
            if isinstance(tier.points, bool) or not isinstance(tier.points, (int, float)):
                raise ScoringError("tier points must be numeric")
            if not math.isfinite(float(tier.points)):
                raise ScoringError("tier points must be finite")
            if tier.max_points is None:
                if index != last:
                    raise ScoringError("only the last points-allowed tier may be open")
                continue
            if isinstance(tier.max_points, bool) or not isinstance(tier.max_points, int):
                raise ScoringError("max_points must be an integer")
            if tier.max_points < 0:
                raise ScoringError("max_points must be zero or positive")
            if previous is not None and tier.max_points <= previous:
                raise ScoringError("points-allowed upper bounds must increase")
            previous = tier.max_points

    def scoring_table(self) -> dict[str, object]:
        """Canonical table contents. The version string is not included."""

        yards: dict[str, int] = {}
        events: dict[str, int | float] = {}
        for spec in (*OFFENSE_FIELDS, *DEFENSE_FIELDS):
            if spec.mode == "yards":
                yards[spec.rule] = getattr(self, spec.rule)
            elif spec.mode == "count":
                events[spec.rule] = _canon_number(getattr(self, spec.rule))
        return {
            "yards_per_point": yards,
            "events": events,
            "points_allowed": [
                {"max_points": tier.max_points, "points": _canon_number(tier.points)}
                for tier in self.points_allowed
            ],
        }

    @property
    def table_hash(self) -> str:
        """SHA-256 of the scoring table. A mismatch must refuse a model load."""

        payload = json.dumps(self.scoring_table(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True, eq=False)
class ScoreResult:
    """Points for one stat line.

    ``components`` holds observed categories only. A category listed in
    ``unavailable`` was not in the stat line (or was null) and was not
    recorded as zero. ``fantasy_points`` sums observed components and is
    allowed to be negative.
    """

    fantasy_points: float
    touchdown_points: float
    components: Mapping[str, float] = field(hash=False)
    unavailable: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        return not self.unavailable


def require_scoring_hash(rules: LeagueScoring, expected_hash: str) -> None:
    """Refuse ``rules`` when the table hash is not ``expected_hash``."""

    actual = rules.table_hash
    if not isinstance(expected_hash, str) or actual != expected_hash:
        raise ScoringError(
            f"scoring table hash {actual} does not match required {expected_hash} "
            f"for {rules.version}"
        )


def default_rules() -> LeagueScoring:
    """Load the league scoring table from ``gridiron_lab.toml``."""

    from gridiron_lab.config import load_config

    return load_config().scoring


def defense_points_allowed(
    points_allowed: float,
    rules: LeagueScoring | None = None,
) -> float:
    """Return the tier value for an integer points-allowed total."""

    if rules is None:
        rules = default_rules()
    value, missing = _read_stat(
        {"points_allowed": points_allowed},
        "points_allowed",
        omit="unavailable",
        allow_negative=True,
    )
    if missing or value is None:
        raise ScoringError("points_allowed is required")
    if not value.is_integer():
        raise ScoringError(
            "points_allowed must be an integer; "
            f"the league table does not define {points_allowed!r}"
        )
    if value < 0:
        raise ScoringError(
            "points_allowed must be zero or positive; "
            "the league table does not define negative points allowed"
        )
    whole = int(value)
    for tier in rules.points_allowed:
        if tier.max_points is None or whole <= tier.max_points:
            return float(tier.points)
    raise ScoringError(f"points_allowed {whole} is above the configured tiers")


def score_offense(
    stat_line: Mapping[str, object],
    rules: LeagueScoring | None = None,
) -> ScoreResult:
    """Score one offensive or kicker stat line. Negative totals are kept."""

    if rules is None:
        rules = default_rules()
    return _score_fields(stat_line, rules, OFFENSE_FIELDS, DEFENSE_FIELDS)


def score_defense(
    stat_line: Mapping[str, object],
    rules: LeagueScoring | None = None,
) -> ScoreResult:
    """Score one DST stat line. Negative totals are kept."""

    if rules is None:
        rules = default_rules()
    return _score_fields(stat_line, rules, DEFENSE_FIELDS, OFFENSE_FIELDS)


def score_offensive_frame(
    rows: Iterable[Mapping[str, object]],
    rules: LeagueScoring | None = None,
) -> list[ScoreResult]:
    """Score an iterable of offensive stat lines, in order.

    This is the batch form of the previous DataFrame scorer. Pass mappings,
    not a columnar frame.
    """

    if isinstance(rows, (str, bytes, Mapping)):
        raise ScoringError("score_offensive_frame expects an iterable of stat lines")
    materialized = list(rows)
    if not materialized:
        return []
    first = materialized[0]
    if isinstance(first, str) or not isinstance(first, Mapping):
        raise ScoringError("score_offensive_frame expects stat-line mappings, not a columnar frame")
    if rules is None:
        rules = default_rules()
    return [score_offense(row, rules) for row in materialized]


def _score_fields(
    stat_line: Mapping[str, object],
    rules: LeagueScoring,
    fields: tuple[StatField, ...],
    other_fields: tuple[StatField, ...],
) -> ScoreResult:
    if not isinstance(stat_line, Mapping):
        raise ScoringError("stat line must be a mapping")
    _reject_mixed_unit(stat_line, fields, other_fields)
    components: dict[str, float] = {}
    unavailable: list[str] = []
    counts: dict[str, float] = {}
    for spec in fields:
        value, missing = _read_stat(
            stat_line,
            spec.name,
            omit=spec.omit,
            allow_negative=spec.allow_negative or spec.mode == "points_allowed",
        )
        if missing or value is None:
            unavailable.append(spec.name)
            continue
        counts[spec.name] = value
        if spec.mode == "yards":
            per_point = getattr(rules, spec.rule)
            points = value / per_point
        elif spec.mode == "count":
            points = value * float(getattr(rules, spec.rule))
        elif spec.mode == "points_allowed":
            points = defense_points_allowed(value, rules)
        else:
            raise ScoringError(f"unknown scoring mode {spec.mode}")
        components[spec.name] = points
    _check_long_touchdowns(counts, unavailable, {spec.name for spec in fields})
    touchdown_keys = [spec.name for spec in fields if spec.touchdown and spec.name in components]
    return ScoreResult(
        fantasy_points=math.fsum(components.values()),
        touchdown_points=math.fsum(components[key] for key in touchdown_keys),
        components=MappingProxyType(components),
        unavailable=tuple(unavailable),
    )


def _reject_mixed_unit(
    stat_line: Mapping[str, object],
    fields: tuple[StatField, ...],
    other_fields: tuple[StatField, ...],
) -> None:
    own = {spec.name for spec in fields}
    foreign = {spec.name for spec in other_fields} - own
    mixed = sorted(name for name in stat_line if name in foreign)
    if mixed:
        joined = ", ".join(mixed)
        raise ScoringError(f"stat line mixes offense and defense fields: {joined}")


def _check_long_touchdowns(
    counts: Mapping[str, float],
    unavailable: list[str],
    field_names: set[str],
) -> None:
    missing = set(unavailable)
    for touchdown_name, bonus_name in _LONG_TOUCHDOWN_PAIRS:
        if bonus_name not in field_names or bonus_name in missing:
            continue
        bonus = counts[bonus_name]
        touchdowns = counts.get(touchdown_name, 0.0)
        if bonus > touchdowns:
            raise ScoringError(
                f"{bonus_name} ({bonus}) exceeds {touchdown_name} ({touchdowns}); "
                "the 40+ bonus is added on top of the touchdown, not instead of it"
            )


def _read_stat(
    stat_line: Mapping[str, object],
    name: str,
    *,
    omit: OmitPolicy,
    allow_negative: bool,
) -> tuple[float | None, bool]:
    """Return ``(value, unavailable)``.

    Omitted core counts are an observed zero. Omitted event fields, nulls,
    and NaN are unavailable and are not coerced to zero.
    """

    if name not in stat_line:
        if omit == "zero":
            return 0.0, False
        return None, True
    raw = stat_line[name]
    if raw is None:
        return None, True
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ScoringError(f"{name} must be a number, got {type(raw).__name__}")
    value = float(raw)
    if math.isnan(value):
        return None, True
    if not math.isfinite(value):
        raise ScoringError(f"{name} must be finite")
    if value < 0 and not allow_negative:
        raise ScoringError(f"{name} must be zero or positive")
    return value, False


def _canon_number(value: float) -> int | float:
    number = float(value)
    if number.is_integer():
        return int(number)
    return number
