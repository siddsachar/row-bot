"""Typed composer projection and admission snapshots over exact-model reasoning."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from typing import Any

from row_bot.providers import reasoning
from row_bot.application.client_platform import ClientPlatformError


def capability_revision(model_ref: str, caps: reasoning.ReasoningCapabilities | None) -> str:
    """Invalidate choices when the cached exact-model capability contract changes."""
    value = {"model_ref": model_ref, "capabilities": caps.to_json() if caps else None}
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def reasoning_view(conversation_id: str, model_ref: str) -> dict[str, Any]:
    """Read only; stale saved preferences are visible, never rewritten by a query."""
    from row_bot.threads import get_thread_reasoning_selection
    caps = reasoning.resolve_reasoning_capabilities_for_ref(model_ref)
    saved = get_thread_reasoning_selection(conversation_id, model_ref)
    stale = False
    try:
        selection = reasoning.validate_reasoning_selection(reasoning.ReasoningSelection.from_json(saved), caps)
    except (TypeError, ValueError):
        selection = reasoning.ReasoningSelection()
        stale = True
    return {
        "model_ref": model_ref, "capability_revision": capability_revision(model_ref, caps),
        "available": bool(caps and caps.controllable), "selection": selection.to_json(),
        "choices": [{"selection": value.to_json(), "label": value.label}
                    for value in reasoning.reasoning_choices(caps)],
        "supports_budget": bool(caps and caps.supports_budget),
        "budget_min": caps.budget_min if caps else 0, "budget_max": caps.budget_max if caps else 0,
        "stale": stale,
    }


def validated_control(model_ref: str, control: dict) -> reasoning.ReasoningSelection:
    if control.get("model_ref") != model_ref:
        raise ClientPlatformError("reasoning_model_mismatch")
    caps = reasoning.resolve_reasoning_capabilities_for_ref(model_ref)
    if control.get("capability_revision") != capability_revision(model_ref, caps):
        raise ClientPlatformError("reasoning_capabilities_changed")
    try:
        return reasoning.validate_reasoning_selection(
            reasoning.ReasoningSelection.from_json(control.get("selection")), caps)
    except (TypeError, ValueError) as exc:
        raise ClientPlatformError("invalid_reasoning_selection") from exc


def merged_selection(serialized: str, model_ref: str, selection: reasoning.ReasoningSelection) -> str:
    """Merge one canonical model under the caller's existing thread transaction."""
    try:
        values = json.loads(serialized) if serialized else {}
    except (TypeError, ValueError):
        values = {}
    values = dict(values) if isinstance(values, dict) else {}
    if selection.is_default:
        values.pop(model_ref, None)
    else:
        values[model_ref] = selection.to_json()
    return json.dumps(values, sort_keys=True, separators=(",", ":"))


def freeze_reasoning(configurable: dict[str, Any], conversation_id: str, *, revalidate: bool = False) -> None:
    """Snapshot only server-owned config; admitted calls never reread mutable prefs."""
    model_ref = str(configurable.get("model_override") or "")
    snapshot = configurable.get("reasoning_snapshot")
    if snapshot is not None:
        if (not isinstance(snapshot, dict) or snapshot.get("model_ref") != model_ref
                or snapshot.get("conversation_id") != conversation_id):
            raise ClientPlatformError("reasoning_snapshot_unavailable")
        if revalidate:
            caps = reasoning.resolve_reasoning_capabilities_for_ref(model_ref)
            if snapshot.get("capability_revision") != capability_revision(model_ref, caps):
                raise ClientPlatformError("reasoning_capabilities_changed")
        configurable["reasoning_snapshot"] = deepcopy(snapshot)
        return
    # Legacy queued entries predate snapshots; they retain the existing selection
    # resolution on their first admission, then freeze for subsequent requests.
    plan = reasoning.request_plan_for(conversation_id, model_ref, use_snapshot=False)
    configurable["reasoning_snapshot"] = {
        "conversation_id": conversation_id, "model_ref": model_ref,
        "capability_revision": capability_revision(model_ref, plan.capabilities),
        "selection": plan.selection.to_json(),
        "capabilities": plan.capabilities.to_json() if plan.capabilities else None,
    }


def restore_resume_reasoning(configurable: dict[str, Any], conversation_id: str, *, pass_id: str = "") -> None:
    """Resume the saved request cut; an explicit different model gets its own cut.

    Reuse the existing private accepted execution context, never a browser value.
    Approval continuations additionally bind that context to the approved pass.
    Pre-snapshot legacy executions retain their original resolution behavior.
    """
    if "reasoning_snapshot" in configurable:
        return
    from row_bot.application.client_queue import _staged
    from row_bot.runtime import admissions
    _, values = _staged(conversation_id)
    context = values.get("accepted_context") or {}
    snapshot = (context.get("configurable") or {}).get("reasoning_snapshot")
    if snapshot is None:
        return
    if pass_id:
        with admissions.transaction() as conn:
            row = conn.execute("SELECT generation_id FROM generation_passes WHERE pass_id=? AND conversation_id=?",
                               (pass_id, conversation_id)).fetchone()
        if row is None or row[0] != context.get("generation_id"):
            raise ClientPlatformError("reasoning_snapshot_unavailable")
    if snapshot.get("model_ref") != configurable.get("model_override"):
        if pass_id:
            raise ClientPlatformError("reasoning_snapshot_unavailable")
        return
    configurable["reasoning_snapshot"] = deepcopy(snapshot)
    freeze_reasoning(configurable, conversation_id, revalidate=True)
