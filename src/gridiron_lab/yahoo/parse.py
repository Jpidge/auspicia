"""Parse Yahoo Fantasy JSON and XML into plain structures.

Yahoo's ``format=json`` encodes repeated XML elements as objects with
numeric keys and a ``count``. A single resource is often a list of
one-key objects. ``collapse`` turns that into dicts and lists. Fixtures in
this repo are fictional and follow that documented shape.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from dataclasses import dataclass

from gridiron_lab.yahoo.errors import YahooError


@dataclass(frozen=True)
class StatModifier:
    stat_id: int
    value: float


@dataclass(frozen=True)
class YahooPlayer:
    player_key: str
    yahoo_id: str
    name: str
    editorial_team: str
    position: str
    selected: str
    status: str
    bye_weeks: tuple[int, ...]
    projection: float | None


@dataclass(frozen=True)
class YahooTeam:
    team_key: str
    team_id: int
    name: str
    manager: str
    wins: int
    losses: int
    players: tuple[YahooPlayer, ...]


@dataclass(frozen=True)
class YahooMatchup:
    week: int
    team_ids: tuple[int, int]


@dataclass(frozen=True)
class YahooTransaction:
    transaction_key: str
    kind: str
    status: str
    timestamp: str
    player_keys: tuple[str, ...]
    destination_team_id: int | None


def collapse(node: object) -> object:
    """Flatten Yahoo's numeric maps and fragment lists."""

    if isinstance(node, list):
        parts = [collapse(item) for item in node]
        if parts and all(isinstance(part, dict) for part in parts):
            dicts = [part for part in parts if isinstance(part, dict)]
            keys = [key for part in dicts for key in part]
            unique = len(keys) == len(set(keys))
            single = [part for part in dicts if len(part) == 1]
            mixed_fragments = bool(single) and any(len(part) > 1 for part in dicts)
            if unique and (len(single) == len(dicts) or mixed_fragments):
                merged: dict[str, object] = {}
                for part in dicts:
                    merged.update(part)
                return merged
        if len(parts) == 1 and isinstance(parts[0], dict):
            return parts[0]
        return parts
    if isinstance(node, dict):
        keys = [key for key in node if key != "count"]
        if keys and all(str(key).isdigit() for key in keys):
            ordered = sorted(keys, key=lambda key: int(str(key)))
            return [collapse(node[key]) for key in ordered]
        return {str(key): collapse(value) for key, value in node.items() if key != "count"}
    return node


def load_json_document(text: str) -> object:
    try:
        return collapse(json.loads(text))
    except json.JSONDecodeError as exc:
        raise YahooError("Yahoo response was not JSON") from exc


def load_xml_document(text: str) -> object:
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise YahooError("Yahoo response was not XML") from exc
    return collapse(_xml_node(root))


def load_document(body: bytes) -> object:
    stripped = body.lstrip()
    if stripped.startswith(b"<"):
        try:
            return load_xml_document(body.decode("utf-8"))
        except UnicodeDecodeError as exc:
            raise YahooError("Yahoo XML was not UTF-8") from exc
    try:
        return load_json_document(body.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise YahooError("Yahoo JSON was not UTF-8") from exc


def fantasy_content(document: object) -> Mapping[str, object]:
    if not isinstance(document, dict):
        raise YahooError("Yahoo document was not an object")
    content = document.get("fantasy_content", document)
    if not isinstance(content, dict):
        raise YahooError("fantasy_content was not an object")
    return content


def league_view(document: object) -> Mapping[str, object]:
    content = fantasy_content(document)
    league = _as_mapping(content.get("league"))
    if league is not None:
        return league
    game = _as_mapping(content.get("game"))
    if game is not None:
        return game
    raise YahooError("Yahoo document has no league")


def game_key(document: object) -> str:
    content = fantasy_content(document)
    game = _as_mapping(content.get("game"))
    if game is None:
        raise YahooError("game response has no game object")
    key = game.get("game_key")
    if not isinstance(key, str) or not key:
        raise YahooError("game response has no game_key")
    return key


def game_season(document: object) -> int | None:
    content = fantasy_content(document)
    game = _as_mapping(content.get("game"))
    if game is None:
        return None
    return _optional_int(game.get("season"))


def stat_modifiers(document: object) -> tuple[StatModifier, ...]:
    league = league_view(document)
    settings = league.get("settings")
    if not isinstance(settings, dict):
        return ()
    raw = settings.get("stat_modifiers")
    found: list[StatModifier] = []
    for stat_id, value in _walk_stats(raw):
        found.append(StatModifier(stat_id, value))
    return tuple(found)


def current_week(document: object) -> int | None:
    league = league_view(document)
    return _optional_int(league.get("current_week"))


def league_season(document: object) -> int | None:
    league = league_view(document)
    return _optional_int(league.get("season"))


def teams_from(document: object) -> tuple[YahooTeam, ...]:
    league = league_view(document)
    raw_teams = league.get("teams")
    if not isinstance(raw_teams, list):
        return ()
    teams: list[YahooTeam] = []
    for raw in raw_teams:
        if isinstance(raw, dict):
            teams.append(_team(raw))
    return tuple(teams)


def matchups_from(document: object) -> tuple[YahooMatchup, ...]:
    league = league_view(document)
    board = league.get("scoreboard")
    if not isinstance(board, dict):
        return ()
    raw = board.get("matchups")
    if not isinstance(raw, list):
        return ()
    week = _optional_int(board.get("week")) or 0
    found: list[YahooMatchup] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        matchup = item.get("matchup", item)
        if not isinstance(matchup, dict):
            continue
        ids = _team_ids(matchup.get("teams"))
        if len(ids) == 2:
            found.append(YahooMatchup(_optional_int(matchup.get("week")) or week, (ids[0], ids[1])))
    return tuple(found)


def transactions_from(document: object) -> tuple[YahooTransaction, ...]:
    league = league_view(document)
    raw = league.get("transactions")
    if not isinstance(raw, list):
        return ()
    found: list[YahooTransaction] = []
    for item in raw:
        if isinstance(item, dict):
            found.append(_transaction(item))
    return tuple(found)


def projections_from(document: object) -> dict[str, float]:
    """Map player_key to Yahoo's points total for the requested coverage.

    An empty stats list and a missing total contribute nothing. That is how
    a past-week fixture records "no projection was served".
    """

    league = league_view(document)
    raw = league.get("players")
    if not isinstance(raw, list):
        return {}
    points: dict[str, float] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        player = item.get("player", item)
        if not isinstance(player, dict):
            continue
        key = player.get("player_key")
        total = _projection_total(player)
        if isinstance(key, str) and key and total is not None:
            points[key] = total
    return points


def _projection_total(player: Mapping[str, object]) -> float | None:
    block = player.get("player_points")
    if not isinstance(block, dict):
        return None
    stats = player.get("player_stats")
    if isinstance(stats, dict):
        raw_stats = stats.get("stats")
        if isinstance(raw_stats, list) and not raw_stats:
            return None
    total = block.get("total")
    if isinstance(total, str):
        try:
            return float(total)
        except ValueError:
            return None
    if isinstance(total, (int, float)) and not isinstance(total, bool):
        return float(total)
    return None


def _team(raw: Mapping[str, object]) -> YahooTeam:
    team = raw.get("team", raw)
    if not isinstance(team, dict):
        raise YahooError("team entry was not an object")
    team_id = _optional_int(team.get("team_id"))
    team_key = team.get("team_key")
    name = team.get("name")
    if team_id is None or not isinstance(team_key, str) or not isinstance(name, str):
        raise YahooError("team is missing team_id, team_key, or name")
    wins, losses = _record(team.get("team_standings"))
    players = _players(team.get("roster"))
    return YahooTeam(
        team_key=team_key,
        team_id=team_id,
        name=name,
        manager=_manager_name(team.get("managers")),
        wins=wins,
        losses=losses,
        players=players,
    )


def _record(standings: object) -> tuple[int, int]:
    if not isinstance(standings, dict):
        return (0, 0)
    totals = standings.get("outcome_totals")
    if not isinstance(totals, dict):
        return (0, 0)
    return (_optional_int(totals.get("wins")) or 0, _optional_int(totals.get("losses")) or 0)


def _manager_name(managers: object) -> str:
    if isinstance(managers, dict):
        managers = [managers]
    if not isinstance(managers, list):
        return ""
    for item in managers:
        if not isinstance(item, dict):
            continue
        manager = item.get("manager", item)
        if not isinstance(manager, dict):
            continue
        nickname = manager.get("nickname")
        if isinstance(nickname, str) and nickname:
            return nickname
    return ""


def _players(roster: object) -> tuple[YahooPlayer, ...]:
    if not isinstance(roster, dict):
        return ()
    raw = roster.get("players")
    if not isinstance(raw, list):
        return ()
    found: list[YahooPlayer] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        player = item.get("player", item)
        if isinstance(player, dict):
            found.append(_player(player))
    return tuple(found)


def _player(player: Mapping[str, object]) -> YahooPlayer:
    key = player.get("player_key")
    yahoo_id = player.get("player_id")
    if not isinstance(key, str) or not key:
        raise YahooError("player is missing player_key")
    name = _full_name(player.get("name"))
    position = player.get("primary_position") or player.get("display_position") or ""
    if not isinstance(position, str):
        position = ""
    selected = _selected(player.get("selected_position"))
    status = player.get("status")
    if not isinstance(status, str):
        status = ""
    return YahooPlayer(
        player_key=key,
        yahoo_id=str(yahoo_id) if yahoo_id is not None else "",
        name=name,
        editorial_team=_abbr(player.get("editorial_team_abbr")),
        position=position,
        selected=selected,
        status=status,
        bye_weeks=_bye_weeks(player.get("bye_weeks")),
        projection=_projection_total(player),
    )


def _full_name(name: object) -> str:
    if isinstance(name, dict):
        full = name.get("full")
        if isinstance(full, str):
            return full
    if isinstance(name, str):
        return name
    return ""


def _abbr(value: object) -> str:
    if isinstance(value, str):
        return value.upper()
    return ""


def _selected(value: object) -> str:
    if isinstance(value, dict):
        position = value.get("position")
        if isinstance(position, str):
            return position
    if isinstance(value, str):
        return value
    return ""


def _bye_weeks(value: object) -> tuple[int, ...]:
    if isinstance(value, dict):
        week = _optional_int(value.get("week"))
        if week is None:
            raw = value.get("week")
            if isinstance(raw, list):
                return tuple(week for item in raw if (week := _optional_int(item)) is not None)
            return ()
        return (week,)
    if isinstance(value, list):
        return tuple(week for item in value if (week := _optional_int(item)) is not None)
    return ()


def _team_ids(teams: object) -> list[int]:
    if not isinstance(teams, list):
        return []
    ids: list[int] = []
    for item in teams:
        if not isinstance(item, dict):
            continue
        team = item.get("team", item)
        if isinstance(team, dict):
            team_id = _optional_int(team.get("team_id"))
            if team_id is not None:
                ids.append(team_id)
    return ids


def _transaction(raw: Mapping[str, object]) -> YahooTransaction:
    item = raw.get("transaction", raw)
    if not isinstance(item, dict):
        raise YahooError("transaction was not an object")
    key = item.get("transaction_key")
    kind = item.get("type")
    status = item.get("status")
    timestamp = item.get("timestamp")
    players = item.get("players")
    keys: list[str] = []
    destination: int | None = None
    if isinstance(players, list):
        for player in players:
            if not isinstance(player, dict):
                continue
            body = player.get("player", player)
            if not isinstance(body, dict):
                continue
            player_key = body.get("player_key")
            if isinstance(player_key, str):
                keys.append(player_key)
            data = body.get("transaction_data")
            if isinstance(data, dict) and destination is None:
                destination = _team_id_from_key(data.get("destination_team_key"))
    return YahooTransaction(
        transaction_key=str(key) if isinstance(key, str) else "",
        kind=str(kind) if isinstance(kind, str) else "",
        status=str(status) if isinstance(status, str) else "",
        timestamp=str(timestamp) if timestamp is not None else "",
        player_keys=tuple(keys),
        destination_team_id=destination,
    )


def _team_id_from_key(value: object) -> int | None:
    if not isinstance(value, str) or ".t." not in value:
        return None
    return _optional_int(value.rsplit(".t.", 1)[1])


def _walk_stats(node: object) -> list[tuple[int, float]]:
    found: list[tuple[int, float]] = []
    if isinstance(node, dict):
        stat_id = _optional_int(node.get("stat_id"))
        value = node.get("value")
        numeric = isinstance(value, (str, int, float)) and not isinstance(value, bool)
        if stat_id is not None and numeric:
            try:
                found.append((stat_id, float(value)))
            except ValueError:
                pass
        for key, child in node.items():
            if key in {"stat_id", "value"}:
                continue
            found.extend(_walk_stats(child))
    elif isinstance(node, list):
        for child in node:
            found.extend(_walk_stats(child))
    return found


def _as_mapping(value: object) -> Mapping[str, object] | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, list) and len(value) == 1 and isinstance(value[0], dict):
        return value[0]
    return None


def _optional_int(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.lstrip("-").isdigit():
        return int(value)
    return None


def _xml_node(elem: ET.Element) -> object:
    children = list(elem)
    if not children:
        return (elem.text or "").strip()
    grouped: dict[str, object] = {}
    for child in children:
        tag = _local_name(child.tag)
        value = _xml_node(child)
        current = grouped.get(tag)
        if current is None:
            grouped[tag] = value
        elif isinstance(current, list):
            current.append(value)
        else:
            grouped[tag] = [current, value]
    return grouped


def _local_name(tag: str) -> str:
    if tag.startswith("{"):
        return tag.rsplit("}", 1)[-1]
    return tag
