"""Weekly fantasy-point predictors.

Skill positions (QB, RB, WR, TE) each get one LightGBM model with a squared-error
head and three quantile heads. K and DST get a small OLS model that routes on
``vegas_missing``. ``legacy_baseline_v1`` is the previous tool's blend, applied
to the v2.0 feature row.

LightGBM 4.6.0 is the booster because it has a quantile objective, L2, shallow
trees, and early stopping on an explicit validation set. scikit-learn's
``HistGradientBoostingRegressor`` early-stops on a random internal split, which
would mix later training weeks into the stopping decision. The parameters below
were fixed before the 2022–2025 evaluation and are not tuned on those seasons.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from gridiron_lab.features import FEATURE_COLUMNS, FEATURE_VERSION
from gridiron_lab.logging import get_logger
from gridiron_lab.scoring import SCORING_VERSION

if TYPE_CHECKING:
    import lightgbm as lgb

logger = get_logger("models")

MODEL_VERSION = "lgbm_skill_v1"
BASELINE_VERSION = "legacy_baseline_v1"
VEGAS_VERSION = "vegas_simple_v1"
SKILL_POSITIONS = ("QB", "RB", "WR", "TE")
VEGAS_POSITIONS = ("K", "DST")

# Pre-registered. Do not retune on the 2022–2025 test seasons.
MAX_ROUNDS = 80
EARLY_STOPPING_ROUNDS = 20
FIXED_ROUNDS_SHORT = 30
MIN_TRAIN = 40
MIN_WEEKS_FOR_EARLY_STOP = 5
VAL_FRACTION = 0.2
MIN_OLS_ROWS = 8
DEFAULT_SIGMA = 4.0
# Central 80% of a normal distribution: Φ^{-1}(0.9).
Z80 = 1.2815515655446004

_EXCLUDED_FEATURES = frozenset(
    {
        "player_id",
        "season",
        "season_type",
        "team",
        "opponent",
        "position",
        "game_id",
        "as_of",
        "as_of_policy",
        "feature_version",
        "injury_status",
        "practice_status",
        "depth_position",
        "force_zero",
    }
)
_ENCODED_FEATURES = (
    "injury_out",
    "injury_doubtful",
    "injury_questionable",
    "practice_dnp",
    "practice_limited",
    "practice_full",
)
MODEL_FEATURES = tuple(name for name in FEATURE_COLUMNS if name not in _EXCLUDED_FEATURES) + (
    _ENCODED_FEATURES
)

FROZEN_PARAMS: dict[str, object] = {
    "library": "lightgbm",
    "library_version": "4.6.0",
    "objective_mean": "regression",
    "objective_quantile": "quantile",
    "quantiles": (0.1, 0.5, 0.9),
    "max_depth": 3,
    "num_leaves": 8,
    "lambda_l2": 1.0,
    "learning_rate": 0.05,
    "min_data_in_leaf_cap": 40,
    "bagging_fraction": 1.0,
    "bagging_freq": 0,
    "feature_fraction": 1.0,
    "max_rounds": MAX_ROUNDS,
    "early_stopping_rounds": EARLY_STOPPING_ROUNDS,
    "fixed_rounds_when_history_is_short": FIXED_ROUNDS_SHORT,
    "min_train_rows": MIN_TRAIN,
    "min_weeks_for_early_stop": MIN_WEEKS_FOR_EARLY_STOP,
    "validation_week_fraction": VAL_FRACTION,
    "num_threads": 1,
    "deterministic": True,
    "tuned_on_test": False,
}


class ModelError(Exception):
    """A predictor was asked to score a row it cannot represent."""


@dataclass(frozen=True, slots=True)
class Prediction:
    """One player's distribution for one week."""

    mean: float
    q10: float
    q50: float
    q90: float
    model_version: str
    feature_version: str
    scoring_version: str
    trained_through: str | None
    fallback: str | None = None
    regime: str | None = None


def sort_quantiles(q10: float, q50: float, q90: float) -> tuple[float, float, float]:
    """Put quantile heads in order. The mean head is left alone."""

    ordered = sorted((q10, q50, q90))
    return ordered[0], ordered[1], ordered[2]


def _position(row: Mapping[str, object]) -> str:
    raw = str(row.get("position") or "").strip().upper()
    if raw == "DEF":
        return "DST"
    return raw


def _is_force_zero(row: Mapping[str, object]) -> bool:
    value = row.get("force_zero")
    if value is None or isinstance(value, bool):
        return bool(value)
    if isinstance(value, (int, float)):
        return float(value) == 1.0
    return False


def _has_actual(row: Mapping[str, object]) -> bool:
    value = row.get("actual")
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value))


def _finite(value: object) -> float | None:
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return number


def _filled(value: object, default: float) -> float:
    number = _finite(value)
    if number is None:
        return default
    return number


def _feature_version(row: Mapping[str, object]) -> str:
    value = row.get("feature_version")
    if isinstance(value, str) and value:
        return value
    return FEATURE_VERSION


def _zero(row: Mapping[str, object], version: str, trained_through: str | None) -> Prediction:
    return Prediction(
        mean=0.0,
        q10=0.0,
        q50=0.0,
        q90=0.0,
        model_version=version,
        feature_version=_feature_version(row),
        scoring_version=SCORING_VERSION,
        trained_through=trained_through,
        fallback="force_zero",
        regime="force_zero",
    )


def _number_or_nan(value: object) -> float:
    number = _finite(value)
    if number is None:
        return math.nan
    return number


def _injury_flags(row: Mapping[str, object]) -> dict[str, float]:
    injury = str(row.get("injury_status") or "").strip().upper()
    practice = str(row.get("practice_status") or "").strip().upper()
    return {
        "injury_out": 1.0 if injury in {"OUT", "O", "IR", "PUP"} else 0.0,
        "injury_doubtful": 1.0 if injury in {"DOUBTFUL", "D"} else 0.0,
        "injury_questionable": 1.0 if injury in {"QUESTIONABLE", "Q"} else 0.0,
        "practice_dnp": 1.0 if practice in {"DNP", "DID NOT PARTICIPATE"} else 0.0,
        "practice_limited": 1.0 if practice == "LIMITED" else 0.0,
        "practice_full": 1.0 if practice == "FULL" else 0.0,
    }


def feature_matrix(rows: Sequence[Mapping[str, object]]) -> np.ndarray:
    """Numeric model matrix. Missing values stay NaN so LightGBM can split on them."""

    matrix = np.empty((len(rows), len(MODEL_FEATURES)), dtype=np.float64)
    encoded = set(_ENCODED_FEATURES)
    for index, row in enumerate(rows):
        flags = _injury_flags(row)
        for column, name in enumerate(MODEL_FEATURES):
            if name in encoded:
                matrix[index, column] = flags[name]
            else:
                matrix[index, column] = _number_or_nan(row.get(name))
    return matrix


class LegacyBaseline:
    """``project_baseline`` from the previous tool, on v2.0 columns.

    The old blend is 0.45 · EWMA + 0.25 · roll3 + 0.15 · roll5 + 0.15 · season.
    The season term reuses EWMA, so the effective weights are 0.60 / 0.25 / 0.15.
    Opportunity momentum and TD dependency are read when a caller supplies them
    and otherwise contribute 0, which is what ``fillna(0)`` did. v2.0 has no
    ``points_roll2``, so volatility uses the four summaries that do exist.
    The projection and the low end of the range are floored at 0.
    """

    version = BASELINE_VERSION

    def __init__(self) -> None:
        self.trained_through: str | None = None

    def fit(self, rows: Sequence[Mapping[str, object]], *, trained_through: str) -> None:
        del rows
        self.trained_through = trained_through

    def predict(self, rows: Sequence[Mapping[str, object]]) -> list[Prediction]:
        return [self._one(row) for row in rows]

    def _one(self, row: Mapping[str, object]) -> Prediction:
        if _is_force_zero(row):
            return _zero(row, self.version, self.trained_through)
        ewma = _filled(row.get("points_ewma"), 0.0)
        roll3 = _filled(row.get("points_roll3"), ewma)
        roll5 = _filled(row.get("points_roll5"), ewma)
        season = ewma
        base = 0.45 * ewma + 0.25 * roll3 + 0.15 * roll5 + 0.15 * season
        momentum = _filled(row.get("opportunity_momentum"), 0.0)
        adjustment = min(2.5, max(-2.5, momentum * 0.35))
        dependency = _filled(row.get("td_dependency"), 0.0)
        penalty = min(2.0, max(0.0, (dependency - 0.45) * 2.0))
        projection = max(0.0, base + adjustment - penalty)
        samples: list[float] = []
        for key in ("points_last", "points_roll2", "points_roll3", "points_roll5", "points_ewma"):
            sample = _finite(row.get(key))
            if sample is not None:
                samples.append(sample)
        if len(samples) >= 2:
            volatility = float(np.std(np.asarray(samples, dtype=np.float64), ddof=0))
        else:
            volatility = DEFAULT_SIGMA
        low = max(0.0, projection - 1.15 * volatility)
        high = projection + 1.15 * volatility
        q10, q50, q90 = sort_quantiles(low, projection, high)
        return Prediction(
            mean=projection,
            q10=q10,
            q50=q50,
            q90=q90,
            model_version=self.version,
            feature_version=_feature_version(row),
            scoring_version=SCORING_VERSION,
            trained_through=self.trained_through,
            regime="baseline",
        )


def _leaf_size(n_rows: int) -> int:
    return min(40, max(5, n_rows // 10))


def _lgb_params(seed: int, *, quantile: float | None, leaf: int) -> dict[str, object]:
    params: dict[str, object] = {
        "max_depth": 3,
        "num_leaves": 8,
        "lambda_l2": 1.0,
        "learning_rate": 0.05,
        "min_data_in_leaf": leaf,
        "bagging_fraction": 1.0,
        "bagging_freq": 0,
        "feature_fraction": 1.0,
        "deterministic": True,
        "force_col_wise": True,
        "feature_pre_filter": False,
        "num_threads": 1,
        "verbosity": -1,
        "seed": seed,
        "bagging_seed": seed,
        "feature_fraction_seed": seed,
        "data_random_seed": seed,
        "extra_seed": seed,
    }
    if quantile is None:
        params["objective"] = "regression"
        params["metric"] = "l2"
        params["boost_from_average"] = True
    else:
        params["objective"] = "quantile"
        params["alpha"] = quantile
        params["metric"] = "quantile"
        params["boost_from_average"] = False
    return params


def _best_rounds(
    params: dict[str, object],
    features: np.ndarray,
    target: np.ndarray,
    train_mask: np.ndarray,
) -> int:
    import lightgbm as lgb

    train_set = lgb.Dataset(
        features[train_mask],
        label=target[train_mask],
        feature_name=list(MODEL_FEATURES),
        free_raw_data=False,
    )
    valid_set = lgb.Dataset(
        features[~train_mask],
        label=target[~train_mask],
        reference=train_set,
        feature_name=list(MODEL_FEATURES),
        free_raw_data=False,
    )
    booster = lgb.train(
        params,
        train_set,
        num_boost_round=MAX_ROUNDS,
        valid_sets=[valid_set],
        valid_names=["temporal"],
        callbacks=[
            lgb.early_stopping(EARLY_STOPPING_ROUNDS, verbose=False),
            lgb.log_evaluation(period=0),
        ],
    )
    best = int(booster.best_iteration or 0)
    if best < 1:
        return FIXED_ROUNDS_SHORT
    return min(best, MAX_ROUNDS)


def _fit_booster(
    params: dict[str, object],
    features: np.ndarray,
    target: np.ndarray,
    rounds: int,
) -> lgb.Booster:
    import lightgbm as lgb

    dataset = lgb.Dataset(
        features,
        label=target,
        feature_name=list(MODEL_FEATURES),
        free_raw_data=False,
    )
    return lgb.train(
        params,
        dataset,
        num_boost_round=rounds,
        callbacks=[lgb.log_evaluation(period=0)],
    )


class SkillGBM:
    """One regularized gradient-boosted model for a single skill position."""

    def __init__(self, position: str, seed: int) -> None:
        if position not in SKILL_POSITIONS:
            raise ModelError(f"{position} is not a skill position")
        self.position = position
        self.seed = seed
        self.trained_through: str | None = None
        self.fallback: str | None = None
        self.rounds: dict[str, int] = {}
        self._boosters: dict[str, lgb.Booster] = {}
        self._baseline = LegacyBaseline()

    def fit(self, rows: Sequence[Mapping[str, object]], *, trained_through: str) -> None:
        self.trained_through = trained_through
        self._baseline.trained_through = trained_through
        usable = [
            row
            for row in rows
            if _position(row) == self.position and not _is_force_zero(row) and _has_actual(row)
        ]
        self._boosters = {}
        self.rounds = {}
        if len(usable) < MIN_TRAIN:
            self.fallback = "insufficient_history"
            return
        self.fallback = None
        weeks = sorted({(int(row["season"]), int(row["week"])) for row in usable})
        features = feature_matrix(usable)
        target = np.asarray([float(row["actual"]) for row in usable], dtype=np.float64)
        leaf = _leaf_size(len(usable))
        heads: tuple[tuple[str, float | None], ...] = (
            ("mean", None),
            ("q10", 0.1),
            ("q50", 0.5),
            ("q90", 0.9),
        )
        train_mask = _temporal_mask(usable, weeks)
        for name, quantile in heads:
            params = _lgb_params(self.seed, quantile=quantile, leaf=leaf)
            if train_mask is None:
                rounds = FIXED_ROUNDS_SHORT
            else:
                rounds = _best_rounds(params, features, target, train_mask)
            self.rounds[name] = rounds
            self._boosters[name] = _fit_booster(params, features, target, rounds)

    def predict(self, rows: Sequence[Mapping[str, object]]) -> list[Prediction]:
        if self.fallback == "insufficient_history" or not self._boosters:
            predictions = self._baseline.predict(rows)
            return [
                Prediction(
                    mean=item.mean,
                    q10=item.q10,
                    q50=item.q50,
                    q90=item.q90,
                    model_version=MODEL_VERSION,
                    feature_version=item.feature_version,
                    scoring_version=item.scoring_version,
                    trained_through=self.trained_through,
                    fallback="force_zero"
                    if item.fallback == "force_zero"
                    else "insufficient_history",
                    regime="force_zero" if item.regime == "force_zero" else "baseline",
                )
                for item in predictions
            ]
        order = [index for index, row in enumerate(rows) if not _is_force_zero(row)]
        scored = [rows[index] for index in order]
        predicted: dict[int, Prediction] = {}
        if scored:
            matrix = feature_matrix(scored)
            raw = {
                name: booster.predict(matrix, num_threads=1)
                for name, booster in self._boosters.items()
            }
            for offset, row in enumerate(scored):
                mean = float(raw["mean"][offset])
                q10 = float(raw["q10"][offset])
                q50 = float(raw["q50"][offset])
                q90 = float(raw["q90"][offset])
                if not all(math.isfinite(value) for value in (mean, q10, q50, q90)):
                    replacement = self._baseline.predict([row])[0]
                    predicted[order[offset]] = Prediction(
                        mean=replacement.mean,
                        q10=replacement.q10,
                        q50=replacement.q50,
                        q90=replacement.q90,
                        model_version=MODEL_VERSION,
                        feature_version=replacement.feature_version,
                        scoring_version=SCORING_VERSION,
                        trained_through=self.trained_through,
                        fallback="non_finite",
                        regime="baseline",
                    )
                    continue
                q10, q50, q90 = sort_quantiles(q10, q50, q90)
                predicted[order[offset]] = Prediction(
                    mean=mean,
                    q10=q10,
                    q50=q50,
                    q90=q90,
                    model_version=MODEL_VERSION,
                    feature_version=_feature_version(row),
                    scoring_version=SCORING_VERSION,
                    trained_through=self.trained_through,
                    regime="gbm",
                )
        output: list[Prediction] = []
        for index, row in enumerate(rows):
            if index in predicted:
                output.append(predicted[index])
            else:
                output.append(_zero(row, MODEL_VERSION, self.trained_through))
        return output


def _temporal_mask(
    rows: Sequence[Mapping[str, object]],
    weeks: Sequence[tuple[int, int]],
) -> np.ndarray | None:
    """Hold out the last 20% of training weeks. The test week is not in ``weeks``."""

    if len(weeks) < MIN_WEEKS_FOR_EARLY_STOP:
        return None
    n_valid = max(1, int(len(weeks) * VAL_FRACTION))
    cut = len(weeks) - n_valid
    if cut < 1:
        return None
    valid = set(weeks[cut:])
    mask = np.asarray(
        [(int(row["season"]), int(row["week"])) not in valid for row in rows],
        dtype=bool,
    )
    if int(mask.sum()) < 5 or int((~mask).sum()) < 5:
        return None
    return mask


def _vegas_missing(row: Mapping[str, object]) -> bool:
    value = row.get("vegas_missing")
    if value is None:
        return True
    if isinstance(value, bool):
        return value
    number = _finite(value)
    if number is None:
        return True
    return number != 0.0


def _opponent_implied(row: Mapping[str, object]) -> float | None:
    total = _finite(row.get("game_total"))
    team = _finite(row.get("implied_team_total"))
    if total is None or team is None:
        return None
    return total - team


def _has_line(row: Mapping[str, object], position: str) -> bool:
    if _vegas_missing(row):
        return False
    if position == "DST":
        return _opponent_implied(row) is not None
    return _finite(row.get("implied_team_total")) is not None


def _vegas_design(
    row: Mapping[str, object], position: str, *, with_lines: bool
) -> list[float] | None:
    ewma = _filled(row.get("points_ewma"), 0.0)
    if with_lines:
        line = (
            _opponent_implied(row) if position == "DST" else _finite(row.get("implied_team_total"))
        )
        if line is None:
            return None
        return [1.0, line, ewma]
    roll3 = _finite(row.get("points_roll3"))
    return [1.0, ewma, ewma if roll3 is None else roll3]


def _fit_ols(
    rows: Sequence[Mapping[str, object]], position: str, *, with_lines: bool
) -> tuple[np.ndarray, float] | None:
    design_rows: list[list[float]] = []
    target: list[float] = []
    for row in rows:
        design = _vegas_design(row, position, with_lines=with_lines)
        if design is None or not _has_actual(row):
            continue
        design_rows.append(design)
        target.append(float(row["actual"]))
    if len(design_rows) < MIN_OLS_ROWS:
        return None
    matrix = np.asarray(design_rows, dtype=np.float64)
    observed = np.asarray(target, dtype=np.float64)
    coef, _, _, _ = np.linalg.lstsq(matrix, observed, rcond=None)
    if not np.all(np.isfinite(coef)):
        return None
    residual = observed - matrix @ coef
    if residual.shape[0] < MIN_OLS_ROWS:
        sigma = DEFAULT_SIGMA
    else:
        sigma = float(np.std(residual, ddof=1))
        if not math.isfinite(sigma) or sigma <= 0:
            sigma = DEFAULT_SIGMA
    return coef, sigma


class VegasModel:
    """OLS for K or DST, with a separate fit for rows that have a line.

    With lines, K uses the implied team total and DST uses the opponent's
    implied total (game total minus team total), plus the points EWMA.
    Without lines, both use points EWMA and the 3-game rolling mean.
    A missing line coefficient falls back to the no-line fit. Means are not
    floored: this league allows negative scores.
    """

    def __init__(self, position: str) -> None:
        if position not in VEGAS_POSITIONS:
            raise ModelError(f"{position} is not a K or DST model")
        self.position = position
        self.trained_through: str | None = None
        self.with_lines: tuple[np.ndarray, float] | None = None
        self.without_lines: tuple[np.ndarray, float] | None = None
        self._baseline = LegacyBaseline()

    def fit(self, rows: Sequence[Mapping[str, object]], *, trained_through: str) -> None:
        self.trained_through = trained_through
        self._baseline.trained_through = trained_through
        usable = [
            row
            for row in rows
            if _position(row) == self.position and not _is_force_zero(row) and _has_actual(row)
        ]
        lined = [row for row in usable if _has_line(row, self.position)]
        unlined = [row for row in usable if not _has_line(row, self.position)]
        self.with_lines = _fit_ols(lined, self.position, with_lines=True)
        self.without_lines = _fit_ols(unlined, self.position, with_lines=False)

    def predict(self, rows: Sequence[Mapping[str, object]]) -> list[Prediction]:
        return [self._one(row) for row in rows]

    def _one(self, row: Mapping[str, object]) -> Prediction:
        if _is_force_zero(row):
            return _zero(row, VEGAS_VERSION, self.trained_through)
        use_lines = _has_line(row, self.position)
        fallback: str | None = None
        coef: np.ndarray | None = None
        sigma = DEFAULT_SIGMA
        regime = "without_lines"
        if use_lines and self.with_lines is not None:
            coef, sigma = self.with_lines
            regime = "with_lines"
        elif self.without_lines is not None:
            coef, sigma = self.without_lines
            regime = "without_lines"
            if use_lines:
                fallback = "lines_untrained"
        if coef is None:
            replacement = self._baseline.predict([row])[0]
            return Prediction(
                mean=replacement.mean,
                q10=replacement.q10,
                q50=replacement.q50,
                q90=replacement.q90,
                model_version=VEGAS_VERSION,
                feature_version=replacement.feature_version,
                scoring_version=SCORING_VERSION,
                trained_through=self.trained_through,
                fallback="insufficient_history",
                regime="baseline",
            )
        design = _vegas_design(row, self.position, with_lines=regime == "with_lines")
        if design is None:
            replacement = self._baseline.predict([row])[0]
            return Prediction(
                mean=replacement.mean,
                q10=replacement.q10,
                q50=replacement.q50,
                q90=replacement.q90,
                model_version=VEGAS_VERSION,
                feature_version=replacement.feature_version,
                scoring_version=SCORING_VERSION,
                trained_through=self.trained_through,
                fallback="insufficient_history",
                regime="baseline",
            )
        mean = float(np.asarray(design, dtype=np.float64) @ coef)
        if not math.isfinite(mean):
            replacement = self._baseline.predict([row])[0]
            return Prediction(
                mean=replacement.mean,
                q10=replacement.q10,
                q50=replacement.q50,
                q90=replacement.q90,
                model_version=VEGAS_VERSION,
                feature_version=replacement.feature_version,
                scoring_version=SCORING_VERSION,
                trained_through=self.trained_through,
                fallback="non_finite",
                regime="baseline",
            )
        q10, q50, q90 = sort_quantiles(mean - Z80 * sigma, mean, mean + Z80 * sigma)
        return Prediction(
            mean=mean,
            q10=q10,
            q50=q50,
            q90=q90,
            model_version=VEGAS_VERSION,
            feature_version=_feature_version(row),
            scoring_version=SCORING_VERSION,
            trained_through=self.trained_through,
            fallback=fallback,
            regime=regime,
        )


class GridironPredictor:
    """Skill GBMs plus the K/DST line models, behind one fit/predict pair."""

    def __init__(self, seed: int = 0) -> None:
        self.seed = seed
        self.trained_through: str | None = None
        self.skill = {position: SkillGBM(position, seed) for position in SKILL_POSITIONS}
        self.vegas = {position: VegasModel(position) for position in VEGAS_POSITIONS}
        self.baseline = LegacyBaseline()

    def fit(self, rows: Sequence[Mapping[str, object]], *, trained_through: str) -> None:
        self.trained_through = trained_through
        self.baseline.trained_through = trained_through
        grouped: dict[str, list[Mapping[str, object]]] = defaultdict(list)
        for row in rows:
            grouped[_position(row)].append(row)
        for position, model in self.skill.items():
            model.fit(grouped.get(position, []), trained_through=trained_through)
        for position, model in self.vegas.items():
            model.fit(grouped.get(position, []), trained_through=trained_through)
        fitted = sum(1 for model in self.skill.values() if model.fallback is None)
        logger.info(
            "Fitted %s · skill models %s/%s · through %s · rows %s",
            MODEL_VERSION,
            fitted,
            len(self.skill),
            trained_through,
            len(rows),
        )

    def predict(self, rows: Sequence[Mapping[str, object]]) -> list[Prediction]:
        output: list[Prediction | None] = [None] * len(rows)
        buckets: dict[str, list[int]] = defaultdict(list)
        for index, row in enumerate(rows):
            buckets[_position(row)].append(index)
        for position, indexes in buckets.items():
            group = [rows[index] for index in indexes]
            if position in self.skill:
                predictions = self.skill[position].predict(group)
            elif position in self.vegas:
                predictions = self.vegas[position].predict(group)
            else:
                predictions = self.baseline.predict(group)
            for index, prediction in zip(indexes, predictions, strict=True):
                output[index] = prediction
        return [item for item in output if item is not None]
