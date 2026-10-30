"""Recommended lineups under the league's roster slots.

The objective is the mean, the expected-points head: starters are chosen to
maximize the lineup's expected total. The median (q50) stays the headline
number on the dashboard card, but it is not the selection objective — on
right-skewed scoring the median sits below the mean, so median ranking
systematically benches high-variance players for steadier ones projected
for fewer points on average. On the 2022–2025 backtest the mean-ranked
lineup out-gained the q50-ranked one (+1,591.28 vs +1,472.10 points against
the same baseline, with lower regret). Owner decision 2026-10-29,
superseding the q50 objective recorded 2026-10-24. A player marked
``force_zero``, bye, or OUT/IR is not started when anyone else can fill the
slot. Ties break on ``player_id`` so the same roster always yields the
same lineup.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence

from gridiron_lab.config import Lineup

FLEX_POSITIONS = ("RB", "WR", "TE")
REQUIRED = ("QB", "RB", "WR", "TE", "K", "DST")
INACTIVE_INJURIES = frozenset({"OUT", "O", "IR", "PUP", "SUS"})
CLOSE_CALL_MARGIN = 1.0
SLOT_LABELS = {
    "QB": "QB",
    "RB": "RB",
    "WR": "WR",
    "TE": "TE",
    "FLEX": "W/R/T",
    "K": "K",
    "DST": "DEF",
}


class LineupError(Exception):
    """A roster row cannot be placed under the slot rules."""


def normalize_position(raw: object) -> str:
    text = str(raw or "").strip().upper()
    if text == "DEF":
        return "DST"
    if text not in (*REQUIRED, "FLEX"):
        raise LineupError(f"unknown position {raw!r}")
    return text


def projection(player: Mapping[str, object]) -> float:
    """Mean when present, otherwise q50. Missing both is 0."""

    value = player.get("mean")
    if value is None:
        value = player.get("q50")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value)


def is_eligible(player: Mapping[str, object]) -> bool:
    if player.get("force_zero") is True or player.get("bye") is True:
        return False
    injury = str(player.get("injury") or "").strip().upper()
    return injury not in INACTIVE_INJURIES


def _player_id(player: Mapping[str, object]) -> str:
    return str(player.get("player_id") or "")


def _order(player: Mapping[str, object]) -> tuple[float, str]:
    return (-projection(player), _player_id(player))


def _take(
    pools: dict[str, list[Mapping[str, object]]],
    position: str,
    count: int,
) -> list[Mapping[str, object]]:
    group = pools.get(position, [])
    chosen = group[:count]
    del group[:count]
    return list(chosen)


def select_starters(
    players: Sequence[Mapping[str, object]],
    lineup: Lineup,
) -> list[tuple[str, Mapping[str, object]]]:
    """Fill QB, RB, WR, TE, K, DST, then the best remaining FLEX.

    Each player has one position, so filling the required slots before FLEX
    maximizes total mean. Ineligible players are left out.
    """

    pools: dict[str, list[Mapping[str, object]]] = {position: [] for position in REQUIRED}
    for player in players:
        if not is_eligible(player):
            continue
        position = normalize_position(player.get("position"))
        pools[position].append(player)
    for group in pools.values():
        group.sort(key=_order)

    chosen: list[tuple[str, Mapping[str, object]]] = []
    for position in REQUIRED:
        count = int(getattr(lineup, position.lower()))
        for player in _take(pools, position, count):
            chosen.append((position, player))
    flex_pool: list[Mapping[str, object]] = []
    for position in FLEX_POSITIONS:
        flex_pool.extend(pools[position])
    flex_pool.sort(key=_order)
    for player in flex_pool[: lineup.flex]:
        chosen.append(("FLEX", player))
    return chosen


def _meets_minimums(players: Sequence[Mapping[str, object]], lineup: Lineup) -> bool:
    counts: Counter[str] = Counter(normalize_position(player.get("position")) for player in players)
    for position in REQUIRED:
        if counts[position] < int(getattr(lineup, position.lower())):
            return False
    extra = 0
    for position in FLEX_POSITIONS:
        extra += counts[position] - int(getattr(lineup, position.lower()))
    return extra >= lineup.flex


def _can_replace(
    starters: Sequence[Mapping[str, object]],
    starter: Mapping[str, object],
    candidate: Mapping[str, object],
    lineup: Lineup,
) -> bool:
    if not is_eligible(candidate):
        return False
    pool = [player for player in starters if _player_id(player) != _player_id(starter)]
    pool.append(candidate)
    if not _meets_minimums(pool, lineup):
        return False
    chosen_ids = {_player_id(player) for _, player in select_starters(pool, lineup)}
    return _player_id(candidate) in chosen_ids and _player_id(starter) not in chosen_ids


def recommend(
    players: Sequence[Mapping[str, object]],
    lineup: Lineup,
) -> dict[str, object]:
    """Return starters, bench, inactive players, the projected total, and close calls.

    The projected total is the sum of the starters' means. A starter is
    ``monitor`` when a legal bench replacement is within
    ``CLOSE_CALL_MARGIN`` points (mean). That replacement is ``monitor`` too.
    Other starters are ``start``. Everyone else who is eligible is ``sit``.
    """

    starter_pairs = select_starters(players, lineup)
    starter_ids = {_player_id(player) for _, player in starter_pairs}
    starters = [player for _, player in starter_pairs]
    bench = [
        player
        for player in players
        if _player_id(player) not in starter_ids and is_eligible(player)
    ]
    inactive = [player for player in players if not is_eligible(player)]
    bench_sorted = sorted(bench, key=_order)

    closest: dict[str, tuple[float, Mapping[str, object]]] = {}
    for starter in starters:
        best: tuple[float, Mapping[str, object]] | None = None
        for candidate in bench_sorted:
            if not _can_replace(starters, starter, candidate, lineup):
                continue
            gap = round(projection(starter) - projection(candidate), 4)
            if best is None or gap < best[0]:
                best = (gap, candidate)
        if best is not None:
            closest[_player_id(starter)] = best

    monitor_ids: set[str] = set()
    calls: list[dict[str, object]] = []
    for slot, starter in starter_pairs:
        found = closest.get(_player_id(starter))
        if found is None or found[0] > CLOSE_CALL_MARGIN:
            continue
        gap, alternative = found
        monitor_ids.add(_player_id(starter))
        monitor_ids.add(_player_id(alternative))
        calls.append(
            {
                "slot": SLOT_LABELS[slot],
                "margin": gap,
                "starter_id": _player_id(starter),
                "starter_name": str(starter.get("name") or ""),
                "starter_projection": projection(starter),
                "alternative_id": _player_id(alternative),
                "alternative_name": str(alternative.get("name") or ""),
                "alternative_projection": projection(alternative),
            }
        )
    calls.sort(key=lambda item: (float(item["margin"]), str(item["starter_id"])))

    def decorate(player: Mapping[str, object], slot: str | None) -> dict[str, object]:
        row = dict(player)
        row["slot"] = slot
        if not is_eligible(player):
            row["verdict"] = "sit"
        elif _player_id(player) in monitor_ids:
            row["verdict"] = "monitor"
        elif slot is not None:
            row["verdict"] = "start"
        else:
            row["verdict"] = "sit"
        return row

    decorated = [decorate(player, slot) for slot, player in starter_pairs]
    display_order = ("QB", "WR", "RB", "TE", "FLEX", "K", "DST")
    decorated.sort(key=lambda row: (display_order.index(str(row["slot"])), _player_id(row)))
    total = round(sum(projection(player) for player in starters), 4)
    return {
        "starters": decorated,
        "bench": [decorate(player, None) for player in bench_sorted],
        "inactive": [decorate(player, None) for player in inactive],
        "projected_total": total,
        "calls": calls[:3],
    }
