"""Old tool output is cleared in sticky jumps, not rewritten on every model call (WP A4a).

Every rewrite of an earlier message breaks the provider's prompt cache from that message on. These
cases drive the real Agent preparation over scripted conversations and check that:

* old results are cleared only when the marks jump, in one step, and the jump is rare;
* between jumps each request is an exact prefix of the next, and a cleared result renders the same
  bytes on every later call;
* the marks are kept with the thread and reset when the message they hang on is gone;
* browser snapshots are stubbed on the same jumps;
* a repeated result is stubbed as the newer copy, and the earlier copy never changes;
* proportional shrinking happens only when the newest turn alone reaches the compaction point.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage

import row_bot.agent as agent
from row_bot import history_hygiene
from tests.helpers.agent_requests import (
    UNLIMITED,
    AgentConversation,
    is_prefix,
    text_of_tokens,
    tool_contents,
)

pytestmark = pytest.mark.subsystem


@pytest.fixture(autouse=True)
def thread_db(tmp_path, monkeypatch):
    from row_bot import threads

    monkeypatch.setattr(threads, "DB_PATH", str(tmp_path / "threads.db"))


def _cleared(content: str) -> bool:
    return content.startswith("[read_file]: ")


def _converse(conversation: AgentConversation, turns: int, tokens: int) -> list[dict]:
    """*turns* person turns, each reading one distinct file, and the request after each result."""
    record = []
    for number in range(1, turns + 1):
        conversation.say(f"Read part {number} of the report.")
        call = conversation.tool("read_file", text_of_tokens(f"part {number}", tokens))
        record.append({"turn": number, "call": call, "request": conversation.request()})
        conversation.answer(f"Part {number} read.")
    return record


def test_old_tool_output_is_cleared_only_in_rare_jumps_and_then_stays_the_same(monkeypatch) -> None:
    conversation = AgentConversation(agent, monkeypatch, context_window=40_000)
    per_turn = 3_000
    threshold = history_hygiene.jump_threshold(agent._agent_history_budget_tokens(40_000))
    keep = history_hygiene.KEEP_PERSON_TURNS

    record = _converse(conversation, 11, per_turn)

    jumps = [
        current["turn"]
        for previous, current in zip(record, record[1:])
        if not is_prefix(previous["request"], current["request"])
    ]
    # A jump comes once the output the window left behind is worth a rewrite, not every turn.
    turns_per_jump = -(-threshold // per_turn)
    first_jump = keep + turns_per_jump
    assert jumps == [first_jump, first_jump + turns_per_jump]
    for turn in jumps:
        contents = tool_contents(record[turn - 1]["request"])
        cleared = [_cleared(contents[entry["call"]]) for entry in record[:turn]]
        # One step: everything before the latest person turns; the latest turns stay in full.
        assert cleared == [True] * (turn - keep) + [False] * keep
    # Once cleared, a result renders the same line on every later call, across the next jump too.
    first_result = [tool_contents(entry["request"])[record[0]["call"]] for entry in record[first_jump - 1:]]
    assert set(first_result) == {first_result[0]}
    assert first_result[0].startswith("[read_file]: part 1: the quarterly figures")


def test_the_marks_are_kept_with_the_thread(monkeypatch) -> None:
    from row_bot import threads

    conversation = AgentConversation(agent, monkeypatch, context_window=40_000)
    _converse(conversation, 8, 3_000)

    state = threads.load_history_hygiene(conversation.thread_id)
    assert state["version"] == history_hygiene.STATE_VERSION
    assert state["jumps"] == 1
    assert state["mask"]["id"] == "person-4"  # the latest five person turns stay in full

    # A new process reads the same marks, so its first request clears exactly the same results.
    marks = history_hygiene.load_marks(conversation.thread_id, conversation.state["messages"])
    assert conversation.state["messages"][marks.mask].id == "person-4"


def test_a_mark_resets_when_its_message_is_gone() -> None:
    messages = [HumanMessage(content=f"turn {number}", id=f"person-{number}") for number in range(1, 7)]
    anchor = history_hygiene.anchor(messages, 4)

    assert history_hygiene.resolve(messages, anchor) == 4
    assert history_hygiene.resolve([AIMessage(content="new", id="ai-0"), *messages], anchor) == 5
    assert history_hygiene.resolve(messages[:4], anchor) == 0  # a retry or rewind past it


def test_browser_snapshots_are_stubbed_on_the_same_jumps(monkeypatch) -> None:
    # About stubbing alone: the progress note after silent tool rounds (one request each) is kept out of it.
    monkeypatch.setattr(agent, "SILENT_ROUNDS", 1_000)
    conversation = AgentConversation(agent, monkeypatch, context_window=40_000)
    conversation.say("Walk through the docs pages.")
    requests = []
    calls = []
    for number in range(1, 11):
        calls.append(conversation.tool(
            "browser_snapshot",
            f"URL: https://docs.example.test/page-{number}\nTitle: Page {number}\n"
            + text_of_tokens(f"dom {number}", 2_000),
        ))
        requests.append(conversation.request())

    breaks = [index + 1 for index in range(1, len(requests)) if not is_prefix(requests[index - 1], requests[index])]
    assert breaks == [6, 10]  # batches: never one stub per new snapshot
    after_first = tool_contents(requests[5])
    # (Fenced too wherever the browser tools are registered as returning outside text.)
    stubbed = ["[Prior browser snapshot — URL: https://docs.example.test/" in after_first[call]
               for call in calls[:6]]
    assert stubbed == [True, True, True, True, False, False]
    assert (
        "[Prior browser snapshot — URL: https://docs.example.test/page-1, Title: Page 1. "
        "Full snapshot omitted to save context.]"
    ) in after_first[calls[0]]
    assert "dom 1: the quarterly figures" not in after_first[calls[0]]


def test_a_repeated_result_is_stubbed_as_the_newer_copy(monkeypatch) -> None:
    conversation = AgentConversation(agent, monkeypatch)
    conversation.say("Look up the quarterly figures twice.")
    repeated = text_of_tokens("figures", 400)
    first = conversation.tool("read_file", repeated)
    before = conversation.request()
    second = conversation.tool("read_file", repeated)
    after = conversation.request()

    assert is_prefix(before, after)  # the earlier copy is never rewritten
    contents = tool_contents(after)
    assert contents[first] == repeated
    assert contents[second] == f"[Identical to the result of call {first} (read_file) above; not repeated.]"


def test_proportional_shrinking_happens_only_when_the_newest_turn_reaches_the_limit(monkeypatch) -> None:
    exports = {number: text_of_tokens(f"export {number}", 8_000) for number in (1, 2, 3)}

    def newest_turn_reads_three_exports(compact_at: int) -> dict[str, str]:
        conversation = AgentConversation(agent, monkeypatch, context_window=40_000, compact_at=compact_at)
        conversation.say("Reconcile the three exports.")
        calls = {number: conversation.tool("read_file", text) for number, text in exports.items()}
        contents = tool_contents(conversation.request())
        return {number: contents[call] for number, call in calls.items()}

    # Below the compaction point the results go in full, though they pass the old 65% tool budget.
    below = newest_turn_reads_three_exports(UNLIMITED)
    assert below == exports
    # The newest turn alone reaches it (compaction can't bring it back): every result is cut once.
    over = newest_turn_reads_three_exports(20_000)
    for number, text in over.items():
        assert text.endswith("[Tool result shortened to fit the context token budget]")
        assert exports[number].startswith(text.split("\n\n[Tool result shortened")[0])


def test_a_long_turn_sends_only_its_newest_reasoning_once_it_reaches_the_limit(monkeypatch) -> None:
    """30 Oct demo run: one turn of a thinking model carried ~27k tokens of its steps' reasoning, which compaction
    can't shorten (it is the turn in flight), until the request no longer fit."""

    def a_turn_of_reasoned_steps(compact_at: int) -> list:
        conversation = AgentConversation(agent, monkeypatch, context_window=40_000, compact_at=compact_at)
        conversation.say("Make the demo video.")
        for step in range(1, 5):
            conversation.tool("read_file", f"notes {step}")
            ai = conversation.state["messages"][-2]
            ai.additional_kwargs["reasoning_content"] = text_of_tokens(f"plan {step}", 3_000)
        return [message for message in conversation.request() if isinstance(message, AIMessage)]

    assert all(message.additional_kwargs.get("reasoning_content") for message in a_turn_of_reasoned_steps(UNLIMITED))
    steps = a_turn_of_reasoned_steps(10_000)
    assert [bool(message.additional_kwargs.get("reasoning_content")) for message in steps] == [
        False, False, False, True], "only the newest step's reasoning is sent"


def test_an_earlier_turns_reasoning_is_never_sent_again(monkeypatch) -> None:
    conversation = AgentConversation(agent, monkeypatch)
    conversation.say("Plan the video.")
    conversation.answer("Here's the plan.")
    conversation.state["messages"][-1].additional_kwargs["reasoning_content"] = text_of_tokens("plan", 2_000)
    conversation.say("Now make it.")
    conversation.tool("read_file", "notes")
    conversation.state["messages"][-2].additional_kwargs["reasoning_content"] = "Reading the notes first."

    sent = [message for message in conversation.request() if isinstance(message, AIMessage)]

    assert [message.additional_kwargs.get("reasoning_content") for message in sent] == [
        None, "Reading the notes first."]
    assert conversation.state["messages"][1].additional_kwargs["reasoning_content"], "the conversation keeps it"


def test_large_results_in_older_turns_wait_for_compaction_instead_of_shrinking(monkeypatch) -> None:
    conversation = AgentConversation(agent, monkeypatch, context_window=40_000, compact_at=20_000)
    conversation.say("Reconcile the three exports.")
    calls = [conversation.tool("read_file", text_of_tokens(f"export {number}", 8_000)) for number in (1, 2, 3)]
    conversation.answer("Reconciled.")
    conversation.say("Thanks, what changed?")

    contents = tool_contents(conversation.request())

    assert conversation.summarizer.requests  # compaction is asked to make the room (it fails here)
    assert all("[Tool result shortened" not in contents[call] for call in calls)


def test_the_compaction_point_clears_old_output_before_a_summary_is_needed(monkeypatch) -> None:
    # Seven turns of 2,000 tokens stay far below a 200K window's batch threshold.
    probe = AgentConversation(agent, monkeypatch)
    sizes = [agent._count_prepared_tokens(entry["request"], ()) for entry in _converse(probe, 7, 2_000)]
    assert not any(_cleared(text) for text in tool_contents(probe.request()).values())

    # The same conversation where the last request of turn 7 reaches the compaction point.
    conversation = AgentConversation(agent, monkeypatch, compact_at=sizes[-1])
    record = _converse(conversation, 7, 2_000)

    for entry in record[:-1]:
        assert not any(_cleared(text) for text in tool_contents(entry["request"]).values())
    last = record[-1]["request"]
    # Clear first (D2): everything before the latest five turns, and the request is back below it.
    assert [_cleared(tool_contents(last)[entry["call"]]) for entry in record] == [True, True] + [False] * 5
    assert agent._count_prepared_tokens(last, ()) < sizes[-1]


def test_results_behind_a_compaction_summary_never_count_towards_a_jump(monkeypatch) -> None:
    conversation = AgentConversation(agent, monkeypatch, context_window=40_000)
    record = _converse(conversation, 9, 3_000)
    boundary = next(index for index, message in enumerate(conversation.state["messages"])
                    if getattr(message, "id", "") == "person-5")
    summary = {"schema_version": 1, "mode": "agent", "boundary_message_count": boundary,
               "boundary_digest": "0" * 64, "summary": "## Current Goal\nRead the report."}
    monkeypatch.setattr(agent, "_agent_summary_state", lambda messages: summary)
    fresh = AgentConversation(agent, monkeypatch, context_window=40_000)
    fresh.state = {"messages": list(conversation.state["messages"]),
                   "execution_budget": agent.new_execution_budget("after-summary")}

    contents = tool_contents(fresh.request())

    # Turns 1-4 are behind the summary; nothing after it has left the latest five turns.
    assert not any(_cleared(contents[entry["call"]]) for entry in record)
