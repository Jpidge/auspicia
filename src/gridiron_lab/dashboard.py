"""Build the dashboard JSON from a roster file and optional predictions.

League-wide Yahoo rosters are not available until M5. This exporter reads a
roster file the caller supplies. The repository ships an example file of
fictional players and managers. Real league files stay out of this repo.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from pathlib import Path

from gridiron_lab.config import AppConfig, Lineup
from gridiron_lab.lineup import CLOSE_CALL_MARGIN, FLEX_POSITIONS, recommend

DASHBOARD_VERSION = "1"
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
                    "WR 2022, K 2024, K 2025, and DST 2025."
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
    for key in ("q10", "q50", "q90", "mean", "yahoo"):
        if key in prediction and prediction[key] is not None:
            row[key] = _finite(prediction[key], f"{row.get('player_id')}.{key}")
    return row


def _win_probability(mine: float, theirs: float | None) -> float | None:
    if theirs is None:
        return None
    zed = (mine - theirs) / WIN_PROBABILITY_SD
    return round(0.5 * (1.0 + math.erf(zed / math.sqrt(2.0))), 4)


def _record(wins: int, losses: int) -> str:
    return f"{wins}–{losses}"


def _player_out(player: Mapping[str, object]) -> dict[str, object]:
    q50 = _optional_number(player.get("q50"))
    yahoo = _optional_number(player.get("yahoo"))
    delta = None if q50 is None or yahoo is None else q50 - yahoo
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
        "opp_rank": player.get("opp_rank"),
        "vegas_implied": _optional_number(player.get("vegas_implied")),
        "snap_share": (
            player.get("snap_share") if isinstance(player.get("snap_share"), list) else []
        ),
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
) -> dict[str, object]:
    """Assemble one week of dashboard JSON. Manager names come from the roster file."""

    teams_raw = roster_doc.get("teams")
    if not isinstance(teams_raw, list) or not teams_raw:
        raise DashboardError("rosters.teams must be a non-empty list")
    prediction_rows = _prediction_index(predictions)
    anonymize = config.dashboard.anonymize_managers
    prepared: list[dict[str, object]] = []
    names: dict[int, str] = {}
    managers: dict[int, str] = {}
    for team in teams_raw:
        if not isinstance(team, dict):
            raise DashboardError("each team must be an object")
        team_id = _int(team, "team_id", "team")
        name = _text(team, "name", f"team {team_id}")
        manager = _text(team, "manager", f"team {team_id}")
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
        managers[team_id] = manager
        prepared.append(
            {
                "team_id": team_id,
                "name": name,
                "manager": manager,
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
        display = f"Manager {team_id}" if anonymize else str(team["manager"])
        exported_teams.append(
            {
                "team_id": team_id,
                "name": team["name"],
                "manager_display": display,
                "wins": team["wins"],
                "losses": team["losses"],
                "record": _record(int(team["wins"]), int(team["losses"])),
                "opponent_id": opponent_id,
                "opponent_name": names.get(opponent_id),
                "projected_total": total,
                "opponent_projected_total": opponent_total,
                "win_probability": _win_probability(total, opponent_total),
                "starters": [_player_out(player) for player in result["starters"]],  # type: ignore[union-attr]
                "bench": [_player_out(player) for player in result["bench"]],  # type: ignore[union-attr]
                "inactive": [_player_out(player) for player in result["inactive"]],  # type: ignore[union-attr]
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
                "manager_display": row["manager_display"],
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

    waivers = _waivers(roster_doc.get("waivers"), prepared, anonymize, managers)
    default_team_id = config.league.my_team_id
    if default_team_id not in names:
        raise DashboardError(f"league.my_team_id {default_team_id} is not in the roster file")
    example = roster_doc.get("example") is True
    return {
        "version": DASHBOARD_VERSION,
        "example": example,
        "disclaimer": (
            "Fictional players, managers, and NFL-style teams. Not a real roster."
            if example
            else "Roster file supplied by the exporter. Not from the Yahoo API."
        ),
        "anonymize_managers": anonymize,
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
    anonymize: bool,
    managers: Mapping[int, str],
) -> list[dict[str, object]]:
    del anonymize, managers
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
                "snap_share": (
                    item.get("snap_share") if isinstance(item.get("snap_share"), list) else []
                ),
                "note": item.get("note") or "",
                "drop_player_id": drop_id,
                "drop_name": None if drop is None else drop.get("name"),
                "drop_position": None if drop is None else drop.get("position"),
            }
        )
    return exported


def export_dashboard(
    *,
    config: AppConfig,
    rosters_path: Path,
    output: Path,
    predictions_path: Path | None = None,
    lab: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Read the roster file and write one dashboard JSON document."""

    roster_doc = load_json(rosters_path)
    predictions = load_json(predictions_path) if predictions_path is not None else None
    payload = build_dashboard(config, roster_doc, predictions, lab)
    text = json.dumps(payload, indent=2, allow_nan=False) + "\n"
    if config.dashboard.anonymize_managers:
        for team in roster_doc.get("teams", []):
            if isinstance(team, dict):
                manager = team.get("manager")
                if isinstance(manager, str) and manager and manager in text:
                    raise DashboardError("anonymized export still contains a manager name")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text, encoding="utf-8")
    return payload
