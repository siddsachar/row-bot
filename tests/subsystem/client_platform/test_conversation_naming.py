"""Conversations name themselves (B230).

The first message names a conversation at once from its first words; after the
first reply a background call asks the conversation's own model for a title.
Real admission, checkpoint and thread owners; the agent stream and the title
model are scripted, so nothing calls a provider.
"""
from __future__ import annotations

import threading

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
import pytest

from tests.contracts.client_platform.test_headless_lifecycle import command, platform  # noqa: F401
from tests.helpers.client_platform_fakes import CheckpointCommit, ScriptedAgentStream, StreamBarrier, fixture_id

pytestmark = pytest.mark.subsystem

CONVERSATION = "conversation-a"
MODEL = {"provider_id": "fixture", "model_ref": "fixture/model"}
FIRST = "Please help me plan a relaxed three day walking trip along the coast of Cornwall in May"
FIRST_WORDS = "Please help me plan a relaxed three day walking trip along"


class TitleModel:
    """The conversation's model, scripted: records each title request and answers it."""

    def __init__(self) -> None:
        self.answer = '**Title:** "Cornwall Coast Walking Trip."'
        self.error: Exception | None = None
        self.gate: threading.Event | None = None
        self.capabilities = None
        self.on_call = None
        self.requests: list[dict] = []
        self.entered = threading.Event()

    def get_llm_for(self, label, num_ctx=None, *, reasoning_plan=None):
        return _Request(self, {"model": label, "plan": reasoning_plan, "bound": {}})


class _Request:
    def __init__(self, owner: TitleModel, request: dict) -> None:
        self.owner = owner
        self.request = request

    def bind(self, **kwargs):
        return _Request(self.owner, {**self.request, "bound": {**self.request["bound"], **kwargs}})

    def invoke(self, messages, *args, **kwargs):
        owner = self.owner
        owner.requests.append({**self.request, "messages": list(messages)})
        if owner.on_call is not None:
            owner.on_call()
        owner.entered.set()
        if owner.gate is not None and not owner.gate.wait(10):
            raise TimeoutError("Synthetic title gate was not released")
        if owner.error is not None:
            raise owner.error
        return AIMessage(content=owner.answer)


@pytest.fixture
def naming(platform, monkeypatch):  # noqa: F811
    from row_bot import models
    from row_bot.providers import reasoning

    model = TitleModel()
    monkeypatch.setattr(models, "get_llm_for", model.get_llm_for)
    monkeypatch.setattr(reasoning, "resolve_reasoning_capabilities_for_ref", lambda ref: model.capabilities)
    yield model
    if model.gate is not None:
        model.gate.set()
    settle()


def reply(label: str, *before):
    native_id = fixture_id(label + ":assistant")
    return (*before, ("token", label), CheckpointCommit((AIMessage(content=label, id=native_id),), native_id),
            ("done", label))


def submit(platform, fake: ScriptedAgentStream, label: str, text: str = FIRST) -> dict:  # noqa: F811
    platform.stream_factory = fake.stream
    platform.resume_factory = fake.resume
    revision = platform.get_conversation(CONVERSATION)["revision"]
    return platform.execute(owner_id="fixture-owner", idempotency_key=fixture_id(label + ":key"),
                            target=CONVERSATION, command=command("conversation.submit", label, {
                                "submission_id": fixture_id(label), "text": text, "attachment_refs": [],
                                "model_selection": MODEL}, revision))


def rename(platform, label: str, title: str) -> None:  # noqa: F811
    revision = platform.get_conversation(CONVERSATION)["revision"]
    platform.execute(owner_id="fixture-owner", idempotency_key=fixture_id(label + ":key"), target=CONVERSATION,
                     command=command("conversation.rename", label, {"title": title}, revision))


def settle(platform=None, receipt: dict | None = None) -> None:  # noqa: F811
    """Wait for a turn's worker, then for the naming it started and the change it announced."""
    if receipt is not None:
        assert platform.registry.get(receipt["execution_id"]).producer_done.wait(10)
    for name in ("row-bot-conversation", "conversation-naming", "conversation-changed"):
        for thread in [thread for thread in threading.enumerate() if thread.name == name]:
            thread.join(10)


def title(platform) -> str:  # noqa: F811
    return platform.get_conversation(CONVERSATION)["title"]


def test_first_message_names_at_once_and_the_first_reply_brings_a_smart_name(platform, naming):
    barrier = StreamBarrier()
    fake = ScriptedAgentStream(reply("Here is a gentle plan for three days.", barrier), reply("Second answer"))
    streaming: list[bool] = []
    naming.on_call = lambda: streaming.append(bool(platform.registry.active(CONVERSATION)))
    receipt = submit(platform, fake, "first")
    assert barrier.entered.wait(10)
    assert title(platform) == FIRST_WORDS, "the first words name it while the answer streams"
    assert naming.requests == [], "no title is asked while the answer streams"
    barrier.release.set()
    settle(platform, receipt)
    assert title(platform) == "Cornwall Coast Walking Trip"
    assert streaming == [False], "the title call starts only once the turn is over"
    settle(platform, submit(platform, fake, "second", "And what should I pack?"))
    assert title(platform) == "Cornwall Coast Walking Trip", "only the first message names a conversation"
    assert len(naming.requests) == 1, "one title call per conversation"


def test_the_smart_name_tells_open_pages_through_the_change_event(platform, naming):
    naming.gate = threading.Event()
    submit(platform, ScriptedAgentStream(reply("A plan.")), "first")
    assert naming.entered.wait(10), "the title call starts once the turn has finished"
    cursor = platform.events_since(CONVERSATION)["cursor"]
    naming.gate.set()
    settle()
    assert title(platform) == "Cornwall Coast Walking Trip"
    events = platform.events_since(CONVERSATION, cursor)["events"]
    assert "transcript.checkpoint" in [event["type"] for event in events]


@pytest.mark.parametrize(("capabilities", "selection", "temperature"), [
    ({"supported_efforts": ("low", "high"), "default_effort": "high", "can_disable": True},
     {"kind": "off"}, 0.2),
    ({"supported_efforts": ("high", "minimal", "low")},
     {"kind": "effort", "effort": "minimal"}, None),
])
def test_the_title_request_is_capped_and_goes_to_the_conversations_own_model(
        platform, naming, capabilities, selection, temperature):
    from row_bot.providers.reasoning import ReasoningCapabilities

    naming.capabilities = ReasoningCapabilities(**capabilities)
    message = "m" * 1400 + " " + "x" * 600
    answer = "r" * 450 + " " + "y" * 450
    settle(platform, submit(platform, ScriptedAgentStream(reply(answer)), "first", message))
    [request] = naming.requests
    assert request["model"] == "model:fixture:fixture/model"
    assert request["plan"].selection.to_json() == selection
    assert request["bound"]["max_tokens"] == 24
    assert request["bound"].get("temperature") == temperature
    assert "tools" not in request["bound"]
    system, human = request["messages"]
    assert isinstance(system, SystemMessage) and isinstance(human, HumanMessage)
    assert "\n" not in system.content, "a one-line instruction"
    assert message[:1500] in human.content and message[:1501] not in human.content
    assert answer[:500] in human.content and answer[:501] not in human.content


@pytest.mark.parametrize("owner", ["person", "workflow", "agent"])
def test_names_the_person_gave_workflow_runs_and_agents_threads_are_never_changed(
        platform, naming, monkeypatch, owner):
    from row_bot import agent_runs, threads

    if owner == "person":
        rename(platform, "manual", "My own name")
    elif owner == "workflow":
        monkeypatch.setattr(threads, "get_workflow_thread_ids", lambda: {CONVERSATION})
    else:
        agent_runs.create_agent_run(parent_thread_id="conversation-b", thread_id=CONVERSATION)
    before = title(platform)
    settle(platform, submit(platform, ScriptedAgentStream(reply("An answer.")), "first"))
    assert title(platform) == before
    assert naming.requests == []


def test_a_rename_during_the_title_call_wins(platform, naming):
    naming.on_call = lambda: rename(platform, "meanwhile", "Named meanwhile")
    settle(platform, submit(platform, ScriptedAgentStream(reply("An answer.")), "first"))
    assert len(naming.requests) == 1
    assert title(platform) == "Named meanwhile"


@pytest.mark.parametrize("outcome", ["error", "empty", "timeout"])
def test_a_failed_empty_or_slow_title_keeps_the_first_words(platform, naming, monkeypatch, outcome):
    from row_bot.application import conversation_naming

    if outcome == "error":
        naming.error = RuntimeError("Synthetic provider failure")
    elif outcome == "empty":
        naming.answer = '"."'
    else:
        naming.gate = threading.Event()
        monkeypatch.setattr(conversation_naming, "TITLE_TIMEOUT_SECONDS", 0.2)
    settle(platform, submit(platform, ScriptedAgentStream(reply("An answer.")), "first"))
    assert title(platform) == FIRST_WORDS
    if outcome == "timeout":
        naming.gate.set()
        for thread in [thread for thread in threading.enumerate() if thread.name == "conversation-title"]:
            thread.join(10)
        assert title(platform) == FIRST_WORDS, "an answer after the timeout is dropped"
    assert len(naming.requests) == 1, "one attempt"


def test_a_hanging_title_model_never_delays_or_blocks_the_conversation(platform, naming):
    naming.gate = threading.Event()
    fake = ScriptedAgentStream(reply("First answer."), reply("Second answer."))
    first = submit(platform, fake, "first")
    assert platform.registry.get(first["execution_id"]).producer_done.wait(10)
    assert naming.entered.wait(10)
    second = submit(platform, fake, "second", "Another question")
    assert platform.registry.get(second["execution_id"]).producer_done.wait(10)
    assert platform.registry.get(second["execution_id"]).view()["status"] == "completed"
    assert not naming.gate.is_set(), "both turns finished while the title call still hung"
    assert title(platform) == FIRST_WORDS
    naming.gate.set()
    settle()
    assert title(platform) == "Cornwall Coast Walking Trip"
    assert len(naming.requests) == 1
