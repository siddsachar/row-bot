"""Planning training (L2c): a run is planned only when the engine is ready, nothing else is current,
there are enough examples (else "Needs more examples" with the count) and, after a version, enough
new ones; the rows are measured, written into the job's own lease folder before it starts, and the
job carries the recipe of this computer's tier."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from row_bot.learning.training import jobs as jobs_module
from row_bot.learning.training import planner as planner_module
from row_bot.learning.training import recipes
from tests.fixtures.learning import CapturedWork, FakeClock, FakeEngine

pytestmark = [pytest.mark.subsystem]


@pytest.fixture
def work(writer, chats, clock):
    return CapturedWork(writer, chats, clock)


@pytest.fixture
def setup(learning_on, chats, clock):
    recipe = replace(recipes.load("16gb"), min_examples=3, min_new_examples=2)
    measured: list[int] = []
    trained: set[str] = set()

    def measure(rows, max_seq):
        measured.append(len(rows))
        return [(100, 10) for _ in rows]

    jobs = jobs_module.TrainingJobs(learning_on, clock=clock)
    engine = FakeEngine()
    planner = planner_module.Planner(learning_on, jobs=jobs, engine=engine, clock=clock, recipe_for=lambda: recipe,
                                     measure=measure, lookup=chats, roots=lambda: (), trained_ids=lambda: trained)
    return planner, jobs, engine, recipe, measured, trained


def _turns(work, count: int, start: int = 0) -> None:
    for index in range(start, start + count):
        work.turn(f"chat-{index}", f"Question {index}", f"Answer {index}.")


def test_enough_examples_plan_a_run_with_its_rows_already_leased(work, setup, monkeypatch):
    from row_bot.learning import evaluation

    monkeypatch.setattr(evaluation, "refresh_audit", lambda **_kwargs: {"added": 0})
    planner, jobs, _engine, recipe, measured, _trained = setup
    _turns(work, 4)

    outcome = planner.plan(force=True)

    job = outcome.job
    assert job is not None and job.state == "planned" and job.version == 1
    assert job.steps_total == recipe.steps_for(4) == 3  # one step a pass, three passes (O114)
    plan = jobs.plan_of(job)
    assert plan["recipe"]["name"] == "16gb-v1" and plan["recipe"]["settings"]["max_seq_length"] == 16_384
    assert plan["dataset"]["examples"] == 4 and plan["dataset"]["id"].startswith("ds_")
    run = jobs_module.run_dir(job.id, planner.root)
    rows = (run / "data" / "examples.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(rows) == 4 and json.loads((run / "lease.json").read_text(encoding="utf-8"))["examples"] == 4
    assert measured == [4]  # every row measured with the engine before planning
    assert outcome.summary.examples == 4 and outcome.summary.enough


def test_too_few_examples_say_how_many_there_are(work, setup):
    planner, jobs, *_ = setup
    _turns(work, 2)

    outcome = planner.plan(force=True)

    assert outcome.job is None and outcome.reason == "no_data"
    assert (outcome.summary.examples, outcome.summary.needed) == (2, 3)
    assert jobs.all() == []


def test_no_engine_or_a_current_run_plans_nothing(work, setup, monkeypatch):
    from row_bot.learning import evaluation

    monkeypatch.setattr(evaluation, "refresh_audit", lambda **_kwargs: {"added": 0})
    planner, jobs, engine, *_ = setup
    _turns(work, 4)
    engine.is_ready = False
    assert planner.plan(force=True).reason == "no_engine"

    engine.is_ready = True
    first = planner.plan(force=True).job
    second = planner.plan(force=True)

    assert first is not None and second.reason == "current" and len(jobs.all()) == 1


def test_after_a_version_only_enough_new_examples_plan_the_next(work, setup, monkeypatch):
    from row_bot.learning import evaluation

    monkeypatch.setattr(evaluation, "refresh_audit", lambda **_kwargs: {"added": 0})
    planner, jobs, _engine, _recipe, _measured, trained = setup
    _turns(work, 4)
    first = planner.plan(force=True).job
    for state in ("preparing", "running", "evaluating", "ready"):
        jobs.move(first.id, state)
    manifest = json.loads((jobs_module.run_dir(first.id, planner.root) / "data" / "dataset.json")
                          .read_text(encoding="utf-8"))
    trained.update(manifest["example_ids"], manifest["validation_ids"])

    _turns(work, 1, start=4)
    planner.forget()
    waiting = planner.plan(force=True)
    assert waiting.reason == "up_to_date" and waiting.summary.new_examples == 1

    _turns(work, 1, start=5)
    planner.forget()
    second = planner.plan(force=True).job
    assert second is not None and second.version == 2


def test_planning_waits_between_tries_and_the_count_is_cached(work, setup):
    planner, _jobs, _engine, _recipe, _measured, _trained = setup
    clock: FakeClock = planner.clock
    _turns(work, 1)

    assert planner.plan(force=True).reason == "no_data"
    assert planner.plan().reason == "later" and not planner.due()

    _turns(work, 1, start=1)
    assert planner.summary().examples == 1  # cached for a while
    clock.advance(planner_module.SUMMARY_SECONDS)
    assert planner.summary().examples == 2
    clock.advance(planner_module.PLAN_SECONDS)
    assert planner.due()


def test_a_job_planned_without_rows_gets_them_when_it_starts(work, setup, tmp_path, monkeypatch):
    from row_bot.learning import evaluation
    from row_bot.learning.training.supervisor import TrainingError

    monkeypatch.setattr(evaluation, "refresh_audit", lambda **_kwargs: {"added": 0})
    planner, jobs, *_ = setup
    job = jobs.plan(steps_total=1, checkpoint_every=1)
    dataset = planner_module.LearningDataset(planner)
    with pytest.raises(TrainingError) as error:
        dataset.prepare(job, tmp_path / "empty")
    assert error.value.code == "nothing_to_train"

    _turns(work, 2)

    assert dataset.prepare(job, tmp_path / "data") == 2
    assert len((tmp_path / "data" / "examples.jsonl").read_text(encoding="utf-8").splitlines()) == 2


def test_a_run_planned_under_an_older_recipe_starts_with_the_current_one_and_the_persons_choices(
        work, setup, monkeypatch):
    # The preview's v2 was planned under three fixed passes; the recipe now keeps the best pass (O117).
    from row_bot.learning import evaluation
    from row_bot.learning.training import advanced

    monkeypatch.setattr(evaluation, "refresh_audit", lambda **_kwargs: {"added": 0})
    planner, jobs, engine, recipe, _measured, _trained = setup
    older = replace(recipe, epochs=2)
    _turns(work, 4)
    job = planner_module.Planner(planner.root, jobs=jobs, engine=engine, clock=planner.clock,
                                 recipe_for=lambda: older, measure=lambda rows, limit: [(100, 10) for _ in rows],
                                 lookup=planner._lookup, roots=lambda: ()).plan(force=True).job
    assert (job.steps_total, jobs.plan_of(job)["recipe"]["settings"]["passes"]) == (2, 2)
    advanced.save({"rank": 32}, recipe, planner.root)  # the person's own choice in Advanced

    started = planner_module.LearningDataset(planner).starting(job)

    settings = jobs.plan_of(started)["recipe"]["settings"]
    assert (settings["passes"], started.steps_total) == (3, 3)  # one step a pass
    assert (settings["rank"], settings["alpha"]) == (32, 32)  # their choice stays
    assert jobs.plan_of(started)["dataset"] == jobs.plan_of(job)["dataset"]  # the same rows
    assert jobs.get(job.id).steps_total == 3


def test_a_version_that_couldnt_start_doesnt_hold_up_the_next_one(work, setup, monkeypatch):
    # The preview's first pilot: v1 trained in another shape, then failed its ready check. Its run stays
    # "evaluating" (Try again, Change where it runs, Delete), but the next version is planned, and what v1
    # was trained on isn't counted as trained.
    from row_bot.learning import evaluation
    from row_bot.learning.training import releases

    monkeypatch.setattr(evaluation, "refresh_audit", lambda **_kwargs: {"added": 0})
    planner, jobs, engine, recipe, measured, _fake = setup
    real = planner_module.Planner(planner.root, jobs=jobs, engine=engine, clock=planner.clock,
                                  recipe_for=lambda: recipe, measure=lambda rows, limit: [(100, 10) for _ in rows],
                                  lookup=planner._lookup, roots=lambda: ())  # what it was trained on, from v1's folder
    _turns(work, 4)
    first = real.plan(force=True).job
    for state in ("preparing", "running", "evaluating"):
        jobs.move(first.id, state)
    folder = releases.version_dir(1, planner.root)
    folder.mkdir(parents=True)
    (folder / releases.DATASET_FILE).write_text(
        (jobs_module.run_dir(first.id, planner.root) / "data" / "dataset.json").read_text(encoding="utf-8"),
        encoding="utf-8")

    # A runtime that isn't there yet (waiting for the person) still holds the next version back.
    releases.write_state(folder, releases.VersionState(version=1, phase=releases.WAITING, job_id=first.id,
                                                       failure="not_running"))
    real.forget()
    assert real.plan(force=True).reason == "current"

    releases.write_state(folder, releases.VersionState(version=1, phase=releases.RUNTIME_FAILED, job_id=first.id,
                                                       failure="tool_turn_failed", gguf="v1.gguf"))
    real.forget()
    second = real.plan(force=True)

    assert second.job is not None and second.job.version == 2 and second.summary.new_examples == 4
    assert jobs.get(first.id).state == "evaluating"  # v1 stays as it is, with its ways out
    assert releases.trained_example_ids(planner.root) == set()
