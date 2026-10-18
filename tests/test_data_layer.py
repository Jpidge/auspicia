"""M1 storage, clocks, and offline ingestion."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from gridiron_lab.cli import parse_seasons, run
from gridiron_lab.data.clock import (
    SNAPS_LAG,
    STATS_LAG,
    VEGAS_LEAD,
    format_utc,
    implied_totals,
    kickoff_from_schedule,
    shift,
)
from gridiron_lab.data.errors import IngestError
from gridiron_lab.data.fetch import FixtureFetch, HttpFetch, release_url
from gridiron_lab.data.ingest import ingest
from gridiron_lab.data.normalize import build_games, build_identity, build_snaps
from gridiron_lab.data.pbp import adjusted_points_allowed
from gridiron_lab.data.store import ParquetStore, open_db
from gridiron_lab.scoring import SCORING_VERSION

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "tests" / "fixtures"
KICKOFF = "2024-09-06T00:20:00Z"


def _kickoff():
    parsed = kickoff_from_schedule("2024-09-05", "20:20")
    assert parsed is not None
    return parsed[0]


def test_home_favorite_spread_raises_the_home_implied_total() -> None:
    home, away = implied_totals(46, 3)
    assert home == 24.5
    assert away == 21.5
    dog_home, dog_away = implied_totals(40, -3)
    assert dog_home == 18.5
    assert dog_away == 21.5


def test_opener_kickoff_and_lags_are_utc() -> None:
    kickoff, method = kickoff_from_schedule("2024-09-05", "20:20")
    assert kickoff is not None
    assert method == "schedule_kickoff_et"
    assert format_utc(kickoff) == KICKOFF
    assert shift(kickoff, -VEGAS_LEAD) == "2024-09-05T23:20:00Z"
    assert shift(kickoff, STATS_LAG) == "2024-09-06T06:20:00Z"
    assert shift(kickoff, SNAPS_LAG) == "2024-09-07T00:20:00Z"


def test_unplayed_game_does_not_publish_a_result() -> None:
    rows, clocks, _notes = build_games(
        [
            {
                "game_id": "2026_01_A_B",
                "season": 2026,
                "game_type": "REG",
                "week": 1,
                "gameday": "2026-09-10",
                "gametime": "20:15",
                "home_team": "A",
                "away_team": "B",
                "spread_line": 1,
                "total_line": 44,
                "home_score": "",
                "away_score": "",
            }
        ],
        2026,
    )
    assert [row["record_kind"] for row in rows] == ["pregame"]
    assert rows[0]["home_score"] is None
    assert clocks[0].spread_line == 1


def test_negative_points_allowed_adjustment_is_missing() -> None:
    assert adjusted_points_allowed(6, 2) is None
    assert adjusted_points_allowed(20, 1) == 14
    assert adjusted_points_allowed(None, 0) is None


def test_season_parser_accepts_years_and_ranges() -> None:
    assert parse_seasons(["2025"]) == [2025]
    assert parse_seasons(["2018-2020", "2020", "2024"]) == [2018, 2019, 2020, 2024]


def test_offline_ingest_scores_fixture_and_is_idempotent(tmp_path: Path) -> None:
    first = ingest(
        seasons=[2024],
        data_dir=tmp_path,
        fetch=FixtureFetch(FIXTURES),
    )
    parts = sorted((tmp_path / "raw").rglob("*.parquet"))
    second = ingest(
        seasons=[2024],
        data_dir=tmp_path,
        fetch=FixtureFetch(FIXTURES),
    )
    assert parts == sorted((tmp_path / "raw").rglob("*.parquet"))
    assert all(source.appended == 0 for source in second[0].sources)
    assert second[0].table_counts == first[0].table_counts
    counts = first[0].table_counts
    assert counts["games"] == 2
    assert counts["player_weeks"] == 5
    assert counts["players"] == 7
    assert counts["player_xref"] == 4
    assert counts["identity_issues"] == 1
    assert counts["injuries"] == 3
    assert counts["depth_charts"] == 2
    assert counts["rosters"] == 1
    assert counts["snap_counts"] == 1
    assert counts["pbp_events"] == 6

    db = open_db(tmp_path)
    kickoff = _kickoff()
    weeks = {
        row["player_id"]: row
        for row in db.as_of("player_weeks", kickoff + STATS_LAG + timedelta(seconds=1))
    }
    mahomes = weeks["00-0033873"]
    kelce = weeks["00-0030506"]
    butker = weeks["00-0023853"]
    chiefs = weeks["DEF:KC"]
    ravens = weeks["DEF:BAL"]
    assert mahomes["passing_tds_40_plus"] == 1
    assert mahomes["passing_yards"] == 250
    assert math.isclose(mahomes["fantasy_points"], 18.0)
    assert mahomes["score_complete"] is True
    assert mahomes["scoring_version"] == SCORING_VERSION
    assert math.isclose(kelce["fantasy_points"], 11.5)
    assert kelce["receiving_tds_40_plus"] == 1
    assert math.isclose(butker["fantasy_points"], 5.8)
    assert butker["field_goal_yards_made"] == 48
    assert chiefs["points_allowed"] == 14
    assert chiefs["opponent_score"] == 20
    assert chiefs["sacks"] == 1
    assert math.isclose(chiefs["fantasy_points"], 2.0)
    assert ravens["points_allowed"] == 27
    assert ravens["return_tds"] == 1
    assert math.isclose(ravens["fantasy_points"], 9.96)
    assert all(row["known_as_of"] == "2024-09-06T06:20:00Z" for row in weeks.values())


def test_as_of_never_returns_a_row_known_at_or_after_the_cutoff(tmp_path: Path) -> None:
    ingest(seasons=[2024], data_dir=tmp_path, fetch=FixtureFetch(FIXTURES))
    db = open_db(tmp_path)
    kickoff = _kickoff()
    postgame = kickoff + STATS_LAG
    pregame_only = db.as_of("games", postgame)
    assert len(pregame_only) == 1
    assert pregame_only[0]["record_kind"] == "pregame"
    assert pregame_only[0]["home_score"] is None
    assert pregame_only[0]["spread_line"] == 3
    assert pregame_only[0]["home_implied_total"] == 24.5
    assert db.as_of("player_weeks", postgame) == []
    assert db.as_of("snap_counts", kickoff + SNAPS_LAG) == []

    visible = db.as_of("games", kickoff)
    assert len(visible) == 1
    assert visible[0]["home_score"] is None
    final = db.as_of("games", postgame + timedelta(seconds=1))
    assert final[0]["record_kind"] == "postgame"
    assert final[0]["home_score"] == 27
    assert final[0]["away_score"] == 20
    snaps = db.as_of("snap_counts", kickoff + SNAPS_LAG + timedelta(seconds=1))
    assert snaps[0]["offense_snaps"] == 70
    assert snaps[0]["known_as_of_method"] == "kickoff_plus_24h"

    injuries = {
        row["player_id"]: row for row in db.as_of("injuries", kickoff + timedelta(seconds=1))
    }
    assert injuries["00-0033873"]["known_as_of"] == "2024-09-04T18:00:00Z"
    assert injuries["00-0033873"]["known_as_of_method"] == "report_timestamp"
    assert injuries["00-0031111"]["known_as_of"] == "2024-09-05T03:59:00Z"
    assert injuries["00-0031111"]["known_as_of_method"] == "report_date_2359_et"
    assert injuries["00-0030506"]["known_as_of"] == KICKOFF
    assert injuries["00-0030506"]["known_as_of_method"] == "kickoff_proxy_missing_report_time"
    hidden = {row["player_id"] for row in db.as_of("injuries", kickoff)}
    assert "00-0030506" not in hidden
    assert "00-0033873" in hidden

    charts = {row["player_id"]: row for row in db.as_of("depth_charts", kickoff)}
    assert charts["00-0033873"]["known_as_of_method"] == "snapshot_timestamp"
    assert charts["00-0033873"]["known_as_of"] == "2024-09-04T15:00:00Z"
    assert charts["00-0030506"]["known_as_of_method"] == "day_before_gameday_2359_et"
    assert charts["00-0030506"]["known_as_of"] == "2024-09-05T03:59:00Z"
    roster = db.as_of("rosters", kickoff)
    assert roster[0]["status"] == "ACT"
    assert roster[0]["known_as_of"] == "2024-09-05T03:59:00Z"


def test_ambiguous_external_id_is_not_assigned(tmp_path: Path) -> None:
    ingest(seasons=[2024], data_dir=tmp_path, fetch=FixtureFetch(FIXTURES))
    db = open_db(tmp_path)
    xref = db.as_of("player_xref", _kickoff())
    yahoo = {
        row["external_id"]: row["player_id"] for row in xref if row["source_system"] == "yahoo"
    }
    assert yahoo["1001"] == "00-0033873"
    assert "999" not in yahoo
    issues = db.as_of("identity_issues", _kickoff())
    assert len(issues) == 1
    assert issues[0]["external_id"] == "999"
    assert issues[0]["candidate_player_ids"] == "00-00AAAAA,00-00BBBBB"
    assert "player_id" not in issues[0]


def test_changed_payload_appends_a_new_version(tmp_path: Path) -> None:
    ticks = {"n": 0}

    def clock() -> datetime:
        ticks["n"] += 1
        return datetime(2024, 9, ticks["n"], 12, tzinfo=UTC)

    store = ParquetStore(tmp_path, clock=clock)
    first, _clocks, _notes = build_games(
        [
            {
                "game_id": "2024_01_BAL_KC",
                "season": 2024,
                "game_type": "REG",
                "week": 1,
                "gameday": "2024-09-05",
                "gametime": "20:20",
                "home_team": "KC",
                "away_team": "BAL",
                "spread_line": 3,
                "total_line": 46,
                "home_score": 27,
                "away_score": 20,
            }
        ],
        2024,
    )
    pregame = next(row for row in first if row["record_kind"] == "pregame")
    assert store.append("games", [pregame]) == 1
    assert store.append("games", [pregame]) == 0
    revised = dict(pregame)
    revised["spread_line"] = 2.5
    assert store.append("games", [revised]) == 1
    rows = open_db(tmp_path).as_of("games", _kickoff())
    assert len(rows) == 1
    assert rows[0]["spread_line"] == 2.5


def test_store_rejects_a_row_without_known_as_of(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    try:
        store.append(
            "players",
            [
                {
                    "player_id": "00-1",
                    "display_name": "A",
                    "entry_year": 2018,
                    "season": 0,
                }
            ],
        )
    except IngestError as exc:
        assert "known_as_of" in str(exc)
    else:
        raise AssertionError("missing known_as_of was accepted")


def test_http_fetch_reads_release_bytes_without_a_second_download(tmp_path: Path) -> None:
    table = pa.table({"season": pa.array([2024]), "player_id": pa.array(["00-1"])})
    sink = pa.BufferOutputStream()
    pq.write_table(table, sink)
    payload = sink.getvalue().to_pybytes()
    calls: list[str] = []

    class _Body:
        def read(self) -> bytes:
            return payload

        def __enter__(self) -> _Body:
            return self

        def __exit__(self, *_args: object) -> bool:
            return False

    def opener(request: object, timeout: float) -> _Body:
        assert timeout == 5
        calls.append(request.full_url)  # type: ignore[attr-defined]
        return _Body()

    fetch = HttpFetch(tmp_path, timeout=5, opener=opener)
    rows = fetch.load("player_stats", 2024)
    again = fetch.load("player_stats", 2024)
    assert rows == again == [{"season": 2024, "player_id": "00-1"}]
    assert calls == [release_url("player_stats", 2024)]


def test_cli_ingests_fixtures(tmp_path: Path) -> None:
    code = run(
        [
            "ingest",
            "--season",
            "2024",
            "--data-dir",
            str(tmp_path),
            "--fixture-dir",
            str(FIXTURES),
        ]
    )
    assert code == 0
    assert (tmp_path / "gridiron.duckdb").is_file()
    missing = run(
        [
            "ingest",
            "--season",
            "2024",
            "--data-dir",
            str(tmp_path / "other"),
            "--fixture-dir",
            str(tmp_path / "missing"),
        ]
    )
    assert missing == 1


def test_crosswalk_uses_rookie_season_and_roster_yahoo_ids() -> None:
    players, xref, issues, _notes = build_identity(
        [
            {
                "gsis_id": "00-1",
                "display_name": "A",
                "rookie_season": 2019,
                "pfr_id": "AaaaAa00",
            }
        ],
        [{"gsis_id": "00-1", "yahoo_id": "55", "season": 2024, "week": 1, "team": "KC"}],
    )
    assert players[0]["entry_year"] == 2019
    assert players[0]["known_as_of_method"] == "entry_year_sept_1_et"
    found = {(row["source_system"], row["external_id"], row["player_id"]) for row in xref}
    assert ("pfr", "AaaaAa00", "00-1") in found
    assert ("yahoo", "55", "00-1") in found
    assert issues == []

    _players, collided, surfaced, notes = build_identity(
        [
            {"gsis_id": "00-1", "display_name": "A", "yahoo_id": "7"},
            {"gsis_id": "00-2", "display_name": "B", "yahoo_id": "7"},
        ]
    )
    assert collided == []
    assert surfaced[0]["candidate_player_ids"] == "00-1,00-2"
    assert notes["ambiguous_external_id"] == 1


def test_snap_counts_join_on_pfr_id() -> None:
    _rows, clocks, _notes = build_games(
        [
            {
                "game_id": "2024_01_BAL_KC",
                "season": 2024,
                "game_type": "REG",
                "week": 1,
                "gameday": "2024-09-05",
                "gametime": "20:20",
                "home_team": "KC",
                "away_team": "BAL",
                "spread_line": 3,
                "total_line": 46,
                "home_score": 27,
                "away_score": 20,
            }
        ],
        2024,
    )
    stored, notes = build_snaps(
        [
            {
                "game_id": "2024_01_BAL_KC",
                "pfr_player_id": "MahoPa00",
                "team": "KC",
                "opponent": "BAL",
                "offense_snaps": 10,
                "defense_snaps": 0,
                "st_snaps": 1,
            }
        ],
        clocks,
        {"MahoPa00": "00-0033873"},
    )
    assert stored[0]["player_id"] == "00-0033873"
    assert stored[0]["known_as_of"] == "2024-09-07T00:20:00Z"
    assert notes["snap_without_player_or_game"] == 0


def test_feature_modules_can_only_read_through_as_of() -> None:
    package = REPO_ROOT / "src" / "gridiron_lab"
    paths = []
    single = package / "features.py"
    nested = package / "features"
    if single.is_file():
        paths.append(single)
    if nested.is_dir():
        paths.extend(nested.rglob("*.py"))
    banned = ("read_parquet", "duckdb", "ParquetStore", "gridiron.duckdb")
    offenders: list[str] = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        if "as_of" not in text:
            offenders.append(f"{path.name} does not call as_of")
        for needle in banned:
            if needle in text:
                offenders.append(f"{path.name} references {needle}")
    assert offenders == []
