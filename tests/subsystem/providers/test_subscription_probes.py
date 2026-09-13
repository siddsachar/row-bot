"""Reviewed subscription probe boundaries use captured accounts and fake transports."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from row_bot.providers import auth_store, config, claude_subscription, xai_oauth
from tests.subsystem.providers.test_provider_settings_controls import store as store

pytestmark = pytest.mark.subsystem


@pytest.fixture(params=["claude_subscription", "xai_oauth"])
def transport(request, store, monkeypatch):
    from row_bot.providers.transports.claude_subscription_messages import ChatClaudeSubscriptionMessages
    from row_bot.providers.transports.xai_oauth_responses import ChatXAIOAuthResponses
    from tests.test_claude_subscription_transport import _FakeAnthropicClient, _ClientFactory, _SDKError
    from tests.test_xai_oauth_transport import _HttpClient, _SSETextResponse
    provider = request.param
    monkeypatch.setattr(claude_subscription, "claude_subscription_cli_version", lambda: "synthetic-version")
    if provider == "claude_subscription":
        client = _FakeAnthropicClient(creates=[_SDKError(401, "private-synthetic-wire-body")])
        factory = _ClientFactory([client])
        model = ChatClaudeSubscriptionMessages(model_name="claude-sonnet-4-6", client_factory=factory)
        module, save, token = claude_subscription, claude_subscription.save_claude_subscription_oauth_tokens, claude_subscription.ClaudeSubscriptionTokenSet
        def count():
            return len(client.messages.create_calls)
    else:
        client = _HttpClient([_SSETextResponse(status_code=401, payload={"error": {"message": "private-synthetic-wire-body"}})])
        model = ChatXAIOAuthResponses(model_name="grok-4", http_client=client)
        module, save, token = xai_oauth, xai_oauth.save_xai_oauth_tokens, xai_oauth.XAIOAuthTokenSet
        def count():
            return len(client.calls)
    save(token(access_token="old-synthetic-token", refresh_token="old-synthetic-refresh", expires_at="2030-01-01T00:00:00+00:00"))
    return provider, model, module, save, token, count


@pytest.mark.parametrize("replacement", ["disconnect", "new-account"])
def test_actual_401_late_refresh_cannot_replace_disconnected_or_new_account(transport, monkeypatch, replacement):
    from langchain_core.messages import HumanMessage
    provider, model, module, save, token, count = transport
    arrived, release = Event(), Event()
    def refresh(value):
        assert value == "old-synthetic-refresh"
        arrived.set()
        assert release.wait(5)
        return token(access_token="late-synthetic-token", refresh_token=value)
    monkeypatch.setattr(module, "refresh_claude_subscription_token" if provider == "claude_subscription" else "refresh_xai_oauth_token", refresh)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(model.invoke, [HumanMessage(content="synthetic probe")])
        try:
            assert arrived.wait(5)
            if replacement == "disconnect":
                getattr(module, f"disconnect_{provider}_metadata")()
            else:
                save(token(access_token="new-synthetic-account", refresh_token="new-synthetic-refresh"))
            before = config.CONFIG_PATH.read_bytes()
        finally:
            release.set()
        with pytest.raises(config.ProviderConfigError, match="revision_conflict"):
            future.result(5)
    assert config.CONFIG_PATH.read_bytes() == before
    assert auth_store.get_provider_secret(provider, "access_token") == ("" if replacement == "disconnect" else "new-synthetic-account")
    assert count() == 1


def test_strict_probe_401_does_not_refresh_or_replay(transport, monkeypatch, caplog):
    from langchain_core.messages import HumanMessage
    provider, model, module, _, _, count = transport
    captured = auth_store.read_provider_oauth_bundle_snapshot(provider)
    model.bind_probe_credentials(captured, lambda: None)
    monkeypatch.setattr(module, "refresh_claude_subscription_token" if provider == "claude_subscription" else "refresh_xai_oauth_token",
        lambda *_a, **_kw: pytest.fail("Strict probe must not refresh"))
    before = config.CONFIG_PATH.read_bytes()
    with pytest.raises(RuntimeError):
        model.invoke([HumanMessage(content="synthetic probe")])
    assert count() == 1 and config.CONFIG_PATH.read_bytes() == before
    assert "old-synthetic-token" not in str(model.model_dump())
    assert "private-synthetic-wire-body" not in caplog.text


def test_strict_probe_revalidates_before_transport_and_keeps_snapshot_private(transport):
    from langchain_core.messages import HumanMessage
    provider, model, _, save, token, count = transport
    captured = auth_store.read_provider_oauth_bundle_snapshot(provider)
    def validate():
        if controls_revision() != captured[2]:
            raise config.ProviderConfigError("revision_conflict")
    model.bind_probe_credentials(captured, validate)
    save(token(access_token="new-synthetic-account"))
    with pytest.raises(config.ProviderConfigError, match="revision_conflict"):
        model.invoke([HumanMessage(content="synthetic probe")])
    assert count() == 0
    assert "old-synthetic-token" not in str(model)


def controls_revision():
    return config.provider_config_revision(config.load_provider_config(strict=True))


@pytest.fixture(params=["claude-runtime", "xai-runtime", "xai-vision"])
def runner(request, store, monkeypatch):
    kind = request.param
    module = claude_subscription if kind == "claude-runtime" else xai_oauth
    name = "run_claude_subscription_runtime_probe" if kind == "claude-runtime" else "run_xai_oauth_vision_probe" if kind == "xai-vision" else "run_xai_oauth_runtime_probe"
    provider = "claude_subscription" if kind == "claude-runtime" else "xai_oauth"
    model_id = "claude-sonnet-4-6" if kind == "claude-runtime" else "grok-4"
    monkeypatch.setattr(xai_oauth, "list_xai_oauth_model_infos", lambda **_kw: pytest.fail("No incidental catalog refresh"))
    return getattr(module, name), provider, model_id


def test_strict_runner_bounds_diagnostics_and_uses_canonical_cas(runner):
    run, provider, model_id = runner
    class Model:
        def invoke(self, _messages):
            raise RuntimeError("private-synthetic-token and provider body")
    revision = controls_revision()
    proof = {"owner_id": "synthetic-owner", "key": "synthetic-key", "command_id": "synthetic-command"}
    result = run(model_id, chat_model=Model(), strict=True, expected_revision=revision, command_proof=proof)
    assert not result["ok"]
    text = config.CONFIG_PATH.read_text(encoding="utf-8")
    assert "private-synthetic-token" not in str(result) and "private-synthetic-token" not in text
    assert "probe_failed" in str(result)
    assert config.load_provider_config()["providers"][provider]["subscription_probe_command"] == proof


def test_strict_runner_revalidates_after_provider_return_before_publish(runner):
    from types import SimpleNamespace
    run, _, model_id = runner
    revision = controls_revision()
    class Model:
        def invoke(self, _messages):
            config.update_provider_config(lambda cfg: cfg.update({"concurrent_writer": "retained"}))
            return SimpleNamespace(content="image")
    def validate():
        if controls_revision() != revision:
            raise config.ProviderConfigError("revision_conflict")
    with pytest.raises(config.ProviderConfigError, match="revision_conflict"):
        run(model_id, chat_model=Model(), strict=True, expected_revision=revision, validate=validate)
    assert "last_runtime_probe" not in config.CONFIG_PATH.read_text(encoding="utf-8")
    assert "last_vision_probe" not in config.CONFIG_PATH.read_text(encoding="utf-8")
    assert config.load_provider_config()["concurrent_writer"] == "retained"


def test_strict_runner_requires_explicit_model_and_injected_transport(runner):
    run, _, model_id = runner
    before = config.CONFIG_PATH.read_bytes()
    with pytest.raises(ValueError, match="invalid_subscription_probe_model"):
        run(model_id, strict=True)
    with pytest.raises(ValueError, match="invalid_subscription_probe_model"):
        run("", chat_model=object(), strict=True)
    assert config.CONFIG_PATH.read_bytes() == before


def test_strict_claude_headers_use_cache_without_cli_process(monkeypatch):
    monkeypatch.setattr(claude_subscription, "_claude_subscription_cli_version_cache", None)
    monkeypatch.setattr(claude_subscription, "claude_cli_info", lambda: pytest.fail("No CLI execution"))
    headers = claude_subscription.claude_subscription_oauth_headers(allow_cli_probe=False)
    assert claude_subscription.CLAUDE_SUBSCRIPTION_OAUTH_USER_AGENT_VERSION_FALLBACK in headers["user-agent"]


@pytest.fixture
def probes(store, monkeypatch):
    from types import SimpleNamespace
    from row_bot.application.subscription_controls import SubscriptionFlows
    from row_bot.application import provider_default_model
    monkeypatch.setattr(provider_default_model, "_saved_catalog", lambda: SimpleNamespace(cloud_cache={
        f"model:{provider}:{model}": {"provider": provider, "model_id": model,
            "capabilities_snapshot": {"tasks": ["chat"], "input_modalities": ["text", "image"], "output_modalities": ["text"]}}
        for provider, model in (("claude_subscription", "claude-sonnet-4-6"), ("xai_oauth", "grok-4"))
    }, ollama_rows=[]))
    owner = SubscriptionFlows()
    yield owner
    assert owner.dispose()


def probe_command(provider="xai_oauth", kind="runtime"):
    from uuid import uuid4
    from row_bot.application import subscription_probes as controls
    return {"command_id": str(uuid4()), "type": "provider.subscription.probe", "expected_revision": "0", "payload": {
        "provider_id": provider, "provider_revision": controls.read_probes().revision, "kind": kind,
        "model_ref": None if kind == "tokens" else f"model:{provider}:" + ("grok-4" if provider == "xai_oauth" else "claude-sonnet-4-6")}}


def fake_probe_model(provider, _model_id, _captured, _validate, _client, _stack):
    from types import SimpleNamespace
    class Model:
        calls = 0
        def bind_tools(self, *_a, **_kw):
            return self
        def invoke(self, _messages):
            self.calls += 1
            return SimpleNamespace(content="image row-bot-claude-smoke-ok row-bot-xai-smoke-ok", tool_calls=[
                {"name": "calculate", "args": {"expression": "1 + 1"}, "id": "synthetic-call"}])
    return Model()


def run_probe(owner, command, *, factory=fake_probe_model, validate=lambda: None, validate_review=lambda _: None):
    from row_bot.application import subscription_probes as controls
    return controls.execute_probe(flows=owner, owner_id="synthetic-owner", key=command["command_id"], command=command,
        validate=validate, validate_review=validate_review, model_factory=factory)


@pytest.mark.parametrize("provider,kind", [("codex", "tokens"), ("claude_subscription", "tokens"), ("xai_oauth", "tokens"),
    ("claude_subscription", "runtime"), ("xai_oauth", "runtime"), ("xai_oauth", "vision")])
def test_explicit_probe_returns_closed_saved_checks_and_quiescent_owner(probes, provider, kind):
    from row_bot.application import subscription_probes as controls
    command = probe_command(provider, kind)
    result = run_probe(probes, command)
    assert result["result"]["status"] == ("missing" if kind == "tokens" else "passed")
    status = probes.probe_status(owner_id="synthetic-owner", command_id=command["command_id"], validate=lambda: None)
    assert status["state"] == "completed" and status["quiescent"]
    assert not probes.has_pending()
    before = config.CONFIG_PATH.read_bytes()
    receipt = controls.read_probe_receipt(owner_id="synthetic-owner", command_id=command["command_id"], validate=lambda: None)
    assert receipt["published"] and receipt["status"] == "completed"
    assert config.CONFIG_PATH.read_bytes() == before
    assert "access_token" not in str(result) and "synthetic-refresh" not in str(result)


def test_probe_cancel_during_transport_creation_retains_actual_drain_and_excludes_signin(probes):
    from row_bot.application.subscription_controls import SubscriptionError
    arrived, release = Event(), Event()
    def factory(*args):
        arrived.set()
        assert release.wait(5)
        return fake_probe_model(*args)
    command = probe_command()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(run_probe, probes, command, factory=factory)
        try:
            assert arrived.wait(5)
            stopped = probes.cancel_probe(owner_id="synthetic-owner", command_id=command["command_id"], validate=lambda: None)
            assert stopped["state"] == "draining" and not stopped["quiescent"]
            with pytest.raises(SubscriptionError, match="subscription_busy"):
                run_probe(probes, probe_command())
            with pytest.raises(SubscriptionError, match="subscription_busy"):
                probes.execute(owner_id="synthetic-owner", key="another-signin", command={"command_id": "another-signin", "type": "provider.subscription.start",
                    "payload": {"provider_id": "xai_oauth", "provider_revision": controls_revision()}}, validate=lambda: None, validate_review=lambda _: None)
            assert not probes.dispose()
        finally:
            release.set()
        with pytest.raises(SubscriptionError, match="subscription_cancelled"):
            future.result(5)
    assert probes.dispose() and not probes.has_pending()
    assert "last_runtime_probe" not in config.CONFIG_PATH.read_text(encoding="utf-8")


def test_probe_unknown_owner_cannot_cancel_or_read(probes):
    from row_bot.application.subscription_controls import SubscriptionError
    command = probe_command()
    run_probe(probes, command)
    assert probes.probe_status(owner_id="different-owner", command_id=command["command_id"], validate=lambda: None) is None
    with pytest.raises(SubscriptionError, match="subscription_flow_unavailable"):
        probes.cancel_probe(owner_id="different-owner", command_id=command["command_id"], validate=lambda: None)


def test_probe_receipt_publication_failure_recovers_without_provider_replay(probes, monkeypatch):
    from row_bot.application import subscription_probes as controls
    from row_bot.runtime import admissions
    command = probe_command()
    original = admissions.complete_command
    monkeypatch.setattr(admissions, "complete_command", lambda *_a: (_ for _ in ()).throw(OSError("private reply failure")))
    with pytest.raises(config.ProviderConfigError, match="subscription_probe_unconfirmed"):
        run_probe(probes, command)
    before = config.CONFIG_PATH.read_bytes()
    assert controls.read_probe_receipt(owner_id="synthetic-owner", command_id=command["command_id"], validate=lambda: None)["published"]
    monkeypatch.setattr(admissions, "complete_command", original)
    result = run_probe(probes, command, factory=lambda *_a: pytest.fail("No provider replay"))
    assert result["result"]["status"] == "passed" and config.CONFIG_PATH.read_bytes() == before


def test_probe_unknown_execution_failure_never_replays(probes):
    command = probe_command()
    def fail(*_a):
        raise OSError("private uncertain transport construction")
    with pytest.raises(config.ProviderConfigError, match="subscription_probe_unconfirmed"):
        run_probe(probes, command, factory=fail)
    with pytest.raises(config.ProviderConfigError, match="operation_uncertain"):
        run_probe(probes, command, factory=lambda *_a: pytest.fail("No blind replay"))


def test_probe_passive_snapshot_never_reads_tokens_or_constructs_provider(probes, monkeypatch):
    from row_bot.application import subscription_probes as controls
    monkeypatch.setattr(auth_store, "read_provider_oauth_bundle_snapshot", lambda *_a: pytest.fail("No token read"))
    monkeypatch.setattr(controls, "_make_model", lambda *_a: pytest.fail("No provider construction"))
    before = config.CONFIG_PATH.read_bytes()
    assert len(controls.read_probes().items) == 6
    assert config.CONFIG_PATH.read_bytes() == before


@pytest.mark.parametrize("provider,kind", [("claude_subscription", "runtime"), ("xai_oauth", "runtime"), ("xai_oauth", "vision")])
def test_actual_strict_model_and_sdk_use_bounded_fake_http_only(probes, monkeypatch, provider, kind):
    import json
    import httpx
    from row_bot.application import subscription_probes as controls
    from tests.test_xai_oauth_transport import _text_sse, _sse_event
    if provider == "claude_subscription":
        claude_subscription.save_claude_subscription_oauth_tokens(claude_subscription.ClaudeSubscriptionTokenSet(access_token="synthetic-bearer"))
        monkeypatch.setattr(claude_subscription, "claude_cli_info", lambda: pytest.fail("No CLI process"))
        monkeypatch.setattr(claude_subscription, "_known_row_bot_tool", lambda *_a: pytest.fail("No global tool/plugin discovery during a synthetic probe"))
    else:
        xai_oauth.save_xai_oauth_tokens(xai_oauth.XAIOAuthTokenSet(access_token="synthetic-bearer"))
    calls, closed = [], []
    class Response:
        status_code = 200
        def __init__(self, url, kwargs):
            self.request = httpx.Request("POST", url)
            body = json.loads(kwargs["content"]) if "content" in kwargs else kwargs["json"]
            calls.append((url, httpx.Headers(kwargs["headers"]), body))
            self.headers = {"content-type": "application/json"}
            if provider == "claude_subscription":
                content = [{"type": "tool_use", "id": "synthetic-call", "name": "mcp_calculate", "input": {"expression": "1 + 1"}}] if len(calls) == 2 else [{"type": "text", "text": "row-bot-claude-smoke-ok"}]
                payload = {"id": "synthetic-message", "type": "message", "role": "assistant", "model": "claude-sonnet-4-6", "content": content,
                    "stop_reason": "tool_use" if len(calls) == 2 else "end_turn", "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1}}
                self.data = json.dumps(payload).encode()
            elif kind == "vision":
                self.data = json.dumps({"id": "synthetic-response", "output": [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "image"}]}]}).encode()
            else:
                self.headers = {"content-type": "text/event-stream"}
                events = _sse_event("response.output_item.done", {"item": {"type": "function_call", "name": "calculate", "call_id": "synthetic-call", "arguments": '{"expression":"1 + 1"}'}}) if len(calls) == 2 else _text_sse("row-bot-xai-smoke-ok")
                self.data = ("\n".join(events) + "\n\n").encode()
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_a):
            closed.append("response")
        async def aiter_raw(self):
            yield self.data
    class Http:
        def __init__(self, **kwargs):
            assert kwargs["follow_redirects"] is False
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_a):
            closed.append("client")
        def stream(self, method, url, **kwargs):
            assert method == "POST" and kwargs["timeout"] <= 30
            assert kwargs["headers"]["Accept-Encoding"] == "identity"
            return Response(url, kwargs)
    monkeypatch.setattr(httpx, "AsyncClient", Http)
    result = run_probe(probes, probe_command(provider, kind), factory=controls._make_model)
    assert result["result"]["status"] == "passed"
    assert len(calls) == (1 if kind == "vision" else 3)
    assert closed.count("response") == len(calls) == closed.count("client")
    assert all(headers["authorization"] == "Bearer synthetic-bearer" for _, headers, _ in calls)
    assert all(body.get("max_output_tokens", body.get("max_tokens")) <= 128 for _, _, body in calls)


def test_probe_cold_read_creates_no_data_or_credential_import(tmp_path):
    import os
    import subprocess
    import sys
    code = """
from pathlib import Path
import os,sys
from row_bot.application.subscription_probes import read_probes
assert len(read_probes().items) == 6
assert 'row_bot.api_keys' not in sys.modules
assert not Path(os.environ['ROW_BOT_DATA_DIR']).exists()
"""
    child = subprocess.run([sys.executable, "-c", code], env={**os.environ, "ROW_BOT_DATA_DIR": str(tmp_path / "cold-probes")},
        capture_output=True, text=True, timeout=20)
    assert child.returncode == 0, child.stderr


@pytest.mark.parametrize("change", [
    {"provider_id": []}, {"kind": []}, {"kind": "discovery"}, {"provider_revision": "not-a-revision"},
    {"model_ref": "model:claude_subscription:grok-4"}, {"model_ref": "model:xai_oauth:unknown"},
    {"model_ref": "model:xai_oauth:invalid\x00model"}, {"model_ref": "model:xai_oauth:" + "x" * 513},
])
def test_probe_invalid_or_unsaved_review_never_constructs_provider(probes, change):
    from row_bot.application import subscription_probes as controls
    command = probe_command()
    intent = {**command["payload"], **change}
    before = config.CONFIG_PATH.read_bytes()
    with pytest.raises(config.ProviderConfigError):
        controls.review_probe(**intent, validate=lambda: None)
    assert config.CONFIG_PATH.read_bytes() == before


def test_probe_reservation_can_cancel_before_durable_admission_returns(probes, monkeypatch):
    from row_bot.runtime import admissions
    arrived, release = Event(), Event()
    claim = admissions.claim_command
    def blocked_claim(*args):
        arrived.set()
        assert release.wait(5)
        return claim(*args)
    monkeypatch.setattr(admissions, "claim_command", blocked_claim)
    command = probe_command()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(run_probe, probes, command, factory=lambda *_: pytest.fail("Cancelled before provider construction"))
        try:
            assert arrived.wait(5)
            result = probes.cancel_probe(owner_id="synthetic-owner", command_id=command["command_id"], validate=lambda: None)
            assert result["state"] == "draining" and not result["quiescent"]
        finally:
            release.set()
        with pytest.raises(config.ProviderConfigError, match="subscription_probe_review_invalid"):
            future.result(5)
    assert admissions.read_command_metadata("synthetic-owner", command["command_id"])["status"] == "rejected"
    assert "last_runtime_probe" not in config.CONFIG_PATH.read_text(encoding="utf-8")


def test_probe_final_publication_revalidates_auth_and_preserves_config(probes):
    from types import SimpleNamespace
    authority = True
    def validate():
        if not authority:
            raise config.ProviderConfigError("capability_revoked")
    class Model:
        def invoke(self, _messages):
            nonlocal authority
            authority = False
            return SimpleNamespace(content="image")
    before = config.CONFIG_PATH.read_bytes()
    with pytest.raises(config.ProviderConfigError, match="capability_revoked"):
        run_probe(probes, probe_command(kind="vision"), factory=lambda *_: Model(), validate=validate)
    assert config.CONFIG_PATH.read_bytes() == before


def test_probe_actual_http_cancel_retains_cleanup_ownership(probes, monkeypatch):
    import asyncio
    import httpx
    from row_bot.application import subscription_probes as controls
    from row_bot.application.subscription_controls import SubscriptionError
    arrived, closing, release = Event(), Event(), Event()
    closed, calls = [], []
    class Response:
        status_code = 200
        headers = {"content-type": "application/json"}
        request = httpx.Request("POST", "https://api.x.ai/v1/responses")
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_a):
            closing.set()
            assert await asyncio.to_thread(release.wait, 5)
            closed.append("response")
        async def aiter_raw(self):
            arrived.set()
            await asyncio.Future()
            yield b""
    class Client:
        def __init__(self, **_kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_a):
            closed.append("client")
        def stream(self, *_args, **_kwargs):
            calls.append(True)
            return Response()
    monkeypatch.setattr(httpx, "AsyncClient", Client)
    xai_oauth.save_xai_oauth_tokens(xai_oauth.XAIOAuthTokenSet(access_token="synthetic-token"))
    command = probe_command(kind="vision")
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(run_probe, probes, command, factory=controls._make_model)
        try:
            assert arrived.wait(5)
            value = probes.cancel_probe(owner_id="synthetic-owner", command_id=command["command_id"], validate=lambda: None)
            assert closing.wait(5)
            assert value["state"] == "draining" and not value["quiescent"]
            assert probes.has_pending()
            with pytest.raises(SubscriptionError, match="subscription_busy"):
                run_probe(probes, probe_command())
        finally:
            release.set()
        with pytest.raises(SubscriptionError, match="subscription_cancelled"):
            future.result(5)
    status = probes.probe_status(owner_id="synthetic-owner", command_id=command["command_id"], validate=lambda: None)
    assert status["quiescent"] and status["state"] == "cancelled"
    assert calls == [True] and closed == ["response", "client"]
    assert "last_vision_probe" not in config.CONFIG_PATH.read_text(encoding="utf-8")


def test_probe_actual_http_oversize_fails_closed_and_keeps_wire_diagnostics_private(probes, monkeypatch):
    import httpx
    from row_bot.application import subscription_probes as controls
    closed, calls = [], []
    class Response:
        status_code = 200
        headers = {"content-type": "application/json"}
        request = httpx.Request("POST", "https://api.x.ai/v1/responses")
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_a):
            closed.append("response")
        async def aiter_raw(self):
            yield b"private-wire-response" * 14000
    class Client:
        def __init__(self, **_kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_a):
            closed.append("client")
        def stream(self, *_args, **_kwargs):
            calls.append(True)
            return Response()
    monkeypatch.setattr(httpx, "AsyncClient", Client)
    xai_oauth.save_xai_oauth_tokens(xai_oauth.XAIOAuthTokenSet(access_token="synthetic-token"))
    result = run_probe(probes, probe_command(kind="vision"), factory=controls._make_model)
    assert result["result"]["status"] == "failed"
    assert calls == [True] and closed == ["response", "client"]
    assert "private-wire-response" not in str(result) + config.CONFIG_PATH.read_text(encoding="utf-8")


def test_probe_saved_invalid_model_text_is_not_exposed(probes):
    from row_bot.application import subscription_probes as controls
    config.update_provider_config(lambda cfg: cfg["providers"].setdefault("xai_oauth", {}).update({
        "last_runtime_probe": {"model_id": "legacy\ud800model", "ok": True},
    }))
    item = next(row for row in controls.read_probes().items if row.provider_id == "xai_oauth" and row.kind == "runtime")
    assert item.model_ref is None and item.status == "passed"


def test_probe_status_result_cannot_mutate_saved_owner_state(probes):
    command = probe_command()
    run_probe(probes, command)
    result = probes.probe_status(owner_id="synthetic-owner", command_id=command["command_id"], validate=lambda: None)
    result["result"]["status"] = "failed"
    assert probes.probe_status(owner_id="synthetic-owner", command_id=command["command_id"], validate=lambda: None)["result"]["status"] == "passed"
