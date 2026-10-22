"""Training rows in the shape the personal model serves in (O71, L2w): a row captured from a cloud
main model (every tool bound, the large prompt) is re-rendered into the serving envelope at the
recipe's context: the core tools stay bound, the rest go behind ``tool_search`` / ``tool_invoke``,
a call to a deferred tool becomes the bridge call the served model would make (with a search that
finds it where the conversation hasn't yet), tool results are kept, the prompt is the small
profile's; the rows that fit the training length are counted before and after; a rendered row is
derived (never labelled raw output) and the manifest and release name the rendering."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from row_bot.learning import evaluation
from row_bot.learning.store import LearningStore
from row_bot.learning.training import dataset, recipes, shape

pytestmark = [pytest.mark.subsystem]

LARGE_ROOT = "You are Row-Bot.\nHEAD RULES\nTASKS: use the task tools for reminders.\nMEMORY: save what lasts.\nTAIL RULES"
SMALL_ROOT = "You are Row-Bot.\nHEAD RULES\nTAIL RULES"
SELF_KNOWLEDGE = "ABOUT YOU (SELF-KNOWLEDGE):\nYou are a personal AI agent that runs locally."
PLATFORM = "PLATFORM: Windows."
FILES_GUIDE = "FILES GUIDE: list a folder before you read in it."
CALENDAR_GUIDE = "CALENDAR GUIDE: check the day before you add to it."
TURN_CONTEXT = ("<row_bot_turn_context>\nCurrent date and time: Friday.\n\nCurrent state:\n- Model: GPT\n"
                "- Channels: none\n</row_bot_turn_context>")


def _tool(name: str, description: str, **properties: str) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": {key: {"type": value} for key, value in properties.items()},
                       "required": sorted(properties)}}}


READ = _tool("workspace_read_file", "Read a file in the workspace.", path="string")
SAVE = _tool("save_memory", "Save a long-term memory.", content="string")
CALENDAR = _tool("calendar_create_event", "Create an event in the calendar.", title="string", date="string",
                 start="string")
MAIL = _tool("mail_send", "Send an email.", to="string", subject="string", body="string")
CLOUD_TOOLS = [READ, SAVE, CALENDAR, MAIL]


def _index(groups, loadable):
    return "TOOL GUIDES (index):\n" + "\n".join(f"- {group}" for group in groups)


ENVELOPE = shape.Envelope(
    serve_context=32_768, profile="small", bound=frozenset({"workspace_read_file", "save_memory"}),
    root_removed="TASKS: use the task tools for reminders.\nMEMORY: save what lasts.\n", root_follows="TAIL RULES",
    root_guides=(("task", frozenset({"task_create"}), "TASKS: use the task tools for reminders."),
                 ("memory", frozenset({"save_memory"}), "MEMORY: save what lasts.")),
    guides=((FILES_GUIDE, ("filesystem",)), (CALENDAR_GUIDE, ("calendar",))),
    guide_index=_index, dropped_texts=(SELF_KNOWLEDGE,),
)
CLOUD_SYSTEM = [LARGE_ROOT, SELF_KNOWLEDGE, PLATFORM, FILES_GUIDE + "\n\n" + CALENDAR_GUIDE]


class CloudWork:
    """Turns captured as a cloud main model is sent them: the large prompt, every tool bound."""

    def __init__(self, writer, chats, clock) -> None:
        self.writer, self.chats, self.clock = writer, chats, clock
        self.history: dict[str, list] = {}
        self.count = 0

    def turn(self, thread_id: str, question: str, calls: list[tuple[str, dict, str]], answer: str, *,
             system: list[str] | None = None, tools: list[dict] | None = None) -> list[str]:
        """One turn: ``calls`` are (tool, arguments, result) steps, one model call each."""

        if thread_id not in self.chats.threads:
            self.chats.chat(thread_id)
        history = self.history.setdefault(thread_id, [])
        self.count += 1
        number = self.count
        turn = self.writer.begin_turn(thread_id=thread_id, surface="normal_chat")
        prompt = [SystemMessage(content=text, id=f"sys{index}") for index, text in enumerate(system or CLOUD_SYSTEM)]
        asked = HumanMessage(content=f"{question}\n\n{TURN_CONTEXT}", id=f"h{number}")
        sent = [*prompt, *history, asked]
        ids = []
        added = [asked]
        for step, (name, args, result) in enumerate(calls):
            call = {"id": f"call_{number}_{step}", "name": name, "args": args}
            message = AIMessage(content="", id=f"a{number}_{step}", tool_calls=[call])
            ids.append(self.writer.model_call(turn, messages=sent, response=message, tools=tools or CLOUD_TOOLS,
                                              model_ref="gpt-6.1-sol", provider_id="codex"))
            output = ToolMessage(content=result, tool_call_id=call["id"], id=f"t{number}_{step}")
            self.writer.tool_call(turn, call=call, result=output)
            sent = [*sent, message, output]
            added += [message, output]
        reply = AIMessage(content=answer, id=f"r{number}")
        ids.append(self.writer.model_call(turn, messages=sent, response=reply, tools=tools or CLOUD_TOOLS,
                                          model_ref="gpt-6.1-sol", provider_id="codex"))
        added.append(reply)
        self.writer.end_turn(turn, "completed")
        self.writer.drain()
        history += added
        self.clock.advance(60)
        return ids


@pytest.fixture
def work(writer, chats, clock):
    return CloudWork(writer, chats, clock)


@pytest.fixture
def recipe():
    return recipes.load("8gb")


def _compile(root, chats, recipe, **kwargs):
    kwargs.setdefault("refresh_vault", False)
    kwargs.setdefault("envelope", ENVELOPE)
    return dataset.compile_dataset(LearningStore(root), recipe, lookup=chats, **kwargs)


def _calls(message: dict) -> list[tuple[str, dict]]:
    return [(call["function"]["name"], json.loads(call["function"]["arguments"])) for call in message["tool_calls"]]


def test_a_call_to_a_deferred_tool_becomes_the_bridge_call_the_served_model_makes(work, learning_on, chats, recipe):
    event = {"title": "Dentist", "date": "2026-10-23", "start": "10:00"}
    work.turn("chat-1", "Add the dentist on Friday at 10", [("calendar_create_event", event, '{"created": "evt_1"}')],
              "Done: the dentist is on Friday at 10.")

    [example] = _compile(learning_on, chats, recipe).train
    row = example.row
    messages = row["messages"]

    # The core tools stay bound; the others are reached through Row-Bot's own bridge, whose catalog names them.
    names = [tool["function"]["name"] for tool in row["tools"]]
    assert names == ["workspace_read_file", "save_memory", "tool_search", "tool_invoke"]
    catalog = row["tools"][2]["function"]["description"]
    assert "calendar_create_event" in catalog and "mail_send" in catalog
    assert [message["role"] for message in messages] == [
        "system", "user", "assistant", "tool", "assistant", "tool", "assistant"]
    # First it finds the tool (the bridge's own search, fenced as Row-Bot fences search results)...
    assert _calls(messages[2]) == [("tool_search", {"query": "calendar create event"})]
    found = messages[3]["content"]
    assert found.startswith('<EXTERNAL_CONTENT source="tool_search">')
    result = json.loads(found[found.index("{"):found.rindex("}") + 1])
    assert result["results"][0]["name"] == "calendar_create_event"
    assert result["results"][0]["input_schema"] == CALENDAR["function"]["parameters"]
    # ...then calls it through tool_invoke with the same id, so its result is kept as it was.
    assert _calls(messages[4]) == [("tool_invoke", {"name": "calendar_create_event", "arguments": event})]
    assert messages[4]["tool_calls"][0]["id"] == messages[5]["tool_call_id"] == "call_1_0"
    assert messages[5]["content"] == '{"created": "evt_1"}'
    assert messages[6] == {"role": "assistant", "content": "Done: the dentist is on Friday at 10.", "weight": 1}
    # Every step of this turn is a target; the search step is the served model's own move.
    assert [message.get("weight") for message in messages if message["role"] == "assistant"] == [1, 1, 1]


def test_the_prompt_is_the_small_profiles_and_the_live_state_leaves_the_turn_context(work, learning_on, chats,
                                                                                    recipe):
    work.turn("chat-1", "Read my notes", [("workspace_read_file", {"path": "notes.txt"}, "water the plants")],
              "Your notes say: water the plants.")

    [example] = _compile(learning_on, chats, recipe).train
    system, question = example.row["messages"][:2]

    # The root without what the small profile sends as guides; no self-knowledge; the guides as an
    # index, with the MEMORY guidance because its tools are bound (and no TASKS: none of its tools are).
    assert system["content"] == "\n\n".join([
        SMALL_ROOT, PLATFORM, "MEMORY: save what lasts.\n\nTOOL GUIDES (index):\n- calendar\n- filesystem"])
    assert "Current state" not in question["content"]
    assert question["content"].endswith("Current date and time: Friday.\n</row_bot_turn_context>")
    # Only bound tools were called, so the calls are as captured; the row is derived all the same.
    assert _calls(example.row["messages"][2]) == [("workspace_read_file", {"path": "notes.txt"})]
    assert example.derived


def test_a_prompt_row_bot_sent_as_one_system_message_is_rendered_the_same(work, learning_on, chats, recipe):
    # For an endpoint that takes one system message Row-Bot joins its sections first.
    for joined, thread in ((False, "chat-1"), (True, "chat-2")):
        work.turn(thread, "Read my notes", [("workspace_read_file", {"path": "notes.txt"}, "water the plants")],
                  "Your notes say: water the plants.", system=["\n\n".join(CLOUD_SYSTEM)] if joined else None)

    compiled = _compile(learning_on, chats, recipe)
    systems = {item.thread_id: item.row["messages"][0]["content"] for item in compiled.train}

    assert systems["chat-1"] == systems["chat-2"]
    assert SELF_KNOWLEDGE not in systems["chat-2"] and CALENDAR_GUIDE not in systems["chat-2"]


def test_a_tool_the_conversation_already_found_needs_no_new_search(work, learning_on, chats, recipe):
    first = {"title": "Dentist", "date": "2026-10-23", "start": "10:00"}
    second = {"title": "Physio", "date": "2026-10-24", "start": "09:00"}
    work.turn("chat-1", "Add the dentist", [("calendar_create_event", first, '{"created": "evt_1"}')], "Added.")
    work.turn("chat-1", "And the physio", [("calendar_create_event", second, '{"created": "evt_2"}')], "Added too.")

    compiled = _compile(learning_on, chats, recipe)
    later = next(item.row for item in compiled.train if "physio" in json.dumps(item.row))
    messages = later["messages"]
    targets = [message for message in messages if message["role"] == "assistant" and message["weight"]]

    # The earlier turn (context) found the tool; this turn calls it straight through tool_invoke.
    searches = [message for message in messages if message.get("tool_calls")
                and _calls(message)[0][0] == "tool_search"]
    assert len(searches) == 1 and searches[0]["weight"] == 0
    assert [_calls(message) for message in targets if message.get("tool_calls")] == [
        [("tool_invoke", {"name": "calendar_create_event", "arguments": second})]]
    assert compiled.rendering["calls_bridged"] == 3 and compiled.rendering["searches_added"] == 2


def test_rows_too_long_as_captured_fit_once_rendered(work, learning_on, chats, recipe):
    many = [_tool(f"app{index}_action", "An app action. " + "It does many careful things. " * 60,
                  **{f"field{number}": "string" for number in range(12)}) for index in range(40)]
    tools = [READ, SAVE, *many]
    for index in range(3):
        work.turn(f"chat-{index}", f"Question {index}", [("app7_action", {"field0": "x"}, "ok")], "Done.", tools=tools)

    captured = _compile(learning_on, chats, recipe, shape="captured")
    served = _compile(learning_on, chats, recipe)

    # Every tool bound directly makes each row longer than the 8 GB tier's 16K; behind the bridge they fit.
    assert captured.examples == 0 and captured.left_out == {"too_long": 3}
    assert served.examples == 3 and served.left_out == {}
    assert max(item.estimated_tokens for item in served.train) < recipe.max_seq_length


def test_the_manifest_and_the_release_name_the_rendering_and_count_derived_rows(work, learning_on, chats, recipe,
                                                                               tmp_path):
    work.turn("chat-1", "Add the dentist", [("calendar_create_event", {"title": "Dentist"}, "{}")], "Added.")

    served = _compile(learning_on, chats, recipe)
    captured = _compile(learning_on, chats, recipe, shape="captured")
    dataset.write(served, tmp_path / "data")
    manifest = dataset.read_manifest(tmp_path / "data")

    assert manifest["rendering"] == {
        "name": "serving-v1@32768", "version": shape.RENDERING_VERSION, "serve_context": 32_768, "profile": "small",
        "rows": 1, "tools_deferred": 2, "calls_bridged": 1, "searches_added": 1, "system_rewritten": 1,
        "state_removed": 1, "past_steps": {"kept": 0, "tool_not_offered": 0, "arguments_invalid": 0}}
    assert manifest["derived"] == 1 and manifest["derived_ids"] == [served.train[0].id]
    assert served.data_spec().rendering == "serving-v1@32768"
    # Kept as captured: nothing derived, and the data says so.
    assert captured.rendering == {"name": "captured", "version": 0}
    assert not captured.train[0].derived and captured.data_spec().rendering == "captured"
    assert captured.id != served.id
    assert captured.train[0].row["tools"] == CLOUD_TOOLS


# ── Imported past chats in the serving shape (O113, C3) ─────────────────────


def _import(learning_on, clock, chats, *conversations: tuple) -> None:
    """Past conversations the import reads (the last 30 days), each (thread, tool steps, reply[, question]):
    the person's message, its steps (a call and its result each) and the reply."""

    import uuid

    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    from row_bot.learning import past_chats
    from tests.subsystem.learning.test_learning_past_chats import DAY, FakeChats

    source = FakeChats()
    for thread_id, steps, answer, *question in conversations:
        saved: list = [HumanMessage(content=(question or ["Add the dentist on Friday at 10"])[0],
                                    id=f"h-{uuid.uuid4().hex[:6]}")]
        for index, (name, args, result) in enumerate(steps):
            call = {"id": f"past_{thread_id}_{index}", "name": name, "args": args}
            saved += [AIMessage(content="", id=f"a-{uuid.uuid4().hex[:6]}", tool_calls=[call]),
                      ToolMessage(content=result, tool_call_id=call["id"], name=name, id=f"t-{uuid.uuid4().hex[:6]}")]
        saved.append(AIMessage(content=answer, id=f"r-{uuid.uuid4().hex[:6]}"))
        chats.chat(thread_id)
        source.add(thread_id, clock() - 2 * DAY, saved)
    past_chats.run_import(store=LearningStore(learning_on, clock=clock), source=source, lookup=chats, clock=clock,
                          busy=lambda: False, wait=lambda seconds: None)


def _past(compiled) -> list:
    return [item for item in compiled.train if item.historical]


def test_an_imported_tool_step_trains_rendered_like_a_live_one(work, learning_on, clock, chats, recipe):
    # O113: the import keeps each step's call and result but not the prompt or tools; framed with the newest
    # live Agent request and rendered, a past calendar call becomes the bridge call the served model makes.
    work.turn("chat-live", "Read my notes", [("workspace_read_file", {"path": "notes.txt"}, "plants")], "Plants.")
    event = {"title": "Dentist", "date": "2026-10-23", "start": "10:00"}
    _import(learning_on, clock, chats, ("chat-past", [("calendar_create_event", event, '{"created": "evt_9"}')],
                                        "Done: the dentist is on Friday at 10."))

    compiled = _compile(learning_on, chats, recipe)
    [example] = _past(compiled)
    messages = example.row["messages"]

    assert example.derived and [tool["function"]["name"] for tool in example.row["tools"]] == [
        "workspace_read_file", "save_memory", "tool_search", "tool_invoke"]
    assert messages[0]["role"] == "system" and messages[0]["content"].startswith(SMALL_ROOT)
    targets = [message for message in messages if message["role"] == "assistant" and message["weight"]]
    assert [_calls(message) for message in targets if message.get("tool_calls")] == [
        [("tool_search", {"query": "calendar create event"})],
        [("tool_invoke", {"name": "calendar_create_event", "arguments": event})]]
    assert targets[-1]["content"] == "Done: the dentist is on Friday at 10."
    assert compiled.rendering["past_steps"] == {"kept": 1, "tool_not_offered": 0, "arguments_invalid": 0}


def test_an_imported_step_whose_tool_isnt_offered_or_whose_arguments_dont_fit_is_context_only(
        work, learning_on, clock, chats, recipe):
    work.turn("chat-live", "Read my notes", [("workspace_read_file", {"path": "notes.txt"}, "plants")], "Plants.")
    _import(learning_on, clock, chats, ("chat-gone", [("old_notes_tool", {"path": "a.txt"}, "a")], "It says a."),
            ("chat-bad", [("calendar_create_event", {"title": "Dentist", "when": 10}, "{}")], "Added."))

    compiled = _compile(learning_on, chats, recipe)

    assert compiled.rendering["past_steps"] == {"kept": 0, "tool_not_offered": 1, "arguments_invalid": 1}
    for example in _past(compiled):  # each still teaches its reply, after the step it no longer teaches
        targets = [message for message in example.row["messages"]
                   if message["role"] == "assistant" and message["weight"]]
        assert [message.get("tool_calls") for message in targets] == [None]


def test_an_imported_chat_without_tools_trains_as_a_direct_answer(work, learning_on, clock, chats, recipe):
    work.turn("chat-live", "Read my notes", [("workspace_read_file", {"path": "notes.txt"}, "plants")], "Plants.")
    _import(learning_on, clock, chats, ("chat-past", [], "Paris is the capital of France.",
                                        "What's the capital of France?"))

    [example] = _past(_compile(learning_on, chats, recipe))
    messages = example.row["messages"]

    assert messages[0]["role"] == "system" and [message["role"] for message in messages[1:]] == ["user", "assistant"]
    assert messages[-1] == {"role": "assistant", "content": "Paris is the capital of France.", "weight": 1}


def test_imported_rows_wait_for_a_first_live_turn_with_tools(learning_on, clock, chats, recipe):
    # Nothing live yet: no prompt or tools to frame them with, so they wait (and say so); the captured shape
    # (the paper's ablation) keeps them as imported.
    _import(learning_on, clock, chats, ("chat-past", [("calendar_create_event", {"title": "x"}, "{}")], "Added."),
            ("chat-chat", [], "Paris.", "Capital of France?"))

    served = _compile(learning_on, chats, recipe)
    captured = _compile(learning_on, chats, recipe, shape="captured")

    assert served.examples == 0 and served.left_out == {"past_chat_waiting": 2}
    assert len(captured.train) == 2 and all(item.historical and not item.derived for item in captured.train)
    for example in captured.train:  # as imported: only the reply trains, its steps are context
        assert not any(message.get("tool_calls") and message.get("weight") for message in example.row["messages"])


def test_a_row_already_in_the_serving_shape_is_left_as_it_is():
    system = [SMALL_ROOT, PLATFORM]
    tools = [READ, SAVE, *shape.render(CLOUD_SYSTEM, [], CLOUD_TOOLS, ENVELOPE)[2][2:]]
    messages = [
        {"role": "user", "content": "Add the dentist"},
        {"role": "assistant", "content": "", "weight": 1, "tool_calls": [{"id": "c1", "type": "function", "function": {
            "name": "tool_search", "arguments": json.dumps({"query": "calendar"})}}]},
        {"role": "tool", "content": '{"ok": true, "results": [{"name": "calendar_create_event"}]}', "tool_call_id": "c1"},
        {"role": "assistant", "content": "", "weight": 1, "tool_calls": [{"id": "c2", "type": "function", "function": {
            "name": "tool_invoke", "arguments": json.dumps({"name": "calendar_create_event", "arguments": {}})}}]},
        {"role": "tool", "content": "{}", "tool_call_id": "c2"},
        {"role": "assistant", "content": "Added.", "weight": 1},
    ]

    out_system, out_messages, out_tools, stats = shape.render(system, messages, tools, ENVELOPE)

    assert (out_system, out_messages, out_tools) == (system, messages, tools) and not stats.changed


def test_rendering_twice_gives_the_same_row():
    messages = [
        {"role": "user", "content": f"Book it\n\n{TURN_CONTEXT}"},
        {"role": "assistant", "content": "Booking.", "weight": 1, "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "calendar_create_event", "arguments": "{}"}},
            {"id": "c2", "type": "function", "function": {"name": "workspace_read_file", "arguments": "{}"}}]},
        {"role": "tool", "content": "{}", "tool_call_id": "c1"},
        {"role": "tool", "content": "notes", "tool_call_id": "c2"},
        {"role": "assistant", "content": "Booked.", "weight": 1},
    ]

    once = shape.render(CLOUD_SYSTEM, messages, CLOUD_TOOLS, ENVELOPE)
    twice = shape.render(*once[:3], ENVELOPE)

    assert once[:3] == twice[:3] and not twice[3].changed
    # Parallel calls: the search comes first (with the turn's words), then both calls together.
    assistant = [message for message in once[1] if message["role"] == "assistant"]
    assert assistant[0]["content"] == "Booking." and _calls(assistant[0])[0][0] == "tool_search"
    assert [name for name, _ in _calls(assistant[1])] == ["tool_invoke", "workspace_read_file"]


def test_a_larger_context_defers_only_the_rarely_used_groups_and_the_largest_nothing():
    window = replace(ENVELOPE, serve_context=65_536, profile="window", bound=frozenset(),
                     deferred=frozenset({"mail_send"}))
    full = replace(ENVELOPE, serve_context=200_000, profile="full")
    messages = [{"role": "user", "content": "Mail Dana"}, {"role": "assistant", "content": "", "weight": 1,
                 "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "mail_send", "arguments": "{}"}}]},
                {"role": "tool", "content": "sent", "tool_call_id": "c1"},
                {"role": "assistant", "content": "Sent.", "weight": 1}]

    _, windowed, window_tools, _ = shape.render(CLOUD_SYSTEM, messages, CLOUD_TOOLS, window)
    unchanged = shape.render(CLOUD_SYSTEM, messages, CLOUD_TOOLS, full)

    assert [tool["function"]["name"] for tool in window_tools] == [
        "workspace_read_file", "save_memory", "calendar_create_event", "tool_search", "tool_invoke"]
    assert _calls(windowed[-3])[0][0] == "tool_invoke"
    assert unchanged[:3] == (CLOUD_SYSTEM, messages, CLOUD_TOOLS) and not unchanged[3].changed


def test_row_bots_own_envelope_binds_its_core_groups_at_32k(data_dir):
    from row_bot import prompts

    small = shape.envelope_for(32_768)
    window = shape.envelope_for(65_536)

    assert small.profile == "small" and small.name == "serving-v1@32768"
    assert {"workspace_read_file", "save_memory", "task_create"} <= small.bound
    assert small.defers("row_bot_status") and small.defers("calendar_create_event")
    assert not small.defers("tool_search") and not small.defers("read_tool_output")
    # Taking the root's TASKS and MEMORY guidance out of the large root gives exactly the small one.
    large, small_root = prompts.get_agent_system_prompt(), prompts.get_agent_system_prompt(small_context=True)
    assert large.replace(small.root_removed + small.root_follows, small.root_follows, 1) == small_root
    assert window.profile == "window" and window.defers("row_bot_status") and not window.defers("calendar_create_event")
    assert shape.envelope_for(200_000).profile == "full"


# ── Replay: a decision asked as the arm serves (O71, pre-registration §4) ───


def _replay_case(history: list | None = None) -> evaluation.ReplayCase:
    from langchain_core.messages import messages_to_dict

    event = {"title": "Dentist", "date": "2026-10-23", "start": "10:00"}
    sent = [*(SystemMessage(content=text) for text in CLOUD_SYSTEM), *(history or []),
            HumanMessage(content=f"Add the dentist on Friday at 10\n\n{TURN_CONTEXT}")]
    return evaluation.ReplayCase(
        "dec_1", "chat-1", "chat-1", 1.0, "Your work", "exact", False, tuple(messages_to_dict(sent)),
        tuple(CLOUD_TOOLS), "model:codex:gpt-6.1-sol", evaluation.Action((("calendar_create_event", event),)),
        "captured", "exact_action", "Add the dentist on Friday at 10")


def _ask(case, reply: AIMessage, *, serve_context: int | None = 32_768):
    from tests.fixtures.learning_eval import ScriptedClient

    client = ScriptedClient(lambda _messages, _tools, _model: reply, local=True)
    result = evaluation.run_replay_case(case, evaluation.Arm("v1", model_ref="model:ollama:v1",
                                                             serve_context=serve_context), client=client)
    return result, client.calls[0] if client.calls else None


def _reply(*calls: tuple[str, dict]) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"r{index}", "type": "tool_call"}
                                             for index, (name, args) in enumerate(calls)])


@pytest.fixture
def served_at_32k(monkeypatch):
    monkeypatch.setattr(shape, "envelope_for", lambda context: ENVELOPE if context <= 32_768 else replace(
        ENVELOPE, serve_context=context, profile="full"))


def test_a_replayed_decision_reaches_a_small_context_model_in_its_serving_shape(served_at_32k):
    event = {"title": "Dentist", "date": "2026-10-23", "start": "10:00"}

    found, sent = _ask(_replay_case(), _reply(("tool_search", {"query": "calendar create event"})))

    # Asked as it serves: the small prompt, the core tools and the bridge, no live state.
    assert [tool["function"]["name"] for tool in sent["tools"]] == [
        "workspace_read_file", "save_memory", "tool_search", "tool_invoke"]
    system, question = sent["messages"]
    assert SELF_KNOWLEDGE not in system.content and CALENDAR_GUIDE not in system.content
    assert "Current state" not in question.content
    # The calendar tool is behind the bridge and not yet found: finding it is the right first move.
    assert (found.status, found.detail) == ("passed", "searched for the tool it needed and found it")
    assert _ask(_replay_case(), _reply(("tool_search", {"query": "weather today"})))[0].status == "failed"
    # Calling it through tool_invoke counts as the call it carries.
    invoked = _ask(_replay_case(), _reply(("tool_invoke", {"name": "calendar_create_event", "arguments": event})))[0]
    wrong = _ask(_replay_case(), _reply(("tool_invoke", {"name": "mail_send", "arguments": {"to": "dana"}})))[0]
    assert (invoked.status, wrong.status) == ("passed", "failed")


def test_a_tool_the_replayed_conversation_already_found_is_called_straight_through_the_bridge(served_at_32k):
    earlier = [HumanMessage(content="Add the physio"),
               AIMessage(content="", tool_calls=[{"name": "calendar_create_event", "args": {"title": "Physio"},
                                                  "id": "c1", "type": "tool_call"}]),
               ToolMessage(content='{"created": "evt_1"}', tool_call_id="c1"), AIMessage(content="Added.")]
    event = {"title": "Dentist", "date": "2026-10-23", "start": "10:00"}

    searched, sent = _ask(_replay_case(earlier), _reply(("tool_search", {"query": "calendar create event"})))
    invoked = _ask(_replay_case(earlier), _reply(("tool_invoke", {"name": "calendar_create_event",
                                                                   "arguments": event})))[0]

    # The history shows the tool found (a search step) and called through tool_invoke, as a row would.
    history = [message for message in sent["messages"] if getattr(message, "tool_calls", None)]
    assert [call["name"] for message in history for call in message.tool_calls] == ["tool_search", "tool_invoke"]
    assert (searched.status, invoked.status) == ("failed", "passed")


def test_a_cloud_model_is_asked_the_decision_as_captured(served_at_32k):
    _result, sent = _ask(_replay_case(), _reply(("calendar_create_event", {"title": "Dentist"})), serve_context=None)

    assert sent["tools"] == CLOUD_TOOLS and len(sent["messages"]) == len(CLOUD_SYSTEM) + 1
