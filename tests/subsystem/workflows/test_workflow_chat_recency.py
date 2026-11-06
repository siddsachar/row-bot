"""A workflow's conversation moves up the chats list only when something new shows in it.

A run that is skipped, a rename, or a setting changed leaves it where it was; a run that starts moves it
up. What other conversations left for a run shows under the run's line, and a run that didn't finish says
why there. The model is scripted, so nothing calls a provider.
"""
from __future__ import annotations

import sqlite3

import pytest

from tests.contracts.client_platform.test_headless_lifecycle import platform  # noqa: F401
from tests.subsystem.workflows.test_workflow_runs_as_chat import (  # noqa: F401
    Recording,
    create,
    reply,
    run,
    workflows,
)

pytestmark = pytest.mark.subsystem
EARLIER = "2026-01-01T09:00:00"


def set_earlier(thread_id: str) -> None:
    from row_bot import threads
    with sqlite3.connect(threads.DB_PATH) as conn:
        conn.execute("UPDATE thread_meta SET updated_at = ? WHERE thread_id = ?", (EARLIER, thread_id))


def last_moved(thread_id: str) -> str:
    from row_bot import threads
    with sqlite3.connect(threads.DB_PATH) as conn:
        return conn.execute("SELECT updated_at FROM thread_meta WHERE thread_id = ?", (thread_id,)).fetchone()[0]


def lines(platform, thread_id: str) -> list[dict]:  # noqa: F811
    return [row for row in platform.snapshot(thread_id)["rows"] if row.get("note") == "continuation"]


def test_a_skipped_run_leaves_the_chat_where_it_was(workflows, platform):  # noqa: F811
    task = create(workflows, name="Trends")
    thread_id = run(workflows, platform, Recording(reply("This week's trends.")), task)["thread_id"]
    shown = platform.snapshot(thread_id)["rows"]
    # The run before it is still going when the schedule fires again.
    workflows._record_run_start(task["id"], thread_id, 1, task_name=task["name"], task_icon="")
    set_earlier(thread_id)

    workflows._on_task_fire(task["id"])

    assert workflows.get_run_history(task["id"], limit=1)[0]["status"] == "skipped"
    assert last_moved(thread_id) == EARLIER
    assert platform.snapshot(thread_id)["rows"] == shown


def test_a_run_that_starts_moves_the_chat_up_and_a_rename_alone_does_not(workflows, platform):  # noqa: F811
    task = create(workflows, name="Daily results")
    thread_id = run(workflows, platform, Recording(reply("First.")), task)["thread_id"]
    set_earlier(thread_id)

    workflows.update_task(task["id"], name="Daily numbers")
    workflows._prepare_task_thread(workflows.get_task(task["id"]))
    assert last_moved(thread_id) == EARLIER
    assert platform.snapshot(thread_id)["rows"][0]["role"] == "user"

    run(workflows, platform, Recording(reply("Second.")), workflows.get_task(task["id"]))
    assert last_moved(thread_id) > EARLIER
    assert len(lines(platform, thread_id)) == 2


def test_changing_a_chats_settings_does_not_move_it_up(workflows, platform):  # noqa: F811
    from row_bot import threads
    thread_id = run(workflows, platform, Recording(reply("Done.")), create(workflows))["thread_id"]
    set_earlier(thread_id)

    threads.set_thread_app(thread_id, "app:example", False)
    threads.set_thread_skills_override(thread_id, ["research"])
    threads.set_thread_reasoning_selection(thread_id, "model:fixture:fixture/model", None)
    threads.bump_thread_client_revision(thread_id)

    assert last_moved(thread_id) == EARLIER
    assert threads.get_thread_apps_off(thread_id) == ["app:example"]
    assert threads.get_thread_skills_override(thread_id) == ["research"]


def test_a_run_that_didnt_finish_says_why_under_its_line(workflows, platform, monkeypatch):  # noqa: F811
    from row_bot import workflow_turns
    from row_bot.application.client_platform import settle_background
    monkeypatch.setattr(workflow_turns, "RETRY_WAITS", ())
    task = create(workflows, "Draft the post.", "Post it.", name="X content")
    thread_id = run(workflows, platform, Recording(reply("Drafted."), (("error", "The provider is busy."),)),
                    task)["thread_id"]
    assert settle_background(10)

    first, second = lines(platform, thread_id)
    assert "run_failure" not in first, "only the run's last line says it"
    assert second["run_failure"] == "The run didn't finish: The provider is busy."

    run(workflows, platform, Recording(reply("Drafted again."), reply("Posted.")), workflows.get_task(task["id"]))
    assert [row.get("run_failure") for row in lines(platform, thread_id)][2:] == [None, None]
    assert "run_failure" in lines(platform, thread_id)[1], "an earlier run keeps its reason"


def test_a_run_whose_turn_already_shows_its_error_says_nothing_more(workflows, platform, monkeypatch):  # noqa: F811
    from row_bot import workflow_turns
    from row_bot.application.client_platform import settle_background
    monkeypatch.setattr(workflow_turns, "RETRY_WAITS", ())
    shown_error = "⚠️ Rate limit reached — try again in a minute."
    fake = Recording((*reply(shown_error)[:1], ("error", "Rate limit reached")))
    thread_id = run(workflows, platform, fake, create(workflows))["thread_id"]
    assert settle_background(10)

    assert workflows.get_run_history(workflows.list_tasks()[0]["id"], limit=1)[0]["status"] == "failed"
    assert "run_failure" not in lines(platform, thread_id)[0]


def test_messages_left_for_the_next_run_show_under_its_line(workflows, platform, monkeypatch):  # noqa: F811
    from row_bot import linked_conversations
    fake = Recording(reply("Numbers are in."), reply("Noted the numbers."))
    sender = run(workflows, platform, fake, create(workflows, name="Daily results"))["thread_id"]
    task = create(workflows, name="Trends")
    monkeypatch.setattr(linked_conversations, "origin",
                        lambda: linked_conversations.Origin(sender, "turn-a", "⚡ Daily results"))
    linked_conversations.send("Trends", "Downloads rose 8% this week.", when="next_run")
    linked_conversations.send("Trends", "Skip the release thread today.", when="next_run")

    thread_id = run(workflows, platform, fake, task)["thread_id"]

    (line,) = lines(platform, thread_id)
    assert line["held"] == [
        {"id": sender, "title": "⚡ Daily results", "text": "Downloads rose 8% this week."},
        {"id": sender, "title": "⚡ Daily results", "text": "Skip the release thread today."},
    ]
    assert "- From “⚡ Daily results”: Downloads rose 8% this week." in fake.prompts[1], "the model reads them"
    run(workflows, platform, Recording(reply("Again.")), workflows.get_task(task["id"]))
    assert "held" not in lines(platform, thread_id)[1], "read once"
