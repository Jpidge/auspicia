"""Yahoo Fantasy API client for one league.

The game key comes from ``/fantasy/v2/game/nfl``. League resources use
``{game_key}.l.{league_id}``. Calls send ``format=json`` and a bearer token.
HTTP 429 and 999 wait on ``Retry-After`` or exponential backoff. A 401
refreshes the access token once. A new refresh token is written to the token
file and is not logged.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from gridiron_lab.logging import get_logger
from gridiron_lab.yahoo.auth import (
    TokenResponse,
    YahooCredentials,
    persist_rotated_refresh_token,
    refresh_access_token,
    response_is_rate_limited,
)
from gridiron_lab.yahoo.errors import YahooAuthError, YahooError, YahooRateLimitError
from gridiron_lab.yahoo.http import HttpResult, Transport, UrllibTransport
from gridiron_lab.yahoo.parse import game_key as parse_game_key
from gridiron_lab.yahoo.parse import load_document

logger = get_logger("yahoo.client")

API_BASE = "https://fantasysports.yahooapis.com/fantasy/v2"
DEFAULT_LEAGUE_ID = 893771


class YahooClient:
    """Read-only Fantasy Sports calls for the configured league."""

    def __init__(
        self,
        credentials: YahooCredentials,
        *,
        transport: Transport | None = None,
        token_file: Path,
        league_id: int = DEFAULT_LEAGUE_ID,
        sleep: Callable[[float], None] | None = None,
        max_attempts: int = 5,
        base_delay: float = 1.0,
    ) -> None:
        self.credentials = credentials
        self.transport = transport if transport is not None else UrllibTransport()
        self.token_file = token_file
        self.league_id = league_id
        self.sleep = sleep if sleep is not None else _sleep
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self._access_token = ""
        self._game_key = ""
        self.refresh_rotated = False

    def game_key(self) -> str:
        if not self._game_key:
            document = self.get_json("game/nfl")
            self._game_key = parse_game_key(document)
            logger.info("Yahoo NFL game key %s", self._game_key)
        return self._game_key

    def league_key(self) -> str:
        return f"{self.game_key()}.l.{self.league_id}"

    def league_settings(self) -> object:
        return self.get_json(f"league/{self.league_key()}/settings")

    def teams(self) -> object:
        return self.get_json(f"league/{self.league_key()}/teams")

    def rosters(self, week: int) -> object:
        return self.get_json(f"league/{self.league_key()}/teams/roster;week={week}")

    def scoreboard(self, week: int) -> object:
        return self.get_json(f"league/{self.league_key()}/scoreboard;week={week}")

    def transactions(self) -> object:
        return self.get_json(f"league/{self.league_key()}/transactions")

    def projections(self, week: int, player_keys: list[str]) -> object:
        if not player_keys:
            return {"fantasy_content": {"league": {"players": []}}}
        keys = ",".join(player_keys)
        resource = (
            f"league/{self.league_key()}/players;player_keys={keys}/stats;type=week;week={week}"
        )
        return self.get_json(resource)

    def get_json(self, resource: str) -> object:
        body = self._request(resource)
        return load_document(body)

    def _request(self, resource: str) -> bytes:
        if not self._access_token:
            self._refresh()
        refreshed = False
        last_status = 0
        for attempt in range(self.max_attempts):
            result = self.transport.request(
                "GET",
                _resource_url(resource),
                {
                    "Authorization": f"Bearer {self._access_token}",
                    "Accept": "application/json",
                },
                None,
            )
            last_status = result.status
            if result.status == 401 and not refreshed:
                refreshed = True
                self._refresh()
                continue
            if response_is_rate_limited(result):
                if attempt == self.max_attempts - 1:
                    break
                delay = _delay(result, attempt, self.base_delay)
                logger.info(
                    "Yahoo rate limit on %s · attempt %s · sleeping %.1fs",
                    resource,
                    attempt + 1,
                    delay,
                )
                self.sleep(delay)
                continue
            if result.status == 200:
                return result.body
            if result.status in {500, 502, 503, 504} and attempt < self.max_attempts - 1:
                delay = _delay(result, attempt, self.base_delay)
                logger.info("Yahoo HTTP %s on %s · sleeping %.1fs", result.status, resource, delay)
                self.sleep(delay)
                continue
            raise YahooError(f"Yahoo HTTP {result.status} on {resource}")
        raise YahooRateLimitError(
            f"Yahoo rate limit on {resource} after {self.max_attempts} attempts "
            f"(last HTTP {last_status})"
        )

    def _refresh(self) -> None:
        try:
            tokens = refresh_access_token(self.transport, self.credentials)
        except YahooAuthError:
            logger.error("Yahoo token refresh failed")
            raise
        self._apply_tokens(tokens)

    def _apply_tokens(self, tokens: TokenResponse) -> None:
        self._access_token = tokens.access_token
        rotated = persist_rotated_refresh_token(
            self.credentials.refresh_token,
            tokens.refresh_token,
            self.token_file,
        )
        if rotated and tokens.refresh_token is not None:
            self.credentials = YahooCredentials(
                self.credentials.client_id,
                self.credentials.client_secret,
                tokens.refresh_token,
            )
            self.refresh_rotated = True


def _resource_url(resource: str) -> str:
    separator = "&" if "?" in resource else "?"
    return f"{API_BASE}/{resource}{separator}format=json"


def _delay(result: HttpResult, attempt: int, base_delay: float) -> float:
    retry_after = result.headers.get("retry-after", "")
    if retry_after.isdigit():
        return float(retry_after)
    return min(32.0, base_delay * (2**attempt))


def _sleep(seconds: float) -> None:
    import time

    time.sleep(seconds)
