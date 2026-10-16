"""Memory extraction and personal-model training never share the graphics card (O41): with a
local model, extraction counts as heavy work in the lease (training doesn't start, Train now
waits) and waits itself while a personal model trains. On a cloud model they don't meet."""

from __future__ import annotations

import sys

import pytest

import row_bot.memory_extraction as memory_extraction  # bound to the session's folder first
from row_bot.learning import lease as lease_module
from row_bot.learning.lease import LeaseState, ResourceLease
from tests.fixtures.learning import FakeLease

pytestmark = pytest.mark.subsystem


def _lease(**probes) -> ResourceLease:
    defaults = {"generations": lambda: [], "workflows": lambda: False, "indexing": lambda: False,
                "dreaming": lambda: False, "since_activity": lambda: 3600.0}
    return ResourceLease(**{**defaults, **probes})


def test_extraction_on_a_local_model_holds_the_computer():
    state = _lease(extracting=lambda: True).read()

    assert state.extraction and state.busy and not state.free
    assert state.holder == "extraction"


def test_the_lease_reads_extraction_only_when_its_model_is_local(data_dir, monkeypatch):
    extraction = memory_extraction
    monkeypatch.setattr(extraction, "_running_model", "model:ollama:qwen3:8b")
    monkeypatch.setattr(lease_module, "model_is_local", lambda ref: ref.startswith("model:ollama:"))
    assert lease_module._extracting() is True  # noqa: SLF001 - the default probe

    monkeypatch.setattr(extraction, "_running_model", "model:openai:gpt-5.6-sol")
    assert lease_module._extracting() is False  # noqa: SLF001

    monkeypatch.setattr(extraction, "_running_model", None)
    assert lease_module._extracting() is False  # noqa: SLF001


@pytest.fixture
def extraction(data_dir, monkeypatch):
    import row_bot.models as models

    module = memory_extraction

    seen: list[str | None] = []
    monkeypatch.setattr(models, "get_current_model", lambda: "model:ollama:qwen3:8b")
    monkeypatch.setattr(module, "_read_conversations",
                        lambda *args, **kwargs: seen.append(module.running_model()) or 3)
    return module, seen


def test_extraction_says_which_model_it_uses_while_it_runs(extraction):
    module, seen = extraction

    assert module.run_extraction() == 3

    assert seen == ["model:ollama:qwen3:8b"] and module.running_model() is None


def test_extraction_on_a_local_model_waits_while_a_personal_model_trains(extraction, monkeypatch):
    module, seen = extraction
    monkeypatch.setattr(lease_module, "model_is_local", lambda ref: True)
    lease_module.claim_training()
    try:
        statuses: list[str] = []
        assert module.run_extraction(on_status=statuses.append) == 0
    finally:
        lease_module.release_training()

    assert seen == [] and statuses == ["Waiting while your personal model trains"]
    assert module.run_extraction() == 3  # once training ends it runs


def test_extraction_on_a_cloud_model_runs_during_training(extraction, monkeypatch):
    module, seen = extraction
    monkeypatch.setattr(lease_module, "model_is_local", lambda ref: False)
    lease_module.claim_training()
    try:
        assert module.run_extraction() == 3
    finally:
        lease_module.release_training()
    assert seen == ["model:ollama:qwen3:8b"]


def test_extraction_without_learning_loaded_never_looks_at_the_lease(extraction, monkeypatch):
    module, seen = extraction
    monkeypatch.delitem(sys.modules, "row_bot.learning.lease")

    assert module.run_extraction() == 3


@pytest.mark.platform
def test_train_now_waits_for_extraction_and_says_so(learning_on, clock):
    from row_bot.learning.runner import LearningRunner
    from row_bot.learning.training.supervisor import TrainingSupervisor
    from tests.fixtures.learning import FakeDataset, FakeEngine

    lease = FakeLease(extraction=True, since_activity=3600.0)
    supervisor = TrainingSupervisor(root=learning_on, clock=clock, engine=FakeEngine(), dataset=FakeDataset(),
                                    lease=lease, start_threads=False)
    job = supervisor.jobs.plan(steps_total=10, checkpoint_every=5)

    waiting = supervisor.train_now(job.id)

    assert waiting.state == "waiting"
    assert LearningRunner(supervisor, root=learning_on).status().reason == "extraction"
    assert LeaseState(extraction=True).holder == "extraction"
