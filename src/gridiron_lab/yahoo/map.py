"""Map a Yahoo capture onto the point-in-time store and the dashboard roster.

``player_id`` stays a GSIS id when a crosswalk supplies one. A DST with an
editorial team abbreviation maps to ``DEF:<TEAM>``. Everyone else is an
``identity_issues`` row and keeps the Yahoo player key on the dashboard.
The Parquet ``rosters`` table has no fantasy-team column, so ``team`` there
is the NFL abbreviation. Fantasy team, manager, and opponent live in the
dashboard roster document.
"""

from __future__ import annotations

from collections.abc import Mapping

from gridiron_lab.data.schema import YAHOO_SOURCE
from gridiron_lab.lineup import LineupError, normalize_position
from gridiron_lab.yahoo.parse import YahooPlayer, YahooTeam

KNOWN_AS_OF_METHOD = "yahoo_capture"
INACTIVE = {"O", "OUT", "IR"}


def store_rows(
    teams: list[YahooTeam],
    *,
    season: int,
    week: int,
    known_as_of: str,
    xref: Mapping[str, str],
) -> dict[str, list[dict[str, object]]]:
    """Rows for ``player_xref``, ``identity_issues``, and ``rosters``."""

    xref_rows: list[dict[str, object]] = []
    issues: list[dict[str, object]] = []
    rosters: list[dict[str, object]] = []
    for team in teams:
        for player in team.players:
            player_id, method = _resolve(player, xref)
            if player_id is None or method is None:
                issues.append(
                    _meta(
                        known_as_of,
                        season=0,
                        source_system="yahoo",
                        external_id=player.player_key,
                        candidate_player_ids="",
                    )
                )
                continue
            xref_rows.append(
                _meta(
                    known_as_of,
                    season=0,
                    source_system="yahoo",
                    external_id=player.player_key,
                    player_id=player_id,
                    match_method=method,
                    match_confidence=1.0,
                )
            )
            if player.editorial_team:
                rosters.append(
                    _meta(
                        known_as_of,
                        season=season,
                        player_id=player_id,
                        week=week,
                        season_type="REG",
                        team=player.editorial_team,
                        position=_roster_position(player),
                        status=player.selected or player.status or "roster",
                    )
                )
    return {
        "player_xref": xref_rows,
        "identity_issues": issues,
        "rosters": rosters,
    }


def dashboard_roster(
    teams: list[YahooTeam],
    opponents: Mapping[int, int],
    projections: Mapping[str, float],
    *,
    season: int,
    week: int,
    known_as_of: str,
    xref: Mapping[str, str],
    example: bool,
) -> dict[str, object]:
    """Roster document accepted by ``export-dashboard``."""

    exported: list[dict[str, object]] = []
    for team in teams:
        exported.append(
            {
                "team_id": team.team_id,
                "name": team.name,
                "manager": team.manager,
                "wins": team.wins,
                "losses": team.losses,
                "opponent_id": opponents.get(team.team_id, 0),
                "players": [
                    _dashboard_player(player, week, projections, xref) for player in team.players
                ],
            }
        )
    return {
        "example": example,
        "source": "yahoo",
        "known_as_of": known_as_of,
        "known_as_of_method": KNOWN_AS_OF_METHOD,
        "season": season,
        "week": week,
        "kickoff_label": None,
        "countdown_label": None,
        "teams": exported,
        "waivers": [],
    }


def _dashboard_player(
    player: YahooPlayer,
    week: int,
    projections: Mapping[str, float],
    xref: Mapping[str, str],
) -> dict[str, object]:
    player_id, _method = _resolve(player, xref)
    injury, force_zero = _injury(player)
    yahoo = projections.get(player.player_key, player.projection)
    return {
        "player_id": player_id or player.player_key,
        "name": player.name,
        "position": _roster_position(player),
        "nfl_team": player.editorial_team or None,
        "opponent": None,
        "home": None,
        "kickoff": None,
        "injury": injury,
        "force_zero": force_zero,
        "bye": week in player.bye_weeks,
        "q10": None,
        "q50": None,
        "q90": None,
        "mean": None,
        "yahoo": yahoo,
        "notes": ["Yahoo capture. Model quantiles are not filled yet."],
    }


def _resolve(player: YahooPlayer, xref: Mapping[str, str]) -> tuple[str | None, str | None]:
    supplied = xref.get(player.player_key)
    if supplied:
        return supplied, "supplied_xref"
    if player.position.upper() in {"DEF", "DST"} and _team_abbr(player.editorial_team):
        return f"DEF:{player.editorial_team}", "editorial_team_abbr"
    return None, None


def _roster_position(player: YahooPlayer) -> str:
    try:
        return normalize_position(player.position)
    except LineupError:
        return player.position.upper()


def _injury(player: YahooPlayer) -> tuple[str | None, bool]:
    status = player.status.strip().upper()
    selected = player.selected.strip().upper()
    if selected == "IR" or status in INACTIVE:
        label = "IR" if selected == "IR" or status == "IR" else "OUT"
        return label, True
    if status in {"Q", "D", "DTD"}:
        return status, False
    return None, False


def _team_abbr(value: str) -> bool:
    return value.isalpha() and 2 <= len(value) <= 3


def _meta(known_as_of: str, **fields: object) -> dict[str, object]:
    row: dict[str, object] = {
        "known_as_of": known_as_of,
        "known_as_of_method": KNOWN_AS_OF_METHOD,
        "source": YAHOO_SOURCE,
    }
    row.update(fields)
    return row
