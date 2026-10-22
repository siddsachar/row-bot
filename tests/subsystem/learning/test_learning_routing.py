"""Routing a turn to the personal model, with fallback to the main model (L2c2, D11; UX §5).

A new message of a kind of work the person gave a version runs on it; when it gets stuck (an error,
a tool loop, the step budget, an empty answer) its work stays and the main model finishes the turn,
counted as a cloud finish. The chat says "Answered by your personal model" or "Your personal model
got stuck; <main model> finished this.". Nothing is routed while nothing is in use."""

# ruff: noqa: F401, F811 -- the imported pytest fixtures are requested by name.
from __future__ import annotations

import sys
import threading

import pytest

from row_bot import learning
from row_bot.learning import routing, use
from row_bot.learning.training import releases
from tests.subsystem.client_protocol.test_protocol_application import _client, service
from tests.subsystem.client_protocol.test_protocol_security import bootstrap

pytestmark = [pytest.mark.subsystem]

MAIN = "model:openai:gpt-6.1-sol"
PERSONAL = "model:ollama:row-bot-personal-v1:abcd1234"
DOCS = "According to the travel policy document, what is the client dinner limit?"


def _ready(root, version: int = 1, ref: str = PERSONAL) -> None:
    releases.write_state(releases.version_dir(version, root), releases.VersionState(
        version=version, phase=releases.READY, job_id=f"job_{version}", model_ref=ref))


def _route(root, text: str = DOCS, **overrides):
    values = {"conversation_id": "chat-1", "text": text, "model_ref": MAIN, "surface": "normal_chat",
              "root": root, "main_ref": MAIN}
    values.update(overrides)
    return routing.route(**values)


def test_nothing_is_routed_while_nothing_is_in_use_and_it_costs_one_path_check(data_dir, monkeypatch):
    for module in ("row_bot.learning.routing", "row_bot.learning.use", "row_bot.learning.kinds"):
        monkeypatch.delitem(sys.modules, module, raising=False)

    assert learning.route_turn(conversation_id="c", text=DOCS, model_ref=MAIN) is None

    assert "row_bot.learning.routing" not in sys.modules and "row_bot.learning.use" not in sys.modules


def test_a_request_of_the_given_kind_goes_to_the_version_on_trial(data_dir):
    _ready(data_dir)
    use.start_trial(1, "document_lookups", PERSONAL, root=data_dir)

    found = _route(data_dir)

    assert found is not None and (found.kind, found.version, found.personal_ref) == ("document_lookups", 1, PERSONAL)
    assert found.main_ref == MAIN and found.state == "trial"


@pytest.mark.parametrize(("override", "why"), [
    ({"text": "Convert orders.csv into JSON."}, "another kind of work"),
    ({"text": "How are you?"}, "other always goes to the main model"),
    ({"model_ref": "model:anthropic:claude-opus"}, "the person picked another model for this chat"),
    ({"surface": "channel"}, "a channel chat"),
    ({"text": "   "}, "nothing to route"),
])
def test_everything_else_stays_on_the_main_model(data_dir, override, why):
    _ready(data_dir)
    use.start_trial(1, "document_lookups", PERSONAL, root=data_dir)

    assert _route(data_dir, **override) is None, why


def test_a_version_that_isnt_ready_answers_nothing(data_dir):
    use.start_trial(1, "document_lookups", PERSONAL, root=data_dir)
    releases.write_state(releases.version_dir(1, data_dir), releases.VersionState(
        version=1, phase=releases.RUNTIME_FAILED, job_id="job_1"))

    assert _route(data_dir) is None


def test_without_the_preview_flag_nothing_is_routed(data_dir, monkeypatch):
    from tests.fixtures.learning import set_learning_preview

    _ready(data_dir)
    use.start_trial(1, "document_lookups", PERSONAL, root=data_dir)
    set_learning_preview(monkeypatch, False)

    assert learning.route_turn(conversation_id="c", text=DOCS, model_ref=MAIN) is None


# ── Watching a routed turn ──────────────────────────────────────────────────


class Factory:
    """``stream_agent`` stand-in: the personal model's events, then the main model's."""

    def __init__(self, personal, main=(("token", "The limit is 85 EUR."), ("done", "The limit is 85 EUR."))):
        self.scripts = [list(personal), list(main)]
        self.calls: list[tuple[str, dict, threading.Event | None]] = []

    def __call__(self, text, enabled, config, *, stop_event=None):
        self.calls.append((text, config, stop_event))
        script = self.scripts[len(self.calls) - 1]
        for event in script:
            if stop_event is not None and stop_event.is_set():
                return
            yield event


def _stream(data_dir, factory, *, stop_event=None):
    route_ = routing.Route("document_lookups", 1, "trial", PERSONAL, MAIN, "GPT-6.1 Sol",
                           configurable={"thread_id": "chat-1", "model_override": PERSONAL})
    closed: list[str] = []
    events = list(routing.stream(route_, factory, DOCS, ["filesystem"], {"configurable": {
        "thread_id": "chat-1", "model_override": MAIN}}, stop_event=stop_event, root=data_dir,
        answer_open_calls=lambda thread, reason: closed.append(thread)))
    return events, closed


def _call(name="read_file", path="docs/a.md"):
    return ("tool_call", {"name": name, "args": {"path": path}})


def test_a_personal_model_that_answers_is_counted_as_answered(data_dir):
    use.start_trial(1, "document_lookups", PERSONAL, root=data_dir)
    factory = Factory([_call(), ("tool_done", {"name": "read_file"}), ("done", "85")])

    events, closed = _stream(data_dir, factory)

    assert [event[0] for event in events] == ["tool_call", "tool_done", "done"]
    assert len(factory.calls) == 1 and factory.calls[0][1]["configurable"]["model_override"] == PERSONAL
    tally = use.read(data_dir).assignments["document_lookups"].trial
    assert (tally.requests, tally.answered, tally.fallbacks) == (1, 1, 0) and closed == []


@pytest.mark.parametrize(("personal", "reason"), [
    ([("error", "Ollama stopped")], "error"),
    ([_call(), ("terminal", {"message": "work limit"}), ("done", "I reached this turn's work limit.")],
     "step_budget"),
    ([_call(), _call(), _call(), ("done", "85")], "tool_loop"),
    ([*(_call(path=f"docs/{index}.md") for index in range(routing.MAX_TOOL_CALLS + 1)), ("done", "x")],
     "step_budget"),
    ([("token", ""), ("done", "   ")], "empty_answer"),
])
def test_a_stuck_personal_model_is_finished_by_the_main_model_and_counts_as_a_cloud_finish(data_dir, personal,
                                                                                           reason):
    use.start_trial(1, "document_lookups", PERSONAL, root=data_dir)
    factory = Factory(personal)

    events, closed = _stream(data_dir, factory)

    kinds_seen = [event[0] for event in events]
    assert "error" not in kinds_seen and "terminal" not in kinds_seen  # the person never sees a failed turn
    fallback = next(event for event in events if event[0] == routing.FALLBACK_EVENT)[1]
    assert fallback["reason"] == reason and fallback["note"] == "Your personal model got stuck; GPT-6.1 Sol finished this."
    assert kinds_seen[-1] == "done" and events[-1][1] == "The limit is 85 EUR."
    main_config = factory.calls[1][1]["configurable"]
    assert main_config["model_override"] == MAIN and main_config["thread_event_context"] is True
    [handover] = main_config["thread_event_messages"]
    assert handover["role"] == "human" and handover["row_bot_ui"]["learning_fallback"]["text"] == fallback["note"]
    # The request is repeated for the main model (a smaller one loses it behind the stuck steps).
    assert f"> {DOCS.splitlines()[0]}" in handover["content"]
    assert closed == ["chat-1"]  # its open steps closed, its work kept
    tally = use.read(data_dir).assignments["document_lookups"].trial
    assert (tally.requests, tally.answered, tally.fallbacks) == (1, 0, 1)


def test_a_tool_loop_ends_the_personal_models_run(data_dir):
    use.start_trial(1, "document_lookups", PERSONAL, root=data_dir)
    factory = Factory([_call(), _call(), _call(), _call(), ("done", "85")])

    _stream(data_dir, factory)

    assert factory.calls[0][2].is_set()  # its own stop: the run ends at its next step


def test_the_persons_stop_is_never_turned_into_a_fallback(data_dir):
    use.start_trial(1, "document_lookups", PERSONAL, root=data_dir)
    stop = threading.Event()
    stop.set()
    factory = Factory([("error", "stopped")])

    events, _ = _stream(data_dir, factory, stop_event=stop)

    assert len(factory.calls) == 1 and not any(event[0] == routing.FALLBACK_EVENT for event in events)
    assert use.read(data_dir).assignments["document_lookups"].trial.requests == 0


def test_an_approval_passes_through_to_the_person(data_dir):
    use.start_trial(1, "document_lookups", PERSONAL, root=data_dir)
    factory = Factory([_call(), ("interrupt", [{"tool": "write_file"}])])

    events, _ = _stream(data_dir, factory)

    assert [event[0] for event in events] == ["tool_call", "interrupt"] and len(factory.calls) == 1


# ── Through a real conversation turn ────────────────────────────────────────

from tests.contracts.client_platform.test_headless_lifecycle import command, platform  # noqa: E402
from tests.helpers.client_platform_fakes import CheckpointCommit, ScriptedAgentStream, fixture_id  # noqa: E402


def test_a_routed_turn_runs_on_the_personal_model_and_the_main_model_finishes_a_stuck_one(platform, monkeypatch):
    from langchain_core.messages import AIMessage

    answer = fixture_id("main-answer")
    fake = ScriptedAgentStream(
        (("error", "the personal model's runtime stopped"),),
        (("token", "Finished by the main model"),
         CheckpointCommit((AIMessage(content="Finished by the main model", id=answer),), answer),
         ("done", "Finished by the main model")),
    )
    seen: dict = {}

    def route_turn(**request):
        seen.update(request)
        return routing.Route("document_lookups", 1, "trial", PERSONAL, request["model_ref"], "GPT-6.1 Sol")

    monkeypatch.setattr(learning, "route_turn", route_turn)
    configs: list[dict] = []

    def stream(text, enabled, config, *, stop_event=None):
        configs.append(dict(config["configurable"]))
        yield from fake.stream(text, enabled, config, stop_event=stop_event)

    platform.stream_factory = stream
    platform.resume_factory = fake.resume
    receipt = platform.execute(owner_id="fixture-owner", idempotency_key=fixture_id("routed:key"),
                               target="conversation-a", command=command("conversation.submit", "routed", {
                                   "submission_id": fixture_id("routed"), "text": DOCS, "attachment_refs": [],
                                   "model_selection": {"provider_id": "fixture", "model_ref": "fixture/model"}}))
    handle = platform.registry.get(receipt["execution_id"])
    assert handle.producer_done.wait(10)

    calls = configs
    assert seen["text"] == DOCS and seen["surface"] == "normal_chat" and seen["attachments"] == []
    assert calls[0]["model_override"] == PERSONAL and "thread_event_context" not in calls[0]
    assert calls[0]["reasoning_snapshot"]["model_ref"] == PERSONAL  # its own settings, thinking off
    assert calls[1]["model_override"] == seen["model_ref"] and calls[1]["thread_event_context"] is True
    assert handle.view()["status"] == "completed"
    texts = [block.get("text", "") for row in platform.snapshot("conversation-a")["rows"] for block in row["blocks"]]
    assert "Finished by the main model" in "".join(texts)


# ── The chat's quiet lines ──────────────────────────────────────────────────


def test_the_chat_says_answered_by_your_personal_model_and_the_fallback_note(service):
    from langchain_core.messages import AIMessage, HumanMessage
    from row_bot import threads

    conversation = threads.create_thread("Policies", seed_default_skills=False)
    note = "Your personal model got stuck; GPT-6.1 Sol finished this."
    assert threads.append_checkpoint_messages(conversation, [
        HumanMessage(id="ask-1", content="What is the dinner limit?"),
        AIMessage(id="personal", content="85 EUR.", response_metadata={"model": "row-bot-personal-v1:abcd1234"}),
        HumanMessage(id="ask-2", content="And the hotel limit?"),
        AIMessage(id="stuck-step", content="", tool_calls=[{"id": "c1", "name": "read_file", "args": {}}],
                  response_metadata={"model": "row-bot-personal-v1:abcd1234"}),
        HumanMessage(id="handover", content="[Row-Bot] The personal model started my last request…",
                     additional_kwargs={"row_bot_ui": {"thread_event": True, "source_event_id": "",
                                                       "learning_fallback": {
                                                           "text": note, "version": 1, "kind": "document_lookups",
                                                           "reason": "tool_loop"}}}),
        AIMessage(id="main", content="140 EUR a night.", response_metadata={"model_name": "gpt-6.1-sol"}),
        AIMessage(id="base", content="Maybe 100?", response_metadata={"model": "row-bot-personal-base:abcd1234"}),
    ])

    with _client(service) as client:
        _, headers = bootstrap(client)
        page = client.get(f"/api/v1/conversations/{conversation}/transcript", headers=headers)

    assert page.status_code == 200, page.text
    rows = {row["message_id"]: row for row in page.json()["rows"]}
    assert rows["personal"].get("answered_by") == "personal_model"
    assert rows["main"].get("answered_by") is None and rows["base"].get("answered_by") is None
    assert rows["stuck-step"].get("answered_by") is None  # a step that calls a tool isn't an answer
    handover = rows["handover"]
    assert handover["note"] == "personal_model_fallback"
    assert "".join(block.get("text", "") for block in handover["blocks"]) == note  # never the model's own text


def test_the_handover_is_never_the_persons_words_for_the_approval_reviewer():
    from langchain_core.messages import AIMessage, HumanMessage

    from row_bot.approval_review import person_messages

    messages = [HumanMessage(content="Book the hotel."),
                AIMessage(content="", tool_calls=[{"id": "c1", "name": "hotel_search", "args": {}}]),
                HumanMessage(content="[Row-Bot] The personal model started my last request…",
                             additional_kwargs={"row_bot_ui": {"thread_event": True,
                                                               "learning_fallback": {"text": "x"}}})]

    assert person_messages(messages) == ("Book the hotel.",)  # E8: never the person's words


def test_the_handover_is_never_remembered_or_learned_from_as_the_persons_message():
    from langchain_core.messages import HumanMessage

    from row_bot.learning.past_chats import _person_started
    from row_bot.memory_extraction import _is_app_event

    handover = HumanMessage(content="[Row-Bot] …", additional_kwargs={"row_bot_ui": {
        "thread_event": True, "learning_fallback": {"text": "x"}}})

    assert not _person_started(handover) and _is_app_event(handover)
