from __future__ import annotations

import logging
import json
import re
from typing import Any

from providers.auth_store import delete_provider_secret, get_provider_secret, set_provider_secret
from providers.catalog import model_info_from_metadata, model_info_to_cache_entry
from providers.config import load_provider_config, save_provider_config
from providers.models import AuthMethod, ModelInfo, ProviderDefinition, TransportMode

CUSTOM_OPENAI_PREFIX = "custom_openai_"
DEFAULT_CUSTOM_ENDPOINT_PROFILE = "generic_openai"
DEFAULT_CUSTOM_ENDPOINT_CONTEXT_FALLBACK = 32_768
TOOL_PROBE_MAX_TOKENS = 1024
TOOL_ROUND_TRIP_PROBE_MAX_TOKENS = 256
STREAMING_PROBE_MAX_TOKENS = 128
logger = logging.getLogger(__name__)


class CustomEndpointModelRefreshResult(list):
    def __init__(
        self,
        infos: list[ModelInfo],
        *,
        stale_pin_count: int = 0,
        default_reset: bool = False,
    ) -> None:
        super().__init__(infos)
        self.stale_pin_count = int(stale_pin_count or 0)
        self.default_reset = bool(default_reset)


def _positive_int(value: Any, default: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


CUSTOM_ENDPOINT_PROFILES: dict[str, dict[str, Any]] = {
    "generic_openai": {
        "display_name": "OpenAI-compatible",
        "transport": TransportMode.OPENAI_CHAT.value,
        "system_message_mode": "system_first",
        "tool_history_mode": "native_required",
        "message_content_mode": "openai",
        "drop_unsupported_params": True,
        "supports_runtime_context_override": False,
        "context_param_name": "",
        "unknown_context_fallback": DEFAULT_CUSTOM_ENDPOINT_CONTEXT_FALLBACK,
    },
    "omlx": {
        "display_name": "oMLX",
        "transport": TransportMode.OPENAI_CHAT.value,
        "system_message_mode": "system_first",
        "tool_history_mode": "native_required",
        "message_content_mode": "string_text",
        "drop_unsupported_params": True,
        "supports_runtime_context_override": False,
        "context_param_name": "",
        "unknown_context_fallback": DEFAULT_CUSTOM_ENDPOINT_CONTEXT_FALLBACK,
    },
    "vllm": {
        "display_name": "vLLM",
        "transport": TransportMode.OPENAI_CHAT.value,
        "system_message_mode": "system_first",
        "tool_history_mode": "native_required",
        "message_content_mode": "openai",
        "drop_unsupported_params": True,
        "supports_runtime_context_override": True,
        "context_param_name": "max_model_len",
        "unknown_context_fallback": DEFAULT_CUSTOM_ENDPOINT_CONTEXT_FALLBACK,
    },
    "lmstudio": {
        "display_name": "LM Studio",
        "transport": TransportMode.OPENAI_CHAT.value,
        "system_message_mode": "system_first",
        "tool_history_mode": "native_required",
        "message_content_mode": "string_text",
        "drop_unsupported_params": True,
        "supports_runtime_context_override": False,
        "context_param_name": "",
        "unknown_context_fallback": DEFAULT_CUSTOM_ENDPOINT_CONTEXT_FALLBACK,
    },
    "llama_cpp": {
        "display_name": "llama.cpp",
        "transport": TransportMode.OPENAI_CHAT.value,
        "system_message_mode": "system_first",
        "tool_history_mode": "native_required",
        "message_content_mode": "string_text",
        "drop_unsupported_params": True,
        "supports_runtime_context_override": True,
        "context_param_name": "n_ctx",
        "unknown_context_fallback": DEFAULT_CUSTOM_ENDPOINT_CONTEXT_FALLBACK,
    },
    "localai": {
        "display_name": "LocalAI",
        "transport": TransportMode.OPENAI_CHAT.value,
        "system_message_mode": "system_first",
        "tool_history_mode": "native_required",
        "message_content_mode": "openai",
        "drop_unsupported_params": True,
        "supports_runtime_context_override": False,
        "context_param_name": "",
        "unknown_context_fallback": DEFAULT_CUSTOM_ENDPOINT_CONTEXT_FALLBACK,
    },
    "litellm": {
        "display_name": "LiteLLM",
        "transport": TransportMode.OPENAI_CHAT.value,
        "system_message_mode": "system_first",
        "tool_history_mode": "native_required",
        "message_content_mode": "openai",
        "drop_unsupported_params": True,
        "supports_runtime_context_override": False,
        "context_param_name": "",
        "unknown_context_fallback": DEFAULT_CUSTOM_ENDPOINT_CONTEXT_FALLBACK,
    },
    "sglang": {
        "display_name": "SGLang",
        "transport": TransportMode.OPENAI_CHAT.value,
        "system_message_mode": "system_first",
        "tool_history_mode": "native_required",
        "message_content_mode": "openai",
        "drop_unsupported_params": True,
        "supports_runtime_context_override": True,
        "context_param_name": "context_length",
        "unknown_context_fallback": DEFAULT_CUSTOM_ENDPOINT_CONTEXT_FALLBACK,
    },
}


def custom_endpoint_profile(profile_id: str | None) -> dict[str, Any]:
    profile = str(profile_id or DEFAULT_CUSTOM_ENDPOINT_PROFILE).strip().lower()
    return dict(CUSTOM_ENDPOINT_PROFILES.get(profile) or CUSTOM_ENDPOINT_PROFILES[DEFAULT_CUSTOM_ENDPOINT_PROFILE])


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
    profile = str(endpoint.get("profile") or endpoint.get("compatibility_profile") or DEFAULT_CUSTOM_ENDPOINT_PROFILE).strip().lower()
    profile_defaults = custom_endpoint_profile(profile)
    transport = str(endpoint.get("transport") or profile_defaults.get("transport") or TransportMode.OPENAI_CHAT.value)
    execution_location = str(endpoint.get("execution_location") or endpoint.get("privacy") or "remote")
    risk_label = str(endpoint.get("risk_label") or ("local_private" if execution_location == "local" else "custom_endpoint"))
    unknown_context_fallback = max(
        _positive_int(endpoint.get("unknown_context_fallback")),
        _positive_int(profile_defaults.get("unknown_context_fallback")),
        DEFAULT_CUSTOM_ENDPOINT_CONTEXT_FALLBACK,
    )
    system_message_mode = str(
        endpoint.get("system_message_mode")
        or profile_defaults.get("system_message_mode")
        or "system_first"
    )
    if profile == "litellm" and system_message_mode == "provider_default":
        system_message_mode = "system_first"

    normalized = {
        "id": endpoint_id,
        "provider_id": custom_provider_id(endpoint_id),
        "name": name,
        "display_name": name,
        "base_url": base_url,
        "profile": profile if profile in CUSTOM_ENDPOINT_PROFILES else DEFAULT_CUSTOM_ENDPOINT_PROFILE,
        "transport": transport,
        "auth_required": bool(endpoint.get("auth_required", bool(endpoint.get("api_key")))),
        "api_key_header": str(endpoint.get("api_key_header") or "Authorization"),
        "execution_location": execution_location,
        "risk_label": risk_label,
        "enabled": bool(endpoint.get("enabled", True)),
        "capability_probe": bool(endpoint.get("capability_probe", True)),
        "source_confidence": str(endpoint.get("source_confidence") or "user_configured"),
        "system_message_mode": system_message_mode,
        "tool_history_mode": _normalize_tool_history_mode(
            endpoint.get("tool_history_mode") or profile_defaults.get("tool_history_mode") or "native_required"
        ),
        "message_content_mode": str(endpoint.get("message_content_mode") or profile_defaults.get("message_content_mode") or "openai"),
        "drop_unsupported_params": bool(endpoint.get("drop_unsupported_params", profile_defaults.get("drop_unsupported_params", True))),
        "supports_runtime_context_override": bool(endpoint.get("supports_runtime_context_override", profile_defaults.get("supports_runtime_context_override", False))),
        "context_param_name": str(endpoint.get("context_param_name") or profile_defaults.get("context_param_name") or ""),
        "unknown_context_fallback": unknown_context_fallback,
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
    last_probe = endpoint.get("last_probe")
    if isinstance(last_probe, dict):
        normalized["last_probe"] = dict(last_probe)
    extra_body = endpoint.get("extra_body")
    if isinstance(extra_body, dict):
        normalized["extra_body"] = dict(extra_body)
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


def delete_custom_endpoint(endpoint_or_provider_id: str) -> int:
    endpoint_id = endpoint_id_from_provider_id(endpoint_or_provider_id)
    provider_id = custom_provider_id(endpoint_id)
    cfg = load_provider_config()
    removed_model_ids: set[str] = set()
    for item in cfg.get("custom_endpoints", []):
        if not isinstance(item, dict) or normalize_custom_endpoint(item).get("id") != endpoint_id:
            continue
        models = item.get("models") if isinstance(item.get("models"), list) else []
        removed_model_ids = {
            str(model.get("model_id") or model.get("id") or "")
            for model in models
            if isinstance(model, dict) and str(model.get("model_id") or model.get("id") or "")
        }
        break
    cfg["custom_endpoints"] = [
        item for item in cfg.get("custom_endpoints", [])
        if not isinstance(item, dict) or normalize_custom_endpoint(item).get("id") != endpoint_id
    ]
    save_provider_config(cfg)
    removed_pins = 0
    try:
        from providers.selection import remove_quick_choices_for_provider

        removed_pins = remove_quick_choices_for_provider(provider_id)
    except Exception:
        logger.debug("Failed to remove quick choices for custom provider %s", provider_id, exc_info=True)
    try:
        from models import reset_current_model_if_removed

        reset_current_model_if_removed(provider_id, removed_model_ids=removed_model_ids)
    except Exception:
        logger.debug("Failed to reset current model after deleting %s", provider_id, exc_info=True)
    delete_provider_secret(provider_id, "api_key")
    return removed_pins


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
    secret = custom_endpoint_secret(str(endpoint["provider_id"])) if endpoint.get("auth_required") else ""
    if secret:
        header_name = str(endpoint.get("api_key_header") or "Authorization")
        headers[header_name] = f"Bearer {secret}" if header_name.lower() == "authorization" else secret
    url = f"{str(endpoint['base_url']).rstrip('/')}/models"
    response = httpx.get(url, headers=headers, timeout=15)
    response.raise_for_status()
    native_metadata = _fetch_custom_endpoint_native_model_metadata(endpoint, headers=headers)
    infos = model_infos_from_openai_compatible_catalog(endpoint, response.json(), native_metadata_by_model=native_metadata)
    _store_custom_endpoint_models(endpoint["id"], infos)
    valid_model_ids = {info.model_id for info in infos}
    stale_pin_count = 0
    default_reset = False
    try:
        from providers.selection import remove_quick_choices_for_missing_models

        stale_pin_count = remove_quick_choices_for_missing_models(str(endpoint["provider_id"]), valid_model_ids)
    except Exception:
        logger.debug("Failed to prune stale quick choices for %s", endpoint["provider_id"], exc_info=True)
    try:
        previous_model_ids = {
            str(item.get("model_id") or item.get("id") or "")
            for item in (endpoint.get("models") if isinstance(endpoint.get("models"), list) else [])
            if isinstance(item, dict) and str(item.get("model_id") or item.get("id") or "")
        }
        removed_model_ids = previous_model_ids - valid_model_ids
        try:
            from models import get_current_model
            from providers.selection import parse_model_ref

            parsed_current = parse_model_ref(get_current_model())
            if (
                parsed_current
                and parsed_current[0] == str(endpoint["provider_id"])
                and parsed_current[1] not in valid_model_ids
            ):
                removed_model_ids.add(parsed_current[1])
        except Exception:
            pass
        if removed_model_ids:
            from models import reset_current_model_if_removed

            default_reset = reset_current_model_if_removed(str(endpoint["provider_id"]), removed_model_ids=removed_model_ids)
    except Exception:
        logger.debug("Failed to reset current model after refreshing %s", endpoint["provider_id"], exc_info=True)
    return CustomEndpointModelRefreshResult(
        infos,
        stale_pin_count=stale_pin_count,
        default_reset=default_reset,
    )


def probe_custom_endpoint(endpoint_or_provider_id: str, model_id: str | None = None) -> dict[str, Any]:
    endpoint = get_custom_endpoint(endpoint_or_provider_id)
    if not endpoint:
        raise ValueError("Custom endpoint not found.")
    if not endpoint.get("base_url"):
        raise ValueError("Custom endpoint is missing a base URL.")

    import httpx

    headers = _custom_endpoint_headers(endpoint)
    result: dict[str, Any] = {
        "ok": False,
        "agent_ok": False,
        "chat_only_ok": False,
        "classification": "unavailable",
        "models_ok": False,
        "chat_ok": False,
        "streaming_ok": None,
        "tool_calling": None,
        "tool_round_trip": None,
        "streaming_tool_calling": None,
        "context_window": 0,
        "profile": endpoint.get("profile") or DEFAULT_CUSTOM_ENDPOINT_PROFILE,
        "transport": endpoint.get("transport") or TransportMode.OPENAI_CHAT.value,
        "errors": [],
    }
    try:
        infos = refresh_custom_endpoint_models(endpoint_or_provider_id)
        result["models_ok"] = True
    except Exception as exc:
        infos = []
        result["errors"].append(f"models: {exc}")

    target_model = str(model_id or "")
    if not target_model and infos:
        target_model = infos[0].model_id
    if not target_model:
        result["errors"].append("chat: no model available to probe")
        _classify_probe_result(result)
        _store_custom_endpoint_probe(endpoint["id"], result)
        logger.warning(
            "Custom endpoint probe failed: id=%s base_url=%s profile=%s errors=%s",
            endpoint["id"],
            endpoint.get("base_url"),
            endpoint.get("profile"),
            "; ".join(str(error) for error in result.get("errors", [])) or "unknown error",
        )
        return result

    matched = next((info for info in infos if info.model_id == target_model), None)
    if matched and matched.context_window:
        result["context_window"] = int(matched.context_window)

    chat_url = f"{str(endpoint['base_url']).rstrip('/')}/chat/completions"
    body = {
        "model": target_model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
        "stream": False,
    }
    try:
        response = httpx.post(chat_url, headers=headers, json=body, timeout=20)
        response.raise_for_status()
        result["chat_ok"] = True
    except Exception as exc:
        result["errors"].append(f"chat: {exc}")

    tool_body = {
        "model": target_model,
        "messages": [{
            "role": "user",
            "content": "Call the thoth_probe_echo tool with value set to ok. Do not answer in text.",
        }],
        "tools": [_probe_tool_schema()],
        "tool_choice": "auto",
        "max_tokens": TOOL_PROBE_MAX_TOKENS,
        "stream": False,
    }
    tool_call_payload: dict[str, Any] | None = None
    try:
        response = httpx.post(chat_url, headers=headers, json=tool_body, timeout=30)
        response.raise_for_status()
        payload = response.json()
        tool_call_payload = _extract_structured_tool_call(payload, "thoth_probe_echo")
        result["tool_calling"] = tool_call_payload is not None
        if tool_call_payload is None:
            result["errors"].append("tools: no structured tool call returned")
    except Exception as exc:
        result["tool_calling"] = False
        result["errors"].append(f"tools: {exc}")

    if tool_call_payload is not None:
        call_id = str(tool_call_payload.get("id") or "call_thoth_probe_echo")
        round_trip_body = {
            "model": target_model,
            "messages": [
                tool_body["messages"][0],
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [tool_call_payload],
                },
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": "{\"value\":\"ok\"}",
                },
            ],
            "max_tokens": TOOL_ROUND_TRIP_PROBE_MAX_TOKENS,
            "stream": False,
        }
        try:
            response = httpx.post(chat_url, headers=headers, json=round_trip_body, timeout=30)
            response.raise_for_status()
            result["tool_round_trip"] = True
        except Exception as exc:
            result["tool_round_trip"] = False
            result["errors"].append(f"tool_round_trip: {exc}")
    stream_body = dict(body)
    stream_body["stream"] = True
    stream_body["max_tokens"] = STREAMING_PROBE_MAX_TOKENS
    try:
        saw_stream_event = False
        with httpx.stream("POST", chat_url, headers=headers, json=stream_body, timeout=20) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                text = line.decode("utf-8", errors="replace") if isinstance(line, bytes) else str(line or "")
                if _stream_line_has_usable_delta(text):
                    saw_stream_event = True
                    break
        result["streaming_ok"] = saw_stream_event
        if not saw_stream_event:
            result["errors"].append("streaming: no usable stream delta returned")
    except Exception as exc:
        result["streaming_ok"] = False
        result["errors"].append(f"streaming: {exc}")

    stream_tool_body = dict(tool_body)
    stream_tool_body["stream"] = True
    stream_tool_body["max_tokens"] = TOOL_PROBE_MAX_TOKENS
    try:
        with httpx.stream("POST", chat_url, headers=headers, json=stream_tool_body, timeout=30) as response:
            response.raise_for_status()
            tool_call_payload = _extract_streamed_structured_tool_call(response.iter_lines(), "thoth_probe_echo")
        result["streaming_tool_calling"] = tool_call_payload is not None
        if tool_call_payload is None:
            result["streaming_tool_error"] = "no streamed structured tool call returned"
            result["errors"].append("streaming_tools: no streamed structured tool call returned")
    except Exception as exc:
        result["streaming_tool_calling"] = False
        result["streaming_tool_error"] = str(exc)
        result["errors"].append(f"streaming_tools: {exc}")

    _classify_probe_result(result)
    _store_custom_endpoint_probe(endpoint["id"], result)
    if result["ok"]:
        logger.info(
            "Custom endpoint probe ok: id=%s base_url=%s models_ok=%s chat_ok=%s streaming_ok=%s tool_calling=%s streaming_tool_calling=%s",
            endpoint["id"],
            endpoint.get("base_url"),
            result.get("models_ok"),
            result.get("chat_ok"),
            result.get("streaming_ok"),
            result.get("tool_calling"),
            result.get("streaming_tool_calling"),
        )
    else:
        logger.warning(
            "Custom endpoint probe failed: id=%s base_url=%s profile=%s errors=%s",
            endpoint["id"],
            endpoint.get("base_url"),
            endpoint.get("profile"),
            "; ".join(str(error) for error in result.get("errors", [])) or "unknown error",
        )
    return result


def _classify_probe_result(result: dict[str, Any]) -> None:
    agent_ok = bool(
        result.get("chat_ok") is True
        and result.get("tool_calling") is True
        and result.get("tool_round_trip") is True
    )
    chat_only_ok = bool(result.get("chat_ok") is True and not agent_ok)
    result["agent_ok"] = agent_ok
    result["chat_only_ok"] = chat_only_ok
    result["classification"] = "agent_ready" if agent_ok else "chat_only" if chat_only_ok else "unavailable"
    # Preserve the existing meaning of ok: fully Agent-ready.
    result["ok"] = agent_ok


def _probe_tool_schema() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "thoth_probe_echo",
            "description": "Echo a probe value to verify structured tool calling.",
            "parameters": {
                "type": "object",
                "properties": {
                    "value": {"type": "string"},
                },
                "required": ["value"],
            },
        },
    }


def _extract_structured_tool_call(payload: dict[str, Any], expected_name: str) -> dict[str, Any] | None:
    choices = payload.get("choices") if isinstance(payload, dict) else []
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0] if isinstance(choices[0], dict) else {}
    message = first.get("message") if isinstance(first.get("message"), dict) else {}
    tool_calls = message.get("tool_calls")
    if not isinstance(tool_calls, list):
        return None
    for index, call in enumerate(tool_calls):
        if not isinstance(call, dict):
            continue
        function = call.get("function") if isinstance(call.get("function"), dict) else {}
        if str(function.get("name") or "") != expected_name:
            continue
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            try:
                parsed = json.loads(arguments or "{}")
            except json.JSONDecodeError:
                return None
            if not isinstance(parsed, dict):
                return None
        elif isinstance(arguments, dict):
            function = dict(function)
            function["arguments"] = json.dumps(arguments)
            call = dict(call)
            call["function"] = function
        else:
            return None
        normalized = dict(call)
        normalized.setdefault("id", f"call_thoth_probe_{index}")
        normalized.setdefault("type", "function")
        return normalized
    return None


def _extract_streamed_structured_tool_call(lines: Any, expected_name: str) -> dict[str, Any] | None:
    states: dict[int, dict[str, Any]] = {}
    order: list[int] = []
    for line in lines:
        payload = _decode_sse_payload(line)
        if not payload:
            continue
        choices = payload.get("choices") if isinstance(payload, dict) else []
        if isinstance(choices, list) and choices:
            choice = choices[0] if isinstance(choices[0], dict) else {}
            delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
            tool_calls = delta.get("tool_calls")
        else:
            message = payload.get("message") if isinstance(payload, dict) and isinstance(payload.get("message"), dict) else {}
            tool_calls = message.get("tool_calls")
        if not isinstance(tool_calls, list):
            continue
        for fallback_index, call in enumerate(tool_calls):
            if not isinstance(call, dict):
                continue
            if call.get("index") is None and len(order) == 1:
                index = order[0]
            else:
                try:
                    index = int(call.get("index") if call.get("index") is not None else fallback_index)
                except (TypeError, ValueError):
                    index = fallback_index
            if index not in states:
                states[index] = {"id": "", "type": "", "name": "", "arguments": []}
                order.append(index)
            state = states[index]
            if call.get("id") and not state["id"]:
                state["id"] = str(call.get("id"))
            if call.get("type") and not state["type"]:
                state["type"] = str(call.get("type"))
            function = call.get("function") if isinstance(call.get("function"), dict) else {}
            if function.get("name") and not state["name"]:
                state["name"] = str(function.get("name")).strip()
            if "arguments" in function:
                arguments = function.get("arguments")
                if not isinstance(arguments, str):
                    arguments = json.dumps(arguments if arguments is not None else {})
                state["arguments"].append(arguments)
    for index in order:
        state = states[index]
        if str(state.get("name") or "") != expected_name:
            continue
        arguments = "".join(state.get("arguments") or [])
        try:
            parsed = json.loads(arguments or "{}")
        except json.JSONDecodeError:
            return None
        if not isinstance(parsed, dict) or parsed.get("value") != "ok":
            return None
        return {
            "id": str(state.get("id") or f"call_thoth_probe_stream_{index}"),
            "type": str(state.get("type") or "function"),
            "function": {
                "name": expected_name,
                "arguments": json.dumps(parsed),
            },
        }
    return None


def _decode_sse_payload(line: Any) -> dict[str, Any] | None:
    text = line.decode("utf-8", errors="replace") if isinstance(line, bytes) else str(line or "")
    text = text.strip()
    if not text:
        return None
    if text.startswith("data:"):
        text = text[5:].strip()
    if not text or text == "[DONE]":
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _stream_line_has_usable_delta(line: str) -> bool:
    text = str(line or "").strip()
    if not text:
        return False
    if text.startswith("data:"):
        text = text[5:].strip()
    if not text or text == "[DONE]":
        return False
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return False
    choices = payload.get("choices") if isinstance(payload, dict) else []
    if isinstance(choices, list) and choices:
        choice = choices[0] if isinstance(choices[0], dict) else {}
        delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
        if delta.get("content") or delta.get("reasoning_content") or delta.get("reasoning") or delta.get("tool_calls"):
            return True
    message = payload.get("message") if isinstance(payload, dict) and isinstance(payload.get("message"), dict) else {}
    return bool(message.get("content") or message.get("thinking") or message.get("tool_calls"))


def _custom_endpoint_headers(endpoint: dict[str, Any]) -> dict[str, str]:
    headers = dict(endpoint.get("headers") or {})
    secret = custom_endpoint_secret(str(endpoint["provider_id"])) if endpoint.get("auth_required") else ""
    if secret:
        header_name = str(endpoint.get("api_key_header") or "Authorization")
        headers[header_name] = f"Bearer {secret}" if header_name.lower() == "authorization" else secret
    return headers


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


def _store_custom_endpoint_probe(endpoint_or_provider_id: str, probe: dict[str, Any]) -> None:
    endpoint_id = endpoint_id_from_provider_id(endpoint_or_provider_id)
    cfg = load_provider_config()
    endpoints = [item for item in cfg.get("custom_endpoints", []) if isinstance(item, dict)]
    for item in endpoints:
        if normalize_custom_endpoint(item).get("id") == endpoint_id:
            item["last_probe"] = dict(probe)
            break
    cfg["custom_endpoints"] = endpoints
    save_provider_config(cfg)


def model_infos_from_openai_compatible_catalog(
    endpoint: dict[str, Any],
    payload: dict[str, Any],
    *,
    native_metadata_by_model: dict[str, dict[str, Any]] | None = None,
) -> list[ModelInfo]:
    normalized = normalize_custom_endpoint(endpoint)
    try:
        transport = TransportMode(str(normalized.get("transport") or TransportMode.OPENAI_CHAT.value))
    except ValueError:
        transport = TransportMode.OPENAI_CHAT
    data = payload.get("data") if isinstance(payload, dict) else []
    if not isinstance(data, list):
        return []
    results: list[ModelInfo] = []
    native_metadata_by_model = native_metadata_by_model or {}
    for item in data:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("id") or "")
        if not model_id:
            continue
        metadata = dict(item)
        metadata.update(native_metadata_by_model.get(model_id) or {})
        _apply_native_capability_fields(metadata)
        manual = normalized.get("manual_capabilities")
        if isinstance(manual, dict):
            metadata.update(manual)
        context_window = _metadata_context_window(metadata, fallback=_positive_int(normalized.get("unknown_context_fallback")))
        if context_window:
            metadata["context_window"] = context_window
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


_CONTEXT_METADATA_KEYS = (
    "context_length",
    "context_window",
    "max_model_len",
    "max_context_length",
    "max_sequence_length",
    "n_ctx",
    "num_ctx",
)
_NESTED_CONTEXT_METADATA_KEYS = ("meta", "metadata", "details", "parameters", "model_info", "config")


def _metadata_context_window(metadata: dict[str, Any], *, fallback: int = 0) -> int:
    for key in _CONTEXT_METADATA_KEYS:
        value = _positive_int(metadata.get(key))
        if value > 0:
            return value
    for key in _NESTED_CONTEXT_METADATA_KEYS:
        nested = metadata.get(key)
        if isinstance(nested, dict):
            value = _metadata_context_window(nested, fallback=0)
            if value > 0:
                return value
    return _positive_int(fallback)


def _apply_native_capability_fields(metadata: dict[str, Any]) -> None:
    max_context = _metadata_context_window(metadata)
    if max_context > 0:
        metadata.setdefault("context_window", max_context)
    capabilities = metadata.get("capabilities")
    if isinstance(capabilities, dict):
        if capabilities.get("trained_for_tool_use") is True:
            metadata["tool_calling"] = True
        if capabilities.get("vision") is True:
            metadata["vision"] = True
    elif isinstance(capabilities, list) and any(str(capability).lower() in {"tool_use", "tools", "tool_calling"} for capability in capabilities):
        metadata["tool_calling"] = True


def _normalize_tool_history_mode(value: Any) -> str:
    mode = str(value or "").strip()
    if mode in {"flatten_for_non_tool_models", "native_or_drop", "native_when_supported"}:
        return "native_required"
    return mode or "native_required"


def _fetch_custom_endpoint_native_model_metadata(
    endpoint: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
) -> dict[str, dict[str, Any]]:
    profile = str(endpoint.get("profile") or "").lower()
    import httpx

    root_url = _native_root_url(str(endpoint.get("base_url") or ""))
    if not root_url:
        return {}
    if profile == "llama_cpp":
        try:
            response = httpx.get(f"{root_url}/props", headers=headers or {}, timeout=10)
            response.raise_for_status()
            metadata = _llamacpp_props_metadata_by_id(response.json())
            if metadata:
                return metadata
        except Exception:
            logger.debug("llama.cpp props metadata fetch failed for %s/props", root_url, exc_info=True)
        return {}
    if profile != "lmstudio":
        return {}
    for path in ("/api/v1/models", "/api/v0/models"):
        try:
            response = httpx.get(f"{root_url}{path}", headers=headers or {}, timeout=10)
            response.raise_for_status()
            metadata = _lmstudio_native_models_by_id(response.json())
            if metadata:
                return metadata
        except Exception:
            logger.debug("LM Studio native metadata fetch failed for %s%s", root_url, path, exc_info=True)
    return {}


def _native_root_url(base_url: str) -> str:
    value = str(base_url or "").rstrip("/")
    for suffix in ("/v1", "/api/v1", "/api/v0"):
        if value.endswith(suffix):
            return value[: -len(suffix)]
    return value


def _llamacpp_props_metadata_by_id(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if not isinstance(payload, dict):
        return {}
    model_id = str(payload.get("model_alias") or "").strip()
    if not model_id:
        return {}
    metadata: dict[str, Any] = {}
    settings = payload.get("default_generation_settings")
    if isinstance(settings, dict):
        metadata.update(settings)
        params = settings.get("params")
        if isinstance(params, dict):
            metadata.update({key: value for key, value in params.items() if key in _CONTEXT_METADATA_KEYS})
    for key in _CONTEXT_METADATA_KEYS:
        if key in payload:
            metadata[key] = payload[key]
    context_window = _metadata_context_window(metadata)
    if context_window:
        metadata["context_window"] = context_window
    return {model_id: metadata} if metadata else {}


def _lmstudio_native_models_by_id(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    models = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(models, list):
        models = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(models, list):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for item in models:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("id") or item.get("key") or "").strip()
        if not model_id:
            continue
        metadata = dict(item)
        _apply_native_capability_fields(metadata)
        result[model_id] = metadata
    return result
