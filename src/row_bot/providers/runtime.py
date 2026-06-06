from __future__ import annotations

from typing import Any

from row_bot.providers.capabilities import snapshot_supports_surface
from row_bot.providers.auth_store import get_provider_secret, provider_secret_status
from row_bot.providers.catalog import get_provider_definition, model_info_from_metadata
from row_bot.providers.custom import custom_endpoint_secret, get_custom_endpoint, is_custom_openai_provider


def is_provider_available(provider_id: str) -> bool:
    return bool(get_provider_secret(provider_id, "api_key"))


def list_configured_provider_ids() -> list[str]:
    configured = [
        provider_id for provider_id in (
            "openai",
            "ollama_cloud",
            "openrouter",
            "opencode_zen",
            "opencode_go",
            "anthropic",
            "google",
            "xai",
            "minimax",
        )
        if is_provider_available(provider_id)
    ]
    try:
        if provider_status("codex").get("configured"):
            configured.append("codex")
    except Exception:
        pass
    try:
        from row_bot.providers.custom import list_custom_endpoints
        configured.extend(str(endpoint["provider_id"]) for endpoint in list_custom_endpoints() if endpoint.get("enabled", True))
    except Exception:
        pass
    return configured


def provider_status(provider_id: str) -> dict:
    if provider_id == "codex":
        from row_bot.providers.codex import check_codex_token_health
        from row_bot.providers.codex import discover_codex_credentials
        from row_bot.providers.config import load_provider_config

        token_health = check_codex_token_health(refresh_if_needed=True)
        token_status = provider_secret_status("codex", "access_token")
        provider_cfg = load_provider_config().get("providers", {}).get("codex", {})
        external_configured = bool(
            provider_cfg.get("source") == "external_cli"
            and provider_cfg.get("external_reference_exists")
        )
        discovered = discover_codex_credentials()
        configured = bool(token_status.get("configured") or external_configured)
        source = ""
        if token_status.get("configured"):
            source = str(provider_cfg.get("source") or token_status.get("source") or "keyring")
        elif external_configured:
            source = "external_cli"
        elif provider_cfg.get("source") == "external_cli":
            source = "external_cli"
        elif discovered.get("exists") or discovered.get("cli_installed"):
            source = "external_cli_detected"
        return {
            "provider_id": provider_id,
            "configured": configured,
            "source": source,
            "fingerprint": token_status.get("fingerprint") or provider_cfg.get("fingerprint") or "",
            "auth_method": provider_cfg.get("auth_method") or "",
            "expires_at": provider_cfg.get("expires_at") or "",
            "account_id_hash": provider_cfg.get("account_id_hash") or "",
            "plan_type": provider_cfg.get("plan_type") or "",
            "external_reference_label": provider_cfg.get("external_reference_label") or discovered.get("label") or "",
            "external_reference_path_hash": provider_cfg.get("external_reference_path_hash") or discovered.get("path_hash") or "",
            "external_reference_exists": bool(provider_cfg.get("external_reference_exists") or discovered.get("exists")),
            "cli_installed": bool(discovered.get("cli_installed")),
            "runtime_enabled": token_health.runnable,
            "token_health": token_health.status,
            "token_health_detail": token_health.detail,
            "last_error": provider_cfg.get("last_error") or "",
        }
    if provider_id == "ollama":
        try:
            from row_bot.models import _ollama_reachable, list_local_models
            running = _ollama_reachable()
            count = len(list_local_models()) if running else 0
        except Exception:
            running = False
            count = 0
        return {
            "provider_id": provider_id,
            "configured": running,
            "source": "local_daemon" if running else "not_running",
            "fingerprint": "",
            "model_count": count,
        }
    if is_custom_openai_provider(provider_id):
        endpoint = get_custom_endpoint(provider_id)
        configured = bool(endpoint and endpoint.get("base_url") and endpoint.get("enabled", True))
        status = provider_secret_status(provider_id, "api_key")
        return {
            "provider_id": provider_id,
            "configured": configured and (not endpoint.get("auth_required") or bool(status.get("configured"))),
            "source": status.get("source") or ("no_auth" if endpoint and not endpoint.get("auth_required") else ""),
            "fingerprint": status.get("fingerprint") or "",
            "base_url": endpoint.get("base_url") if endpoint else "",
        }
    status = provider_secret_status(provider_id, "api_key")
    status["provider_id"] = provider_id
    return status


def create_chat_model(model_name: str, provider_id: str | None = None):
    """Create the LangChain chat model for an existing API-key provider."""
    from row_bot.providers.resolution import resolve_provider_config

    resolved = resolve_provider_config(
        model_name,
        provider_id,
        allow_legacy_local=provider_id is not None,
    )
    provider = resolved.provider_id
    model_name = resolved.runtime_model
    ensure_chat_model_compatible(model_name, provider)
    if provider == "ollama":
        from langchain_ollama import ChatOllama
        from row_bot.models import _ollama_base_url, _ollama_runtime_model_name, get_context_size
        from row_bot.providers.ollama import is_ollama_reasoning_model

        runtime_model = _ollama_runtime_model_name(resolved.selection_ref)
        kwargs = {
            "model": runtime_model,
            "base_url": _ollama_base_url(),
            "num_ctx": get_context_size(resolved.selection_ref),
        }
        if is_ollama_reasoning_model(runtime_model):
            kwargs["reasoning"] = True
        return ChatOllama(**kwargs)
    if provider == "ollama_cloud":
        from row_bot.providers.transports.ollama_cloud import ChatOllamaCloud

        api_key = get_provider_secret("ollama_cloud")
        if not api_key:
            raise ValueError("Ollama Cloud API key not configured. Set it in Settings -> Providers.")
        definition = get_provider_definition("ollama_cloud")
        base_url = definition.base_url if definition and definition.base_url else "https://ollama.com"
        return ChatOllamaCloud(model_name=model_name, api_key=api_key, base_url=base_url)
    if provider in {"opencode_zen", "opencode_go"}:
        from row_bot.providers.opencode import (
            OpenCodeUnsupportedRouteError,
            opencode_anthropic_base_url,
            opencode_base_url,
            opencode_model_route,
        )

        provider_label = "OpenCode Zen" if provider == "opencode_zen" else "OpenCode Go"
        api_key = get_provider_secret(provider)
        if not api_key:
            raise ValueError(f"{provider_label} API key not configured. Set it in Settings -> Providers.")
        try:
            route = opencode_model_route(provider, model_name)
        except OpenCodeUnsupportedRouteError:
            raise
        transport = route.transport
        if transport == "openai_chat" or transport.value == "openai_chat":
            from row_bot.providers.transports.openai_compatible import ChatOpenAICompatible

            return ChatOpenAICompatible(
                model_name=model_name,
                api_key=api_key,
                base_url=opencode_base_url(provider),
                endpoint={
                    "provider_id": provider,
                    "display_name": provider_label,
                    "base_url": opencode_base_url(provider),
                    "transport": transport.value,
                    "profile": "opencode",
                },
            )
        if transport == "openai_responses" or transport.value == "openai_responses":
            from langchain_openai import ChatOpenAI

            return ChatOpenAI(
                model=model_name,
                api_key=api_key,
                base_url=opencode_base_url(provider),
                use_responses_api=True,
                output_version="responses/v1",
            )
        if transport == "anthropic_messages" or transport.value == "anthropic_messages":
            from langchain_anthropic import ChatAnthropic

            return ChatAnthropic(
                model=model_name,
                api_key=api_key,
                base_url=opencode_anthropic_base_url(provider),
            )
        raise OpenCodeUnsupportedRouteError(
            f"OpenCode route for {provider_label} model '{model_name}' uses unsupported/deferred transport {transport.value}."
        )
    if is_custom_openai_provider(provider):
        from row_bot.providers.transports.openai_compatible import ChatOpenAICompatible
        endpoint = get_custom_endpoint(provider)
        if not endpoint or not endpoint.get("base_url"):
            raise ValueError("Custom OpenAI-compatible endpoint is missing a base URL.")
        api_key = custom_endpoint_secret(provider) or "not-needed"
        if endpoint.get("transport") == "openai_responses":
            from langchain_openai import ChatOpenAI

            kwargs = {
                "model": model_name,
                "api_key": api_key,
                "base_url": endpoint["base_url"],
            }
            headers = endpoint.get("headers")
            if isinstance(headers, dict) and headers:
                kwargs["default_headers"] = headers
            kwargs.update({"use_responses_api": True, "output_version": "responses/v1"})
            return ChatOpenAI(**kwargs)
        return ChatOpenAICompatible(
            model_name=model_name,
            api_key=api_key,
            base_url=str(endpoint["base_url"]),
            endpoint=endpoint,
        )
    if provider == "codex":
        from row_bot.providers.codex import codex_runtime_available
        from row_bot.providers.transports.codex_responses import ChatCodexResponses

        if not codex_runtime_available():
            raise ValueError(
                "Codex subscription runtime needs an in-app ChatGPT login with runnable OAuth tokens. "
                "Connect ChatGPT in Settings -> Providers, then try the Codex model again."
            )
        return ChatCodexResponses(model_name=model_name)
    if provider == "openai":
        from langchain_openai import ChatOpenAI
        api_key = get_provider_secret("openai")
        if not api_key:
            raise ValueError("OpenAI API key not configured. Set it in Settings → Providers.")
        kwargs = {"model": model_name, "api_key": api_key}
        if openai_model_uses_responses_api(model_name):
            kwargs.update({"use_responses_api": True, "output_version": "responses/v1"})
        return ChatOpenAI(**kwargs)
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        api_key = get_provider_secret("anthropic")
        if not api_key:
            raise ValueError("Anthropic API key not configured. Set it in Settings → Providers.")
        return ChatAnthropic(model=model_name, api_key=api_key)
    if provider == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI
        api_key = get_provider_secret("google")
        if not api_key:
            raise ValueError("Google AI API key not configured. Set it in Settings → Providers.")
        return ChatGoogleGenerativeAI(model=model_name, google_api_key=api_key)
    if provider == "xai":
        from langchain_xai import ChatXAI
        api_key = get_provider_secret("xai")
        if not api_key:
            raise ValueError("xAI API key not configured. Set it in Settings → Providers.")
        return ChatXAI(model=model_name, api_key=api_key)
    if provider == "minimax":
        from langchain_anthropic import ChatAnthropic
        api_key = get_provider_secret("minimax")
        if not api_key:
            raise ValueError("MiniMax API key not configured. Set it in Settings → Providers.")
        definition = get_provider_definition("minimax")
        api_url = definition.base_url if definition and definition.base_url else "https://api.minimax.io/anthropic"
        return ChatAnthropic(
            model=model_name,
            api_key=api_key,
            base_url=api_url,
        )

    from langchain_openrouter import ChatOpenRouter
    api_key = get_provider_secret("openrouter")
    if not api_key:
        raise ValueError("OpenRouter API key not configured. Set it in Settings → Providers.")
    return ChatOpenRouter(model_name=model_name, openrouter_api_key=api_key)


def ensure_chat_model_compatible(model_name: str, provider_id: str | None = None) -> None:
    provider = provider_id or _infer_provider(model_name)
    snapshot = _capability_snapshot_for_selection(model_name, provider)
    if snapshot and not snapshot_supports_surface(snapshot, "chat"):
        raise ValueError(
            f"{model_name} is not compatible with chat for provider {provider}. "
            "Choose a chat-capable Quick Choice from Settings -> Providers."
        )


def _capability_snapshot_for_selection(model_name: str, provider_id: str) -> dict[str, Any]:
    if is_custom_openai_provider(provider_id):
        endpoint = get_custom_endpoint(provider_id)
        models = endpoint.get("models") if isinstance(endpoint, dict) else []
        if isinstance(models, list):
            for model in models:
                if not isinstance(model, dict):
                    continue
                if str(model.get("model_id") or model.get("id") or "") != model_name:
                    continue
                snapshot = model.get("capabilities_snapshot")
                return dict(snapshot) if isinstance(snapshot, dict) else {}
        return {}
    if provider_id == "ollama":
        try:
            from row_bot.providers.ollama import ollama_model_info
            return ollama_model_info(model_name).capability_snapshot()
        except Exception:
            return {}
    cached = _cached_provider_capability_snapshot(provider_id, model_name)
    if cached:
        return cached
    return model_info_from_metadata(provider_id, model_name).capability_snapshot()


def _cached_provider_capability_snapshot(provider_id: str, model_name: str) -> dict[str, Any]:
    try:
        from row_bot.models import _cloud_model_cache

        cached = _cloud_model_cache.get(f"model:{provider_id}:{model_name}") or _cloud_model_cache.get(model_name)
    except Exception:
        return {}
    if not isinstance(cached, dict):
        return {}
    provider = str(cached.get("provider") or "")
    if provider and provider != provider_id:
        return {}
    snapshot = cached.get("capabilities_snapshot")
    if isinstance(snapshot, dict) and snapshot:
        return dict(snapshot)
    return {}


def _infer_provider(model_name: str) -> str:
    from row_bot.providers.resolution import resolve_provider_config

    return resolve_provider_config(model_name, allow_legacy_local=False).provider_id


def openai_model_uses_responses_api(model_name: str) -> bool:
    """Return True for direct OpenAI models that are not chat-completions native."""
    bare = str(model_name or "").split("/")[-1].strip().lower()
    return bare.startswith("gpt-5")
