"""Hand-checked Fantasy Football Coalition scoring.

Expected points are derived from the league rules, not from the engine:

- 25 passing yards = 1; 10 rushing or receiving yards = 1; 25 return yards = 1
- touchdown = 6, and a 40+ yard passing/rushing/receiving touchdown adds 1
- interception = -2; fumble lost = -2; two-point conversion = 2; reception = 0
- return touchdown = 6; offensive fumble-return touchdown = 6
- PAT made = 1; made field-goal yards / 10
- DST: sack 1, interception 2, fumble recovery 2, touchdown 6, safety 2,
  blocked kick 2, return yards 1/25, kickoff/punt return touchdown 6,
  extra-point return 2
- points allowed: 0 → 10, 1–6 → 7, 7–13 → 4, 14–20 → 1, 21–27 → 0,
  28–34 → -1, 35+ → -4
"""

from __future__ import annotations

import math
from dataclasses import replace

import pytest

from gridiron_lab.scoring import (
    DEFENSE_FIELDS,
    OFFENSE_FIELDS,
    LeagueScoring,
    PointsAllowedTier,
    ScoringError,
    defense_points_allowed,
    require_scoring_hash,
    score_defense,
    score_offense,
    score_offensive_frame,
)

_OFFENSE_EVENTS = tuple(spec.name for spec in OFFENSE_FIELDS if spec.omit == "unavailable")
_DEFENSE_EVENTS = tuple(spec.name for spec in DEFENSE_FIELDS if spec.omit == "unavailable")


def _approx(actual: float, expected: float) -> None:
    assert actual == pytest.approx(expected, abs=1e-9)


def _offense(**stats: float) -> dict[str, float]:
    line = dict.fromkeys(_OFFENSE_EVENTS, 0.0)
    line.update(stats)
    return line


def _defense(**stats: float) -> dict[str, float]:
    line = dict.fromkeys(_DEFENSE_EVENTS, 0.0)
    line.update(stats)
    return line


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("passing_yards", 25, 1.0),
        ("passing_yards", 1, 0.04),
        ("passing_yards", 0, 0.0),
        ("passing_yards", -25, -1.0),
        ("passing_tds", 1, 6.0),
        ("passing_tds", 2, 12.0),
        ("interceptions", 1, -2.0),
        ("interceptions", 3, -6.0),
        ("rushing_yards", 10, 1.0),
        ("rushing_yards", 7, 0.7),
        ("rushing_yards", -3, -0.3),
        ("rushing_tds", 1, 6.0),
        ("receptions", 9, 0.0),
        ("receiving_yards", 10, 1.0),
        ("receiving_yards", 1, 0.1),
        ("receiving_tds", 1, 6.0),
        ("fumbles_lost", 1, -2.0),
        ("two_point", 1, 2.0),
        ("two_point", 2, 4.0),
        ("return_yards", 25, 1.0),
        ("return_yards", 10, 0.4),
        ("return_tds", 1, 6.0),
        ("offensive_fumble_return_tds", 1, 6.0),
        ("extra_points_made", 1, 1.0),
        ("extra_points_made", 4, 4.0),
        ("field_goal_yards_made", 10, 1.0),
        ("field_goal_yards_made", 53, 5.3),
        ("field_goal_yards_made", 0, 0.0),
    ],
)
def test_offensive_rule_component(
    field: str,
    value: float,
    expected: float,
    rules: LeagueScoring,
) -> None:
    result = score_offense(_offense(**{field: value}), rules)
    _approx(result.components[field], expected)
    assert result.complete


@pytest.mark.parametrize(
    ("touchdowns", "bonus_field", "bonus"),
    [
        ("passing_tds", "passing_tds_40_plus", 1.0),
        ("rushing_tds", "rushing_tds_40_plus", 1.0),
        ("receiving_tds", "receiving_tds_40_plus", 1.0),
    ],
)
def test_long_touchdown_bonus_stacks_on_six_points(
    touchdowns: str,
    bonus_field: str,
    bonus: float,
    rules: LeagueScoring,
) -> None:
    # One 40+ touchdown is 6 + 1, not 1 instead of 6.
    result = score_offense(_offense(**{touchdowns: 1, bonus_field: 1}), rules)
    _approx(result.components[touchdowns], 6.0)
    _approx(result.components[bonus_field], bonus)
    _approx(result.fantasy_points, 7.0)
    _approx(result.touchdown_points, 7.0)


def test_two_touchdowns_with_one_long_bonus(rules: LeagueScoring) -> None:
    # 2 * 6 + 1 bonus = 13. The short touchdown does not get the bonus.
    result = score_offense(_offense(passing_tds=2, passing_tds_40_plus=1), rules)
    _approx(result.fantasy_points, 13.0)
    _approx(result.touchdown_points, 13.0)


def test_long_bonus_without_a_touchdown_is_rejected(rules: LeagueScoring) -> None:
    with pytest.raises(ScoringError, match="passing_tds_40_plus"):
        score_offense(_offense(passing_tds_40_plus=1), rules)


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("sacks", 1, 1.0),
        ("sacks", 4, 4.0),
        ("interceptions", 1, 2.0),
        ("fumble_recoveries", 1, 2.0),
        ("defense_tds", 1, 6.0),
        ("safeties", 1, 2.0),
        ("blocked_kicks", 1, 2.0),
        ("return_yards", 25, 1.0),
        ("return_yards", 75, 3.0),
        ("return_tds", 1, 6.0),
        ("extra_point_returns", 1, 2.0),
    ],
)
def test_defense_rule_component(
    field: str,
    value: float,
    expected: float,
    rules: LeagueScoring,
) -> None:
    result = score_defense(_defense(**{field: value}, points_allowed=21), rules)
    _approx(result.components[field], expected)
    # 21 points allowed scores 0, so the line total is just this category.
    _approx(result.fantasy_points, expected)


@pytest.mark.parametrize(
    ("allowed", "expected"),
    [
        (0, 10.0),
        (1, 7.0),
        (3, 7.0),
        (6, 7.0),
        (7, 4.0),
        (10, 4.0),
        (13, 4.0),
        (14, 1.0),
        (17, 1.0),
        (20, 1.0),
        (21, 0.0),
        (24, 0.0),
        (27, 0.0),
        (28, -1.0),
        (31, -1.0),
        (34, -1.0),
        (35, -4.0),
        (36, -4.0),
        (100, -4.0),
    ],
)
def test_points_allowed_tier(allowed: int, expected: float, rules: LeagueScoring) -> None:
    assert defense_points_allowed(allowed, rules) == expected
    result = score_defense(_defense(points_allowed=allowed), rules)
    assert result.components["points_allowed"] == expected
    _approx(result.fantasy_points, expected)


def test_hand_checked_passing_line(rules: LeagueScoring) -> None:
    # 312 / 25 = 12.48; three touchdowns = 18; one 40+ bonus = 1; two INT = -4.
    # 12.48 + 18 + 1 - 4 = 27.48. Touchdown portion = 19.
    result = score_offense(
        _offense(passing_yards=312, passing_tds=3, passing_tds_40_plus=1, interceptions=2),
        rules,
    )
    _approx(result.fantasy_points, 27.48)
    _approx(result.touchdown_points, 19.0)
    assert result.complete


def test_hand_checked_rushing_line(rules: LeagueScoring) -> None:
    # 86 / 10 = 8.6; one touchdown = 6; one fumble lost = -2. Total 12.6.
    result = score_offense(_offense(rushing_yards=86, rushing_tds=1, fumbles_lost=1), rules)
    _approx(result.fantasy_points, 12.6)
    _approx(result.touchdown_points, 6.0)


def test_hand_checked_receiving_line(rules: LeagueScoring) -> None:
    # 7 receptions = 0; 94 / 10 = 9.4; one 40+ receiving touchdown = 6 + 1;
    # one two-point conversion = 2. Total 18.4. The conversion is not a touchdown.
    result = score_offense(
        _offense(
            receptions=7,
            receiving_yards=94,
            receiving_tds=1,
            receiving_tds_40_plus=1,
            two_point=1,
        ),
        rules,
    )
    _approx(result.fantasy_points, 18.4)
    _approx(result.touchdown_points, 7.0)
    _approx(result.components["receptions"], 0.0)


def test_hand_checked_return_and_fumble_line(rules: LeagueScoring) -> None:
    # 80 / 25 = 3.2; return touchdown = 6; offensive fumble-return touchdown = 6;
    # fumble lost = -2. Total 13.2. Touchdowns = 12.
    result = score_offense(
        _offense(
            return_yards=80,
            return_tds=1,
            offensive_fumble_return_tds=1,
            fumbles_lost=1,
        ),
        rules,
    )
    _approx(result.fantasy_points, 13.2)
    _approx(result.touchdown_points, 12.0)


def test_hand_checked_kicker_line(rules: LeagueScoring) -> None:
    # 3 made PATs = 3. Made field goals of 28, 41, and 51 yards are 120 / 10 = 12.
    # A miss is not in the made-yards total. Total 15. No touchdown points.
    result = score_offense(_offense(extra_points_made=3, field_goal_yards_made=120), rules)
    _approx(result.fantasy_points, 15.0)
    _approx(result.touchdown_points, 0.0)


def test_hand_checked_defense_line(rules: LeagueScoring) -> None:
    # 4 sacks = 4; 1 INT = 2; 2 fumble recoveries = 4; 1 TD = 6; 1 blocked kick = 2;
    # 75 return yards = 3; 13 points allowed = 4. Total 25. Touchdowns = 6.
    result = score_defense(
        _defense(
            sacks=4,
            interceptions=1,
            fumble_recoveries=2,
            defense_tds=1,
            blocked_kicks=1,
            return_yards=75,
            points_allowed=13,
        ),
        rules,
    )
    _approx(result.fantasy_points, 25.0)
    _approx(result.touchdown_points, 6.0)
    assert result.complete
    _approx(math.fsum(result.components.values()), result.fantasy_points)


def test_legacy_core_stat_line(rules: LeagueScoring) -> None:
    # Previous engine case: 250/25 + 12 - 2 + 2 + 6 + 0 + 4 - 2 + 2 = 32.
    # Touchdowns are the two passing scores and the rushing score: 18.
    result = score_offense(
        {
            "passing_yards": 250,
            "passing_tds": 2,
            "interceptions": 1,
            "rushing_yards": 20,
            "rushing_tds": 1,
            "receptions": 12,
            "receiving_yards": 40,
            "fumbles_lost": 1,
            "two_point": 1,
        },
        rules,
    )
    assert result.fantasy_points == 32.0
    assert result.touchdown_points == 18.0


def test_legacy_defense_stat_line(rules: LeagueScoring) -> None:
    # 3 + 4 + 2 + 6 + 2 + 2 + 50/25 + 6 + 2 + 4 (10 points allowed is the 7–13 tier).
    result = score_defense(
        {
            "sacks": 3,
            "interceptions": 2,
            "fumble_recoveries": 1,
            "defense_tds": 1,
            "safeties": 1,
            "blocked_kicks": 1,
            "return_yards": 50,
            "return_tds": 1,
            "extra_point_returns": 1,
            "points_allowed": 10,
        },
        rules,
    )
    assert result.fantasy_points == 33.0
    assert result.touchdown_points == 12.0


def test_event_line_with_base_touchdowns(rules: LeagueScoring) -> None:
    # Each 40+ bonus has its touchdown. 6+1 three times, plus return TD 6 and
    # offensive fumble-return TD 6 = 33 touchdown points. 75/25 = 3, two PATs = 2,
    # 87 field-goal yards = 8.7. Total 46.7.
    result = score_offense(
        _offense(
            passing_tds=1,
            passing_tds_40_plus=1,
            rushing_tds=1,
            rushing_tds_40_plus=1,
            receiving_tds=1,
            receiving_tds_40_plus=1,
            return_yards=75,
            return_tds=1,
            offensive_fumble_return_tds=1,
            extra_points_made=2,
            field_goal_yards_made=87,
        ),
        rules,
    )
    _approx(result.touchdown_points, 33.0)
    _approx(result.fantasy_points, 46.7)


def test_negative_offensive_score_is_not_floored(rules: LeagueScoring) -> None:
    result = score_offense(_offense(interceptions=2, fumbles_lost=1), rules)
    assert result.fantasy_points == -6.0


def test_negative_rushing_yards_are_not_floored(rules: LeagueScoring) -> None:
    result = score_offense(_offense(rushing_yards=-10), rules)
    assert result.fantasy_points == -1.0


def test_negative_defense_score_is_not_floored(rules: LeagueScoring) -> None:
    shutout_failure = score_defense(_defense(points_allowed=35), rules)
    assert shutout_failure.fantasy_points == -4.0
    with_a_sack = score_defense(_defense(sacks=1, points_allowed=40), rules)
    assert with_a_sack.fantasy_points == -3.0


def test_interception_sign_depends_on_the_unit(rules: LeagueScoring) -> None:
    thrown = score_offense(_offense(interceptions=1), rules)
    taken = score_defense(_defense(interceptions=1, points_allowed=21), rules)
    assert thrown.components["interceptions"] == -2.0
    assert taken.components["interceptions"] == 2.0


def test_omitted_fumble_return_is_unavailable_not_zero(rules: LeagueScoring) -> None:
    result = score_offense({"passing_yards": 25}, rules)
    assert "offensive_fumble_return_tds" in result.unavailable
    assert "offensive_fumble_return_tds" not in result.components
    assert result.complete is False
    _approx(result.fantasy_points, 1.0)


def test_explicit_zero_fumble_return_is_observed(rules: LeagueScoring) -> None:
    result = score_offense(_offense(offensive_fumble_return_tds=0), rules)
    assert result.components["offensive_fumble_return_tds"] == 0.0
    assert "offensive_fumble_return_tds" not in result.unavailable
    assert result.complete


def test_null_and_nan_are_unavailable(rules: LeagueScoring) -> None:
    result = score_offense({"passing_yards": None, "rushing_yards": float("nan")}, rules)
    assert "passing_yards" in result.unavailable
    assert "rushing_yards" in result.unavailable
    assert "passing_yards" not in result.components
    assert "rushing_yards" not in result.components


def test_missing_points_allowed_is_not_a_shutout(rules: LeagueScoring) -> None:
    result = score_defense({"sacks": 2}, rules)
    assert "points_allowed" in result.unavailable
    assert "points_allowed" not in result.components
    assert result.fantasy_points == 2.0
    assert result.fantasy_points != 12.0


def test_explicit_shutout_scores_ten(rules: LeagueScoring) -> None:
    result = score_defense(_defense(points_allowed=0), rules)
    assert result.components["points_allowed"] == 10.0
    assert result.fantasy_points == 10.0


@pytest.mark.parametrize("value", [-1, -1.0])
def test_negative_points_allowed_is_rejected(value: float, rules: LeagueScoring) -> None:
    with pytest.raises(ScoringError, match="negative"):
        defense_points_allowed(value, rules)


@pytest.mark.parametrize("value", [6.5, 0.4])
def test_fractional_points_allowed_is_rejected(value: float, rules: LeagueScoring) -> None:
    with pytest.raises(ScoringError, match="integer"):
        defense_points_allowed(value, rules)


def test_closed_points_allowed_ladder_rejects_the_overflow(rules: LeagueScoring) -> None:
    closed = replace(
        rules,
        points_allowed=(PointsAllowedTier(points=3, max_points=10),),
    )
    assert defense_points_allowed(10, closed) == 3.0
    with pytest.raises(ScoringError, match="above"):
        defense_points_allowed(11, closed)


def test_non_numeric_stat_raises(rules: LeagueScoring) -> None:
    with pytest.raises(ScoringError, match="passing_yards"):
        score_offense({"passing_yards": "lots"}, rules)


def test_boolean_stat_is_rejected(rules: LeagueScoring) -> None:
    with pytest.raises(ScoringError, match="sacks"):
        score_defense({"sacks": True, "points_allowed": 0}, rules)


def test_negative_count_is_rejected(rules: LeagueScoring) -> None:
    with pytest.raises(ScoringError, match="interceptions"):
        score_offense(_offense(interceptions=-1), rules)


def test_negative_field_goal_yards_are_rejected(rules: LeagueScoring) -> None:
    with pytest.raises(ScoringError, match="field_goal_yards_made"):
        score_offense(_offense(field_goal_yards_made=-10), rules)


def test_infinite_yardage_is_rejected(rules: LeagueScoring) -> None:
    with pytest.raises(ScoringError, match="finite"):
        score_offense(_offense(receiving_yards=float("inf")), rules)


def test_mixed_offense_and_defense_is_rejected(rules: LeagueScoring) -> None:
    with pytest.raises(ScoringError, match="mixes"):
        score_offense(_offense(sacks=1), rules)
    with pytest.raises(ScoringError, match="mixes"):
        score_defense(_defense(passing_yards=10, points_allowed=0), rules)


def test_metadata_keys_are_ignored(rules: LeagueScoring) -> None:
    result = score_offense(
        _offense(passing_yards=25, player_id="00-0036322", week=3),  # type: ignore[arg-type]
        rules,
    )
    _approx(result.fantasy_points, 1.0)


def test_frame_scoring_matches_single_lines(rules: LeagueScoring) -> None:
    scored = score_offensive_frame(
        [
            {"passing_yards": 125, "passing_tds": 1},
            {"rushing_yards": 80, "receiving_yards": 20, "receiving_tds": 1},
        ],
        rules,
    )
    assert [row.fantasy_points for row in scored] == [11.0, 16.0]
    assert [row.touchdown_points for row in scored] == [6.0, 6.0]


def test_empty_frame(rules: LeagueScoring) -> None:
    assert score_offensive_frame([], rules) == []


def test_columnar_frame_is_rejected(rules: LeagueScoring) -> None:
    with pytest.raises(ScoringError, match="mappings"):
        score_offensive_frame(["passing_yards", "passing_tds"], rules)  # type: ignore[list-item]


def test_engine_follows_a_different_table(rules: LeagueScoring) -> None:
    ppr = replace(rules, reception=1.0)
    assert score_offense({"receptions": 5}, ppr).fantasy_points == 5.0
    stingy_pick = replace(rules, defense_return_touchdown=9.0)
    defense = score_defense(_defense(return_tds=1, points_allowed=21), stingy_pick)
    offense = score_offense(_offense(return_tds=1), rules)
    assert defense.components["return_tds"] == 9.0
    assert offense.components["return_tds"] == 6.0


def test_table_hash_tracks_rates_and_not_the_version_label(rules: LeagueScoring) -> None:
    assert rules.table_hash == replace(rules, version="other").table_hash
    assert rules.table_hash != replace(rules, reception=1.0).table_hash
    require_scoring_hash(rules, rules.table_hash)
    with pytest.raises(ScoringError, match="hash"):
        require_scoring_hash(rules, "0" * 64)


def test_default_rules_match_the_explicit_table(rules: LeagueScoring) -> None:
    assert score_offense({"passing_yards": 50}).fantasy_points == 2.0
    assert score_offense({"passing_yards": 50}, rules).fantasy_points == 2.0


def test_components_sum_to_the_total(rules: LeagueScoring) -> None:
    # 10 / 25 = 0.4; one interception = -2; -2 rushing yards = -0.2. Total -1.8.
    result = score_offense(
        _offense(passing_yards=10, interceptions=1, rushing_yards=-2),
        rules,
    )
    _approx(result.fantasy_points, -1.8)
    _approx(result.fantasy_points, math.fsum(result.components.values()))
    assert result.fantasy_points < 0
