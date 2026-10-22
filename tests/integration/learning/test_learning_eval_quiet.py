"""Learning's evaluation never reaches the person's desktop (L2v). The comparison runs in quiet hours
through the real Agent runtime; a test turn that runs out of its token budget, or whose provider fails,
must not pop a desktop notification or play the error sound. Real Agent turns with a fake model."""

from __future__ import annotations

import pytest

from row_bot import notifications
from row_bot.agent_budget import ModelCallRefused
from row_bot.learning import eval_worker
from row_bot.providers import usage_ledger
from tests.helpers.learning_turns import AgentTurns, ScriptedModel, says

pytestmark = [pytest.mark.integration]


class FailingModel(ScriptedModel):
    """Fails every call the way a local runtime's parser does (Ollama's HTTP 500)."""

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        raise RuntimeError("Error code: 500 - XML syntax error on line 8: element <function> closed by </parameter>")


@pytest.fixture
def desktop(monkeypatch):
    """What reached the desktop: every notification shown and every sound played."""

    reached: list[tuple[str, tuple]] = []
    monkeypatch.setattr(notifications, "_desktop_notify", lambda *args: reached.append(("notification", args)))
    monkeypatch.setattr(notifications, "_play_sound", lambda *args: reached.append(("sound", args)))
    return reached


@pytest.fixture
def budget_gate(monkeypatch):
    """The worker's per-task token gate on the usage ledger's callback (undone afterwards)."""

    for name in ("on_chat_model_start", "on_llm_end", "raise_error"):
        monkeypatch.setattr(usage_ledger.UsageLedgerCallback, name, getattr(usage_ledger.UsageLedgerCallback, name))
    gate = eval_worker._TaskGate()  # noqa: SLF001 - the worker's own gate
    eval_worker._install_gate(gate)  # noqa: SLF001
    return gate


def test_a_turn_refused_by_its_token_budget_ends_quietly_as_out_of_budget(learning_dir, monkeypatch, desktop,
                                                                          budget_gate):
    model = ScriptedModel(responses=[says("never sent")], seen=[])
    usage_ledger.attach_usage_ledger(model, provider_id="ollama", model_name="row-bot-personal-v1")
    turns = AgentTurns(monkeypatch, model, [])
    budget_gate.reset(10, None)  # less than any request: the first call is refused before it is sent

    events = turns.send("What is the travel policy's dinner limit?")

    errors = [str(payload) for kind, payload in events if kind == "error"]
    assert errors == ["learning evaluation budget: the task's token budget is spent"]
    assert model.seen == [] and budget_gate.refused == "task"  # nothing was sent; the worker counts it as budget
    assert desktop == []  # no notification, no sound


def test_a_refused_call_is_its_own_kind_of_failure_never_an_api_error():
    gate = eval_worker._TaskGate()  # noqa: SLF001
    gate.reset(10, None)

    with pytest.raises(ModelCallRefused):
        gate.admit("call", 5_000, local=True)


def test_a_provider_error_in_the_workers_process_reaches_no_desktop(learning_dir, monkeypatch, desktop):
    monkeypatch.setenv(notifications.DESKTOP_SWITCH, "off")  # as the worker's boot and environment set it
    turns = AgentTurns(monkeypatch, FailingModel(responses=[says("unused")], seen=[]), [])

    events = turns.send("Move my dentist appointment to 10:00.")

    assert [kind for kind, _ in events if kind == "error"] == ["error"]
    assert desktop == []


def test_the_same_provider_error_in_the_persons_app_still_notifies(learning_dir, monkeypatch, desktop):
    monkeypatch.delenv(notifications.DESKTOP_SWITCH, raising=False)
    turns = AgentTurns(monkeypatch, FailingModel(responses=[says("unused")], seen=[]), [])

    turns.send("Move my dentist appointment to 10:00.")

    assert [kind for kind, _ in desktop] == ["notification", "sound"]  # the person's app is unchanged


def test_the_worker_starts_with_the_desktop_switched_off(tmp_path, monkeypatch):
    import importlib.util

    environment = eval_worker.worker_environment(tmp_path / "data")
    assert environment[notifications.DESKTOP_SWITCH] == "off" == environment[eval_worker.DESKTOP_SWITCH]

    # Its boot switches it off too, before the worker (and any Row-Bot module it imports) runs.
    monkeypatch.setenv(notifications.DESKTOP_SWITCH, "on")
    seen: list[str] = []
    monkeypatch.setattr(eval_worker, "worker_main", lambda: seen.append(
        __import__("os").environ[notifications.DESKTOP_SWITCH]) or 0)
    spec = importlib.util.spec_from_file_location("eval_worker_boot_under_test", eval_worker.BOOT)
    boot = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(boot)

    assert boot.main([]) == 0 and seen == ["off"] and not notifications.desktop_enabled()
