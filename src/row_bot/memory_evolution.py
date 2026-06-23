"""Conservative memory integrity helpers.

Phase 4 keeps evolution state in the existing knowledge-graph properties JSON
so memory.py and older callers stay compatible.
"""

from __future__ import annotations

from datetime import datetime
import json
import logging
import os
from pathlib import Path
from typing import Any

from row_bot.data_paths import get_row_bot_data_dir

logger = logging.getLogger(__name__)

VALID_STATUSES = {"active", "archived", "superseded", "needs_review"}
VALID_TIERS = {"core", "semantic", "episodic", "resource"}
HIGH_AUTHORITY_ACTORS = {"manual", "wiki"}
JOURNAL_MAX_ENTRIES = 200
EVIDENCE_MAX_ITEMS = 8
SUPERSEDES_MAX_ITEMS = 12

_DATA_DIR = get_row_bot_data_dir()
_JOURNAL_FILE = _DATA_DIR / "memory_evolution_journal.json"


def now_iso() -> str:
    return datetime.now().isoformat()


def json_props(raw: Any) -> dict[str, Any]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "{}")
        except (json.JSONDecodeError, TypeError):
            raw = {}
    return dict(raw) if isinstance(raw, dict) else {}


def get_properties(entity: dict | None) -> dict[str, Any]:
    return json_props((entity or {}).get("properties", {}))


def normalize_status(status: str | None) -> str:
    value = (status or "active").strip().lower()
    return value if value in VALID_STATUSES else "active"


def normalize_tier(tier: str | None, *, source: str = "", entity_type: str = "") -> str:
    value = (tier or "").strip().lower()
    if value in VALID_TIERS:
        return value
    if source.startswith("document:") or entity_type == "media":
        return "resource"
    if source.startswith("dream_"):
        return "semantic"
    return "semantic"


def normalize_properties(
    props: dict | str | None,
    *,
    source: str = "",
    entity_type: str = "",
) -> dict[str, Any]:
    normalized = json_props(props or {})
    normalized["status"] = normalize_status(normalized.get("status"))
    normalized["memory_tier"] = normalize_tier(
        normalized.get("memory_tier"),
        source=source,
        entity_type=entity_type,
    )
    if "confidence" in normalized:
        try:
            normalized["confidence"] = max(0.0, min(1.0, float(normalized["confidence"])))
        except (TypeError, ValueError):
            normalized.pop("confidence", None)
    evidence = normalized.get("evidence")
    if evidence is None:
        evidence_items: list[Any] = []
    elif isinstance(evidence, list):
        evidence_items = evidence
    else:
        evidence_items = [evidence]
    normalized["evidence"] = evidence_items[:EVIDENCE_MAX_ITEMS]
    normalized["evidence_count"] = int(normalized.get("evidence_count") or len(evidence_items))
    if normalized.get("supersedes") and not isinstance(normalized["supersedes"], list):
        normalized["supersedes"] = [normalized["supersedes"]]
    return normalized


def merge_source_context(
    existing: dict | str | None,
    incoming: dict | None,
    *,
    actor: str | None = None,
) -> dict[str, Any]:
    ctx = json_props(existing or {})
    if incoming:
        for key, value in incoming.items():
            if value is not None:
                ctx[key] = value
    if actor:
        ctx["actor"] = actor
    return ctx


def merge_evidence(existing: Any, incoming: Any) -> list[Any]:
    items: list[Any] = []
    for value in (existing, incoming):
        if value is None:
            continue
        if isinstance(value, list):
            candidates = value
        else:
            candidates = [value]
        for item in candidates:
            if not item:
                continue
            if item not in items:
                items.append(item)
    return items[:EVIDENCE_MAX_ITEMS]


def merge_unique_list(existing: Any, incoming: Any) -> list[Any]:
    items: list[Any] = []
    for value in (existing, incoming):
        if value is None:
            continue
        candidates = value if isinstance(value, list) else [value]
        for item in candidates:
            if not item:
                continue
            if item not in items:
                items.append(item)
    return items


def merge_properties(
    existing: dict | str | None,
    incoming: dict | str | None,
    *,
    source: str = "",
    entity_type: str = "",
    actor: str | None = None,
    source_context: dict | None = None,
    high_authority: bool = False,
) -> dict[str, Any]:
    merged = normalize_properties(existing, source=source, entity_type=entity_type)
    incoming_props = normalize_properties(incoming, source=source, entity_type=entity_type)

    for key, value in incoming_props.items():
        if value is None:
            continue
        if key == "source_context":
            merged[key] = merge_source_context(merged.get(key), value, actor=actor)
        elif key == "evidence":
            merged[key] = merge_evidence(merged.get("evidence"), value)
        elif key in {"aliases", "tags"}:
            merged[key] = merge_unique_list(merged.get(key), value)
        elif key == "confidence" and merged.get("confidence") is not None:
            try:
                merged[key] = max(float(merged.get(key)), float(value))
            except (TypeError, ValueError):
                merged[key] = value
        elif key in {"last_user_modified_at"} and merged.get(key) and not high_authority:
            continue
        elif key == "status" and merged.get("status") in {"archived", "needs_review"} and not high_authority:
            continue
        else:
            merged[key] = value

    if source_context or actor:
        merged["source_context"] = merge_source_context(
            merged.get("source_context"),
            source_context,
            actor=actor,
        )
    if high_authority:
        merged["last_user_modified_at"] = now_iso()
        if merged.get("status") in {"needs_review", "superseded"}:
            merged["status"] = "active"
        if merged.get("status") == "active":
            merged.pop("review_reason", None)
            merged.pop("review_candidate", None)
    merged["evidence_count"] = max(
        int(merged.get("evidence_count") or 0),
        len(merged.get("evidence") or []),
    )
    return normalize_properties(merged, source=source, entity_type=entity_type)


def _load_journal() -> list[dict[str, Any]]:
    try:
        if not _JOURNAL_FILE.exists():
            return []
        data = json.loads(_JOURNAL_FILE.read_text(encoding="utf-8") or "[]")
        return data if isinstance(data, list) else []
    except Exception:
        return []


def get_journal(limit: int = 50) -> list[dict[str, Any]]:
    return _load_journal()[-limit:]


def append_journal(
    action: str,
    *,
    entity_id: str | None = None,
    entity_ids: list[str] | None = None,
    actor: str = "system",
    reason: str = "",
    source: str = "",
    old_status: str | None = None,
    new_status: str | None = None,
    details: dict | None = None,
) -> None:
    try:
        _JOURNAL_FILE.parent.mkdir(parents=True, exist_ok=True)
        rows = _load_journal()
        rows.append({
            "timestamp": now_iso(),
            "action": action,
            "entity_id": entity_id,
            "entity_ids": entity_ids or ([entity_id] if entity_id else []),
            "actor": actor,
            "reason": reason,
            "source": source,
            "old_status": old_status,
            "new_status": new_status,
            "details": details or {},
        })
        _JOURNAL_FILE.write_text(
            json.dumps(rows[-JOURNAL_MAX_ENTRIES:], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        logger.debug("Failed to append memory evolution journal", exc_info=True)


def _update_entity_properties(entity_id: str, props: dict[str, Any]) -> dict | None:
    import row_bot.knowledge_graph as kg

    entity = kg.get_entity(entity_id)
    if not entity:
        return None
    return kg.update_entity(
        entity_id,
        entity.get("description", "") or "",
        properties=props,
    )


def set_status(
    entity_id: str,
    status: str,
    *,
    reason: str = "",
    actor: str = "system",
    extra: dict | None = None,
) -> dict | None:
    import row_bot.knowledge_graph as kg

    entity = kg.get_entity(entity_id)
    if not entity:
        return None
    props = normalize_properties(
        get_properties(entity),
        source=entity.get("source", ""),
        entity_type=entity.get("entity_type", ""),
    )
    old_status = props.get("status", "active")
    props["status"] = normalize_status(status)
    props["last_evolved_at"] = now_iso()
    if reason:
        props["review_reason" if props["status"] == "needs_review" else "evolution_reason"] = reason
    if extra:
        props.update(extra)
    updated = _update_entity_properties(entity_id, props)
    append_journal(
        "set_status",
        entity_id=entity_id,
        actor=actor,
        reason=reason,
        source=entity.get("source", ""),
        old_status=old_status,
        new_status=props["status"],
    )
    return updated


def mark_needs_review(
    entity_id: str,
    reason: str,
    *,
    actor: str = "system",
    incoming: dict | None = None,
) -> dict | None:
    extra = {"review_reason": reason[:500]}
    if incoming:
        extra["review_candidate"] = {
            key: incoming.get(key)
            for key in ("subject", "category", "entity_type", "content", "description", "source")
            if incoming.get(key)
        }
    return set_status(entity_id, "needs_review", reason=reason, actor=actor, extra=extra)


def mark_superseded(
    old_id: str,
    new_id: str,
    *,
    reason: str = "",
    actor: str = "system",
) -> tuple[dict | None, dict | None]:
    import row_bot.knowledge_graph as kg

    old = kg.get_entity(old_id)
    new = kg.get_entity(new_id)
    if not old or not new:
        return None, None

    old_props = normalize_properties(
        get_properties(old),
        source=old.get("source", ""),
        entity_type=old.get("entity_type", ""),
    )
    new_props = normalize_properties(
        get_properties(new),
        source=new.get("source", ""),
        entity_type=new.get("entity_type", ""),
    )
    old_status = old_props.get("status", "active")
    old_props["status"] = "superseded"
    old_props["superseded_by"] = new_id
    old_props["last_evolved_at"] = now_iso()
    supersedes = list(dict.fromkeys([*(new_props.get("supersedes") or []), old_id]))
    new_props["supersedes"] = supersedes[:SUPERSEDES_MAX_ITEMS]
    new_props["status"] = normalize_status(new_props.get("status"))
    new_props["last_evolved_at"] = now_iso()

    updated_old = _update_entity_properties(old_id, old_props)
    updated_new = _update_entity_properties(new_id, new_props)
    append_journal(
        "supersede",
        entity_ids=[old_id, new_id],
        actor=actor,
        reason=reason,
        source=new.get("source", ""),
        old_status=old_status,
        new_status="superseded",
        details={"old_id": old_id, "new_id": new_id},
    )
    return updated_old, updated_new


def mark_user_modified(
    entity_id: str,
    *,
    actor: str = "manual",
    source_context: dict | None = None,
    status: str | None = "active",
) -> dict | None:
    import row_bot.knowledge_graph as kg

    entity = kg.get_entity(entity_id)
    if not entity:
        return None
    props = merge_properties(
        get_properties(entity),
        {"status": status or "active"},
        source=entity.get("source", ""),
        entity_type=entity.get("entity_type", ""),
        actor=actor,
        source_context=source_context,
        high_authority=True,
    )
    updated = _update_entity_properties(entity_id, props)
    append_journal(
        "user_modified",
        entity_id=entity_id,
        actor=actor,
        reason="high_authority_update",
        source=entity.get("source", ""),
        new_status=props.get("status"),
    )
    return updated
