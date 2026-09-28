"""Goals keep working on the client-platform path (B148, decision 16).

Real admission, checkpoint, goal and approval owners; the model and the goal
verifier are scripted, so nothing calls a provider.
"""
from __future__ import annotations

import sqlite3
import threading

from langchain_core.messages import AIMessage
import pytest

from tests.contracts.client_platform.test_headless_lifecycle import command, platform  # noqa: F401
from tests.helpers.client_platform_fakes import CheckpointCommit, ScriptedAgentStream, StreamBarrier, fixture_id

pytestmark = pytest.mark.subsystem

CONVERSATION = "conversation-a"


def completed(label: str, *before):
    native_id = fixture_id(label + ":assistant")
    return (*before, CheckpointCommit((AIMessage(content=label, id=native_id),), native_id), ("done", label))


class Recording(ScriptedAgentStream):
    """Keeps the prompt each turn received."""

    def __init__(self, *scripts) -> None:
        super().__init__(*scripts)
        self.prompts: list[str] = []

    def stream(self, text, enabled_tools, config, *, stop_event=None):
        self.prompts.append(text)
        yield from super().stream(text, enabled_tools, config, stop_event=stop_event)


@pytest.fixture
def goal_setup(platform, monkeypatch):  # noqa: F811
    from row_bot import goals, threads
    from row_bot.application import conversation_followups

    with sqlite3.connect(threads.DB_PATH) as connection:
        connection.execute("UPDATE thread_meta SET model_override='fixture/model' WHERE thread_id=?",
                           (CONVERSATION,))
    verdicts: list[dict] = []
    monkeypatch.setattr(goals, "_invoke_goal_verifier",
                        lambda goal, context: verdicts.pop(0) if verdicts else
                        {"verdict": "continue", "reason": "More to do."})
    for key in list(conversation_followups._PENDING):
        conversation_followups._PENDING.pop(key, None)
    return platform, verdicts


def start(platform, fake, max_turns: int):  # noqa: F811
    from row_bot import goals
    from row_bot.application import conversation_followups

    platform.stream_factory = fake.stream
    platform.resume_factory = fake.resume
    goal = goals.start_goal(CONVERSATION, "Write three synthetic notes", max_turns=max_turns)
    conversation_followups.after_goal_change(platform, CONVERSATION, "start", goal)
    return goal


def wait_idle(platform, fake, calls: int) -> None:  # noqa: F811
    from row_bot.application.client_platform import _COMMAND_LOCK
    for _ in range(200):
        with _COMMAND_LOCK:
            if len(fake.calls) >= calls and not platform.registry.active(CONVERSATION):
                return
        threading.Event().wait(0.05)
    raise AssertionError(f"expected {calls} turns, saw {len(fake.calls)}")


def test_start_goal_works_at_once_and_continues_to_its_limit(goal_setup):
    from row_bot import goals
    platform, _ = goal_setup
    fake = Recording(*(completed(f"step {index}") for index in range(4)))
    goal = start(platform, fake, max_turns=3)
    wait_idle(platform, fake, 3)
    assert len(fake.calls) == 3, "a goal with limit 3 runs exactly three turns"
    assert fake.prompts[0].startswith("[Goal mode started]")
    assert all(prompt.startswith("[Goal continuation]") for prompt in fake.prompts[1:])
    latest = goals.get_goal(goal["id"])
    assert latest["turns_used"] == 3
    assert latest["status"] == "paused"
    assert latest["last_reason"].startswith("Turn budget reached")
    notes = [row for row in platform.snapshot(CONVERSATION)["rows"] if row.get("note") == "continuation"]
    assert [row["blocks"][0]["text"] for row in notes] == [
        "Goal · turn 1 of 3", "Goal · turn 2 of 3", "Goal · turn 3 of 3",
    ]
    assert all("Goal mode" not in str(row) for row in notes), "the prompt never shows in the transcript"


def test_goal_ends_when_the_verifier_says_it_is_done(goal_setup):
    from row_bot import goals
    platform, verdicts = goal_setup
    verdicts.extend([{"verdict": "continue", "reason": "Two notes left."},
                     {"verdict": "complete", "reason": "All three notes are written."}])
    fake = Recording(*(completed(f"step {index}") for index in range(5)))
    goal = start(platform, fake, max_turns=10)
    wait_idle(platform, fake, 2)
    latest = goals.get_goal(goal["id"])
    assert len(fake.calls) == 2
    assert latest["status"] == "completed"
    assert latest["last_reason"] == "All three notes are written."


def test_an_approval_pauses_the_goal_until_it_is_decided(goal_setup):
    from row_bot import goals
    platform, _ = goal_setup
    fake = Recording(
        (("interrupt", {"__interrupt_id": "goal-approval", "tool": "fixture_tool",
                         "description": "Synthetic approval", "args": {}}),),
        completed("approved step"),
        completed("third step"),
    )
    goal = start(platform, fake, max_turns=3)
    wait_idle(platform, fake, 1)
    assert not platform.registry.active(CONVERSATION)
    waiting = goals.get_goal(goal["id"])
    assert waiting["status"] == "waiting_approval"
    assert len(fake.calls) == 1, "nothing continues while an approval waits"
    from row_bot.tasks import _get_conn
    with _get_conn() as conn:
        approval_id = conn.execute("SELECT id FROM approval_requests WHERE source_thread_id=? AND status='pending'",
                                   (CONVERSATION,)).fetchone()[0]
    platform.execute(owner_id="owner", idempotency_key="goal-approve", target=approval_id,
                     command=command("approval.resolve", "goal-approve", {"decision": "approve"}))
    wait_idle(platform, fake, 3)
    latest = goals.get_goal(goal["id"])
    assert fake.calls[1]["kind"] == "resume" and fake.calls[1]["approved"] is True
    assert latest["turns_used"] == 3
    assert latest["status"] == "paused"


def test_a_denied_approval_stops_the_goal(goal_setup):
    from row_bot import goals
    platform, _ = goal_setup
    fake = Recording(
        (("interrupt", {"__interrupt_id": "goal-deny", "tool": "fixture_tool",
                         "description": "Synthetic approval", "args": {}}),),
        completed("denied step"),
        completed("never runs"),
    )
    goal = start(platform, fake, max_turns=5)
    wait_idle(platform, fake, 1)
    from row_bot.tasks import _get_conn
    with _get_conn() as conn:
        approval_id = conn.execute("SELECT id FROM approval_requests WHERE source_thread_id=? AND status='pending'",
                                   (CONVERSATION,)).fetchone()[0]
    platform.execute(owner_id="owner", idempotency_key="goal-deny", target=approval_id,
                     command=command("approval.resolve", "goal-deny", {"decision": "deny"}))
    wait_idle(platform, fake, 2)
    latest = goals.get_goal(goal["id"])
    assert len(fake.calls) == 2
    assert latest["status"] == "blocked"
    assert latest["last_reason"] == "You denied the approval it needed."


def test_stop_ends_the_goal_loop(goal_setup):
    from row_bot import goals
    platform, _ = goal_setup
    barrier = StreamBarrier(release_on_cancel=True)
    fake = Recording((("token", "Working on it"), barrier), completed("never runs"))
    goal = start(platform, fake, max_turns=5)
    assert barrier.entered.wait(10)
    platform.execute(owner_id="owner", idempotency_key="goal-stop", target=CONVERSATION,
                     command=command("conversation.stop", "goal-stop", {}))
    wait_idle(platform, fake, 1)
    latest = goals.get_goal(goal["id"])
    assert len(fake.calls) == 1
    assert latest["status"] == "paused"
    assert latest["last_reason"] == "You stopped the reply."


def test_pause_keeps_the_running_turn_and_starts_nothing_after_it(goal_setup):
    from row_bot import goals
    from row_bot.application import conversation_followups
    platform, _ = goal_setup
    barrier = StreamBarrier()
    fake = Recording(completed("current step", barrier), completed("never runs"))
    goal = start(platform, fake, max_turns=5)
    assert barrier.entered.wait(10)
    paused = goals.set_goal_status(goal["id"], "paused", reason="Paused by user.", verdict="paused")
    conversation_followups.after_goal_change(platform, CONVERSATION, "pause", paused)
    barrier.release.set()
    wait_idle(platform, fake, 1)
    assert len(fake.calls) == 1
    assert goals.get_goal(goal["id"])["status"] == "paused"


def test_a_restart_pauses_goals_left_working(goal_setup):
    from row_bot import goals
    goal = goals.start_goal(CONVERSATION, "Survive a restart", max_turns=4)
    assert goals.settle_interrupted_goals() == 1
    latest = goals.get_goal(goal["id"])
    assert latest["status"] == "paused"
    assert latest["last_reason"] == "Row-Bot restarted. Resume to continue."
