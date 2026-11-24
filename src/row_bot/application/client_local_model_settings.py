"""Each local model's settings sheet (U1): its window, its Thinking, its reply limit and its sampling, whatever runs it.

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

Sampling goes with each request on every engine: the model card's preset for its family
(``providers.sampling_presets``, D2) unless the person chose the engine's own or their own values; a caller's value and
the endpoint's ``extra_body`` still win.

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
            "room_bytes": None, "choices": [{"tokens": size, "memory_bytes": None, "slow": False} for size in choices],
            "native": native}


def _built_in_context(model_id: str) -> dict[str, Any]:
    from row_bot.local_runtime import builds, hardware, models

    model = models.get(model_id)
    assert model is not None  # _identify checked it
    found = hardware.peek()
    metal = found.acceleration == builds.METAL
    automatic = models.context_for(model, found)
    on_card = not metal and models.fit(model, found) == "gpu"

    # What the model and its context take loaded (``need_bytes``: file, cache, working memory), out of the card's
    # memory, or the computer's where it runs on the processor or a Mac.
    room = found.gpu.memory_bytes if on_card and found.gpu is not None else found.ram_bytes
    return {"editable": True, "automatic": automatic, "automatic_memory_bytes": model.need_bytes(automatic),
            "memory_place": "card" if on_card else "memory", "room_bytes": room or None,
            "choices": [{"tokens": size, "memory_bytes": model.need_bytes(size),
                         "slow": metal and size > METAL_FAST_CONTEXT}
                        for size in models.context_choices(model, found)],
            "native": model.native_context}


def _sampling(provider_id: str, model_id: str, profile: str, saved: dict[str, Any], thinking: bool) -> dict[str, Any]:
    """The sheet's Sampling: the person's choice, the model card's values this server is sent, and which of them a
    reply gets unless a chat chooses (thinking or not)."""
    from row_bot.models import ENGINE_SAMPLING, SAMPLING_FIELDS
    from row_bot.providers import sampling_presets
    from row_bot.providers.reasoning import resolve_reasoning_capabilities

    own = saved.get("sampling")
    thinks = True  # without a Thinking control the request doesn't say, and the card's thinking values go
    if thinking:
        try:
            thinks = resolve_reasoning_capabilities(provider_id, model_id).default_enabled is not False
        except Exception:  # noqa: BLE001 - the model's own default: thinking, as every family here
            thinks = True
    unlisted = sampling_presets.unlisted_fields(profile)
    return {"value": "engine" if own == ENGINE_SAMPLING else "custom" if isinstance(own, dict) else "auto",
            "custom": dict(own) if isinstance(own, dict) else None,
            "card": sampling_presets.card_values(model_id, profile),
            "thinks": thinks,
            "fields": [name for name in SAMPLING_FIELDS if name not in unlisted]}


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
                   "room_bytes": None, "choices": [], "native": policy.native_limit_tokens}
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
    if thinking and endpoint is not None:
        from row_bot.providers.reasoning import local_thinking_default

        thinks_by_default = local_thinking_default(endpoint, model_id)
    else:
        thinks_by_default = None  # Ollama's template decides
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
                     "value": {"on": "on", "off": "off"}.get(saved.get("reasoning") if thinking else "", "auto"),
                     "automatic": thinks_by_default},
        "reply_limit": {"value": saved.get("reply_limit"), "automatic": automatic_reply,
                        "choices": [size for size in REPLY_LIMITS if not window or size < window]},
        "sampling": _sampling(provider_id, model_id, profile, saved, thinking),
    }
    validate()
    return result


def save_local_model_settings(model_ref: str, *, context: int | None, thinking: str, reply_limit: int | None,
                              validate: Callable[[], None], sampling: str = "keep",
                              sampling_values: dict[str, float | int | None] | None = None) -> dict[str, Any]:
    """Save the sheet (None and "auto" are Automatic) and apply it: Ollama and a server the person runs from the
    next request; Row-Bot's own server from its next start. ``sampling`` is "auto" (the model card), "engine",
    "custom" (``sampling_values``) or "keep" (what is saved)."""
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
    saved = models.person_local_model_settings(provider_id, model_id)
    if sampling == "keep":
        own_sampling = saved.get("sampling")
    elif sampling == "auto":
        own_sampling = None
    elif sampling == "engine":
        own_sampling = models.ENGINE_SAMPLING
    elif sampling == "custom":
        own_sampling = {name: value for name, value in (sampling_values or {}).items() if value is not None}
        if not own_sampling or set(own_sampling) - set(view["sampling"]["fields"]):
            raise ValueError("invalid_command")
    else:
        raise ValueError("invalid_command")
    before = saved.get("context")
    try:
        models.save_person_local_model_settings(provider_id, model_id, context=context,
                                                reasoning=None if thinking == "auto" else thinking,
                                                reply_limit=reply_limit, sampling=own_sampling)
    except ValueError as error:
        raise ValueError("invalid_command") from error
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
