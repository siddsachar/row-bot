"""Try Row-Bot free: the OpenRouter sign-in (PKCE), with OpenRouter faked."""
from __future__ import annotations

import base64
import hashlib
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse
import urllib.request
import urllib.error

import pytest

pytestmark = pytest.mark.subsystem


@pytest.fixture
def openrouter(monkeypatch):
    """OpenRouter's key exchange and the browser, faked; saving a key is recorded."""
    import httpx

    from row_bot.application import openrouter_sign_in, provider_settings_commands
    from row_bot.providers import credential_controls, model_catalog_cache

    state = SimpleNamespace(opened=[], exchanges=[], saved=[], answer={"key": "sk-or-synthetic"}, status=200)

    class _Response:
        def __init__(self):
            self.status_code = state.status

        def raise_for_status(self):
            if self.status_code >= 400:
                raise httpx.HTTPStatusError("refused", request=None, response=None)

        def json(self):
            return state.answer

    def post(url, json=None, **_kwargs):
        state.exchanges.append((url, json))
        return _Response()

    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(openrouter_sign_in, "_open_browser", state.opened.append)
    monkeypatch.setattr(credential_controls, "save_api_key", lambda provider, key, **_k: state.saved.append((provider, key)))
    monkeypatch.setattr(provider_settings_commands, "invalidate_provider_runtime", lambda: None)
    monkeypatch.setattr(model_catalog_cache, "provider_connection_changed", lambda *a, **k: None)
    openrouter_sign_in._FLOWS.clear()
    state.module = openrouter_sign_in
    yield state
    for flow in list(openrouter_sign_in._FLOWS.values()):
        openrouter_sign_in._close(flow)
    openrouter_sign_in._FLOWS.clear()


def _device(on_this_computer=True, origins=()):
    return SimpleNamespace(on_this_computer=on_this_computer, public_origins=origins)


def _visit(url: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url.replace("localhost", "127.0.0.1"), timeout=10) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode()


def test_on_this_computer_the_browser_opens_openrouter_and_its_code_becomes_the_saved_key(openrouter):
    flow = openrouter.module.start(owner_id="owner", device=_device())

    assert flow["phase"] == "waiting" and flow["route"] == "this_computer"
    assert openrouter.opened == [flow["url"]]
    query = parse_qs(urlparse(flow["url"]).query)
    assert flow["url"].startswith("https://openrouter.ai/auth?")
    assert query["code_challenge_method"] == ["S256"]
    callback = query["callback_url"][0]
    assert urlparse(callback).hostname == "localhost"

    status, text = _visit(callback + "?code=one-time-code")

    assert (status, text) == (200, "Row-Bot is signed in to OpenRouter. You can close this tab.")
    url, body = openrouter.exchanges[0]
    assert url == "https://openrouter.ai/api/v1/auth/keys" and body["code"] == "one-time-code"
    # The verifier sent is the one the challenge was made from.
    challenge = base64.urlsafe_b64encode(hashlib.sha256(body["code_verifier"].encode()).digest()).rstrip(b"=").decode()
    assert query["code_challenge"] == [challenge]
    assert openrouter.saved == [("openrouter", "sk-or-synthetic")]
    assert openrouter.module.read(owner_id="owner", flow_id=flow["flow_id"])["phase"] == "done"


def test_the_loopback_turns_away_any_address_but_this_sign_ins(openrouter):
    flow = openrouter.module.start(owner_id="owner", device=_device())
    callback = parse_qs(urlparse(flow["url"]).query)["callback_url"][0]
    port = urlparse(callback).port

    status, _ = _visit(f"http://127.0.0.1:{port}/not-the-state?code=one-time-code")

    assert status == 400 and openrouter.exchanges == [] and openrouter.saved == []
    assert openrouter.module.read(owner_id="owner", flow_id=flow["flow_id"])["phase"] == "waiting"


def test_another_device_returns_through_row_bots_https_address_with_the_one_time_state(openrouter):
    flow = openrouter.module.start(owner_id="owner", device=_device(False, ("https://rowbot.example.test",)))

    assert openrouter.opened == [] and flow["route"] == "device"
    callback = parse_qs(urlparse(flow["url"]).query)["callback_url"][0]
    assert callback.startswith("https://rowbot.example.test/api/v1/settings/providers/openrouter/sign-in/callback?state=")
    state = parse_qs(urlparse(callback).query)["state"][0]

    assert openrouter.module.accept_callback(state="wrong", code="one-time-code") == "invalid"
    # OpenRouter adds its code to the callback's own query.
    assert openrouter.module.accept_callback(state=f"{state}?code=one-time-code", code="") == "done"
    assert openrouter.saved == [("openrouter", "sk-or-synthetic")]
    assert openrouter.module.accept_callback(state=state, code="again") == "invalid"


def test_without_an_https_address_another_device_is_told_what_works_instead(openrouter):
    flow = openrouter.module.start(owner_id="owner", device=_device(False, ()))

    assert flow["phase"] == "unavailable" and flow["url"] is None
    assert "use an OpenRouter key" in flow["message"]
    assert openrouter.opened == []


def test_a_refused_exchange_saves_nothing_and_says_what_to_do(openrouter):
    openrouter.status = 400
    flow = openrouter.module.start(owner_id="owner", device=_device(False, ("https://rowbot.example.test",)))
    state = parse_qs(urlparse(parse_qs(urlparse(flow["url"]).query)["callback_url"][0]).query)["state"][0]

    assert openrouter.module.accept_callback(state=state, code="one-time-code") == "failed"
    snapshot = openrouter.module.read(owner_id="owner", flow_id=flow["flow_id"])
    assert snapshot["message"] == "Signing in to OpenRouter didn't finish. Try again, or use an OpenRouter key."
    assert openrouter.saved == []


def test_a_cancelled_sign_in_takes_no_code_and_is_only_its_owners(openrouter):
    from row_bot.application.client_platform import ClientPlatformError

    flow = openrouter.module.start(owner_id="owner", device=_device(False, ("https://rowbot.example.test",)))
    with pytest.raises(ClientPlatformError):
        openrouter.module.read(owner_id="someone-else", flow_id=flow["flow_id"])
    state = parse_qs(urlparse(parse_qs(urlparse(flow["url"]).query)["callback_url"][0]).query)["state"][0]

    assert openrouter.module.cancel(owner_id="owner", flow_id=flow["flow_id"])["phase"] == "cancelled"
    assert openrouter.module.accept_callback(state=state, code="one-time-code") == "invalid"
    assert openrouter.saved == []


@pytest.mark.parametrize(("raw", "reason"), [
    ("API error: Error code: 429 - {'error': {'message': 'Rate limit exceeded: free-models-per-day. Add 10 credits',"
     " 'metadata': {'headers': {'X-RateLimit-Reset': '1800000000000'}}}}", "Today's free messages are used up. They come back at "),
    ("Rate limit exceeded: free-models-per-day", "Today's free messages are used up. They come back tomorrow."),
    ("Rate limit exceeded: free-models-per-min", "Free models take about 20 messages a minute. Wait a minute, then resume."),
])
def test_a_reached_free_limit_reads_as_a_plain_sentence(raw, reason):
    from row_bot.application.turn_failure import failure_reason

    assert failure_reason(raw).startswith(reason)
