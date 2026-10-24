"""Yahoo client, fixtures, and capture. No network and no real league data."""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from gridiron_lab.cli import run
from gridiron_lab.config import DashboardSettings, load_config
from gridiron_lab.dashboard import export_dashboard
from gridiron_lab.data.errors import IngestError
from gridiron_lab.data.store import ParquetStore
from gridiron_lab.yahoo.auth import (
    YahooCredentials,
    authorization_url,
    finish_yahoo_auth,
    github_secret_set_argv,
    write_refresh_token,
)
from gridiron_lab.yahoo.capture import capture
from gridiron_lab.yahoo.client import YahooClient
from gridiron_lab.yahoo.errors import YahooAuthError, YahooError, YahooRateLimitError
from gridiron_lab.yahoo.http import HttpResult
from gridiron_lab.yahoo.parse import (
    current_week,
    game_key,
    load_document,
    stat_modifiers,
    teams_from,
)
from gridiron_lab.yahoo.reconcile import past_week_report

FIXTURES = Path(__file__).parent / "fixtures" / "yahoo"
CLOCK = datetime(2026, 10, 22, 20, 0, tzinfo=UTC)
ORIGINAL_REFRESH = "original-refresh-secret"
ROTATED_REFRESH = "rotated-refresh-secret"
CLIENT_SECRET = "client-secret-value"
ACCESS_TOKEN = "access-token-secret"


class ScriptTransport:
    def __init__(self, results: list[HttpResult]) -> None:
        self.results = list(results)
        self.calls: list[tuple[str, str, dict[str, str], bytes | None]] = []

    def request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
    ) -> HttpResult:
        self.calls.append((method, url, dict(headers), body))
        if not self.results:
            raise AssertionError("unexpected request")
        return self.results.pop(0)


def _json_response(
    status: int,
    payload: object,
    headers: dict[str, str] | None = None,
) -> HttpResult:
    return HttpResult(status, headers or {}, json.dumps(payload).encode("utf-8"))


def _game_body() -> dict[str, object]:
    return {"fantasy_content": {"game": [{"game_key": "449", "code": "nfl", "season": "2026"}]}}


def _credentials() -> YahooCredentials:
    return YahooCredentials("client-id", CLIENT_SECRET, ORIGINAL_REFRESH)


def _client(tmp_path: Path, transport: ScriptTransport) -> YahooClient:
    return YahooClient(
        _credentials(),
        transport=transport,
        token_file=tmp_path / "yahoo_refresh_token",
        sleep=lambda _seconds: None,
        base_delay=0.25,
    )


def test_refresh_rotation_is_persisted_and_not_logged(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    token_body = {
        "access_token": ACCESS_TOKEN,
        "refresh_token": ROTATED_REFRESH,
        "expires_in": 3600,
    }
    transport = ScriptTransport(
        [
            _json_response(200, token_body),
            HttpResult(401, {}, b""),
            _json_response(200, token_body),
            _json_response(200, _game_body()),
        ]
    )
    client = _client(tmp_path, transport)
    with caplog.at_level(logging.DEBUG):
        assert client.game_key() == "449"
    text = caplog.text
    assert ORIGINAL_REFRESH not in text
    assert ROTATED_REFRESH not in text
    assert CLIENT_SECRET not in text
    assert ACCESS_TOKEN not in text
    stored = (tmp_path / "yahoo_refresh_token").read_text(encoding="utf-8").strip()
    assert stored == ROTATED_REFRESH
    assert (tmp_path / "yahoo_refresh_token").stat().st_mode & 0o777 == 0o600
    assert client.refresh_rotated is True
    methods = [call[0] for call in transport.calls]
    assert methods == ["POST", "GET", "POST", "GET"]
    assert all(ORIGINAL_REFRESH not in call[1] for call in transport.calls)
    assert all(ROTATED_REFRESH not in call[1] for call in transport.calls)


def test_rate_limit_retries_then_succeeds(tmp_path: Path) -> None:
    delays: list[float] = []
    transport = ScriptTransport(
        [
            _json_response(200, {"access_token": ACCESS_TOKEN, "expires_in": 3600}),
            _json_response(200, _game_body()),
            HttpResult(429, {"retry-after": "0"}, b""),
            HttpResult(999, {}, b""),
            _json_response(200, {"fantasy_content": {"league": {"league_key": "449.l.893771"}}}),
        ]
    )
    client = _client(tmp_path, transport)
    client.sleep = delays.append
    document = client.league_settings()
    assert isinstance(document, dict)
    assert delays == [0.0, 0.5]
    urls = [call[1] for call in transport.calls]
    assert "/game/nfl?" in urls[1]
    assert "/league/449.l.893771/settings?" in urls[2]
    assert urls[2].endswith("format=json")


def test_rate_limit_gives_up(tmp_path: Path) -> None:
    transport = ScriptTransport(
        [_json_response(200, {"access_token": ACCESS_TOKEN, "expires_in": 3600})]
        + [HttpResult(429, {"retry-after": "0"}, b"")] * 5
    )
    client = _client(tmp_path, transport)
    with pytest.raises(YahooRateLimitError, match="game/nfl"):
        client.game_key()
    assert len(transport.calls) == 6


def test_auth_helper_writes_refresh_token_without_logging_it(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "helper-refresh-secret"
    transport = ScriptTransport(
        [
            _json_response(
                200,
                {"access_token": ACCESS_TOKEN, "refresh_token": secret, "expires_in": 3600},
            )
        ]
    )
    destination = tmp_path / "yahoo_refresh_token"
    with caplog.at_level(logging.DEBUG):
        finish_yahoo_auth(
            client_id="client-id",
            client_secret=CLIENT_SECRET,
            redirect_uri="http://127.0.0.1:8085/callback",
            expected_state="state-1",
            callback={"code": "auth-code", "state": "state-1", "error": ""},
            token_file=destination,
            transport=transport,
        )
    assert secret not in caplog.text
    assert CLIENT_SECRET not in caplog.text
    assert destination.read_text(encoding="utf-8").strip() == secret
    printed = capsys.readouterr().out
    assert str(destination) in printed
    assert secret not in printed
    posted = transport.calls[0][3]
    assert posted is not None
    assert b"authorization_code" in posted


def test_auth_state_mismatch_stores_nothing(tmp_path: Path) -> None:
    transport = ScriptTransport([])
    with pytest.raises(YahooAuthError, match="state"):
        finish_yahoo_auth(
            client_id="client-id",
            client_secret=CLIENT_SECRET,
            redirect_uri="http://127.0.0.1:8085/callback",
            expected_state="expected",
            callback={"code": "auth-code", "state": "other", "error": ""},
            token_file=tmp_path / "yahoo_refresh_token",
            transport=transport,
        )
    assert transport.calls == []
    assert not (tmp_path / "yahoo_refresh_token").exists()


def test_authorization_url_and_secret_argv(tmp_path: Path) -> None:
    url = authorization_url("client-id", "http://127.0.0.1:8085/callback", "state-1")
    assert url.startswith("https://api.login.yahoo.com/oauth2/request_auth?")
    assert "client_secret" not in url
    argv = github_secret_set_argv("Jpidge/gridiron-lab", tmp_path / "yahoo_refresh_token")
    assert argv[:4] == ["gh", "secret", "set", "YAHOO_REFRESH_TOKEN"]
    assert "--body-file" in argv
    assert ORIGINAL_REFRESH not in " ".join(argv)


def test_xml_and_json_settings_parse() -> None:
    xml = load_document((FIXTURES / "league_settings.xml").read_bytes())
    modifiers = stat_modifiers(xml)
    assert (4, 0.04) in [(item.stat_id, item.value) for item in modifiers]
    assert current_week(xml) == 7
    document = load_document((FIXTURES / "league_settings.json").read_bytes())
    assert current_week(document) == 7
    assert len(stat_modifiers(document)) == 30
    teams = teams_from(load_document((FIXTURES / "teams.json").read_bytes()))
    assert [team.manager for team in teams] == ["Wynn Adler", "Nia Pell"]
    game = load_document((FIXTURES / "game.json").read_bytes())
    assert game_key(game) == "449"


def test_fixtures_are_fictional() -> None:
    text = "\n".join(path.read_text(encoding="utf-8") for path in FIXTURES.iterdir())
    assert "Fantasy Football Coalition" not in text
    assert "refresh_token" not in text
    assert "client_secret" not in text


def test_capture_maps_store_dashboard_and_reports(tmp_path: Path) -> None:
    output = tmp_path / "out"
    data_dir = tmp_path / "data"
    code = capture(
        config=load_config(),
        output=output,
        data_dir=data_dir,
        fixture_dir=FIXTURES,
        clock=lambda: CLOCK,
    )
    assert code == 0
    snapshot = next((output / "yahoo").rglob("manifest.json")).parent
    manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["known_as_of"] == "2026-10-22T20:00:00Z"
    assert manifest["source"] == "yahoo"
    assert manifest["lines_status"] == "not_captured"
    report = json.loads((snapshot / "reconciliation.json").read_text(encoding="utf-8"))
    assert report["dst_points_allowed"]["status"] == "needs_live_comparison"
    assert report["past_week_projections"]["status"] == "needs_live_request"
    assert report["past_week_projections"]["fixture_status"] == "empty_in_fixture"
    assert report["past_week_projections"]["requested_week"] == 6
    scoring = report["scoring_settings"]
    assert scoring["status"] == "mismatches"
    mismatch_ids = {row["stat_id"] for row in scoring["mismatches"]}
    assert mismatch_ids == {5, 56}
    assert scoring["unmapped_stat_ids"] == [{"stat_id": 999, "yahoo_value": 1.0}]
    assert "return_yards_per_point" in scoring["toml_rules_without_yahoo_stat_id"]
    assert scoring["shape_mismatches"][0]["toml_rule"] == "field_goal_yards_per_point"
    assert report["lines"]["status"] == "not_captured"
    roster_path = snapshot / "roster_for_dashboard.json"
    roster = json.loads(roster_path.read_text(encoding="utf-8"))
    assert roster["example"] is True
    assert roster["source"] == "yahoo"
    purple = next(team for team in roster["teams"] if team["team_id"] == 10)
    assert purple["manager"] == "Wynn Adler"
    assert purple["opponent_id"] == 4
    ids = {player["player_id"] for player in purple["players"]}
    assert "gsis-fixture-90001" in ids
    assert "449.p.90002" in ids
    assert "DEF:HBR" in ids

    store = ParquetStore(data_dir)
    assert set(store.column_values("player_xref", "source")) == {"yahoo"}
    assert set(store.column_values("player_xref", "known_as_of")) == {"2026-10-22T20:00:00Z"}
    assert set(store.column_values("identity_issues", "external_id")) == {
        "449.p.90002",
        "449.p.90003",
    }
    assert "gsis-fixture-90001" in store.column_values("rosters", "player_id")
    assert set(store.column_values("rosters", "season")) == {2026}
    assert set(store.column_values("player_xref", "season")) == {0}

    plain = tmp_path / "dash.json"
    export_dashboard(config=load_config(), rosters_path=roster_path, output=plain)
    exported = json.loads(plain.read_text(encoding="utf-8"))
    shown = next(team for team in exported["teams"] if team["team_id"] == 10)
    assert shown["manager_display"] == "Wynn Adler"
    yahoo_points = [player["yahoo"] for player in shown["starters"] + shown["bench"]]
    assert 18.4 in yahoo_points

    hidden = replace(load_config(), dashboard=DashboardSettings(anonymize_managers=True))
    anon_path = tmp_path / "anon.json"
    export_dashboard(config=hidden, rosters_path=roster_path, output=anon_path)
    anon_text = anon_path.read_text(encoding="utf-8")
    assert "Wynn Adler" not in anon_text
    assert "Nia Pell" not in anon_text
    assert "Manager 10" in anon_text
    marker = json.loads((output / "refresh_rotation.json").read_text(encoding="utf-8"))
    assert marker == {"rotated": False, "secret_name": "YAHOO_REFRESH_TOKEN"}


def test_capture_without_secrets_is_a_noop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("YAHOO_CLIENT_ID", raising=False)
    output = tmp_path / "out"
    code = capture(
        config=load_config(),
        output=output,
        allow_missing_secrets=True,
        clock=lambda: CLOCK,
    )
    assert code == 0
    assert not output.exists()
    assert run(["yahoo-capture", "--output", str(output), "--allow-missing-secrets"]) == 0


def test_store_rejects_an_unknown_source(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    row = {
        "source_system": "yahoo",
        "external_id": "449.p.1",
        "player_id": "gsis-fixture-1",
        "match_method": "supplied_xref",
        "match_confidence": 1.0,
        "known_as_of": "2026-10-22T20:00:00Z",
        "known_as_of_method": "yahoo_capture",
        "source": "yahoo",
        "season": 0,
    }
    assert store.append("player_xref", [row]) == 1
    rejected = dict(row)
    rejected["source"] = "somewhere"
    rejected["external_id"] = "449.p.2"
    with pytest.raises(IngestError, match="nflverse or yahoo"):
        store.append("player_xref", [rejected])


def test_past_week_report_does_not_treat_a_fixture_as_proof() -> None:
    report = past_week_report(6, 4, from_fixture=True)
    assert report["fixture_status"] == "present_in_fixture"
    assert report["status"] == "needs_live_request"


def test_token_file_mode(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "yahoo_refresh_token"
    write_refresh_token(path, "local-only-token")
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.read_text(encoding="utf-8").strip() == "local-only-token"


def test_workflows_noop_until_secrets_exist() -> None:
    root = Path(__file__).resolve().parents[1]
    capture_yml = (root / ".github" / "workflows" / "yahoo-capture.yml").read_text(encoding="utf-8")
    pages_yml = (root / ".github" / "workflows" / "pages.yml").read_text(encoding="utf-8")
    assert 'cron: "0 20 * * 4"' in capture_yml
    for name in (
        "YAHOO_CLIENT_ID",
        "YAHOO_CLIENT_SECRET",
        "YAHOO_REFRESH_TOKEN",
        "YAHOO_SECRETS_ADMIN_TOKEN",
        "DATA_REPO_TOKEN",
        "DATA_REPO_DEPLOY_KEY",
    ):
        assert f"secrets.{name}" in capture_yml
    assert "YAHOO_CLIENT_ID is unset" in capture_yml
    assert "Jpidge/gridiron-lab-data" in capture_yml
    assert "examples/dashboard/rosters.json" in pages_yml
    assert "roster_for_dashboard.json" in pages_yml
    assert "DATA_REPO_TOKEN" in pages_yml
    assert "gridiron-lab-data" in pages_yml


def test_http_error_is_not_retried_as_a_rate_limit(tmp_path: Path) -> None:
    transport = ScriptTransport(
        [
            _json_response(200, {"access_token": ACCESS_TOKEN, "expires_in": 3600}),
            _json_response(200, _game_body()),
            HttpResult(400, {}, b""),
        ]
    )
    client = _client(tmp_path, transport)
    with pytest.raises(YahooError, match="HTTP 400"):
        client.league_settings()
    assert len(transport.calls) == 3
