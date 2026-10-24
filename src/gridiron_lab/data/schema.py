"""Canonical column lists for the append-only tables.

``season`` on ``players``, ``player_xref``, and ``identity_issues`` is 0.
Those rows are a crosswalk, not one NFL season. Game-level tables use the
NFL season both as a column and as the Parquet partition.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import pyarrow as pa

from gridiron_lab.data.errors import IngestError

Kind = str

_ARROW = {
    "string": pa.string(),
    "int": pa.int64(),
    "float": pa.float64(),
    "bool": pa.bool_(),
}


@dataclass(frozen=True)
class Column:
    name: str
    kind: Kind


def _columns(*items: tuple[str, Kind]) -> tuple[Column, ...]:
    return tuple(Column(name, kind) for name, kind in items)


_META: tuple[tuple[str, Kind], ...] = (
    ("known_as_of", "string"),
    ("known_as_of_method", "string"),
    ("ingested_at", "string"),
    ("source", "string"),
    ("row_hash", "string"),
    ("season", "int"),
)


@dataclass(frozen=True)
class TableSpec:
    name: str
    keys: tuple[str, ...]
    columns: tuple[Column, ...]
    dimension: bool = False

    @property
    def names(self) -> frozenset[str]:
        return frozenset(column.name for column in self.columns)

    def arrow_schema(self) -> pa.Schema:
        return pa.schema([(column.name, _ARROW[column.kind]) for column in self.columns])


def _table(
    name: str,
    keys: tuple[str, ...],
    body: tuple[tuple[str, Kind], ...],
    *,
    dimension: bool = False,
) -> TableSpec:
    return TableSpec(name, keys, _columns(*body, *_META), dimension)


TABLES: dict[str, TableSpec] = {
    spec.name: spec
    for spec in (
        _table(
            "games",
            ("game_id",),
            (
                ("game_id", "string"),
                ("week", "int"),
                ("season_type", "string"),
                ("game_type", "string"),
                ("home_team", "string"),
                ("away_team", "string"),
                ("kickoff_utc", "string"),
                ("spread_line", "float"),
                ("total_line", "float"),
                ("home_implied_total", "float"),
                ("away_implied_total", "float"),
                ("home_score", "int"),
                ("away_score", "int"),
                ("record_kind", "string"),
            ),
        ),
        _table(
            "players",
            ("player_id",),
            (
                ("player_id", "string"),
                ("display_name", "string"),
                ("entry_year", "int"),
            ),
            dimension=True,
        ),
        _table(
            "player_xref",
            ("source_system", "external_id"),
            (
                ("source_system", "string"),
                ("external_id", "string"),
                ("player_id", "string"),
                ("match_method", "string"),
                ("match_confidence", "float"),
            ),
            dimension=True,
        ),
        _table(
            "identity_issues",
            ("source_system", "external_id"),
            (
                ("source_system", "string"),
                ("external_id", "string"),
                ("candidate_player_ids", "string"),
            ),
            dimension=True,
        ),
        _table(
            "player_weeks",
            ("player_id", "season", "week", "season_type"),
            (
                ("player_id", "string"),
                ("display_name", "string"),
                ("position", "string"),
                ("week", "int"),
                ("season_type", "string"),
                ("game_id", "string"),
                ("team", "string"),
                ("opponent", "string"),
                ("targets", "float"),
                ("carries", "float"),
                ("pass_attempts", "float"),
                ("passing_air_yards", "float"),
                ("receiving_air_yards", "float"),
                ("dropbacks", "float"),
                ("scrambles", "float"),
                ("redzone_touches", "float"),
                ("passing_yards", "float"),
                ("passing_tds", "float"),
                ("interceptions", "float"),
                ("passing_tds_40_plus", "float"),
                ("rushing_yards", "float"),
                ("rushing_tds", "float"),
                ("rushing_tds_40_plus", "float"),
                ("receptions", "float"),
                ("receiving_yards", "float"),
                ("receiving_tds", "float"),
                ("receiving_tds_40_plus", "float"),
                ("fumbles_lost", "float"),
                ("two_point", "float"),
                ("return_yards", "float"),
                ("return_tds", "float"),
                ("offensive_fumble_return_tds", "float"),
                ("extra_points_made", "float"),
                ("field_goal_yards_made", "float"),
                ("sacks", "float"),
                ("fumble_recoveries", "float"),
                ("defense_tds", "float"),
                ("safeties", "float"),
                ("blocked_kicks", "float"),
                ("extra_point_returns", "float"),
                ("points_allowed", "float"),
                ("opponent_score", "float"),
                ("fantasy_points", "float"),
                ("touchdown_points", "float"),
                ("unavailable", "string"),
                ("score_complete", "bool"),
                ("scoring_version", "string"),
            ),
        ),
        _table(
            "snap_counts",
            ("player_id", "game_id"),
            (
                ("player_id", "string"),
                ("week", "int"),
                ("season_type", "string"),
                ("game_id", "string"),
                ("team", "string"),
                ("opponent", "string"),
                ("offense_snaps", "int"),
                ("defense_snaps", "int"),
                ("st_snaps", "int"),
                ("offense_pct", "float"),
                ("defense_pct", "float"),
                ("st_pct", "float"),
            ),
        ),
        _table(
            "injuries",
            ("player_id", "season", "week", "season_type"),
            (
                ("player_id", "string"),
                ("week", "int"),
                ("season_type", "string"),
                ("team", "string"),
                ("game_id", "string"),
                ("report_status", "string"),
                ("practice_status", "string"),
                ("report_primary_injury", "string"),
            ),
        ),
        _table(
            "depth_charts",
            ("player_id", "team", "position"),
            (
                ("player_id", "string"),
                ("week", "int"),
                ("season_type", "string"),
                ("team", "string"),
                ("position", "string"),
                ("depth_rank", "int"),
                ("formation", "string"),
            ),
        ),
        _table(
            "rosters",
            ("player_id",),
            (
                ("player_id", "string"),
                ("week", "int"),
                ("season_type", "string"),
                ("team", "string"),
                ("position", "string"),
                ("status", "string"),
            ),
        ),
        _table(
            "pbp_events",
            ("player_id", "season", "week", "season_type", "unit"),
            (
                ("player_id", "string"),
                ("week", "int"),
                ("season_type", "string"),
                ("game_id", "string"),
                ("team", "string"),
                ("unit", "string"),
                ("dropbacks", "float"),
                ("scrambles", "float"),
                ("redzone_touches", "float"),
                ("passing_tds_40_plus", "float"),
                ("rushing_tds_40_plus", "float"),
                ("receiving_tds_40_plus", "float"),
                ("return_tds", "float"),
                ("offensive_fumble_return_tds", "float"),
                ("field_goal_yards_made", "float"),
                ("extra_points_made", "float"),
                ("sacks", "float"),
                ("interceptions", "float"),
                ("fumble_recoveries", "float"),
                ("defense_tds", "float"),
                ("safeties", "float"),
                ("blocked_kicks", "float"),
                ("return_yards", "float"),
                ("extra_point_returns", "float"),
                ("points_allowed", "float"),
                ("opponent_score", "float"),
            ),
        ),
    )
}

TABLE_NAMES: tuple[str, ...] = tuple(TABLES)
SOURCE_NAME = "nflverse"
YAHOO_SOURCE = "yahoo"
ALLOWED_SOURCES = frozenset({SOURCE_NAME, YAHOO_SOURCE})
_STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def require_table(name: str) -> TableSpec:
    try:
        return TABLES[name]
    except KeyError as exc:
        raise IngestError(f"unknown table {name}") from exc


def coerce(value: object, kind: Kind) -> object:
    if value is None or value == "":
        return None
    if kind == "string":
        return str(value)
    if kind == "bool":
        if isinstance(value, bool):
            return value
        if value in {"true", "True", "1"}:
            return True
        if value in {"false", "False", "0"}:
            return False
        raise IngestError(f"expected a boolean, got {value!r}")
    if kind == "int":
        if isinstance(value, bool):
            raise IngestError(f"expected an integer, got {value!r}")
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            if not value.is_integer():
                raise IngestError(f"expected an integer, got {value!r}")
            return int(value)
        text = str(value).strip()
        if text == "":
            return None
        number = float(text)
        if not number.is_integer():
            raise IngestError(f"expected an integer, got {value!r}")
        return int(number)
    if kind == "float":
        if isinstance(value, bool):
            raise IngestError(f"expected a number, got {value!r}")
        return float(value)
    raise IngestError(f"unknown column kind {kind}")


def stamp_ok(value: object) -> bool:
    return isinstance(value, str) and _STAMP.fullmatch(value) is not None
