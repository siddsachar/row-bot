from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from row_bot.providers.catalog import get_provider_definition, infer_provider_id
from row_bot.providers.models import TransportMode
from row_bot.providers.selection import model_ref, parse_model_ref, provider_display_label, provider_icon_label


@dataclass(frozen=True)
class ResolvedProviderConfig:
    selection_ref: str
    provider_id: str
    model_id: str
    runtime_model: str
    provider_display_name: str
    provider_icon: str = ""
    transport: TransportMode = TransportMode.OPENAI_CHAT
    base_url: str = ""
    execution_location: str = "remote"
    risk_label: str = "api_key"
    source: str = "inferred"
    endpoint_id: str = ""
    endpoint: dict[str, Any] = field(default_factory=dict)


def resolve_provider_config(
    value: str,
    provider_id: str | None = None,
    *,
    allow_legacy_local: bool = True,
) -> ResolvedProviderConfig:
    raw = str(value or "").strip()
    parsed = parse_model_ref(raw)
    if parsed:
        parsed_provider, model_id = parsed
        provider = provider_id or parsed_provider
        source = "provider_ref"
    else:
        model_id = raw
        provider = provider_id or infer_provider_id(model_id)
        source = "explicit_provider" if provider_id else "inferred"
        if not provider:
            if not allow_legacy_local:
                raise ValueError(
                    f"Provider is required for model '{model_id}'. Select a provider-backed model "
                    "instead of relying on implicit OpenRouter routing."
                )
            provider = "local"
            source = "legacy_local"

    provider = "ollama" if provider == "local" else str(provider or "")
    if not provider or not model_id:
        raise ValueError("Model selection is missing a provider or model id.")

    if provider.startswith("custom_openai_"):
        return _resolve_custom_openai(provider, model_id, source)

    definition = get_provider_definition(provider)
    transport = definition.default_transport if definition else TransportMode.OLLAMA_CHAT if provider == "ollama" else TransportMode.OPENAI_CHAT
    base_url = definition.base_url if definition else ""
    risk_label = definition.risk_label if definition else ("local_private" if provider == "ollama" else "api_key")
    if provider in {"opencode_zen", "opencode_go"}:
        try:
            from row_bot.providers.opencode import opencode_base_url, opencode_known_route

            base_url = opencode_base_url(provider)
            route = opencode_known_route(provider, model_id)
            if route:
                transport = route.transport
        except Exception:
            pass
    if provider == "ollama":
        try:
            from row_bot.providers.ollama import is_ollama_cloud_offload_model

            if is_ollama_cloud_offload_model(model_id):
                risk_label = "cloud_provider"
        except Exception:
            pass
    execution_location = "local" if risk_label == "local_private" else "remote"
    return ResolvedProviderConfig(
        selection_ref=model_ref(provider, model_id),
        provider_id=provider,
        model_id=model_id,
        runtime_model=model_id,
        provider_display_name=definition.display_name if definition else provider_display_label(provider),
        provider_icon=definition.icon if definition else provider_icon_label(provider),
        transport=transport,
        base_url=base_url,
        execution_location=execution_location,
        risk_label=risk_label,
        source=source,
    )


def _resolve_custom_openai(provider: str, model_id: str, source: str) -> ResolvedProviderConfig:
    from row_bot.providers.custom import endpoint_id_from_provider_id, get_custom_endpoint

    endpoint = get_custom_endpoint(provider) or {}
    try:
        transport = TransportMode(str(endpoint.get("transport") or TransportMode.OPENAI_CHAT.value))
    except ValueError:
        transport = TransportMode.OPENAI_CHAT
    return ResolvedProviderConfig(
        selection_ref=model_ref(provider, model_id),
        provider_id=provider,
        model_id=model_id,
        runtime_model=model_id,
        provider_display_name=str(endpoint.get("display_name") or endpoint.get("name") or provider_display_label(provider)),
        provider_icon=provider_icon_label(provider),
        transport=transport,
        base_url=str(endpoint.get("base_url") or ""),
        execution_location=str(endpoint.get("execution_location") or "remote"),
        risk_label=str(endpoint.get("risk_label") or "custom_endpoint"),
        source=source,
        endpoint_id=endpoint_id_from_provider_id(provider),
        endpoint=dict(endpoint),
    )
