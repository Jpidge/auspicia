"""Reports the first live Yahoo capture should write.

Past-week projections and the DST points-allowed exclusion need a live
response. Stat modifiers can be compared to ``gridiron_lab.toml`` as soon
as settings are in hand. Unmapped stat ids are listed, not treated as matches.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from gridiron_lab.scoring import LeagueScoring
from gridiron_lab.yahoo.parse import StatModifier

# Stat ids and names are the NFL categories in Yahoo's public settings sample.
# https://sports.yahoo.com/developer/docs/
_YARD_STATS: dict[int, tuple[str, str]] = {
    4: ("Passing Yards", "passing_yards_per_point"),
    9: ("Rushing Yards", "rushing_yards_per_point"),
    12: ("Receiving Yards", "receiving_yards_per_point"),
}
_EVENT_STATS: dict[int, tuple[str, str]] = {
    5: ("Passing Touchdowns", "passing_touchdown"),
    6: ("Interceptions", "interception"),
    10: ("Rushing Touchdowns", "rushing_touchdown"),
    11: ("Receptions", "reception"),
    13: ("Receiving Touchdowns", "receiving_touchdown"),
    15: ("Return Touchdowns", "return_touchdown"),
    16: ("2-Point Conversions", "two_point_conversion"),
    18: ("Fumbles Lost", "fumble_lost"),
    29: ("Point After Attempt Made", "extra_point_made"),
    32: ("Sack", "defense_sack"),
    33: ("Interception", "defense_interception"),
    34: ("Fumble Recovery", "defense_fumble_recovery"),
    35: ("Touchdown", "defense_touchdown"),
    36: ("Safety", "defense_safety"),
    37: ("Block Kick", "defense_blocked_kick"),
    49: ("Kickoff and Punt Return Touchdowns", "defense_return_touchdown"),
    57: ("Offensive Fumble Return TD", "offensive_fumble_return_touchdown"),
    82: ("Extra Point Returned", "defense_extra_point_return"),
}
# Inclusive upper bound of the tier. None is the open 35+ tier (stat 56).
_POINTS_ALLOWED_STATS: dict[int, tuple[str, int | None]] = {
    50: ("Points Allowed 0 points", 0),
    51: ("Points Allowed 1-6 points", 6),
    52: ("Points Allowed 7-13 points", 13),
    53: ("Points Allowed 14-20 points", 20),
    54: ("Points Allowed 21-27 points", 27),
    55: ("Points Allowed 28-34 points", 34),
    56: ("Points Allowed 35+ points", None),
}
_FIELD_GOAL_BUCKETS = {
    19: "Field Goals 0-19 Yards",
    20: "Field Goals 20-29 Yards",
    21: "Field Goals 30-39 Yards",
    22: "Field Goals 40-49 Yards",
    23: "Field Goals 50+ Yards",
}
_UNMAPPED_TOML_RULES = (
    "passing_touchdown_40_plus",
    "rushing_touchdown_40_plus",
    "receiving_touchdown_40_plus",
    "return_yards_per_point",
)
_DST_ESTIMATE = (
    "Opponent final score minus 6 for each opponent kickoff or punt return "
    "touchdown and each opponent defensive touchdown. Safeties stay in the "
    "total. The extra point after an excluded touchdown is not removed."
)


@dataclass(frozen=True)
class ComparedStat:
    stat_id: int
    name: str
    yahoo_value: float
    toml_value: float
    match: bool


def reconcile_reports(
    modifiers: tuple[StatModifier, ...],
    scoring: LeagueScoring,
    *,
    known_as_of: str,
    past_week: int | None,
    past_projection_count: int,
    past_from_fixture: bool,
) -> dict[str, object]:
    scoring_report = compare_scoring(modifiers, scoring)
    return {
        "known_as_of": known_as_of,
        "dst_points_allowed": dst_points_allowed_report(modifiers, scoring),
        "past_week_projections": past_week_report(
            past_week,
            past_projection_count,
            from_fixture=past_from_fixture,
        ),
        "scoring_settings": scoring_report,
        "lines": lines_report(),
    }


def compare_scoring(
    modifiers: tuple[StatModifier, ...],
    scoring: LeagueScoring,
) -> dict[str, object]:
    if not modifiers:
        return {
            "question": "Do Yahoo league stat modifiers match gridiron_lab.toml?",
            "status": "needs_live_settings",
            "matches": [],
            "mismatches": [],
            "unmapped_stat_ids": [],
            "toml_rules_without_yahoo_stat_id": list(_UNMAPPED_TOML_RULES),
            "shape_mismatches": [],
        }
    matches: list[dict[str, object]] = []
    mismatches: list[dict[str, object]] = []
    unmapped: list[dict[str, object]] = []
    shape: list[dict[str, object]] = []
    seen_buckets: list[int] = []
    for modifier in modifiers:
        row = _compare_one(modifier, scoring)
        if row is None:
            if modifier.stat_id in _FIELD_GOAL_BUCKETS:
                seen_buckets.append(modifier.stat_id)
                continue
            unmapped.append({"stat_id": modifier.stat_id, "yahoo_value": modifier.value})
            continue
        target = matches if row.match else mismatches
        target.append(
            {
                "stat_id": row.stat_id,
                "name": row.name,
                "yahoo_value": row.yahoo_value,
                "toml_value": row.toml_value,
            }
        )
    if seen_buckets:
        shape.append(
            {
                "stat_ids": seen_buckets,
                "yahoo_shape": "field-goal distance buckets",
                "toml_rule": "field_goal_yards_per_point",
                "note": (
                    "The toml prices total made field-goal yards divided by 10. "
                    "Yahoo's bucket modifiers are a different shape, so they are "
                    "not scored as a match or a mismatch."
                ),
            }
        )
    status = "match" if not mismatches else "mismatches"
    return {
        "question": "Do Yahoo league stat modifiers match gridiron_lab.toml?",
        "status": status,
        "matches": matches,
        "mismatches": mismatches,
        "unmapped_stat_ids": unmapped,
        "toml_rules_without_yahoo_stat_id": list(_UNMAPPED_TOML_RULES),
        "shape_mismatches": shape,
    }


def dst_points_allowed_report(
    modifiers: tuple[StatModifier, ...],
    scoring: LeagueScoring,
) -> dict[str, object]:
    tiers: list[dict[str, object]] = []
    by_id = {modifier.stat_id: modifier for modifier in modifiers}
    for stat_id, (name, upper) in _POINTS_ALLOWED_STATS.items():
        expected = _tier_points(scoring, upper)
        modifier = by_id.get(stat_id)
        yahoo_value = None if modifier is None else modifier.value
        tiers.append(
            {
                "stat_id": stat_id,
                "name": name,
                "yahoo_value": yahoo_value,
                "toml_points": expected,
                "match": yahoo_value is not None and _close(yahoo_value, expected),
            }
        )
    return {
        "question": (
            "Does Yahoo's DST points-allowed total exclude points scored by "
            "the opponent's defense or special teams?"
        ),
        "status": "needs_live_comparison",
        "why": (
            "Tier modifiers say what each bucket is worth. They do not show "
            "whether the points-allowed count Yahoo stored for a played game "
            "dropped defensive and special-teams scores. A fixture cannot "
            "answer that."
        ),
        "our_estimate": _DST_ESTIMATE,
        "tier_modifiers": tiers,
    }


def past_week_report(
    week: int | None,
    projection_count: int,
    *,
    from_fixture: bool,
) -> dict[str, object]:
    if week is None:
        fixture_status = "no_prior_week"
    elif projection_count == 0:
        fixture_status = "empty_in_fixture" if from_fixture else "empty_response"
    else:
        fixture_status = "present_in_fixture" if from_fixture else "present_in_response"
    return {
        "question": "Does the Yahoo API serve past-week projections?",
        "requested_week": week,
        "projection_points_present": projection_count,
        "fixture_status": fixture_status,
        "status": "needs_live_request",
        "why": (
            "The capture requests week W-1 player stats on the same endpoint "
            "as the current week. A recorded fixture cannot prove the live "
            "API still serves that week. The first live run records the count "
            "it actually gets."
        ),
    }


def lines_report() -> dict[str, object]:
    return {
        "status": "not_captured",
        "label": "thursday_capture",
        "why": (
            "Betting lines are design-only. Thursday 20:00 UTC is not the "
            "close. See docs/DESIGN.md section 21."
        ),
    }


def _compare_one(modifier: StatModifier, scoring: LeagueScoring) -> ComparedStat | None:
    if modifier.stat_id in _YARD_STATS:
        name, attr = _YARD_STATS[modifier.stat_id]
        per_point = int(getattr(scoring, attr))
        expected = 1.0 / per_point
        return _compared(modifier, name, expected)
    if modifier.stat_id in _EVENT_STATS:
        name, attr = _EVENT_STATS[modifier.stat_id]
        expected = float(getattr(scoring, attr))
        return _compared(modifier, name, expected)
    if modifier.stat_id in _POINTS_ALLOWED_STATS:
        name, upper = _POINTS_ALLOWED_STATS[modifier.stat_id]
        expected = _tier_points(scoring, upper)
        return _compared(modifier, name, expected)
    return None


def _compared(modifier: StatModifier, name: str, expected: float) -> ComparedStat:
    return ComparedStat(
        modifier.stat_id,
        name,
        modifier.value,
        expected,
        _close(modifier.value, expected),
    )


def _tier_points(scoring: LeagueScoring, upper: int | None) -> float:
    for tier in scoring.points_allowed:
        if tier.max_points == upper:
            return float(tier.points)
    raise ValueError(f"scoring table has no points-allowed tier ending at {upper}")


def _close(left: float, right: float) -> bool:
    return math.isclose(left, right, rel_tol=0.0, abs_tol=1e-9)
