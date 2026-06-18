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


def _dedup_text_list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        items = value
    elif value:
        items = [value]
    else:
        items = []
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _normalize_tool_policy(policy: Mapping[str, Any] | None) -> dict[str, Any]:
    result = copy.deepcopy(dict(policy or {}))
    result.pop("deny_memory_write", None)
    result.pop("allow_tool_groups", None)
    result["capability"] = str(result.get("capability") or "read_only")
    result["allow_tools"] = _dedup_text_list(result.get("allow_tools"))
    if "allow_delegation" in result:
        result["allow_delegation"] = bool(result.get("allow_delegation"))
    return result


def _normalize_context_policy(policy: Mapping[str, Any] | None) -> dict[str, Any]:
    result = copy.deepcopy(dict(policy or {}))
    result.pop("include_memory", None)
    result["default_context_mode"] = str(result.get("default_context_mode") or "auto")
    return result


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


_COMMON_PROFILE_TOOLS = [
    "memory",
    "row_bot_status",
    "conversation_search",
    "duckduckgo",
    "web_search",
    "url_reader",
    "filesystem",
    "shell",
]


def _profile_tools(*items: str) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in [*_COMMON_PROFILE_TOOLS, *items]:
        text = str(item or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


_PLAN_TOOLS = _profile_tools(
    "calendar",
    "documents",
    "gmail",
    "task",
    "tracker",
    "weather",
    "calculator",
)

_RESEARCH_TOOLS = _profile_tools(
    "arxiv",
    "browser",
    "documents",
    "wiki",
    "wikipedia",
    "youtube",
)

_WRITE_TOOLS = _profile_tools(
    "calendar",
    "documents",
    "gmail",
    "task",
)

_IDEAS_TOOLS = _profile_tools(
    "image_gen",
)

_KNOWLEDGE_TOOLS = _profile_tools(
    "documents",
    "wiki",
    "wikipedia",
)

_DATA_TOOLS = _profile_tools(
    "calculator",
    "chart",
    "documents",
    "wolfram_alpha",
)

_CREATIVE_TOOLS = _profile_tools(
    "designer",
    "documents",
    "image_gen",
    "video_gen",
    "vision",
)

_AUTOMATE_TOOLS = _profile_tools(
    "calendar",
    "gmail",
    "task",
    "tracker",
    "weather",
)

_QUALITY_REVIEW_TOOLS = _profile_tools(
    "documents",
    "calculator",
)

_DEVELOP_TOOLS = _profile_tools(
    "browser",
    "custom_tool_builder",
    "developer",
    "system_info",
    "vision",
)

_SYNTHESIS_TOOLS = _profile_tools(
    "documents",
)

_VERIFIER_TOOLS = _profile_tools(
    "browser",
    "system_info",
    "vision",
)

_CODE_REVIEW_TOOLS = _profile_tools(
    "browser",
    "developer",
    "documents",
    "system_info",
)

_WEB_UI_CHECKER_TOOLS = _profile_tools(
    "browser",
    "system_info",
    "vision",
)


def _tool_policy(
    capability: str,
    *,
    allow_tools: list[str] | None = None,
    allow_delegation: bool = False,
) -> dict[str, Any]:
    return {
        "capability": capability,
        "allow_tools": list(allow_tools or []),
        "allow_delegation": allow_delegation,
    }


def _skill_policy(
    *,
    skills_override: list[str] | None = None,
) -> dict[str, Any]:
    return {"skills_override": list(skills_override or [])}


def _context_policy(mode: str) -> dict[str, Any]:
    return {
        "default_context_mode": mode,
        "include_parent_summary": True,
        "include_selected_messages": False,
        "include_workspace_context": True,
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
    ui_group: str = "Everyday",
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
        "runtime_limits_json": _limits(max_turns, timeout_seconds),
        "ui_json": {"icon": ui_icon, "color": ui_color, "group": ui_group},
        "provenance_json": {"builtin": True},
        "last_used_at": "",
        "usage_count": 0,
    }
    return profile


BUILTIN_AGENT_PROFILES: tuple[dict[str, Any], ...] = (
    _builtin(
        "row_bot_default",
        "Default",
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
        ui_group="Everyday",
    ),
    _builtin(
        "plan",
        "Plan",
        "Plan projects, decisions, errands, travel, schedules, reminders, and career next steps.",
        "Use when the user needs a clear plan, logistics, tradeoffs, sequencing, or career/job-search support.",
        "Turn fuzzy goals into a concise plan with assumptions, options, risks, owners, and practical next actions.",
        capability="write_capable",
        context_mode="focused",
        workspace_mode="auto",
        max_turns=8,
        timeout_seconds=900,
        ui_icon="route",
        ui_color="indigo",
        ui_group="Everyday",
        allow_tools=_PLAN_TOOLS,
        skills_override=["brain_dump", "task_automation"],
    ),
    _builtin(
        "research",
        "Research",
        "Research current facts, sources, documents, videos, references, and explain concepts clearly.",
        "Use when facts may have changed, source attribution matters, or the user wants evidence-backed learning.",
        "Verify claims against source material. Include dates, links, uncertainty, examples, and a useful synthesis.",
        capability="read_only",
        context_mode="focused",
        workspace_mode="read_only",
        max_turns=8,
        timeout_seconds=900,
        ui_icon="manage_search",
        ui_color="teal",
        ui_group="Everyday",
        allow_tools=_RESEARCH_TOOLS,
        skills_override=["deep_research", "web_navigator"],
    ),
    _builtin(
        "write",
        "Write",
        "Draft, revise, summarize, polish, and turn notes or meetings into follow-ups.",
        "Use for tone, structure, clarity, grammar, summaries, meeting outcomes, and follow-up drafts.",
        "Improve writing while preserving intent. Extract decisions, owners, due dates, unanswered questions, and concise next drafts.",
        capability="write_capable",
        context_mode="focused",
        workspace_mode="auto",
        max_turns=8,
        timeout_seconds=900,
        ui_icon="edit_note",
        ui_color="green",
        ui_group="Everyday",
        allow_tools=_WRITE_TOOLS,
        skills_override=["humanizer", "meeting_notes"],
    ),
    _builtin(
        "ideas",
        "Ideas",
        "Generate ideas, names, alternatives, creative directions, gifts, events, and angles.",
        "Use when the user wants divergent options before choosing or refining a direction.",
        "Offer varied options, explain the strongest candidates, and avoid converging too early.",
        capability="read_only",
        context_mode="focused",
        workspace_mode="read_only",
        max_turns=8,
        timeout_seconds=600,
        ui_icon="psychology",
        ui_color="amber",
        ui_group="Everyday",
        allow_tools=_IDEAS_TOOLS,
    ),
    _builtin(
        "knowledge",
        "Knowledge",
        "Organize memories, documents, project notes, and relationships between saved facts.",
        "Use when the user asks what Row-Bot knows, wants notes organized, or needs durable context cleaned up.",
        "Use memory deliberately. Surface what is known, what is uncertain, and what should be saved or updated.",
        capability="write_capable",
        context_mode="focused",
        workspace_mode="read_only",
        max_turns=8,
        timeout_seconds=900,
        ui_icon="library_books",
        ui_color="brown",
        ui_group="Work",
        allow_tools=_KNOWLEDGE_TOOLS,
        skills_override=["knowledge_base", "self_reflection", "brain_dump"],
    ),
    _builtin(
        "data",
        "Data",
        "Analyze spreadsheets, CSVs, tables, charts, metrics, and lightweight forecasts.",
        "Use when the user has data to compare, summarize, visualize, or sanity-check.",
        "Inspect the data, state assumptions, show calculations, and separate observations from recommendations.",
        capability="write_capable",
        context_mode="focused",
        workspace_mode="auto",
        max_turns=8,
        timeout_seconds=1200,
        ui_icon="query_stats",
        ui_color="deep-purple",
        ui_group="Work",
        allow_tools=_DATA_TOOLS,
        skills_override=["data_analyst"],
    ),
    _builtin(
        "automate",
        "Automate",
        "Design reminders, monitors, recurring tasks, and multi-step workflows.",
        "Use when the user wants Row-Bot to remember, watch, schedule, or repeat work later.",
        "Keep automations reviewable. Explain triggers, actions, approval points, and how to adjust them.",
        capability="write_capable",
        context_mode="focused",
        workspace_mode="auto",
        max_turns=8,
        timeout_seconds=1200,
        ui_icon="precision_manufacturing",
        ui_color="orange",
        ui_group="Work",
        allow_tools=_AUTOMATE_TOOLS,
        skills_override=["task_automation"],
    ),
    _builtin(
        "review",
        "Review",
        "Review plans, writing, data analysis, workflows, artifacts, and code changes for risk.",
        "Use after a draft, plan, workflow, or artifact needs a careful second pass.",
        "Findings first. Include severity, evidence, reproduction notes when relevant, and residual test gaps.",
        capability="read_only",
        context_mode="focused",
        workspace_mode="read_only",
        max_turns=8,
        timeout_seconds=900,
        ui_icon="fact_check",
        ui_color="purple",
        ui_group="Work",
        allow_tools=_QUALITY_REVIEW_TOOLS,
    ),
    _builtin(
        "design",
        "Design",
        "Create visual directions, images, layouts, brand assets, and presentation-ready concepts.",
        "Use for visual ideation, mockups, generated images, design critique, and creative production.",
        "Make concrete visual choices and preserve user brand or taste preferences when memory is available.",
        capability="write_capable",
        context_mode="focused",
        workspace_mode="auto",
        max_turns=8,
        timeout_seconds=1200,
        ui_icon="palette",
        ui_color="pink",
        ui_group="Creative",
        allow_tools=_CREATIVE_TOOLS,
        skills_override=["design_creator"],
    ),
    _builtin(
        "develop",
        "Develop",
        "Implement, debug, inspect repository context, make focused code changes, and run checks.",
        "Use for normal developer work when the user wants code inspected, changed, debugged, or verified.",
        "Ground changes in the current repository. Keep edits focused, run relevant checks, and report files, tests, risks, and follow-ups.",
        capability="write_capable",
        context_mode="focused",
        workspace_mode="single_writer",
        max_turns=12,
        timeout_seconds=1800,
        tests_required=True,
        ui_icon="terminal",
        ui_color="blue-grey",
        ui_group="Developer",
        allow_tools=_DEVELOP_TOOLS,
    ),
    _builtin(
        "code_review",
        "Code Review",
        "Review implementation correctness, regressions, and test coverage.",
        "Use when code-specific review is needed instead of general quality review.",
        "Find bugs first. Include file references, severity, reproduction notes, and missing tests.",
        capability="read_only",
        context_mode="focused",
        workspace_mode="read_only",
        max_turns=8,
        timeout_seconds=900,
        ui_icon="code",
        ui_color="blue-grey",
        ui_group="Developer",
        allow_tools=_CODE_REVIEW_TOOLS,
    ),
    _builtin(
        "ui_check",
        "UI Check",
        "Reproduce browser UI behavior with snapshots, screenshots, console clues, and visual evidence.",
        "Use for visible UI bugs, browser automation, screenshots, console clues, and reproduction steps.",
        "Reproduce first. Capture observed vs expected behavior, evidence, likely owner files, and next action.",
        capability="write_capable",
        context_mode="focused",
        workspace_mode="read_only",
        max_turns=8,
        timeout_seconds=1200,
        ui_icon="bug_report",
        ui_color="red",
        ui_group="Developer",
        allow_tools=_WEB_UI_CHECKER_TOOLS,
        skills_override=["web_navigator"],
    ),
    _builtin(
        "worker",
        "Worker",
        "Advanced internal helper for scoped implementation work after requirements are clear.",
        "Use for child-agent implementation work that should inherit the normal enabled tool set.",
        "Make the requested change narrowly. Report changed files, tests, risks, and follow-ups.",
        capability="write_capable",
        context_mode="focused",
        workspace_mode="single_writer",
        max_turns=12,
        timeout_seconds=1800,
        ui_icon="construction",
        ui_color="deep-orange",
        ui_group="Advanced/Internal",
    ),
    _builtin(
        "synthesize",
        "Synthesize",
        "Advanced internal helper for combining child results and resolving conflicts.",
        "Use after multiple agents finish or when partial results need consolidation.",
        "Summarize each result, resolve conflicts, state the decision, and identify next steps.",
        capability="read_only",
        context_mode="focused",
        workspace_mode="read_only",
        max_turns=4,
        timeout_seconds=600,
        ui_icon="hub",
        ui_color="blue",
        ui_group="Advanced/Internal",
        allow_tools=_SYNTHESIS_TOOLS,
    ),
    _builtin(
        "verify",
        "Verify",
        "Advanced helper for running focused checks and summarizing failures or artifacts.",
        "Use when tests, lint, build, browser checks, or verification commands need to run.",
        "Run scoped checks under the active policy. Report commands, exits, failures, artifacts, and likely owners.",
        capability="write_capable",
        context_mode="focused",
        workspace_mode="single_writer",
        max_turns=8,
        timeout_seconds=1200,
        tests_required=True,
        ui_icon="science",
        ui_color="orange",
        ui_group="Advanced/Internal",
        allow_tools=_VERIFIER_TOOLS,
    ),
)

_BUILTINS_BY_SLUG = {p["slug"]: p for p in BUILTIN_AGENT_PROFILES}
_BUILTINS_BY_ID = {p["id"]: p for p in BUILTIN_AGENT_PROFILES}
_BUILTIN_PROFILE_ALIASES = {
    "default": "row_bot_default",
    "planner": "plan",
    "life_admin": "plan",
    "career_guide": "plan",
    "researcher": "research",
    "learning_coach": "research",
    "writer_editor": "write",
    "meeting_followup": "write",
    "brainstormer": "ideas",
    "knowledge_librarian": "knowledge",
    "data_analyst": "data",
    "automation_builder": "automate",
    "quality_reviewer": "review",
    "creative_designer": "design",
    "code_reviewer": "code_review",
    "web_ui_checker": "ui_check",
    "synthesizer": "synthesize",
    "verifier": "verify",
}


def builtin_profile_aliases() -> dict[str, str]:
    """Return folded built-in profile aliases mapped to canonical slugs."""
    return dict(_BUILTIN_PROFILE_ALIASES)


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
    result["tool_policy_json"] = _normalize_tool_policy(result.get("tool_policy_json"))
    result["context_policy_json"] = _normalize_context_policy(result.get("context_policy_json"))
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


def _builtin_profile_by_ref(
    ref: str,
    *,
    include_aliases: bool = True,
    include_display_names: bool = True,
) -> dict[str, Any] | None:
    value = str(ref or "").strip()
    if not value:
        return None
    slug = normalize_profile_slug(value)
    profile = _BUILTINS_BY_ID.get(value) or _BUILTINS_BY_SLUG.get(slug)
    if profile is not None:
        return copy.deepcopy(profile)
    if include_aliases:
        alias_slug = _BUILTIN_PROFILE_ALIASES.get(slug)
        if alias_slug:
            profile = _BUILTINS_BY_SLUG.get(alias_slug)
            if profile is not None:
                return copy.deepcopy(profile)
    if include_display_names:
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
    """Resolve an Agent Profile by id, slug, folded built-in alias, or display name."""
    ref = str(profile_id_or_slug or "").strip()
    if not ref:
        return None
    profile = (
        _builtin_profile_by_ref(ref, include_aliases=False, include_display_names=False)
        or _db_profile_by_ref(ref)
        or _builtin_profile_by_ref(ref, include_aliases=True, include_display_names=True)
    )
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
    if _builtin_profile_by_ref(ref, include_aliases=False, include_display_names=False):
        raise AgentProfileError("Built-in Agent Profiles cannot be deleted.")
    profile = _db_profile_by_ref(ref)
    if profile is None:
        if _builtin_profile_by_ref(ref):
            raise AgentProfileError("Built-in Agent Profiles cannot be deleted.")
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
    tool_scope = "selected tools" if tool_policy.get("allow_tools") else "inherits enabled tools"
    return (
        f"{profile.get('display_name') or profile.get('slug')} "
        f"({capability}, {tool_scope}, context={context}) - {profile.get('description') or ''}"
    ).strip()
