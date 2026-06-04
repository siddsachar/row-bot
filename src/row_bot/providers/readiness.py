from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping

from row_bot.providers.capabilities import CHAT_TASKS, normalize_snapshot
from row_bot.providers.catalog import model_info_from_metadata
from row_bot.providers.models import TransportMode
from row_bot.providers.resolution import ResolvedProviderConfig, resolve_provider_config
from row_bot.providers.runtime import provider_status

AGENT_MODE_MIN_CONTEXT = 32_000
CHAT_ONLY_MIN_CONTEXT = 16_384
RuntimeMode = Literal["agent", "chat_only", "blocked"]

TRUSTED_AGENT_PROVIDERS = {
    "openai",
    "anthropic",
    "google",
    "xai",
    "minimax",
    "opencode_zen",
    "opencode_go",
    "codex",
    "ollama_cloud",
}

OPENROUTER_AGENT_TOOL_SUPPORT_OVERRIDES: frozenset[str] = frozenset()

AGENT_TRANSPORTS = {
    TransportMode.OPENAI_CHAT.value,
    TransportMode.OPENAI_RESPONSES.value,
    TransportMode.OLLAMA_CHAT.value,
    TransportMode.OLLAMA_CLOUD_CHAT.value,
    TransportMode.ANTHROPIC_MESSAGES.value,
    TransportMode.GOOGLE_GENAI.value,
}


def _positive_int(value: Any, default: int | None = None) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


@dataclass(frozen=True)
class AgentReadinessResult:
    ready: bool
    provider_id: str
    model_id: str
    runtime_model: str
    selection_ref: str
    transport: TransportMode
    context_window: int | None
    required_context: int = AGENT_MODE_MIN_CONTEXT
    tool_calling: bool | None = None
    tool_calling_source: str = "unknown"
    tool_round_trip: bool | None = None
    streaming: bool | None = None
    streaming_tool_calling: bool | None = None
    credential_status: str = "unknown"
    capability_source: str = "unknown"
    confidence: str = "low"
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)

    def user_message(self) -> str:
        if self.ready:
            return f"{self.model_id} via {self.provider_id} is Agent-ready."
        reasons = "; ".join(self.errors) or "Agent Mode requirements were not met."
        return f"{self.model_id} via {self.provider_id} is not Agent-ready: {reasons}"


class AgentCompatibilityError(ValueError):
    def __init__(self, result: AgentReadinessResult):
        self.result = result
        super().__init__(result.user_message())


@dataclass(frozen=True)
class ChatReadinessResult:
    ready: bool
    provider_id: str
    model_id: str
    runtime_model: str
    selection_ref: str
    transport: TransportMode
    context_window: int | None
    required_context: int = CHAT_ONLY_MIN_CONTEXT
    streaming: bool | None = None
    credential_status: str = "unknown"
    capability_source: str = "unknown"
    confidence: str = "low"
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)

    def user_message(self) -> str:
        if self.ready:
            return f"{self.model_id} via {self.provider_id} is ready for Chat Only."
        reasons = "; ".join(self.errors) or "Chat Only requirements were not met."
        return f"{self.model_id} via {self.provider_id} is not chat-ready: {reasons}"


@dataclass(frozen=True)
class ModelRuntimeReadiness:
    agent: AgentReadinessResult
    chat: ChatReadinessResult
    selected_mode: RuntimeMode
    selection_reason: str


def evaluate_agent_readiness(
    value: str | ResolvedProviderConfig,
    provider_id: str | None = None,
    *,
    capability_snapshot: Mapping[str, Any] | None = None,
    status: Mapping[str, Any] | None = None,
    context_window_override: int | None = None,
    probe_ollama_tools: bool = False,
) -> AgentReadinessResult:
    resolved = value if isinstance(value, ResolvedProviderConfig) else resolve_provider_config(
        str(value or ""),
        provider_id,
        allow_legacy_local=True,
    )
    errors: list[str] = []
    warnings: list[str] = []
    actions: list[str] = []

    override_context = _positive_int(context_window_override)
    if override_context:
        context_policy = None
        context_window = override_context
    else:
        context_window = None
    try:
        from row_bot.models import get_context_policy

        if context_window is None:
            context_policy = get_context_policy(resolved.selection_ref)
    except Exception:
        context_policy = None

    if context_window is None:
        context_window = _positive_int(getattr(context_policy, "effective_context", 0))
    if context_window is None:
        errors.append("context window could not be determined")
        actions.append("Re-probe the endpoint or choose a model with provider context metadata.")
    elif context_window < AGENT_MODE_MIN_CONTEXT:
        errors.append(
            f"context window is {context_window:,} tokens; Agent Mode requires at least 32,000 tokens"
        )
        actions.append("Increase the configured context or choose a larger-context model.")

    snapshot = _snapshot_for_resolved(resolved, capability_snapshot)
    normalized = normalize_snapshot(snapshot)
    if not normalized:
        errors.append("capability metadata is missing")
        actions.append("Refresh the provider catalog or re-probe the endpoint.")

    tasks = normalized.get("tasks", set())
    output_modalities = normalized.get("output_modalities", set())
    transport = normalized.get("transport") or resolved.transport.value
    tool_calling = normalized.get("tool_calling")
    streaming = normalized.get("streaming")

    if tasks and not tasks.intersection(CHAT_TASKS):
        errors.append("model does not expose a chat or responses generation task")
    if output_modalities and "text" not in output_modalities:
        errors.append("model does not produce text output")
    if transport not in AGENT_TRANSPORTS:
        errors.append(f"transport {transport or resolved.transport.value} is not supported for Agent Mode")
    opencode_unsupported_reason = _opencode_unsupported_reason(resolved)
    if opencode_unsupported_reason:
        errors.append(opencode_unsupported_reason)
        actions.append("Choose an OpenCode model with chat, responses, or Anthropic Messages support.")

    source = _snapshot_source(snapshot, resolved)
    confidence = "high" if source in {"trusted_provider", "probe", "catalog"} else "low"
    tool_round_trip: bool | None = None
    streaming_tool_calling: bool | None = None
    tool_calling_source = source

    if resolved.provider_id.startswith("custom_openai_"):
        endpoint = resolved.endpoint or {}
        probe = endpoint.get("last_probe") if isinstance(endpoint.get("last_probe"), dict) else {}
        tool_calling = probe.get("tool_calling") if probe else None
        tool_round_trip = probe.get("tool_round_trip") if probe else None
        streaming = probe.get("streaming_ok", streaming) if probe else streaming
        streaming_tool_calling = probe.get("streaming_tool_calling") if probe else None
        tool_calling_source = "probe" if tool_calling is not None else "missing_probe"
        source = "probe" if bool(probe.get("ok")) else "custom_endpoint"
        confidence = "high" if tool_calling is True and tool_round_trip is True else "low"
        if tool_calling is not True:
            errors.append("structured tool calling has not been proven by endpoint probe")
            actions.append("Run the custom endpoint probe for this model.")
        if tool_round_trip is not True:
            errors.append("tool-result round trip has not been proven by endpoint probe")
            actions.append("Re-probe the endpoint after enabling native tool support.")
        if tool_calling is True and tool_round_trip is True and streaming_tool_calling is not True:
            warnings.append("streamed tool calling is not verified; tool requests will use non-stream fallback")
        if context_policy and getattr(context_policy, "cap_source", "") in {"profile_default", "heuristic"}:
            warnings.append("custom endpoint context is inferred; provider metadata or a manual context setting is safer")
    elif resolved.provider_id == "openrouter":
        if resolved.model_id in OPENROUTER_AGENT_TOOL_SUPPORT_OVERRIDES:
            tool_calling = True
            tool_round_trip = True
            tool_calling_source = "curated_override"
            source = "curated_override"
            confidence = "medium"
        elif tool_calling is True:
            tool_round_trip = True
            tool_calling_source = "openrouter_metadata"
            source = "catalog"
            confidence = "medium"
        elif tool_calling is False:
            tool_round_trip = False
            errors.append("OpenRouter metadata says this model does not support structured tools")
            actions.append("Choose a routed model whose OpenRouter metadata includes tools or tool_choice.")
        else:
            tool_round_trip = None
            errors.append("OpenRouter tool metadata is missing or inconclusive")
            actions.append("Choose a model with explicit OpenRouter tool support metadata.")
    elif resolved.provider_id in TRUSTED_AGENT_PROVIDERS:
        if tool_calling is not False:
            tool_calling = True
            tool_round_trip = True
            tool_calling_source = "trusted_provider"
            source = "trusted_provider"
            confidence = "high"
        else:
            tool_round_trip = False
            errors.append("model metadata says structured tool calling is not supported")
    elif resolved.provider_id == "ollama":
        probe: dict[str, Any] | None = None
        if probe_ollama_tools:
            try:
                from row_bot.providers.ollama import probe_ollama_tool_round_trip

                probe = probe_ollama_tool_round_trip(resolved.runtime_model)
            except Exception as exc:
                probe = {"ok": False, "tool_calling": None, "tool_round_trip": None, "error": str(exc)}
        if probe is not None:
            tool_calling = probe.get("tool_calling") if probe.get("tool_calling") in (True, False) else None
            tool_round_trip = probe.get("tool_round_trip") if probe.get("tool_round_trip") in (True, False) else None
            tool_calling_source = "ollama_probe"
            source = "probe"
            confidence = "high" if probe.get("ok") is True else "low"
            probe_error = str(probe.get("error") or "")
            probe_timed_out = "timeout" in probe_error.lower() or "timed out" in probe_error.lower()
            if probe_timed_out:
                errors.append("Ollama tool probe timed out before proving structured tool calling")
                actions.append("Retry Agent verification or choose a model with confirmed tool support.")
            elif tool_calling is not True:
                errors.append("Ollama tool probe did not produce a structured tool call")
                actions.append("Use Chat Only or choose an Ollama model that passes the tool probe.")
            if probe_timed_out:
                pass
            elif tool_round_trip is not True:
                errors.append("Ollama tool-result round trip has not been proven")
                actions.append("Use Chat Only or choose an Ollama model with native tool round-trip support.")
        elif tool_calling is True:
            tool_round_trip = True
            tool_calling_source = "ollama_catalog_hint"
            source = "catalog"
            confidence = "medium"
        elif tool_calling is False:
            tool_round_trip = False
            errors.append("Ollama model is not marked as tool-capable in catalog metadata")
            actions.append("Probe the model or use Chat Only.")
        else:
            errors.append("Ollama tool support is unknown")
            actions.append("Probe the model or use Chat Only.")
    elif tool_calling is True:
        tool_round_trip = True
    else:
        errors.append("structured tool calling is unknown")

    status_info = dict(status or provider_status(resolved.provider_id) or {})
    configured = bool(status_info.get("configured"))
    if resolved.provider_id == "ollama" and not status_info:
        configured = True
    credential_status = "configured" if configured else "missing"
    if not configured:
        errors.append("provider credentials or runtime are not configured")
        actions.append("Connect this provider in Settings -> Providers.")

    ready = not errors
    return AgentReadinessResult(
        ready=ready,
        provider_id=resolved.provider_id,
        model_id=resolved.model_id,
        runtime_model=resolved.runtime_model,
        selection_ref=resolved.selection_ref,
        transport=resolved.transport,
        context_window=context_window,
        tool_calling=tool_calling if tool_calling in (True, False) else None,
        tool_calling_source=tool_calling_source,
        tool_round_trip=tool_round_trip if tool_round_trip in (True, False) else None,
        streaming=streaming if streaming in (True, False) else None,
        streaming_tool_calling=streaming_tool_calling if streaming_tool_calling in (True, False) else None,
        credential_status=credential_status,
        capability_source=source,
        confidence=confidence,
        errors=errors,
        warnings=warnings,
        actions=_dedupe(actions),
    )


def evaluate_chat_readiness(
    value: str | ResolvedProviderConfig,
    provider_id: str | None = None,
    *,
    capability_snapshot: Mapping[str, Any] | None = None,
    status: Mapping[str, Any] | None = None,
    context_window_override: int | None = None,
) -> ChatReadinessResult:
    resolved = value if isinstance(value, ResolvedProviderConfig) else resolve_provider_config(
        str(value or ""),
        provider_id,
        allow_legacy_local=True,
    )
    errors: list[str] = []
    warnings: list[str] = []
    actions: list[str] = []

    override_context = _positive_int(context_window_override)
    if override_context:
        context_policy = None
        context_window = override_context
    else:
        context_window = None
    try:
        from row_bot.models import get_context_policy

        if context_window is None:
            context_policy = get_context_policy(resolved.selection_ref)
    except Exception:
        context_policy = None

    if context_window is None:
        context_window = _positive_int(getattr(context_policy, "effective_context", 0))
    if context_window is None:
        errors.append("context window could not be determined")
        actions.append("Re-probe the endpoint or choose a model with provider context metadata.")
    elif context_window < CHAT_ONLY_MIN_CONTEXT:
        errors.append(
            f"context window is {context_window:,} tokens; Chat Only requires at least 16,384 tokens"
        )
        actions.append("Increase the configured context or choose a larger-context model.")

    snapshot = _snapshot_for_resolved(resolved, capability_snapshot)
    normalized = normalize_snapshot(snapshot)
    if not normalized:
        errors.append("capability metadata is missing")
        actions.append("Refresh the provider catalog or re-probe the endpoint.")

    tasks = normalized.get("tasks", set())
    output_modalities = normalized.get("output_modalities", set())
    transport = normalized.get("transport") or resolved.transport.value
    streaming = normalized.get("streaming")
    source = _snapshot_source(snapshot, resolved)
    confidence = "high" if source in {"trusted_provider", "probe", "catalog"} else "low"

    if tasks and not tasks.intersection(CHAT_TASKS):
        errors.append("model does not expose a chat or responses generation task")
    if output_modalities and "text" not in output_modalities:
        errors.append("model does not produce text output")
    if transport not in AGENT_TRANSPORTS:
        errors.append(f"transport {transport or resolved.transport.value} is not supported for Chat Only")
    opencode_unsupported_reason = _opencode_unsupported_reason(resolved)
    if opencode_unsupported_reason:
        errors.append(opencode_unsupported_reason)
        actions.append("Choose an OpenCode model with chat, responses, or Anthropic Messages support.")

    if resolved.provider_id.startswith("custom_openai_"):
        endpoint = resolved.endpoint or {}
        probe = endpoint.get("last_probe") if isinstance(endpoint.get("last_probe"), dict) else {}
        if probe:
            streaming = probe.get("streaming_ok", streaming)
            source = "probe"
            confidence = "high" if probe.get("chat_ok") is True else "low"
            if probe.get("chat_ok") is not True:
                errors.append("chat completion has not been proven by endpoint probe")
                actions.append("Run the custom endpoint probe for this model.")
        else:
            confidence = "low"
            errors.append("custom endpoint chat capability has not been proven by endpoint probe")
            actions.append("Run the custom endpoint probe for this model.")
        if context_policy and getattr(context_policy, "cap_source", "") in {"profile_default", "heuristic"}:
            warnings.append("custom endpoint context is inferred; provider metadata or a manual context setting is safer")

    status_info = dict(status or provider_status(resolved.provider_id) or {})
    configured = bool(status_info.get("configured"))
    if resolved.provider_id == "ollama" and not status_info:
        configured = True
    credential_status = "configured" if configured else "missing"
    if not configured:
        errors.append("provider credentials or runtime are not configured")
        actions.append("Connect this provider in Settings -> Providers.")

    ready = not errors
    return ChatReadinessResult(
        ready=ready,
        provider_id=resolved.provider_id,
        model_id=resolved.model_id,
        runtime_model=resolved.runtime_model,
        selection_ref=resolved.selection_ref,
        transport=resolved.transport,
        context_window=context_window,
        streaming=streaming if streaming in (True, False) else None,
        credential_status=credential_status,
        capability_source=source,
        confidence=confidence,
        errors=errors,
        warnings=warnings,
        actions=_dedupe(actions),
    )


def evaluate_runtime_readiness(
    value: str | ResolvedProviderConfig,
    provider_id: str | None = None,
    *,
    capability_snapshot: Mapping[str, Any] | None = None,
    status: Mapping[str, Any] | None = None,
    context_window_override: int | None = None,
    probe_ollama_tools: bool = False,
) -> ModelRuntimeReadiness:
    resolved = value if isinstance(value, ResolvedProviderConfig) else resolve_provider_config(
        str(value or ""),
        provider_id,
        allow_legacy_local=True,
    )
    agent = evaluate_agent_readiness(
        resolved,
        capability_snapshot=capability_snapshot,
        status=status,
        context_window_override=context_window_override,
        probe_ollama_tools=probe_ollama_tools,
    )
    chat = evaluate_chat_readiness(
        resolved,
        capability_snapshot=capability_snapshot,
        status=status,
        context_window_override=context_window_override,
    )
    if agent.ready:
        return ModelRuntimeReadiness(agent=agent, chat=chat, selected_mode="agent", selection_reason="Agent Mode requirements are satisfied.")
    if chat.ready:
        if _agent_verification_inconclusive(agent):
            return ModelRuntimeReadiness(
                agent=agent,
                chat=chat,
                selected_mode="blocked",
                selection_reason="Agent Mode verification timed out; retry verification or choose a confirmed Agent-ready model.",
            )
        return ModelRuntimeReadiness(agent=agent, chat=chat, selected_mode="chat_only", selection_reason="Model can chat but is not Agent-ready.")
    reason = chat.errors[0] if chat.errors else (agent.errors[0] if agent.errors else "No supported runtime is available.")
    return ModelRuntimeReadiness(agent=agent, chat=chat, selected_mode="blocked", selection_reason=reason)


def _agent_verification_inconclusive(agent: AgentReadinessResult) -> bool:
    errors = " ".join(str(error).lower() for error in getattr(agent, "errors", []) or [])
    return "tool probe timed out" in errors or "verification is inconclusive" in errors


def ensure_agent_ready(value: str | ResolvedProviderConfig, provider_id: str | None = None) -> AgentReadinessResult:
    result = evaluate_agent_readiness(value, provider_id)
    if not result.ready:
        raise AgentCompatibilityError(result)
    return result


def _snapshot_for_resolved(
    resolved: ResolvedProviderConfig,
    capability_snapshot: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if isinstance(capability_snapshot, Mapping):
        return dict(capability_snapshot)
    if resolved.provider_id.startswith("custom_openai_"):
        endpoint = resolved.endpoint or {}
        models = endpoint.get("models") if isinstance(endpoint.get("models"), list) else []
        for model in models:
            if not isinstance(model, dict):
                continue
            if str(model.get("model_id") or model.get("id") or "") != resolved.runtime_model:
                continue
            snapshot = model.get("capabilities_snapshot")
            if isinstance(snapshot, Mapping):
                return dict(snapshot)
            return model_info_from_metadata(
                resolved.provider_id,
                resolved.runtime_model,
                model,
                context_window=int(model.get("context_window") or model.get("ctx") or 0),
                transport=resolved.transport,
            ).capability_snapshot()
        return model_info_from_metadata(
            resolved.provider_id,
            resolved.runtime_model,
            {},
            transport=resolved.transport,
        ).capability_snapshot()
    if resolved.provider_id == "ollama":
        try:
            from row_bot.providers.ollama import ollama_model_info

            return ollama_model_info(resolved.runtime_model).capability_snapshot()
        except Exception:
            pass
    if resolved.provider_id in {"opencode_zen", "opencode_go"}:
        try:
            from row_bot.providers.opencode import opencode_known_route, opencode_model_info

            route = opencode_known_route(resolved.provider_id, resolved.runtime_model)
            if route:
                return opencode_model_info(route).capability_snapshot()
        except Exception:
            pass
    cached = _cached_capability_snapshot_for_resolved(resolved)
    if cached:
        return cached
    return model_info_from_metadata(
        resolved.provider_id,
        resolved.runtime_model,
        {},
        transport=resolved.transport,
    ).capability_snapshot()


def _cached_capability_snapshot_for_resolved(resolved: ResolvedProviderConfig) -> dict[str, Any]:
    try:
        from row_bot.models import _cloud_model_cache

        info = _cloud_model_cache.get(resolved.selection_ref) or _cloud_model_cache.get(resolved.runtime_model)
    except Exception:
        return {}
    if not isinstance(info, Mapping):
        return {}
    provider = str(info.get("provider") or "")
    if provider and provider != resolved.provider_id:
        return {}
    snapshot = info.get("capabilities_snapshot")
    if isinstance(snapshot, Mapping) and snapshot:
        return dict(snapshot)
    return {}


def _snapshot_source(snapshot: Mapping[str, Any], resolved: ResolvedProviderConfig) -> str:
    if resolved.provider_id in TRUSTED_AGENT_PROVIDERS:
        return "trusted_provider"
    source_confidence = str(snapshot.get("source_confidence") or "")
    if source_confidence:
        return source_confidence
    return "catalog" if snapshot else "unknown"


def _opencode_unsupported_reason(resolved: ResolvedProviderConfig) -> str:
    if resolved.provider_id not in {"opencode_zen", "opencode_go"}:
        return ""
    try:
        from row_bot.providers.opencode import opencode_known_route

        route = opencode_known_route(resolved.provider_id, resolved.runtime_model)
    except Exception:
        route = None
    if route and route.unsupported_reason:
        return route.unsupported_reason
    if route is None:
        return (
            f"OpenCode model '{resolved.runtime_model}' has no supported route mapping "
            f"for provider '{resolved.provider_id}'."
        )
    return ""


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result
