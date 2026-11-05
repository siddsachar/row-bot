"""Amendment A12's secondary analyses in the freeze evaluation (evaluation.freeze_check): v2's window (only real work
captured after the v1 freeze, the rest counted), the gap closed (a ratio with a cluster bootstrap), and the scoring
sensitivity analyses of the same runs (teacher agreement with the main reference, the right tool, judged long
answers). The primary results never change, and only aggregates leave the module (E10)."""

from __future__ import annotations

import json
import re
from typing import Any

import pytest
from langchain_core.messages import AIMessage

from row_bot.learning import evaluation
from row_bot.learning.evaluation import Action, Arm, Budget, Calibration, ReplayCase
from row_bot.learning.spending import TokenMeter
from row_bot.learning.store import LearningStore
from tests.fixtures.learning_eval import ScriptedClient

pytestmark = [pytest.mark.subsystem]

CANARY = "canary-a12x"
SINCE = 100.0
FREEZE = 1_000.0
# A reference longer than the checker compares by its text: no executable check decides it.
LONG = ("The total for the trip, worked out from the shared receipts and the booking notes, comes to 85 EUR once the "
        "refund for the cancelled museum tickets is taken off and the train fares are split between both travellers")


def _case(number: int, *, chat: str = "c", at: float = 10.0, reference: Action | None = None,
          source: str = "captured", question: str | None = None) -> dict[str, Any]:
    question = question or f"Problem {number}: what does {CANARY}-{number}.txt say?"
    case = ReplayCase(f"dec_{CANARY}_{number}", f"thread-{CANARY}-{chat}", f"thread-{CANARY}-{chat}", at,
                      "Document lookups", "exact", False, ({"type": "human", "data": {"content": question}},), None,
                      f"model:codex:gpt-{CANARY}", reference or Action(text=f"answer {number}"), source,
                      "exact_action", question)
    return {"id": case.decision_id, "kind_of_work": case.kind_of_work, "thread_id": case.thread_id,
            "conversation_id": case.conversation_id, "created_at": at, **case.to_content()}


class FakeVault:
    def __init__(self, replay) -> None:
        self.replay = list(replay)

    def hidden_tasks(self, *, asked_by, packs=None):
        return []

    def replay_tasks(self, *, asked_by):
        return list(self.replay)


@pytest.fixture
def vault(monkeypatch, learning_on):
    def install(replay):
        fake = FakeVault(replay)
        monkeypatch.setattr(evaluation, "_vault", lambda _store=None: fake)
        return fake

    return install


def _number(messages) -> int:
    return int(re.search(r"Problem (\d+):", str(messages[-1].content)).group(1))


def _by_run(reply):
    """Replies from ``reply(model, number, run)``: a text, or a (tool name, arguments) call."""

    runs: dict[tuple[str, int], int] = {}

    def respond(messages, _tools, model):
        number = _number(messages)
        runs[(model, number)] = runs.get((model, number), 0) + 1
        found = reply(model, number, runs[(model, number)])
        if isinstance(found, tuple):
            return AIMessage(content="", tool_calls=[{"name": found[0], "args": found[1], "id": "call_1",
                                                      "type": "tool_call"}])
        return AIMessage(content=found)

    return respond


def _freeze(reply, *, arms=("start", "personal"), contrasts=(("start", "personal"),), **kwargs) -> dict[str, Any]:
    return evaluation.freeze_check([Arm(name, model_ref=name) for name in arms], as_of=FREEZE, asked_by="test",
                                   contrasts=list(contrasts), client=ScriptedClient(_by_run(reply), local=True),
                                   meter=TokenMeter(10**9), store=LearningStore(), **kwargs)


def _real(result: dict[str, Any], contrast: str = "personal vs start", split: str = "real_uncorrected"):
    return result["contrasts"][contrast][split]


def _scoring(result: dict[str, Any], arm: str, name: str, split: str = "real_uncorrected"):
    return result["arms"][arm]["splits"][split]["scoring"][name]


# ── 1. v2's window ──────────────────────────────────────────────────────────


def test_v2_uses_only_real_work_after_the_v1_freeze_and_counts_what_came_before(vault):
    times = (10.0, SINCE, SINCE + 0.5, 500.0, FREEZE, FREEZE + 1)
    replay = [_case(number, at=at) for number, at in enumerate(times)]
    vault(replay)

    v2 = _freeze(lambda model, number, run: f"answer {number}", since=SINCE)
    v1 = _freeze(lambda model, number, run: f"answer {number}")

    assert (v2["problems"]["real_uncorrected"], v2["problems"]["before_since"], v2["problems"]["after_as_of"]) == (3, 2, 1)
    assert v2["arms"]["personal"]["splits"]["real_uncorrected"]["runs"] == {"passed": 9}
    # v1 is as it was: every problem up to the freeze, and no window counted.
    assert (v1["problems"]["real_uncorrected"], v1["problems"]["after_as_of"]) == (5, 1)
    assert "before_since" not in v1["problems"]


# ── 2. The gap closed ───────────────────────────────────────────────────────


def test_the_gap_closed_is_the_ratio_of_the_mean_gains():
    same = evaluation.ratio_analysis([(f"c{number % 5}", 0.25, 0.5) for number in range(40)])
    assert (same["estimate"], same["low"], same["high"]) == (0.5, 0.5, 0.5)
    assert (same["problems"], same["clusters"], same["enough"], same["bounded"]) == (40, 5, True, True)
    assert same["denominator_nonpositive_share"] == 0.0

    rows = [(f"c{number}", 0.1 * (number % 3), 0.4) for number in range(30)]  # mean gain 0.1 of a 0.4 gap
    varied = evaluation.ratio_analysis(rows)
    assert varied["estimate"] == 0.25 and varied["low"] < 0.25 < varied["high"] and varied["bounded"]
    assert evaluation.ratio_analysis(rows) == varied  # the recorded seed: the same interval every time


def test_the_gap_closed_is_unbounded_when_its_denominator_reaches_zero_in_the_resamples():
    shaky = evaluation.ratio_analysis([(f"c{number}", 0.1, gap) for number, gap
                                       in enumerate((0.5, -0.4, 0.5, -0.4, 0.1, -0.2))])
    assert shaky["estimate"] == 6.0  # 0.6 / 0.1, but the gap's sign isn't settled
    assert shaky["denominator_nonpositive_share"] > 0.025
    assert (shaky["low"], shaky["high"], shaky["bounded"]) == (None, None, False)

    negative = evaluation.ratio_analysis([(f"c{number}", 0.1, -0.1) for number in range(30)])
    assert (negative["estimate"], negative["bounded"], negative["denominator_nonpositive_share"]) == (None, False, 1.0)


def test_the_gap_closed_with_too_few_problems():
    none = evaluation.ratio_analysis([])
    assert (none["problems"], none["estimate"], none["low"], none["bounded"], none["enough"]) == (0, None, None, False,
                                                                                                    False)
    few = evaluation.ratio_analysis([(f"c{number}", 0.1, 0.2) for number in range(5)])
    assert (few["estimate"], few["enough"]) == (0.5, False)  # reported, below §7's minimum size


def test_the_freeze_reports_the_gap_closed_when_the_main_reference_ran(vault):
    replay = ([_case(number, chat=f"c{number % 4}", at=10.0 + number) for number in range(8)]
              + [_case(10 + number, chat="d", at=50.0 + number, source="corrected") for number in range(2)])
    vault(replay)

    def reply(model, number, run):  # start never passes, main always, personal on the even problems
        passes = {"start": False, "main": True, "personal": number % 2 == 0}[model]
        return f"answer {number}" if passes else "no idea"

    result = _freeze(reply, arms=("start", "personal", "main"), contrasts=(("start", "personal"), ("start", "main")))
    gap = result["gap_closed"]["real_uncorrected"]
    assert (gap["estimate"], gap["problems"], gap["clusters"], gap["bounded"]) == (0.5, 8, 4, True)
    assert 0.0 <= gap["low"] <= 0.5 <= gap["high"] <= 1.0
    assert result["gap_closed"]["real_corrected"]["estimate"] == 0.5

    without = _freeze(reply, arms=("start", "personal", "main"), contrasts=(("start", "personal"),))
    assert "gap_closed" not in without


# ── 4a. Teacher agreement ───────────────────────────────────────────────────


def test_teacher_agreement_passes_a_failed_run_that_matches_a_counted_main_run(vault):
    replay = [_case(number, at=10.0 + number) for number in range(4)]
    replay.append(_case(4, at=20.0, question="Problem 4: " + "x " * 12_000))  # over the budget for every arm
    replay.append(_case(5, at=21.0, reference=Action(text=LONG)))  # a long answer: no check decides it
    vault(replay)

    def reply(model, number, run):
        if number == 5:
            return LONG
        if model == "main":  # its own answer once, then another; on problem 3 a third the others never give
            return f"answer {number}" if run == 1 else ("odd 3" if number == 3 and run == 3 else f"alt {number}")
        return f"alt {number}" if model == "personal" else "nope"

    result = _freeze(reply, arms=("start", "personal", "main"),
                     contrasts=(("start", "personal"), ("start", "main")), budget=Budget(task_tokens=6_000))

    # The personal model matches the teacher's spread of actions on problems 0-3; its budget run stays failed and
    # its undecided run stays out.
    teacher = _scoring(result, "personal", "teacher_agreement")
    assert (teacher["problems"], teacher["counted"], teacher["mean"]) == (6, 5, 0.8)
    assert result["arms"]["personal"]["splits"]["real_uncorrected"]["mean"] == 0.0
    assert result["arms"]["personal"]["splits"]["real_uncorrected"]["runs"] == {"budget": 3, "failed": 12,
                                                                                "unverified": 3}
    # The main arm against its own other runs only: on problem 3 its two later answers match neither other run.
    main = _scoring(result, "main", "teacher_agreement")
    assert main["mean"] == round((3 + 1 / 3) / 5, 6)
    assert result["arms"]["main"]["splits"]["real_uncorrected"]["mean"] == round((4 / 3) / 5, 6)
    assert _scoring(result, "start", "teacher_agreement")["mean"] == 0.0
    row = _real(result)["teacher_agreement"]
    assert (row["problems"], row["difference"]) == (5, 0.8)
    assert _real(result)["primary"]["difference"] == 0.0  # the primary checker is unchanged


def test_teacher_agreement_needs_the_main_reference(vault):
    vault([_case(number, at=10.0 + number) for number in range(3)])

    result = _freeze(lambda model, number, run: f"answer {number}")

    assert "teacher_agreement" not in _real(result)
    assert "teacher_agreement" not in result["arms"]["personal"]["splits"]["real_uncorrected"]["scoring"]


# ── 4b. The right tool ──────────────────────────────────────────────────────


def test_right_tool_passes_the_same_tools_whatever_the_arguments_on_problems_whose_reference_calls_tools(vault):
    replay = [_case(number, at=10.0 + number,
                    reference=Action(tool_calls=(("read_file", {"path": f"{CANARY}-{number}.txt"}),)))
              for number in range(3)]
    replay.append(_case(3, at=20.0))  # its reference is an answer: not a right-tool problem
    vault(replay)

    def reply(model, number, run):
        if number == 3:
            return "answer 3" if model == "start" else "something else"
        return ("read_file", {"path": "other.txt"}) if model == "personal" else ("list_files", {})

    result = _freeze(reply)

    right = _scoring(result, "personal", "right_tool")
    assert (right["problems"], right["counted"], right["mean"]) == (3, 3, 1.0)
    assert _scoring(result, "start", "right_tool")["mean"] == 0.0  # a different tool still fails
    row = _real(result)["right_tool"]
    assert (row["problems"], row["difference"], row["baseline"], row["arm"]) == (3, 1.0, 0.0, 1.0)
    primary = _real(result)["primary"]
    assert (primary["problems"], primary["arm"]) == (4, 0.0)  # different arguments fail the primary check


# ── 4c. Judged long answers ─────────────────────────────────────────────────


class FakeJudge:
    """Passes an answer that says "good"; records what it was asked."""

    model_ref = "main"

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self.tokens = 0

    def judge(self, task, reference, candidate):
        self.calls.append((task, reference, candidate))
        self.tokens += 10
        return "good" in candidate


RELIABLE = Calibration(cases=10, agreed=10, false_pass=0, false_fail=0)
UNRELIABLE = Calibration(cases=10, agreed=5, false_pass=0, false_fail=5)


def _long_answers(vault):
    vault([_case(number, chat=f"c{number}", at=10.0 + number, reference=Action(text=f"{LONG} ({number})"))
           for number in range(4)])

    def reply(model, number, run):
        if model == "start":
            return "A bad answer, worked out from nothing in particular, that names no total at all."
        # Problem 3 offers the reference's total as one of two (METRIC_VERSION 3's guard fails it).
        return {0: "good: 85 EUR", 1: "good, 85 EUR all told", 2: "bad: 40 EUR", 3: "good: it is 85 or 60 EUR"}[number]

    return reply


def test_the_judge_decides_only_the_long_answers_and_the_primary_results_dont_change(vault):
    reply = _long_answers(vault)
    judge = FakeJudge()

    judged = _freeze(reply, judge=judge, calibration=RELIABLE)
    plain = _freeze(reply)

    row = _scoring(judged, "personal", "judged_long_answers")
    assert (row["problems"], row["counted"], row["mean"]) == (4, 4, 0.5)  # 0 and 1 pass; 2 fails; 3 is guarded
    assert _real(judged)["judged_long_answers"]["difference"] == 0.5
    block = judged["judged_long_answers"]
    assert (block["state"], block["reason"], block["tokens"]) == ("run", None, 80)
    assert block["runs"]["personal"] == {"undecided": 12, "passed": 6, "failed": 6, "still_undecided": 0}
    assert block["calibration"] == RELIABLE.to_dict()
    assert len(judge.calls) == 8  # each answer judged once per problem, never during the runs
    # The primary results are exactly those of a run with no judge: every long answer stays undecided there.
    def primary(found):
        return ({name: {key: value for key, value in part.items() if key not in evaluation.SCORINGS}
                 for name, part in found["contrasts"]["personal vs start"].items()},
                {arm: {split: {key: value for key, value in part.items() if key != "scoring"}
                       for split, part in row["splits"].items()} for arm, row in found["arms"].items()})

    assert primary(judged) == primary(plain)
    assert plain["arms"]["personal"]["splits"]["real_uncorrected"]["runs"] == {"unverified": 12}
    assert "judged_long_answers" not in plain


def test_an_unreliable_judge_decides_nothing_and_its_calibration_is_reported(vault):
    reply = _long_answers(vault)
    judge = FakeJudge()

    result = _freeze(reply, judge=judge, calibration=UNRELIABLE)

    block = result["judged_long_answers"]
    assert (block["state"], block["reason"], block["calibration"]["reliable"]) == ("not_run", "not_reliable", False)
    assert block["runs"]["personal"]["undecided"] == 12 and judge.calls == []
    assert "judged_long_answers" not in _real(result)


# ── E10: only aggregates leave ──────────────────────────────────────────────


def test_none_of_a12s_analyses_lets_a_problem_out(vault):
    replay = [_case(number, chat=f"c{number % 3}", at=SINCE + 1 + number) for number in range(6)]
    replay += [_case(10, at=SINCE + 20, reference=Action(tool_calls=(("read_file", {"path": f"{CANARY}.txt"}),))),
               _case(11, at=SINCE + 21, reference=Action(text=f"{LONG} {CANARY}")), _case(12, at=1.0)]
    vault(replay)

    def reply(model, number, run):
        if number == 10:
            return ("read_file", {"path": f"{CANARY}-{model}.txt"})
        return f"good {CANARY} answer {number}" if model != "start" else f"answer {number}"

    result = _freeze(reply, arms=("start", "personal", "main"), contrasts=(("start", "personal"), ("start", "main")),
                     since=SINCE, judge=FakeJudge(), calibration=RELIABLE)

    text = json.dumps(result)
    for secret in (CANARY, "dec_", "thread-", "gpt-", "answer ", "read_file", "good "):
        assert secret not in text
    assert {"gap_closed", "judged_long_answers"} <= set(result)
    assert set(evaluation.SCORINGS) <= set(_real(result))


def test_a_replayed_runs_action_is_kept_out_of_its_printed_form(learning_on):
    case = ReplayCase.from_content(_case(1))

    found = evaluation.run_replay_case(case, Arm("personal", model_ref="personal"),
                                       client=ScriptedClient(lambda *_: AIMessage(content="zebra-reply"), local=True))

    assert found.status == "failed" and found.action == Action(text="zebra-reply")
    assert "zebra" not in repr(found)  # kept for the freeze's scorings in memory, never printed
