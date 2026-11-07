"""The backup model finishing real Agent turns (#140).

The graph is the one ``agent.get_agent_graph`` builds; only the provider and the tools are fakes.
One scripted model stands in for both models and fails as the main model when told to, so a test
sees exactly which model each call ran on. Nothing reaches a provider.
"""
from __future__ import annotations

import threading
from typing import Any

import pytest
from langchain_core.tools import tool

from row_bot.providers import backup
from tests.helpers.learning_turns import AgentTurns, ScriptedModel, asks, call, says

pytestmark = pytest.mark.subsystem

CLOUD = "model:openai:gpt-5-mini"


class StatusError(Exception):
    def __init__(self, status: int, text: str = "") -> None:
        super().__init__(text or f"Error code: {status}")
        self.status_code = status


class TwoModels(ScriptedModel):
    """Answers in order; fails as the main model after ``main_answers`` of its calls."""

    refs: list = []
    failure: Any = None
    main_answers: int = 0
    backup_failure: Any = None
    stop: Any = None

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        from row_bot.models import _active_model_override

        ref = _active_model_override.get("")
        self.refs.append(ref)
        if ref == CLOUD and self.backup_failure is not None:
            raise self.backup_failure
        if ref != CLOUD and self.failure is not None and self.refs.count(ref) > self.main_answers:
            if self.stop is not None:
                self.stop.set()  # the person pressed Stop as it failed
            raise self.failure
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


reads: list[str] = []


@tool
def read_note(name: str) -> str:
    """Read one of the person's notes."""
    reads.append(name)
    return f"The {name} note says: water the plants."


@tool
def delete_file(path: str) -> str:
    """Delete one of the person's files (asks first)."""
    from row_bot.tools import approval_gate

    refusal = approval_gate.gate_action({"tool": "delete_file", "label": "Delete a file",
                                         "description": f"Delete {path}"})
    return refusal or f"Deleted {path}."


@pytest.fixture
def turns(monkeypatch, tmp_path):
    from row_bot.providers import saved_model_settings

    monkeypatch.setattr(saved_model_settings, "SETTINGS_PATH", tmp_path / "model_settings.json")
    backup.save_setting(enabled=True, ref=CLOUD)
    reads.clear()

    def start(failure: Any = None, *responses, tools=(read_note,), **fields) -> AgentTurns:
        model = TwoModels(responses=list(responses), seen=[], refs=[], failure=failure, **fields)
        return AgentTurns(monkeypatch, model, list(tools))

    return start


def _rows(thread_id: str) -> list[dict]:
    from row_bot.runtime.checkpoint_reader import open_checkpoint

    with open_checkpoint(thread_id) as reader:
        return [reader.public_row(record) for _, record in reader.records()]


def _notes(thread_id: str) -> list[tuple[str, str]]:
    return [(row.get("note") or "", " ".join(block.get("text", "") for block in row["blocks"]))
            for row in _rows(thread_id) if row.get("note")]


@pytest.mark.parametrize(("failure", "why"), [
    (StatusError(503, "Service Unavailable"), "it had a server error"),
    (ConnectionError("Failed to connect to Ollama. Please check that Ollama is running."), "it couldn't be reached"),
    (TimeoutError("Request timed out."), "it timed out"),
    (StatusError(429, "Rate limit reached"), "it's busy right now"),
    (StatusError(402, "Insufficient credits"), "it's out of credit"),
    (StatusError(401, "Incorrect API key provided"), "its sign-in expired"),
])
def test_the_backup_answers_when_the_main_model_cant_and_the_chat_says_so(turns, failure, why):
    conversation = turns(failure, says("Here is the plan."))

    events = conversation.send("Plan my day.")

    kinds = [kind for kind, _ in events]
    assert "error" not in kinds and "provider_refusal" not in kinds
    [switched] = [payload for kind, payload in events if kind == backup.EVENT]
    line = f"gpt-4.1 didn't answer ({why}), so gpt-5-mini took over."
    assert switched == {"note": line, "reason": switched["reason"], "model_ref": CLOUD}
    assert ("done", "Here is the plan.") in events
    assert conversation.model.refs == [conversation.model_ref, CLOUD], "one switch, main model first"
    assert _notes(conversation.thread_id) == [("backup_model", line)]
    rows = _rows(conversation.thread_id)
    assert [row["role"] for row in rows] == ["user", "user", "assistant"]
    assert rows[0]["blocks"][0]["text"] == "Plan my day." and not rows[0].get("note")
    assert "My main model couldn't answer" not in str(rows), "the hand-over is never shown"
    # The backup was told the request it is finishing.
    assert "Plan my day." in str(conversation.model.seen[-1][-1].content)


def test_a_sign_in_the_provider_refuses_goes_to_the_backup_without_its_card(turns):
    from row_bot.providers.runtime import ProviderSignInRequired

    conversation = turns(ProviderSignInRequired("codex", "ChatGPT", "Sign in to ChatGPT again"), says("Done."))

    events = conversation.send("Hello")

    assert [kind for kind, _ in events if kind in {"provider_refusal", "error", backup.EVENT}] == [backup.EVENT]
    assert ("done", "Done.") in events


@pytest.mark.parametrize("failure", [
    StatusError(400, "Error code: 400 - invalid_request_error"),
    RuntimeError("This model does not support tools"),
    RuntimeError("Your request was flagged by our content policy"),
])
def test_a_request_shaped_failure_never_switches(turns, failure):
    conversation = turns(failure, says("never"))

    events = conversation.send("Hello")

    assert backup.EVENT not in [kind for kind, _ in events]
    assert any(kind == "error" for kind, _ in events)
    assert conversation.model.refs == [conversation.model_ref]
    assert _notes(conversation.thread_id) == []


def test_context_too_long_never_switches(turns):
    conversation = turns(ValueError("Error code: 400 - prompt is too long: 250000 tokens > 200000 maximum"),
                         says("never"))

    events = conversation.send("Hello")

    assert backup.EVENT not in [kind for kind, _ in events]
    assert CLOUD not in conversation.model.refs


def test_stop_never_switches(turns):
    stop = threading.Event()
    conversation = turns(StatusError(503), says("never"), stop=stop)

    events = conversation.send("Hello", stop_event=stop)

    assert backup.EVENT not in [kind for kind, _ in events]
    assert conversation.model.refs == [conversation.model_ref]


def test_with_the_backup_off_the_failure_shows_as_today(turns):
    conversation = turns(StatusError(503, "Service Unavailable"), says("never"))
    backup.save_setting(enabled=False)

    events = conversation.send("Hello")

    assert [kind for kind, _ in events if kind in {"error", backup.EVENT}] == ["error"]
    assert conversation.model.refs == [conversation.model_ref]


def test_an_incognito_chat_never_goes_to_a_cloud_backup(turns):
    from row_bot import threads

    conversation = turns(StatusError(503), says("never"))
    threads.set_thread_incognito(conversation.thread_id, True)

    events = conversation.send("Hello")

    assert backup.EVENT not in [kind for kind, _ in events]
    assert CLOUD not in conversation.model.refs


def test_a_failure_after_a_tool_ran_is_finished_without_running_it_again(turns):
    conversation = turns(StatusError(503), asks(call("read_note", "c1", name="home")),
                         says("Your home note says: water the plants."), main_answers=1)

    events = conversation.send("What does my home note say?")

    assert reads == ["home"], "the tool ran once"
    assert [kind for kind, _ in events if kind in {"error", backup.EVENT}] == [backup.EVENT]
    assert ("done", "Your home note says: water the plants.") in events
    # The backup saw the tool's result already in the conversation.
    assert any("water the plants" in str(message.content) for message in conversation.model.seen[-1])


def test_a_failure_after_an_approval_goes_to_the_backup(turns):
    conversation = turns(StatusError(503), asks(call("delete_file", "d1", path="old.txt")),
                         says("Deleted old.txt."), tools=(delete_file,), main_answers=1)

    paused = conversation.send("Delete old.txt")
    events = conversation.answer(True, paused)

    assert [kind for kind, _ in events if kind in {"error", backup.EVENT}] == [backup.EVENT]
    assert ("done", "Deleted old.txt.") in events


def test_when_both_fail_one_error_names_both(turns):
    conversation = turns(StatusError(503), says("never"), backup_failure=StatusError(503, "Service Unavailable"))

    events = conversation.send("Hello")

    errors = [payload for kind, payload in events if kind == "error"]
    assert len(errors) == 1 and errors[0].startswith("⚠️ Neither gpt-4.1 nor gpt-5-mini answered — ")
    assert conversation.model.refs == [conversation.model_ref, CLOUD]


# ── The app's own path: a chat message through the platform's turn runner ─────
# Nothing below fakes the agent stream: the message goes through the platform, ``agent.stream_agent``
# and ``backup.stream``; the main model is refused by the real readiness check (Ollama faked as not
# running, nothing else), and only the backup's graph and model are fakes.

from types import SimpleNamespace  # noqa: E402

from tests.contracts.client_platform.test_headless_lifecycle import command, platform  # noqa: E402,F401
from tests.helpers.client_platform_fakes import fixture_id  # noqa: E402

LOCAL_DOWN = "model:ollama:gpt-oss:20b"


@pytest.fixture
def app_turn(platform, monkeypatch, tmp_path):  # noqa: F811
    import sqlite3

    import row_bot.agent as agent
    from row_bot import models, threads
    from row_bot.providers import readiness, saved_model_settings

    monkeypatch.setattr(saved_model_settings, "SETTINGS_PATH", tmp_path / "model_settings.json")
    backup.save_setting(enabled=True, ref=CLOUD)
    monkeypatch.setattr(models, "_ollama_reachable", lambda *_a, **_k: False)  # Ollama isn't running
    real_graph, real_readiness = agent.get_agent_graph, readiness.evaluate_runtime_readiness
    model = TwoModels(responses=[says("Here is the plan.")], seen=[], refs=[])
    AgentTurns(monkeypatch, model, [read_note])  # the backup's graph, on a fake model
    backup_graph = agent.get_agent_graph

    state = SimpleNamespace(stop=False)

    def graph(enabled=None, model_override=None, **kwargs):
        if model_override == CLOUD:
            return backup_graph(enabled, model_override=model_override, **kwargs)
        if state.stop:  # the person presses Stop as the main model fails
            for handle in platform.registry.active("conversation-a"):
                platform.registry.cancel(handle)
        return real_graph(enabled, model_override=model_override, **kwargs)

    ready = SimpleNamespace(ready=True, provider_id="openai", context_window=200_000)
    monkeypatch.setattr(agent, "get_agent_graph", graph)
    monkeypatch.setattr(readiness, "evaluate_runtime_readiness", lambda ref, *a, **k: SimpleNamespace(
        agent=ready, chat=ready, selected_mode="agent", selection_reason="ready", timings={},
    ) if ref == CLOUD else real_readiness(ref, *a, **k))
    platform.stream_factory = None
    platform.resume_factory = None

    def send(text: str, *, runtime_mode: str = "agent") -> list[dict]:
        with sqlite3.connect(threads.DB_PATH) as connection:
            connection.execute("UPDATE thread_meta SET client_runtime_mode=? WHERE thread_id='conversation-a'",
                               (runtime_mode,))
        receipt = platform.execute(owner_id="fixture-owner", idempotency_key=fixture_id(text + ":key"),
                                   target="conversation-a", command=command("conversation.submit", text, {
                                       "submission_id": fixture_id(text), "text": text, "attachment_refs": [],
                                       "model_selection": {"provider_id": "ollama", "model_ref": LOCAL_DOWN}}))
        assert platform.registry.get(receipt["execution_id"]).producer_done.wait(20)
        return platform.snapshot("conversation-a")["rows"]

    return SimpleNamespace(send=send, model=model, platform=platform, state=state)


@pytest.mark.parametrize("runtime_mode", ["agent", "auto"])
def test_a_chat_message_whose_local_model_isnt_running_is_answered_by_the_backup(app_turn, runtime_mode):
    rows = app_turn.send("Plan my day.", runtime_mode=runtime_mode)

    texts = [(row["role"], row.get("note"), " ".join(b.get("text", "") for b in row["blocks"])) for row in rows]
    assert texts[0] == ("user", None, "Plan my day.")
    assert texts[1] == ("user", "backup_model",
                        "gpt-oss:20b didn't answer (it isn't available), so gpt-5-mini took over.")
    assert texts[-1][0] == "assistant" and texts[-1][2] == "Here is the plan."
    assert app_turn.model.refs == [CLOUD]


def _row_notes(rows: list[dict]) -> list[str | None]:
    return [row.get("note") for row in rows]


def test_a_chat_message_stopped_as_its_model_fails_never_goes_to_the_backup(app_turn):
    app_turn.state.stop = True

    rows = app_turn.send("Plan my day.")

    assert "backup_model" not in _row_notes(rows)
    assert app_turn.model.refs == [], "the backup is never asked"


def test_a_learning_routed_chat_message_never_goes_to_the_backup(app_turn, monkeypatch):
    """Learning finishes a stuck personal model with the main model itself: no backup on top."""
    from row_bot import learning
    from row_bot.learning import routing

    route = routing.Route("writing", 1, "trial", "model:ollama:row-bot-personal-v1", LOCAL_DOWN, "gpt-oss:20b")
    monkeypatch.setattr(learning, "route_turn", lambda **_kwargs: route)

    rows = app_turn.send("Draft my note.")

    assert "backup_model" not in _row_notes(rows)
    assert app_turn.model.refs == [], "the backup is never asked"


def test_when_the_backup_cant_take_over_the_log_says_why(app_turn, caplog):
    import logging

    # A request-shaped failure: the backup is on, but this isn't its case.
    with caplog.at_level(logging.WARNING, logger="row_bot.providers.backup"):
        list(backup.stream(lambda: iter([("error", "⚠️ API error: bad request")]),
                           lambda _config: iter(()),
                           {"configurable": {"model_override": LOCAL_DOWN}}, "hi"))

    assert "The backup model didn't take over" in caplog.text
