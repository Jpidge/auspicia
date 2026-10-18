"""Baseline arithmetic, Vegas routing, and the skill-model contract."""

from __future__ import annotations

import math

import numpy as np

from gridiron_lab.models import (
    MODEL_VERSION,
    LegacyBaseline,
    SkillGBM,
    VegasModel,
    sort_quantiles,
)


def _row(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "player_id": "p",
        "season": 2021,
        "week": 1,
        "position": "QB",
        "points_ewma": 10.0,
        "points_roll3": 8.0,
        "points_roll5": 12.0,
        "points_last": 10.0,
        "force_zero": 0,
        "feature_version": "v2.0",
    }
    base.update(overrides)
    return base


def test_sort_quantiles_repairs_crossing() -> None:
    assert sort_quantiles(5.0, 1.0, 3.0) == (1.0, 3.0, 5.0)


def test_legacy_baseline_matches_the_old_blend() -> None:
    model = LegacyBaseline()
    plain = model.predict([_row()])[0]
    assert plain.mean == plain.q50
    assert math.isclose(plain.mean, 9.8, abs_tol=1e-9)
    samples = np.asarray([10.0, 8.0, 12.0, 10.0])
    volatility = float(np.std(samples, ddof=0))
    assert math.isclose(plain.q10, max(0.0, 9.8 - 1.15 * volatility), abs_tol=1e-9)
    assert math.isclose(plain.q90, 9.8 + 1.15 * volatility, abs_tol=1e-9)
    assert plain.q10 <= plain.q50 <= plain.q90

    boosted = model.predict([_row(opportunity_momentum=10)])[0]
    assert math.isclose(boosted.mean, 12.3, abs_tol=1e-9)
    penalized = model.predict([_row(td_dependency=1.0)])[0]
    assert math.isclose(penalized.mean, 8.7, abs_tol=1e-9)
    both = model.predict([_row(opportunity_momentum=10, td_dependency=1.0)])[0]
    assert math.isclose(both.mean, 11.2, abs_tol=1e-9)
    missing = model.predict([_row()])[0]
    assert missing.mean == plain.mean
    infinite = model.predict([_row(opportunity_momentum=math.inf)])[0]
    assert infinite.mean == plain.mean

    floored = model.predict([_row(points_ewma=-20, points_roll3=-20, points_roll5=-20)])[0]
    assert floored.mean == 0.0
    assert floored.q10 == 0.0
    assert floored.q90 >= 0.0


def test_legacy_baseline_zeros_force_zero_rows() -> None:
    prediction = LegacyBaseline().predict([_row(force_zero=1, points_ewma=30)])[0]
    assert prediction.mean == prediction.q10 == prediction.q50 == prediction.q90 == 0.0
    assert prediction.fallback == "force_zero"


def test_skill_model_ignores_force_zero_and_actual() -> None:
    history = []
    for week in range(1, 5):
        for index in range(10):
            ewma = 4.0 + index
            history.append(
                _row(
                    player_id=f"QB{index}",
                    week=week,
                    points_ewma=ewma,
                    points_roll3=ewma,
                    points_roll5=ewma,
                    points_last=ewma,
                    actual=ewma + 0.25,
                    force_zero=0,
                )
            )
    poison = _row(player_id="poison", week=1, force_zero=1, actual=1e6, points_ewma=1.0)
    clean = SkillGBM("QB", seed=0)
    dirty = SkillGBM("QB", seed=0)
    clean.fit(history, trained_through="2021-W04")
    dirty.fit([*history, poison], trained_through="2021-W04")
    probe = _row(player_id="QB3", week=6, points_ewma=7.0, points_roll3=7.0, points_roll5=7.0)
    left = clean.predict([probe])[0]
    right = dirty.predict([probe])[0]
    assert (left.mean, left.q10, left.q50, left.q90) == (
        right.mean,
        right.q10,
        right.q50,
        right.q90,
    )
    scrambled = dict(probe, actual=1e9)
    again = clean.predict([scrambled])[0]
    assert (left.mean, left.q50) == (again.mean, again.q50)
    assert left.q10 <= left.q50 <= left.q90
    assert left.model_version == MODEL_VERSION
    zero = clean.predict([_row(force_zero=1, actual=50)])[0]
    assert zero.mean == 0.0
    assert zero.fallback == "force_zero"


def test_skill_model_falls_back_when_history_is_short() -> None:
    model = SkillGBM("RB", seed=0)
    model.fit([_row(position="RB", actual=3.0)], trained_through="2021-W01")
    prediction = model.predict([_row(position="RB")])[0]
    baseline = LegacyBaseline().predict([_row(position="RB")])[0]
    assert prediction.fallback == "insufficient_history"
    assert prediction.mean == baseline.mean
    assert prediction.q50 == baseline.q50


def test_vegas_routes_rows_with_and_without_lines() -> None:
    lined = []
    unlined = []
    for index in range(20):
        implied = 10.0 + index
        ewma = 6.0 + (index % 4) * 0.2
        lined.append(
            {
                "player_id": f"K{index}",
                "season": 2021,
                "week": 1,
                "position": "K",
                "force_zero": 0,
                "vegas_missing": 0,
                "implied_team_total": implied,
                "game_total": implied + 21.0,
                "points_ewma": ewma,
                "points_roll3": ewma,
                "actual": 0.35 * implied + 0.1 * ewma,
            }
        )
        bare_ewma = 2.0 + index * 0.4
        unlined.append(
            {
                "player_id": f"U{index}",
                "season": 2021,
                "week": 2,
                "position": "K",
                "force_zero": 0,
                "vegas_missing": 1,
                "implied_team_total": 99.0,
                "game_total": 40.0,
                "points_ewma": bare_ewma,
                "points_roll3": bare_ewma - 0.5,
                "actual": bare_ewma,
            }
        )
    model = VegasModel("K")
    model.fit([*lined, *unlined], trained_through="2021-W02")
    lined_row = dict(lined[5])
    bare_row = dict(unlined[5])
    lined_pred = model.predict([lined_row])[0]
    bare_pred = model.predict([bare_row])[0]
    assert lined_pred.regime == "with_lines"
    assert bare_pred.regime == "without_lines"
    bumped = dict(lined_row, implied_team_total=float(lined_row["implied_team_total"]) + 5.0)
    assert model.predict([bumped])[0].mean != lined_pred.mean
    bumped_bare = dict(bare_row, implied_team_total=0.0)
    assert model.predict([bumped_bare])[0].mean == bare_pred.mean
    assert lined_pred.q10 <= lined_pred.q50 <= lined_pred.q90
    zero = model.predict([dict(lined_row, force_zero=1)])[0]
    assert zero.mean == 0.0

    defense = []
    for index in range(12):
        team_total = 20.0 + index * 0.3
        game_total = 44.0
        defense.append(
            {
                "player_id": f"D{index}",
                "season": 2021,
                "week": 1,
                "position": "DST",
                "force_zero": 0,
                "vegas_missing": 0,
                "implied_team_total": team_total,
                "game_total": game_total,
                "points_ewma": 5.0,
                "points_roll3": 5.0,
                "actual": 0.2 * (game_total - team_total),
            }
        )
    dst = VegasModel("DST")
    dst.fit(defense, trained_through="2021-W01")
    first = dst.predict([defense[0]])[0]
    shifted = dict(defense[0], implied_team_total=float(defense[0]["implied_team_total"]) + 4.0)
    assert first.regime == "with_lines"
    assert dst.predict([shifted])[0].mean != first.mean
    dark = dict(defense[0], vegas_missing=1)
    # No without-lines fit was possible, so a missing line falls back.
    assert dst.predict([dark])[0].regime == "baseline"
