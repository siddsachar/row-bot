from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any, Iterable

from row_bot.providers.capabilities import snapshot_supports_surface
from row_bot.providers.catalog import infer_provider_id

# Every job that runs on a chat model reads the chat list (your models that can chat).
CHAT_JOBS = frozenset({"chat", "workflow", "channels", "designer", "status_tool"})


def _job(surface: str) -> str:
    return "chat" if surface in CHAT_JOBS else surface


@dataclass(frozen=True)
class ResolvedSelection:
    ref: str
    kind: str
    model_id: str = ""
    provider_id: str = ""
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
    selection. It reads custom endpoint metadata and the cached model catalog
    only; it does not probe providers or refresh live catalogs. When
    require_pinned is true, only your starred models are accepted.
    """
    raw = str(value or "").strip()
    if not raw or raw.lower() == "default":
        if allow_default:
            return CatalogModelResolution(ref="", display_label="Default", source="default")
        raise ModelSelectionError("Model selection is empty.")

    parsed = parse_model_ref(raw)
    candidates = _strict_catalog_model_candidates(surface=surface)
    favourites = _favourite_refs() if require_pinned else frozenset()
    if parsed:
        provider_id, model_id = parsed
        ref = model_ref(provider_id, model_id)
        exact = _unique_catalog_candidates(
            item for item in candidates if item["ref"].lower() == ref.lower()
        )
        if exact:
            if require_pinned and not _catalog_candidate_is_pinned(exact[0], favourites):
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
        pinned_matches = [item for item in matches if _catalog_candidate_is_pinned(item, favourites)]
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


def _favourite_refs() -> frozenset[str]:
    from row_bot.providers.model_visibility import read_model_visibility

    return frozenset(read_model_visibility().favourites)


def _catalog_candidate_is_pinned(candidate: dict[str, Any], favourites: frozenset[str]) -> bool:
    return str(candidate.get("ref") or "") in favourites


def _pinned_model_suggestions(
    candidates: Iterable[dict[str, Any]],
    *,
    provider_id: str = "",
    limit: int = 5,
) -> tuple[str, ...]:
    provider = _canonical_provider_id(provider_id)
    favourites = _favourite_refs()
    pinned = [
        item
        for item in _unique_catalog_candidates(candidates)
        if _catalog_candidate_is_pinned(item, favourites) and item.get("active") is not False
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
    """Return compact, agent-readable starred model choices for a surface."""
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
            "source": str(option.get("source") or "star"),
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


def _catalog_candidate_supports_surface(
    surface: str,
    *,
    visibility: Iterable[str] | None,
    capabilities_snapshot: dict[str, Any],
) -> bool:
    selected_surface = _job(str(surface or "").strip())
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

    match = _canonical_star_match(raw, surface)
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


def _canonical_star_match(raw: str, surface: str) -> CanonicalModelSelection | None:
    """A bare name or label that matches exactly one of your models for this job."""
    lower = raw.lower()
    matches = [
        option for option in list_model_choice_options(surface, include_inactive=True)
        if lower in {str(option.get(key) or "").lower() for key in ("value", "model_id", "display_name")}
    ]
    if not matches:
        return None
    if len(matches) > 1:
        refs = ", ".join(str(option["value"]) for option in matches)
        raise ModelSelectionError(f"Ambiguous model selection '{raw}'. Use one of: {refs}.")
    option = matches[0]
    if star_cannot_run(option):
        raise ModelSelectionError(f"Model selection '{raw}' is inactive: {option.get('reason') or 'unavailable'}")
    provider_id, model_id = str(option["provider_id"]), str(option["model_id"])
    return CanonicalModelSelection(
        ref=str(option["value"]), provider_id=provider_id, model_id=model_id,
        display_label=format_model_choice_label(provider_id, model_id, str(option.get("display_name") or model_id),
                                                include_icon=False),
        active=True, source="star",
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
    "claude_subscription": "Claude plan",
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
        surface = _job(surface)
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
    return option


def _star_options(surface: str, *, include_inactive: bool) -> list[dict[str, Any]]:
    """Your starred models that can do this job, in the order they were starred, with readiness.

    Learning's offered personal models are listed for chat too. A star whose
    model is gone from the saved catalog is not listed.
    """
    from row_bot.providers.client_status import _read
    from row_bot.providers.model_visibility import read_model_visibility

    job = _job(surface)
    _snapshot, rows = _read(None, include_readiness=True)
    by_ref = {row.selection_ref: row for row in rows}
    refs = list(read_model_visibility().favourites)
    if job == "chat":
        refs += [row.selection_ref for row in rows if row.offered and row.selection_ref not in refs]
    options = []
    for ref in refs:
        row = by_ref.get(ref)
        if row is None or (row.categories and job not in row.categories):
            continue
        reason = ""
        if not row.categories:
            reason = "No saved details for this model yet. Refresh the catalog."
        elif not (row.configured and row.runtime_ready and row.installed):
            reason = row.status_reason or "This model isn't available right now."
        if reason and not include_inactive:
            continue
        options.append({
            "value": ref,
            "label": format_model_choice_label(row.provider_id, row.model_id, row.display_name, include_icon=False),
            "provider_id": row.provider_id,
            "model_id": row.model_id,
            "display_name": row.display_name,
            "source": "star",
            "active": not reason,
            "reason": reason,
            # Known by name but not yet described (a local server before its first check): it still runs.
            "metadata_missing": not row.categories,
        })
    return options


def star_cannot_run(option: dict[str, Any]) -> bool:
    """A starred model that can't run now; one only missing its saved details can."""
    return option.get("active") is False and not option.get("metadata_missing")


def starred_model_refs(surface: str = "chat") -> list[str]:
    """Your ready starred models for one job, as provider-qualified refs."""
    return [str(option["value"]) for option in _star_options(surface, include_inactive=False)]


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

    stars_started = time.perf_counter()
    for option in _star_options(surface, include_inactive=include_inactive):
        add_option(option)
    diagnostics["stars_ms"] = (time.perf_counter() - stars_started) * 1000.0
    option_build_started = time.perf_counter()
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


def _surface_unsupported_reason(surface: str) -> str:
    label = surface.replace("_", " ") if surface else "this surface"
    return f"Capability metadata says this model is not compatible with {label}."


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


def resolve_selection(value: str) -> ResolvedSelection | None:
    """Read a stored model value (a provider-qualified ref, or a legacy bare name)."""
    raw = (value or "").strip()
    if not raw:
        return None
    parsed = parse_model_ref(raw)
    if parsed:
        provider_id, model_id = parsed
        return ResolvedSelection(ref=raw, kind="model", provider_id=provider_id, model_id=model_id, display_name=model_id, legacy_value=raw)
    provider_id = infer_provider_id(raw)
    if provider_id:
        return ResolvedSelection(ref=model_ref(provider_id, raw), kind="model", provider_id=provider_id, model_id=raw, display_name=raw, legacy_value=raw)
    return ResolvedSelection(ref=model_ref("ollama", raw), kind="model", provider_id="ollama", model_id=raw, display_name=raw, legacy_value=raw)
