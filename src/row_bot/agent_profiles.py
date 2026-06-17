"""Agent Profile registry and persistence.

Agent Profiles are lightweight reusable actor definitions. They are overlays
for chats, workflows, and child agents; they are not separate application
homes, secret stores, memories, or channel gateways.
"""

from __future__ import annotations

import copy
import json
import re
import sqlite3
import threading
import uuid
from datetime import datetime
from typing import Any, Mapping

from row_bot.approval_policy import DEFAULT_APPROVAL_MODE, normalize_approval_mode


_SCHEMA_LOCK = threading.RLock()
_SCHEMA_READY = False

_SLUG_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")

_SCOPES = {"system", "user", "workspace", "plugin", "imported"}
_SOURCES = {
    "builtin",
    "promoted_run",
    "user_created",
    "workflow_created",
    "plugin",
    "imported_pack",
}
_CAPABILITIES = {"read_only", "write_capable", "orchestrator"}
_CONTEXT_MODES = {"auto", "focused", "recent", "full", "empty", "resume"}
_WORKSPACE_MODES = {"auto", "read_only", "single_writer", "worktree"}
_APPROVAL_MODES = {"inherit", "block", "approve", "allow_all"}

_JSON_FIELDS = {
    "output_contract_json",
    "model_policy_json",
    "tool_policy_json",
    "skill_policy_json",
    "context_policy_json",
    "workspace_policy_json",
    "approval_policy_json",
    "memory_policy_json",
    "runtime_limits_json",
    "ui_json",
    "provenance_json",
}

_TEXT_FIELDS = {
    "id",
    "slug",
    "display_name",
    "description",
    "when_to_use",
    "instructions",
    "handoff_contract",
    "scope",
    "source",
    "created_at",
    "updated_at",
    "created_from_run_id",
    "created_from_workflow_id",
    "owner_workspace_id",
    "last_used_at",
}

_PROFILE_COLUMNS: dict[str, str] = {
    "id": "TEXT PRIMARY KEY",
    "slug": "TEXT UNIQUE NOT NULL",
    "display_name": "TEXT NOT NULL",
    "description": "TEXT DEFAULT ''",
    "when_to_use": "TEXT DEFAULT ''",
    "instructions": "TEXT DEFAULT ''",
    "handoff_contract": "TEXT DEFAULT ''",
    "output_contract_json": "TEXT DEFAULT '{}'",
    "scope": "TEXT DEFAULT 'user'",
    "source": "TEXT DEFAULT 'user_created'",
    "enabled": "INTEGER DEFAULT 1",
    "version": "INTEGER DEFAULT 1",
    "revision": "INTEGER DEFAULT 1",
    "created_at": "TEXT NOT NULL",
    "updated_at": "TEXT NOT NULL",
    "created_from_run_id": "TEXT DEFAULT ''",
    "created_from_workflow_id": "TEXT DEFAULT ''",
    "owner_workspace_id": "TEXT DEFAULT ''",
    "model_policy_json": "TEXT DEFAULT '{}'",
    "tool_policy_json": "TEXT DEFAULT '{}'",
    "skill_policy_json": "TEXT DEFAULT '{}'",
    "context_policy_json": "TEXT DEFAULT '{}'",
    "workspace_policy_json": "TEXT DEFAULT '{}'",
    "approval_policy_json": "TEXT DEFAULT '{}'",
    "memory_policy_json": "TEXT DEFAULT '{}'",
    "runtime_limits_json": "TEXT DEFAULT '{}'",
    "ui_json": "TEXT DEFAULT '{}'",
    "provenance_json": "TEXT DEFAULT '{}'",
    "last_used_at": "TEXT DEFAULT ''",
    "usage_count": "INTEGER DEFAULT 0",
}


class AgentProfileError(ValueError):
    """Raised when an Agent Profile reference or payload is invalid."""


def _now() -> str:
    return datetime.now().isoformat()


def normalize_profile_slug(value: str) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text


def _validate_slug(slug: str) -> str:
    normalized = normalize_profile_slug(slug)
    if not _SLUG_RE.match(normalized):
        raise AgentProfileError(
            "Agent Profile slug must start with a letter and contain 2-64 "
            "lowercase letters, numbers, underscores, or hyphens."
        )
    return normalized


def _json_obj(value: Any, *, field: str) -> dict[str, Any]:
    if value is None or value == "":
        return {}
    if isinstance(value, dict):
        return copy.deepcopy(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise AgentProfileError(f"{field} must be valid JSON.") from exc
        if isinstance(parsed, dict):
            return parsed
    raise AgentProfileError(f"{field} must be a JSON object.")


def _json_text(value: Any, *, field: str) -> str:
    return json.dumps(_json_obj(value, field=field), sort_keys=True)


def _default_output_contract(*, summary: bool = True, tests: bool = False) -> dict[str, Any]:
    return {
        "summary_required": summary,
        "evidence_required": True,
        "files_touched_required": False,
        "tests_run_required": tests,
    }


def _default_model_policy() -> dict[str, Any]:
    return {"mode": "inherit"}


def _default_approval_policy() -> dict[str, Any]:
    return {"mode": "inherit"}


def _default_memory_policy() -> dict[str, Any]:
    return {"mode": "none", "deny_memory_write": True}


_READ_ONLY_DENY_TOOLS = [
    "agents",
    "calendar",
    "custom_tool_builder",
    "designer",
    "gmail",
    "goal",
    "image_gen",
    "row_bot_updater",
    "task",
    "tracker",
    "video_gen",
    "x",
]

_LOCAL_INSPECTION_TOOLS = [
    "conversation_search",
    "filesystem",
    "memory",
    "row_bot_status",
    "system_info",
]

_RESEARCH_TOOLS = [
    "arxiv",
    "browser",
    "documents",
    "duckduckgo",
    "row_bot_status",
    "url_reader",
    "web_search",
    "wiki",
    "wikipedia",
    "youtube",
]


def _tool_policy(
    capability: str,
    *,
    allow_tools: list[str] | None = None,
    allow_delegation: bool = False,
) -> dict[str, Any]:
    return {
        "capability": capability,
        "allow_tools": list(allow_tools or []),
        "allow_tool_groups": [],
        "deny_memory_write": True,
        "allow_delegation": allow_delegation,
    }


def _skill_policy(
    *,
    skills_override: list[str] | None = None,
) -> dict[str, Any]:
    return {"skills_override": list(skills_override or [])}


def _context_policy(mode: str, *, include_memory: bool = False) -> dict[str, Any]:
    return {
        "default_context_mode": mode,
        "include_parent_summary": True,
        "include_selected_messages": False,
        "include_workspace_context": True,
        "include_memory": include_memory,
        "max_context_tokens": 0,
    }


def _workspace_policy(mode: str, *, lock: bool = False) -> dict[str, Any]:
    return {
        "workspace_mode_default": mode,
        "write_lock_required": lock,
        "worktree_allowed": mode == "worktree",
        "developer_workspace_required": False,
    }


def _limits(max_turns: int, timeout_seconds: int) -> dict[str, Any]:
    return {"max_turns": max_turns, "timeout_seconds": timeout_seconds}


def _builtin(
    slug: str,
    display_name: str,
    description: str,
    when_to_use: str,
    instructions: str,
    *,
    capability: str,
    context_mode: str,
    workspace_mode: str,
    max_turns: int,
    timeout_seconds: int,
    tests_required: bool = False,
    ui_icon: str = "smart_toy",
    ui_color: str = "blue",
    allow_tools: list[str] | None = None,
    skills_override: list[str] | None = None,
) -> dict[str, Any]:
    profile = {
        "id": f"builtin:{slug}",
        "slug": slug,
        "display_name": display_name,
        "description": description,
        "when_to_use": when_to_use,
        "instructions": instructions,
        "handoff_contract": (
            "Return a concise summary, key evidence, commands or files inspected, "
            "risks, and recommended next action."
        ),
        "output_contract_json": _default_output_contract(tests=tests_required),
        "scope": "system",
        "source": "builtin",
        "enabled": True,
        "version": 1,
        "revision": 1,
        "created_at": "2026-06-15T00:00:00",
        "updated_at": "2026-06-15T00:00:00",
        "created_from_run_id": "",
        "created_from_workflow_id": "",
        "owner_workspace_id": "",
        "model_policy_json": _default_model_policy(),
        "tool_policy_json": _tool_policy(
            capability,
            allow_tools=allow_tools,
        ),
        "skill_policy_json": _skill_policy(skills_override=skills_override),
        "context_policy_json": _context_policy(context_mode),
        "workspace_policy_json": _workspace_policy(
            workspace_mode,
            lock=capability == "write_capable",
        ),
        "approval_policy_json": _default_approval_policy(),
        "memory_policy_json": _default_memory_policy(),
        "runtime_limits_json": _limits(max_turns, timeout_seconds),
        "ui_json": {"icon": ui_icon, "color": ui_color},
        "provenance_json": {"builtin": True},
        "last_used_at": "",
        "usage_count": 0,
    }
    return profile


BUILTIN_AGENT_PROFILES: tuple[dict[str, Any], ...] = (
    _builtin(
        "row_bot_default",
        "Row-Bot Default",
        "Normal Row-Bot behavior for ordinary chats and channel conversations.",
        "Use for default chats when no specialist Agent Profile is selected.",
        "Use Row-Bot's normal behavior for the active surface.",
        capability="orchestrator",
        context_mode="auto",
        workspace_mode="auto",
        max_turns=0,
        timeout_seconds=0,
        ui_icon="auto_awesome",
        ui_color="blue-grey",
    ),
    _builtin(
        "planner",
        "Planner",
        "Break down ambiguous work, dependencies, risks, and acceptance checks.",
        "Use before implementation when requirements, scope, or sequencing are unclear.",
        "Produce a compact plan with assumptions, blockers, suggested agents, and success checks.",
        capability="read_only",
        context_mode="focused",
        workspace_mode="read_only",
        max_turns=4,
        timeout_seconds=600,
        ui_icon="route",
        ui_color="indigo",
        allow_tools=["conversation_search", "row_bot_status", "system_info"],
    ),
    _builtin(
        "explorer",
        "Explorer",
        "Map code, files, data, or processes without changing them.",
        "Use for codebase search, symbol mapping, and evidence gathering.",
        "Search and inspect. Do not edit. Return relevant paths, evidence, confidence, and next leads.",
        capability="read_only",
        context_mode="focused",
        workspace_mode="read_only",
        max_turns=6,
        timeout_seconds=600,
        ui_icon="travel_explore",
        ui_color="cyan",
        allow_tools=_LOCAL_INSPECTION_TOOLS,
    ),
    _builtin(
        "researcher",
        "Researcher",
        "Research web, docs, and sources with citations and uncertainty.",
        "Use when facts may have changed or source attribution matters.",
        "Verify claims against source material. Include dates, links, uncertainty, and recommended next action.",
        capability="read_only",
        context_mode="focused",
        workspace_mode="read_only",
        max_turns=8,
        timeout_seconds=900,
        ui_icon="manage_search",
        ui_color="teal",
        allow_tools=_RESEARCH_TOOLS,
    ),
    _builtin(
        "docs_researcher",
        "Docs Researcher",
        "Verify API, framework, or library behavior against official or primary docs.",
        "Use when implementation depends on specific API behavior or current docs.",
        "Prefer official or primary sources. Label verified facts separately from inferences.",
        capability="read_only",
        context_mode="focused",
        workspace_mode="read_only",
        max_turns=6,
        timeout_seconds=600,
        ui_icon="article",
        ui_color="green",
        allow_tools=_RESEARCH_TOOLS,
    ),
    _builtin(
        "reviewer",
        "Reviewer",
        "Review correctness, security, behavior regressions, and missing tests.",
        "Use after a change, plan, workflow, or artifact needs critical review.",
        "Findings first. Include severity, file or evidence refs, reproduction notes, and residual test gaps.",
        capability="read_only",
        context_mode="focused",
        workspace_mode="read_only",
        max_turns=8,
        timeout_seconds=900,
        ui_icon="fact_check",
        ui_color="purple",
        allow_tools=_LOCAL_INSPECTION_TOOLS,
    ),
    _builtin(
        "tester",
        "Tester",
        "Run focused checks and summarize failures, artifacts, and likely causes.",
        "Use when tests, lint, build, or verification commands need to run.",
        "Run scoped checks under the active policy. Report commands, exits, failures, artifacts, and next fix owner.",
        capability="write_capable",
        context_mode="focused",
        workspace_mode="single_writer",
        max_turns=8,
        timeout_seconds=1200,
        tests_required=True,
        ui_icon="science",
        ui_color="orange",
        allow_tools=["filesystem", "row_bot_status", "shell", "system_info"],
    ),
    _builtin(
        "worker",
        "Worker",
        "Implement scoped fixes or changes after requirements are clear.",
        "Use for write-capable implementation work with one writer lock.",
        "Make the requested change narrowly. Report changed files, tests, risks, and follow-ups.",
        capability="write_capable",
        context_mode="focused",
        workspace_mode="single_writer",
        max_turns=12,
        timeout_seconds=1800,
        ui_icon="construction",
        ui_color="deep-orange",
    ),
    _builtin(
        "browser_debugger",
        "Browser Debugger",
        "Reproduce UI/browser behavior and capture logs, screenshots, and clues.",
        "Use for visible UI bugs, browser automation, console/network evidence, and reproduction steps.",
        "Reproduce first. Capture observed vs expected behavior, evidence, likely owner files, and next agent.",
        capability="write_capable",
        context_mode="focused",
        workspace_mode="read_only",
        max_turns=8,
        timeout_seconds=1200,
        ui_icon="bug_report",
        ui_color="pink",
        allow_tools=["browser", "filesystem", "row_bot_status", "system_info", "vision"],
    ),
    _builtin(
        "synthesizer",
        "Synthesizer",
        "Combine child results, resolve conflicts, and produce a final handoff.",
        "Use after multiple agents finish or when partial results need consolidation.",
        "Summarize each result, resolve conflicts, state the decision, and identify next steps.",
        capability="read_only",
        context_mode="focused",
        workspace_mode="read_only",
        max_turns=4,
        timeout_seconds=600,
        ui_icon="hub",
        ui_color="blue",
        allow_tools=["conversation_search", "memory", "row_bot_status"],
    ),
)

_BUILTINS_BY_SLUG = {p["slug"]: p for p in BUILTIN_AGENT_PROFILES}
_BUILTINS_BY_ID = {p["id"]: p for p in BUILTIN_AGENT_PROFILES}


def ensure_agent_profiles_schema(*, force: bool = False) -> None:
    """Create and migrate the DB-backed Agent Profiles table."""
    global _SCHEMA_READY
    with _SCHEMA_LOCK:
        if _SCHEMA_READY and not force:
            return
        from row_bot.tasks import _get_conn

        conn = _get_conn()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_profiles (
                    id TEXT PRIMARY KEY,
                    slug TEXT UNIQUE NOT NULL,
                    display_name TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    when_to_use TEXT DEFAULT '',
                    instructions TEXT DEFAULT '',
                    handoff_contract TEXT DEFAULT '',
                    output_contract_json TEXT DEFAULT '{}',
                    scope TEXT DEFAULT 'user',
                    source TEXT DEFAULT 'user_created',
                    enabled INTEGER DEFAULT 1,
                    version INTEGER DEFAULT 1,
                    revision INTEGER DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    created_from_run_id TEXT DEFAULT '',
                    created_from_workflow_id TEXT DEFAULT '',
                    owner_workspace_id TEXT DEFAULT '',
                    model_policy_json TEXT DEFAULT '{}',
                    tool_policy_json TEXT DEFAULT '{}',
                    skill_policy_json TEXT DEFAULT '{}',
                    context_policy_json TEXT DEFAULT '{}',
                    workspace_policy_json TEXT DEFAULT '{}',
                    approval_policy_json TEXT DEFAULT '{}',
                    memory_policy_json TEXT DEFAULT '{}',
                    runtime_limits_json TEXT DEFAULT '{}',
                    ui_json TEXT DEFAULT '{}',
                    provenance_json TEXT DEFAULT '{}',
                    last_used_at TEXT DEFAULT '',
                    usage_count INTEGER DEFAULT 0
                )
                """
            )
            cols = {
                row[1]
                for row in conn.execute("PRAGMA table_info(agent_profiles)").fetchall()
            }
            for column, definition in _PROFILE_COLUMNS.items():
                if column not in cols:
                    conn.execute(f"ALTER TABLE agent_profiles ADD COLUMN {column} {definition}")
                    cols.add(column)
            conn.commit()
            _SCHEMA_READY = True
        finally:
            conn.close()


def _normalize_profile_dict(profile: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for field in _TEXT_FIELDS:
        result[field] = str(profile.get(field) or "")
    result["slug"] = _validate_slug(result["slug"])
    result["display_name"] = result["display_name"].strip() or result["slug"].replace("_", " ").title()
    result["scope"] = result["scope"] or "user"
    result["source"] = result["source"] or "user_created"
    if result["scope"] not in _SCOPES:
        raise AgentProfileError(f"Invalid Agent Profile scope: {result['scope']}")
    if result["source"] not in _SOURCES:
        raise AgentProfileError(f"Invalid Agent Profile source: {result['source']}")
    for field in _JSON_FIELDS:
        result[field] = _json_obj(profile.get(field), field=field)
    result["enabled"] = bool(profile.get("enabled", True))
    result["version"] = int(profile.get("version") or 1)
    result["revision"] = int(profile.get("revision") or 1)
    result["usage_count"] = int(profile.get("usage_count") or 0)
    _validate_policy_fields(result)
    return result


def _validate_policy_fields(profile: Mapping[str, Any]) -> None:
    tool_policy = _json_obj(profile.get("tool_policy_json"), field="tool_policy_json")
    capability = str(tool_policy.get("capability") or "read_only")
    if capability not in _CAPABILITIES:
        raise AgentProfileError(f"Invalid Agent Profile capability: {capability}")

    context_policy = _json_obj(profile.get("context_policy_json"), field="context_policy_json")
    context_mode = str(context_policy.get("default_context_mode") or "auto")
    if context_mode not in _CONTEXT_MODES:
        raise AgentProfileError(f"Invalid Agent Profile context mode: {context_mode}")

    workspace_policy = _json_obj(profile.get("workspace_policy_json"), field="workspace_policy_json")
    workspace_mode = str(workspace_policy.get("workspace_mode_default") or "auto")
    if workspace_mode not in _WORKSPACE_MODES:
        raise AgentProfileError(f"Invalid Agent Profile workspace mode: {workspace_mode}")

    approval_policy = _json_obj(profile.get("approval_policy_json"), field="approval_policy_json")
    approval_mode = str(approval_policy.get("mode") or "inherit")
    if approval_mode not in _APPROVAL_MODES:
        raise AgentProfileError(f"Invalid Agent Profile approval mode: {approval_mode}")


def _profile_from_row(row: sqlite3.Row | Mapping[str, Any]) -> dict[str, Any]:
    raw = dict(row)
    for field in _JSON_FIELDS:
        raw[field] = _json_obj(raw.get(field), field=field)
    raw["enabled"] = bool(raw.get("enabled", 1))
    return _normalize_profile_dict(raw)


def _db_profile_by_ref(ref: str) -> dict[str, Any] | None:
    ensure_agent_profiles_schema()
    value = str(ref or "").strip()
    slug = normalize_profile_slug(value)
    from row_bot.tasks import _get_conn

    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM agent_profiles WHERE id = ? OR slug = ?",
            (value, slug),
        ).fetchone()
    finally:
        conn.close()
    return _profile_from_row(row) if row else None


def _db_profiles() -> list[dict[str, Any]]:
    ensure_agent_profiles_schema()
    from row_bot.tasks import _get_conn

    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM agent_profiles ORDER BY scope, display_name, slug"
        ).fetchall()
    finally:
        conn.close()
    return [_profile_from_row(row) for row in rows]


def _builtin_profile_by_ref(ref: str) -> dict[str, Any] | None:
    value = str(ref or "").strip()
    if not value:
        return None
    slug = normalize_profile_slug(value)
    profile = _BUILTINS_BY_ID.get(value) or _BUILTINS_BY_SLUG.get(slug)
    if profile is not None:
        return copy.deepcopy(profile)
    for item in BUILTIN_AGENT_PROFILES:
        if normalize_profile_slug(item["display_name"]) == slug:
            return copy.deepcopy(item)
    return None


def list_agent_profiles(
    scope: str | None = None,
    *,
    enabled_only: bool = True,
    include_builtins: bool = True,
) -> list[dict[str, Any]]:
    """Return built-in and DB-backed Agent Profiles."""
    profiles: list[dict[str, Any]] = []
    if include_builtins:
        profiles.extend(copy.deepcopy(list(BUILTIN_AGENT_PROFILES)))
    db_rows = _db_profiles()
    builtin_slugs = {p["slug"] for p in profiles}
    profiles.extend(row for row in db_rows if row["slug"] not in builtin_slugs)
    if scope:
        profiles = [p for p in profiles if p.get("scope") == scope]
    if enabled_only:
        profiles = [p for p in profiles if p.get("enabled", True)]
    return profiles


def get_agent_profile(
    profile_id_or_slug: str,
    *,
    enabled_only: bool = False,
) -> dict[str, Any] | None:
    """Resolve an Agent Profile by id, slug, or display name."""
    ref = str(profile_id_or_slug or "").strip()
    if not ref:
        return None
    profile = _builtin_profile_by_ref(ref) or _db_profile_by_ref(ref)
    if profile is None:
        return None
    if enabled_only and not profile.get("enabled", True):
        return None
    return profile


def require_agent_profile(
    profile_id_or_slug: str,
    *,
    enabled_only: bool = True,
) -> dict[str, Any]:
    profile = get_agent_profile(profile_id_or_slug, enabled_only=enabled_only)
    if profile is None:
        raise AgentProfileError(f"Agent Profile not found or disabled: {profile_id_or_slug}")
    return profile


def _unique_slug(base: str) -> str:
    slug = _validate_slug(base)
    existing = {p["slug"] for p in list_agent_profiles(enabled_only=False)}
    if slug not in existing:
        return slug
    for index in range(2, 1000):
        candidate = f"{slug}_{index}"
        if candidate not in existing:
            return candidate
    raise AgentProfileError(f"Could not create a unique slug for {slug}")


def _profile_payload_for_save(profile: Mapping[str, Any]) -> dict[str, Any]:
    raw = dict(profile)
    raw["slug"] = _validate_slug(str(raw.get("slug") or raw.get("display_name") or ""))
    if raw["slug"] in _BUILTINS_BY_SLUG:
        raise AgentProfileError("Built-in Agent Profiles cannot be overwritten; duplicate one first.")
    if not str(raw.get("display_name") or "").strip():
        raw["display_name"] = raw["slug"].replace("_", " ").title()
    for field in _JSON_FIELDS:
        raw[field] = _json_obj(raw.get(field), field=field)
    raw.setdefault("scope", "user")
    raw.setdefault("source", "user_created")
    raw.setdefault("enabled", True)
    raw.setdefault("version", 1)
    raw.setdefault("revision", 1)
    raw.setdefault("usage_count", 0)
    raw.setdefault("created_at", _now())
    raw.setdefault("updated_at", _now())
    normalized = _normalize_profile_dict(raw)
    if normalized["scope"] == "system":
        raise AgentProfileError("Only built-in Agent Profiles may use scope='system'.")
    if normalized["source"] == "builtin":
        raise AgentProfileError("Only built-in Agent Profiles may use source='builtin'.")
    return normalized


def save_agent_profile(
    profile: Mapping[str, Any] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Create or update a DB-backed Agent Profile.

    Built-ins are read-only. To customize a built-in, call
    :func:`duplicate_agent_profile` and edit the duplicate.
    """
    ensure_agent_profiles_schema()
    payload = dict(profile or {})
    payload.update(kwargs)
    existing: dict[str, Any] | None = None
    profile_id = str(payload.get("id") or "").strip()
    if profile_id.startswith("builtin:") or profile_id in _BUILTINS_BY_ID:
        raise AgentProfileError("Built-in Agent Profiles cannot be edited.")
    slug_value = str(payload.get("slug") or payload.get("display_name") or "").strip()
    if profile_id:
        existing = _db_profile_by_ref(profile_id)
    if existing is None and slug_value:
        existing = _db_profile_by_ref(slug_value)

    now = _now()
    if existing:
        payload["id"] = existing["id"]
        payload["created_at"] = existing.get("created_at") or now
        payload["revision"] = int(existing.get("revision") or 1) + 1
    else:
        payload["id"] = profile_id or uuid.uuid4().hex[:12]
        payload.setdefault("created_at", now)
        payload.setdefault("revision", 1)
    payload["updated_at"] = now
    normalized = _profile_payload_for_save(payload)

    conflict = _db_profile_by_ref(normalized["slug"])
    if conflict and conflict["id"] != normalized["id"]:
        raise AgentProfileError(f"Agent Profile slug already exists: {normalized['slug']}")

    fields = list(_PROFILE_COLUMNS)
    placeholders = ", ".join("?" for _ in fields)
    updates = ", ".join(f"{field} = excluded.{field}" for field in fields if field != "id")
    values: list[Any] = []
    for field in fields:
        value = normalized.get(field)
        if field in _JSON_FIELDS:
            values.append(_json_text(value, field=field))
        elif field == "enabled":
            values.append(1 if value else 0)
        else:
            values.append(value)

    from row_bot.tasks import _get_conn

    conn = _get_conn()
    try:
        conn.execute(
            f"INSERT INTO agent_profiles ({', '.join(fields)}) "
            f"VALUES ({placeholders}) "
            f"ON CONFLICT(id) DO UPDATE SET {updates}",
            values,
        )
        conn.commit()
    finally:
        conn.close()
    return require_agent_profile(normalized["id"], enabled_only=False)


def delete_agent_profile(profile_id: str) -> bool:
    """Delete a DB-backed Agent Profile. Built-ins cannot be deleted."""
    ref = str(profile_id or "").strip()
    if not ref:
        return False
    if _builtin_profile_by_ref(ref):
        raise AgentProfileError("Built-in Agent Profiles cannot be deleted.")
    profile = _db_profile_by_ref(ref)
    if profile is None:
        return False
    from row_bot.tasks import _get_conn

    conn = _get_conn()
    try:
        conn.execute("DELETE FROM agent_profiles WHERE id = ?", (profile["id"],))
        conn.commit()
    finally:
        conn.close()
    return True


def duplicate_agent_profile(
    profile_id_or_slug: str,
    overrides: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    source = require_agent_profile(profile_id_or_slug, enabled_only=False)
    override = dict(overrides or {})
    base_slug = str(override.pop("slug", "") or f"{source['slug']}_copy")
    duplicate = copy.deepcopy(source)
    duplicate.update(override)
    duplicate["id"] = uuid.uuid4().hex[:12]
    duplicate["slug"] = _unique_slug(base_slug)
    duplicate["display_name"] = str(
        override.get("display_name") or f"{source['display_name']} Copy"
    )
    duplicate["scope"] = str(override.get("scope") or "user")
    duplicate["source"] = str(override.get("source") or "user_created")
    duplicate["enabled"] = bool(override.get("enabled", True))
    duplicate["created_from_run_id"] = str(override.get("created_from_run_id") or "")
    duplicate["created_from_workflow_id"] = str(override.get("created_from_workflow_id") or "")
    duplicate["provenance_json"] = {
        **_json_obj(source.get("provenance_json"), field="provenance_json"),
        "duplicated_from_profile_id": source["id"],
        "duplicated_from_profile_slug": source["slug"],
    }
    duplicate["usage_count"] = 0
    duplicate["last_used_at"] = ""
    return save_agent_profile(duplicate)


def snapshot_agent_profile(profile_id_or_slug: str) -> dict[str, Any]:
    """Return an immutable point-in-time snapshot for an Agent Run."""
    profile = require_agent_profile(profile_id_or_slug, enabled_only=False)
    snapshot = copy.deepcopy(profile)
    snapshot["snapshot_at"] = _now()
    snapshot["snapshot_profile_id"] = profile["id"]
    snapshot["snapshot_profile_slug"] = profile["slug"]
    snapshot["snapshot_revision"] = profile.get("revision", 1)
    return snapshot


def mark_agent_profile_used(profile_id_or_slug: str) -> None:
    """Increment usage metadata for DB-backed profiles."""
    profile = get_agent_profile(profile_id_or_slug, enabled_only=False)
    if not profile or profile["source"] == "builtin":
        return
    from row_bot.tasks import _get_conn

    conn = _get_conn()
    try:
        conn.execute(
            "UPDATE agent_profiles SET usage_count = usage_count + 1, "
            "last_used_at = ?, updated_at = ? WHERE id = ?",
            (_now(), _now(), profile["id"]),
        )
        conn.commit()
    finally:
        conn.close()


def canonical_profile_ref(profile_id_or_slug: str) -> str:
    profile = require_agent_profile(profile_id_or_slug, enabled_only=True)
    return profile["id"]


def _approval_rank(mode: str) -> int:
    mode = normalize_approval_mode(mode, DEFAULT_APPROVAL_MODE)
    return {"block": 0, "approve": 1, "allow_all": 2}.get(mode, 1)


def _effective_approval(parent_mode: str, profile_mode: str) -> tuple[str, str]:
    parent = normalize_approval_mode(parent_mode, DEFAULT_APPROVAL_MODE)
    requested = str(profile_mode or "inherit")
    if requested == "inherit":
        return parent, ""
    requested = normalize_approval_mode(requested, "")
    if requested not in {"block", "approve", "allow_all"}:
        return parent, f"Invalid profile approval mode '{profile_mode}' ignored."
    if _approval_rank(requested) > _approval_rank(parent):
        return parent, (
            f"Profile requested approval mode '{requested}', but parent cap "
            f"'{parent}' is stricter; using '{parent}'."
        )
    return requested, ""


def resolve_profile_for_run(
    profile_id_or_slug: str | None = None,
    *,
    parent_approval_mode: str = DEFAULT_APPROVAL_MODE,
    require_enabled: bool = True,
) -> dict[str, Any]:
    """Resolve a profile and return effective policy data for a run."""
    ref = str(profile_id_or_slug or "").strip() or "row_bot_default"
    profile = require_agent_profile(ref, enabled_only=require_enabled)
    approval_policy = _json_obj(profile.get("approval_policy_json"), field="approval_policy_json")
    effective_approval, warning = _effective_approval(
        parent_approval_mode,
        str(approval_policy.get("mode") or "inherit"),
    )
    warnings = [warning] if warning else []
    snapshot = snapshot_agent_profile(profile["id"])
    return {
        "profile": profile,
        "profile_id": profile["id"],
        "profile_slug": profile["slug"],
        "profile_display_name": profile["display_name"],
        "profile_snapshot": snapshot,
        "effective_approval_mode": effective_approval,
        "tool_policy": copy.deepcopy(profile["tool_policy_json"]),
        "skill_policy": copy.deepcopy(profile["skill_policy_json"]),
        "context_policy": copy.deepcopy(profile["context_policy_json"]),
        "workspace_policy": copy.deepcopy(profile["workspace_policy_json"]),
        "warnings": warnings,
    }


def profile_summary(profile: Mapping[str, Any]) -> str:
    tool_policy = _json_obj(profile.get("tool_policy_json"), field="tool_policy_json")
    context_policy = _json_obj(profile.get("context_policy_json"), field="context_policy_json")
    capability = str(tool_policy.get("capability") or "read_only")
    context = str(context_policy.get("default_context_mode") or "auto")
    return (
        f"{profile.get('display_name') or profile.get('slug')} "
        f"({capability}, context={context}) - {profile.get('description') or ''}"
    ).strip()
