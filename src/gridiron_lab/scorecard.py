"""Weekly report cards: grade one week's published projections against
what actually happened. The Lab's report card renders the output.

Inputs are archived per week under ``scorecards/`` (see the manifest
there): the predictions file the site published from, and the dashboard
snapshot that carried those numbers to the page. Actuals come from one of
two recorded sources, named per week in the manifest:

- ``store`` — the store's ``player_weeks``: fantasy points under this
  league's scoring, computed at ingest. A rostered player whose team
  played but who recorded no stats grades as 0.00 (the did-not-play
  convention); players on a bye are excluded.
- ``yahoo_capture`` — Yahoo's published Fan Points for the week, captured
  from the league pages into a text file in the inputs directory. Players
  Yahoo no longer lists for that week (dropped from their rosters since)
  have no published line and are excluded as ungradeable.

Nothing here trains or predicts: it only grades.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from gridiron_lab.data.store import open_db

POSITIONS = ("QB", "RB", "WR", "TE", "K", "DEF")
_FLEX_POSITIONS = {"RB", "WR", "TE"}
# Dashboard team abbreviations the store spells differently.
_TEAM_ALIASES = {"LAR": "LA"}
# NFL mascot names, as Yahoo's pages label defenses, to store abbreviations.
_MASCOT_TO_ABBR = {
    "cardinals": "ARI",
    "falcons": "ATL",
    "ravens": "BAL",
    "bills": "BUF",
    "panthers": "CAR",
    "bears": "CHI",
    "bengals": "CIN",
    "browns": "CLE",
    "cowboys": "DAL",
    "broncos": "DEN",
    "lions": "DET",
    "packers": "GB",
    "texans": "HOU",
    "colts": "IND",
    "jaguars": "JAX",
    "chiefs": "KC",
    "raiders": "LV",
    "chargers": "LAC",
    "rams": "LA",
    "dolphins": "MIA",
    "vikings": "MIN",
    "patriots": "NE",
    "saints": "NO",
    "giants": "NYG",
    "jets": "NYJ",
    "eagles": "PHI",
    "steelers": "PIT",
    "49ers": "SF",
    "seahawks": "SEA",
    "buccaneers": "TB",
    "titans": "TEN",
    "commanders": "WAS",
}
# Legal starting slots beyond the flex, in the order they are filled.
_SLOT_COUNTS = (("QB", 1), ("RB", 2), ("WR", 2), ("TE", 1), ("K", 1), ("DEF", 1))
_STARTER_SLOTS = {"QB", "RB", "WR", "TE", "W/R/T", "K", "DEF"}
_FINAL_RE = re.compile(r"^(.+?) (\d+\.\d+) vs (.+?) (\d+\.\d+)$")
_TEAM_RE = re.compile(r"^TEAM (\d+) \|")


def _metrics(pairs: list[tuple[float, float]]) -> dict[str, float]:
    """MAE and bias (projection minus actual) for (projection, actual) pairs."""
    errors = [projection - actual for projection, actual in pairs]
    count = len(errors)
    return {
        "mae": round(sum(abs(error) for error in errors) / count, 2),
        "bias": round(sum(errors) / count, 2),
    }


def grade_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Grade merged player rows. Pure: every row already carries its actual.

    Each row needs name, position, team, mean, q50, q10, q90, yahoo, actual.
    Rows are the eligible universe (no byes, projections present).
    """
    headline = {
        key: _metrics([(row[key], row["actual"]) for row in rows])
        for key in ("mean", "q50", "yahoo")
    }
    ranged = [row for row in rows if row["q10"] is not None and row["q90"] is not None]
    coverage = (
        round(
            sum(1 for row in ranged if row["q10"] <= row["actual"] <= row["q90"]) / len(ranged),
            3,
        )
        if ranged
        else None
    )
    positions = []
    for position in POSITIONS:
        group = [row for row in rows if row["position"] == position]
        if not group:
            continue
        positions.append(
            {
                "pos": position,
                "n": len(group),
                "model": _metrics([(row["mean"], row["actual"]) for row in group]),
                "yahoo": _metrics([(row["yahoo"], row["actual"]) for row in group]),
            }
        )
    by_miss = sorted(rows, key=lambda row: (-abs(row["mean"] - row["actual"]), row["name"]))
    by_hit = sorted(rows, key=lambda row: (abs(row["mean"] - row["actual"]), row["name"]))

    def spotlight(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "name": row["name"],
            "pos": row["position"],
            "team": row["team"],
            "model": row["mean"],
            "yahoo": row["yahoo"],
            "actual": row["actual"],
        }

    return {
        "graded": len(rows),
        "headline": headline,
        "coverage": coverage,
        "positions": positions,
        "biggest_miss": spotlight(by_miss[0]) if by_miss else None,
        "biggest_hit": spotlight(by_hit[0]) if by_hit else None,
    }


def _optimal_total(rows: list[dict[str, Any]]) -> float:
    """Best legal lineup total from one team's graded rows, in hindsight."""
    picked: list[dict[str, Any]] = []
    used: set[int] = set()
    for position, count in _SLOT_COUNTS:
        pool = sorted(
            (row for row in rows if row["position"] == position),
            key=lambda row: (-row["actual"], row["name"]),
        )
        for row in pool[:count]:
            picked.append(row)
            used.add(id(row))
    flex = sorted(
        (row for row in rows if row["position"] in _FLEX_POSITIONS and id(row) not in used),
        key=lambda row: (-row["actual"], row["name"]),
    )
    picked.extend(flex[:1])
    return round(sum(row["actual"] for row in picked), 2)


def store_actuals(data_dir: Path, season: int, week: int) -> dict[str, float]:
    """Recorded fantasy points for one week, keyed by store player id.

    Skill players and kickers key by GSIS id; defenses key as ``DEF:<TEAM>``.
    The far-future as-of cutoff reads the latest recorded version of every
    row — grading always uses final stats, never a mid-week snapshot.
    """
    rows = open_db(data_dir).as_of("player_weeks", datetime(2030, 1, 1, tzinfo=UTC))
    actuals: dict[str, float] = {}
    for row in rows:
        if row.get("season") != season or row.get("week") != week:
            continue
        if row.get("season_type") not in (None, "REG"):
            continue
        points = row.get("fantasy_points")
        if points is None:
            continue
        actuals[str(row["player_id"])] = float(points)
    return actuals


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def capture_actuals(
    text: str, dashboard: dict[str, Any]
) -> tuple[dict[str, float], set[str], list[dict[str, Any]]]:
    """Parse a Yahoo-capture text file into actuals keyed by player id.

    Returns (actuals, bye player ids, finals). Did-not-play lines grade as
    0.00. Defense lines carry Yahoo's mascot names ("Raiders", "Seahawks")
    where the dashboard uses city names ("Las Vegas", "Seattle"), so a
    defense line resolves by mascot when the plain name match fails. A
    line that matches no rostered player — a defense picked up after the
    freeze, say — is ignored: unpredicted players stay ungraded.
    """
    teams = {team["team_id"]: team for team in dashboard["teams"]}
    actuals: dict[str, float] = {}
    byes: set[str] = set()
    finals: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    slug_to_pid: dict[str, str] = {}
    def_by_abbr: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        final_match = _FINAL_RE.match(line)
        if final_match and current is None:
            finals.append(
                {
                    "home": final_match.group(1),
                    "home_total": float(final_match.group(2)),
                    "away": final_match.group(3),
                    "away_total": float(final_match.group(4)),
                }
            )
            continue
        team_match = _TEAM_RE.match(line)
        if team_match:
            current = teams.get(int(team_match.group(1)))
            slug_to_pid = {}
            def_by_abbr = {}
            if current is not None:
                for group in ("starters", "bench", "inactive"):
                    for player in current.get(group, []):
                        slug_to_pid[_slug(player["name"])] = player["player_id"]
                        if _normalize_position(player["position"]) == "DEF":
                            store_id = _defense_store_id(player.get("nfl_team"))
                            if store_id is not None:
                                def_by_abbr[store_id.removeprefix("DEF:")] = player["player_id"]
            continue
        if current is None:
            continue
        parts = [part.strip() for part in line.split("|")]
        if len(parts) != 3 or parts[0] not in _STARTER_SLOTS | {"BN", "IR"}:
            continue
        slot, name, value = parts
        pid = slug_to_pid.get(_slug(name))
        if pid is None:
            abbr = _MASCOT_TO_ABBR.get(_slug(name))
            if abbr is not None:
                pid = def_by_abbr.get(abbr)
        if pid is None:
            continue
        if value == "bye":
            byes.add(pid)
        else:
            actuals[pid] = 0.0 if value == "DNP" else float(value)
    return actuals, byes, finals


def _normalize_position(position: str) -> str:
    return "DEF" if position in ("DEF", "DST") else position


def _defense_store_id(nfl_team: str | None) -> str | None:
    if not nfl_team:
        return None
    abbr = nfl_team.upper()
    return f"DEF:{_TEAM_ALIASES.get(abbr, abbr)}"


def build_week(entry: dict[str, Any], inputs_dir: Path, data_dir: Path) -> dict[str, Any]:
    """Assemble and grade one week from its archived inputs."""
    predictions = json.loads((inputs_dir / entry["predictions"]).read_text(encoding="utf-8"))
    published = {row["player_id"]: row for row in predictions["players"]}
    dashboard = json.loads((inputs_dir / entry["dashboard"]).read_text(encoding="utf-8"))
    source = entry["actuals"]
    if source == "store":
        recorded = store_actuals(data_dir, entry["season"], entry["week"])
        capture = None
    elif source == "yahoo_capture":
        text = (inputs_dir / entry["actuals_file"]).read_text(encoding="utf-8")
        capture = capture_actuals(text, dashboard)
        recorded = None
    else:
        raise ValueError(f"Unknown actuals source: {source}")

    rows: list[dict[str, Any]] = []
    starters: dict[str, list[dict[str, Any]]] = {}
    team_rows: dict[str, list[dict[str, Any]]] = {}
    unmatched = 0
    ungradeable = 0
    zero_no_stats = 0
    for team in dashboard["teams"]:
        for group in ("starters", "bench", "inactive"):
            for player in team.get(group, []):
                forecast = published.get(player["player_id"])
                if forecast is None:
                    # Inactive-list players carry no published projection;
                    # only a starter or bench player missing one is a gap.
                    if group != "inactive":
                        unmatched += 1
                    continue
                if player.get("bye") or forecast.get("mean") is None:
                    continue
                position = _normalize_position(player["position"])
                if capture is not None:
                    cap_actuals, cap_byes, _ = capture
                    if player["player_id"] in cap_byes:
                        continue
                    actual = cap_actuals.get(player["player_id"])
                    if actual is None:
                        ungradeable += 1
                        continue
                else:
                    assert recorded is not None
                    if position == "DEF":
                        store_id = _defense_store_id(player.get("nfl_team"))
                    else:
                        store_id = forecast.get("gsis_id")
                    actual = recorded.get(store_id) if store_id else None
                    if actual is None:
                        # His team played and he recorded nothing: 0.00.
                        actual = 0.0
                        zero_no_stats += 1
                row = {
                    "name": player["name"],
                    "position": position,
                    "team": team["name"],
                    "mean": forecast["mean"],
                    "q50": forecast["q50"],
                    "q10": forecast["q10"],
                    "q90": forecast["q90"],
                    "yahoo": forecast["yahoo"],
                    "actual": actual,
                }
                rows.append(row)
                team_rows.setdefault(team["name"], []).append(row)
                if group == "starters":
                    starters.setdefault(team["name"], []).append(row)
    card = grade_rows(rows)
    card["season"] = entry["season"]
    card["week"] = entry["week"]
    card["provenance"] = entry["provenance"]
    card["zero_no_stats"] = zero_no_stats
    card["unmatched"] = unmatched
    card["ungradeable"] = ungradeable
    focus_name = entry.get("focus_team")
    if capture is not None and focus_name:
        for final in capture[2]:
            if focus_name in (final["home"], final["away"]):
                if final["home"] == focus_name:
                    card["final"] = {
                        "opponent": final["away"],
                        "focus_total": final["home_total"],
                        "opponent_total": final["away_total"],
                    }
                else:
                    card["final"] = {
                        "opponent": final["home"],
                        "focus_total": final["away_total"],
                        "opponent_total": final["home_total"],
                    }
    if focus_name and team_rows.get(focus_name):
        card["focus"] = {
            "team": focus_name,
            "recommended_total": round(
                sum(row["actual"] for row in starters.get(focus_name, [])), 2
            ),
            "optimal_total": _optimal_total(team_rows[focus_name]),
        }
    return card


def build_scorecards(inputs_dir: Path, data_dir: Path) -> dict[str, Any]:
    """Grade every week in the manifest; weeks come out oldest first."""
    manifest = json.loads((inputs_dir / "manifest.json").read_text(encoding="utf-8"))
    focus_team = manifest.get("focus_team")
    weeks = []
    for entry in manifest["weeks"]:
        weeks.append(build_week({**entry, "focus_team": focus_team}, inputs_dir, data_dir))
    weeks.sort(key=lambda card: (card["season"], card["week"]))
    return {"version": 1, "weeks": weeks}
