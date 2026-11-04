"""Weekly report cards: grading math, capture parsing, and the shipped file."""

from __future__ import annotations

import json
from pathlib import Path

from gridiron_lab.scorecard import _optimal_total, capture_actuals, grade_rows

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"


def _row(
    name: str,
    position: str,
    mean: float,
    actual: float,
    yahoo: float | None = None,
) -> dict[str, object]:
    return {
        "name": name,
        "position": position,
        "team": "Team",
        "mean": mean,
        "q50": mean,
        "q10": mean - 5.0,
        "q90": mean + 5.0,
        "yahoo": mean if yahoo is None else yahoo,
        "actual": actual,
    }


def test_grade_rows_metrics_and_spotlights() -> None:
    rows = [
        _row("Alpha", "QB", 20.0, 22.0, yahoo=18.0),
        _row("Beta", "QB", 10.0, 4.0, yahoo=9.0),
        _row("Gamma", "RB", 8.0, 8.5, yahoo=12.0),
    ]
    card = grade_rows(rows)
    assert card["graded"] == 3
    # Mean errors: -2, +6, -0.5 -> MAE 8.5/3, bias 3.5/3.
    assert card["headline"]["mean"] == {"mae": 2.83, "bias": 1.17}
    # Yahoo errors: -4, +5, +3.5 -> MAE 12.5/3, bias 4.5/3.
    assert card["headline"]["yahoo"] == {"mae": 4.17, "bias": 1.5}
    # Alpha (22 in [15, 25]) and Gamma (8.5 in [3, 13]) land in range; Beta
    # (4 below [5, 15]) does not.
    assert card["coverage"] == 0.667
    assert card["biggest_miss"]["name"] == "Beta"
    assert card["biggest_hit"]["name"] == "Gamma"
    positions = {item["pos"]: item for item in card["positions"]}
    assert positions["QB"]["n"] == 2
    assert positions["RB"]["model"]["mae"] == 0.5


def test_optimal_total_fills_slots_then_flex() -> None:
    rows = [
        _row("Q1", "QB", 0.0, 10.0),
        _row("R1", "RB", 0.0, 9.0),
        _row("R2", "RB", 0.0, 8.0),
        _row("R3", "RB", 0.0, 7.0),
        _row("W1", "WR", 0.0, 6.0),
        _row("W2", "WR", 0.0, 5.0),
        _row("T1", "TE", 0.0, 4.0),
        _row("K1", "K", 0.0, 3.0),
        _row("D1", "DEF", 0.0, 2.0),
    ]
    # Slots take 10+9+8+6+5+4+3+2 = 47; the flex adds R3's 7.
    assert _optimal_total(rows) == 54.0


def _mini_dashboard() -> dict[str, object]:
    def player(pid: str, name: str, position: str, nfl: str) -> dict[str, object]:
        return {
            "player_id": pid,
            "name": name,
            "position": position,
            "nfl_team": nfl,
            "bye": False,
        }

    return {
        "teams": [
            {
                "team_id": 5,
                "name": "Lynch Mob",
                "starters": [player("yh-t5-las-vegas", "Las Vegas", "DEF", "LV")],
                "bench": [player("yh-t5-josh-jacobs", "Josh Jacobs", "RB", "LV")],
                "inactive": [player("yh-t5-houston", "Houston", "DEF", "Hou")],
            }
        ]
    }


def test_capture_actuals_resolves_mascot_defenses() -> None:
    text = (
        "WEEK 8 FINALS\n"
        "Lynch Mob 30.00 vs Someone 20.00\n"
        "\n"
        "TEAM 5 | Lynch Mob\n"
        "RB | Josh Jacobs | 17.20\n"
        "DEF | Raiders | 14.76\n"
        "BN | Texans | bye\n"
    )
    actuals, byes, finals = capture_actuals(text, _mini_dashboard())
    # The mascot line lands on the city-named defense; the benched defense
    # is a bye, not a zero.
    assert actuals == {"yh-t5-josh-jacobs": 17.2, "yh-t5-las-vegas": 14.76}
    assert byes == {"yh-t5-houston"}
    assert finals[0]["home_total"] == 30.0


def test_lab_has_a_report_card_slot_and_renderer() -> None:
    index = (SITE / "index.html").read_text(encoding="utf-8")
    app = (SITE / "app.js").read_text(encoding="utf-8")
    # The card sits at the top of the Lab, above the backtest blocks, and
    # is rendered from scorecards.json when that file loads.
    assert '<div id="report-root"></div>' in index
    assert index.index('id="report-root"') < index.index('id="lab-root"')
    assert 'fetch("scorecards.json")' in app
    assert "function renderReport()" in app
    # Owner call (2026-11-04): MAE carries its definition hover everywhere
    # the Lab mentions it — he can never remember what it means.
    assert 'mae: "Mean absolute error' in app
    assert 'defLabel("mae", "MAE")' in app
    assert "function defMae(" in app
    # Owner call (2026-11-04): the winning MAE in the report card is green
    # text (the site's --good), not just bold.
    assert 'class="win"' in app
    css = (SITE / "app.css").read_text(encoding="utf-8")
    assert ".rep-tbl .win { color: var(--good)" in css
    # Owner call (2026-11-04): the forecaster is named AUSPICIA, not
    # "Model", on the report card and across the Lab's label surfaces.
    assert 'headRow("AUSPICIA (mean)"' in app
    assert 'headRow("AUSPICIA (median)"' in app
    assert "Model (mean)" not in app
    assert "minus AUSPICIA. Positive means AUSPICIA was closer" in app
    # Owner call (2026-11-04): the Lab's top "not promoted" note bar is
    # gone; the Promotion rule card's title carries the verdict instead.
    assert 'role="note"' not in app
    assert "Promotion rule — Promoted: " in app


def test_shipped_scorecards_cover_weeks_7_and_8() -> None:
    payload = json.loads((SITE / "scorecards.json").read_text(encoding="utf-8"))
    weeks = {card["week"]: card for card in payload["weeks"]}
    assert sorted(weeks) == [7, 8]
    for card in weeks.values():
        assert set(card["headline"]) == {"mean", "q50", "yahoo"}
        assert card["focus"]["team"] == "Purple Reign"
    # The Week 8 count reconciles with the hand-checked markdown scorecard:
    # 155 frozen predictions, 6 ungradeable (Yahoo no longer lists them).
    assert weeks[8]["graded"] == 149
    assert weeks[8]["ungradeable"] == 6
    assert weeks[8]["final"]["focus_total"] == 119.16
