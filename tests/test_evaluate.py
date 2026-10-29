"""Harness leakage, determinism, promotion, and the evaluate command."""

from __future__ import annotations

import json
import random
from collections.abc import Mapping, Sequence
from pathlib import Path

from gridiron_lab.cli import run
from gridiron_lab.evaluate import (
    adjust_residual,
    bootstrap_mae_diff,
    boundary_atom_straddles,
    calibration_window,
    choose_primary_mae,
    conformal_quantile,
    promotion_rule,
    residual_tails,
    rolling_origin,
)
from gridiron_lab.models import Prediction


class _Recorder:
    def __init__(self, calls: list[list[tuple[int, int, str, object]]]) -> None:
        self._calls = calls
        self.through = "none"

    def fit(self, rows: Sequence[Mapping[str, object]], *, trained_through: str) -> None:
        self.through = trained_through
        self._calls.append(
            [
                (
                    int(row["season"]),  # type: ignore[arg-type]
                    int(row["week"]),  # type: ignore[arg-type]
                    str(row.get("player_id")),
                    row.get("force_zero"),
                )
                for row in rows
            ]
        )

    def predict(self, rows: Sequence[Mapping[str, object]]) -> list[Prediction]:
        return [
            Prediction(
                mean=0.0,
                q10=0.0,
                q50=0.0,
                q90=0.0,
                model_version="recorder",
                feature_version="v2.0",
                scoring_version="ffc_yahoo_v1",
                trained_through=self.through,
                regime="baseline",
            )
            for _ in rows
        ]


class _Flat:
    def fit(self, rows: Sequence[Mapping[str, object]], *, trained_through: str) -> None:
        del rows, trained_through

    def predict(self, rows: Sequence[Mapping[str, object]]) -> list[Prediction]:
        return [
            Prediction(
                mean=1.0,
                q10=0.0,
                q50=1.0,
                q90=2.0,
                model_version="flat",
                feature_version="v2.0",
                scoring_version="ffc_yahoo_v1",
                trained_through=None,
                regime="baseline",
            )
            for _ in rows
        ]


def _player(
    season: int, week: int, position: str, index: int, *, force_zero: int = 0
) -> dict[str, object]:
    ewma = float(index + 1)
    return {
        "player_id": f"{position}{index}",
        "season": season,
        "week": week,
        "position": position,
        "points_ewma": ewma,
        "points_roll3": ewma,
        "points_roll5": ewma,
        "points_last": ewma,
        "force_zero": force_zero,
        "vegas_missing": 1,
        "actual": 0.0 if force_zero else ewma,
        "feature_version": "v2.0",
    }


def _weeks() -> dict[tuple[int, int], list[dict[str, object]]]:
    frames: dict[tuple[int, int], list[dict[str, object]]] = {}
    for week in range(1, 6):
        rows = [_player(2021, week, "QB", index) for index in range(10)]
        rows.extend(_player(2021, week, "RB", index) for index in range(10))
        rows.append(_player(2021, week, "QB", 99, force_zero=1))
        frames[(2021, week)] = rows
    test = [_player(2022, 1, "QB", index) for index in range(10)]
    test.append(_player(2022, 1, "QB", 99, force_zero=1))
    test.append(
        {
            "player_id": "FUTURE",
            "season": 2022,
            "week": 1,
            "position": "QB",
            "points_ewma": 1.0,
            "points_roll3": 1.0,
            "points_roll5": 1.0,
            "points_last": 1.0,
            "force_zero": 0,
            "actual": 1e6,
            "feature_version": "v2.0",
        }
    )
    frames[(2022, 1)] = test
    frames[(2022, 2)] = [_player(2022, 2, "QB", index) for index in range(10)]
    return frames


def test_no_training_row_from_the_test_week_or_later() -> None:
    calls: list[list[tuple[int, int, str, object]]] = []
    weeks = _weeks()
    rolling_origin(
        weeks,
        [2022],
        seed=0,
        n_boot=20,
        teams=2,
        make_model=lambda: _Recorder(calls),
        make_baseline=_Flat,
    )
    assert len(calls) == 2
    for row in calls[0]:
        assert (row[0], row[1]) < (2022, 1)
        assert row[3] != 1
        assert row[2] != "FUTURE"
    for row in calls[1]:
        assert (row[0], row[1]) < (2022, 2)
        assert row[3] != 1
    assert all(row[0] != 2022 or row[1] != 2 for batch in calls for row in batch)
    trained_ids = {row[2] for row in calls[0]}
    assert "QB99" not in trained_ids
    assert "FUTURE" not in trained_ids


def test_force_zero_does_not_enter_mae() -> None:
    weeks = {
        (2022, 1): [
            _player(2022, 1, "QB", 0),
            {
                "player_id": "OUT",
                "season": 2022,
                "week": 1,
                "position": "QB",
                "points_ewma": 0.0,
                "force_zero": 1,
                "actual": 1e6,
            },
        ]
    }
    report = rolling_origin(
        weeks,
        [2022],
        seed=0,
        n_boot=20,
        teams=1,
        make_model=_Flat,
        make_baseline=_Flat,
    )
    folds = report["folds"]
    assert isinstance(folds, list)
    positions = folds[0]["positions"]  # type: ignore[index]
    assert isinstance(positions, dict)
    qb = positions["QB"]
    assert isinstance(qb, dict)
    assert qb["n"] == 1
    assert qb["mae"] == 0.0


def test_rolling_origin_can_keep_row_level_residuals() -> None:
    weeks = _weeks()
    residuals: list[dict[str, object]] = []
    report = rolling_origin(
        weeks,
        [2022],
        seed=0,
        n_boot=20,
        teams=2,
        make_model=_Flat,
        make_baseline=_Flat,
        residuals=residuals,
    )
    assert residuals, "expected the scored test rows to be captured"
    assert all(row["season"] == 2022 for row in residuals)
    assert all(row["player_id"] != "QB99" for row in residuals)
    for key in ("actual", "mean", "q50", "baseline_mean", "points_ewma"):
        assert key in residuals[0]
    folds = report["folds"]
    assert isinstance(folds, list)
    positions = folds[0]["positions"]  # type: ignore[index]
    assert isinstance(positions, dict)
    qb_bias = positions["QB"]["bias"]  # type: ignore[index]
    captured = [row for row in residuals if row["position"] == "QB"]
    bias = sum(float(row["mean"]) - float(row["actual"]) for row in captured) / len(captured)
    assert abs(bias - float(qb_bias)) < 1e-9


def test_rolling_origin_is_deterministic() -> None:
    weeks = _weeks()
    first = rolling_origin(weeks, [2022], seed=0, n_boot=30, teams=2)
    second = rolling_origin(weeks, [2022], seed=0, n_boot=30, teams=2)
    assert first["prediction_digest"] == second["prediction_digest"]
    assert first["folds"] == second["folds"]
    assert first["promotion"] == second["promotion"]


def test_bootstrap_interval_is_reproducible() -> None:
    weeks = [
        {"sum_model": 10.0, "sum_base": 14.0, "n": 2},
        {"sum_model": 8.0, "sum_base": 8.0, "n": 2},
        {"sum_model": 6.0, "sum_base": 9.0, "n": 2},
    ]
    left = bootstrap_mae_diff(weeks, random.Random(0), 200)
    right = bootstrap_mae_diff(weeks, random.Random(0), 200)
    assert left == right
    assert left is not None
    assert left[0] <= left[1]


def _fold(
    season: int,
    *,
    model: float,
    baseline: float,
    ci: tuple[float, float],
    bias: float = 0.0,
    position_gap: float = -0.1,
) -> dict[str, object]:
    def block(
        mae: float, base: float, interval: tuple[float, float] | None = None
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "n": 20,
            "mae": mae,
            "mae_baseline": base,
            "bias": bias,
            "bias_q50": 5.0,
            "coverage": 0.8,
        }
        if interval is not None:
            payload["mae_diff_ci"] = [interval[0], interval[1]]
        return payload

    positions = {
        position: block(baseline + position_gap, baseline)
        for position in ("QB", "RB", "WR", "TE", "K", "DST")
    }
    return {
        "season": season,
        "positions": positions,
        "pooled_offense": block(model, baseline, ci),
    }


def _report(
    folds: list[dict[str, object]],
    *,
    points: float | None = 1.0,
    points_q50: float | None = None,
    coverage: float = 0.8,
) -> dict[str, object]:
    return {
        "folds": folds,
        "calibration": {
            "coverage_by_position": {
                position: {"coverage": coverage, "n": 40}
                for position in ("QB", "RB", "WR", "TE", "K", "DST")
            }
        },
        "lineup": {
            "points_gained_total": points,
            "points_gained_q50_total": points if points_q50 is None else points_q50,
        },
    }


def test_promotion_rule_passes_only_when_every_criterion_holds() -> None:
    folds = [
        _fold(2022, model=4.0, baseline=4.4, ci=(-0.6, -0.1)),
        _fold(2023, model=4.1, baseline=4.5, ci=(-0.5, -0.05)),
        _fold(2024, model=4.2, baseline=4.3, ci=(-0.2, -0.01)),
        _fold(2025, model=4.0, baseline=4.2, ci=(-0.3, -0.02)),
    ]
    verdict = promotion_rule(_report(folds))
    assert verdict["promoted"] is True
    mae = verdict["mae_seasons"]
    assert isinstance(mae, dict)
    assert mae["pass"] is True
    assert mae["seasons_passing"] == 4

    wide = [
        _fold(2022, model=4.0, baseline=4.4, ci=(-0.6, 0.1)),
        _fold(2023, model=4.1, baseline=4.5, ci=(-0.5, -0.05)),
        _fold(2024, model=4.2, baseline=4.3, ci=(-0.2, -0.01)),
        _fold(2025, model=4.0, baseline=4.2, ci=(-0.3, -0.02)),
    ]
    missed = promotion_rule(_report(wide))
    mae_missed = missed["mae_seasons"]
    assert isinstance(mae_missed, dict)
    assert mae_missed["seasons_passing"] == 3
    assert mae_missed["pass"] is True
    assert missed["promoted"] is True
    only_two = [
        _fold(2022, model=4.0, baseline=4.4, ci=(-0.6, 0.1)),
        _fold(2023, model=4.1, baseline=4.5, ci=(-0.5, -0.05)),
        _fold(2024, model=4.2, baseline=4.3, ci=(-0.2, 0.2)),
        _fold(2025, model=4.0, baseline=4.2, ci=(-0.3, -0.02)),
    ]
    failed_count = promotion_rule(_report(only_two))
    count_block = failed_count["mae_seasons"]
    assert isinstance(count_block, dict)
    assert count_block["seasons_passing"] == 2
    assert count_block["pass"] is False
    assert failed_count["promoted"] is False

    worse = [
        _fold(season, model=4.0, baseline=4.4, ci=(-0.5, -0.1), position_gap=0.2)
        for season in (2022, 2023, 2024, 2025)
    ]
    # position_gap applies to every position, so each is worse in 4 seasons.
    worse[0] = _fold(2022, model=4.0, baseline=4.4, ci=(-0.5, -0.1), position_gap=-0.1)
    position_fail = promotion_rule(_report(worse))
    position_block = position_fail["position_mae"]
    assert isinstance(position_block, dict)
    assert position_block["pass"] is False

    biased = [
        _fold(season, model=4.0, baseline=4.4, ci=(-0.5, -0.1), bias=0.31)
        for season in (2022, 2023, 2024, 2025)
    ]
    bias_fail = promotion_rule(_report(biased))
    bias_block = bias_fail["bias"]
    assert isinstance(bias_block, dict)
    assert bias_block["pass"] is False

    covered = promotion_rule(_report(folds, coverage=0.7))
    coverage_block = covered["coverage"]
    assert isinstance(coverage_block, dict)
    assert coverage_block["pass"] is False

    negative = promotion_rule(_report(folds, points=-0.5))
    lineup_block = negative["lineup_points"]
    assert isinstance(lineup_block, dict)
    assert lineup_block["pass"] is False
    assert negative["promoted"] is False

    graded_on_q50 = promotion_rule(_report(folds, points=5.0, points_q50=-1.0))
    graded_block = graded_on_q50["lineup_points"]
    assert isinstance(graded_block, dict)
    assert graded_block["pass"] is False
    assert graded_block["points_gained"] == -1.0
    assert graded_block["points_gained_mean_head"] == 5.0
    assert graded_on_q50["promoted"] is False


class _MeanAboveMedian:
    def fit(self, rows: Sequence[Mapping[str, object]], *, trained_through: str) -> None:
        del rows, trained_through

    def predict(self, rows: Sequence[Mapping[str, object]]) -> list[Prediction]:
        return [
            Prediction(
                mean=4.0,
                q10=0.0,
                q50=1.0,
                q90=2.0,
                model_version="split",
                feature_version="v2.0",
                scoring_version="ffc_yahoo_v1",
                trained_through=None,
                regime="gbm",
            )
            for _ in rows
        ]


def test_reported_bias_is_the_mean_head() -> None:
    weeks = {(2022, 1): [_player(2022, 1, "QB", 0)]}
    report = rolling_origin(
        weeks,
        [2022],
        seed=0,
        n_boot=5,
        teams=1,
        make_model=_MeanAboveMedian,
        make_baseline=_Flat,
    )
    folds = report["folds"]
    assert isinstance(folds, list)
    positions = folds[0]["positions"]
    assert isinstance(positions, dict)
    qb = positions["QB"]
    assert isinstance(qb, dict)
    assert qb["bias"] == 3.0
    assert qb["bias_q50"] == 0.0
    assert qb["mae"] == 0.0
    assert qb["mae_mean"] == 3.0
    assert qb["coverage"] == qb["coverage_raw"]


def test_q50_bias_does_not_gate_promotion() -> None:
    folds = [
        _fold(season, model=4.0, baseline=4.4, ci=(-0.5, -0.1), bias=0.0)
        for season in (2022, 2023, 2024, 2025)
    ]
    verdict = promotion_rule(_report(folds))
    assert verdict["promoted"] is True
    bias = verdict["bias"]
    assert isinstance(bias, dict)
    assert bias["head"] == "mean"
    assert bias["failures"] == []


def test_conformal_quantile_uses_the_finite_sample_index() -> None:
    assert conformal_quantile([0.0] * 29) is None
    assert conformal_quantile(list(range(30))) == 24.0
    assert conformal_quantile(list(range(30)), alpha=0.1) == 27.0


def test_boundary_atom_straddles_only_when_the_band_is_unreachable() -> None:
    straddling = [-1.0] * 60 + [0.0] * 25 + [1.0] * 15
    assert boundary_atom_straddles(straddling) is True
    open_inside = [-1.0] * 78 + [0.0] * 12 + [1.0] * 10
    assert boundary_atom_straddles(open_inside) is False
    closed_inside = [-1.0] * 70 + [0.0] * 12 + [1.0] * 18
    assert boundary_atom_straddles(closed_inside) is False
    assert boundary_atom_straddles([0.0] * 29) is False


def test_residual_tails_use_half_the_two_sided_alpha() -> None:
    rows: list[dict[str, object]] = []
    predictions: list[Prediction] = []
    for index in range(30):
        rows.append(
            {
                "player_id": f"TE{index}",
                "season": 2021,
                "week": 1,
                "position": "TE",
                "force_zero": 0,
                "actual": float(index),
            }
        )
        predictions.append(
            Prediction(
                mean=0.0,
                q10=-1.0,
                q50=0.0,
                q90=1.0,
                model_version="stub",
                feature_version="v2.0",
                scoring_version="ffc_yahoo_v1",
                trained_through=None,
                regime="baseline",
            )
        )
    tails = residual_tails(rows, predictions)
    assert tails["TE"] == (-2.0, 27.0)
    shifted = adjust_residual(predictions[0], tails["TE"][0], tails["TE"][1])
    assert shifted.q50 == 0.0
    assert (shifted.q10, shifted.q90) == (0.0, 27.0)


def test_straddling_boundary_atom_uses_residual_quantiles() -> None:
    def row(season: int, week: int, index: int, actual: float) -> dict[str, object]:
        return {
            "player_id": f"TE{index}",
            "season": season,
            "week": week,
            "position": "TE",
            "points_ewma": 1.0,
            "points_roll3": 1.0,
            "points_roll5": 1.0,
            "points_last": 1.0,
            "force_zero": 0,
            "actual": actual,
            "feature_version": "v2.0",
        }

    class _Band:
        def fit(self, rows: Sequence[Mapping[str, object]], *, trained_through: str) -> None:
            del rows, trained_through

        def predict(self, rows: Sequence[Mapping[str, object]]) -> list[Prediction]:
            return [
                Prediction(
                    mean=5.0,
                    q10=0.0,
                    q50=5.0,
                    q90=10.0,
                    model_version="band",
                    feature_version="v2.0",
                    scoring_version="ffc_yahoo_v1",
                    trained_through=None,
                    regime="baseline",
                )
                for _ in rows
            ]

    calibration: list[dict[str, object]] = [row(2021, 18, index, 5.0) for index in range(24)]
    calibration.extend(row(2021, 18, 24 + index, 0.0) for index in range(10))
    calibration.extend(row(2021, 18, 34 + index, 15.0) for index in range(6))
    report = rolling_origin(
        {(2021, 18): calibration, (2022, 1): [row(2022, 1, 0, 12.0)]},
        [2022],
        seed=0,
        n_boot=5,
        teams=1,
        calibration_weeks=1,
        make_model=_Band,
        make_baseline=_Flat,
    )
    folds = report["folds"]
    assert isinstance(folds, list)
    weeks = folds[0]["weeks"]  # type: ignore[index]
    assert isinstance(weeks, list)
    entry = weeks[0]
    assert isinstance(entry, dict)
    assert entry["interval_rule"]["TE"] == "residual_quantile"
    positions = folds[0]["positions"]  # type: ignore[index]
    assert isinstance(positions, dict)
    te = positions["TE"]
    assert isinstance(te, dict)
    assert te["coverage"] == 1.0
    assert te["coverage_raw"] == 0.0
    assert te["mae"] == 7.0


def test_calibration_window_drops_the_test_week_and_later() -> None:
    history: list[dict[str, object]] = []
    for week in range(1, 11):
        history.append(_player(2021, week, "QB", 1))
    history.append(_player(2022, 1, "QB", 7))
    history.append(_player(2022, 3, "QB", 8))
    rows, weeks = calibration_window(history, (2022, 1), n_weeks=8)
    assert weeks == [(2021, week) for week in range(3, 11)]
    assert all(key < (2022, 1) for key in weeks)
    assert all((int(row["season"]), int(row["week"])) < (2022, 1) for row in rows)
    assert all(row["player_id"] != "QB7" for row in rows)


def test_rolling_origin_calibration_stays_before_the_test_week() -> None:
    report = rolling_origin(_weeks(), [2022], seed=0, n_boot=5, teams=1)
    folds = report["folds"]
    assert isinstance(folds, list)
    seen = 0
    for fold in folds:
        assert isinstance(fold, dict)
        entries = fold["weeks"]
        assert isinstance(entries, list)
        for entry in entries:
            assert isinstance(entry, dict)
            labels = entry["calibration_weeks"]
            assert isinstance(labels, list)
            assert labels
            seen += 1
            for label in labels:
                assert isinstance(label, str)
                season = int(label[:4])
                week = int(label[-2:])
                assert (season, week) < (int(entry["season"]), int(entry["week"]))
    assert seen == 2


def test_primary_mae_switches_only_after_three_mean_wins() -> None:
    def pooled(q50: float, mean_mae: float) -> dict[str, object]:
        return {"pooled_offense": {"mae": q50, "mae_mean": mean_mae}}

    mean_wins = [pooled(4.0, 3.5), pooled(4.0, 3.5), pooled(4.0, 3.5), pooled(4.0, 4.2)]
    assert choose_primary_mae(mean_wins) == "mean"
    q50_wins = [pooled(3.0, 3.5), pooled(3.0, 3.5), pooled(4.0, 3.5), pooled(4.0, 3.5)]
    assert choose_primary_mae(q50_wins) == "q50"


def test_empty_evaluation_does_not_promote() -> None:
    verdict = promotion_rule({"folds": [], "calibration": {}, "lineup": {}})
    assert verdict["promoted"] is False


def test_evaluate_command_writes_json(tmp_path: Path) -> None:
    output = tmp_path / "nested" / "results.json"
    code = run(
        [
            "evaluate",
            "--season",
            "2024",
            "--test-season",
            "2024",
            "--data-dir",
            str(tmp_path / "data"),
            "--output",
            str(output),
            "--seed",
            "0",
            "--bootstrap",
            "10",
        ]
    )
    assert code == 0
    text = output.read_text(encoding="utf-8")
    assert "NaN" not in text
    payload = json.loads(text)
    assert payload["promotion"]["promoted"] is False
    assert payload["calibration"]["method"] == "cqr_trailing_weeks"
    assert payload["experiment"]["tuned_on_test"] is False
    assert payload["experiment"]["model_version"] == "lgbm_skill_v1"
    assert (tmp_path / "data" / "experiments.jsonl").is_file()
