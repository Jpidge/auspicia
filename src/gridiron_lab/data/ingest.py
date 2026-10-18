"""Load one or more seasons into the append-only store.

The fetch implementation is injected. The CLI passes ``HttpFetch``; tests
pass ``FixtureFetch``. Re-running the same season appends no duplicate rows.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from gridiron_lab.data.clock import format_utc, season_open
from gridiron_lab.data.fetch import SourceFetch
from gridiron_lab.data.normalize import (
    build_depth_charts,
    build_games,
    build_identity,
    build_injuries,
    build_player_weeks,
    build_rosters,
    build_snaps,
    dst_players,
)
from gridiron_lab.data.pbp import extract_events
from gridiron_lab.data.schema import SOURCE_NAME
from gridiron_lab.data.store import ParquetStore
from gridiron_lab.logging import get_logger
from gridiron_lab.scoring import LeagueScoring, ScoringError, default_rules

logger = get_logger("data.ingest")


@dataclass
class SourceReport:
    source: str
    rows: int
    appended: int


@dataclass
class IngestReport:
    season: int
    sources: list[SourceReport] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    table_counts: dict[str, int] = field(default_factory=dict)


def ingest(
    *,
    seasons: list[int],
    data_dir: Path,
    fetch: SourceFetch,
    rules: LeagueScoring | None = None,
) -> list[IngestReport]:
    """Ingest each season, then rebuild DuckDB once."""

    scoring = rules if rules is not None else default_rules()
    store = ParquetStore(data_dir)
    reports: list[IngestReport] = []
    for season in seasons:
        reports.append(_ingest_season(season, fetch, store, scoring))
    counts = store.rebuild()
    for report in reports:
        report.table_counts = dict(counts)
    return reports


def _ingest_season(
    season: int,
    fetch: SourceFetch,
    store: ParquetStore,
    rules: LeagueScoring,
) -> IngestReport:
    report = IngestReport(season=season)
    schedules = fetch.load("schedules", season)
    game_rows, clocks, game_notes = build_games(schedules, season)
    _stage(report, store, "schedules", len(schedules), "games", game_rows, game_notes)

    player_rows = fetch.load("players", season)
    roster_rows = fetch.load("rosters", season)
    players, xref, issues, identity_notes = build_identity(player_rows, roster_rows)
    players.extend(dst_players(clocks))
    _stage(report, store, "players", len(player_rows), "players", players, identity_notes)
    _stage(report, store, "player_xref", len(xref), "player_xref", xref, Counter())
    _stage(report, store, "identity_issues", len(issues), "identity_issues", issues, Counter())

    plays = fetch.load("pbp", season)
    extracted = extract_events(plays, clocks)
    _stage(report, store, "pbp", len(plays), "pbp_events", extracted.rows, Counter(extracted.notes))

    stats = fetch.load("player_stats", season)
    try:
        weeks, week_notes = build_player_weeks(stats, clocks, extracted, rules)
    except ScoringError as exc:
        logger.error("Scoring failed during season %s: %s", season, exc)
        raise
    _stage(report, store, "player_stats", len(stats), "player_weeks", weeks, week_notes)
    _remember_players(store, report, weeks)

    snaps = fetch.load("snap_counts", season)
    pfr = {
        str(row["external_id"]): str(row["player_id"])
        for row in xref
        if row["source_system"] == "pfr"
    }
    snap_rows, snap_notes = build_snaps(snaps, clocks, pfr)
    _stage(report, store, "snap_counts", len(snaps), "snap_counts", snap_rows, snap_notes)

    injuries = fetch.load("injuries", season)
    injury_rows, injury_notes = build_injuries(injuries, clocks)
    _stage(report, store, "injuries", len(injuries), "injuries", injury_rows, injury_notes)

    charts = fetch.load("depth_charts", season)
    depth_rows, depth_notes = build_depth_charts(charts, clocks, season)
    _stage(report, store, "depth_charts", len(charts), "depth_charts", depth_rows, depth_notes)

    roster_built, roster_notes = build_rosters(roster_rows, clocks)
    _stage(report, store, "rosters", len(roster_rows), "rosters", roster_built, roster_notes)
    return report


def _stage(
    report: IngestReport,
    store: ParquetStore,
    source: str,
    read: int,
    table: str,
    rows: list[dict[str, object]],
    notes: Counter[str],
) -> None:
    appended = store.append(table, rows)
    report.sources.append(SourceReport(source, read, appended))
    logger.info(
        "Ingest season %s · source %s · rows %s · appended %s",
        report.season,
        source,
        read,
        appended,
    )
    for code, count in sorted(notes.items()):
        if count <= 0:
            continue
        message = f"{code} · rows {count}"
        report.warnings.append(message)
        logger.warning("Ingest season %s · %s", report.season, message)


def _remember_players(
    store: ParquetStore,
    report: IngestReport,
    weeks: list[dict[str, object]],
) -> None:
    """Add a dimension row when a weekly stat has no crosswalk record."""

    known = {str(value) for value in store.column_values("players", "player_id")}
    extra: list[dict[str, object]] = []
    for week in weeks:
        player_id = str(week["player_id"])
        if player_id in known:
            continue
        known.add(player_id)
        extra.append(
            {
                "player_id": player_id,
                "display_name": week.get("display_name") or player_id,
                "entry_year": None,
                "known_as_of": format_utc(season_open(int(week["season"]))),
                "known_as_of_method": "season_open_proxy",
                "ingested_at": "",
                "source": SOURCE_NAME,
                "row_hash": "",
                "season": 0,
            }
        )
    if not extra:
        return
    appended = store.append("players", extra)
    logger.info(
        "Ingest season %s · source unlisted_players · rows %s · appended %s",
        report.season,
        len(extra),
        appended,
    )
