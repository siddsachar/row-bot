"""Phase 4 goal and profile settings have one reviewed mutation owner."""

from __future__ import annotations

import importlib
import json
from uuid import UUID

import pytest

from row_bot import agent_profiles, goals, tasks
from row_bot.application import client_goal_profile_commands as commands


pytestmark = pytest.mark.subsystem


def _valid() -> None:
    return None


@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch):
    current_tasks = importlib.import_module("row_bot.tasks")
    for owner in {tasks, current_tasks}:
        monkeypatch.setattr(owner, "_DB_PATH", tmp_path / "tasks.db")
        monkeypatch.setattr(owner, "_SCHEMA_READY_PATH", None)
    monkeypatch.setattr(agent_profiles, "_SCHEMA_READY", False)


def _profile_fields(**overrides):
    value = {
        "slug": "focused_writer",
        "display_name": "Focused Writer",
        "description": "Writes within a reviewed workspace.",
        "when_to_use": "Use for focused edits.",
        "instructions": "Keep private workspace details private.",
        "capability": "write_capable",
        "allow_tools": ["read_file", "write_file"],
        "skills": ["local-style"],
        "context_mode": "focused",
        "workspace_mode": "single_writer",
        "approval_mode": "inherit",
        "enabled": True,
    }
    value.update(overrides)
    return value


def _profile_request(operation: str, **overrides):
    value = {
        "profile_id": None,
        "revision": "none",
        "operation": operation,
        "fields": _profile_fields() if operation == "create" else None,
        "target_slug": None,
        "target_name": None,
    }
    value.update(overrides)
    return value


def _goal_request(conversation_id: str, operation: str, **overrides):
    current = goals.get_current_goal(conversation_id, include_terminal=True)
    value = {
        "conversation_id": conversation_id,
        "goal_id": current["id"] if current else None,
        "revision": str(current["revision"]) if current else "none",
        "operation": operation,
        "objective": "Finish the isolated migration" if operation == "start" else None,
        "max_turns": 12 if operation == "start" else None,
        "reason": None if operation == "start" else "User requested this change.",
    }
    value.update(overrides)
    return value


def _execute_goal(request: dict, *, command_id: int = 1, validate=_valid):
    review = commands.review_goal_command(request, validate=_valid)
    command = {
        "command_id": str(UUID(int=command_id)),
        "type": "goal.control",
        "payload": request,
    }
    return commands.execute_goal_command(
        owner_id="owner",
        key=command["command_id"],
        command=command,
        validate=validate,
        validate_review=lambda current: (
            current == review or pytest.fail("review changed")
        ),
    )


def _execute_profile(request: dict, *, command_id: int = 100, validate=_valid):
    review = commands.review_profile_command(request, validate=_valid)
    command = {
        "command_id": str(UUID(int=command_id)),
        "type": "profile.mutate",
        "payload": request,
    }
    return commands.execute_profile_command(
        owner_id="owner",
        key=command["command_id"],
        command=command,
        validate=validate,
        validate_review=lambda current: (
            current == review or pytest.fail("review changed")
        ),
    )


def test_goal_reads_are_conversation_scoped_bounded_and_redacted():
    first = goals.start_goal(
        "conversation-a", "Use D:/private/work and sk-private123456"
    )
    goals.start_goal("conversation-b", "Never enumerate this conversation")
    page = commands.read_goals("conversation-a", validate=_valid)
    assert page["scope"] == "conversation"
    assert page["current_goal_id"] == first["id"]
    assert page["current_revision"] == str(first["revision"])
    assert [item["conversation_id"] for item in page["items"]] == ["conversation-a"]
    public = json.dumps(page)
    assert "D:/private/work" not in public
    assert "sk-private123456" not in public
    assert "conversation-b" not in public


def test_profile_reads_are_global_bounded_and_never_return_instruction_bodies():
    saved = agent_profiles.save_agent_profile(
        _profile_fields(
            id="private-profile",
            instructions="Private C:/Users/alice/work sk-private123456 instruction",
        )
    )
    detail = commands.read_profile(saved["id"], validate=_valid)
    assert detail["profile"]["surface_scope"] == "global"
    assert detail["profile"]["instruction_edit"] == {
        "mode": "replace_only",
        "stored": True,
    }
    public = json.dumps(detail)
    assert "Private" not in public
    assert "C:/Users/alice/work" not in public
    assert "sk-private123456" not in public
    assert detail["profile"]["instructions_preview"] == ""
    assert detail["profile"]["instructions_truncated"] is True


def test_goal_review_is_closed_and_detects_revision_conflict():
    request = _goal_request("conversation-a", "start")
    with pytest.raises(commands.GoalProfileCommandError, match="invalid_command"):
        commands.review_goal_command({**request, "surprise": True}, validate=_valid)
    goals.start_goal("conversation-a", "Already running")
    with pytest.raises(
        commands.GoalProfileCommandError, match="goal_revision_conflict"
    ):
        commands.review_goal_command(request, validate=_valid)


def test_goal_commands_start_pause_resume_complete_and_replay_once(monkeypatch):
    start_request = _goal_request("conversation-a", "start")
    start = _execute_goal(start_request)
    assert start["status"] == "completed"
    assert start["goal"]["status"] == "active"

    calls = 0
    original = goals.set_goal_status

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(goals, "set_goal_status", counted)
    pause_request = _goal_request("conversation-a", "pause")
    pause = _execute_goal(pause_request, command_id=2)
    command_id = str(UUID(int=2))
    replay = commands.execute_goal_command(
        owner_id="owner",
        key=command_id,
        command={
            "command_id": command_id,
            "type": "goal.control",
            "payload": pause_request,
        },
        validate=_valid,
        validate_review=lambda _current: pytest.fail("replay was re-reviewed"),
    )
    assert replay == pause
    assert calls == 1
    assert goals.get_goal(start["goal"]["id"])["status"] == "paused"

    resumed = _execute_goal(_goal_request("conversation-a", "resume"), command_id=3)
    assert resumed["goal"]["status"] == "active"
    completed = _execute_goal(_goal_request("conversation-a", "complete"), command_id=4)
    assert completed["goal"]["status"] == "completed"


def test_goal_revision_cas_rejects_a_change_after_review(monkeypatch):
    started = goals.start_goal("conversation-a", "Initial")
    request = _goal_request("conversation-a", "pause")
    review = commands.review_goal_command(request, validate=_valid)
    original = commands._execute_goal

    def race(current, *, goal_owner):
        goals.set_goal_status(
            started["id"],
            "paused",
            reason="Concurrent owner",
            expected_revision=started["revision"],
        )
        return original(current, goal_owner=goal_owner)

    monkeypatch.setattr(commands, "_execute_goal", race)
    command_id = str(UUID(int=8))
    with pytest.raises(
        commands.GoalProfileCommandError, match="goal_revision_conflict"
    ):
        commands.execute_goal_command(
            owner_id="owner",
            key=command_id,
            command={
                "command_id": command_id,
                "type": "goal.control",
                "payload": request,
            },
            validate=_valid,
            validate_review=lambda current: (
                current == review or pytest.fail("review changed")
            ),
        )


def test_profile_create_edit_preserves_instruction_and_uses_revision_cas():
    created = _execute_profile(_profile_request("create"))
    assert created["status"] == "completed"
    profile = agent_profiles.get_agent_profile(created["profile_id"])
    assert profile["instructions"] == "Keep private workspace details private."

    fields = _profile_fields(
        display_name="Focused Editor",
        instructions=None,
        capability="orchestrator",
    )
    edited = _execute_profile(
        _profile_request(
            "edit",
            profile_id=profile["id"],
            revision=str(profile["revision"]),
            fields=fields,
        ),
        command_id=101,
    )
    saved = agent_profiles.get_agent_profile(profile["id"])
    assert edited["profile"]["display_name"] == "Focused Editor"
    assert saved["instructions"] == "Keep private workspace details private."
    assert saved["revision"] == profile["revision"] + 1


def test_profile_review_blocks_builtin_writes_and_slug_collisions():
    builtin = agent_profiles.list_agent_profiles(enabled_only=False)[0]
    with pytest.raises(commands.GoalProfileCommandError, match="profile_read_only"):
        commands.review_profile_command(
            _profile_request(
                "disable",
                profile_id=builtin["id"],
                revision=str(builtin["revision"]),
            ),
            validate=_valid,
        )
    first = agent_profiles.save_agent_profile(
        _profile_fields(id="first", slug="first", display_name="First")
    )
    agent_profiles.save_agent_profile(
        _profile_fields(id="second", slug="second", display_name="Second")
    )
    with pytest.raises(commands.GoalProfileCommandError, match="profile_slug_conflict"):
        commands.review_profile_command(
            _profile_request(
                "edit",
                profile_id=first["id"],
                revision=str(first["revision"]),
                fields=_profile_fields(slug="second"),
            ),
            validate=_valid,
        )


def test_profile_duplicate_disable_delete_and_replay():
    source = agent_profiles.save_agent_profile(
        _profile_fields(id="source", slug="source", display_name="Source")
    )
    duplicate = _execute_profile(
        _profile_request(
            "duplicate",
            profile_id=source["id"],
            revision=str(source["revision"]),
            target_slug="source_copy",
            target_name="Source Copy",
        ),
        command_id=110,
    )
    copied = agent_profiles.get_agent_profile(duplicate["profile_id"])
    assert copied["slug"] == "source_copy"
    disabled_request = _profile_request(
        "disable",
        profile_id=copied["id"],
        revision=str(copied["revision"]),
    )
    disabled = _execute_profile(disabled_request, command_id=111)
    assert disabled["profile"]["enabled"] is False
    command_id = str(UUID(int=111))
    assert (
        commands.execute_profile_command(
            owner_id="owner",
            key=command_id,
            command={
                "command_id": command_id,
                "type": "profile.mutate",
                "payload": disabled_request,
            },
            validate=_valid,
            validate_review=lambda _current: pytest.fail("replay was re-reviewed"),
        )
        == disabled
    )

    current = agent_profiles.get_agent_profile(copied["id"])
    deleted = _execute_profile(
        _profile_request(
            "delete",
            profile_id=current["id"],
            revision=str(current["revision"]),
        ),
        command_id=112,
    )
    assert deleted["profile"] is None
    assert agent_profiles.get_agent_profile(copied["id"]) is None


def test_profile_auth_revocation_after_saved_effect_retains_partial_receipt():
    checks = 0

    def revoked_after_write():
        nonlocal checks
        checks += 1
        if checks >= 6:
            raise RuntimeError("authentication revoked")

    request = _profile_request("create")
    result = _execute_profile(request, command_id=120, validate=revoked_after_write)
    assert result["status"] == "partial"
    assert result["code"] == "profile_saved_read_unconfirmed"
    profile = agent_profiles.get_agent_profile("focused_writer")
    assert profile is not None
    command_id = str(UUID(int=120))
    replay = commands.execute_profile_command(
        owner_id="owner",
        key=command_id,
        command={
            "command_id": command_id,
            "type": "profile.mutate",
            "payload": request,
        },
        validate=_valid,
        validate_review=lambda _current: pytest.fail("replay was re-reviewed"),
    )
    assert replay == result
    assert (
        len(
            [
                item
                for item in agent_profiles.list_agent_profiles(enabled_only=False)
                if item["slug"] == "focused_writer"
            ]
        )
        == 1
    )


def test_receipts_are_bound_to_owner_target_and_command_type():
    request = _profile_request("create")
    receipt = _execute_profile(request, command_id=130)
    command_id = str(UUID(int=130))
    assert (
        commands.read_profile_receipt(
            "focused_writer",
            owner_id="owner",
            command_id=command_id,
            validate=_valid,
        )
        == receipt
    )
    with pytest.raises(commands.GoalProfileCommandError, match="receipt_unavailable"):
        commands.read_profile_receipt(
            "other",
            owner_id="owner",
            command_id=command_id,
            validate=_valid,
        )
