"""Which providers a live eval run can use: credentials come from environment variables only.

The run never reads a Row-Bot data folder's keys or the OS keychain. A provider
is available when its API-key variable is set (the same names Row-Bot itself
reads, ``row_bot.providers.auth_store.PROVIDER_API_KEY_ENV``). This module
imports nothing from Row-Bot, so planning a run (``--dry-run``) touches no data
folder and no network.

xAI OAuth and Claude Subscription sign in with OAuth and have no
environment-variable credentials; they are in the manual checklist. The ChatGPT
subscription (``codex``) has its own lane (D31): its sign-in comes from a
dedicated eval profile outside the repo (``eval_profile``), decided here from
local files only.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Mapping

PROVIDERS_ENV = "ROW_BOT_LIVE_EVAL_PROVIDERS"
OLLAMA_MODEL_ENV = "ROW_BOT_LIVE_EVAL_OLLAMA_MODEL"
MODEL_ENV_PREFIX = "ROW_BOT_LIVE_EVAL_MODEL_"


@dataclass(frozen=True)
class ProviderTarget:
    """One provider the eval can run, with its default model and what it reports."""

    provider_id: str
    key_env: str
    default_model: str
    # The provider returns a cost per call (OpenRouter); others need the rates file.
    reports_cost: bool = False
    # Agent-mode tasks need tool calling through Row-Bot's readiness checks.
    agent: bool = True
    notes: str = ""

    def model(self, env: Mapping[str, str] | None = None) -> str:
        source = os.environ if env is None else env
        override = str(source.get(model_env(self.provider_id), "") or "").strip()
        return override or self.default_model


def model_env(provider_id: str) -> str:
    """The variable that overrides a provider's eval model, e.g. ROW_BOT_LIVE_EVAL_MODEL_ANTHROPIC."""
    return MODEL_ENV_PREFIX + provider_id.upper()


# The defaults are recent general models each provider serves; override any of
# them with ROW_BOT_LIVE_EVAL_MODEL_<PROVIDER>. Order is the run order.
TARGETS: tuple[ProviderTarget, ...] = (
    ProviderTarget("anthropic", "ANTHROPIC_API_KEY", "claude-opus-5-5"),
    ProviderTarget("openai", "OPENAI_API_KEY", "gpt-5.5"),
    ProviderTarget("google", "GOOGLE_API_KEY", "gemini-3.1-flash-lite-preview"),
    ProviderTarget("xai", "XAI_API_KEY", "grok-4-1-fast-reasoning"),
    ProviderTarget("openrouter", "OPENROUTER_API_KEY", "z-ai/glm-5.2", reports_cost=True),
    ProviderTarget("requesty", "REQUESTY_API_KEY", "anthropic/claude-haiku-4-5"),
    ProviderTarget("minimax", "MINIMAX_API_KEY", "MiniMax-M2.7"),
    ProviderTarget("atlascloud", "ATLASCLOUD_API_KEY", "anthropic/claude-opus-4.8"),
    ProviderTarget("opencode_zen", "OPENCODE_ZEN_API_KEY", "kimi-k2.6"),
    ProviderTarget("opencode_go", "OPENCODE_GO_API_KEY", "qwen3.7-max"),
    ProviderTarget("ollama_cloud", "OLLAMA_API_KEY", "gpt-oss:120b-cloud"),
    ProviderTarget(
        "ollama", "", "",
        notes=f"local Ollama: runs when {OLLAMA_MODEL_ENV} names an installed model and OLLAMA_HOST answers; free",
    ),
    # The ChatGPT lane (D31): "auto" is GPT-6.1 Sol if the live catalog lists it, else GPT-5.6 Sol.
    ProviderTarget("codex", "", "auto", notes="the ChatGPT subscription, signed in once in the eval profile"),
)
OAUTH_ONLY = ("xai_oauth", "claude_subscription")
# Claude models the cache-marker checks use on the gateways (override with the variables).
OPENROUTER_CLAUDE_ENV = "ROW_BOT_LIVE_EVAL_OPENROUTER_CLAUDE"
OPENROUTER_CLAUDE_DEFAULT = "anthropic/claude-haiku-4.5"
REQUESTY_CLAUDE_ENV = "ROW_BOT_LIVE_EVAL_REQUESTY_CLAUDE"
REQUESTY_CLAUDE_DEFAULT = "anthropic/claude-haiku-4-5"
# DeepSeek's own API (cache hit/miss counts) is reached as a custom OpenAI-compatible
# endpoint whose key lives only in the run's memory; it is used by the B1 usage check.
DEEPSEEK_KEY_ENV = "DEEPSEEK_API_KEY"


@dataclass(frozen=True)
class Availability:
    target: ProviderTarget
    model: str
    available: bool
    reason: str = ""
    needs_probe: bool = False


@dataclass
class Selection:
    chosen: list[Availability] = field(default_factory=list)
    skipped: list[Availability] = field(default_factory=list)


def target(provider_id: str) -> ProviderTarget:
    for item in TARGETS:
        if item.provider_id == provider_id:
            return item
    raise KeyError(provider_id)


def availability(item: ProviderTarget, env: Mapping[str, str] | None = None) -> Availability:
    """Whether *item* can run, decided from the environment alone (no network)."""
    source = os.environ if env is None else env
    if item.provider_id == "codex":
        from .eval_profile import availability as lane

        ready, reason = lane(source)
        return Availability(item, item.model(source), ready, reason)
    if item.provider_id == "ollama":
        model = str(source.get(OLLAMA_MODEL_ENV, "") or "").strip()
        if not model:
            return Availability(item, "", False, f"set {OLLAMA_MODEL_ENV} to an installed model")
        host = str(source.get("OLLAMA_HOST", "") or "127.0.0.1:11434").strip()
        return Availability(item, model, True, f"probes OLLAMA_HOST ({host}) when the run starts", needs_probe=True)
    if not str(source.get(item.key_env, "") or "").strip():
        return Availability(item, item.model(source), False, f"set {item.key_env}")
    return Availability(item, item.model(source), True)


def select(env: Mapping[str, str] | None = None) -> Selection:
    """The providers to run: ROW_BOT_LIVE_EVAL_PROVIDERS (comma list) or every available one."""
    source = os.environ if env is None else env
    wanted = [name.strip() for name in str(source.get(PROVIDERS_ENV, "") or "").split(",") if name.strip()]
    unknown = [name for name in wanted if name not in {item.provider_id for item in TARGETS}]
    if unknown:
        raise ValueError(
            f"{PROVIDERS_ENV} names unknown providers {unknown}; known: "
            + ", ".join(item.provider_id for item in TARGETS)
            + (f" (OAuth-only providers {', '.join(OAUTH_ONLY)} are manual checks)" if set(unknown) & set(OAUTH_ONLY) else "")
        )
    selection = Selection()
    for item in TARGETS:
        state = availability(item, source)
        if wanted and item.provider_id not in wanted:
            continue
        (selection.chosen if state.available else selection.skipped).append(state)
    return selection
