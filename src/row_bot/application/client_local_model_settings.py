"""Each local model's settings sheet (U1): its window, its Thinking and its reply limit, whatever runs it.

One store keeps them (``models.local_model_settings``, keyed by provider and model). Each engine applies them its
own way:

- **Ollama**: the window is ``num_ctx`` on every request (a change reloads the model on the next one); Thinking and
  the reply limit (``num_predict``) go with each request. Without its own window a model gets the default every
  Ollama model has had (Automatic, 64K, or the fixed size chosen before U2).
- **Row-Bot's own llama.cpp** (Run on this computer): the window goes through ``local_runtime.models.context_for``
  into the presets' ``ctx-size``; only sizes that fit this computer are offered, and the server serves the new
  one from its next start (at once when nothing is being answered).
- **A server the person runs** (LM Studio, llama.cpp, vLLM, SGLang): the window is the server's, shown read-only
  with where to change it; LM Studio can load the model again at another size when the person asks. Thinking and
  the reply limit go with each request.

Learning's personal models are never shown here (their settings are Learning's). Reading never touches the network.
"""
from __future__ import annotations

from typing import Any, Callable, Literal

Engine = Literal["ollama", "built_in", "server"]

# Reply limits a person may choose; the window caps them.
REPLY_LIMITS = (4_096, 8_192, 16_384, 32_768)
# Sizes LM Studio may be asked to load a model at.
LM_STUDIO_SIZES = (16_384, 32_768, 65_536, 131_072, 262_144)
# On a Mac, a first reply on a long chat is slow past this window (measured on an M4, ``docs/LOCAL_RUNTIME.md``).
METAL_FAST_CONTEXT = 32_768


def _refused() -> ValueError:
    return ValueError("model_configuration_unavailable")


def _identify(model_ref: str) -> tuple[str, str, dict[str, Any] | None, Engine]:
    """(provider id, model id, endpoint, engine) of a local model the person may set; refuses any other."""
    from row_bot import local_runtime
    from row_bot.models import is_learning_local_entry, local_model_settings, local_settings_key
    from row_bot.providers.model_visibility import is_personal_model_endpoint, withheld
    from row_bot.providers.selection import parse_model_ref

    parsed = parse_model_ref(model_ref)
    if not parsed:
        raise _refused()
    provider_id, model_id = parsed
    if withheld(provider_id, model_id) or is_learning_local_entry(
            local_settings_key(provider_id, model_id), local_model_settings(model_id, provider_id)):
        raise _refused()  # Learning's: served as trained
    if provider_id == "ollama":
        return provider_id, model_id, None, "ollama"
    if not provider_id.startswith("custom_openai_"):
        raise _refused()
    from row_bot.providers.custom import get_custom_endpoint

    endpoint = get_custom_endpoint(provider_id)
    if (endpoint is None or is_personal_model_endpoint(endpoint)
            or str(endpoint.get("execution_location") or "") != "local"):
        raise _refused()
    if endpoint.get("managed") == local_runtime.OWNER:
        from row_bot.local_runtime import models

        if models.get(model_id) is None:
            raise _refused()
        return provider_id, model_id, endpoint, "built_in"
    if endpoint.get("managed"):
        raise _refused()
    return provider_id, model_id, endpoint, "server"


def _thinking_available(provider_id: str, model_id: str) -> bool:
    try:
        from row_bot.providers.reasoning import resolve_reasoning_capabilities

        found = resolve_reasoning_capabilities(provider_id, model_id)
    except Exception:  # noqa: BLE001 - no control known: none shown
        return False
    return bool(found and found.thinking_mode == "toggle" and found.can_disable and not found.mandatory)


def _ollama_context(model_id: str) -> dict[str, Any]:
    from row_bot import models

    native = models.get_model_max_context(f"model:ollama:{model_id}", allow_probe=False)
    native = int(native) if native else None
    automatic = models.get_user_context_size()
    if native:
        automatic = min(automatic, native)
    choices = [size for size in models.CONTEXT_SIZE_OPTIONS if not native or size <= native]
    return {"editable": True, "automatic": automatic, "automatic_memory_bytes": None, "memory_place": None,
            "choices": [{"tokens": size, "memory_bytes": None, "slow": False} for size in choices],
            "native": native}


def _built_in_context(model_id: str) -> dict[str, Any]:
    from row_bot.local_runtime import builds, hardware, models

    model = models.get(model_id)
    assert model is not None  # _identify checked it
    found = hardware.peek()
    metal = found.acceleration == builds.METAL
    automatic = models.context_for(model, found)
    on_card = not metal and models.fit(model, found) == "gpu"

    def memory(tokens: int) -> int:
        return model.kv_bytes_per_token * tokens

    return {"editable": True, "automatic": automatic, "automatic_memory_bytes": memory(automatic),
            "memory_place": "card" if on_card else "memory",
            "choices": [{"tokens": size, "memory_bytes": memory(size), "slow": metal and size > METAL_FAST_CONTEXT}
                        for size in models.context_choices(model, found)],
            "native": model.native_context}


def read_local_model_settings(model_ref: str, *, validate: Callable[[], None] = lambda: None) -> dict[str, Any]:
    """The sheet for one local model: what it reads and why, and what may be chosen. No network."""
    validate()
    from row_bot import local_runtime, models
    from row_bot.providers.transports.openai_compatible import LLAMA_CPP_REPLY_TOKEN_LIMIT

    provider_id, model_id, endpoint, engine = _identify(model_ref)
    saved = models.person_local_model_settings(provider_id, model_id)
    policy = models.get_context_policy(model_ref, allow_probe=False)
    window = policy.effective_limit_tokens
    if engine == "ollama":
        context = _ollama_context(model_id)
        name, engine_name, profile = model_id, "Ollama", "ollama"
    elif engine == "built_in":
        context = _built_in_context(model_id)
        name = local_runtime.model_label(model_id) or model_id
        engine_name, profile = local_runtime.DISPLAY_NAME, "llama_cpp"
    else:
        assert endpoint is not None
        profile = str(endpoint.get("profile") or "generic_openai")
        context = {"editable": False, "automatic": None, "automatic_memory_bytes": None, "memory_place": None,
                   "choices": [], "native": policy.native_limit_tokens}
        name, engine_name = model_id, str(endpoint.get("name") or "This server")
    reload_choices = [size for size in LM_STUDIO_SIZES if size != policy.native_limit_tokens] \
        if engine == "server" and profile == "lmstudio" else []
    if engine == "ollama":
        automatic_reply = window
    elif profile == "llama_cpp":
        automatic_reply = min(LLAMA_CPP_REPLY_TOKEN_LIMIT, window) if window else LLAMA_CPP_REPLY_TOKEN_LIMIT
    else:
        automatic_reply = None  # the server's own default
    thinking = _thinking_available(provider_id, model_id)
    result = {
        "schema_version": 1,
        "model_ref": model_ref,
        "name": name[:256],
        "engine": engine,
        "engine_name": engine_name[:256],
        "profile": profile[:64],
        "context": {
            **{key: value for key, value in context.items() if key != "native"},
            "chosen": saved.get("context") if context["editable"] else None,
            "current": window,
            "loaded": context["native"] if engine == "server" else None,
            "reload_choices": reload_choices,
        },
        "thinking": {"available": thinking,
                     "value": {"on": "on", "off": "off"}.get(saved.get("reasoning") if thinking else "", "auto")},
        "reply_limit": {"value": saved.get("reply_limit"), "automatic": automatic_reply,
                        "choices": [size for size in REPLY_LIMITS if not window or size < window]},
    }
    validate()
    return result


def save_local_model_settings(model_ref: str, *, context: int | None, thinking: str, reply_limit: int | None,
                              validate: Callable[[], None]) -> dict[str, Any]:
    """Save the sheet (None and "auto" are Automatic) and apply it: Ollama and a server the person runs from the
    next request; Row-Bot's own server from its next start."""
    validate()
    from row_bot import models

    provider_id, model_id, _endpoint, engine = _identify(model_ref)
    view = read_local_model_settings(model_ref, validate=validate)
    if context is not None and (not view["context"]["editable"]
                                or context not in {item["tokens"] for item in view["context"]["choices"]}):
        raise ValueError("invalid_command")
    if thinking not in {"auto", "on", "off"} or (thinking != "auto" and not view["thinking"]["available"]):
        raise ValueError("invalid_command")
    if reply_limit is not None and reply_limit not in view["reply_limit"]["choices"]:
        raise ValueError("invalid_command")
    before = models.person_local_model_settings(provider_id, model_id).get("context")
    models.save_person_local_model_settings(provider_id, model_id, context=context,
                                            reasoning=None if thinking == "auto" else thinking,
                                            reply_limit=reply_limit)
    applied = None
    if engine == "built_in" and before != context:
        from row_bot.local_runtime import server

        applied = server.apply_settings()
    result = read_local_model_settings(model_ref, validate=validate)
    if applied:
        result["applied"] = applied
    return result


def reload_in_lm_studio(model_ref: str, context: int, *, validate: Callable[[], None]) -> dict[str, Any]:
    """Load a model in LM Studio again at ``context`` tokens (the person pressed "Reload in LM Studio")."""
    validate()
    from row_bot.providers.custom import LmStudioReloadError, reload_lmstudio_model

    provider_id, model_id, endpoint, engine = _identify(model_ref)
    if engine != "server" or str((endpoint or {}).get("profile") or "") != "lmstudio" or \
            context not in LM_STUDIO_SIZES:
        raise ValueError("invalid_command")
    try:
        loaded = reload_lmstudio_model(provider_id, model_id, context, validate=validate)
        notice = f"LM Studio loaded it at {loaded // 1024}K."
    except LmStudioReloadError as error:
        notice = error.words
    return {**read_local_model_settings(model_ref, validate=validate), "notice": notice[:256]}
