"""The conversation list's live signal moves only for what its rows show (request budget).

Every open client reads the sidebar's list again when the ``conversations`` topic moves on, so the server
publishes it for a row added, renamed, pinned, deleted or moved up, and a conversation starting or settling,
and never for what happens inside a turn that no row shows: its words streaming, a helper's progress line,
a wait being checked, a skipped workflow run.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from langchain_core.messages import AIMessage

from row_bot.application import live_updates as live_module
from row_bot.application.live_updates import LiveUpdates
from tests.contracts.client_platform.test_headless_lifecycle import command, platform, submit  # noqa: F401
from tests.helpers.client_platform_fakes import CheckpointCommit, ScriptedAgentStream, StreamBarrier, fixture_id
from tests.subsystem.agents.test_linked_conversations import linked  # noqa: F401
from tests.subsystem.agents.test_waits import waits  # noqa: F401

pytestmark = pytest.mark.subsystem


@pytest.fixture
def live(monkeypatch):
    fresh = LiveUpdates()
    monkeypatch.setattr(live_module, "live_updates", fresh)
    return fresh


class Moved:
    """The topics that moved on since the last look."""

    def __init__(self, live: LiveUpdates) -> None:
        self.live = live
        self.seen = live.frame()["topics"]

    def __call__(self) -> set[str]:
        now = self.live.frame()["topics"]
        moved = {topic for topic, revision in now.items() if revision != self.seen[topic]}
        self.seen = now
        return moved


def _run(platform, kind: str, label: str, payload: dict | None = None, target: str = "conversation-a") -> dict:  # noqa: F811
    return platform.execute(owner_id="fixture-owner", idempotency_key=fixture_id(label + ":key"), target=target,
                            command=command(kind, label, payload or {},
                                            platform.get_conversation(target)["revision"] if target != "new" else "0"))


def test_a_new_chat_a_title_a_pin_and_a_delete_each_signal_the_list(platform, live):  # noqa: F811
    moved = Moved(live)

    created = _run(platform, "conversation.create", "new-chat", {"title": "Trip plans"}, target="new")
    assert "conversations" in moved()
    assert created["conversation_id"] in {row["id"] for row in platform.list_conversations()["items"]}

    _run(platform, "conversation.rename", "rename", {"title": "Quarterly numbers"})
    assert "conversations" in moved()
    assert platform.get_conversation("conversation-a")["title"] == "Quarterly numbers"

    _run(platform, "conversation.pin", "pin", {"pinned": True})
    assert "conversations" in moved()
    assert platform.get_conversation("conversation-a")["pinned"] is True

    _run(platform, "conversation.delete", "delete", target="conversation-b")
    assert "conversations" in moved()
    assert "conversation-b" not in {row["id"] for row in platform.list_conversations()["items"]}


def test_a_turn_signals_when_it_starts_and_when_it_settles_never_for_its_words(platform, live):  # noqa: F811
    started, streamed = StreamBarrier(), StreamBarrier()
    answer = fixture_id("answer")
    words = tuple(("token", f"Word {index}. ") for index in range(40))
    fake = ScriptedAgentStream((started, *words, ("thinking", None), streamed,
                                CheckpointCommit((AIMessage(content="Done.", id=answer),), answer), ("done", "Done.")))
    moved = Moved(live)

    receipt = submit(platform, fake, "turn")
    handle = platform.registry.get(receipt["execution_id"])
    assert started.entered.wait(10)
    # Running: the list moved on once the turn started (its row shows it running).
    assert "conversations" in moved()
    assert platform.get_conversation("conversation-a")["generation_state"][0]["status"] == "running"

    started.release.set()
    assert streamed.entered.wait(10)
    # Forty words and a thinking step streamed while it ran: none of them moved the list.
    assert sum(1 for kind, _ in fake.events if kind == "token") == 40
    assert "conversations" not in moved()

    streamed.release.set()
    assert handle.producer_done.wait(10)
    # Finished: the list moves on again, and the row no longer shows it running.
    assert "conversations" in moved()
    assert platform.get_conversation("conversation-a")["generation_state"] == []


def test_a_helpers_progress_line_signals_helpers_and_only_its_state_signals_the_list(platform, live):  # noqa: F811
    from row_bot import agent_runs

    agent_runs.create_agent_run(run_id="helper-1", kind="subagent", status="running", thread_id="helper-thread",
                                parent_thread_id="conversation-a", display_name="Researcher")
    moved = Moved(live)

    agent_runs.update_agent_status("helper-1", "running", "Reading the second source")
    agent_runs.update_agent_status("helper-1", "running", "Comparing the two")
    assert moved() == {"helpers"}

    agent_runs.update_agent_status("helper-1", "waiting_approval", "Waiting for your approval")
    assert moved() == {"helpers", "conversations"}
    assert agent_runs.get_agent_run("helper-1")["status"] == "waiting_approval"

    # A run that isn't there changes nothing and says so to no one.
    assert agent_runs.update_agent_status("no-such-run", "completed") is None
    assert "conversations" not in moved()


def test_a_wait_signals_its_own_topic_never_the_list(waits, platform, live):  # noqa: F811
    moved = Moved(live)
    record = waits.create("conversation-a", "time", "the deploy has had time to finish", spec={},
                          at=datetime.now() + timedelta(hours=1))
    assert moved() == {"waits"}
    assert waits.stop(record["id"], conversation_id="conversation-a")
    assert moved() == {"waits"}
    assert waits.get(record["id"])["status"] == "stopped"


def test_a_skipped_workflow_run_signals_runs_only(platform, live):  # noqa: F811
    from row_bot import tasks

    moved = Moved(live)
    tasks._record_skipped_run({"id": "task-1", "name": "Morning digest", "icon": ""}, "conversation-a",
                              "The last run is still going.")
    assert moved() == {"runs"}
