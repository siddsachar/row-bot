"""Explicit subscription actions use isolated secrets and deterministic transports."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import json
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import pytest

from row_bot.application import subscription_controls as controls
from row_bot.providers import codex, claude_subscription, xai_oauth, config, auth_store
from row_bot.runtime import admissions
from tests.subsystem.providers.test_provider_settings_controls import store as store

pytestmark = pytest.mark.subsystem


class Client:
    def __init__(self):
        self.closed = Event()
    def close(self):
        self.closed.set()


@pytest.fixture(params=controls.PROVIDERS)
def account(request, store, monkeypatch):
    provider = request.param
    real = {"codex": codex, "claude_subscription": claude_subscription, "xai_oauth": xai_oauth}[provider]
    calls = []
    flow = SimpleNamespace(verification_uri="https://example.invalid/device" if provider == "codex" else None,
        authorization_url="https://example.invalid/authorize?state=synthetic", user_code="SYNTHETIC" if provider == "codex" else None,
        expires_at="2030-01-01T00:00:00+00:00", code_verifier="private-verifier", redirect_uri="http://127.0.0.1:1455/callback",
        token_url="https://example.invalid/token", token_endpoint="https://example.invalid/token", client_id="synthetic-client", state="synthetic-state")
    module = SimpleNamespace(**{name: getattr(real, name) for name in dir(real) if not name.startswith("__")})
    def start(**kwargs):
        calls.append("start")
        return flow
    def exchange(*_a, **_kw):
        calls.append("exchange")
        token_class = codex.CodexTokenSet if provider == "codex" else claude_subscription.ClaudeSubscriptionTokenSet if provider == "claude_subscription" else xai_oauth.XAIOAuthTokenSet
        return token_class(access_token="synthetic-access", refresh_token="synthetic-refresh", account_id="synthetic-account")
    setattr(module, {"codex": "start_codex_device_flow", "claude_subscription": "start_claude_subscription_oauth_flow", "xai_oauth": "start_xai_oauth_flow"}[provider], start)
    setattr(module, {"codex": "exchange_codex_device_authorization", "claude_subscription": "exchange_claude_subscription_authorization", "xai_oauth": "exchange_xai_oauth_authorization"}[provider], exchange)
    module.poll_codex_device_authorization = lambda *_a, **_kw: SimpleNamespace()
    module.authorization_from_xai_oauth_callback = lambda *_a: SimpleNamespace()
    clients = []
    def factory(_guard):
        value = Client()
        clients.append(value)
        return value
    listener_done = Event()
    def listener(_flow, *, ready_callback, **_kw):
        ready_callback()
        listener_done.set()
        return SimpleNamespace()
    owner = controls.SubscriptionFlows(client_factory=factory, listener=listener)
    monkeypatch.setattr(owner, "_module", lambda _: module)
    monkeypatch.setattr("row_bot.application.provider_settings_commands.invalidate_provider_runtime", lambda: calls.append("invalidate"))
    yield provider, owner, module, calls, clients, listener_done
    owner.dispose()
    # The fake callback event precedes the owner's final cleanup. Join the
    # actual owned listener before asserting that shutdown has quiesced.
    for retained in owner._flows.values():
        if retained.thread is not None:
            retained.thread.join(5)
            assert not retained.thread.is_alive()
    assert owner.dispose()


def command(provider, operation="start", flow=None, value=None):
    payload = {"provider_id": provider, "provider_revision": controls.read_accounts().revision}
    if flow:
        payload.update({"flow_id": flow["flow_id"], "server_epoch": flow["server_epoch"]})
    if value is not None:
        payload["value"] = value
    return {"command_id": str(uuid4()), "type": f"provider.subscription.{operation}", "expected_revision": "0", "payload": payload}


def execute(owner, cmd, **kwargs):
    return owner.execute(owner_id=kwargs.get("owner_id", "synthetic-owner"), key=cmd["command_id"], command=cmd,
        validate=kwargs.get("validate", lambda: None), validate_review=kwargs.get("validate_review", lambda _: None))


def _wait_listener(account):
    _, owner, *_, listener_done = account
    assert listener_done.wait(5)
    for retained in owner._flows.values():
        if retained.thread is not None:
            retained.thread.join(5)
            assert not retained.thread.is_alive()


def finish(account, flow):
    provider, owner, *_ = account
    operation = "submit" if provider == "claude_subscription" else "check"
    return execute(owner, command(provider, operation, flow, "synthetic-code" if operation == "submit" else None))


def test_passive_account_views_have_no_secrets_runtime_or_effects(account, store, monkeypatch):
    provider, owner, module, calls, *_ = account
    before = config.CONFIG_PATH.read_bytes()
    monkeypatch.setattr(auth_store, "get_provider_secret", lambda *_a, **_kw: pytest.fail("No secret reads"))
    snapshot = controls.read_accounts()
    assert len(snapshot.accounts) == 3 and all(item.runtime_state == "unknown" for item in snapshot.accounts)
    assert calls == [] and config.CONFIG_PATH.read_bytes() == before
    assert "private" not in json.dumps(asdict(snapshot))


def test_explicit_signin_publishes_once_and_durable_receipt_is_redacted(account):
    provider, owner, _, calls, _, listener_done = account
    cmd = command(provider)
    start = execute(owner, cmd)
    if provider == "xai_oauth":
        _wait_listener(account)
    assert start["flow"]["authorization_url"].startswith("https://") and calls == ["start"]
    result = finish(account, start["flow"])
    assert result["flow"]["state"] == "connected" and calls.count("exchange") == 1
    assert auth_store.get_provider_secret(provider, "access_token") == "synthetic-access"
    assert result["flow"]["quiescent"]
    receipt = controls.read_receipt(provider_id=provider, owner_id="synthetic-owner", command_id=result["command_id"], validate=lambda: None)
    assert receipt["published"] and receipt["status"] == "completed"
    encoded = json.dumps(receipt)
    assert all(value not in encoded for value in ("synthetic-access", "synthetic-refresh", "private-verifier", "synthetic-code"))
    saved_start = admissions.receipt("synthetic-owner", cmd["command_id"])
    assert saved_start["flow"]["authorization_url"] is None and saved_start["flow"]["device_code"] is None


def test_original_start_receipt_retry_does_not_begin_second_flow(account, monkeypatch):
    provider, owner, _, calls, *_ = account
    original = admissions.complete_command
    monkeypatch.setattr(admissions, "complete_command", lambda *_a: (_ for _ in ()).throw(OSError("synthetic failure")))
    cmd = command(provider)
    with pytest.raises(controls.SubscriptionError):
        execute(owner, cmd)
    monkeypatch.setattr(admissions, "complete_command", original)
    assert execute(owner, cmd)["flow"]["state"] == "waiting"
    assert calls == ["start"]


def test_wrong_owner_and_epoch_cannot_read_or_submit_flow(account):
    provider, owner, _, calls, *_ = account
    flow = execute(owner, command(provider))["flow"]
    for owner_id, epoch in (("other-owner", flow["server_epoch"]), ("synthetic-owner", str(uuid4()))):
        with pytest.raises(controls.SubscriptionError, match="subscription_flow_unavailable"):
            owner.read(owner_id=owner_id, flow_id=flow["flow_id"], server_epoch=epoch, validate=lambda: None)
    with pytest.raises(controls.SubscriptionError, match="subscription_flow_unavailable"):
        execute(owner, command(provider, "check", flow), owner_id="other-owner")
    assert calls == ["start"]


def test_cancel_during_exchange_retains_admission_until_real_return(account, monkeypatch):
    provider, owner, module, calls, clients, listener_done = account
    flow = execute(owner, command(provider))["flow"]
    if provider == "xai_oauth":
        _wait_listener(account)
    arrived, release = Event(), Event()
    name = {"codex": "exchange_codex_device_authorization", "claude_subscription": "exchange_claude_subscription_authorization", "xai_oauth": "exchange_xai_oauth_authorization"}[provider]
    original = getattr(module, name)
    def wait(*args, **kwargs):
        arrived.set()
        assert release.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(module, name, wait)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(finish, account, flow)
        try:
            assert arrived.wait(5)
            result = execute(owner, command(provider, "cancel", flow))
            assert result["flow"]["state"] == "draining" and not result["flow"]["quiescent"]
            with pytest.raises(controls.SubscriptionError, match="subscription_busy"):
                execute(owner, command(provider))
            assert clients[-1].closed.is_set()
        finally:
            release.set()
        with pytest.raises(controls.SubscriptionError, match="subscription_cancelled"):
            pending.result(5)
    assert not auth_store.get_provider_secret(provider, "access_token")
    assert owner.read(owner_id="synthetic-owner", flow_id=flow["flow_id"], server_epoch=flow["server_epoch"], validate=lambda: None).quiescent


def test_flow_review_binds_exact_identity(account):
    provider, owner, *_ = account
    flow = execute(owner, command(provider))["flow"]
    revision = controls.read_accounts().revision
    first = controls.review_action(provider, revision, "check", flow_id=flow["flow_id"], server_epoch=flow["server_epoch"], validate=lambda: None)
    other = controls.review_action(provider, revision, "check", flow_id=str(uuid4()), server_epoch=flow["server_epoch"], validate=lambda: None)
    assert first["action_digest"] != other["action_digest"]


def test_uncertain_exchange_is_not_replayed(account, monkeypatch):
    provider, owner, module, calls, _, listener_done = account
    flow = execute(owner, command(provider))["flow"]
    if provider == "xai_oauth":
        _wait_listener(account)
    name = {"codex": "exchange_codex_device_authorization", "claude_subscription": "exchange_claude_subscription_authorization", "xai_oauth": "exchange_xai_oauth_authorization"}[provider]
    def fail(*_a, **_kw):
        calls.append("uncertain-exchange")
        raise OSError("synthetic lost reply")
    monkeypatch.setattr(module, name, fail)
    operation = "submit" if provider == "claude_subscription" else "check"
    cmd = command(provider, operation, flow, "synthetic-code" if operation == "submit" else None)
    with pytest.raises(controls.SubscriptionError, match="subscription_uncertain"):
        execute(owner, cmd)
    with pytest.raises(controls.SubscriptionError, match="subscription_uncertain"):
        execute(owner, cmd)
    assert calls.count("uncertain-exchange") == 1


def test_publication_failure_reuses_held_tokens_without_second_exchange(account, monkeypatch):
    provider, owner, _, calls, _, listener_done = account
    flow = execute(owner, command(provider))["flow"]
    if provider == "xai_oauth":
        _wait_listener(account)
    operation = "submit" if provider == "claude_subscription" else "check"
    cmd = command(provider, operation, flow, "synthetic-code" if operation == "submit" else None)
    original = auth_store.save_provider_config
    monkeypatch.setattr(auth_store, "save_provider_config", lambda *_a, **_kw: (_ for _ in ()).throw(OSError("synthetic failure")))
    with pytest.raises(controls.SubscriptionError, match="subscription_uncertain"):
        execute(owner, cmd)
    monkeypatch.setattr(auth_store, "save_provider_config", original)
    assert execute(owner, cmd)["flow"]["state"] == "connected"
    assert calls.count("exchange") == 1


def test_passive_published_receipt_and_exact_recovery_do_not_reexchange(account, monkeypatch):
    provider, owner, _, calls, _, listener_done = account
    flow = execute(owner, command(provider))["flow"]
    if provider == "xai_oauth":
        _wait_listener(account)
    operation = "submit" if provider == "claude_subscription" else "check"
    cmd = command(provider, operation, flow, "synthetic-code" if operation == "submit" else None)
    original = admissions.complete_command
    monkeypatch.setattr(admissions, "complete_command", lambda *_a: (_ for _ in ()).throw(OSError("synthetic reply failure")))
    with pytest.raises(controls.SubscriptionError, match="subscription_uncertain"):
        execute(owner, cmd)
    before = config.CONFIG_PATH.read_bytes()
    receipt = controls.read_receipt(provider_id=provider, owner_id="synthetic-owner", command_id=cmd["command_id"], validate=lambda: None)
    assert receipt["published"] and receipt["status"] == "completed"
    assert config.CONFIG_PATH.read_bytes() == before
    monkeypatch.setattr(admissions, "complete_command", original)
    assert execute(owner, cmd)["flow"]["state"] == "connected"
    assert calls.count("exchange") == 1 and config.CONFIG_PATH.read_bytes() == before


def test_explicit_disconnect_and_restore_preserve_original_bytes(account):
    provider, owner, _, calls, _, listener_done = account
    flow = execute(owner, command(provider))["flow"]
    if provider == "xai_oauth":
        _wait_listener(account)
    finish(account, flow)
    execute(owner, command(provider, "disconnect"))
    assert not auth_store.get_provider_secret(provider, "access_token")
    restored = execute(owner, command(provider, "restore"))
    assert restored["status"] == "completed" and auth_store.get_provider_secret(provider, "access_token") == "synthetic-access"
    assert calls.count("exchange") == 1


def test_explicit_claude_import_has_no_signin_network(account):
    provider, owner, _, calls, *_ = account
    if provider != "claude_subscription":
        with pytest.raises(controls.SubscriptionError, match="subscription_review_invalid"):
            execute(owner, command(provider, "import_token", value="synthetic-token"))
    else:
        execute(owner, command(provider, "import_token", value="synthetic-token"))
        assert auth_store.get_provider_secret(provider, "access_token") == "synthetic-token"
    assert "start" not in calls and "exchange" not in calls


def test_cold_subscription_snapshot_creates_nothing(tmp_path):
    import os
    import subprocess
    import sys
    cold = tmp_path / "cold-saved-subscriptions"
    code = """
from pathlib import Path
import os,sys
from row_bot.application.subscription_controls import read_accounts
assert len(read_accounts().accounts) == 3
assert 'row_bot.api_keys' not in sys.modules
assert not Path(os.environ['ROW_BOT_DATA_DIR']).exists()
"""
    result = subprocess.run([sys.executable, "-c", code], env={**os.environ, "ROW_BOT_DATA_DIR": str(cold)}, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("chunks,encoding", [([b"x" * (256 * 1024 + 1)], "identity"), ([b"{}"], "gzip")], ids=["oversized", "compressed"])
def test_bounded_auth_client_rejects_large_or_encoded_responses(monkeypatch, chunks, encoding):
    import httpx
    class Response:
        headers = {"content-encoding": encoding}
        status_code = 200
        request = httpx.Request("GET", "https://example.invalid")
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_a):
            return False
        async def aiter_raw(self):
            for chunk in chunks:
                yield chunk
    class Http:
        def __init__(self, **_kw):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_a):
            return False
        def stream(self, *_a, **kwargs):
            assert kwargs["headers"]["Accept-Encoding"] == "identity"
            return Response()
    monkeypatch.setattr(httpx, "AsyncClient", Http)
    with pytest.raises(controls.SubscriptionError, match="subscription_response_invalid"):
        controls._BoundedClient(lambda: None).get("https://example.invalid")


def test_original_start_cancel_during_admission_wait_retains_real_drain(account, monkeypatch):
    provider, owner, _, calls, *_ = account
    arrived, release = Event(), Event()
    original = admissions.claim_command
    def waiting(*args, **kwargs):
        result = original(*args, **kwargs)
        arrived.set()
        assert release.wait(5)
        return result
    monkeypatch.setattr(admissions, "claim_command", waiting)
    cmd = command(provider)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(execute, owner, cmd)
        try:
            assert arrived.wait(5)
            cancelled = owner.cancel_start(owner_id="synthetic-owner", command_id=cmd["command_id"], validate=lambda: None)
            assert cancelled.state == "draining" and not cancelled.quiescent
            assert owner.has_pending()
            with pytest.raises(controls.SubscriptionError, match="subscription_busy"):
                execute(owner, command(provider))
        finally:
            release.set()
        with pytest.raises(controls.SubscriptionError, match="subscription_cancelled"):
            future.result(5)
    assert calls == [] and not owner.has_pending()
    assert controls.read_receipt(provider_id=provider, owner_id="synthetic-owner", command_id=cmd["command_id"], validate=lambda: None)["status"] == "rejected"


def test_original_start_cancel_during_provider_wait_and_wrong_owner_denial(account, monkeypatch):
    provider, owner, module, calls, *_ = account
    arrived, release = Event(), Event()
    name = {"codex": "start_codex_device_flow", "claude_subscription": "start_claude_subscription_oauth_flow", "xai_oauth": "start_xai_oauth_flow"}[provider]
    original = getattr(module, name)
    def waiting(**kwargs):
        arrived.set()
        assert release.wait(5)
        return original(**kwargs)
    monkeypatch.setattr(module, name, waiting)
    cmd = command(provider)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(execute, owner, cmd)
        try:
            assert arrived.wait(5)
            with pytest.raises(controls.SubscriptionError, match="subscription_flow_unavailable"):
                owner.cancel_start(owner_id="different-owner", command_id=cmd["command_id"], validate=lambda: None)
            assert owner.cancel_start(owner_id="synthetic-owner", command_id=cmd["command_id"], validate=lambda: None).state == "draining"
            assert not owner.dispose()
        finally:
            release.set()
        with pytest.raises(controls.SubscriptionError, match="subscription_cancelled"):
            future.result(5)
    assert owner.dispose() and not owner.has_pending()
    assert "exchange" not in calls


def test_cancel_after_publication_reports_connected_not_cancelled(account, monkeypatch):
    provider, owner, _, _, _, listener_done = account
    flow = execute(owner, command(provider))["flow"]
    if provider == "xai_oauth":
        _wait_listener(account)
    arrived, release = Event(), Event()
    def after_publication():
        arrived.set()
        assert release.wait(5)
    monkeypatch.setattr("row_bot.application.provider_settings_commands.invalidate_provider_runtime", after_publication)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(finish, account, flow)
        try:
            assert arrived.wait(5)
            result = execute(owner, command(provider, "cancel", flow))
            assert result["flow"]["state"] == "connected" and not result["flow"]["quiescent"]
        finally:
            release.set()
        assert future.result(5)["flow"]["state"] == "connected"
    assert auth_store.get_provider_secret(provider, "access_token") == "synthetic-access"


def test_bounded_auth_client_close_cancels_stalled_body_and_closes_transport(monkeypatch):
    import asyncio
    import httpx
    reading, response_closed, client_closed = Event(), Event(), Event()
    class Response:
        headers = {}
        status_code = 200
        request = httpx.Request("GET", "https://example.invalid")
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_a):
            response_closed.set()
        async def aiter_raw(self):
            reading.set()
            await asyncio.Event().wait()
            yield b"unreachable"
    class Http:
        def __init__(self, **_kw):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_a):
            client_closed.set()
        def stream(self, *_a, **_kw):
            return Response()
    monkeypatch.setattr(httpx, "AsyncClient", Http)
    client = controls._BoundedClient(lambda: None)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(client.get, "https://example.invalid")
        try:
            assert reading.wait(5)
        finally:
            client.close()
        with pytest.raises(controls.SubscriptionError, match="subscription_cancelled"):
            future.result(5)
    assert response_closed.is_set() and client_closed.is_set()
    assert client.loop is None and client.task is None


def test_xai_strict_start_does_not_publish_discovery_and_legacy_default_does(store, monkeypatch):
    from tests.test_xai_oauth_provider import _HttpClient, _Response, _discovery_payload
    before = config.CONFIG_PATH.read_bytes()
    client = _HttpClient([_Response(payload=_discovery_payload())])
    flow = xai_oauth.start_xai_oauth_flow(http_client=client, persist_discovery=False)
    assert flow.authorization_url.startswith("https://auth.x.ai/")
    assert config.CONFIG_PATH.read_bytes() == before
    xai_oauth.start_xai_oauth_flow(http_client=_HttpClient([_Response(payload=_discovery_payload())]))
    assert config.CONFIG_PATH.read_bytes() != before


def test_xai_strict_listener_releases_accepted_partial_header_after_cancel(monkeypatch):
    import socket
    from tests.test_xai_oauth_provider import _free_loopback_port
    port = _free_loopback_port()
    ready, reading, cancel = Event(), Event(), Event()
    original = socket.socket.recv_into
    def receive(connection, *args, **kwargs):
        if connection.getsockname()[1] == port:
            reading.set()
        return original(connection, *args, **kwargs)
    monkeypatch.setattr(socket.socket, "recv_into", receive)
    flow = SimpleNamespace(redirect_uri=f"http://127.0.0.1:{port}/callback")
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(xai_oauth.wait_for_xai_oauth_loopback_authorization, flow,
            open_browser=False, ready_callback=ready.set, cancel_event=cancel,
            timeout_seconds=10, receive_timeout=0.05)
        try:
            assert ready.wait(5)
            with socket.create_connection(("127.0.0.1", port), timeout=5) as peer:
                peer.sendall(b"GET /callback HTTP/1.1\r\nX-Partial:")
                assert reading.wait(5)
                cancel.set()
                with pytest.raises(xai_oauth.XAIOAuthError) as error:
                    future.result(5)
                assert error.value.kind == "loopback_cancelled"
                assert peer.recv(1) == b""
        finally:
            cancel.set()
    with socket.socket() as rebound:
        rebound.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        rebound.bind(("127.0.0.1", port))
