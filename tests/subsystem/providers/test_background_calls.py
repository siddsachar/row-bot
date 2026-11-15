"""Background calls: same model, lowest effort, output caps, structured answers (WP C1, D1).

Fake chat models stand in for providers, and a fake Anthropic Messages API
answers the real SDK in-process, so nothing here reaches the network.
"""
from __future__ import annotations

import contextvars
import json
import sqlite3
from contextlib import closing
from typing import Any

import httpx
import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from row_bot import models
from row_bot.providers import background, reasoning, routing
from row_bot.providers import runtime
from row_bot.providers.reasoning import ReasoningCapabilities, ReasoningRequestPlan
from row_bot.providers.transports import anthropic_cancellable, thinking_binding
from row_bot.providers.transports.reasoning_fallback import ReasoningFallbackChatModel
from row_bot.providers.usage_ledger import attach_usage_ledger

pytestmark = pytest.mark.subsystem

ANSWER = background.structured_answer("yes_no", {"answer": {"type": "string", "enum": ["yes", "no"]}})


@pytest.fixture(autouse=True)
def _fresh_process_memory(monkeypatch):
    """Refusal memories and the model cache are per process: give each test its own."""
    from row_bot.providers.config import normalize_provider_config

    monkeypatch.setattr(reasoning, "_background_rejections", set())
    monkeypatch.setattr(background, "_schema_refusals", set())
    monkeypatch.setattr(models, "_override_llm_cache", {})
    monkeypatch.setattr(thinking_binding, "_REJECTED_ROUTES", set())
    # The routes Row-Bot ships, never the machine's own provider settings.
    monkeypatch.setattr(routing, "load_provider_config", lambda: normalize_provider_config({}))
    # Outside a turn: an earlier test's streamed turn can leave its model set in this thread.
    token = models._active_model_override.set(None)
    yield
    models._active_model_override.reset(token)


class _HttpError(RuntimeError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"HTTP {status}: {message}")
        self.status_code = status


class _Chat(BaseChatModel):
    """A chat model that records each call's keyword arguments and answers in order."""

    replies: list[Any] = []
    calls: list[dict[str, Any]] = []
    refuse_schema: bool = False
    num_ctx: int | None = None
    reasoning: Any = None

    @property
    def _llm_type(self) -> str:
        return "background-fixture"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls.append(dict(kwargs))
        if self.refuse_schema and {"response_format", "output_config", "format", "response_json_schema"} & set(kwargs):
            raise _HttpError(400, "this model does not take a json schema for the reply")
        reply = self.replies.pop(0) if self.replies else "no"
        if isinstance(reply, Exception):
            raise reply
        message = AIMessage(content=reply, usage_metadata={"input_tokens": 40, "output_tokens": 3, "total_tokens": 43})
        return ChatResult(generations=[ChatGeneration(message=message)])


class _Factory:
    """Stands in for ``models.get_llm_for`` and records which model and plan each call asked for."""

    def __init__(self, chat: _Chat) -> None:
        self.chat = chat
        self.requests: list[tuple[str, ReasoningRequestPlan | None]] = []

    def __call__(self, ref, num_ctx=None, *, reasoning_plan=None):
        self.requests.append((ref, reasoning_plan))
        return self.chat


@pytest.fixture
def chat(monkeypatch):
    model = _Chat()
    factory = _Factory(model)
    monkeypatch.setattr(models, "get_llm_for", factory)
    monkeypatch.setattr(background, "resolve_reasoning_capabilities_for_ref", lambda _ref: None)
    return factory


def _in_turn(model_override: str, fn):
    """Run ``fn`` the way a tool runs inside a turn whose model is ``model_override``."""
    def run():
        models.set_active_model_override(model_override)
        return fn()
    return contextvars.copy_context().run(run)


# ── Which model ──────────────────────────────────────────────────────────────


def test_thread_tied_calls_use_the_turns_model_and_unattended_jobs_the_default(monkeypatch) -> None:
    monkeypatch.setattr(models, "get_current_model", lambda: "model:openai:gpt-5")
    thread = "model:ollama:qwen3:8b"

    chosen = _in_turn(thread, lambda: {
        purpose: background.background_model_ref(purpose)
        for purpose in ("compaction", "contradiction", "condition", "verifier", "extraction", "dream")
    })

    assert chosen == {
        "compaction": thread, "contradiction": thread, "condition": thread, "verifier": thread,
        "extraction": "model:openai:gpt-5", "dream": "model:openai:gpt-5",
    }
    # Outside a turn, a thread-tied call falls back to the default chat model.
    assert background.background_model_ref("contradiction") == "model:openai:gpt-5"


def test_a_tasks_model_wins_over_the_threads_model(monkeypatch) -> None:
    monkeypatch.setattr(models, "get_current_model", lambda: "model:openai:gpt-5")

    assert background.background_model_ref(
        "condition", task_model="model:anthropic:claude-opus-5-5", thread_model="model:google:gemini-3-flash-preview",
    ) == "model:anthropic:claude-opus-5-5"
    assert background.background_model_ref(
        "condition", thread_model="model:google:gemini-3-flash-preview",
    ) == "model:google:gemini-3-flash-preview"


def test_task_routes_are_read_but_never_swap_a_local_threads_model(monkeypatch) -> None:
    routes = {"dream": "model:openai:gpt-5-mini", "condition": "model:openai:gpt-5-mini"}
    monkeypatch.setattr(routing, "task_route_model", lambda purpose, route_id="balanced": routes.get(purpose, ""))
    monkeypatch.setattr(models, "get_current_model", lambda: "model:openai:gpt-5")

    assert background.background_model_ref("dream") == "model:openai:gpt-5-mini"
    assert background.background_model_ref("condition", thread_model="model:openai:gpt-5") == "model:openai:gpt-5-mini"
    # A route for a thread-tied call would load another local model mid-turn: the thread's model stays.
    assert background.background_model_ref("condition", thread_model="model:ollama:qwen3:8b") == "model:ollama:qwen3:8b"
    assert background.background_model_ref("verifier", thread_model="model:openai:gpt-5") == "model:openai:gpt-5"


def test_shipped_routes_name_no_background_model(monkeypatch) -> None:
    from row_bot.providers.config import normalize_provider_config

    assert all(routing.task_route_model(purpose) == "" for purpose in ("dream", "extraction", "verifier"))

    configured = normalize_provider_config({"task_routes": {"dream": " model:openai:gpt-5-mini "}})
    monkeypatch.setattr(routing, "load_provider_config", lambda: configured)
    assert routing.task_route_model("dream") == "model:openai:gpt-5-mini"
    assert routing.task_route_model("verifier") == ""


# ── Lowest effort per provider ───────────────────────────────────────────────


_OPENAI = reasoning._EXACT_REASONING_CAPABILITIES[("openai", "gpt-5")]
_OPUS_48 = reasoning._EXACT_REASONING_CAPABILITIES[("anthropic", "claude-opus-4-8")]


@pytest.mark.parametrize(("ref", "caps", "kwargs", "reasoning_runs"), [
    # Claude 5 always thinks adaptively: effort low, thinking left as it is.
    ("model:anthropic:claude-opus-5-5", None, {"effort": "low"}, True),
    ("model:anthropic:claude-fable-5-1", None, {"effort": "low"}, True),
    # Opus 4.x without thinking stays without: effort only, no adaptive thinking switched on.
    ("model:anthropic:claude-opus-4-8", _OPUS_48, {"effort": "low"}, False),
    # Older Claude without effort controls: left at its default.
    ("model:anthropic:claude-haiku-4-5-20251001", None, {}, False),
    ("model:openai:gpt-5", _OPENAI, {"reasoning_effort": "minimal"}, True),
    ("model:openai:gpt-5.5",
     ReasoningCapabilities(supported_efforts=("none", "low", "high"), request_style="openai"),
     {"reasoning_effort": "none"}, False),
    ("model:google:gemini-2.5-flash", reasoning._EXACT_REASONING_CAPABILITIES[("google", "gemini-2.5-flash")],
     {"thinking_budget": 0}, False),
    ("model:google:gemini-2.5-pro", reasoning._EXACT_REASONING_CAPABILITIES[("google", "gemini-2.5-pro")],
     {"thinking_budget": 128}, True),
    ("model:google:gemini-3-flash-preview",
     reasoning._EXACT_REASONING_CAPABILITIES[("google", "gemini-3-flash-preview")], {"thinking_level": "low"}, True),
    ("model:xai:grok-4.5", reasoning._EXACT_REASONING_CAPABILITIES[("xai", "grok-4.5")],
     {"reasoning_effort": "low"}, True),
    ("model:ollama:qwen3:8b", reasoning.ollama_thinking_capabilities("qwen3:8b"), {"reasoning": False}, False),
    ("model:ollama:gpt-oss:20b", reasoning.ollama_thinking_capabilities("gpt-oss:20b"), {"reasoning": "low"}, True),
    ("model:openrouter:deepseek/deepseek-r1",
     ReasoningCapabilities(supported_efforts=("low", "high"), can_disable=True, request_style="openrouter"),
     {"reasoning": {"enabled": False}}, False),
    # Custom endpoints are left exactly as configured; Row-Bot cannot tell whether they reason.
    ("model:custom_openai_lab:qwen", ReasoningCapabilities(supported_efforts=("low",), request_style="openai"), {}, None),
])
def test_background_reasoning_is_the_lowest_the_provider_supports(ref, caps, kwargs, reasoning_runs) -> None:
    provider = ref.split(":", 2)[1]

    lowered = background.background_reasoning(ref, caps)

    assert runtime._reasoning_constructor_kwargs(lowered.plan, provider=provider) == kwargs
    assert lowered.reasoning_runs is reasoning_runs
    # The provider path accepts the plan exactly as it would a person's choice.
    runtime._validated_runtime_reasoning_plan(lowered.plan, ref)
    if kwargs:
        assert lowered.plan.is_background


def test_a_background_plan_never_shares_a_cached_model_with_the_same_choice_by_a_person() -> None:
    lowered = background.background_reasoning("model:anthropic:claude-opus-4-8", _OPUS_48).plan
    persons = ReasoningRequestPlan("model:anthropic:claude-opus-4-8", lowered.selection, _OPUS_48)

    # The person's "low" turns adaptive thinking on; the background one does not.
    assert runtime._reasoning_constructor_kwargs(persons, provider="anthropic") == {
        "effort": "low", "thinking": {"type": "adaptive"}}
    assert lowered.fingerprint != persons.fingerprint


def test_a_refused_lowering_is_remembered_for_later_background_calls() -> None:
    ref = "model:openai:gpt-5"
    assert background.background_reasoning(ref, _OPENAI).plan.is_background

    reasoning.remember_background_rejection(ref)

    assert background.background_reasoning(ref, _OPENAI).plan.is_default
    # Document jobs keep their captured plan fixed for the whole job.
    assert background.background_reasoning(ref, _OPENAI, remembered_refusals=False).plan.is_background


# ── Output caps ──────────────────────────────────────────────────────────────


def test_caps_leave_room_for_reasoning_that_still_runs(chat, monkeypatch) -> None:
    caps = {"model:openai:gpt-5": _OPENAI,
            "model:openai:gpt-5.5": ReasoningCapabilities(supported_efforts=("none", "low"), request_style="openai")}
    monkeypatch.setattr(background, "resolve_reasoning_capabilities_for_ref", lambda ref: caps.get(ref))

    capped = background.resolve_background_model("condition", thread_model="model:openai:gpt-5.5")
    assert capped.max_tokens == background.ANSWER_TOKENS["condition"]

    roomy = background.resolve_background_model("condition", thread_model="model:openai:gpt-5")
    assert roomy.max_tokens == background.ANSWER_TOKENS["condition"] + background.REASONING_HEADROOM_TOKENS

    roomy.model.invoke([HumanMessage(content="Is it done?")])
    assert chat.chat.calls[-1]["max_tokens"] == roomy.max_tokens
    assert [ref for ref, _plan in chat.requests] == ["model:openai:gpt-5.5", "model:openai:gpt-5"]


def test_compaction_keeps_its_own_allowance_unless_reasoning_is_known_to_run(chat, monkeypatch) -> None:
    caps = {"model:openai:gpt-5": _OPENAI}
    monkeypatch.setattr(background, "resolve_reasoning_capabilities_for_ref", lambda ref: caps.get(ref))

    unknown = background.resolve_background_model("compaction", thread_model="model:openai:gpt-4o", max_tokens=2000)
    thinking = background.resolve_background_model("compaction", thread_model="model:openai:gpt-5", max_tokens=2000)
    # A short answer from a model Row-Bot knows nothing about still gets room.
    yes_no = background.resolve_background_model("condition", thread_model="model:openai:gpt-4o")

    assert unknown.max_tokens == 2000
    assert thinking.max_tokens == 2000 + background.REASONING_HEADROOM_TOKENS
    assert yes_no.max_tokens == background.ANSWER_TOKENS["condition"] + background.REASONING_HEADROOM_TOKENS


def test_custom_endpoints_get_no_cap_and_no_lowered_effort(chat, monkeypatch) -> None:
    monkeypatch.setattr(background, "resolve_reasoning_capabilities_for_ref",
                        lambda _ref: ReasoningCapabilities(supported_efforts=("low", "high"), request_style="openai"))

    target = background.resolve_background_model("verifier", thread_model="model:custom_openai_lab:qwen3")
    target.model.invoke([HumanMessage(content="Judge.")])

    assert target.max_tokens is None and target.uncapped is None
    assert chat.requests[-1][1].is_default
    assert "max_tokens" not in chat.chat.calls[-1]


def test_a_refused_cap_is_asked_once_more_without_it(chat) -> None:
    chat.chat.replies = [_HttpError(400, "max_tokens: 4160 > 4096, the maximum for this model"), '{"answer": "no"}']

    text = background.invoke_background("condition", [HumanMessage(content="Done?")], answer=ANSWER,
                                        thread_model="model:openai:gpt-4-turbo")

    assert text == '{"answer": "no"}'
    first, second = chat.chat.calls
    assert first["max_tokens"] == 4160 and "max_tokens" not in second
    # The schema stays: only the cap was refused.
    assert "response_format" in second and background._schema_refusals == set()


@pytest.mark.parametrize(("purpose", "ref", "bound"), [
    ("verifier", "model:google:gemini-2.5-flash", {"max_output_tokens": 512}),
    ("extraction", "model:google:gemini-2.5-flash", {"max_output_tokens": 4096}),
    ("dream", "model:google:gemini-2.5-flash", {"max_output_tokens": 1536}),
    ("contradiction", "model:google:gemini-2.5-flash", {"max_output_tokens": 160}),
])
def test_each_purpose_has_its_own_cap(chat, monkeypatch, purpose, ref, bound) -> None:
    flash = reasoning._EXACT_REASONING_CAPABILITIES[("google", "gemini-2.5-flash")]
    monkeypatch.setattr(background, "resolve_reasoning_capabilities_for_ref", lambda _ref: flash)
    monkeypatch.setattr(models, "get_current_model", lambda: ref)

    background.background_model_for(purpose, thread_model=ref).invoke([HumanMessage(content="x")])

    assert {key: chat.chat.calls[-1][key] for key in bound} == bound


# ── Local models: no swap, no reload ─────────────────────────────────────────


def test_ollama_background_call_keeps_the_threads_model_and_context(monkeypatch) -> None:
    from langchain_ollama import ChatOllama

    monkeypatch.setattr(models, "_local_num_ctx_for", lambda _model: 24_576)
    monkeypatch.setattr(models, "get_current_model", lambda: "model:ollama:llama3.2:3b")
    monkeypatch.setattr(models, "require_model_choice", lambda _model: True)
    monkeypatch.setattr(background, "resolve_reasoning_capabilities_for_ref",
                        lambda _ref: reasoning.ollama_thinking_capabilities("qwen3:8b"))
    thread = "model:ollama:qwen3:8b"
    main = models.get_llm_for(thread)

    target = _in_turn(thread, lambda: background.resolve_background_model("contradiction"))

    bound = target.model
    inner = bound.bound
    assert isinstance(inner, ChatOllama)
    # The turn's own model with the same context allocation: Ollama reuses what is loaded.
    assert inner.model == main.model == "qwen3:8b"
    assert bound.kwargs["options"] == {"num_ctx": main.num_ctx, "num_predict": 160}
    assert main.num_ctx == 24_576
    # Thinking off for a bounded yes/no.
    assert inner.reasoning is False
    assert target.reasoning.reasoning_runs is False


# ── Structured answers ───────────────────────────────────────────────────────


@pytest.mark.parametrize(("ref", "expected"), [
    ("model:openai:gpt-5", {"response_format": {"type": "json_schema", "json_schema": {
        "name": "yes_no", "schema": ANSWER.schema, "strict": True}}}),
    ("model:xai:grok-4.5", {"response_format": {"type": "json_schema", "json_schema": {
        "name": "yes_no", "schema": ANSWER.schema, "strict": True}}}),
    ("model:anthropic:claude-opus-5-5", {"output_config": {"format": {"type": "json_schema", "schema": ANSWER.schema}}}),
    ("model:google:gemini-3-flash-preview", {"response_mime_type": "application/json",
                                             "response_json_schema": ANSWER.schema}),
    ("model:ollama:qwen3:8b", {"format": ANSWER.schema}),
])
def test_structured_answers_use_the_providers_schema_mode(chat, ref, expected) -> None:
    chat.chat.replies = ['{"answer": "yes"}']

    text = background.invoke_background("condition", [HumanMessage(content="Done?")], answer=ANSWER, thread_model=ref)

    assert background.parse_json_object(text) == {"answer": "yes"}
    sent = chat.chat.calls[-1]
    assert {key: sent[key] for key in expected} == expected
    assert "tool_choice" not in sent and "tools" not in sent


@pytest.mark.parametrize("ref", ["model:openrouter:anthropic/claude-opus-5.5", "model:custom_openai_lab:qwen",
                                 "model:codex:gpt-5.5", "model:claude_subscription:claude-opus-5-5"])
def test_other_providers_are_asked_in_plain_text(chat, ref) -> None:
    chat.chat.replies = ["Yes."]

    text = background.invoke_background("condition", [HumanMessage(content="Done?")], answer=ANSWER, thread_model=ref)

    assert text == "Yes."
    assert not {"response_format", "output_config", "format", "response_json_schema"} & set(chat.chat.calls[-1])


def test_a_refused_schema_is_asked_once_more_in_plain_text_and_remembered(chat) -> None:
    chat.chat.refuse_schema = True
    chat.chat.replies = ["yes", "no"]

    first = background.invoke_background("condition", [HumanMessage(content="Done?")], answer=ANSWER,
                                         thread_model="model:openai:gpt-4-turbo")
    second = background.invoke_background("condition", [HumanMessage(content="Done?")], answer=ANSWER,
                                          thread_model="model:openai:gpt-4-turbo")

    assert (first, second) == ("yes", "no")
    with_schema = ["response_format" in call for call in chat.chat.calls]
    assert with_schema == [True, False, False]


@pytest.mark.parametrize("error", [_HttpError(503, "overloaded"), _HttpError(429, "rate limit"), TimeoutError("slow")])
def test_an_outage_is_not_mistaken_for_a_refused_schema(chat, error) -> None:
    chat.chat.replies = [error]

    with pytest.raises(type(error)):
        background.invoke_background("condition", [HumanMessage(content="Done?")], answer=ANSWER,
                                     thread_model="model:openai:gpt-5")

    assert len(chat.chat.calls) == 1
    assert background._schema_refusals == set()


def test_reply_text_drops_thinking_and_keeps_text_blocks() -> None:
    reply = AIMessage(content=[
        {"type": "thinking", "thinking": "hidden"},
        {"type": "text", "text": '{"answer": '},
        {"type": "text", "text": '"no"}'},
    ])

    assert background.reply_text(reply) == '{"answer": "no"}'
    assert background.reply_text(AIMessage(content="<think>hmm</think>\nyes")) == "yes"


def test_text_fields_never_store_a_cut_short_schema_reply() -> None:
    assert background.text_field('{"description": "Alice writes tests."}', "description") == "Alice writes tests."
    assert background.text_field('{"description": "Alice wri', "description") == ""
    assert background.text_field("Alice writes tests.", "description") == "Alice writes tests."
    assert background.list_field('{"insights": [{"title": "x"}]}', "insights") == [{"title": "x"}]
    assert background.list_field("[]", "insights") is None


# ── Purpose in the usage ledger ──────────────────────────────────────────────


def test_background_calls_are_counted_under_their_purpose(chat, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    attach_usage_ledger(chat.chat, provider_id="openai", model_name="gpt-5")
    monkeypatch.setattr(models, "get_current_model", lambda: "model:openai:gpt-5")
    chat.chat.replies = ['{"answer": "yes"}', "[]", "A description.", '{"progress": "done"}']

    background.invoke_background("condition", [HumanMessage(content="?")], answer=ANSWER, thread_id="thread-w")
    background.invoke_background("extraction", [HumanMessage(content="?")])
    background.invoke_background("dream", [HumanMessage(content="?")])
    background.invoke_background("verifier", [HumanMessage(content="?")], thread_id="thread-g", generation_id="gen-1")

    with closing(sqlite3.connect(tmp_path / "usage.db")) as connection:
        rows = connection.execute(
            "SELECT purpose, thread_id, generation_id, input_tokens, output_tokens FROM model_usage ORDER BY id"
        ).fetchall()
    assert rows == [
        ("condition", "thread-w", "", 40, 3),
        ("extraction", "", "", 40, 3),
        ("dream", "", "", 40, 3),
        ("verifier", "thread-g", "gen-1", 40, 3),
    ]


# ── A refused lowering never touches the conversation's setting ──────────────


class _Runnable:
    def __init__(self, error=None):
        self.error = error
        self.calls = 0

    def invoke(self, messages, **kwargs):
        self.calls += 1
        if self.error:
            raise self.error
        return AIMessage(content="fallback")

    def stream(self, messages, **kwargs):
        self.calls += 1
        if self.error:
            raise self.error
        yield AIMessageChunk(content="fallback")


def test_a_refused_background_lowering_retries_once_and_leaves_the_conversation_alone(monkeypatch) -> None:
    import row_bot.providers.transports.reasoning_fallback as fallback_module

    resets: list[tuple] = []
    monkeypatch.setattr(fallback_module, "_active_thread_id", lambda: "thread-a")
    monkeypatch.setattr("row_bot.threads.set_thread_reasoning_selection", lambda *args: resets.append(args))
    plan = background.background_reasoning("model:openai:gpt-5", _OPENAI).plan
    primary = _Runnable(_HttpError(400, "reasoning_effort 'minimal' is not supported with this model"))
    default = _Runnable()
    model = ReasoningFallbackChatModel(primary=primary, provider_default=default, plan=plan, provider_id="openai")

    reply = model.invoke([HumanMessage(content="Done?")])

    assert reply.content == "fallback"
    assert primary.calls == default.calls == 1
    assert resets == []
    assert reasoning.consume_reasoning_notices("thread-a") == []
    assert ("thread-a", "model:openai:gpt-5") not in reasoning._suppressed_overrides
    assert reasoning.background_reasoning_rejected("model:openai:gpt-5")


# ── End to end on the direct Anthropic API ───────────────────────────────────


class _MessagesAPI:
    def __init__(self, *answers: tuple[int, str]) -> None:
        self.answers = list(answers)
        self.bodies: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.bodies.append(body)
        status, text = self.answers.pop(0)
        if status != 200:
            return httpx.Response(status, json={"type": "error", "error": {
                "type": "invalid_request_error", "message": text}})
        return httpx.Response(200, json={
            "id": "msg_1", "type": "message", "role": "assistant", "model": body["model"],
            "content": [{"type": "text", "text": text}], "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 120, "output_tokens": 9},
        })


@pytest.fixture
def messages_api(monkeypatch, tmp_path):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    holder: dict[str, _MessagesAPI] = {}
    transport = httpx.MockTransport(lambda request: holder["api"](request))
    sync_client = anthropic_cancellable.cancellable_http_client
    async_client = anthropic_cancellable.cancellable_async_http_client
    monkeypatch.setattr(anthropic_cancellable, "cancellable_http_client",
                        lambda **kwargs: sync_client(transport=transport, **kwargs))
    monkeypatch.setattr(anthropic_cancellable, "cancellable_async_http_client",
                        lambda **kwargs: async_client(transport=transport, **kwargs))
    monkeypatch.setattr(runtime, "get_provider_secret", lambda *_args, **_kwargs: "anthropic-key")
    monkeypatch.setattr(background, "resolve_reasoning_capabilities_for_ref", lambda _ref: None)

    def install(*answers: tuple[int, str]) -> _MessagesAPI:
        holder["api"] = _MessagesAPI(*answers)
        return holder["api"]

    return install


VERDICT = background.structured_answer("goal_verdict", {"progress": {"type": "string"}})


def test_a_claude_verifier_call_is_low_effort_capped_structured_and_counted(messages_api, tmp_path) -> None:
    api = messages_api((200, '{"progress": "done"}'))

    text = background.invoke_background("verifier", [HumanMessage(content="Judge the turn.")], answer=VERDICT,
                                        thread_model="model:anthropic:claude-opus-5-5", thread_id="thread-g")

    assert background.parse_json_object(text) == {"progress": "done"}
    [body] = api.bodies
    assert body["model"] == "claude-opus-5-5"
    assert body["output_config"] == {"format": {"type": "json_schema", "schema": VERDICT.schema}, "effort": "low"}
    # Opus 5.5 thinks adaptively anyway: room for it on top of the verdict.
    assert body["max_tokens"] == background.ANSWER_TOKENS["verifier"] + background.REASONING_HEADROOM_TOKENS
    assert body["thinking"]["type"] == "adaptive"
    assert "tool_choice" not in body
    with closing(sqlite3.connect(tmp_path / "usage.db")) as connection:
        rows = connection.execute("SELECT purpose, thread_id, provider, model FROM model_usage").fetchall()
    assert rows == [("verifier", "thread-g", "anthropic", "claude-opus-5-5")]


def test_opus_4_without_thinking_gets_effort_only(messages_api, monkeypatch) -> None:
    monkeypatch.setattr(background, "resolve_reasoning_capabilities_for_ref", lambda _ref: _OPUS_48)
    api = messages_api((200, "NO"))

    background.invoke_background("contradiction", [HumanMessage(content="Contradiction?")],
                                 thread_model="model:anthropic:claude-opus-4-8")

    [body] = api.bodies
    assert body["output_config"] == {"effort": "low"}
    assert "thinking" not in body
    assert body["max_tokens"] == background.ANSWER_TOKENS["contradiction"]


def test_claude_refusing_effort_falls_back_once_and_later_calls_skip_it(messages_api, monkeypatch) -> None:
    resets: list[tuple] = []
    monkeypatch.setattr("row_bot.threads.set_thread_reasoning_selection", lambda *args: resets.append(args))
    api = messages_api(
        (400, "output_config.effort: effort is not supported on this model"),
        (200, "NO"),
        (200, "NO"),
    )

    for _ in range(2):
        assert background.invoke_background(
            "contradiction", [HumanMessage(content="Contradiction?")],
            thread_model="model:anthropic:claude-opus-5-5",
        ) == "NO"

    efforts = [body.get("output_config", {}).get("effort") for body in api.bodies]
    assert efforts == ["low", None, None]
    assert resets == []


def test_claude_refusing_the_schema_is_answered_in_plain_text(messages_api) -> None:
    api = messages_api(
        (400, "output_config.format: structured outputs are not supported on this model"),
        (200, '{"progress": "progress"}'),
    )

    text = background.invoke_background("verifier", [HumanMessage(content="Judge.")], answer=VERDICT,
                                        thread_model="model:anthropic:claude-opus-5-5")

    assert text == '{"progress": "progress"}'
    assert [("format" in body.get("output_config", {})) for body in api.bodies] == [True, False]
    assert [body["output_config"]["effort"] for body in api.bodies] == ["low", "low"]
