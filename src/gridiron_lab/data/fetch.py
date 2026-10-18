"""NFLverse downloads. Tests never call this module's network path.

``HttpFetch`` is the only code that opens a release URL. ``FixtureFetch``
reads CSV files checked into ``tests/fixtures``.
"""

from __future__ import annotations

import csv
import urllib.error
import urllib.request
from pathlib import Path
from typing import Protocol

import pyarrow as pa
import pyarrow.parquet as pq

from gridiron_lab.data.errors import FetchError
from gridiron_lab.data.values import as_int
from gridiron_lab.logging import get_logger

logger = get_logger("data.fetch")

RELEASE_BASE = "https://github.com/nflverse/nflverse-data/releases/download"

# Paths match the nflverse-data release layout used by nflreadpy.
RELEASE_PATHS: dict[str, str] = {
    "schedules": "schedules/games.parquet",
    "player_stats": "stats_player/stats_player_week_{season}.parquet",
    "snap_counts": "snap_counts/snap_counts_{season}.parquet",
    "injuries": "injuries/injuries_{season}.parquet",
    "depth_charts": "depth_charts/depth_charts_{season}.parquet",
    "rosters": "weekly_rosters/roster_weekly_{season}.parquet",
    "players": "players/players.parquet",
    "pbp": "pbp/play_by_play_{season}.parquet",
}

SEASONAL = frozenset(RELEASE_PATHS) - {"schedules", "players"}


def release_url(source: str, season: int) -> str:
    try:
        path = RELEASE_PATHS[source]
    except KeyError as exc:
        raise FetchError(f"unknown source {source}") from exc
    if "{season}" in path:
        path = path.format(season=season)
    return f"{RELEASE_BASE}/{path}"


class SourceFetch(Protocol):
    def load(self, source: str, season: int) -> list[dict[str, object]]:
        """Return raw rows for ``source`` in ``season``."""


class HttpFetch:
    """Download a release parquet into the data-dir cache, then read it."""

    def __init__(
        self,
        cache_dir: Path,
        *,
        timeout: float = 120.0,
        opener: object | None = None,
    ) -> None:
        self.cache_dir = cache_dir
        self.timeout = timeout
        self._opener = opener or urllib.request.urlopen

    def load(self, source: str, season: int) -> list[dict[str, object]]:
        url = release_url(source, season)
        relative = url.removeprefix(RELEASE_BASE + "/")
        destination = self.cache_dir / "nflverse" / relative
        self._ensure(source, season, url, destination)
        try:
            table = pq.read_table(destination)
        except (OSError, pa.ArrowInvalid) as exc:
            raise FetchError(f"Could not read cached {source} for season {season}") from exc
        rows = table.to_pylist()
        logger.info("Fetched %s · season %s · rows %s", source, season, len(rows))
        return rows

    def _ensure(self, source: str, season: int, url: str, destination: Path) -> None:
        if destination.is_file() and destination.stat().st_size > 0:
            logger.info("Using cached %s · season %s", source, season)
            return
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".partial")
        request = urllib.request.Request(url, headers={"User-Agent": "gridiron-lab"})
        try:
            with self._opener(request, timeout=self.timeout) as response:  # type: ignore[operator]
                payload = response.read()
        except urllib.error.HTTPError as exc:
            raise FetchError(f"{source} season {season} returned HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise FetchError(f"{source} season {season} could not be downloaded") from exc
        except OSError as exc:
            raise FetchError(f"{source} season {season} could not be downloaded") from exc
        if not payload:
            raise FetchError(f"{source} season {season} download was empty")
        try:
            temporary.write_bytes(payload)
            temporary.replace(destination)
        except OSError as exc:
            raise FetchError(f"Could not store {source} season {season}") from exc


class FixtureFetch:
    """Read small CSV fixtures. A shared file is filtered to ``season``."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def load(self, source: str, season: int) -> list[dict[str, object]]:
        if source not in RELEASE_PATHS:
            raise FetchError(f"unknown source {source}")
        seasonal = self.root / f"{source}_{season}.csv"
        shared = self.root / f"{source}.csv"
        path = seasonal if seasonal.is_file() else shared
        if not path.is_file():
            raise FetchError(f"fixture {source} for season {season} is missing")
        try:
            with path.open(newline="", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                rows = [dict(row) for row in reader]
        except OSError as exc:
            raise FetchError(f"Could not read fixture {path.name}") from exc
        if source in SEASONAL and rows and "season" in rows[0]:
            rows = [row for row in rows if as_int(row.get("season")) == season]
        elif source == "schedules" and rows and "season" in rows[0]:
            rows = [row for row in rows if as_int(row.get("season")) == season]
        logger.info("Loaded fixture %s · season %s · rows %s", source, season, len(rows))
        return rows
