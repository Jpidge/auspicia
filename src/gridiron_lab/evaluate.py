"""Rolling-origin evaluation and the pre-registered promotion rule.

Training rows for a test week are earlier weeks, and nothing from that week
or a later one. The last 8 of those weeks are a calibration window and are
not fit rows. Metrics ignore ``force_zero`` rows and rows with no actual.
Promotion bias uses the mean head. The 10/90 interval is shifted by a
split-conformal quantile computed on the calibration window.

A season counts toward the MAE rule only when the model MAE is lower than the
baseline and the week-bootstrap 95% interval of (model − baseline) lies
entirely below zero.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import subprocess
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from gridiron_lab.config import Lineup
from gridiron_lab.features import FEATURE_VERSION, FeatureError, build_features
from gridiron_lab.logging import get_logger
from gridiron_lab.models import (
    BASELINE_VERSION,
    FROZEN_PARAMS,
    MODEL_VERSION,
    VEGAS_VERSION,
    GridironPredictor,
    LegacyBaseline,
    Prediction,
    _has_actual,
    _is_force_zero,
    _position,
    sort_quantiles,
)
from gridiron_lab.scoring import SCORING_VERSION

logger = get_logger("evaluate")

OFFENSE = ("QB", "RB", "WR", "TE")
POSITIONS = ("QB", "RB", "WR", "TE", "K", "DST")
COVERAGE_LOW = 0.77
COVERAGE_HIGH = 0.83
BIAS_LIMIT = 0.3
WORSE_MAE = 0.05
MAE_SEASONS_REQUIRED = 3
DEFAULT_TEAMS = 12
ROSTER_COUNTS = {"QB": 2, "RB": 4, "WR": 5, "TE": 2, "K": 1, "DST": 1}
DEFAULT_LINEUP = Lineup(qb=1, rb=2, wr=2, te=1, flex=1, k=1, dst=1, bench=6, ir=1)
CALIBRATION_WEEKS = 8
MIN_CALIBRATION_SCORES = 30
CONFORMAL_ALPHA = 0.2

CALIBRATION_REASON = (
    "Split conformalized quantile regression on the last 8 weeks before each "
    "test week. The model is fit only on weeks before that window. Both are "
    "strictly before the test week. Fewer than 30 scores leaves the raw interval."
)

ModelFactory = Callable[[], object]
BaselineFactory = Callable[[], object]


class EvalError(Exception):
    """The evaluation harness was given a fold or a row it cannot score."""


@dataclass(frozen=True, slots=True)
class Scored:
    season: int
    week: int
    position: str
    player_id: str
    actual: float
    model: Prediction
    baseline: Prediction
    q10_raw: float
    q90_raw: float


def _jsonable(value: object) -> object:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    raise EvalError(f"cannot encode {type(value).__name__} in the results file")


def _percentile(sorted_values: Sequence[float], fraction: float) -> float:
    if not sorted_values:
        raise EvalError("bootstrap sample is empty")
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    index = (len(sorted_values) - 1) * fraction
    low = math.floor(index)
    high = math.ceil(index)
    if low == high:
        return float(sorted_values[low])
    weight = index - low
    return float(sorted_values[low] * (1.0 - weight) + sorted_values[high] * weight)


def bootstrap_mae_diff(
    weeks: Sequence[Mapping[str, float | int]],
    rng: random.Random,
    n_boot: int,
) -> tuple[float, float] | None:
    """95% CI for MAE(model) − MAE(baseline), resampling whole weeks."""

    usable = [week for week in weeks if int(week["n"]) > 0]
    if not usable or n_boot < 1:
        return None
    diffs: list[float] = []
    count = len(usable)
    for _ in range(n_boot):
        sum_model = 0.0
        sum_base = 0.0
        total = 0
        for _draw in range(count):
            week = usable[rng.randrange(count)]
            sum_model += float(week["sum_model"])
            sum_base += float(week["sum_base"])
            total += int(week["n"])
        if total <= 0:
            continue
        diffs.append(sum_model / total - sum_base / total)
    if not diffs:
        return None
    diffs.sort()
    return _percentile(diffs, 0.025), _percentile(diffs, 0.975)


def _ranks(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    cursor = 0
    while cursor < len(order):
        end = cursor
        while end + 1 < len(order) and values[order[end + 1]] == values[order[cursor]]:
            end += 1
        average = (cursor + end) / 2.0 + 1.0
        for index in range(cursor, end + 1):
            ranks[order[index]] = average
        cursor = end + 1
    return ranks


def _pearson(left: Sequence[float], right: Sequence[float]) -> float | None:
    count = len(left)
    if count < 3 or count != len(right):
        return None
    mean_left = sum(left) / count
    mean_right = sum(right) / count
    covariance = sum((x - mean_left) * (y - mean_right) for x, y in zip(left, right, strict=True))
    scale_left = math.sqrt(sum((item - mean_left) ** 2 for item in left))
    scale_right = math.sqrt(sum((item - mean_right) ** 2 for item in right))
    if scale_left == 0 or scale_right == 0:
        return None
    return covariance / (scale_left * scale_right)


def _spearman(rows: Sequence[Scored]) -> float | None:
    by_week: dict[tuple[int, int], list[Scored]] = defaultdict(list)
    for row in rows:
        by_week[(row.season, row.week)].append(row)
    correlations: list[float] = []
    for group in by_week.values():
        if len(group) < 3:
            continue
        left = _ranks([item.model.q50 for item in group])
        right = _ranks([item.actual for item in group])
        correlation = _pearson(left, right)
        if correlation is not None:
            correlations.append(correlation)
    if not correlations:
        return None
    return sum(correlations) / len(correlations)


def _top12(rows: Sequence[Scored]) -> float | None:
    by_week: dict[tuple[int, int], list[Scored]] = defaultdict(list)
    for row in rows:
        by_week[(row.season, row.week)].append(row)
    hits: list[float] = []
    for group in by_week.values():
        if len(group) < 12:
            continue
        predicted = sorted(group, key=lambda item: (-item.model.q50, item.player_id))[:12]
        observed = sorted(group, key=lambda item: (-item.actual, item.player_id))[:12]
        overlap = {item.player_id for item in predicted} & {item.player_id for item in observed}
        hits.append(len(overlap) / 12.0)
    if not hits:
        return None
    return sum(hits) / len(hits)


def conformal_quantile(scores: Sequence[float], alpha: float = CONFORMAL_ALPHA) -> float | None:
    """Finite-sample quantile of CQR scores. ``None`` when the window is too small.

    The index is ``ceil((n + 1) * (1 - alpha))``, which targets an 80% interval
    when ``alpha`` is 0.2. The window length is not chosen from test coverage.
    """

    finite = [float(score) for score in scores if math.isfinite(float(score))]
    count = len(finite)
    if count < MIN_CALIBRATION_SCORES:
        return None
    ordered = sorted(finite)
    index = math.ceil((count + 1) * (1.0 - alpha))
    if index < 1:
        index = 1
    if index > count:
        return ordered[-1]
    return ordered[index - 1]


def adjust_interval(prediction: Prediction, delta: float) -> Prediction:
    """Shift q10/q90 by a conformal score and keep q50 inside the interval."""

    low = min(prediction.q10 - delta, prediction.q50)
    high = max(prediction.q90 + delta, prediction.q50)
    q10, q50, q90 = sort_quantiles(low, prediction.q50, high)
    return Prediction(
        mean=prediction.mean,
        q10=q10,
        q50=q50,
        q90=q90,
        model_version=prediction.model_version,
        feature_version=prediction.feature_version,
        scoring_version=prediction.scoring_version,
        trained_through=prediction.trained_through,
        fallback=prediction.fallback,
        regime=prediction.regime,
    )


def calibration_window(
    history: Sequence[Mapping[str, object]],
    test_key: tuple[int, int],
    n_weeks: int = CALIBRATION_WEEKS,
) -> tuple[list[Mapping[str, object]], list[tuple[int, int]]]:
    """Trailing weeks strictly before ``test_key`` that have an actual.

    A row whose own season and week are at or after the test week is dropped
    even if it was stored under an earlier key.
    """

    eligible: list[Mapping[str, object]] = []
    for row in history:
        row_key = _row_key(row)
        if row_key >= test_key or _is_force_zero(row) or not _has_actual(row):
            continue
        eligible.append(row)
    weeks = sorted({_row_key(row) for row in eligible})
    chosen = weeks[-n_weeks:] if n_weeks > 0 else []
    chosen_set = set(chosen)
    rows = [row for row in eligible if _row_key(row) in chosen_set]
    return rows, chosen


def _cqr_scores(
    rows: Sequence[Mapping[str, object]],
    predictions: Sequence[Prediction],
) -> dict[str, list[float]]:
    scores: dict[str, list[float]] = {position: [] for position in POSITIONS}
    for row, prediction in zip(rows, predictions, strict=True):
        if _is_force_zero(row) or not _has_actual(row) or prediction.fallback == "force_zero":
            continue
        position = _position(row)
        if position not in scores:
            continue
        actual = float(row["actual"])  # type: ignore[arg-type]
        scores[position].append(max(prediction.q10 - actual, actual - prediction.q90))
    return scores


def _empty_metrics() -> dict[str, object]:
    return {
        "n": 0,
        "mae": None,
        "rmse": None,
        "mae_mean": None,
        "rmse_mean": None,
        "bias": None,
        "bias_q50": None,
        "coverage": None,
        "coverage_raw": None,
        "median_ae": None,
        "spearman": None,
        "top12_hit_rate": None,
        "mae_baseline": None,
        "mae_diff": None,
        "mae_mean_diff": None,
        "n_fallback": 0,
    }


def _metric_dict(rows: Sequence[Scored]) -> dict[str, object]:
    count = len(rows)
    if count == 0:
        return _empty_metrics()
    errors = [row.model.q50 - row.actual for row in rows]
    mean_errors = [row.model.mean - row.actual for row in rows]
    baseline_errors = [abs(row.baseline.q50 - row.actual) for row in rows]
    absolute = [abs(error) for error in errors]
    absolute_mean = [abs(error) for error in mean_errors]
    covered = sum(1 for row in rows if row.model.q10 <= row.actual <= row.model.q90)
    covered_raw = sum(1 for row in rows if row.q10_raw <= row.actual <= row.q90_raw)
    ordered = sorted(absolute)
    mid = count // 2
    if count % 2:
        median = ordered[mid]
    else:
        median = (ordered[mid - 1] + ordered[mid]) / 2.0
    mae = sum(absolute) / count
    mae_mean = sum(absolute_mean) / count
    mae_baseline = sum(baseline_errors) / count
    fallbacks = sum(1 for row in rows if row.model.fallback not in (None, "force_zero"))
    return {
        "n": count,
        "mae": mae,
        "rmse": math.sqrt(sum(error * error for error in errors) / count),
        "mae_mean": mae_mean,
        "rmse_mean": math.sqrt(sum(error * error for error in mean_errors) / count),
        "bias": sum(mean_errors) / count,
        "bias_q50": sum(errors) / count,
        "coverage": covered / count,
        "coverage_raw": covered_raw / count,
        "median_ae": median,
        "spearman": _spearman(rows),
        "top12_hit_rate": _top12(rows),
        "mae_baseline": mae_baseline,
        "mae_diff": mae - mae_baseline,
        "mae_mean_diff": mae_mean - mae_baseline,
        "n_fallback": fallbacks,
    }


def _week_sums(rows: Sequence[Scored]) -> list[dict[str, float | int]]:
    grouped: dict[tuple[int, int], list[Scored]] = defaultdict(list)
    for row in rows:
        grouped[(row.season, row.week)].append(row)
    weeks: list[dict[str, float | int]] = []
    for season, week in sorted(grouped):
        group = grouped[(season, week)]
        weeks.append(
            {
                "season": season,
                "week": week,
                "sum_model": sum(abs(item.model.q50 - item.actual) for item in group),
                "sum_base": sum(abs(item.baseline.q50 - item.actual) for item in group),
                "sum_model_mean": sum(abs(item.model.mean - item.actual) for item in group),
                "sum_base_mean": sum(abs(item.baseline.mean - item.actual) for item in group),
                "n": len(group),
            }
        )
    return weeks


def _with_ci(
    rows: Sequence[Scored],
    rng: random.Random,
    n_boot: int,
) -> dict[str, object]:
    metrics = _metric_dict(rows)
    weeks = _week_sums(rows)
    interval = bootstrap_mae_diff(weeks, rng, n_boot)
    mean_weeks = [
        {"sum_model": week["sum_model_mean"], "sum_base": week["sum_base_mean"], "n": week["n"]}
        for week in weeks
    ]
    mean_interval = bootstrap_mae_diff(mean_weeks, rng, n_boot)
    metrics["mae_diff_ci"] = [None, None] if interval is None else [interval[0], interval[1]]
    metrics["mae_mean_diff_ci"] = (
        [None, None] if mean_interval is None else [mean_interval[0], mean_interval[1]]
    )
    return metrics


def _coverage_on_target(coverage: float) -> bool:
    return (COVERAGE_LOW - 1e-9) <= coverage <= (COVERAGE_HIGH + 1e-9)


def choose_primary_mae(folds: Sequence[Mapping[str, object]]) -> str:
    """Use the mean head for MAE only when it wins at least 3 pooled seasons.

    The rule is fixed in DESIGN §20. It does not look at bias or coverage.
    """

    wins = 0
    compared = 0
    for fold in folds:
        if not isinstance(fold, Mapping):
            continue
        pooled = fold.get("pooled_offense")
        if not isinstance(pooled, Mapping):
            continue
        q50 = pooled.get("mae")
        mean_mae = pooled.get("mae_mean")
        if not isinstance(q50, (int, float)) or not isinstance(mean_mae, (int, float)):
            continue
        if not math.isfinite(float(q50)) or not math.isfinite(float(mean_mae)):
            continue
        compared += 1
        if float(mean_mae) < float(q50):
            wins += 1
    if compared >= MAE_SEASONS_REQUIRED and wins >= MAE_SEASONS_REQUIRED:
        return "mean"
    return "q50"


def _primary_from(report: Mapping[str, object]) -> str:
    experiment = report.get("experiment")
    if isinstance(experiment, Mapping) and experiment.get("primary_mae") == "mean":
        return "mean"
    return "q50"


def _mae_view(block: Mapping[str, object], primary: str) -> dict[str, object]:
    if primary == "mean":
        return {
            "mae": block.get("mae_mean"),
            "mae_baseline": block.get("mae_baseline"),
            "mae_diff_ci": block.get("mae_mean_diff_ci"),
        }
    return {
        "mae": block.get("mae"),
        "mae_baseline": block.get("mae_baseline"),
        "mae_diff_ci": block.get("mae_diff_ci"),
    }


def promotion_rule(report: Mapping[str, object]) -> dict[str, object]:
    """Pass/fail for each promotion criterion.

    Bias is the mean head. q50 bias is ignored. MAE uses q50 unless the
    experiment selected the mean head under the §20 rule. Empty folds fail
    the criterion they cannot support.
    """

    folds = report.get("folds")
    if not isinstance(folds, list):
        folds = []
    primary = _primary_from(report)
    season_detail: list[dict[str, object]] = []
    seasons_passing = 0
    worse: dict[str, int] = {position: 0 for position in POSITIONS}
    bias_failures: list[dict[str, object]] = []
    scored_bias = 0
    for fold in folds:
        if not isinstance(fold, dict):
            continue
        season = fold.get("season")
        pooled = fold.get("pooled_offense")
        if isinstance(pooled, dict):
            viewed = _mae_view(pooled, primary)
            passed = _season_mae_passes(viewed)
            if passed:
                seasons_passing += 1
            season_detail.append(
                {
                    "season": season,
                    "mae": viewed.get("mae"),
                    "mae_baseline": viewed.get("mae_baseline"),
                    "mae_diff_ci": viewed.get("mae_diff_ci"),
                    "pass": passed,
                    "head": primary,
                }
            )
        positions = fold.get("positions")
        if not isinstance(positions, dict):
            continue
        for position in POSITIONS:
            block = positions.get(position)
            if not isinstance(block, dict):
                continue
            count = block.get("n")
            if not isinstance(count, int) or count <= 0:
                continue
            viewed = _mae_view(block, primary)
            model_mae = viewed.get("mae")
            base_mae = viewed.get("mae_baseline")
            if (
                isinstance(model_mae, (int, float))
                and isinstance(base_mae, (int, float))
                and (float(model_mae) - float(base_mae)) > WORSE_MAE
            ):
                worse[position] += 1
            bias = block.get("bias")
            if isinstance(bias, (int, float)) and math.isfinite(float(bias)):
                scored_bias += 1
                if abs(float(bias)) > BIAS_LIMIT + 1e-9:
                    bias_failures.append(
                        {
                            "season": season,
                            "position": position,
                            "bias": float(bias),
                            "head": "mean",
                        }
                    )
    mae_pass = seasons_passing >= MAE_SEASONS_REQUIRED
    worse_positions = {position: count for position, count in worse.items() if count > 1}
    position_pass = not worse_positions and bool(season_detail)
    bias_pass = scored_bias > 0 and not bias_failures
    calibration = report.get("calibration")
    coverage_map = {}
    if isinstance(calibration, dict):
        raw_coverage = calibration.get("coverage_by_position")
        if isinstance(raw_coverage, dict):
            coverage_map = raw_coverage
    coverage_detail: dict[str, object] = {}
    coverage_failures: list[str] = []
    measured = 0
    for position in POSITIONS:
        block = coverage_map.get(position)
        if not isinstance(block, dict):
            coverage_detail[position] = None
            continue
        coverage = block.get("coverage")
        count = block.get("n")
        coverage_detail[position] = {"coverage": coverage, "n": count}
        if not isinstance(count, int) or count <= 0:
            continue
        if not isinstance(coverage, (int, float)) or not math.isfinite(float(coverage)):
            coverage_failures.append(position)
            continue
        measured += 1
        if not _coverage_on_target(float(coverage)):
            coverage_failures.append(position)
    coverage_pass = measured > 0 and not coverage_failures
    lineup = report.get("lineup")
    points: object = None
    if isinstance(lineup, dict):
        points = lineup.get("points_gained_total")
    lineup_pass = (
        isinstance(points, (int, float)) and math.isfinite(float(points)) and float(points) >= 0
    )
    return {
        "mae_seasons": {
            "pass": mae_pass,
            "seasons_passing": seasons_passing,
            "required": MAE_SEASONS_REQUIRED,
            "detail": season_detail,
        },
        "position_mae": {
            "pass": position_pass,
            "worse_seasons": {position: worse[position] for position in POSITIONS},
            "failures": worse_positions,
        },
        "bias": {
            "pass": bias_pass,
            "failures": bias_failures,
            "limit": BIAS_LIMIT,
            "head": "mean",
        },
        "coverage": {
            "pass": coverage_pass,
            "band": [COVERAGE_LOW, COVERAGE_HIGH],
            "by_position": coverage_detail,
            "failures": coverage_failures,
        },
        "lineup_points": {"pass": lineup_pass, "points_gained": points},
        "promoted": bool(
            mae_pass and position_pass and bias_pass and coverage_pass and lineup_pass
        ),
    }


def _season_mae_passes(pooled: Mapping[str, object]) -> bool:
    model_mae = pooled.get("mae")
    base_mae = pooled.get("mae_baseline")
    interval = pooled.get("mae_diff_ci")
    if not isinstance(model_mae, (int, float)) or not isinstance(base_mae, (int, float)):
        return False
    if float(model_mae) >= float(base_mae):
        return False
    if not isinstance(interval, (list, tuple)) or len(interval) != 2:
        return False
    high = interval[1]
    if not isinstance(high, (int, float)) or not math.isfinite(float(high)):
        return False
    return float(high) < 0.0


def _row_key(row: Mapping[str, object]) -> tuple[int, int]:
    try:
        season = row["season"]
        week = row["week"]
        if isinstance(season, bool) or isinstance(week, bool):
            raise TypeError
        if not isinstance(season, (int, float)) or not isinstance(week, (int, float)):
            raise TypeError
        return int(season), int(week)
    except (KeyError, TypeError, ValueError) as exc:
        raise EvalError("a feature row is missing season or week") from exc


def _trained_through(history: Sequence[Mapping[str, object]]) -> str:
    if not history:
        return "none"
    season, week = max(_row_key(row) for row in history)
    return f"{season:04d}-W{week:02d}"


def _prediction_digest(scored: Sequence[Scored]) -> str:
    digest = hashlib.sha256()
    ordered = sorted(
        scored, key=lambda item: (item.season, item.week, item.player_id, item.position)
    )
    for item in ordered:
        line = (
            f"{item.season}|{item.week}|{item.player_id}|{item.model.mean:.8f}|"
            f"{item.model.q10:.8f}|{item.model.q50:.8f}|{item.model.q90:.8f}|"
            f"{item.model.fallback}|{item.model.regime}\n"
        )
        digest.update(line.encode())
    return digest.hexdigest()


def _starters(
    roster: Sequence[Mapping[str, object]], scores: Mapping[str, float], lineup: Lineup
) -> list[Mapping[str, object]]:
    by_position: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for row in roster:
        by_position[_position(row)].append(row)

    def order(row: Mapping[str, object]) -> tuple[float, str]:
        return (-scores.get(str(row.get("player_id")), 0.0), str(row.get("player_id")))

    for group in by_position.values():
        group.sort(key=order)
    taken: list[Mapping[str, object]] = []

    def take(position: str, count: int) -> None:
        group = by_position[position]
        chosen = group[:count]
        del group[:count]
        taken.extend(chosen)

    take("QB", lineup.qb)
    take("RB", lineup.rb)
    take("WR", lineup.wr)
    take("TE", lineup.te)
    take("K", lineup.k)
    take("DST", lineup.dst)
    flex_pool = by_position["RB"] + by_position["WR"] + by_position["TE"]
    flex_pool.sort(key=order)
    taken.extend(flex_pool[: lineup.flex])
    return taken


def _weighted_sample(
    rng: random.Random,
    pool: list[Mapping[str, object]],
    count: int,
    totals: Mapping[str, float],
) -> list[Mapping[str, object]]:
    chosen: list[Mapping[str, object]] = []
    for _ in range(min(count, len(pool))):
        weights = [max(totals.get(str(row.get("player_id")), 0.0), 0.0) + 0.1 for row in pool]
        total = sum(weights)
        ticket = rng.random() * total
        running = 0.0
        index = len(pool) - 1
        for cursor, weight in enumerate(weights):
            running += weight
            if running >= ticket:
                index = cursor
                break
        chosen.append(pool.pop(index))
    return chosen


def _lineup_week(
    rows: Sequence[Mapping[str, object]],
    model_preds: Sequence[Prediction],
    baseline_preds: Sequence[Prediction],
    *,
    season: int,
    week: int,
    history: Sequence[Mapping[str, object]],
    seed: int,
    teams: int,
    lineup: Lineup,
) -> dict[str, object]:
    totals: dict[str, float] = defaultdict(float)
    for row in history:
        if not _has_actual(row):
            continue
        if _row_key(row) >= (season, week):
            continue
        totals[str(row.get("player_id"))] += float(row["actual"])  # type: ignore[arg-type]
    eligible: dict[str, list[dict[str, object]]] = {position: [] for position in ROSTER_COUNTS}
    actuals: dict[str, float] = {}
    model_mean: dict[str, float] = {}
    baseline_mean: dict[str, float] = {}
    for row, model_pred, baseline_pred in zip(rows, model_preds, baseline_preds, strict=True):
        if not _has_actual(row):
            continue
        position = _position(row)
        if position not in eligible:
            continue
        player_id = str(row.get("player_id"))
        payload = dict(row)
        eligible[position].append(payload)
        actuals[player_id] = float(row["actual"])  # type: ignore[arg-type]
        model_mean[player_id] = model_pred.mean
        baseline_mean[player_id] = baseline_pred.mean
    for group in eligible.values():
        group.sort(key=lambda item: str(item.get("player_id")))
    rng = random.Random(seed + season * 100 + week)
    gained = 0.0
    regret = 0.0
    for _team in range(teams):
        roster: list[Mapping[str, object]] = []
        for position, count in ROSTER_COUNTS.items():
            roster.extend(_weighted_sample(rng, eligible[position], count, totals))
        if not roster:
            continue
        model_starters = _starters(roster, model_mean, lineup)
        baseline_starters = _starters(roster, baseline_mean, lineup)
        hindsight = _starters(roster, actuals, lineup)
        model_points = sum(actuals[str(item.get("player_id"))] for item in model_starters)
        baseline_points = sum(actuals[str(item.get("player_id"))] for item in baseline_starters)
        hindsight_points = sum(actuals[str(item.get("player_id"))] for item in hindsight)
        gained += model_points - baseline_points
        regret += hindsight_points - model_points
    return {"season": season, "week": week, "points_gained": gained, "regret": regret}


def rolling_origin(
    weeks: Mapping[tuple[int, int], Sequence[Mapping[str, object]]],
    test_seasons: Sequence[int],
    *,
    seed: int = 0,
    n_boot: int = 1000,
    teams: int = DEFAULT_TEAMS,
    lineup: Lineup = DEFAULT_LINEUP,
    make_model: ModelFactory | None = None,
    make_baseline: BaselineFactory | None = None,
    calibration_weeks: int = CALIBRATION_WEEKS,
) -> dict[str, object]:
    """Fit before each test week and score that week.

    ``weeks`` maps ``(season, week)`` to feature rows. Rows may carry ``actual``.
    Keys outside ``test_seasons`` are history only.
    """

    if n_boot < 1:
        raise EvalError("bootstrap draws must be at least 1")
    test_set = {int(season) for season in test_seasons}
    if make_model is None:

        def make_model() -> GridironPredictor:
            return GridironPredictor(seed=seed)

    if make_baseline is None:

        def make_baseline() -> LegacyBaseline:
            return LegacyBaseline()

    history: list[Mapping[str, object]] = []
    by_season: dict[int, list[Scored]] = defaultdict(list)
    week_logs: dict[int, list[dict[str, object]]] = defaultdict(list)
    lineup_weeks: list[dict[str, object]] = []
    digest_rows: list[Scored] = []
    for key in sorted(weeks):
        season, week = key
        rows = list(weeks[key])
        if season in test_set:
            calibrating, cal_weeks = calibration_window(history, key, calibration_weeks)
            cal_keys = set(cal_weeks)
            train: list[Mapping[str, object]] = []
            for row in history:
                row_key = _row_key(row)
                if row_key >= key:
                    raise EvalError(f"training row {row_key} is not strictly before {key}")
                if row_key in cal_keys or _is_force_zero(row) or not _has_actual(row):
                    continue
                train.append(row)
            through = _trained_through(train)
            model = make_model()
            baseline = make_baseline()
            fit = getattr(model, "fit", None)
            baseline_fit = getattr(baseline, "fit", None)
            if not callable(fit) or not callable(baseline_fit):
                raise EvalError("predictors need fit() and predict()")
            fit(train, trained_through=through)
            baseline_fit(train, trained_through=through)
            predict = getattr(model, "predict", None)
            baseline_predict = getattr(baseline, "predict", None)
            if not callable(predict) or not callable(baseline_predict):
                raise EvalError("predictors need fit() and predict()")
            cal_preds = list(predict(calibrating)) if calibrating else []
            if len(cal_preds) != len(calibrating):
                raise EvalError("predict() must return one row per calibration input")
            deltas = {
                position: conformal_quantile(values)
                for position, values in _cqr_scores(calibrating, cal_preds).items()
            }
            raw_preds = list(predict(rows))
            baseline_preds = list(baseline_predict(rows))
            if len(raw_preds) != len(rows) or len(baseline_preds) != len(rows):
                raise EvalError("predict() must return one row per input")
            model_preds: list[Prediction] = []
            for row, raw in zip(rows, raw_preds, strict=True):
                delta = deltas.get(_position(row))
                if delta is None or _is_force_zero(row):
                    model_preds.append(raw)
                else:
                    model_preds.append(adjust_interval(raw, delta))
            fallbacks: dict[str, int] = defaultdict(int)
            for row, raw, model_pred, baseline_pred in zip(
                rows, raw_preds, model_preds, baseline_preds, strict=True
            ):
                fallbacks[model_pred.fallback or "model"] += 1
                if _is_force_zero(row) or not _has_actual(row):
                    continue
                scored = Scored(
                    season=season,
                    week=week,
                    position=_position(row),
                    player_id=str(row.get("player_id")),
                    actual=float(row["actual"]),  # type: ignore[arg-type]
                    model=model_pred,
                    baseline=baseline_pred,
                    q10_raw=raw.q10,
                    q90_raw=raw.q90,
                )
                by_season[season].append(scored)
                digest_rows.append(scored)
            lineup_weeks.append(
                _lineup_week(
                    rows,
                    model_preds,
                    baseline_preds,
                    season=season,
                    week=week,
                    history=history,
                    seed=seed,
                    teams=teams,
                    lineup=lineup,
                )
            )
            week_logs[season].append(
                {
                    "season": season,
                    "week": week,
                    "train_rows": len(train),
                    "trained_through": through,
                    "calibration_weeks": [f"{item[0]:04d}-W{item[1]:02d}" for item in cal_weeks],
                    "conformal_delta": deltas,
                    "fallbacks": dict(sorted(fallbacks.items())),
                }
            )
            logger.info(
                "Evaluated %s week %s · train rows %s · through %s",
                season,
                week,
                len(train),
                through,
            )
        history.extend(rows)

    rng = random.Random(seed)
    folds: list[dict[str, object]] = []
    for season in sorted(test_set):
        scored = by_season.get(season, [])
        positions: dict[str, object] = {}
        for position in POSITIONS:
            group = [row for row in scored if row.position == position]
            block = _with_ci(group, rng, n_boot)
            if position not in OFFENSE:
                block["spearman"] = block.get("spearman")
            positions[position] = block
        offense = [row for row in scored if row.position in OFFENSE]
        pooled = _with_ci(offense, rng, n_boot)
        pooled["spearman"] = None
        pooled["top12_hit_rate"] = None
        vegas: dict[str, object] = {}
        for position in ("K", "DST"):
            group = [row for row in scored if row.position == position]
            vegas[position] = {
                "with_lines": _metric_dict(
                    [row for row in group if row.model.regime == "with_lines"]
                ),
                "without_lines": _metric_dict(
                    [row for row in group if row.model.regime == "without_lines"]
                ),
            }
        folds.append(
            {
                "season": season,
                "positions": positions,
                "pooled_offense": pooled,
                "vegas": vegas,
                "weeks": week_logs.get(season, []),
            }
        )
    coverage_by_position: dict[str, object] = {}
    coverage_raw_by_position: dict[str, object] = {}
    for position in POSITIONS:
        group = [row for row in digest_rows if row.position == position]
        if not group:
            coverage_by_position[position] = {"coverage": None, "n": 0}
            coverage_raw_by_position[position] = {"coverage": None, "n": 0}
            continue
        covered = sum(1 for row in group if row.model.q10 <= row.actual <= row.model.q90)
        covered_raw = sum(1 for row in group if row.q10_raw <= row.actual <= row.q90_raw)
        coverage_by_position[position] = {"coverage": covered / len(group), "n": len(group)}
        coverage_raw_by_position[position] = {
            "coverage": covered_raw / len(group),
            "n": len(group),
        }
    primary = choose_primary_mae(folds)
    if lineup_weeks:
        points_gained = sum(float(item["points_gained"]) for item in lineup_weeks)
        regret = sum(float(item["regret"]) for item in lineup_weeks)
    else:
        points_gained = None
        regret = None
    report: dict[str, object] = {
        "experiment": {
            "id": f"{MODEL_VERSION}-seed{seed}",
            "git_sha": None,
            "model_version": MODEL_VERSION,
            "baseline_version": BASELINE_VERSION,
            "vegas_version": VEGAS_VERSION,
            "feature_version": FEATURE_VERSION,
            "scoring_version": SCORING_VERSION,
            "seed": seed,
            "test_seasons": [int(season) for season in test_seasons],
            "params": dict(FROZEN_PARAMS),
            "tuned_on_test": False,
            "primary_mae": primary,
            "rule": "amended_2026-10-18_post_hoc",
            "notes": [
                "No hyperparameter was changed after looking at 2022–2025.",
                "The 2026-10-18 bias amendment was decided after the M3 results.",
                "Promotion bias is the mean head. q50 bias is informational.",
                "Primary MAE stays on q50 unless the mean head wins at least 3 seasons.",
                "Intervals are CQR-adjusted on the last 8 weeks before the test week.",
                "min_data_in_leaf is min(40, max(5, n // 10)) so a small history can still split.",
                "Lineup expected value uses the mean head.",
                "force_zero rows predict 0 and are excluded from fit and from metrics.",
                "The legacy baseline reads points_ewma, points_roll3, and points_roll5.",
                "opportunity_momentum and td_dependency are absent on v2.0, so those terms are 0.",
                "points_roll2 is absent, so baseline volatility drops that summary.",
                "Thursday simulated_capture has no closing line, so K/DST use the no-line fit.",
            ],
        },
        "folds": folds,
        "calibration": {
            "method": "cqr_trailing_weeks",
            "reason": CALIBRATION_REASON,
            "weeks": calibration_weeks,
            "min_scores": MIN_CALIBRATION_SCORES,
            "alpha": CONFORMAL_ALPHA,
            "coverage_by_position": coverage_by_position,
            "coverage_raw_by_position": coverage_raw_by_position,
        },
        "lineup": {
            "method": "seeded_synthetic_rosters",
            "teams": teams,
            "roster": dict(ROSTER_COUNTS),
            "points_gained_total": points_gained,
            "regret_total": regret,
            "points_gained_per_week": lineup_weeks,
            "note": (
                "Yahoo rosters are not in the store. Historical weeks use seeded "
                "15-player rosters drawn by prior actual points, disjoint across teams. "
                "Points gained is the model lineup minus the baseline lineup on the same rosters."
            ),
        },
        "prediction_digest": _prediction_digest(digest_rows),
    }
    report["promotion"] = promotion_rule(report)
    return report


def collect_weeks(
    data_dir: Path,
    seasons: Sequence[int],
    *,
    policy: str = "simulated_capture",
) -> dict[tuple[int, int], list[dict[str, object]]]:
    """Build every regular-season week and attach actual fantasy points.

    Labels come from ``player_weeks`` as of 2030, which is after the games.
    They are targets, not features. A week with no schedule is skipped.
    """

    from gridiron_lab.data.store import open_db

    actuals: dict[tuple[int, int, str], float] = {}
    for row in open_db(data_dir).as_of("player_weeks", datetime(2030, 1, 1, tzinfo=UTC)):
        if row.get("season_type") != "REG":
            continue
        player_id = row.get("player_id")
        points = row.get("fantasy_points")
        season = row.get("season")
        week = row.get("week")
        if not isinstance(player_id, str) or not player_id:
            continue
        if isinstance(season, bool) or isinstance(week, bool):
            continue
        if not isinstance(season, int) or not isinstance(week, int):
            continue
        if isinstance(points, bool) or not isinstance(points, (int, float)):
            continue
        if not math.isfinite(float(points)):
            continue
        actuals[(season, week, player_id)] = float(points)
    frames: dict[tuple[int, int], list[dict[str, object]]] = {}
    skipped = 0
    for season in seasons:
        for week in range(1, 19):
            try:
                built = build_features(season, week, policy, data_dir=data_dir)
            except FeatureError:
                skipped += 1
                continue
            frame: list[dict[str, object]] = []
            for row in built:
                item = dict(row)
                item["actual"] = actuals.get((season, week, str(row.get("player_id"))))
                frame.append(item)
            frames[(season, week)] = frame
    logger.info("Collected %s weeks · skipped %s · policy %s", len(frames), skipped, policy)
    return frames


def git_sha() -> str | None:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    sha = completed.stdout.strip()
    return sha or None


def write_report(report: Mapping[str, object], output: Path) -> None:
    payload = _jsonable(dict(report))
    output.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, allow_nan=False)
    output.write_text(text + "\n", encoding="utf-8")


def run_evaluation(
    *,
    data_dir: Path,
    seasons: Sequence[int],
    test_seasons: Sequence[int],
    output: Path,
    seed: int = 0,
    policy: str = "simulated_capture",
    n_boot: int = 1000,
    teams: int = DEFAULT_TEAMS,
    lineup: Lineup = DEFAULT_LINEUP,
) -> dict[str, object]:
    """Evaluate ``test_seasons`` and write one JSON file. No model file is written."""

    if policy != "simulated_capture":
        raise EvalError("the backtest policy is simulated_capture")
    weeks = collect_weeks(data_dir, seasons, policy=policy)
    report = rolling_origin(
        weeks,
        test_seasons,
        seed=seed,
        n_boot=n_boot,
        teams=teams,
        lineup=lineup,
    )
    experiment = report.get("experiment")
    if isinstance(experiment, dict):
        experiment["git_sha"] = git_sha()
        experiment["policy"] = policy
        experiment["seasons"] = [int(season) for season in seasons]
    write_report(report, output)
    log_path = data_dir / "experiments.jsonl"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(_jsonable(report), allow_nan=False) + "\n")
    except OSError as exc:
        logger.warning("Could not append %s: %s", log_path, exc.strerror or exc)
    logger.info("Wrote evaluation %s", output)
    return report
