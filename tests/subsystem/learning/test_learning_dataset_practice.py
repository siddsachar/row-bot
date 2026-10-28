"""Practice in the training data (L4b; D6, O18, O71): with the build's switch off the dataset is byte for byte what it
was (rows, DataSpec, manifest); with it on, the main model's passing practice turns are their own slice, rendered in
the serving shape exactly as captured rows are, training only, at most a quarter of the rows; and held-out or hidden
problems never reach it. Pull-request lane (Part 3's freeze arm and A10's run rebuild rows and check their hash)."""

from __future__ import annotations

import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage, message_to_dict

from row_bot.learning import corrections, evaluation, practice
from row_bot.learning.evaluation import CaseResult
from row_bot.learning.improver import strategy
from row_bot.learning.spending import TokenMeter
from row_bot.learning.store import MARK_HELD_OUT, LearningStore
from row_bot.learning.training import dataset, recipes
from tests.fixtures.learning import CapturedWork, signal
from tests.fixtures.learning_eval import capture_decision
from tests.subsystem.learning.test_learning_dataset_shape import CLOUD_SYSTEM, CLOUD_TOOLS, ENVELOPE, CloudWork
from tests.subsystem.learning.test_learning_practice import GOOD, GOOD_2, Tester, lookup_task, teacher

pytestmark = [pytest.mark.subsystem]

HOUR = 3600.0
NIGHT = "2027-01-16"
#: This fixture's rows as ``main`` compiles them before L4b (``a9cc5ceeb``), captured and in the serving shape.
ROWS_BEFORE = {"captured": "ef330b4c5798e2367cfe226302986e92c0b90d39b4a1c6a7763da6c0ac711836",
               "serving": "9ac59db3d505222b2f5f27bcaf956a39eee7455c8b8924525dd459c40dda7dbc"}
MANIFEST_KEYS = {"format", "version", "id", "sha256", "recipe", "max_seq_length", "measured", "examples", "validation",
                 "historical", "rendering", "derived", "derived_ids", "left_out", "providers", "terms_note",
                 "corrections_waiting", "paths_made_relative", "first_at", "last_at", "conversations", "example_ids",
                 "validation_ids"}


@pytest.fixture
def recipe():
    return recipes.load("8gb")


@pytest.fixture
def store(learning_on, clock):
    return LearningStore(learning_on, clock=clock)


def switch(monkeypatch, on: bool) -> None:
    from row_bot import learning

    if on:
        monkeypatch.setenv(learning.PRACTICE_ENV, "1")
    else:
        monkeypatch.delenv(learning.PRACTICE_ENV, raising=False)


def fixture_work(writer, chats, clock) -> None:
    work = CapturedWork(writer, chats, clock)
    for index in range(6):
        work.turn(f"chat-{index}", f"Question {index}: what is 6 times 7?", f"It is 42 ({index}).")
    event = {"title": "Dentist", "date": "2026-10-23", "start": "10:00"}
    CloudWork(writer, chats, clock).turn("chat-c", "Add the dentist on Friday at 10",
                                         [("calendar_create_event", event, '{"created": "evt_1"}')], "Done.")


def corrected_lookups(writer, chats, clock, store) -> list[str]:
    work = CapturedWork(writer, chats, clock)
    replies = []
    for index in range(3):
        thread = f"lookups-{index}"
        reply = work.turn(thread, f"According to the rota policy, how many work shift {index}?", "Three.",
                          tool=False, person_message=True)[-1]
        replies.append(reply)
        store.put(signal(thread, f"sig_t{index}", decision_id=reply, created_at=clock.now, note="Old policy",
                         category="wrong_answer"))
    clock.advance(HOUR)
    corrections.confirm(store)
    return replies


def practice_turn(prompt: str, answer: str) -> dict:
    """A practice task's last request and reply as the real runtime sent them (the cloud model's own envelope)."""

    call = {"id": "call_p1", "name": "calendar_create_event",
            "args": {"title": "Gasket check", "date": "2027-02-01", "start": "09:00"}}
    sent = [*(SystemMessage(content=text) for text in CLOUD_SYSTEM), HumanMessage(content=prompt),
            AIMessage(content="", tool_calls=[call]), ToolMessage(content='{"created": "evt_9"}', tool_call_id="call_p1")]
    return {"messages": [message_to_dict(item) for item in sent], "tools": CLOUD_TOOLS,
            "reply": message_to_dict(AIMessage(content=answer)), "purpose": "agent"}


class Runner:
    """The real runtime as the night's practice uses it: every task passes and brings back its turn."""

    keep_turns = False

    def run(self, task, arm, *, budget, meter, arm_left):
        return CaseResult(task.id, "pack", task.kind_of_work, "passed", "answer", 500,
                          turn=practice_turn(task.prompt, task.reference.answer) if self.keep_turns else None)


def practise(store, clock, *tasks: dict) -> dict:
    return practice.practise(store, night=NIGHT, client=teacher(*tasks), tester=Tester(runner=Runner()),
                             method=strategy.FIRST, now=clock.now, meter=TokenMeter(10**9))


def compile_rows(store, chats, recipe, shape: str = "serving", **kwargs):
    envelope = ENVELOPE if shape == "serving" else None
    return dataset.compile_dataset(store, recipe, lookup=chats, refresh_vault=False, shape=shape, envelope=envelope,
                                   **kwargs)


@pytest.mark.parametrize("shape", ["captured", "serving"])
def test_with_practice_off_the_rows_are_what_they_were_before_practice(writer, chats, clock, store, recipe, shape):
    fixture_work(writer, chats, clock)

    compiled = compile_rows(store, chats, recipe, shape)

    assert compiled.sha256 == ROWS_BEFORE[shape]
    assert compiled.data_spec().sources == ("main_model_work",)
    assert set(compiled.manifest()) == MANIFEST_KEYS


def test_practice_kept_in_the_store_changes_nothing_while_the_switch_is_off(writer, chats, clock, store, recipe,
                                                                         monkeypatch):
    fixture_work(writer, chats, clock)
    corrected_lookups(writer, chats, clock, store)
    before = compile_rows(store, chats, recipe)

    switch(monkeypatch, True)
    assert practise(store, clock, GOOD, GOOD_2)["passed"] == 2 and len(practice.turns(store)) == 2
    switch(monkeypatch, False)
    after = compile_rows(store, chats, recipe, practice_share=0.25)

    assert after.id == before.id and after.sha256 == before.sha256
    assert after.data_spec() == before.data_spec() and after.manifest() == before.manifest()
    assert "practice" not in after.manifest()


def test_practice_rows_train_rendered_like_captured_rows_and_at_most_a_quarter(writer, chats, clock, store, recipe,
                                                                             monkeypatch):
    work = CloudWork(writer, chats, clock)
    for index in range(3):
        work.turn(f"chat-{index}", f"Add the review {index}", [(
            "calendar_create_event", {"title": f"Review {index}", "date": "2026-10-23", "start": "10:00"},
            '{"created": "evt_1"}')], "Done.")
    corrected_lookups(writer, chats, clock, store)
    switch(monkeypatch, True)
    practise(store, clock, GOOD, GOOD_2)

    compiled = compile_rows(store, chats, recipe)

    drills = [item for item in compiled.train if item.practice]
    work_rows = [item for item in (*compiled.train, *compiled.validation) if not item.practice]
    assert len(work_rows) == 3 and len(drills) == 1  # a quarter of the training rows at most: 3 / 3
    assert compiled.left_out["practice_over_share"] == 1
    assert not [item for item in compiled.validation if item.practice]  # the person's own work validates
    captured = next(item for item in compiled.train if not item.practice)
    for drill in drills:
        # The same serving shape as a captured row: its prompt, the core tools bound, the rest behind the bridge.
        names = [tool["function"]["name"] for tool in drill.row["tools"]]
        assert names == [tool["function"]["name"] for tool in captured.row["tools"]]
        assert names == ["workspace_read_file", "save_memory", "tool_search", "tool_invoke"]
        assert drill.derived and drill.row["messages"][0] == captured.row["messages"][0]
        assert [message["function"]["name"] for message in
                [call for message in drill.row["messages"] for call in message.get("tool_calls") or ()]] == [
                   "tool_search", "tool_invoke"]
    assert compiled.data_spec().sources == ("main_model_work", "practice")
    manifest = compiled.manifest()
    assert manifest["practice"] == {"rows": 1, "conversations": ["lookups-0", "lookups-1", "lookups-2"]}
    assert "lookups-0" not in manifest["conversations"]  # practice holds no turn of the person's own
    # Deleting a chat a practice row came from finds the training run that leased it.
    from row_bot.learning.training import jobs as jobs_module

    dataset.write(compiled, jobs_module.runs_dir(store.root) / "job_practice" / "data")
    assert dataset.runs_holding(["lookups-1"], store.root) == ["job_practice"]
    assert compile_rows(store, chats, recipe, practice_share=0.1).left_out["practice_over_share"] == 2


def test_held_out_and_hidden_problems_never_reach_the_practice_slice(writer, chats, clock, store, recipe,
                                                                    monkeypatch):
    replies = corrected_lookups(writer, chats, clock, store)
    held = store.get("decision", replies[1])
    store.add_marks([("decision", held.id, held.conversation_id)], MARK_HELD_OUT)
    for index in range(4):  # the vault holds every 4th: "Errand 3: book room 3 for Friday."
        clock.advance(60)
        capture_decision(writer, chats, "chat-errands", person=f"Errand {index}: book room {index} for Friday.",
                         answer=AIMessage(content=f"Booked room {index}."))
    evaluation.refresh_audit(asked_by="nightly")
    switch(monkeypatch, True)
    hidden_twin = lookup_task("Errand 3: book room 3 for Friday.", "Room 3", "Room 4", body="Room 3")
    client = teacher(GOOD, hidden_twin)
    practice.practise(store, night=NIGHT, client=client, tester=Tester(runner=Runner()), method=strategy.FIRST,
                      now=clock.now, meter=TokenMeter(10**9))

    compiled = compile_rows(store, chats, recipe)

    text = json.dumps([item.row for item in compiled.train if item.practice])
    assert GOOD["prompt"] in text
    assert "Errand 3" not in text and "how many work shift 1" not in text
    assert "how many work shift 1" not in json.dumps([str(m.content) for m in client.calls[0]["messages"]])
