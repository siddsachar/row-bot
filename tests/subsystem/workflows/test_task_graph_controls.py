from dataclasses import replace
import json
import threading

import pytest

from row_bot.application import task_graph_controls as control
from tests.fixtures.tasks import fresh_tasks_module

pytestmark = pytest.mark.subsystem


@pytest.fixture
def owner(tmp_path, monkeypatch):
    tasks = fresh_tasks_module(tmp_path, monkeypatch)
    monkeypatch.setattr(control, "tasks", tasks)
    monkeypatch.setattr(tasks, "_canonicalize_agent_profile_reference", lambda value: value)
    monkeypatch.setattr(tasks, "_canonicalize_workflow_model_override", lambda value: value)
    monkeypatch.setattr(tasks, "run_task_background", lambda *a, **k: pytest.fail("unexpected run"))
    monkeypatch.setattr(tasks, "_get_scheduler", lambda: pytest.fail("unexpected scheduler"))
    task_id = tasks.create_task(name="Synthetic graph", prompts=["Draft"], channels=[], enabled=False)
    return tasks, task_id


def store(owner, steps):
    tasks, task_id = owner
    conn = tasks._get_conn()
    try:
        conn.execute("UPDATE tasks SET steps = ?, advanced_mode = 1 WHERE id = ?",
                     (json.dumps(steps), task_id))
        conn.commit()
    finally:
        conn.close()
    return control.get_task_graph(task_id)


def edits(snapshot):
    return tuple(control.TaskGraphStepEdit(s.id, s.type, s.fields) for s in snapshot.steps)


def save(snapshot, steps=None, **kwargs):
    return control.update_saved_task_graph(snapshot.task_id, steps or edits(snapshot),
        expected_revision=snapshot.revision, validate=kwargs.pop("validate", lambda: None), **kwargs)


def test_reorder_preserves_named_outputs_branch_identity_and_hidden_metadata(owner, monkeypatch):
    tasks, task_id = owner
    snapshot = store(owner, [
        {"id": "draft", "type": "prompt", "prompt": "Draft", "next": "approve",
         "model_override": "legacy:exact", "future_metadata": {"literal": [1, 2]}},
        {"id": "other", "type": "prompt", "prompt": "Other"},
        {"id": "approve", "type": "approval", "message": "Review {{step.draft.output}}",
         "if_approved": "end", "if_denied": "other", "timeout_minutes": 0},
    ])
    before, _ = tasks.read_task_for_edit(task_id)
    monkeypatch.setattr(tasks, "_canonicalize_workflow_steps", lambda *a: pytest.fail("model rewrite"))
    monkeypatch.setattr(tasks, "evaluate_condition", lambda *a: pytest.fail("provider evaluation"))
    saved = save(snapshot, (edits(snapshot)[1], edits(snapshot)[0], edits(snapshot)[2]))
    after, _ = tasks.read_task_for_edit(task_id)
    assert [s.id for s in saved.steps] == ["other", "draft", "approve"]
    assert after["steps"][1] == before["steps"][0]
    assert after["steps"][2] == before["steps"][2]
    assert after["channels"] == [] and not after["enabled"]
    assert tasks.get_recent_runs() == []


def test_all_seven_step_types_and_retry_delay_are_saved_without_execution(owner):
    tasks, task_id = owner
    child = tasks.create_task(name="Child", prompts=["Child"], enabled=False)
    snapshot = control.get_task_graph(task_id)
    F, S = control.TaskGraphFields, control.TaskGraphStepEdit
    steps = (
        S("p", "prompt", F(prompt="Hello", max_retries=3, retry_delay_seconds=9, on_error="stop")),
        S("c", "condition", F(condition="and:[json:count:gte:2,not_empty]", if_true="a", if_false="end")),
        S("a", "approval", F(message="Accept?", timeout_minutes=0, if_approved="s", if_denied="end")),
        S("s", "subtask", F(task_id=child, pass_output=False)),
        S("d", "delegate_agent", F(objective="Inspect", profile="worker", editing_safety="read_only",
                                    return_mode="background", timeout_seconds=99)),
        S("w", "wait_for_agents", F(run_ids=("run_1",), timeout_seconds=300)),
        S("n", "notify", F(message="Complete", channel="desktop", next="end")),
    )
    result = save(snapshot, steps)
    assert [(s.id, s.type) for s in result.steps] == [(s.id, s.type) for s in steps]
    raw = tasks.get_task(task_id)["steps"]
    assert raw[4]["wait"] is False and raw[4]["workspace_mode"] == "read_only"
    assert raw[3]["pass_output"] is False and raw[0]["retry_delay_seconds"] == 9
    assert tasks.get_recent_runs() == []


@pytest.mark.parametrize("bad", [
    control.TaskGraphFields(prompt=""), control.TaskGraphFields(prompt="x", max_retries=0),
    control.TaskGraphFields(prompt="x", retry_delay_seconds=301),
    control.TaskGraphFields(prompt="x", max_retries=True),
    control.TaskGraphFields(prompt="x", channel="desktop"),
    control.TaskGraphFields(prompt="x", on_error="ignore"),
])
def test_invalid_fields_do_not_save(owner, bad):
    tasks, task_id = owner
    snapshot = control.get_task_graph(task_id)
    with pytest.raises(control.TaskGraphError, match="invalid_task_graph"):
        save(snapshot, (control.TaskGraphStepEdit("p", "prompt", bad),))
    assert control.get_task_graph(task_id) == snapshot


@pytest.mark.parametrize("condition", ["unknown:x", "gte:nan", "matches:[", "and:[]", "json:missing"])
def test_invalid_conditions_are_rejected_without_evaluation(owner, monkeypatch, condition):
    monkeypatch.setattr(owner[0], "evaluate_condition", lambda *a: pytest.fail("evaluated"))
    snapshot = control.get_task_graph(owner[1])
    with pytest.raises(control.TaskGraphError):
        save(snapshot, (control.TaskGraphStepEdit("c", "condition",
            control.TaskGraphFields(condition=condition, if_true="end")),))
    assert control.get_task_graph(owner[1]) == snapshot


def test_llm_condition_is_saved_without_provider_call(owner, monkeypatch):
    monkeypatch.setattr(owner[0], "_eval_llm_condition", lambda *a: pytest.fail("provider"))
    snapshot = control.get_task_graph(owner[1])
    result = save(snapshot, (control.TaskGraphStepEdit("c", "condition",
        control.TaskGraphFields(condition="llm:Was the task completed?", if_true="end")),))
    assert result.steps[0].fields.condition.startswith("llm:")


@pytest.mark.parametrize("field,value", [("next", "missing"), ("next", "p"),
    ("prompt", "{{step.removed.output}}")])
def test_dangling_and_self_references_cannot_silently_end_execution(owner, field, value):
    snapshot = control.get_task_graph(owner[1])
    fields = replace(control.TaskGraphFields(prompt="Text"), **{field: value})
    with pytest.raises(control.TaskGraphError, match="task_graph_invalid_reference"):
        save(snapshot, (control.TaskGraphStepEdit("p", "prompt", fields),))


def test_stale_revision_preserves_concurrent_basic_edit(owner):
    tasks, task_id = owner
    snapshot = control.get_task_graph(task_id)
    tasks.update_task(task_id, name="Concurrent edit")
    with pytest.raises(control.TaskGraphError, match="task_revision_conflict"):
        save(snapshot)
    assert tasks.get_task(task_id)["name"] == "Concurrent edit"


def test_simultaneous_duplicate_save_admits_exactly_one(owner):
    snapshot = control.get_task_graph(owner[1])
    barrier = threading.Barrier(2)
    results = []
    def submit():
        barrier.wait(timeout=5)
        try:
            results.append(save(snapshot))
        except control.TaskGraphError as exc:
            results.append(exc.code)
    workers = [threading.Thread(target=submit) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=5)
        assert not worker.is_alive()
    assert sum(isinstance(result, control.TaskGraphSnapshot) for result in results) == 1
    assert results.count("task_revision_conflict") == 1


@pytest.mark.parametrize("reject_call", [1, 2])
def test_authority_checked_after_writer_admission_and_before_commit(owner, reject_call):
    snapshot = control.get_task_graph(owner[1])
    calls = 0
    def validate():
        nonlocal calls
        calls += 1
        if calls == reject_call:
            raise PermissionError("revoked")
    with pytest.raises(PermissionError):
        save(snapshot, validate=validate)
    assert control.get_task_graph(owner[1]) == snapshot


def test_receipt_proof_commits_atomically_and_failure_rolls_back(owner):
    snapshot = control.get_task_graph(owner[1])
    tasks = owner[0]
    conn = tasks._get_conn()
    conn.execute("CREATE TABLE synthetic_receipt (task_id TEXT)")
    conn.commit()
    conn.close()
    def proof(conn, task_id):
        conn.execute("INSERT INTO synthetic_receipt VALUES (?)", (task_id,))
        raise RuntimeError("receipt unavailable")
    with pytest.raises(RuntimeError):
        save(snapshot, record_commit=proof)
    assert control.get_task_graph(owner[1]) == snapshot
    conn = tasks._get_conn()
    assert conn.execute("SELECT count(*) FROM synthetic_receipt").fetchone()[0] == 0
    conn.close()


def test_committed_response_failure_never_rewrites_on_replay(owner, monkeypatch):
    snapshot = control.get_task_graph(owner[1])
    original = control.get_task_graph
    monkeypatch.setattr(control, "get_task_graph", lambda *a: (_ for _ in ()).throw(RuntimeError()))
    with pytest.raises(control.TaskGraphError) as caught:
        save(snapshot)
    assert caught.value.committed and caught.value.code == "task_graph_read_unconfirmed"
    monkeypatch.setattr(control, "get_task_graph", original)
    committed = original(owner[1])
    with pytest.raises(control.TaskGraphError, match="task_revision_conflict"):
        save(snapshot)
    assert original(owner[1]) == committed


def test_unknown_step_preserved_but_not_fabricated(owner):
    snapshot = store(owner, [{"id": "future", "type": "future_step", "private": {"preserve": True}},
                             {"id": "p", "type": "prompt", "prompt": "Text"}])
    assert not snapshot.steps[0].editable and snapshot.steps[0].retained_fields
    save(snapshot)
    assert owner[0].get_task(owner[1])["steps"][0]["private"] == {"preserve": True}
    snapshot = control.get_task_graph(owner[1])
    with pytest.raises(control.TaskGraphError):
        save(snapshot, (control.TaskGraphStepEdit("new", "future_step", control.TaskGraphFields()),))


def test_missing_and_circular_subtasks_rejected_in_writer_snapshot(owner):
    tasks, task_id = owner
    snapshot = control.get_task_graph(task_id)
    for target, code in (("absent", "task_graph_missing_subtask"), (task_id, "task_graph_cycle")):
        with pytest.raises(control.TaskGraphError, match=code):
            save(snapshot, (control.TaskGraphStepEdit("s", "subtask", control.TaskGraphFields(task_id=target)),))
    child = tasks.create_task(name="Child", prompts=["Text"], enabled=False)
    conn = tasks._get_conn()
    conn.execute("UPDATE tasks SET steps = ? WHERE id = ?", (json.dumps([
        {"id": "s", "type": "subtask", "task_id": task_id}]), child))
    conn.commit()
    conn.close()
    with pytest.raises(control.TaskGraphError, match="task_graph_cycle"):
        save(snapshot, (control.TaskGraphStepEdit("s", "subtask", control.TaskGraphFields(task_id=child)),))


def test_unicode_wire_budget_fails_before_write_without_truncation(owner):
    snapshot = control.get_task_graph(owner[1])
    steps = tuple(control.TaskGraphStepEdit(f"p{i}", "prompt",
        control.TaskGraphFields(prompt="\U0001f600" * 10000)) for i in range(3))
    with pytest.raises(control.TaskGraphError, match="task_graph_too_large"):
        save(snapshot, steps)
    assert control.get_task_graph(owner[1]) == snapshot


def test_existing_dependency_cycle_not_containing_edited_task_is_rejected(owner):
    tasks, task_id = owner
    child = tasks.create_task(name="Child", prompts=["Text"], enabled=False)
    conn = tasks._get_conn()
    conn.execute("UPDATE tasks SET steps = ? WHERE id = ?", (json.dumps([
        {"id": "s", "type": "subtask", "task_id": child}]), child))
    conn.commit()
    conn.close()
    snapshot = control.get_task_graph(task_id)
    with pytest.raises(control.TaskGraphError, match="task_graph_cycle"):
        save(snapshot, (control.TaskGraphStepEdit("s", "subtask", control.TaskGraphFields(task_id=child)),))
    assert control.get_task_graph(task_id) == snapshot


def test_legacy_wait_ids_keep_original_serialized_form_when_unchanged(owner):
    snapshot = store(owner, [{"id": "w", "type": "wait_for_agents", "run_ids": " run-a, run-b "}])
    assert snapshot.steps[0].fields.run_ids == ("run-a", "run-b")
    save(snapshot)
    assert owner[0].get_task(owner[1])["steps"][0]["run_ids"] == " run-a, run-b "


def test_metadata_envelope_rejects_growth_without_dropping_hidden_fields(owner):
    snapshot = store(owner, [{"id": "p0", "type": "prompt", "prompt": "Text", "hidden": "h" * 1950000}])
    steps = tuple(control.TaskGraphStepEdit(f"p{i}", "prompt",
        control.TaskGraphFields(prompt="x" * 16000)) for i in range(6))
    with pytest.raises(control.TaskGraphError, match="task_metadata_too_large"):
        save(snapshot, steps)
    assert control.get_task_graph(owner[1]) == snapshot
    assert len(owner[0].get_task(owner[1])["steps"][0]["hidden"]) == 1950000


def test_canonical_builtin_delegate_profile_is_preserved_on_save(owner):
    snapshot = store(owner, [{"id": "d", "type": "delegate_agent", "objective": "Inspect",
                             "profile": "builtin:worker"}])
    saved = save(snapshot)
    assert saved.steps[0].fields.profile == "builtin:worker"
    assert owner[0].get_task(owner[1])["steps"][0]["profile"] == "builtin:worker"
