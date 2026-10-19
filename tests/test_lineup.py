"""Slot rules for the recommended lineup."""

from __future__ import annotations

from gridiron_lab.config import Lineup
from gridiron_lab.lineup import recommend

LINEUP = Lineup(qb=1, rb=2, wr=2, te=1, flex=1, k=1, dst=1, bench=6, ir=1)


def _player(player_id: str, position: str, q50: float, **extra: object) -> dict[str, object]:
    row: dict[str, object] = {
        "player_id": player_id,
        "name": player_id,
        "position": position,
        "q50": q50,
    }
    row.update(extra)
    return row


def _slots(result: dict[str, object]) -> dict[str, str]:
    starters = result["starters"]
    assert isinstance(starters, list)
    return {str(player["player_id"]): str(player["slot"]) for player in starters}


def test_flex_takes_the_best_remaining_skill_player() -> None:
    roster = [
        _player("qb", "QB", 10),
        _player("rb1", "RB", 20),
        _player("rb2", "RB", 19),
        _player("rb3", "RB", 18),
        _player("wr1", "WR", 22),
        _player("wr2", "WR", 21),
        _player("wr3", "WR", 17),
        _player("te", "TE", 6),
        _player("k", "K", 3),
        _player("dst", "DST", 2),
    ]
    slots = _slots(recommend(roster, LINEUP))
    assert slots["rb3"] == "FLEX"
    assert "wr3" not in slots
    assert slots["wr1"] == "WR"
    assert slots["wr2"] == "WR"


def test_flex_can_be_a_wide_receiver() -> None:
    roster = [
        _player("qb", "QB", 10),
        _player("rb1", "RB", 20),
        _player("rb2", "RB", 19),
        _player("rb3", "RB", 10),
        _player("wr1", "WR", 22),
        _player("wr2", "WR", 21),
        _player("wr3", "WR", 17),
        _player("te", "TE", 6),
        _player("k", "K", 3),
        _player("dst", "DEF", 2),
    ]
    slots = _slots(recommend(roster, LINEUP))
    assert slots["wr3"] == "FLEX"
    assert slots["dst"] == "DST"
    assert "rb3" not in slots


def test_force_zero_and_out_do_not_start() -> None:
    roster = [
        _player("hurt", "QB", 40, force_zero=True),
        _player("backup", "QB", 12),
        _player("out", "RB", 30, injury="OUT"),
        _player("rb", "RB", 9),
        _player("rb2", "RB", 8),
        _player("wr1", "WR", 7),
        _player("wr2", "WR", 6),
        _player("te", "TE", 5),
        _player("k", "K", 3),
        _player("dst", "DST", 2),
    ]
    result = recommend(roster, LINEUP)
    slots = _slots(result)
    assert slots["backup"] == "QB"
    assert "hurt" not in slots
    assert "out" not in slots
    inactive = result["inactive"]
    assert isinstance(inactive, list)
    assert {player["player_id"] for player in inactive} == {"hurt", "out"}


def test_unfilled_slot_stays_empty() -> None:
    roster = [
        _player("qb", "QB", 10),
        _player("rb1", "RB", 9),
        _player("rb2", "RB", 8),
        _player("wr", "WR", 7),
        _player("te", "TE", 5),
        _player("k", "K", 3),
        _player("dst", "DST", 2),
    ]
    slots = _slots(recommend(roster, LINEUP))
    assert list(slots.values()).count("WR") == 1
    assert "FLEX" not in slots.values()


def test_close_call_is_monitor_and_repeatable() -> None:
    roster = [
        _player("qb1", "QB", 18.4),
        _player("qb2", "QB", 17.6),
        _player("rb1", "RB", 12),
        _player("rb2", "RB", 11),
        _player("wr1", "WR", 10),
        _player("wr2", "WR", 9),
        _player("wr3", "WR", 7),
        _player("te", "TE", 8),
        _player("k", "K", 4),
        _player("dst", "DST", 3),
    ]
    first = recommend(roster, LINEUP)
    second = recommend(roster, LINEUP)
    assert first == second
    starters = first["starters"]
    assert isinstance(starters, list)
    by_id = {player["player_id"]: player for player in starters}
    assert by_id["qb1"]["verdict"] == "monitor"
    bench = first["bench"]
    assert isinstance(bench, list)
    bench_by_id = {player["player_id"]: player for player in bench}
    assert bench_by_id["qb2"]["verdict"] == "monitor"
    calls = first["calls"]
    assert isinstance(calls, list)
    assert calls[0]["alternative_id"] == "qb2"
