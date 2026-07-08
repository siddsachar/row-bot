from __future__ import annotations

import queue
import threading
from types import SimpleNamespace

from row_bot.cancellation import CancellationScope
from row_bot.ui.state import GenerationState, _active_generations
from row_bot.ui import streaming


class _FakeButton:
    client = None

    def __init__(self) -> None:
        self.props_calls: list[str] = []
        self.disabled = False

    def props(self, value: str) -> "_FakeButton":
        self.props_calls.append(value)
        return self

    def disable(self) -> "_FakeButton":
        self.disabled = True
        return self


def _generation(thread_id: str = "thread-stop") -> GenerationState:
    stop_event = threading.Event()
    scope = CancellationScope(stop_event)
    return GenerationState(
        thread_id=thread_id,
        q=queue.Queue(),
        stop_event=stop_event,
        config={},
        enabled_tools=[],
        cancel_scope=scope,
        generation_id="gen-1",
    )


def teardown_function() -> None:
    _active_generations.clear()


def test_request_generation_stop_detaches_wakes_queue_and_cancels_scope(monkeypatch) -> None:
    gen = _generation()
    callback_calls: list[str] = []
    gen.cancel_scope.register(lambda: callback_calls.append("closed"), "fake.close")
    _active_generations[gen.thread_id] = gen
    monkeypatch.setattr(streaming, "_stop_generation_child_agent_runs", lambda _gen: None)
    button = _FakeButton()
    state = SimpleNamespace(thread_id=gen.thread_id, voice_coordinator=None, tts_service=None)
    p = SimpleNamespace(stop_btn=button)

    result = streaming.request_generation_stop(gen.thread_id, state=state, p=p, reason="test")

    assert result.status == "stopped"
    assert result.stopped is True
    assert gen.status == "stopped"
    assert gen.stop_event.is_set() is True
    assert gen.cancel_scope.is_cancelled() is True
    assert callback_calls == ["closed"]
    assert _active_generations.get(gen.thread_id) is None
    assert button.disabled is True
    assert "icon=stop" in button.props_calls
    assert gen.q.get_nowait() == ("stopped", {"reason": "test"})


def test_request_generation_stop_handles_idle_generation_without_active_entry() -> None:
    fallback_event = threading.Event()
    button = _FakeButton()
    state = SimpleNamespace(thread_id="missing", stop_event=fallback_event)
    p = SimpleNamespace(stop_btn=button)

    result = streaming.request_generation_stop("missing", state=state, p=p)

    assert result.status == "not_found"
    assert fallback_event.is_set() is True
    assert button.disabled is True


def test_generation_stop_stops_only_generation_linked_child_runs(monkeypatch) -> None:
    gen = _generation()
    gen.baseline_child_agent_run_ids = {"old"}
    gen.live_agent_run_ids = {"live"}
    gen.live_async_agent_run_ids = {"async"}
    stopped: list[str] = []

    monkeypatch.setattr(streaming, "_child_agent_run_ids_for_thread", lambda _thread_id: {"old", "new"})

    import row_bot.agent_runner as agent_runner

    monkeypatch.setattr(agent_runner, "stop_agent_run", lambda run_id: stopped.append(run_id))

    streaming._stop_generation_child_agent_runs(gen)

    assert stopped == ["async", "live", "new"]
