"""Fit the predictor for one week and write dashboard predictions.

The serving path shares the evaluation's machinery end to end. Feature rows
come from ``build_features`` under the ``simulated_capture`` policy (through
``evaluate.collect_weeks``), training rows are every earlier row that has an
actual and is not force-zero — the same filter the backtest applies — and the
predictor is ``GridironPredictor``. What this module adds is only the last
mile: resolving roster-file players to store identities and shaping the
output for ``export-dashboard --predictions``.

Roster players are matched to feature rows by normalized name at the same
position, with the NFL team as tiebreak — the name bridge the captures have
always needed. A player who cannot be resolved uniquely is reported in the
output's ``unmatched`` list, never guessed. Bye and force-zero roster
players are skipped: the exporter owns their display (null projections on a
bye, zeroes when out), and a prediction must not override either.

Known gap, disclosed in the output metadata: the backtest's interval
calibration (the conformal adjustment of q10/q90) is not applied here yet,
so shipped intervals are the model's raw quantiles. The q50 and mean heads —
the headline number and the lineup objective — are unaffected.
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

from gridiron_lab.dashboard import load_json
from gridiron_lab.data.store import open_db
from gridiron_lab.evaluate import collect_weeks
from gridiron_lab.logging import get_logger
from gridiron_lab.models import MODEL_VERSION, GridironPredictor, Prediction

logger = get_logger("predict")

AS_OF_POLICY = "simulated_capture"

# Yahoo and older nflverse team codes that differ from the store's codes.
TEAM_ALIASES = {
    "GNB": "GB",
    "JAC": "JAX",
    "KAN": "KC",
    "LA": "LAR",
    "NOR": "NO",
    "NWE": "NE",
    "OAK": "LV",
    "SD": "LAC",
    "SFO": "SF",
    "STL": "LAR",
    "TAM": "TB",
    "WSH": "WAS",
}

_SUFFIX_TOKENS = {"jr", "sr", "ii", "iii", "iv", "v"}


class PredictError(Exception):
    """The week's predictions cannot be built from what is on disk."""


def normalize_name(name: str) -> str:
    """Case/punctuation/suffix-insensitive name key for the roster bridge."""

    decomposed = unicodedata.normalize("NFKD", name)
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    lowered = plain.lower().replace("'", "").replace("’", "")
    spaced = re.sub(r"[^a-z0-9]+", " ", lowered)
    tokens = [token for token in spaced.split() if token not in _SUFFIX_TOKENS]
    return " ".join(tokens)


def normalize_team(code: str) -> str:
    upper = code.strip().upper()
    return TEAM_ALIASES.get(upper, upper)


def load_names(data_dir: Path, player_ids: set[str]) -> dict[str, str]:
    """Display names for store identities. Names are identity, not features."""

    names: dict[str, str] = {}
    rows = open_db(data_dir).as_of("players", datetime(2030, 1, 1, tzinfo=UTC))
    for row in rows:
        player_id = row.get("player_id")
        display = row.get("display_name")
        if isinstance(player_id, str) and player_id in player_ids and isinstance(display, str):
            names[player_id] = display
    return names


def _row_key(row: Mapping[str, object]) -> tuple[int, int]:
    return int(row["season"]), int(row["week"])  # type: ignore[arg-type]


def _trained_through(train: Sequence[Mapping[str, object]]) -> str:
    if not train:
        return "none"
    season, week = max(_row_key(row) for row in train)
    return f"{season:04d}-W{week:02d}"


_RANK_POSITIONS = frozenset({"QB", "RB", "WR", "TE", "K"})

_PRACTICE_CODES = (
    ("Full Participation", "FP"),
    ("Limited Participation", "LP"),
    ("Did Not Participate", "DNP"),
)


def _practice_code(value: object) -> str | None:
    """Shorten an injury report's practice text to FP / LP / DNP."""

    if not isinstance(value, str):
        return None
    for prefix, code in _PRACTICE_CODES:
        if value.startswith(prefix):
            return code
    return None


def _opponent_ranks(
    frames: Mapping[tuple[int, int], list[dict[str, object]]],
    season: int,
    week: int,
) -> dict[tuple[str, str], int]:
    """Rank defenses by fantasy points allowed per game to each position.

    Season-to-date before the target week, in the frames' own scored
    ``actual`` points: rank 1 allows the fewest points (toughest matchup),
    and tied defenses share a rank. Returns ``{(defense, position): rank}``
    for the positions a rank is meaningful for — defenses themselves are
    not ranked against an offense here.
    """

    per_game: dict[tuple[str, str, object], float] = {}
    for (row_season, row_week), rows in frames.items():
        if row_season != season or row_week >= week:
            continue
        for row in rows:
            position = row.get("position")
            opponent = row.get("opponent")
            actual = row.get("actual")
            if position not in _RANK_POSITIONS or not isinstance(opponent, str):
                continue
            if not isinstance(actual, (int, float)) or isinstance(actual, bool):
                continue
            if row.get("team") == opponent:
                continue
            game_key = row.get("game_id") or (row_season, row_week)
            key = (opponent, str(position), game_key)
            per_game[key] = per_game.get(key, 0.0) + float(actual)
    totals: dict[tuple[str, str], list[float]] = {}
    for (opponent, position, _game), points in per_game.items():
        totals.setdefault((opponent, position), []).append(points)
    allowed = {key: sum(values) / len(values) for key, values in totals.items()}
    by_position: dict[str, list[tuple[str, float]]] = {}
    for (opponent, position), value in allowed.items():
        by_position.setdefault(position, []).append((opponent, value))
    ranks: dict[tuple[str, str], int] = {}
    for position, entries in by_position.items():
        for opponent, value in entries:
            ranks[(opponent, position)] = 1 + sum(1 for _other, other in entries if other < value)
    return ranks


def _is_trainable(row: Mapping[str, object]) -> bool:
    if row.get("force_zero"):
        return False
    actual = row.get("actual")
    return isinstance(actual, (int, float)) and not isinstance(actual, bool)


def _skippable(player: Mapping[str, object]) -> bool:
    """Bye and force-zero roster players keep the exporter's own display."""

    return bool(player.get("bye") or player.get("force_zero"))


class _FrameIndex:
    """Target-week feature rows by identity key and by normalized name."""

    def __init__(self, rows: Sequence[Mapping[str, object]], names: Mapping[str, str]) -> None:
        self.by_defense: dict[str, Mapping[str, object]] = {}
        self.by_name: dict[tuple[str, str], list[Mapping[str, object]]] = {}
        for row in rows:
            player_id = str(row.get("player_id"))
            if player_id.startswith("DEF:"):
                # The store's defense ids carry its own team code (LA, not
                # LAR); normalize both sides of the bridge the same way.
                self.by_defense[f"DEF:{normalize_team(player_id[4:])}"] = row
                continue
            display = names.get(player_id)
            if display is None:
                continue
            key = (normalize_name(display), str(row.get("position")))
            self.by_name.setdefault(key, []).append(row)

    def resolve(self, player: Mapping[str, object]) -> Mapping[str, object] | None:
        position = str(player.get("position"))
        team = normalize_team(str(player.get("nfl_team") or ""))
        if position == "DEF":
            return self.by_defense.get(f"DEF:{team}")
        candidates = self.by_name.get((normalize_name(str(player.get("name"))), position), [])
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            same_team = [row for row in candidates if normalize_team(str(row.get("team"))) == team]
            if len(same_team) == 1:
                return same_team[0]
        return None


def build_predictions(
    frames: Mapping[tuple[int, int], list[dict[str, object]]],
    season: int,
    week: int,
    roster_doc: Mapping[str, object],
    names: Mapping[str, str],
    *,
    seed: int = 0,
    make_predictor: Callable[[], object] | None = None,
) -> dict[str, object]:
    """Fit on every earlier week and predict the target week's roster."""

    target = frames.get((season, week))
    if not target:
        raise PredictError(f"no feature rows for {season} week {week}")
    train: list[Mapping[str, object]] = []
    for key, frame in frames.items():
        if key >= (season, week):
            continue
        train.extend(row for row in frame if _is_trainable(row))
    if not train:
        raise PredictError(f"no training rows before {season} week {week}")
    through = _trained_through(train)

    factory = make_predictor or (lambda: GridironPredictor(seed))
    predictor = factory()
    fit = getattr(predictor, "fit", None)
    predict = getattr(predictor, "predict", None)
    if not callable(fit) or not callable(predict):
        raise PredictError("predictor needs fit() and predict()")
    fit(train, trained_through=through)
    predictions: list[Prediction] = list(predict(target))
    if len(predictions) != len(target):
        raise PredictError("predict() must return one row per input")
    by_row = {id(row): pred for row, pred in zip(target, predictions, strict=True)}

    index = _FrameIndex(target, names)
    ranks = _opponent_ranks(frames, season, week)
    players: list[dict[str, object]] = []
    unmatched: list[dict[str, object]] = []
    teams = roster_doc.get("teams")
    if not isinstance(teams, list):
        raise PredictError("roster file must carry a teams list")
    for team in teams:
        if not isinstance(team, dict):
            continue
        roster = team.get("players")
        if not isinstance(roster, list):
            continue
        for player in roster:
            if not isinstance(player, dict) or _skippable(player):
                continue
            row = index.resolve(player)
            if row is None:
                unmatched.append(
                    {
                        "player_id": player.get("player_id"),
                        "name": player.get("name"),
                        "position": player.get("position"),
                        "nfl_team": player.get("nfl_team"),
                    }
                )
                continue
            pred = by_row[id(row)]
            entry: dict[str, object] = {
                "player_id": player.get("player_id"),
                "gsis_id": row.get("player_id"),
                "q10": round(pred.q10, 2),
                "q50": round(pred.q50, 2),
                "q90": round(pred.q90, 2),
                "mean": round(pred.mean, 2),
            }
            yahoo = player.get("yahoo")
            if isinstance(yahoo, (int, float)) and not isinstance(yahoo, bool):
                entry["yahoo"] = yahoo
            implied = row.get("implied_team_total")
            if (
                isinstance(implied, (int, float))
                and not isinstance(implied, bool)
                and math.isfinite(float(implied))
            ):
                entry["vegas_implied"] = round(float(implied), 2)
            position = row.get("position")
            opponent = row.get("opponent")
            if isinstance(position, str) and isinstance(opponent, str):
                rank = ranks.get((opponent, position))
                if rank is not None:
                    entry["opp_rank"] = rank
            practice = _practice_code(row.get("practice_status"))
            if practice is not None:
                entry["practice"] = practice
            if pred.fallback is not None:
                entry["fallback"] = pred.fallback
            players.append(entry)
    for miss in unmatched:
        logger.warning(
            "No unique store match for %s (%s, %s)",
            miss["name"],
            miss["position"],
            miss["nfl_team"],
        )
    logger.info(
        "Predictions %s week %s · %s players · %s unmatched · trained through %s",
        season,
        week,
        len(players),
        len(unmatched),
        through,
    )
    return {
        "season": season,
        "week": week,
        "generated_by": "gridiron_lab.predict",
        "model_version": MODEL_VERSION,
        "trained_through": through,
        "as_of_policy": AS_OF_POLICY,
        "conformal_adjusted": False,
        "scope": "rostered players only; waivers excluded",
        "players": players,
        "unmatched": unmatched,
    }


def run_predict(
    *,
    data_dir: Path,
    season: int,
    week: int,
    history_seasons: Sequence[int],
    rosters_path: Path,
    output: Path,
    seed: int = 0,
    collect: Callable[..., dict[tuple[int, int], list[dict[str, object]]]] = collect_weeks,
    make_predictor: Callable[[], object] | None = None,
    names: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Collect weeks, fit, predict, and write the predictions JSON."""

    roster_doc = load_json(rosters_path)
    seasons = sorted({*history_seasons, season})
    frames = collect(data_dir, seasons)
    if names is None:
        target = frames.get((season, week), [])
        names = load_names(data_dir, {str(row.get("player_id")) for row in target})
    payload = build_predictions(
        frames,
        season,
        week,
        roster_doc,
        names,
        seed=seed,
        make_predictor=make_predictor,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, allow_nan=False)
    output.write_text(text + "\n", encoding="utf-8")
    logger.info("Wrote predictions %s", output)
    return payload
