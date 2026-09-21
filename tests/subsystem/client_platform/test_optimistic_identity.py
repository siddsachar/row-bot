"""Optimistic renderer identity is the exact admitted Human identity."""
from __future__ import annotations

from tests.contracts.client_platform.test_headless_lifecycle import platform  # noqa: F401


def test_optimistic_user_key_survives_exact_checkpoint_replacement(platform):
    from row_bot.ui.streaming import _optimistic_user_submission
    from row_bot.ui.transcript import message_key
    from row_bot.message_projection import langchain_messages_to_ui_messages
    from row_bot.threads import get_latest_checkpoint_messages
    optimistic = _optimistic_user_submission("Same synthetic text", [])
    config = {"configurable": {"platform_submission_id": optimistic["message_id"]}}
    handle = platform.admit_execution("conversation-a", config, text=optimistic["content"])
    try:
        stored = langchain_messages_to_ui_messages(get_latest_checkpoint_messages("conversation-a"))[0]
        assert stored["checkpoint_message_id"] == optimistic["message_id"]
        assert message_key(3, optimistic) == message_key(3, stored)
        other = _optimistic_user_submission("Same synthetic text", [])
        assert other["message_id"] != optimistic["message_id"]
    finally:
        platform.finish_execution(handle, "interrupted")


def test_admission_publishes_durable_user_row_before_running_generation(platform):
    cursor = platform.projection.snapshot("conversation-a")["cursor"]
    submission_id = "immediate-durable-input"

    handle = platform.admit_execution(
        "conversation-a",
        {"configurable": {"platform_submission_id": submission_id}},
        text="Visible immediately",
    )
    try:
        events = platform.projection.events_since("conversation-a", cursor)["events"]
        checkpoint = next(
            index
            for index, event in enumerate(events)
            if event["type"] == "transcript.checkpoint"
        )
        running = next(
            index
            for index, event in enumerate(events)
            if event["type"] == "generation.state"
        )
        assert checkpoint < running
        rows = platform.projection.snapshot("conversation-a")["rows"]
        assert [
            (row["message_id"], row["blocks"][0]["text"])
            for row in rows
            if row.get("message_id") == submission_id
        ] == [(submission_id, "Visible immediately")]
    finally:
        platform.finish_execution(handle, "interrupted")


def test_queued_submission_reuses_exact_existing_intent_identity():
    from row_bot.ui.streaming import _optimistic_user_submission, _queued_control_message
    first = _queued_control_message("Same text", kind="follow_up", status="dispatching", label="Queued", message_id="first-intent")
    second = _queued_control_message("Same text", kind="follow_up", status="queued_parent_turn", label="Queued", message_id="second-intent")
    for _ in range(2):
        submission = _optimistic_user_submission("Same text", [first, second], queued_ids=["second-intent"])
        assert submission["message_id"] == "second-intent"
    assert first["message_id"] == "first-intent"
