"""The Learning screens refresh live (O127): when Learning's state changes, the ``learning`` live topic moves,
so every open window reads Learning again at once (B407's stream). A setting saved, a command, a job planned,
running or ended, a version's phase, a comparison's or a change test's start and end, the vault taking new work, a
personal model's use, a deleted chat that fed something and the quiet hours move it; capture's records and a job's
progress don't, and a comparison's or a test's count moves it at most every ``PROGRESS_SECONDS`` (FL5)."""

from __future__ import annotations

from dataclasses import replace

import pytest
from langchain_core.messages import AIMessage

from row_bot import learning, threads  # noqa: F401 - bind threads to the session's folder first
from row_bot.application import live_updates as live_module
from row_bot.application.live_updates import LiveUpdates
from row_bot.learning import evaluation, model_compare, use
from row_bot.learning.contracts import Job
from row_bot.learning.store import LearningStore
from row_bot.learning.training import releases
from tests.fixtures.learning import START, CapturedWork, episode, release
from tests.subsystem.learning.test_learning_compare import FakeWorker, _comparer, ready
from tests.subsystem.learning.test_learning_surfaces_api import _command, _ok, _turn_on, api, no_channels

# ruff: noqa: F401, F811 -- the imported pytest fixture is requested by name.

pytestmark = pytest.mark.subsystem


@pytest.fixture
def live(monkeypatch):
    fresh = LiveUpdates()
    monkeypatch.setattr(live_module, "live_updates", fresh)
    return fresh


def _moved(live: LiveUpdates, before: dict) -> set[str]:
    after = live.frame()["topics"]
    return {topic for topic, revision in after.items() if revision != before[topic]}


def _learning_moves(live: LiveUpdates, change) -> bool:
    before = live.frame()["topics"]
    change()
    moved = _moved(live, before)
    assert moved <= {"learning"}, moved
    return moved == {"learning"}


def test_a_setting_saved_and_a_version_moving_through_training_publish_learning(learning_on, live):
    from row_bot.application.settings_commands import write_learning_settings

    store = LearningStore(learning_on)
    job = Job(id="job_v2", kind="training", created_at=START, updated_at=START, state="planned", version=2)

    assert _learning_moves(live, lambda: write_learning_settings({"monthly_token_limit": 50_000_000}))
    assert _learning_moves(live, lambda: store.put(job))  # v2 planned
    running = replace(job, state="running", steps_total=120)
    assert _learning_moves(live, lambda: store.put(running))
    # Progress alone isn't a change of state: the training view polls it while it shows.
    assert not _learning_moves(live, lambda: store.put(replace(running, steps_done=60)))
    assert _learning_moves(live, lambda: store.put(replace(running, state="failed", error_code="engine_error")))

    folder = releases.version_dir(2, learning_on)
    assert _learning_moves(live, lambda: releases.write_state(folder, releases.VersionState(2, "exporting")))
    assert _learning_moves(live, lambda: releases.write_state(folder, releases.VersionState(2, "ready")))


def test_a_comparison_publishes_when_it_starts_moves_on_and_ends_never_per_case(ready, live):
    before = live.frame()["topics"]["learning"]

    _comparer(ready).run(1)

    asked = len(FakeWorker.instances[0].asked)
    published = live.frame()["topics"]["learning"] - before
    # Started, visible, hidden, done and its saved test runs: a handful, never one per task asked.
    assert asked == 90 and 4 <= published <= 8
    assert model_compare.read_progress(1, ready)["state"] == "done"


def test_the_vault_taking_new_work_publishes_and_an_empty_refresh_does_not(writer, chats, clock, learning_on, live):
    work = CapturedWork(writer, chats, clock)
    for number in range(8):
        work.turn(f"chat-{number}", f"What is {number} times seven?", f"It is {number * 7}.")

    assert _learning_moves(live, lambda: evaluation.refresh_audit(store=LearningStore(learning_on), asked_by="test"))
    assert not _learning_moves(live, lambda: evaluation.refresh_audit(store=LearningStore(learning_on),
                                                                       asked_by="test"))


def test_capture_never_publishes_and_a_personal_models_use_does(writer, chats, clock, learning_on, live):
    work = CapturedWork(writer, chats, clock)

    assert not _learning_moves(live, lambda: work.turn("chat-1", "What is six times seven?", "It is 42."))
    turn = writer.begin_turn(thread_id="chat-1", surface="normal_chat")
    writer.model_call(turn, messages=[], response=AIMessage(content="Yes.", id="a-later"))
    assert not _learning_moves(live, writer.drain)
    personal = "model:ollama:row-bot-personal-v1:abcd1234"
    assert _learning_moves(live, lambda: use.start_trial(1, "document_lookups", personal, root=learning_on))
    # A routed request the personal model answered (the trial's counts on the Personal model view).
    assert _learning_moves(live, lambda: use.record("document_lookups", 1, "answered", 3.0, root=learning_on))


def test_a_learning_command_publishes_for_every_other_window(api, no_channels, live):
    client, headers, _data, _ = api
    _turn_on(client, headers)

    before = live.frame()["topics"]
    receipt = _ok(_command(client, headers, "pause"))

    assert receipt["snapshot"]["state"] == "paused" and _moved(live, before) == {"learning"}
    assert learning.read_settings().paused is True


def test_a_comparisons_count_moves_the_screens_at_a_slow_pace(ready, live):
    # FL5: the count ("70 of 489 tasks") reaches open screens too, at most every PROGRESS_SECONDS.
    frozen = _comparer(ready)
    frozen.clock = lambda: 1.0
    before = live.frame()["topics"]["learning"]
    frozen.run(1)
    still = live.frame()["topics"]["learning"] - before
    model_compare.comparison_path(1, ready).unlink()

    now = [0.0]

    def clock() -> float:
        now[0] += 1.0  # each read of the clock is a second later
        return now[0]

    moving = _comparer(ready)
    moving.clock = clock
    before = live.frame()["topics"]["learning"]
    moving.run(1)
    moved = live.frame()["topics"]["learning"] - before

    assert moved > still  # the count went out as it moved on
    assert moved - still <= now[0] / learning.PROGRESS_SECONDS + 1  # never more often than the pace


def test_a_deleted_chat_that_fed_a_version_moves_the_screens(learning_on, live):
    # FL6: Needs you can then offer "Retrain without it" at once.
    store = LearningStore(learning_on)
    store.put_many([episode("chat-1"), release(source_thread_ids=("chat-1",))])

    assert _learning_moves(live, lambda: store.purge_thread("chat-1"))
    assert not _learning_moves(live, lambda: store.purge_thread("chat-2"))  # it fed nothing


def test_moving_the_quiet_hours_moves_the_screens(learning_on, live):
    # FL5: Learning's next look is in the same quiet hours as Dream Cycle.
    from row_bot.application.settings_commands import apply_settings_change

    assert _learning_moves(live, lambda: apply_settings_change("preferences", "dream_cycle.window_start", 11))
