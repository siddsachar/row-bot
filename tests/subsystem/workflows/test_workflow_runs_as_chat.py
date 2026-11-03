"""A workflow runs as a chat turn in its own conversation (F31).

Real admission, checkpoint, approval and run owners; the model is scripted, so
nothing calls a provider and nothing is posted or sent.
"""
from __future__ import annotations

import json
import sqlite3
import threading

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from tests.contracts.client_platform.test_headless_lifecycle import command, platform  # noqa: F401
from tests.helpers.client_platform_fakes import CheckpointCommit, ScriptedAgentStream, fixture_id

pytestmark = pytest.mark.subsystem

POST_TEXT = "Row-Bot 5.3 runs a browser beside your chat. You watch it work and take over any time. https://row-bot.ai"
POST_LINK = "https://x.com/row_bot_ai/status/2114329103603990962"
X_POST = {
    "__interrupt_id": "x-post-1", "tool": "x_post", "label": "Post to X as @row_bot_ai",
    "question": "Post to X as @row_bot_ai?", "description": "Post to X as @row_bot_ai",
    "args": {"account": "@row_bot_ai", "action": "post", "text": POST_TEXT},
    "x_post": {"account_id": "2047775631836844032", "username": "row_bot_ai", "name": "Row-Bot",
               "action": "post", "text": POST_TEXT, "target_id": "", "target_url": "", "media": []},
}


class Recording(ScriptedAgentStream):
    """Keeps each turn's message and the tools it was given."""

    def __init__(self, *scripts) -> None:
        super().__init__(*scripts)
        self.prompts: list[str] = []
        self.tools: list[list[str]] = []

    def stream(self, text, enabled_tools, config, *, stop_event=None):
        self.prompts.append(text)
        self.tools.append(list(enabled_tools))
        yield from super().stream(text, enabled_tools, config, stop_event=stop_event)


def reply(text: str, *before):
    native_id = fixture_id(text + ":assistant")
    return (*before, CheckpointCommit((AIMessage(content=text, id=native_id),), native_id), ("done", text))


def posted(label: str = "posted"):
    """A turn whose x_post call went through: its call, X's answer and the reply, as the chat saves them."""
    call_id = fixture_id(label + ":call")
    call = AIMessage(content="", id=fixture_id(label + ":call-message"),
                     tool_calls=[{"name": "x_post", "args": {"account": "@row_bot_ai", "text": POST_TEXT},
                                  "id": call_id}])
    result = ToolMessage(content=f"Posted on X as @row_bot_ai: {POST_LINK}", name="x_post",
                         tool_call_id=call_id, id=fixture_id(label + ":result"))
    final = AIMessage(content="Posted.", id=fixture_id(label + ":assistant"))
    return (CheckpointCommit((call, result, final), final.id), ("done", "Posted."))


@pytest.fixture
def workflows(platform, monkeypatch):  # noqa: F811
    from row_bot import tasks
    from row_bot.application import client_platform, conversation_followups
    from row_bot.tools import x_tool  # noqa: F401  (registers X, whose post is an outside action)

    monkeypatch.setattr(client_platform, "client_platform_service", platform)
    monkeypatch.setattr(tasks, "_deliver_to_channels", lambda task, text, **_: ("", ""))
    for key in list(conversation_followups._PENDING):
        conversation_followups._PENDING.pop(key, None)
    return tasks


def create(tasks, *messages: str, name: str = "Daily X") -> dict:
    task_id = tasks.create_task(name, prompts=list(messages or ["Draft today's post for @row_bot_ai and post it."]),
                                channels=[], apply_default_skills=False)
    return tasks.get_task(task_id)


def run(tasks, platform, fake, task: dict, **options) -> dict:  # noqa: F811
    from row_bot import threads
    from row_bot.tools import registry as tool_registry
    platform.stream_factory = fake.stream
    platform.resume_factory = fake.resume
    thread_id = tasks._prepare_task_thread(tasks.get_task(task["id"]))
    with sqlite3.connect(threads.DB_PATH) as connection:  # The model the conversation runs on.
        connection.execute("UPDATE thread_meta SET model_override='fixture/model' WHERE thread_id=?", (thread_id,))
    tasks.run_task_background(task["id"], thread_id, [t.name for t in tool_registry.get_enabled_tools()],
                              **options)
    settle(platform, thread_id)
    return latest(tasks, task["id"])


def settle(platform, thread_id: str) -> None:  # noqa: F811
    """Wait until the run's turns are over: it has ended, or it waits on a card."""
    from row_bot import tasks
    from row_bot.application.client_platform import settle_background
    for _ in range(400):
        run = tasks.active_run_for_conversation(thread_id)
        if not platform.registry.active(thread_id) and (run is None or run["status"] == "waiting"):
            break
        threading.Event().wait(0.025)
    assert settle_background(10)


def latest(tasks, task_id: str) -> dict:
    return tasks.get_run_history(task_id, limit=1)[0]


def resolve(platform, approval_id: str, decision: str, label: str):  # noqa: F811
    receipt = platform.execute(owner_id="owner", idempotency_key=label, target=approval_id,
                               command=command("approval.resolve", label, {"decision": decision}))
    assert platform.registry.get(receipt["execution_id"]).producer_done.wait(10)


def pending_approval(tasks, thread_id: str) -> str:
    conn = tasks._get_conn()
    try:
        row = conn.execute("SELECT id FROM approval_requests WHERE source_thread_id=? AND status='pending'",
                           (thread_id,)).fetchone()
    finally:
        conn.close()
    assert row is not None, "the turn waits on its card"
    return str(row[0])


def test_a_run_is_a_turn_in_the_workflows_own_conversation_with_every_chat_tool(workflows, platform):  # noqa: F811
    from row_bot.tools import registry as tool_registry
    fake = Recording(reply("Here is today's research."), reply("A second report."))
    task = create(workflows, "Summarise what's new on {{date}}.")

    first = run(workflows, platform, fake, task)
    second = run(workflows, platform, fake, workflows.get_task(task["id"]))

    assert first["thread_id"] == second["thread_id"], "one conversation, a new turn each run"
    assert fake.tools[0] == [t.name for t in tool_registry.get_enabled_tools()], "the same tools as a chat"
    assert "{{date}}" not in fake.prompts[0]
    assert (second["status"], second["notice"]) == ("completed", "A second report.")
    notes = [row["blocks"][0]["text"] for row in platform.snapshot(first["thread_id"])["rows"]
             if row.get("note") == "continuation"]
    assert len(notes) == 2 and all(note.startswith("Run · ") for note in notes)


def test_a_post_asks_on_its_full_card_and_the_run_ends_with_the_posts_link(workflows, platform):  # noqa: F811
    fake = Recording((("interrupt", X_POST),), posted())
    task = create(workflows)
    waiting = run(workflows, platform, fake, task)
    assert waiting["status"] == "waiting"
    assert waiting["notice"], "the run says what it waits on"

    approval_id = pending_approval(workflows, waiting["thread_id"])
    card = platform.get_approval(approval_id)
    assert (card["x_post"]["username"], card["x_post"]["text"]) == ("row_bot_ai", POST_TEXT), \
        "the whole post and its account, never a cut line"

    resolve(platform, approval_id, "approve", "approve-post")
    settle(platform, waiting["thread_id"])
    done = latest(workflows, task["id"])
    assert done["status"] == "completed"
    assert done["notice"] == f"Posted on X as @row_bot_ai: {POST_LINK}"
    assert json.loads(done["actions_json"])[0]["link"] == POST_LINK


def test_saying_no_on_the_card_ends_the_run_as_denied(workflows, platform):  # noqa: F811
    fake = Recording((("interrupt", X_POST),), reply("Stopped.", ("tool_done", {
        "name": "x_post", "tool_call_id": "call-1", "content": "Action cancelled by user."})))
    task = create(workflows)
    waiting = run(workflows, platform, fake, task)
    resolve(platform, pending_approval(workflows, waiting["thread_id"]), "deny", "deny-post")
    settle(platform, waiting["thread_id"])
    done = latest(workflows, task["id"])
    assert done["status"] == "denied"
    assert done["notice"].startswith("You said no to “") and done["notice"].endswith("”, so it stopped there.")
    from row_bot import workflow_turns
    kept = workflow_turns.notes(task["id"])
    assert "the person said no to “" in kept and "Don't do it another way" in kept, "the next run knows"


def test_a_new_run_replaces_one_still_waiting_on_its_card(workflows, platform):  # noqa: F811
    fake = Recording((("interrupt", X_POST),), reply("Nothing new today."))
    task = create(workflows)
    waiting = run(workflows, platform, fake, task)
    approval_id = pending_approval(workflows, waiting["thread_id"])

    run(workflows, platform, fake, workflows.get_task(task["id"]))

    old = next(r for r in workflows.get_run_history(task["id"], limit=5) if r["id"] == waiting["id"])
    assert (old["status"], old["notice"]) == ("expired", "A newer run replaced it before it was approved.")
    assert platform.get_approval(approval_id)["status"] == "cancelled", "an old card can't post today"


def test_stop_ends_a_run_waiting_on_its_card(workflows, platform):  # noqa: F811
    fake = Recording((("interrupt", X_POST),), reply("Should never run"))
    task = create(workflows)
    waiting = run(workflows, platform, fake, task)
    assert workflows.stop_task(waiting["thread_id"]) is True
    stopped = workflows.get_run_history(task["id"], limit=1)[0]
    assert stopped["status"] == "stopped"
    assert len(fake.calls) == 1, "nothing runs after Stop"


def test_each_message_is_its_own_turn_in_order(workflows, platform):  # noqa: F811
    fake = Recording(reply("Found three creators."), reply("Wrote to the first."))
    task = create(workflows, "Find three creators.", "Draft a note to the first one.")
    done = run(workflows, platform, fake, task)
    assert fake.prompts[0].endswith("\n\n---\n\nFind three creators.")  # After the workflow's notes (G4).
    assert fake.prompts[1] == "Draft a note to the first one."
    assert (done["status"], done["steps_done"], done["steps_total"]) == ("completed", 2, 2)


def test_a_workflow_keeps_notes_its_next_run_reads_first(workflows, platform):  # noqa: F811
    """G4: a run keeps short notes with task_update(notes=...); the next run's first message carries them."""
    from row_bot.tools.task_tool import _task_update
    fake = Recording(reply("Checked the 5.3.0 release."), reply("Nothing new since."), reply("Posted."),
                     reply("Said what went out."))
    task = create(workflows, "Check what's new and post about it.", "Then say what you posted.")

    run(workflows, platform, fake, task)
    kept = _task_update(task["id"], notes="Release 5.3.0 checked 27 Oct. Last post ids: 2114329103603990962.")
    second = run(workflows, platform, fake, workflows.get_task(task["id"]))

    assert fake.prompts[0].startswith("This workflow has no notes from earlier runs yet.")
    assert f'task_update(task_id="{task["id"]}", notes=...)' in fake.prompts[0]
    assert kept.endswith("Notes: 65 characters kept for the next run")
    assert fake.prompts[2].startswith("Notes this workflow kept from its earlier runs:\nRelease 5.3.0 checked 27 Oct. "
                                      "Last post ids: 2114329103603990962.\n\n")
    assert fake.prompts[2].endswith("Check what's new and post about it.")
    assert second["status"] == "completed"
    notes = [row["blocks"][0]["text"] for row in platform.snapshot(second["thread_id"])["rows"]
             if row.get("note") == "continuation"]
    assert not any("Notes this workflow kept" in note for note in notes), "the chat shows the run, not its notes"


def test_workflow_notes_are_cut_to_their_limit_and_cleared_with_empty_text(workflows):
    from row_bot import workflow_turns
    from row_bot.tools.task_tool import _task_update
    task = create(workflows, "Check the release.")

    cut = _task_update(task["id"], notes="x" * 2_500)
    assert cut.endswith("Notes: 2,000 characters kept for the next run (cut to 2,000)")
    assert len(workflow_turns.notes(task["id"])) == 2_000
    assert _task_update(task["id"], notes="").endswith("Notes: cleared")
    assert workflow_turns.notes(task["id"]) == ""


def test_a_passing_failure_tries_again_but_never_after_something_went_out(workflows, platform, monkeypatch):  # noqa: F811
    from row_bot import workflow_turns
    monkeypatch.setattr(workflow_turns, "RETRY_WAITS", (0.0,))
    fake = Recording((("error", "The provider is busy."),), reply("Done on the second try."))
    done = run(workflows, platform, fake, create(workflows))
    assert (done["status"], len(fake.calls)) == ("completed", 2)

    fake = Recording((*posted("went-out")[:1], ("error", "The provider is busy.")),)
    done = run(workflows, platform, fake, create(workflows, name="Posts once"))
    assert done["status"] == "failed"
    assert len(fake.calls) == 1, "a turn that already posted is never run again"


def test_a_run_whose_turn_cannot_start_ends_failed_with_a_reason(workflows, platform, monkeypatch):  # noqa: F811
    from row_bot.application import conversation_followups

    def no_worker(*args, **kwargs):
        raise RuntimeError("cannot start new thread")

    monkeypatch.setattr(conversation_followups, "start", no_worker)
    done = run(workflows, platform, Recording(), create(workflows))
    assert done["status"] == "failed"
    assert done["notice"] == "The run couldn't start. Check this workflow's model, then run it again."
    assert not workflows.get_running_tasks()


def test_the_workflows_always_ask_apps_reach_every_turn_of_its_conversation(workflows, platform):  # noqa: F811
    """N9: the person's own rule holds in the workflow's turns under every approval level."""
    seen: list[dict] = []

    class Rules(Recording):
        def stream(self, text, enabled_tools, config, *, stop_event=None):
            seen.append(dict(config["configurable"].get("person_rules") or {}))
            yield from super().stream(text, enabled_tools, config, stop_event=stop_event)

    task = create(workflows)
    workflows.update_task(task["id"], always_ask=["builtin:account:x"], safety_mode="full")
    run(workflows, platform, Rules(reply("Done.")), workflows.get_task(task["id"]))
    assert seen == [{"step": False, "items": ["builtin:account:x"]}]


# ── What reaches the channels (5.3.1 G8) ─────────────────────────────────────


@pytest.fixture
def delivered(workflows, monkeypatch) -> list[str]:
    """What each run sent to the workflow's channels (none are real here)."""
    sent: list[str] = []

    def deliver(task, text, **_):
        sent.append(text)
        return "delivered", "Delivered to Telegram"
    monkeypatch.setattr(workflows, "_deliver_to_channels", deliver)
    return sent


def test_each_delivery_says_first_what_the_run_did(workflows, platform, delivered):  # noqa: F811
    report = run(workflows, platform, Recording(reply("Three new posts about local AI today.")),
                 create(workflows, "Read what's new about local AI."))
    fake = Recording((("interrupt", X_POST),), posted())
    task = create(workflows)
    waiting = run(workflows, platform, fake, task)
    resolve(platform, pending_approval(workflows, waiting["thread_id"]), "approve", "approve-post")
    settle(platform, waiting["thread_id"])

    assert report["status"] == "completed"
    assert delivered == ["Finished · nothing was posted or sent\n\nThree new posts about local AI today.",
                         f"Sent · Posted on X as @row_bot_ai: {POST_LINK}\n\nPosted."]
    assert latest(workflows, task["id"])["status_message"] == "Delivered to Telegram"


def test_only_when_it_needs_me_sends_a_run_that_didnt_finish_and_nothing_else(
        workflows, platform, delivered, monkeypatch):  # noqa: F811
    from row_bot import workflow_turns
    monkeypatch.setattr(workflow_turns, "RETRY_WAITS", ())
    quiet = create(workflows, "Read what's new.")
    workflows.update_task(quiet["id"], send_results="needs_me")
    fake = Recording(reply("Nothing new."), (("error", "The provider is busy."),))
    done = run(workflows, platform, fake, workflows.get_task(quiet["id"]))
    failed = run(workflows, platform, fake, workflows.get_task(quiet["id"]))

    assert (done["status"], done["status_message"]) == ("completed", "")
    assert failed["status"] == "failed"
    assert delivered == [f"Didn't finish · {failed['notice']}"]


def test_a_daily_digest_sends_one_line_per_run_once_a_day_from_six(workflows, platform, delivered):  # noqa: F811
    from datetime import datetime, timedelta
    task = create(workflows, "Read what's new.", name="Daily read")
    workflows.update_task(task["id"], send_results="digest")
    fake = Recording(reply("One new release."), (("interrupt", X_POST),), posted())
    first = run(workflows, platform, fake, workflows.get_task(task["id"]))
    waiting = run(workflows, platform, fake, workflows.get_task(task["id"]))
    resolve(platform, pending_approval(workflows, waiting["thread_id"]), "approve", "approve-post")
    settle(platform, waiting["thread_id"])
    assert delivered == [], "a digest workflow sends nothing per run"
    finished = datetime.fromisoformat(latest(workflows, task["id"])["finished_at"]).astimezone()
    evening = finished.replace(hour=18, minute=5) + (timedelta(days=1) if finished.hour >= 18 else timedelta())

    assert workflows.send_due_digests(evening.replace(hour=17, minute=59)) == 0, "not before six"
    assert workflows.send_due_digests(evening) == 1
    assert workflows.send_due_digests(evening + timedelta(hours=2)) == 0, "once a day"

    def stamp(moment: datetime) -> str:
        return f"{moment:%H:%M}" if moment.date() == evening.date() else f"{moment:%a %H:%M}"

    [digest] = delivered
    read = stamp(datetime.fromisoformat(first["finished_at"]).astimezone())
    assert first["status"] == "completed"
    assert digest == (f"Daily digest · 2 runs\n{read} · Finished · nothing was posted or sent\n"
                      f"{stamp(finished)} · Sent · Posted on X as @row_bot_ai: {POST_LINK}")


def test_a_workflow_reads_in_full_with_its_messages_notes_time_zone_and_conversation(workflows):
    """W1/W4: rewriting a workflow starts from what it says and keeps, not from a message count."""
    import json

    from row_bot.tools.task_tool import _task_list, _task_update
    task = create(workflows, "Check the release.", "Then say what's new.")
    workflows.update_task(task["id"], schedule="daily:09:00")
    _task_update(task["id"], notes="Release 5.3.0 checked.")

    assert _task_update(task["id"], timezone="Mars/Olympus").startswith(
        "Error updating task: timezone must be an IANA zone such as Europe/London")
    assert "Time zone: Europe/London" in _task_update(task["id"], timezone="Europe/London")
    detail = json.loads(_task_list(task_id=task["id"]))

    assert detail["messages"] == ["Check the release.", "Then say what's new."]
    assert (detail["notes"], detail["schedule"], detail["timezone"]) == (
        "Release 5.3.0 checked.", "daily:09:00", "Europe/London")
    assert detail["conversation"].startswith("not yet"), "it hasn't run, so it has no conversation yet"
    listed = json.loads(_task_list())
    assert [entry["timezone"] for entry in listed if entry["id"] == task["id"]] == ["Europe/London"]


def test_a_one_off_run_is_not_asked_to_keep_notes_for_a_next_run(workflows, platform):  # noqa: F811
    """U21: a workflow that runs once has no next run, so its message comes without notes or the ask to keep them."""
    fake = Recording(reply("The workspace holds 3 files."))
    task = create(workflows, "List the files in the workspace.")
    workflows.update_task(task["id"], at="2027-01-01T10:00", schedule=None)

    done = run(workflows, platform, fake, workflows.get_task(task["id"]))

    assert fake.prompts[0] == "List the files in the workspace."
    assert done["status"] == "completed"
