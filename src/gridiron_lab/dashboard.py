"""Build the dashboard JSON from a roster file and optional predictions.

League-wide Yahoo rosters are not available until M5. This exporter reads a
roster file the caller supplies. The repository ships an example file of
fictional players.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from pathlib import Path

from gridiron_lab.config import AppConfig, Lineup
from gridiron_lab.features import RecentSeries, recent_series
from gridiron_lab.lineup import CLOSE_CALL_MARGIN, FLEX_POSITIONS, recommend

DASHBOARD_VERSION = "1.1"
RECENT_LIMIT = 6
WIN_PROBABILITY_SD = 25.0
OBJECTIVE = "q50"

# Published M3.1 backtest (main 33d5bbc). Not refit for the dashboard.
# Columns: position, season, q50 MAE, baseline MAE, mean bias, adjusted coverage.
_M3_ROWS: tuple[tuple[str, int, float, float, float, float], ...] = (
    ("QB", 2022, 7.091, 7.479, 0.658, 0.798),
    ("RB", 2022, 3.672, 4.063, 0.243, 0.821),
    ("WR", 2022, 3.312, 3.731, 0.400, 0.817),
    ("TE", 2022, 2.392, 2.763, 0.132, 0.837),
    ("K", 2022, 3.741, 4.077, -0.128, 0.806),
    ("DST", 2022, 4.231, 4.592, -0.295, 0.814),
    ("QB", 2023, 7.189, 7.521, 0.700, 0.782),
    ("RB", 2023, 3.608, 3.913, 0.219, 0.802),
    ("WR", 2023, 3.311, 3.614, 0.277, 0.831),
    ("TE", 2023, 2.190, 2.553, 0.206, 0.846),
    ("K", 2023, 3.826, 4.197, -0.111, 0.797),
    ("DST", 2023, 4.832, 5.235, 0.046, 0.790),
    ("QB", 2024, 7.422, 7.901, -0.050, 0.805),
    ("RB", 2024, 3.703, 3.915, -0.106, 0.804),
    ("WR", 2024, 3.358, 3.688, 0.033, 0.832),
    ("TE", 2024, 2.275, 2.509, 0.112, 0.858),
    ("K", 2024, 4.061, 4.433, -0.565, 0.789),
    ("DST", 2024, 4.543, 4.930, 0.159, 0.807),
    ("QB", 2025, 7.427, 8.358, 0.716, 0.824),
    ("RB", 2025, 3.800, 4.162, -0.158, 0.808),
    ("WR", 2025, 3.252, 3.544, 0.050, 0.819),
    ("TE", 2025, 2.391, 2.687, 0.020, 0.844),
    ("K", 2025, 4.049, 4.406, -0.552, 0.800),
    ("DST", 2025, 4.342, 4.742, -1.513, 0.805),
)
_M3_POOLED_COVERAGE = {
    "QB": 0.802,
    "RB": 0.809,
    "WR": 0.825,
    "TE": 0.846,
    "K": 0.798,
    "DST": 0.804,
}
_M3_MAE_DIFF = (
    (2022, -0.398),
    (2023, -0.319),
    (2024, -0.295),
    (2025, -0.382),
)


class DashboardError(Exception):
    """The roster file or the prediction file cannot be exported."""


def published_m3_lab() -> dict[str, object]:
    """The M3.1 verdict. The candidate was not promoted."""

    by_position: dict[str, list[dict[str, object]]] = {}
    for position, season, mae, baseline, bias, coverage in _M3_ROWS:
        by_position.setdefault(position, []).append(
            {
                "season": season,
                "mae": mae,
                "mae_baseline": baseline,
                "bias": bias,
                "coverage": coverage,
            }
        )
    return {
        "source": "m3.1_backtest",
        "model_version": "lgbm_skill_v1",
        "primary_mae": "q50",
        "promoted": False,
        "points_gained_vs_baseline": 1591.3,
        "mae_diff_by_season": [
            {"season": season, "mae_diff": diff} for season, diff in _M3_MAE_DIFF
        ],
        "pooled_coverage": dict(_M3_POOLED_COVERAGE),
        "positions": [
            {"position": position, "seasons": seasons} for position, seasons in by_position.items()
        ],
        "criteria": [
            {
                "name": "mae_seasons",
                "pass": True,
                "detail": "Pooled-offense q50 MAE beat the legacy baseline in 4 of 4 seasons.",
            },
            {
                "name": "position_mae",
                "pass": True,
                "detail": "No position was worse by more than 0.05 MAE in more than one season.",
            },
            {
                "name": "bias",
                "pass": False,
                "detail": (
                    "Mean bias is outside ±0.3 for QB 2022, QB 2023, QB 2025, "
                    "WR 2022, K 2024, K 2025, and DEF 2025."
                ),
            },
            {
                "name": "coverage",
                "pass": False,
                "detail": (
                    "TE pooled coverage is 0.846, above the 0.83 cap. "
                    "The other positions are inside the band."
                ),
            },
            {
                "name": "lineup_points",
                "pass": True,
                "detail": (
                    "+1591.3 lineup points versus the legacy baseline across 72 weeks. "
                    "This is not a Yahoo comparison."
                ),
            },
        ],
        "notes": [
            "The M3 candidate was not promoted.",
            "Mean-head bias is still outside ±0.3 in seven position-seasons.",
            "Calibrated 10/90 coverage is inside 80% ± 3 points except at tight end.",
            "q50 remains the primary accuracy metric. The mean head did not win three seasons.",
            (
                "These figures compare the model with the legacy baseline. "
                "They are not a claim against Yahoo. Yahoo capture is a later milestone."
            ),
            (
                "Player rows on an example export are fictional. "
                "This Lab block is the published backtest."
            ),
        ],
    }


_LAB_POSITIONS = ("QB", "RB", "WR", "TE", "K", "DST")


def _lab_label(position: str) -> str:
    """The page calls the defense slot DEF; the report calls it DST."""

    return "DEF" if position == "DST" else position


def _lab_section(report: Mapping[str, object], key: str) -> Mapping[str, object]:
    block = report.get(key)
    if not isinstance(block, Mapping):
        raise DashboardError(f"lab report.{key} must be an object")
    return block


def _lab_checklist_item(promotion: Mapping[str, object], name: str) -> Mapping[str, object]:
    item = promotion.get(name)
    if not isinstance(item, Mapping):
        raise DashboardError(f"lab report.promotion.{name} is missing")
    return item


def lab_block_from_report(report: Mapping[str, object]) -> dict[str, object]:
    """The Lab block for one evaluation report, in the published shape.

    Every figure is read from the report: folds for the per-season rows,
    the calibration block for pooled coverage, the promotion checklist for
    the criteria, and the q50 lineup total for points gained. Nothing is
    recomputed and nothing is invented. A report that cannot support the
    block (no folds, no promotion verdict, no q50 lineup measurement)
    raises instead of publishing a partial block.
    """

    experiment = _lab_section(report, "experiment")
    model_version = experiment.get("model_version")
    if not isinstance(model_version, str) or not model_version.strip():
        raise DashboardError("lab report.experiment.model_version must be a non-empty string")
    primary = "mean" if experiment.get("primary_mae") == "mean" else "q50"
    mae_key = "mae_mean" if primary == "mean" else "mae"
    diff_key = "mae_mean_diff" if primary == "mean" else "mae_diff"

    folds = report.get("folds")
    if not isinstance(folds, list) or not folds:
        raise DashboardError("lab report.folds must be a non-empty list")
    promotion = _lab_section(report, "promotion")
    if not isinstance(promotion.get("promoted"), bool):
        raise DashboardError("lab report.promotion.promoted must be a boolean")
    promoted = bool(promotion["promoted"])

    by_position: dict[str, list[dict[str, object]]] = {}
    mae_diff_by_season: list[dict[str, object]] = []
    for fold in folds:
        if not isinstance(fold, Mapping):
            raise DashboardError("lab report.folds entries must be objects")
        season = _int(fold, "season", "lab report fold")
        blocks = fold.get("positions")
        if not isinstance(blocks, Mapping):
            raise DashboardError("lab report fold.positions must be an object")
        for position in _LAB_POSITIONS:
            block = blocks.get(position)
            if not isinstance(block, Mapping):
                continue
            count = block.get("n")
            if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
                continue
            by_position.setdefault(position, []).append(
                {
                    "season": season,
                    "mae": _finite(block.get(mae_key), f"lab report {position} {season} mae"),
                    "mae_baseline": _finite(
                        block.get("mae_baseline"), f"lab report {position} {season} mae_baseline"
                    ),
                    "bias": _finite(block.get("bias"), f"lab report {position} {season} bias"),
                    "coverage": _finite(
                        block.get("coverage"), f"lab report {position} {season} coverage"
                    ),
                }
            )
        pooled = fold.get("pooled_offense")
        if isinstance(pooled, Mapping):
            mae_diff_by_season.append(
                {
                    "season": season,
                    "mae_diff": _finite(pooled.get(diff_key), f"lab report {season} mae_diff"),
                }
            )
    if not by_position:
        raise DashboardError("lab report.folds contain no scored position rows")
    for rows in by_position.values():
        rows.sort(key=lambda row: int(row["season"]))
    mae_diff_by_season.sort(key=lambda row: int(row["season"]))

    calibration = _lab_section(report, "calibration")
    coverage_map = calibration.get("coverage_by_position")
    if not isinstance(coverage_map, Mapping):
        raise DashboardError("lab report.calibration.coverage_by_position must be an object")
    pooled_coverage: dict[str, object] = {}
    for position in _LAB_POSITIONS:
        entry = coverage_map.get(position)
        if not isinstance(entry, Mapping):
            continue
        coverage = _optional_number(entry.get("coverage"))
        if coverage is not None:
            pooled_coverage[position] = coverage
    if not pooled_coverage:
        raise DashboardError("lab report.calibration.coverage_by_position has no measured coverage")

    lineup = _lab_section(report, "lineup")
    raw_points = lineup.get("points_gained_q50_total")
    if raw_points is None:
        raise DashboardError(
            "lab report.lineup.points_gained_q50_total is missing; "
            "the report has no q50 lineup measurement"
        )
    points = _finite(raw_points, "lab report.lineup.points_gained_q50_total")
    week_rows = lineup.get("points_gained_per_week")
    weeks = len(week_rows) if isinstance(week_rows, list) else 0

    mae_item = _lab_checklist_item(promotion, "mae_seasons")
    mae_pass = mae_item.get("pass") is True
    seasons_passing = mae_item.get("seasons_passing")
    if isinstance(seasons_passing, bool) or not isinstance(seasons_passing, int):
        raise DashboardError("lab report.promotion.mae_seasons.seasons_passing must be an integer")
    detail_rows = mae_item.get("detail")
    measured = len(detail_rows) if isinstance(detail_rows, list) else len(mae_diff_by_season)
    mae_text = (
        f"Pooled-offense {primary} MAE beat the legacy baseline "
        f"in {seasons_passing} of {measured} seasons."
    )

    position_item = _lab_checklist_item(promotion, "position_mae")
    position_pass = position_item.get("pass") is True
    if position_pass:
        position_text = "No position was worse by more than 0.05 MAE in more than one season."
    else:
        worse = position_item.get("failures")
        names = []
        if isinstance(worse, Mapping):
            for position in _LAB_POSITIONS:
                count = worse.get(position)
                if isinstance(count, int) and not isinstance(count, bool) and count > 1:
                    names.append(f"{_lab_label(position)} ({count} seasons)")
        if names:
            position_text = (
                "Positions worse by more than 0.05 MAE in more than one season: "
                + ", ".join(names)
                + "."
            )
        else:
            position_text = (
                "One or more positions were worse by more than 0.05 MAE in more than one season."
            )

    bias_item = _lab_checklist_item(promotion, "bias")
    bias_pass = bias_item.get("pass") is True
    limit = _optional_number(bias_item.get("limit"))
    limit = 0.3 if limit is None else limit
    failure_rows: list[tuple[int, str]] = []
    raw_failures = bias_item.get("failures")
    if isinstance(raw_failures, list):
        for row in raw_failures:
            if not isinstance(row, Mapping):
                continue
            row_season = row.get("season")
            row_position = row.get("position")
            if isinstance(row_season, int) and isinstance(row_position, str):
                failure_rows.append((row_season, row_position))
    failure_rows.sort(
        key=lambda item: (
            item[0],
            _LAB_POSITIONS.index(item[1]) if item[1] in _LAB_POSITIONS else 99,
        )
    )
    if bias_pass:
        bias_text = f"Mean bias is inside ±{limit:g} for every position-season."
    elif failure_rows:
        named = ", ".join(f"{_lab_label(position)} {season}" for season, position in failure_rows)
        bias_text = f"Mean bias is outside ±{limit:g} for {named}."
    else:
        bias_text = f"Mean bias is outside ±{limit:g} in at least one position-season."

    coverage_item = _lab_checklist_item(promotion, "coverage")
    coverage_pass = coverage_item.get("pass") is True
    low, high = 0.77, 0.83
    band = coverage_item.get("band")
    if isinstance(band, (list, tuple)) and len(band) == 2:
        low_num = _optional_number(band[0])
        high_num = _optional_number(band[1])
        if low_num is not None and high_num is not None:
            low, high = low_num, high_num
    raw_failing = coverage_item.get("failures")
    failing_names = [
        position
        for position in _LAB_POSITIONS
        if isinstance(raw_failing, list) and position in raw_failing
    ]
    by_position_cov = coverage_item.get("by_position")

    def _measured(position: str) -> float | None:
        if not isinstance(by_position_cov, Mapping):
            return None
        entry = by_position_cov.get(position)
        if not isinstance(entry, Mapping):
            return None
        return _optional_number(entry.get("coverage"))

    if coverage_pass:
        coverage_text = f"Pooled coverage is inside {low:g}–{high:g} for every position."
    else:
        parts = []
        for position in failing_names:
            value = _measured(position)
            if value is None:
                parts.append(f"{_lab_label(position)} pooled coverage could not be measured.")
            elif value > high:
                parts.append(
                    f"{_lab_label(position)} pooled coverage is {value:.3f}, "
                    f"above the {high:g} cap."
                )
            else:
                parts.append(
                    f"{_lab_label(position)} pooled coverage is {value:.3f}, "
                    f"below the {low:g} floor."
                )
        others = [
            position
            for position in _LAB_POSITIONS
            if position not in failing_names and _measured(position) is not None
        ]
        if others:
            parts.append("The other positions are inside the band.")
        coverage_text = (
            " ".join(parts) if parts else f"Pooled coverage is outside {low:g}–{high:g}."
        )

    lineup_item = _lab_checklist_item(promotion, "lineup_points")
    lineup_pass = lineup_item.get("pass") is True
    span = f" across {weeks} weeks" if weeks else ""
    lineup_text = (
        f"{points:+.1f} lineup points versus the legacy baseline{span}. "
        "Graded on q50, the shipped objective. This is not a Yahoo comparison."
    )

    criteria = [
        {"name": "mae_seasons", "pass": mae_pass, "detail": mae_text},
        {"name": "position_mae", "pass": position_pass, "detail": position_text},
        {"name": "bias", "pass": bias_pass, "detail": bias_text},
        {"name": "coverage", "pass": coverage_pass, "detail": coverage_text},
        {"name": "lineup_points", "pass": lineup_pass, "detail": lineup_text},
    ]

    if bias_pass:
        bias_note = f"Mean-head bias is inside ±{limit:g} in every position-season."
    elif failure_rows:
        plural = "s" if len(failure_rows) != 1 else ""
        bias_note = (
            f"Mean-head bias is outside ±{limit:g} in {len(failure_rows)} position-season{plural}."
        )
    else:
        bias_note = f"Mean-head bias is outside ±{limit:g} in at least one position-season."
    if coverage_pass:
        coverage_note = f"Calibrated 10/90 coverage is inside {low:g}–{high:g} at every position."
    elif failing_names:
        named = ", ".join(_lab_label(position) for position in failing_names)
        coverage_note = f"Calibrated 10/90 coverage is outside {low:g}–{high:g} at {named}."
    else:
        coverage_note = f"Calibrated 10/90 coverage is outside {low:g}–{high:g}."
    notes = [
        (
            "The candidate in this report was promoted."
            if promoted
            else "The candidate in this report was not promoted."
        ),
        bias_note,
        coverage_note,
        (
            "q50 is the primary accuracy metric for this report."
            if primary == "q50"
            else "The mean head is the primary accuracy metric for this report."
        ),
        (
            "These figures compare the model with the legacy baseline. "
            "They are not a claim against Yahoo."
        ),
        (
            f"This Lab block was generated from an evaluation report "
            f"(model {model_version}), not hand-copied constants."
        ),
    ]

    return {
        "source": "evaluation_report",
        "model_version": model_version,
        "primary_mae": primary,
        "promoted": promoted,
        "points_gained_vs_baseline": points,
        "mae_diff_by_season": mae_diff_by_season,
        "pooled_coverage": pooled_coverage,
        "positions": [
            {"position": position, "seasons": by_position[position]}
            for position in _LAB_POSITIONS
            if position in by_position
        ],
        "criteria": criteria,
        "notes": notes,
    }


def _finite(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DashboardError(f"{label} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise DashboardError(f"{label} must be finite")
    return number


def _optional_number(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return number


def _text(row: Mapping[str, object], key: str, label: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value.strip():
        raise DashboardError(f"{label}.{key} must be a non-empty string")
    return value.strip()


def _int(row: Mapping[str, object], key: str, label: str) -> int:
    value = row.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise DashboardError(f"{label}.{key} must be an integer")
    return value


def load_json(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise DashboardError(f"Could not read {path}") from exc
    except json.JSONDecodeError as exc:
        raise DashboardError(f"Could not parse {path}") from exc
    if not isinstance(payload, dict):
        raise DashboardError(f"{path} must be a JSON object")
    return payload


def _prediction_index(payload: Mapping[str, object] | None) -> dict[str, Mapping[str, object]]:
    if payload is None:
        return {}
    rows = payload.get("players")
    if not isinstance(rows, list):
        raise DashboardError("predictions.players must be a list")
    index: dict[str, Mapping[str, object]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise DashboardError("each prediction must be an object")
        player_id = _text(row, "player_id", "prediction")
        index[player_id] = row
    return index


def _apply_prediction(
    player: Mapping[str, object],
    prediction: Mapping[str, object] | None,
) -> dict[str, object]:
    row = dict(player)
    if prediction is None:
        return row
    for key in ("q10", "q50", "q90", "mean", "yahoo", "vegas_implied", "opp_rank"):
        if key in prediction and prediction[key] is not None:
            row[key] = _finite(prediction[key], f"{row.get('player_id')}.{key}")
    practice = prediction.get("practice")
    if isinstance(practice, str) and practice:
        row["practice"] = practice
    return row


def _win_probability(mine: float, theirs: float | None) -> float | None:
    if theirs is None:
        return None
    zed = (mine - theirs) / WIN_PROBABILITY_SD
    return round(0.5 * (1.0 + math.erf(zed / math.sqrt(2.0))), 4)


def _record(wins: int, losses: int) -> str:
    return f"{wins}–{losses}"


def _number_series(value: object, *, limit: int = RECENT_LIMIT) -> list[float] | None:
    """Last numeric values from a roster-file list. Empty or non-lists are missing."""

    if not isinstance(value, list):
        return None
    numbers: list[float] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int | float):
            continue
        number = float(item)
        if not math.isfinite(number):
            continue
        numbers.append(round(number, 4))
    if not numbers:
        return None
    return numbers[-limit:]


def _is_dst(player: Mapping[str, object]) -> bool:
    return str(player.get("position") or "").upper() in {"DST", "DEF"}


def _series_for(
    player: Mapping[str, object],
    history: Mapping[str, RecentSeries] | None,
) -> tuple[list[float], list[float] | None]:
    """Points and snap share for one player.

    ``history is None`` copies the roster file (the example site). A dict, even
    an empty one, is a point-in-time lookup: ids the store does not know get an
    empty points list and a null snap share. DST snap share is always null.
    """

    dst = _is_dst(player)
    if history is None:
        points = _number_series(player.get("recent_points")) or []
        shares = None if dst else _number_series(player.get("snap_share"))
        return points, shares
    found = history.get(str(player.get("player_id") or ""))
    if found is None:
        return [], None
    points = [float(value) for value in found.recent_points]
    if dst or found.snap_share is None:
        return points, None
    return points, [float(value) for value in found.snap_share]


def _player_out(
    player: Mapping[str, object],
    history: Mapping[str, RecentSeries] | None,
) -> dict[str, object]:
    q50 = _optional_number(player.get("q50"))
    yahoo = _optional_number(player.get("yahoo"))
    delta = None if q50 is None or yahoo is None else q50 - yahoo
    recent_points, snap_share = _series_for(player, history)
    kept = {
        "player_id": str(player.get("player_id") or ""),
        "name": str(player.get("name") or ""),
        "position": str(player.get("position") or ""),
        "nfl_team": player.get("nfl_team"),
        "opponent": player.get("opponent"),
        "home": player.get("home"),
        "kickoff": player.get("kickoff"),
        "injury": player.get("injury") or None,
        "force_zero": player.get("force_zero") is True,
        "bye": player.get("bye") is True,
        "q10": _optional_number(player.get("q10")),
        "q50": q50,
        "q90": _optional_number(player.get("q90")),
        "mean": _optional_number(player.get("mean")),
        "yahoo": yahoo,
        "yahoo_delta": delta,
        "slot": player.get("slot"),
        "verdict": player.get("verdict"),
        "notes": list(player.get("notes") or []) if isinstance(player.get("notes"), list) else [],
        "sparkline": player.get("sparkline") if isinstance(player.get("sparkline"), list) else [],
        "recent_points": recent_points,
        "opp_rank": player.get("opp_rank"),
        "vegas_implied": _optional_number(player.get("vegas_implied")),
        "snap_share": snap_share,
        "practice": player.get("practice"),
    }
    return kept


def _lineup_block(config_lineup: Lineup) -> dict[str, object]:
    return {
        "qb": config_lineup.qb,
        "rb": config_lineup.rb,
        "wr": config_lineup.wr,
        "te": config_lineup.te,
        "flex": config_lineup.flex,
        "k": config_lineup.k,
        "dst": config_lineup.dst,
        "bench": config_lineup.bench,
        "ir": config_lineup.ir,
        "flex_positions": list(FLEX_POSITIONS),
    }


def build_dashboard(
    config: AppConfig,
    roster_doc: Mapping[str, object],
    predictions: Mapping[str, object] | None = None,
    lab: Mapping[str, object] | None = None,
    *,
    history: Mapping[str, RecentSeries] | None = None,
) -> dict[str, object]:
    """Assemble one week of dashboard JSON.

    ``history`` is the point-in-time series from ``recent_series``. Omit it to
    copy ``recent_points`` and ``snap_share`` from the roster file.
    """

    teams_raw = roster_doc.get("teams")
    if not isinstance(teams_raw, list) or not teams_raw:
        raise DashboardError("rosters.teams must be a non-empty list")
    prediction_rows = _prediction_index(predictions)
    prepared: list[dict[str, object]] = []
    names: dict[int, str] = {}
    for team in teams_raw:
        if not isinstance(team, dict):
            raise DashboardError("each team must be an object")
        team_id = _int(team, "team_id", "team")
        name = _text(team, "name", f"team {team_id}")
        players_raw = team.get("players")
        if not isinstance(players_raw, list):
            raise DashboardError(f"team {team_id} players must be a list")
        players: list[dict[str, object]] = []
        for player in players_raw:
            if not isinstance(player, dict):
                raise DashboardError(f"team {team_id} has a player that is not an object")
            player_id = _text(player, "player_id", "player")
            merged = _apply_prediction(player, prediction_rows.get(player_id))
            players.append(merged)
        result = recommend(players, config.lineup)
        names[team_id] = name
        prepared.append(
            {
                "team_id": team_id,
                "name": name,
                "wins": _int(team, "wins", f"team {team_id}"),
                "losses": _int(team, "losses", f"team {team_id}"),
                "opponent_id": _int(team, "opponent_id", f"team {team_id}"),
                "result": result,
            }
        )

    totals = {
        int(team["team_id"]): float(team["result"]["projected_total"])  # type: ignore[index]
        for team in prepared
    }
    exported_teams: list[dict[str, object]] = []
    for team in prepared:
        team_id = int(team["team_id"])
        opponent_id = int(team["opponent_id"])
        total = totals[team_id]
        opponent_total = totals.get(opponent_id)
        result = team["result"]
        assert isinstance(result, dict)
        exported_teams.append(
            {
                "team_id": team_id,
                "name": team["name"],
                "wins": team["wins"],
                "losses": team["losses"],
                "record": _record(int(team["wins"]), int(team["losses"])),
                "opponent_id": opponent_id,
                "opponent_name": names.get(opponent_id),
                "projected_total": total,
                "opponent_projected_total": opponent_total,
                "win_probability": _win_probability(total, opponent_total),
                "starters": [_player_out(player, history) for player in result["starters"]],  # type: ignore[union-attr]
                "bench": [_player_out(player, history) for player in result["bench"]],  # type: ignore[union-attr]
                "inactive": [_player_out(player, history) for player in result["inactive"]],  # type: ignore[union-attr]
                "calls": result["calls"],
            }
        )

    standings = sorted(
        exported_teams,
        key=lambda row: (-float(row["projected_total"]), int(row["team_id"])),
    )
    standing_rows = []
    max_total = max(float(row["projected_total"]) for row in standings)
    for rank, row in enumerate(standings, start=1):
        standing_rows.append(
            {
                "rank": rank,
                "team_id": row["team_id"],
                "name": row["name"],
                "record": row["record"],
                "wins": row["wins"],
                "opponent_id": row["opponent_id"],
                "opponent_name": row["opponent_name"],
                "projected_total": row["projected_total"],
                "win_probability": row["win_probability"],
                "share_of_leader": (
                    round(float(row["projected_total"]) / max_total, 4) if max_total else 0.0
                ),
            }
        )

    waivers = _waivers(roster_doc.get("waivers"), prepared, history)
    default_team_id = config.league.my_team_id
    if default_team_id not in names:
        raise DashboardError(f"league.my_team_id {default_team_id} is not in the roster file")
    example = roster_doc.get("example") is True
    return {
        "version": DASHBOARD_VERSION,
        "example": example,
        "disclaimer": (
            "Fictional players and NFL-style teams. Not a real roster."
            if example
            else "Roster file supplied by the exporter. Not from the Yahoo API."
        ),
        "league": {
            "name": config.league.name,
            "yahoo_id": config.league.yahoo_id,
            "teams": config.league.teams,
            "format": config.league.format,
            "ppr": config.league.ppr,
            "scoring_version": config.league.scoring_version,
        },
        "lineup": _lineup_block(config.lineup),
        "week": {
            "season": _int(roster_doc, "season", "rosters"),
            "week": _int(roster_doc, "week", "rosters"),
            "kickoff_label": roster_doc.get("kickoff_label"),
            "countdown_label": roster_doc.get("countdown_label"),
            "countdown_is_live": False,
        },
        "default_team_id": default_team_id,
        "objective": OBJECTIVE,
        "close_call_margin": CLOSE_CALL_MARGIN,
        "win_probability_sd": WIN_PROBABILITY_SD,
        "teams": [
            {key: value for key, value in team.items() if key != "calls"} | {"calls": team["calls"]}
            for team in exported_teams
        ],
        "waivers": waivers,
        "standings": standing_rows,
        "lab": dict(lab) if lab is not None else published_m3_lab(),
    }


def _waivers(
    raw: object,
    teams: Sequence[Mapping[str, object]],
    history: Mapping[str, RecentSeries] | None,
) -> list[dict[str, object]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise DashboardError("waivers must be a list")
    catalog: dict[str, Mapping[str, object]] = {}
    for team in teams:
        result = team["result"]
        assert isinstance(result, dict)
        for bucket in ("starters", "bench", "inactive"):
            rows = result.get(bucket)
            if not isinstance(rows, list):
                continue
            for player in rows:
                if isinstance(player, dict):
                    catalog[str(player.get("player_id"))] = player
    exported: list[dict[str, object]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise DashboardError("each waiver must be an object")
        drop_id = item.get("drop_player_id")
        drop = catalog.get(str(drop_id)) if isinstance(drop_id, str) else None
        exported.append(
            {
                "player_id": _text(item, "player_id", "waiver"),
                "name": _text(item, "name", "waiver"),
                "position": _text(item, "position", "waiver"),
                "nfl_team": item.get("nfl_team"),
                "q50_next": item.get("q50_next") if isinstance(item.get("q50_next"), list) else [],
                "ros": _optional_number(item.get("ros")),
                "snap_share": _series_for(item, history)[1],
                "note": item.get("note") or "",
                "drop_player_id": drop_id,
                "drop_name": None if drop is None else drop.get("name"),
                "drop_position": None if drop is None else drop.get("position"),
            }
        )
    return exported


def _listed_players(roster_doc: Mapping[str, object]) -> tuple[list[str], dict[str, str]]:
    ids: list[str] = []
    positions: dict[str, str] = {}

    def take(rows: object) -> None:
        if not isinstance(rows, list):
            return
        for row in rows:
            if not isinstance(row, dict):
                continue
            player_id = row.get("player_id")
            if not isinstance(player_id, str) or not player_id:
                continue
            ids.append(player_id)
            position = row.get("position")
            if isinstance(position, str) and position:
                positions[player_id] = position

    teams = roster_doc.get("teams")
    if isinstance(teams, list):
        for team in teams:
            if isinstance(team, dict):
                take(team.get("players"))
    take(roster_doc.get("waivers"))
    return ids, positions


def export_dashboard(
    *,
    config: AppConfig,
    rosters_path: Path,
    output: Path,
    predictions_path: Path | None = None,
    lab: Mapping[str, object] | None = None,
    data_dir: Path | None = None,
) -> dict[str, object]:
    """Read the roster file and write one dashboard JSON document.

    With ``data_dir``, ``recent_points`` and ``snap_share`` come from
    ``recent_series`` at the Thursday simulated capture for the roster week.
    Without it, those fields are copied from the roster file.
    """

    roster_doc = load_json(rosters_path)
    predictions = load_json(predictions_path) if predictions_path is not None else None
    history = None
    if data_dir is not None:
        season = _int(roster_doc, "season", "rosters")
        week = _int(roster_doc, "week", "rosters")
        player_ids, positions = _listed_players(roster_doc)
        history = recent_series(
            season,
            week,
            player_ids,
            data_dir=data_dir,
            positions=positions,
        )
    payload = build_dashboard(config, roster_doc, predictions, lab, history=history)
    text = json.dumps(payload, indent=2, allow_nan=False) + "\n"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text, encoding="utf-8")
    return payload
