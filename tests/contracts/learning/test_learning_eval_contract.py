"""Contracts of evaluation's records and the pack task format: TaskFixture and TestRun as packs
and evaluation produce them, and what a pack task and its verifier must be."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest

from row_bot.learning import contracts
from row_bot.learning.contracts import TaskFixture, TestRun
from row_bot.learning.evaluation import (
    Action,
    Arm,
    ReplayCase,
    compare,
    fixture_for,
    replay_fixture_for,
)
from row_bot.learning.packs import (
    PACKS,
    TOOLSETS,
    VERIFIER_KINDS,
    PackError,
    PackTask,
    Solution,
    Verifier,
    validate,
    visible_tasks,
)
from row_bot.learning.packs.verify import Verdict, solve, verify
from row_bot.learning.spending import TokenMeter
from tests.fixtures.learning_eval import ScriptedClient, oracle

pytestmark = pytest.mark.contract


def test_every_visible_task_has_the_pack_format_and_round_trips() -> None:
    tasks = visible_tasks()
    assert len({task.id for task in tasks}) == len(tasks) == 64
    for task in tasks:
        assert validate(task) is task
        assert task.pack in PACKS and task.split == "visible" and task.kind_of_work == PACKS[task.pack]
        assert task.verifier.kind in VERIFIER_KINDS and set(task.tools) <= set(TOOLSETS)
        data = json.loads(json.dumps(task.to_dict()))
        assert PackTask.from_dict(data) == task


def test_a_malformed_task_is_refused() -> None:
    good = visible_tasks(["structured_transforms"])[0]
    for bad in (replace(good, pack="poems"), replace(good, split="secret"), replace(good, tools=("shell",)),
                replace(good, verifier=Verifier("vibes")), replace(good, verifier=Verifier("file", "x", match="text")),
                replace(good, files={"../outside.txt": "x"}), replace(good, max_steps=0),
                replace(good, verifier=Verifier("code_tests", None, path="solution.py"))):
        with pytest.raises(PackError):
            validate(bad)
    with pytest.raises(PackError):
        PackTask.from_dict({**good.to_dict(), "version": 99})


def test_a_verifier_returns_a_verdict_of_its_own_kind() -> None:
    for task in visible_tasks():
        if task.verifier.kind == "code_tests":
            continue
        verdict = verify(task, solve(task, task.reference))
        assert isinstance(verdict, Verdict) and verdict.kind == task.verifier.kind and verdict.passed
        assert isinstance(solve(task, Solution()).apps, dict)


def test_a_pack_task_becomes_a_task_fixture_with_its_input_and_expected_blobs() -> None:
    task = visible_tasks(["tool_workflows"])[0]
    fixture, data, expected = fixture_for(task, created_at=10.0)
    assert isinstance(fixture, TaskFixture) and fixture.origin == "pack" and fixture.pack == "tool_workflows"
    assert fixture.input_blob == hashlib.sha256(data).hexdigest()
    assert fixture.expected_blob == hashlib.sha256(expected).hexdigest()
    assert PackTask.from_dict(json.loads(data)) == task and fixture.verifier == "app_state"
    assert contracts.from_manifest(contracts.to_manifest(fixture)) == fixture
    assert contracts.owner_threads(fixture) == ()  # a pack task belongs to no chat


def test_a_replayed_decision_becomes_a_fixture_its_chat_owns() -> None:
    case = ReplayCase("dec_7", "agent-child-1", "chat-1", 5.0, "Your work", "redacted", False,
                      ({"type": "human", "data": {"content": "hi", "type": "human"}},), None, "model:openai:gpt-test",
                      Action(text="Hello."), "captured", "exact_action", "hi")
    fixture, data, _ = replay_fixture_for(case, created_at=6.0)
    assert fixture.origin == "replay" and fixture.source_decision_id == "dec_7"
    assert contracts.owner_threads(fixture) == ("agent-child-1", "chat-1")
    assert ReplayCase.from_content({**json.loads(data), "id": "dec_7", "thread_id": "agent-child-1",
                                    "conversation_id": "chat-1", "created_at": 5.0,
                                    "kind_of_work": "Your work"}) == case


def test_a_comparison_gives_each_arm_a_test_run_against_the_baseline() -> None:
    tasks = visible_tasks(["document_lookups"])[:3]
    result = compare([Arm("without"), Arm("with", guidance=("x",), subject_kind="lesson", subject_id="les_1")],
                     tasks=tasks, client=ScriptedClient(oracle(tasks)), meter=TokenMeter(10**9), save=False)
    baseline, change = (arm.run for arm in result.arms)
    for run in (baseline, change):
        assert isinstance(run, TestRun) and contracts.from_json(contracts.to_json(run)) == run
        assert run.passed + run.failed == run.total == 3 and run.suite == "pack" and run.status == "completed"
        assert run.comparison_set == result.comparison_set and run.cost.tokens == 3 * 1_100
        assert run.budget_tokens == 3 * 150_000 and run.ended_at >= run.started_at
    assert baseline.subject_kind == "baseline" and baseline.baseline_id is None
    assert change.subject_kind == "lesson" and change.subject_id == "les_1" and change.baseline_id == baseline.id
