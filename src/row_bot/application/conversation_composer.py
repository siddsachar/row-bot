"""Bounded canonical conversation-composer reads and skill mutations.

The transport owns authentication, request idempotency, and durable receipts.
This module owns deterministic projection and a revision-checked invocation of
the existing thread/skill domain owners.
"""
from __future__ import annotations

from collections.abc import Callable
import hashlib
import importlib
import json
import threading

from row_bot import skills, skills_activation, slash_commands
from row_bot.application.client_platform import ClientPlatformError

_LOCK = threading.RLock()
_MAX_DRAFT = 16_384
_MAX_COMMAND_QUERY = 256
_MAX_COMMANDS = 256
_MAX_ACTIVE = 64
_MAX_DESCRIPTION = 180
_ACTIONS = {"activate", "remove", "dismiss", "reset"}


def _noop() -> None:
    return None


def _thread_domain():
    """Resolve the active storage owner after supported runtime reloads."""
    return importlib.import_module("row_bot.threads")


def _error(code: str) -> ClientPlatformError:
    return ClientPlatformError(code)


def _text(value: object, maximum: int, *, required: bool = False) -> str:
    if not isinstance(value, str) or len(value) > maximum or "\0" in value:
        raise _error("invalid_composer_request")
    result = value.strip()
    if required and not result:
        raise _error("invalid_composer_request")
    return result


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _normalize_context(value: dict) -> dict:
    override = value.get("skills_override")
    if isinstance(override, str):
        try:
            override = json.loads(override) if override else None
        except json.JSONDecodeError:
            override = None
    if not isinstance(override, list) or any(
        not isinstance(item, str) for item in override
    ):
        override = None
    elif override is not None:
        override = list(dict.fromkeys(item.strip() for item in override if item.strip()))
    return {
        "skills_override": override,
        "agent_profile_id": str(value.get("agent_profile_id") or ""),
        "agent_profile_slug": str(value.get("agent_profile_slug") or ""),
        "client_revision": int(value.get("client_revision") or 0),
    }


def _manual_library() -> tuple[dict, list, dict[str, dict]]:
    """Return availability metadata, enabled manual objects, and public rows."""

    try:
        snapshot = skills.read_client_skills()
    except (OSError, UnicodeError, ValueError):
        return {"availability": "unavailable", "revision": "unavailable"}, [], {}
    enabled_manual = []
    records: dict[str, dict] = {}
    for name, item in snapshot["items"].items():
        skill = item["skill"]
        if skills.is_tool_guide(skill) or not snapshot["enabled"].get(name, False):
            continue
        enabled_manual.append(skill)
        records[name] = {
            "id": name,
            "display_name": str(skill.display_name)[:128],
            "icon": str(skill.icon or "✨")[:32],
            "description": str(skill.description or "")[:_MAX_DESCRIPTION],
            "library_source": "manual",
        }
    enabled_manual.sort(key=lambda item: (item.display_name.casefold(), item.name))
    return (
        {"availability": "available", "revision": snapshot["revision"]},
        enabled_manual,
        records,
    )


def _passive_plugin_records() -> tuple[list, dict[str, dict]]:
    """Use only the already-loaded plugin registry and enablement cache."""

    try:
        from row_bot.plugins import state as plugin_state

        if plugin_state.get_cached_plugin_enablement() is None:
            return [], {}
        from row_bot.skill_discovery import collect_enabled_skill_records

        plugin_records = [
            record
            for record in collect_enabled_skill_records(load_manual=False)
            if record.canonical_id.startswith("plugin:")
        ]
    except (OSError, UnicodeError, ValueError):
        return [], {}
    public = {
        record.canonical_id: {
            "id": record.canonical_id,
            "display_name": record.display_name[:128],
            "icon": str(record.icon or "🔌")[:32],
            "description": record.description[:_MAX_DESCRIPTION],
            "library_source": "plugin",
        }
        for record in plugin_records
    }
    return plugin_records, public


def _selected_profile(context: dict) -> dict:
    reference = context["agent_profile_id"] or context["agent_profile_slug"]
    if not reference:
        return {}
    try:
        from row_bot.agent_profiles import get_agent_profile

        profile = get_agent_profile(reference, enabled_only=False)
    except (OSError, ValueError):
        return {}
    if not profile or not profile.get("enabled", True):
        return {}
    return dict(profile)


def _active_skill_rows(
    *,
    records: dict[str, dict],
    activation: dict,
    context: dict,
    profile: dict,
) -> list[dict]:
    disabled = set(activation["disabled"])
    default_seeded = set(activation["default_seeded"])
    candidates: list[tuple[str, str, bool]] = []
    if profile:
        policy = profile.get("skill_policy_json") or {}
        if isinstance(policy, dict):
            candidates.extend(
                (str(skill_id), "thread", False)
                for skill_id in policy.get("skills_override", [])
            )
    else:
        override = context["skills_override"]
        if override is not None:
            candidates.extend((skill_id, "thread", True) for skill_id in override)
        candidates.extend(
            (
                skill_id,
                "default" if skill_id in default_seeded else "pinned",
                True,
            )
            for skill_id in activation["pinned"]
        )
        candidates.extend((skill_id, "auto", True) for skill_id in activation["auto_loaded"])
    rows = []
    seen: set[str] = set()
    for skill_id, source, removable in candidates:
        if skill_id in seen or skill_id in disabled or skill_id not in records:
            continue
        seen.add(skill_id)
        rows.append({**records[skill_id], "source": source, "removable": removable})
        if len(rows) >= _MAX_ACTIVE:
            break
    return rows


def _command_rows(manual_skills: list, query: str, limit: int) -> tuple[list[dict], int]:
    specs = slash_commands.get_command_specs(manual_skills=manual_skills)
    all_filtered = slash_commands.filter_command_specs(specs, query, limit=len(specs))
    filtered = all_filtered[:min(limit, _MAX_COMMANDS)]
    return [
        {
            "id": spec.id,
            "token": spec.slash,
            "aliases": list(spec.aliases),
            "label": spec.title,
            "description": spec.description,
            "icon": spec.icon,
            "category": spec.category,
            "argument_mode": spec.argument_behavior,
            "argument_hint": slash_commands.argument_hint(spec),
            "handler_kind": spec.handler_key,
            "skill_id": spec.skill_name or None,
        }
        for spec in filtered
    ], len(all_filtered)


def _read_snapshot(
    conversation_id: str,
    *,
    draft: str,
    command_query: str,
    command_limit: int,
    context: dict | None = None,
) -> dict:
    if context is None:
        try:
            context = _thread_domain().get_thread_composer_context(conversation_id)
        except ValueError:
            raise _error("conversation_missing") from None
    context = _normalize_context(context)
    library, manual_skills, records = _manual_library()
    plugin_records, plugin_public = _passive_plugin_records()
    records.update(plugin_public)
    activation = skills_activation.get_thread_activation_state(conversation_id)
    profile = _selected_profile(context)
    active = _active_skill_rows(
        records=records,
        activation=activation,
        context=context,
        profile=profile,
    )
    active_ids = [item["id"] for item in active]
    suggestions = skills_activation.suggest_skills_from_snapshot(
        conversation_id,
        draft,
        manual_skills,
        active_skill_ids=active_ids,
        extra_excluded=(
            (profile.get("skill_policy_json") or {}).get("skills_override", [])
            if profile else context["skills_override"] or []
        ),
        limit=3,
    )
    commands, command_total = _command_rows(manual_skills, command_query, command_limit)
    profile_policy = profile.get("skill_policy_json") or {} if profile else {}
    revision = _digest({
        "activation": activation,
        "override": context["skills_override"],
        "library": library["revision"],
        "plugins": [
            {
                "id": record.canonical_id,
                "display_name": record.display_name,
                "description": record.description,
                "activation": dict(record.activation),
            }
            for record in plugin_records
        ],
        "profile": {
            "id": profile.get("id", "") if profile else "",
            "enabled": profile.get("enabled", True) if profile else False,
            "skill_policy": profile_policy,
        },
    })
    return {
        "schema_version": 1,
        "conversation_id": conversation_id,
        "conversation_revision": str(context["client_revision"]),
        "composer_revision": revision,
        "library": library,
        "smart_skills_off": activation["smart_off"],
        "active_skills": active,
        "suggestions": [
            {
                "id": f"skill:{item.name}",
                "skill_id": item.name,
                "display_name": item.display_name[:128],
                "icon": str(item.icon or "✨")[:32],
                "description": str(item.description or "")[:_MAX_DESCRIPTION],
                "reason": item.reason[:256],
            }
            for item in suggestions
        ],
        "commands": commands,
        "command_total": command_total,
        "commands_truncated": len(commands) < command_total,
    }


def read_conversation_composer(
    conversation_id: str,
    *,
    draft: str = "",
    command_query: str = "",
    command_limit: int = 128,
    context: dict | None = None,
    validate: Callable[[], None] = _noop,
) -> dict:
    """Read one passive, bounded composer snapshot."""

    conversation_id = _text(conversation_id, 256, required=True)
    draft = _text(draft, _MAX_DRAFT)
    command_query = _text(command_query, _MAX_COMMAND_QUERY)
    if type(command_limit) is not int or not 1 <= command_limit <= _MAX_COMMANDS:
        raise _error("invalid_composer_request")
    validate()
    result = _read_snapshot(
        conversation_id,
        draft=draft,
        command_query=command_query,
        command_limit=command_limit,
        context=context,
    )
    validate()
    return result


def apply_conversation_skill_command(
    conversation_id: str,
    action: str,
    *,
    composer_revision: str,
    skill_id: str = "",
    draft: str = "",
    validate: Callable[[], None] = _noop,
) -> dict:
    """Apply one conversation-local skill command against a composer revision."""

    conversation_id = _text(conversation_id, 256, required=True)
    action = _text(action, 32, required=True)
    composer_revision = _text(composer_revision, 128, required=True)
    skill_id = _text(skill_id, 384)
    draft = _text(draft, _MAX_DRAFT)
    if action not in _ACTIONS or (action != "reset" and not skill_id):
        raise _error("invalid_composer_request")
    validate()
    with _LOCK, skills_activation.activation_state_lock():
        current = _read_snapshot(
            conversation_id,
            draft=draft,
            command_query="",
            command_limit=128,
        )
        if current["composer_revision"] != composer_revision:
            raise ClientPlatformError("skill_revision_conflict", current["composer_revision"])
        if current["library"]["availability"] != "available":
            raise _error("skills_unavailable")
        manual_snapshot = skills.read_client_skills()
        if manual_snapshot["revision"] != current["library"]["revision"]:
            raise ClientPlatformError("skill_revision_conflict", current["composer_revision"])
        manual_ids = {
            name
            for name, item in manual_snapshot["items"].items()
            if manual_snapshot["enabled"].get(name, False)
            and not skills.is_tool_guide(item["skill"])
        }
        plugin_records, _plugin_public = _passive_plugin_records()
        plugin_ids = {record.canonical_id for record in plugin_records}
        changed = False
        if action == "activate":
            if skill_id not in manual_ids | plugin_ids:
                raise _error("skill_missing")
            already_active = any(
                item["id"] == skill_id for item in current["active_skills"]
            )
            if already_active:
                changed = False
            elif skill_id in manual_ids:
                skills_activation.pin_skill(conversation_id, skill_id)
                changed = True
            else:
                changed, _evicted = skills_activation.load_auto_skill(
                    conversation_id,
                    skill_id,
                    available_ids=plugin_ids,
                )
        elif action == "remove":
            active = next(
                (item for item in current["active_skills"] if item["id"] == skill_id),
                None,
            )
            if active is None:
                raise _error("skill_not_active")
            if not active["removable"]:
                raise _error("skill_not_removable")
            if active["source"] == "auto":
                changed = skills_activation.remove_auto_loaded_skill(conversation_id, skill_id)
            else:
                skills_activation.disable_skill(conversation_id, skill_id)
                changed = True
        elif action == "dismiss":
            if not any(item["skill_id"] == skill_id for item in current["suggestions"]):
                raise _error("skill_missing")
            skills_activation.dismiss_suggestion(conversation_id, skill_id)
            changed = True
        else:
            defaults = [
                name
                for name in manual_snapshot["pinned"]
                if name in manual_ids
            ]
            before = skills_activation.get_thread_activation_state(conversation_id)
            skills_activation.reset_thread_to_defaults(conversation_id, defaults)
            after = skills_activation.get_thread_activation_state(conversation_id)
            changed = before != after
        if changed:
            _thread_domain().bump_thread_client_revision(conversation_id)
        validate()
        result = _read_snapshot(
            conversation_id,
            draft=draft,
            command_query="",
            command_limit=128,
        )
    return {
        "schema_version": 1,
        "action": action,
        "changed": changed,
        "conversation_revision": result["conversation_revision"],
        "snapshot": result,
    }
