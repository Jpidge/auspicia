"""OAuth 2.0 authorization-code and refresh-token flow for Yahoo Fantasy.

The local helper binds ``127.0.0.1:8085`` and waits for one callback. The
authorization code and both tokens stay out of the log. A new refresh token
is written to a mode-0600 file and is not printed.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

from gridiron_lab.logging import get_logger
from gridiron_lab.yahoo.errors import YahooAuthError, YahooConfigError
from gridiron_lab.yahoo.http import HttpResult, Transport, UrllibTransport

logger = get_logger("yahoo.auth")

AUTHORIZE_URL = "https://api.login.yahoo.com/oauth2/request_auth"
TOKEN_URL = "https://api.login.yahoo.com/oauth2/get_token"
DEFAULT_REDIRECT_URI = "http://127.0.0.1:8085/callback"
DEFAULT_TOKEN_FILE = Path("yahoo_refresh_token")
CLIENT_ID_ENV = "YAHOO_CLIENT_ID"
CLIENT_SECRET_ENV = "YAHOO_CLIENT_SECRET"
REFRESH_TOKEN_ENV = "YAHOO_REFRESH_TOKEN"
TOKEN_FILE_ENV = "YAHOO_TOKEN_FILE"
ADMIN_TOKEN_ENV = "YAHOO_SECRETS_ADMIN_TOKEN"


@dataclass(frozen=True)
class YahooCredentials:
    client_id: str
    client_secret: str
    refresh_token: str


@dataclass(frozen=True)
class TokenResponse:
    access_token: str
    refresh_token: str | None
    expires_in: int | None


def credentials_from_env() -> YahooCredentials:
    """Read the three Yahoo secrets. Raise if any of them is empty."""

    client_id = os.environ.get(CLIENT_ID_ENV, "").strip()
    client_secret = os.environ.get(CLIENT_SECRET_ENV, "").strip()
    refresh_token = os.environ.get(REFRESH_TOKEN_ENV, "").strip()
    missing = [
        name
        for name, value in (
            (CLIENT_ID_ENV, client_id),
            (CLIENT_SECRET_ENV, client_secret),
            (REFRESH_TOKEN_ENV, refresh_token),
        )
        if not value
    ]
    if missing:
        raise YahooConfigError("missing Yahoo environment variable: " + ", ".join(missing))
    return YahooCredentials(client_id, client_secret, refresh_token)


def token_file_path() -> Path:
    override = os.environ.get(TOKEN_FILE_ENV, "").strip()
    if override:
        return Path(override)
    return DEFAULT_TOKEN_FILE


def write_refresh_token(path: Path, token: str) -> None:
    """Write the refresh token as one line, mode 0600. Do not log it."""

    if not token or "\n" in token or "\r" in token:
        raise YahooAuthError("refresh token is empty or contains a newline")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, token.encode("utf-8") + b"\n")
    finally:
        os.close(fd)
    os.chmod(path, 0o600)


def authorization_url(client_id: str, redirect_uri: str, state: str) -> str:
    query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "state": state,
            "language": "en-us",
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


def basic_authorization(client_id: str, client_secret: str) -> str:
    raw = f"{client_id}:{client_secret}".encode()
    return "Basic " + base64.b64encode(raw).decode("ascii")


def parse_token_response(body: bytes) -> TokenResponse:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise YahooAuthError("token response was not JSON") from exc
    if not isinstance(payload, dict):
        raise YahooAuthError("token response was not an object")
    access = payload.get("access_token")
    if not isinstance(access, str) or not access:
        raise YahooAuthError("token response has no access_token")
    refresh = payload.get("refresh_token")
    if refresh is not None and not isinstance(refresh, str):
        raise YahooAuthError("token response refresh_token was not a string")
    expires = payload.get("expires_in")
    if expires is not None and not isinstance(expires, int):
        raise YahooAuthError("token response expires_in was not an integer")
    return TokenResponse(access, refresh if refresh else None, expires)


def exchange_token(
    transport: Transport,
    *,
    client_id: str,
    client_secret: str,
    form: Mapping[str, str],
) -> TokenResponse:
    """POST the token endpoint. The form is not logged."""

    body = urlencode(form).encode("utf-8")
    result = transport.request(
        "POST",
        TOKEN_URL,
        {
            "Authorization": basic_authorization(client_id, client_secret),
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        body,
    )
    if result.status != 200:
        raise YahooAuthError(f"token endpoint returned HTTP {result.status}")
    return parse_token_response(result.body)


def refresh_access_token(
    transport: Transport,
    credentials: YahooCredentials,
    redirect_uri: str = DEFAULT_REDIRECT_URI,
) -> TokenResponse:
    return exchange_token(
        transport,
        client_id=credentials.client_id,
        client_secret=credentials.client_secret,
        form={
            "grant_type": "refresh_token",
            "refresh_token": credentials.refresh_token,
            "redirect_uri": redirect_uri,
        },
    )


def exchange_authorization_code(
    transport: Transport,
    *,
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
) -> TokenResponse:
    return exchange_token(
        transport,
        client_id=client_id,
        client_secret=client_secret,
        form={
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
            "code": code,
        },
    )


def github_secret_set_argv(repo: str, token_file: Path) -> list[str]:
    """Argv for ``gh secret set``. The token stays in the file, not in argv."""

    return [
        "gh",
        "secret",
        "set",
        REFRESH_TOKEN_ENV,
        "--repo",
        repo,
        "--body-file",
        str(token_file),
    ]


class _CallbackHandler(BaseHTTPRequestHandler):
    """One GET on ``/callback``. The access log is disabled so the code is not written."""

    server: _CallbackServer

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler name
        parsed = urlparse(self.path)
        if parsed.path != "/callback":
            self.send_error(404)
            return
        query = parse_qs(parsed.query)
        self.server.callback = {
            "code": _first(query, "code"),
            "state": _first(query, "state"),
            "error": _first(query, "error"),
        }
        body = b"Yahoo authorization received. You can close this tab.\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


class _CallbackServer(ThreadingHTTPServer):
    callback: dict[str, str] | None

    def __init__(self, address: tuple[str, int]) -> None:
        super().__init__(address, _CallbackHandler)
        self.callback = None


def _first(query: Mapping[str, list[str]], key: str) -> str:
    values = query.get(key)
    if not values:
        return ""
    return values[0]


def wait_for_callback(port: int, timeout: float) -> dict[str, str]:
    """Block until one callback arrives or ``timeout`` seconds pass."""

    try:
        server = _CallbackServer(("127.0.0.1", port))
    except OSError as exc:
        raise YahooAuthError(f"could not listen on 127.0.0.1:{port}") from exc
    server.timeout = timeout
    try:
        server.handle_request()
    finally:
        server.server_close()
    if server.callback is None:
        raise YahooAuthError("no authorization callback arrived")
    return server.callback


def run_yahoo_auth(
    *,
    redirect_uri: str = DEFAULT_REDIRECT_URI,
    token_file: Path | None = None,
    timeout: float = 300.0,
    transport: Transport | None = None,
) -> Path:
    """Walk through the browser consent once and store the refresh token.

    Tests call :func:`finish_yahoo_auth` with a prebuilt callback instead of
    opening a port.
    """

    client_id = os.environ.get(CLIENT_ID_ENV, "").strip()
    client_secret = os.environ.get(CLIENT_SECRET_ENV, "").strip()
    if not client_id or not client_secret:
        raise YahooConfigError(f"set {CLIENT_ID_ENV} and {CLIENT_SECRET_ENV} before yahoo-auth")
    parsed = urlparse(redirect_uri)
    if parsed.hostname not in {"127.0.0.1", "localhost"} or parsed.path != "/callback":
        raise YahooConfigError("redirect URI must be http://127.0.0.1:8085/callback")
    port = parsed.port or 8085
    state = secrets.token_urlsafe(16)
    url = authorization_url(client_id, redirect_uri, state)
    print("Open this URL and approve Fantasy Sports read access:")
    print(url)
    print(f"Waiting for the callback on {redirect_uri}")
    callback = wait_for_callback(port, timeout)
    destination = token_file if token_file is not None else token_file_path()
    finish_yahoo_auth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        expected_state=state,
        callback=callback,
        token_file=destination,
        transport=transport or UrllibTransport(),
    )
    return destination


def finish_yahoo_auth(
    *,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    expected_state: str,
    callback: Mapping[str, str],
    token_file: Path,
    transport: Transport,
) -> TokenResponse:
    """Exchange a callback for a refresh token and write the file."""

    error = callback.get("error") or ""
    if error:
        raise YahooAuthError("Yahoo authorization was declined")
    if callback.get("state") != expected_state:
        raise YahooAuthError("authorization state did not match")
    code = callback.get("code") or ""
    if not code:
        raise YahooAuthError("authorization callback had no code")
    tokens = exchange_authorization_code(
        transport,
        client_id=client_id,
        client_secret=client_secret,
        code=code,
        redirect_uri=redirect_uri,
    )
    if not tokens.refresh_token:
        raise YahooAuthError("token response had no refresh token to store")
    write_refresh_token(token_file, tokens.refresh_token)
    logger.info("Wrote the Yahoo refresh token to %s", token_file)
    print(f"Refresh token written to {token_file}")
    print(f"Put that file's contents in the GitHub Actions secret {REFRESH_TOKEN_ENV}.")
    print("Do not commit the file.")
    return tokens


def persist_rotated_refresh_token(
    current: str,
    received: str | None,
    token_file: Path,
) -> bool:
    """Store ``received`` when Yahoo issued a different refresh token.

    Returns True when the file was updated. The values are not logged.
    """

    if not received or received == current:
        return False
    write_refresh_token(token_file, received)
    logger.info("Yahoo returned a new refresh token; wrote it to %s", token_file)
    return True


def response_is_rate_limited(result: HttpResult) -> bool:
    if result.status in {429, 999}:
        return True
    return False
