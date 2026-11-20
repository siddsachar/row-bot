"""Practice (L4b; D17, O18, O21): tasks Row-Bot makes up from its own mistakes. A task is kept only when its check
passes its right answer and fails its wrong one and the audit vault says it is novel (a yes or no); what the model
is shown never holds a held-out decision; coding practice needs a container; the night makes, runs and keeps them as
one run that deleting a source chat takes out of use; drafted changes are tested on them; with the build's switch
off there is no practice at all. Pull-request lane (E10's leave-out rules)."""

from __future__ import annotations

import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from row_bot.learning import changes, corrections, diagnosis, evaluation, practice
from row_bot.learning.evaluation import CaseResult
from row_bot.learning.improver import strategy
from row_bot.learning.practice import generate, sandbox
from row_bot.learning.spending import TokenMeter
from row_bot.learning.store import MARK_HELD_OUT, LearningStore
from tests.fixtures.learning import CapturedWork, signal
from tests.fixtures.learning_eval import ScriptedClient, capture_decision

pytestmark = [pytest.mark.subsystem]

HOUR = 3600.0
NIGHT = "2027-01-16"


@pytest.fixture
def work(writer, chats, clock):
    return CapturedWork(writer, chats, clock)


@pytest.fixture
def store(learning_on, clock):
    return LearningStore(learning_on, clock=clock)


@pytest.fixture
def practice_on(monkeypatch):
    from row_bot import learning

    monkeypatch.setenv(learning.PRACTICE_ENV, "1")


def lookups(work, store, clock, count: int = 3) -> list[str]:
    """``count`` document lookups the person corrected with Teach, each in its own chat."""

    replies = []
    for index in range(count):
        thread = f"chat-{index}"
        reply = work.turn(thread, f"According to the rota policy, how many work shift {index}?", "Three.",
                          tool=False, person_message=True)[-1]
        replies.append(reply)
        store.put(signal(thread, f"sig_t{index}", decision_id=reply, created_at=clock.now, note="Old policy",
                         category="wrong_answer"))
    clock.advance(HOUR)
    corrections.confirm(store)
    return replies


def lookup_task(prompt: str, answer: str, wrong: str, *, body: str = "") -> dict:
    """A made-up document lookup, as the model writes one (no id, split or pack)."""

    body = body or f"Status: current\nThe answer is {answer}.\n"
    return {"prompt": prompt, "files": {"docs/ops-7.md": body}, "apps": {}, "tools": ["files"], "max_steps": 8,
            "verifier": {"kind": "answer", "expected": answer, "match": "exact", "path": "", "tolerance": 0},
            "reference": {"answer": answer, "files": {}, "calls": []},
            "decoy": {"answer": wrong, "files": {}, "calls": []}}


GOOD = lookup_task("In the Quillon depot memo, which crate colour holds the spare gaskets?", "Teal", "Amber")
GOOD_2 = lookup_task("In the Quillon depot memo, which bay is the overflow bay?", "Bay 9", "Bay 4")


def teacher(*tasks: dict) -> ScriptedClient:
    return ScriptedClient(lambda messages, tools, model_ref: AIMessage(content=json.dumps(list(tasks))))


def issue_of(store, clock, kind: str = "document_lookups"):
    issues, _left = diagnosis.collect(store, now=clock.now)
    return next(issue for issue in issues if issue.kind == kind)


def make(store, clock, client, *, count: int = 4, container: bool = False):
    return generate.make(store, issue_of(store, clock), client=client, count=count, now=clock.now,
                         meter=TokenMeter(10**9), container=lambda: container)


def test_a_task_is_kept_only_when_its_check_tells_right_from_wrong_and_the_vault_says_it_is_novel(
        work, store, clock, writer, chats):
    lookups(work, store, clock)
    for index in range(4):  # the vault holds every 4th: "Errand 3: book room 3 for Friday."
        clock.advance(60)
        capture_decision(writer, chats, "chat-errands", person=f"Errand {index}: book room {index} for Friday.",
                         answer=AIMessage(content=f"Booked room {index}."))
    evaluation.refresh_audit(asked_by="nightly")
    no_check = lookup_task("In the Quillon depot memo, who signs the gasket log?", "Rhea", "Rhea")
    hidden_twin = lookup_task("Errand 3: book room 3 for Friday.", "Room 3", "Room 4", body="Room 3")
    sample_twin = {key: value for key, value in generate.sample("document_lookups").to_dict().items()
                   if key not in ("format", "version", "id", "split", "pack")}
    client = teacher(GOOD, no_check, hidden_twin, sample_twin, {"prompt": ""})

    made = make(store, clock, client, count=5)

    assert [task.prompt for task in made.kept] == [GOOD["prompt"]]
    assert (made.unverifiable, made.not_novel, made.repeats, made.unreadable) == (1, 1, 1, 1)
    kept = made.kept[0]
    assert kept.split == "practice" and kept.pack == "document_lookups" and kept.id.startswith("practice_")
    # One call, tagged as Learning's teacher; the vault answered yes or no and logged the looks.
    assert [call["purpose"] for call in client.calls] == ["learning_teacher"]
    novelty = [look.answer for look in evaluation.audit_looks() if look.kind == "novelty"]
    assert sorted(novelty) == ["no", "yes"]


def test_what_the_model_is_shown_never_holds_a_held_out_decision(work, store, clock):
    replies = lookups(work, store, clock)
    held = store.get("decision", replies[1])
    store.add_marks([("decision", held.id, held.conversation_id)], MARK_HELD_OUT)
    client = teacher(GOOD)

    make(store, clock, client)

    shown = "\n".join(str(message.content) for message in client.calls[0]["messages"])
    assert "how many work shift 0" in shown and "how many work shift 2" in shown
    assert "how many work shift 1" not in shown
    # Made up from the pattern, in the packs' shape: the sample is one visible task, never a hidden one.
    assert generate.sample("document_lookups").split == "visible"


def test_coding_practice_needs_a_container_and_only_coding_tasks_run_code(work, store, clock):
    lookups(work, store, clock)
    issue = issue_of(store, clock)
    coding = diagnosis.Issue("small_coding", "corrected", issue.evidence)
    client = teacher(GOOD)

    made = generate.make(store, coding, client=client, count=2, now=clock.now, container=lambda: False)

    assert made.state == "needs_container" and not made.kept and client.calls == []
    runs_code = {**GOOD, "verifier": {"kind": "code_tests", "expected": "import unittest\n", "path": "a.py"}}
    with pytest.raises(Exception, match="only coding tasks run code"):
        generate.task_from(runs_code, "document_lookups")
    # A coding practice task made where a container was runs only where there is one (O21).
    made_with_one = generate.task_from({**runs_code, "verifier": {**runs_code["verifier"], "path": "a.py"}},
                                       "small_coding")
    assert sandbox.runnable([made_with_one], container=False) == ([], 1)
    assert sandbox.runnable([made_with_one], container=True) == ([made_with_one], 0)


class Tester:
    """The changes' tester as practice uses it: the main model, its runner, the container check."""

    __test__ = False

    def __init__(self, client=None, *, runner=None, container: bool = False) -> None:
        self.client = client
        self.plan = changes.TestPlan(runtime=runner is not None)
        self.runner = runner
        self.container = container

    def _has_container(self) -> bool:
        return self.container

    def _main(self) -> str:
        return "model:fake:main"

    def _runner(self, arms, main):
        return self.runner, None


def answers_from_the_files(messages, tools, model_ref):
    """The assistant reads the task's prompt and answers what the made-up memo says."""

    prompt = next(str(m.content) for m in messages if isinstance(m, HumanMessage))
    return AIMessage(content={GOOD["prompt"]: "Teal", GOOD_2["prompt"]: "Bay 4"}.get(prompt, "?"))


def test_the_night_makes_runs_and_keeps_practice_and_a_deleted_chat_takes_it_out_of_use(
        work, store, clock, practice_on):
    lookups(work, store, clock)

    report = practice.practise(store, night=NIGHT, client=teacher(GOOD, GOOD_2),
                               tester=Tester(ScriptedClient(answers_from_the_files)), method=strategy.FIRST,
                               now=clock.now, meter=TokenMeter(10**9))

    assert report["state"] == "done" and report["made"] == 2 and report["ran"] == 2 and report["passed"] == 1
    assert report["by_kind"] == {"document_lookups": [2, 1]}
    assert {task.prompt for task in practice.pool(store, "document_lookups")} == {GOOD["prompt"], GOOD_2["prompt"]}
    assert practice.pool(store, "tool_workflows") == []
    # Nothing is made again from the same evidence.
    again = teacher(GOOD)
    practice.practise(store, night="2027-01-17", client=again, tester=Tester(ScriptedClient(answers_from_the_files)),
                      method=strategy.FIRST, now=clock.now, meter=TokenMeter(10**9))
    assert again.calls == []
    # Deleting a chat its issue came from takes the night's practice out of use.
    store.purge_thread("chat-0")
    assert practice.pool(store) == [] and practice.turns(store) == []


def test_drafted_changes_are_tested_on_practice_of_their_kind(work, store, clock, practice_on):
    lookups(work, store, clock)
    practice.practise(store, night=NIGHT, client=teacher(GOOD, GOOD_2),
                      tester=Tester(ScriptedClient(answers_from_the_files)), method=strategy.FIRST, now=clock.now,
                      meter=TokenMeter(10**9))
    tester = changes.Tester(store.root, store=store, plan=changes.TestPlan.of(strategy.FIRST, runtime=False))

    chosen = tester._visible("document_lookups", container=False)  # noqa: SLF001

    assert tester.plan.practice == strategy.FIRST.practice.tested == 2
    assert {task.prompt for task in chosen if task.split == "practice"} == {GOOD["prompt"], GOOD_2["prompt"]}
    assert not [task for task in tester._visible("tool_workflows", container=False)  # noqa: SLF001
                if task.split == "practice"]


def test_a_practice_run_through_the_real_runtime_keeps_a_passing_turn_as_sent(work, store, clock, practice_on):
    lookups(work, store, clock)
    turn = {"messages": [{"type": "human", "data": {"content": GOOD["prompt"]}}], "tools": None,
            "reply": {"type": "ai", "data": {"content": "Teal"}}, "purpose": "agent"}

    class Runner:
        keep_turns = False

        def run(self, task, arm, *, budget, meter, arm_left):
            passed = task.prompt == GOOD["prompt"]
            return CaseResult(task.id, "pack", task.kind_of_work, "passed" if passed else "failed", "answer", 500,
                              turn=turn if passed and self.keep_turns else None)

    runner = Runner()
    practice.practise(store, night=NIGHT, client=teacher(GOOD, GOOD_2), tester=Tester(runner=runner),
                      method=strategy.FIRST, now=clock.now, meter=TokenMeter(10**9))

    assert runner.keep_turns is True
    [kept] = practice.turns(store)
    assert kept["turn"] == turn and kept["kind"] == "document_lookups" and kept["model_ref"] == "model:fake:main"
    assert set(kept["threads"]) == {"chat-0", "chat-1", "chat-2"}


def test_with_the_switch_off_there_is_no_practice(work, store, clock):
    lookups(work, store, clock)
    client = teacher(GOOD)

    assert practice.practise(store, night=NIGHT, client=client, tester=Tester(), method=strategy.FIRST,
                             now=clock.now) is None
    assert client.calls == [] and practice.pool(store) == [] and practice.last_night(store) is None
    assert changes.TestPlan.of(strategy.FIRST).practice == 0
    assert "practises" not in strategy.FIRST.describe()


def test_with_practice_on_the_method_says_it_practises_and_only_then_can_change_it(practice_on):
    import random

    from row_bot.learning.improver import meta

    assert "practises on made-up tasks" in strategy.FIRST.describe()
    changed = {path for seed in range(200)
               for path in strategy.differences(strategy.FIRST, meta.mutate(strategy.FIRST, random.Random(seed)))}
    assert {"practice.made", "practice.tested"} & changed


def test_without_practice_no_new_method_changes_its_practice():
    import random

    from row_bot.learning.improver import meta

    changed = {path for seed in range(200)
               for path in strategy.differences(strategy.FIRST, meta.mutate(strategy.FIRST, random.Random(seed)))}
    assert not {"practice.made", "practice.tested"} & changed


def test_a_method_written_before_practice_reads_with_the_first_methods_practice():
    text = strategy.FIRST.to_dict()
    text.pop("practice")
    text["schema_version"] = 1

    assert strategy.from_dict(text) == strategy.FIRST


def test_a_night_stopped_before_its_practice_ran_runs_the_rest_when_it_goes_on(work, store, clock, practice_on):
    lookups(work, store, clock)
    answers = [False, True]  # the person comes back after the first task ran

    stopped = practice.practise(store, night=NIGHT, client=teacher(GOOD, GOOD_2),
                                tester=Tester(ScriptedClient(answers_from_the_files)), method=strategy.FIRST,
                                should_stop=lambda: answers.pop(0) if answers else False, now=clock.now,
                                meter=TokenMeter(10**9))
    assert stopped["state"] == "stopped" and stopped["made"] == 2 and stopped["ran"] == 1

    later = teacher(GOOD)
    went_on = practice.practise(store, night=NIGHT, client=later, tester=Tester(ScriptedClient(answers_from_the_files)),
                                method=strategy.FIRST, now=clock.now, meter=TokenMeter(10**9))

    assert went_on["state"] == "done" and went_on["made"] == 2 and went_on["ran"] == 2
    assert later.calls == []  # nothing was made again
    assert len(practice.pool(store)) == 2
