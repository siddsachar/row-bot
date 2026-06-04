"""Display helpers for Knowledge audit surfaces.

The functions here are intentionally UI-framework free so Settings, the graph
panel, and tests can share the same normalization rules.
"""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from typing import Any

STATUS_OPTIONS = ["All", "Active", "Needs review", "Superseded", "Archived"]
SOURCE_OPTIONS = ["All", "Manual/live", "Extraction", "Document", "Wiki/dream", "Other"]
TIER_OPTIONS = ["All", "Core", "Semantic", "Episodic", "Resource"]

STATUS_COLORS = {
    "active": "positive",
    "needs_review": "warning",
    "superseded": "grey",
    "archived": "grey",
}


def parse_properties(raw: Any) -> dict[str, Any]:
    """Return entity properties as a dict, tolerating legacy/raw values."""
    if isinstance(raw, str):
        try:
            loaded = json.loads(raw or "{}")
        except (json.JSONDecodeError, TypeError):
            return {}
        return dict(loaded) if isinstance(loaded, dict) else {}
    return dict(raw) if isinstance(raw, dict) else {}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _truncate(text: str, limit: int = 180) -> str:
    text = " ".join(_clean(text).split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "..."


def normalize_status(value: Any) -> str:
    status = _clean(value).lower()
    return status if status in {"active", "needs_review", "superseded", "archived"} else "active"


def status_label(status: str) -> str:
    return {
        "active": "Active",
        "needs_review": "Needs review",
        "superseded": "Superseded",
        "archived": "Archived",
    }.get(normalize_status(status), "Active")


def normalize_tier(value: Any, *, source: str = "", entity_type: str = "") -> str:
    tier = _clean(value).lower()
    if tier in {"core", "semantic", "episodic", "resource"}:
        return tier
    if source.startswith("document:") or entity_type == "media":
        return "resource"
    return "semantic"


def tier_label(tier: str) -> str:
    return {
        "core": "Core",
        "semantic": "Semantic",
        "episodic": "Episodic",
        "resource": "Resource",
    }.get(normalize_tier(tier), "Semantic")


def source_bucket(source: str, props: dict[str, Any] | None = None) -> str:
    props = props or {}
    source = _clean(source).lower()
    ctx = props.get("source_context")
    ctx = ctx if isinstance(ctx, dict) else {}
    actor = _clean(ctx.get("actor")).lower()
    kind = _clean(ctx.get("kind")).lower()

    if source.startswith("document:") or kind == "document":
        return "Document"
    if actor == "wiki" or source.startswith("wiki") or source.startswith("dream"):
        return "Wiki/dream"
    if actor == "extraction" or source in {"extraction", "background_extraction"}:
        return "Extraction"
    if actor == "manual" or source in {"live", "manual", "chat", ""}:
        return "Manual/live"
    return "Other"


def source_label(source: str, props: dict[str, Any] | None = None) -> str:
    bucket = source_bucket(source, props)
    raw = _clean(source)
    return bucket if not raw else f"{bucket}: {raw}"


def _evidence_items(props: dict[str, Any]) -> list[str]:
    raw = props.get("evidence") or []
    values = raw if isinstance(raw, list) else [raw]
    items: list[str] = []
    for item in values:
        if isinstance(item, dict):
            text = item.get("quote") or item.get("text") or item.get("content") or item.get("summary")
        else:
            text = item
        text = _truncate(_clean(text), 160)
        if text and text not in items:
            items.append(text)
    return items


def source_context_lines(props: dict[str, Any]) -> list[str]:
    ctx = props.get("source_context")
    if not isinstance(ctx, dict):
        return []
    keys = (
        "actor",
        "kind",
        "thread_name",
        "thread_id",
        "display_name",
        "document_title",
        "vault_path",
        "window_count",
        "chunk",
        "page",
    )
    lines: list[str] = []
    for key in keys:
        value = ctx.get(key)
        if value not in (None, "", []):
            lines.append(f"{key.replace('_', ' ')}: {_truncate(str(value), 160)}")
    return lines


def audit_summary(entity: dict[str, Any] | None) -> dict[str, Any]:
    """Return display-ready audit metadata for a memory/entity."""
    entity = entity or {}
    props = parse_properties(entity.get("properties", {}))
    source = _clean(entity.get("source"))
    entity_type = _clean(entity.get("entity_type") or entity.get("category"))
    status = normalize_status(props.get("status"))
    tier = normalize_tier(props.get("memory_tier"), source=source, entity_type=entity_type)
    evidence = _evidence_items(props)

    confidence = props.get("confidence")
    try:
        confidence_value = float(confidence) if confidence is not None else None
    except (TypeError, ValueError):
        confidence_value = None

    evidence_count = props.get("evidence_count")
    try:
        evidence_count = int(evidence_count)
    except (TypeError, ValueError):
        evidence_count = len(evidence)
    evidence_count = max(evidence_count, len(evidence))

    return {
        "id": entity.get("id", ""),
        "subject": entity.get("subject", ""),
        "entity_type": entity_type,
        "status": status,
        "status_label": status_label(status),
        "status_color": STATUS_COLORS.get(status, "blue-grey"),
        "tier": tier,
        "tier_label": tier_label(tier),
        "source": source,
        "source_bucket": source_bucket(source, props),
        "source_label": source_label(source, props),
        "confidence": confidence_value,
        "confidence_label": f"{confidence_value:.0%}" if confidence_value is not None else "",
        "review_reason": _clean(props.get("review_reason")),
        "review_candidate": props.get("review_candidate") if isinstance(props.get("review_candidate"), dict) else {},
        "superseded_by": _clean(props.get("superseded_by")),
        "supersedes": props.get("supersedes") if isinstance(props.get("supersedes"), list) else [],
        "recalled_at": _clean(props.get("recalled_at")),
        "recall_count": props.get("recall_count", ""),
        "last_user_modified_at": _clean(props.get("last_user_modified_at")),
        "last_evolved_at": _clean(props.get("last_evolved_at")),
        "evidence_count": evidence_count,
        "evidence": evidence[:3],
        "source_context_lines": source_context_lines(props),
        "properties": props,
    }


def _matches_search(mem: dict[str, Any], query: str) -> bool:
    query = _clean(query).lower()
    if not query:
        return True
    props = parse_properties(mem.get("properties", {}))
    haystack = " ".join(
        _clean(v)
        for v in (
            mem.get("subject"),
            mem.get("description"),
            mem.get("content"),
            mem.get("aliases"),
            mem.get("tags"),
            props.get("review_reason"),
        )
    ).lower()
    return query in haystack


def filter_memories(
    memories: list[dict[str, Any]],
    *,
    status: str = "All",
    source: str = "All",
    tier: str = "All",
    query: str = "",
) -> list[dict[str, Any]]:
    """Filter memory rows using normalized audit fields."""
    status_key = _clean(status).lower().replace(" ", "_")
    source_key = _clean(source)
    tier_key = _clean(tier).lower()
    filtered: list[dict[str, Any]] = []
    for mem in memories:
        audit = audit_summary(mem)
        if status_key and status_key != "all" and audit["status"] != status_key:
            continue
        if source_key and source_key != "All" and audit["source_bucket"] != source_key:
            continue
        if tier_key and tier_key != "all" and audit["tier"] != tier_key:
            continue
        if not _matches_search(mem, query):
            continue
        filtered.append(mem)
    return filtered


def status_counts(memories: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(audit_summary(mem)["status"] for mem in memories)
    return {status: counts.get(status, 0) for status in ("active", "needs_review", "superseded", "archived")}


def bucket_counts(memories: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for mem in memories:
        audit = audit_summary(mem)
        counts[_clean(audit.get(key)) or "Other"] += 1
    return dict(counts)


def load_recent_recall_traces(limit: int = 10) -> list[dict[str, Any]]:
    """Load recent recall decisions without touching memories."""
    try:
        import row_bot.memory_policy as memory_policy

        path = Path(memory_policy._RECALL_TRACE_FILE)
        if not path.exists():
            return []
        rows = json.loads(path.read_text(encoding="utf-8") or "[]")
        if not isinstance(rows, list):
            return []
        return [row for row in rows[-limit:] if isinstance(row, dict)]
    except Exception:
        return []


def load_recent_evolution_journal(limit: int = 20) -> list[dict[str, Any]]:
    """Load recent memory evolution journal entries."""
    try:
        import row_bot.memory_evolution as memory_evolution

        rows = memory_evolution.get_journal(limit=limit)
        return [row for row in rows if isinstance(row, dict)]
    except Exception:
        return []

