"""Background and auxiliary model calls: same model, lowest effort (WP C1, decision D1).

A background call is work Row-Bot does around the person's turn: the
contradiction check in the memory tool, a workflow's yes/no condition, the goal
verifier, the Auto approval reviewer, compaction, memory extraction, Dream Cycle
and document processing.
None of them is the person's answer, so each one runs

- on the **thread's own model** when it belongs to a thread (compaction,
  contradiction, condition, verifier). On local Ollama that means it never
  loads a different model in the middle of a turn (F13);
- on the **default chat model** when it runs unattended (extraction, Dream,
  Learning's ``learning_*`` calls, which may instead name the model they test);
- at the **lowest reasoning effort** the provider supports, with no setting:
  Anthropic ``effort: low`` without turning thinking on (Claude 5 models think
  adaptively anyway, Opus 4.x without thinking stays without); OpenAI, Gemini,
  xAI and OpenRouter the lowest listed effort, the smallest budget, or off where
  the model allows it; Ollama ``think=false``; custom endpoints unchanged;
- with an **output cap** for its purpose, plus room for reasoning that cannot
  be turned off;
- tagged with its **purpose** in the local usage ledger.

A provider that refuses the lowered reasoning is asked once more at its own
default (``ReasoningFallbackChatModel``) and the model is remembered for the
process. A structured answer uses the provider's JSON-schema mode where Row-Bot
knows it exists; a refusal is retried once without it and remembered, and the
callers always keep their text parsers. Forced ``tool_choice`` is never used:
Claude 5.5-generation models reject it.

``routing.task_route_model`` is read for every purpose, so a cheaper model for
one purpose can be named later without a redesign. Nothing sets it today.

A user-facing purpose (``synthesis``: the final answer written from child
Agents' results, WP C3) goes through the same resolver as a tool-less call on
the conversation's model, but keeps the conversation's own reasoning setting
and has no output cap: the person reads its reply.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from dataclasses import dataclass
from typing import Any

from row_bot.providers.reasoning import (
    BACKGROUND_SCOPE,
    ReasoningCapabilities,
    ReasoningRequestPlan,
    ReasoningSelection,
    background_reasoning_rejected,
    canonical_reasoning_model_ref,
    resolve_reasoning_capabilities_for_ref,
)
from row_bot.providers.selection import parse_model_ref

logger = logging.getLogger(__name__)

# Unattended jobs use the default chat model; every other call belongs to a thread.
UNATTENDED_PURPOSES = frozenset({"extraction", "dream"})
# Learning's own calls (E13): the default chat model at the lowest effort (D1), unless the
# call names the model under test (an evaluation arm or a personal-model version). They
# never belong to a conversation, so the running turn's model is never borrowed.
LEARNING_PURPOSES = frozenset({"learning_diagnosis", "learning_test", "learning_teacher", "learning_meta"})
# Replies the person reads: the conversation's reasoning setting, not the lowest, and no cap.
USER_FACING_PURPOSES = frozenset({"synthesis"})
# Purposes whose caller sizes the reply itself: room for reasoning only when it is
# known to run (compaction's allowance already follows the context window).
_CALLER_SIZED_PURPOSES = frozenset({"compaction"})

# Answer tokens per purpose. A cap is only a ceiling: a model that answers
# "no" stops after a few tokens whatever it is. Compaction passes its own
# bounded allowance and document processing stays uncapped.
ANSWER_TOKENS: dict[str, int] = {
    "contradiction": 160,
    "condition": 64,
    "verifier": 512,
    # The Auto reviewer: {"decision", "reason"} in one short line.
    "approval_review": 200,
    "extraction": 4096,
    "dream": 1536,
}
# Room for reasoning that still runs at the lowest effort (it counts against the
# output cap on every provider), or that Row-Bot cannot see is off.
REASONING_HEADROOM_TOKENS = 4096
# Words in a refusal that mean the cap itself was too large for the model.
_CAP_REFUSAL_TERMS = (
    "max_tokens", "max_output_tokens", "max_completion_tokens", "maxoutputtokens",
    "context length", "context_length", "too many tokens", "num_predict",
)
# Thinking budgets are approximate; the answer needs a little room past them.
BUDGET_OVERSHOOT_TOKENS = 256

_ANTHROPIC_PROVIDERS = frozenset({"anthropic", "claude_subscription"})
# Providers whose LangChain model takes a JSON schema for the reply.
_SCHEMA_PROVIDERS = frozenset({"openai", "xai", "anthropic", "google", "ollama"})
# ChatOllama fields that make up its ``options``; binding ``options`` replaces them all.
_OLLAMA_OPTION_FIELDS = (
    "mirostat", "mirostat_eta", "mirostat_tau", "num_ctx", "num_gpu", "num_thread",
    "repeat_last_n", "repeat_penalty", "temperature", "seed", "tfs_z", "top_k", "top_p",
)
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL)
_THINK_TAG = re.compile(r"</?think>")
_RESOLVE: Any = object()

# Model refs whose provider refused a JSON-schema reply in this process.
_schema_refusals: set[str] = set()


@dataclass(frozen=True)
class BackgroundReasoning:
    """The reasoning request for one background call and whether reasoning still runs.

    ``reasoning_runs`` is None when Row-Bot cannot tell (no capability data).
    """

    plan: ReasoningRequestPlan
    reasoning_runs: bool | None = True
    headroom: int = REASONING_HEADROOM_TOKENS

    def output_cap(self, answer_tokens: int | None, *, room_when_unknown: bool = True) -> int | None:
        if answer_tokens is None:
            return None
        room = self.reasoning_runs is True or (self.reasoning_runs is None and room_when_unknown)
        return answer_tokens + (self.headroom if room else 0)


@dataclass(frozen=True)
class StructuredAnswer:
    """A JSON-schema reply: a name and a strict JSON schema (every key required)."""

    name: str
    schema: dict[str, Any]


@dataclass(frozen=True)
class BackgroundModel:
    model: Any
    model_ref: str
    provider_id: str
    reasoning: BackgroundReasoning
    max_tokens: int | None
    # The same model without the output cap, for a provider that refuses the cap.
    uncapped: Any = None


def json_object(properties: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """A strict JSON-schema object: every property required, nothing else allowed."""
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def structured_answer(name: str, properties: dict[str, dict[str, Any]]) -> StructuredAnswer:
    return StructuredAnswer(name, json_object(properties))


# ── Model rule ───────────────────────────────────────────────────────────────


def active_thread_model() -> str:
    """The model the running turn uses (its override), else the default chat model."""
    from row_bot import models

    return str(models._active_model_override.get() or models.get_current_model() or "")


def thread_model_for(thread_id: str | None) -> str:
    """A stored thread's own model: its override, else the default chat model."""
    from row_bot import models

    override = ""
    if thread_id:
        try:
            from row_bot.threads import _get_thread_model_override

            override = str(_get_thread_model_override(str(thread_id)) or "")
        except Exception:
            logger.debug("Could not read the model of thread %s", thread_id, exc_info=True)
    return override or str(models.get_current_model() or "")


def active_turn_ids() -> tuple[str, str]:
    """(thread id, generation id) of the agent turn running in this context, if any."""
    agent = sys.modules.get("row_bot.agent")
    getter = getattr(agent, "get_active_runtime_context", None)
    if not callable(getter):
        return "", ""
    try:
        runtime = getter()
    except Exception:
        return "", ""
    if not isinstance(runtime, dict):
        return "", ""
    return str(runtime.get("thread_id") or ""), str(runtime.get("generation_id") or "")


def background_model_ref(
    purpose: str,
    *,
    thread_model: str | None = None,
    task_model: str | None = None,
) -> str:
    """The model a background call uses (D1).

    Unattended jobs use the default chat model. Every other call prefers the
    task's model, then the thread's model, then the running turn's model.
    """
    from row_bot import models

    if purpose in UNATTENDED_PURPOSES:
        base = str(models.get_current_model() or "")
    elif purpose in LEARNING_PURPOSES:
        base = str(task_model or models.get_current_model() or "")
    else:
        base = str(task_model or thread_model or active_thread_model() or "")
    try:
        from row_bot.providers.routing import task_route_model

        routed = task_route_model(purpose)
    except Exception:
        logger.debug("Background route lookup failed for %s", purpose, exc_info=True)
        routed = ""
    if not routed or routed == base:
        return base
    if purpose not in UNATTENDED_PURPOSES and _is_local(base):
        # A thread-tied call never swaps the local model mid-turn.
        return base
    return routed


def _is_local(model: str) -> bool:
    parsed = parse_model_ref(model)
    if parsed:
        return parsed[0] == "ollama"
    try:
        from row_bot.models import is_model_local

        return bool(model) and bool(is_model_local(model))
    except Exception:
        return False


# ── Effort ───────────────────────────────────────────────────────────────────


def background_reasoning(
    model_ref: str,
    capabilities: ReasoningCapabilities | None = _RESOLVE,
    *,
    remembered_refusals: bool = True,
) -> BackgroundReasoning:
    """The lowest reasoning the provider supports for ``model_ref``, as a request plan.

    ``capabilities`` defaults to the catalog's; document processing passes the
    ones it captured with the conversation's reasoning snapshot, and handles a
    refusal per job (``remembered_refusals=False``) so its plan stays fixed.
    """
    parsed = parse_model_ref(model_ref)
    if parsed is None:
        return BackgroundReasoning(ReasoningRequestPlan(str(model_ref or "")))
    provider, runtime_model = parsed
    ref = canonical_reasoning_model_ref(provider, runtime_model)
    caps = resolve_reasoning_capabilities_for_ref(ref) if capabilities is _RESOLVE else capabilities
    unchanged = BackgroundReasoning(
        ReasoningRequestPlan(ref),
        reasoning_runs=_reasons_by_default(provider, runtime_model, caps),
    )
    if provider.startswith("custom_openai_") or (remembered_refusals and background_reasoning_rejected(ref)):
        return unchanged
    if provider in _ANTHROPIC_PROVIDERS:
        return _anthropic_reasoning(ref, runtime_model, caps) or unchanged
    selection = _lowest_selection(caps)
    if selection is None:
        return unchanged
    plan = ReasoningRequestPlan(ref, selection, caps, scope=BACKGROUND_SCOPE)
    if selection.kind == "off" or (selection.kind == "effort" and selection.effort == "none"):
        return BackgroundReasoning(plan, reasoning_runs=False)
    if selection.kind == "budget":
        return BackgroundReasoning(plan, headroom=selection.budget + BUDGET_OVERSHOOT_TOKENS)
    return BackgroundReasoning(plan)


def _reasons_by_default(provider: str, runtime_model: str, caps: ReasoningCapabilities | None) -> bool | None:
    if provider in _ANTHROPIC_PROVIDERS:
        return _runs_adaptive_by_default(runtime_model)
    if provider == "ollama":
        # Row-Bot knows which local models think (Ollama says so, else the family list).
        return caps is not None
    # Unknown: a short answer cap keeps room in case the model reasons.
    return None


def _lowest_selection(caps: ReasoningCapabilities | None) -> ReasoningSelection | None:
    if caps is None or not caps.controllable:
        return None
    if caps.can_disable and not caps.mandatory:
        return ReasoningSelection(kind="off")
    if caps.supported_efforts:
        return ReasoningSelection(kind="effort", effort=caps.supported_efforts[0])
    if caps.supports_budget:
        return ReasoningSelection(kind="budget", budget=max(1, caps.budget_min))
    return None


def _anthropic_reasoning(
    ref: str,
    runtime_model: str,
    caps: ReasoningCapabilities | None,
) -> BackgroundReasoning | None:
    """``effort: low`` on Claude, leaving thinking as the model runs it by default.

    Claude 5 models think adaptively whether or not ``thinking`` is sent and
    cannot turn it off; Opus 4.x does not think unless asked, and a background
    call must not ask it to. So the plan carries the effort only.
    """
    adaptive = _runs_adaptive_by_default(runtime_model)
    efforts = tuple(caps.supported_efforts) if caps is not None else ()
    if not efforts:
        if not adaptive:
            return None
        efforts = ("low",)
    base = caps.to_json() if caps is not None else {"source": "background_default"}
    effort_only = ReasoningCapabilities(**{
        **base,
        "supported_efforts": efforts,
        "thinking_mode": "none",
        "request_style": "anthropic",
    })
    selection = ReasoningSelection(kind="effort", effort=effort_only.supported_efforts[0])
    plan = ReasoningRequestPlan(ref, selection, effort_only, scope=BACKGROUND_SCOPE)
    return BackgroundReasoning(plan, reasoning_runs=adaptive)


def conversation_reasoning(model_ref: str, thread_id: str = "") -> BackgroundReasoning:
    """The conversation's own reasoning request for ``model_ref``: the person's effort, not lowered."""
    from row_bot.providers.reasoning import request_plan_for

    if parse_model_ref(model_ref) is None:
        return BackgroundReasoning(ReasoningRequestPlan(str(model_ref or "")), reasoning_runs=None)
    try:
        plan = request_plan_for(thread_id or None, model_ref)
    except Exception:
        logger.debug("Reasoning setting of %s unavailable; provider default", thread_id, exc_info=True)
        plan = ReasoningRequestPlan(model_ref)
    return BackgroundReasoning(plan, reasoning_runs=None)


def _runs_adaptive_by_default(runtime_model: str) -> bool:
    from row_bot.providers.transports.thinking_binding import runs_adaptive_by_default

    return runs_adaptive_by_default(runtime_model)


# ── The model ────────────────────────────────────────────────────────────────


def resolve_background_model(
    purpose: str,
    *,
    thread_model: str | None = None,
    task_model: str | None = None,
    thread_id: str = "",
    generation_id: str = "",
    max_tokens: int | None = None,
) -> BackgroundModel:
    """The model for one background call: reasoning lowered, output capped, purpose tagged."""
    from row_bot import models
    from row_bot.providers.usage_ledger import with_usage_purpose

    ref = background_model_ref(purpose, thread_model=thread_model, task_model=task_model)
    selection_ref, provider, canonical = ref, "", ref
    if ref:
        try:
            from row_bot.providers.resolution import resolve_provider_config

            resolved = resolve_provider_config(ref, allow_legacy_local=True)
            selection_ref, provider = resolved.selection_ref, resolved.provider_id
            canonical = canonical_reasoning_model_ref(resolved.provider_id, resolved.runtime_model)
        except Exception:
            logger.debug("Background model %s did not resolve; using it as given", ref, exc_info=True)

    def tagged(runnable: Any) -> Any:
        return with_usage_purpose(runnable, purpose, thread_id=thread_id, generation_id=generation_id)

    if purpose in USER_FACING_PURPOSES:
        reasoning = conversation_reasoning(canonical, thread_id)
        llm = models.get_llm_for(selection_ref, reasoning_plan=reasoning.plan)
        return BackgroundModel(tagged(llm), selection_ref, provider, reasoning, None)
    reasoning = background_reasoning(canonical)
    llm = models.get_llm_for(selection_ref, reasoning_plan=reasoning.plan)
    cap = None
    if not provider.startswith("custom_openai_"):
        # A custom endpoint is left as configured: its server knows its own limits.
        answer_tokens = max_tokens if max_tokens is not None else ANSWER_TOKENS.get(purpose)
        cap = reasoning.output_cap(answer_tokens, room_when_unknown=purpose not in _CALLER_SIZED_PURPOSES)
    return BackgroundModel(
        tagged(_capped(llm, provider, reasoning, cap)), selection_ref, provider, reasoning, cap,
        uncapped=tagged(_capped(llm, provider, reasoning, None)) if cap is not None else None,
    )


def background_model_for(
    purpose: str,
    *,
    thread_model: str | None = None,
    task_model: str | None = None,
    thread_id: str = "",
    generation_id: str = "",
    max_tokens: int | None = None,
) -> Any:
    """A ready chat model for one background purpose (see the module docstring)."""
    return resolve_background_model(
        purpose,
        thread_model=thread_model,
        task_model=task_model,
        thread_id=thread_id,
        generation_id=generation_id,
        max_tokens=max_tokens,
    ).model


def _capped(llm: Any, provider: str, reasoning: BackgroundReasoning, cap: int | None) -> Any:
    bind: dict[str, Any] = {}
    if provider == "ollama":
        if cap is not None:
            # Ollama replaces all its options with these: keep the chat model's
            # context allocation so the loaded model is reused, not reloaded.
            options = {
                name: getattr(llm, name)
                for name in _OLLAMA_OPTION_FIELDS
                if getattr(llm, name, None) is not None
            }
            options["num_predict"] = cap
            bind["options"] = options
        if getattr(llm, "reasoning", None) is True and reasoning.plan.selection.kind != "effort":
            # A thinking model the plan could not lower: a bounded answer needs
            # final text, not hidden thought.
            bind["reasoning"] = False
    elif cap is not None:
        bind["max_output_tokens" if provider == "google" else "max_tokens"] = cap
    return llm.bind(**bind) if bind else llm


# ── One call ─────────────────────────────────────────────────────────────────


def invoke_background(
    purpose: str,
    messages: list[Any],
    *,
    answer: StructuredAnswer | None = None,
    thread_model: str | None = None,
    task_model: str | None = None,
    thread_id: str = "",
    generation_id: str = "",
    max_tokens: int | None = None,
) -> str:
    """Make one background call and return its reply text, thinking removed.

    With ``answer``, providers known to take a JSON schema are asked for one;
    the caller parses JSON first and keeps its text parser for everyone else.
    A provider that refuses the output cap or the schema is asked once more
    without it, so a background call never fails over a request shape.
    """
    target = resolve_background_model(
        purpose,
        thread_model=thread_model,
        task_model=task_model,
        thread_id=thread_id,
        generation_id=generation_id,
        max_tokens=max_tokens,
    )
    model = target.model
    request = _schema_request(target, answer) if answer is not None else None
    schema_refused = False
    while True:
        try:
            text = reply_text((model.bind(**request) if request else model).invoke(messages))
        except Exception as exc:
            if model is target.model and target.uncapped is not None and _cap_refused(exc):
                logger.info("Background %s call: %s refused the output cap (%s); asking once without it",
                            purpose, target.model_ref, type(exc).__name__)
                model = target.uncapped
                continue
            if request and _schema_refused(exc):
                logger.info("Background %s call: %s refused a JSON-schema reply (%s); asking once without it",
                            purpose, target.model_ref, type(exc).__name__)
                request, schema_refused = None, True
                continue
            raise
        if schema_refused:
            _schema_refusals.add(target.model_ref)
        return text


def _schema_request(target: BackgroundModel, answer: StructuredAnswer) -> dict[str, Any] | None:
    provider = target.provider_id
    if provider not in _SCHEMA_PROVIDERS or target.model_ref in _schema_refusals:
        return None
    if provider in {"openai", "xai"}:
        return {"response_format": {"type": "json_schema", "json_schema": {
            "name": answer.name, "schema": answer.schema, "strict": True}}}
    if provider == "anthropic":
        # Merged with the constructor's ``effort`` into one ``output_config``.
        return {"output_config": {"format": {"type": "json_schema", "schema": answer.schema}}}
    if provider == "google":
        return {"response_mime_type": "application/json", "response_json_schema": answer.schema}
    return {"format": answer.schema}


def _request_refused(exc: BaseException, *, local_errors: bool = False) -> bool:
    """A request-shape refusal (HTTP 400/422, or with ``local_errors`` a client-side
    schema error), never a stop, a timeout or an outage."""
    from row_bot.cancellation import current_cancellation_scope
    from row_bot.providers.transports.reasoning_fallback import _status_code

    if isinstance(exc, TimeoutError):
        return False
    scope = current_cancellation_scope()
    if scope is not None and scope.is_cancelled():
        return False
    if local_errors and isinstance(exc, (ValueError, NotImplementedError)):
        return True
    return _status_code(exc) in {400, 422}


def _cap_refused(exc: BaseException) -> bool:
    """The model refused the output cap itself (too large for it, or for its context)."""
    from row_bot.providers.transports.reasoning_fallback import _safe_error_detail

    if not _request_refused(exc):
        return False
    detail = _safe_error_detail(exc).lower()
    return any(term in detail for term in _CAP_REFUSAL_TERMS)


def _schema_refused(exc: BaseException) -> bool:
    """The provider refused the JSON-schema reply."""
    return _request_refused(exc, local_errors=True)


def reply_text(response: Any) -> str:
    """The text of a model reply: text blocks only, ``<think>`` sections removed."""
    content = getattr(response, "content", response)
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") in {"text", "output_text"}:
                parts.append(str(block.get("text") or ""))
        content = "".join(parts)
    text = str(content or "") if not isinstance(content, str) else content
    return _THINK_TAG.sub("", _THINK_BLOCK.sub("", text)).strip()


def parse_json_object(text: str) -> dict[str, Any] | None:
    """The reply as a JSON object when it is one (a schema reply, possibly fenced), else None."""
    raw = str(text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    if not raw.startswith("{"):
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def text_field(text: str, field: str) -> str:
    """A plain-text answer from a ``{field: ...}`` schema reply, or the reply itself.

    A reply that is a JSON object but unusable (cut short, or missing the
    field) gives "", so it is never stored as text.
    """
    parsed = parse_json_object(text)
    if parsed is not None:
        value = parsed.get(field)
        return value.strip() if isinstance(value, str) else ""
    raw = str(text or "").strip()
    return "" if raw.startswith("{") else raw


def list_field(text: str, field: str) -> list[Any] | None:
    """The list under ``field`` in a schema reply, or None when the reply is not one."""
    parsed = parse_json_object(text)
    if parsed is None:
        return None
    value = parsed.get(field)
    return value if isinstance(value, list) else None
