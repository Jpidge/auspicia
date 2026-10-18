"""Play-by-play events the scoring engine cannot see on the weekly box score.

Counts are attached to a player or to a team (``DEF:<TEAM>``). A missing
column leaves that event unavailable for the whole file. It is not stored
as an observed zero. Raw play rows are not kept; ``pbp_events`` is the
canonical table.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from gridiron_lab.data.clock import STATS_LAG, GameClock, shift
from gridiron_lab.data.schema import SOURCE_NAME
from gridiron_lab.data.values import as_flag, as_float, as_text

PLAYER_FIELDS = (
    "passing_tds_40_plus",
    "rushing_tds_40_plus",
    "receiving_tds_40_plus",
    "return_tds",
    "offensive_fumble_return_tds",
    "field_goal_yards_made",
    "extra_points_made",
)
TEAM_FIELDS = (
    "sacks",
    "interceptions",
    "fumble_recoveries",
    "defense_tds",
    "safeties",
    "blocked_kicks",
    "return_yards",
    "return_tds",
    "extra_point_returns",
    "points_allowed",
)
_RETURNER_COLUMNS = (
    "returner_player_id",
    "kickoff_returner_player_id",
    "punt_returner_player_id",
)
_KICK_PLAYS = frozenset({"kickoff", "punt"})


@dataclass
class PbpExtract:
    player_available: set[str]
    team_available: set[str]
    player_counts: dict[tuple[str, str], dict[str, float]]
    team_counts: dict[tuple[str, str], dict[str, float]]
    opponent_score: dict[tuple[str, str], float | None]
    notes: dict[str, int] = field(default_factory=dict)
    rows: list[dict[str, object]] = field(default_factory=list)


def adjusted_points_allowed(opponent_score: int | None, excluded_tds: int) -> int | None:
    """Opponent score minus 6 for each opponent return or defensive touchdown.

    Safeties stay in the total. A negative result is treated as missing
    rather than passed to the scorer.
    """

    if opponent_score is None:
        return None
    adjusted = opponent_score - 6 * excluded_tds
    if adjusted < 0:
        return None
    return adjusted


def column_set(rows: list[dict[str, object]]) -> set[str]:
    if not rows:
        return set()
    return set(rows[0])


def event_availability(columns: set[str]) -> tuple[set[str], set[str], dict[str, int]]:
    notes: dict[str, int] = {}
    player: set[str] = set()
    team: set[str] = set()
    long_cols = {
        "touchdown",
        "yards_gained",
        "pass_touchdown",
        "rush_touchdown",
        "passer_player_id",
        "rusher_player_id",
        "receiver_player_id",
    }
    if long_cols <= columns:
        player.update({"passing_tds_40_plus", "rushing_tds_40_plus", "receiving_tds_40_plus"})
    else:
        notes["pbp_missing_long_touchdown_columns"] = 1
    if {"play_type", "return_touchdown"} <= columns and (set(_RETURNER_COLUMNS) & columns):
        player.add("return_tds")
    else:
        notes["pbp_missing_return_touchdown_columns"] = 1
    fumble_cols = {
        "touchdown",
        "td_team",
        "posteam",
        "pass_touchdown",
        "rush_touchdown",
        "play_type",
        "fumble_recovery_1_team",
        "fumble_recovery_1_player_id",
    }
    if fumble_cols <= columns:
        player.add("offensive_fumble_return_tds")
    else:
        notes["pbp_missing_offensive_fumble_return_columns"] = 1
    if {"kicker_player_id", "field_goal_result", "kick_distance"} <= columns:
        player.add("field_goal_yards_made")
    else:
        notes["pbp_missing_field_goal_columns"] = 1
    if {"kicker_player_id", "extra_point_result"} <= columns:
        player.add("extra_points_made")
    else:
        notes["pbp_missing_extra_point_columns"] = 1
    if {"sack", "defteam"} <= columns:
        team.add("sacks")
    else:
        notes["pbp_missing_sack_columns"] = 1
    if {"interception", "defteam"} <= columns:
        team.add("interceptions")
    else:
        notes["pbp_missing_interception_columns"] = 1
    if {"defteam", "fumble_recovery_1_team"} <= columns:
        team.add("fumble_recoveries")
    else:
        notes["pbp_missing_fumble_recovery_columns"] = 1
    if {"touchdown", "td_team", "defteam", "posteam", "play_type"} <= columns:
        team.add("defense_tds")
    else:
        notes["pbp_missing_defense_td_columns"] = 1
    if {"safety", "defteam"} <= columns:
        team.add("safeties")
    else:
        notes["pbp_missing_safety_columns"] = 1
    blocked_cols = {"field_goal_result", "punt_blocked", "extra_point_result"}
    if "defteam" in columns and (blocked_cols & columns):
        team.add("blocked_kicks")
    else:
        notes["pbp_missing_blocked_kick_columns"] = 1
    if {"play_type", "return_yards", "return_team"} <= columns:
        team.add("return_yards")
    else:
        notes["pbp_missing_return_yard_columns"] = 1
    if {"play_type", "return_touchdown", "return_team"} <= columns:
        team.add("return_tds")
    else:
        notes["pbp_missing_dst_return_td_columns"] = 1
    if {"defensive_extra_point_conv", "defteam"} <= columns:
        team.add("extra_point_returns")
    else:
        notes["pbp_missing_extra_point_return_columns"] = 1
    if {"touchdown", "td_team", "play_type", "posteam", "defteam"} <= columns:
        team.add("points_allowed")
    else:
        notes["pbp_missing_points_allowed_columns"] = 1
    return player, team, notes


def extract_events(plays: list[dict[str, object]], clocks: list[GameClock]) -> PbpExtract:
    """Aggregate one season of plays against the schedule clocks."""

    by_id = {clock.game_id: clock for clock in clocks}
    if not plays:
        return PbpExtract(set(), set(), {}, {}, {}, {"pbp_empty": 1}, [])
    columns = column_set(plays)
    player_available, team_available, notes = event_availability(columns)
    player_counts: dict[tuple[str, str], dict[str, float]] = defaultdict(dict)
    player_teams: dict[tuple[str, str], str] = {}
    team_counts: dict[tuple[str, str], dict[str, float]] = defaultdict(dict)
    excluded: dict[tuple[str, str], int] = defaultdict(int)
    seen_games: set[str] = set()

    for play in plays:
        game_id = as_text(play.get("game_id"))
        if game_id is None or game_id not in by_id:
            notes["pbp_play_without_schedule"] = notes.get("pbp_play_without_schedule", 0) + 1
            continue
        seen_games.add(game_id)
        _player_play(play, game_id, player_available, player_counts, player_teams)
        _team_play(play, game_id, team_available, team_counts, excluded)

    opponent_score: dict[tuple[str, str], float | None] = {}
    if "points_allowed" in team_available:
        for clock in clocks:
            if clock.game_id not in seen_games and (clock.home_score is not None):
                notes["pbp_game_without_plays"] = notes.get("pbp_game_without_plays", 0) + 1
            for team in (clock.home_team, clock.away_team):
                opponent = clock.opponent(team)
                if opponent is None:
                    continue
                score = clock.score(opponent)
                key = (team, clock.game_id)
                opponent_score[key] = None if score is None else float(score)
                adjusted = adjusted_points_allowed(
                    score,
                    excluded.get((opponent, clock.game_id), 0),
                )
                if score is not None and adjusted is None:
                    notes["points_allowed_negative"] = notes.get("points_allowed_negative", 0) + 1
                bucket = team_counts[key]
                if adjusted is None:
                    bucket["points_allowed"] = None  # type: ignore[assignment]
                else:
                    bucket["points_allowed"] = float(adjusted)

    rows = _rows(
        clocks,
        player_available,
        team_available,
        player_counts,
        player_teams,
        team_counts,
        opponent_score,
    )
    return PbpExtract(
        player_available,
        team_available,
        dict(player_counts),
        dict(team_counts),
        opponent_score,
        notes,
        rows,
    )


def _bump(bucket: dict[tuple[str, str], dict[str, float]], key: tuple[str, str], field: str, amount: float) -> None:
    current = bucket[key]
    current[field] = current.get(field, 0.0) + amount


def _remember(teams: dict[tuple[str, str], str], player_id: str, game_id: str, team: str | None) -> None:
    if team and (player_id, game_id) not in teams:
        teams[(player_id, game_id)] = team


def _player_play(
    play: dict[str, object],
    game_id: str,
    available: set[str],
    counts: dict[tuple[str, str], dict[str, float]],
    teams: dict[tuple[str, str], str],
) -> None:
    play_type = (as_text(play.get("play_type")) or "").lower()
    posteam = as_text(play.get("posteam"))
    if {"passing_tds_40_plus", "rushing_tds_40_plus", "receiving_tds_40_plus"} & available:
        yards = as_float(play.get("yards_gained"))
        if as_flag(play.get("touchdown")) and yards is not None and yards >= 40:
            if as_flag(play.get("pass_touchdown")):
                passer = as_text(play.get("passer_player_id"))
                receiver = as_text(play.get("receiver_player_id"))
                if passer:
                    _bump(counts, (passer, game_id), "passing_tds_40_plus", 1)
                    _remember(teams, passer, game_id, posteam)
                if receiver:
                    _bump(counts, (receiver, game_id), "receiving_tds_40_plus", 1)
                    _remember(teams, receiver, game_id, posteam)
            elif as_flag(play.get("rush_touchdown")):
                rusher = as_text(play.get("rusher_player_id"))
                if rusher:
                    _bump(counts, (rusher, game_id), "rushing_tds_40_plus", 1)
                    _remember(teams, rusher, game_id, posteam)
    if "return_tds" in available and play_type in _KICK_PLAYS and as_flag(play.get("return_touchdown")):
        returner = _returner(play)
        if returner:
            _bump(counts, (returner, game_id), "return_tds", 1)
            _remember(teams, returner, game_id, as_text(play.get("return_team")) or posteam)
    if "offensive_fumble_return_tds" in available and _offensive_fumble_return(play, play_type):
        player_id = as_text(play.get("fumble_recovery_1_player_id"))
        if player_id:
            _bump(counts, (player_id, game_id), "offensive_fumble_return_tds", 1)
            _remember(teams, player_id, game_id, posteam)
    if (
        "field_goal_yards_made" in available
        and (as_text(play.get("field_goal_result")) or "").lower() == "made"
    ):
        kicker = as_text(play.get("kicker_player_id"))
        distance = as_float(play.get("kick_distance"))
        if kicker and distance is not None and distance >= 0:
            _bump(counts, (kicker, game_id), "field_goal_yards_made", distance)
            _remember(teams, kicker, game_id, posteam)
    if (
        "extra_points_made" in available
        and (as_text(play.get("extra_point_result")) or "").lower() == "good"
    ):
        kicker = as_text(play.get("kicker_player_id"))
        if kicker:
            _bump(counts, (kicker, game_id), "extra_points_made", 1)
            _remember(teams, kicker, game_id, posteam)


def _team_play(
    play: dict[str, object],
    game_id: str,
    available: set[str],
    counts: dict[tuple[str, str], dict[str, float]],
    excluded: dict[tuple[str, str], int],
) -> None:
    play_type = (as_text(play.get("play_type")) or "").lower()
    defteam = as_text(play.get("defteam"))
    posteam = as_text(play.get("posteam"))
    td_team = as_text(play.get("td_team"))
    kick_return = play_type in _KICK_PLAYS and as_flag(play.get("return_touchdown"))
    if "sacks" in available and defteam and as_flag(play.get("sack")):
        _bump(counts, (defteam, game_id), "sacks", 1)
    if "interceptions" in available and defteam and as_flag(play.get("interception")):
        _bump(counts, (defteam, game_id), "interceptions", 1)
    if "fumble_recoveries" in available and defteam:
        for slot in (1, 2):
            recovered = as_text(play.get(f"fumble_recovery_{slot}_team"))
            if recovered == defteam:
                _bump(counts, (defteam, game_id), "fumble_recoveries", 1)
    if "defense_tds" in available and as_flag(play.get("touchdown")) and td_team and defteam:
        if td_team == defteam and td_team != posteam and not kick_return:
            _bump(counts, (defteam, game_id), "defense_tds", 1)
    if "safeties" in available and defteam and as_flag(play.get("safety")):
        _bump(counts, (defteam, game_id), "safeties", 1)
    if "blocked_kicks" in available and defteam and _blocked(play):
        _bump(counts, (defteam, game_id), "blocked_kicks", 1)
    return_team = as_text(play.get("return_team"))
    if play_type in _KICK_PLAYS and return_team:
        if "return_yards" in available:
            yards = as_float(play.get("return_yards")) or 0.0
            _bump(counts, (return_team, game_id), "return_yards", yards)
        if "return_tds" in available and as_flag(play.get("return_touchdown")):
            _bump(counts, (return_team, game_id), "return_tds", 1)
    if "extra_point_returns" in available and defteam and as_flag(play.get("defensive_extra_point_conv")):
        _bump(counts, (defteam, game_id), "extra_point_returns", 1)
    if "points_allowed" in available and as_flag(play.get("touchdown")) and td_team:
        defensive = td_team == defteam and td_team != posteam and not kick_return
        if kick_return or defensive:
            excluded[(td_team, game_id)] += 1


def _returner(play: dict[str, object]) -> str | None:
    for name in _RETURNER_COLUMNS:
        found = as_text(play.get(name))
        if found:
            return found
    return None


def _offensive_fumble_return(play: dict[str, object], play_type: str) -> bool:
    if play_type in {"kickoff", "punt", "field_goal", "extra_point"}:
        return False
    if not as_flag(play.get("touchdown")):
        return False
    if as_flag(play.get("pass_touchdown")) or as_flag(play.get("rush_touchdown")):
        return False
    posteam = as_text(play.get("posteam"))
    td_team = as_text(play.get("td_team"))
    recovered = as_text(play.get("fumble_recovery_1_team"))
    return bool(posteam) and td_team == posteam and recovered == posteam


def _blocked(play: dict[str, object]) -> bool:
    field_goal = (as_text(play.get("field_goal_result")) or "").lower() == "blocked"
    extra = (as_text(play.get("extra_point_result")) or "").lower() == "blocked"
    return field_goal or extra or as_flag(play.get("punt_blocked"))


def _rows(
    clocks: list[GameClock],
    player_available: set[str],
    team_available: set[str],
    player_counts: dict[tuple[str, str], dict[str, float]],
    player_teams: dict[tuple[str, str], str],
    team_counts: dict[tuple[str, str], dict[str, float]],
    opponent_score: dict[tuple[str, str], float | None],
) -> list[dict[str, object]]:
    by_id = {clock.game_id: clock for clock in clocks}
    rows: list[dict[str, object]] = []
    for (player_id, game_id), counts in player_counts.items():
        if not any(counts.get(name, 0) for name in PLAYER_FIELDS):
            continue
        clock = by_id.get(game_id)
        if clock is None:
            continue
        rows.append(
            _event_row(
                player_id=player_id,
                team=player_teams.get((player_id, game_id), ""),
                unit="player",
                clock=clock,
                counts=counts,
                player_available=player_available,
                team_available=set(),
                opponent_score=None,
            )
        )
    for clock in clocks:
        for team in (clock.home_team, clock.away_team):
            counts = team_counts.get((team, clock.game_id), {})
            if not team_available and not counts:
                continue
            rows.append(
                _event_row(
                    player_id=f"DEF:{team}",
                    team=team,
                    unit="dst",
                    clock=clock,
                    counts=counts,
                    player_available=set(),
                    team_available=team_available,
                    opponent_score=opponent_score.get((team, clock.game_id)),
                )
            )
    return rows


def _event_row(
    *,
    player_id: str,
    team: str,
    unit: str,
    clock: GameClock,
    counts: dict[str, float],
    player_available: set[str],
    team_available: set[str],
    opponent_score: float | None,
) -> dict[str, object]:
    row: dict[str, object] = {
        "player_id": player_id,
        "week": clock.week,
        "season_type": clock.season_type,
        "game_id": clock.game_id,
        "team": team,
        "unit": unit,
        "opponent_score": opponent_score,
        "known_as_of": shift(clock.kickoff, STATS_LAG),
        "known_as_of_method": "kickoff_plus_6h",
        "ingested_at": "",
        "source": SOURCE_NAME,
        "row_hash": "",
        "season": clock.season,
    }
    for name in PLAYER_FIELDS:
        if name in player_available:
            row[name] = counts.get(name, 0.0)
        else:
            row[name] = None
    for name in TEAM_FIELDS:
        if name == "points_allowed":
            row[name] = counts.get(name) if name in team_available else None
            continue
        if name in team_available:
            row[name] = counts.get(name, 0.0)
        else:
            row[name] = None
    return row
