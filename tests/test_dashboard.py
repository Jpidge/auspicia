"""Dashboard export, the example file, and the static site contract."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

from gridiron_lab.cli import run
from gridiron_lab.config import load_config
from gridiron_lab.dashboard import DASHBOARD_VERSION, build_dashboard, published_m3_lab

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
ROSTERS = ROOT / "examples" / "dashboard" / "rosters.json"
# Fields the page reads. anonymize_managers is an export flag; the page
# shows manager_display, which the exporter already resolved.
UI_FIELDS = (
    "manager_display",
    "default_team_id",
    "q10",
    "q50",
    "q90",
    "verdict",
    "win_probability",
    "projected_total",
    "aria-sort",
)


def _mini_roster() -> dict[str, object]:
    def player(player_id: str, position: str, q50: float) -> dict[str, object]:
        return {
            "player_id": player_id,
            "name": player_id,
            "position": position,
            "q50": q50,
            "q10": q50 - 2,
            "q90": q50 + 4,
            "mean": q50,
        }

    def team(team_id: int, name: str, manager: str, opponent: int) -> dict[str, object]:
        base = team_id * 0.1
        return {
            "team_id": team_id,
            "name": name,
            "manager": manager,
            "wins": 1,
            "losses": 0,
            "opponent_id": opponent,
            "players": [
                player(f"t{team_id}-qb", "QB", 10 + base),
                player(f"t{team_id}-rb1", "RB", 8 + base),
                player(f"t{team_id}-rb2", "RB", 7 + base),
                player(f"t{team_id}-wr1", "WR", 6 + base),
                player(f"t{team_id}-wr2", "WR", 5 + base),
                player(f"t{team_id}-te", "TE", 4 + base),
                player(f"t{team_id}-k", "K", 3 + base),
                player(f"t{team_id}-dst", "DST", 2 + base),
                player(f"t{team_id}-flex", "WR", 4.5 + base),
            ],
        }

    return {
        "example": True,
        "season": 2026,
        "week": 7,
        "teams": [
            team(10, "Purple Reign", "Avery Quinn", 11),
            team(11, "Taco Tuesday Titans", "Drew Okada", 10),
        ],
        "waivers": [
            {
                "player_id": "waive-1",
                "name": "Calvin Thistle",
                "position": "RB",
                "nfl_team": "BRZ",
                "q50_next": [9.0, 8.0, 7.0],
                "ros": 40.0,
                "note": "Inherits early-down work",
                "drop_player_id": "t10-flex",
            }
        ],
    }


def test_anonymize_flag_drops_manager_names() -> None:
    config = load_config(ROOT / "gridiron_lab.toml")
    shown = build_dashboard(config, _mini_roster())
    assert shown["anonymize_managers"] is False
    teams = shown["teams"]
    assert isinstance(teams, list)
    assert teams[0]["manager_display"] == "Avery Quinn"
    hidden_config = config.__class__(
        path=config.path,
        league=config.league,
        lineup=config.lineup,
        scoring=config.scoring,
        dashboard=config.dashboard.__class__(anonymize_managers=True),
    )
    hidden = build_dashboard(hidden_config, _mini_roster())
    text = json.dumps(hidden)
    assert "Avery Quinn" not in text
    assert "Drew Okada" not in text
    assert "Manager 10" in text
    assert hidden["teams"][0]["name"] == "Purple Reign"  # type: ignore[index]


def test_predictions_override_the_roster_file() -> None:
    config = load_config(ROOT / "gridiron_lab.toml")
    payload = build_dashboard(
        config,
        _mini_roster(),
        {"players": [{"player_id": "t10-qb", "q50": 30, "q10": 20, "q90": 40, "mean": 31}]},
    )
    teams = payload["teams"]
    assert isinstance(teams, list)
    starters = teams[0]["starters"]
    assert isinstance(starters, list)
    qb = next(player for player in starters if player["player_id"] == "t10-qb")
    assert qb["q50"] == 30
    assert qb["q10"] == 20


def test_export_command_writes_json(tmp_path: Path) -> None:
    source = tmp_path / "rosters.json"
    source.write_text(json.dumps(_mini_roster()), encoding="utf-8")
    output = tmp_path / "out" / "dashboard.json"
    code = run(
        [
            "export-dashboard",
            "--rosters",
            str(source),
            "--output",
            str(output),
            "--config",
            str(ROOT / "gridiron_lab.toml"),
        ]
    )
    assert code == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["version"] == DASHBOARD_VERSION
    assert payload["league"]["name"] == "Fantasy Football Coalition"
    assert payload["lineup"]["qb"] == 1
    assert payload["lineup"]["flex"] == 1
    assert payload["default_team_id"] == 10


def test_shipped_example_matches_the_contract() -> None:
    payload = json.loads((SITE / "dashboard.json").read_text(encoding="utf-8"))
    assert payload["version"] == "1.1"
    assert payload["example"] is True
    assert payload["anonymize_managers"] is False
    assert payload["default_team_id"] == 10
    assert payload["league"]["name"] == "Fantasy Football Coalition"
    teams = payload["teams"]
    assert isinstance(teams, list)
    assert len(teams) == 12
    purple = next(team for team in teams if team["team_id"] == 10)
    assert purple["manager_display"] == "Avery Quinn"
    assert purple["name"] == "Purple Reign"
    starters = purple["starters"]
    assert isinstance(starters, list)
    counts: dict[str, int] = {}
    for player in starters:
        slot = str(player["slot"])
        counts[slot] = counts.get(slot, 0) + 1
        assert player["q10"] is not None
        assert player["q50"] is not None
        assert player["q90"] is not None
        assert player["verdict"] in {"start", "monitor", "sit"}
    assert counts == {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "K": 1, "DST": 1}
    flex = next(player for player in starters if player["slot"] == "FLEX")
    assert flex["position"] in {"RB", "WR", "TE"}
    assert purple["win_probability"] is not None
    for team in teams:
        for bucket in ("starters", "bench", "inactive"):
            rows = team[bucket]
            assert isinstance(rows, list)
            for player in rows:
                _assert_drawn_series(player)
    waivers = payload["waivers"]
    assert isinstance(waivers, list)
    assert waivers
    for waiver in waivers:
        _assert_drawn_series(waiver, waiver=True)
    lab = payload["lab"]
    assert isinstance(lab, dict)
    assert lab["promoted"] is False
    notes = " ".join(lab["notes"])
    assert "not promoted" in notes
    assert "±0.3" in notes
    assert published_m3_lab()["promoted"] is False


def test_site_reads_the_contract_fields() -> None:
    script = (SITE / "app.js").read_text(encoding="utf-8")
    css = (SITE / "app.css").read_text(encoding="utf-8")
    for field in UI_FIELDS:
        assert field in script
    assert "Avery Quinn" not in script
    assert "Purple Reign" not in script
    assert "dashboard.json" in script
    # You is the owner's team (default_team_id). Viewing marks the selected team.
    assert "rowItem.team_id === data.default_team_id" in script
    assert ">You</span>" in script
    assert "rowItem.team_id === state.teamId && rowItem.team_id !== data.default_team_id" in script
    assert ">Viewing</span>" in script
    assert "not found, showing" in script
    assert "svg[hidden] { display: none; }" in css
    assert "grid-template-columns: minmax(0, 1fr)" in css
    assert ".wc { min-width: 0; }" in css
    assert ".wc .pm { white-space: normal; overflow-wrap: anywhere; }" in css
    assert "min-width: 44px" in css
    assert "function spark(" in script
    assert "Last 6 weeks" in script
    assert "Snap share" in script
    assert "Snap trend n/a" in script
    assert "recent_points" in script
    assert "values.length < 2" in script
    assert 'aria-hidden="true"' in script
    assert 'fill="var(--c)"' in script
    assert ".trend .up { color: var(--good); }" in css
    assert ".trend .dn { color: var(--bad); }" in css
    assert ".stat.wide" in css
    assert ".wm { display: flex;" in css
    assert "flex-wrap: wrap" in css


def _assert_drawn_series(player: dict[str, object], *, waiver: bool = False) -> None:
    if not waiver:
        points = player["recent_points"]
        assert isinstance(points, list)
        assert len(points) >= 2
    position = player["position"]
    if position in {"DST", "DEF"}:
        assert player["snap_share"] is None
    else:
        shares = player["snap_share"]
        assert isinstance(shares, list)
        assert len(shares) >= 2


def test_short_and_missing_series_stay_short() -> None:
    config = load_config(ROOT / "gridiron_lab.toml")
    roster = _mini_roster()
    players = roster["teams"][0]["players"]  # type: ignore[index]
    assert isinstance(players, list)
    players[0]["recent_points"] = [4.0, 9, "nope", 8, 7, 6, 5, 11]
    players[0]["snap_share"] = [70, 72]
    defense_row = next(player for player in players if player["player_id"] == "t10-dst")
    defense_row["recent_points"] = [3.0]
    defense_row["snap_share"] = [10, 20, 30]
    payload = build_dashboard(config, roster)
    teams = payload["teams"]
    assert isinstance(teams, list)
    rows = list(teams[0]["starters"]) + list(teams[0]["bench"])  # type: ignore[arg-type]
    quarterback = next(player for player in rows if player["player_id"] == "t10-qb")
    defense = next(player for player in rows if player["player_id"] == "t10-dst")
    runner = next(player for player in rows if player["player_id"] == "t10-rb1")
    assert quarterback["recent_points"] == [9.0, 8.0, 7.0, 6.0, 5.0, 11.0]
    assert quarterback["snap_share"] == [70.0, 72.0]
    assert defense["recent_points"] == [3.0]
    assert defense["snap_share"] is None
    assert runner["recent_points"] == []
    assert runner["snap_share"] is None
    waivers = payload["waivers"]
    assert isinstance(waivers, list)
    assert waivers[0]["snap_share"] is None


def test_store_history_replaces_roster_file_series(tmp_path: Path) -> None:
    from gridiron_lab.features import RecentSeries

    config = load_config(ROOT / "gridiron_lab.toml")
    roster = _mini_roster()
    players = roster["teams"][0]["players"]  # type: ignore[index]
    assert isinstance(players, list)
    players[0]["recent_points"] = [999.0, 999.0]
    players[0]["snap_share"] = [1.0, 2.0]
    players[-1]["recent_points"] = [8.0, 9.0]
    players[-1]["snap_share"] = [4.0, 5.0]
    history = {
        "t10-qb": RecentSeries(recent_points=(20.0, 18.0), snap_share=(80.0,)),
        "t10-dst": RecentSeries(recent_points=(6.0, 7.0), snap_share=(50.0, 55.0)),
        "waive-1": RecentSeries(recent_points=(1.0, 2.0), snap_share=(22.0, 41.0)),
    }
    payload = build_dashboard(config, roster, history=history)
    teams = payload["teams"]
    assert isinstance(teams, list)
    rows = list(teams[0]["starters"]) + list(teams[0]["bench"])  # type: ignore[arg-type]
    quarterback = next(player for player in rows if player["player_id"] == "t10-qb")
    defense = next(player for player in rows if player["player_id"] == "t10-dst")
    runner = next(player for player in rows if player["player_id"] == "t10-rb1")
    assert quarterback["recent_points"] == [20.0, 18.0]
    assert quarterback["snap_share"] == [80.0]
    assert 999.0 not in quarterback["recent_points"]
    assert defense["recent_points"] == [6.0, 7.0]
    assert defense["snap_share"] is None
    assert runner["recent_points"] == []
    assert runner["snap_share"] is None
    waivers = payload["waivers"]
    assert isinstance(waivers, list)
    assert waivers[0]["snap_share"] == [22.0, 41.0]


def test_lab_prose_says_def() -> None:
    criteria = published_m3_lab()["criteria"]
    assert isinstance(criteria, list)
    detail = next(item["detail"] for item in criteria if item["name"] == "bias")
    assert isinstance(detail, str)
    assert "DEF 2025" in detail
    assert "DST" not in detail


def test_first_screen_is_under_the_gzip_budget() -> None:
    total = 0
    for name in ("index.html", "app.css", "app.js"):
        raw = (SITE / name).read_bytes()
        total += len(gzip.compress(raw, mtime=0))
    assert total < 150 * 1024
