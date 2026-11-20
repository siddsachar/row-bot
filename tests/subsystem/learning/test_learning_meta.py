"""Strategy trials (L4a; D15, D16, O82; pre-registration §4, §6.1, §7): a trial waits for 30 fresh hidden problems,
every arm gets the same budget within the trials' share, the frozen control is the method from two trials back,
an arm never tests on the problems that judge it and never shows its drafts, the winner switches by itself with
Undo and a morning line, and H3's estimate is right on judgments with known answers."""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, SystemMessage

from row_bot import evolution, learning
from row_bot.learning import changes, evaluation
from row_bot.learning.improver import archive, meta
from row_bot.learning.improver import strategy as strategy_module
from row_bot.learning.spending import TokenMeter
from row_bot.learning.store import LearningStore
from tests.fixtures.learning import (CapturedWork, FakeClock, FakeThreads, save_learning_settings,
                                    set_learning_preview, signal)
from tests.fixtures.learning_eval import ScriptedClient

pytestmark = [pytest.mark.subsystem]

HOUR = 3600.0
MARKER = "Answer from the current rota policy and say which version you used."
# A method whose own words make its diagnosis draft the helpful lesson (the scripted model below).
SPECIFIC = "Be specific about which version of a document a reply should rest on."
# Arms test their own drafts only on hidden work: no pack tasks, so the tests run in-process and fast.
QUIET = strategy_module.BudgetSplit(visible_own=0, visible_other=0, hidden_own=0, harder_own=0, own_problems=8,
                                    other_problems=2)


def model(messages, tools, model_ref):
    """The scripted main model: a diagnosis drafts the helpful lesson only under a method that asks it to be
    specific; a replayed rota question is answered right only with the helpful lesson as guidance."""

    system = "\n".join(str(item.content) for item in messages if isinstance(item, SystemMessage))
    if system.startswith("You are Row-Bot's Learning"):
        helpful = SPECIFIC.split(" about")[0] in system
        text = MARKER if helpful else "Read the documents carefully before answering."
        return AIMessage(content=json.dumps({"finding": "It answered from an old rota.", "change": "lesson",
                                             "title": "Current rota", "text": text}))
    asked = re.search(r"shift (\d+)\?", str(messages[-1].content))
    if asked and MARKER in system:
        return AIMessage(content=f"Answer {asked.group(1)}.")
    return AIMessage(content="I don't know.")


def add_work(work, store, clock, *, corrected: int = 0, plain: int = 0, start: int = 0) -> None:
    """Rota questions the person corrected (the evidence) and ``plain`` ones nobody did (every 4th goes to the
    vault, its captured answer the reference); then the vault's refresh."""

    for index in range(start, start + corrected):
        thread = f"taught-{index}"
        reply = work.turn(thread, f"According to the rota policy, how many on shift {index}?", "Three.", tool=False,
                          person_message=True)[-1]
        store.put(signal(thread, f"sig_{index}", decision_id=reply, created_at=clock.now, note="Old rota",
                         category="wrong_answer"))
        clock.advance(60)
    for index in range(start, start + plain):
        work.turn(f"plain-{index}", f"According to the rota policy, how many on shift {1000 + index}?",
                  f"Answer {1000 + index}.", tool=False, person_message=True)
        clock.advance(60)
    clock.advance(HOUR)
    evaluation.refresh_audit(store=store, asked_by="test")


def _template(folder: Path, *, plain: int) -> float:
    """A data folder with three corrected rota questions and ``plain`` uncorrected ones, captured once."""

    from row_bot.learning.capture import Capture

    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("ROW_BOT_DATA_DIR", str(folder))
        set_learning_preview(patch, True)
        save_learning_settings(folder, enabled=True, monthly_token_limit=1_000_000_000)
        clock, chats = FakeClock(), FakeThreads()
        writer = Capture(clock=clock, start_thread=False, lookup=chats)
        try:
            add_work(CapturedWork(writer, chats, clock), LearningStore(folder, clock=clock), clock, corrected=3,
                     plain=plain)
        finally:
            writer.shutdown()
            learning.invalidate()
    return clock.now


@pytest.fixture(scope="module")
def templates(tmp_path_factory):
    """Two captured worlds, made once: 160 uncorrected questions (40 hidden problems: a trial draws the newest 30
    and its arms test their own drafts on the 10 older ones) and 116 (29 hidden problems)."""

    found = {}
    for name, plain in (("forty", 160), ("twenty_nine", 116)):
        folder = tmp_path_factory.mktemp(f"meta-{name}")
        found[name] = (folder, _template(folder, plain=plain))
    return found


@pytest.fixture
def world(request, templates, learning_on, clock, writer, chats, monkeypatch):
    """The test's data folder holding a copy of a template world (``@pytest.mark.parametrize("world", ...)``
    names it; forty problems by default), the clock after it, and captured work to add more."""

    name = getattr(request, "param", "forty")
    folder, ended = templates[name]
    shutil.copytree(folder, learning_on, dirs_exist_ok=True)
    learning.invalidate()
    clock.now = ended
    monkeypatch.setattr(evolution, "_now", lambda: datetime.fromtimestamp(clock.now, tz=timezone.utc).isoformat())
    for thread in [f"taught-{index}" for index in range(3)]:
        chats.chat(thread)
    return CapturedWork(writer, chats, clock)


@pytest.fixture
def store(learning_on, clock):
    return LearningStore(learning_on, clock=clock)


def make_tester(store, client):
    def make(method):
        return changes.Tester(store.root, store=store, client=client, main_ref=lambda: "model:fake:main",
                              container=lambda: False, plan=changes.TestPlan.of(method, runtime=False),
                              purpose=meta.PURPOSE)

    return make


def quiet_first(store) -> archive.Entry:
    """The first method, with tests on hidden work only (kept as v1)."""

    first = archive.ensure_first(store)
    method = replace(first.strategy, budget=QUIET)
    blob = store.put_blob(method.to_json().encode("utf-8"))
    store.put(replace(first.release, content_blob=blob))
    return archive.current(store)


def challenger_says(monkeypatch, words: str = SPECIFIC) -> None:
    def propose(store, *, seed, client=None, meter=None):
        parent = archive.current(store)
        return replace(parent.strategy, prompts=strategy_module.Prompts(words)), parent

    monkeypatch.setattr(meta, "propose", propose)


def trial(store, client, clock, *, night="2027-01-16", should_stop=lambda: False):
    return meta.run_trial(store, night=night, client=client, tester_factory=make_tester(store, client),
                          now=clock.now, should_stop=should_stop)


@pytest.mark.parametrize("world", ["twenty_nine"], indirect=True)
def test_a_trial_waits_for_thirty_fresh_hidden_problems_and_draws_the_newest(world, store, clock, monkeypatch):
    challenger_says(monkeypatch)
    quiet_first(store)
    assert evaluation.fresh_problems(asked_by="test", store=store)["fresh"] == 29
    client = ScriptedClient(model)

    report = trial(store, client, clock)

    assert report.state == "fewer_fresh" and client.calls == [] and archive.trials(store) == []
    add_work(world, store, clock, plain=4, start=500)  # one more hidden problem
    report = trial(store, client, clock, night="2027-01-17")
    assert report.state == "done" and report.problems == 30
    assert evaluation.fresh_problems(asked_by="test", store=store)["fresh"] == 0  # the next waits for new ones
    add_work(world, store, clock, plain=4, start=600)
    assert evaluation.fresh_problems(asked_by="test", store=store)["fresh"] == 1
    assert trial(store, client, clock, night="2027-01-18").state == "fewer_fresh"


def test_every_arm_gets_the_same_budget_within_the_trials_share(world, store, clock, monkeypatch):
    challenger_says(monkeypatch)
    quiet_first(store)
    # The trials together may use 20% of the monthly limit, less what trials used, and keep enough to judge.
    save_learning_settings(store.root, enabled=True, monthly_token_limit=1_000_000)
    tight = meta.plan(store)
    assert tight.state == "limit" and tight.room == 200_000 and tight.reserve > tight.room
    save_learning_settings(store.root, enabled=True, monthly_token_limit=1_000_000_000)
    found = meta.plan(store)
    assert found.state == "ready" and found.arms == 2 and found.arm_tokens == meta.ARM_TOKENS_MAX
    # With no limit only each arm's equal budget bounds the trial (it runs so below).
    save_learning_settings(store.root, enabled=True, monthly_token_limit=None)
    unlimited = meta.plan(store)
    assert (unlimited.state, unlimited.arms, unlimited.arm_tokens) == ("ready", 2, meta.ARM_TOKENS_MAX)
    client = ScriptedClient(model)

    report = trial(store, client, clock)

    [summary] = archive.trials(store)
    assert summary["arm_tokens"] == meta.ARM_TOKENS_MAX and summary["repeats"] == 3
    assert {arm["role"] for arm in summary["arms"]} == {"incumbent", "challenger"}
    assert all(0 < arm["tokens"] <= summary["arm_tokens"] for arm in summary["arms"])
    assert {call["purpose"] for call in client.calls} == {"learning_meta"}  # every trial call counts as a trial's
    assert report.tokens == sum(arm["tokens"] for arm in summary["arms"]) + summary["judging_tokens"]


def test_an_arm_stops_at_its_equal_budget():
    parent = TokenMeter(10**9)
    arm = meta.ArmMeter(parent, 5_000, gpu_seconds=10.0)
    assert arm.allows(4_000) and not arm.allows(6_000)
    arm.charge(3_000)
    arm.charge(1_500, local=True)  # local tokens count toward the arm's equal budget too
    assert arm.used == 4_500 and parent.spent == 3_000 and not arm.allows(1_000)
    assert arm.allows(400, local=True)
    arm.charge_gpu(11.0)
    assert not arm.allows(1, local=True) and arm.exhausted


def test_with_no_limit_a_trial_still_stops_each_arm_at_its_equal_budget(store):
    from row_bot.learning import spending

    save_learning_settings(store.root, enabled=True, monthly_token_limit=None)
    room = meta.trial_room(store)
    assert room == spending.UNLIMITED_TOKENS
    trial_meter = TokenMeter(room)  # the trial's own meter, as run_trial makes it
    # Each arm's budget with no limit (the plan's, test_every_arm_gets_the_same_budget_within_the_trials_share).
    arm = meta.ArmMeter(trial_meter, meta.ARM_TOKENS_MAX)

    calls = 0
    while arm.allows(150_000):
        arm.charge(150_000)
        calls += 1

    # The month never refuses; the arm's equal budget does, before the call that would pass it.
    assert calls == 13 and arm.used == 1_950_000 <= meta.ARM_TOKENS_MAX
    assert arm.exhausted and arm.note() == "Stopped: this method's share of the trial was spent."
    assert trial_meter.spent == 1_950_000 and trial_meter.refused == 0 and not trial_meter.exhausted


def test_an_arm_never_tests_on_the_problems_that_judge_it_and_never_shows_its_drafts(world, store, clock, monkeypatch):
    challenger_says(monkeypatch)
    quiet_first(store)
    client = ScriptedClient(model)
    seen, judging_from = [], []
    real_check, real_judge = evaluation.audit_check, evaluation.judge_trial

    def audit_check(*args, **kwargs):
        seen.append(kwargs.get("leave_out"))
        return real_check(*args, **kwargs)

    def judge_trial(*args, **kwargs):
        judging_from.append(len(client.calls))
        return real_judge(*args, **kwargs)

    monkeypatch.setattr(evaluation, "audit_check", audit_check)
    monkeypatch.setattr(evaluation, "judge_trial", judge_trial)

    trial(store, client, clock)

    [summary] = archive.trials(store)
    assert seen and all(item == summary["id"] for item in seen)  # every own test leaves the trial's problems out

    def asked(calls):
        return {int(number) for call in calls for number in re.findall(r"shift (\d+)\?", str(call["messages"][-1].content))
                if not str(call["messages"][0].content).startswith("You are Row-Bot's Learning")}

    own, judged = asked(client.calls[: judging_from[0]]), asked(client.calls[judging_from[0]:])
    assert len(judged) == 30 and min(judged) > max(own)  # the newest 30 judge the trial
    assert own and not own & judged  # the arms tested on older problems only
    assert evolution.list_proposals() == []  # an arm's drafts are never shown or used
    assert changes.lessons_in_use() == [] and store.find("attempt", subkind="lesson") == []


def test_the_winner_switches_by_itself_with_undo_and_a_morning_line(world, store, clock, monkeypatch):
    challenger_says(monkeypatch)
    first = quiet_first(store)
    archive.record_trial(store, {"id": "trial_before", "incumbent": first.id, "control": first.id, "arms": []})
    client = ScriptedClient(model)

    report = trial(store, client, clock)

    summary = archive.trials(store)[-1]
    by_role = {arm["role"]: arm for arm in summary["arms"]}
    assert by_role["challenger"]["better"] == 1 and by_role["incumbent"]["better"] == 0
    assert by_role["challenger"]["yield"] > by_role["incumbent"]["yield"] == 0.0
    assert summary["switched"] and report.switched == {"from": 1, "to": 2,
                                                       "words": "words its own instructions differently"}
    now = archive.current(store)
    assert now.version == 2 and now.strategy.prompts.diagnosis == SPECIFIC
    assert [step["by"] for step in archive.chain(store)] == ["first", "trial"]
    [attempt] = store.find("attempt", subkind="strategy")
    assert attempt.kept and attempt.outcome == "improved" and attempt.strategy_release_id == now.id
    # This trial judged the method the trial before left in use (the first) against the frozen control (the
    # first, for the first two trials): the same arm, so the same yield (H3's generation 1).
    [generation] = archive.generations(store)
    assert generation["inherited"] == generation["control"] and summary["judged"]["same"]

    from row_bot.learning.lease import ResourceLease
    from row_bot.learning.runner import NightLook, QuietHours

    store.set_meta_values({changes.NIGHT_KEY: json.dumps(changes.NightTests("2027-01-16").to_dict())})
    look = NightLook(lease=ResourceLease(), root=store.root,
                     hours=QuietHours(store.root, now=lambda: datetime(2027, 1, 16, 9)))
    said = []
    look.morning(said.append)
    assert said == ["Overnight: changed how it learns (method v2): words its own instructions differently "
                    "(Undo in Learning)"]

    back = meta.undo(store)
    assert back.version == 1 and archive.current(store).version == 1
    assert archive.get(store, now.id).release.status == "not_used"
    assert meta.undo(store) is None  # only a trial's switch can be undone


def test_a_tie_keeps_the_method_in_use(world, store, clock, monkeypatch):
    challenger_says(monkeypatch, "Read every message in the chat before drafting.")  # drafts the same lesson
    quiet_first(store)

    trial(store, ScriptedClient(model), clock)

    [summary] = archive.trials(store)
    assert not summary["switched"] and archive.current(store).version == 1
    challenger = archive.get(store, next(arm["release"] for arm in summary["arms"] if arm["role"] == "challenger"))
    assert challenger.release.status == "not_used"
    assert store.find("attempt", subkind="strategy")[0].outcome == "not_better"
    assert "judged" not in summary  # the first trial judges no generation


def test_the_frozen_control_is_the_method_two_trials_back_and_the_first_for_the_first_two(learning_on):
    store = LearningStore(learning_on)
    first = archive.ensure_first(store)
    methods = [first] + [archive.add(store, strategy_module.FIRST) for _ in range(3)]
    assert archive.frozen_control(store).id == first.id
    archive.record_trial(store, {"id": "t1", "incumbent": methods[0].id})
    assert archive.frozen_control(store).id == first.id  # still the first for trial 2
    archive.record_trial(store, {"id": "t2", "incumbent": methods[1].id})
    assert archive.frozen_control(store).id == methods[0].id  # trial 3: trial 1's incumbent
    archive.record_trial(store, {"id": "final", "incumbent": methods[3].id, "final": True})  # the final set isn't one
    archive.record_trial(store, {"id": "t3", "incumbent": methods[2].id})
    assert archive.frozen_control(store).id == methods[1].id  # trial 4: trial 2's incumbent


def test_a_stop_keeps_what_is_done_and_the_trial_goes_on_later(world, store, clock, monkeypatch):
    challenger_says(monkeypatch)
    quiet_first(store)
    client = ScriptedClient(model)
    asked = []

    def stop_part_way():
        asked.append(1)
        return len(asked) == 4

    first = trial(store, client, clock, should_stop=stop_part_way)
    assert first.state == "stopped" and meta.plan(store).state == "resume" and archive.trials(store) == []
    second = trial(store, client, clock)
    assert second.state == "done" and second.trial == first.trial and len(archive.trials(store)) == 1


def test_the_final_set_judges_every_generation_and_the_control_without_switching(world, store, clock, monkeypatch):
    first = quiet_first(store)
    second = archive.add(store, replace(first.strategy, prompts=strategy_module.Prompts(SPECIFIC)))
    archive.switch(store, second, by="trial", trial="t1")
    archive.record_trial(store, {"id": "t1", "incumbent": first.id, "winner": second.id, "arms": []})
    client = ScriptedClient(model)

    report = meta.run_final(store, label="freeze", client=client, tester_factory=make_tester(store, client),
                            arm_tokens=500_000, meter=TokenMeter(10**9), now=clock.now)

    assert report.state == "done" and report.problems == 30 and report.switched is None
    final = archive.trials(store)[-1]
    assert final["final"] and {arm["version"]: arm["better"] for arm in final["arms"]} == {1: 0, 2: 1}
    assert final["judged"]["inherited"]["release"] == second.id and final["judged"]["control"]["release"] == first.id
    assert archive.current(store).id == second.id


def test_the_h3_estimate_on_judgments_with_known_answers(learning_on, monkeypatch):
    kept: dict[tuple[str, str], list] = {}

    class Vault:
        def judgments(self, trial):
            return {change: rows for (stored, change), rows in kept.items() if stored == trial}

    monkeypatch.setattr(evaluation, "_vault", lambda _store=None: Vault())
    problems = [(f"p{index}", f"chat{index % 10}", "document_lookups") for index in range(30)]

    def rows(gain: float):
        return [(task, cluster, kind, 0.0, gain) for task, cluster, kind in problems]

    generations = []
    for number in range(3):
        trial_id = f"trial_{number}"
        kept[(trial_id, "inherited")] = rows(1.0)  # better on every problem
        kept[(trial_id, "control")] = rows(-1 / 3)  # worse
        generations.append({"trial": trial_id,
                            "inherited": {"changes": [["inherited", "document_lookups"]], "tokens": 500_000},
                            "control": {"changes": [["control", "document_lookups"]], "tokens": 1_000_000}})

    found = evaluation.h3_estimate(generations, resamples=500)

    # Inherited: 1 better change per 0.5M tokens = 2.0; control: 0 better = 0.0; the difference is 2.0 every time.
    assert [row["difference"] for row in found["generations"]] == [2.0, 2.0, 2.0]
    assert found["estimate"] == 2.0 and found["low"] == found["high"] == 2.0 and found["p_value"] == 0.0
    assert found["enough"] and all(row["problems"] == 30 and row["clusters"] == 10 for row in found["generations"])

    # Two generations, or one judged on fewer than 30 problems: below §7's minimum.
    assert not evaluation.h3_estimate(generations[:2], resamples=50)["enough"]
    kept[("trial_2", "inherited")] = rows(1.0)[:20]
    kept[("trial_2", "control")] = rows(-1 / 3)[:20]
    assert not evaluation.h3_estimate(generations, resamples=50)["enough"]


def test_the_h3_bootstrap_is_stratified_by_trial_and_reads_the_keeping_rule(learning_on, monkeypatch):
    kept: dict[str, dict[str, list]] = {}
    monkeypatch.setattr(evaluation, "_vault", lambda _store=None: type("V", (), {
        "judgments": lambda self, trial: kept.get(trial, {})})())
    mixed = [(f"p{index}", f"chat{index}", "document_lookups", 0.0, 1.0 if index % 2 else -0.5) for index in range(40)]
    elsewhere = [(f"q{index}", f"chat{index}", "tool_workflows", 1.0, 0.0) for index in range(10)]
    kept["t"] = {"a": mixed, "b": [*[(t, c, k, 0.0, 1.0) for t, c, k, *_ in mixed], *elsewhere]}
    generation = {"trial": "t", "inherited": {"changes": [["a", "document_lookups"]], "tokens": 1_000_000},
                  "control": {"changes": [["b", "document_lookups"]], "tokens": 1_000_000}}

    found = evaluation.h3_estimate([generation], resamples=400, seed=7)

    # "a" gains +0.25 on its own kind: better. "b" gains +1 on its kind but loses 1.0 on the rest: not better.
    [row] = found["generations"]
    assert row["inherited"]["better"] == 1 and row["control"]["better"] == 0 and row["difference"] == 1.0
    assert found["low"] <= 1.0 <= found["high"] and 0.0 <= found["p_value"] <= 1.0
    assert found == evaluation.h3_estimate([generation], resamples=400, seed=7)  # the recorded seed
