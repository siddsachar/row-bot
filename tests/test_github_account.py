from __future__ import annotations

from dataclasses import dataclass
from email.message import Message
import json
import urllib.error

import row_bot.github_account as github_account


@dataclass(frozen=True)
class _Gh:
    installed: bool = True
    authenticated: bool = True
    version: str = "gh"
    user: str = "octo"
    message: str = "Authenticated with GitHub CLI."
    path: str = "gh"


def setup_function():
    github_account._clear_token_cache_for_tests()


class _Response:
    def __init__(self, body: dict, headers: dict[str, str] | None = None, status: int = 200):
        self._body = json.dumps(body).encode("utf-8")
        self.headers = Message()
        for key, value in (headers or {}).items():
            self.headers[key] = value
        self.status = status

    def read(self, _limit: int = -1) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _tb):
        return False


def _rate_headers(limit: int = 5000, remaining: int = 4999, reset: int = 1893456000) -> dict[str, str]:
    return {
        "x-ratelimit-limit": str(limit),
        "x-ratelimit-remaining": str(remaining),
        "x-ratelimit-used": str(max(limit - remaining, 0)),
        "x-ratelimit-reset": str(reset),
    }


def _request_headers(request) -> dict[str, str]:
    return {key.lower(): value for key, value in request.header_items()}


def test_github_token_resolution_prefers_environment(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "env-token")
    monkeypatch.setattr(github_account.api_keys, "get_key", lambda _name: "saved-token")
    monkeypatch.setattr(github_account, "_github_cli_token", lambda: "cli-token")

    token = github_account.resolve_github_token()

    assert token.value == "env-token"
    assert token.source == "environment"
    assert token.fingerprint.endswith("oken")


def test_github_token_resolution_uses_saved_token_before_cli(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setattr(github_account.api_keys, "get_key", lambda _name: "saved-token")
    monkeypatch.setattr(github_account, "_github_cli_token", lambda: "cli-token")

    token = github_account.resolve_github_token()

    assert token.value == "saved-token"
    assert token.source == "keyring"


def test_github_token_resolution_uses_cli_when_no_saved_token(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setattr(github_account.api_keys, "get_key", lambda _name: "")
    monkeypatch.setattr(github_account, "_github_cli_token", lambda: "cli-token")

    token = github_account.resolve_github_token()

    assert token.value == "cli-token"
    assert token.source == "github_cli"


def test_non_cli_status_lookup_does_not_poison_cli_token_cache(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setattr(github_account.api_keys, "get_key", lambda _name: "")
    monkeypatch.setattr(github_account, "_github_cli_token", lambda: "cli-token")

    empty = github_account.resolve_github_token(include_cli=False)
    token = github_account.resolve_github_token(include_cli=True)

    assert not empty.value
    assert token.value == "cli-token"
    assert token.source == "github_cli"


def test_github_headers_include_bearer_without_exposing_token(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "env-token")

    headers = github_account.github_api_headers()

    assert headers["Authorization"] == "Bearer env-token"
    assert headers["User-Agent"].startswith("Row-Bot-GitHub")


def test_invalid_token_does_not_poison_public_headers(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "bad-token")
    monkeypatch.setattr(github_account.api_keys, "get_key", lambda _name: "")
    monkeypatch.setattr(github_account, "_github_cli_status", lambda: _Gh(user=""))

    def fake_urlopen(request, timeout=0):
        headers = _request_headers(request)
        if request.full_url.endswith("/rate_limit") and "authorization" in headers:
            raise urllib.error.HTTPError(
                request.full_url,
                401,
                "Bad credentials",
                Message(),
                None,
            )
        return _Response({"resources": {"core": {}}}, _rate_headers(60, 42))

    monkeypatch.setattr(github_account.urllib.request, "urlopen", fake_urlopen)

    status = github_account.get_verified_github_account_status(use_cache=False)
    public_headers = github_account.github_public_api_headers()

    assert not status.connected
    assert status.state == github_account.GITHUB_STATE_INVALID_TOKEN
    assert status.anonymous_ok
    assert "Authorization" not in public_headers


def test_rate_limited_token_falls_back_to_anonymous_public_headers(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "limited-token")
    monkeypatch.setattr(github_account.api_keys, "get_key", lambda _name: "")
    monkeypatch.setattr(github_account, "_github_cli_status", lambda: _Gh(user=""))

    def fake_urlopen(request, timeout=0):
        headers = _request_headers(request)
        if request.full_url.endswith("/rate_limit") and "authorization" in headers:
            return _Response({"resources": {"core": {}}}, _rate_headers(5000, 0))
        return _Response({"resources": {"core": {}}}, _rate_headers(60, 21))

    monkeypatch.setattr(github_account.urllib.request, "urlopen", fake_urlopen)

    status = github_account.get_verified_github_account_status(use_cache=False)
    public_headers = github_account.github_public_api_headers()

    assert status.state == github_account.GITHUB_STATE_RATE_LIMITED
    assert status.token_valid
    assert status.token_limited
    assert status.anonymous_ok
    assert "Authorization" not in public_headers


def test_verified_token_uses_auth_for_public_headers(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "good-token")
    monkeypatch.setattr(github_account.api_keys, "get_key", lambda _name: "")
    monkeypatch.setattr(github_account, "_github_cli_status", lambda: _Gh(user=""))

    def fake_urlopen(request, timeout=0):
        if request.full_url.endswith("/user"):
            return _Response({"login": "octo"}, _rate_headers(5000, 4998))
        return _Response({"resources": {"core": {}}}, _rate_headers(5000, 4999))

    monkeypatch.setattr(github_account.urllib.request, "urlopen", fake_urlopen)

    status = github_account.get_verified_github_account_status(use_cache=False)
    public_headers = github_account.github_public_api_headers()

    assert status.connected
    assert status.user == "octo"
    assert public_headers["Authorization"] == "Bearer good-token"


def test_verified_status_cache_avoids_repeated_network(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "good-token")
    monkeypatch.setattr(github_account.api_keys, "get_key", lambda _name: "")
    monkeypatch.setattr(github_account, "_github_cli_status", lambda: _Gh(user=""))
    calls = {"count": 0}

    def fake_urlopen(request, timeout=0):
        calls["count"] += 1
        if request.full_url.endswith("/user"):
            return _Response({"login": "octo"}, _rate_headers(5000, 4998))
        return _Response({"resources": {"core": {}}}, _rate_headers(5000, 4999))

    monkeypatch.setattr(github_account.urllib.request, "urlopen", fake_urlopen)

    github_account.get_verified_github_account_status(use_cache=True)
    github_account.get_verified_github_account_status(use_cache=True)

    assert calls["count"] == 2


def test_rate_limit_headers_parse_primary_limit():
    rate = github_account.rate_limit_from_headers(
        {
            "x-ratelimit-limit": "60",
            "x-ratelimit-remaining": "0",
            "x-ratelimit-used": "60",
            "x-ratelimit-reset": "1893456000",
        },
        status_code=403,
        body='{"message":"API rate limit exceeded"}',
    )

    assert rate.limited
    assert not rate.secondary
    assert rate.limit == 60
    assert rate.remaining == 0
    assert "Settings -> Accounts" in github_account.rate_limit_message(rate)


def test_rate_limit_headers_parse_secondary_limit():
    rate = github_account.rate_limit_from_headers(
        {"retry-after": "45", "x-ratelimit-remaining": "10"},
        status_code=403,
        body='{"message":"You have exceeded a secondary rate limit"}',
    )

    assert rate.limited
    assert rate.secondary
    assert rate.retry_after_seconds == 45
    assert "secondary" in github_account.rate_limit_message(rate).lower()


def test_github_account_status_reports_cli_as_unverified_until_checked(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setattr(github_account.api_keys, "get_key", lambda _name: "")
    monkeypatch.setattr(github_account, "_github_cli_token", lambda: "cli-token")
    monkeypatch.setattr(github_account, "_github_cli_status", lambda: _Gh())

    status = github_account.get_github_account_status()

    assert not status.connected
    assert status.source == "github_cli"
    assert status.state == github_account.GITHUB_STATE_CONFIGURED_UNCHECKED
    assert status.user == "octo"


def test_cli_status_can_be_authenticated_while_api_token_is_invalid(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setattr(github_account.api_keys, "get_key", lambda _name: "")
    monkeypatch.setattr(github_account, "_github_cli_token", lambda: "bad-cli-token")
    monkeypatch.setattr(github_account, "_github_cli_status", lambda: _Gh())

    def fake_urlopen(request, timeout=0):
        headers = _request_headers(request)
        if request.full_url.endswith("/rate_limit") and "authorization" in headers:
            raise urllib.error.HTTPError(request.full_url, 401, "Bad credentials", Message(), None)
        return _Response({"resources": {"core": {}}}, _rate_headers(60, 12))

    monkeypatch.setattr(github_account.urllib.request, "urlopen", fake_urlopen)

    status = github_account.get_verified_github_account_status(use_cache=False)

    assert status.gh_authenticated
    assert status.state == github_account.GITHUB_STATE_INVALID_TOKEN
    assert not status.connected
    assert status.anonymous_ok


def test_settings_exposes_github_account_panel():
    source = open("src/row_bot/ui/settings.py", "r", encoding="utf-8").read()

    assert "_build_github_account_panel" in source
    assert "GitHub Personal Access Token" in source
    assert "GitHub — ✅ Connected" in source
    assert "Check GitHub" in source
    assert "Reconnect CLI" in source
    assert "gh auth login -h github.com" in source
    assert "gh auth refresh -h github.com" in source
    assert "github account status load" in source
    assert "safe_ui_task(lambda token=token, force=force: _load_github_status" in source


def test_settings_github_label_does_not_verify_synchronously():
    source = open("src/row_bot/ui/settings.py", "r", encoding="utf-8").read()
    helper_start = source.index("def _github_status_text")
    helper_end = source.index("with ui.expansion(_github_status_text", helper_start)
    helper_body = source[helper_start:helper_end]

    assert "get_verified_github_account_status" not in helper_body
    assert "Checking..." in helper_body


def test_settings_github_status_load_runs_off_ui_thread():
    source = open("src/row_bot/ui/settings.py", "r", encoding="utf-8").read()
    load_start = source.index("async def _load_github_status")
    load_end = source.index("with ui.row().classes(\"gap-2 items-center\")", load_start)
    load_body = source[load_start:load_end]

    assert "await run.io_bound" in load_body
    assert "get_verified_github_account_status" in load_body


def test_status_bar_registers_github_account_pill():
    from row_bot.ui import status_checks
    from row_bot.ui.status_bar import _STATUS_ICON_MAP

    assert status_checks.check_github_oauth in status_checks.ALL_CHECKS
    assert status_checks.check_github_oauth in status_checks.HEAVY_CHECKS
    assert _STATUS_ICON_MAP["GitHub"] == "code"
