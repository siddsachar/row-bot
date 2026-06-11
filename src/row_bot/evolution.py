"""Controlled self-evolution proposals, audits, and packaged-safe validators."""

from __future__ import annotations

import difflib
import json
import logging
import platform
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from row_bot.data_paths import get_row_bot_data_dir
from row_bot.tools.approval_gate import gate_action

logger = logging.getLogger(__name__)

VALID_PROPOSAL_TYPES = {
    "investigate",
    "create_skill",
    "patch_skill",
    "consolidate_skills",
    "send_feedback",
    "settings_change",
    "memory_correction",
}
VALID_PROPOSAL_STATUSES = {
    "draft",
    "ready",
    "approved",
    "applied",
    "verified",
    "rejected",
    "failed",
}
VALID_RISKS = {"low", "medium", "high"}
TERMINAL_PROPOSAL_STATUSES = {"applied", "verified", "rejected", "failed"}
MUTATING_PROPOSAL_TYPES = {
    "create_skill",
    "patch_skill",
    "consolidate_skills",
    "send_feedback",
    "settings_change",
    "memory_correction",
}
SKILL_MUTATION_TYPES = {"create_skill", "patch_skill", "consolidate_skills"}

MAX_SKILL_PATCH_CHANGED_LINES = 80
MAX_DIFF_PREVIEW_LINES = 220
MAX_FEEDBACK_BODY_CHARS = 8000
MAX_LOG_CHARS = 2500
MAX_SKILL_TOKENS_WARNING = 1800

_SKILL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_SKILL_BLOCKED_SURFACE_TERMS = {
    "api key",
    "automation",
    "background",
    "cache",
    "catalog",
    "config",
    "configuration",
    "database",
    "discovery",
    "error",
    "failed",
    "failure",
    "graph",
    "integration",
    "keyring",
    "log",
    "logs",
    "mcp",
    "model",
    "payload",
    "performance",
    "provider",
    "refresh",
    "render",
    "scheduler",
    "slow",
    "startup",
    "status bar",
    "system",
    "task hygiene",
    "enabled task",
    "enabled tasks",
    "scheduled task",
    "scheduled tasks",
    "test automation",
    "test automations",
    "have not run",
    "never run",
    "duplicated test",
    "timeout",
    "tool",
    "tools",
    "xai",
}
_SKILL_POSITIVE_WORKFLOW_TERMS = {
    "user repeatedly",
    "user often",
    "repeatedly asks",
    "workflow",
    "writing",
    "research",
    "review",
    "summarize",
    "draft",
    "planning",
    "analysis",
}
_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{16,}"),
    re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(
        r"(?i)\b(api[_-]?key|token|secret|password|credential)\b\s*[:=]\s*['\"]?[^'\"\s,;]{8,}"
    ),
]
_WINDOWS_USER_PATH_RE = re.compile(r"[A-Za-z]:\\Users\\[^\\\r\n\t ]+")
_POSIX_USER_PATH_RE = re.compile(r"/(?:Users|home)/[^/\r\n\t ]+")
_EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"{prefix}_{stamp}_{uuid.uuid4().hex[:8]}"


def _data_dir() -> Path:
    root = get_row_bot_data_dir()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _store_path() -> Path:
    return _data_dir() / "controlled_evolution.json"


def _feedback_reports_dir() -> Path:
    return _data_dir() / "feedback_reports"


def _backups_dir() -> Path:
    return _data_dir() / "evolution_backups"


def _blank_store() -> dict[str, Any]:
    return {
        "proposals": [],
        "action_runs": [],
        "rejected_proposals": [],
        "outcomes": [],
        "curator_reports": [],
        "meta": {"schema_version": 1},
    }


def _load_store() -> dict[str, Any]:
    path = _store_path()
    if not path.exists():
        return _blank_store()
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to load controlled evolution store: %s", exc)
        return _blank_store()
    store = _blank_store()
    if isinstance(loaded, dict):
        for key in store:
            if key in loaded:
                store[key] = loaded[key]
    for list_key in ("proposals", "action_runs", "rejected_proposals", "outcomes", "curator_reports"):
        if not isinstance(store.get(list_key), list):
            store[list_key] = []
    if not isinstance(store.get("meta"), dict):
        store["meta"] = {"schema_version": 1}
    _migrate_legacy_feedback_records(store)
    return store


def _save_store(store: dict[str, Any]) -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(store, indent=2, ensure_ascii=False), encoding="utf-8")


def _migrate_legacy_feedback_records(store: dict[str, Any]) -> None:
    """Treat pre-rename report_issue proposals as send-feedback drafts."""

    try:
        from row_bot.brand import APP_SUPPORT_URL
    except Exception:
        APP_SUPPORT_URL = ""

    for proposal in store.get("proposals", []):
        if not isinstance(proposal, dict) or proposal.get("proposal_type") != "report_issue":
            continue
        proposal["proposal_type"] = "send_feedback"
        title = str(proposal.get("title") or "")
        if title.startswith("Report issue:"):
            proposal["title"] = "Send feedback:" + title[len("Report issue:") :]
        payload = _safe_payload(proposal.get("payload"))
        draft = payload.get("feedback_draft") or payload.get("issue_draft") or {}
        proposal["payload"] = {"feedback_draft": draft} if isinstance(draft, dict) else {}
        preview = _safe_preview(proposal.get("preview"))
        preview_draft = preview.get("feedback_draft") or preview.get("issue_draft") or draft
        preview = {"feedback_draft": preview_draft} if isinstance(preview_draft, dict) else {}
        if APP_SUPPORT_URL:
            preview["contact_url"] = APP_SUPPORT_URL
        proposal["preview"] = preview
        proposal["fingerprint"] = _proposal_fingerprint(
            "send_feedback",
            str(proposal.get("title") or ""),
            [str(item) for item in (proposal.get("insight_ids") or [])],
            proposal["payload"],
        )

    for action_run in store.get("action_runs", []):
        if isinstance(action_run, dict) and action_run.get("action_type") == "report_issue":
            action_run["action_type"] = "send_feedback"


def _word_tokens(text: str) -> set[str]:
    stop = {
        "the",
        "and",
        "for",
        "with",
        "from",
        "this",
        "that",
        "when",
        "into",
        "your",
        "skill",
        "skills",
        "use",
        "using",
        "work",
        "workflow",
    }
    return {
        token
        for token in re.findall(r"[a-z0-9_]{3,}", (text or "").lower())
        if token not in stop
    }


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _coerce_confidence(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.5


def _safe_preview(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {"text": str(value or "")}


def _safe_payload(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def normalize_skill_name(name: str) -> str:
    value = re.sub(r"[^a-z0-9_]+", "_", str(name or "").strip().lower())
    value = re.sub(r"_+", "_", value).strip("_")
    if value and not value[0].isalpha():
        value = f"skill_{value}"
    return value[:64].rstrip("_")


def _title_to_skill_name(title: str) -> str:
    return normalize_skill_name(title) or f"skill_{uuid.uuid4().hex[:6]}"


def _validate_skill_name(name: str) -> str | None:
    if not name:
        return "Skill name is required."
    if not _SKILL_NAME_RE.fullmatch(name):
        return "Skill name must be snake_case, start with a letter, and be 2-64 characters."
    if any(part in name for part in ("/", "\\", "..")):
        return "Skill name must not contain path separators."
    return None


def redact_text(text: str, *, max_chars: int | None = None) -> str:
    """Redact secrets, user-local paths, emails, and oversized content."""

    value = str(text or "")
    for pattern in _SECRET_PATTERNS:
        value = pattern.sub("[redacted-secret]", value)
    value = _WINDOWS_USER_PATH_RE.sub("[local-user-path]", value)
    value = _POSIX_USER_PATH_RE.sub("[local-user-path]", value)
    value = _EMAIL_RE.sub("[redacted-email]", value)
    if max_chars is not None and len(value) > max_chars:
        value = value[:max_chars].rstrip() + "\n\n[truncated]"
    return value


def contains_unredacted_sensitive_text(text: str) -> bool:
    value = str(text or "")
    return any(pattern.search(value) for pattern in _SECRET_PATTERNS)


def detect_skill_overlaps(
    *,
    name: str = "",
    description: str = "",
    instructions: str = "",
    include_tool_guides: bool = True,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Return rough lexical overlap matches across bundled, installed, and user skills."""

    try:
        from row_bot.skills import get_all_skills, is_tool_guide, load_skills, skills_loaded

        if not skills_loaded():
            load_skills()
        query_text = " ".join([name, description, instructions])
        query_tokens = _word_tokens(query_text)
        query_name = normalize_skill_name(name)
        matches: list[dict[str, Any]] = []
        for skill in get_all_skills():
            guide = is_tool_guide(skill)
            if guide and not include_tool_guides:
                continue
            skill_text = " ".join(
                [skill.name, skill.display_name, skill.description, skill.instructions[:4000]]
            )
            score = _jaccard(query_tokens, _word_tokens(skill_text))
            if query_name and query_name == skill.name:
                score = max(score, 1.0)
            elif query_name and query_name in {normalize_skill_name(skill.display_name), normalize_skill_name(skill.name)}:
                score = max(score, 0.9)
            if score < 0.12:
                continue
            matches.append(
                {
                    "name": skill.name,
                    "display_name": skill.display_name,
                    "source": skill.source,
                    "is_tool_guide": guide,
                    "score": round(score, 3),
                    "description": skill.description,
                }
            )
        matches.sort(key=lambda item: item["score"], reverse=True)
        return matches[:limit]
    except Exception:
        logger.debug("Skill overlap detection failed", exc_info=True)
        return []


def build_skill_diff_preview(target_skill_name: str, updated_instructions: str) -> dict[str, Any]:
    skill = _resolve_skill(target_skill_name)
    if not skill:
        return {"diff": "", "changed_lines": 0, "error": f"Skill not found: {target_skill_name}"}
    current = str(skill.instructions or "")
    updated = str(updated_instructions or "")
    diff_lines = list(
        difflib.unified_diff(
            current.splitlines(),
            updated.splitlines(),
            fromfile=f"{skill.name}/current",
            tofile=f"{skill.name}/proposed",
            lineterm="",
        )
    )
    changed = sum(
        1
        for line in diff_lines
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
    )
    truncated = len(diff_lines) > MAX_DIFF_PREVIEW_LINES
    if truncated:
        diff_lines = diff_lines[:MAX_DIFF_PREVIEW_LINES] + ["... diff truncated ..."]
    return {
        "diff": "\n".join(diff_lines),
        "changed_lines": changed,
        "bounded": changed <= MAX_SKILL_PATCH_CHANGED_LINES,
        "truncated": truncated,
    }


def _proposal_fingerprint(
    proposal_type: str,
    title: str,
    insight_ids: list[str],
    payload: dict[str, Any],
) -> str:
    payload_bits: list[str] = []
    if proposal_type in {"create_skill", "patch_skill"}:
        payload_bits.append(str(payload.get("name") or payload.get("target_skill") or ""))
    if proposal_type == "send_feedback":
        payload_bits.append(str(payload.get("title") or title))
    basis = "|".join([proposal_type, title.lower().strip(), ",".join(sorted(insight_ids)), *payload_bits])
    return re.sub(r"[^a-z0-9_|-]+", "_", basis.lower())[:240]


def _find_rejection_memory(
    proposal_type: str,
    title: str,
    insight_ids: list[str],
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    store = _load_store()
    fingerprint = _proposal_fingerprint(proposal_type, title, insight_ids, payload)
    title_tokens = _word_tokens(title)
    for rejection in reversed(store.get("rejected_proposals", [])):
        if rejection.get("proposal_type") != proposal_type:
            continue
        if rejection.get("fingerprint") == fingerprint:
            return rejection
        rejected_tokens = _word_tokens(str(rejection.get("title") or ""))
        if title_tokens and _jaccard(title_tokens, rejected_tokens) >= 0.65:
            return rejection
    return None


def _existing_active_proposal(
    proposal_type: str,
    title: str,
    insight_ids: list[str],
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    fingerprint = _proposal_fingerprint(proposal_type, title, insight_ids, payload)
    for proposal in _load_store().get("proposals", []):
        if proposal.get("status") in TERMINAL_PROPOSAL_STATUSES:
            continue
        if proposal.get("fingerprint") == fingerprint:
            return proposal
    return None


def create_proposal(
    *,
    insight_ids: list[str] | None,
    proposal_type: str,
    title: str,
    rationale: str,
    risk: str = "low",
    confidence: float = 0.5,
    payload: dict[str, Any] | None = None,
    preview: dict[str, Any] | None = None,
    verification_plan: str = "",
    status: str = "ready",
    dedupe: bool = True,
) -> dict[str, Any]:
    """Create and persist a proposal without applying it."""

    if proposal_type not in VALID_PROPOSAL_TYPES:
        raise ValueError(f"Unsupported proposal type: {proposal_type}")
    if risk not in VALID_RISKS:
        risk = "medium"
    if status not in VALID_PROPOSAL_STATUSES:
        status = "draft"
    safe_payload = _safe_payload(payload)
    safe_preview = _safe_preview(preview)
    safe_insight_ids = [str(item) for item in (insight_ids or []) if str(item or "").strip()]
    title = str(title or proposal_type.replace("_", " ").title()).strip()
    rationale = str(rationale or "").strip()
    existing = _existing_active_proposal(proposal_type, title, safe_insight_ids, safe_payload)
    if dedupe and existing:
        return existing

    rejection = _find_rejection_memory(proposal_type, title, safe_insight_ids, safe_payload)
    if rejection:
        reason = str(rejection.get("reason") or "No reason recorded.")
        safe_preview["previous_rejection"] = {
            "proposal_id": rejection.get("proposal_id"),
            "reason": reason,
            "rejected_at": rejection.get("rejected_at"),
        }
        rationale = (
            f"{rationale}\n\nPrevious similar proposal was rejected: {reason}".strip()
        )
        confidence = min(_coerce_confidence(confidence), 0.45)

    now = _now()
    proposal = {
        "id": _new_id("proposal"),
        "insight_ids": safe_insight_ids,
        "proposal_type": proposal_type,
        "title": title,
        "rationale": rationale,
        "risk": risk,
        "confidence": round(_coerce_confidence(confidence), 2),
        "payload": safe_payload,
        "preview": safe_preview,
        "verification_plan": str(verification_plan or ""),
        "status": status,
        "fingerprint": _proposal_fingerprint(proposal_type, title, safe_insight_ids, safe_payload),
        "created_at": now,
        "updated_at": now,
    }
    validation = validate_proposal(proposal)
    proposal["preview"]["validation"] = validation
    if not validation.get("ok") and status == "ready":
        proposal["status"] = "draft"

    store = _load_store()
    store["proposals"].append(proposal)
    _save_store(store)
    return proposal


def list_proposals(
    *,
    status: str | None = None,
    proposal_type: str | None = None,
    insight_id: str | None = None,
    include_terminal: bool = True,
) -> list[dict[str, Any]]:
    proposals = list(_load_store().get("proposals", []))
    if status:
        proposals = [p for p in proposals if p.get("status") == status]
    if proposal_type:
        proposals = [p for p in proposals if p.get("proposal_type") == proposal_type]
    if insight_id:
        proposals = [p for p in proposals if insight_id in (p.get("insight_ids") or [])]
    if not include_terminal:
        proposals = [p for p in proposals if p.get("status") not in TERMINAL_PROPOSAL_STATUSES]
    proposals.sort(key=lambda p: p.get("created_at", ""), reverse=True)
    return proposals


def list_proposals_for_insight(
    insight_id: str,
    *,
    include_terminal: bool = True,
) -> list[dict[str, Any]]:
    """Return proposals already linked to an insight without creating new ones."""

    insight_id = str(insight_id or "").strip()
    if not insight_id:
        return []
    return list_proposals(insight_id=insight_id, include_terminal=include_terminal)


def has_any_proposal_for_insight(insight_id: str) -> bool:
    return bool(list_proposals_for_insight(insight_id, include_terminal=True))


def _proposal_type_sort_key(proposal_type: str) -> int:
    order = {
        "send_feedback": 0,
        "investigate": 1,
        "create_skill": 2,
        "patch_skill": 3,
        "consolidate_skills": 4,
    }
    return order.get(str(proposal_type or ""), 99)


def _proposal_is_obsolete_for_insight(proposal: dict[str, Any], insight: dict[str, Any] | None) -> bool:
    """Return true for legacy proposals that no longer match normalized insight routing."""

    proposal_type = str(proposal.get("proposal_type") or "")
    if proposal_type not in SKILL_MUTATION_TYPES:
        return False
    if not insight:
        payload = _safe_payload(proposal.get("payload"))
        candidate = {
            "category": "skill_proposal",
            "title": proposal.get("title"),
            "body": proposal.get("rationale"),
            "suggestion": proposal.get("verification_plan"),
            "skill_draft": {
                "name": payload.get("name") or payload.get("target_skill"),
                "display_name": payload.get("display_name"),
                "description": payload.get("description") or payload.get("reason"),
                "instructions": payload.get("instructions") or payload.get("updated_instructions"),
            },
        }
        return _looks_like_system_maintenance(candidate)
    desired_types = set(proposal_types_for_insight(insight))
    if proposal_type not in desired_types:
        return True
    if proposal_type == "create_skill":
        payload = _safe_payload(proposal.get("payload"))
        candidate = dict(insight)
        candidate["skill_draft"] = {
            "name": payload.get("name"),
            "display_name": payload.get("display_name"),
            "description": payload.get("description"),
            "instructions": payload.get("instructions"),
        }
        return not is_skill_evolution_candidate(candidate)
    return False


def _choose_display_proposal(proposals: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Choose one proposal to represent a proposal type in compact UI surfaces."""

    if not proposals:
        return None

    def updated_key(proposal: dict[str, Any]) -> str:
        return str(proposal.get("updated_at") or proposal.get("created_at") or "")

    completed = [
        proposal
        for proposal in proposals
        if proposal.get("status") in {"verified", "applied"}
    ]
    if completed:
        completed.sort(
            key=lambda proposal: (
                2 if proposal.get("status") == "verified" else 1,
                updated_key(proposal),
            ),
            reverse=True,
        )
        return completed[0]

    active = [
        proposal
        for proposal in proposals
        if proposal.get("status") not in TERMINAL_PROPOSAL_STATUSES
    ]
    if active:
        active.sort(
            key=lambda proposal: (
                {"ready": 3, "approved": 2, "draft": 1}.get(str(proposal.get("status") or ""), 0),
                updated_key(proposal),
            ),
            reverse=True,
        )
        return active[0]

    proposals.sort(key=updated_key, reverse=True)
    return proposals[0]


def collapse_proposals_for_display(proposals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse duplicate historical proposals to one visible row per type."""

    grouped: dict[str, list[dict[str, Any]]] = {}
    for proposal in proposals:
        grouped.setdefault(str(proposal.get("proposal_type") or "proposal"), []).append(proposal)
    collapsed = [
        chosen
        for chosen in (_choose_display_proposal(items) for items in grouped.values())
        if chosen is not None
    ]
    collapsed.sort(
        key=lambda proposal: (
            _proposal_type_sort_key(str(proposal.get("proposal_type") or "")),
            str(proposal.get("created_at") or ""),
        )
    )
    return collapsed


def list_display_proposals_for_insight(
    insight: dict[str, Any],
    *,
    include_terminal: bool = True,
) -> list[dict[str, Any]]:
    """Return drawer-friendly linked proposals without obsolete legacy skill rows."""

    insight_id = str((insight or {}).get("id") or "").strip()
    if not insight_id:
        return []
    proposals = list_proposals_for_insight(insight_id, include_terminal=include_terminal)
    proposals = [
        proposal
        for proposal in proposals
        if not _proposal_is_obsolete_for_insight(proposal, insight)
    ]
    return collapse_proposals_for_display(proposals)


def get_proposal(proposal_id: str) -> dict[str, Any] | None:
    for proposal in _load_store().get("proposals", []):
        if proposal.get("id") == proposal_id:
            return proposal
    return None


def update_proposal_status(
    proposal_id: str,
    status: str,
    *,
    error: str = "",
    verification_note: str = "",
) -> bool:
    if status not in VALID_PROPOSAL_STATUSES:
        return False
    store = _load_store()
    for proposal in store.get("proposals", []):
        if proposal.get("id") == proposal_id:
            proposal["status"] = status
            proposal["updated_at"] = _now()
            if error:
                proposal["error"] = error
            if verification_note:
                proposal["verification_note"] = verification_note
            _save_store(store)
            return True
    return False


def reject_proposal(proposal_id: str, reason: str = "") -> dict[str, Any]:
    store = _load_store()
    for proposal in store.get("proposals", []):
        if proposal.get("id") != proposal_id:
            continue
        now = _now()
        proposal["status"] = "rejected"
        proposal["updated_at"] = now
        proposal["rejection_reason"] = str(reason or "")
        rejection = {
            "proposal_id": proposal_id,
            "proposal_type": proposal.get("proposal_type"),
            "title": proposal.get("title"),
            "fingerprint": proposal.get("fingerprint"),
            "insight_ids": proposal.get("insight_ids", []),
            "reason": str(reason or ""),
            "rejected_at": now,
        }
        store["rejected_proposals"].append(rejection)
        store["outcomes"].append(
            {
                "proposal_id": proposal_id,
                "outcome": "rejected",
                "feedback": str(reason or ""),
                "recorded_at": now,
            }
        )
        _save_store(store)
        return proposal
    raise ValueError(f"Proposal not found: {proposal_id}")


def record_proposal_outcome(
    proposal_id: str,
    outcome: str,
    *,
    feedback: str = "",
) -> dict[str, Any]:
    entry = {
        "proposal_id": str(proposal_id),
        "outcome": str(outcome or ""),
        "feedback": str(feedback or ""),
        "recorded_at": _now(),
    }
    store = _load_store()
    store["outcomes"].append(entry)
    _save_store(store)
    return entry


def mark_proposal_verified(proposal_id: str, note: str = "") -> bool:
    ok = update_proposal_status(proposal_id, "verified", verification_note=note)
    if ok:
        record_proposal_outcome(proposal_id, "verified", feedback=note)
    return ok


def list_action_runs(*, proposal_id: str | None = None, limit: int | None = None) -> list[dict[str, Any]]:
    runs = list(_load_store().get("action_runs", []))
    if proposal_id:
        runs = [run for run in runs if run.get("proposal_id") == proposal_id]
    runs.sort(key=lambda run: run.get("started_at", ""), reverse=True)
    return runs[:limit] if limit else runs


def list_rejected_proposals(*, limit: int | None = None) -> list[dict[str, Any]]:
    rejected = list(_load_store().get("rejected_proposals", []))
    rejected.sort(key=lambda item: item.get("rejected_at", ""), reverse=True)
    return rejected[:limit] if limit else rejected


def list_curator_reports(*, limit: int | None = None) -> list[dict[str, Any]]:
    reports = list(_load_store().get("curator_reports", []))
    reports.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    return reports[:limit] if limit else reports


def _insight_text(insight: dict[str, Any]) -> str:
    evidence = insight.get("evidence") if isinstance(insight.get("evidence"), list) else []
    parts = [
        str(insight.get("title") or ""),
        str(insight.get("body") or ""),
        str(insight.get("suggestion") or ""),
        str(insight.get("affected_surface") or ""),
        " ".join(str(item) for item in evidence),
    ]
    draft = insight.get("skill_draft")
    if isinstance(draft, dict):
        parts.extend(
            [
                str(draft.get("name") or ""),
                str(draft.get("display_name") or ""),
                str(draft.get("description") or ""),
                str(draft.get("instructions") or ""),
            ]
        )
    return " ".join(parts).lower()


def _has_meaningful_skill_draft(insight: dict[str, Any]) -> bool:
    draft = insight.get("skill_draft")
    if not isinstance(draft, dict):
        return False
    instructions = str(draft.get("instructions") or "").strip()
    display = str(draft.get("display_name") or draft.get("name") or "").strip()
    description = str(draft.get("description") or "").strip()
    return bool(display and description and len(instructions) >= 40)


def _looks_like_system_maintenance(insight: dict[str, Any]) -> bool:
    category = str(insight.get("category") or "")
    text = _insight_text(insight)
    if category in {"error_pattern", "tool_config", "system_health"}:
        return True
    return any(term in text for term in _SKILL_BLOCKED_SURFACE_TERMS)


def is_skill_evolution_candidate(insight: dict[str, Any]) -> bool:
    """Return true only for reusable user-facing workflow insights."""

    category = str(insight.get("category") or "")
    if category not in {"skill_proposal", "usage_pattern"}:
        return False
    if _looks_like_system_maintenance(insight):
        return False
    if not _has_meaningful_skill_draft(insight):
        return False
    text = _insight_text(insight)
    if category == "usage_pattern" and not any(term in text for term in _SKILL_POSITIVE_WORKFLOW_TERMS):
        return False
    return True


def normalize_insight_for_evolution(insight: dict[str, Any]) -> dict[str, Any]:
    """Coerce over-eager skill insights back to safer categories."""

    normalized = dict(insight or {})
    category = str(normalized.get("category") or "")
    if category in {"skill_proposal", "usage_pattern"} and not is_skill_evolution_candidate(normalized):
        if _looks_like_system_maintenance(normalized):
            normalized["category"] = "system_health"
        else:
            normalized["category"] = "usage_pattern" if category == "usage_pattern" else "knowledge_quality"
        normalized["skill_draft"] = None
    if normalized.get("category") != "skill_proposal":
        if not is_skill_evolution_candidate(normalized):
            normalized["skill_draft"] = None
    return normalized


def proposal_types_for_insight(insight: dict[str, Any]) -> list[str]:
    insight = normalize_insight_for_evolution(insight)
    category = str(insight.get("category") or "")
    if category == "error_pattern":
        return ["investigate", "send_feedback"]
    if category == "skill_proposal":
        if is_skill_evolution_candidate(insight):
            return ["create_skill", "patch_skill"]
        return ["investigate"]
    if category == "tool_config":
        return ["investigate", "send_feedback"]
    if category == "knowledge_quality":
        return ["investigate"]
    if category == "usage_pattern":
        if is_skill_evolution_candidate(insight):
            return ["create_skill", "patch_skill"]
        return ["investigate"]
    if category == "system_health":
        return ["send_feedback", "investigate"]
    return ["investigate"]


def build_investigation_prompt(insight: dict[str, Any]) -> str:
    title = str(insight.get("title") or "Untitled insight")
    body = str(insight.get("body") or "")
    suggestion = str(insight.get("suggestion") or "")
    evidence = insight.get("evidence") if isinstance(insight.get("evidence"), list) else []
    lines = [
        "An automated insight was generated.",
        "",
        f"Title: {title}",
        f"Category: {insight.get('category', 'unknown')}",
        f"Severity: {insight.get('severity', 'info')}",
    ]
    if body:
        lines.extend(["", "Body:", body])
    if suggestion:
        lines.extend(["", "Suggestion:", suggestion])
    if evidence:
        lines.append("")
        lines.append("Evidence:")
        lines.extend(f"- {item}" for item in evidence[:6])
    lines.extend(
        [
            "",
            "Please investigate this. Explain what happened, whether this should become a skill proposal, a skill patch, a send feedback proposal, or no action, and what validation would be needed before any mutation.",
        ]
    )
    return "\n".join(lines)


def create_investigation_thread_from_insight(insight: dict[str, Any]) -> dict[str, str]:
    from row_bot.threads import create_thread, save_thread_draft

    title = str(insight.get("title") or "Insight").strip()
    thread_id = create_thread(
        f"Investigate: {title[:80]}",
        seed_default_skills=True,
    )
    prompt = build_investigation_prompt(insight)
    save_thread_draft(thread_id, prompt, source="insight_investigate")
    return {"thread_id": thread_id, "draft": prompt}


def _draft_from_insight(insight: dict[str, Any]) -> dict[str, Any]:
    draft = insight.get("skill_draft")
    if isinstance(draft, dict) and draft:
        return dict(draft)
    title = str(insight.get("title") or "Reusable Workflow")
    suggestion = str(insight.get("suggestion") or "").strip()
    body = str(insight.get("body") or "").strip()
    instructions = suggestion or body or "Describe the repeatable workflow and constraints for this skill."
    return {
        "name": _title_to_skill_name(title),
        "display_name": title[:80],
        "icon": "sparkles",
        "description": body[:180] or suggestion[:180] or title,
        "instructions": instructions,
        "tags": ["self-improvement"],
        "enabled": True,
        "version": "1.0",
    }


def _normalize_tags(value: Any) -> list[str]:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item or "").strip()]
    return []


def build_create_skill_proposal(
    draft: dict[str, Any],
    *,
    insight_ids: list[str] | None = None,
    rationale: str = "",
    title: str | None = None,
) -> dict[str, Any]:
    name = normalize_skill_name(str(draft.get("name") or draft.get("display_name") or ""))
    display_name = str(draft.get("display_name") or name.replace("_", " ").title()).strip()
    description = str(draft.get("description") or "").strip()
    instructions = str(draft.get("instructions") or "").strip()
    tags = _normalize_tags(draft.get("tags"))
    payload = {
        "name": name,
        "display_name": display_name,
        "icon": str(draft.get("icon") or "sparkles"),
        "description": description,
        "instructions": instructions,
        "tags": tags,
        "enabled": bool(draft.get("enabled", draft.get("enabled_by_default", True))),
        "version": str(draft.get("version") or "1.0"),
    }
    overlaps = detect_skill_overlaps(
        name=name,
        description=description,
        instructions=instructions,
        include_tool_guides=True,
    )
    try:
        from row_bot.skills import estimate_text_tokens

        token_estimate = estimate_text_tokens(instructions)
    except Exception:
        token_estimate = len(instructions) // 4
    preview = {
        "skill_name": name,
        "display_name": display_name,
        "description": description,
        "instructions_preview": instructions[:1200],
        "tags": tags,
        "estimated_tokens": token_estimate,
        "overlaps": overlaps,
        "suggested_enabled": payload["enabled"],
    }
    return create_proposal(
        insight_ids=insight_ids or [],
        proposal_type="create_skill",
        title=title or f"Create skill: {display_name}",
        rationale=rationale or "A repeated workflow appears reusable enough to become a manual skill.",
        risk="medium" if any(item.get("score", 0) >= 0.55 for item in overlaps) else "low",
        confidence=0.72,
        payload=payload,
        preview=preview,
        verification_plan="Validate metadata, check for overlap, create the skill only after preview approval, then confirm it appears in Settings > Skills.",
    )


def build_patch_skill_proposal(
    *,
    target_skill: str,
    updated_instructions: str,
    reason: str,
    insight_ids: list[str] | None = None,
    title: str | None = None,
) -> dict[str, Any]:
    skill = _resolve_skill(target_skill)
    display = skill.display_name if skill else target_skill
    diff = build_skill_diff_preview(target_skill, updated_instructions)
    payload = {
        "target_skill": target_skill,
        "updated_instructions": str(updated_instructions or ""),
        "reason": str(reason or ""),
    }
    return create_proposal(
        insight_ids=insight_ids or [],
        proposal_type="patch_skill",
        title=title or f"Patch skill: {display}",
        rationale=reason or "An existing skill appears to need a bounded guidance update.",
        risk="medium",
        confidence=0.66,
        payload=payload,
        preview={
            "target_skill": target_skill,
            "target_display_name": display,
            "diff": diff.get("diff", ""),
            "changed_lines": diff.get("changed_lines", 0),
            "bounded": diff.get("bounded", False),
        },
        verification_plan="Validate the target is a manual skill, inspect the bounded diff, back up the original, apply after approval, and keep rollback metadata.",
    )


def build_send_feedback_proposal(
    insight: dict[str, Any] | None = None,
    *,
    title: str = "",
    summary: str = "",
    include_logs: bool = False,
    insight_ids: list[str] | None = None,
) -> dict[str, Any]:
    draft = build_feedback_draft(insight, title=title, summary=summary, include_logs=include_logs)
    from row_bot.brand import APP_SUPPORT_URL

    return create_proposal(
        insight_ids=insight_ids or ([str(insight.get("id"))] if insight and insight.get("id") else []),
        proposal_type="send_feedback",
        title=f"Send feedback: {draft['title']}",
        rationale="This looks like an app/system problem rather than reusable skill guidance.",
        risk="medium" if include_logs else "low",
        confidence=0.7,
        payload={"feedback_draft": draft},
        preview={
            "feedback_draft": draft,
            "contact_url": APP_SUPPORT_URL,
            "redaction_ok": not contains_unredacted_sensitive_text(draft["body"]),
        },
        verification_plan="Review the redacted feedback report. Applying saves local markdown; Submit opens the Row-Bot contact page so the user can send it manually.",
    )


def map_insight_to_proposals(
    insight: dict[str, Any],
    *,
    only_types: set[str] | None = None,
) -> list[dict[str, Any]]:
    insight = normalize_insight_for_evolution(insight)
    proposals: list[dict[str, Any]] = []
    insight_id = str(insight.get("id") or "")
    insight_ids = [insight_id] if insight_id else []
    types = proposal_types_for_insight(insight)
    if only_types is not None:
        types = [proposal_type for proposal_type in types if proposal_type in only_types]

    if "investigate" in types:
        proposals.append(
            create_proposal(
                insight_ids=insight_ids,
                proposal_type="investigate",
                title=f"Investigate: {insight.get('title', 'Insight')}",
                rationale="The observation needs diagnosis before any mutation.",
                risk="low",
                confidence=float(insight.get("confidence", 0.5) or 0.5),
                payload={"prompt": build_investigation_prompt(insight)},
                preview={"draft_prompt": build_investigation_prompt(insight)},
                verification_plan="Open a new thread with a draft prompt and review the answer before taking action.",
            )
        )

    if "send_feedback" in types:
        proposals.append(build_send_feedback_proposal(insight, insight_ids=insight_ids))

    if "create_skill" in types:
        draft = _draft_from_insight(insight)
        proposals.append(
            build_create_skill_proposal(
                draft,
                insight_ids=insight_ids,
                rationale="The insight suggests repeatable behavior that may belong in a reusable skill.",
            )
        )

    if "patch_skill" in types:
        draft = _draft_from_insight(insight)
        overlaps = detect_skill_overlaps(
            name=str(draft.get("name") or ""),
            description=str(draft.get("description") or ""),
            instructions=str(draft.get("instructions") or ""),
            include_tool_guides=False,
        )
        target = next((item for item in overlaps if not item.get("is_tool_guide")), None)
        if target and target.get("score", 0) >= 0.22:
            skill = _resolve_skill(str(target["name"]))
            if skill:
                addition = str(draft.get("instructions") or insight.get("suggestion") or insight.get("body") or "").strip()
                updated = skill.instructions
                if addition and addition not in updated:
                    updated = f"{updated.rstrip()}\n\n## Controlled Evolution Note\n\n{addition}\n"
                proposals.append(
                    build_patch_skill_proposal(
                        target_skill=skill.name,
                        updated_instructions=updated,
                        reason=f"Related insight suggests improving existing skill '{skill.display_name}'.",
                        insight_ids=insight_ids,
                    )
                )
    return proposals


def ensure_proposals_for_insight(insight: dict[str, Any]) -> list[dict[str, Any]]:
    insight_id = str(insight.get("id") or "")
    desired_types = proposal_types_for_insight(insight)
    existing = list_proposals_for_insight(insight_id, include_terminal=True) if insight_id else []
    usable = [
        proposal
        for proposal in existing
        if not _proposal_is_obsolete_for_insight(proposal, insight)
    ]
    existing_types = {str(proposal.get("proposal_type") or "") for proposal in usable}
    missing_types = [proposal_type for proposal_type in desired_types if proposal_type not in existing_types]
    created = map_insight_to_proposals(insight, only_types=set(missing_types)) if missing_types else []
    return collapse_proposals_for_display(usable + created)


def validate_proposal(proposal: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    for field in (
        "id",
        "insight_ids",
        "proposal_type",
        "title",
        "rationale",
        "risk",
        "confidence",
        "payload",
        "preview",
        "verification_plan",
        "status",
        "created_at",
        "updated_at",
    ):
        if field not in proposal:
            errors.append(f"Missing field: {field}")
    proposal_type = proposal.get("proposal_type")
    if proposal_type not in VALID_PROPOSAL_TYPES:
        errors.append(f"Invalid proposal type: {proposal_type}")
    if proposal.get("status") not in VALID_PROPOSAL_STATUSES:
        errors.append(f"Invalid proposal status: {proposal.get('status')}")
    if proposal.get("risk") not in VALID_RISKS:
        errors.append(f"Invalid risk: {proposal.get('risk')}")

    payload = _safe_payload(proposal.get("payload"))
    if proposal_type == "create_skill":
        name = normalize_skill_name(str(payload.get("name") or ""))
        name_error = _validate_skill_name(name)
        if name_error:
            errors.append(name_error)
        instructions = str(payload.get("instructions") or "")
        if not instructions.strip():
            errors.append("Skill instructions are required.")
        if payload.get("tools"):
            errors.append("Tool guide metadata cannot be created through skill proposals.")
        if contains_unredacted_sensitive_text(instructions + "\n" + str(payload.get("description") or "")):
            errors.append("Skill proposal appears to contain an unredacted secret.")
        try:
            from row_bot.skills import get_skill, estimate_text_tokens, is_tool_guide, load_skills, skills_loaded

            if not skills_loaded():
                load_skills()

            existing = get_skill(name)
            if existing:
                if is_tool_guide(existing):
                    errors.append("Skill proposal targets an existing tool guide; tool guides are separate from skills.")
                else:
                    errors.append(f"Skill already exists: {name}")
            tokens = estimate_text_tokens(instructions)
        except Exception:
            tokens = len(instructions) // 4
        if tokens > MAX_SKILL_TOKENS_WARNING:
            warnings.append(f"Skill is large ({tokens} estimated tokens).")
        overlaps = detect_skill_overlaps(
            name=name,
            description=str(payload.get("description") or ""),
            instructions=instructions,
            include_tool_guides=True,
        )
        if any(item.get("score", 0) >= 0.75 and not item.get("is_tool_guide") for item in overlaps):
            warnings.append("Strong overlap detected; consider patching or consolidating instead.")
        if any(item.get("is_tool_guide") and item.get("score", 0) >= 0.7 for item in overlaps):
            warnings.append("Strong tool-guide overlap detected; do not patch tool guides through skills.")

    elif proposal_type == "patch_skill":
        target = str(payload.get("target_skill") or payload.get("name") or "").strip()
        if not target:
            errors.append("Patch proposal requires target_skill.")
        skill = _resolve_skill(target)
        if not skill:
            errors.append(f"Skill not found: {target}")
        else:
            try:
                from row_bot.skills import is_tool_guide

                if is_tool_guide(skill):
                    errors.append("Tool guides cannot be patched through skill proposals.")
            except Exception:
                pass
        updated = _updated_instructions_for_patch(skill, payload) if skill else str(payload.get("updated_instructions") or "")
        if not updated.strip():
            errors.append("Patch proposal requires updated instructions.")
        if contains_unredacted_sensitive_text(updated):
            errors.append("Patch proposal appears to contain an unredacted secret.")
        diff = build_skill_diff_preview(target, updated) if target else {"changed_lines": 0, "bounded": False}
        if diff.get("changed_lines", 0) == 0:
            warnings.append("Patch proposal does not change the skill instructions.")
        if diff.get("changed_lines", 0) > MAX_SKILL_PATCH_CHANGED_LINES:
            errors.append(
                f"Patch changes {diff.get('changed_lines')} lines; limit is {MAX_SKILL_PATCH_CHANGED_LINES}."
            )

    elif proposal_type == "send_feedback":
        draft = payload.get("feedback_draft") if isinstance(payload.get("feedback_draft"), dict) else {}
        body = str(draft.get("body") or "")
        if not str(draft.get("title") or "").strip():
            errors.append("Feedback draft requires a title.")
        if not body.strip():
            errors.append("Feedback draft requires a body.")
        if contains_unredacted_sensitive_text(body):
            errors.append("Feedback draft contains unredacted sensitive text.")
        if len(body) > MAX_FEEDBACK_BODY_CHARS:
            warnings.append("Feedback draft body is long and will be trimmed before saving.")

    elif proposal_type == "consolidate_skills":
        targets = payload.get("skill_names")
        if not isinstance(targets, list) or len(targets) < 2:
            errors.append("Consolidation proposal requires at least two skill names.")
        for target in targets or []:
            skill = _resolve_skill(str(target))
            if skill:
                try:
                    from row_bot.skills import is_tool_guide

                    if is_tool_guide(skill):
                        errors.append(f"Tool guide cannot be consolidated through skill patching: {target}")
                except Exception:
                    pass

    return {"ok": not errors, "errors": errors, "warnings": warnings}


def _resolve_skill(name: str):
    try:
        from row_bot.skills import get_skill, load_skills, skills_loaded

        if not skills_loaded():
            load_skills()
        return get_skill(str(name or "").strip())
    except Exception:
        logger.debug("Failed to resolve skill %s", name, exc_info=True)
        return None


def _updated_instructions_for_patch(skill: Any, payload: dict[str, Any]) -> str:
    if "updated_instructions" in payload:
        return str(payload.get("updated_instructions") or "")
    current = str(getattr(skill, "instructions", "") or "")
    find = str(payload.get("find") or "")
    replace = str(payload.get("replace") or "")
    if find and find in current:
        return current.replace(find, replace, 1)
    append = str(payload.get("append") or "")
    if append:
        return f"{current.rstrip()}\n\n{append.strip()}\n"
    return current


def _preview_for_gate(proposal: dict[str, Any]) -> str:
    preview = proposal.get("preview") if isinstance(proposal.get("preview"), dict) else {}
    if proposal.get("proposal_type") == "patch_skill":
        return str(preview.get("diff") or "")[:3000]
    if proposal.get("proposal_type") == "send_feedback":
        draft = preview.get("feedback_draft") if isinstance(preview.get("feedback_draft"), dict) else {}
        return str(draft.get("body") or "")[:3000]
    return json.dumps(preview, indent=2, ensure_ascii=False)[:3000]


def apply_proposal(
    proposal_id: str,
    *,
    require_approval: bool = True,
    approved_by_user: bool = False,
) -> dict[str, Any]:
    """Apply an approved proposal and write an ActionRun audit record."""

    proposal = get_proposal(proposal_id)
    if not proposal:
        return {"ok": False, "message": "Proposal not found", "action_run": None}
    if proposal.get("status") in {"applied", "verified", "rejected"}:
        return {"ok": False, "message": f"Proposal is already {proposal.get('status')}", "action_run": None}
    proposal_type = str(proposal.get("proposal_type") or "")
    if proposal_type in SKILL_MUTATION_TYPES:
        insight = _first_insight_for_proposal(proposal)
        if _proposal_is_obsolete_for_insight(proposal, insight):
            return {
                "ok": False,
                "message": "This skill proposal is obsolete for the current insight. Generate fresh proposals instead.",
                "action_run": None,
            }

    validation = validate_proposal(proposal)
    if not validation.get("ok"):
        update_proposal_status(proposal_id, "failed", error="; ".join(validation.get("errors") or []))
        return {
            "ok": False,
            "message": "Proposal validation failed: " + "; ".join(validation.get("errors") or []),
            "action_run": None,
            "validation": validation,
        }

    if require_approval and proposal_type in MUTATING_PROPOSAL_TYPES:
        blocked = gate_action(
            {
                "tool": "row_bot_apply_proposal",
                "label": f"Apply proposal: {proposal.get('title')}",
                "description": _preview_for_gate(proposal),
                "args": {"proposal_id": proposal_id, "proposal_type": proposal_type},
            },
            blocked_message="BLOCKED: Controlled self-evolution mutations are disabled in Block approval mode.",
        )
        if blocked:
            return {"ok": False, "message": blocked, "action_run": None}
        approved_by_user = True

    action = {
        "id": _new_id("action"),
        "proposal_id": proposal_id,
        "action_type": proposal_type,
        "approved_by_user": bool(approved_by_user),
        "started_at": _now(),
        "finished_at": "",
        "result": "running",
        "result_refs": [],
        "rollback_ref": "",
        "error": "",
    }
    store = _load_store()
    store["action_runs"].append(action)
    for item in store.get("proposals", []):
        if item.get("id") == proposal_id:
            item["status"] = "approved"
            item["updated_at"] = _now()
            break
    _save_store(store)

    try:
        if proposal_type == "investigate":
            result = _apply_investigate(proposal)
        elif proposal_type == "create_skill":
            result = _apply_create_skill(proposal)
        elif proposal_type == "patch_skill":
            result = _apply_patch_skill(proposal, action["id"])
        elif proposal_type == "send_feedback":
            result = _apply_send_feedback(proposal)
        elif proposal_type == "consolidate_skills":
            result = {
                "message": "Consolidation remains proposal-only in this version.",
                "result_refs": [],
                "rollback_ref": "",
            }
        else:
            result = {
                "message": f"{proposal_type} proposals are not executable yet.",
                "result_refs": [],
                "rollback_ref": "",
            }
        action["result"] = "success"
        action["result_refs"] = result.get("result_refs", [])
        action["rollback_ref"] = result.get("rollback_ref", "")
        message = result.get("message", "Proposal applied.")
        update_status = "applied"
        record_proposal_outcome(proposal_id, "applied", feedback=message)
        ok = True
    except Exception as exc:
        logger.warning("Failed to apply proposal %s: %s", proposal_id, exc, exc_info=True)
        action["result"] = "failed"
        action["error"] = str(exc)
        message = f"Failed to apply proposal: {exc}"
        update_status = "failed"
        ok = False

    action["finished_at"] = _now()
    store = _load_store()
    for idx, item in enumerate(store.get("action_runs", [])):
        if item.get("id") == action["id"]:
            store["action_runs"][idx] = action
            break
    for item in store.get("proposals", []):
        if item.get("id") == proposal_id:
            item["status"] = update_status
            item["updated_at"] = _now()
            if action.get("error"):
                item["error"] = action["error"]
            break
    _save_store(store)
    return {"ok": ok, "message": message, "action_run": action}


def _apply_investigate(proposal: dict[str, Any]) -> dict[str, Any]:
    insight = _first_insight_for_proposal(proposal)
    if insight:
        created = create_investigation_thread_from_insight(insight)
    else:
        from row_bot.threads import create_thread, save_thread_draft

        thread_id = create_thread(str(proposal.get("title") or "Investigate"), seed_default_skills=True)
        prompt = str(_safe_payload(proposal.get("payload")).get("prompt") or _preview_for_gate(proposal))
        save_thread_draft(thread_id, prompt, source="proposal_investigate")
        created = {"thread_id": thread_id, "draft": prompt}
    return {
        "message": f"Investigation thread created: {created['thread_id']}",
        "result_refs": [created["thread_id"]],
        "rollback_ref": "",
    }


def _first_insight_for_proposal(proposal: dict[str, Any]) -> dict[str, Any] | None:
    ids = proposal.get("insight_ids") or []
    if not ids:
        return None
    try:
        from row_bot.insights import get_insight_by_id

        return get_insight_by_id(str(ids[0]))
    except Exception:
        return None


def _apply_create_skill(proposal: dict[str, Any]) -> dict[str, Any]:
    payload = _safe_payload(proposal.get("payload"))
    from row_bot.skills import create_skill

    skill = create_skill(
        name=normalize_skill_name(str(payload.get("name") or "")),
        display_name=str(payload.get("display_name") or ""),
        icon=str(payload.get("icon") or "sparkles"),
        description=str(payload.get("description") or ""),
        instructions=str(payload.get("instructions") or ""),
        tags=_normalize_tags(payload.get("tags")),
        enabled=bool(payload.get("enabled", True)),
        version=str(payload.get("version") or "1.0"),
    )
    refs = [str(skill.path / "SKILL.md")] if skill and skill.path else []
    return {
        "message": f"Skill created: {getattr(skill, 'display_name', payload.get('display_name', 'skill'))}",
        "result_refs": refs,
        "rollback_ref": "",
    }


def _backup_skill_content(skill: Any, action_id: str, reason: str) -> str:
    content = ""
    if getattr(skill, "path", None):
        md_path = Path(skill.path) / "SKILL.md"
        if md_path.exists():
            content = md_path.read_text(encoding="utf-8")
    backup_root = _backups_dir() / "skills" / str(skill.name)
    backup_root.mkdir(parents=True, exist_ok=True)
    backup_path = backup_root / f"{action_id}.md"
    backup_path.write_text(content, encoding="utf-8")
    meta_path = backup_root / f"{action_id}.json"
    meta_path.write_text(
        json.dumps(
            {
                "skill_name": skill.name,
                "source": skill.source,
                "reason": reason,
                "created_at": _now(),
                "backup_path": str(backup_path),
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return str(backup_path)


def _apply_patch_skill(proposal: dict[str, Any], action_id: str) -> dict[str, Any]:
    payload = _safe_payload(proposal.get("payload"))
    target = str(payload.get("target_skill") or payload.get("name") or "").strip()
    skill = _resolve_skill(target)
    if not skill:
        raise ValueError(f"Skill not found: {target}")
    try:
        from row_bot.skills import create_skill, is_tool_guide, update_skill
    except ImportError as exc:
        raise ValueError("Skills module not available") from exc
    if is_tool_guide(skill):
        raise ValueError("Tool guides cannot be patched through skill proposals.")
    updated = _updated_instructions_for_patch(skill, payload)
    reason = str(payload.get("reason") or proposal.get("rationale") or "")
    rollback_ref = _backup_skill_content(skill, action_id, reason)
    if skill.source == "bundled":
        patched = create_skill(
            name=skill.name,
            display_name=skill.display_name,
            icon=skill.icon,
            description=skill.description,
            instructions=updated,
            tags=list(skill.tags),
            activation=dict(skill.activation),
            enabled=True,
            version=skill.version,
            allow_override_existing_bundled=True,
        )
    else:
        patched = update_skill(name=skill.name, instructions=updated)
    if not patched:
        raise ValueError("Skill patch failed.")
    refs = [str(patched.path / "SKILL.md")] if patched.path else []
    return {
        "message": f"Skill patched: {patched.display_name}",
        "result_refs": refs,
        "rollback_ref": rollback_ref,
    }


def build_feedback_draft(
    insight: dict[str, Any] | None = None,
    *,
    title: str = "",
    summary: str = "",
    include_logs: bool = False,
) -> dict[str, str]:
    insight = insight or {}
    raw_title = title or str(insight.get("title") or "Row-Bot feedback report")
    safe_title = redact_text(raw_title, max_chars=180).replace("\n", " ").strip()
    body_lines = [
        "## Summary",
        redact_text(summary or str(insight.get("body") or "Automated insight suggests feedback for Row-Bot."), max_chars=1600),
        "",
        "## Expected Behavior",
        "Row-Bot should handle this flow without recurring errors or unsafe self-modification.",
        "",
        "## Actual Behavior",
        redact_text(str(insight.get("suggestion") or "See insight context below."), max_chars=1200),
        "",
        "## Insight Context",
        f"- Insight ID: {redact_text(str(insight.get('id') or 'not provided'))}",
        f"- Category: {redact_text(str(insight.get('category') or 'unknown'))}",
        f"- Severity: {redact_text(str(insight.get('severity') or 'info'))}",
        f"- Confidence: {redact_text(str(insight.get('confidence') or 'unknown'))}",
    ]
    evidence = insight.get("evidence") if isinstance(insight.get("evidence"), list) else []
    if evidence:
        body_lines.extend(["", "## Evidence"])
        for item in evidence[:8]:
            body_lines.append(f"- {redact_text(str(item), max_chars=500)}")
    body_lines.extend(
        [
            "",
            "## Environment",
            f"- Platform: {redact_text(platform.platform(), max_chars=240)}",
            f"- Python: {sys.version.split()[0]}",
        ]
    )
    try:
        from row_bot.version import __version__

        body_lines.append(f"- Row-Bot: v{__version__}")
    except Exception:
        pass
    if include_logs:
        logs = _recent_redacted_logs()
        if logs:
            body_lines.extend(["", "## Recent Redacted Logs", "```", logs, "```"])
    body = redact_text("\n".join(body_lines), max_chars=MAX_FEEDBACK_BODY_CHARS)
    return {"title": safe_title or "Row-Bot feedback report", "body": body}


def _recent_redacted_logs() -> str:
    try:
        from row_bot.logging_config import read_recent_logs

        entries = read_recent_logs(n=30)
    except Exception:
        return ""
    lines: list[str] = []
    for entry in entries[:20]:
        level = str(entry.get("level") or "")
        if level not in {"WARNING", "ERROR", "CRITICAL"}:
            continue
        lines.append(f"{entry.get('ts', '')} {level}: {entry.get('msg', '')}")
    return redact_text("\n".join(lines), max_chars=MAX_LOG_CHARS)


def write_local_feedback_report(draft: dict[str, str]) -> str:
    reports = _feedback_reports_dir()
    reports.mkdir(parents=True, exist_ok=True)
    safe_slug = normalize_skill_name(draft.get("title", "feedback_report")) or "feedback_report"
    path = reports / f"{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{safe_slug}.md"
    body = f"# {draft.get('title', 'Feedback Report')}\n\n{draft.get('body', '')}\n"
    path.write_text(redact_text(body, max_chars=MAX_FEEDBACK_BODY_CHARS + 500), encoding="utf-8")
    return str(path)


def _apply_send_feedback(proposal: dict[str, Any]) -> dict[str, Any]:
    payload = _safe_payload(proposal.get("payload"))
    draft = payload.get("feedback_draft") if isinstance(payload.get("feedback_draft"), dict) else {}
    draft = {
        "title": str(draft.get("title") or proposal.get("title") or "Row-Bot feedback report"),
        "body": redact_text(str(draft.get("body") or ""), max_chars=MAX_FEEDBACK_BODY_CHARS),
    }
    if contains_unredacted_sensitive_text(draft["body"]):
        raise ValueError("Feedback draft contains unredacted sensitive text.")
    local_path = write_local_feedback_report(draft)
    from row_bot.brand import APP_SUPPORT_URL

    return {
        "message": f"Feedback report prepared: {local_path}\nSubmit here: {APP_SUPPORT_URL}",
        "result_refs": [local_path, APP_SUPPORT_URL],
        "rollback_ref": "",
    }


def review_skill_library_dry_run(*, create_proposals: bool = True) -> dict[str, Any]:
    """Review skill library health and optionally create proposals only."""

    try:
        from row_bot.skills import (
            get_manual_skill_statuses,
            get_pinned_skill_names,
            is_tool_guide,
            load_skills,
            skills_loaded,
        )

        if not skills_loaded():
            load_skills()
        statuses = get_manual_skill_statuses()
        pinned = set(get_pinned_skill_names())
    except Exception as exc:
        raise RuntimeError(f"Could not load skill library: {exc}") from exc

    skills = [skill for skill, _enabled in statuses if not is_tool_guide(skill)]
    enabled = [skill for skill, is_enabled in statuses if is_enabled and not is_tool_guide(skill)]
    findings: list[dict[str, Any]] = []
    proposal_ids: list[str] = []

    for idx, left in enumerate(skills):
        left_tokens = _word_tokens(" ".join([left.name, left.display_name, left.description, left.instructions]))
        for right in skills[idx + 1 :]:
            right_tokens = _word_tokens(" ".join([right.name, right.display_name, right.description, right.instructions]))
            score = _jaccard(left_tokens, right_tokens)
            if score < 0.28:
                continue
            finding = {
                "type": "overlap",
                "skill_names": [left.name, right.name],
                "score": round(score, 3),
                "protected": left.source == "bundled" or right.source == "bundled" or left.name in pinned or right.name in pinned,
            }
            findings.append(finding)
            if create_proposals:
                proposal = create_proposal(
                    insight_ids=[],
                    proposal_type="consolidate_skills",
                    title=f"Review overlap: {left.display_name} and {right.display_name}",
                    rationale="The curator found overlapping manual skills. This is a review proposal only; no skill files are changed.",
                    risk="medium",
                    confidence=min(0.85, 0.4 + score),
                    payload={"skill_names": [left.name, right.name], "score": score},
                    preview={
                        "summary": "Review whether these skills should be consolidated, patched, or left separate.",
                        "skill_names": [left.name, right.name],
                        "protected": finding["protected"],
                    },
                    verification_plan="Review both skills, preserve pinned/protected skills, and apply any future edits through bounded patch proposals.",
                )
                proposal_ids.append(proposal["id"])

    try:
        from row_bot.insights import get_active_insights

        active_skill_insights = [
            item
            for item in get_active_insights()
            if item.get("category") in {"skill_proposal", "usage_pattern"}
        ][:5]
    except Exception:
        active_skill_insights = []

    for insight in active_skill_insights:
        findings.append(
            {
                "type": "skill_insight",
                "insight_id": insight.get("id"),
                "title": insight.get("title"),
                "category": insight.get("category"),
            }
        )
        if create_proposals:
            proposal_ids.extend(item["id"] for item in ensure_proposals_for_insight(insight))

    report = {
        "id": _new_id("curator"),
        "created_at": _now(),
        "summary": {
            "manual_skill_count": len(skills),
            "enabled_manual_skill_count": len(enabled),
            "pinned_skill_count": len(pinned),
            "finding_count": len(findings),
            "proposal_count": len(proposal_ids),
        },
        "findings": findings,
        "proposal_ids": proposal_ids,
        "mutated_skills": [],
    }
    store = _load_store()
    store["curator_reports"].append(report)
    _save_store(store)
    return report


def evolution_summary() -> str:
    proposals = list_proposals()
    runs = list_action_runs(limit=8)
    reports = list_curator_reports(limit=3)
    active = [p for p in proposals if p.get("status") not in TERMINAL_PROPOSAL_STATUSES]
    by_status: dict[str, int] = {}
    for proposal in proposals:
        by_status[proposal.get("status", "unknown")] = by_status.get(proposal.get("status", "unknown"), 0) + 1
    lines = [
        "**Controlled Self-Evolution**",
        f"- Proposals: {len(proposals)} total, {len(active)} active",
    ]
    if by_status:
        lines.append("- By status: " + ", ".join(f"{key}={value}" for key, value in sorted(by_status.items())))
    if active:
        lines.append("- Active proposals:")
        for proposal in active[:8]:
            lines.append(
                f"  - [{proposal.get('proposal_type')}/{proposal.get('risk')}/{proposal.get('status')}] {proposal.get('title')} ({proposal.get('id')})"
            )
    else:
        lines.append("- No active proposals")
    if runs:
        lines.append("- Recent action runs:")
        for run in runs[:5]:
            lines.append(
                f"  - [{run.get('action_type')}/{run.get('result')}] {run.get('proposal_id')} rollback={bool(run.get('rollback_ref'))}"
            )
    if reports:
        latest = reports[0]
        summary = latest.get("summary", {})
        lines.append(
            f"- Latest curator dry-run: {latest.get('created_at')} with {summary.get('finding_count', 0)} finding(s), {summary.get('proposal_count', 0)} proposal(s)"
        )
    rejected = list_rejected_proposals(limit=3)
    if rejected:
        lines.append("- Recent rejection memory:")
        for item in rejected:
            lines.append(f"  - {item.get('title')}: {item.get('reason') or 'no reason'}")
    return "\n".join(lines)
