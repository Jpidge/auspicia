"""Kickoff clocks and the documented ``known_as_of`` proxies.

Every instant is converted to UTC and serialized as
``YYYY-MM-DDTHH:MM:SSZ`` so string order matches time order.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from gridiron_lab.data.errors import IngestError

ET = ZoneInfo("America/New_York")

STATS_LAG = timedelta(hours=6)
SNAPS_LAG = timedelta(hours=24)
VEGAS_LEAD = timedelta(hours=1)

_UTC_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def format_utc(value: datetime) -> str:
    """Format an aware datetime as a UTC second-resolution stamp."""

    if value.tzinfo is None:
        raise IngestError("known_as_of timestamps must be timezone-aware")
    utc = value.astimezone(UTC).replace(microsecond=0)
    return utc.strftime(_UTC_FORMAT)


def parse_utc(value: str) -> datetime:
    """Parse a stamp produced by :func:`format_utc`."""

    try:
        parsed = datetime.strptime(value, _UTC_FORMAT)
    except ValueError as exc:
        raise IngestError(f"known_as_of {value!r} is not a UTC stamp") from exc
    return parsed.replace(tzinfo=UTC)


def shift(kickoff: datetime, delta: timedelta) -> str:
    return format_utc(kickoff + delta)


def at_2359_et(day: datetime) -> datetime:
    """23:59 America/New_York on ``day``'s calendar date."""

    return datetime(day.year, day.month, day.day, 23, 59, tzinfo=ET)


def day_before_gameday(gameday: str) -> datetime:
    """23:59 Eastern on the calendar day before ``gameday`` (``YYYY-MM-DD``)."""

    try:
        day = datetime.strptime(gameday, "%Y-%m-%d")
    except ValueError as exc:
        raise IngestError(f"gameday {gameday!r} is not YYYY-MM-DD") from exc
    prior = day - timedelta(days=1)
    return at_2359_et(prior)


def season_open(year: int) -> datetime:
    """1 September, America/New_York. Identity proxy when a season is known."""

    return datetime(year, 9, 1, tzinfo=ET)


def parse_report_time(value: object) -> tuple[datetime, str] | None:
    """Return a report clock and the method name.

    A value with a time of day is that timestamp. A calendar date with no
    time is 23:59 America/New_York on that date. Anything else is missing.
    """

    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC), "naive_timestamp_as_utc"
        return value.astimezone(UTC), "report_timestamp"
    if value is None:
        return None
    text = str(value).strip()
    if text == "" or text.lower() in {"nan", "none", "nat", "null"}:
        return None
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        try:
            day = datetime.strptime(text, "%Y-%m-%d")
        except ValueError:
            return None
        return at_2359_et(day), "report_date_2359_et"
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC), "naive_timestamp_as_utc"
    return parsed.astimezone(UTC), "report_timestamp"


def kickoff_from_schedule(gameday: object, gametime: object) -> tuple[datetime, str] | None:
    """Eastern kickoff from NFLverse ``gameday`` and ``gametime``.

    A missing clock with a known date uses 23:59 Eastern so the game is not
    treated as known that morning.
    """

    day_text = None if gameday is None else str(gameday).strip()
    if not day_text or day_text.lower() in {"nan", "none", "nat"}:
        return None
    try:
        day = datetime.strptime(day_text[:10], "%Y-%m-%d")
    except ValueError:
        return None
    time_text = None if gametime is None else str(gametime).strip()
    if not time_text or time_text.lower() in {"nan", "none", "nat"}:
        return at_2359_et(day), "gameday_2359_et_missing_gametime"
    try:
        hour, minute = time_text.split(":", 1)
        local = datetime(day.year, day.month, day.day, int(hour), int(minute), tzinfo=ET)
    except ValueError:
        return None
    return local, "schedule_kickoff_et"


def implied_totals(
    total_line: float | None,
    spread_line: float | None,
) -> tuple[float | None, float | None]:
    """Home and away implied totals.

    NFLverse ``spread_line`` is positive when the home team is favored.
    Home = total/2 + spread/2. Away = total/2 − spread/2.
    """

    if total_line is None or spread_line is None:
        return None, None
    return total_line / 2 + spread_line / 2, total_line / 2 - spread_line / 2


@dataclass(frozen=True)
class GameClock:
    """One scheduled game, used to stamp every other table."""

    game_id: str
    season: int
    week: int
    season_type: str
    game_type: str
    home_team: str
    away_team: str
    kickoff: datetime
    kickoff_method: str
    spread_line: float | None
    total_line: float | None
    home_score: int | None
    away_score: int | None
    gameday: str

    def team_is_home(self, team: str) -> bool:
        return team == self.home_team

    def opponent(self, team: str) -> str | None:
        if team == self.home_team:
            return self.away_team
        if team == self.away_team:
            return self.home_team
        return None

    def score(self, team: str) -> int | None:
        if team == self.home_team:
            return self.home_score
        if team == self.away_team:
            return self.away_score
        return None
