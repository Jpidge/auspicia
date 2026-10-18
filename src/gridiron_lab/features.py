"""One feature pipeline for training and for serving.

Every table is read through ``open_db(data_dir).as_of``. A Week W row uses
only inputs known before that player's cutoff, and it never uses Week W
stats or snaps, even when those rows were stamped early.

``as_of`` on the feature row is the cutoff that was applied. ``simulated_capture``
sets that cutoff from the Thursday 20:00 UTC capture. ``capture`` uses ``now``,
then clamps each player to their own kickoff.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from gridiron_lab.data.clock import ET, format_utc, parse_utc, schedule_released
from gridiron_lab.data.errors import IngestError
from gridiron_lab.data.store import Database, open_db
from gridiron_lab.logging import get_logger

logger = get_logger("features")

FEATURE_VERSION = "v2.0"
EWMA_ALPHA = 0.3
POLICIES = ("capture", "simulated_capture")
SKILL_POSITIONS = frozenset({"QB", "RB", "WR", "TE", "K"})
_COUNT_STATS = (
    "points",
    "targets",
    "carries",
    "air_yards",
    "redzone_touches",
    "pass_attempts",
    "dropbacks",
    "designed_rushes",
)
_SHARE_STATS = ("snap", "target", "carry", "air_yards", "redzone")
_WINDOWS = ("last", "roll3", "roll5", "ewma")


class FeatureError(Exception):
    """The feature build was asked for a week or a policy it cannot serve."""


def _columns() -> tuple[str, ...]:
    names = [
        "player_id",
        "season",
        "week",
        "season_type",
        "team",
        "opponent",
        "position",
        "game_id",
        "as_of",
        "as_of_policy",
        "feature_version",
        "is_home",
        "is_home_missing",
        "rest_days",
        "rest_days_missing",
        "games_played",
        "prior_season_ppg",
        "prior_season_ppg_missing",
        "carryover_weight",
        "team_changed",
        "team_changed_missing",
        "is_bye",
        "force_zero",
    ]
    for stat in _COUNT_STATS:
        for window in _WINDOWS:
            names.append(f"{stat}_{window}")
            names.append(f"{stat}_{window}_missing")
    for name in ("points_slope4", "opportunity_slope4"):
        names.append(name)
        names.append(f"{name}_missing")
    for stat in _SHARE_STATS:
        for window in _WINDOWS:
            names.append(f"{stat}_share_{window}")
            names.append(f"{stat}_share_{window}_missing")
    names.extend(
        [
            "implied_team_total",
            "team_spread",
            "game_total",
            "vegas_missing",
            "opp_points_allowed_pos_roll5",
            "opp_points_allowed_pos_roll5_missing",
            "injury_status",
            "practice_status",
            "injury_missing",
            "teammates_out_same_position",
            "teammates_out_same_position_missing",
            "depth_rank",
            "depth_rank_missing",
            "depth_position",
            "depth_missing",
        ]
    )
    return tuple(names)


FEATURE_COLUMNS = _columns()


def thursday_capture(earliest_kickoff: datetime) -> datetime:
    """Thursday 20:00 UTC of the NFL week that contains ``earliest_kickoff``.

    The week runs Thursday through the following Wednesday, in America/New_York.
    """

    local = earliest_kickoff.astimezone(ET)
    weekday = local.weekday()
    back = weekday - 3 if weekday >= 3 else weekday + 4
    day = (local - timedelta(days=back)).date()
    return datetime(day.year, day.month, day.day, 20, 0, tzinfo=UTC)


def build_features(
    season: int,
    week: int,
    as_of_policy: str,
    *,
    data_dir: Path,
    now: datetime | None = None,
    season_type: str = "REG",
) -> list[dict[str, object]]:
    """Feature rows for one regular-season week.

    ``capture`` serves at ``now`` (default: the current UTC time).
    ``simulated_capture`` trains and evaluates at Thursday 20:00 UTC.
    """

    if as_of_policy not in POLICIES:
        raise FeatureError(f"unknown as_of policy {as_of_policy!r}")
    if season_type != "REG":
        raise FeatureError("features are built for the regular season")
    if week < 1:
        raise FeatureError(f"week must be positive, got {week}")

    db = open_db(data_dir)
    probe_at = parse_utc(schedule_released(season)) + timedelta(days=1)
    schedule = _games(db.as_of("games", probe_at))
    week_schedule = [
        game
        for game in schedule.values()
        if game.season == season and game.week == week and game.season_type == season_type
    ]
    if not week_schedule:
        raise FeatureError(f"no {season_type} schedule for {season} week {week}")
    earliest = min(game.kickoff for game in week_schedule)
    if as_of_policy == "simulated_capture":
        policy_as_of = thursday_capture(earliest)
    else:
        if now is None:
            now = datetime.now(UTC)
        if now.tzinfo is None:
            raise FeatureError("now must be timezone-aware")
        policy_as_of = now.astimezone(UTC)

    cutoffs = {policy_as_of}
    for game in week_schedule:
        if game.kickoff < policy_as_of:
            cutoffs.add(game.kickoff)
    snapshots = {
        format_utc(ts): _Snapshot.load(db, ts, season, week, season_type) for ts in cutoffs
    }
    policy = snapshots[format_utc(policy_as_of)]
    if not _week_games(policy, season, week, season_type):
        raise FeatureError(f"{season} week {week} is not knowable at {format_utc(policy_as_of)}")

    rows: list[dict[str, object]] = []
    seen: set[str] = set()
    for player in _universe(policy, season):
        cutoff = _player_cutoff(player.team, policy_as_of, week_schedule)
        snap = snapshots[format_utc(cutoff)]
        resolved = _resolve(snap, player.player_id, season)
        if resolved is None:
            continue
        if resolved.player_id in seen:
            continue
        seen.add(resolved.player_id)
        rows.append(
            _feature_row(
                snap,
                resolved,
                season=season,
                week=week,
                season_type=season_type,
                cutoff=cutoff,
                policy=as_of_policy,
            )
        )
    rows.sort(key=lambda row: str(row["player_id"]))
    logger.info(
        "Features %s week %s %s · %s players · policy_as_of %s",
        season,
        week,
        as_of_policy,
        len(rows),
        format_utc(policy_as_of),
    )
    return rows


@dataclass(frozen=True)
class _Game:
    game_id: str
    season: int
    week: int
    season_type: str
    home: str
    away: str
    kickoff: datetime
    spread: float | None
    total: float | None
    home_implied: float | None
    away_implied: float | None


@dataclass(frozen=True)
class _Player:
    player_id: str
    team: str
    position: str


@dataclass(frozen=True)
class _Totals:
    targets: float | None
    carries: float | None
    redzone: float | None
    qb_air: float | None
    rec_air: float | None
    snap_den: float | None


class _Snapshot:
    def __init__(
        self,
        games: dict[str, _Game],
        weeks: list[dict[str, object]],
        snaps: list[dict[str, object]],
        injuries: list[dict[str, object]],
        depth: list[dict[str, object]],
        rosters: list[dict[str, object]],
    ) -> None:
        self.games = games
        self.weeks_by_player: dict[str, list[dict[str, object]]] = defaultdict(list)
        for row in weeks:
            player_id = _text(row.get("player_id"))
            if player_id:
                self.weeks_by_player[player_id].append(row)
        for history in self.weeks_by_player.values():
            history.sort(key=_week_sort)
        self.snaps_by_player_game = {
            (_text(row.get("player_id")) or "", _text(row.get("game_id")) or ""): row
            for row in snaps
        }
        self.injuries = {
            (
                _text(row.get("player_id")) or "",
                _num_int(row.get("season")),
                _num_int(row.get("week")),
                _text(row.get("season_type")) or "",
            ): row
            for row in injuries
        }
        self.depth_by_player: dict[str, list[dict[str, object]]] = defaultdict(list)
        for row in depth:
            player_id = _text(row.get("player_id"))
            if player_id:
                self.depth_by_player[player_id].append(row)
        self.roster_by_player = {
            _text(row.get("player_id")) or "": row for row in rosters if _text(row.get("player_id"))
        }
        self.totals = _team_totals(weeks, snaps)
        self.allowed = _points_allowed(weeks)

    @classmethod
    def load(
        cls,
        db: Database,
        ts: datetime,
        season: int,
        week: int,
        season_type: str,
    ) -> _Snapshot:
        weeks = [
            row
            for row in db.as_of("player_weeks", ts)
            if _is_history(row, season, week, season_type)
        ]
        snaps = [
            row
            for row in db.as_of("snap_counts", ts)
            if _is_history(row, season, week, season_type)
        ]
        return cls(
            _games(db.as_of("games", ts)),
            weeks,
            snaps,
            list(db.as_of("injuries", ts)),
            list(db.as_of("depth_charts", ts)),
            list(db.as_of("rosters", ts)),
        )


def _feature_row(
    snap: _Snapshot,
    player: _Player,
    *,
    season: int,
    week: int,
    season_type: str,
    cutoff: datetime,
    policy: str,
) -> dict[str, object]:
    game = _team_game(snap, season, week, season_type, player.team)
    bye = game is None
    history = snap.weeks_by_player.get(player.player_id, [])
    opponent = _opponent(game, player.team)
    is_home = _is_home(game, player.team)
    rest = _rest_days(snap, history, game)
    prior = [row for row in history if _num_int(row.get("season")) == season - 1]
    prior_points = _observed(prior, lambda row: _num(row.get("fantasy_points")))
    prior_ppg = _mean(prior_points)
    prior_team = _text(prior[-1].get("team")) if prior else None
    if prior_team is not None and prior_team == player.team:
        carry = 0.5 ** (week - 1)
    else:
        carry = 0.0
    if prior_team is None:
        changed: int | None = None
        changed_missing = 1
    else:
        changed = 0 if prior_team == player.team else 1
        changed_missing = 0
    injury = snap.injuries.get((player.player_id, season, week, season_type))
    status = _text(injury.get("report_status")) if injury else None
    practice = _text(injury.get("practice_status")) if injury else None
    out = _is_out(status)
    teammates, teammates_missing = _teammates_out(snap, player, season, week, season_type)
    charts = snap.depth_by_player.get(player.player_id, [])
    depth = _pick_depth(charts, player.team, player.position)
    implied, spread, total, vegas_missing = _vegas(game, player.team)
    allowed = _allowed_roll(snap, opponent, player.position)

    out_row: dict[str, object] = {
        "player_id": player.player_id,
        "season": season,
        "week": week,
        "season_type": season_type,
        "team": player.team,
        "opponent": opponent,
        "position": player.position,
        "game_id": None if game is None else game.game_id,
        "as_of": format_utc(cutoff),
        "as_of_policy": policy,
        "feature_version": FEATURE_VERSION,
        "is_home": is_home,
        "is_home_missing": 1 if is_home is None else 0,
        "rest_days": rest,
        "rest_days_missing": 1 if rest is None else 0,
        "games_played": len(history),
        "prior_season_ppg": prior_ppg,
        "prior_season_ppg_missing": 1 if prior_ppg is None else 0,
        "carryover_weight": carry,
        "team_changed": changed,
        "team_changed_missing": changed_missing,
        "is_bye": 1 if bye else 0,
        "force_zero": 1 if bye or out else 0,
    }
    series = {
        "points": _observed(history, lambda row: _num(row.get("fantasy_points"))),
        "targets": _observed(history, lambda row: _num(row.get("targets"))),
        "carries": _observed(history, lambda row: _num(row.get("carries"))),
        "air_yards": _observed(history, _air_yards),
        "redzone_touches": _observed(history, lambda row: _num(row.get("redzone_touches"))),
        "pass_attempts": _observed(history, lambda row: _num(row.get("pass_attempts"))),
        "dropbacks": _observed(history, lambda row: _num(row.get("dropbacks"))),
        "designed_rushes": _observed(history, _designed),
    }
    for name, values in series.items():
        _put_windows(out_row, name, values)
    _put(out_row, "points_slope4", _slope(series["points"]))
    _put(out_row, "opportunity_slope4", _slope(_observed(history, _opportunity)))
    shares = {
        "snap": _share_series(snap, history, _snap_share),
        "target": _share_series(snap, history, _target_share),
        "carry": _share_series(snap, history, _carry_share),
        "air_yards": _share_series(snap, history, _air_share),
        "redzone": _share_series(snap, history, _redzone_share),
    }
    for name, values in shares.items():
        _put_windows(out_row, f"{name}_share", values)
    out_row["implied_team_total"] = implied
    out_row["team_spread"] = spread
    out_row["game_total"] = total
    out_row["vegas_missing"] = vegas_missing
    _put(out_row, "opp_points_allowed_pos_roll5", allowed)
    out_row["injury_status"] = status
    out_row["practice_status"] = practice
    out_row["injury_missing"] = 0 if injury else 1
    out_row["teammates_out_same_position"] = teammates
    out_row["teammates_out_same_position_missing"] = teammates_missing
    rank = _num_int(depth.get("depth_rank")) if depth else None
    out_row["depth_rank"] = rank
    out_row["depth_rank_missing"] = 1 if rank is None else 0
    out_row["depth_position"] = _text(depth.get("position")) if depth else None
    out_row["depth_missing"] = 0 if depth else 1
    return {name: out_row[name] for name in FEATURE_COLUMNS}


def _universe(snap: _Snapshot, season: int) -> list[_Player]:
    teams = _season_teams(snap, season)
    found: dict[str, _Player] = {}
    for player_id, roster in snap.roster_by_player.items():
        if player_id.startswith("DEF:"):
            continue
        team = _text(roster.get("team"))
        position = _pos(roster.get("position"))
        if team in teams and position in SKILL_POSITIONS:
            found[player_id] = _Player(player_id, team, position)
    for player_id, charts in snap.depth_by_player.items():
        if player_id in found or player_id.startswith("DEF:"):
            continue
        skill_charts = [
            row
            for row in charts
            if _text(row.get("team")) in teams and _pos(row.get("position")) in SKILL_POSITIONS
        ]
        chart = _pick_depth(skill_charts, "", "")
        if chart is None:
            continue
        team = _text(chart.get("team"))
        position = _pos(chart.get("position"))
        if team in teams and position in SKILL_POSITIONS:
            found[player_id] = _Player(player_id, team, position)
    for team in sorted(teams):
        player_id = f"DEF:{team}"
        found.setdefault(player_id, _Player(player_id, team, "DST"))
    return list(found.values())


def _resolve(snap: _Snapshot, player_id: str, season: int) -> _Player | None:
    teams = _season_teams(snap, season)
    if player_id.startswith("DEF:"):
        team = player_id.removeprefix("DEF:")
        if team in teams:
            return _Player(player_id, team, "DST")
        return None
    roster = snap.roster_by_player.get(player_id)
    if roster is not None:
        team = _text(roster.get("team"))
        position = _pos(roster.get("position"))
        if team in teams and position in SKILL_POSITIONS:
            return _Player(player_id, team, position)
        return None
    charts = [
        row
        for row in snap.depth_by_player.get(player_id, [])
        if _text(row.get("team")) in teams and _pos(row.get("position")) in SKILL_POSITIONS
    ]
    chart = _pick_depth(charts, "", "")
    if chart is None:
        return None
    team = _text(chart.get("team"))
    position = _pos(chart.get("position"))
    if team in teams and position in SKILL_POSITIONS:
        return _Player(player_id, team, position)
    return None


def _player_cutoff(team: str, policy_as_of: datetime, week_schedule: list[_Game]) -> datetime:
    kickoffs = [game.kickoff for game in week_schedule if team in {game.home, game.away}]
    if not kickoffs:
        return policy_as_of
    kickoff = min(kickoffs)
    if kickoff < policy_as_of:
        return kickoff
    return policy_as_of


def _week_games(snap: _Snapshot, season: int, week: int, season_type: str) -> list[_Game]:
    return [
        game
        for game in snap.games.values()
        if game.season == season and game.week == week and game.season_type == season_type
    ]


def _team_game(
    snap: _Snapshot,
    season: int,
    week: int,
    season_type: str,
    team: str,
) -> _Game | None:
    matches = [
        game
        for game in _week_games(snap, season, week, season_type)
        if team in {game.home, game.away}
    ]
    if not matches:
        return None
    return sorted(matches, key=lambda game: game.kickoff)[0]


def _season_teams(snap: _Snapshot, season: int) -> set[str]:
    teams: set[str] = set()
    for game in snap.games.values():
        if game.season == season and game.season_type == "REG":
            teams.add(game.home)
            teams.add(game.away)
    return teams


def _opponent(game: _Game | None, team: str) -> str | None:
    if game is None:
        return None
    if team == game.home:
        other = game.away
    elif team == game.away:
        other = game.home
    else:
        return None
    if other == team:
        return None
    return other


def _is_home(game: _Game | None, team: str) -> int | None:
    if game is None or team not in {game.home, game.away}:
        return None
    return 1 if team == game.home else 0


def _vegas(
    game: _Game | None,
    team: str,
) -> tuple[float | None, float | None, float | None, int]:
    if game is None or team not in {game.home, game.away}:
        return None, None, None, 1
    total = game.total
    if team == game.home:
        implied = game.home_implied
        spread = game.spread
    else:
        implied = game.away_implied
        spread = None if game.spread is None else -game.spread
    missing = 1 if implied is None or spread is None or total is None else 0
    return implied, spread, total, missing


def _rest_days(
    snap: _Snapshot,
    history: list[dict[str, object]],
    game: _Game | None,
) -> float | None:
    if game is None or not history:
        return None
    previous = snap.games.get(_text(history[-1].get("game_id")) or "")
    if previous is None:
        return None
    return (game.kickoff - previous.kickoff).total_seconds() / 86400


def _teammates_out(
    snap: _Snapshot,
    player: _Player,
    season: int,
    week: int,
    season_type: str,
) -> tuple[int | None, int]:
    if player.position == "DST":
        return None, 1
    reports = []
    for (player_id, inj_season, inj_week, inj_type), row in snap.injuries.items():
        if inj_season != season or inj_week != week or inj_type != season_type:
            continue
        if _text(row.get("team")) != player.team:
            continue
        reports.append((player_id, row))
    if not reports:
        return None, 1
    count = 0
    for player_id, row in reports:
        if player_id == player.player_id or not _is_out(_text(row.get("report_status"))):
            continue
        if _teammate_position(snap, player_id) == player.position:
            count += 1
    return count, 0


def _teammate_position(snap: _Snapshot, player_id: str) -> str | None:
    roster = snap.roster_by_player.get(player_id)
    if roster is not None:
        position = _pos(roster.get("position"))
        if position:
            return position
    history = snap.weeks_by_player.get(player_id, [])
    if history:
        return _pos(history[-1].get("position"))
    charts = snap.depth_by_player.get(player_id, [])
    picked = _pick_depth(charts, "", "")
    if picked is None:
        return None
    return _pos(picked.get("position"))


def _pick_depth(
    rows: list[dict[str, object]],
    team: str,
    position: str,
) -> dict[str, object] | None:
    if team:
        rows = [row for row in rows if _text(row.get("team")) == team]
    if not rows:
        return None
    matched = [row for row in rows if position and _pos(row.get("position")) == position]
    pool = matched or rows
    pool = sorted(pool, key=_depth_sort)
    return pool[0]


def _depth_sort(row: dict[str, object]) -> tuple[int, int, str]:
    rank = _num_int(row.get("depth_rank"))
    return (
        1 if rank is None else 0,
        rank if rank is not None else 0,
        _text(row.get("position")) or "",
    )


def _allowed_roll(snap: _Snapshot, opponent: str | None, position: str) -> float | None:
    if opponent is None or position not in SKILL_POSITIONS:
        return None
    games = snap.allowed.get((opponent, position), [])
    if not games:
        return None
    return _mean([points for _season, _week, _game, points in games[-5:]])


def _points_allowed(
    weeks: list[dict[str, object]],
) -> dict[tuple[str, str], list[tuple[int, int, str, float]]]:
    buckets: dict[tuple[str, str, int, int, str], float] = {}
    for row in weeks:
        position = _pos(row.get("position"))
        opponent = _text(row.get("opponent"))
        points = _num(row.get("fantasy_points"))
        if position not in SKILL_POSITIONS or opponent is None or points is None:
            continue
        if opponent == _text(row.get("team")):
            continue
        key = (
            opponent,
            position,
            _num_int(row.get("season")) or 0,
            _num_int(row.get("week")) or 0,
            _text(row.get("game_id")) or "",
        )
        buckets[key] = buckets.get(key, 0.0) + points
    grouped: dict[tuple[str, str], list[tuple[int, int, str, float]]] = defaultdict(list)
    for (opponent, position, season, week, game_id), points in buckets.items():
        grouped[(opponent, position)].append((season, week, game_id, points))
    for games in grouped.values():
        games.sort()
    return grouped


def _team_totals(
    weeks: list[dict[str, object]],
    snaps: list[dict[str, object]],
) -> dict[tuple[str, str], _Totals]:
    buckets: dict[tuple[str, str], dict[str, list[float]]] = defaultdict(
        lambda: {"targets": [], "carries": [], "redzone": [], "qb_air": [], "rec_air": []}
    )
    for row in weeks:
        game_id = _text(row.get("game_id"))
        team = _text(row.get("team"))
        if not game_id or not team or team == _text(row.get("opponent")):
            continue
        bucket = buckets[(game_id, team)]
        _keep(bucket["targets"], _num(row.get("targets")))
        _keep(bucket["carries"], _num(row.get("carries")))
        _keep(bucket["redzone"], _num(row.get("redzone_touches")))
        if _pos(row.get("position")) == "QB":
            _keep(bucket["qb_air"], _num(row.get("passing_air_yards")))
        else:
            _keep(bucket["rec_air"], _num(row.get("receiving_air_yards")))
    snap_den: dict[tuple[str, str], float] = {}
    for row in snaps:
        game_id = _text(row.get("game_id"))
        team = _text(row.get("team"))
        snaps_played = _num(row.get("offense_snaps"))
        if not game_id or not team or snaps_played is None:
            continue
        key = (game_id, team)
        snap_den[key] = max(snap_den.get(key, 0.0), snaps_played)
    totals: dict[tuple[str, str], _Totals] = {}
    keys = set(buckets) | set(snap_den)
    for key in keys:
        bucket = buckets.get(key, {})
        totals[key] = _Totals(
            targets=_summed(bucket.get("targets", [])),
            carries=_summed(bucket.get("carries", [])),
            redzone=_summed(bucket.get("redzone", [])),
            qb_air=_summed(bucket.get("qb_air", [])),
            rec_air=_summed(bucket.get("rec_air", [])),
            snap_den=snap_den.get(key),
        )
    return totals


def _share_series(
    snap: _Snapshot,
    history: list[dict[str, object]],
    getter,
) -> list[float]:
    values: list[float] = []
    for row in history:
        value = getter(snap, row)
        if value is not None:
            values.append(value)
    return values


def _snap_share(snap: _Snapshot, row: dict[str, object]) -> float | None:
    game_id = _text(row.get("game_id")) or ""
    player_id = _text(row.get("player_id")) or ""
    played = snap.snaps_by_player_game.get((player_id, game_id))
    if played is None:
        return None
    pct = _num(played.get("offense_pct"))
    if pct is not None:
        return pct
    offense = _num(played.get("offense_snaps"))
    totals = snap.totals.get((game_id, _text(row.get("team")) or ""))
    den = None if totals is None else totals.snap_den
    return _ratio(offense, den)


def _target_share(snap: _Snapshot, row: dict[str, object]) -> float | None:
    totals = _row_totals(snap, row)
    den = None if totals is None else totals.targets
    return _ratio(_num(row.get("targets")), den)


def _carry_share(snap: _Snapshot, row: dict[str, object]) -> float | None:
    totals = _row_totals(snap, row)
    den = None if totals is None else totals.carries
    return _ratio(_num(row.get("carries")), den)


def _air_share(snap: _Snapshot, row: dict[str, object]) -> float | None:
    totals = _row_totals(snap, row)
    if totals is None:
        return None
    if _pos(row.get("position")) == "QB":
        return _ratio(_num(row.get("passing_air_yards")), totals.qb_air)
    return _ratio(_num(row.get("receiving_air_yards")), totals.rec_air)


def _redzone_share(snap: _Snapshot, row: dict[str, object]) -> float | None:
    totals = _row_totals(snap, row)
    den = None if totals is None else totals.redzone
    return _ratio(_num(row.get("redzone_touches")), den)


def _row_totals(snap: _Snapshot, row: dict[str, object]) -> _Totals | None:
    return snap.totals.get((_text(row.get("game_id")) or "", _text(row.get("team")) or ""))


def _air_yards(row: dict[str, object]) -> float | None:
    if _pos(row.get("position")) == "QB":
        return _num(row.get("passing_air_yards"))
    return _num(row.get("receiving_air_yards"))


def _designed(row: dict[str, object]) -> float | None:
    if _pos(row.get("position")) != "QB":
        return 0.0
    carries = _num(row.get("carries"))
    scrambles = _num(row.get("scrambles"))
    if carries is None or scrambles is None:
        return None
    return carries - scrambles


def _opportunity(row: dict[str, object]) -> float | None:
    targets = _num(row.get("targets"))
    carries = _num(row.get("carries"))
    if targets is None or carries is None:
        return None
    return targets + carries


def _put_windows(row: dict[str, object], prefix: str, values: list[float]) -> None:
    rolled = {
        "last": _last(values),
        "roll3": _roll(values, 3),
        "roll5": _roll(values, 5),
        "ewma": _ewma(values),
    }
    for window, value in rolled.items():
        _put(row, f"{prefix}_{window}", value)


def _put(row: dict[str, object], name: str, value: float | None) -> None:
    row[name] = value
    row[f"{name}_missing"] = 1 if value is None else 0


def _observed(rows: list[dict[str, object]], getter) -> list[float]:
    values: list[float] = []
    for row in rows:
        value = getter(row)
        if value is not None:
            values.append(value)
    return values


def _last(values: list[float]) -> float | None:
    if not values:
        return None
    return values[-1]


def _roll(values: list[float], window: int) -> float | None:
    if not values:
        return None
    return _mean(values[-window:])


def _ewma(values: list[float]) -> float | None:
    if not values:
        return None
    acc = values[0]
    for value in values[1:]:
        acc = EWMA_ALPHA * value + (1.0 - EWMA_ALPHA) * acc
    return acc


def _slope(values: list[float]) -> float | None:
    points = values[-4:]
    count = len(points)
    if count < 2:
        return None
    x_bar = (count - 1) / 2
    y_bar = sum(points) / count
    numer = sum((index - x_bar) * (point - y_bar) for index, point in enumerate(points))
    denom = sum((index - x_bar) ** 2 for index in range(count))
    if denom == 0:
        return None
    return numer / denom


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None:
        return None
    if denominator == 0:
        return 0.0 if numerator == 0 else None
    return numerator / denominator


def _summed(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values)


def _keep(bucket: list[float], value: float | None) -> None:
    if value is not None:
        bucket.append(value)


def _is_history(row: dict[str, object], season: int, week: int, season_type: str) -> bool:
    if _text(row.get("season_type")) != "REG":
        return False
    row_season = _num_int(row.get("season"))
    row_week = _num_int(row.get("week"))
    if row_season is None or row_week is None:
        return False
    if row_season > season:
        return False
    if row_season == season and row_week >= week:
        return False
    return season_type == "REG"


def _games(rows: list[dict[str, object]]) -> dict[str, _Game]:
    games: dict[str, _Game] = {}
    for row in rows:
        game_id = _text(row.get("game_id"))
        kickoff_text = _text(row.get("kickoff_utc"))
        home = _text(row.get("home_team"))
        away = _text(row.get("away_team"))
        season = _num_int(row.get("season"))
        week = _num_int(row.get("week"))
        if (
            not game_id
            or not kickoff_text
            or not home
            or not away
            or season is None
            or week is None
        ):
            continue
        try:
            kickoff = parse_utc(kickoff_text)
        except IngestError:
            continue
        games[game_id] = _Game(
            game_id=game_id,
            season=season,
            week=week,
            season_type=_text(row.get("season_type")) or "",
            home=home,
            away=away,
            kickoff=kickoff,
            spread=_num(row.get("spread_line")),
            total=_num(row.get("total_line")),
            home_implied=_num(row.get("home_implied_total")),
            away_implied=_num(row.get("away_implied_total")),
        )
    return games


def _week_sort(row: dict[str, object]) -> tuple[int, int, str]:
    return (
        _num_int(row.get("season")) or 0,
        _num_int(row.get("week")) or 0,
        _text(row.get("game_id")) or "",
    )


def _is_out(status: str | None) -> bool:
    if status is None:
        return False
    return status.strip().upper() in {"OUT", "O"}


def _pos(value: object) -> str | None:
    text = _text(value)
    if text is None:
        return None
    return text.upper()


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if text == "" or text.lower() in {"none", "nan", "null"}:
        return None
    return text


def _num(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if number != number or number in {float("inf"), float("-inf")}:
            return None
        return number
    return None


def _num_int(value: object) -> int | None:
    number = _num(value)
    if number is None or not number.is_integer():
        return None
    return int(number)
