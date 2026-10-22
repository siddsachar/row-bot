"""The seeded people and their generator (S1): a seed in, the same history out, every outcome checkable.

The release check's people are made up and their data are plain files in ``tests/e2e/learning/seeded/
people/``. These tests hold the generator to its promises: the same seed gives the same history on every
system (line endings included), a longer run keeps the earlier days, each request's right outcome passes
the product's own verifier and its plausible mistake fails it, and the past is a well-formed conversation
for the past-chat import.
"""

from __future__ import annotations

import shutil
from collections import Counter

import pytest

from row_bot.learning.packs.verify import solve, verify
from tests.e2e.learning.seeded import coding_bank, generator

pytestmark = [pytest.mark.subsystem]

SEED = 20261022


@pytest.fixture(scope="module")
def histories():
    return {person: generator.generate(person, seed=SEED, days=8) for person in generator.people()}


def test_there_are_three_or_four_made_up_people_each_with_a_description():
    people = generator.people()
    assert 3 <= len(people) <= 4
    for person_id in people:
        person = generator.load_person(person_id)
        assert person["fictional"] is True
        for key in ("name", "about", "uses", "asks", "corrects", "tools", "approval_mode", "policy", "work"):
            assert person[key], (person_id, key)
        assert person["files"], person_id  # their documents
        for fact in person.get("facts") or ():
            assert all(path in person["files"] for path in fact["files"]), (person_id, fact["id"])


def test_the_same_seed_gives_the_same_history_and_another_seed_another(histories):
    for person, history in histories.items():
        again = generator.generate(person, seed=SEED, days=8)
        assert again.fingerprint() == history.fingerprint()
        assert again.to_dict() == history.to_dict()
        assert generator.generate(person, seed=SEED + 1, days=8).fingerprint() != history.fingerprint()


def test_a_longer_run_keeps_the_earlier_days(histories):
    for person, history in histories.items():
        longer = generator.generate(person, seed=SEED, days=12)
        assert [item.to_dict() for item in longer.past] == [item.to_dict() for item in history.past]
        assert [item.to_dict() for item in longer.stream[: len(history.stream)]] == [
            item.to_dict() for item in history.stream]
        assert len(longer.stream) > len(history.stream)


def test_the_pasts_replies_are_in_the_main_models_words_and_keep_every_fact(histories):
    for person, history in histories.items():
        templates = generator.generate(person, seed=SEED, days=0, phrased=False)
        pairs = [(plain, phrased) for before, after in zip(templates.past, history.past, strict=True)
                 for plain, phrased in zip(before.requests, after.requests, strict=True)]
        reworded = [(plain, phrased) for plain, phrased in pairs if plain.answer != phrased.answer]
        assert len(reworded) >= 0.8 * len(pairs), person  # the kept file covers this seed's past
        for plain, phrased in reworded:
            assert generator.keeps_facts(plain.answer, phrased.answer, generator.answer_of(plain)), phrased.id
        # Not a few replies over and over: about one of its own per distinct template reply, or more.
        assert len({phrased.answer for _, phrased in reworded}) >= 0.9 * len({plain.answer for plain, _ in reworded})
        # Another seed has another past: the kept replies don't apply to it.
        assert (generator.generate(person, seed=SEED + 1, days=0).to_dict()
                == generator.generate(person, seed=SEED + 1, days=0, phrased=False).to_dict())


def test_the_history_is_the_same_with_windows_line_endings(tmp_path, monkeypatch, histories):
    people = tmp_path / "people"
    shutil.copytree(generator.PEOPLE_DIR, people)
    for path in people.rglob("*"):
        if path.is_file():
            path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    monkeypatch.setattr(generator, "PEOPLE_DIR", people)
    person = generator.people()[0]
    assert generator.generate(person, seed=SEED, days=8).fingerprint() == histories[person].fingerprint()


def test_every_live_request_has_a_hidden_intent_and_a_checkable_right_outcome(histories):
    kinds_seen: Counter = Counter()
    for history in histories.values():
        days = [request.day for request in history.stream]
        assert days == sorted(days) and min(days) == 1 and max(days) == 8
        low, high = history.person["policy"]["requests_per_day"]
        assert all(low <= count <= high for count in Counter(days).values())
        for request in history.stream:
            kinds_seen[request.intended] += 1
            assert request.prompt.strip() and request.intent.strip()
            assert request.intent not in request.prompt  # the intent is the person's, never sent as is
            if request.task.verifier.kind == "code_tests":
                continue  # run in a sandbox: the slow test below
            assert verify(request.task, solve(request.task, request.task.reference)).passed, request.id
            assert not verify(request.task, solve(request.task, request.task.decoy)).passed, request.id
    # Across the people: documents, transforms, coding, tool workflows and other work.
    assert set(kinds_seen) == set(generator.KINDS_OF_WORK)


@pytest.mark.slow
@pytest.mark.platform
def test_every_coding_request_and_coding_problem_is_checked_by_its_tests_in_a_sandbox(histories):
    for problem in coding_bank.PROBLEMS:
        path = f"tools/{problem['module']}.py"
        from row_bot.learning.packs import PackTask, Solution, Verifier

        task = PackTask(id=problem["id"], pack="small_coding", split="visible", prompt=problem["description"],
                        verifier=Verifier("code_tests", coding_bank.tests_for(problem["name"], path, problem["tests"]),
                                          path=path),
                        reference=Solution("done", {path: coding_bank.code(problem["reference"])}),
                        decoy=Solution("done", {path: coding_bank.code(problem["decoy"])}))
        assert verify(task, solve(task, task.reference), backend="local").passed, problem["id"]
        assert not verify(task, solve(task, task.decoy), backend="local").passed, problem["id"]
    coding = [request for history in histories.values() for request in history.stream
              if request.task.verifier.kind == "code_tests"]
    assert coding
    for request in coding[:4]:
        assert verify(request.task, solve(request.task, request.task.reference), backend="local").passed


def test_the_world_moves_on_with_each_right_outcome(histories):
    # A request that writes or moves a file leaves it for the next ones; app checks count what came before.
    for history in histories.values():
        moved = [request for request in history.stream if request.template == "move_file"]
        for request in moved:
            assert request.approvals and request.approvals[0] in request.inputs
        mail = [request for request in history.stream if request.template == "mail_send"]
        for request in mail:
            (check,) = request.task.verifier.expected
            assert check["count"] >= 1


def test_the_past_is_thirty_days_of_well_formed_conversations(histories):
    for history in histories.values():
        days = {conversation.day for conversation in history.past}
        assert days == set(range(-generator.PAST_DAYS, 0))
        person = generator.load_person(history.person["id"])
        tool_names = {"workspace_read_file", "workspace_write_file", "workspace_move_file",
                      "calendar_create_event", "mail_send", "mail_archive", "mail_list", "issues_update",
                      "issues_comment", "calculate"}
        mistakes = 0
        for conversation in history.past:
            assert 1 <= len(conversation.requests) <= 3
            for request in conversation.requests:
                assert request.day == conversation.day and request.answer
                assert {step.name for step in request.steps} <= tool_names
                for step in request.steps:
                    assert step.result
                if request.mistake is not None:
                    mistakes += 1
                    _, answer, correction = request.mistake
                    assert answer != request.answer and correction
        share = mistakes / sum(len(conversation.requests) for conversation in history.past)
        assert 0 < share < 0.3, (person["id"], share)


@pytest.mark.slow
def test_the_seeded_requests_are_novel_against_the_hidden_packs(learning_on, histories):
    # Nothing the seeded people ask is close to a hidden pack task (the vault answers yes or no only).
    from row_bot.learning import evaluation
    from row_bot.learning.store import LearningStore

    LearningStore(learning_on).create()
    texts = {request.task.text() for history in histories.values() for request in history.stream}
    texts |= {request.task.text() for history in histories.values() for conversation in history.past[:20]
              for request in conversation.requests}
    assert all(evaluation.is_novel(text, asked_by="tests: seeded people novelty") for text in texts)


@pytest.mark.slow
def test_the_past_goes_through_the_past_chat_import_as_partial_history(learning_on, clock, chats):
    # The release check writes each past conversation as these messages; Row-Bot's own import reads them.
    from row_bot.learning import past_chats
    from row_bot.learning.contracts import Decision, Episode
    from row_bot.learning.store import LearningStore
    from tests.e2e.learning.seeded.live import _past_messages, _thread_id

    history = generator.generate("wren", seed=SEED, days=1)
    past = [item for item in history.past if item.day >= -4]  # a few days of it: the same shapes as all 30
    assert any(request.mistake for item in history.past for request in item.requests)
    now = float(clock())

    class Past:
        def __init__(self) -> None:
            self.saved = {_thread_id("wren", item.id): (item, _past_messages(item)) for item in past}

        def conversations(self, since):
            return [(thread, now + item.day * 86_400 + 9 * 3_600) for thread, (item, _) in self.saved.items()]

        def messages(self, thread_id):
            return list(self.saved[thread_id][1])

        def window_start(self, thread_id, since, created_at):
            return 0, created_at

    source = Past()
    for thread in source.saved:
        chats.chat(thread)
    store = LearningStore(learning_on)
    store.create()
    progress = past_chats.run_import(store=store, source=source, lookup=chats, clock=clock, busy=lambda: False)
    assert progress == past_chats.ImportProgress(total=len(past), done=len(past), state="ready")
    episodes = [item for item in store.find("episode", limit=10_000) if isinstance(item, Episode)]
    turns = sum(len(item.requests) + sum(1 for request in item.requests if request.mistake) for item in past)
    assert len(episodes) == turns
    assert all(item.historical and item.fidelity == "partial" for item in episodes)
    decisions = [item for item in store.find("decision", limit=100_000) if isinstance(item, Decision)]
    steps = sum(len(request.steps) + (len(request.mistake[0]) if request.mistake else 0)
                for item in past for request in item.requests)
    assert sum(1 for item in decisions if item.kind == "tool_call") == steps
    assert all(item.executed == "repaired" and item.raw_output_blob is None
               for item in decisions if item.kind == "model_call")
