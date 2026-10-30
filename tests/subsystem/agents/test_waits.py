"""Waits (5.3.1): a conversation waits for something and carries on there when it happens.

Real admission and turns with a scripted model; the clock is passed in, so nothing sleeps. Nothing runs a model
while it waits; when it happens, or the wait runs out, one turn starts in the conversation that set it.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta

import pytest

from tests.contracts.client_platform.test_headless_lifecycle import command, platform  # noqa: F401
from tests.subsystem.agents.test_linked_conversations import linked, rows, settle  # noqa: F401
from tests.subsystem.workflows.test_workflow_runs_as_chat import Recording, reply

pytestmark = pytest.mark.subsystem


@pytest.fixture
def waits(linked, monkeypatch):  # noqa: F811
    """Waits set from conversation-a's turn, as the tool sets them."""
    from row_bot import waits as module

    return module


def _set(**args) -> str:
    from row_bot.tools.waits_tool import _wait_for

    return _wait_for(**args)


def _notes(platform, conversation: str) -> list[str]:  # noqa: F811
    return [row["blocks"][0]["text"] for row in rows(platform, conversation) if row.get("note") == "continuation"]


def test_a_time_wait_starts_one_turn_when_its_time_comes_and_nothing_before(waits, platform):  # noqa: F811
    fake = Recording(reply("Checked the deploy: it's live."))
    platform.stream_factory = fake.stream

    said = _set(kind="time", label="the deploy has had time to finish", in_minutes=30, then="Check the deploy.")
    assert said.startswith("Waiting for “the deploy has had time to finish”") and "End this turn now." in said
    [record] = waits.waiting("conversation-a")

    assert waits.tick(record["next_at"] - 1) == 0 and fake.prompts == [], "nothing runs while it waits"
    assert waits.tick(record["next_at"] + 1) == 1
    settle(platform, "conversation-a")

    assert fake.prompts[0].startswith("Checked back: the deploy has had time to finish.")
    assert "What you said to do then: Check the deploy." in fake.prompts[0]
    assert _notes(platform, "conversation-a") == ["Checked back: the deploy has had time to finish."]
    assert waits.waiting("conversation-a") == [] and waits.get(record["id"])["status"] == "done"


def test_a_check_runs_its_command_until_the_text_shows_then_carries_on(waits, platform, monkeypatch):  # noqa: F811
    import subprocess

    fake = Recording(reply("CI is green: merging."))
    platform.stream_factory = fake.stream
    outputs = iter(["checks: pending\n", "checks: pending\n", "All checks were successful\n"])
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, next(outputs), ""))

    said = _set(kind="check", label="CI on PR 590 is green", command="git status", text="all checks were successful",
                every_minutes=2, hours=1)
    assert "checks every 2 min" in said
    [record] = waits.waiting("conversation-a")
    first = record["next_at"]

    assert waits.tick(first) == 0 and waits.get(record["id"])["next_at"] == first + 120, "looked, not yet: again later"
    assert waits.tick(first + 120) == 0 and fake.prompts == []
    assert waits.tick(first + 240) == 1
    settle(platform, "conversation-a")
    assert "`git status` shows “all checks were successful”" in fake.prompts[0]
    assert "All checks were successful" in fake.prompts[0]


def test_a_wait_that_runs_out_says_so_in_one_turn(waits, platform, monkeypatch):  # noqa: F811
    import subprocess

    fake = Recording(reply("It didn't finish in time."))
    platform.stream_factory = fake.stream
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1, "still building", ""))
    _set(kind="check", label="the build passes", command="git status", every_minutes=30, hours=1)
    [record] = waits.waiting("conversation-a")

    assert waits.tick(record["until"] + 1) == 1
    settle(platform, "conversation-a")
    assert fake.prompts[0].startswith("Checked back: “the build passes” didn't happen before the wait ran out.")
    assert waits.get(record["id"])["status"] == "ran_out"


def test_a_busy_conversation_gets_its_checked_back_turn_after_its_current_one(waits, platform, monkeypatch):  # noqa: F811
    fake = Recording(reply("Back to it."))
    platform.stream_factory = fake.stream
    busy = {"conversation-a"}
    real_active = platform.registry.active
    monkeypatch.setattr(platform.registry, "active",
                        lambda *given: ["turn"] if given and given[0] in busy else real_active(*given))
    _set(kind="time", label="ten minutes have passed", in_minutes=10)
    [record] = waits.waiting("conversation-a")

    assert waits.tick(record["next_at"] + 1) == 0 and waits.get(record["id"])["status"] == "due"
    busy.clear()
    assert waits.tick(record["next_at"] + 60) == 1
    settle(platform, "conversation-a")
    assert fake.prompts[0].startswith("Checked back: ten minutes have passed.")


def test_a_stopped_wait_starts_nothing_and_limits_are_said_in_words(waits, platform):  # noqa: F811
    fake = Recording()
    platform.stream_factory = fake.stream
    _set(kind="time", label="a reminder", in_minutes=5)
    [record] = waits.waiting("conversation-a")

    assert waits.stop(record["id"]) is True and waits.tick(record["next_at"] + 1) == 0 and fake.prompts == []
    assert _set(kind="time", label="", in_minutes=5).startswith("Not set: Say what it waits for")
    assert "at most once a minute" in _set(kind="check", label="x", command="git status", every_minutes=0.1)
    assert "at most 7 days ahead" in _set(kind="time", label="x", at=(datetime.now() + timedelta(days=9)).isoformat())
    for index in range(5):
        _set(kind="time", label=f"wait {index}", in_minutes=5)
    assert "already waits for 5 things" in _set(kind="time", label="one more", in_minutes=5)


def test_a_check_command_asks_once_by_the_approval_level_and_never_reads_private_data(waits, platform,  # noqa: F811
                                                                                     monkeypatch):
    from row_bot.tools import approval_gate

    asked: list[dict] = []
    monkeypatch.setattr(approval_gate, "approval_check",
                        lambda payload, **_: asked.append(payload) or ("Nothing ran: the person said no.", ""))

    denied = _set(kind="check", label="the server answers", command=f'"{sys.executable}" -c "print(1)"')
    assert denied == "Nothing ran: the person said no." and waits.waiting("conversation-a") == []
    assert asked[0]["description"].startswith("Run `") and "every 5 min" in asked[0]["description"]
    assert _set(kind="check", label="x", command="git status").startswith("Waiting for"), "a safe command asks nothing"
    assert len(asked) == 1
    assert "can't read Row-Bot's private data" in _set(kind="check", label="x",
                                                       command="type %ROW_BOT_DATA_DIR%\\threads.db")


def test_waiting_for_another_conversation_carries_on_when_its_turn_ends(waits, platform):  # noqa: F811
    from row_bot import linked_conversations

    fake = Recording(reply("B is done; summarising."))
    platform.stream_factory = fake.stream
    said = _set(kind="conversation", label="conversation-b has finished", conversation="conversation-b")
    assert said.startswith("Waiting for “conversation-b has finished”")
    [record] = waits.waiting("conversation-a")
    assert waits.tick(record["next_at"]) == 0, "nothing new there yet"

    linked_conversations._service()._metadata("conversation-b")  # It exists, and has just moved on:
    import sqlite3

    from row_bot import threads
    with sqlite3.connect(threads.DB_PATH) as connection:
        connection.execute("UPDATE thread_meta SET updated_at = ? WHERE thread_id = 'conversation-b'",
                           (datetime.fromtimestamp(record["created_at"] + 5).isoformat(),))
    assert waits.tick(record["next_at"] + 30) == 1
    settle(platform, "conversation-a")
    assert "“conversation-b” finished its turn." in fake.prompts[0]


def test_a_message_from_the_awaited_conversation_ends_the_wait_without_a_second_turn(waits, platform):  # noqa: F811
    from row_bot import linked_conversations

    fake = Recording(reply("Here is B's draft; it looks good."))
    platform.stream_factory = fake.stream
    _set(kind="conversation", label="conversation-b has finished", conversation="conversation-b")
    [record] = waits.waiting("conversation-a")

    # conversation-b sends its draft back to conversation-a, the one waiting for it.
    linked_conversations.current["origin"] = linked_conversations.Origin("conversation-b", "turn-b", "conversation-b")
    linked_conversations.send("conversation-a", "Draft: Welcome to Fernleaf Teas.", reply=False)
    settle(platform, "conversation-a")

    assert waits.waiting("conversation-a") == [], "the draft is the answer the wait was for"
    assert waits.get(record["id"])["status"] == "done"
    assert len(fake.prompts) == 1, "one turn reads the draft; no 'Checked back' turn repeats it"


def test_the_api_lists_waits_with_their_conversation_and_stops_one(waits, platform):  # noqa: F811
    from tests.subsystem.client_protocol.test_protocol_application import _client
    from tests.subsystem.client_protocol.test_protocol_security import bootstrap

    _set(kind="time", label="the deploy has had time to finish", in_minutes=30)
    waits.create("conversation-gone", "time", "for a deleted chat", spec={},
                 at=datetime.now() + timedelta(minutes=5))
    with _client(platform) as client:
        _, headers = bootstrap(client)
        page = client.get("/api/v1/waits", headers=headers)
        assert page.status_code == 200, page.text
        [item] = page.json()["items"]
        assert (item["conversation_id"], item["label"], item["kind"]) == (
            "conversation-a", "the deploy has had time to finish", "time")
        assert item["how"].startswith("at ") and item["conversation_title"]

        stopped = client.post(f"/api/v1/waits/{item['wait_id']}/stop", headers=headers)
        assert stopped.status_code == 200 and stopped.json()["items"] == []
        again = client.post(f"/api/v1/waits/{item['wait_id']}/stop", headers=headers)
        assert (again.status_code, again.json()["code"]) == (404, "wait_unavailable")
