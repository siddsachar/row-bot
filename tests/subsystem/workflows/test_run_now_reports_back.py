"""A run started from a chat reports back there when it ends, even a run quicker than the chat (F3)."""
from __future__ import annotations

import sqlite3
import time

import pytest

from tests.contracts.client_platform.test_headless_lifecycle import command, platform  # noqa: F401
from tests.subsystem.agents.test_linked_conversations import linked, rows  # noqa: F401
from tests.subsystem.agents.test_linked_conversations import settle as settle_chat
from tests.subsystem.workflows.test_workflow_runs_as_chat import Recording, reply, settle

pytestmark = pytest.mark.subsystem


def test_a_run_started_from_a_chat_reports_back_there_when_it_ends(linked, platform, monkeypatch):  # noqa: F811
    from row_bot import tasks, threads, waits
    from row_bot.tools import task_tool

    monkeypatch.setattr(tasks, "_deliver_to_channels", lambda task, text, **_: ("", ""))
    fake = Recording(reply("Steam rises.\nLeaves unfurl.\nA quiet cup."), reply("It ran: three lines about tea."))
    platform.stream_factory = fake.stream
    task_id = tasks.create_task("Morning lines", prompts=["Write three short lines about tea."], channels=[],
                                apply_default_skills=False)
    thread_id = tasks._prepare_task_thread(tasks.get_task(task_id))
    with sqlite3.connect(threads.DB_PATH) as connection:
        connection.execute("UPDATE thread_meta SET model_override='fixture/model' WHERE thread_id=?", (thread_id,))

    said = task_tool._task_run_now(task_id)  # From conversation-a, a new chat.
    settle(platform, thread_id)  # A quick run: it has ended before anything checks.
    assert "hears back by itself" in said
    [record] = waits.waiting("conversation-a")
    assert waits.tick(record["next_at"]) == 1
    settle_chat(platform, "conversation-a")
    # The chat's turn has the run's outcome and its whole reply.
    assert "finished its run: completed" in fake.prompts[1] and "A quiet cup." in fake.prompts[1]
    notes = [row["blocks"][0]["text"] for row in rows(platform, "conversation-a") if row.get("note") == "continuation"]
    assert notes == ["Checked back: Morning lines finishes its run."]
    # Once only.
    assert waits.waiting("conversation-a") == [] and waits.tick(time.time() + 3600) == 0
