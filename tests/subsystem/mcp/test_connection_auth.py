"""Real SDK OAuth against a deterministic HTTP transport; no vendor account."""
from __future__ import annotations

import asyncio
import json
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
import pytest

from row_bot import secret_store
from row_bot.application import client_mcp_auth as owner
from row_bot.application import capability_configuration_controls as configuration
from row_bot.mcp_client import auth, config
from tests.subsystem.plugins.conftest import MemoryKeyring

pytestmark = pytest.mark.platform


@pytest.fixture
def isolated(tmp_path, monkeypatch, reload_for_data_dir):
    reload_for_data_dir(tmp_path, "row_bot.tasks", "row_bot.mcp_client.config")
    backend = MemoryKeyring()
    secret_store._set_backend_for_tests(backend)
    monkeypatch.setattr(owner, "_FLOWS", {})
    config.CONFIG_PATH.write_text(json.dumps({"version": 1, "enabled": False, "servers": {
        "Work": {"transport": "streamable_http", "url": "https://mcp.example.test/mcp", "enabled": False},
        "Personal": {"transport": "streamable_http", "url": "https://mcp.example.test/mcp", "enabled": False}}}))
    yield backend
    secret_store._set_backend_for_tests(None)


def test_real_sdk_pkce_completion_and_refresh_keep_connection_binding(isolated):
    issued = []
    captured = {}
    def respond(request):
        url = str(request.url)
        if url == "https://mcp.example.test/mcp":
            return httpx.Response(200 if request.headers.get("authorization") == "Bearer fixture-access" else 401,
                headers={"WWW-Authenticate": 'Bearer resource_metadata="https://mcp.example.test/.well-known/oauth-protected-resource"'}, json={})
        if "oauth-protected-resource" in url:
            return httpx.Response(200, json={"resource": "https://mcp.example.test/mcp", "authorization_servers": ["https://auth.example.test"]})
        if ".well-known" in url:
            return httpx.Response(200, json={"issuer": "https://auth.example.test", "authorization_endpoint": "https://auth.example.test/authorize",
                "token_endpoint": "https://auth.example.test/token", "registration_endpoint": "https://auth.example.test/register",
                "response_types_supported": ["code"], "code_challenge_methods_supported": ["S256"], "token_endpoint_auth_methods_supported": ["none"]})
        if url.endswith("/register"):
            return httpx.Response(201, json={"client_id": "fixture-client", "redirect_uris": ["http://127.0.0.1:4321/callback"], "token_endpoint_auth_method": "none"})
        if url.endswith("/token"):
            body = parse_qs(request.content.decode())
            issued.append(body)
            return httpx.Response(200, json={"access_token": "fixture-access", "token_type": "Bearer", "refresh_token": "fixture-refresh", "expires_in": 300})
        raise AssertionError(url)
    async def run():
        async def redirect(url):
            captured.update(parse_qs(urlsplit(url).query))
        async def callback():
            return "fixture-code", captured["state"][0]
        cfg = config.read_saved_configuration().document["servers"]["Work"]
        ref = uuid4().hex
        storage = auth.TokenStorage(ref, auth.binding("Work", cfg), staged=True)
        provider = auth.oauth_provider(cfg["url"], "http://127.0.0.1:4321/callback", storage, redirect=redirect, callback=callback)
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond), auth=provider) as client:
            assert (await client.post(cfg["url"])).status_code == 200
        assert captured["code_challenge_method"] == ["S256"]
        assert issued[0]["code_verifier"] and issued[0]["resource"] == [cfg["url"]]
        storage.data["expires_at"] = 1  # Expired without a sleep or real clock wait.
        auth.write_credentials(ref, storage.data)
        restarted = auth.TokenStorage(ref, storage.binding)
        provider = auth.oauth_provider(cfg["url"], "http://127.0.0.1:4321/callback", restarted)
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond), auth=provider) as client:
            assert (await client.post(cfg["url"])).status_code == 200
        assert issued[-1]["grant_type"] == ["refresh_token"]
        with pytest.raises(auth.McpAuthError, match="endpoint_changed"):
            auth.TokenStorage(ref, auth.binding("Personal", cfg))
    asyncio.run(run())


def test_a_registration_that_issues_a_secret_without_its_method_sends_it_as_the_server_lists(isolated):
    """Supabase registers every client with a secret, omits how to send it, and refuses a code exchange
    without it ("Required parameter: client_secret")."""
    issued = []
    captured = {}

    def respond(request):
        url = str(request.url)
        if url == "https://mcp.example.test/mcp":
            return httpx.Response(200 if request.headers.get("authorization") == "Bearer fixture-access" else 401,
                headers={"WWW-Authenticate": 'Bearer resource_metadata="https://mcp.example.test/.well-known/oauth-protected-resource"'}, json={})
        if "oauth-protected-resource" in url:
            return httpx.Response(200, json={"resource": "https://mcp.example.test/mcp", "authorization_servers": ["https://auth.example.test"]})
        if ".well-known" in url:
            return httpx.Response(200, json={"issuer": "https://auth.example.test", "authorization_endpoint": "https://auth.example.test/authorize",
                "token_endpoint": "https://auth.example.test/token", "registration_endpoint": "https://auth.example.test/register",
                "response_types_supported": ["code"], "code_challenge_methods_supported": ["S256"],
                "token_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post"]})
        if url.endswith("/register"):
            return httpx.Response(201, json={"client_id": "fixture-client", "client_secret": "fixture-secret",
                                             "redirect_uris": ["http://127.0.0.1:4321/callback"]})
        if url.endswith("/token"):
            body = parse_qs(request.content.decode())
            issued.append(body)
            if body.get("client_secret") != ["fixture-secret"]:
                return httpx.Response(422, json={"message": "Required parameter: client_secret"})
            return httpx.Response(200, json={"access_token": "fixture-access", "token_type": "Bearer", "expires_in": 300})
        raise AssertionError(url)

    async def run():
        async def redirect(url):
            captured.update(parse_qs(urlsplit(url).query))

        async def callback():
            return "fixture-code", captured["state"][0]
        cfg = config.read_saved_configuration().document["servers"]["Work"]
        storage = auth.TokenStorage(uuid4().hex, auth.binding("Work", cfg), staged=True)
        provider = auth.oauth_provider(cfg["url"], "http://127.0.0.1:4321/callback", storage, redirect=redirect, callback=callback)
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond), auth=provider) as client:
            assert (await client.post(cfg["url"])).status_code == 200
        assert issued[0]["client_secret"] == ["fixture-secret"]
        assert storage.data["client"]["token_endpoint_auth_method"] == "client_secret_post"  # Kept for refreshes.
    asyncio.run(run())


def test_api_key_save_replay_disconnect_and_two_accounts_are_isolated(isolated):
    bindings = [{"kind": "header", "name": "Authorization", "key": "token", "prefix": "Bearer "}]
    def save(name, value):
        page = configuration.read_mcp_configuration()
        server_id = configuration._server_id(name)
        command = dict(owner_id="owner", command_id=str(uuid4()), server_id=server_id,
            configuration_revision=page.revision, action="start", mode="api_key", label=name, bindings=bindings, values={"token": value})
        result = owner.execute_auth(**command)
        assert result["state"] == "signed_in" and value not in str(result)
        assert owner.execute_auth(**command)["state"] == "signed_in"
        return server_id
    work = save("Work", "work-fixture-secret")
    save("Personal", "personal-fixture-secret")
    document = config.read_saved_configuration().document
    assert not document["enabled"] and not document["servers"]["Work"]["enabled"]
    assert "fixture-secret" not in config.CONFIG_PATH.read_text()
    for name in ("Work", "Personal"):
        effective, _ = auth.transport_options(name, document["servers"][name])
        assert effective["headers"]["Authorization"] == "Bearer " + name.lower() + "-fixture-secret"
    result = owner.execute_auth(owner_id="owner", command_id=str(uuid4()), server_id=work,
        configuration_revision=configuration.read_mcp_configuration().revision, action="disconnect", mode="api_key")
    assert result["state"] == "disconnected"
    document = config.read_saved_configuration().document
    assert document["servers"]["Work"]["auth"]["mode"] == "none"
    assert auth.transport_options("Personal", document["servers"]["Personal"])[0]["headers"]["Authorization"] == "Bearer personal-fixture-secret"


def test_chunked_protected_storage_and_failed_write_preserve_original(isolated, monkeypatch):
    ref = uuid4().hex
    original = {"binding": "x", "value": "a" * 6000}
    auth.write_credentials(ref, original)
    assert auth.read_credentials(ref) == original
    assert all(len(value.encode()) <= 900 for value in isolated.values.values())
    setter = isolated.set_password
    def fail(service, account, value):
        if account.endswith(":" + ref):
            raise secret_store.SecretStoreError("fixture failure")
        setter(service, account, value)
    # Exercise the owner boundary instead of allowing a platform fallback.
    write = secret_store.set_secret
    def rejected(name, value, **kwargs):
        if name == ref:
            raise secret_store.SecretStoreError("fixture failure")
        return write(name, value, **kwargs)
    monkeypatch.setattr(secret_store, "set_secret", rejected)
    with pytest.raises(secret_store.SecretStoreError):
        auth.write_credentials(ref, {"value": "replacement"})
    assert auth.read_credentials(ref) == original


@pytest.mark.parametrize("url", ["http://example.test", "https://127.0.0.1", "https://169.254.169.254/metadata", "https://localhost", "https://u:p@example.test", "https://example.test:9000",
                                 # IPv6 forms that carry a private IPv4 address: NAT64, IPv4-compatible, IPv4-mapped.
                                 "https://[64:ff9b::a9fe:a9fe]/", "https://[::a9fe:a9fe]/", "https://[::ffff:10.0.0.1]/"])
def test_oauth_discovery_rejects_unapproved_internal_origins(url):
    with pytest.raises(auth.McpAuthError):
        auth.public_endpoint(url)


@pytest.mark.parametrize("address", ["64:ff9b::a9fe:a9fe", "::a9fe:a9fe", "::ffff:10.0.0.1"])
def test_a_sign_in_never_follows_a_name_to_a_private_address_written_as_ipv6(monkeypatch, address):
    import socket
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", (address, 443, 0, 0))])
    with pytest.raises(auth.McpAuthError, match="mcp_auth_endpoint_invalid"):
        auth.public_endpoint("https://notes.example.test/mcp", resolve=True)
    with pytest.raises(auth.McpAuthError, match="mcp_auth_endpoint_invalid"):
        asyncio.run(auth.PublicTransport().handle_async_request(httpx.Request("GET", "https://notes.example.test/token")))


def test_callback_origin_selection_and_unmatched_state(isolated):
    assert owner.callback_uri(local_origin="http://127.0.0.1:49152", public_origins=()).startswith("http://127.0.0.1:49152/")
    assert owner.callback_uri(local_origin=None, public_origins=("https://rowbot.example.test",)).startswith("https://rowbot.example.test/")
    for values in ((), ("http://rowbot.example.test",), ("https://one.test", "https://two.test")):
        with pytest.raises(auth.McpAuthError, match="callback_unavailable"):
            owner.callback_uri(local_origin=None, public_origins=values)
    with pytest.raises(auth.McpAuthError, match="callback_invalid"):
        owner.accept_callback(state="a" * 40, code="fixture-code")


def test_published_secret_survives_lost_completion_and_explicit_recovery(isolated, monkeypatch):
    from row_bot.runtime import admissions
    command_id = str(uuid4())
    page = configuration.read_mcp_configuration()
    command = dict(owner_id="owner", command_id=command_id, server_id=configuration._server_id("Work"),
        configuration_revision=page.revision, action="start", mode="api_key",
        bindings=[{"kind": "header", "name": "X-Key", "key": "token"}], values={"token": "fixture-secret"})
    with monkeypatch.context() as fault:
        fault.setattr(admissions, "complete_command", lambda *a, **k: (_ for _ in ()).throw(OSError("lost completion")))
        with pytest.raises(OSError):
            owner.execute_auth(**command)
    before = config.CONFIG_PATH.read_bytes()
    assert owner.auth_status(owner_id="owner", command_id=command_id)["state"] == "signed_in"
    assert config.CONFIG_PATH.read_bytes() == before  # Status is observational.
    assert owner.execute_auth(**command)["state"] == "signed_in"
    assert admissions.read_command_metadata("owner", command_id)["status"] == "completed"
    assert config.CONFIG_PATH.read_bytes() == before


@pytest.mark.parametrize("change", ["cancelled", "expired", "configuration", "revoked"])
def test_cancelled_expired_changed_or_revoked_callback_cannot_publish(isolated, monkeypatch, change):
    page = configuration.read_mcp_configuration()
    cfg = config.read_saved_configuration().document["servers"]["Work"]
    flow = owner.Flow("owner", str(uuid4()), configuration._server_id("Work"), "Work", None, page.revision,
        cfg, "http://127.0.0.1:4321/callback", lambda: None)
    flow.state, flow.oauth_state = "waiting", "s" * 40
    owner._FLOWS[(flow.owner_id, flow.command_id)] = flow
    if change == "cancelled":
        flow.state = "cancelled"
    elif change == "expired":
        monkeypatch.setattr(owner.time, "monotonic", lambda: flow.created + 301)
    elif change == "revoked":
        flow.validate = lambda: (_ for _ in ()).throw(auth.McpAuthError("session_expired"))
    else:
        config.CONFIG_PATH.write_text('{"version":1,"servers":{}}')
    with pytest.raises(auth.McpAuthError):
        owner.accept_callback(state=flow.oauth_state, code="fixture-code")
    assert not flow.code and not isolated.values


def test_callback_can_be_used_only_once(isolated):
    cfg = config.read_saved_configuration().document["servers"]["Work"]
    flow = owner.Flow("owner", str(uuid4()), configuration._server_id("Work"), "Work", None,
        configuration.read_mcp_configuration().revision, cfg, "http://127.0.0.1:4321/callback", lambda: None)
    flow.state, flow.oauth_state = "waiting", "s" * 40
    owner._FLOWS[(flow.owner_id, flow.command_id)] = flow
    owner.accept_callback(state=flow.oauth_state, code="first")
    with pytest.raises(auth.McpAuthError, match="callback_invalid"):
        owner.accept_callback(state=flow.oauth_state, code="second")
    assert flow.code == "first" and not isolated.values


@pytest.mark.parametrize(("listed", "reads"), [
    # Atlassian: reads and searches, plus the account's own; writes, deletes and admin left out.
    ("read:me read:jira write:jira search:jira delete:jira manage:jira offline_access email",
     "read:me read:jira search:jira offline_access email"),
    ("Mail.Read Mail.ReadWrite User.Read offline_access", "Mail.Read User.Read offline_access"),  # Microsoft
    ("https://www.googleapis.com/auth/gmail.readonly https://www.googleapis.com/auth/gmail.send openid",
     "https://www.googleapis.com/auth/gmail.readonly openid"),  # Google
    ("read write", "read"),
    ("read:all search:all", ""),  # Nothing to leave out: asked for as it is.
    ("openid email write:all", ""),  # Nothing but the account left: asked for as it is.
    ("mcp", ""),  # A scope Row-Bot can't place counts as a change.
    # A read word at one end never makes a scope that can write a read one.
    ("read_write admin read:me", "read:me"),
    ("view_and_edit:files read-write delete search:files", "search:files"),
])
def test_looking_things_up_asks_for_the_reads_of_what_a_server_lists(listed, reads):
    assert auth.read_scope(listed) == reads
    assert auth.limited(listed, reads) is bool(reads)  # A sign-in granted only those can't make changes.
