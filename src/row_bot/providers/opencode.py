from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable

from row_bot.providers.models import ModelInfo, ModelModality, ModelTask, TransportMode
from row_bot.providers.selection import model_ref

OPENCODE_ZEN_PROVIDER_ID = "opencode_zen"
OPENCODE_GO_PROVIDER_ID = "opencode_go"
OPENCODE_PROVIDER_IDS = frozenset({OPENCODE_ZEN_PROVIDER_ID, OPENCODE_GO_PROVIDER_ID})

OPENCODE_ZEN_BASE_URL = "https://opencode.ai/zen/v1"
OPENCODE_GO_BASE_URL = "https://opencode.ai/zen/go/v1"


class OpenCodeUnsupportedRouteError(ValueError):
    """Raised when an OpenCode model is known but intentionally unsupported."""


@dataclass(frozen=True)
class OpenCodeModelRoute:
    provider_id: str
    model_id: str
    display_name: str
    transport: TransportMode
    context_window: int
    tool_calling: bool | None = True
    streaming: bool | None = True
    unsupported_reason: str = ""
    image_input: bool | None = None

    @property
    def selection_ref(self) -> str:
        return model_ref(self.provider_id, self.model_id)


_STALE_MODELS: dict[tuple[str, str], str] = {
    (OPENCODE_ZEN_PROVIDER_ID, "deepseek-v3.2"): "OpenCode Zen no longer lists deepseek-v3.2; use deepseek-v4-flash-free.",
    (OPENCODE_GO_PROVIDER_ID, "deepseek-v3.2"): "OpenCode Go no longer lists deepseek-v3.2; use deepseek-v4-pro or deepseek-v4-flash.",
    (OPENCODE_ZEN_PROVIDER_ID, "mimo-v2.5-pro"): "OpenCode Zen no longer lists mimo-v2.5-pro; this model is available through OpenCode Go.",
    (OPENCODE_ZEN_PROVIDER_ID, "codex-mini-latest"): "OpenCode Zen no longer lists codex-mini-latest; use a current gpt-*-codex model.",
}

_ZEN_STATIC_FALLBACK_IDS: tuple[str, ...] = (
    "claude-opus-4-8",
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-opus-4-5",
    "claude-opus-4-1",
    "claude-sonnet-4-6",
    "claude-sonnet-4-5",
    "claude-sonnet-4",
    "claude-haiku-4-5",
    "gpt-5.5",
    "gpt-5.5-pro",
    "gpt-5.4",
    "gpt-5.4-pro",
    "gpt-5.4-mini",
    "gpt-5.4-nano",
    "gpt-5.3-codex-spark",
    "gpt-5.3-codex",
    "gpt-5.2",
    "gpt-5.2-codex",
    "gpt-5.1",
    "gpt-5.1-codex-max",
    "gpt-5.1-codex",
    "gpt-5.1-codex-mini",
    "gpt-5",
    "gpt-5-codex",
    "gpt-5-nano",
    "grok-build-0.1",
    "glm-5.1",
    "glm-5",
    "minimax-m2.7",
    "minimax-m2.5",
    "kimi-k2.6",
    "kimi-k2.5",
    "qwen3.6-plus",
    "qwen3.5-plus",
    "big-pickle",
    "deepseek-v4-flash-free",
    "mimo-v2.5-free",
    "nemotron-3-super-free",
)

_GO_STATIC_FALLBACK_IDS: tuple[str, ...] = (
    "minimax-m2.7",
    "minimax-m2.5",
    "kimi-k2.6",
    "kimi-k2.5",
    "glm-5.1",
    "glm-5",
    "deepseek-v4-pro",
    "deepseek-v4-flash",
    "qwen3.7-max",
    "qwen3.6-plus",
    "qwen3.5-plus",
    "mimo-v2-pro",
    "mimo-v2-omni",
    "mimo-v2.5-pro",
    "mimo-v2.5",
)

_ZEN_CHAT_EXACT = {
    "big-pickle",
    "deepseek-v4-flash-free",
    "grok-build-0.1",
    "nemotron-3-super-free",
}
_ZEN_CHAT_PREFIXES = ("glm-", "kimi-", "minimax-", "mimo-")
_GO_CHAT_PREFIXES = ("glm-", "kimi-", "deepseek-", "mimo-")
_GO_MESSAGES_PREFIXES = ("minimax-", "qwen3.")

_CONTEXT_BY_PREFIX: tuple[tuple[str, int], ...] = (
    ("gpt-5.5", 400_000),
    ("gpt-5.4", 400_000),
    ("gpt-5.3", 400_000),
    ("gpt-5.2", 400_000),
    ("gpt-5.1", 400_000),
    ("gpt-5", 400_000),
    ("claude-", 200_000),
    ("minimax-", 204_800),
    ("gemini-", 1_048_576),
)


_MODELS_DEV_IMAGE_INPUT_IDS: dict[str, frozenset[str]] = {
    OPENCODE_ZEN_PROVIDER_ID: frozenset({
        "claude-3-5-haiku",
        "claude-haiku-4-5",
        "claude-opus-4-1",
        "claude-opus-4-5",
        "claude-opus-4-6",
        "claude-opus-4-7",
        "claude-opus-4-8",
        "claude-sonnet-4",
        "claude-sonnet-4-5",
        "claude-sonnet-4-6",
        "gpt-5",
        "gpt-5-codex",
        "gpt-5-nano",
        "gpt-5.1",
        "gpt-5.1-codex",
        "gpt-5.1-codex-max",
        "gpt-5.1-codex-mini",
        "gpt-5.2",
        "gpt-5.2-codex",
        "gpt-5.3-codex",
        "gpt-5.4",
        "gpt-5.4-mini",
        "gpt-5.4-nano",
        "gpt-5.4-pro",
        "gpt-5.5",
        "gpt-5.5-pro",
        "grok-build-0.1",
        "kimi-k2.5",
        "kimi-k2.5-free",
        "kimi-k2.6",
        "mimo-v2-omni-free",
        "mimo-v2.5-free",
        "qwen3.5-plus",
        "qwen3.6-plus",
        "qwen3.6-plus-free",
    }),
    OPENCODE_GO_PROVIDER_ID: frozenset({
        "kimi-k2.5",
        "kimi-k2.6",
        "mimo-v2-omni",
        "mimo-v2.5",
        "qwen3.5-plus",
        "qwen3.6-plus",
    }),
}


def _route_supports_image_input(route: OpenCodeModelRoute) -> bool:
    if route.unsupported_reason:
        return False
    if route.image_input is not None:
        return route.image_input
    lower = route.model_id.lower()
    return lower in _MODELS_DEV_IMAGE_INPUT_IDS.get(route.provider_id, frozenset())


def _with_image_input_metadata(
    route: OpenCodeModelRoute,
    image_input_model_ids: set[str] | None,
) -> OpenCodeModelRoute:
    if image_input_model_ids is None or route.unsupported_reason:
        return route
    return replace(route, image_input=route.model_id.lower() in image_input_model_ids)


def is_opencode_provider(provider_id: str | None) -> bool:
    return str(provider_id or "") in OPENCODE_PROVIDER_IDS


def opencode_base_url(provider_id: str) -> str:
    if provider_id == OPENCODE_ZEN_PROVIDER_ID:
        return OPENCODE_ZEN_BASE_URL
    if provider_id == OPENCODE_GO_PROVIDER_ID:
        return OPENCODE_GO_BASE_URL
    raise ValueError(f"Unknown OpenCode provider: {provider_id}")


def opencode_anthropic_base_url(provider_id: str) -> str:
    return opencode_base_url(provider_id).removesuffix("/v1")


def opencode_models_url(provider_id: str) -> str:
    return f"{opencode_base_url(provider_id)}/models"


def _display_name(model_id: str) -> str:
    special = {
        "glm": "GLM",
        "gpt": "GPT",
        "mimo": "MiMo",
        "qwen3.5": "Qwen3.5",
        "qwen3.6": "Qwen3.6",
        "qwen3.7": "Qwen3.7",
    }
    parts = str(model_id or "").replace("_", "-").split("-")
    labels: list[str] = []
    for index, part in enumerate(parts):
        joined = ".".join(parts[: index + 1])
        if joined in special:
            labels = [special[joined]]
            continue
        labels.append(special.get(part, part.upper() if len(part) <= 3 else part.capitalize()))
    return " ".join(labels).replace("V ", "V").replace("K ", "K")


def _context_window(model_id: str, transport: TransportMode) -> int:
    lower = str(model_id or "").lower()
    for prefix, context_window in _CONTEXT_BY_PREFIX:
        if lower.startswith(prefix):
            return context_window
    if transport == TransportMode.OPENAI_RESPONSES:
        return 400_000
    return 131_072


def _unsupported_route(provider_id: str, model_id: str, reason: str, *, transport: TransportMode = TransportMode.OPENAI_CHAT) -> OpenCodeModelRoute:
    return OpenCodeModelRoute(
        provider_id,
        model_id,
        _display_name(model_id),
        transport,
        _context_window(model_id, transport),
        tool_calling=False,
        unsupported_reason=reason,
    )


def classify_opencode_model_route(provider_id: str, model_id: str) -> OpenCodeModelRoute | None:
    provider_id = str(provider_id or "")
    model_id = str(model_id or "").strip()
    lower = model_id.lower()
    if not is_opencode_provider(provider_id) or not lower:
        return None

    stale_reason = _STALE_MODELS.get((provider_id, lower))
    if stale_reason:
        return _unsupported_route(provider_id, model_id, stale_reason)

    if lower.startswith("gemini-"):
        return _unsupported_route(
            provider_id,
            model_id,
            "OpenCode Gemini routes are deferred and not supported yet.",
            transport=TransportMode.GOOGLE_GENAI,
        )

    transport: TransportMode | None = None
    if provider_id == OPENCODE_ZEN_PROVIDER_ID:
        if lower.startswith("gpt-"):
            transport = TransportMode.OPENAI_RESPONSES
        elif lower.startswith("claude-") or lower in {"qwen3.6-plus", "qwen3.5-plus"}:
            transport = TransportMode.ANTHROPIC_MESSAGES
        elif lower in _ZEN_CHAT_EXACT or lower.startswith(_ZEN_CHAT_PREFIXES):
            transport = TransportMode.OPENAI_CHAT
    elif provider_id == OPENCODE_GO_PROVIDER_ID:
        if lower.startswith(_GO_MESSAGES_PREFIXES):
            transport = TransportMode.ANTHROPIC_MESSAGES
        elif lower.startswith(_GO_CHAT_PREFIXES):
            transport = TransportMode.OPENAI_CHAT

    if transport is None:
        return None
    return OpenCodeModelRoute(
        provider_id,
        model_id,
        _display_name(model_id),
        transport,
        _context_window(model_id, transport),
    )


def opencode_model_route(provider_id: str, model_id: str) -> OpenCodeModelRoute:
    route = classify_opencode_model_route(provider_id, str(model_id or ""))
    if not route:
        raise OpenCodeUnsupportedRouteError(
            f"OpenCode model '{model_id}' has no supported route mapping for provider '{provider_id}'."
        )
    if route.unsupported_reason:
        raise OpenCodeUnsupportedRouteError(route.unsupported_reason)
    return route


def opencode_model_transport(provider_id: str, model_id: str) -> TransportMode:
    return opencode_model_route(provider_id, model_id).transport


def opencode_known_route(provider_id: str, model_id: str) -> OpenCodeModelRoute | None:
    return classify_opencode_model_route(provider_id, str(model_id or ""))


def opencode_static_fallback_model_ids(provider_id: str) -> tuple[str, ...]:
    if provider_id == OPENCODE_ZEN_PROVIDER_ID:
        return _ZEN_STATIC_FALLBACK_IDS
    if provider_id == OPENCODE_GO_PROVIDER_ID:
        return _GO_STATIC_FALLBACK_IDS
    return ()


def list_opencode_model_routes(
    provider_id: str | None = None,
    *,
    include_unsupported: bool = True,
    model_ids: Iterable[str] | None = None,
    image_input_model_ids: Iterable[str] | None = None,
) -> list[OpenCodeModelRoute]:
    provider_ids: Iterable[str] = [provider_id] if provider_id else sorted(OPENCODE_PROVIDER_IDS)
    routes: list[OpenCodeModelRoute] = []
    image_input_lookup = (
        {str(model_id or "").strip().lower() for model_id in image_input_model_ids}
        if image_input_model_ids is not None
        else None
    )
    for item in provider_ids:
        ids = list(model_ids) if model_ids is not None else list(opencode_static_fallback_model_ids(str(item)))
        if include_unsupported:
            ids.extend(["gemini-3.5-flash", "gemini-3.1-pro", "gemini-3-flash"])
        for model_id in dict.fromkeys(str(model_id or "") for model_id in ids):
            route = classify_opencode_model_route(str(item), model_id)
            if not route:
                continue
            route = _with_image_input_metadata(route, image_input_lookup)
            if include_unsupported or not route.unsupported_reason:
                routes.append(route)
    return routes


def opencode_model_info(route: OpenCodeModelRoute) -> ModelInfo:
    tasks = {ModelTask.RESPONSES.value} if route.transport == TransportMode.OPENAI_RESPONSES else {ModelTask.CHAT.value}
    capabilities = {"text", "chat", "streaming"}
    input_modalities = {ModelModality.TEXT.value}
    if route.transport == TransportMode.OPENAI_RESPONSES:
        capabilities.add("responses")
    if route.tool_calling:
        capabilities.add("tool_calling")
    if _route_supports_image_input(route):
        input_modalities.add(ModelModality.IMAGE.value)
        capabilities.add("vision")
    if route.unsupported_reason:
        tasks = set()
        capabilities = {"unsupported"}
        input_modalities = {ModelModality.TEXT.value}
    return ModelInfo(
        provider_id=route.provider_id,
        model_id=route.model_id,
        display_name=route.display_name,
        context_window=route.context_window,
        transport=route.transport,
        capabilities=frozenset(capabilities),
        input_modalities=frozenset(input_modalities),
        output_modalities=frozenset({ModelModality.TEXT.value}),
        tasks=frozenset(tasks),
        tool_calling=route.tool_calling if not route.unsupported_reason else False,
        streaming=route.streaming,
        endpoint_compatibility=frozenset({route.transport}),
        risk_label="cloud_provider",
        source="opencode_catalog",
    )


def list_opencode_model_infos(
    provider_id: str | None = None,
    *,
    model_ids: Iterable[str] | None = None,
    image_input_model_ids: Iterable[str] | None = None,
) -> list[ModelInfo]:
    return [
        opencode_model_info(route)
        for route in list_opencode_model_routes(
            provider_id,
            include_unsupported=False,
            model_ids=model_ids,
            image_input_model_ids=image_input_model_ids,
        )
    ]


def opencode_route_diagnostics(provider_id: str, model_id: str) -> dict[str, object]:
    route = opencode_known_route(provider_id, model_id)
    transport = route.transport if route else None
    return {
        "provider_id": provider_id,
        "model_id": model_id,
        "selection_ref": model_ref(provider_id, model_id),
        "base_url": opencode_base_url(provider_id) if is_opencode_provider(provider_id) else "",
        "anthropic_base_url": opencode_anthropic_base_url(provider_id) if is_opencode_provider(provider_id) else "",
        "transport": transport.value if transport else "",
        "unsupported_reason": route.unsupported_reason if route else "OpenCode model has no supported route mapping.",
    }


def opencode_failure_diagnostics(provider_id: str, model_id: str, exc: BaseException) -> dict[str, object]:
    diagnostics = opencode_route_diagnostics(provider_id, model_id)
    text = str(exc or "")
    lower = text.lower()
    hint = ""
    if "model" in lower and "not supported" in lower:
        hint = "OpenCode says this model is not supported for the configured account or current route. Refresh the catalog and choose a currently listed OpenCode model."
    elif "401" in lower or "unauthorized" in lower or "api key" in lower or "auth" in lower:
        label = "OpenCode Zen" if provider_id == OPENCODE_ZEN_PROVIDER_ID else "OpenCode Go"
        hint = f"{label} authentication failed. Check the {provider_id} API key."
    elif "404" in lower or "not found" in lower:
        hint = "OpenCode returned 404; the configured model route may be stale or mapped to the wrong transport."
    elif diagnostics.get("unsupported_reason"):
        hint = str(diagnostics["unsupported_reason"])
    diagnostics.update({
        "error": text,
        "hint": hint,
    })
    return diagnostics
