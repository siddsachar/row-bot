"""Thoth Insights Engine — storage and management for automated insights.

The dream cycle's Phase 5 generates insights about error patterns, tool
configuration, knowledge quality, usage patterns, and system health.
Insights are stored as JSON and surfaced in the Command Center UI.
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from row_bot.data_paths import get_row_bot_data_dir

logger = logging.getLogger(__name__)

_DATA_DIR = get_row_bot_data_dir()
_INSIGHTS_PATH = _DATA_DIR / "insights.json"

# ── Constants ────────────────────────────────────────────────────────────────

MAX_ACTIVE_INSIGHTS = 50
AUTO_PRUNE_DAYS = 14
DEDUP_SIMILARITY_THRESHOLD = 0.85
SEMANTIC_DEDUP_SIMILARITY_THRESHOLD = 0.85

VALID_CATEGORIES = {
    "error_pattern",
    "skill_proposal",
    "tool_config",
    "knowledge_quality",
    "usage_pattern",
    "system_health",
}

VALID_SEVERITIES = {"info", "warning", "critical"}

VALID_STATUSES = {"new", "pinned", "dismissed", "applied", "reviewed"}
INACTIVE_STATUSES = {"dismissed", "applied", "reviewed"}

CATEGORY_ICONS = {
    "error_pattern": "🔴",
    "skill_proposal": "🧩",
    "tool_config": "⚙️",
    "knowledge_quality": "🧠",
    "usage_pattern": "📊",
    "system_health": "🏥",
}

SEVERITY_SORT = {"critical": 0, "warning": 1, "info": 2}


# ── Storage ──────────────────────────────────────────────────────────────────

def _load_store() -> dict:
    """Load the insights store from disk."""
    try:
        if _INSIGHTS_PATH.exists():
            return json.loads(_INSIGHTS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to load insights: %s", exc)
    return {"insights": [], "meta": {"last_analysis": None, "total_generated": 0,
                                      "total_dismissed": 0, "total_applied": 0}}


def _save_store(store: dict) -> None:
    """Persist the insights store to disk."""
    try:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        _INSIGHTS_PATH.write_text(
            json.dumps(store, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except OSError as exc:
        logger.warning("Failed to save insights: %s", exc)


# ── CRUD ─────────────────────────────────────────────────────────────────────

def add_insight(
    *,
    category: str,
    severity: str = "info",
    title: str,
    body: str,
    evidence: Optional[list[str]] = None,
    suggestion: str = "",
    auto_fixable: bool = False,
    confidence: float = 0.5,
    source_cycle: str = "",
    skill_draft: Optional[dict] = None,
) -> Optional[dict]:
    """Add a new insight, deduplicating against existing ones.

    Returns the insight dict if added/merged, or None if rejected as duplicate.
    """
    if category not in VALID_CATEGORIES:
        logger.warning("Invalid insight category: %s", category)
        return None
    if severity not in VALID_SEVERITIES:
        severity = "info"

    store = _load_store()
    insights = store["insights"]

    # Deduplicate: check for similar active insights
    for existing in insights:
        if existing["status"] in INACTIVE_STATUSES:
            continue
        if existing["category"] != category:
            continue

        similarity, threshold = _insight_similarity(existing, category, title)
        if similarity >= threshold:
            # Merge: update evidence and bump confidence
            existing["evidence"] = list(set(existing.get("evidence", []) + (evidence or [])))
            existing["confidence"] = min(1.0, max(existing["confidence"], confidence))
            existing["body"] = body  # use latest description
            if severity == "critical" or (severity == "warning" and existing["severity"] == "info"):
                existing["severity"] = severity
            logger.info("Merged insight into existing: %s", existing["id"])
            _save_store(store)
            existing["_merged"] = True  # transient flag for callers
            return existing

    # New insight
    insight = {
        "id": f"ins_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}",
        "created": datetime.now(timezone.utc).isoformat(),
        "category": category,
        "severity": severity,
        "title": title,
        "body": body,
        "evidence": evidence or [],
        "suggestion": suggestion,
        "auto_fixable": auto_fixable,
        "confidence": round(confidence, 2),
        "status": "new",
        "source_cycle": source_cycle,
        "skill_draft": skill_draft,
    }

    insights.append(insight)
    store["meta"]["total_generated"] = store["meta"].get("total_generated", 0) + 1

    # Cap active insights
    _enforce_cap(insights)

    _save_store(store)
    logger.info("Added insight: %s — %s", insight["id"], title)
    return insight


def get_insights(
    *,
    status: Optional[str] = None,
    category: Optional[str] = None,
    include_dismissed: bool = False,
) -> list[dict]:
    """Return insights, optionally filtered. Sorted by severity then recency."""
    store = _load_store()
    results = store["insights"]

    if not include_dismissed:
        results = [i for i in results if i["status"] not in INACTIVE_STATUSES]
    if status:
        results = [i for i in results if i["status"] == status]
    if category:
        results = [i for i in results if i["category"] == category]

    # Sort by newest first, then stable-sort by severity so critical/warning
    # always rise to the top while preserving recency ordering within each tier.
    results.sort(key=lambda i: i.get("created", ""), reverse=True)
    results.sort(key=lambda i: SEVERITY_SORT.get(i.get("severity", "info"), 2))

    return results


def get_active_insights() -> list[dict]:
    """Return insights visible in the UI (new + pinned)."""
    return get_insights(status=None, include_dismissed=False)


def update_insight_status(insight_id: str, new_status: str) -> bool:
    """Change an insight's status. Returns True on success."""
    if new_status not in VALID_STATUSES:
        return False
    store = _load_store()
    for insight in store["insights"]:
        if insight["id"] == insight_id:
            old = insight["status"]
            insight["status"] = new_status
            if new_status == "dismissed":
                store["meta"]["total_dismissed"] = store["meta"].get("total_dismissed", 0) + 1
            elif new_status == "applied":
                store["meta"]["total_applied"] = store["meta"].get("total_applied", 0) + 1
            _save_store(store)
            logger.info("Insight %s: %s → %s", insight_id, old, new_status)
            return True
    return False


def dismiss_insight(insight_id: str) -> bool:
    """Dismiss an insight (hides it from the UI)."""
    return update_insight_status(insight_id, "dismissed")


def pin_insight(insight_id: str) -> bool:
    """Pin an insight (prevents auto-prune)."""
    return update_insight_status(insight_id, "pinned")


def get_insight_by_id(insight_id: str) -> Optional[dict]:
    """Look up a single insight by ID."""
    store = _load_store()
    for insight in store["insights"]:
        if insight["id"] == insight_id:
            return insight
    return None


def apply_insight(insight_id: str) -> dict:
    """Apply a backend-fixable insight and mark it handled."""
    insight = get_insight_by_id(insight_id)
    if not insight:
        return {"ok": False, "message": "Insight not found", "action": None}

    status = insight.get("status", "new")
    if status in INACTIVE_STATUSES:
        return {
            "ok": False,
            "message": f"Insight already {status}",
            "action": None,
        }

    if insight.get("category") != "skill_proposal":
        return {
            "ok": False,
            "message": "Only skill proposal insights can be applied automatically",
            "action": None,
        }

    if not insight.get("auto_fixable"):
        return {
            "ok": False,
            "message": "Insight is not marked auto-fixable",
            "action": None,
        }

    draft = insight.get("skill_draft")
    if not isinstance(draft, dict):
        return {"ok": False, "message": "Insight has no skill draft", "action": None}

    name = (draft.get("name", "") or "").strip()
    if not name:
        return {"ok": False, "message": "Skill draft has no name", "action": None}

    try:
        from row_bot.skills import create_skill, get_skill

        existing = get_skill(name)
        if existing is not None:
            return {
                "ok": False,
                "message": f"Skill already exists: {name}",
                "action": None,
            }

        skill = create_skill(
            name=name,
            display_name=draft.get("display_name", name) or name,
            icon=draft.get("icon", "🧩"),
            description=draft.get("description", ""),
            instructions=draft.get("instructions", ""),
            tags=draft.get("tags"),
            enabled=bool(draft.get("enabled", draft.get("enabled_by_default", True))),
            version=draft.get("version", "1.0"),
        )
    except ValueError as exc:
        return {"ok": False, "message": str(exc), "action": None}
    except Exception as exc:
        logger.warning("Failed to apply insight %s: %s", insight_id, exc, exc_info=True)
        return {
            "ok": False,
            "message": f"Failed to create skill: {exc}",
            "action": None,
        }

    if not skill:
        return {"ok": False, "message": "Failed to create skill", "action": None}

    if not update_insight_status(insight_id, "applied"):
        return {
            "ok": False,
            "message": "Skill created but failed to mark insight applied",
            "action": skill.name,
        }

    return {
        "ok": True,
        "message": f"Skill created: {skill.display_name}",
        "action": skill.name,
    }


def get_insights_meta() -> dict:
    """Return the meta section of the insights store."""
    return _load_store().get("meta", {})


def set_last_analysis(timestamp: Optional[str] = None) -> None:
    """Record the timestamp of the last insights analysis."""
    store = _load_store()
    store["meta"]["last_analysis"] = timestamp or datetime.now(timezone.utc).isoformat()
    _save_store(store)


# ── Maintenance ──────────────────────────────────────────────────────────────

def auto_prune() -> int:
    """Dismiss insights older than AUTO_PRUNE_DAYS with status 'new'.

    Returns the number of pruned insights.
    """
    store = _load_store()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=AUTO_PRUNE_DAYS)).isoformat()
    pruned = 0
    for insight in store["insights"]:
        if insight["status"] == "new" and insight.get("created", "") < cutoff:
            insight["status"] = "dismissed"
            pruned += 1
    if pruned:
        store["meta"]["total_dismissed"] = store["meta"].get("total_dismissed", 0) + pruned
        _save_store(store)
        logger.info("Auto-pruned %d stale insights", pruned)
    return pruned


def _enforce_cap(insights: list[dict]) -> None:
    """Ensure active insights don't exceed MAX_ACTIVE_INSIGHTS.

    Dismisses oldest low-severity insights first.
    """
    active = [i for i in insights if i["status"] in ("new", "pinned")]
    if len(active) <= MAX_ACTIVE_INSIGHTS:
        return
    # Sort: info before warning before critical, oldest first
    active.sort(key=lambda i: (
        -SEVERITY_SORT.get(i.get("severity", "info"), 2),  # info first (highest number)
        i.get("created", ""),
    ))
    excess = len(active) - MAX_ACTIVE_INSIGHTS
    for i in range(excess):
        active[i]["status"] = "dismissed"
    logger.info("Capped insights: dismissed %d to stay under %d", excess, MAX_ACTIVE_INSIGHTS)


# ── Deduplication ────────────────────────────────────────────────────────────

def _cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """Compute cosine similarity for two embedding vectors."""
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0

    dot = sum(float(a) * float(b) for a, b in zip(vec_a, vec_b))
    norm_a = sum(float(a) * float(a) for a in vec_a) ** 0.5
    norm_b = sum(float(b) * float(b) for b in vec_b) ** 0.5
    if not norm_a or not norm_b:
        return 0.0
    return dot / (norm_a * norm_b)


def _supports_semantic_dedup(title: str) -> bool:
    """Use semantic dedup only for titles with enough information content."""
    tokens = [
        token.strip(".,:;!?()[]{}\"'").lower()
        for token in title.split()
    ]
    tokens = [token for token in tokens if token]
    return len(tokens) >= 3


def _insight_similarity(existing: dict, category: str, title: str) -> tuple[float, float]:
    """Return the best available similarity score and its threshold."""
    existing_title = existing.get("title", "")
    if not (_supports_semantic_dedup(existing_title) and _supports_semantic_dedup(title)):
        return _title_similarity(existing_title, title), DEDUP_SIMILARITY_THRESHOLD

    try:
        from row_bot.knowledge_graph import _get_embedding_model

        emb = _get_embedding_model()
        existing_vec = emb.embed_query(f"{existing.get('category', '')}: {existing_title}")
        new_vec = emb.embed_query(f"{category}: {title}")
        return _cosine_similarity(existing_vec, new_vec), SEMANTIC_DEDUP_SIMILARITY_THRESHOLD
    except Exception as exc:
        logger.debug("Insight semantic dedup fallback: %s", exc)
        return _title_similarity(existing_title, title), DEDUP_SIMILARITY_THRESHOLD

def _title_similarity(a: str, b: str) -> float:
    """Quick word-overlap similarity between two titles (Jaccard)."""
    words_a = set(a.lower().split())
    words_b = set(b.lower().split())
    if not words_a or not words_b:
        return 0.0
    intersection = words_a & words_b
    union = words_a | words_b
    return len(intersection) / len(union)
