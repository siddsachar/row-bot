"""Every model provider's sign-in finishes in a browser on another device (H5).

A hosted Row-Bot's browser is never on the server's computer, so no sign-in may
depend on a browser opening there or on a redirect reaching the server's own
loopback address. ChatGPT uses a device code the server checks itself; Grok's
shared xAI client can only return to 127.0.0.1, so another device finishes by
pasting the address its browser ends on, bound to the sign-in by its state and
PKCE verifier. (The Claude plan is H15's own panel: test_claude_sign_in.py.)
The provider's side is a fake HTTP client: nothing reaches OpenAI or xAI.
"""
from __future__ import annotations

import socket
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import pytest

from row_bot.application import subscription_controls as controls
from row_bot.providers import auth_store, codex, xai_oauth
from tests.subsystem.providers.test_provider_settings_controls import store as store

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]


class Response:
    def __init__(self, payload: dict, status_code: int = 200):
        self.status_code = status_code
        self._payload = payload
        self.text = ""

    def json(self) -> dict:
        return self._payload


class XaiServer:
    """xAI's discovery and token endpoints, as a fake HTTP client."""

    def __init__(self):
        self.exchanged: list[dict] = []

    def get(self, url, **_kwargs):
        assert url == xai_oauth.XAI_OAUTH_DISCOVERY_URL
        return Response({"issuer": "https://auth.x.ai", "authorization_endpoint": "https://auth.x.ai/oauth2/authorize",
                         "token_endpoint": "https://auth.x.ai/oauth2/token"})

    def post(self, url, *, data, **_kwargs):
        assert url == "https://auth.x.ai/oauth2/token"
        self.exchanged.append(dict(data))
        return Response({"access_token": "synthetic-grok-access", "refresh_token": "synthetic-grok-refresh"})

    def close(self):
        pass


@pytest.fixture
def no_local_browser(monkeypatch):
    def refuse(*_args, **_kwargs):
        raise AssertionError("a sign-in must never open a browser on the server's computer")

    monkeypatch.setattr("webbrowser.open", refuse)
    monkeypatch.setattr("webbrowser.open_new_tab", refuse)


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _command(provider: str, operation: str = "start", flow: dict | None = None, value: str | None = None) -> dict:
    payload = {"provider_id": provider, "provider_revision": controls.read_accounts().revision}
    if flow:
        payload.update({"flow_id": flow["flow_id"], "server_epoch": flow["server_epoch"]})
    if value is not None:
        payload["value"] = value
    return {"command_id": str(uuid4()), "type": f"provider.subscription.{operation}", "expected_revision": "0",
            "payload": payload}


def _execute(owner, command: dict) -> dict:
    return owner.execute(owner_id="phone-session", key=command["command_id"], command=command,
                         validate=lambda: None, validate_review=lambda _review: None)


def test_grok_finishes_from_another_device_with_the_address_its_browser_ends_on(store, no_local_browser, monkeypatch):
    monkeypatch.setenv(xai_oauth.ROW_BOT_XAI_OAUTH_REDIRECT_PORT_ENV, str(_free_port()))
    server = XaiServer()
    owner = controls.SubscriptionFlows(client_factory=lambda _guard: server)
    try:
        started = _execute(owner, _command("xai_oauth"))["flow"]
        # The phone gets xAI's own page; Row-Bot opens nothing on the server.
        link = urlparse(started["authorization_url"])
        assert (link.scheme, link.hostname) == ("https", "auth.x.ai")
        query = parse_qs(link.query)
        redirect, state = query["redirect_uri"][0], query["state"][0]
        assert urlparse(redirect).hostname == "127.0.0.1"
        assert started["state"] == "waiting"

        # After signing in, xAI sends the phone to 127.0.0.1, which the phone can't open. An
        # address from another sign-in, or something else pasted, is refused in plain words and
        # the sign-in keeps waiting; the right one finishes it.
        for wrong in (f"{redirect}?code=stolen&state=someone-else", "https://x.ai/"):
            with pytest.raises(controls.SubscriptionError, match="subscription_callback_invalid"):
                _execute(owner, _command("xai_oauth", "submit", started, wrong))
        assert server.exchanged == [] and not auth_store.get_provider_secret("xai_oauth", "access_token")
        assert owner.read(owner_id="phone-session", flow_id=started["flow_id"], server_epoch=started["server_epoch"],
                          validate=lambda: None).state == "waiting"

        done = _execute(owner, _command("xai_oauth", "submit", started, f"{redirect}?code=synthetic-code&state={state}"))
    finally:
        owner.dispose()

    assert done["flow"]["state"] == "connected"
    assert [(item["code"], item["redirect_uri"]) for item in server.exchanged] == [("synthetic-code", redirect)]
    assert auth_store.get_provider_secret("xai_oauth", "access_token") == "synthetic-grok-access"


def test_grok_says_when_xai_refused_the_sign_in(store, no_local_browser, monkeypatch):
    monkeypatch.setenv(xai_oauth.ROW_BOT_XAI_OAUTH_REDIRECT_PORT_ENV, str(_free_port()))
    server = XaiServer()
    owner = controls.SubscriptionFlows(client_factory=lambda _guard: server)
    try:
        started = _execute(owner, _command("xai_oauth"))["flow"]
        query = parse_qs(urlparse(started["authorization_url"]).query)
        refused = f"{query['redirect_uri'][0]}?error=access_denied&state={query['state'][0]}"
        with pytest.raises(controls.SubscriptionError, match="subscription_denied"):
            _execute(owner, _command("xai_oauth", "submit", started, refused))
    finally:
        owner.dispose()
    assert server.exchanged == []


def test_chatgpt_gives_another_device_a_code_and_finishes_on_the_server(store, no_local_browser, monkeypatch):
    approved: list[bool] = []
    monkeypatch.setattr(codex, "start_codex_device_flow", lambda **_kw: SimpleNamespace(
        verification_uri="https://auth.openai.com/codex/device", user_code="ABCD-EFGH", interval_seconds=0.01,
        expires_at="2030-01-01T00:00:00+00:00"))
    monkeypatch.setattr(codex, "poll_codex_device_authorization",
                        lambda *_a, **_kw: SimpleNamespace() if approved else None)
    monkeypatch.setattr(codex, "exchange_codex_device_authorization", lambda *_a, **_kw: codex.CodexTokenSet(
        access_token="synthetic-chatgpt-access", refresh_token="synthetic-chatgpt-refresh",
        account_id="synthetic-account"))
    owner = controls.SubscriptionFlows(client_factory=lambda _guard: SimpleNamespace(close=lambda: None))
    try:
        started = _execute(owner, _command("codex"))["flow"]
        # Everything the phone needs is in the answer: OpenAI's page and the code to type there.
        assert started["device_code"] == "ABCD-EFGH"
        assert urlparse(started["authorization_url"]).scheme == "https"

        approved.append(True)  # The person approves on the phone; no page asks the server to check.
        retained = owner._flows[started["flow_id"]]
        retained.thread.join(5)
        seen = owner.read(owner_id="phone-session", flow_id=started["flow_id"], server_epoch=started["server_epoch"],
                          validate=lambda: None)
    finally:
        owner.dispose()

    assert seen.state == "connected"
    assert auth_store.get_provider_secret("codex", "access_token") == "synthetic-chatgpt-access"
