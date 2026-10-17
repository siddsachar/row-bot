"""Using a personal model for a kind of work (L2c2, D11, D24): a trial with its own numbers, Keep,
Not now, Undo at once (back to the version before or the main model), deleting a version undoes
it everywhere, and the choices hold only counts."""

from __future__ import annotations

import json

import pytest

from row_bot.learning import use

pytestmark = [pytest.mark.subsystem]

DAY = 86_400.0


class Clock:
    def __init__(self, now: float = 1_800_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def test_use_starts_a_trial_and_nothing_is_saved_before(data_dir):
    assert not use.exists(data_dir) and use.read(data_dir).assignments == {}

    item = use.start_trial(1, "document_lookups", "model:ollama:row-bot-personal-v1:abcd", root=data_dir,
                           clock=Clock())

    assert (item.state, item.version, item.previous) == ("trial", 1, None)
    assert use.exists(data_dir)
    saved = use.read(data_dir).assignments["document_lookups"]
    assert saved == item


def test_a_trial_counts_its_own_requests_and_a_fallback_is_a_cloud_finish(data_dir):
    use.start_trial(1, "document_lookups", "model:ollama:p1", root=data_dir, clock=Clock())

    use.record("document_lookups", 1, "answered", 3.0, root=data_dir)
    use.record("document_lookups", 1, "fallback", 9.0, root=data_dir)
    use.record("tool_workflows", 1, "answered", 1.0, root=data_dir)  # not given to it: not counted
    use.record("document_lookups", 2, "answered", 1.0, root=data_dir)  # another version: not counted

    item = use.read(data_dir).assignments["document_lookups"]
    assert (item.trial.requests, item.trial.answered, item.trial.fallbacks) == (2, 1, 1)
    assert item.trial.mean_seconds == 6.0 and item.total == item.trial


def test_keep_is_asked_after_a_few_days_and_enough_requests(data_dir):
    clock = Clock()
    item = use.start_trial(1, "document_lookups", "model:ollama:p1", root=data_dir, clock=clock)
    for _ in range(use.TRIAL_MIN_REQUESTS):
        use.record("document_lookups", 1, "answered", 2.0, root=data_dir)
    item = use.read(data_dir).assignments["document_lookups"]

    assert not use.keep_due(item, now=clock.now + DAY)  # too soon
    assert use.keep_due(item, now=clock.now + use.TRIAL_DAYS * DAY)

    later = use.not_now("document_lookups", root=data_dir, clock=Clock(clock.now + 3 * DAY))
    assert not use.keep_due(later, now=clock.now + 4 * DAY)  # asks again only after as many requests again

    kept = use.keep("document_lookups", root=data_dir, clock=Clock(clock.now + 5 * DAY))
    assert kept.state == "in_use" and not use.keep_due(kept, now=clock.now + 9 * DAY)


def test_undo_goes_back_to_the_main_model_at_once(data_dir):
    use.start_trial(1, "document_lookups", "model:ollama:p1", root=data_dir, clock=Clock())

    back = use.undo("document_lookups", root=data_dir, clock=Clock())

    assert back is None and use.read(data_dir).assignments == {}
    assert [event["event"] for event in use.read(data_dir).history] == ["trial", "undone"]


def test_undo_of_a_new_version_goes_back_to_the_version_kept_before(data_dir):
    use.start_trial(1, "document_lookups", "model:ollama:p1", root=data_dir, clock=Clock())
    use.keep("document_lookups", root=data_dir)
    use.start_trial(2, "document_lookups", "model:ollama:p2", root=data_dir, clock=Clock())

    item = use.read(data_dir).assignments["document_lookups"]
    assert (item.version, item.previous) == (2, 1)
    back = use.undo("document_lookups", root=data_dir, still_ready=lambda version: f"model:ollama:p{version}")

    assert back is not None and (back.version, back.state, back.model_ref) == (1, "in_use", "model:ollama:p1")
    gone = use.start_trial(3, "document_lookups", "model:ollama:p3", root=data_dir)
    assert gone.previous == 1  # v1 is still the one kept
    assert use.undo("document_lookups", root=data_dir, still_ready=lambda version: None) is None  # v1 gone too


def test_deleting_a_version_undoes_every_kind_it_answers(data_dir):
    use.start_trial(1, "document_lookups", "model:ollama:p1", root=data_dir)
    use.keep("document_lookups", root=data_dir)
    use.start_trial(2, "document_lookups", "model:ollama:p2", root=data_dir)
    use.start_trial(2, "tool_workflows", "model:ollama:p2", root=data_dir)

    undone = use.forget_version(2, root=data_dir, still_ready=lambda version: f"model:ollama:p{version}")

    state = use.read(data_dir).assignments
    assert sorted(undone) == ["document_lookups", "tool_workflows"]
    assert state["document_lookups"].version == 1 and "tool_workflows" not in state
    use.forget_version(1, root=data_dir)
    assert use.read(data_dir).assignments == {}


def test_only_the_four_kinds_can_be_given_and_a_missing_choice_is_refused(data_dir):
    with pytest.raises(use.UseError):
        use.start_trial(1, "other", "model:ollama:p1", root=data_dir)
    with pytest.raises(use.UseError):
        use.keep("document_lookups", root=data_dir)
    with pytest.raises(use.UseError):
        use.undo("tool_workflows", root=data_dir)


def test_the_choices_file_holds_counts_never_requests_or_answers(data_dir):
    use.start_trial(1, "document_lookups", "model:ollama:p1", root=data_dir)
    use.record("document_lookups", 1, "answered", 2.5, root=data_dir)

    saved = json.loads(use.use_path(data_dir).read_text(encoding="utf-8"))

    assert set(saved) == {"schema_version", "assignments", "history"}
    assert set(saved["assignments"]["document_lookups"]["trial"]) == {"requests", "answered", "fallbacks", "seconds"}


def test_an_unreadable_file_reads_as_nothing_in_use(data_dir):
    use.use_path(data_dir).parent.mkdir(parents=True)
    use.use_path(data_dir).write_text("{not json", encoding="utf-8")

    assert use.read(data_dir).assignments == {}
