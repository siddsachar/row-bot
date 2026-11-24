from __future__ import annotations

import sys
import types
from dataclasses import replace

import pytest

from tests.fixtures.company_server import company_server, company_state, keychain  # noqa: F401 - the fixtures
from tests.fixtures.providers import FakeChatModel, fake_model_info


pytestmark = pytest.mark.contract


def test_fake_chat_model_contract_is_deterministic() -> None:
    model = FakeChatModel(model="contract-model", responses=["first", "second"], stream_chunks=["a", "b"])

    assert model.invoke("hello").content == "first"
    assert model.invoke([("user", "hello again")]).content == "second"
    assert [chunk.content for chunk in model.stream("stream please")] == ["a", "b"]
    assert model.bind_tools([{"name": "fake_tool"}]) is model
    assert model.bound_tools == [{"name": "fake_tool"}]
    assert len(model.invocations) == 3


def test_model_info_cache_round_trips_capability_snapshot() -> None:
    from row_bot.providers.catalog import model_info_from_legacy, model_info_to_cache_entry

    generation_parameters = {
        "options": {"quality": ["low", "medium"], "resolution": ["1k", "2k"]},
        "defaults": {"quality": "medium", "resolution": "1k"},
        "valid_combinations": [
            {"quality": "low", "resolution": "1k"},
            {"quality": "medium", "resolution": "2k"},
        ],
    }
    info = replace(
        fake_model_info(provider_id="openai", model_id="gpt-4o"),
        generation_parameters=generation_parameters,
    )
    entry = model_info_to_cache_entry(info)
    restored = model_info_from_legacy("model:openai:gpt-4o", entry)

    assert restored is not None
    assert restored.selection_ref == "model:openai:gpt-4o"
    assert restored.context_window == 8192
    assert restored.tool_calling is True
    assert restored.streaming is True
    assert "image" in restored.input_modalities
    assert entry["capabilities_snapshot"]["generation_parameters"] == generation_parameters
    assert restored.generation_parameters == generation_parameters


def test_custom_fake_provider_runtime_uses_fake_transport(tmp_path, monkeypatch) -> None:
    from row_bot.providers import config as provider_config
    from row_bot.providers import custom, runtime
    from row_bot.providers.custom import custom_provider_id

    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    monkeypatch.setattr(runtime, "provider_secret_status", lambda *_args, **_kwargs: {"configured": False, "source": "", "fingerprint": ""})

    provider_id = custom_provider_id("contract-fake")
    custom.save_custom_endpoint(
        {
            "id": "contract-fake",
            "name": "Contract Fake",
            "base_url": "http://127.0.0.1:65535/v1",
            "auth_required": False,
            "models": [
                {
                    "id": "fake-chat",
                    "model_id": "fake-chat",
                    "display_name": "Fake Chat",
                    "context_window": 4096,
                    "capabilities_snapshot": fake_model_info(provider_id=provider_id).capability_snapshot(),
                }
            ],
        }
    )

    fake_transport = types.ModuleType("row_bot.providers.transports.openai_compatible")
    fake_transport.ChatOpenAICompatible = FakeChatModel
    monkeypatch.setitem(sys.modules, "row_bot.providers.transports.openai_compatible", fake_transport)

    status = runtime.provider_status(provider_id)
    chat_model = runtime.create_chat_model("fake-chat", provider_id=provider_id)

    assert status["configured"] is True
    assert status["source"] == "no_auth"
    assert isinstance(chat_model, FakeChatModel)
    assert chat_model.model == "fake-chat"
    assert chat_model.kwargs["base_url"] == "http://127.0.0.1:65535/v1"


def test_the_company_provider_resolves_is_ready_and_runs_through_its_gateway_while_joined(
        company_server, tmp_path, monkeypatch) -> None:  # noqa: F811 - the fixture
    """Row-Bot for Teams (C2): ``company`` passes what every provider must: a provider-qualified ref resolves
    to it under the company's name, readiness reads it as configured with the catalog's context, and
    create_chat_model returns a model whose request goes to the company's gateway, not to another provider."""
    from langchain_core.messages import HumanMessage

    from row_bot.company import provider
    from row_bot.models import get_cloud_model_context
    from row_bot.providers import config as provider_config, model_catalog_cache
    from row_bot.providers.readiness import evaluate_runtime_readiness
    from row_bot.providers.resolution import resolve_provider_config
    from row_bot.providers.runtime import create_chat_model
    from tests.fixtures.company_server import COMPANY, GATEWAY_OPENAI, REPLY

    monkeypatch.setattr(model_catalog_cache, "CATALOG_CACHE_PATH", tmp_path / "model_catalog_cache.json")
    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    company_server.join_here()
    provider.refresh()

    resolved = resolve_provider_config("model:company:qwen3-32b", allow_legacy_local=False)
    readiness = evaluate_runtime_readiness(resolved, refresh_provider_status=False)
    answer = create_chat_model("qwen3-32b", provider_id="company").invoke([HumanMessage("Hello?")])

    assert (resolved.provider_id, resolved.model_id, resolved.provider_display_name) == (
        "company", "qwen3-32b", COMPANY)
    assert readiness.selected_mode == "agent" and readiness.agent.credential_status == "configured"
    assert get_cloud_model_context("model:company:qwen3-32b") == 131072
    assert answer.content == REPLY
    assert [request.url.path for request in company_server.gateway] == [GATEWAY_OPENAI]


def test_provider_error_contract_normalizes_common_failures() -> None:
    from row_bot.providers.errors import ProviderErrorKind, normalize_provider_error

    assert normalize_provider_error(ValueError("401 unauthorized API key")).kind == ProviderErrorKind.AUTHENTICATION
    assert normalize_provider_error(RuntimeError("quota exceeded")).kind == ProviderErrorKind.QUOTA_EXHAUSTED
    assert normalize_provider_error(RuntimeError("tools are not supported")).kind == ProviderErrorKind.UNSUPPORTED_CAPABILITY


def test_every_builtin_provider_has_refresh_ownership_or_an_explicit_exemption() -> None:
    from row_bot.models import REFRESHABLE_CLOUD_PROVIDER_IDS
    from row_bot.providers.catalog import list_provider_definitions

    provider_ids = {definition.id for definition in list_provider_definitions()}
    explicit_exemptions = {"ollama"}

    assert set(REFRESHABLE_CLOUD_PROVIDER_IDS).isdisjoint(explicit_exemptions)
    assert set(REFRESHABLE_CLOUD_PROVIDER_IDS) | explicit_exemptions == provider_ids


def _counting_clock(monkeypatch, step: float = 0.01) -> None:
    """Each look at the clock moves it on, so a long stream spans many seconds without real waiting."""
    from row_bot.providers import runtime

    now = {"t": 0.0}

    def monotonic() -> float:
        now["t"] += step
        return now["t"]

    monkeypatch.setattr(runtime, "time", types.SimpleNamespace(monotonic=monotonic))


def test_a_token_by_token_local_stream_rechecks_authority_about_once_a_second(monkeypatch) -> None:
    """B288: a streaming Ollama sends one chunk per token; the per-response authority check is costly."""
    from row_bot.providers.runtime import captured_http_clients
    from tests.helpers.fake_ollama import fake_streaming_ollama

    _counting_clock(monkeypatch)
    checks = []
    with fake_streaming_ollama(tokens=[f"w{i} " for i in range(400)]) as server:
        with captured_http_clients(server.url, lambda: checks.append(1)) as (sync, _async):
            response = sync.post(f"{server.url}/api/chat", json={"model": server.model, "stream": True})
            lines = [line for line in response.iter_lines() if line]

    assert len(lines) == 401
    assert 3 <= len(checks) <= 25  # request, response, about one a second of the stream, and the end


def test_revoking_authority_still_stops_a_stream_part_way(monkeypatch) -> None:
    from row_bot.providers.runtime import captured_http_clients
    from tests.helpers.fake_ollama import fake_streaming_ollama

    _counting_clock(monkeypatch)
    checks = []

    def validate() -> None:
        checks.append(1)
        if len(checks) > 4:
            raise PermissionError("document authority revoked")

    with fake_streaming_ollama(tokens=[f"w{i} " for i in range(400)]) as server:
        with captured_http_clients(server.url, validate) as (sync, _async):
            read = []
            with pytest.raises(PermissionError):
                with sync.stream("POST", f"{server.url}/api/chat", json={"model": server.model}) as response:
                    for line in response.iter_lines():
                        read.append(line)
    assert 0 < len(read) < 400


def _usage_shapes() -> dict:
    import json
    from pathlib import Path

    payloads = json.loads(
        (Path(__file__).resolve().parents[1] / "fixtures" / "provider_usage_payloads.json").read_text(encoding="utf-8")
    )
    return {name: value for name, value in payloads.items() if not name.startswith("_")}


@pytest.mark.parametrize("shape", sorted(_usage_shapes()))
def test_every_usage_shape_keeps_one_meaning_for_input_and_output(shape) -> None:
    """Input always includes cache reads and writes; output always includes reasoning.

    The usage ledger, the context meter and compaction calibration all rely on
    this, whatever provider or transport reported the counts.
    """
    from row_bot.prompt_cache import normalize_model_usage

    payload = _usage_shapes()[shape]
    usage = normalize_model_usage(payload["response_metadata"], payload["usage_metadata"])

    if usage.input_tokens is not None:
        assert usage.input_tokens >= (usage.cache_read_tokens or 0) + (usage.cache_write_tokens or 0)
    if usage.output_tokens is not None:
        assert usage.output_tokens >= (usage.reasoning_tokens or 0)
    if usage.cache_write_1h_tokens is not None:
        assert usage.cache_write_tokens is not None and usage.cache_write_1h_tokens <= usage.cache_write_tokens
    assert usage.cost is None or usage.cost >= 0


def test_a_conversation_has_one_opaque_cache_key_per_provider(monkeypatch) -> None:
    """Cache routing keys (WP B2): stable within a conversation, unrelated across
    conversations and providers, never the conversation id, and absent outside one."""
    from row_bot.providers.cache_affinity import conversation_cache_key

    current = {"thread": ""}
    monkeypatch.setitem(
        sys.modules, "row_bot.agent", types.SimpleNamespace(get_current_thread_id=lambda: current["thread"])
    )

    def key(thread: str, provider: str) -> str | None:
        current["thread"] = thread
        return conversation_cache_key(provider)

    conversation = "whatsapp:15550001111"
    first = key(conversation, "openai")

    assert first == key(conversation, "openai")
    assert first != key("another-conversation", "openai")
    assert first != key(conversation, "openrouter")
    assert "15550001111" not in first
    assert len(first) <= 64  # OpenAI's prompt_cache_key limit; OpenRouter's session_id allows 256
    assert key("", "openai") is None


def test_every_request_for_a_local_chat_model_keeps_its_context_size_and_lifetime(monkeypatch) -> None:
    """Ollama reloads a runner, losing its prompt cache, when num_ctx changes (WP B2, F13).

    The agent, background jobs and Chat Only build the model by different routes,
    and some calls pass their own options; every request still carries the same
    num_ctx and the chat keep_alive.
    """
    from langchain_core.messages import HumanMessage

    import row_bot.models as models
    from row_bot.providers import runtime
    from row_bot.providers.transports.ollama_local import OLLAMA_CHAT_KEEP_ALIVE

    monkeypatch.delenv("OLLAMA_KEEP_ALIVE", raising=False)
    monkeypatch.setattr(models, "_ollama_base_url", lambda: "http://127.0.0.1:9")  # nothing listens
    monkeypatch.setattr(models, "_num_ctx", 16_384)
    monkeypatch.setattr(models, "_local_context_mode", "fixed")
    monkeypatch.setattr(models, "get_model_max_context", lambda *args, **kwargs: 8_192)
    monkeypatch.setattr(models, "_observed_local_context", {})
    monkeypatch.setattr(models, "_override_llm_cache", {})

    built = [
        models.get_llm_for("model:ollama:contract-local:7b"),
        runtime.create_chat_model("model:ollama:contract-local:7b"),
    ]
    prompt = [HumanMessage(content="hi")]
    requests = [
        params
        for model in built
        for params in (model._chat_params(prompt), model._chat_params(prompt, options={"num_predict": 8}))
    ]

    assert {params["options"]["num_ctx"] for params in requests} == {8_192}
    assert {params["keep_alive"] for params in requests} == {OLLAMA_CHAT_KEEP_ALIVE}


# ── Claude (your Claude plan): the person's own Claude Code per call (N1) ─────


def _claude_reply(events):
    from langchain_core.messages import AIMessageChunk

    from row_bot.providers.transports.claude_plan_cli import ClaudeTurnReader

    reader = ClaudeTurnReader(names={"read_calendar": "read_calendar"}, model="claude-sonnet-5")
    message = AIMessageChunk(content="")
    for event in events:
        for chunk in reader.feed(event):
            message += chunk.message
        if reader.finished:
            break
    return reader, message + reader.last_chunk().message


def test_a_claude_plan_reply_streams_text_and_thinking_and_ends_with_usage_and_why() -> None:
    from tests.helpers.fake_claude import text_reply

    reader, message = _claude_reply(text_reply("The result of 2+2 is 4.", thinking="Add them.", cache_read=900))

    assert message.content == "The result of 2+2 is 4."
    assert message.additional_kwargs["reasoning_content"] == "Add them."
    assert message.response_metadata["stop_reason"] == "end_turn"
    assert message.response_metadata["model_name"] == "claude-sonnet-5"
    # Input counts cache reads, as every other route reports it.
    assert message.usage_metadata["input_tokens"] == 1056 + 900
    assert message.usage_metadata["output_tokens"] == 15
    assert message.usage_metadata["input_token_details"]["cache_read"] == 900
    assert reader.finished


def test_a_claude_plan_tool_call_is_read_from_the_stream_under_row_bots_own_name() -> None:
    from tests.helpers.fake_claude import tool_reply

    reader, message = _claude_reply(tool_reply("read_calendar", {"day": "Friday", "count": 3}))

    assert message.tool_calls == [{"name": "read_calendar", "args": {"day": "Friday", "count": 3},
                                   "id": "toolu_synthetic01", "type": "tool_call"}]
    assert message.response_metadata["stop_reason"] == "tool_use"
    # The turn ends at the model's own stop; Claude Code's next turn is never read.
    assert message.usage_metadata["input_tokens"] == 1160


def test_claude_plan_tool_names_fit_mcp_and_come_back_unchanged() -> None:
    from row_bot.providers.transports.claude_plan_cli import ClaudeTurnReader, wire_tool_name

    long_name = "mcp_google_workspace_calendar_events_list_for_a_shared_calendar"
    odd_name = "plugin.tool/with spaces"
    for name in ("read_calendar", long_name, odd_name):
        wire = wire_tool_name(name)
        assert len("mcp__rowbot__" + wire) <= 64 and wire.replace("_", "").replace("-", "").isalnum()
        assert wire_tool_name(name) == wire  # the same name on every call, so history replays match
        assert ClaudeTurnReader(names={wire: name}).tool_name("mcp__rowbot__" + wire) == name
    assert wire_tool_name("read_calendar") == "read_calendar"
    assert wire_tool_name(long_name) != wire_tool_name(long_name + "_2")


def test_a_claude_plan_conversation_goes_as_frames_and_only_the_last_asks_the_model() -> None:
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

    from row_bot.providers.transports.claude_plan_cli import conversation_frames

    system, frames = conversation_frames([
        SystemMessage(content="You are Row-Bot."),
        HumanMessage(content=[{"type": "text", "text": "What's on Friday?"},
                              {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]),
        AIMessage(content=[{"type": "thinking", "thinking": "Look it up.", "signature": "bound-to-a-prefix"},
                           {"type": "text", "text": "Checking."}],
                  tool_calls=[{"name": "read_calendar", "args": {"day": "Friday"}, "id": "toolu_1", "type": "tool_call"}]),
        ToolMessage(content="Dentist at 3pm", tool_call_id="toolu_1", name="read_calendar"),
        ToolMessage(content="failed", tool_call_id="toolu_2", name="read_calendar", status="error"),
    ])

    assert system == "You are Row-Bot."
    assert [(frame["type"], frame.get("shouldQuery")) for frame in frames] == [
        ("user", False), ("assistant", None), ("user", None)]
    assert frames[0]["message"]["content"][1] == {
        "type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}}
    # Thinking is never replayed: its signature is bound to a request prefix Claude Code builds.
    assert frames[1]["message"]["content"] == [
        {"type": "text", "text": "Checking."},
        {"type": "tool_use", "id": "toolu_1", "name": "mcp__rowbot__read_calendar", "input": {"day": "Friday"}}]
    # Every result of one batch goes back together, in one user turn.
    assert frames[2]["message"]["content"] == [
        {"type": "tool_result", "tool_use_id": "toolu_1", "content": "Dentist at 3pm"},
        {"type": "tool_result", "tool_use_id": "toolu_2", "content": "failed", "is_error": True}]
    with pytest.raises(ValueError, match="end with a message to Claude"):
        conversation_frames([HumanMessage(content="hi"), AIMessage(content="hello")])
    assert conversation_frames([HumanMessage(content="hi")])[0]  # never Claude Code's own default prompt


@pytest.mark.parametrize(("code", "text", "state"), [
    ("rate_limit", "You've hit your session limit · resets 3pm (Europe/London)", "plan_limit"),
    ("rate_limit", "API Error: 429 rate_limit_error", "rate_limited"),
    ("billing_error", "Fable 5.1 requires usage credits", "billing"),
    ("invalid_request", "400 Claude Code 2.1.200 does not support this model", "too_old"),
    ("server_error", "API Error: 503", "unavailable"),
    ("", "Prompt is too long", "error"),
])
def test_claude_code_errors_say_what_happened_and_what_to_do(code, text, state) -> None:
    from row_bot.providers.transports.claude_plan_cli import claude_code_error

    error = claude_code_error(code, text)

    assert error.state == state
    message = str(error)
    if state == "plan_limit":
        assert message == "Your Claude plan's limit is reached until 3pm (Europe/London). Pick another model for now."
    if state == "too_old":
        assert "Update Claude Code in Settings › Models" in message and "terminal" not in message
    if code == "":
        assert "Prompt is too long" in message  # Row-Bot's context handling reads Claude Code's own words


def test_a_claude_code_sign_in_problem_asks_the_person_to_sign_in_again() -> None:
    from row_bot.providers.runtime import ProviderSignInRequired
    from row_bot.providers.transports.claude_plan_cli import claude_code_error

    error = claude_code_error("authentication_failed", "OAuth token has expired")

    assert isinstance(error, ProviderSignInRequired)
    assert error.provider_id == "claude_subscription" and error.label == "Claude"
    assert str(error).startswith("Claude signed you out. Sign in again to keep using your plan.")
    assert "terminal" not in str(error)


def test_claude_code_runs_without_row_bots_keys_or_anything_that_leaves_the_plan(monkeypatch) -> None:
    """Security: no API key, token, endpoint, header or other cloud reaches Claude Code from Row-Bot."""
    from row_bot.providers.claude_subscription import claude_code_environment

    monkeypatch.setattr("row_bot.api_keys.saved_key_names", lambda: frozenset({"OPENROUTER_API_KEY"}))
    inherited = {
        "PATH": "/usr/bin", "HOME": "/home/person", "HTTPS_PROXY": "http://proxy.example:8080",
        "CLAUDE_CONFIG_DIR": "/home/person/.claude-work", "OPENROUTER_API_KEY": "saved-in-row-bot",
        "ANTHROPIC_API_KEY": "sk-ant-stray", "ANTHROPIC_AUTH_TOKEN": "stray-bearer",
        "ANTHROPIC_BASE_URL": "https://relay.example", "ANTHROPIC_CUSTOM_HEADERS": "Authorization: Bearer x",
        "ANTHROPIC_MODEL": "claude-other", "CLAUDE_CODE_USE_BEDROCK": "1", "CLAUDE_CODE_USE_VERTEX": "1",
        "CLAUDE_CODE_USE_FOUNDRY": "1", "CLAUDE_CODE_OAUTH_TOKEN": "env-token", "CLAUDE_CODE_EXTRA_BODY": "{}",
        "CLAUDE_CODE_EFFORT_LEVEL": "max", "MAX_THINKING_TOKENS": "1", "CLAUDECODE": "1",
        "CLAUDE_CODE_SESSION_ID": "parent-session", "ROW_BOT_LAUNCH_SECRET": "launch",
    }

    env = claude_code_environment(inherited)

    assert {"PATH", "HOME", "HTTPS_PROXY", "CLAUDE_CONFIG_DIR"} <= set(env)
    assert not [name for name in env if name.startswith("ANTHROPIC_")]
    for name in ("OPENROUTER_API_KEY", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
                 "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_EXTRA_BODY", "CLAUDE_CODE_EFFORT_LEVEL", "MAX_THINKING_TOKENS",
                 "CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "ROW_BOT_LAUNCH_SECRET"):
        assert name not in env, name
    # Its own telemetry, error reports and auto-updates are off for these runs (AGENTS.md ground rule).
    assert env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"
    # Row-Bot updates Claude Code itself, once a day (claude_update.py): Claude Code's own updater stays off.
    assert env["DISABLE_AUTOUPDATER"] == "1"



# ── Media prices on approval cards (D4) ──────────────────────────────────────


def test_every_shipped_media_price_is_a_dated_positive_price_for_a_billed_provider() -> None:
    """A price the card shows says when it was read, and is for a provider that bills an API key."""
    from datetime import date

    from row_bot.providers.media import IMAGE_PROVIDER_META, VIDEO_PROVIDER_META
    from row_bot.providers.media_prices import CHOICE_KEYS, UNITS, table_rows

    rows = table_rows()
    assert rows
    seen = set()
    for row in rows:
        meta = IMAGE_PROVIDER_META if row["unit"] == "picture" else VIDEO_PROVIDER_META
        assert row["unit"] in UNITS and meta[row["provider"]].get("key"), row
        assert isinstance(row["usd"], (int, float)) and 0 < row["usd"] < 10, row
        assert date.fromisoformat(row["checked"]) <= date.today(), row
        key = (row["provider"], row["model"], row["unit"], *(row.get(name) for name in CHOICE_KEYS))
        assert key not in seen, row  # One price per tier, never two to choose from.
        seen.add(key)


def _local_runtime_endpoint(tmp_path, monkeypatch, *, installed: bool):
    """Run on this computer's endpoint (E1) saved in a scratch provider config."""
    import row_bot.providers.config as provider_config
    from row_bot import local_runtime
    from row_bot.local_runtime import models
    from row_bot.providers import custom

    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    custom.save_custom_endpoint({
        "id": local_runtime.ENDPOINT_ID, "name": local_runtime.DISPLAY_NAME, "base_url": "http://127.0.0.1:9931/v1",
        "profile": "llama_cpp", "execution_location": "local", "auth_required": False,
        "managed": local_runtime.OWNER, "manual_capabilities": {"context_window": 32768},
    }, manage_secret=False)
    if installed:
        monkeypatch.setattr("row_bot.local_runtime.install.installed_models",
                            lambda root=None: [models.get("qwen3.5-9b")])
        monkeypatch.setattr("row_bot.local_runtime.install.installed_build", lambda root=None: object())
    return custom.get_custom_endpoint(local_runtime.PROVIDER_ID)


def test_the_local_runtime_endpoint_resolves_through_its_owner_and_starts_nothing_while_not_installed(
        tmp_path, monkeypatch) -> None:
    from row_bot.providers import custom

    endpoint = _local_runtime_endpoint(tmp_path, monkeypatch, installed=False)
    monkeypatch.setattr("row_bot.local_runtime.server._servers", {})

    assert endpoint["managed"] == "local"
    assert custom.managed_connection(endpoint, start=False) is None
    # Nothing is installed: a request finds no server, and no probe or start is attempted.
    assert custom.managed_connection(endpoint) is None
    with pytest.raises(ValueError, match="isn't available"):
        custom._custom_endpoint_headers(endpoint)  # noqa: SLF001 - what a probe would send


def test_the_local_runtime_model_counts_as_local_everywhere_row_bot_asks(tmp_path, monkeypatch) -> None:
    from row_bot import local_runtime
    from row_bot.agent import _is_cloud_model_ref
    from row_bot.providers import backup
    from row_bot.providers.background import _is_local
    from row_bot.providers.catalog import provider_billing

    _local_runtime_endpoint(tmp_path, monkeypatch, installed=True)
    ref = local_runtime.model_ref("qwen3.5-9b")

    assert ref == "model:custom_openai_llamacpp:qwen3.5-9b"
    assert provider_billing(local_runtime.PROVIDER_ID) == "local"
    assert backup.is_local(ref)
    assert _is_local(ref)  # a thread's background call never swaps this model mid-turn
    assert not _is_cloud_model_ref(ref)  # prepared compaction (B6) treats it as local


def test_the_local_runtime_models_show_by_name_under_a_provider_that_says_who_runs_them(
        tmp_path, monkeypatch) -> None:
    """llama.cpp lists only ids ("qwen3.5-4b"); pickers show the names Setup used (W12)."""
    from row_bot import local_runtime
    from row_bot.providers import custom

    endpoint = _local_runtime_endpoint(tmp_path, monkeypatch, installed=False)
    listed = {"object": "list", "data": [{"id": "qwen3.5-4b"}, {"id": "qwen3.8-27b-q3"}, {"id": "other"}]}

    infos = custom.model_infos_from_openai_compatible_catalog(endpoint, listed)

    assert [info.display_name for info in infos] == ["Qwen3.5 4B", "Qwen3.8 27B · 3-bit", "other"]
    names = {item.id: item.display_name for item in custom.list_custom_provider_definitions()}
    assert names[local_runtime.PROVIDER_ID] == "Row-Bot (llama.cpp)"
    # Another llama.cpp server keeps the names its own list gives.
    other = {**endpoint, "managed": None}
    assert custom.model_infos_from_openai_compatible_catalog(other, listed)[0].display_name == "qwen3.5-4b"
