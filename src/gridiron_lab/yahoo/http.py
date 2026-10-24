"""HTTP for the Yahoo client. The default transport is urllib.

Nothing in this module logs a URL, a header, or a body. Callers log a path
they built themselves, never the Authorization header or a token request.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from gridiron_lab.yahoo.errors import YahooError


@dataclass(frozen=True)
class HttpResult:
    status: int
    headers: Mapping[str, str]
    body: bytes


class Transport(Protocol):
    """Something that performs one HTTP request and returns the result."""

    def request(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
    ) -> HttpResult: ...


class UrllibTransport:
    """One request through ``urllib.request``. HTTP errors become results."""

    def __init__(self, timeout: float = 30.0) -> None:
        self.timeout = timeout

    def request(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
    ) -> HttpResult:
        req = urllib.request.Request(url, data=body, headers=dict(headers), method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                raw_headers = {key.lower(): value for key, value in response.headers.items()}
                return HttpResult(response.status, raw_headers, response.read())
        except urllib.error.HTTPError as exc:
            payload = exc.read()
            raw_headers = {key.lower(): value for key, value in exc.headers.items()}
            return HttpResult(exc.code, raw_headers, payload)
        except urllib.error.URLError as exc:
            raise YahooError("Yahoo request failed before a response") from exc
