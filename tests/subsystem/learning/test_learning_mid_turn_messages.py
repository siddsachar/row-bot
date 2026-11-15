"""What the person sends while a turn works, read at its next step (5.3.1), gives Learning the same signals.

Before 5.3.1 such a message waited and started a turn of its own after the reply. Now the running turn reads
it at its next step: a plain human message with the person's submission id and ``mid_turn`` set, added after
the tool results of the step it arrived during (one read before the turn's first step goes after the message
the turn started with). Each scenario here runs both ways through Capture's seams, and the next-message
corrections, withdrawals, held-out corrections (A3: the accepted redo's first move as the reference) and
training rows come out the same."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from row_bot.learning import capture, corrections, evaluation
from row_bot.learning.evaluation import Arm
from row_bot.learning.store import LearningStore
from row_bot.learning.training import dataset, recipes
from tests.fixtures.learning_eval import ScriptedClient

pytestmark = [pytest.mark.subsystem]

HOUR = 3600.0
MODEL = "gpt-5.6-sol"
TOOLS = [{"type": "function", "function": {
    "name": "calculate", "description": "Evaluate a math expression.",
    "parameters": {"type": "object", "properties": {"expression": {"type": "string"}}, "required": ["expression"]}}}]
MODES = ("next_turn", "mid_turn")
CONTEXT = "\n\n<row_bot_turn_context>\nNow: 10:00\n</row_bot_turn_context>"


@dataclass(frozen=True)
class Step:
    expression: str


@dataclass(frozen=True)
class Said:
    """The person's messages, read at this step."""

    texts: tuple[str, ...]
    marked: bool = True


class Chat:
    """One conversation through Capture's seams. Each request carries the conversation so far; a message
    read mid-turn is added as 5.3.1 adds it, after the step's tool results and before the next request."""

    SYSTEM = SystemMessage(content="You are Row-Bot. Use tools when they help.", id="sys")

    def __init__(self, writer, chats, clock, thread_id: str) -> None:
        self.writer, self.clock, self.thread_id = writer, clock, thread_id
        self.history: list = []
        self.count = 0
        chats.chat(thread_id)

    def _id(self, prefix: str) -> str:
        self.count += 1
        return f"{prefix}-{self.thread_id}-{self.count}"

    def _sent(self, text: str, *, marked: bool = True, context: str = "") -> HumanMessage:
        return HumanMessage(content=text + context, id=self._id("sub"),
                            additional_kwargs={"mid_turn": True} if marked else {})

    def turn(self, question: str, *moves, early: tuple[str, ...] = (), extra: tuple = ()) -> list[str]:
        """A turn the person started with ``question``: ``moves`` are calculator steps, messages read
        mid-turn and, last, the answer. ``early``: messages read before its first step (the turn context
        folded into the last, as the request sends it). ``extra``: Row-Bot's own messages its later requests
        add. Returns its model calls' decision ids, in order."""

        turn = self.writer.begin_turn(thread_id=self.thread_id, surface="normal_chat", person_message=True)
        self.history.append(HumanMessage(content=question + ("" if early else CONTEXT), id=self._id("h")))
        self.history += [self._sent(text, context=CONTEXT if number == len(early) - 1 else "")
                         for number, text in enumerate(early)]
        ids: list[str] = []
        for move in moves:
            if isinstance(move, Said):
                self.history += [self._sent(text, marked=move.marked) for text in move.texts]
                continue
            sent = [self.SYSTEM, *self.history, *(extra if ids else ())]
            if isinstance(move, Step):
                call = {"id": self._id("call"), "name": "calculate", "args": {"expression": move.expression}}
                step = AIMessage(content="", id=self._id("a"), tool_calls=[call])
                ids.append(self.writer.model_call(turn, messages=sent, response=step, tools=TOOLS, model_ref=MODEL,
                                                  provider_id="openai"))
                result = ToolMessage(content=f"{move.expression} worked out", tool_call_id=call["id"],
                                     id=self._id("t"))
                self.writer.tool_call(turn, call=call, result=result)
                self.history += [step, result]
            else:
                reply = AIMessage(content=move, id=self._id("r"))
                ids.append(self.writer.model_call(turn, messages=sent, response=reply, tools=TOOLS, model_ref=MODEL,
                                                  provider_id="openai"))
                self.history.append(reply)
            self.clock.advance(1)
        self.writer.end_turn(turn, "completed")
        self.writer.drain()
        self.clock.advance(60)
        return ids


def _price(chat: Chat, mode: str, follow_ups: list[str], *, item: str = "boxes") -> tuple[str, str]:
    """The person asks for a price, Row-Bot's first step multiplies the wrong numbers, and ``follow_ups``
    come next: as turns of their own after its answer, or read at its next step. Returns the last model call
    before them and the first after them."""

    question = f"Price for 6 {item} at 8?"
    if mode == "next_turn":
        first = chat.turn(question, Step("6 * 7"), "It's 42.")
        after = chat.turn(follow_ups[0], Step("6 * 8"), "It's 48.")
        for text in follow_ups[1:]:
            chat.turn(text, "OK.")
        return first[-1], after[0]
    ids = chat.turn(question, Step("6 * 7"), Said(tuple(follow_ups)), Step("6 * 8"), "It's 48.")
    return ids[0], ids[1]


def _calls(expression: str):
    return lambda messages, tools, model_ref: AIMessage(content="", tool_calls=[
        {"name": "calculate", "args": {"expression": expression}, "id": "c1", "type": "tool_call"}])


# (corrects the step before it, withdrawn, ambiguous)
FOLLOW_UPS = {
    "correction": (["No, boxes are 8 each."], (True, 0, 0)),
    "not a correction": (["Thanks! Can you also add the totals?"], (False, 0, 0)),
    "ambiguous": (["Actually, can you also include Mondays?"], (False, 0, 1)),
    "withdrawn with it": (["No, boxes are 8 each.", "Never mind, you were right."], (False, 1, None)),
}


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("name", list(FOLLOW_UPS))
def test_a_message_read_mid_turn_is_judged_as_the_same_message_as_a_next_turn(writer, chats, learning_on, clock,
                                                                              mode, name):
    follow_ups, (corrects, withdrawn, ambiguous) = FOLLOW_UPS[name]
    before, _after = _price(Chat(writer, chats, clock, "chat-1"), mode, follow_ups)
    store = LearningStore(learning_on, clock=clock)
    clock.advance(HOUR)

    report = corrections.confirm(store)

    assert report.confirmed == ((before,) if corrects else ())
    assert report.withdrawn == withdrawn
    if ambiguous is not None:
        assert report.ambiguous == ambiguous


@pytest.mark.parametrize("mode", MODES)
def test_a_held_out_correction_read_mid_turn_is_checked_against_the_same_redo(writer, chats, learning_on, clock,
                                                                             mode):
    moves = [_price(Chat(writer, chats, clock, f"chat-{item}"), mode, [f"No, {item} are 8 each."], item=item)
             for item in ("boxes", "crates")]
    store = LearningStore(learning_on, clock=clock)
    clock.advance(HOUR)

    report = evaluation.refresh_audit(store=store, asked_by="nightly")

    assert (report["corrections_added"], report["corrections_held"], report["corrections_waiting"]) == (1, 1, 0)
    book = corrections.Book(store)
    for before, after in moves:  # the redo is the work after the correction; its first move is the reference
        redo = corrections.accepted_redo(store, book.decisions[before], book=book)
        assert redo is not None and corrections.first_call(book, redo).id == after
    for respond, passed in ((_calls("6 * 8"), 1), (_calls("6 * 7"), 0)):
        result = evaluation.audit_check([Arm("main")], asked_by="test", client=ScriptedClient(respond), packs=(),
                                        store=store, save=False, repeats=1, corrected=True)
        [arm] = result.arms
        assert (arm.passed, arm.total) == (passed, 1)


def _targets(compiled) -> list[list[str]]:
    rows = []
    for item in (*compiled.train, *compiled.validation):
        rows.append([str(message.get("content") or "") or message["tool_calls"][0]["function"]["arguments"]
                     for message in item.row["messages"] if message["role"] == "assistant" and message.get("weight")])
    return sorted(rows)


@pytest.mark.parametrize("mode", MODES)
def test_steps_a_mid_turn_correction_interrupted_are_never_trained_and_the_redo_is(writer, chats, learning_on,
                                                                                   clock, mode):
    _price(Chat(writer, chats, clock, "chat-1"), mode, ["No, boxes are 8 each."])
    store = LearningStore(learning_on, clock=clock)
    clock.advance(HOUR)

    compiled = dataset.compile_dataset(store, recipes.load("16gb"), lookup=chats, refresh_vault=False,
                                       shape="captured")

    assert compiled.left_out.get("corrected_or_stopped") == 1
    assert _targets(compiled) == [['{"expression": "6 * 8"}', "It's 48."]]


@pytest.mark.parametrize("mode", MODES)
def test_steps_before_a_mid_turn_message_that_corrects_nothing_still_train(writer, chats, learning_on, clock, mode):
    _price(Chat(writer, chats, clock, "chat-1"), mode, ["Thanks! Can you also add the totals?"])
    store = LearningStore(learning_on, clock=clock)
    clock.advance(HOUR)

    compiled = dataset.compile_dataset(store, recipes.load("16gb"), lookup=chats, refresh_vault=False,
                                       shape="captured")

    targets = [target for row in _targets(compiled) for target in row]
    assert '{"expression": "6 * 7"}' in targets and '{"expression": "6 * 8"}' in targets
    assert not compiled.left_out.get("corrected_or_stopped")


@pytest.mark.parametrize("marked", [True, False])
def test_a_message_read_mid_turn_starts_the_next_episode_after_the_step_before_it(writer, chats, learning_on, clock,
                                                                                  marked):
    # Without its marker (a copy that lost it) the message is still found by where it is.
    ids = Chat(writer, chats, clock, "chat-1").turn("Price for 6 boxes at 8?", Step("6 * 7"),
                                                    Said(("No, boxes are 8 each.",), marked=marked), Step("6 * 8"),
                                                    "It's 48.")
    store = LearningStore(learning_on, clock=clock)

    first, second = sorted(store.find("episode"), key=lambda item: item.started_at)
    decisions = {item.id: item for item in store.find("decision")}
    assert (first.status, second.status) == ("completed", "completed")
    assert first.ended_at == second.started_at and second.follows_decision_id == ids[0]
    assert decisions[ids[0]].episode_id == first.id
    assert {decisions[item].episode_id for item in ids[1:]} == {second.id}
    tools = [item for item in decisions.values() if item.kind == "tool_call"]
    assert sorted(item.episode_id == first.id for item in tools) == [False, True]  # each step's tool with it


def _step(writer, turn, sent: list, number: int, *, status: str = "ok", retry_of: str | None = None):
    call = {"id": f"call-{number}", "name": "calculate", "args": {"expression": f"{number} + 1"}}
    step = AIMessage(content="", id=f"a-{number}", tool_calls=[call])
    decision = writer.model_call(turn, messages=sent, response=step if status == "ok" else None, tools=TOOLS,
                                 model_ref=MODEL, provider_id="openai", status=status, retry_of=retry_of)
    if status != "ok":
        return decision, []
    result = ToolMessage(content=f"{number + 1}", tool_call_id=call["id"], id=f"t-{number}")
    writer.tool_call(turn, call=call, result=result)
    return decision, [step, result]


def _answer(writer, turn, sent: list, number: int) -> AIMessage:
    reply = AIMessage(content=f"Done ({number}).", id=f"r-{number}")
    writer.model_call(turn, messages=sent, response=reply, tools=TOOLS, model_ref=MODEL, provider_id="openai")
    return reply


def ordinary_turns(writer, chats, clock) -> int:
    """Turns of every ordinary kind, none with a message read mid-turn, through Capture's seams as the agent
    sends them. Returns how many turns there were."""

    from row_bot.learning.scope import ThreadInfo

    def folded(message: HumanMessage, context: str = CONTEXT) -> HumanMessage:
        # turn_context.merge_into_preceding: a new copy each call, the same id, the content changed.
        return message.model_copy(update={"content": f"{message.content}{context}"})

    def context_alone(turn_id: str) -> HumanMessage:
        # turn_context.provider_message: a context that follows no message of the person's, sent unmarked.
        return HumanMessage(content=f"<row_bot_turn_context>\nNow: 10:0{len(turn_id)}\n</row_bot_turn_context>",
                            id=f"row-bot-turn-context-{turn_id}")

    system = Chat.SYSTEM
    chats.chat("chat-1").chat("chat-2").child("agent-1", "chat-1")
    chats.threads["workflow-1"] = ThreadInfo(exists=True, is_workflow=True)
    history: list = []

    # 1. A chat turn: the context folded into its message on every call, a refused call retried, a progress
    #    note on the last call.
    turn = writer.begin_turn(thread_id="chat-1", surface="normal_chat", person_message=True)
    opening = HumanMessage(content="Add up the March invoices.", id="h-1")
    _first, done = _step(writer, turn, [system, folded(opening)], 1)
    refused, _ = _step(writer, turn, [system, folded(opening), *done], 2, status="error")
    _second, more = _step(writer, turn, [system, folded(opening), *done], 2, retry_of=refused)
    note = HumanMessage(content="Row-Bot note, not from the person: say what you found.",
                        additional_kwargs={"ephemeral": True, "row_bot_note": "progress"})
    reply = _answer(writer, turn, [system, folded(opening), *done, *more, note], 3)
    writer.end_turn(turn, "completed")
    history += [opening, *done, *more, reply]
    clock.advance(60)

    # 2. A compaction mid-turn: the summary, then the context again on its own after the latest message.
    turn = writer.begin_turn(thread_id="chat-1", surface="normal_chat", person_message=True)
    opening = HumanMessage(content="Now April.", id="h-2")
    _first, done = _step(writer, turn, [system, *history, folded(opening)], 4)
    summary = HumanMessage(content='<HISTORICAL_CONTEXT untrusted="true">\nMarch: 3.\n</HISTORICAL_CONTEXT>')
    guidance = SystemMessage(content="The summary is background.")
    reply = _answer(writer, turn, [system, guidance, summary, folded(opening), *done, context_alone("t2-1")], 5)
    writer.end_turn(turn, "completed")
    history += [opening, *done, reply]
    clock.advance(60)

    # 3. A browser step: the page's picture after it (no id).
    turn = writer.begin_turn(thread_id="chat-1", surface="normal_chat", person_message=True)
    opening = HumanMessage(content="Open the settings page.", id="h-3")
    _first, done = _step(writer, turn, [system, *history, folded(opening)], 6)
    picture = HumanMessage(content=[{"type": "text", "text": "The browser page after that action."},
                                    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,/9j/AAAA"}}])
    reply = _answer(writer, turn, [system, *history, folded(opening), *done, picture], 7)
    writer.end_turn(turn, "completed")
    history += [opening, *done, reply]
    clock.advance(60)

    # 4. A delegated agent: its task, then its parent's guidance while it works.
    turn = writer.begin_turn(thread_id="agent-1", surface="agent_child", agent_run_id="run-1")
    task = HumanMessage(content="Find the April invoices.", id="task-1",
                        additional_kwargs={"platform_note": "agent_task", "platform_public_content": "Find them"})
    _first, done = _step(writer, turn, [system, task, context_alone("t4")], 8)
    guide = HumanMessage(content="Only the paid ones.", id="guide-1", additional_kwargs={"platform_note": "agent_guidance"})
    _answer(writer, turn, [system, task, context_alone("t4"), *done, guide], 9)
    writer.end_turn(turn, "completed")
    clock.advance(60)

    # 5. A goal run Row-Bot started: its event, then the context on its own.
    turn = writer.begin_turn(thread_id="chat-1", surface="goal", goal=True)
    event = HumanMessage(content="Carry on with the goal.", id="event-1",
                         additional_kwargs={"row_bot_ui": {"thread_event": True}})
    _first, done = _step(writer, turn, [system, *history, event, context_alone("t5")], 10)
    reply = _answer(writer, turn, [system, *history, event, context_alone("t5"), *done], 11)
    writer.end_turn(turn, "completed")
    history += [event, *done, reply]
    clock.advance(60)

    # 6. A workflow run: its first message is the workflow's own.
    turn = writer.begin_turn(thread_id="workflow-1", surface="workflow")
    prompt = HumanMessage(content="Send the weekly summary.", id="workflow-message-1")
    _first, done = _step(writer, turn, [system, folded(prompt)], 12)
    _answer(writer, turn, [system, folded(prompt), *done], 13)
    writer.end_turn(turn, "completed")
    clock.advance(60)

    # 7. A turn that waits at an approval and carries on once it is given.
    turn = writer.begin_turn(thread_id="chat-2", surface="normal_chat", person_message=True)
    opening = HumanMessage(content="Archive the newsletter.", id="h-7")
    _first, done = _step(writer, turn, [system, folded(opening)], 14)
    writer.end_turn(turn, "waiting")
    clock.advance(30)
    turn = writer.begin_turn(thread_id="chat-2", surface="normal_chat", resumed=True)
    reply = _answer(writer, turn, [system, folded(opening), *done], 15)
    writer.end_turn(turn, "completed")
    clock.advance(60)

    # 8. Retry: the same turn again in place, its message the same.
    writer.retry("chat-2", [reply.id])
    turn = writer.begin_turn(thread_id="chat-2", surface="normal_chat", person_message=True)
    _first, done = _step(writer, turn, [system, folded(opening)], 16)
    _answer(writer, turn, [system, folded(opening), *done], 17)
    writer.end_turn(turn, "completed")
    writer.drain()
    return 8


def test_ordinary_turns_are_captured_one_episode_each(writer, chats, learning_on, clock):
    # A turn with no message read mid-turn is captured as before 5.3.1: one episode, holding all it did.
    turns = ordinary_turns(writer, chats, clock)
    store = LearningStore(learning_on, clock=clock)

    episodes = store.find("episode")
    decisions = store.find("decision")
    assert len(episodes) == turns
    assert {item.episode_id for item in decisions} == {item.id for item in episodes}
    by_thread = sorted((item for item in episodes if item.thread_id == "chat-1"), key=lambda item: item.started_at)
    assert [item.follows_decision_id is not None for item in by_thread] == [False, True, True, False]
    book = corrections.Book(store)
    for episode in episodes:  # what each started with reads as it did
        first = book.calls[episode.id][0]
        assert book.text_of(episode) == corrections.person_text(capture.read_request(store, first))


def test_only_the_persons_own_messages_start_the_next_episode(writer, chats, learning_on, clock):
    # Row-Bot's own human-role messages in a later request (a progress note, a compaction summary) don't.
    note = HumanMessage(content="Row-Bot note, not from the person: say what you found.",
                        additional_kwargs={"ephemeral": True, "row_bot_note": "progress"})
    summary = HumanMessage(content='<HISTORICAL_CONTEXT untrusted="true">\nEarlier work.\n</HISTORICAL_CONTEXT>')
    Chat(writer, chats, clock, "chat-1").turn("Price for 6 boxes at 8?", Step("6 * 7"), Step("6 * 8"), "It's 48.",
                                              extra=(summary, note))

    assert len(LearningStore(learning_on, clock=clock).find("episode")) == 1


# (the opening corrects the reply before it, withdrawn)
EARLY = {
    "correction": (["No, use the 2026 agreement.", "Also add March."], (True, 0)),
    "withdrawn with it": (["No, that's wrong.", "Never mind, you were right."], (False, 1)),
}


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("name", list(EARLY))
def test_a_message_read_before_the_turns_first_step_joins_the_message_it_started_with(writer, chats, learning_on,
                                                                                     clock, mode, name):
    (opening, sent), (corrects, withdrawn) = EARLY[name]
    chat = Chat(writer, chats, clock, "chat-1")
    reply = chat.turn("What is the fee?", "It's 70 EUR.")[-1]
    if mode == "next_turn":
        chat.turn(opening, Step("85 * 1"), "It's 85 EUR.")
        chat.turn(sent, "OK.")
    else:
        chat.turn(opening, Step("85 * 1"), "It's 85 EUR.", early=(sent,))
    store = LearningStore(learning_on, clock=clock)
    clock.advance(HOUR)

    report = corrections.confirm(store)

    assert report.confirmed == ((reply,) if corrects else ())
    assert report.withdrawn == withdrawn
    if mode == "mid_turn":
        assert len(store.find("episode")) == 2  # nothing came before it in its turn: no episode of its own
