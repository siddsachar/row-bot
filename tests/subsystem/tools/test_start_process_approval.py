"""Starting a background process asks exactly as run_command does, by the approval level (F1).

Ask asks the person; Auto asks the reviewer first; Hands-off (stored ``full``) allows it and
records that; Block refuses. Blocked patterns and the private data folder are hard limits at
every level. Stopping a process Row-Bot started needs no approval. The tracked-process owner is
a fake here: nothing runs.
"""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]

CONFIG = {"configurable": {"thread_id": "conversation-a"}}
OTHER = {"configurable": {"thread_id": "conversation-b"}}


class _Tracked:
    def __init__(self, on_quiesced, on_output):
        self.on_quiesced, self.on_output = on_quiesced, on_output
        self.process = SimpleNamespace(pid=None)
        self.child_pid = None
        self.done = threading.Event()
        self.stop_requested = False
        self.code = ""
        self.exit_code = None

    def exit(self, code: int) -> None:
        if not self.done.is_set():
            self.exit_code = code
            self.on_quiesced(self)
            self.done.set()


@pytest.fixture
def tools(tmp_path, monkeypatch, reload_for_data_dir):
    approval_gate, shell_tool = reload_for_data_dir(
        tmp_path / "data", "row_bot.tools.approval_gate", "row_bot.tools.shell_tool")
    from row_bot import background_processes
    from row_bot.developer import runtime

    started: list[dict] = []

    def launch(root, argv, command, *, on_quiesced=None, on_output=None, env=None, **_kw):
        tracked = _Tracked(on_quiesced, on_output)
        on_output("stdout", "ready on http://localhost:5173\n")
        started.append({"command": command, "folder": str(root), "env": env, "tracked": tracked})
        return tracked

    def stop(state):
        state.stop_requested = True
        state.exit(130)

    monkeypatch.setattr(runtime, "launch_tracked_process", launch)
    monkeypatch.setattr(runtime, "stop_tracked_process", stop)
    monkeypatch.setattr(runtime, "retire_tracked_process", lambda _state: True)
    monkeypatch.setattr(shell_tool, "START_WATCH_SECONDS", 0.0)
    yield SimpleNamespace(gate=approval_gate, shell=shell_tool, started=started, tmp=tmp_path)
    background_processes.stop_all(timeout=1)


def _level(tools, monkeypatch, mode: str) -> None:
    monkeypatch.setattr(tools.gate, "current_approval_mode", lambda: mode)


def _reviewer(monkeypatch, decision: str) -> list[dict]:
    from row_bot.approval_review import ReviewVerdict

    seen: list[dict] = []
    monkeypatch.setattr("row_bot.approval_review.review",
                        lambda payload, **_kw: seen.append(payload) or ReviewVerdict(decision, "You asked for it."))
    return seen


def _start(tools, command: str = "npm run dev", **kwargs) -> str:
    kwargs.setdefault("folder", str(tools.tmp))
    return tools.shell.ShellTool().start_process(command, runtime_config=CONFIG, **kwargs)


def test_ask_shows_what_starts_where_and_with_what(tools, monkeypatch):
    _level(tools, monkeypatch, "approve")
    asked: list[dict] = []
    monkeypatch.setattr("langgraph.types.interrupt", lambda payload: asked.append(payload) or True)

    result = _start(tools, env={"PORT": "3000"}, name="Dev server", approval_reason="To preview the site.")

    payload = asked[0]
    assert payload["tool"] == "start_process"
    assert payload["approval_reason"] == "To preview the site."
    assert payload["args"] == {"command": "npm run dev", "folder": str(tools.tmp.resolve()), "env": ["PORT"]}
    assert payload["description"] == (
        "Start “Dev server” in the background: npm run dev with PORT=3000. It keeps running after this reply "
        f"until it's stopped or Row-Bot quits. Folder: {tools.tmp.resolve()}")
    assert result.startswith("Approval: asked; approved by you\nBackground process proc-")
    assert tools.started[0]["env"] == {"PORT": "3000"}
    assert "Serving: http://localhost:5173" in result


def test_ask_denied_starts_nothing(tools, monkeypatch):
    _level(tools, monkeypatch, "approve")
    monkeypatch.setattr("langgraph.types.interrupt", lambda _payload: False)

    result = _start(tools)

    assert result == "Approval: asked; denied by you — did not run\nCommand cancelled by user."
    assert tools.started == []


@pytest.mark.parametrize("decision", ["approve", "ask"])
def test_auto_asks_the_reviewer_first(tools, monkeypatch, decision):
    _level(tools, monkeypatch, "auto")
    reviewed = _reviewer(monkeypatch, decision)
    asked: list[dict] = []
    monkeypatch.setattr("langgraph.types.interrupt", lambda payload: asked.append(payload) or True)

    result = _start(tools)

    assert [payload["tool"] for payload in reviewed] == ["start_process"]
    if decision == "approve":
        assert asked == []
        assert result.startswith("Approval: approved by the reviewer: You asked for it")
    else:
        assert asked[0]["review"] == {"decision": "ask", "reason": "You asked for it."}
        assert result.startswith("Approval: asked; approved by you. The reviewer asked you: You asked for it")
    assert len(tools.started) == 1


def test_hands_off_starts_it_without_asking_and_records_that(tools, monkeypatch):
    _level(tools, monkeypatch, "full")
    monkeypatch.setattr("langgraph.types.interrupt", lambda _payload: pytest.fail("must not ask"))
    monkeypatch.setattr("row_bot.approval_review.review", lambda *_a, **_kw: pytest.fail("no reviewer"))

    result = _start(tools)

    assert result.startswith(tools.gate.APPROVAL_FULL_AUTONOMY + "\nBackground process proc-")
    assert len(tools.started) == 1


def test_block_refuses_it(tools, monkeypatch):
    _level(tools, monkeypatch, "block")
    monkeypatch.setattr("langgraph.types.interrupt", lambda _payload: pytest.fail("must not ask"))

    result = _start(tools)

    assert result.startswith("BLOCKED: This command requires approval and cannot start in Block approval mode")
    assert tools.started == []


@pytest.mark.parametrize("mode", ["block", "approve", "auto", "full"])
def test_a_blocked_command_is_refused_at_every_level(tools, monkeypatch, mode):
    _level(tools, monkeypatch, mode)
    monkeypatch.setattr("langgraph.types.interrupt", lambda _payload: pytest.fail("must not ask"))
    monkeypatch.setattr("row_bot.approval_review.review", lambda *_a, **_kw: pytest.fail("no reviewer"))

    assert _start(tools, "rm -rf /").startswith("🚫 BLOCKED")
    # Its environment counts as part of the command.
    assert _start(tools, "sh -c \"$TASK\"", env={"TASK": "mkfs /dev/sda1"}).startswith("🚫 BLOCKED")
    assert tools.started == []


def test_the_private_data_folder_stays_out_of_reach(tools, monkeypatch):
    from row_bot.data_paths import get_row_bot_data_dir

    _level(tools, monkeypatch, "full")
    monkeypatch.setattr("langgraph.types.interrupt", lambda _payload: pytest.fail("must not ask"))
    data = get_row_bot_data_dir()
    data.mkdir(parents=True, exist_ok=True)

    in_folder = _start(tools, "python serve.py", folder=str(data))
    by_variable = _start(tools, "python serve.py", env={"ROOT": str(data)})

    assert in_folder == tools.shell.PRIVATE_DATA_REFUSAL and by_variable == tools.shell.PRIVATE_DATA_REFUSAL
    assert tools.started == []


def test_a_safe_command_starts_without_asking_as_run_command_runs_it(tools, monkeypatch):
    _level(tools, monkeypatch, "approve")
    monkeypatch.setattr("langgraph.types.interrupt", lambda _payload: pytest.fail("a safe command never asks"))

    result = _start(tools, "ping -t 127.0.0.1")

    assert result.startswith("Approval: not needed (safe command)\nBackground process proc-")


def test_check_and_stop_need_no_approval_and_see_only_this_conversation(tools, monkeypatch):
    _level(tools, monkeypatch, "full")
    started = _start(tools, name="Dev server")
    process_id = started.split("Background process ", 1)[1].split(":", 1)[0]
    _level(tools, monkeypatch, "approve")
    monkeypatch.setattr("langgraph.types.interrupt", lambda _payload: pytest.fail("must not ask"))
    tool = tools.shell.ShellTool()

    assert f"{process_id} · running for" in tool.check_process(runtime_config=CONFIG)
    status = tool.check_process(process_id, runtime_config=CONFIG)
    assert status.startswith(f"Background process {process_id}: running for")
    assert "ready on http://localhost:5173" in status
    assert "No background process" in tool.check_process(process_id, runtime_config=OTHER)
    assert "No background process" in tool.stop_process(process_id, runtime_config=OTHER)

    stopped = tool.stop_process(process_id, runtime_config=CONFIG)

    assert stopped.startswith(f"Background process {process_id}: stopped by you after")
    assert tools.started[0]["tracked"].stop_requested


def test_its_result_leaves_a_live_card_in_the_conversation(tools, monkeypatch):
    from row_bot.application.conversation_traces import specialize_tool_result

    _level(tools, monkeypatch, "full")
    result = _start(tools)
    process_id = result.split("Background process ", 1)[1].split(":", 1)[0]

    card = specialize_tool_result({"name": "start_process", "content": result})

    assert (card.kind, card.process_id) == ("background_process", process_id)
    # Only the line Row-Bot writes first names it: output that prints the same words does not.
    printed = f"$ echo\nBackground process {process_id}: running for 1s.\n"
    assert specialize_tool_result({"name": "run_command", "content": printed}) is None
    assert specialize_tool_result({"name": "check_process", "content": result}) is None
