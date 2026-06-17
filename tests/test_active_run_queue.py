from __future__ import annotations

import ast
import importlib
import inspect
import sys


def _fresh_modules(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(data_dir))
    for name in (
        "row_bot.tasks",
        "row_bot.agent_runs",
        "row_bot.ui.streaming",
        "row_bot.ui.transcript",
    ):
        sys.modules.pop(name, None)

    import row_bot.tasks as tasks
    import row_bot.agent_runs as agent_runs
    import row_bot.ui.streaming as streaming
    import row_bot.ui.transcript as transcript

    tasks = importlib.reload(tasks)
    agent_runs = importlib.reload(agent_runs)
    streaming = importlib.reload(streaming)
    transcript = importlib.reload(transcript)
    return agent_runs, streaming, transcript


def test_queued_control_message_dispatch_updates_render_key(tmp_path, monkeypatch):
    _agent_runs, streaming, transcript = _fresh_modules(tmp_path, monkeypatch)

    msg = streaming._queued_control_message(
        "use another agent for the quantum essay",
        kind="follow_up",
        status="queued_parent_turn",
        label="Queued as your next chat message",
        message_id="queued-1",
    )
    before = transcript.message_key(0, msg)

    changed = streaming._mark_queued_controls_dispatching([msg], ["queued-1"])
    after = transcript.message_key(0, msg)

    assert changed is True
    assert msg["queued_control"]["status"] == "dispatching"
    assert msg["queued_control"]["label"] == "Sent after current response"
    assert before != after


def test_queued_control_settlement_removes_rendered_badge(tmp_path, monkeypatch):
    _agent_runs, streaming, transcript = _fresh_modules(tmp_path, monkeypatch)

    msg = streaming._queued_control_message(
        "use another agent for the quantum essay",
        kind="follow_up",
        status="queued_parent_turn",
        label="Queued as your next chat message",
        message_id="queued-1",
    )
    assert streaming._mark_queued_controls_dispatching([msg], ["queued-1"])
    before = transcript.message_key(0, msg)

    changed = streaming._settle_queued_controls([msg], ["queued-1"])
    after = transcript.message_key(0, msg)

    assert changed is True
    assert "queued_control" not in msg
    assert before != after


def test_assistant_insert_respects_future_and_current_queued_turns(tmp_path, monkeypatch):
    _agent_runs, streaming, _transcript = _fresh_modules(tmp_path, monkeypatch)

    first_user = {"role": "user", "content": "first"}
    queued = streaming._queued_control_message(
        "use another agent",
        kind="follow_up",
        status="queued_parent_turn",
        label="Queued as your next chat message",
        message_id="queued-1",
    )
    messages = [first_user, queued]

    inserted = streaming._insert_assistant_before_future_queued_turns(
        messages,
        {"role": "assistant", "content": "first answer"},
    )

    assert inserted == 1
    assert [msg["role"] for msg in messages] == ["user", "assistant", "user"]
    assert messages[2]["queued_control"]["id"] == "queued-1"

    streaming._mark_queued_controls_dispatching(messages, ["queued-1"])
    inserted = streaming._insert_assistant_before_future_queued_turns(
        messages,
        {"role": "assistant", "content": "second answer"},
        current_queued_ids=["queued-1"],
    )

    assert inserted == 3
    assert [msg["content"] for msg in messages] == [
        "first",
        "first answer",
        "use another agent",
        "second answer",
    ]


def test_assistant_insert_respects_direct_agent_turn_boundaries(tmp_path, monkeypatch):
    _agent_runs, streaming, _transcript = _fresh_modules(tmp_path, monkeypatch)

    messages = [
        {"role": "user", "content": "first"},
        {
            "role": "user",
            "content": "use another agent",
            "turn_boundary": {"after_generation_id": "gen-1"},
        },
        {
            "role": "assistant",
            "content": "Started Agent",
            "agent_run_ids": ["run-1"],
            "agent_run_refresh_key": "queued",
        },
    ]

    inserted = streaming._insert_assistant_before_future_queued_turns(
        messages,
        {"role": "assistant", "content": "first answer"},
        current_generation_id="gen-1",
    )

    assert inserted == 1
    assert [msg["content"] for msg in messages] == [
        "first",
        "first answer",
        "use another agent",
        "Started Agent",
    ]

    inserted = streaming._insert_assistant_before_future_queued_turns(
        messages,
        {"role": "assistant", "content": "later answer"},
        current_generation_id="gen-2",
    )

    assert inserted == 4
    assert messages[-1]["content"] == "later answer"


def test_agent_run_refresh_key_updates_render_key(tmp_path, monkeypatch):
    _agent_runs, _streaming, transcript = _fresh_modules(tmp_path, monkeypatch)

    msg = {
        "role": "assistant",
        "content": "Started Agent",
        "agent_run_ids": ["run-1"],
        "agent_run_refresh_key": "queued",
    }
    before = transcript.message_key(0, msg)
    msg["agent_run_refresh_key"] = "completed"
    after = transcript.message_key(0, msg)

    assert before != after


def test_direct_agent_completion_summary_appends_once(tmp_path, monkeypatch):
    agent_runs, streaming, _transcript = _fresh_modules(tmp_path, monkeypatch)

    agent_runs.create_agent_run(
        run_id="run-1",
        kind="subagent",
        status="running",
        parent_thread_id="parent",
        display_name="PDF Writer",
    )
    messages = [
        {
            "role": "assistant",
            "content": "Started a Worker agent for this. I'll keep this thread updated.",
            "agent_run_ids": ["run-1"],
            "agent_run_refresh_key": "running",
            "agent_lifecycle": {
                "kind": "direct_agent_spawn",
                "run_id": "run-1",
                "completion_summary_emitted": False,
            },
        }
    ]

    assert streaming._append_direct_agent_completion_messages(messages, ["run-1"]) is False

    agent_runs.finish_agent_run(
        "run-1",
        "completed",
        summary="Created C:\\Users\\v_2si\\Documents\\Row-Bot\\ai_agent_smoke.pdf",
    )

    assert streaming._append_direct_agent_completion_messages(messages, ["run-1"]) is True
    assert messages[0]["agent_lifecycle"]["completion_summary_emitted"] is True
    assert messages[1]["role"] == "assistant"
    assert "PDF Writer completed" in messages[1]["content"]
    assert "ai_agent_smoke.pdf" in messages[1]["content"]
    assert streaming._append_direct_agent_completion_messages(messages, ["run-1"]) is False
    assert len(messages) == 2


def test_new_agent_request_is_not_child_steering(tmp_path, monkeypatch):
    _agent_runs, streaming, _transcript = _fresh_modules(tmp_path, monkeypatch)

    assert streaming._looks_like_new_agent_request("also use another agent to write the PDF")
    assert not streaming._looks_like_new_agent_request("also make the essay more technical")


def test_single_steering_target_is_conservative(tmp_path, monkeypatch):
    agent_runs, streaming, _transcript = _fresh_modules(tmp_path, monkeypatch)

    assert streaming._single_steering_target("parent") is None

    first = agent_runs.create_agent_run(
        run_id="target-1",
        kind="subagent",
        status="queued",
        parent_thread_id="parent",
        display_name="Queued Writer",
    )
    assert streaming._single_steering_target("parent")["id"] == first["id"]

    agent_runs.create_agent_run(
        run_id="target-2",
        kind="subagent",
        status="queued",
        parent_thread_id="parent",
        display_name="Second Writer",
    )
    assert streaming._single_steering_target("parent") is None


def test_send_message_does_not_shadow_queue_module(tmp_path, monkeypatch):
    _agent_runs, streaming, _transcript = _fresh_modules(tmp_path, monkeypatch)

    tree = ast.parse(inspect.getsource(streaming.send_message))
    assigned_names: set[str] = set()

    class _AssignedNameVisitor(ast.NodeVisitor):
        def visit_Name(self, node: ast.Name) -> None:  # noqa: N802
            if isinstance(node.ctx, ast.Store):
                assigned_names.add(node.id)

    _AssignedNameVisitor().visit(tree)

    assert "queue" not in assigned_names
