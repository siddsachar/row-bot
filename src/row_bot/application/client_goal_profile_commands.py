"""Bounded client views and reviewed controls for goals and agent profiles.

Goals are always scoped to one authenticated conversation.  Profiles are a
global library, but only their public policy projection is returned.  Local
paths, provenance, model internals, and stored instruction bodies are not part
of the passive wire contract.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
import hashlib
import json
import re
import sqlite3
from typing import Any
from uuid import UUID, uuid5

from row_bot import agent_profiles, goals
from row_bot.runtime import admissions


_PROFILE_SLUG = re.compile(r"[a-z][a-z0-9_-]{1,63}")
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9:_.@/-]{0,255}")
_GOAL_OPERATIONS = frozenset({"start", "pause", "resume", "complete", "clear"})
_PROFILE_OPERATIONS = frozenset(
    {"create", "edit", "duplicate", "delete", "enable", "disable"}
)
_PROFILE_SCOPES = frozenset({"system", "user", "workspace", "plugin", "imported"})
_CAPABILITIES = frozenset({"read_only", "write_capable", "orchestrator"})
_CONTEXT_MODES = frozenset({"auto", "focused", "recent", "full", "empty", "resume"})
_WORKSPACE_MODES = frozenset({"auto", "read_only", "single_writer", "worktree"})
_APPROVAL_MODES = frozenset({"inherit", "block", "approve", "allow_all"})
_MAX_ROWS = 500


class GoalProfileCommandError(ValueError):
    """A stable, client-safe goal/profile failure."""

    def __init__(self, code: str, current_revision: str | None = None):
        self.code = code
        self.current_revision = current_revision
        super().__init__(code)


def _text(value: object, maximum: int, *, required: bool = False) -> str:
    if not isinstance(value, str):
        raise GoalProfileCommandError("invalid_fields")
    try:
        if (
            len(value.encode("utf-8")) > maximum
            or "\0" in value
            or any(0xD800 <= ord(char) <= 0xDFFF for char in value)
        ):
            raise ValueError
    except (UnicodeError, ValueError):
        raise GoalProfileCommandError("invalid_fields") from None
    result = value.strip()
    if required and not result:
        raise GoalProfileCommandError("invalid_fields")
    return result


def _public_text(value: object, maximum: int) -> str:
    """Return bounded text with common local paths and secrets redacted."""

    if not isinstance(value, str):
        return ""
    value = value.replace("\0", "")
    value = re.sub(
        r"(?i)\b(?:sk|rk|pk)-[A-Za-z0-9_-]{12,}\b",
        "[secret redacted]",
        value,
    )
    value = re.sub(
        r"(?i)\b(?:api[_-]?key|token|password|secret)\s*[:=]\s*[^\s,;]+",
        "secret=[redacted]",
        value,
    )
    value = re.sub(
        r"(?i)\b[A-Z]:[\\/](?:Users[\\/][^\\/\s]+|[^\s<>\"|?*]+)(?:[\\/][^\s<>\"|?*]+)*",
        "[local path]",
        value,
    )
    value = re.sub(r"(?<!\w)/(?:Users|home)/[^\s]+", "[local path]", value)
    encoded = value.encode("utf-8", errors="ignore")
    if len(encoded) <= maximum:
        return value
    return encoded[:maximum].decode("utf-8", errors="ignore").rstrip() + "…"


def _string_list(value: object, *, count: int, length: int) -> list[str]:
    if not isinstance(value, list) or len(value) > count:
        raise GoalProfileCommandError("invalid_fields")
    result: list[str] = []
    for item in value:
        text = _text(item, length, required=True)
        if not _IDENTIFIER.fullmatch(text) or text in result:
            raise GoalProfileCommandError("invalid_fields")
        result.append(text)
    return result


def _uuid(value: object) -> str:
    try:
        if not isinstance(value, str) or str(UUID(value)) != value:
            raise ValueError
        return value
    except (ValueError, AttributeError, TypeError):
        raise GoalProfileCommandError("invalid_command") from None


def _hash(value: object) -> str:
    try:
        wire = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError, RecursionError):
        raise GoalProfileCommandError("invalid_command") from None
    return hashlib.sha256(wire.encode()).hexdigest()


def _cursor(cursor: str | None, revision: str, limit: int) -> int:
    if cursor is None:
        return 0
    try:
        prior, raw_offset = cursor.split(":", 1)
        offset = int(raw_offset)
        if (
            prior != revision
            or not 0 < offset <= _MAX_ROWS
            or offset % limit
            or len(cursor) > 80
        ):
            raise ValueError
        return offset
    except (AttributeError, ValueError):
        raise GoalProfileCommandError("cursor_expired") from None


def _goal_public(goal: Mapping[str, Any], conversation_id: str) -> dict[str, Any]:
    evidence = goal.get("evidence_json")
    blockers = goal.get("blockers_json")
    return {
        "id": _public_text(goal.get("id"), 128),
        "scope": "conversation",
        "conversation_id": conversation_id,
        "objective": _public_text(goal.get("objective"), 4096),
        "status": str(goal.get("status") or ""),
        "revision": str(max(0, int(goal.get("revision") or 0))),
        "turns_used": max(0, int(goal.get("turns_used") or 0)),
        "max_turns": max(0, int(goal.get("max_turns") or 0)),
        "token_budget": max(0, int(goal.get("token_budget") or 0)),
        "tokens_used": max(0, int(goal.get("tokens_used") or 0)),
        "last_progress": _public_text(goal.get("last_progress"), 2048),
        "last_reason": _public_text(goal.get("last_reason"), 2048),
        "evidence": [
            _public_text(
                item if isinstance(item, str) else json.dumps(item, sort_keys=True), 512
            )
            for item in (evidence if isinstance(evidence, list) else [])[-10:]
        ],
        "blockers": [
            _public_text(
                item if isinstance(item, str) else json.dumps(item, sort_keys=True), 512
            )
            for item in (blockers if isinstance(blockers, list) else [])[-10:]
        ],
        "active_profile_id": _public_text(goal.get("profile_id"), 256),
    }


def _policy(profile: Mapping[str, Any], field: str) -> dict[str, Any]:
    value = profile.get(field)
    return deepcopy(value) if isinstance(value, dict) else {}


def _profile_public(
    profile: Mapping[str, Any], *, detail: bool = False
) -> dict[str, Any]:
    tools = _policy(profile, "tool_policy_json")
    skills = _policy(profile, "skill_policy_json")
    context = _policy(profile, "context_policy_json")
    workspace = _policy(profile, "workspace_policy_json")
    approval = _policy(profile, "approval_policy_json")
    instructions = str(profile.get("instructions") or "")
    value = {
        "id": _public_text(profile.get("id"), 256),
        "slug": _public_text(profile.get("slug"), 64),
        "display_name": _public_text(profile.get("display_name"), 160),
        "description": _public_text(profile.get("description"), 1024),
        "when_to_use": _public_text(profile.get("when_to_use"), 1024),
        "scope": str(profile.get("scope") or "user"),
        "surface_scope": "global",
        "source": str(profile.get("source") or ""),
        "enabled": profile.get("enabled") is not False,
        "editable": str(profile.get("source") or "") != "builtin",
        "revision": str(max(1, int(profile.get("revision") or 1))),
        "capability": str(tools.get("capability") or "read_only"),
        "allow_tools": [
            _public_text(item, 256)
            for item in tools.get("allow_tools", [])[:100]
            if isinstance(item, str)
        ],
        "skills": [
            _public_text(item, 256)
            for item in skills.get("skills_override", [])[:100]
            if isinstance(item, str)
        ],
        "context_mode": str(context.get("default_context_mode") or "auto"),
        "workspace_mode": str(workspace.get("workspace_mode_default") or "auto"),
        "approval_mode": str(approval.get("mode") or "inherit"),
        # Reusable instructions can contain private local policy and prompt data.
        # Their presence is enough for a passive browser projection; edits use
        # an explicit replace-only field and never round-trip the stored body.
        "instructions_preview": "",
        "instructions_truncated": bool(instructions),
    }
    if detail:
        # A replacement is explicit.  The stored body is never silently round-tripped
        # through a redacted browser projection.
        value["instruction_edit"] = {
            "mode": "replace_only",
            "stored": bool(instructions),
        }
    return value


def read_goals(
    conversation_id: str,
    *,
    query: str = "",
    cursor: str | None = None,
    limit: int = 50,
    validate: Callable[[], None],
    goal_owner: Any = goals,
) -> dict[str, Any]:
    """Return a stable page from one conversation; never enumerate other chats."""

    validate()
    conversation_id = _text(conversation_id, 256, required=True)
    query = _text(query, 256).casefold()
    if type(limit) is not int or not 1 <= limit <= 50:
        raise GoalProfileCommandError("invalid_query")
    rows = list(goal_owner.list_goals(thread_id=conversation_id, limit=_MAX_ROWS + 1))
    if len(rows) > _MAX_ROWS:
        raise GoalProfileCommandError("goal_library_too_large")
    items = [_goal_public(row, conversation_id) for row in rows]
    if query:
        items = [
            item
            for item in items
            if query in f"{item['objective']} {item['status']}".casefold()
        ]
    revision = _hash(items)
    offset = _cursor(cursor, revision, limit)
    current = _current_goal(goal_owner, conversation_id)
    current_id = str(current.get("id") or "") if current else None
    current_revision = (
        str(max(0, int(current.get("revision") or 0))) if current else "none"
    )
    validate()
    return {
        "schema_version": 1,
        "scope": "conversation",
        "conversation_id": conversation_id,
        "revision": revision,
        "current_goal_id": current_id,
        "current_revision": current_revision,
        "items": items[offset : offset + limit],
        "total": len(items),
        "next_cursor": (
            f"{revision}:{offset + limit}" if offset + limit < len(items) else None
        ),
    }


def read_goal(
    conversation_id: str,
    goal_id: str,
    *,
    validate: Callable[[], None],
    goal_owner: Any = goals,
) -> dict[str, Any]:
    validate()
    conversation_id = _text(conversation_id, 256, required=True)
    goal_id = _text(goal_id, 128, required=True)
    goal = goal_owner.get_goal(goal_id)
    if not goal or str(goal.get("thread_id") or "") != conversation_id:
        raise GoalProfileCommandError("not_found")
    value = _goal_public(goal, conversation_id)
    validate()
    return {"schema_version": 1, "goal": value}


def read_profiles(
    *,
    query: str = "",
    scope: str | None = None,
    cursor: str | None = None,
    limit: int = 50,
    validate: Callable[[], None],
    profile_owner: Any = agent_profiles,
) -> dict[str, Any]:
    validate()
    query = _text(query, 256).casefold()
    if scope is not None and scope not in _PROFILE_SCOPES:
        raise GoalProfileCommandError("invalid_query")
    if type(limit) is not int or not 1 <= limit <= 50:
        raise GoalProfileCommandError("invalid_query")
    rows = list(profile_owner.list_agent_profiles(enabled_only=False))
    if len(rows) > _MAX_ROWS:
        raise GoalProfileCommandError("profile_library_too_large")
    items = [_profile_public(row) for row in rows]
    if scope is not None:
        items = [item for item in items if item["scope"] == scope]
    if query:
        items = [
            item
            for item in items
            if query
            in f"{item['display_name']} {item['slug']} {item['description']}".casefold()
        ]
    items.sort(key=lambda item: (item["display_name"].casefold(), item["id"]))
    revision = _hash(items)
    offset = _cursor(cursor, revision, limit)
    validate()
    return {
        "schema_version": 1,
        "scope": "global",
        "revision": revision,
        "items": items[offset : offset + limit],
        "total": len(items),
        "next_cursor": (
            f"{revision}:{offset + limit}" if offset + limit < len(items) else None
        ),
    }


def read_profile(
    profile_id: str,
    *,
    validate: Callable[[], None],
    profile_owner: Any = agent_profiles,
) -> dict[str, Any]:
    validate()
    profile_id = _text(profile_id, 256, required=True)
    profile = profile_owner.get_agent_profile(profile_id, enabled_only=False)
    if profile is None:
        raise GoalProfileCommandError("not_found")
    value = _profile_public(profile, detail=True)
    validate()
    return {"schema_version": 1, "profile": value}


def _current_goal(goal_owner: Any, conversation_id: str) -> dict[str, Any] | None:
    return goal_owner.get_current_goal(conversation_id, include_terminal=True)


def _goal_review(
    payload: object,
    *,
    validate: Callable[[], None],
    goal_owner: Any,
) -> dict[str, Any]:
    validate()
    if not isinstance(payload, dict) or set(payload) != {
        "conversation_id",
        "goal_id",
        "revision",
        "operation",
        "objective",
        "max_turns",
        "reason",
    }:
        raise GoalProfileCommandError("invalid_command")
    conversation_id = _text(payload["conversation_id"], 256, required=True)
    operation = payload["operation"]
    if operation not in _GOAL_OPERATIONS:
        raise GoalProfileCommandError("invalid_command")
    current = _current_goal(goal_owner, conversation_id)
    current_id = str(current.get("id") or "") if current else None
    current_revision = str(int(current.get("revision") or 0)) if current else "none"
    if payload["goal_id"] != current_id or payload["revision"] != current_revision:
        raise GoalProfileCommandError("goal_revision_conflict", current_revision)
    objective: str | None = None
    max_turns: int | None = None
    reason: str | None = None
    if operation == "start":
        objective = _text(payload["objective"], 4096, required=True)
        if (
            type(payload["max_turns"]) is not int
            or not 1 <= payload["max_turns"] <= 1000
        ):
            raise GoalProfileCommandError("invalid_fields")
        max_turns = payload["max_turns"]
        if payload["reason"] is not None:
            raise GoalProfileCommandError("invalid_command")
    else:
        if (
            not current
            or payload["objective"] is not None
            or payload["max_turns"] is not None
        ):
            raise GoalProfileCommandError("invalid_command")
        reason = _text(payload["reason"] or "", 1024)
        allowed = {
            "pause": {"active", "waiting_approval"},
            "resume": {"paused", "blocked", "waiting_approval"},
            "complete": {"active", "paused", "waiting_approval", "blocked"},
            "clear": {"active", "paused", "waiting_approval", "blocked", "completed"},
        }[operation]
        if current.get("status") not in allowed:
            raise GoalProfileCommandError("goal_action_unavailable")
    intent = {
        "conversation_id": conversation_id,
        "goal_id": current_id,
        "revision": current_revision,
        "operation": operation,
        "objective": objective,
        "max_turns": max_turns,
        "reason": reason,
    }
    validate()
    return {
        "schema_version": 1,
        **intent,
        "action_digest": admissions.keyed_digest(intent),
        "disclosures": (
            ["The current goal will be replaced."]
            if operation == "start" and current
            else []
        ),
    }


def review_goal_command(
    payload: dict[str, Any],
    *,
    validate: Callable[[], None],
    goal_owner: Any = goals,
) -> dict[str, Any]:
    return _goal_review(payload, validate=validate, goal_owner=goal_owner)


def _profile_fields(value: object, *, create: bool) -> dict[str, Any]:
    keys = {
        "slug",
        "display_name",
        "description",
        "when_to_use",
        "instructions",
        "capability",
        "allow_tools",
        "skills",
        "context_mode",
        "workspace_mode",
        "approval_mode",
        "enabled",
    }
    if not isinstance(value, dict) or set(value) != keys:
        raise GoalProfileCommandError("invalid_fields")
    slug = _text(value["slug"], 64, required=True)
    if not _PROFILE_SLUG.fullmatch(slug):
        raise GoalProfileCommandError("invalid_fields")
    instructions = value["instructions"]
    if instructions is not None:
        instructions = _text(instructions, 48 * 1024)
    if create and instructions is None:
        instructions = ""
    if (
        value["capability"] not in _CAPABILITIES
        or value["context_mode"] not in _CONTEXT_MODES
        or value["workspace_mode"] not in _WORKSPACE_MODES
        or value["approval_mode"] not in _APPROVAL_MODES
        or type(value["enabled"]) is not bool
    ):
        raise GoalProfileCommandError("invalid_fields")
    return {
        "slug": slug,
        "display_name": _text(value["display_name"], 160, required=True),
        "description": _text(value["description"], 2048),
        "when_to_use": _text(value["when_to_use"], 2048),
        "instructions": instructions,
        "capability": value["capability"],
        "allow_tools": _string_list(value["allow_tools"], count=100, length=256),
        "skills": _string_list(value["skills"], count=100, length=256),
        "context_mode": value["context_mode"],
        "workspace_mode": value["workspace_mode"],
        "approval_mode": value["approval_mode"],
        "enabled": value["enabled"],
    }


def _profile_review(
    payload: object,
    *,
    validate: Callable[[], None],
    profile_owner: Any,
) -> dict[str, Any]:
    validate()
    if not isinstance(payload, dict) or set(payload) != {
        "profile_id",
        "revision",
        "operation",
        "fields",
        "target_slug",
        "target_name",
    }:
        raise GoalProfileCommandError("invalid_command")
    operation = payload["operation"]
    if operation not in _PROFILE_OPERATIONS:
        raise GoalProfileCommandError("invalid_command")
    profile_id = payload["profile_id"]
    profile = None
    if profile_id is not None:
        profile_id = _text(profile_id, 256, required=True)
        profile = profile_owner.get_agent_profile(profile_id, enabled_only=False)
    revision = payload["revision"]
    if operation == "create":
        if profile_id is not None or revision != "none":
            raise GoalProfileCommandError("invalid_command")
        fields = _profile_fields(payload["fields"], create=True)
        if profile_owner.get_agent_profile(fields["slug"], enabled_only=False):
            raise GoalProfileCommandError("profile_slug_conflict")
        target_slug = fields["slug"]
        target_name = fields["display_name"]
    else:
        if profile is None:
            raise GoalProfileCommandError("not_found")
        current_revision = str(max(1, int(profile.get("revision") or 1)))
        if revision != current_revision:
            raise GoalProfileCommandError("profile_revision_conflict", current_revision)
        if str(profile.get("source") or "") == "builtin" and operation != "duplicate":
            raise GoalProfileCommandError("profile_read_only")
        fields = (
            _profile_fields(payload["fields"], create=False)
            if operation == "edit"
            else None
        )
        if operation == "edit":
            conflict = profile_owner.get_agent_profile(
                fields["slug"], enabled_only=False
            )
            if conflict is not None and str(conflict.get("id") or "") != str(
                profile.get("id") or ""
            ):
                raise GoalProfileCommandError("profile_slug_conflict")
        if operation == "duplicate":
            target_slug = _text(payload["target_slug"], 64, required=True)
            target_name = _text(payload["target_name"], 160, required=True)
            if not _PROFILE_SLUG.fullmatch(target_slug):
                raise GoalProfileCommandError("invalid_fields")
            if profile_owner.get_agent_profile(target_slug, enabled_only=False):
                raise GoalProfileCommandError("profile_slug_conflict")
        else:
            target_slug = None
            target_name = None
        if operation != "duplicate" and (
            payload["target_slug"] is not None or payload["target_name"] is not None
        ):
            raise GoalProfileCommandError("invalid_command")
        if operation not in {"edit", "duplicate"} and payload["fields"] is not None:
            raise GoalProfileCommandError("invalid_command")
    intent = {
        "profile_id": profile_id,
        "revision": revision,
        "operation": operation,
        "fields": fields,
        "target_slug": target_slug,
        "target_name": target_name,
    }
    changes: dict[str, Any]
    if operation == "edit":
        changes = {key: value for key, value in fields.items() if key != "instructions"}
        changes["instructions"] = (
            "replace" if fields["instructions"] is not None else "preserve"
        )
    elif operation == "create":
        changes = {key: value for key, value in fields.items() if key != "instructions"}
        changes["instructions"] = "set" if fields["instructions"] else "empty"
    elif operation == "duplicate":
        changes = {"slug": target_slug, "display_name": target_name}
    else:
        changes = {"enabled": operation == "enable"}
    validate()
    return {
        "schema_version": 1,
        **intent,
        "changes": changes,
        "action_digest": admissions.keyed_digest(intent),
        "disclosures": (
            ["Deleting this reusable profile cannot be undone."]
            if operation == "delete"
            else []
        ),
    }


def review_profile_command(
    payload: dict[str, Any],
    *,
    validate: Callable[[], None],
    profile_owner: Any = agent_profiles,
) -> dict[str, Any]:
    return _profile_review(payload, validate=validate, profile_owner=profile_owner)


def _profile_payload(
    existing: Mapping[str, Any] | None, fields: Mapping[str, Any]
) -> dict[str, Any]:
    payload = deepcopy(dict(existing or {}))
    tool_policy = _policy(payload, "tool_policy_json")
    tool_policy.update(
        capability=fields["capability"],
        allow_tools=list(fields["allow_tools"]),
    )
    skill_policy = _policy(payload, "skill_policy_json")
    skill_policy["skills_override"] = list(fields["skills"])
    context_policy = _policy(payload, "context_policy_json")
    context_policy["default_context_mode"] = fields["context_mode"]
    workspace_policy = _policy(payload, "workspace_policy_json")
    workspace_policy.update(
        workspace_mode_default=fields["workspace_mode"],
        write_lock_required=fields["capability"] in {"write_capable", "orchestrator"},
        worktree_allowed=fields["workspace_mode"] == "worktree",
    )
    approval_policy = _policy(payload, "approval_policy_json")
    approval_policy["mode"] = fields["approval_mode"]
    payload.update(
        slug=fields["slug"],
        display_name=fields["display_name"],
        description=fields["description"],
        when_to_use=fields["when_to_use"],
        tool_policy_json=tool_policy,
        skill_policy_json=skill_policy,
        context_policy_json=context_policy,
        workspace_policy_json=workspace_policy,
        approval_policy_json=approval_policy,
        enabled=fields["enabled"],
    )
    if fields["instructions"] is not None:
        payload["instructions"] = fields["instructions"]
    return payload


def _execute_goal(
    review: Mapping[str, Any],
    *,
    goal_owner: Any,
) -> dict[str, Any]:
    operation = review["operation"]
    conversation_id = review["conversation_id"]
    if operation == "start":
        return goal_owner.start_goal(
            conversation_id,
            review["objective"],
            max_turns=review["max_turns"],
            replace=True,
        )
    goal_id = review["goal_id"]
    revision = int(review["revision"])
    if operation == "resume" and (current := goal_owner.get_goal(goal_id)):
        if int(current.get("turns_used") or 0) >= int(current.get("max_turns") or 0):
            goal_owner.extend_goal_budget(goal_id)
    status, verdict, default_reason, finish = {
        "pause": ("paused", "paused", "Paused by user.", ""),
        "resume": ("active", "continue", "Goal resumed.", ""),
        "complete": ("completed", "complete", "Marked complete.", "completed"),
        "clear": ("cleared", "paused", "Cleared by user.", "stopped"),
    }[operation]
    updated = goal_owner.set_goal_status(
        goal_id,
        status,
        reason=review["reason"] or default_reason,
        verdict=verdict,
        finish_run_status=finish,
        expected_revision=revision,
    )
    if updated is None:
        raise GoalProfileCommandError("goal_revision_conflict")
    return updated


def _execute_profile(
    review: Mapping[str, Any],
    *,
    command_id: str,
    owner_id: str,
    key: str,
    profile_owner: Any,
) -> dict[str, Any] | None:
    if profile_owner is agent_profiles:
        return _execute_profile_cas(
            review,
            command_id=command_id,
            owner_id=owner_id,
            key=key,
        )
    operation = review["operation"]
    if operation == "create":
        stable_id = uuid5(UUID(command_id), "agent-profile").hex[:12]
        payload = _profile_payload(None, review["fields"])
        payload.update(id=stable_id, scope="user", source="user_created")
        return profile_owner.save_agent_profile(payload)
    profile = profile_owner.get_agent_profile(review["profile_id"], enabled_only=False)
    if profile is None:
        raise GoalProfileCommandError("not_found")
    if str(max(1, int(profile.get("revision") or 1))) != review["revision"]:
        raise GoalProfileCommandError(
            "profile_revision_conflict", str(profile.get("revision") or 1)
        )
    if operation == "edit":
        return profile_owner.save_agent_profile(
            _profile_payload(profile, review["fields"])
        )
    if operation == "duplicate":
        return profile_owner.duplicate_agent_profile(
            profile["id"],
            {"slug": review["target_slug"], "display_name": review["target_name"]},
        )
    if operation == "delete":
        if not profile_owner.delete_agent_profile(profile["id"]):
            raise GoalProfileCommandError("profile_delete_unconfirmed")
        return None
    payload = deepcopy(profile)
    payload["enabled"] = operation == "enable"
    return profile_owner.save_agent_profile(payload)


def _execute_profile_cas(
    review: Mapping[str, Any],
    *,
    command_id: str,
    owner_id: str,
    key: str,
) -> dict[str, Any] | None:
    """Mutate the canonical profile table with a revision predicate.

    The profile row and a recovery proof share the tasks database transaction,
    so a lost response can be recovered without applying the edit twice.
    """

    agent_profiles.ensure_agent_profiles_schema()
    from row_bot.tasks import _get_conn

    operation = review["operation"]
    connection = _get_conn()
    try:
        connection.execute("BEGIN IMMEDIATE")
        profile: dict[str, Any] | None = None
        if review["profile_id"] is not None:
            row = connection.execute(
                "SELECT * FROM agent_profiles WHERE id = ? OR slug = ?",
                (review["profile_id"], review["profile_id"]),
            ).fetchone()
            profile = agent_profiles._profile_from_row(row) if row else None
            if profile is None and operation == "duplicate":
                profile = agent_profiles.get_agent_profile(
                    review["profile_id"], enabled_only=False
                )
        if operation == "create":
            slug = review["fields"]["slug"]
            if connection.execute(
                "SELECT 1 FROM agent_profiles WHERE slug = ?", (slug,)
            ).fetchone():
                raise GoalProfileCommandError("profile_slug_conflict")
            stable_id = uuid5(UUID(command_id), "agent-profile").hex[:12]
            payload = _profile_payload(None, review["fields"])
            now = agent_profiles._now()
            payload.update(
                id=stable_id,
                scope="user",
                source="user_created",
                revision=1,
                created_at=now,
                updated_at=now,
            )
            normalized = agent_profiles._profile_payload_for_save(payload)
            _insert_profile(connection, normalized)
            changed = normalized
        else:
            if profile is None:
                raise GoalProfileCommandError("not_found")
            current_revision = str(max(1, int(profile.get("revision") or 1)))
            if current_revision != review["revision"]:
                raise GoalProfileCommandError(
                    "profile_revision_conflict", current_revision
                )
            if operation == "delete":
                changed_rows = connection.execute(
                    "DELETE FROM agent_profiles WHERE id = ? AND revision = ?",
                    (profile["id"], int(review["revision"])),
                ).rowcount
                if changed_rows != 1:
                    raise GoalProfileCommandError("profile_revision_conflict")
                changed = None
            else:
                if operation == "edit":
                    payload = _profile_payload(profile, review["fields"])
                elif operation == "duplicate":
                    if connection.execute(
                        "SELECT 1 FROM agent_profiles WHERE slug = ?",
                        (review["target_slug"],),
                    ).fetchone():
                        raise GoalProfileCommandError("profile_slug_conflict")
                    payload = deepcopy(profile)
                    payload.update(
                        id=uuid5(UUID(command_id), "agent-profile-copy").hex[:12],
                        slug=review["target_slug"],
                        display_name=review["target_name"],
                        scope="user",
                        source="user_created",
                        revision=1,
                        usage_count=0,
                        last_used_at="",
                        created_from_run_id="",
                        created_from_workflow_id="",
                        provenance_json={
                            **_policy(profile, "provenance_json"),
                            "duplicated_from_profile_id": profile["id"],
                            "duplicated_from_profile_slug": profile["slug"],
                        },
                    )
                    now = agent_profiles._now()
                    payload.update(created_at=now, updated_at=now)
                    normalized = agent_profiles._profile_payload_for_save(payload)
                    _insert_profile(connection, normalized)
                    changed = normalized
                    payload = None
                else:
                    payload = deepcopy(profile)
                    payload["enabled"] = operation == "enable"
                if operation != "duplicate":
                    payload["revision"] = int(review["revision"]) + 1
                    payload["updated_at"] = agent_profiles._now()
                    normalized = agent_profiles._profile_payload_for_save(payload)
                    changed_rows = _update_profile(
                        connection,
                        normalized,
                        expected_revision=int(review["revision"]),
                    )
                    if changed_rows != 1:
                        raise GoalProfileCommandError("profile_revision_conflict")
                    changed = normalized
        progress = {
            "command_id": command_id,
            "status": "partial",
            "code": "profile_saved_read_unconfirmed",
            "profile_saved": True,
            "profile_id": (changed or profile or {}).get("id"),
            "operation": operation,
        }
        recorded = connection.execute(
            "UPDATE client_commands SET result_json = ? "
            "WHERE owner_id = ? AND key = ? AND status = 'admitting'",
            (json.dumps(progress, separators=(",", ":")), owner_id, key),
        ).rowcount
        if recorded != 1:
            raise GoalProfileCommandError("operation_uncertain")
        connection.commit()
        return deepcopy(changed) if changed is not None else None
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def _profile_values(profile: Mapping[str, Any]) -> list[Any]:
    values: list[Any] = []
    for field in agent_profiles._PROFILE_COLUMNS:
        value = profile.get(field)
        if field in agent_profiles._JSON_FIELDS:
            values.append(agent_profiles._json_text(value, field=field))
        elif field == "enabled":
            values.append(1 if value else 0)
        else:
            values.append(value)
    return values


def _insert_profile(connection: sqlite3.Connection, profile: Mapping[str, Any]) -> None:
    fields = list(agent_profiles._PROFILE_COLUMNS)
    connection.execute(
        f"INSERT INTO agent_profiles ({', '.join(fields)}) "
        f"VALUES ({', '.join('?' for _ in fields)})",
        _profile_values(profile),
    )


def _update_profile(
    connection: sqlite3.Connection,
    profile: Mapping[str, Any],
    *,
    expected_revision: int,
) -> int:
    fields = [field for field in agent_profiles._PROFILE_COLUMNS if field != "id"]
    values = _profile_values(profile)
    by_field = dict(zip(agent_profiles._PROFILE_COLUMNS, values, strict=True))
    return connection.execute(
        f"UPDATE agent_profiles SET {', '.join(f'{field} = ?' for field in fields)} "
        "WHERE id = ? AND revision = ?",
        ([by_field[field] for field in fields] + [profile["id"], expected_revision]),
    ).rowcount


def _public_receipt(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if not key.startswith("_")}


def execute_goal_command(
    *,
    owner_id: str,
    key: str,
    command: dict[str, Any],
    validate: Callable[[], None],
    validate_review: Callable[[dict[str, Any]], None],
    goal_owner: Any = goals,
) -> dict[str, Any]:
    validate()
    if not isinstance(command, dict) or set(command) != {
        "command_id",
        "type",
        "payload",
    }:
        raise GoalProfileCommandError("invalid_command")
    if command["type"] != "goal.control":
        raise GoalProfileCommandError("invalid_command")
    _uuid(command["command_id"])
    raw_payload = command.get("payload")
    if not isinstance(raw_payload, dict):
        raise GoalProfileCommandError("invalid_command")
    conversation_id = _text(raw_payload.get("conversation_id"), 256, required=True)
    target = f"settings:goal:{conversation_id}"
    initial = {
        "command_id": command["command_id"],
        "status": "partial",
        "code": "goal_operation_unconfirmed",
    }
    try:
        replay = admissions.claim_command(
            owner_id,
            key,
            command,
            target,
            exclusive_target=True,
            initial_result=initial,
        )
    except admissions.AdmissionError as error:
        if str(error) != "operation_uncertain":
            raise GoalProfileCommandError(str(error), error.current_revision) from None
        retained = admissions.read_command_receipt(owner_id, command["command_id"])
        return _public_receipt(retained or initial)
    if replay is not None:
        validate()
        return _public_receipt(replay)
    try:
        review = _goal_review(
            command["payload"], validate=validate, goal_owner=goal_owner
        )
        validate_review(review)
        current = _goal_review(
            command["payload"], validate=validate, goal_owner=goal_owner
        )
        if current != review:
            raise GoalProfileCommandError("goal_review_stale")
        validate_review(current)
        changed = _execute_goal(review, goal_owner=goal_owner)
        validate()
        result = {
            "command_id": command["command_id"],
            "status": "completed",
            "operation": review["operation"],
            "goal": _goal_public(changed, review["conversation_id"]),
            "code": None,
        }
        return _public_receipt(admissions.complete_command(owner_id, key, result))
    except GoalProfileCommandError as error:
        admissions.reject_command(owner_id, key, error.code, error.current_revision)
        raise
    except Exception:
        retained = admissions.read_command_receipt(owner_id, command["command_id"])
        return _public_receipt(retained or initial)


def execute_profile_command(
    *,
    owner_id: str,
    key: str,
    command: dict[str, Any],
    validate: Callable[[], None],
    validate_review: Callable[[dict[str, Any]], None],
    profile_owner: Any = agent_profiles,
) -> dict[str, Any]:
    validate()
    if not isinstance(command, dict) or set(command) != {
        "command_id",
        "type",
        "payload",
    }:
        raise GoalProfileCommandError("invalid_command")
    if command["type"] != "profile.mutate":
        raise GoalProfileCommandError("invalid_command")
    _uuid(command["command_id"])
    raw_payload = command.get("payload")
    if not isinstance(raw_payload, dict):
        raise GoalProfileCommandError("invalid_command")
    target_ref = raw_payload.get("profile_id") or raw_payload.get("target_slug")
    if target_ref is None and isinstance(raw_payload.get("fields"), dict):
        target_ref = raw_payload["fields"].get("slug")
    target_ref = _text(target_ref, 256, required=True)
    target = f"settings:profile:{target_ref}"
    initial = {
        "command_id": command["command_id"],
        "status": "partial",
        "code": "profile_operation_unconfirmed",
    }
    try:
        replay = admissions.claim_command(
            owner_id,
            key,
            command,
            target,
            exclusive_target=True,
            initial_result=initial,
        )
    except admissions.AdmissionError as error:
        if str(error) != "operation_uncertain":
            raise GoalProfileCommandError(str(error), error.current_revision) from None
        retained = admissions.read_command_receipt(owner_id, command["command_id"])
        return _public_receipt(retained or initial)
    if replay is not None:
        validate()
        return _public_receipt(replay)
    try:
        review = _profile_review(
            command["payload"], validate=validate, profile_owner=profile_owner
        )
        validate_review(review)
        current = _profile_review(
            command["payload"], validate=validate, profile_owner=profile_owner
        )
        if current != review:
            raise GoalProfileCommandError("profile_review_stale")
        validate_review(current)
        changed = _execute_profile(
            review,
            command_id=command["command_id"],
            owner_id=owner_id,
            key=key,
            profile_owner=profile_owner,
        )
        validate()
        result = {
            "command_id": command["command_id"],
            "status": "completed",
            "operation": review["operation"],
            "profile": _profile_public(changed, detail=True) if changed else None,
            "profile_id": (changed or {}).get("id")
            if changed
            else review["profile_id"],
            "code": None,
        }
        return _public_receipt(admissions.complete_command(owner_id, key, result))
    except GoalProfileCommandError as error:
        admissions.reject_command(owner_id, key, error.code, error.current_revision)
        raise
    except agent_profiles.AgentProfileError as error:
        admissions.reject_command(owner_id, key, "profile_mutation_rejected")
        raise GoalProfileCommandError("profile_mutation_rejected") from error
    except Exception:
        retained = admissions.read_command_receipt(owner_id, command["command_id"])
        return _public_receipt(retained or initial)


def read_goal_receipt(
    conversation_id: str,
    *,
    owner_id: str,
    command_id: str,
    validate: Callable[[], None],
) -> dict[str, Any] | None:
    return _read_receipt(
        owner_id=owner_id,
        command_id=command_id,
        target=f"settings:goal:{_text(conversation_id, 256, required=True)}",
        command_type="goal.control",
        validate=validate,
    )


def read_profile_receipt(
    profile_ref: str,
    *,
    owner_id: str,
    command_id: str,
    validate: Callable[[], None],
) -> dict[str, Any] | None:
    return _read_receipt(
        owner_id=owner_id,
        command_id=command_id,
        target=f"settings:profile:{_text(profile_ref, 256, required=True)}",
        command_type="profile.mutate",
        validate=validate,
    )


def _read_receipt(
    *,
    owner_id: str,
    command_id: str,
    target: str,
    command_type: str,
    validate: Callable[[], None],
) -> dict[str, Any] | None:
    validate()
    _uuid(command_id)
    metadata = admissions.read_command_metadata(owner_id, command_id)
    if metadata is None:
        validate()
        return None
    if metadata.get("target") != target or metadata.get("type") != command_type:
        raise GoalProfileCommandError("receipt_unavailable")
    value = admissions.read_command_receipt(owner_id, command_id)
    validate()
    return _public_receipt(value) if value else None
