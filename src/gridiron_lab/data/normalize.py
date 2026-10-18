"""Map NFLverse extracts onto the canonical tables.

Weekly box scores supply the counting stats. Play-level events come from
``pbp_events`` and stay null when that file did not carry the column.
``passing_40`` on the weekly file is not a 40-yard touchdown count.
"""

from __future__ import annotations

from collections import Counter, defaultdict

from gridiron_lab.data.clock import (
    SNAPS_LAG,
    STATS_LAG,
    VEGAS_LEAD,
    GameClock,
    day_before_gameday,
    format_utc,
    implied_totals,
    kickoff_from_schedule,
    parse_report_time,
    schedule_released,
    season_open,
    shift,
)
from gridiron_lab.data.pbp import PbpExtract
from gridiron_lab.data.schema import SOURCE_NAME
from gridiron_lab.data.values import as_float, as_int, as_text
from gridiron_lab.scoring import (
    SCORING_VERSION,
    LeagueScoring,
    ScoringError,
    score_defense,
    score_offense,
)

_XREF_FIELDS = ("yahoo_id", "espn_id", "pfr_id", "sleeper_id")
_FUMBLE_COLUMNS = ("sack_fumbles_lost", "rushing_fumbles_lost", "receiving_fumbles_lost")
_TWO_POINT_COLUMNS = (
    "passing_2pt_conversions",
    "rushing_2pt_conversions",
    "receiving_2pt_conversions",
)
_RETURN_YARD_COLUMNS = ("kickoff_return_yards", "punt_return_yards")
_OFFENSE_STATS = (
    "passing_yards",
    "passing_tds",
    "interceptions",
    "passing_tds_40_plus",
    "rushing_yards",
    "rushing_tds",
    "rushing_tds_40_plus",
    "receptions",
    "receiving_yards",
    "receiving_tds",
    "receiving_tds_40_plus",
    "fumbles_lost",
    "two_point",
    "return_yards",
    "return_tds",
    "offensive_fumble_return_tds",
    "extra_points_made",
    "field_goal_yards_made",
)
_DEFENSE_STATS = (
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
_USAGE_FIELDS = (
    "targets",
    "carries",
    "pass_attempts",
    "passing_air_yards",
    "receiving_air_yards",
    "dropbacks",
    "scrambles",
    "redzone_touches",
)
_WEEKLY_USAGE = (
    ("targets", "targets"),
    ("carries", "carries"),
    ("pass_attempts", "attempts"),
    ("passing_air_yards", "passing_air_yards"),
    ("receiving_air_yards", "receiving_air_yards"),
)
_PBP_USAGE = ("dropbacks", "scrambles", "redzone_touches")


def season_type_of(row: dict[str, object]) -> str:
    raw = as_text(row.get("season_type")) or as_text(row.get("game_type")) or ""
    upper = raw.upper()
    if upper in {"REG", "REGULAR"}:
        return "REG"
    if upper in {"POST", "POSTSEASON", "WC", "DIV", "CON", "SB"}:
        return "POST"
    return upper


def build_games(
    rows: list[dict[str, object]],
    season: int,
) -> tuple[list[dict[str, object]], list[GameClock], Counter[str]]:
    notes: Counter[str] = Counter()
    stored: list[dict[str, object]] = []
    clocks: list[GameClock] = []
    for row in rows:
        if as_int(row.get("season")) != season:
            continue
        game_id = as_text(row.get("game_id"))
        home = as_text(row.get("home_team"))
        away = as_text(row.get("away_team"))
        week = as_int(row.get("week"))
        if not game_id or not home or not away or week is None:
            notes["schedule_row_missing_identity"] += 1
            continue
        kickoff = kickoff_from_schedule(row.get("gameday"), row.get("gametime"))
        if kickoff is None:
            notes["schedule_row_missing_kickoff"] += 1
            continue
        kickoff_at, kickoff_method = kickoff
        game_type = as_text(row.get("game_type")) or season_type_of(row)
        clock = GameClock(
            game_id=game_id,
            season=season,
            week=week,
            season_type=season_type_of(row),
            game_type=game_type,
            home_team=home,
            away_team=away,
            kickoff=kickoff_at,
            kickoff_method=kickoff_method,
            spread_line=as_float(row.get("spread_line")),
            total_line=as_float(row.get("total_line")),
            home_score=as_int(row.get("home_score")),
            away_score=as_int(row.get("away_score")),
            gameday=str(row.get("gameday")).strip()[:10],
        )
        clocks.append(clock)
        stored.extend(_game_versions(clock))
        if clock.spread_line is None or clock.total_line is None:
            notes["schedule_missing_vegas_line"] += 1
        if kickoff_method != "schedule_kickoff_et":
            notes[kickoff_method] += 1
    return stored, clocks, notes


def _game_versions(clock: GameClock) -> list[dict[str, object]]:
    home_total, away_total = implied_totals(clock.total_line, clock.spread_line)
    identity = {
        "game_id": clock.game_id,
        "week": clock.week,
        "season_type": clock.season_type,
        "game_type": clock.game_type,
        "home_team": clock.home_team,
        "away_team": clock.away_team,
        "kickoff_utc": format_utc(clock.kickoff),
        "source": SOURCE_NAME,
        "ingested_at": "",
        "row_hash": "",
        "season": clock.season,
    }
    lines = {
        "spread_line": clock.spread_line,
        "total_line": clock.total_line,
        "home_implied_total": home_total,
        "away_implied_total": away_total,
    }
    no_lines = {
        "spread_line": None,
        "total_line": None,
        "home_implied_total": None,
        "away_implied_total": None,
    }
    schedule = {
        **identity,
        **no_lines,
        "home_score": None,
        "away_score": None,
        "record_kind": "schedule",
        "known_as_of": schedule_released(clock.season),
        "known_as_of_method": "schedule_released_may_15",
    }
    pregame = {
        **identity,
        **lines,
        "home_score": None,
        "away_score": None,
        "record_kind": "pregame",
        "known_as_of": shift(clock.kickoff, -VEGAS_LEAD),
        "known_as_of_method": "kickoff_minus_1h",
    }
    versions = [schedule, pregame]
    if clock.home_score is not None and clock.away_score is not None:
        versions.append(
            {
                **identity,
                **lines,
                "home_score": clock.home_score,
                "away_score": clock.away_score,
                "record_kind": "postgame",
                "known_as_of": shift(clock.kickoff, STATS_LAG),
                "known_as_of_method": "kickoff_plus_6h",
            }
        )
    return versions


def build_identity(
    rows: list[dict[str, object]],
    extra_rows: list[dict[str, object]] | None = None,
) -> tuple[list[dict[str, object]], list[dict[str, object]], list[dict[str, object]], Counter[str]]:
    """Players, unambiguous crosswalk rows, and surfaced collisions."""

    notes: Counter[str] = Counter()
    players: list[dict[str, object]] = []
    grouped: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in rows:
        player_id = as_text(row.get("gsis_id")) or as_text(row.get("player_id"))
        if not player_id or player_id.startswith("DEF:"):
            notes["player_row_missing_gsis_id"] += 1
            continue
        name = (
            as_text(row.get("display_name"))
            or as_text(row.get("player_display_name"))
            or as_text(row.get("full_name"))
            or player_id
        )
        entry_year = _first_year(row)
        known, method = _identity_clock(entry_year)
        players.append(
            {
                "player_id": player_id,
                "display_name": name,
                "entry_year": entry_year,
                "known_as_of": known,
                "known_as_of_method": method,
                "ingested_at": "",
                "source": SOURCE_NAME,
                "row_hash": "",
                "season": 0,
            }
        )
    for system, external, linked_id in external_links([*rows, *(extra_rows or [])]):
        grouped[(system, external)].add(linked_id)
    xref: list[dict[str, object]] = []
    issues: list[dict[str, object]] = []
    for (system, external), player_ids in grouped.items():
        ordered = ",".join(sorted(player_ids))
        if len(player_ids) == 1:
            player_id = next(iter(player_ids))
            xref.append(
                {
                    "source_system": system,
                    "external_id": external,
                    "player_id": player_id,
                    "match_method": "nflverse_crosswalk",
                    "match_confidence": 1.0,
                    "known_as_of": "1970-01-01T00:00:00Z",
                    "known_as_of_method": "crosswalk_undated",
                    "ingested_at": "",
                    "source": SOURCE_NAME,
                    "row_hash": "",
                    "season": 0,
                }
            )
        else:
            notes["ambiguous_external_id"] += 1
            issues.append(
                {
                    "source_system": system,
                    "external_id": external,
                    "candidate_player_ids": ordered,
                    "known_as_of": "1970-01-01T00:00:00Z",
                    "known_as_of_method": "crosswalk_undated",
                    "ingested_at": "",
                    "source": SOURCE_NAME,
                    "row_hash": "",
                    "season": 0,
                }
            )
    return players, xref, issues, notes


def dst_players(clocks: list[GameClock]) -> list[dict[str, object]]:
    teams = {team for clock in clocks for team in (clock.home_team, clock.away_team)}
    return [
        {
            "player_id": f"DEF:{team}",
            "display_name": f"{team} DST",
            "entry_year": None,
            "known_as_of": "1970-01-01T00:00:00Z",
            "known_as_of_method": "dst_identity",
            "ingested_at": "",
            "source": SOURCE_NAME,
            "row_hash": "",
            "season": 0,
        }
        for team in sorted(teams)
    ]


def build_player_weeks(
    stats: list[dict[str, object]],
    clocks: list[GameClock],
    events: PbpExtract,
    rules: LeagueScoring,
) -> tuple[list[dict[str, object]], Counter[str]]:
    notes: Counter[str] = Counter()
    by_id = {clock.game_id: clock for clock in clocks}
    columns = set(stats[0]) if stats else set()
    rows: list[dict[str, object]] = []
    for stat in stats:
        position = (as_text(stat.get("position")) or "").upper()
        if position in {"DST", "DEF"}:
            notes["skipped_weekly_dst_row"] += 1
            continue
        player_id = as_text(stat.get("player_id")) or as_text(stat.get("gsis_id"))
        game_id = as_text(stat.get("game_id"))
        clock = by_id.get(game_id or "")
        if player_id is None:
            notes["player_week_missing_player_id"] += 1
            continue
        if clock is None:
            notes["player_week_without_game"] += 1
            continue
        offense = _offense_line(stat, columns, events, player_id, clock.game_id)
        usage = _usage_line(stat, columns, events, player_id, clock.game_id)
        rows.append(_scored_week(stat, clock, player_id, offense, None, usage, rules, notes))
    for clock in clocks:
        for team in (clock.home_team, clock.away_team):
            defense = _defense_line(events, team, clock)
            identity = {
                "player_display_name": f"{team} DST",
                "position": "DST",
                "team": team,
                "opponent_team": clock.opponent(team),
            }
            rows.append(
                _scored_week(
                    identity,
                    clock,
                    f"DEF:{team}",
                    None,
                    defense,
                    {name: None for name in _USAGE_FIELDS},
                    rules,
                    notes,
                )
            )
    return rows, notes


def build_snaps(
    rows: list[dict[str, object]],
    clocks: list[GameClock],
    xref_pfr: dict[str, str],
) -> tuple[list[dict[str, object]], Counter[str]]:
    notes: Counter[str] = Counter()
    by_id = {clock.game_id: clock for clock in clocks}
    stored: list[dict[str, object]] = []
    for row in rows:
        player_id = as_text(row.get("gsis_id")) or as_text(row.get("player_id"))
        if player_id is None:
            pfr = as_text(row.get("pfr_player_id"))
            player_id = xref_pfr.get(pfr or "")
        game_id = as_text(row.get("game_id"))
        clock = by_id.get(game_id or "")
        if player_id is None or clock is None:
            notes["snap_without_player_or_game"] += 1
            continue
        stored.append(
            {
                "player_id": player_id,
                "week": clock.week,
                "season_type": clock.season_type,
                "game_id": clock.game_id,
                "team": as_text(row.get("team")) or "",
                "opponent": (
                    as_text(row.get("opponent")) or clock.opponent(as_text(row.get("team")) or "")
                ),
                "offense_snaps": as_int(row.get("offense_snaps")) or 0,
                "defense_snaps": as_int(row.get("defense_snaps")) or 0,
                "st_snaps": as_int(row.get("st_snaps")) or 0,
                "offense_pct": as_float(row.get("offense_pct")),
                "defense_pct": as_float(row.get("defense_pct")),
                "st_pct": as_float(row.get("st_pct")),
                "known_as_of": shift(clock.kickoff, SNAPS_LAG),
                "known_as_of_method": "kickoff_plus_24h",
                "ingested_at": "",
                "source": SOURCE_NAME,
                "row_hash": "",
                "season": clock.season,
            }
        )
    return stored, notes


def build_injuries(
    rows: list[dict[str, object]],
    clocks: list[GameClock],
) -> tuple[list[dict[str, object]], Counter[str]]:
    notes: Counter[str] = Counter()
    index = _team_week_index(clocks, notes)
    stored: list[dict[str, object]] = []
    for row in rows:
        player_id = as_text(row.get("gsis_id")) or as_text(row.get("player_id"))
        team = as_text(row.get("team"))
        week = as_int(row.get("week"))
        if player_id is None or team is None or week is None:
            notes["injury_missing_identity"] += 1
            continue
        season = as_int(row.get("season"))
        kind = season_type_of(row)
        clock = _lookup_clock(index, season, week, kind, team)
        if clock is None:
            notes["injury_without_game"] += 1
            continue
        known = parse_report_time(row.get("date_modified"))
        method = "report_timestamp"
        if known is None:
            known = parse_report_time(row.get("report_date"))
        if known is None:
            stamped = clock.kickoff
            method = "kickoff_proxy_missing_report_time"
            notes[method] += 1
        else:
            stamped, method = known
            if method != "report_timestamp":
                notes[method] += 1
        stored.append(
            {
                "player_id": player_id,
                "week": clock.week,
                "season_type": clock.season_type,
                "team": team,
                "game_id": clock.game_id,
                "report_status": as_text(row.get("report_status")),
                "practice_status": _practice_status(row),
                "report_primary_injury": as_text(row.get("report_primary_injury")),
                "known_as_of": format_utc(stamped),
                "known_as_of_method": method,
                "ingested_at": "",
                "source": SOURCE_NAME,
                "row_hash": "",
                "season": clock.season,
            }
        )
    return stored, notes


def build_depth_charts(
    rows: list[dict[str, object]],
    clocks: list[GameClock],
    season: int,
) -> tuple[list[dict[str, object]], Counter[str]]:
    notes: Counter[str] = Counter()
    index = _team_week_index(clocks, notes)
    week_anchor = _week_anchor(clocks)
    stored: list[dict[str, object]] = []
    for row in rows:
        player_id = as_text(row.get("gsis_id")) or as_text(row.get("player_id"))
        team = as_text(row.get("team")) or as_text(row.get("club_code"))
        position = (
            as_text(row.get("depth_position"))
            or as_text(row.get("pos_abb"))
            or as_text(row.get("position"))
        )
        if player_id is None or team is None or position is None:
            notes["depth_missing_identity"] += 1
            continue
        published = parse_report_time(row.get("dt"))
        week = as_int(row.get("week"))
        row_season = as_int(row.get("season")) or season
        kind = season_type_of(row)
        clock = _lookup_clock(index, row_season, week, kind, team) if week is not None else None
        if published is not None:
            stamped, parsed_method = published
            method = "snapshot_timestamp" if parsed_method == "report_timestamp" else parsed_method
        elif clock is not None:
            stamped = day_before_gameday(clock.gameday)
            method = "day_before_gameday_2359_et"
            notes[method] += 1
        else:
            anchor = None
            if week is not None:
                anchor = week_anchor.get((row_season, week, kind)) or week_anchor.get(
                    (row_season, week, "")
                )
            if anchor is None:
                notes["depth_without_clock"] += 1
                continue
            stamped = day_before_gameday(anchor)
            method = "day_before_week_anchor_2359_et"
            notes[method] += 1
        stored.append(
            {
                "player_id": player_id,
                "week": week,
                "season_type": kind or (clock.season_type if clock else None),
                "team": team,
                "position": position,
                "depth_rank": as_int(row.get("depth_team")) or as_int(row.get("pos_rank")),
                "formation": as_text(row.get("formation")),
                "known_as_of": format_utc(stamped),
                "known_as_of_method": method,
                "ingested_at": "",
                "source": SOURCE_NAME,
                "row_hash": "",
                "season": row_season,
            }
        )
    return stored, notes


def build_rosters(
    rows: list[dict[str, object]],
    clocks: list[GameClock],
) -> tuple[list[dict[str, object]], Counter[str]]:
    notes: Counter[str] = Counter()
    index = _team_week_index(clocks, notes)
    week_anchor = _week_anchor(clocks)
    stored: list[dict[str, object]] = []
    for row in rows:
        player_id = as_text(row.get("gsis_id")) or as_text(row.get("player_id"))
        team = as_text(row.get("team"))
        week = as_int(row.get("week"))
        season = as_int(row.get("season"))
        if player_id is None or team is None or week is None or season is None:
            notes["roster_missing_identity"] += 1
            continue
        kind = season_type_of(row)
        clock = _lookup_clock(index, season, week, kind, team)
        if clock is not None:
            stamped = day_before_gameday(clock.gameday)
            method = "day_before_gameday_2359_et"
        else:
            anchor = week_anchor.get((season, week, kind)) or week_anchor.get((season, week, ""))
            if anchor is None:
                notes["roster_without_week_anchor"] += 1
                continue
            stamped = day_before_gameday(anchor)
            method = "day_before_week_anchor_2359_et"
        notes[method] += 1
        stored.append(
            {
                "player_id": player_id,
                "week": week,
                "season_type": kind or (clock.season_type if clock else ""),
                "team": team,
                "position": as_text(row.get("position")),
                "status": as_text(row.get("status")),
                "known_as_of": format_utc(stamped),
                "known_as_of_method": method,
                "ingested_at": "",
                "source": SOURCE_NAME,
                "row_hash": "",
                "season": season,
            }
        )
    return stored, notes


def external_links(rows: list[dict[str, object]]) -> list[tuple[str, str, str]]:
    """NFLverse-supplied ids. Names are not matched here."""

    links: list[tuple[str, str, str]] = []
    for row in rows:
        player_id = as_text(row.get("gsis_id")) or as_text(row.get("player_id"))
        if not player_id or player_id.startswith("DEF:"):
            continue
        for system in _XREF_FIELDS:
            external = as_text(row.get(system))
            if external:
                links.append((system.removesuffix("_id"), external, player_id))
    return links


def _first_year(row: dict[str, object]) -> int | None:
    for key in ("entry_year", "rookie_year", "rookie_season"):
        year = as_int(row.get(key))
        if year is not None:
            return year
    return None


def _identity_clock(entry_year: int | None) -> tuple[str, str]:
    if entry_year is None:
        return "1970-01-01T00:00:00Z", "crosswalk_undated"
    return format_utc(season_open(entry_year)), "entry_year_sept_1_et"


def _offense_line(
    stat: dict[str, object],
    columns: set[str],
    events: PbpExtract,
    player_id: str,
    game_id: str,
) -> dict[str, float | None]:
    counts = events.player_counts.get((player_id, game_id), {})
    line: dict[str, float | None] = {
        "passing_yards": _box(stat, columns, "passing_yards"),
        "passing_tds": _box(stat, columns, "passing_tds"),
        "interceptions": _interceptions(stat, columns),
        "rushing_yards": _box(stat, columns, "rushing_yards"),
        "rushing_tds": _box(stat, columns, "rushing_tds"),
        "receptions": _box(stat, columns, "receptions"),
        "receiving_yards": _box(stat, columns, "receiving_yards"),
        "receiving_tds": _box(stat, columns, "receiving_tds"),
        "fumbles_lost": _sum_columns(stat, columns, _FUMBLE_COLUMNS, default=0.0),
        "two_point": _sum_columns(stat, columns, _TWO_POINT_COLUMNS, default=0.0),
        "return_yards": _sum_columns(stat, columns, _RETURN_YARD_COLUMNS, default=None),
        "extra_points_made": _optional_box(stat, columns, "pat_made"),
        "field_goal_yards_made": _optional_box(stat, columns, "fg_made_distance"),
    }
    for name in (
        "passing_tds_40_plus",
        "rushing_tds_40_plus",
        "receiving_tds_40_plus",
        "return_tds",
        "offensive_fumble_return_tds",
    ):
        if name in events.player_available:
            line[name] = float(counts.get(name, 0.0))
        else:
            line[name] = None
    if line["field_goal_yards_made"] is None and "field_goal_yards_made" in events.player_available:
        line["field_goal_yards_made"] = float(counts.get("field_goal_yards_made", 0.0))
    if line["extra_points_made"] is None and "extra_points_made" in events.player_available:
        line["extra_points_made"] = float(counts.get("extra_points_made", 0.0))
    if line["return_yards"] is None and "return_tds" in events.player_available:
        line["return_yards"] = 0.0 if counts.get("return_tds", 0.0) == 0 else None
    return line


def _defense_line(events: PbpExtract, team: str, clock: GameClock) -> dict[str, float | None]:
    counts = events.team_counts.get((team, clock.game_id), {})
    line: dict[str, float | None] = {}
    for name in _DEFENSE_STATS:
        if name not in events.team_available:
            line[name] = None
            continue
        if name == "points_allowed":
            value = counts.get("points_allowed")
            line[name] = None if value is None else float(value)
            continue
        line[name] = float(counts.get(name, 0.0))
    return line


def _usage_line(
    stat: dict[str, object],
    columns: set[str],
    events: PbpExtract,
    player_id: str,
    game_id: str,
) -> dict[str, float | None]:
    line: dict[str, float | None] = {}
    for dest, source in _WEEKLY_USAGE:
        line[dest] = _optional_box(stat, columns, source)
    counts = events.player_counts.get((player_id, game_id), {})
    for name in _PBP_USAGE:
        if name in events.player_available:
            line[name] = float(counts.get(name, 0.0))
        else:
            line[name] = None
    return line


def _scored_week(
    stat: dict[str, object],
    clock: GameClock,
    player_id: str,
    offense: dict[str, float | None] | None,
    defense: dict[str, float | None] | None,
    usage: dict[str, float | None],
    rules: LeagueScoring,
    notes: Counter[str],
) -> dict[str, object]:
    team = as_text(stat.get("team")) or ""
    opponent = as_text(stat.get("opponent_team")) or clock.opponent(team) or ""
    display = (
        as_text(stat.get("player_display_name"))
        or as_text(stat.get("player_name"))
        or as_text(stat.get("full_name"))
        or player_id
    )
    row: dict[str, object] = {
        "player_id": player_id,
        "display_name": display,
        "position": as_text(stat.get("position")),
        "week": clock.week,
        "season_type": clock.season_type,
        "game_id": clock.game_id,
        "team": team,
        "opponent": opponent,
        "opponent_score": None if defense is None else clock.score(opponent) if opponent else None,
        "known_as_of": shift(clock.kickoff, STATS_LAG),
        "known_as_of_method": "kickoff_plus_6h",
        "ingested_at": "",
        "source": SOURCE_NAME,
        "row_hash": "",
        "season": clock.season,
        "scoring_version": SCORING_VERSION,
    }
    for name in _USAGE_FIELDS:
        row[name] = usage.get(name)
    for name in _OFFENSE_STATS:
        row[name] = None if offense is None else offense.get(name)
    for name in _DEFENSE_STATS:
        if offense is not None and name in _OFFENSE_STATS:
            continue
        row[name] = None if defense is None else defense.get(name)
    stat_line = _present(offense if offense is not None else defense or {})
    try:
        result = (
            score_offense(stat_line, rules)
            if offense is not None
            else score_defense(stat_line, rules)
        )
    except ScoringError as exc:
        raise ScoringError(f"{player_id} {clock.game_id}: {exc}") from exc
    row["fantasy_points"] = result.fantasy_points
    row["touchdown_points"] = result.touchdown_points
    row["unavailable"] = ",".join(result.unavailable)
    row["score_complete"] = result.complete
    if not result.complete:
        notes["player_week_incomplete"] += 1
    return row


def _present(values: dict[str, float | None]) -> dict[str, float]:
    return {key: value for key, value in values.items() if value is not None}


def _box(stat: dict[str, object], columns: set[str], name: str) -> float:
    if name not in columns:
        return 0.0
    return as_float(stat.get(name)) or 0.0


def _optional_box(stat: dict[str, object], columns: set[str], name: str) -> float | None:
    if name not in columns:
        return None
    return as_float(stat.get(name)) or 0.0


def _interceptions(stat: dict[str, object], columns: set[str]) -> float:
    if "passing_interceptions" in columns:
        return as_float(stat.get("passing_interceptions")) or 0.0
    if "interceptions" in columns:
        return as_float(stat.get("interceptions")) or 0.0
    return 0.0


def _sum_columns(
    stat: dict[str, object],
    columns: set[str],
    names: tuple[str, ...],
    *,
    default: float | None,
) -> float | None:
    present = [name for name in names if name in columns]
    if not present:
        return default
    return float(sum(as_float(stat.get(name)) or 0.0 for name in present))


def _practice_status(row: dict[str, object]) -> str | None:
    direct = as_text(row.get("practice_status"))
    if direct:
        return direct
    parts = []
    for key in (
        "practice_primary_injury",
        "practice_status_wed",
        "practice_status_thu",
        "practice_status_fri",
    ):
        value = as_text(row.get(key))
        if value:
            parts.append(value)
    return ";".join(parts) or None


def _team_week_index(
    clocks: list[GameClock],
    notes: Counter[str],
) -> dict[tuple[int, int, str, str], GameClock]:
    found: dict[tuple[int, int, str, str], list[GameClock]] = defaultdict(list)
    for clock in clocks:
        for team in (clock.home_team, clock.away_team):
            found[(clock.season, clock.week, clock.season_type, team)].append(clock)
            found[(clock.season, clock.week, "", team)].append(clock)
    index: dict[tuple[int, int, str, str], GameClock] = {}
    for key, matches in found.items():
        unique = {item.game_id: item for item in matches}
        if len(unique) > 1 and key[2] != "":
            notes["duplicate_team_week"] += 1
        index[key] = sorted(unique.values(), key=lambda item: item.kickoff)[0]
    return index


def _lookup_clock(
    index: dict[tuple[int, int, str, str], GameClock],
    season: int | None,
    week: int | None,
    kind: str,
    team: str,
) -> GameClock | None:
    if season is None or week is None:
        return None
    return index.get((season, week, kind, team)) or index.get((season, week, "", team))


def _week_anchor(clocks: list[GameClock]) -> dict[tuple[int, int, str], str]:
    anchors: dict[tuple[int, int, str], str] = {}
    for clock in clocks:
        for key in ((clock.season, clock.week, clock.season_type), (clock.season, clock.week, "")):
            current = anchors.get(key)
            if current is None or clock.gameday < current:
                anchors[key] = clock.gameday
    return anchors
