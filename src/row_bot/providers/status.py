from __future__ import annotations

from row_bot.providers.catalog import list_provider_definitions
from row_bot.providers.models import ModelInfo
from row_bot.providers.runtime import provider_status


def _provider_group(provider_id: str, risk_label: str) -> str:
    if provider_id == "ollama":
        return "Local"
    if risk_label == "subscription":
        return "Subscription Accounts"
    if provider_id.startswith("custom_openai_") or risk_label == "custom_endpoint":
        return "Custom Endpoints"
    return "API Providers"


def _cached_model_stats(provider_id: str) -> dict[str, int]:
    stats = {"model_count": 0, "chat_count": 0, "media_count": 0}
    try:
        from row_bot.models import _cloud_model_cache, _sync_custom_model_cache
        _sync_custom_model_cache()
        for info in _cloud_model_cache.values():
            if not isinstance(info, dict) or info.get("provider") != provider_id:
                continue
            stats["model_count"] += 1
            snapshot = info.get("capabilities_snapshot") if isinstance(info.get("capabilities_snapshot"), dict) else {}
            tasks = set(snapshot.get("tasks") or []) if isinstance(snapshot.get("tasks"), list) else set()
            if tasks.intersection({"chat", "responses"}) or not tasks:
                stats["chat_count"] += 1
            if tasks.intersection({"image_generation", "image_edit", "video_generation"}):
                stats["media_count"] += 1
    except Exception:
        pass
    return stats


def _source_label_for_model_infos(infos: list[ModelInfo], fallback: str) -> str:
    sources = sorted({str(info.source or "").strip() for info in infos if str(info.source or "").strip()})
    return sources[0] if len(sources) == 1 else (fallback if sources else "")


def _model_infos_stats(infos: list[ModelInfo], *, fallback_source: str) -> dict[str, object]:
    stats: dict[str, object] = {
        "model_count": len(infos),
        "chat_count": 0,
        "media_count": 0,
        "model_count_source": _source_label_for_model_infos(infos, fallback_source),
        "model_count_status": "known" if infos else "empty_verified",
    }
    for info in infos:
        tasks = {str(task) for task in (info.tasks or frozenset()) if str(task)}
        if tasks.intersection({"chat", "responses"}) or not tasks:
            stats["chat_count"] = int(stats["chat_count"]) + 1
        if tasks.intersection({"image_generation", "image_edit", "video_generation"}):
            stats["media_count"] = int(stats["media_count"]) + 1
    return stats


def _provider_catalog_stats(provider_id: str) -> dict[str, object]:
    try:
        if provider_id == "codex":
            from row_bot.providers.codex import list_codex_model_infos_for_status

            return _model_infos_stats(
                list_codex_model_infos_for_status(),
                fallback_source="codex_status_catalog",
            )
        if provider_id == "claude_subscription":
            from row_bot.providers.claude_subscription import list_claude_subscription_model_infos_for_status

            return _model_infos_stats(
                list_claude_subscription_model_infos_for_status(),
                fallback_source="claude_subscription_status_catalog",
            )
        if provider_id == "xai_oauth":
            from row_bot.providers.xai_oauth import list_xai_oauth_model_infos_for_status

            return _model_infos_stats(
                list_xai_oauth_model_infos_for_status(),
                fallback_source="xai_oauth_status_catalog",
            )
    except Exception:
        return {
            "model_count": None,
            "chat_count": 0,
            "media_count": 0,
            "model_count_source": "",
            "model_count_status": "unavailable",
        }
    return {
        "model_count": None,
        "chat_count": 0,
        "media_count": 0,
        "model_count_source": "",
        "model_count_status": "unknown",
    }


def provider_status_cards(*, refresh_tokens: bool = False) -> list[dict]:
    cards: list[dict] = []
    for definition in list_provider_definitions():
        try:
            status = provider_status(definition.id, refresh_tokens=refresh_tokens)
        except TypeError:
            status = provider_status(definition.id)
        cache_stats = _cached_model_stats(definition.id)
        catalog_stats = _provider_catalog_stats(definition.id)
        model_count = status.get("model_count")
        model_count_source = str(status.get("model_count_source") or "")
        model_count_status = str(status.get("model_count_status") or "")
        chat_count = cache_stats["chat_count"]
        media_count = cache_stats["media_count"]
        if model_count is None:
            if cache_stats["model_count"]:
                model_count = cache_stats["model_count"]
                model_count_source = "cloud_cache"
                model_count_status = "known"
            elif catalog_stats.get("model_count") is not None:
                model_count = catalog_stats.get("model_count")
                model_count_source = str(catalog_stats.get("model_count_source") or "")
                model_count_status = str(catalog_stats.get("model_count_status") or "")
                chat_count = int(catalog_stats.get("chat_count") or 0)
                media_count = int(catalog_stats.get("media_count") or 0)
            else:
                model_count = None
                model_count_status = model_count_status or str(catalog_stats.get("model_count_status") or "unknown")
        else:
            try:
                model_count = int(model_count)
            except (TypeError, ValueError):
                model_count = None
                model_count_status = model_count_status or "unknown"
            else:
                model_count_status = model_count_status or ("empty_verified" if model_count == 0 else "known")
                model_count_source = model_count_source or "runtime_status"
        cards.append({
            "provider_id": definition.id,
            "display_name": definition.display_name,
            "configured": bool(status.get("configured")),
            "source": status.get("source") or "",
            "fingerprint": status.get("fingerprint") or "",
            "auth_method": status.get("auth_method") or "",
            "expires_at": status.get("expires_at") or "",
            "account_id_hash": status.get("account_id_hash") or "",
            "user_hash": status.get("user_hash") or "",
            "plan_type": status.get("plan_type") or "",
            "external_reference_label": status.get("external_reference_label") or "",
            "external_reference_path_hash": status.get("external_reference_path_hash") or "",
            "external_reference_exists": bool(status.get("external_reference_exists")),
            "external_reference_source": status.get("external_reference_source") or "",
            "external_reference_metadata_only": bool(status.get("external_reference_metadata_only")),
            "cli_installed": bool(status.get("cli_installed")),
            "cli_version": status.get("cli_version") or "",
            "runtime_enabled": bool(status.get("runtime_enabled")),
            "token_health": status.get("token_health") or "",
            "token_health_detail": status.get("token_health_detail") or "",
            "last_runtime_probe": dict(status.get("last_runtime_probe") or {}),
            "last_vision_probe": dict(status.get("last_vision_probe") or {}),
            "runtime_probes": {
                str(model_id): dict(probe)
                for model_id, probe in (status.get("runtime_probes") or {}).items()
                if isinstance(probe, dict)
            } if isinstance(status.get("runtime_probes"), dict) else {},
            "last_error": status.get("last_error") or "",
            "model_count": model_count,
            "model_count_status": model_count_status or "unknown",
            "model_count_source": model_count_source,
            "chat_count": chat_count,
            "media_count": media_count,
            "oauth_client_id_configured": bool(status.get("oauth_client_id_configured")),
            "oauth_client_id_source": status.get("oauth_client_id_source") or "",
            "oauth_client_id_fingerprint": status.get("oauth_client_id_fingerprint") or "",
            "oauth_client_id_detail": status.get("oauth_client_id_detail") or "",
            "auth_methods": tuple(method.value for method in definition.auth_methods),
            "supports_catalog": bool(definition.supports_catalog),
            "risk_label": definition.risk_label,
            "icon": definition.icon,
            "group": _provider_group(definition.id, definition.risk_label),
            **({"claude_plan": dict(status["claude_plan"])} if isinstance(status.get("claude_plan"), dict) else {}),
        })
    return cards


# A saved sign-in that can't be renewed any more: only the person signs in again.
_SIGN_IN_GONE = frozenset({"expired", "revoked"})


def short_provider_name(display_name: str) -> str:
    """"ChatGPT / Codex" → "ChatGPT", "Claude (your Claude plan)" → "Claude"."""
    return display_name.split(" (", 1)[0].split(" / ", 1)[0].strip() or display_name


def sign_in_problems() -> list[dict[str, str]]:
    """Subscription sign-ins someone set up that can't be used now: provider id, name, status, words.

    Local reads only: saved sign-ins are read without renewing them, and Claude Code is asked
    only once Claude has been used here. A provider never connected is not a problem.
    """
    from row_bot.providers import claude_subscription

    problems: list[dict[str, str]] = []
    for definition in list_provider_definitions():
        if definition.risk_label != "subscription":
            continue
        name = short_provider_name(definition.display_name)
        if definition.id == claude_subscription.CLAUDE_SUBSCRIPTION_PROVIDER_ID:
            if not claude_subscription.signed_in_before():
                continue
            state = claude_subscription.claude_plan_status().state
            if state == "signed_out":
                problems.append(_sign_in_problem(definition.id, name, "error",
                                                 f"{name} isn't signed in any more. Sign in again to use its models."))
            elif state == "too_old":
                problems.append(_sign_in_problem(definition.id, name, "warn",
                                                 "Claude Code needs an update before Claude's models can run."))
            continue
        health = str(provider_status(definition.id, refresh_tokens=False).get("token_health") or "")
        if health in _SIGN_IN_GONE:
            problems.append(_sign_in_problem(definition.id, name, "error",
                                             f"Your {name} sign-in has expired. Sign in again to use its models."))
        elif health == "error":
            problems.append(_sign_in_problem(definition.id, name, "warn",
                                             f"Row-Bot couldn't read your {name} sign-in. Open it to check."))
    return problems


def _sign_in_problem(provider_id: str, name: str, status: str, detail: str) -> dict[str, str]:
    return {"provider_id": provider_id, "name": name, "status": status, "detail": detail}


def summarize_providers() -> str:
    lines = ["**Providers**"]
    for card in provider_status_cards():
        state = "configured" if card["configured"] else "not set"
        source = f" ({card['source']})" if card.get("source") else ""
        count_label = "local model(s)" if card.get("provider_id") == "ollama" else "catalog model(s)"
        if card.get("model_count") is not None:
            count = f", {card['model_count']} {count_label}"
        elif card.get("configured") or card.get("runtime_enabled"):
            count = ", catalog count unknown"
        else:
            count = ""
        extra = ""
        provider_id = str(card.get("provider_id") or "")
        if provider_id.startswith("custom_openai_"):
            try:
                from row_bot.providers.custom import custom_probe_summary, get_custom_endpoint

                endpoint = get_custom_endpoint(provider_id) or {}
                probe = endpoint.get("last_probe") if isinstance(endpoint.get("last_probe"), dict) else {}
                if probe:
                    summary = custom_probe_summary(probe)
                    classification = str(summary.get("classification") or "unknown").replace("_", " ")
                    details = [f"probe {classification}"]
                    if probe.get("tool_round_trip") is not None:
                        details.append(f"round-trip {'ok' if probe.get('tool_round_trip') else 'failed'}")
                    if probe.get("streaming_tool_calling") is not None:
                        details.append(f"stream tools {'ok' if probe.get('streaming_tool_calling') else 'failed'}")
                    if probe.get("vision_probed") is False:
                        skip = str(probe.get("vision_probe_skip_reason") or "not run")
                        details.append(f"vision not run ({skip})")
                    elif probe.get("vision_ok") is True:
                        details.append("vision ok")
                    elif probe.get("vision_ok") is False:
                        details.append("vision failed")
                    elif probe.get("vision_probed"):
                        details.append("vision inconclusive")
                    extra = f"; {', '.join(details)}"
            except Exception:
                extra = ""
        lines.append(f"- {card['display_name']}: {state}{source}{count}{extra}")
    from row_bot.providers.model_visibility import read_model_visibility

    quick_count = len(read_model_visibility().favourites)
    lines.append(f"- Quick Choices: {quick_count} model(s)")
    return "\n".join(lines)
