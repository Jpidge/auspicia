"""Dashboard export, the example file, and the static site contract."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from gridiron_lab.cli import run
from gridiron_lab.config import load_config
from gridiron_lab.dashboard import (
    DASHBOARD_VERSION,
    DashboardError,
    _volatility_percentiles,
    build_dashboard,
    lab_block_from_report,
    published_m3_lab,
)

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
ROSTERS = ROOT / "examples" / "dashboard" / "rosters.json"
# Fields the page reads.
UI_FIELDS = (
    "default_team_id",
    "q10",
    "q50",
    "q90",
    "verdict",
    "volatility",
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


def test_exports_carry_no_manager_names() -> None:
    config = load_config(ROOT / "gridiron_lab.toml")
    shown = build_dashboard(config, _mini_roster())
    teams = shown["teams"]
    assert isinstance(teams, list)
    assert teams[0]["name"] == "Purple Reign"
    assert all("manager_display" not in team for team in teams if isinstance(team, dict))
    standings = shown["standings"]
    assert isinstance(standings, list)
    assert all("manager_display" not in row for row in standings if isinstance(row, dict))
    text = json.dumps(shown)
    assert "Avery Quinn" not in text
    assert "Drew Okada" not in text


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


def test_volatility_percentiles_rank_within_position() -> None:
    def row(player_id: str, position: str, q10: float, q90: float) -> dict[str, object]:
        return {"player_id": player_id, "position": position, "q10": q10, "q90": q90}

    players: list[dict[str, object]] = [
        row("rb-narrow", "RB", 4, 8),
        row("rb-mid", "RB", 2, 12),
        row("rb-wide", "RB", 0, 16),
        row("wr-wide", "WR", 0, 30),
        {"player_id": "rb-none", "position": "RB"},
        row("xx-wide", "XX", 0, 99),
    ]
    assert _volatility_percentiles(players) == {
        "rb-narrow": 33,
        "rb-mid": 67,
        "rb-wide": 100,
        "wr-wide": 100,
    }


def test_volatility_reaches_the_player_row() -> None:
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
    assert qb["volatility"] == 100
    bench = teams[0]["bench"]
    assert isinstance(bench, list)
    assert all(player["volatility"] is None for player in bench)


def test_prediction_vegas_implied_reaches_the_player_row() -> None:
    config = load_config(ROOT / "gridiron_lab.toml")
    payload = build_dashboard(
        config,
        _mini_roster(),
        {
            "players": [
                {
                    "player_id": "t10-qb",
                    "q50": 30,
                    "q10": 20,
                    "q90": 40,
                    "mean": 31,
                    "vegas_implied": 26.75,
                }
            ]
        },
    )
    teams = payload["teams"]
    assert isinstance(teams, list)
    starters = teams[0]["starters"]
    assert isinstance(starters, list)
    qb = next(player for player in starters if player["player_id"] == "t10-qb")
    runner = next(player for player in starters if player["player_id"] == "t10-rb1")
    assert qb["vegas_implied"] == 26.75
    assert runner["vegas_implied"] is None


def test_prediction_opp_rank_and_practice_reach_the_player_row() -> None:
    config = load_config(ROOT / "gridiron_lab.toml")
    payload = build_dashboard(
        config,
        _mini_roster(),
        {
            "players": [
                {
                    "player_id": "t10-qb",
                    "q50": 30,
                    "q10": 20,
                    "q90": 40,
                    "mean": 31,
                    "opp_rank": 4,
                    "practice": "LP",
                }
            ]
        },
    )
    teams = payload["teams"]
    assert isinstance(teams, list)
    starters = teams[0]["starters"]
    assert isinstance(starters, list)
    qb = next(player for player in starters if player["player_id"] == "t10-qb")
    runner = next(player for player in starters if player["player_id"] == "t10-rb1")
    assert qb["opp_rank"] == 4
    assert qb["practice"] == "LP"
    assert runner["opp_rank"] is None
    assert runner["practice"] is None


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


def test_shipped_dashboard_matches_the_contract() -> None:
    payload = json.loads((SITE / "dashboard.json").read_text(encoding="utf-8"))
    assert payload["version"] == "1.1"
    assert payload["example"] is False
    assert payload["default_team_id"] == 10
    assert payload["league"]["name"] == "Fantasy Football Coalition"
    teams = payload["teams"]
    assert isinstance(teams, list)
    assert len(teams) == 12
    purple = next(team for team in teams if team["team_id"] == 10)
    assert "manager_display" not in purple
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
    # The name bridge filled real 2026 series for nearly every rostered
    # player; the handful without one have not played a 2026 snap.
    drawn = sum(
        1
        for team in teams
        for bucket in ("starters", "bench", "inactive")
        for player in team[bucket]
        if isinstance(player["recent_points"], list) and len(player["recent_points"]) >= 2
    )
    assert drawn >= 175
    assert sum(1 for player in starters if len(player["recent_points"]) >= 2) >= 8
    waivers = payload["waivers"]
    assert isinstance(waivers, list)
    assert waivers
    for waiver in waivers:
        _assert_drawn_series(waiver, waiver=True)
    lab = payload["lab"]
    assert isinstance(lab, dict)
    assert lab["promoted"] is False
    assert lab["source"] == "evaluation_report"
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
    assert "[hidden] { display: none !important; }" in css
    # Definitions: one DEFS table feeds the label tooltips and the card.
    assert "data-def" in script
    # The tooltip is one fixed, viewport-clamped element: a per-label
    # ::after is clipped by the player row's overflow:hidden.
    assert "def-tip" in script
    assert "#def-tip" in css
    assert "position: fixed" in css
    assert ".def::after" not in css
    # Main cards use the same 24px breathing room in every view, including
    # the standalone engine card after the Lab grid.
    assert (
        ".lab { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 24px; }"
    ) in css
    assert ".engine-card { margin-top: 24px; }" in css
    assert (
        ".wgrid { display: grid; grid-template-columns: "
        "repeat(auto-fill, minmax(300px, 1fr)); gap: 24px; }"
    ) in css
    assert 'class="defs"' in script
    assert "Steady under 34" in script
    assert "the lineup is picked on the mean, not the median" in script
    # Stale median-objective copy is gone (the objective is the mean, 0.5.20).
    assert "maximizes median" not in script
    assert "maximizes mean (expected) points" in script
    assert "median</small>" not in script
    assert "mean</small>" in script
    assert "svg[hidden] { display: none; }" in css
    note = (SITE / "index.html").read_text(encoding="utf-8")
    assert 'id="team-note"' in note
    assert 'class="note" id="team-note" role="status" hidden' in note
    assert "note.hidden = true" in script
    assert "note.hidden = false" in script
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
    assert '"var(--good)"' in script
    assert '"var(--bad)"' in script
    assert 'style="color:' in script
    assert ".dgrid .stat:first-child { grid-column: 1 / -1; }" in css
    assert ".wm svg.spark { width: 90px;" in css
    assert ".wm { display: flex;" in css
    assert "flex-wrap: wrap" in css


def _assert_drawn_series(player: dict[str, object], *, waiver: bool = False) -> None:
    if not waiver:
        points = player["recent_points"]
        assert isinstance(points, list)
    position = player["position"]
    if position in {"DST", "DEF"}:
        assert player["snap_share"] is None
    else:
        shares = player["snap_share"]
        assert shares is None or isinstance(shares, list)


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


def test_site_start_pill_shows_on_mobile_and_is_green() -> None:
    css = (SITE / "app.css").read_text(encoding="utf-8")
    assert ".v-start { display: none; }" not in css
    assert ".v-start { background: var(--good); color: #0b2013; }" in css
    assert "background: var(--good)" in css


def test_site_coverage_rows_have_dots_on_a_zoomed_scale() -> None:
    script = (SITE / "app.js").read_text(encoding="utf-8")
    css = (SITE / "app.css").read_text(encoding="utf-8")
    assert 'class="l cov"' in script
    assert 'class="band"' in script
    assert 'class="dot"' in script
    assert "(value - 0.70) / 0.20" in script
    assert "axis-row" in script
    assert ".mae .band" in css
    assert "left: 35%" in css
    assert "width: 30%" in css
    assert ".mae .dot" in css


def test_site_has_a_single_charcoal_theme() -> None:
    html = (SITE / "index.html").read_text(encoding="utf-8")
    script = (SITE / "app.js").read_text(encoding="utf-8")
    css = (SITE / "app.css").read_text(encoding="utf-8")
    # The theme system was retired by owner decision (2026-10-31): one
    # charcoal scheme, no dropdown, no stored preference, no palettes.
    assert 'id="theme-select"' not in html
    assert "[data-theme" not in css
    assert "gl-theme" not in script
    assert "THEME_MODES" not in script
    # Charcoal chrome tokens (the former Carbon palette, promoted to :root).
    for hex_value in ("#0d0e0f", "#16181a", "#1e2023", "#2e3136", "#f2f3f4", "#8a9096"):
        assert hex_value in css
    # Data colours are unchanged: colour is reserved for data and the emblem.
    for hex_value in ("#3ddc84", "#ff6b6b", "#ffc24b"):
        assert hex_value in css


def test_site_header_brand_gap_is_wide() -> None:
    css = (SITE / "app.css").read_text(encoding="utf-8")
    # Owner call (2026-10-31): roughly four to five spaces of air between
    # the emblem and the page title in the header brand row.
    assert ".brand { display: flex; align-items: center; gap: 30px;" in css


def test_site_control_center_states_the_engine_record() -> None:
    html = (SITE / "control.html").read_text(encoding="utf-8")
    assert "Control Center" in html
    # Owner call (2026-10-31): the back-link to the dashboard says AUSPICIA.
    assert 'class="cc-back" href="index.html">AUSPICIA</a>' in html
    assert "Dashboard →" not in html
    assert 'href="app.css"' in html
    assert 'src="workflow.svg"' in html
    assert (SITE / "workflow.svg").exists()
    # The dashboard footer links to it; it is a separate page, not a tab.
    index = (SITE / "index.html").read_text(encoding="utf-8")
    assert 'href="control.html"' in index
    # The page explains what promotion is: a title, not a usage switch.
    assert "a title, not a switch" in html
    assert "already runs this model's projections" in html
    assert "<dt>Promoted</dt>" in html
    # Gate status is stated honestly: not promoted, bias is the failure.
    assert "Promoted:" in html
    assert '<span class="fail">No</span>' in html
    assert '<span class="fail">FAIL</span>' in html
    # Benchmark figures from the 2022-2025 evaluation report.
    for figure in ("3.63", "4.03", "3.65", "4.00", "1,591.28", "81.9"):
        assert figure in html
    # The theme system stays retired on this page too.
    assert 'id="theme-select"' not in html


def test_site_workflow_diagram_uses_the_charcoal_scheme() -> None:
    svg = (SITE / "workflow.svg").read_text(encoding="utf-8")
    # The diagram was recoloured from the retired navy chrome to charcoal;
    # geometry and labels are unchanged, only fills and strokes moved.
    assert "#0B1324" not in svg
    assert "#16233F" not in svg
    assert "#1e2023" in svg
    assert "#2e3136" in svg


def test_first_screen_is_under_the_gzip_budget() -> None:
    total = 0
    for name in ("index.html", "app.css", "app.js"):
        raw = (SITE / name).read_bytes()
        total += len(gzip.compress(raw, mtime=0))
    assert total < 150 * 1024


def _lab_report(primary: str = "q50") -> dict[str, object]:
    """A synthetic evaluate report with a failing bias and coverage gate."""

    def block(
        mae: float, mae_mean: float, baseline: float, bias: float, coverage: float
    ) -> dict[str, object]:
        return {
            "n": 100,
            "mae": mae,
            "mae_mean": mae_mean,
            "mae_baseline": baseline,
            "bias": bias,
            "coverage": coverage,
            "mae_diff": mae - baseline,
            "mae_mean_diff": mae_mean - baseline,
        }

    folds = []
    for season, diff, mean_diff in ((2024, -0.30, -0.28), (2025, -0.38, -0.36)):
        folds.append(
            {
                "season": season,
                "positions": {
                    "QB": block(7.4, 7.6, 7.9, 0.716, 0.824),
                    "RB": block(3.8, 3.9, 4.16, -0.158, 0.808),
                    "WR": block(3.25, 3.3, 3.54, 0.05, 0.819),
                    "TE": block(2.39, 2.45, 2.69, 0.02, 0.844),
                    "K": block(4.05, 4.1, 4.41, -0.552, 0.800),
                    "DST": block(4.34, 4.4, 4.74, -1.513, 0.805),
                },
                "pooled_offense": {"mae_diff": diff, "mae_mean_diff": mean_diff},
                "weeks": [],
            }
        )
    coverage = {
        "QB": {"coverage": 0.802, "n": 500},
        "RB": {"coverage": 0.809, "n": 900},
        "WR": {"coverage": 0.825, "n": 900},
        "TE": {"coverage": 0.846, "n": 300},
        "K": {"coverage": 0.798, "n": 200},
        "DST": {"coverage": 0.804, "n": 200},
    }
    return {
        "experiment": {"model_version": "lgbm_skill_v1", "primary_mae": primary},
        "folds": folds,
        "calibration": {"coverage_by_position": coverage},
        "lineup": {
            "points_gained_total": 1750.0,
            "points_gained_q50_total": 1600.5,
            "points_gained_per_week": [{"season": 2024, "week": 1}, {"season": 2025, "week": 1}],
        },
        "promotion": {
            "promoted": False,
            "mae_seasons": {
                "pass": True,
                "seasons_passing": 2,
                "required": 2,
                "detail": [{"season": 2024}, {"season": 2025}],
            },
            "position_mae": {"pass": True, "worse_seasons": {}, "failures": {}},
            "bias": {
                "pass": False,
                "limit": 0.3,
                "head": "mean",
                "failures": [
                    {"season": 2025, "position": "DST", "bias": -1.513, "head": "mean"},
                    {"season": 2024, "position": "QB", "bias": 0.7, "head": "mean"},
                ],
            },
            "coverage": {
                "pass": False,
                "band": [0.77, 0.83],
                "failures": ["TE"],
                "by_position": coverage,
            },
            "lineup_points": {
                "pass": True,
                "points_gained": 1750.0,
                "points_gained_q50_head": 1600.5,
                "graded_on": "mean",
            },
        },
    }


def test_lab_block_from_report_builds_the_block() -> None:
    lab = lab_block_from_report(_lab_report())
    assert lab["source"] == "evaluation_report"
    assert lab["model_version"] == "lgbm_skill_v1"
    assert lab["primary_mae"] == "q50"
    assert lab["promoted"] is False
    # The graded mean figure, not the q50-head total.
    assert lab["points_gained_vs_baseline"] == 1750.0
    assert lab["mae_diff_by_season"] == [
        {"season": 2024, "mae_diff": -0.30},
        {"season": 2025, "mae_diff": -0.38},
    ]
    pooled = lab["pooled_coverage"]
    assert isinstance(pooled, dict)
    assert pooled["TE"] == 0.846
    positions = lab["positions"]
    assert isinstance(positions, list)
    assert [row["position"] for row in positions] == ["QB", "RB", "WR", "TE", "K", "DST"]
    qb = positions[0]
    assert qb["seasons"][0] == {
        "season": 2024,
        "mae": 7.4,
        "mae_baseline": 7.9,
        "bias": 0.716,
        "coverage": 0.824,
    }
    criteria = lab["criteria"]
    assert isinstance(criteria, list)
    assert [item["name"] for item in criteria] == [
        "mae_seasons",
        "position_mae",
        "bias",
        "coverage",
        "lineup_points",
    ]
    by_name = {item["name"]: item for item in criteria}
    assert "in 2 of 2 seasons" in str(by_name["mae_seasons"]["detail"])
    bias_detail = str(by_name["bias"]["detail"])
    assert "DEF 2025" in bias_detail
    assert "QB 2024" in bias_detail
    assert "DST" not in bias_detail
    coverage_detail = str(by_name["coverage"]["detail"])
    assert "TE pooled coverage is 0.846, above the 0.83 cap." in coverage_detail
    assert "The other positions are inside the band." in coverage_detail
    lineup_detail = str(by_name["lineup_points"]["detail"])
    assert "+1750.0 lineup points" in lineup_detail
    assert "across 2 weeks" in lineup_detail
    notes = " ".join(lab["notes"])  # type: ignore[arg-type]
    assert "not promoted" in notes
    assert "±0.3" in notes


def test_lab_block_from_report_uses_the_mean_head_when_selected() -> None:
    lab = lab_block_from_report(_lab_report(primary="mean"))
    assert lab["primary_mae"] == "mean"
    positions = lab["positions"]
    assert isinstance(positions, list)
    qb = positions[0]
    assert qb["seasons"][0]["mae"] == 7.6
    assert lab["mae_diff_by_season"][0] == {"season": 2024, "mae_diff": -0.28}  # type: ignore[index]


def test_lab_block_from_report_rejects_incomplete_reports() -> None:
    with pytest.raises(DashboardError):
        lab_block_from_report({})
    report = _lab_report()
    report["folds"] = []
    with pytest.raises(DashboardError):
        lab_block_from_report(report)
    report = _lab_report()
    lineup = report["lineup"]
    assert isinstance(lineup, dict)
    lineup["points_gained_total"] = None
    with pytest.raises(DashboardError):
        lab_block_from_report(report)
    report = _lab_report()
    del report["promotion"]
    with pytest.raises(DashboardError):
        lab_block_from_report(report)


def test_export_command_with_lab_report(tmp_path: Path) -> None:
    source = tmp_path / "rosters.json"
    source.write_text(json.dumps(_mini_roster()), encoding="utf-8")
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(_lab_report()), encoding="utf-8")
    output = tmp_path / "out" / "dashboard.json"
    code = run(
        [
            "export-dashboard",
            "--rosters",
            str(source),
            "--output",
            str(output),
            "--lab-report",
            str(report_path),
            "--config",
            str(ROOT / "gridiron_lab.toml"),
        ]
    )
    assert code == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    lab = payload["lab"]
    assert lab["source"] == "evaluation_report"
    assert lab["points_gained_vs_baseline"] == 1750.0
    assert lab["promoted"] is False
