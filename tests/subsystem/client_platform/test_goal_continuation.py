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
    assert latest["last_reason"] == "Reached its limit of 3 turns. Resume to keep going."
    assert "Turn budget: 1/3" in fake.prompts[1], "a set limit is in the prompt"
    notes = [row for row in platform.snapshot(CONVERSATION)["rows"] if row.get("note") == "continuation"]
    assert [row["blocks"][0]["text"] for row in notes] == [
        "Goal · turn 1 of 3", "Goal · turn 2 of 3", "Goal · turn 3 of 3",
    ]
    assert all("Goal mode" not in str(row) for row in notes), "the prompt never shows in the transcript"


def test_a_goal_without_a_turn_limit_never_pauses_for_turns(goal_setup):
    """B243: goals stopped after a fixed number of turns; by default none."""
    from row_bot import goals
    from row_bot.application import conversation_followups
    platform, verdicts = goal_setup
    verdicts.extend([{"verdict": "continue", "reason": "More to do."}] * 2
                    + [{"verdict": "complete", "reason": "All notes are written."}])
    fake = Recording(*(completed(f"step {index}") for index in range(6)))
    platform.stream_factory = fake.stream
    platform.resume_factory = fake.resume
    goal = goals.start_goal(CONVERSATION, "Write synthetic notes until they are done")
    conversation_followups.after_goal_change(platform, CONVERSATION, "start", goal)
    wait_idle(platform, fake, 3)
    latest = goals.get_goal(goal["id"])
    assert latest["max_turns"] == 0, "no limit unless someone sets one"
    assert (latest["status"], latest["turns_used"]) == ("completed", 3)
    assert not any("Turn budget" in prompt for prompt in fake.prompts)
    notes = [row for row in platform.snapshot(CONVERSATION)["rows"] if row.get("note") == "continuation"]
    assert [row["blocks"][0]["text"] for row in notes] == [
        "Goal · turn 1", "Goal · turn 2", "Goal · turn 3",
    ]


def test_a_goal_that_stops_making_progress_pauses_with_the_reason(goal_setup):
    """B244: two turns in a row without progress pause the goal for the person."""
    from row_bot import goals
    platform, verdicts = goal_setup
    verdicts.extend([{"progress": "no_progress", "reason": "Searched the same folder again."}] * 2)
    fake = Recording(*(completed(f"step {index}") for index in range(4)))
    goal = start(platform, fake, max_turns=0)
    wait_idle(platform, fake, 2)
    latest = goals.get_goal(goal["id"])
    assert len(fake.calls) == 2, "nothing runs after the goal stalls"
    assert latest["status"] == "paused"
    assert latest["last_reason"] == "No progress in the last 2 turns: Searched the same folder again."


def test_a_usage_limit_waits_for_the_reset_and_then_continues(goal_setup, monkeypatch):
    """B244: a provider limit that says when it resets makes the goal wait, then go on."""
    from row_bot import goals
    from row_bot.application import conversation_followups
    platform, verdicts = goal_setup
    waits: list[tuple[float, object]] = []
    monkeypatch.setattr(conversation_followups, "_schedule", lambda delay, run: waits.append((delay, run)))
    verdicts.append({"verdict": "complete", "reason": "Notes written."})
    limited = ("error", "⚠️ Rate limit reached — please wait a moment and try again. "
                        "The provider says: try again in 1m30s.")
    fake = Recording((limited,), completed("after the reset"))
    goal = start(platform, fake, max_turns=0)
    wait_idle(platform, fake, 1)
    waiting = goals.get_goal(goal["id"])
    assert waiting["status"] == "active"
    assert waiting["last_reason"] == "The provider's usage limit was reached. Continuing in about 2 minutes."
    assert [delay for delay, _run in waits] == [90.0]

    waits[0][1]()  # The fake clock reaches the reset time.
    wait_idle(platform, fake, 2)
    assert goals.get_goal(goal["id"])["status"] == "completed"
    assert fake.prompts[1].startswith("[Goal continuation]")


def test_a_usage_limit_without_a_reset_time_pauses_and_says_so(goal_setup, monkeypatch):
    from row_bot import goals
    from row_bot.application import conversation_followups
    platform, _ = goal_setup
    waits: list[float] = []
    monkeypatch.setattr(conversation_followups, "_schedule", lambda delay, run: waits.append(delay))
    limited = ("error", "⚠️ API error: Claude subscription rate/usage limit reached: {\"type\": \"error\"}")
    fake = Recording((limited,), completed("never runs"))
    goal = start(platform, fake, max_turns=0)
    wait_idle(platform, fake, 1)
    latest = goals.get_goal(goal["id"])
    assert (latest["status"], latest["last_reason"]) == (
        "paused", "The provider's rate or usage limit stopped the goal. Resume it once the limit resets.")
    assert waits == [] and len(fake.calls) == 1


def test_the_goal_counts_the_tokens_its_turns_used(goal_setup):
    """B244: the card shows the tokens the goal used, turn after turn."""
    from row_bot import goals
    platform, verdicts = goal_setup
    verdicts.extend([{"verdict": "continue", "reason": "One more step."},
                     {"verdict": "complete", "reason": "Both steps are done."}])

    def with_usage(label: str, tokens: int):
        native_id = fixture_id(label + ":assistant")
        message = AIMessage(content=label, id=native_id, usage_metadata={
            "input_tokens": tokens - 100, "output_tokens": 100, "total_tokens": tokens})
        return (CheckpointCommit((message,), native_id), ("done", label))

    fake = Recording(with_usage("step 0", 1200), with_usage("step 1", 800))
    goal = start(platform, fake, max_turns=0)
    wait_idle(platform, fake, 2)
    assert goals.get_goal(goal["id"])["tokens_used"] == 2000


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


def test_an_active_goal_continues_after_a_restart_from_its_last_saved_turn(goal_setup):
    """B244: overnight goals keep going when Row-Bot restarts."""
    from row_bot import goals, tasks
    from row_bot.application import conversation_followups
    platform, verdicts = goal_setup
    goal = goals.start_goal(CONVERSATION, "Survive a restart")
    goals.update_goal_progress(goal_id=goal["id"], progress="Two of three notes written.")
    channel_goal = goals.start_goal("channel-thread", "Answer on the channel")
    tasks.record_thread_channel_ref("channel-thread", channel="sms", target="fixture-chat")
    verdicts.append({"verdict": "complete", "reason": "All three notes are written."})
    fake = Recording(completed("third note"))
    platform.stream_factory = fake.stream
    platform.resume_factory = fake.resume

    # A new process: nothing is pending in memory, then start-up runs.
    conversation_followups._PENDING.clear()
    goals.settle_interrupted_goals()
    assert conversation_followups.continue_goals_after_restart(platform) == 1
    wait_idle(platform, fake, 1)

    assert fake.prompts[0].startswith("[Goal continuation]")
    assert "Two of three notes written." in fake.prompts[0]
    assert goals.get_goal(goal["id"])["status"] == "completed"
    # The channel's own runtime continues channel goals; here they pause.
    channel = goals.get_goal(channel_goal["id"])
    assert (channel["status"], channel["last_reason"]) == ("paused", "Row-Bot restarted. Resume to continue.")


def test_the_agent_graph_input_keeps_the_follow_up_note(goal_setup):
    """The real graph re-adds the admitted input by id; the follow-up note must
    ride on it or the transcript shows the note as the person's bubble."""
    from row_bot import goals
    from row_bot.agent import _new_agent_graph_input
    from row_bot.application import conversation_followups
    platform, _ = goal_setup
    fake = Recording(completed("step 0"))
    graph_inputs = []

    def provider(text, tools, config, **kwargs):
        graph_inputs.append(_new_agent_graph_input(text, config)[1])
        yield from fake.stream(text, tools, config, **kwargs)

    platform.stream_factory = provider
    platform.resume_factory = fake.resume
    goal = goals.start_goal(CONVERSATION, "Write one synthetic note", max_turns=1)
    conversation_followups.after_goal_change(platform, CONVERSATION, "start", goal)
    wait_idle(platform, fake, 1)
    human = graph_inputs[0]["messages"][0]
    assert human.content.startswith("[Goal mode started]")
    assert human.additional_kwargs["platform_note"] == "continuation"
    assert human.additional_kwargs["platform_public_content"] == "Goal · turn 1 of 1"


def test_the_turn_that_finishes_a_goal_counts(goal_setup):
    """The model can mark the goal done with its goal tool during a turn; that
    turn still counts ("Done · turn 1 of 3", not "turn 0"), while a later chat
    turn after the goal ended does not."""
    from row_bot import goals
    platform, _ = goal_setup

    class FinishesGoal(Recording):
        def stream(self, text, enabled_tools, config, *, stop_event=None):
            if len(self.prompts) == 0:
                current = goals.get_current_goal(CONVERSATION)
                goals.set_goal_status(current["id"], "completed", reason="All three notes are written.",
                                      verdict="complete")
            yield from super().stream(text, enabled_tools, config, stop_event=stop_event)

    fake = FinishesGoal(completed("step 0"), completed("chat after"))
    goal = start(platform, fake, max_turns=3)
    wait_idle(platform, fake, 1)
    latest = goals.get_goal(goal["id"])
    assert latest["status"] == "completed"
    assert latest["turns_used"] == 1, "the turn that finished the goal counts"
    assert latest["last_reason"] == "All three notes are written."
    platform.execute(owner_id="fixture", idempotency_key="chat-after", target=CONVERSATION,
                     command=command("conversation.submit", "chat-after", {
                         "text": "Thanks", "submission_id": "chat-after",
                         "model_selection": {"provider_id": "fixture", "model_ref": "fixture::model"}}))
    wait_idle(platform, fake, 2)
    assert goals.get_goal(goal["id"])["turns_used"] == 1, "a chat turn after the goal ended is not counted"


def answer(platform, text: str, label: str) -> None:  # noqa: F811
    platform.execute(owner_id="fixture", idempotency_key=label, target=CONVERSATION,
                     command=command("conversation.submit", label, {
                         "text": text, "submission_id": label,
                         "model_selection": {"provider_id": "fixture", "model_ref": "fixture::model"}}))


def test_answering_the_goals_question_resumes_it_and_the_verifier_judges_the_answer(goal_setup):
    """B318: the person's message is the answer; the goal needs no Resume click."""
    from row_bot import goals
    platform, verdicts = goal_setup
    verdicts.extend([{"progress": "blocked", "reason": "Needs the launch date."},
                     {"progress": "done", "reason": "The X post uses 18 October."}])
    fake = Recording(completed("Which launch date?"), completed("Here is the post for 18 October."))
    goal = start(platform, fake, max_turns=10)
    wait_idle(platform, fake, 1)
    assert goals.get_goal(goal["id"])["status"] == "blocked"

    answer(platform, "Launch is 18 October.", "goal-answer")
    wait_idle(platform, fake, 2)

    latest = goals.get_goal(goal["id"])
    assert (latest["status"], latest["last_reason"]) == ("completed", "The X post uses 18 October.")


def test_a_paused_goal_stays_paused_when_the_person_chats(goal_setup):
    from row_bot import goals
    platform, _ = goal_setup
    fake = Recording(completed("step 0"), completed("chat"))
    goal = start(platform, fake, max_turns=1)
    wait_idle(platform, fake, 1)
    assert goals.get_goal(goal["id"])["status"] == "paused"
    answer(platform, "What did you do so far?", "goal-chat")
    wait_idle(platform, fake, 2)
    assert goals.get_goal(goal["id"])["status"] == "paused"


def test_a_goal_waiting_on_the_person_starts_no_hand_off_until_they_answer(goal_setup):
    """B316: no "Continuing in <design>" turn runs while the goal reads Needs you."""
    from row_bot import goals
    from row_bot.application import conversation_followups
    platform, verdicts = goal_setup
    verdicts.extend([{"progress": "blocked", "reason": "Needs the launch date."},
                     {"progress": "progress", "reason": "Has the date."},
                     {"progress": "done", "reason": "Page and post are ready."}])
    hand_off = conversation_followups.Followup("resource", "Build the landing page now.",
                                               "Continuing in Launch page")

    class SetsUpADesign(Recording):
        def stream(self, text, enabled_tools, config, *, stop_event=None):
            if not self.prompts:
                conversation_followups.schedule(CONVERSATION, hand_off)
            yield from super().stream(text, enabled_tools, config, stop_event=stop_event)

    fake = SetsUpADesign(completed("Which launch date?"), completed("Noted."), completed("Page built."))
    goal = start(platform, fake, max_turns=10)
    wait_idle(platform, fake, 1)
    threading.Event().wait(0.3)
    assert len(fake.calls) == 1 and goals.get_goal(goal["id"])["status"] == "blocked"
    assert conversation_followups.pending(CONVERSATION) == hand_off

    answer(platform, "Launch is 18 October.", "goal-answer-design")
    wait_idle(platform, fake, 3)

    assert fake.prompts[2] == "Build the landing page now."
    assert goals.get_goal(goal["id"])["status"] == "completed"
