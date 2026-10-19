"""Thursday Yahoo capture.

With fixtures, nothing touches the network. Without ``YAHOO_CLIENT_ID`` and
with ``allow_missing_secrets``, the command exits 0 and writes nothing.
Snapshots go under ``yahoo/season=<year>/week=<week>/captured_at=<stamp>/``.
Real responses belong in the private repo, not in this public one.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from gridiron_lab.config import AppConfig
from gridiron_lab.data.clock import format_utc
from gridiron_lab.data.store import ParquetStore
from gridiron_lab.logging import get_logger
from gridiron_lab.yahoo.auth import CLIENT_ID_ENV, credentials_from_env, token_file_path
from gridiron_lab.yahoo.client import YahooClient
from gridiron_lab.yahoo.errors import YahooConfigError, YahooError
from gridiron_lab.yahoo.http import Transport
from gridiron_lab.yahoo.map import dashboard_roster, store_rows
from gridiron_lab.yahoo.parse import (
    YahooTeam,
    YahooTransaction,
    current_week,
    game_season,
    league_season,
    load_document,
    matchups_from,
    projections_from,
    stat_modifiers,
    teams_from,
    transactions_from,
)
from gridiron_lab.yahoo.reconcile import reconcile_reports

logger = get_logger("yahoo.capture")

Clock = Callable[[], datetime]


def capture(
    *,
    config: AppConfig,
    output: Path,
    data_dir: Path | None = None,
    fixture_dir: Path | None = None,
    week: int | None = None,
    season: int | None = None,
    allow_missing_secrets: bool = False,
    clock: Clock | None = None,
    transport: Transport | None = None,
    token_file: Path | None = None,
) -> int:
    """Snapshot one week. Return 0 on success or an intentional no-op."""

    if fixture_dir is None and not os.environ.get(CLIENT_ID_ENV, "").strip():
        if allow_missing_secrets:
            logger.warning(
                "%s is unset; Yahoo capture is a no-op until credentials exist",
                CLIENT_ID_ENV,
            )
            return 0
        raise YahooConfigError(f"{CLIENT_ID_ENV} is unset")

    now = (clock or _utc_now)()
    known_as_of = format_utc(now)
    destination = token_file if token_file is not None else token_file_path()
    bundle = _load(config, fixture_dir, week, transport, destination)
    resolved_week = week if week is not None else bundle.week
    if resolved_week is None:
        raise YahooError("Yahoo settings did not include a current week")
    resolved_season = season if season is not None else bundle.season
    if resolved_season is None:
        raise YahooError("Yahoo settings did not include a season")

    teams = _merge_teams(bundle.directory, bundle.roster_teams)
    opponents = _opponents(bundle.matchups)
    xref = _load_xref(fixture_dir)
    rows = store_rows(
        teams,
        season=resolved_season,
        week=resolved_week,
        known_as_of=known_as_of,
        xref=xref,
    )
    roster_doc = dashboard_roster(
        teams,
        opponents,
        bundle.projections,
        season=resolved_season,
        week=resolved_week,
        known_as_of=known_as_of,
        xref=xref,
        example=fixture_dir is not None,
    )
    past_count = len(bundle.past_projections)
    reports = reconcile_reports(
        bundle.modifiers,
        config.scoring,
        known_as_of=known_as_of,
        past_week=resolved_week - 1 if resolved_week > 1 else None,
        past_projection_count=past_count,
        past_from_fixture=fixture_dir is not None,
    )
    snapshot = (
        output
        / "yahoo"
        / f"season={resolved_season}"
        / f"week={resolved_week}"
        / f"captured_at={known_as_of.replace(':', '')}"
    )
    _write_snapshot(
        snapshot,
        bundle,
        roster_doc,
        reports,
        rows,
        known_as_of=known_as_of,
    )
    if data_dir is not None:
        store = ParquetStore(data_dir, clock=lambda: now)
        for table, table_rows in rows.items():
            written = store.append(table, table_rows)
            logger.info("Yahoo store %s appended %s", table, written)
    _write_json(
        output / "refresh_rotation.json",
        {"rotated": bundle.rotated, "secret_name": "YAHOO_REFRESH_TOKEN"},
    )
    matched = len(rows["player_xref"])
    unmatched = len(rows["identity_issues"])
    logger.info(
        "Yahoo capture season %s week %s · teams %s · matched %s · unmatched %s",
        resolved_season,
        resolved_week,
        len(teams),
        matched,
        unmatched,
    )
    return 0


class _Bundle:
    def __init__(
        self,
        *,
        game: object,
        settings: object,
        directory_doc: object,
        rosters_doc: object,
        directory: tuple[YahooTeam, ...],
        roster_teams: tuple[YahooTeam, ...],
        scoreboard: object,
        transactions: object,
        projections_doc: object,
        past_doc: object,
        week: int | None,
        season: int | None,
        modifiers: tuple,
        projections: dict[str, float],
        past_projections: dict[str, float],
        matchups: tuple,
        transactions_parsed: tuple[YahooTransaction, ...],
        rotated: bool,
    ) -> None:
        self.game = game
        self.settings = settings
        self.directory_doc = directory_doc
        self.rosters_doc = rosters_doc
        self.directory = directory
        self.roster_teams = roster_teams
        self.scoreboard = scoreboard
        self.transactions = transactions
        self.projections_doc = projections_doc
        self.past_doc = past_doc
        self.week = week
        self.season = season
        self.modifiers = modifiers
        self.projections = projections
        self.past_projections = past_projections
        self.matchups = matchups
        self.transactions_parsed = transactions_parsed
        self.rotated = rotated


def _load(
    config: AppConfig,
    fixture_dir: Path | None,
    week: int | None,
    transport: Transport | None,
    token_file: Path,
) -> _Bundle:
    if fixture_dir is not None:
        return _load_fixtures(fixture_dir)
    client = YahooClient(
        credentials_from_env(),
        transport=transport,
        token_file=token_file,
        league_id=config.league.yahoo_id,
    )
    game = client.get_json("game/nfl")
    settings = client.league_settings()
    directory_doc = client.teams()
    resolved_week = week if week is not None else current_week(settings)
    if resolved_week is None:
        raise YahooError("Yahoo settings did not include a current week")
    rosters_doc = client.rosters(resolved_week)
    scoreboard = client.scoreboard(resolved_week)
    transactions = client.transactions()
    roster_teams = teams_from(rosters_doc)
    keys = [player.player_key for team in roster_teams for player in team.players]
    projections_doc = client.projections(resolved_week, keys)
    past_doc: object = {"fantasy_content": {"league": {"players": []}}}
    if resolved_week > 1:
        past_doc = client.projections(resolved_week - 1, keys)
    return _bundle_from_documents(
        game=game,
        settings=settings,
        directory_doc=directory_doc,
        rosters_doc=rosters_doc,
        scoreboard=scoreboard,
        transactions=transactions,
        projections_doc=projections_doc,
        past_doc=past_doc,
        rotated=client.refresh_rotated,
    )


def _load_fixtures(fixture_dir: Path) -> _Bundle:
    def read(name: str) -> object:
        path = fixture_dir / name
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise YahooError(f"missing fixture {name}") from exc
        return load_document(text.encode("utf-8"))

    return _bundle_from_documents(
        game=read("game.json"),
        settings=read("league_settings.json"),
        directory_doc=read("teams.json"),
        rosters_doc=read("rosters.json"),
        scoreboard=read("scoreboard.json"),
        transactions=read("transactions.json"),
        projections_doc=read("projections.json"),
        past_doc=read("projections_past.json"),
        rotated=False,
    )


def _bundle_from_documents(
    *,
    game: object,
    settings: object,
    directory_doc: object,
    rosters_doc: object,
    scoreboard: object,
    transactions: object,
    projections_doc: object,
    past_doc: object,
    rotated: bool,
) -> _Bundle:
    season = league_season(settings) or game_season(game)
    return _Bundle(
        game=game,
        settings=settings,
        directory_doc=directory_doc,
        rosters_doc=rosters_doc,
        directory=teams_from(directory_doc),
        roster_teams=teams_from(rosters_doc),
        scoreboard=scoreboard,
        transactions=transactions,
        projections_doc=projections_doc,
        past_doc=past_doc,
        week=current_week(settings),
        season=season,
        modifiers=stat_modifiers(settings),
        projections=projections_from(projections_doc),
        past_projections=projections_from(past_doc),
        matchups=matchups_from(scoreboard),
        transactions_parsed=transactions_from(transactions),
        rotated=rotated,
    )


def _merge_teams(
    directory: tuple[YahooTeam, ...],
    roster_teams: tuple[YahooTeam, ...],
) -> list[YahooTeam]:
    by_id = {team.team_id: team for team in directory}
    merged: list[YahooTeam] = []
    seen: set[int] = set()
    for roster_team in roster_teams:
        base = by_id.get(roster_team.team_id)
        seen.add(roster_team.team_id)
        if base is None:
            merged.append(roster_team)
            continue
        merged.append(
            replace(
                base,
                players=roster_team.players,
                manager=base.manager or roster_team.manager,
                wins=base.wins,
                losses=base.losses,
                name=base.name or roster_team.name,
            )
        )
    for team in directory:
        if team.team_id not in seen:
            merged.append(team)
    return merged


def _opponents(matchups: tuple) -> dict[int, int]:
    opponents: dict[int, int] = {}
    for matchup in matchups:
        first, second = matchup.team_ids
        opponents[first] = second
        opponents[second] = first
    return opponents


def _load_xref(fixture_dir: Path | None) -> Mapping[str, str]:
    if fixture_dir is None:
        return {}
    path = fixture_dir / "xref.json"
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise YahooError("xref.json was not JSON") from exc
    if not isinstance(payload, dict):
        raise YahooError("xref.json must be an object of player_key to player_id")
    xref: dict[str, str] = {}
    for key, value in payload.items():
        if not isinstance(key, str) or not isinstance(value, str) or not value:
            raise YahooError("xref.json values must be non-empty strings")
        xref[key] = value
    return xref


def _write_snapshot(
    snapshot: Path,
    bundle: _Bundle,
    roster_doc: dict[str, object],
    reports: dict[str, object],
    rows: dict[str, list[dict[str, object]]],
    *,
    known_as_of: str,
) -> None:
    snapshot.mkdir(parents=True, exist_ok=True)
    _write_json(snapshot / "game.json", bundle.game)
    _write_json(snapshot / "league_settings.json", bundle.settings)
    _write_json(snapshot / "teams.json", bundle.directory_doc)
    _write_json(snapshot / "rosters.json", bundle.rosters_doc)
    _write_json(snapshot / "scoreboard.json", bundle.scoreboard)
    _write_json(snapshot / "transactions.json", bundle.transactions)
    _write_json(snapshot / "projections.json", bundle.projections_doc)
    _write_json(snapshot / "projections_past.json", bundle.past_doc)
    _write_json(snapshot / "roster_for_dashboard.json", roster_doc)
    _write_json(snapshot / "reconciliation.json", reports)
    _write_json(snapshot / "lines.json", reports["lines"])
    _write_json(
        snapshot / "transactions_parsed.json",
        [_transaction_dict(item) for item in bundle.transactions_parsed],
    )
    _write_json(
        snapshot / "manifest.json",
        {
            "known_as_of": known_as_of,
            "known_as_of_method": "yahoo_capture",
            "source": "yahoo",
            "matched": len(rows["player_xref"]),
            "unmatched": len(rows["identity_issues"]),
            "roster_rows": len(rows["rosters"]),
            "lines_status": "not_captured",
        },
    )


def _transaction_dict(item: YahooTransaction) -> dict[str, object]:
    return {
        "transaction_key": item.transaction_key,
        "type": item.kind,
        "status": item.status,
        "timestamp": item.timestamp,
        "player_keys": list(item.player_keys),
        "destination_team_id": item.destination_team_id,
    }


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _utc_now() -> datetime:
    return datetime.now(UTC)
