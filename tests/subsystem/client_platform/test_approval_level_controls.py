"""The chat's approval control (A0): four levels; Hands-off needs its one-time confirmation and is only for
the person's own chats; an older client's "allow_all" is reviewed Auto."""

from __future__ import annotations

import json

import pytest

from tests.contracts.client_platform.test_headless_lifecycle import platform, command  # noqa: F401
from tests.contracts.client_platform.test_protocol_boundaries import client, protocol_clock  # noqa: F401
from tests.helpers.client_platform_fakes import fixture_id

pytestmark = pytest.mark.subsystem


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    folder = tmp_path / "approval-data"
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(folder))
    return folder


def save_level(service, label, level, **extra):
    from row_bot.api.v1.schemas import Command

    payload = {"runtime_mode": "agent", "profile_id": "", "approval_mode": level, **extra}
    # As the wire validates it: an older client's value is read in today's words.
    validated = Command.model_validate_json(json.dumps(command(
        "conversation.controls", label, payload, revision=service.get_conversation("conversation-a")["revision"])))
    return service.execute(owner_id="fixture-owner", idempotency_key=fixture_id(label + ":key"),
                           target="conversation-a", command=validated.model_dump(mode="json"))


def level(service) -> str:
    from row_bot.application.workspace_setup import conversation_workspace

    return conversation_workspace(service, "conversation-a")["controls"]["approval_mode"]


def approval_view(service) -> dict:
    from row_bot.application.workspace_setup import conversation_workspace

    return conversation_workspace(service, "conversation-a")["approval"]


def test_full_autonomy_needs_the_dialog_the_first_time_and_never_again(platform, data_dir):
    from row_bot.application.client_platform import ClientPlatformError

    assert approval_view(platform) == {"full_autonomy_confirmed": False, "full_autonomy_available": True}
    with pytest.raises(ClientPlatformError, match="full_autonomy_unconfirmed"):
        save_level(platform, "full-unconfirmed", "full")
    assert level(platform) == "approve"

    save_level(platform, "full-confirmed", "full", confirm_full_autonomy=True)
    assert level(platform) == "full"
    assert approval_view(platform)["full_autonomy_confirmed"] is True

    save_level(platform, "back-to-auto", "auto")
    save_level(platform, "full-again", "full")  # Remembered on this install: no dialog again.
    assert level(platform) == "full"


def test_an_older_clients_allow_all_is_reviewed_auto(platform, data_dir):
    save_level(platform, "legacy-auto", "allow_all")
    assert level(platform) == "auto"


def test_a_conversation_other_people_share_can_not_turn_on_full_autonomy(platform, data_dir):
    from row_bot import full_autonomy, tasks
    from row_bot.application.client_platform import ClientPlatformError

    full_autonomy.confirm()
    tasks.record_thread_channel_ref("conversation-a", channel="slack", target="C1:1700000000.000100",
                                    external_conversation_id="C1:1700000000.000100", shared=True)

    assert approval_view(platform) == {"full_autonomy_confirmed": True, "full_autonomy_available": False}
    with pytest.raises(ClientPlatformError, match="full_autonomy_shared_conversation"):
        save_level(platform, "shared-full", "full", confirm_full_autonomy=True)
    save_level(platform, "shared-auto", "auto")
    assert level(platform) == "auto"


def test_the_card_says_why_the_reviewer_asked(platform, client):
    """Under Auto, an action the reviewer handed to the person carries its reason to the approval card."""
    from langchain_core.messages import AIMessage

    from tests.helpers.client_platform_fakes import CheckpointCommit, ScriptedAgentStream

    tool_id, tool_ai_id = fixture_id("reviewed-tool"), fixture_id("reviewed-tool-ai")
    reason = "You asked to tidy the notes, not to delete any."
    fake = ScriptedAgentStream((
        CheckpointCommit((AIMessage(content="", id=tool_ai_id,
                                    tool_calls=[{"id": tool_id, "name": "workspace_file_delete", "args": {}}]),),
                         tool_ai_id),
        ("tool_call", {"id": tool_id, "message_id": tool_ai_id}),
        ("interrupt", [{"__interrupt_id": fixture_id("reviewed-interrupt"), "tool": "workspace_file_delete",
                        "description": "Delete file: notes/old-draft.md", "args": {},
                        "review": {"decision": "ask", "reason": reason}}]),
    ), ())
    platform.stream_factory, platform.resume_factory = fake.stream, fake.resume
    body = command("conversation.submit", "reviewed-submit", {
        "submission_id": fixture_id("reviewed-submit"), "text": "Tidy my notes", "attachment_refs": [],
        "model_selection": {"provider_id": "fixture", "model_ref": "fixture/model"}})
    body["client_session_id"] = client.headers["X-Client-Session"]
    pending = client.post("/api/v1/conversations/conversation-a/commands", json=body,
                          headers={"Idempotency-Key": fixture_id("reviewed-submit:key")})
    assert pending.status_code == 202, pending.text
    paused = platform.registry.get(pending.json()["execution_id"])
    assert paused.producer_done.wait(10) and paused.status == "waiting_approval"

    view = client.get("/api/v1/approvals/" + paused.approval_id)
    assert view.status_code == 200, view.text
    assert view.json()["reviewer_reason"] == reason
