"""Google and X OAuth actions are explicit, fenced, and isolated; their sign-ins live in the keychain only."""

from __future__ import annotations

import json
from datetime import datetime
from uuid import uuid4

import pytest

from row_bot import account_tokens, secret_store
from row_bot.application import client_account_oauth as owner
from tests.subsystem.client_protocol.test_protocol_security import bootstrap, client_app
from tests.subsystem.plugins.conftest import MemoryKeyring

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]

_CLIENT = {"installed": {"client_id": "fixture-client", "client_secret": "fixture-secret",
                         "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                         "token_uri": "https://oauth2.googleapis.com/token", "redirect_uris": ["http://localhost"]}}


@pytest.fixture
def keychain():
    """Sign-ins are kept in the system keychain only; tests use an in-memory one."""
    keyring = MemoryKeyring()
    secret_store._set_backend_for_tests(keyring)
    yield keyring
    secret_store._set_backend_for_tests(None)


@pytest.fixture(autouse=True)
def _no_waiting_sign_ins():
    """Sign-ins wait in this process; no test leaves one to the next."""
    yield
    with owner._LOCK:
        owner._JOBS.clear()
        owner._ACTIVE.clear()


def _command(snapshot, action, **extra):
    return dict(owner_id="local", command_id=str(uuid4()), account=snapshot["account"],
                expected_revision=snapshot["revision"], action=action, **extra)


def _no_files(profile):
    """No sign-in, client or token is ever written to a file (X's list of accounts holds handles and IDs only)."""
    return not [path for path in profile.rglob("*.json") if path.parent.name in {"gmail", "calendar", "x"}
                and not (path.name == "accounts.json" and "token" not in path.read_text(encoding="utf-8"))]


def _google_ready(tmp_path, monkeypatch, *, token=None):
    profile = tmp_path / "profile"
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(profile))
    account_tokens.write("google_client", _CLIENT)
    if token is not None:
        account_tokens.write("google", token)
    return profile


class ImmediateThread:
    def __init__(self, *, target, args, **_kwargs):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


def _queued(into):
    class FakeThread:
        def __init__(self, *, target, args, **_kwargs):
            into.append((target, args))

        def start(self):
            return None
    return FakeThread


def test_account_auth_read_has_no_profile_or_provider_effect(tmp_path, monkeypatch):
    profile = tmp_path / "profile"
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(profile))
    google = owner.read_account_auth(account="google")
    assert google["state"] == "not_configured"
    assert not profile.exists()


def test_google_credential_import_validates_and_keeps_existing_on_failure(tmp_path, monkeypatch, keychain):
    profile = tmp_path / "profile"
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(profile))
    from row_bot.tools import registry
    monkeypatch.setattr(registry, "set_tool_config", lambda *_args: None)
    before = owner.read_account_auth(account="google")
    with pytest.raises(Exception, match="account_credentials_invalid"):
        owner.execute_account_auth(**_command(before, "import_credentials", credentials_json='{"invalid": true}'))
    assert account_tokens.read("google_client") is None
    imported = owner.execute_account_auth(**_command(before, "import_credentials", credentials_json=json.dumps(_CLIENT)))
    assert imported["phase"] == "completed"
    assert imported["snapshot"]["configured"] is True
    assert "fixture-secret" not in json.dumps(imported)
    assert account_tokens.read("google_client")["installed"]["client_id"] == "fixture-client"  # Keychain, not a file.
    assert _no_files(profile)
    with pytest.raises(Exception, match="account_changed"):
        owner.execute_account_auth(**_command(before, "start"))


@pytest.mark.parametrize(("token_uri", "accepted"), [
    ("https://accounts.google.com/o/oauth2/token", True),  # Older client files still name this one.
    ("https://evil.example.test/token", False),
    ("http://oauth2.googleapis.com/token", False),
    ("https://evil.example.test\\@oauth2.googleapis.com/token", False),  # Parsers disagree on where this goes.
    ("https://user@oauth2.googleapis.com/token", False),
    ("https://oauth2.googleapis.com:8443/token", False),
])
def test_a_google_client_file_must_name_only_googles_own_sign_in_hosts(tmp_path, monkeypatch, keychain, token_uri,
                                                                         accepted):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "profile"))
    from row_bot.tools import registry
    monkeypatch.setattr(registry, "set_tool_config", lambda *_args: None)
    before = owner.read_account_auth(account="google")
    file = json.dumps({"installed": {**_CLIENT["installed"], "token_uri": token_uri}})
    if accepted:
        assert owner.execute_account_auth(**_command(before, "import_credentials", credentials_json=file))["phase"] == "completed"
        saved = account_tokens.read("google_client")["installed"]
        assert saved["token_uri"] == "https://oauth2.googleapis.com/token"  # Saved as Google's own, whatever it said.
    else:
        with pytest.raises(Exception, match="account_credentials_invalid"):
            owner.execute_account_auth(**_command(before, "import_credentials", credentials_json=file))


def test_google_auth_cancellation_discards_late_credentials(tmp_path, monkeypatch, keychain):
    profile = _google_ready(tmp_path, monkeypatch)
    captured = []
    monkeypatch.setattr(owner, "Thread", _queued(captured))
    monkeypatch.setattr(owner, "_google_flow", lambda: b'{"token":"fixture"}')
    snapshot = owner.read_account_auth(account="google")
    command = _command(snapshot, "start")
    running = owner.execute_account_auth(**command)
    assert running["phase"] == "running"
    cancelled = owner.cancel_account_auth(owner_id="local", command_id=command["command_id"])
    assert cancelled["phase"] == "cancel_requested"
    captured[0][0](*captured[0][1])
    recovered = owner.read_account_auth_receipt(owner_id="local", command_id=command["command_id"])
    assert recovered["phase"] == "cancelled"
    assert account_tokens.read("google") is None and _no_files(profile)


def test_google_auth_revoked_before_callback_keeps_prior_tokens(tmp_path, monkeypatch, keychain):
    _google_ready(tmp_path, monkeypatch, token={"token": "original"})
    queued = []
    monkeypatch.setattr(owner, "Thread", _queued(queued))
    monkeypatch.setattr(owner, "_google_flow", lambda: b'{"token":"new"}')
    snapshot = owner.read_account_auth(account="google")
    command = _command(snapshot, "start")
    owner.execute_account_auth(**command, validate=lambda: (_ for _ in ()).throw(RuntimeError("revoked")))
    queued[0][0](*queued[0][1])
    receipt = owner.read_account_auth_receipt(owner_id="local", command_id=command["command_id"])
    assert receipt["phase"] == "failed"
    assert account_tokens.read("google") == {"token": "original"}


def test_a_sign_in_the_keychain_cannot_keep_fails_and_is_never_written_to_a_file(tmp_path, monkeypatch, keychain):
    profile = _google_ready(tmp_path, monkeypatch, token={"token": "original"})
    monkeypatch.setattr(owner, "_google_flow", lambda: b'{"token":"new"}')
    monkeypatch.setattr(owner, "Thread", ImmediateThread)
    snapshot = owner.read_account_auth(account="google")
    def refused(*_args):
        raise RuntimeError("Credential Manager is full")  # The keychain refuses the new sign-in.
    monkeypatch.setattr(keychain, "set_password", refused)
    receipt = owner.execute_account_auth(**_command(snapshot, "start"))
    assert receipt["phase"] == "failed"
    assert account_tokens.read("google") == {"token": "original"} and _no_files(profile)


def test_google_auth_saves_one_sign_in_and_retains_original_command(tmp_path, monkeypatch, keychain):
    profile = _google_ready(tmp_path, monkeypatch, token={"token": "old-token"})
    monkeypatch.setattr(owner, "_google_flow", lambda: b'{"token":"new-token"}')
    monkeypatch.setattr(owner, "Thread", ImmediateThread)
    snapshot = owner.read_account_auth(account="google")
    command = _command(snapshot, "start")
    receipt = owner.execute_account_auth(**command)
    assert receipt["phase"] == "completed"
    # Tokens the provider has just issued are a passing check (B263).
    assert receipt["snapshot"]["state"] == "connected"
    assert account_tokens.read("google") == {"token": "new-token"} and _no_files(profile)
    assert owner.execute_account_auth(**command) == receipt


def test_disconnect_requires_confirmation_and_removes_only_account_tokens(tmp_path, monkeypatch, keychain):
    _google_ready(tmp_path, monkeypatch, token={"token": "google-token"})
    snapshot = owner.read_account_auth(account="google")
    with pytest.raises(Exception, match="account_confirmation_required"):
        owner.execute_account_auth(**_command(snapshot, "disconnect"))
    assert account_tokens.read("google") is not None
    receipt = owner.execute_account_auth(**_command(snapshot, "disconnect", confirmed=True))
    assert receipt["phase"] == "completed"
    assert account_tokens.read("google_client") is not None  # The person's client stays; only the sign-in goes.
    assert account_tokens.read("google") is None
    assert not [value for value in keychain.values.values() if "google-token" in value]


def test_another_device_without_a_public_address_manages_an_account_but_cannot_sign_in(
        tmp_path, monkeypatch, keychain):
    """Every signed-in device reads, disconnects and checks. Without Row-Bot's one
    public HTTPS address, a sign-in can only return to a loopback address on the
    computer Row-Bot runs on, so another device is told so before any flow starts."""
    _google_ready(tmp_path, monkeypatch, token={"token": "google-token"})
    flows = []
    monkeypatch.setattr(owner, "_google_flow", lambda: flows.append("google") or b"{}")
    monkeypatch.setattr(owner, "Thread", ImmediateThread)
    remote, _, active = client_app(remote=True)
    with remote:
        _, headers = bootstrap(remote)
        read = remote.get("/api/v1/accounts/google/auth", headers=headers)
        assert read.status_code == 200, read.text
        snapshot = read.json()
        start = _start(remote, headers, snapshot)
        disconnect_id = str(uuid4())
        disconnect = remote.post("/api/v1/accounts/google/auth/commands",
                                 headers={**headers, "idempotency-key": disconnect_id}, json={
            "command_id": disconnect_id, "account": "google", "action": "disconnect",
            "expected_revision": snapshot["revision"], "confirmed": True,
        })
        active["value"] = False
        signed_out = remote.get("/api/v1/accounts/google/auth", headers=headers)
    assert (snapshot["sign_in"], snapshot["sign_in_blocker"]) == ("unavailable", "public_address_required")
    assert start.status_code == 409 and start.json()["code"] == "account_callback_unavailable"
    assert flows == []
    assert disconnect.status_code == 200, disconnect.text
    assert disconnect.json()["phase"] == "completed"
    assert account_tokens.read("google") is None
    assert signed_out.status_code == 401


# Signing in from another device through Row-Bot's one approved public HTTPS
# address. The phone holds a session; the browser the provider sends back has
# none. Google's and X's token endpoints are fakes: nothing leaves the test.
_ORIGIN = "https://rowbot.example.test"
_GOOGLE_CALLBACK = _ORIGIN + "/api/v1/accounts/google/auth/callback"
_X_CALLBACK = _ORIGIN + "/api/v1/accounts/x/auth/callback"
_WEB = {"web": {"client_id": "fixture-web-client", "client_secret": "fixture-web-secret",
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token", "redirect_uris": [_GOOGLE_CALLBACK]}}
_GOOGLE_SCOPES = "https://mail.google.com/ https://www.googleapis.com/auth/calendar"


def _phone(origins=(_ORIGIN,)):
    """A signed-in phone and a session-less browser, at a Row-Bot with these approved public origins."""
    from fastapi.testclient import TestClient

    from row_bot.access.config import AccessConfig, DeploymentMode
    from row_bot.access.request_context import SessionIdentity
    from row_bot.api.v1.routes import create_client_platform_app
    from tests.subsystem.client_protocol.test_protocol_security import Service

    active = {"value": True}

    def authenticate(scope, provenance):
        signed_in = (b"x-fixture-device", b"phone") in scope.get("headers", [])
        return SessionIdentity("fixture-device", "fixture-session") if signed_in and active["value"] else None

    app = create_client_platform_app(
        Service(), access_config=AccessConfig(deployment_mode=DeploymentMode.SERVER, public_origins=origins),
        session_authenticator=authenticate,
        choices=lambda: {"models": [], "capabilities": [], "catalog_stale": True})
    phone = TestClient(app, base_url="http://localhost", client=("127.0.0.1", 12345),
                       headers={"x-fixture-device": "phone"})
    browser = TestClient(app, base_url="http://localhost", client=("127.0.0.1", 12346), follow_redirects=False)
    return phone, browser, active


def _start(client, headers, snapshot):
    command_id = str(uuid4())
    return client.post(f"/api/v1/accounts/{snapshot['account']}/auth/commands",
                       headers={**headers, "idempotency-key": command_id},
                       json={"command_id": command_id, "account": snapshot["account"], "action": "start",
                             "expected_revision": snapshot["revision"]})


def _query(url):
    from urllib.parse import parse_qs, urlsplit

    return {key: values[0] for key, values in parse_qs(urlsplit(url).query).items()}


def _provider(monkeypatch, respond):
    """Google's or X's token endpoint, faked: ``respond(request)`` answers each exchange."""
    import httpx

    seen = []

    def handle(request):
        seen.append(request)
        return respond(request)
    monkeypatch.setattr(owner, "_token_client", lambda: httpx.Client(transport=httpx.MockTransport(handle)))
    return seen


def _google_grant(request, scope=_GOOGLE_SCOPES):
    import httpx

    return httpx.Response(200, json={"access_token": "fixture-access", "refresh_token": "fixture-refresh",
                                     "expires_in": 3599, "scope": scope, "token_type": "Bearer"})


def _web_client_ready(tmp_path, monkeypatch, *, token=None):
    profile = tmp_path / "profile"
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(profile))
    account_tokens.write("google_client", _WEB)
    if token is not None:
        account_tokens.write("google", token)
    monkeypatch.setattr(owner, "Thread", lambda **_kwargs: pytest.fail("a public-address sign-in waits on no thread"))
    return profile


def _challenge(verifier):
    import base64
    import hashlib

    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def test_a_phone_signs_in_to_google_through_the_public_address(tmp_path, monkeypatch, keychain):
    profile = _web_client_ready(tmp_path, monkeypatch, token={"token": "old"})
    seen = _provider(monkeypatch, _google_grant)
    phone, browser, _ = _phone()
    with phone, browser:
        _, headers = bootstrap(phone)
        snapshot = phone.get("/api/v1/accounts/google/auth", headers=headers).json()
        assert (snapshot["sign_in"], snapshot["sign_in_blocker"]) == ("link", None)
        started = _start(phone, headers, snapshot)
        assert started.status_code == 200, started.text
        receipt = started.json()
        page = _query(receipt["authorization_url"])
        assert receipt["phase"] == "running" and seen == []  # Nothing is contacted until Google sends the browser back.
        assert receipt["authorization_url"].startswith("https://accounts.google.com/o/oauth2/auth?")
        assert (page["client_id"], page["redirect_uri"], page["code_challenge_method"]) == (
            "fixture-web-client", _GOOGLE_CALLBACK, "S256")
        assert (page["scope"], page["access_type"], page["prompt"]) == (_GOOGLE_SCOPES, "offline", "consent")
        # The browser Google sends back carries no session, only the one-time state.
        back = browser.get("/api/v1/accounts/google/auth/callback",
                           params={"state": page["state"], "code": "fixture-code"})
        replayed = browser.get("/api/v1/accounts/google/auth/callback",
                               params={"state": page["state"], "code": "fixture-code"})
        finished = phone.get(f"/api/v1/accounts/google/auth/commands/{receipt['command_id']}", headers=headers).json()
    assert back.status_code == 200 and "signed in to Google" in back.text
    assert back.headers["referrer-policy"] == "no-referrer" and "fixture-code" not in back.text
    assert replayed.status_code == 400  # One callback per state.
    [exchange] = seen
    form = _query("?" + exchange.content.decode())
    assert str(exchange.url) == "https://oauth2.googleapis.com/token"
    assert (form["code"], form["redirect_uri"], form["client_secret"]) == (
        "fixture-code", _GOOGLE_CALLBACK, "fixture-web-secret")
    assert _challenge(form["code_verifier"]) == page["code_challenge"]  # PKCE: only this flow can redeem the code.
    assert finished["phase"] == "completed" and finished["authorization_url"] is None
    assert finished["snapshot"]["state"] == "connected"
    saved = account_tokens.read("google")
    assert (saved["token"], saved["refresh_token"], saved["client_id"]) == (
        "fixture-access", "fixture-refresh", "fixture-web-client")
    from google.oauth2.credentials import Credentials
    assert Credentials.from_authorized_user_info(saved).valid  # What Gmail and Calendar load.
    assert _no_files(profile) and "fixture-refresh" not in json.dumps(finished)


def test_a_phone_signs_in_to_x_through_the_public_address(tmp_path, monkeypatch, keychain):
    import httpx
    from row_bot import x_accounts
    from tests.helpers.fake_x_api import FakeXApi

    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "profile"))
    from row_bot import api_keys
    monkeypatch.setattr(api_keys, "get_key", lambda key: {"X_CLIENT_ID": "fixture-id", "X_CLIENT_SECRET": "fixture-secret"}.get(key, ""))
    monkeypatch.setattr(owner, "Thread", lambda **_kwargs: pytest.fail("no loopback listener"))
    seen = _provider(monkeypatch, lambda request: httpx.Response(200, json={
        "access_token": "fixture-x-access", "refresh_token": "fixture-x-refresh", "expires_in": 7200}))
    fake = FakeXApi()  # X names who signed in (/2/users/me): the sign-in is that account's.
    fake.users["fixture-x-access"] = {"id": "111", "username": "rowbot_ai", "name": "Row-Bot"}
    monkeypatch.setattr(x_accounts, "transport", fake.transport())
    from row_bot.tools import registry
    registry.set_enabled("x", False)
    phone, browser, _ = _phone()
    with phone, browser:
        _, headers = bootstrap(phone)
        settings = phone.get("/api/v1/settings/snapshot", headers=headers).json()
        snapshot = phone.get("/api/v1/accounts/x/auth", headers=headers).json()
        receipt = _start(phone, headers, snapshot).json()
        page = _query(receipt["authorization_url"])
        back = browser.get("/api/v1/accounts/x/auth/callback", params={"state": page["state"], "code": "fixture-code"})
        finished = phone.get(f"/api/v1/accounts/x/auth/commands/{receipt['command_id']}", headers=headers).json()
    # Settings shows the callback this phone's sign-in returns to, to register with X.
    assert settings["accounts"]["x"]["callback_url"] == _X_CALLBACK
    assert snapshot["sign_in"] == "link"
    assert receipt["authorization_url"].startswith("https://x.com/i/oauth2/authorize?")
    assert (page["redirect_uri"], page["client_id"], page["code_challenge_method"]) == (_X_CALLBACK, "fixture-id", "S256")
    assert back.status_code == 200, back.text
    [exchange] = seen
    form = _query("?" + exchange.content.decode())
    assert str(exchange.url) == "https://api.x.com/2/oauth2/token" and exchange.headers["authorization"].startswith("Basic ")
    assert (form["redirect_uri"], _challenge(form["code_verifier"])) == (_X_CALLBACK, page["code_challenge"])
    assert finished["phase"] == "completed" and finished["message"] == "Connected @rowbot_ai on X."
    saved = account_tokens.read("x:111")
    assert saved["access_token"] == "fixture-x-access" and saved["expires_at"] > 0
    assert account_tokens.read("x") is None
    assert registry.is_enabled("x") is True, "F30: a first sign-in turns on the access its setup reviewed"


def test_the_desktop_app_keeps_the_loopback_sign_in_and_x_callback(tmp_path, monkeypatch, keychain):
    profile = _google_ready(tmp_path, monkeypatch)
    monkeypatch.setenv("X_CLIENT_ID", "fixture-id")
    monkeypatch.setenv("X_CLIENT_SECRET", "fixture-secret")
    local, _, _ = client_app()
    with local:
        _, headers = bootstrap(local)
        google = local.get("/api/v1/accounts/google/auth", headers=headers).json()
        x = local.get("/api/v1/accounts/x/auth", headers=headers).json()
        accounts = local.get("/api/v1/settings/snapshot", headers=headers).json()["accounts"]
    assert (google["sign_in"], x["sign_in"]) == ("this_computer", "this_computer")
    assert accounts["x"]["callback_url"] == "http://127.0.0.1:17638/callback"
    assert accounts["gmail"]["callback_url"] is None  # A Desktop app client takes any loopback port.
    assert _no_files(profile)


@pytest.mark.parametrize(("case", "outcome"), [
    ("wrong_state", "still_waiting"),  # A guessed state neither finishes nor spends the real one.
    ("declined", "failed"),  # Google sends the person back with an error.
    ("signed_out", "failed"),  # The session that started it has gone.
    ("cancelled", "cancelled"),  # Cancelled in Row-Bot before Google sent the browser back.
    ("expired", "failed"),  # The callback came after the sign-in expired.
    ("partial_grant", "failed"),  # Calendar's access was left out.
    ("duplicated", "still_waiting"),  # Two states in one callback.
])
def test_a_callback_saves_nothing_unless_it_finishes_its_own_waiting_sign_in(
        tmp_path, monkeypatch, keychain, case, outcome):
    profile = _web_client_ready(tmp_path, monkeypatch, token={"token": "original"})
    seen = _provider(monkeypatch, lambda request: _google_grant(
        request, scope="https://mail.google.com/" if case == "partial_grant" else _GOOGLE_SCOPES))
    if case == "expired":
        monkeypatch.setattr(owner, "LINK_TTL", 0)
    phone, browser, active = _phone()
    with phone, browser:
        _, headers = bootstrap(phone)
        receipt = _start(phone, headers, phone.get("/api/v1/accounts/google/auth", headers=headers).json()).json()
        state = _query(receipt["authorization_url"])["state"]
        path = "/api/v1/accounts/google/auth/callback"
        if case == "cancelled":
            assert phone.post(f"/api/v1/accounts/google/auth/commands/{receipt['command_id']}/cancel",
                              headers=headers).json()["phase"] == "cancelled"
        if case == "signed_out":
            active["value"] = False
        query = {"wrong_state": f"state={'w' * 43}&code=fixture-code",
                 "declined": f"state={state}&error=access_denied",
                 "duplicated": f"state={state}&state={state}&code=fixture-code"}.get(case, f"state={state}&code=fixture-code")
        back = browser.get(f"{path}?{query}")
        active["value"] = True
        finished = phone.get(f"/api/v1/accounts/google/auth/commands/{receipt['command_id']}", headers=headers).json()
    assert back.status_code == 400 and "fixture-code" not in back.text
    assert account_tokens.read("google") == {"token": "original"} and _no_files(profile)
    assert finished["phase"] == ("running" if outcome == "still_waiting" else outcome)
    assert bool(seen) == (case == "partial_grant")  # Only a callback for a live sign-in reaches Google.
    if outcome == "still_waiting":
        assert finished["authorization_url"] == receipt["authorization_url"]


def test_an_expired_sign_in_frees_the_account_for_the_next_one(tmp_path, monkeypatch, keychain):
    _web_client_ready(tmp_path, monkeypatch)
    phone, _, _ = _phone()
    with phone:
        _, headers = bootstrap(phone)
        snapshot = phone.get("/api/v1/accounts/google/auth", headers=headers).json()
        first = _start(phone, headers, snapshot).json()
        busy = _start(phone, headers, snapshot)
        monkeypatch.setattr(owner, "LINK_TTL", 0)
        expired = phone.get(f"/api/v1/accounts/google/auth/commands/{first['command_id']}", headers=headers).json()
        monkeypatch.setattr(owner, "LINK_TTL", 300)
        second = _start(phone, headers, snapshot)
    assert busy.status_code == 429 and busy.json()["code"] == "account_busy"
    assert (expired["phase"], expired["authorization_url"]) == ("failed", None)
    assert "expired" in expired["message"]
    assert second.status_code == 200 and second.json()["phase"] == "running"


@pytest.mark.parametrize(("client", "origins", "code"), [
    ({"web": {**_WEB["web"], "redirect_uris": ["https://elsewhere.example.test/callback"]}}, (_ORIGIN,),
     "account_redirect_mismatch"),
    ({"web": {key: value for key, value in _WEB["web"].items() if key != "redirect_uris"}}, (_ORIGIN,),
     "account_redirect_mismatch"),
    (_WEB, (), "account_callback_unavailable"),
    (_WEB, (_ORIGIN, "https://second.example.test"), "account_callback_unavailable"),
    (_WEB, ("http://rowbot.example.test",), "account_callback_unavailable"),
    ({**_WEB, **_CLIENT}, (_ORIGIN,), "account_credentials_invalid"),  # Exactly one kind of client.
])
def test_a_web_application_client_must_list_the_public_callback(tmp_path, monkeypatch, keychain, client, origins, code):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "profile"))
    from row_bot.tools import registry
    monkeypatch.setattr(registry, "set_tool_config", lambda *_args: None)
    phone = owner.Device(on_this_computer=False, public_origins=origins)
    snapshot = owner.read_account_auth(account="google", device=phone)
    with pytest.raises(Exception, match=code):
        owner.execute_account_auth(**_command(snapshot, "import_credentials", credentials_json=json.dumps(client)),
                                   device=phone)
    assert account_tokens.read("google_client") is None


def test_a_web_application_client_listing_the_callback_is_saved_and_signs_in_here(tmp_path, monkeypatch, keychain):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "profile"))
    from row_bot.tools import registry
    monkeypatch.setattr(registry, "set_tool_config", lambda *_args: None)
    file = {"web": {**_WEB["web"], "token_uri": "https://accounts.google.com/o/oauth2/token",
                    "javascript_origins": [_ORIGIN]}}
    phone, _, _ = _phone()
    with phone:
        _, headers = bootstrap(phone)
        before = phone.get("/api/v1/accounts/google/auth", headers=headers).json()
        command_id = str(uuid4())
        imported = phone.post("/api/v1/accounts/google/auth/commands", headers={**headers, "idempotency-key": command_id},
                              json={"command_id": command_id, "account": "google", "action": "import_credentials",
                                    "expected_revision": before["revision"], "credentials_json": json.dumps(file)})
        gmail = phone.get("/api/v1/settings/snapshot", headers=headers).json()["accounts"]["gmail"]
    assert imported.status_code == 200, imported.text
    assert imported.json()["snapshot"]["sign_in"] == "link" and "fixture-web-secret" not in imported.text
    assert account_tokens.read("google_client")["web"]["token_uri"] == "https://oauth2.googleapis.com/token"
    assert gmail["callback_url"] == _GOOGLE_CALLBACK  # The guide shows the redirect URI to list.


def test_a_desktop_app_client_cannot_return_to_another_device(tmp_path, monkeypatch, keychain):
    """Google allows a Desktop app client only loopback redirects: the phone is told
    to use a Web application client, and is shown the redirect URI it must list."""
    _google_ready(tmp_path, monkeypatch, token={"token": "original"})
    monkeypatch.setattr(owner, "_google_flow", lambda: pytest.fail("no browser opens on the host"))
    phone, _, _ = _phone()
    with phone:
        _, headers = bootstrap(phone)
        snapshot = phone.get("/api/v1/accounts/google/auth", headers=headers).json()
        start = _start(phone, headers, snapshot)
        gmail = phone.get("/api/v1/settings/snapshot", headers=headers).json()["accounts"]["gmail"]
    assert (snapshot["sign_in"], snapshot["sign_in_blocker"]) == ("unavailable", "desktop_client")
    assert start.status_code == 422 and start.json()["code"] == "account_redirect_mismatch"
    assert gmail["callback_url"] == _GOOGLE_CALLBACK
    assert account_tokens.read("google") == {"token": "original"}


def test_the_public_callback_needs_exactly_one_https_origin():
    assert owner.public_callback("x", (_ORIGIN,)) == _X_CALLBACK
    assert owner.public_callback("google", ("https://RowBot.Example.Test:443",)) == _GOOGLE_CALLBACK
    for origins in ((), ("http://rowbot.example.test",), (_ORIGIN, "https://second.example.test")):
        assert owner.public_callback("google", origins) is None


def test_account_auth_api_local_start_receipt_and_cancel(tmp_path, monkeypatch, keychain):
    profile = _google_ready(tmp_path, monkeypatch)
    queued = []
    monkeypatch.setattr(owner, "Thread", _queued(queued))
    monkeypatch.setattr(owner, "_google_flow", lambda: b'{"token":"fixture"}')
    local, _, _ = client_app()
    with local:
        _, headers = bootstrap(local)
        loaded = local.get("/api/v1/accounts/google/auth", headers=headers)
        assert loaded.status_code == 200, loaded.text
        assert queued == []
        command_id = str(uuid4())
        body = {"command_id": command_id, "account": "google", "action": "start",
                "expected_revision": loaded.json()["revision"]}
        launched = local.post("/api/v1/accounts/google/auth/commands", headers={**headers, "idempotency-key": command_id}, json=body)
        assert launched.status_code == 200, launched.text
        assert launched.json()["phase"] == "running"
        assert len(queued) == 1
        assert local.get(f"/api/v1/accounts/google/auth/commands/{command_id}", headers=headers).json()["phase"] == "running"
        cancelled = local.post(f"/api/v1/accounts/google/auth/commands/{command_id}/cancel", headers=headers)
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["phase"] == "cancel_requested"
        queued[0][0](*queued[0][1])
        assert local.get(f"/api/v1/accounts/google/auth/commands/{command_id}", headers=headers).json()["phase"] == "cancelled"
    assert account_tokens.read("google") is None and _no_files(profile)


def test_x_oauth_uses_saved_credentials_and_writes_only_after_explicit_start(tmp_path, monkeypatch, keychain):
    from row_bot import x_accounts
    from tests.helpers.fake_x_api import FakeXApi

    profile = tmp_path / "profile"
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(profile))
    from row_bot import api_keys
    monkeypatch.setattr(api_keys, "get_key", lambda key: {"X_CLIENT_ID": "fixture-id", "X_CLIENT_SECRET": "fixture-secret"}.get(key, ""))
    monkeypatch.setattr(owner, "_x_flow", lambda: b'{"access_token":"fixture-token"}')
    monkeypatch.setattr(owner, "Thread", ImmediateThread)
    fake = FakeXApi()
    fake.users["fixture-token"] = {"id": "111", "username": "rowbot_ai", "name": "Row-Bot"}
    monkeypatch.setattr(x_accounts, "transport", fake.transport())
    before = owner.read_account_auth(account="x")
    assert before["configured"] is True
    assert x_accounts.listed() == [] and fake.requests == []
    command = _command(before, "start")
    completed = owner.execute_account_auth(**command)
    assert completed["phase"] == "completed"
    assert account_tokens.read("x:111") == {"access_token": "fixture-token"} and _no_files(profile)
    assert "fixture-token" not in json.dumps(completed)


def test_add_another_x_account_signs_in_through_this_computers_browser(tmp_path, monkeypatch, keychain):
    """B424: with one X account connected, Add another account opens X's page in this computer's browser,
    and the browser coming back to Row-Bot's loopback address adds the second account beside the first."""
    import socket
    import threading
    import urllib.request
    import webbrowser
    from urllib.parse import parse_qs, urlencode, urlparse

    from row_bot import x_accounts
    from row_bot.tools import x_tool
    from tests.helpers.fake_x_api import PERSONAL, ROWBOT, FakeXApi

    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "profile"))
    monkeypatch.setenv("X_CLIENT_ID", "fixture-id")
    monkeypatch.setenv("X_CLIENT_SECRET", "fixture-secret")
    fake = FakeXApi()
    monkeypatch.setattr(x_accounts, "transport", fake.transport())
    x_accounts.add(fake.sign_in(*ROWBOT))
    fake.codes["fixture-code"] = fake.sign_in(*PERSONAL)  # The account signed in on x.com in the browser.
    opened, answered, browsers = [], [], []

    def browser(url):
        """X's page: the person allows access, and X sends the browser back to the loopback address."""
        opened.append(url)
        query = {key: values[0] for key, values in parse_qs(urlparse(url).query).items()}
        back = query["redirect_uri"] + "?" + urlencode({"code": "fixture-code", "state": query["state"]})
        direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # Loopback, never a proxy.
        thread = threading.Thread(target=lambda: answered.append(direct.open(back, timeout=10).status))
        thread.start()
        browsers.append(thread)
        return True

    # A free port, not X's registered 17638, which a running Row-Bot's own sign-in may be listening on.
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    monkeypatch.setattr(x_tool, "_OAUTH_CALLBACK_PORT", port)
    monkeypatch.setattr(x_tool, "_OAUTH_REDIRECT_URI", f"http://127.0.0.1:{port}/callback")
    monkeypatch.setattr(webbrowser, "open", browser)
    monkeypatch.setattr(owner, "Thread", ImmediateThread)
    before = owner.read_account_auth(account="x")
    completed = owner.execute_account_auth(**_command(before, "start"))
    for thread in browsers:
        thread.join(10)  # The browser reads the answer after Row-Bot has the code (macOS CI, 29 Oct).
    assert completed["phase"] == "completed", completed["message"]
    assert completed["message"] == "Connected @sidd on X."
    assert len(opened) == 1 and opened[0].startswith("https://x.com/i/oauth2/authorize?")
    assert parse_qs(urlparse(opened[0]).query)["client_id"] == ["fixture-id"] and answered == [200]
    assert [row["username"] for row in x_accounts.summaries()] == ["rowbot_ai", "sidd"]
    assert completed["snapshot"]["token_files"] == 2
    assert "access-sidd" not in json.dumps(completed)


def test_each_x_account_is_listed_and_removing_one_keeps_the_other(tmp_path, monkeypatch, keychain):
    """N7: Settings › Apps › X lists every connected account by handle, name and ID (never a token), and
    removes only the one asked."""
    from row_bot import x_accounts
    from tests.helpers.fake_x_api import PERSONAL, ROWBOT, FakeXApi

    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "profile"))
    monkeypatch.setenv("X_CLIENT_ID", "fixture-id")
    monkeypatch.setenv("X_CLIENT_SECRET", "fixture-secret")
    fake = FakeXApi()
    monkeypatch.setattr(x_accounts, "transport", fake.transport())
    for user in (ROWBOT, PERSONAL):
        x_accounts.add(fake.sign_in(*user))
    local, _, _ = client_app()
    with local:
        _, headers = bootstrap(local)
        listed = local.get("/api/v1/settings/snapshot", headers=headers).json()["accounts"]["x"]
        snapshot = local.get("/api/v1/accounts/x/auth", headers=headers).json()
        command_id = str(uuid4())
        removed = local.post("/api/v1/accounts/x/auth/commands", headers={**headers, "idempotency-key": command_id},
                             json={"command_id": command_id, "account": "x", "action": "disconnect", "confirmed": True,
                                   "x_account": "111", "expected_revision": snapshot["revision"]})
        after = local.get("/api/v1/settings/snapshot", headers=headers).json()["accounts"]["x"]
    assert [(row["username"], row["name"], row["id"], row["state"]) for row in listed["x_accounts"]] == [
        ("rowbot_ai", "Row-Bot", "111", "connected"), ("sidd", "Sidd", "222", "connected")]
    assert snapshot["token_files"] == 2 and listed["authentication_state"] == "connected"
    assert removed.status_code == 200, removed.text
    assert removed.json()["message"].startswith("@rowbot_ai was removed from this computer.")
    assert [row["username"] for row in after["x_accounts"]] == ["sidd"]
    assert account_tokens.read("x:111") is None and account_tokens.read("x:222") is not None
    assert "access-" not in json.dumps([listed, snapshot, removed.json(), after])


# B263: Settings › Accounts reports what Check found, not a guess from the
# saved sign-in. Expiry values are far from today so the real clock never
# matters; no test contacts a provider.
_FUTURE, _PAST = "2099-01-01T00:00:00Z", "2000-01-01T00:00:00Z"


def _google_token(expiry, *, refresh=True, access="fixture-access"):
    token = {"token": access, "client_id": "fixture-client", "client_secret": "fixture-secret",
             "token_uri": "https://oauth2.googleapis.com/token", "expiry": expiry}
    if refresh:
        token["refresh_token"] = "fixture-refresh"
    return token


def _states(client, headers, *accounts):
    response = client.get("/api/v1/settings/snapshot", headers=headers)
    assert response.status_code == 200, response.text
    page = response.json()["accounts"]
    return tuple(page[account]["authentication_state"] for account in accounts)


def _check(client, headers, account):
    loaded = client.get(f"/api/v1/accounts/{account}/auth", headers=headers).json()
    command_id = str(uuid4())
    response = client.post(f"/api/v1/accounts/{account}/auth/commands",
                           headers={**headers, "idempotency-key": command_id},
                           json={"command_id": command_id, "account": account, "action": "check",
                                 "expected_revision": loaded["revision"]})
    assert response.status_code == 200, response.text
    return response.json()


def test_a_healthy_check_is_what_accounts_reports_until_the_sign_in_changes(tmp_path, monkeypatch, keychain):
    # The access token has not run out, so Check passes without a refresh.
    _google_ready(tmp_path, monkeypatch, token=_google_token(_FUTURE))
    local, _, _ = client_app()
    with local:
        _, headers = bootstrap(local)
        assert _states(local, headers, "gmail", "calendar") == ("saved_unchecked", "saved_unchecked")
        receipt = _check(local, headers, "google")
        assert receipt["phase"] == "completed"
        assert receipt["snapshot"]["state"] == "connected"
        assert _states(local, headers, "gmail", "calendar") == ("connected", "connected")
        # Anything that replaces the saved sign-in makes it unchecked again.
        account_tokens.write("google", _google_token(_FUTURE, access="fixture-access-rotated"))
        assert _states(local, headers, "gmail", "calendar") == ("saved_unchecked", "saved_unchecked")
        assert local.get("/api/v1/accounts/google/auth", headers=headers).json()["state"] == "saved_unchecked"
        assert "fixture-access" not in json.dumps(receipt)


def test_a_failed_check_says_so_and_accounts_asks_for_a_reconnect(tmp_path, monkeypatch, keychain):
    from google.auth.exceptions import RefreshError
    from google.oauth2.credentials import Credentials

    def revoked(self, request):
        raise RefreshError("invalid_grant: Token has been expired or revoked.")

    _google_ready(tmp_path, monkeypatch, token=_google_token(_PAST))
    monkeypatch.setattr(Credentials, "refresh", revoked)
    local, _, _ = client_app()
    with local:
        _, headers = bootstrap(local)
        receipt = _check(local, headers, "google")
        assert receipt["phase"] == "failed"
        assert receipt["snapshot"]["state"] == "invalid"
        assert _states(local, headers, "gmail", "calendar") == ("invalid", "invalid")


def test_a_check_that_refreshes_saves_the_new_token_in_the_keychain_only(tmp_path, monkeypatch, keychain):
    from google.oauth2.credentials import Credentials

    def refreshed(self, request):
        self.token, self.expiry = "fixture-access-new", datetime(2099, 1, 1)
    profile = _google_ready(tmp_path, monkeypatch, token=_google_token(_PAST))
    monkeypatch.setattr(Credentials, "refresh", refreshed)
    assert account_tokens.check_google()[0] == "refreshed"
    assert account_tokens.read("google")["token"] == "fixture-access-new" and _no_files(profile)
    assert account_tokens.state("google") == "connected"


def test_opening_accounts_never_refreshes_and_an_expired_access_token_with_a_refresh_token_is_fine(
    tmp_path, monkeypatch, keychain,
):
    from google.oauth2.credentials import Credentials

    _google_ready(tmp_path, monkeypatch, token=_google_token(_PAST))
    monkeypatch.setattr(Credentials, "refresh", lambda *_args: pytest.fail("opening Accounts refreshed a token"))
    saved = account_tokens.read("google")
    local, _, _ = client_app()
    with local:
        _, headers = bootstrap(local)
        # Google's access token lasts an hour; the refresh token keeps it working.
        assert _states(local, headers, "gmail", "calendar") == ("saved_unchecked", "saved_unchecked")
        assert local.get("/api/v1/accounts/google/auth", headers=headers).json()["state"] == "saved_unchecked"
        account_tokens.write("google", _google_token(_PAST, refresh=False))
        assert _states(local, headers, "gmail", "calendar") == ("expired", "expired")
    assert account_tokens.read("google") != saved  # Only what the test saved changed it.


def test_x_reads_its_numeric_expiry(tmp_path, monkeypatch, keychain):
    profile = tmp_path / "profile"
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(profile))
    monkeypatch.setenv("X_CLIENT_ID", "fixture-id")
    monkeypatch.setenv("X_CLIENT_SECRET", "fixture-secret")
    local, _, _ = client_app()
    states = []
    with local:
        _, headers = bootstrap(local)
        for saved in (
            {"access_token": "fixture-token", "expires_at": 946684800.0},
            {"access_token": "fixture-token", "expires_at": 946684800, "refresh_token": "fixture-refresh"},
            {"access_token": "fixture-token", "expires_at": 4070908800.5},
        ):
            account_tokens.write("x", saved)
            states.append(_states(local, headers, "x")[0])
    assert states == ["expired", "saved_unchecked", "saved_unchecked"]


def _access(registry):
    return registry.is_enabled("gmail"), registry.is_enabled("calendar")


def test_a_first_google_sign_in_turns_on_the_access_its_setup_reviewed(tmp_path, monkeypatch, keychain):
    """F29: signing in to Google left Gmail and Calendar off, so Google read "Off". The setup reviewed both
    (sending and changes always ask first), so a first sign-in turns them on and Google reads Connected."""
    from row_bot.integrations import builtin
    from row_bot.tools import registry

    _google_ready(tmp_path, monkeypatch)
    monkeypatch.setattr(owner, "_google_flow", lambda: b'{"token":"first-token"}')
    monkeypatch.setattr(owner, "Thread", ImmediateThread)
    registry.set_enabled("gmail", False)
    registry.set_enabled("calendar", False)
    receipt = owner.execute_account_auth(**_command(owner.read_account_auth(account="google"), "start"))
    assert receipt["phase"] == "completed" and receipt["snapshot"]["state"] == "connected"
    assert _access(registry) == (True, True)
    google = next(row for row in builtin.rows() if row["id"] == "builtin:account:google")
    assert (google["lifecycle"], google["blockers"]) == ("installed", [])


def test_signing_in_again_keeps_the_access_switches_as_they_were(tmp_path, monkeypatch, keychain):
    from row_bot.tools import registry

    _google_ready(tmp_path, monkeypatch, token={"token": "old-token"})
    monkeypatch.setattr(owner, "_google_flow", lambda: b'{"token":"new-token"}')
    monkeypatch.setattr(owner, "Thread", ImmediateThread)
    registry.set_enabled("gmail", True)
    registry.set_enabled("calendar", False)  # The person turned Calendar off.
    receipt = owner.execute_account_auth(**_command(owner.read_account_auth(account="google"), "start"))
    assert receipt["phase"] == "completed"
    assert _access(registry) == (True, False)
