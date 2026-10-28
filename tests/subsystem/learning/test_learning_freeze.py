"""The paper's freeze evaluation inside evaluation (pre-registration §7, P1): every hidden problem up to the
freeze, by split (real work uncorrected, real work corrected, the packs), each arm 3 runs a problem; the
primary paired analysis and §7's sensitivity analyses (exact fidelity only, a majority vote, without
clustering, per main model) and per kind of work, all computed here so that only aggregates leave (E10);
Holm across the confirmatory family; the vault's counts for the health check."""

from __future__ import annotations

import json
import re
from typing import Any

import pytest
from langchain_core.messages import AIMessage

from row_bot.learning import audit, evaluation, holdout
from row_bot.learning.evaluation import Action, Arm, CaseResult, ReplayCase
from row_bot.learning.packs import visible_tasks
from row_bot.learning.spending import TokenMeter
from row_bot.learning.store import LearningStore
from tests.fixtures.learning import CapturedWork, signal
from tests.fixtures.learning_eval import ScriptedClient

pytestmark = [pytest.mark.subsystem]

CANARY = "canary-q9z"
FREEZE = 1_000.0


def _problem(number: int, *, chat: str, at: float, source: str = "captured", fidelity: str = "exact",
             model: str = "model:codex:gpt-canary-q9z", kind: str = "Document lookups", untrusted: bool = False,
             picture: bool = False) -> dict[str, Any]:
    question = f"Problem {number}: what does {CANARY}-{number}.txt say?"
    messages: tuple[dict[str, Any], ...] = ({"type": "human", "data": {"content": question}},)
    if picture:  # the page's picture after a browser action (5.3): one extra message with no id
        messages += ({"type": "human", "data": {"content": [
            {"type": "text", "text": "The browser page after that action."},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,/9j/4AAQ"}}]}},)
    case = ReplayCase(f"dec_{CANARY}_{number}", f"thread-{CANARY}-{chat}", f"thread-{CANARY}-{chat}", at, kind,
                      fidelity, untrusted, messages, None, model, Action(text=f"answer {number}"), source,
                      "exact_action", question)
    return {"id": case.decision_id, "kind_of_work": kind, "thread_id": case.thread_id,
            "conversation_id": case.conversation_id, "created_at": at, **case.to_content()}


class FakeVault:
    """The vault's two readers, as evaluation calls them (the real one is sealed to evaluation)."""

    def __init__(self, tasks, replay) -> None:
        self.tasks, self.replay = list(tasks), list(replay)

    def hidden_tasks(self, *, asked_by, packs=None):
        return [task for task in self.tasks if packs is None or task.pack in packs]

    def replay_tasks(self, *, asked_by):
        return list(self.replay)


class Packs:
    """Pack tasks: every arm passes the first two and fails the rest, the same every run."""

    signature = "fake-packs-1"

    def run(self, task, arm, *, budget, meter, arm_left):
        passed = task.id in self.passing
        return CaseResult(task.id, "pack", task.kind_of_work, "passed" if passed else "failed", "answer",
                          tokens=500, local_tokens=500, steps=2, cause="" if passed else "wrong_answer")

    def __init__(self, tasks) -> None:
        self.passing = {task.id for task in tasks[:2]}


def _answers(rule):
    """Replies by arm (the model) and problem number: ``rule(model, number, run)`` says whether it passes."""

    runs: dict[tuple[str, int], int] = {}

    def respond(messages, _tools, model):
        number = int(re.search(r"Problem (\d+):", str(messages[-1].content)).group(1))
        runs[(model, number)] = runs.get((model, number), 0) + 1
        passed = rule(model, number, runs[(model, number)])
        return AIMessage(content=f"answer {number}" if passed else "something else")

    return respond


@pytest.fixture
def vault(monkeypatch, learning_on):
    def install(tasks, replay):
        fake = FakeVault(tasks, replay)
        monkeypatch.setattr(evaluation, "_vault", lambda _store=None: fake)
        return fake

    return install


def _freeze(rule, *, tasks=(), replay=(), arms=None, contrasts=(("start", "personal"),), **kwargs):
    arms = arms or [Arm("start", model_ref="start", serve_context=None), Arm("personal", model_ref="personal")]
    return evaluation.freeze_check(arms, as_of=FREEZE, asked_by="test", contrasts=list(contrasts),
                                   client=ScriptedClient(_answers(rule), local=True), runner=Packs(list(tasks)),
                                   meter=TokenMeter(10**9), store=LearningStore(), **kwargs)


def test_only_aggregates_leave_the_freeze_evaluation(vault):
    tasks = [task for task in visible_tasks() if task.pack == "document_lookups"][:4]
    replay = [_problem(number, chat=f"c{number % 3}", at=100.0 + number) for number in range(9)]
    vault(tasks, replay)

    result = _freeze(lambda model, number, run: model == "personal", tasks=tasks, replay=replay)

    text = json.dumps(result)
    for secret in (CANARY, "dec_", "thread-", "gpt-", "answer ", *(task.id for task in tasks)):
        assert secret not in text
    assert result["problems"] == {"real_uncorrected": 9, "real_corrected": 0, "packs": 4, "after_as_of": 0,
                                  "untrusted": 0, "with_picture": 0}
    assert result["repeats"] == 3 and result["metric_version"] == evaluation.METRIC_VERSION
    personal = result["arms"]["personal"]["splits"]["real_uncorrected"]
    assert (personal["problems"], personal["counted"], personal["mean"]) == (9, 9, 1.0)
    assert personal["runs"] == {"passed": 27}
    assert result["arms"]["start"]["splits"]["packs"]["causes"] == {"wrong_answer": 6}


def test_problems_after_the_freeze_are_left_out_and_counted(vault):
    replay = [_problem(number, chat="c", at=at) for number, at in enumerate((10.0, FREEZE, FREEZE + 0.5, 5_000.0))]
    vault([], replay)

    result = _freeze(lambda model, number, run: True, replay=replay)

    assert result["problems"]["real_uncorrected"] == 2 and result["problems"]["after_as_of"] == 2


def test_each_split_has_the_primary_analysis_clustered_by_source_chat(vault):
    tasks = [task for task in visible_tasks() if task.pack == "tool_workflows"][:3]
    replay = ([_problem(number, chat=f"c{number // 2}", at=10.0 + number) for number in range(6)]
              + [_problem(10 + number, chat="d", at=50.0 + number, source="corrected") for number in range(2)])
    vault(tasks, replay)

    result = _freeze(lambda model, number, run: model == "personal" or number >= 10, tasks=tasks, replay=replay)

    contrast = result["contrasts"]["personal vs start"]
    real = contrast["real_uncorrected"]["primary"]
    assert (real["problems"], real["clusters"], real["difference"], real["baseline"], real["arm"]) == (6, 3, 1.0, 0.0, 1.0)
    assert real["enough"] is False and real["interval_verdict"] == "better"
    corrected = contrast["real_corrected"]["primary"]
    assert (corrected["problems"], corrected["difference"]) == (2, 0.0)
    packs = contrast["packs"]["primary"]
    assert (packs["problems"], packs["clusters"], packs["difference"], packs["not_worse"]) == (3, 3, 0.0, True)
    assert "exact_only" not in contrast["packs"] and "per_main_model" not in contrast["packs"]
    assert result["problems"]["real_corrected"] == 2


def test_the_sensitivity_analyses_are_computed_beside_the_primary_one(vault):
    replay = [
        _problem(1, chat="a", at=10.0, model="model:codex:gpt-early"),
        _problem(2, chat="a", at=11.0, model="model:codex:gpt-early", fidelity="redacted"),
        _problem(3, chat="b", at=12.0, model="model:codex:gpt-later"),
        _problem(4, chat="b", at=13.0, model="model:codex:gpt-later", kind="Tool workflows"),
    ]
    vault([], replay)

    # The personal arm passes problem 1 once in three runs and every other problem every time.
    result = _freeze(lambda model, number, run: model == "personal" and (number != 1 or run == 1), replay=replay)

    real = result["contrasts"]["personal vs start"]["real_uncorrected"]
    assert real["primary"]["arm"] == round((1 / 3 + 3) / 4, 6)
    assert real["majority_vote"]["arm"] == 0.75  # one pass in three is not a majority
    assert real["exact_only"]["problems"] == 3  # the redacted problem is left out
    assert (real["unclustered"]["clusters"], real["primary"]["clusters"]) == (4, 2)
    # Main models are named only by the order of their first use.
    assert set(real["per_main_model"]) == {"model_1", "model_2"}
    assert (real["per_main_model"]["model_1"]["problems"], real["per_main_model"]["model_2"]["problems"]) == (2, 2)
    assert set(real["by_kind"]) == {"document_lookups", "tool_workflows"}
    assert result["main_models"] == 2


def test_the_freeze_adds_a_trusted_only_and_a_without_pictures_analysis(vault):
    # A7: problems with untrusted input count in the primary analysis, with a trusted-only one beside it.
    # A8: problems whose prefix carries a page picture count too (text-only arms are asked without it),
    # with an analysis without them beside it.
    replay = [_problem(number, chat=f"c{number}", at=10.0 + number, untrusted=number >= 2, picture=number >= 4)
              for number in range(6)]
    vault([], replay)

    result = _freeze(lambda model, number, run: model == "personal" and number >= 4, replay=replay)

    real = result["contrasts"]["personal vs start"]["real_uncorrected"]
    assert (real["primary"]["problems"], real["primary"]["difference"]) == (6, round(2 / 6, 6))
    assert (real["trusted_only"]["problems"], real["trusted_only"]["difference"]) == (2, 0.0)
    assert (real["without_pictures"]["problems"], real["without_pictures"]["difference"]) == (4, 0.0)
    assert (result["problems"]["untrusted"], result["problems"]["with_picture"]) == (4, 2)
    personal = result["arms"]["personal"]["splits"]["real_uncorrected"]
    assert personal["runs"] == {"failed": 12, "passed": 6}  # every problem counted: none "unverified"


def test_a_problem_with_no_counted_run_in_one_arm_leaves_the_pair(vault):
    replay = [_problem(number, chat="a", at=10.0 + number) for number in range(3)]
    vault([], replay)

    def respond(messages, _tools, model):
        number = int(re.search(r"Problem (\d+):", str(messages[-1].content)).group(1))
        if model == "personal" and number == 0:
            raise ConnectionError("the runtime went away")  # an outage: outside the denominator (§8.3)
        return AIMessage(content=f"answer {number}")

    result = evaluation.freeze_check(
        [Arm("start", model_ref="start"), Arm("personal", model_ref="personal")], as_of=FREEZE, asked_by="test",
        contrasts=[("start", "personal")], client=ScriptedClient(respond, local=True), meter=TokenMeter(10**9),
        store=LearningStore())

    real = result["contrasts"]["personal vs start"]["real_uncorrected"]
    assert real["left_pair"] == 1 and real["primary"]["problems"] == 2
    assert result["arms"]["personal"]["splits"]["real_uncorrected"]["runs"] == {"error": 3, "passed": 6}


def test_a_contrast_must_name_arms_that_run(vault):
    vault([], [])

    with pytest.raises(evaluation.EvaluationError, match="unknown_arm"):
        _freeze(lambda *_: True, contrasts=(("start", "missing"),))
    with pytest.raises(evaluation.EvaluationError, match="unknown_arm"):
        _freeze(lambda *_: True, contrasts=())


# ── Holm across the confirmatory family (§7) ────────────────────────────────


def test_holm_adjusts_across_the_family_and_stays_monotone():
    assert evaluation.holm({"H1": 0.01, "H2": 0.04}) == {"H1": 0.02, "H2": 0.04}
    assert evaluation.holm({"H1": 0.04, "H2": 0.01}) == {"H1": 0.04, "H2": 0.02}
    # Larger p first adjusted by fewer, but never below an earlier one.
    assert evaluation.holm({"a": 0.03, "b": 0.02, "c": 0.04}) == {"a": 0.06, "b": 0.06, "c": 0.06}
    assert evaluation.holm({"H1": 0.6, "H2": 0.7}) == {"H1": 1.0, "H2": 1.0}


def test_an_untested_hypothesis_still_counts_in_the_family():
    adjusted = evaluation.holm({"H1": 0.03}, family=("H1", "H2"))

    assert adjusted == {"H1": 0.06, "H2": 1.0}  # H2 can't be rejected; H1 is adjusted for both


# ── The vault's counts for the health check ─────────────────────────────────


def test_the_vaults_counts_come_without_reading_any_problem_out(writer, chats, clock, learning_on):
    work = CapturedWork(writer, chats, clock)
    answers = [work.turn(f"chat-{CANARY}-{index}", f"Question {index} about {CANARY}", f"Answer {index}.")[-1]
               for index in range(6)]
    store = LearningStore(learning_on, clock=clock)
    for number in range(2):
        # Taught with what it should have said (A3: a reference), so the 2nd is held out as a correction.
        store.put(signal(f"chat-{CANARY}-{number}", f"sig_{number}", decision_id=answers[number],
                         created_at=clock.now + number, answer=f"It should have said {CANARY}."))
    before = evaluation.audit_counts(asked_by="test", store=store)
    assert before["vault"] == 0 and not audit.vault_exists(learning_on)  # never creates a vault

    refreshed = evaluation.refresh_audit(store=store, asked_by="test")
    counts = evaluation.audit_counts(asked_by="test", store=store)

    # 12 model calls, 2 of them corrected (never uncorrected problems): every 4th of the other 10.
    assert refreshed["held"] == 2
    assert counts["vault"] == 1 and counts["pack_tasks"] == 32 and counts["corrected"] == 1
    assert counts["uncorrected"] == 2 and counts["exact"] == 3 and counts["redacted"] == 0
    assert len(holdout.waiting_for_vault(store)) == 1 and counts["corrections_waiting"] == 0
    assert CANARY not in json.dumps(counts)


class GuardedPacks:
    """Pack tasks: "personal" passes them all; "start" offers two answers on half of them (METRIC_VERSION 3's
    guard fails those, version 2 passed them) and fails the rest."""

    signature = "fake-packs-guarded"

    def run(self, task, arm, *, budget, meter, arm_left):
        if arm.name == "personal":
            return CaseResult(task.id, "pack", task.kind_of_work, "passed", "answer", tokens=1, local_tokens=1)
        guarded = int(task.id.split("_")[-1]) % 2 == 0
        return CaseResult(task.id, "pack", task.kind_of_work, "failed", "answer", tokens=1, local_tokens=1,
                          detail="offered more than one answer" if guarded else "wrong", guarded=guarded)


def test_the_freeze_reads_the_same_runs_under_both_checker_versions(vault):
    # §7's sensitivity analysis: H1 and H2 under METRIC_VERSION 3 (the guard) and under 2 (before it), from the
    # same runs; a run the guard failed passes under version 2.
    tasks = [task for task in visible_tasks() if task.pack == "document_lookups"][:8]
    vault(tasks, [])

    result = evaluation.freeze_check(
        [Arm("start", model_ref="start"), Arm("personal", model_ref="personal")], as_of=FREEZE, asked_by="test",
        contrasts=[("start", "personal")], client=ScriptedClient(_answers(lambda *_: True), local=True),
        runner=GuardedPacks(), meter=TokenMeter(10**9), store=LearningStore(), repeats=1)

    assert result["metric_version"] == 3 and result["previous_metric_version"] == 2
    packs = result["arms"]["start"]["splits"]["packs"]
    assert (packs["mean"], packs["mean_metric_v2"], packs["guarded_runs"]) == (0.0, 0.5, 4)
    contrast = result["contrasts"]["personal vs start"]["packs"]
    assert contrast["primary"]["difference"] == 1.0 and contrast["metric_v2"]["difference"] == 0.5


def test_the_freeze_gives_the_same_results_model_by_model_as_interleaved(vault):
    """C1, 28 Oct: A10's local 27B arms run model by model on a 32 GB card (two 27Bs and the 9B can't stay loaded
    together). The problems, checker, budget, repeats and pairing are the same, and so is every result."""

    tasks = [task for task in visible_tasks() if task.pack == "document_lookups"][:3]
    replay = [_problem(number, chat=f"c{number % 4}", at=10.0 + number) for number in range(12)]
    vault(tasks, replay)
    arms = [Arm("start27", model_ref="start27"), Arm("personal27", model_ref="personal27"),
            Arm("personal", model_ref="personal")]
    contrasts = [("start27", "personal27"), ("personal", "personal27")]

    def rule(model, number, run):  # a different pattern per model, and the run number matters
        return {"start27": number % 3 == 0, "personal27": (number + run) % 2 == 0,
                "personal": number % 4 != 1}[model]

    interleaved = _freeze(rule, tasks=tasks, replay=replay, arms=arms, contrasts=contrasts)
    model = _freeze(rule, tasks=tasks, replay=replay, arms=arms, contrasts=contrasts, order="model")

    assert model == interleaved
    assert interleaved["contrasts"]["personal27 vs start27"]["real_uncorrected"]["primary"]["problems"] == 12
