"""Admission and settlement publish the durable user row and the queue in order."""
from __future__ import annotations

from tests.contracts.client_platform.test_headless_lifecycle import platform  # noqa: F401


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


def test_finishing_run_publishes_its_drained_queue_before_the_final_checkpoint(platform):
    """A client resets on the final checkpoint and resubscribes from a
    snapshot that carries no queue, so the drained queue must come first (B97)."""
    from langchain_core.messages import AIMessage
    from row_bot.threads import append_checkpoint_messages

    handle = platform.admit_execution(
        "conversation-a",
        {"configurable": {"platform_submission_id": "queue-drain-input"}},
        text="Run that ends with durable rows the stream never bound",
    )
    queued = platform.projection.events_since("conversation-a", "0")["events"]
    assert [event["payload"]["submission_ids"] for event in queued
            if event["type"] == "queue.updated"][-1] == ["queue-drain-input"]
    cursor = platform.projection.snapshot("conversation-a")["cursor"]
    append_checkpoint_messages("conversation-a", [AIMessage(id="late-tool-result", content="Done after approval.")])

    platform.finish_execution(handle, "completed")

    events = platform.projection.events_since("conversation-a", cursor)["events"]
    kinds = [event["type"] for event in events]
    checkpoint = kinds.index("transcript.checkpoint")
    drained = [index for index, event in enumerate(events)
               if event["type"] == "queue.updated" and event["payload"]["submission_ids"] == []]
    assert drained and drained[0] < checkpoint
