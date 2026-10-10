"""Public child navigation retains owner relationships without private context."""
from __future__ import annotations

import json

import pytest

from tests.subsystem.client_protocol.test_protocol_application import service, _client  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_security import bootstrap

pytestmark = pytest.mark.subsystem


@pytest.mark.slow
def test_public_child_detail_parent_return_and_foreign_run_denial(service):
    from row_bot import threads, agent_runs
    from row_bot.application.delegated_activity import read_activity, read_run
    from row_bot.application.client_platform import ClientPlatformError
    parent, child, foreign = [threads.create_thread(name, seed_default_skills=False) for name in ("Parent", "Child", "Foreign")]
    run = agent_runs.create_agent_run(parent_thread_id=parent, thread_id=child, display_name="Research", status="completed",
                                      summary="Public result", prompt="PRIVATE-PROMPT", context_summary="PRIVATE-CONTEXT",
                                      error="PRIVATE-ERROR", workspace_path="PRIVATE-PATH", result_json={"private": "PRIVATE-RESULT"})
    detail = read_run(service, parent, run["id"])
    assert detail["child_conversation_id"] == child and detail["summary"] == "Public result"
    assert "PRIVATE" not in json.dumps(detail)
    assert read_activity(service, child)["parent_conversation_id"] == parent
    with pytest.raises(ClientPlatformError, match="not_found"):
        read_run(service, foreign, run["id"])
    client = _client(service)
    _, headers = bootstrap(client)
    response = client.get(f"/api/v1/conversations/{parent}/delegated/{run['id']}", headers=headers)
    assert response.status_code == 200 and "PRIVATE" not in response.text
    assert client.get(f"/api/v1/conversations/{foreign}/delegated/{run['id']}", headers=headers).status_code == 404


def test_deleting_parent_denied_and_deleted_child_history_unavailable(service, monkeypatch):
    from row_bot import threads, agent_runs
    from row_bot.application.delegated_activity import read_activity, read_run
    from row_bot.application.client_platform import ClientPlatformError
    from row_bot.runtime import admissions
    parent, child = [threads.create_thread(name, seed_default_skills=False) for name in ("Parent", "Child")]
    run = agent_runs.create_agent_run(parent_thread_id=parent, thread_id=child)
    admissions.close_admission(child)
    assert read_run(service, parent, run["id"])["child_conversation_id"] is None
    original = agent_runs.get_agent_run
    def deleting_read(run_id):
        result = original(run_id)
        admissions.close_admission(parent)
        return result
    monkeypatch.setattr(agent_runs, "get_agent_run", deleting_read)
    with pytest.raises(ClientPlatformError, match="not_found"):
        read_run(service, parent, run["id"])
    with pytest.raises(ClientPlatformError, match="not_found"):
        read_activity(service, parent)


def test_agent_rows_carry_their_profile_and_a_child_names_its_parent(service):
    """B240/B242: one icon per agent profile; the child's header links back by title."""
    from row_bot import threads, agent_runs
    from row_bot.application.delegated_activity import read_activity
    parent, child = [threads.create_thread(name, seed_default_skills=False) for name in ("Q4 launch plan", "Child")]
    profiled = agent_runs.create_agent_run(parent_thread_id=parent, thread_id=child, display_name="Pricing scan",
                                           status="running", profile_snapshot_json={"id": "profile-7"})
    plain = agent_runs.create_agent_run(parent_thread_id=parent, display_name="Email draft", status="queued")
    items = {item["run_id"]: item for item in read_activity(service, parent)["items"]}
    assert items[profiled["id"]]["profile_id"] == "profile-7"
    assert items[plain["id"]]["profile_id"] == ""
    own = read_activity(service, child)
    assert own["own_run"]["profile_id"] == "profile-7"
    assert (own["parent_conversation_id"], own["parent_title"]) == (parent, "Q4 launch plan")
    assert read_activity(service, parent)["parent_title"] is None


def test_child_pages_complete_and_cursor_cannot_cross_parent(service):
    from row_bot import threads, agent_runs
    from row_bot.application.delegated_activity import read_activity
    from row_bot.application.client_platform import ClientPlatformError
    parent, foreign = [threads.create_thread(name, seed_default_skills=False) for name in ("Parent", "Foreign")]
    ids = {agent_runs.create_agent_run(parent_thread_id=parent, display_name=f"Child {index}")["id"] for index in range(53)}
    first = read_activity(service, parent)
    assert len(first["items"]) == 50 and first["has_more"]
    second = read_activity(service, parent, cursor=first["next_cursor"])
    assert len(second["items"]) == 3 and not second["has_more"]
    assert {item["run_id"] for item in first["items"] + second["items"]} == ids
    with pytest.raises(ClientPlatformError, match="cursor_expired"):
        read_activity(service, foreign, cursor=first["next_cursor"])


def _agent_command(service, target, kind, payload):
    from uuid import uuid4
    identity = str(uuid4())
    return service.execute(owner_id="fixture", idempotency_key=identity, command={
        "type": kind, "command_id": identity,
        "expected_revision": str(service._metadata(target)["client_revision"]),
        "payload": payload,
    }, target=target)


def test_stop_and_message_a_running_agent_from_the_parent_and_its_own_thread(service):
    """Parity row 9: Stop and Message from Agents and from the child thread."""
    from uuid import uuid4
    from row_bot import threads, agent_runs
    from row_bot.application.client_platform import ClientPlatformError
    from row_bot.application.delegated_activity import read_activity
    parent, child, foreign = [threads.create_thread(name, seed_default_skills=False)
                              for name in ("Parent", "Child", "Foreign")]
    run = agent_runs.create_agent_run(parent_thread_id=parent, thread_id=child, display_name="Research",
                                      status="running", prompt="Synthetic task")
    own = read_activity(service, child)["own_run"]
    assert own["run_id"] == run["id"] and own["status"] == "running"
    assert read_activity(service, parent)["own_run"] is None
    receipt = _agent_command(service, parent, "agent.message",
                             {"run_id": run["id"], "message_id": str(uuid4()), "text": "Also cover tides."})
    assert receipt["status"] == "completed"
    _agent_command(service, child, "agent.message",
                   {"run_id": run["id"], "message_id": str(uuid4()), "text": "And keep it short."})
    pending = [record["content"] for record in agent_runs.pending_parent_message_records(run["id"])]
    assert pending == ["Also cover tides.", "And keep it short."]
    with pytest.raises(ClientPlatformError, match="not_found"):
        _agent_command(service, foreign, "agent.stop", {"run_id": run["id"]})
    _agent_command(service, child, "agent.stop", {"run_id": run["id"]})
    stopped = agent_runs.get_agent_run(run["id"])
    # Nothing runs it in this test, so the stop settles at once.
    assert stopped["stop_requested"] and stopped["status"] == "stopped"
    with pytest.raises(ClientPlatformError, match="agent_run_finished"):
        _agent_command(service, parent, "agent.message",
                       {"run_id": run["id"], "message_id": str(uuid4()), "text": "Too late."})


def test_stopping_a_finished_agent_is_a_quiet_no_op(service):
    from row_bot import threads, agent_runs
    parent, child = [threads.create_thread(name, seed_default_skills=False) for name in ("Parent", "Child")]
    run = agent_runs.create_agent_run(parent_thread_id=parent, thread_id=child, status="completed",
                                      summary="Done")
    assert _agent_command(service, parent, "agent.stop", {"run_id": run["id"]})["status"] == "completed"
    assert agent_runs.get_agent_run(run["id"])["status"] == "completed"


def test_agent_start_spawns_a_delegated_agent_from_the_composer(service, monkeypatch):
    """/agent [profile] <task> starts a child run of this conversation (B112)."""
    from row_bot import agent_commands, threads
    from row_bot.application.client_platform import ClientPlatformError
    parent = threads.create_thread("Parent", seed_default_skills=False)
    spawned = []

    def spawn(thread_id, request, **kwargs):
        spawned.append((thread_id, request.objective, request.profile))
        return {"id": "run-started", "parent_thread_id": thread_id, "thread_id": "",
                "display_name": "Agent: Summarise tide tables", "status": "queued", "summary": ""}

    monkeypatch.setattr(agent_commands, "spawn_agent_from_request", spawn)
    receipt = _agent_command(service, parent, "agent.start", {"text": "Summarise tide tables"})
    assert receipt["status"] == "completed"
    assert spawned and spawned[0][:2] == (parent, "Summarise tide tables")
    with pytest.raises(ClientPlatformError, match="invalid_command"):
        _agent_command(service, parent, "agent.start", {"text": "--model="})


@pytest.fixture
def interrupted_work(service):
    """A conversation whose agent a restart cut off mid-run (B220)."""
    from row_bot import agent_orchestrator, agent_runs, threads
    parent = threads.create_thread("Parent", seed_default_skills=False)
    orchestration = agent_orchestrator.create_or_get_orchestration(
        parent_thread_id=parent, parent_generation_id="generation-1", root_objective="Survey the tide tables",
        model_ref="provider:model", approval_mode="block", runtime_surface="normal_chat")
    run = agent_runs.create_agent_run(parent_thread_id=parent, display_name="Research", status="running",
                                      prompt="Synthetic task", model_override="provider:model")
    agent_orchestrator.register_member(orchestration["id"], run["id"], required=True)
    agent_orchestrator.finalize_parent_generation(
        orchestration["id"], continuation_state={"config": {"configurable": {}}, "enabled_tool_names": []})
    calls: list[str] = []
    agent_orchestrator.set_test_executors(
        synthesis=lambda *_args: calls.append("synthesis") or "Final answer",
        retry=lambda *_args: calls.append("retry") or {},
        delivery=lambda *_args: True,
    )
    agent_orchestrator.repair_interrupted_orchestrations_batch(limit=10)
    assert (service.get_conversation(parent)["activity_state"],
            service.get_conversation(parent)["activity_phase"]) == ("attention", "resume_required")
    yield parent, orchestration["id"], run["id"], calls
    agent_orchestrator.set_test_executors()


def test_dismiss_clears_interrupted_agent_work_without_running_it(service, interrupted_work):
    from row_bot import agent_orchestrator, agent_runs
    parent, orchestration_id, run_id, calls = interrupted_work
    client = _client(service)
    _, headers = bootstrap(client)
    from tests.subsystem.client_protocol.test_protocol_application import _command
    response = _command(client, headers, "agent.dismiss", {}, target=parent,
                        revision=str(service._metadata(parent)["client_revision"]))
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "completed"
    assert agent_orchestrator.get_orchestration(orchestration_id)["status"] == "stopped"
    assert agent_runs.get_agent_run(run_id)["status"] == "stopped"
    assert service.get_conversation(parent)["activity_state"] != "attention"
    assert calls == []


def test_resume_reruns_interrupted_agent_work_only_while_something_is_left(service, interrupted_work, monkeypatch):
    from row_bot import agent_orchestrator
    from row_bot.application.client_platform import ClientPlatformError
    parent, orchestration_id, run_id, calls = interrupted_work
    monkeypatch.setattr("row_bot.tools.registry.is_enabled", lambda _name: False)
    with pytest.raises(ClientPlatformError, match="agent_resume_unavailable"):
        _agent_command(service, parent, "agent.resume", {})
    assert calls == [] and service.get_conversation(parent)["activity_phase"] == "resume_required"

    monkeypatch.setattr("row_bot.tools.registry.is_enabled", lambda name: name == "agents")
    monkeypatch.setattr("row_bot.providers.readiness.ensure_agent_ready", lambda _model: object())
    assert _agent_command(service, parent, "agent.resume", {})["status"] == "completed"
    assert calls == ["retry"]
    assert agent_orchestrator.get_orchestration(orchestration_id)["status"] != "interrupted"
    with pytest.raises(ClientPlatformError, match="agent_work_not_resumable"):
        _agent_command(service, parent, "agent.resume", {})
    assert calls == ["retry"]


def _statuses(tasks, ids: list[str]) -> dict[str, str]:
    """The saved status of each approval request, read from its row."""
    conn = tasks._get_conn()
    try:
        rows = conn.execute(
            "SELECT id, status FROM approval_requests WHERE id IN (%s)" % ",".join("?" * len(ids)),
            list(ids),
        ).fetchall()
    finally:
        conn.close()
    return {row["id"]: row["status"] for row in rows}


def test_stopping_an_agent_that_waits_for_approval_withdraws_the_approval(service):
    """B165: Stop left the agent's approval pending, so "Needs approval" stayed
    on the Buddy and in Home until it timed out."""
    from row_bot import threads, agent_runs, tasks
    parent, child = [threads.create_thread(name, seed_default_skills=False) for name in ("Parent", "Child")]
    run = agent_runs.create_agent_run(parent_thread_id=parent, thread_id=child, display_name="Research",
                                      status="waiting_approval", prompt="Synthetic task")
    other = agent_runs.create_agent_run(parent_thread_id=parent, display_name="Other",
                                        status="waiting_approval", prompt="Another task")
    _token, approval_id = tasks.create_approval_request(
        run_id=run["id"], task_id="", step_id="agent_interrupt", message="Run a synthetic command?",
        agent_run_id=run["id"], resume_kind="agent_run", parent_thread_id=parent)
    _other_token, other_approval = tasks.create_approval_request(
        run_id=other["id"], task_id="", step_id="agent_interrupt", message="Another question?",
        agent_run_id=other["id"], resume_kind="agent_run", parent_thread_id=parent)
    _agent_command(service, parent, "agent.stop", {"run_id": run["id"]})
    assert agent_runs.get_agent_run(run["id"])["status"] == "stopped"
    statuses = _statuses(tasks, [approval_id, other_approval])
    assert statuses == {approval_id: "cancelled", other_approval: "pending"}
    assert [item["id"] for item in tasks.get_pending_approvals()] == [other_approval]


def _waiting_child(monkeypatch, parent):
    """A child agent that paused on an approval, with the model faked."""
    from row_bot import agent_runner
    resumed = []

    def interrupt(prompt, enabled_tool_names, config, *, stop_event):
        return {"type": "interrupt", "interrupts": [{
            "id": "interrupt-1", "tool": "workspace_file_delete", "label": "Delete a file",
            "approval_reason": "Remove a stale note.", "args": {"path": "missing-note.txt"}}]}

    def resume(enabled_tool_names, config, approved, *, interrupt_ids=None, stop_event):
        resumed.append(approved)
        return "Child finished."

    monkeypatch.setattr(agent_runner, "_invoke_agent", interrupt)
    monkeypatch.setattr(agent_runner, "_resume_invoke_agent", resume)
    run = agent_runner.spawn_agent_run("Tidy the notes folder.", parent_thread_id=parent,
                                       profile="worker", enabled_tool_names=["filesystem"], wait=True)
    assert run["status"] == "waiting_approval"
    return run, resumed


def test_a_delegated_agents_approval_is_answered_in_its_thread_and_in_the_parent(service, monkeypatch):
    """B162: the child's thread showed "Running a command" with no card, the
    parent a plain message without Approve/Deny, and Home did not list it."""
    from row_bot import agent_runner, tasks, threads
    parent = threads.create_thread("Parent", seed_default_skills=False)
    run, resumed = _waiting_child(monkeypatch, parent)
    child = run["thread_id"]
    approval = next(row for row in tasks.get_pending_approvals() if row["agent_run_id"] == run["id"])
    # Its own thread shows the paused turn, so the card appears there.
    generation = service.snapshot(child)["generation"]
    assert generation is not None
    assert generation["status"] == "waiting_approval" and generation["approval_id"] == approval["id"]
    # The card says what the agent wants to do, not "Requested action".
    view = service.get_approval(approval["id"])
    assert view["action_label"] != "Requested action"
    assert view["reason"] == "Remove a stale note."
    # The parent's notice carries the same approval, so it gets a card too.
    rows = service.snapshot(parent)["rows"]
    assert [row["approval_id"] for row in rows if row.get("approval_id")] == [approval["id"]]
    # Home lists the waiting agent under "Needs you".
    assert service.get_conversation(child)["activity_phase"] == "waiting_approval"
    # Answered from React: the agent goes on and the card goes away.
    service._resolve_approval(approval["id"], {"decision": "approve"})
    final = agent_runner.wait_for_agent_run(run["id"], timeout=5)
    assert final["status"] == "completed" and resumed == [True]
    assert service.snapshot(child)["generation"] is None
    assert service.get_conversation(child)["activity_phase"] != "waiting_approval"


def test_a_delegated_agents_thread_shows_its_task_not_the_handoff_prompt(service, monkeypatch):
    """B167: the child thread opened with AGENT PROFILE / MISSION / RUNTIME
    MODEL / PARENT REFS as the person's bubble."""
    from row_bot import agent, agent_runner, threads
    from uuid import uuid4
    prompts = []

    def record(prompt, enabled_tool_names, config, *, stop_event):
        # What the real graph would store: its input messages.
        _normalized, graph_input = agent._new_agent_graph_input(prompt, config)
        messages = [message if not isinstance(message, tuple) else
                    __import__("langchain_core.messages", fromlist=["HumanMessage"]).HumanMessage(content=message[1])
                    for message in graph_input["messages"]]
        for message in messages:
            if not message.id:
                message.id = str(uuid4())
        threads.append_checkpoint_messages(config["configurable"]["thread_id"], messages)
        prompts.append(prompt)
        return "Done."

    monkeypatch.setattr(agent_runner, "_invoke_agent", record)
    parent = threads.create_thread("Parent", seed_default_skills=False)
    run = agent_runner.spawn_agent_run("Tidy the notes folder.", parent_thread_id=parent,
                                       profile="worker", wait=True)
    assert "MISSION" in prompts[0]
    first = service.snapshot(run["thread_id"])["rows"][0]
    assert first["role"] == "user" and first["note"] == "agent_task"
    shown = json.dumps(first["blocks"])
    assert "Tidy the notes folder." in shown
    for internal in ("AGENT PROFILE", "MISSION", "RUNTIME MODEL", "PARENT REFS"):
        assert internal not in shown


def test_open_pages_hear_about_a_delegated_agents_approval(service, monkeypatch):
    """B186 (found on the real app): the parent's notice and the child's paused
    turn are written outside any turn of those conversations, so an open page
    showed neither until it was reloaded. Both conversations now publish a
    checkpoint change, which makes their pages re-read."""
    import time
    from row_bot import threads
    from row_bot.application import client_platform

    monkeypatch.setattr(client_platform, "client_platform_service", service)
    parent = threads.create_thread("Parent", seed_default_skills=False)
    cursor = service.projection.snapshot(parent)["cursor"]
    run, _resumed = _waiting_child(monkeypatch, parent)
    child = run["thread_id"]

    def changed(conversation, after):
        deadline = time.time() + 5
        while time.time() < deadline:
            events = service.projection.events_since(conversation, after)["events"]
            if any(event["type"] == "transcript.checkpoint" for event in events):
                return True
            time.sleep(0.05)
        return False

    assert changed(parent, cursor)
    assert changed(child, "0")


def test_an_open_agent_thread_hears_that_its_approval_was_answered(service, monkeypatch):
    """B186 (found on the real app): answered in its own thread, the agent went
    on, but the page kept "Waiting for your approval" until it was reloaded."""
    import time
    from row_bot import agent_runner, tasks, threads
    from row_bot.application import client_platform

    monkeypatch.setattr(client_platform, "client_platform_service", service)
    parent = threads.create_thread("Parent", seed_default_skills=False)
    run, _resumed = _waiting_child(monkeypatch, parent)
    child = run["thread_id"]
    approval = next(row for row in tasks.get_pending_approvals() if row["agent_run_id"] == run["id"])
    assert service.snapshot(child)["generation"]["approval_id"] == approval["id"]
    cursor = service.projection.snapshot(child)["cursor"]
    service._resolve_approval(approval["id"], {"decision": "approve"})
    agent_runner.wait_for_agent_run(run["id"], timeout=5)
    deadline = time.time() + 5
    while time.time() < deadline:
        events = service.projection.events_since(child, cursor)["events"]
        if any(event["type"] == "transcript.checkpoint" for event in events):
            break
        time.sleep(0.05)
    else:
        raise AssertionError("the agent's thread was never told")


def test_the_service_settles_outside_change_refreshes_and_the_work_they_start(service, monkeypatch):
    """A page refresh for an outside change runs off the writer's thread and
    reads the checkpointer's shared connection. One still reading when its
    owner closed that connection crashed the process (and with it a test
    worker), so the owner settles the refreshes first, including any a
    refresh starts in turn."""
    import threading
    from row_bot.application.client_platform import settle_background

    release = threading.Event()
    refreshed = []

    def refresh(conversation_id):
        assert release.wait(5)
        refreshed.append(conversation_id)
        if conversation_id == "first":
            service.conversation_changed("second")

    monkeypatch.setattr(service, "_refresh_checkpoint", refresh)
    service.conversation_changed("first")
    assert settle_background(timeout=0.05) is False
    release.set()
    assert settle_background(timeout=5) is True
    assert refreshed == ["first", "second"]


def test_settling_waits_for_work_that_is_still_starting(monkeypatch):
    """Work is registered and started as one step: settling while a thread is still starting waits for it
    rather than failing to join a thread that hasn't started."""
    import threading
    from row_bot.application.client_platform import run_in_background, settle_background

    starting, release, outcome = threading.Event(), threading.Event(), []
    real_start = threading.Thread.start

    def slow_start(thread):
        if thread.name == "slow-start":
            starting.set()
            assert release.wait(5)
        real_start(thread)

    monkeypatch.setattr(threading.Thread, "start", slow_start)
    starter = threading.Thread(target=run_in_background, args=(lambda: None,), kwargs={"name": "slow-start"})
    starter.start()
    assert starting.wait(5)
    settler = threading.Thread(target=lambda: outcome.append(settle_background(timeout=5)))
    settler.start()
    release.set()
    starter.join(5)
    settler.join(5)
    assert outcome == [True]
