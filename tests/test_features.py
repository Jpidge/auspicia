"""Leakage, temporal boundaries, train/serve parity, and determinism."""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from pathlib import Path

from gridiron_lab.cli import run
from gridiron_lab.data.fetch import FixtureFetch
from gridiron_lab.data.ingest import ingest
from gridiron_lab.data.schema import TABLES
from gridiron_lab.data.store import ParquetStore
from gridiron_lab.features import (
    FEATURE_COLUMNS,
    FEATURE_VERSION,
    build_features,
    recent_series,
    thursday_capture,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "tests" / "fixtures"
THURSDAY_W2 = datetime(2024, 9, 12, 20, tzinfo=UTC)
KICKOFF_W1 = "2024-09-06T00:20:00Z"
KICKOFF_W2 = "2024-09-15T17:00:00Z"
AFTER = "2024-09-12T20:00:01Z"
BEFORE = "2024-09-12T19:00:00Z"


def test_feature_contract_lists_every_column() -> None:
    text = (REPO_ROOT / "docs" / "FEATURES.md").read_text(encoding="utf-8")
    missing = [name for name in FEATURE_COLUMNS if f"\n{name}\n" not in text]
    assert missing == []


def test_thursday_capture_is_the_thursday_of_that_nfl_week() -> None:
    opener = datetime(2024, 9, 6, 0, 20, tzinfo=UTC)
    assert thursday_capture(opener) == datetime(2024, 9, 5, 20, tzinfo=UTC)
    sunday = datetime(2024, 9, 15, 17, tzinfo=UTC)
    assert thursday_capture(sunday) == THURSDAY_W2
    wednesday = datetime(2024, 9, 11, 1, tzinfo=UTC)
    assert thursday_capture(wednesday) == datetime(2024, 9, 5, 20, tzinfo=UTC)


def test_train_serve_parity_and_determinism(tmp_path: Path) -> None:
    _league(tmp_path)
    first = build_features(2024, 2, "simulated_capture", data_dir=tmp_path)
    second = build_features(2024, 2, "simulated_capture", data_dir=tmp_path)
    served = build_features(2024, 2, "capture", data_dir=tmp_path, now=THURSDAY_W2)
    _equal(first, second)
    _equal(first, served, ignore={"as_of_policy"})
    assert {row["as_of_policy"] for row in first} == {"simulated_capture"}
    assert {row["as_of_policy"] for row in served} == {"capture"}
    assert first
    assert all(row["as_of"] == "2024-09-12T20:00:00Z" for row in first)
    assert all(row["feature_version"] == FEATURE_VERSION for row in first)
    assert list(first[0]) == list(FEATURE_COLUMNS)


def test_absurd_target_week_stats_do_not_change_any_feature(tmp_path: Path) -> None:
    _league(tmp_path)
    before = build_features(2024, 2, "simulated_capture", data_dir=tmp_path)
    store = ParquetStore(tmp_path)
    store.append(
        "player_weeks",
        [
            _week(
                "00-QBKC",
                2024,
                2,
                "2024_02_CIN_KC",
                "KC",
                "CIN",
                points=1e6,
                targets=1e6,
                carries=1e6,
                attempts=1e6,
                air=1e6,
                dropbacks=1e6,
                scrambles=1e6,
                redzone=1e6,
                known="2024-05-16T00:00:00Z",
            )
        ],
    )
    store.append(
        "player_weeks",
        [
            _week(
                "00-QBPIT",
                2024,
                2,
                "2024_02_CIN_KC",
                "PIT",
                "CIN",
                points=1e6,
                targets=1e6,
                carries=1e6,
                attempts=1e6,
                air=1e6,
                dropbacks=1e6,
                scrambles=1e6,
                redzone=1e6,
                known="2024-05-16T00:00:00Z",
            )
        ],
    )
    store.append(
        "snap_counts",
        [
            _snap(
                "00-QBKC",
                "2024_02_CIN_KC",
                2024,
                2,
                "KC",
                "CIN",
                pct=1.0,
                snaps=70,
                known="2024-05-16T00:00:00Z",
            )
        ],
    )
    after = build_features(2024, 2, "simulated_capture", data_dir=tmp_path)
    _equal(before, after)


def test_temporal_boundaries_for_each_feature_group(tmp_path: Path) -> None:
    _league(tmp_path)
    built = build_features(2024, 2, "simulated_capture", data_dir=tmp_path)
    rows = {row["player_id"]: row for row in built}
    quarterback = rows["00-QBKC"]
    assert quarterback["opponent"] == "CIN"
    assert quarterback["opponent"] != quarterback["team"]
    assert quarterback["is_home"] == 1
    assert quarterback["is_bye"] == 0
    assert quarterback["force_zero"] == 0
    assert quarterback["games_played"] == 2
    assert math.isclose(quarterback["points_last"], 18)
    assert math.isclose(quarterback["points_roll3"], 19)
    assert math.isclose(quarterback["points_ewma"], 19.4)
    assert math.isclose(quarterback["points_slope4"], -2)
    assert quarterback["points_last_missing"] == 0
    assert math.isclose(quarterback["designed_rushes_last"], 5)
    assert math.isclose(quarterback["designed_rushes_ewma"], 3.6)
    assert math.isclose(quarterback["opportunity_slope4"], 3)
    assert math.isclose(quarterback["air_yards_last"], 200)
    assert math.isclose(quarterback["snap_share_last"], 0.8)
    assert math.isclose(quarterback["target_share_last"], 1)
    assert math.isclose(quarterback["team_spread"], 6)
    assert math.isclose(quarterback["game_total"], 44)
    assert math.isclose(quarterback["implied_team_total"], 25)
    assert quarterback["vegas_missing"] == 0
    assert math.isclose(quarterback["opp_points_allowed_pos_roll5"], 30)
    assert quarterback["injury_status"] == "Questionable"
    assert quarterback["practice_status"] == "LP"
    assert quarterback["injury_missing"] == 0
    assert quarterback["teammates_out_same_position"] == 1
    assert quarterback["depth_rank"] == 1
    assert quarterback["depth_position"] == "QB"
    assert math.isclose(quarterback["prior_season_ppg"], 20)
    assert math.isclose(quarterback["carryover_weight"], 0.5)
    assert quarterback["team_changed"] == 0
    rest = datetime(2024, 9, 15, 17, tzinfo=UTC) - datetime(2024, 9, 6, 0, 20, tzinfo=UTC)
    assert math.isclose(quarterback["rest_days"], rest.total_seconds() / 86400)
    for row in rows.values():
        assert row["opponent"] != row["team"]
        _missing_flags_match(row)

    week_one = {
        row["player_id"]: row
        for row in build_features(2024, 1, "simulated_capture", data_dir=tmp_path)
    }["00-QBKC"]
    assert math.isclose(week_one["points_last"], 20)
    assert math.isclose(week_one["carryover_weight"], 1)
    assert week_one["games_played"] == 1
    assert week_one["vegas_missing"] == 1
    assert week_one["opponent"] == "BAL"
    assert week_one["force_zero"] == 0
    assert week_one["as_of"] == "2024-09-05T20:00:00Z"


def test_bye_forces_zero_and_a_scheduled_week_keeps_production(tmp_path: Path) -> None:
    _league(tmp_path)
    scheduled = {
        row["player_id"]: row
        for row in build_features(2024, 2, "simulated_capture", data_dir=tmp_path)
    }
    bye = {
        row["player_id"]: row
        for row in build_features(2024, 3, "simulated_capture", data_dir=tmp_path)
    }
    assert scheduled["00-QBKC"]["force_zero"] == 0
    assert scheduled["00-QBKC"]["points_last"] != 0
    assert bye["00-QBKC"]["is_bye"] == 1
    assert bye["00-QBKC"]["force_zero"] == 1
    assert bye["00-QBKC"]["opponent"] is None
    assert bye["00-QBBAL"]["force_zero"] == 1
    assert math.isclose(bye["00-QBBAL"]["points_last"], 12)
    assert scheduled["00-QB2"]["is_bye"] == 0
    assert scheduled["00-QB2"]["force_zero"] == 1
    assert scheduled["00-QB2"]["injury_status"] == "Out"


def test_recent_series_excludes_the_target_week_and_late_rows(tmp_path: Path) -> None:
    _league(tmp_path)
    found = recent_series(
        2024,
        2,
        ["00-QBKC", "00-QBBAL", "DEF:KC", "missing"],
        data_dir=tmp_path,
        positions={"00-QBBAL": "K"},
    )
    assert "missing" not in found
    assert "DEF:KC" not in found
    assert found["00-QBKC"].recent_points == (20.0, 18.0)
    assert found["00-QBKC"].snap_share == (80.0,)
    assert 1e6 not in found["00-QBKC"].recent_points
    assert found["00-QBBAL"].recent_points == (12.0,)
    assert found["00-QBBAL"].snap_share is None

    store = ParquetStore(tmp_path)
    store.append(
        "player_weeks",
        [
            _week(
                "00-QBKC",
                2024,
                2,
                "2024_02_CIN_KC",
                "KC",
                "CIN",
                points=1e6,
                targets=1e6,
                carries=1e6,
                attempts=1e6,
                air=1e6,
                dropbacks=1e6,
                scrambles=1e6,
                redzone=1e6,
                known="2024-05-16T00:00:00Z",
            ),
            _week(
                "DEF:KC",
                2024,
                1,
                "2024_01_BAL_KC",
                "KC",
                "BAL",
                points=9,
                targets=0,
                carries=0,
                attempts=0,
                air=0,
                dropbacks=0,
                scrambles=0,
                redzone=0,
                known="2024-09-06T06:20:00Z",
            ),
            _week(
                "DEF:KC",
                2024,
                2,
                "2024_02_CIN_KC",
                "KC",
                "CIN",
                points=1e6,
                targets=0,
                carries=0,
                attempts=0,
                air=0,
                dropbacks=0,
                scrambles=0,
                redzone=0,
                known="2024-05-16T00:00:00Z",
            ),
        ],
    )
    store.append(
        "snap_counts",
        [
            _snap(
                "00-QBKC",
                "2024_02_CIN_KC",
                2024,
                2,
                "KC",
                "CIN",
                pct=1.0,
                snaps=70,
                known="2024-05-16T00:00:00Z",
            ),
            _snap(
                "DEF:KC",
                "2024_01_BAL_KC",
                2024,
                1,
                "KC",
                "BAL",
                pct=0.5,
                snaps=30,
                known="2024-09-07T00:20:00Z",
            ),
        ],
    )
    again = recent_series(
        2024,
        2,
        ["00-QBKC", "DEF:KC"],
        data_dir=tmp_path,
        positions={"DEF:KC": "DST"},
    )
    assert again["00-QBKC"].recent_points == (20.0, 18.0)
    assert again["00-QBKC"].snap_share == (80.0,)
    assert again["DEF:KC"].recent_points == (9.0,)
    assert again["DEF:KC"].snap_share is None
    labeled = recent_series(
        2024,
        2,
        ["00-QBKC"],
        data_dir=tmp_path,
        positions={"00-QBKC": "DST"},
    )
    assert labeled["00-QBKC"].recent_points == (20.0, 18.0)
    assert labeled["00-QBKC"].snap_share is None


def test_export_dashboard_reads_recent_series_from_the_store(tmp_path: Path) -> None:
    from gridiron_lab.config import load_config
    from gridiron_lab.dashboard import export_dashboard

    _league(tmp_path)
    roster = {
        "example": False,
        "season": 2024,
        "week": 2,
        "teams": [
            {
                "team_id": 10,
                "name": "Purple Reign",
                "manager": "Avery Quinn",
                "wins": 1,
                "losses": 0,
                "opponent_id": 11,
                "players": [
                    {
                        "player_id": "00-QBKC",
                        "name": "Store QB",
                        "position": "QB",
                        "q50": 18,
                        "recent_points": [999, 999],
                        "snap_share": [1, 2],
                    },
                    {
                        "player_id": "not-in-store",
                        "name": "Ghost",
                        "position": "WR",
                        "q50": 1,
                        "recent_points": [9, 9],
                        "snap_share": [9, 9],
                    },
                ],
            },
            {
                "team_id": 11,
                "name": "Other",
                "manager": "Drew Okada",
                "wins": 0,
                "losses": 1,
                "opponent_id": 10,
                "players": [],
            },
        ],
        "waivers": [
            {
                "player_id": "00-QBBAL",
                "name": "Kicker with no snaps",
                "position": "K",
                "snap_share": [100, 100],
            }
        ],
    }
    source = tmp_path / "rosters.json"
    source.write_text(json.dumps(roster), encoding="utf-8")
    output = tmp_path / "dashboard.json"
    export_dashboard(
        config=load_config(REPO_ROOT / "gridiron_lab.toml"),
        rosters_path=source,
        output=output,
        data_dir=tmp_path,
    )
    payload = json.loads(output.read_text(encoding="utf-8"))
    players = payload["teams"][0]["starters"] + payload["teams"][0]["bench"]
    quarterback = next(player for player in players if player["player_id"] == "00-QBKC")
    ghost = next(player for player in players if player["player_id"] == "not-in-store")
    assert quarterback["recent_points"] == [20.0, 18.0]
    assert quarterback["snap_share"] == [80.0]
    assert ghost["recent_points"] == []
    assert ghost["snap_share"] is None
    assert payload["waivers"][0]["snap_share"] is None
    copied = tmp_path / "copied.json"
    export_dashboard(
        config=load_config(REPO_ROOT / "gridiron_lab.toml"),
        rosters_path=source,
        output=copied,
    )
    copied_payload = json.loads(copied.read_text(encoding="utf-8"))
    copied_rows = copied_payload["teams"][0]["starters"] + copied_payload["teams"][0]["bench"]
    copied_qb = next(player for player in copied_rows if player["player_id"] == "00-QBKC")
    assert copied_qb["recent_points"] == [999.0, 999.0]
    assert copied_qb["snap_share"] == [1.0, 2.0]


def test_fixture_thursday_capture_sees_the_schedule_and_not_the_game(tmp_path: Path) -> None:
    ingest(seasons=[2024], data_dir=tmp_path, fetch=FixtureFetch(FIXTURES))
    rows = {
        row["player_id"]: row
        for row in build_features(2024, 1, "simulated_capture", data_dir=tmp_path)
    }
    mahomes = rows["00-0033873"]
    assert mahomes["opponent"] == "BAL"
    assert mahomes["is_home"] == 1
    assert mahomes["vegas_missing"] == 1
    assert mahomes["implied_team_total"] is None
    assert mahomes["points_last"] is None
    assert mahomes["injury_status"] == "Questionable"
    assert mahomes["depth_rank"] == 1
    assert mahomes["force_zero"] == 0
    assert mahomes["as_of"] == "2024-09-05T20:00:00Z"
    kelce = rows["00-0030506"]
    assert kelce["injury_missing"] == 1
    assert kelce["depth_rank"] == 1
    assert "00-0030506" in rows
    code = run(
        [
            "features",
            "--season",
            "2024",
            "--week",
            "1",
            "--policy",
            "simulated_capture",
            "--data-dir",
            str(tmp_path),
        ]
    )
    assert code == 0


def _league(path: Path) -> None:
    store = ParquetStore(path)
    store.append("games", _games())
    store.append("rosters", _rosters())
    store.append(
        "player_weeks",
        [
            _week(
                "00-QBKC",
                2023,
                18,
                "2023_18_DEN_KC",
                "KC",
                "DEN",
                points=20,
                targets=2,
                carries=4,
                attempts=30,
                air=100,
                dropbacks=32,
                scrambles=1,
                redzone=1,
                known="2024-01-08T00:00:00Z",
            ),
            _week(
                "00-QBKC",
                2024,
                1,
                "2024_01_BAL_KC",
                "KC",
                "BAL",
                points=18,
                targets=1,
                carries=8,
                attempts=35,
                air=200,
                dropbacks=40,
                scrambles=3,
                redzone=2,
                known="2024-09-06T06:20:00Z",
            ),
            _week(
                "00-QBKC",
                2024,
                1,
                "2024_01_BAL_KC",
                "KC",
                "BAL",
                points=1e6,
                targets=1e6,
                carries=1e6,
                attempts=1e6,
                air=1e6,
                dropbacks=1e6,
                scrambles=0,
                redzone=1e6,
                known=AFTER,
            ),
            _week(
                "00-QBBAL",
                2024,
                1,
                "2024_01_BAL_KC",
                "BAL",
                "KC",
                points=12,
                targets=0,
                carries=2,
                attempts=20,
                air=80,
                dropbacks=22,
                scrambles=1,
                redzone=0,
                known="2024-09-06T06:20:00Z",
            ),
            _week(
                "00-QBPIT",
                2024,
                1,
                "2024_01_PIT_CIN",
                "PIT",
                "CIN",
                points=30,
                targets=0,
                carries=3,
                attempts=25,
                air=150,
                dropbacks=28,
                scrambles=1,
                redzone=1,
                known="2024-09-09T02:00:00Z",
            ),
        ],
    )
    store.append(
        "snap_counts",
        [
            _snap(
                "00-QBKC",
                "2024_01_BAL_KC",
                2024,
                1,
                "KC",
                "BAL",
                pct=0.8,
                snaps=40,
                known="2024-09-07T00:20:00Z",
            ),
            _snap(
                "00-QBKC",
                "2024_01_BAL_KC",
                2024,
                1,
                "KC",
                "BAL",
                pct=0.01,
                snaps=1,
                known=AFTER,
            ),
        ],
    )
    store.append(
        "injuries",
        [
            _injury("00-QBKC", 2, "Questionable", "LP", BEFORE),
            _injury("00-QBKC", 2, "Out", "DNP", AFTER),
            _injury("00-QB2", 2, "Out", "DNP", "2024-09-12T18:00:00Z"),
        ],
    )
    store.append(
        "depth_charts",
        [
            _depth("00-QBKC", 1, "2024-09-01T00:00:00Z"),
            _depth("00-QBKC", 9, AFTER),
        ],
    )


def _games() -> list[dict[str, object]]:
    specs = [
        ("2023_18_DEN_KC", 2023, 18, "DEN", "KC", "2024-01-07T18:00:00Z", None, None),
        ("2024_01_BAL_KC", 2024, 1, "BAL", "KC", KICKOFF_W1, None, None),
        ("2024_01_PIT_CIN", 2024, 1, "PIT", "CIN", "2024-09-08T17:00:00Z", None, None),
        ("2024_02_CIN_KC", 2024, 2, "CIN", "KC", KICKOFF_W2, 6.0, 44.0),
        ("2024_03_DAL_NYG", 2024, 3, "DAL", "NYG", "2024-09-22T17:00:00Z", None, None),
    ]
    rows: list[dict[str, object]] = []
    for game_id, season, week, away, home, kickoff, spread, total in specs:
        rows.append(
            _game(
                game_id,
                season,
                week,
                away,
                home,
                kickoff,
                "schedule",
                f"{season}-05-15T16:00:00Z",
            )
        )
        if spread is not None and total is not None:
            rows.append(
                _game(
                    game_id,
                    season,
                    week,
                    away,
                    home,
                    kickoff,
                    "pregame",
                    BEFORE,
                    spread=spread,
                    total=total,
                )
            )
            rows.append(
                _game(
                    game_id,
                    season,
                    week,
                    away,
                    home,
                    kickoff,
                    "pregame",
                    AFTER,
                    spread=99,
                    total=80,
                )
            )
    return rows


def _rosters() -> list[dict[str, object]]:
    players = (
        ("00-QBKC", "KC", "QB"),
        ("00-QB2", "KC", "QB"),
        ("00-QBBAL", "BAL", "QB"),
        ("00-QBPIT", "PIT", "QB"),
    )
    return [
        _row(
            "rosters",
            player_id=player_id,
            week=1,
            season_type="REG",
            team=team,
            position=position,
            status="ACT",
            known_as_of="2024-05-20T00:00:00Z",
            season=2024,
        )
        for player_id, team, position in players
    ]


def _week(
    player_id: str,
    season: int,
    week: int,
    game_id: str,
    team: str,
    opponent: str,
    *,
    points: float,
    targets: float,
    carries: float,
    attempts: float,
    air: float,
    dropbacks: float,
    scrambles: float,
    redzone: float,
    known: str,
) -> dict[str, object]:
    return _row(
        "player_weeks",
        player_id=player_id,
        display_name=player_id,
        position="QB",
        week=week,
        season_type="REG",
        game_id=game_id,
        team=team,
        opponent=opponent,
        targets=targets,
        carries=carries,
        pass_attempts=attempts,
        passing_air_yards=air,
        dropbacks=dropbacks,
        scrambles=scrambles,
        redzone_touches=redzone,
        fantasy_points=points,
        score_complete=True,
        scoring_version="ffc_yahoo_v1",
        known_as_of=known,
        season=season,
    )


def _snap(
    player_id: str,
    game_id: str,
    season: int,
    week: int,
    team: str,
    opponent: str,
    *,
    pct: float,
    snaps: int,
    known: str,
) -> dict[str, object]:
    return _row(
        "snap_counts",
        player_id=player_id,
        week=week,
        season_type="REG",
        game_id=game_id,
        team=team,
        opponent=opponent,
        offense_snaps=snaps,
        defense_snaps=0,
        st_snaps=0,
        offense_pct=pct,
        known_as_of=known,
        season=season,
    )


def _injury(player_id: str, week: int, status: str, practice: str, known: str) -> dict[str, object]:
    return _row(
        "injuries",
        player_id=player_id,
        week=week,
        season_type="REG",
        team="KC",
        game_id="2024_02_CIN_KC",
        report_status=status,
        practice_status=practice,
        known_as_of=known,
        season=2024,
    )


def _depth(player_id: str, rank: int, known: str) -> dict[str, object]:
    return _row(
        "depth_charts",
        player_id=player_id,
        week=2,
        season_type="REG",
        team="KC",
        position="QB",
        depth_rank=rank,
        known_as_of=known,
        season=2024,
    )


def _game(
    game_id: str,
    season: int,
    week: int,
    away: str,
    home: str,
    kickoff: str,
    kind: str,
    known: str,
    *,
    spread: float | None = None,
    total: float | None = None,
) -> dict[str, object]:
    home_implied = None if spread is None or total is None else total / 2 + spread / 2
    away_implied = None if spread is None or total is None else total / 2 - spread / 2
    return _row(
        "games",
        game_id=game_id,
        week=week,
        season_type="REG",
        game_type="REG",
        home_team=home,
        away_team=away,
        kickoff_utc=kickoff,
        spread_line=spread,
        total_line=total,
        home_implied_total=home_implied,
        away_implied_total=away_implied,
        record_kind=kind,
        known_as_of=known,
        season=season,
    )


def _row(table: str, **fields: object) -> dict[str, object]:
    spec = TABLES[table]
    base: dict[str, object] = {}
    for column in spec.columns:
        if column.name in {"ingested_at", "row_hash"}:
            base[column.name] = ""
        elif column.kind == "bool":
            base[column.name] = False
        elif column.name in spec.keys:
            base[column.name] = 0 if column.kind == "int" else ""
        else:
            base[column.name] = None
    base["source"] = "nflverse"
    base["known_as_of"] = "2024-01-01T00:00:00Z"
    base["known_as_of_method"] = "test"
    base["season"] = 0 if spec.dimension else 2024
    base.update(fields)
    return base


def _equal(
    left: list[dict[str, object]],
    right: list[dict[str, object]],
    *,
    ignore: set[str] | None = None,
) -> None:
    skipped = ignore or set()
    assert [row["player_id"] for row in left] == [row["player_id"] for row in right]
    for first, second in zip(left, right, strict=True):
        assert list(first) == list(second)
        for key in first:
            if key in skipped:
                continue
            _close(first[key], second[key], key)


def _close(left: object, right: object, key: str) -> None:
    if isinstance(left, float) or isinstance(right, float):
        assert left is not None and right is not None, key
        assert math.isclose(float(left), float(right), rel_tol=1e-12, abs_tol=1e-12), key
        return
    assert left == right, key


def _missing_flags_match(row: dict[str, object]) -> None:
    for key, flag in row.items():
        if not isinstance(key, str) or not key.endswith("_missing"):
            continue
        base = key[: -len("_missing")]
        if base not in row:
            continue
        if flag == 1:
            assert row[base] is None, key
        else:
            assert row[base] is not None, key
