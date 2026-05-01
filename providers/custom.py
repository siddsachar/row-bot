from __future__ import annotations

import re
from typing import Any

from providers.auth_store import delete_provider_secret, get_provider_secret, set_provider_secret
from providers.catalog import model_info_from_metadata, model_info_to_cache_entry
from providers.config import load_provider_config, save_provider_config
from providers.models import AuthMethod, ModelInfo, ProviderDefinition, TransportMode

CUSTOM_OPENAI_PREFIX = "custom_openai_"


def slugify_endpoint_id(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(value or "").strip().lower()).strip("-")
    return slug or "endpoint"


def custom_provider_id(endpoint_id: str) -> str:
    return f"{CUSTOM_OPENAI_PREFIX}{slugify_endpoint_id(endpoint_id)}"


def endpoint_id_from_provider_id(provider_id: str) -> str:
    provider_id = str(provider_id or "")
    if provider_id.startswith(CUSTOM_OPENAI_PREFIX):
        return provider_id.removeprefix(CUSTOM_OPENAI_PREFIX)
    return slugify_endpoint_id(provider_id)


def is_custom_openai_provider(provider_id: str | None) -> bool:
    return str(provider_id or "").startswith(CUSTOM_OPENAI_PREFIX)


def normalize_custom_endpoint(endpoint: dict[str, Any]) -> dict[str, Any]:
    endpoint_id = slugify_endpoint_id(str(endpoint.get("id") or endpoint.get("name") or "endpoint"))
    base_url = str(endpoint.get("base_url") or endpoint.get("url") or "").rstrip("/")
    name = str(endpoint.get("name") or endpoint.get("display_name") or endpoint_id)
    transport = str(endpoint.get("transport") or TransportMode.OPENAI_CHAT.value)
    execution_location = str(endpoint.get("execution_location") or endpoint.get("privacy") or "remote")
    risk_label = str(endpoint.get("risk_label") or ("local_private" if execution_location == "local" else "custom_endpoint"))
    normalized = {
        "id": endpoint_id,
        "provider_id": custom_provider_id(endpoint_id),
        "name": name,
        "display_name": name,
        "base_url": base_url,
        "transport": transport,
        "auth_required": bool(endpoint.get("auth_required", bool(endpoint.get("api_key")))),
        "api_key_header": str(endpoint.get("api_key_header") or "Authorization"),
        "execution_location": execution_location,
        "risk_label": risk_label,
        "enabled": bool(endpoint.get("enabled", True)),
        "capability_probe": bool(endpoint.get("capability_probe", True)),
        "source_confidence": str(endpoint.get("source_confidence") or "user_configured"),
    }
    manual = endpoint.get("manual_capabilities")
    if isinstance(manual, dict):
        normalized["manual_capabilities"] = dict(manual)
    headers = endpoint.get("headers")
    if isinstance(headers, dict):
        normalized["headers"] = {str(k): str(v) for k, v in headers.items()}
    models = endpoint.get("models")
    if isinstance(models, list):
        normalized["models"] = [dict(item) for item in models if isinstance(item, dict)]
    return normalized


def list_custom_endpoints() -> list[dict]:
    return [normalize_custom_endpoint(item) for item in load_provider_config().get("custom_endpoints", []) if isinstance(item, dict)]


def get_custom_endpoint(endpoint_or_provider_id: str) -> dict[str, Any] | None:
    endpoint_id = endpoint_id_from_provider_id(endpoint_or_provider_id)
    for endpoint in list_custom_endpoints():
        if endpoint.get("id") == endpoint_id or endpoint.get("provider_id") == endpoint_or_provider_id:
            return endpoint
    return None


def list_custom_provider_definitions() -> list[ProviderDefinition]:
    definitions: list[ProviderDefinition] = []
    for endpoint in list_custom_endpoints():
        if not endpoint.get("enabled", True):
            continue
        try:
            transport = TransportMode(str(endpoint.get("transport") or TransportMode.OPENAI_CHAT.value))
        except ValueError:
            transport = TransportMode.OPENAI_CHAT
        definitions.append(ProviderDefinition(
            id=str(endpoint["provider_id"]),
            display_name=str(endpoint.get("display_name") or endpoint["id"]),
            auth_methods=(AuthMethod.API_KEY, AuthMethod.NONE),
            default_transport=transport,
            base_url=str(endpoint.get("base_url") or ""),
            risk_label=str(endpoint.get("risk_label") or "custom_endpoint"),
            supports_catalog=True,
            experimental=True,
            icon="↔",
        ))
    return definitions


def save_custom_endpoint(endpoint: dict) -> None:
    cfg = load_provider_config()
    endpoints = [item for item in cfg.get("custom_endpoints", []) if isinstance(item, dict)]
    secret = str(endpoint.get("api_key") or "")
    normalized = normalize_custom_endpoint(endpoint)
    endpoint_id = normalized.get("id")
    endpoints = [item for item in endpoints if item.get("id") != endpoint_id]
    endpoints.append(normalized)
    cfg["custom_endpoints"] = endpoints
    save_provider_config(cfg)
    if secret:
        set_provider_secret(str(normalized["provider_id"]), "api_key", secret)


def delete_custom_endpoint(endpoint_or_provider_id: str) -> None:
    endpoint_id = endpoint_id_from_provider_id(endpoint_or_provider_id)
    provider_id = custom_provider_id(endpoint_id)
    cfg = load_provider_config()
    cfg["custom_endpoints"] = [
        item for item in cfg.get("custom_endpoints", [])
        if not isinstance(item, dict) or normalize_custom_endpoint(item).get("id") != endpoint_id
    ]
    save_provider_config(cfg)
    delete_provider_secret(provider_id, "api_key")


def custom_endpoint_secret(endpoint_or_provider_id: str) -> str:
    endpoint = get_custom_endpoint(endpoint_or_provider_id)
    provider_id = str(endpoint.get("provider_id") if endpoint else endpoint_or_provider_id)
    return get_provider_secret(provider_id, "api_key")


def custom_endpoint_models(endpoint_or_provider_id: str) -> list[dict[str, Any]]:
    endpoint = get_custom_endpoint(endpoint_or_provider_id)
    if not endpoint:
        return []
    models = endpoint.get("models")
    return [dict(item) for item in models if isinstance(item, dict)] if isinstance(models, list) else []


def custom_model_cache_entries() -> dict[str, dict[str, Any]]:
    entries: dict[str, dict[str, Any]] = {}
    for endpoint in list_custom_endpoints():
        for item in custom_endpoint_models(str(endpoint["id"])):
            model_id = str(item.get("model_id") or item.get("id") or "")
            if not model_id:
                continue
            entry = dict(item)
            entry.setdefault("label", entry.get("display_name") or model_id)
            entry.setdefault("ctx", int(entry.get("context_window") or 0))
            entry.setdefault("provider", endpoint["provider_id"])
            entry.setdefault("risk_label", endpoint.get("risk_label") or "custom_endpoint")
            entries[model_id] = entry
    return entries


def refresh_custom_endpoint_models(endpoint_or_provider_id: str) -> list[ModelInfo]:
    endpoint = get_custom_endpoint(endpoint_or_provider_id)
    if not endpoint:
        raise ValueError("Custom endpoint not found.")
    if not endpoint.get("base_url"):
        raise ValueError("Custom endpoint is missing a base URL.")

    import httpx

    headers = dict(endpoint.get("headers") or {})
    secret = custom_endpoint_secret(str(endpoint["provider_id"]))
    if secret:
        header_name = str(endpoint.get("api_key_header") or "Authorization")
        headers[header_name] = f"Bearer {secret}" if header_name.lower() == "authorization" else secret
    url = f"{str(endpoint['base_url']).rstrip('/')}/models"
    response = httpx.get(url, headers=headers, timeout=15)
    response.raise_for_status()
    infos = model_infos_from_openai_compatible_catalog(endpoint, response.json())
    _store_custom_endpoint_models(endpoint["id"], infos)
    return infos


def _store_custom_endpoint_models(endpoint_or_provider_id: str, infos: list[ModelInfo]) -> None:
    endpoint_id = endpoint_id_from_provider_id(endpoint_or_provider_id)
    cfg = load_provider_config()
    endpoints = [item for item in cfg.get("custom_endpoints", []) if isinstance(item, dict)]
    stored_models = []
    for info in infos:
        entry = model_info_to_cache_entry(info)
        entry.update({
            "id": info.model_id,
            "model_id": info.model_id,
            "display_name": info.display_name,
            "context_window": info.context_window,
        })
        stored_models.append(entry)
    for item in endpoints:
        if normalize_custom_endpoint(item).get("id") == endpoint_id:
            item["models"] = stored_models
            break
    cfg["custom_endpoints"] = endpoints
    save_provider_config(cfg)


def model_infos_from_openai_compatible_catalog(endpoint: dict[str, Any], payload: dict[str, Any]) -> list[ModelInfo]:
    normalized = normalize_custom_endpoint(endpoint)
    try:
        transport = TransportMode(str(normalized.get("transport") or TransportMode.OPENAI_CHAT.value))
    except ValueError:
        transport = TransportMode.OPENAI_CHAT
    data = payload.get("data") if isinstance(payload, dict) else []
    if not isinstance(data, list):
        return []
    results: list[ModelInfo] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("id") or "")
        if not model_id:
            continue
        metadata = dict(item)
        manual = normalized.get("manual_capabilities")
        if isinstance(manual, dict):
            metadata.update(manual)
        context_window = int(item.get("context_length") or item.get("max_model_len") or item.get("max_context_length") or 0)
        results.append(model_info_from_metadata(
            str(normalized["provider_id"]),
            model_id,
            metadata,
            display_name=str(item.get("name") or item.get("id") or model_id),
            context_window=context_window,
            transport=transport,
            risk_label=str(normalized.get("risk_label") or "custom_endpoint"),
            source="custom_openai_catalog",
        ))
    return results