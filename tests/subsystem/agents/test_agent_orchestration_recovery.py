from __future__ import annotations

import importlib
import sys

import pytest


pytestmark = pytest.mark.subsystem


def _fresh_modules(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(data_dir))
    for name in (
        "row_bot.tasks",
        "row_bot.agent_profiles",
        "row_bot.agent_settings",
        "row_bot.agent_runs",
        "row_bot.agent_orchestrator",
    ):
        sys.modules.pop(name, None)
    import row_bot.tasks as tasks
    import row_bot.agent_runs as agent_runs
    import row_bot.agent_orchestrator as orchestrator

    importlib.reload(tasks)
    agent_runs = importlib.reload(agent_runs)
    orchestrator = importlib.reload(orchestrator)
    return agent_runs, orchestrator


def _setup(agent_runs, orchestrator):
    orchestration = orchestrator.create_or_get_orchestration(
        parent_thread_id="parent",
        parent_generation_id="generation",
        root_objective="Finish after restart",
        model_ref="provider:model",
        approval_mode="block",
        runtime_surface="normal_chat",
    )
    completed = agent_runs.create_agent_run(
        run_id="completed",
        status="completed",
        parent_thread_id="parent",
        prompt="Already done",
        summary="Retained result",
        model_override="provider:model",
    )
    running = agent_runs.create_agent_run(
        run_id="running",
        status="running",
        parent_thread_id="parent",
        prompt="Still running",
        model_override="provider:model",
    )
    orchestrator.register_member(orchestration["id"], completed["id"], required=True)
    orchestrator.register_member(orchestration["id"], running["id"], required=True)
    orchestrator.finalize_parent_generation(
        orchestration["id"],
        continuation_state={"config": {"configurable": {}}, "enabled_tool_names": []},
    )
    return orchestration, completed, running


def test_deferred_repair_marks_active_work_interrupted_without_executor_calls(
    tmp_path,
    monkeypatch,
):
    agent_runs, orchestrator = _fresh_modules(tmp_path, monkeypatch)
    orchestration, completed, running = _setup(agent_runs, orchestrator)
    superseded = agent_runs.create_agent_run(
        run_id="superseded",
        status="failed",
        parent_thread_id="parent",
        prompt="Failed earlier attempt",
        model_override="provider:model",
    )
    orchestrator.register_member(
        orchestration["id"],
        superseded["id"],
        required=False,
    )
    conn = agent_runs._get_conn()
    try:
        conn.execute(
            "UPDATE agent_orchestration_members SET status = 'retried' "
            "WHERE orchestration_id = ? AND run_id = ?",
            (orchestration["id"], superseded["id"]),
        )
        conn.commit()
    finally:
        conn.close()
    calls: list[str] = []
    orchestrator.set_test_executors(
        synthesis=lambda *_args: calls.append("synthesis") or "unexpected",
        retry=lambda *_args: calls.append("retry") or {},
        delivery=lambda *_args: True,
    )

    startup_result = agent_runs.recover_stale_agent_runs()

    assert startup_result["orchestrations_interrupted"] == 0
    assert orchestrator.get_orchestration(orchestration["id"])["status"] == "waiting_children"
    assert agent_runs.get_agent_run(running["id"])["status"] == "running"

    result = orchestrator.repair_interrupted_orchestrations_batch(limit=10)

    assert result["processed"] == 1
    assert orchestrator.get_orchestration(orchestration["id"])["status"] == "interrupted"
    assert agent_runs.get_agent_run(completed["id"])["status"] == "completed"
    assert agent_runs.get_agent_run(running["id"])["status"] == "interrupted"
    assert orchestrator.get_member_for_run(superseded["id"])["status"] == "retried"
    assert agent_runs.get_agent_run(superseded["id"])["status"] == "failed"
    assert calls == []


def test_deferred_repair_restores_recorded_terminal_event_before_interrupting(
    tmp_path,
    monkeypatch,
):
    agent_runs, orchestrator = _fresh_modules(tmp_path, monkeypatch)
    orchestration, _completed, running = _setup(agent_runs, orchestrator)
    agent_runs.append_agent_event(
        running["id"],
        "run.stopped",
        {"reason": "Already stopped before the restart repair"},
    )
    conn = agent_runs._get_conn()
    try:
        conn.execute(
            "UPDATE agent_runs SET status = 'interrupted', stop_requested = 1 WHERE id = ?",
            (running["id"],),
        )
        conn.execute(
            "UPDATE agent_orchestration_members SET status = 'interrupted' "
            "WHERE orchestration_id = ? AND run_id = ?",
            (orchestration["id"], running["id"]),
        )
        conn.execute(
            "UPDATE agent_orchestrations SET status = 'interrupted', "
            "parent_state = 'interrupted', lease_owner = 'dead-owner', "
            "wake_requested_at = 'stale-wake' WHERE id = ?",
            (orchestration["id"],),
        )
        conn.commit()
    finally:
        conn.close()
    calls: list[str] = []
    orchestrator.set_test_executors(
        parent=lambda *_args: calls.append("parent") or "unexpected",
        retry=lambda *_args: calls.append("retry") or {},
        delivery=lambda *_args: calls.append("delivery") or True,
    )

    result = orchestrator.repair_interrupted_orchestrations_batch(limit=10)

    assert result["processed"] == 1
    assert agent_runs.get_agent_run(running["id"])["status"] == "stopped"
    assert orchestrator.get_member_for_run(running["id"])["status"] == "stopped"
    repaired = orchestrator.get_orchestration(orchestration["id"])
    assert repaired["status"] == "interrupted"
    assert repaired["lease_owner"] == ""
    assert repaired["wake_requested_at"] == ""
    assert calls == []


def test_explicit_resume_requeues_only_interrupted_required_work(
    tmp_path,
    monkeypatch,
):
    agent_runs, orchestrator = _fresh_modules(tmp_path, monkeypatch)
    orchestration, completed, running = _setup(agent_runs, orchestrator)
    orchestrator.recover_interrupted_orchestrations()
    replacements: list[str] = []

    monkeypatch.setattr("row_bot.tools.registry.is_enabled", lambda name: name == "agents")
    monkeypatch.setattr(
        "row_bot.providers.readiness.ensure_agent_ready",
        lambda _model: object(),
    )

    def resume_executor(row, member, explicit_resume):
        assert explicit_resume is True
        replacement = agent_runs.create_agent_run(
            run_id="resumed",
            status="queued",
            parent_thread_id="parent",
            prompt="Still running",
            model_override=row["model_ref"],
        )
        orchestrator.register_member(
            row["id"],
            replacement["id"],
            required=True,
            attempt=member["attempt"],
            retry_of_run_id=member["run_id"],
        )
        replacements.append(replacement["id"])
        return replacement

    orchestrator.set_test_executors(
        retry=resume_executor,
        synthesis=lambda *_args: "Final after resume",
        delivery=lambda *_args: True,
    )
    resumed = orchestrator.resume_orchestration(orchestration["id"])

    assert resumed["status"] == "waiting_children"
    assert replacements == ["resumed"]
    assert agent_runs.get_agent_run(completed["id"])["status"] == "completed"
    agent_runs.finish_agent_run("resumed", "completed", summary="Finished after restart")
    assert orchestrator.wait_for_synthesis(orchestration["id"])["status"] == "completed"


def test_resume_revalidates_agents_model_and_workspace_without_calling_executor(
    tmp_path,
    monkeypatch,
):
    agent_runs, orchestrator = _fresh_modules(tmp_path, monkeypatch)
    orchestration, _completed, running = _setup(agent_runs, orchestrator)
    orchestrator.recover_interrupted_orchestrations()
    calls: list[str] = []
    orchestrator.set_test_executors(
        retry=lambda *_args: calls.append("retry") or {},
        delivery=lambda *_args: True,
    )

    monkeypatch.setattr("row_bot.tools.registry.is_enabled", lambda _name: False)
    with pytest.raises(orchestrator.OrchestrationError, match="Agents are disabled"):
        orchestrator.resume_orchestration(orchestration["id"])
    assert calls == []

    monkeypatch.setattr("row_bot.tools.registry.is_enabled", lambda _name: True)
    monkeypatch.setattr(
        "row_bot.providers.readiness.ensure_agent_ready",
        lambda _model: (_ for _ in ()).throw(RuntimeError("Configured model unavailable")),
    )
    with pytest.raises(orchestrator.OrchestrationError, match="model unavailable"):
        orchestrator.resume_orchestration(orchestration["id"])
    assert calls == []

    monkeypatch.setattr(
        "row_bot.providers.readiness.ensure_agent_ready",
        lambda _model: object(),
    )
    missing = tmp_path / "removed-worktree"
    conn = agent_runs._get_conn()
    try:
        conn.execute(
            "UPDATE agent_runs SET workspace_path = ? WHERE id = ?",
            (str(missing), running["id"]),
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(orchestrator.OrchestrationError, match="no longer exists"):
        orchestrator.resume_orchestration(orchestration["id"])
    assert calls == []


def test_v2_recovery_keeps_inbox_and_resumes_original_parent_only_on_request(
    tmp_path,
    monkeypatch,
):
    agent_runs, orchestrator = _fresh_modules(tmp_path, monkeypatch)
    schedule_parent = orchestrator._schedule_parent_runner
    monkeypatch.setattr(orchestrator, "_schedule_parent_runner", lambda _id: None)
    orchestration = orchestrator.create_or_get_orchestration(
        parent_thread_id="parent",
        parent_generation_id="v2-generation",
        root_objective="Finish from the retained child event.",
        model_ref="provider:model",
        approval_mode="block",
        runtime_surface="normal_chat",
        orchestration_version=2,
    )
    child = agent_runs.create_agent_run(
        run_id="already-completed",
        status="completed",
        parent_thread_id="parent",
        prompt="Complete before restart",
        summary="Retained v2 result",
        model_override="provider:model",
    )
    orchestrator.register_member(
        orchestration["id"],
        child["id"],
        required=True,
    )
    orchestrator.arm_parent_wait(
        orchestration["id"],
        continuation_state={
            "config": {"configurable": {"thread_id": "parent"}},
            "enabled_tool_names": ["agents"],
        },
    )
    orchestrator.record_thread_event(
        orchestration["id"],
        kind="child_terminal",
        content="Retained v2 result",
        run_id=child["id"],
        source_event_id="run:already-completed:terminal:completed",
        payload={"status": "completed", "summary": "Retained v2 result"},
        request_wake=False,
    )
    calls: list[str] = []
    orchestrator.set_test_executors(
        parent=lambda *_args: calls.append("parent") or "Final after explicit resume",
        synthesis=lambda *_args: calls.append("synthesis") or "unexpected",
        delivery=lambda *_args: True,
    )

    recovered = agent_runs.recover_stale_agent_runs()
    repaired = orchestrator.repair_interrupted_orchestrations_batch(limit=10)

    assert recovered["orchestrations_interrupted"] == 0
    assert repaired["processed"] == 1
    assert calls == []
    interrupted = orchestrator.get_orchestration(orchestration["id"])
    assert interrupted["parent_state"] == "interrupted"
    assert len(orchestrator.pending_thread_events(orchestration["id"])) == 1

    monkeypatch.setattr("row_bot.tools.registry.is_enabled", lambda name: name == "agents")
    monkeypatch.setattr(
        "row_bot.providers.readiness.ensure_agent_ready",
        lambda _model: object(),
    )
    monkeypatch.setattr(
        "row_bot.agent.repair_orphaned_tool_calls",
        lambda *_args, **_kwargs: 0,
    )
    monkeypatch.setattr(orchestrator, "_schedule_parent_runner", schedule_parent)
    orchestrator.resume_orchestration(orchestration["id"])
    final = orchestrator.wait_for_parent(orchestration["id"], timeout=2)

    assert final["status"] == "completed"
    assert calls == ["parent"]
    assert orchestrator.pending_thread_events(orchestration["id"]) == []


def test_v2_foreground_recovery_resumes_terminal_children_with_empty_continuation(
    tmp_path,
    monkeypatch,
):
    agent_runs, orchestrator = _fresh_modules(tmp_path, monkeypatch)
    orchestration = orchestrator.create_or_get_orchestration(
        parent_thread_id="foreground-parent",
        parent_generation_id="foreground-generation",
        root_objective="Finish from three retained child results.",
        model_ref="provider:model",
        approval_mode="block",
        runtime_surface="normal_chat",
        orchestration_version=2,
    )
    child_ids = ["first-completed", "second-completed", "third-completed"]
    for child_id in child_ids:
        child = agent_runs.create_agent_run(
            run_id=child_id,
            status="completed",
            parent_thread_id="foreground-parent",
            prompt=f"Complete {child_id}",
            summary=f"Retained result from {child_id}",
            model_override="provider:model",
        )
        orchestrator.register_member(
            orchestration["id"],
            child["id"],
            required=True,
        )
        orchestrator.record_thread_event(
            orchestration["id"],
            kind="child_terminal",
            content=child["summary"],
            run_id=child["id"],
            source_event_id=f"run:{child['id']}:terminal:completed",
            payload={"status": "completed", "summary": child["summary"]},
            request_wake=False,
        )

    assert orchestrator.get_orchestration(orchestration["id"])[
        "continuation_state_json"
    ] == {}
    repaired = orchestrator.repair_interrupted_orchestrations_batch(limit=10)
    assert repaired["processed"] == 1

    calls: list[str] = []

    def fake_checkpoint_repair(enabled_tools, config, **kwargs):
        calls.append("repair")
        assert enabled_tools == []
        configurable = config["configurable"]
        assert configurable["thread_id"] == "foreground-parent"
        assert configurable["generation_id"] == "foreground-generation"
        assert "app restart" in kwargs["orphan_message"]
        return 3

    import row_bot.agent as agent

    monkeypatch.setattr(agent, "repair_orphaned_tool_calls", fake_checkpoint_repair)
    monkeypatch.setattr("row_bot.tools.registry.is_enabled", lambda name: name == "agents")
    monkeypatch.setattr(
        "row_bot.providers.readiness.ensure_agent_ready",
        lambda _model: object(),
    )
    orchestrator.set_test_executors(
        parent=lambda *_args: calls.append("parent") or "Final retained result",
        retry=lambda *_args: calls.append("retry") or {},
        delivery=lambda *_args: True,
    )

    resumed = orchestrator.resume_orchestration(orchestration["id"])
    final = orchestrator.wait_for_parent(orchestration["id"], timeout=2)

    assert resumed["status"] in {"waiting_children", "completed"}
    assert final["status"] == "completed"
    assert calls == ["repair", "parent"]
    assert orchestrator.pending_thread_events(orchestration["id"]) == []
    assert [agent_runs.get_agent_run(run_id)["status"] for run_id in child_ids] == [
        "completed",
        "completed",
        "completed",
    ]


def _modules(tmp_path, reload_for_data_dir):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    _tasks, agent_runs, orchestrator = reload_for_data_dir(
        data_dir, "row_bot.tasks", "row_bot.agent_runs", "row_bot.agent_orchestrator"
    )
    return agent_runs, orchestrator


def _turn(conversation: str, generation: str) -> None:
    """One turn of the conversation as the client platform records it."""
    import uuid

    from row_bot.runtime import admissions

    admitted = admissions.reserve(conversation, str(uuid.uuid4()), generation)
    admissions.admit(admitted["pass_id"], "")
    admissions.start(admitted["pass_id"], "execution", "epoch")
    admissions.finish(admitted["pass_id"], "stopped")


def _finished_v2_work(agent_runs, orchestrator, *, thread: str, generation: str, child: str):
    """A v2 orchestration whose only agent was stopped while queued; its
    terminal event still waits for a parent pass that never came."""
    orchestration = orchestrator.create_or_get_orchestration(
        parent_thread_id=thread,
        parent_generation_id=generation,
        root_objective="Walk through the developer surface.",
        model_ref="provider:model",
        approval_mode="block",
        runtime_surface="normal_chat",
        orchestration_version=2,
    )
    run = agent_runs.create_agent_run(
        run_id=child,
        status="stopped",
        parent_thread_id=thread,
        prompt="Queued work the person stopped",
        model_override="provider:model",
    )
    orchestrator.register_member(orchestration["id"], run["id"], required=True)
    orchestrator.record_thread_event(
        orchestration["id"],
        kind="child_terminal",
        content="Stopped before it started.",
        run_id=run["id"],
        source_event_id=f"run:{run['id']}:terminal:stopped",
        payload={"status": "stopped"},
        request_wake=False,
    )
    return orchestration


def test_restart_settles_finished_agent_work_the_conversation_has_moved_past(
    tmp_path,
    reload_for_data_dir,
):
    """B220: a stale "Agent work needs your attention" nothing could clear."""
    agent_runs, orchestrator = _modules(tmp_path, reload_for_data_dir)
    _turn("parent", "generation-1")
    orchestration = _finished_v2_work(
        agent_runs, orchestrator, thread="parent", generation="generation-1", child="stopped-child"
    )
    _turn("parent", "generation-2")
    calls: list[str] = []
    orchestrator.set_test_executors(
        parent=lambda *_args: calls.append("parent") or "unexpected",
        retry=lambda *_args: calls.append("retry") or {},
        delivery=lambda *_args: calls.append("delivery") or True,
    )
    try:
        orchestrator.repair_interrupted_orchestrations_batch(limit=10)
        settled = orchestrator.get_orchestration(orchestration["id"])
        assert settled["status"] == "stopped"
        assert orchestrator.get_thread_orchestration_activity(["parent"])["parent"]["state"] == "terminal"

        # The next start leaves it alone instead of asking for Resume again.
        orchestrator.repair_interrupted_orchestrations_batch(limit=10)
        assert orchestrator.get_orchestration(orchestration["id"]) == settled
        assert calls == []
    finally:
        orchestrator.set_test_executors()


def test_restart_keeps_asking_while_finished_agent_work_still_has_an_answer_to_give(
    tmp_path,
    reload_for_data_dir,
):
    agent_runs, orchestrator = _modules(tmp_path, reload_for_data_dir)
    _turn("parent", "generation-1")
    orchestration = _finished_v2_work(
        agent_runs, orchestrator, thread="parent", generation="generation-1", child="stopped-child"
    )

    orchestrator.repair_interrupted_orchestrations_batch(limit=10)

    assert orchestrator.get_orchestration(orchestration["id"])["status"] == "interrupted"
    activity = orchestrator.get_thread_orchestration_activity(["parent"])["parent"]
    assert (activity["state"], activity["phase"]) == ("attention", "resume_required")


def test_restart_settles_finished_agent_work_with_nothing_left_to_resume(
    tmp_path,
    reload_for_data_dir,
):
    agent_runs, orchestrator = _modules(tmp_path, reload_for_data_dir)
    # The parent turn was cut off before it handed its agents over, so Resume
    # would have nothing to run.
    orchestration = orchestrator.create_or_get_orchestration(
        parent_thread_id="parent",
        parent_generation_id="generation-1",
        root_objective="Nothing waits for the parent any more.",
        model_ref="provider:model",
        approval_mode="block",
        runtime_surface="normal_chat",
    )
    run = agent_runs.create_agent_run(
        run_id="done-child",
        status="completed",
        parent_thread_id="parent",
        prompt="Done before the restart",
        model_override="provider:model",
    )
    orchestrator.register_member(orchestration["id"], run["id"], required=True)

    orchestrator.repair_interrupted_orchestrations_batch(limit=10)

    assert orchestrator.get_orchestration(orchestration["id"])["status"] == "stopped"


def test_restart_never_settles_agent_work_that_waits_for_an_approval(
    tmp_path,
    reload_for_data_dir,
):
    agent_runs, orchestrator = _modules(tmp_path, reload_for_data_dir)
    import row_bot.tasks as tasks

    _turn("parent", "generation-1")
    orchestration = _finished_v2_work(
        agent_runs, orchestrator, thread="parent", generation="generation-1", child="stopped-child"
    )
    _turn("parent", "generation-2")
    tasks.create_approval_request(
        run_id="parent", task_id="", step_id=f"orchestration:{orchestration['id']}",
        message="Run the protected step?", resume_kind="parent_orchestration",
        parent_thread_id="parent",
    )
    before = orchestrator.get_orchestration(orchestration["id"])["status"]

    orchestrator.repair_interrupted_orchestrations_batch(limit=10)

    assert orchestrator.get_orchestration(orchestration["id"])["status"] == before == "running"


def test_interrupted_work_with_nothing_left_offers_only_dismiss(
    tmp_path,
    reload_for_data_dir,
):
    agent_runs, orchestrator = _modules(tmp_path, reload_for_data_dir)
    finished = orchestrator.create_or_get_orchestration(
        parent_thread_id="parent",
        parent_generation_id="generation-1",
        root_objective="Its agent finished; its parent step failed.",
        model_ref="provider:model",
        approval_mode="block",
        runtime_surface="normal_chat",
    )
    done = agent_runs.create_agent_run(
        run_id="done-child", status="completed", parent_thread_id="parent",
        prompt="Done", model_override="provider:model",
    )
    orchestrator.register_member(finished["id"], done["id"], required=True)
    orchestrator.transition_orchestration(finished["id"], "interrupted")
    running = orchestrator.create_or_get_orchestration(
        parent_thread_id="other",
        parent_generation_id="generation-1",
        root_objective="Still working.",
        model_ref="provider:model",
        approval_mode="block",
        runtime_surface="normal_chat",
    )
    working = agent_runs.create_agent_run(
        run_id="working-child", status="running", parent_thread_id="other",
        prompt="Working", model_override="provider:model",
    )
    orchestrator.register_member(running["id"], working["id"], required=True)

    activity = orchestrator.get_thread_orchestration_activity(["parent"])["parent"]
    assert (activity["state"], activity["phase"]) == ("attention", "interrupted")

    assert orchestrator.dismiss_orchestrations("other") == 0
    assert orchestrator.get_orchestration(running["id"])["status"] == "running"
    assert orchestrator.dismiss_orchestrations("parent") == 1
    assert orchestrator.get_orchestration(finished["id"])["status"] == "stopped"
    assert orchestrator.get_thread_orchestration_activity(["parent"])["parent"]["state"] == "terminal"
