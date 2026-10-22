"""Learning's own model calls (E13): one client for testing (``evaluation``) and diagnosis.

Each call runs on the default chat model at the lowest effort unless the caller names a model
(the cost plan's D1 rule, ``providers.background``), and is tagged with its ``learning_*`` purpose
in the usage ledger. **Learning makes no calls through the person's Claude plan** (O99, O110): a
default model on it is refused before anything is sent or read (:class:`ClaudePlanRefused`).

This module reads no Learning data; drafting code may use it (it never imports the vault).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from row_bot.learning.spending import is_local_provider

# Providers Learning never calls through (O99: the person's Claude plan runs their own Claude Code).
NEVER_CALLED = frozenset({"claude_subscription"})
CHARS_PER_TOKEN = 3.5
OUTPUT_ALLOWANCE = 4_096


class ClaudePlanRefused(RuntimeError):
    """The model a Learning call would use is the person's Claude plan: nothing is sent."""

    code = "claude_plan"

    def __init__(self) -> None:
        super().__init__("claude_plan")


class NoModel(RuntimeError):
    """No default model is chosen."""

    code = "no_model"

    def __init__(self) -> None:
        super().__init__("no_model")


@dataclass(frozen=True)
class ModelReply:
    message: Any
    input_tokens: int
    output_tokens: int
    local: bool
    model_ref: str

    @property
    def tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class ModelClient(Protocol):
    """How Learning asks a model; tests pass a fake."""

    def invoke(self, messages: Sequence[Any], *, tools: Sequence[Mapping[str, Any]] | None, model_ref: str | None,
               purpose: str) -> ModelReply: ...

    def is_local(self, model_ref: str | None) -> bool: ...


def text_of(message: Any) -> str:
    from row_bot.providers.background import reply_text

    return reply_text(message)


def estimate_tokens(messages: Sequence[Any], tools: Sequence[Mapping[str, Any]] | None = None) -> int:
    """An upper-leaning token estimate of one call: what is sent plus room for the answer."""

    import json

    chars = 0
    for message in messages:
        content = getattr(message, "content", message.get("content") if isinstance(message, Mapping) else "")
        chars += len(content if isinstance(content, str) else json.dumps(content, default=str)) + 16
        calls = getattr(message, "tool_calls", None)
        if calls:
            chars += len(json.dumps(calls, default=str))
    if tools:
        chars += len(json.dumps(list(tools), default=str))
    return math.ceil(chars / CHARS_PER_TOKEN) + OUTPUT_ALLOWANCE


def provider_of(model_ref: str | None, purpose: str) -> tuple[str, str]:
    """(the model reference a call would use, its provider id), without building the model."""

    from row_bot.providers import background
    from row_bot.providers.resolution import resolve_provider_config

    ref = background.background_model_ref(purpose, task_model=model_ref)
    if not ref:
        raise NoModel()
    resolved = resolve_provider_config(ref, allow_legacy_local=True)
    return str(resolved.selection_ref), str(resolved.provider_id)


class LedgerModelClient:
    """Learning's own model calls (E13): the default chat model at the lowest effort unless the
    caller names its model (D1), tagged ``learning_*`` in the usage ledger."""

    def __init__(self) -> None:
        self._models: dict[tuple[str, str], tuple[Any, str, str]] = {}

    def _resolve(self, model_ref: str | None, purpose: str) -> tuple[Any, str, str]:
        key = (str(model_ref or ""), purpose)
        cached = self._models.get(key)
        if cached is not None:
            return cached
        from row_bot import models
        from row_bot.providers import background
        from row_bot.providers.reasoning import canonical_reasoning_model_ref
        from row_bot.providers.resolution import resolve_provider_config

        ref = background.background_model_ref(purpose, task_model=model_ref)
        if not ref:
            raise NoModel()
        resolved = resolve_provider_config(ref, allow_legacy_local=True)
        if resolved.provider_id in NEVER_CALLED:
            raise ClaudePlanRefused()
        canonical = canonical_reasoning_model_ref(resolved.provider_id, resolved.runtime_model)
        reasoning = background.background_reasoning(canonical)
        llm = models.get_llm_for(resolved.selection_ref, reasoning_plan=reasoning.plan)
        found = (llm, resolved.selection_ref, resolved.provider_id)
        self._models[key] = found
        return found

    def is_local(self, model_ref: str | None, purpose: str = "learning_test") -> bool:
        try:
            return is_local_provider(self._resolve(model_ref, purpose)[2])
        except Exception:
            return False

    def invoke(self, messages: Sequence[Any], *, tools: Sequence[Mapping[str, Any]] | None, model_ref: str | None,
               purpose: str = "learning_test") -> ModelReply:
        from row_bot.providers.usage_ledger import with_usage_purpose

        llm, selection_ref, provider = self._resolve(model_ref, purpose)
        runnable: Any = llm.bind_tools(list(tools)) if tools else llm
        if provider == "ollama" and getattr(llm, "reasoning", None) is True:
            runnable = runnable.bind(reasoning=False)
        message = with_usage_purpose(runnable, purpose).invoke(list(messages))
        usage = getattr(message, "usage_metadata", None) or {}
        estimate = estimate_tokens(messages, tools) - OUTPUT_ALLOWANCE
        input_tokens = int(usage.get("input_tokens") or estimate)
        output_tokens = int(usage.get("output_tokens") or math.ceil(len(text_of(message)) / CHARS_PER_TOKEN))
        return ModelReply(message, input_tokens, output_tokens, is_local_provider(provider), selection_ref)


__all__ = [
    "CHARS_PER_TOKEN",
    "ClaudePlanRefused",
    "LedgerModelClient",
    "ModelClient",
    "ModelReply",
    "NEVER_CALLED",
    "NoModel",
    "OUTPUT_ALLOWANCE",
    "estimate_tokens",
    "provider_of",
    "text_of",
]
