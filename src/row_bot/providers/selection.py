from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any, Iterable

from row_bot.providers.capabilities import snapshot_supports_surface
from row_bot.providers.catalog import infer_provider_id
from row_bot.providers.config import load_provider_config, save_provider_config
from row_bot.providers.errors import NormalizedProviderError, normalize_provider_error

CHAT_VISIBILITY = ["chat", "workflow", "channels", "designer", "status_tool"]
DEFAULT_VISIBILITY = list(CHAT_VISIBILITY)
SURFACE_VISIBILITY: dict[str, list[str]] = {
    "chat": list(CHAT_VISIBILITY),
    "vision": ["vision"],
    "image": ["image"],
    "video": ["video"],
    "voice": ["voice"],
}
QUICK_CHOICE_SURFACE_GROUPS = [
    {"id": "chat", "display_name": "Chat"},
    {"id": "vision", "display_name": "Vision"},
    {"id": "image", "display_name": "Image"},
    {"id": "video", "display_name": "Video"},
    {"id": "voice", "display_name": "Voice"},
]
_PROVIDER_STATUS_PICKER_CACHE_TTL_SECONDS = 5.0
_provider_status_picker_cache: dict[str, tuple[float, dict[str, Any]]] = {}


@dataclass(frozen=True)
class ResolvedSelection:
    ref: str
    kind: str
    model_id: str = ""
    provider_id: str = ""
    route_id: str = ""
    display_name: str = ""
    legacy_value: str = ""
    active: bool = True
    reason: str = ""


@dataclass(frozen=True)
class CanonicalModelSelection:
    ref: str
    provider_id: str = ""
    model_id: str = ""
    display_label: str = ""
    active: bool = True
    reason: str = ""
    source: str = ""


@dataclass(frozen=True)
class CatalogModelResolution:
    ref: str
    provider_id: str = ""
    model_id: str = ""
    display_label: str = ""
    source: str = ""
    alternatives: tuple[str, ...] = ()


class ModelSelectionError(ValueError):
    """Raised when a model selection cannot be canonicalized safely."""


def resolve_catalog_model_selection(
    value: str | None,
    *,
    surface: str = "chat",
    allow_default: bool = False,
    require_agent_ready: bool = False,
    require_pinned: bool = False,
) -> CatalogModelResolution:
    """Strictly resolve a model selection without inventing a provider.

    This helper is for user-facing persistent writes and child-agent model
    selection. It reads configured quick choices, custom endpoint metadata, and
    the cached model catalog only; it does not probe providers or refresh live
    catalogs. When require_pinned is true, only configured Quick Choices are
    accepted.
    """
    raw = str(value or "").strip()
    if not raw or raw.lower() == "default":
        if allow_default:
            return CatalogModelResolution(ref="", display_label="Default", source="default")
        raise ModelSelectionError("Model selection is empty.")

    parsed = parse_model_ref(raw)
    candidates = _strict_catalog_model_candidates(surface=surface)
    if parsed:
        provider_id, model_id = parsed
        ref = model_ref(provider_id, model_id)
        exact = _unique_catalog_candidates(
            item for item in candidates if item["ref"].lower() == ref.lower()
        )
        if exact:
            if require_pinned and not _catalog_candidate_is_pinned(exact[0]):
                raise ModelSelectionError(_pinned_model_selection_message(raw, candidates, provider_id=provider_id))
            return _catalog_resolution_from_candidate(
                raw,
                exact[0],
                require_agent_ready=require_agent_ready,
            )
        if require_pinned:
            raise ModelSelectionError(_pinned_model_selection_message(raw, candidates, provider_id=provider_id))
        if _known_catalog_provider(provider_id):
            return CatalogModelResolution(
                ref=ref,
                provider_id=provider_id,
                model_id=model_id,
                display_label=format_model_choice_label(
                    provider_id,
                    model_id,
                    include_icon=False,
                ),
                source="provider_ref",
            )
        raise ModelSelectionError(
            f"Unknown model provider '{provider_id}' in '{raw}'. Choose a configured model."
        )

    lower = raw.lower()
    matches = _unique_catalog_candidates(
        item for item in candidates if lower in item["aliases"]
    )
    if require_pinned:
        pinned_matches = [item for item in matches if _catalog_candidate_is_pinned(item)]
        if matches and not pinned_matches:
            raise ModelSelectionError(_pinned_model_selection_message(raw, candidates))
        matches = pinned_matches
    if not matches:
        if require_pinned:
            raise ModelSelectionError(_pinned_model_selection_message(raw, candidates))
        raise ModelSelectionError(
            f"Model '{raw}' was not found in configured model choices. "
            "Use a provider-qualified model such as model:<provider_id>:<model_id> "
            "or pin the model in Settings -> Models first."
        )
    if len(matches) > 1:
        alternatives = tuple(_catalog_candidate_choice(item) for item in matches[:8])
        raise ModelSelectionError(
            f"Ambiguous model selection '{raw}'. Use one of: "
            f"{', '.join(alternatives)}."
        )
    return _catalog_resolution_from_candidate(
        raw,
        matches[0],
        require_agent_ready=require_agent_ready,
    )


def _known_catalog_provider(provider_id: str) -> bool:
    provider = _canonical_provider_id(provider_id)
    if not provider:
        return False
    try:
        from row_bot.providers.catalog import get_provider_definition

        if get_provider_definition(provider) is not None:
            return True
    except Exception:
        pass
    try:
        from row_bot.providers.custom import get_custom_endpoint

        return bool(get_custom_endpoint(provider))
    except Exception:
        return False


def _catalog_resolution_from_candidate(
    raw: str,
    candidate: dict[str, Any],
    *,
    require_agent_ready: bool,
) -> CatalogModelResolution:
    if candidate.get("active") is False:
        reason = str(candidate.get("reason") or "This model choice is inactive.")
        raise ModelSelectionError(f"Model '{raw}' is inactive. {reason}".strip())
    if require_agent_ready and str(candidate.get("runtime_mode") or "") == "chat_only":
        reason = str(candidate.get("reason") or "This model is Chat Only; tools and actions are off.")
        raise ModelSelectionError(f"Model '{raw}' is not available for agent runs. {reason}".strip())
    return CatalogModelResolution(
        ref=str(candidate["ref"]),
        provider_id=str(candidate["provider_id"]),
        model_id=str(candidate["model_id"]),
        display_label=str(candidate.get("display_label") or candidate.get("model_id") or ""),
        source=str(candidate.get("source") or "catalog"),
    )


def _catalog_candidate_choice(candidate: dict[str, Any]) -> str:
    label = str(candidate.get("display_label") or "")
    ref = str(candidate.get("ref") or "")
    return f"{label} ({ref})" if label and label != ref else ref


def _catalog_candidate_is_pinned(candidate: dict[str, Any]) -> bool:
    return str(candidate.get("source") or "") == "quick_choice"


def _pinned_model_suggestions(
    candidates: Iterable[dict[str, Any]],
    *,
    provider_id: str = "",
    limit: int = 5,
) -> tuple[str, ...]:
    provider = _canonical_provider_id(provider_id)
    pinned = [
        item
        for item in _unique_catalog_candidates(candidates)
        if _catalog_candidate_is_pinned(item) and item.get("active") is not False
    ]
    scoped = [
        item
        for item in pinned
        if provider and str(item.get("provider_id") or "") == provider
    ]
    choices = scoped or pinned
    return tuple(_catalog_candidate_choice(item) for item in choices[:limit])


def _pinned_model_selection_message(
    raw: str,
    candidates: Iterable[dict[str, Any]],
    *,
    provider_id: str = "",
) -> str:
    message = (
        f"Model '{raw}' is not pinned for Brain. "
        "Pin it in Settings -> Models first, or choose an existing pinned Brain choice."
    )
    suggestions = _pinned_model_suggestions(candidates, provider_id=provider_id)
    if suggestions:
        message += f" Active pinned Brain choices: {', '.join(suggestions)}."
    return message


def _unique_catalog_candidates(candidates: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    by_ref: dict[str, dict[str, Any]] = {}
    for item in candidates:
        ref = str(item.get("ref") or "")
        if ref and ref not in by_ref:
            by_ref[ref] = item
    return list(by_ref.values())


def pinned_model_choice_summaries(
    surface: str = "chat",
    *,
    include_inactive: bool = True,
    limit: int = 0,
) -> list[dict[str, Any]]:
    """Return compact, agent-readable pinned model choices for a surface."""
    summaries: list[dict[str, Any]] = []
    try:
        options = list_model_choice_options(surface, include_inactive=include_inactive)
    except Exception:
        return summaries
    for option in options:
        provider_id = _canonical_provider_id(str(option.get("provider_id") or ""))
        model_id = str(option.get("model_id") or "").strip()
        ref = str(option.get("value") or "").strip()
        if not provider_id or not model_id or not ref:
            continue
        active = option.get("active") is not False
        summaries.append({
            "ref": ref,
            "canonical_ref": ref,
            "config_value": f"{provider_id}/{model_id}",
            "label": str(option.get("label") or model_id),
            "display_name": str(option.get("display_name") or model_id),
            "provider_id": provider_id,
            "provider": provider_display_label(provider_id),
            "model_id": model_id,
            "active": active,
            "reason": str(option.get("reason") or ""),
            "source": str(option.get("source") or "quick_choice"),
        })
        if limit and len(summaries) >= limit:
            break
    return summaries


def _strict_catalog_model_candidates(*, surface: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []

    def add_candidate(
        *,
        provider_id: str,
        model_id: str,
        display_name: str = "",
        source: str,
        aliases: Iterable[str] = (),
        active: bool = True,
        reason: str = "",
        visibility: Iterable[str] | None = None,
        capabilities_snapshot: dict[str, Any] | None = None,
        runtime_mode: str = "",
    ) -> None:
        provider = _canonical_provider_id(provider_id)
        model = str(model_id or "").strip()
        if not provider or not model:
            return
        snapshot = dict(capabilities_snapshot or {})
        if not _catalog_candidate_supports_surface(
            surface,
            visibility=visibility,
            capabilities_snapshot=snapshot,
        ):
            return
        ref = model_ref(provider, model)
        label = format_model_choice_label(
            provider,
            model,
            display_name or model,
            include_icon=False,
        )
        alias_values = {
            ref,
            model,
            display_name or model,
            f"{provider}/{model}",
        }
        alias_values.update(str(item or "") for item in aliases)
        candidates.append({
            "ref": ref,
            "provider_id": provider,
            "model_id": model,
            "display_label": label,
            "source": source,
            "aliases": {item.strip().lower() for item in alias_values if item and item.strip()},
            "active": bool(active),
            "reason": str(reason or ""),
            "runtime_mode": str(runtime_mode or ""),
        })

    for choice in _configured_quick_model_choices():
        if choice.get("kind") != "model":
            continue
        provider_id = str(choice.get("provider_id") or "")
        model_id = str(choice.get("model_id") or "")
        display_name = str(choice.get("display_name") or model_id)
        add_candidate(
            provider_id=provider_id,
            model_id=model_id,
            display_name=display_name,
            source="quick_choice",
            aliases=(str(choice.get("id") or ""),),
            active=choice.get("active") is not False,
            reason=str(choice.get("inactive_reason") or choice.get("last_error") or ""),
            visibility=choice.get("visibility") if isinstance(choice.get("visibility"), list) else None,
            capabilities_snapshot=choice.get("capabilities_snapshot")
            if isinstance(choice.get("capabilities_snapshot"), dict)
            else None,
        )

    for provider_id, model_id, display_name in _custom_endpoint_model_matches_for_catalog():
        add_candidate(
            provider_id=provider_id,
            model_id=model_id,
            display_name=display_name,
            source="custom_endpoint_model",
        )

    for provider_id, model_id, display_name, snapshot in _cached_catalog_model_matches():
        add_candidate(
            provider_id=provider_id,
            model_id=model_id,
            display_name=display_name,
            source="cached_catalog",
            capabilities_snapshot=snapshot,
        )

    return _unique_catalog_candidates(candidates)


def _configured_quick_model_choices() -> list[dict[str, Any]]:
    try:
        return [
            dict(choice)
            for choice in load_provider_config().get("quick_choices", [])
            if isinstance(choice, dict)
        ]
    except Exception:
        return []


def _catalog_candidate_supports_surface(
    surface: str,
    *,
    visibility: Iterable[str] | None,
    capabilities_snapshot: dict[str, Any],
) -> bool:
    selected_surface = str(surface or "").strip()
    if not selected_surface:
        return True
    visible = {str(item) for item in (visibility or []) if str(item)}
    if visible and selected_surface not in visible:
        return False
    has_structured_snapshot = bool(
        capabilities_snapshot.get("tasks")
        or capabilities_snapshot.get("input_modalities")
        or capabilities_snapshot.get("output_modalities")
        or capabilities_snapshot.get("capabilities")
    )
    if has_structured_snapshot and not snapshot_supports_surface(capabilities_snapshot, selected_surface):
        return False
    return True


def _custom_endpoint_model_matches_for_catalog() -> list[tuple[str, str, str]]:
    matches: list[tuple[str, str, str]] = []
    try:
        from row_bot.providers.custom import custom_endpoint_models, list_custom_endpoints

        for endpoint in list_custom_endpoints():
            if endpoint.get("enabled") is False:
                continue
            provider_id = str(endpoint.get("provider_id") or "")
            if not provider_id:
                continue
            for item in custom_endpoint_models(provider_id):
                model_id = str(item.get("model_id") or item.get("id") or "")
                display_name = str(item.get("display_name") or item.get("label") or model_id)
                if model_id:
                    matches.append((provider_id, model_id, display_name))
    except Exception:
        return []
    return matches


def _cached_catalog_model_matches() -> list[tuple[str, str, str, dict[str, Any]]]:
    matches: list[tuple[str, str, str, dict[str, Any]]] = []
    try:
        from row_bot.providers.model_catalog_cache import read_model_catalog_cache

        snapshot = read_model_catalog_cache()
    except Exception:
        return []
    for key, info in snapshot.cloud_cache.items():
        if not isinstance(info, dict):
            continue
        parsed = parse_model_ref(str(key))
        provider_id = str(info.get("provider") or (parsed[0] if parsed else "") or "")
        model_id = str((parsed[1] if parsed else "") or info.get("model_id") or key)
        if not provider_id or not model_id:
            continue
        display = str(info.get("display_name") or info.get("label") or info.get("name") or model_id)
        snapshot_data = (
            info.get("capabilities_snapshot")
            if isinstance(info.get("capabilities_snapshot"), dict)
            else {}
        )
        matches.append((provider_id, model_id, display, dict(snapshot_data)))
    for row in snapshot.ollama_rows:
        model_id = str(row.get("model") or row.get("model_id") or row.get("name") or row.get("id") or "")
        if not model_id:
            continue
        display = str(row.get("display_name") or row.get("label") or model_id)
        snapshot_data = (
            row.get("capabilities_snapshot")
            if isinstance(row.get("capabilities_snapshot"), dict)
            else {}
        )
        matches.append(("ollama", model_id, display, dict(snapshot_data)))
    return matches


def model_selection_diagnostics(
    value: str | None,
    *,
    runtime_surface: str = "",
    runtime_mode: str = "",
    tools_bound: bool | None = None,
) -> dict[str, Any]:
    """Return support-friendly provider routing diagnostics for a model value."""
    raw = str(value or "").strip()
    diagnostics: dict[str, Any] = {
        "raw_stored_model_override": raw,
        "runtime_surface": runtime_surface,
        "runtime_mode": runtime_mode,
    }
    if tools_bound is not None:
        diagnostics["tools_bound"] = bool(tools_bound)
    if not raw:
        return diagnostics
    try:
        resolved_selection = resolve_selection(raw)
        if resolved_selection:
            diagnostics.update({
                "resolved_selection_ref": resolved_selection.ref,
                "selection_provider_id": resolved_selection.provider_id,
                "selection_model_id": resolved_selection.model_id,
                "selection_kind": resolved_selection.kind,
                "selection_active": resolved_selection.active,
                "selection_reason": resolved_selection.reason,
            })
        from row_bot.providers.resolution import resolve_provider_config

        resolved_provider = resolve_provider_config(raw, allow_legacy_local=True)
        diagnostics.update({
            "selection_ref": resolved_provider.selection_ref,
            "provider_id": resolved_provider.provider_id,
            "runtime_model": resolved_provider.runtime_model,
            "provider_display_name": resolved_provider.provider_display_name,
            "provider_source": resolved_provider.source,
        })
    except Exception as exc:
        diagnostics["resolve_error"] = str(exc)
    return diagnostics


def canonicalize_model_selection(
    value: str | None,
    surface: str = "chat",
    *,
    allow_default: bool = False,
) -> CanonicalModelSelection:
    """Canonicalize an input-boundary model value to ``model:<provider>:<model>``.

    Existing legacy bare values remain readable by lower-level runtime
    resolution. This helper is for new persistent writes where provider
    identity must not be lost.
    """
    raw = str(value or "").strip()
    if not raw:
        if allow_default:
            return CanonicalModelSelection(ref="", display_label="Default", source="default")
        raise ModelSelectionError("Model selection is empty.")
    if raw.lower() == "default":
        if allow_default:
            return CanonicalModelSelection(ref="", display_label="Default", source="default")
        raise ModelSelectionError("Default model selection is not allowed here.")

    parsed = parse_model_ref(raw)
    if parsed:
        provider_id, model_id = parsed
        try:
            from row_bot.providers.resolution import resolve_provider_config

            resolved = resolve_provider_config(raw, allow_legacy_local=False)
        except Exception as exc:
            raise ModelSelectionError(f"Invalid model selection '{raw}': {exc}") from exc
        return CanonicalModelSelection(
            ref=resolved.selection_ref,
            provider_id=resolved.provider_id,
            model_id=resolved.model_id,
            display_label=format_model_choice_label(
                resolved.provider_id,
                resolved.model_id,
                include_icon=False,
            ),
            active=True,
            source="provider_ref",
        )

    match = _canonical_quick_choice_match(raw, surface)
    if match:
        return match

    custom_matches = _custom_endpoint_model_matches(raw)
    if len(custom_matches) == 1:
        provider_id, model_id, display_name = custom_matches[0]
        ref = model_ref(provider_id, model_id)
        return CanonicalModelSelection(
            ref=ref,
            provider_id=provider_id,
            model_id=model_id,
            display_label=format_model_choice_label(provider_id, model_id, display_name, include_icon=False),
            active=True,
            source="custom_endpoint_model",
        )
    if len(custom_matches) > 1:
        refs = ", ".join(model_ref(provider_id, model_id) for provider_id, model_id, _ in custom_matches)
        raise ModelSelectionError(
            f"Ambiguous model selection '{raw}'. Use one of: {refs}."
        )

    provider_id = infer_provider_id(raw)
    if provider_id:
        provider_id = "ollama" if provider_id == "local" else provider_id
        return CanonicalModelSelection(
            ref=model_ref(provider_id, raw),
            provider_id=provider_id,
            model_id=raw,
            display_label=format_model_choice_label(provider_id, raw, include_icon=False),
            active=True,
            source="inferred_provider",
        )

    raise ModelSelectionError(
        f"Cannot infer a provider for model '{raw}'. Select a provider-qualified "
        "model such as model:<provider_id>:<model_id>."
    )


def _canonical_quick_choice_match(raw: str, surface: str) -> CanonicalModelSelection | None:
    lower = raw.lower()
    matches: list[dict[str, Any]] = []
    seen: set[str] = set()
    for choice in list_quick_choices(surface, include_inactive=True):
        if choice.get("kind") != "model" or not choice.get("model_id"):
            continue
        provider_id = str(choice.get("provider_id") or infer_provider_id(str(choice.get("model_id") or "")) or "")
        model_id = str(choice.get("model_id") or "")
        ref = model_choice_value(model_id, provider_id=provider_id)
        aliases = {
            str(choice.get("id") or "").lower(),
            ref.lower(),
            str(choice.get("display_name") or "").lower(),
            model_id.lower(),
        }
        if lower not in aliases or ref in seen:
            continue
        seen.add(ref)
        item = dict(choice)
        item["id"] = ref
        item["provider_id"] = provider_id
        item["model_id"] = model_id
        matches.append(item)
    if not matches:
        return None
    if len(matches) > 1:
        refs = ", ".join(str(choice.get("id") or "") for choice in matches)
        raise ModelSelectionError(
            f"Ambiguous model selection '{raw}'. Use one of: {refs}."
        )
    choice = matches[0]
    if choice.get("active") is False:
        reason = str(choice.get("inactive_reason") or "This Quick Choice is inactive.")
        raise ModelSelectionError(f"Model selection '{raw}' is inactive: {reason}")
    provider_id = str(choice.get("provider_id") or "")
    model_id = str(choice.get("model_id") or "")
    return CanonicalModelSelection(
        ref=str(choice.get("id") or model_ref(provider_id, model_id)),
        provider_id=provider_id,
        model_id=model_id,
        display_label=format_model_choice_label(
            provider_id,
            model_id,
            str(choice.get("display_name") or model_id),
            include_icon=False,
        ),
        active=True,
        reason=str(choice.get("inactive_reason") or ""),
        source="quick_choice",
    )


def _custom_endpoint_model_matches(raw: str) -> list[tuple[str, str, str]]:
    matches: list[tuple[str, str, str]] = []
    lower = raw.lower()
    try:
        from row_bot.providers.custom import custom_endpoint_models, list_custom_endpoints

        for endpoint in list_custom_endpoints():
            if endpoint.get("enabled") is False:
                continue
            provider_id = str(endpoint.get("provider_id") or "")
            if not provider_id:
                continue
            for item in custom_endpoint_models(provider_id):
                model_id = str(item.get("model_id") or item.get("id") or "")
                display_name = str(item.get("display_name") or item.get("label") or model_id)
                aliases = {model_id.lower(), display_name.lower()}
                if model_id and lower in aliases:
                    matches.append((provider_id, model_id, display_name))
    except Exception:
        return []
    return matches


def model_ref(provider_id: str, model_id: str) -> str:
    return f"model:{provider_id}:{model_id}"


def parse_model_ref(value: str | None) -> tuple[str, str] | None:
    raw = str(value or "").strip()
    if not raw.startswith("model:"):
        return None
    parts = raw.split(":", 2)
    if len(parts) != 3 or not parts[1] or not parts[2]:
        return None
    return _canonical_provider_id(parts[1]), parts[2]


def _canonical_provider_id(provider_id: str) -> str:
    provider = str(provider_id or "").strip()
    try:
        from row_bot.providers.xai_oauth import normalize_xai_oauth_provider_id

        provider = normalize_xai_oauth_provider_id(provider)
    except Exception:
        pass
    return "ollama" if provider == "local" else provider


def provider_display_label(provider_id: str) -> str:
    provider_id = str(provider_id or "")
    try:
        from row_bot.providers.catalog import list_provider_definitions

        for definition in list_provider_definitions():
            if definition.id == provider_id:
                return definition.display_name
    except Exception:
        pass
    if provider_id.startswith("custom_openai_"):
        return provider_id.replace("custom_openai_", "Custom ").replace("_", " ").title()
    if provider_id == "local":
        return "Local"
    return provider_id or "Provider"


_PROVIDER_ICON_LABELS: dict[str, str] = {
    "local": "Local",
    "ollama": "Local",
    "openai": "OpenAI",
    "claude_subscription": "Claude Subscription",
    "openrouter": "OpenRouter",
    "anthropic": "Anthropic",
    "google": "Google",
    "xai": "xAI",
    "xai_oauth": "xAI Grok",
    "minimax": "MiniMax",
}


def provider_icon_label(provider_id: str) -> str:
    provider_id = str(provider_id or "")
    return _PROVIDER_ICON_LABELS.get(provider_id, "")


def model_choice_value(value: str | None, *, provider_id: str | None = None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed = parse_model_ref(raw)
    if parsed:
        provider, model_id = parsed
    else:
        provider = _canonical_provider_id(provider_id) if provider_id else infer_provider_id(raw) or "local"
        model_id = raw
    provider = _canonical_provider_id(provider)
    return model_ref(provider, model_id)


def model_id_from_choice_value(value: str | None) -> str:
    raw = str(value or "").strip()
    parsed = parse_model_ref(raw)
    return parsed[1] if parsed else raw


def provider_id_from_choice_value(value: str | None) -> str:
    raw = str(value or "").strip()
    parsed = parse_model_ref(raw)
    if parsed:
        return parsed[0]
    provider = infer_provider_id(raw) or "local"
    return _canonical_provider_id(provider)


def format_model_choice_label(
    provider_id: str,
    model_id: str,
    display_name: str | None = None,
    *,
    include_icon: bool = True,
) -> str:
    provider = str(provider_id or "local")
    name = str(display_name or model_id or "").strip() or str(model_id or "")
    icon = provider_icon_label(provider) if include_icon else ""
    prefix = f"{icon} " if icon else ""
    return f"{prefix}{name} - {provider_display_label(provider)}"


def _model_choice_option_for_value(
    value: str,
    *,
    surface: str = "chat",
    include_inactive: bool = False,
) -> dict[str, Any] | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    parsed = parse_model_ref(raw)
    if parsed:
        provider_id, model_id = parsed
    else:
        provider_id = infer_provider_id(raw) or "local"
        model_id = raw
    value_ref = model_choice_value(model_id, provider_id=provider_id)
    option = {
        "value": value_ref,
        "label": format_model_choice_label(provider_id, model_id, include_icon=False),
        "provider_id": provider_id,
        "model_id": model_id,
        "display_name": model_id,
        "source": "included_value",
    }
    if surface:
        ref = model_ref(provider_id, model_id)
        snapshot = _selection_capability_snapshot(provider_id, model_id)
        if snapshot and not provider_id.startswith("custom_openai_") and not snapshot_supports_surface(snapshot, surface):
            reason = _surface_unsupported_reason(surface)
            if not include_inactive:
                return None
            option.update({"active": False, "reason": reason})
            option["label"] = f"Unavailable: {option['label']}"
            return option
        if provider_id.startswith("custom_openai_"):
            try:
                from row_bot.providers.custom import custom_endpoint_models, get_custom_endpoint

                endpoint = get_custom_endpoint(provider_id) or {}
                manual = endpoint.get("manual_capabilities")
                if surface == "vision" and isinstance(manual, dict) and manual.get("vision") is False:
                    reason = "manual vision capability disabled"
                    if not include_inactive:
                        return None
                    option.update({"active": False, "reason": reason})
                    option["label"] = f"Unavailable: {option['label']}"
                    return option
                for item in custom_endpoint_models(provider_id):
                    if str(item.get("model_id") or item.get("id") or "") != model_id:
                        continue
                    snapshot = item.get("capabilities_snapshot") if isinstance(item.get("capabilities_snapshot"), dict) else {}
                    if snapshot and not snapshot_supports_surface(snapshot, surface):
                        reason = _surface_unsupported_reason(surface)
                        if not include_inactive:
                            return None
                        option.update({"active": False, "reason": reason})
                        option["label"] = f"Unavailable: {option['label']}"
                    break
            except Exception:
                pass
        for choice in load_provider_config().get("quick_choices", []):
            if not isinstance(choice, dict) or choice.get("id") != ref:
                continue
            inactive_reason = _surface_inactive_reason(choice, surface)
            snapshot = choice.get("capabilities_snapshot") if isinstance(choice.get("capabilities_snapshot"), dict) else {}
            if inactive_reason or (snapshot and not snapshot_supports_surface(snapshot, surface)):
                reason = inactive_reason or _surface_unsupported_reason(surface)
                if not include_inactive:
                    return None
                option.update({
                    "active": False,
                    "reason": reason,
                })
                option["label"] = f"Unavailable: {option['label']}"
            break
    return option


def list_model_choice_options(
    surface: str = "chat",
    *,
    include_values: Iterable[str] | None = None,
    include_inactive: bool = False,
    return_diagnostics: bool = False,
) -> list[dict[str, Any]] | tuple[list[dict[str, Any]], dict[str, Any]]:
    started = time.perf_counter()
    include_values_list = list(include_values or [])
    diagnostics: dict[str, Any] = {
        "surface": surface,
        "include_inactive": include_inactive,
        "include_values_count": len(include_values_list),
    }
    options: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_option(option: dict[str, Any] | None) -> None:
        if not option:
            return
        value = str(option.get("value") or "")
        if not value or value in seen:
            return
        seen.add(value)
        options.append(option)

    quick_started = time.perf_counter()
    quick_result = list_quick_choices(
        surface,
        include_inactive=include_inactive,
        return_diagnostics=return_diagnostics,
    )
    if return_diagnostics:
        quick_choices, quick_diagnostics = quick_result
        diagnostics.update({
            f"quick_choices_{key}": value
            for key, value in quick_diagnostics.items()
        })
    else:
        quick_choices = quick_result
    diagnostics["quick_choices_ms"] = (time.perf_counter() - quick_started) * 1000.0
    option_build_started = time.perf_counter()
    for choice in quick_choices:
        if choice.get("kind") != "model" or not choice.get("model_id"):
            continue
        provider_id = str(choice.get("provider_id") or infer_provider_id(str(choice.get("model_id") or "")) or "local")
        model_id = str(choice.get("model_id") or "")
        value = model_choice_value(model_id, provider_id=provider_id)
        add_option({
            "value": value,
            "label": format_model_choice_label(
                provider_id,
                model_id,
                str(choice.get("display_name") or model_id),
                include_icon=False,
            ),
            "provider_id": provider_id,
            "model_id": model_id,
            "display_name": str(choice.get("display_name") or model_id),
            "source": str(choice.get("source") or "quick_choice"),
            "active": choice.get("active") is not False,
            "reason": str(choice.get("inactive_reason") or ""),
        })

    for value in include_values_list:
        add_option(_model_choice_option_for_value(
            str(value or ""),
            surface=surface,
            include_inactive=include_inactive,
        ))

    diagnostics["option_build_ms"] = (time.perf_counter() - option_build_started) * 1000.0
    diagnostics["total_ms"] = (time.perf_counter() - started) * 1000.0
    diagnostics["options"] = len(options)
    if return_diagnostics:
        return options, diagnostics
    return options


def model_choice_options_map(
    surface: str = "chat",
    *,
    include_values: Iterable[str] | None = None,
    include_inactive: bool = False,
) -> dict[str, str]:
    return {
        str(option["value"]): str(option["label"])
        for option in list_model_choice_options(surface, include_values=include_values, include_inactive=include_inactive)
    }


def route_ref(route_id: str) -> str:
    return f"route:{route_id}"


def _quick_choice_for_model(
    model_id: str,
    *,
    provider_id: str | None = None,
    display_name: str | None = None,
    source: str = "manual",
    capabilities_snapshot: dict[str, Any] | None = None,
    visibility: Iterable[str] | None = None,
) -> dict[str, Any] | None:
    provider_id = _canonical_provider_id(provider_id) if provider_id else infer_provider_id(model_id)
    if not provider_id:
        return None
    try:
        from row_bot.providers.catalog import get_provider_definition
        definition = get_provider_definition(provider_id)
        risk_label = definition.risk_label if definition else "custom_endpoint" if provider_id.startswith("custom_openai_") else "api_key"
    except Exception:
        risk_label = "custom_endpoint" if provider_id.startswith("custom_openai_") else "api_key"
    return {
        "id": model_ref(provider_id, model_id),
        "kind": "model",
        "provider_id": provider_id,
        "model_id": model_id,
        "display_name": display_name or model_id,
        "visibility": list(visibility or DEFAULT_VISIBILITY),
        "pinned": True,
        "order": 1000,
        "recommended": source != "manual",
        "source": source,
        "capabilities_snapshot": dict(capabilities_snapshot or {}),
        "risk_label": risk_label,
        "active": True,
        "inactive_reason": "",
        "inactive_surfaces": {},
        "last_validated_at": "",
        "last_error": "",
    }


def _provider_status_for_picker(provider_id: str, diagnostics: dict[str, Any] | None = None) -> dict[str, Any]:
    cache = None
    if diagnostics is not None:
        cache = diagnostics.setdefault("_provider_status_cache", {})
        if provider_id in cache:
            return dict(cache[provider_id])
    global_cached = _provider_status_picker_cache.get(provider_id)
    now = time.monotonic()
    if global_cached is not None:
        cached_at, cached_status = global_cached
        if now - cached_at <= _PROVIDER_STATUS_PICKER_CACHE_TTL_SECONDS:
            if cache is not None:
                cache[provider_id] = dict(cached_status)
            return dict(cached_status)
    started = time.perf_counter()
    try:
        from row_bot.providers.runtime import provider_status

        try:
            status = dict(provider_status(provider_id, refresh_tokens=False) or {})
        except TypeError:
            status = dict(provider_status(provider_id) or {})
    finally:
        if diagnostics is not None:
            diagnostics["provider_status_ms"] = diagnostics.get("provider_status_ms", 0.0) + (
                time.perf_counter() - started
            ) * 1000.0
            diagnostics["provider_status_calls"] = diagnostics.get("provider_status_calls", 0) + 1
            diagnostics["provider_status_refresh_tokens"] = False
    if cache is not None:
        cache[provider_id] = dict(status)
    _provider_status_picker_cache[provider_id] = (now, dict(status))
    return status


def _surface_inactive_reason(choice: dict[str, Any], surface: str, diagnostics: dict[str, Any] | None = None) -> str:
    if choice.get("provider_id") == "codex":
        try:
            if not _provider_status_for_picker("codex", diagnostics).get("runtime_enabled"):
                return "Codex account is connected, but direct chat runtime is not enabled yet."
        except Exception:
            return "Codex direct chat runtime is not enabled yet."
    if choice.get("provider_id") == "claude_subscription":
        try:
            if not _provider_status_for_picker("claude_subscription", diagnostics).get("runtime_enabled"):
                return "Claude Subscription runtime needs a Row-Bot OAuth connection."
        except Exception:
            return "Claude Subscription runtime is not enabled yet."
    if choice.get("provider_id") == "xai_oauth":
        try:
            if not _provider_status_for_picker("xai_oauth", diagnostics).get("runtime_enabled"):
                return "xAI Grok runtime needs a Row-Bot OAuth connection."
        except Exception:
            return "xAI Grok runtime is not enabled yet."
    if choice.get("active") is False:
        return str(choice.get("inactive_reason") or choice.get("last_error") or "This Quick Choice is inactive.")
    inactive_surfaces = choice.get("inactive_surfaces")
    if surface and isinstance(inactive_surfaces, dict) and inactive_surfaces.get(surface):
        return str(inactive_surfaces[surface])
    return ""


def _annotated_choice(choice: dict[str, Any], surface: str, diagnostics: dict[str, Any] | None = None) -> dict[str, Any]:
    annotated = dict(choice)
    reason = _surface_inactive_reason(choice, surface, diagnostics)
    snapshot = choice.get("capabilities_snapshot") if isinstance(choice.get("capabilities_snapshot"), dict) else {}
    if surface and snapshot and not snapshot_supports_surface(snapshot, surface) and not reason:
        reason = _surface_unsupported_reason(surface)
    annotated["active"] = not bool(reason)
    annotated["inactive_reason"] = reason
    return annotated


def _surface_unsupported_reason(surface: str) -> str:
    label = surface.replace("_", " ") if surface else "this surface"
    return f"Capability metadata says this model is not compatible with {label}."


def _is_auto_capability_reason(reason: Any) -> bool:
    return str(reason or "").startswith("Capability metadata says this model is not compatible with ")


def _ollama_uncached_capability_snapshot() -> dict[str, Any]:
    return {
        "capabilities": ["chat", "streaming", "text"],
        "input_modalities": ["text"],
        "output_modalities": ["text"],
        "tasks": ["chat"],
        "tool_calling": None,
        "streaming": True,
        "endpoint_compatibility": ["ollama_chat"],
        "transport": "ollama_chat",
        "source_confidence": "inferred",
        "last_verified_at": "",
    }


def _strip_weak_ollama_vision(snapshot: dict[str, Any]) -> dict[str, Any]:
    cleaned = dict(snapshot)
    capabilities = cleaned.get("capabilities")
    if isinstance(capabilities, list):
        cleaned["capabilities"] = [
            item for item in capabilities
            if str(item).strip().lower() != "vision"
        ]
    input_modalities = cleaned.get("input_modalities")
    if isinstance(input_modalities, list):
        kept = [
            item for item in input_modalities
            if str(item).strip().lower() != "image"
        ]
        cleaned["input_modalities"] = kept or ["text"]
    return cleaned


def _ollama_snapshot_has_weak_inferred_vision(model_id: str, snapshot: dict[str, Any]) -> bool:
    input_modalities = snapshot.get("input_modalities")
    has_image = isinstance(input_modalities, list) and any(
        str(item).strip().lower() == "image" for item in input_modalities
    )
    if not has_image:
        return False
    if str(snapshot.get("source_confidence") or "").strip().lower() != "inferred":
        return False
    if str(snapshot.get("last_verified_at") or "").strip():
        return False
    try:
        from row_bot.providers.ollama import is_ollama_vision_capable

        return not is_ollama_vision_capable(model_id)
    except Exception:
        return True


def _inferred_capability_snapshot(choice: dict[str, Any]) -> dict[str, Any]:
    provider_id = str(choice.get("provider_id") or "")
    model_id = str(choice.get("model_id") or "")
    if not provider_id or not model_id:
        return {}
    try:
        from row_bot.providers.capability_resolution import (
            resolve_capability_metadata,
            resolve_capability_snapshot,
        )

        if provider_id in {"local", "ollama", "ollama_cloud"}:
            resolved = resolve_capability_metadata(
                provider_id,
                model_id,
                include_static_fallback=False,
            )
            cached = resolved.snapshot
            if cached and _ollama_snapshot_has_weak_inferred_vision(model_id, cached):
                return _strip_weak_ollama_vision(cached)
            return cached or _ollama_uncached_capability_snapshot()
        return resolve_capability_snapshot(provider_id, model_id)
    except Exception:
        return {}


def _cached_provider_capability_snapshot(provider_id: str, model_id: str) -> dict[str, Any]:
    from row_bot.providers.capability_resolution import cached_provider_capability_snapshot

    return cached_provider_capability_snapshot(provider_id, model_id)


def _selection_capability_snapshot(provider_id: str, model_id: str) -> dict[str, Any]:
    return _inferred_capability_snapshot({
        "kind": "model",
        "provider_id": provider_id,
        "model_id": model_id,
    })


def refresh_quick_choice_capability_snapshots() -> list[dict[str, Any]]:
    cfg = load_provider_config()
    quick = [choice for choice in cfg.get("quick_choices", []) if isinstance(choice, dict)]
    changed = False
    for choice in quick:
        if choice.get("kind") != "model":
            continue
        inferred = _inferred_capability_snapshot(choice)
        if not inferred:
            continue
        current = choice.get("capabilities_snapshot") if isinstance(choice.get("capabilities_snapshot"), dict) else {}
        if current != inferred:
            choice["capabilities_snapshot"] = inferred
            inactive_surfaces = choice.get("inactive_surfaces")
            if isinstance(inactive_surfaces, dict):
                choice["inactive_surfaces"] = {
                    surface: reason for surface, reason in inactive_surfaces.items()
                    if not _is_auto_capability_reason(reason)
                }
            if _is_auto_capability_reason(choice.get("last_error")):
                choice["last_error"] = ""
            changed = True
    if changed:
        cfg["quick_choices"] = quick
        return save_provider_config(cfg).get("quick_choices", [])
    return quick


def prune_stale_custom_quick_choices() -> int:
    try:
        from row_bot.providers.custom import is_custom_openai_provider, list_custom_endpoints
    except Exception:
        return 0
    endpoints = list_custom_endpoints()
    active_providers = {str(endpoint.get("provider_id") or "") for endpoint in endpoints}
    known_models_by_provider: dict[str, set[str]] = {}
    for endpoint in endpoints:
        provider_id = str(endpoint.get("provider_id") or "")
        models = endpoint.get("models") if isinstance(endpoint.get("models"), list) else []
        model_ids = {
            str(model.get("model_id") or model.get("id") or "")
            for model in models
            if isinstance(model, dict) and str(model.get("model_id") or model.get("id") or "")
        }
        if provider_id and model_ids:
            known_models_by_provider[provider_id] = model_ids
    cfg = load_provider_config()
    quick = [choice for choice in cfg.get("quick_choices", []) if isinstance(choice, dict)]
    kept: list[dict[str, Any]] = []
    removed = 0
    for choice in quick:
        provider_id = str(choice.get("provider_id") or "")
        if not is_custom_openai_provider(provider_id):
            kept.append(choice)
            continue
        model_id = str(choice.get("model_id") or "")
        if provider_id not in active_providers:
            removed += 1
            continue
        known_models = known_models_by_provider.get(provider_id)
        if known_models is not None and model_id not in known_models:
            removed += 1
            continue
        kept.append(choice)
    if removed:
        cfg["quick_choices"] = kept
        save_provider_config(cfg)
    return removed


def _media_tool_selection(tool_name: str, default_model: str) -> str:
    try:
        from row_bot.tools import registry
        tool = registry.get_tool(tool_name)
        if tool:
            return str(tool.get_config("model", default_model) or default_model)
    except Exception:
        pass
    return default_model


def _quick_choice_for_media_selection(selection: str, surface: str) -> dict[str, Any] | None:
    if "/" not in selection:
        return None
    provider_id, model_id = selection.split("/", 1)
    if not provider_id or not model_id:
        return None
    try:
        from row_bot.providers.media import IMAGE_PROVIDER_META, VIDEO_PROVIDER_META, _media_provider_available

        provider_meta = IMAGE_PROVIDER_META if surface == "image" else VIDEO_PROVIDER_META
        meta = provider_meta.get(provider_id)
        if not meta or not _media_provider_available(provider_id, meta):
            return None
    except Exception:
        return None
    try:
        from row_bot.models import _cloud_model_cache
        cached = _cloud_model_cache.get(model_ref(provider_id, model_id)) or _cloud_model_cache.get(model_id)
        if isinstance(cached, dict) and cached.get("provider") == provider_id:
            snapshot = cached.get("capabilities_snapshot") if isinstance(cached.get("capabilities_snapshot"), dict) else {}
            display_name = str(cached.get("label") or model_id)
        else:
            from row_bot.providers.catalog import model_info_from_metadata
            model_info = model_info_from_metadata(provider_id, model_id)
            snapshot = model_info.capability_snapshot()
            display_name = model_info.display_name
    except Exception:
        return None
    if not snapshot_supports_surface(snapshot, surface):
        return None
    choice = _quick_choice_for_model(
        model_id,
        provider_id=provider_id,
        display_name=display_name,
        source=f"{surface}_tool_default",
        capabilities_snapshot=snapshot,
    )
    if choice:
        choice["visibility"] = SURFACE_VISIBILITY.get(surface, [surface])
        choice["order"] = 50 if surface == "image" else 60
        choice["recommended"] = True
    return choice


def seed_configured_media_quick_choices() -> list[dict[str, Any]]:
    try:
        from row_bot.tools.image_gen_tool import DEFAULT_MODEL as IMAGE_DEFAULT
        from row_bot.tools.video_gen_tool import DEFAULT_MODEL as VIDEO_DEFAULT
    except Exception:
        return load_provider_config().get("quick_choices", [])

    candidates = [
        _quick_choice_for_media_selection(_media_tool_selection("image_gen", IMAGE_DEFAULT), "image"),
        _quick_choice_for_media_selection(_media_tool_selection("video_gen", VIDEO_DEFAULT), "video"),
    ]
    cfg = load_provider_config()
    quick = [choice for choice in cfg.get("quick_choices", []) if isinstance(choice, dict)]
    by_id = {choice.get("id"): choice for choice in quick}
    changed = False
    for choice in candidates:
        if not choice:
            continue
        existing = by_id.get(choice["id"])
        if existing:
            snapshot = choice.get("capabilities_snapshot") if isinstance(choice.get("capabilities_snapshot"), dict) else {}
            if existing.get("source") in {"image_tool_default", "video_tool_default"}:
                existing.update({
                    "display_name": choice["display_name"],
                    "visibility": choice["visibility"],
                    "capabilities_snapshot": snapshot,
                    "active": True,
                    "inactive_reason": "",
                    "inactive_surfaces": {},
                    "last_error": "",
                })
                changed = True
            continue
        quick.append(choice)
        by_id[choice["id"]] = choice
        changed = True
    if changed:
        cfg["quick_choices"] = quick
        return save_provider_config(cfg).get("quick_choices", [])
    return quick


def _choice_matches_surface(
    choice: dict[str, Any],
    surface: str,
    *,
    include_inactive: bool = False,
    diagnostics: dict[str, Any] | None = None,
) -> bool:
    visibility = choice.get("visibility")
    supports_surface = snapshot_supports_surface(choice.get("capabilities_snapshot"), surface)
    inactive_reason = _surface_inactive_reason(choice, surface, diagnostics)
    if inactive_reason and not include_inactive:
        return False
    snapshot = choice.get("capabilities_snapshot") if isinstance(choice.get("capabilities_snapshot"), dict) else {}
    if surface and isinstance(visibility, list) and surface not in visibility:
        return supports_surface and bool(choice.get("capabilities_snapshot"))
    if surface and snapshot and not supports_surface:
        return include_inactive
    return supports_surface or (include_inactive and bool(inactive_reason))


def validate_quick_choices_for_surface(surface: str = "chat") -> list[dict[str, Any]]:
    if not surface:
        return migrate_legacy_starred_models()
    migrate_legacy_starred_models()
    quick = refresh_quick_choice_capability_snapshots()
    cfg = load_provider_config()
    changed = False
    for choice in quick:
        if choice.get("kind") != "model" or choice.get("active") is False:
            continue
        snapshot = choice.get("capabilities_snapshot")
        if not isinstance(snapshot, dict) or not snapshot:
            continue
        if snapshot_supports_surface(snapshot, surface):
            continue
        inactive_surfaces = choice.get("inactive_surfaces")
        if not isinstance(inactive_surfaces, dict):
            inactive_surfaces = {}
            choice["inactive_surfaces"] = inactive_surfaces
        reason = _surface_unsupported_reason(surface)
        if inactive_surfaces.get(surface):
            continue
        if inactive_surfaces.get(surface) != reason:
            inactive_surfaces[surface] = reason
            choice["last_error"] = reason
            changed = True
    if changed:
        cfg["quick_choices"] = quick
        return save_provider_config(cfg).get("quick_choices", [])
    return quick


def migrate_legacy_starred_models(*, cloud_models: Iterable[str] | None = None) -> list[dict[str, Any]]:
    try:
        from row_bot.api_keys import get_cloud_config
        starred = list(get_cloud_config().get("starred_models", []))
    except Exception:
        starred = []
    if cloud_models is not None:
        available = set(cloud_models)
        starred = [model_id for model_id in starred if model_id in available]
    if not starred:
        return load_provider_config().get("quick_choices", [])
    cfg = load_provider_config()
    quick = list(cfg.get("quick_choices", []))
    existing = {choice.get("id") for choice in quick if isinstance(choice, dict)}
    changed = False
    for model_id in starred:
        choice = _quick_choice_for_model(model_id, source="legacy_starred_cloud")
        if choice and choice["id"] not in existing:
            choice["order"] = len(quick) + 100
            quick.append(choice)
            existing.add(choice["id"])
            changed = True
    if changed:
        cfg["quick_choices"] = quick
        save_provider_config(cfg)
    return quick


def add_quick_choice_for_model(
    model_id: str,
    *,
    provider_id: str | None = None,
    display_name: str | None = None,
    source: str = "manual",
    capabilities_snapshot: dict[str, Any] | None = None,
    visibility: Iterable[str] | None = None,
    surface: str | None = None,
) -> None:
    if surface and visibility is None:
        visibility = SURFACE_VISIBILITY.get(surface, [surface])
    if capabilities_snapshot is None:
        inferred_provider = _canonical_provider_id(provider_id) if provider_id else infer_provider_id(model_id)
        if inferred_provider:
            capabilities_snapshot = _selection_capability_snapshot(inferred_provider, model_id)
    choice = _quick_choice_for_model(
        model_id,
        provider_id=provider_id,
        display_name=display_name,
        source=source,
        capabilities_snapshot=capabilities_snapshot,
        visibility=visibility,
    )
    if not choice:
        return
    cfg = load_provider_config()
    quick = [c for c in cfg.get("quick_choices", []) if isinstance(c, dict)]
    for existing in quick:
        if existing.get("id") == choice["id"]:
            existing.update({
                "pinned": True,
                "display_name": existing.get("display_name") or choice["display_name"],
                "visibility": choice["visibility"],
                "active": True,
                "inactive_reason": "",
                "inactive_surfaces": {},
                "last_error": "",
            })
            if capabilities_snapshot:
                existing["capabilities_snapshot"] = dict(capabilities_snapshot)
            cfg["quick_choices"] = quick
            save_provider_config(cfg)
            return
    choice["order"] = len(quick) + 100
    quick.append(choice)
    cfg["quick_choices"] = quick
    save_provider_config(cfg)


def remove_quick_choice_for_model(model_id: str, *, provider_id: str | None = None) -> None:
    provider_id = provider_id or infer_provider_id(model_id)
    if not provider_id:
        return
    ref = model_ref(provider_id, model_id)
    cfg = load_provider_config()
    cfg["quick_choices"] = [
        c for c in cfg.get("quick_choices", [])
        if not isinstance(c, dict) or c.get("id") != ref
    ]
    save_provider_config(cfg)


def remove_quick_choices_for_provider(provider_id: str) -> int:
    provider_id = str(provider_id or "").strip()
    if not provider_id:
        return 0
    cfg = load_provider_config()
    quick = [c for c in cfg.get("quick_choices", []) if isinstance(c, dict)]
    kept = [c for c in quick if str(c.get("provider_id") or "") != provider_id]
    removed = len(quick) - len(kept)
    if removed:
        cfg["quick_choices"] = kept
        save_provider_config(cfg)
    return removed


def remove_quick_choices_for_missing_models(provider_id: str, valid_model_ids: set[str]) -> int:
    provider_id = str(provider_id or "").strip()
    valid = {str(model_id) for model_id in valid_model_ids if str(model_id)}
    if not provider_id:
        return 0
    cfg = load_provider_config()
    quick = [c for c in cfg.get("quick_choices", []) if isinstance(c, dict)]
    kept = [
        c for c in quick
        if str(c.get("provider_id") or "") != provider_id
        or str(c.get("model_id") or "") in valid
    ]
    removed = len(quick) - len(kept)
    if removed:
        cfg["quick_choices"] = kept
        save_provider_config(cfg)
    return removed


def deactivate_quick_choice(
    ref: str | None = None,
    *,
    model_id: str | None = None,
    provider_id: str | None = None,
    surface: str | None = None,
    reason: str = "",
) -> bool:
    if not ref and model_id:
        provider = provider_id or infer_provider_id(model_id)
        ref = model_ref(provider, model_id) if provider else ""
    if not ref:
        return False
    cfg = load_provider_config()
    quick = [choice for choice in cfg.get("quick_choices", []) if isinstance(choice, dict)]
    changed = False
    inactive_reason = reason or "This Quick Choice is inactive."
    for choice in quick:
        if choice.get("id") != ref:
            continue
        if surface:
            inactive_surfaces = choice.get("inactive_surfaces")
            if not isinstance(inactive_surfaces, dict):
                inactive_surfaces = {}
                choice["inactive_surfaces"] = inactive_surfaces
            inactive_surfaces[surface] = inactive_reason
        else:
            choice["active"] = False
            choice["inactive_reason"] = inactive_reason
        choice["last_error"] = inactive_reason
        changed = True
        break
    if changed:
        cfg["quick_choices"] = quick
        save_provider_config(cfg)
    return changed


def deactivate_quick_choice_for_error(
    ref: str | None = None,
    *,
    model_id: str | None = None,
    provider_id: str | None = None,
    surface: str | None = None,
    error: BaseException | NormalizedProviderError,
) -> bool:
    normalized = error if isinstance(error, NormalizedProviderError) else normalize_provider_error(error)
    reason = normalized.next_action or normalized.message or "Choose a compatible provider model."
    return deactivate_quick_choice(ref, model_id=model_id, provider_id=provider_id, surface=surface, reason=reason)


def list_quick_choices(
    surface: str = "chat",
    *,
    include_routes: bool = False,
    include_inactive: bool = False,
    return_diagnostics: bool = False,
) -> list[dict[str, Any]] | tuple[list[dict[str, Any]], dict[str, Any]]:
    started = time.perf_counter()
    diagnostics: dict[str, Any] = {
        "surface": surface,
        "include_routes": include_routes,
        "include_inactive": include_inactive,
        "read_only": True,
        "provider_status_ms": 0.0,
        "provider_status_calls": 0,
    }
    migrate_started = time.perf_counter()
    quick = [choice for choice in load_provider_config().get("quick_choices", []) if isinstance(choice, dict)]
    diagnostics["migrate_ms"] = (time.perf_counter() - migrate_started) * 1000.0
    validate_started = time.perf_counter()
    diagnostics["validate_ms"] = (time.perf_counter() - validate_started) * 1000.0
    annotate_started = time.perf_counter()
    choices = [
        _annotated_choice(choice, surface, diagnostics) for choice in quick
        if isinstance(choice, dict) and _choice_matches_surface(choice, surface, include_inactive=include_inactive, diagnostics=diagnostics)
    ]
    diagnostics["annotate_ms"] = (time.perf_counter() - annotate_started) * 1000.0
    if include_routes:
        routes_started = time.perf_counter()
        cfg = load_provider_config()
        for route in cfg.get("routes", []):
            if isinstance(route, dict) and route.get("enabled"):
                choices.append({
                    "id": route_ref(str(route.get("id"))),
                    "kind": "route",
                    "route_id": str(route.get("id")),
                    "display_name": f"Route: {route.get('display_name') or route.get('id')}",
                    "visibility": list(DEFAULT_VISIBILITY),
                    "order": 10,
                    "pinned": True,
                    "recommended": True,
                    "risk_label": route.get("data_policy", "route"),
                    "active": False,
                    "inactive_reason": "Routing execution is configured but not enabled until the routing phase.",
                })
        diagnostics["routes_ms"] = (time.perf_counter() - routes_started) * 1000.0
    sorted_choices = sorted(choices, key=lambda c: (int(c.get("order", 1000)), str(c.get("display_name") or c.get("id"))))
    diagnostics["total_ms"] = (time.perf_counter() - started) * 1000.0
    diagnostics["choices"] = len(sorted_choices)
    diagnostics.pop("_provider_status_cache", None)
    if return_diagnostics:
        return sorted_choices, diagnostics
    return sorted_choices


def grouped_quick_choices(
    *,
    include_inactive: bool = True,
    include_routes: bool = False,
    include_media_defaults: bool = True,
) -> list[dict[str, Any]]:
    if include_media_defaults:
        seed_configured_media_quick_choices()
    groups: list[dict[str, Any]] = []
    group_defs = list(QUICK_CHOICE_SURFACE_GROUPS)
    if include_routes:
        group_defs.append({"id": "routes", "display_name": "Routes"})
    for group in group_defs:
        surface = group["id"]
        if surface == "routes":
            choices = [
                choice for choice in list_quick_choices("", include_routes=True, include_inactive=True)
                if choice.get("kind") == "route"
            ]
        else:
            choices = [
                choice for choice in list_quick_choices(surface, include_inactive=include_inactive)
                if choice.get("kind") == "model"
            ]
        groups.append({"id": surface, "display_name": group["display_name"], "choices": choices})
    return groups


def list_quick_model_ids(surface: str = "chat") -> list[str]:
    model_ids: list[str] = []
    for choice in list_quick_choices(surface):
        if choice.get("kind") == "model" and choice.get("model_id"):
            model_ids.append(str(choice["model_id"]))
    return model_ids


def resolve_selection(value: str) -> ResolvedSelection | None:
    raw = (value or "").strip()
    if not raw:
        return None
    choices = list_quick_choices("", include_routes=True, include_inactive=True)
    lower = raw.lower()
    for choice in choices:
        aliases = {
            str(choice.get("id", "")).lower(),
            str(choice.get("display_name", "")).lower(),
            str(choice.get("model_id", "")).lower(),
            str(choice.get("route_id", "")).lower(),
        }
        if lower in aliases:
            if choice.get("kind") == "route":
                return ResolvedSelection(
                    ref=str(choice.get("id")), kind="route", route_id=str(choice.get("route_id")),
                    display_name=str(choice.get("display_name") or choice.get("route_id")), legacy_value=raw,
                    active=False, reason="Routing execution is configured but not enabled until the routing phase.",
                )
            return ResolvedSelection(
                ref=str(choice.get("id")), kind="model", model_id=str(choice.get("model_id")),
                provider_id=str(choice.get("provider_id")), display_name=str(choice.get("display_name") or choice.get("model_id")),
                legacy_value=raw, active=choice.get("active") is not False, reason=str(choice.get("inactive_reason") or ""),
            )
    parsed = parse_model_ref(raw)
    if parsed:
        provider_id, model_id = parsed
        return ResolvedSelection(ref=raw, kind="model", provider_id=provider_id, model_id=model_id, display_name=model_id, legacy_value=raw)
    if raw.startswith("route:"):
        return ResolvedSelection(ref=raw, kind="route", route_id=raw.split(":", 1)[1], display_name=raw, legacy_value=raw, active=False, reason="Routing execution is not enabled yet.")
    provider_id = infer_provider_id(raw)
    if provider_id:
        return ResolvedSelection(ref=model_ref(provider_id, raw), kind="model", provider_id=provider_id, model_id=raw, display_name=raw, legacy_value=raw)
    return ResolvedSelection(ref=model_ref("ollama", raw), kind="model", provider_id="ollama", model_id=raw, display_name=raw, legacy_value=raw)
