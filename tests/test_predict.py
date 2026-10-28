"""The predict path: training filter, roster bridge, and output guards."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from gridiron_lab.models import Prediction
from gridiron_lab.predict import (
    PredictError,
    build_predictions,
    normalize_name,
    normalize_team,
    run_predict,
)


class _Stub:
    """Records what it was fit on and returns one fixed distribution."""

    def __init__(self) -> None:
        self.fit_rows: list[Mapping[str, object]] = []
        self.trained_through: str | None = None

    def fit(self, rows: Sequence[Mapping[str, object]], *, trained_through: str) -> None:
        self.fit_rows = list(rows)
        self.trained_through = trained_through

    def predict(self, rows: Sequence[Mapping[str, object]]) -> list[Prediction]:
        return [
            Prediction(
                mean=11.0,
                q10=4.0,
                q50=10.0,
                q90=16.0,
                model_version="stub_v0",
                feature_version="v2.0",
                scoring_version="ffc_yahoo_v1",
                trained_through=self.trained_through,
            )
            for _ in rows
        ]


def _row(
    player_id: str,
    season: int,
    week: int,
    position: str,
    team: str,
    **overrides: object,
) -> dict[str, object]:
    row: dict[str, object] = {
        "player_id": player_id,
        "season": season,
        "week": week,
        "position": position,
        "team": team,
        "force_zero": 0,
    }
    row.update(overrides)
    return row


def _frames() -> dict[tuple[int, int], list[dict[str, object]]]:
    train = [
        _row("g1", 2024, 1, "WR", "LAR", actual=9.0),
        _row("g2", 2024, 1, "QB", "KC", actual=20.0),
        _row("g8", 2024, 1, "RB", "DEN", actual=5.0, force_zero=1),
        _row("g9", 2024, 1, "RB", "DEN"),
    ]
    target = [
        _row("g1", 2024, 2, "WR", "LAR"),
        _row("g2", 2024, 2, "QB", "KC"),
        _row("DEF:GB", 2024, 2, "DST", "GB"),
        _row("DEF:LA", 2024, 2, "DST", "LA"),
        _row("g4", 2024, 2, "RB", "MIA"),
        _row("g5", 2024, 2, "RB", "BUF"),
        _row("g6", 2024, 2, "WR", "KC"),
        _row("g7", 2024, 2, "WR", "DEN"),
    ]
    return {(2024, 1): train, (2024, 2): target}


_NAMES = {
    "g1": "Test Catcher",
    "g2": "Test Passer",
    "g4": "Sam Smith",
    "g5": "Sam Smith",
    "g6": "Pat Twin",
    "g7": "Pat Twin",
}


def _roster() -> dict[str, object]:
    players = [
        {
            "player_id": "yh-a",
            "name": "Test Catcher",
            "position": "WR",
            "nfl_team": "LA",
            "yahoo": 12.5,
        },
        {"player_id": "yh-b", "name": "Test Passer", "position": "QB", "nfl_team": "KC"},
        {"player_id": "yh-def", "name": "Green Bay Packers", "position": "DEF", "nfl_team": "GB"},
        {"player_id": "yh-def2", "name": "Rams", "position": "DEF", "nfl_team": "LA"},
        {"player_id": "yh-c", "name": "Sam Smith Jr.", "position": "RB", "nfl_team": "Buf"},
        {"player_id": "yh-d", "name": "Pat Twin", "position": "WR", "nfl_team": "NYJ"},
        {
            "player_id": "yh-e",
            "name": "Test Catcher",
            "position": "WR",
            "nfl_team": "LA",
            "bye": True,
        },
        {
            "player_id": "yh-f",
            "name": "Test Passer",
            "position": "QB",
            "nfl_team": "KC",
            "force_zero": True,
        },
    ]
    return {"teams": [{"team_id": 1, "players": players}]}


def test_normalize_name_strips_suffixes_punctuation_and_accents() -> None:
    assert normalize_name("Brian Robinson Jr.") == "brian robinson"
    assert normalize_name("Amon-Ra St. Brown") == "amon ra st brown"
    assert normalize_name("Ja'Marr Chase") == "jamarr chase"
    assert normalize_name("Marvin Harrison III") == "marvin harrison"


def test_normalize_team_applies_aliases() -> None:
    assert normalize_team("LA") == "LAR"
    assert normalize_team("Jax") == "JAX"
    assert normalize_team("GB") == "GB"


def test_build_predictions_bridges_and_guards() -> None:
    stub = _Stub()
    payload = build_predictions(_frames(), 2024, 2, _roster(), _NAMES, make_predictor=lambda: stub)

    assert payload["trained_through"] == "2024-W01"
    assert payload["conformal_adjusted"] is False
    fit_ids = {row["player_id"] for row in stub.fit_rows}
    assert fit_ids == {"g1", "g2"}

    players = {entry["player_id"]: entry for entry in payload["players"]}  # type: ignore[attr-defined]
    assert set(players) == {"yh-a", "yh-b", "yh-def", "yh-def2", "yh-c"}
    assert players["yh-def2"]["gsis_id"] == "DEF:LA"
    assert players["yh-a"]["gsis_id"] == "g1"
    assert players["yh-a"]["q50"] == 10.0
    assert players["yh-a"]["yahoo"] == 12.5
    assert "yahoo" not in players["yh-b"]
    assert players["yh-def"]["gsis_id"] == "DEF:GB"
    assert players["yh-c"]["gsis_id"] == "g5"

    unmatched = payload["unmatched"]
    assert [miss["player_id"] for miss in unmatched] == ["yh-d"]  # type: ignore[attr-defined]


def test_build_predictions_carries_the_vegas_implied_total() -> None:
    frames = _frames()
    for row in frames[(2024, 2)]:
        if row["player_id"] == "g1":
            row["implied_team_total"] = 27.5
    payload = build_predictions(frames, 2024, 2, _roster(), _NAMES, make_predictor=_Stub)
    players = {entry["player_id"]: entry for entry in payload["players"]}  # type: ignore[attr-defined]
    assert players["yh-a"]["vegas_implied"] == 27.5
    assert "vegas_implied" not in players["yh-b"]


def test_build_predictions_requires_a_target_week() -> None:
    with pytest.raises(PredictError):
        build_predictions(_frames(), 2024, 9, _roster(), _NAMES, make_predictor=_Stub)


def test_run_predict_writes_the_file(tmp_path: Path) -> None:
    rosters = tmp_path / "rosters.json"
    rosters.write_text(json.dumps(_roster()), encoding="utf-8")
    output = tmp_path / "predictions.json"
    payload = run_predict(
        data_dir=tmp_path,
        season=2024,
        week=2,
        history_seasons=[2024],
        rosters_path=rosters,
        output=output,
        collect=lambda data_dir, seasons: _frames(),
        make_predictor=_Stub,
        names=_NAMES,
    )
    written = json.loads(output.read_text(encoding="utf-8"))
    assert written == payload
    assert written["season"] == 2024
    assert written["week"] == 2
