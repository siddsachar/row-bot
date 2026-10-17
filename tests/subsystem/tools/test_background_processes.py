"""Background processes (F1) and long commands (F6): one manager, bounded logs, caps, leftovers.

These tests stand a fake in for the tracked-process owner (``developer.runtime``), so no
process starts; ``test_background_process_lifecycle`` runs real ones.
"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from row_bot import background_processes as processes
from row_bot.developer import runtime

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]


class FakeTracked:
    """What ``launch_tracked_process`` returns, driven by the test."""

    def __init__(self, argv, on_quiesced, on_output, env, release_on_exit):
        self.argv, self.env, self.release_on_exit = argv, env, release_on_exit
        self.on_quiesced, self.on_output = on_quiesced, on_output
        self.process = SimpleNamespace(pid=None)
        self.child_pid = None
        self.done = threading.Event()
        self.stop_requested = False
        self.code = ""
        self.exit_code = None

    def emit(self, text: str) -> None:
        self.on_output("stdout", text)

    def exit(self, code: int) -> None:
        if self.done.is_set():
            return
        self.exit_code = code
        self.on_quiesced(self)
        self.done.set()


@pytest.fixture
def launched(monkeypatch, tmp_path):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "data"))
    started: list[FakeTracked] = []

    def launch(root, argv, command, *, on_quiesced=None, on_output=None, env=None, release_on_exit=False, **_kw):
        tracked = FakeTracked(argv, on_quiesced, on_output, env, release_on_exit)
        started.append(tracked)
        return tracked

    def stop(state):
        state.stop_requested = True
        state.exit(130)

    monkeypatch.setattr(runtime, "launch_tracked_process", launch)
    monkeypatch.setattr(runtime, "stop_tracked_process", stop)
    monkeypatch.setattr(runtime, "retire_tracked_process", lambda _state: True)
    yield started
    processes.stop_all(timeout=1)


def _start(conversation: str, tmp_path, command: str = "npm run dev", **kwargs) -> processes.BackgroundProcess:
    return processes.start(conversation, [command], command=command, folder=str(tmp_path), **kwargs)


# ── The log ─────────────────────────────────────────────────────────────────


def test_the_log_rotates_keeps_two_files_and_pages_across_them(monkeypatch, tmp_path):
    monkeypatch.setattr(processes, "LOG_ROTATE_BYTES", 100)
    log = processes.ProcessLog(tmp_path / "proc-00000001")
    lines = [f"line {index:03d}\n" for index in range(60)]  # 9 bytes each, 540 in all
    for line in lines:
        log.write(line)
    log.close()

    assert sorted(path.name for path in log.folder.iterdir()) == ["output.1.log", "output.log"]
    assert sum(path.stat().st_size for path in log.folder.iterdir()) <= 200
    assert log.written == 540 and log.available_start > 0
    # A reader from the start is told what's gone and carries on page by page to the end.
    pages, position = [], 0
    while position < log.written:
        text, start, end = log.read(position, 25)
        assert start >= position and end > start
        pages.append(text)
        position = end
    assert "".join(pages) == "".join(lines)[log.available_start:]
    whole, dropped = log.text()
    assert dropped and whole == "".join(lines)[log.available_start:]


def test_a_page_ends_on_a_whole_character(tmp_path):
    log = processes.ProcessLog(tmp_path / "proc-00000002")
    log.write("héllo wörld ✓ done\n")

    text, start, end = log.read(0, 2)  # "h" and the first byte of "é" would cut a character

    assert text == "h" and (start, end) == (0, 1)
    rest, _start, _end = log.read(end, 1000)
    assert text + rest == "héllo wörld ✓ done\n"


def test_tail_starts_on_a_whole_line(tmp_path):
    log = processes.ProcessLog(tmp_path / "proc-00000003")
    for index in range(100):
        log.write(f"tick {index}\n")

    tail, earlier = log.tail(40)

    assert earlier
    assert tail.splitlines()[0].startswith("tick ") and tail.endswith("tick 99\n")


# ── Local addresses (F4) ────────────────────────────────────────────────────


def test_local_addresses_are_found_in_output_even_coloured_or_split(launched, tmp_path):
    record = _start("conversation-a", tmp_path)
    tracked = launched[-1]
    tracked.emit("  VITE ready\n  ➜  Local:   \x1b[36mhttp://localhost:\x1b[1m5173\x1b[22m/\x1b[39m\n")
    tracked.emit("Serving HTTP on 0.0.0.0 port 8000 (http://0.0.0")
    tracked.emit(".0:8000/) ...\n")
    tracked.emit("Uvicorn running on https://127.0.0.1:8443 and http://[::1]:9000\n")
    tracked.emit("Connecting to 127.0.0.1:5432 and fetching http://example.com:8080/api\n")

    assert record.urls == ["http://localhost:5173", "http://localhost:8000", "https://127.0.0.1:8443",
                           "http://[::1]:9000"]
    # Printed is not proof: only a port the process listens on counts as a server Row-Bot started.
    assert processes.owns_local_origin("http://localhost:5173") is False
    record.ports = [5173]
    record._ports_checked = time.monotonic()
    assert processes.owns_local_origin("http://localhost:5173/") is True
    assert processes.owns_local_origin("http://localhost:5173", conversation_id="another") is False
    assert {server["origin"]: server["verified"] for server in processes.local_servers()} == {
        "http://localhost:5173": True, "http://localhost:8000": False, "https://127.0.0.1:8443": False,
        "http://[::1]:9000": False}


# ── Caps ────────────────────────────────────────────────────────────────────


def test_caps_per_conversation_and_in_all(launched, tmp_path, monkeypatch):
    monkeypatch.setattr(processes, "MAX_RUNNING_PER_CONVERSATION", 2)
    monkeypatch.setattr(processes, "MAX_RUNNING_TOTAL", 3)
    first = _start("conversation-a", tmp_path)
    _start("conversation-a", tmp_path)
    with pytest.raises(processes.ProcessLimitError, match="already running in this conversation"):
        _start("conversation-a", tmp_path)
    _start("conversation-b", tmp_path)
    with pytest.raises(processes.ProcessLimitError, match="across Row-Bot"):
        _start("conversation-c", tmp_path)

    processes.stop(first.process_id)

    assert first.status == "stopped" and first.stopped_by == "you"
    assert _start("conversation-a", tmp_path).running
    assert len(launched) == 4


def test_environment_variables_are_names_with_text_values():
    assert processes.check_environment({"PORT": 3000, "NODE_ENV": "development"}) == {
        "PORT": "3000", "NODE_ENV": "development"}
    for bad in ({"1PORT": "x"}, {"A B": "x"}, {"X": None}, {"X": True}, {"X": "a\0b"}, ["PORT"],
                {f"V{index}": "x" for index in range(processes.MAX_ENV_VARIABLES + 1)}):
        with pytest.raises(ValueError):
            processes.check_environment(bad)


# ── run_command (F6) ────────────────────────────────────────────────────────


def test_a_command_that_finishes_in_the_turn_leaves_nothing_behind(launched, tmp_path, monkeypatch):
    real_launch = runtime.launch_tracked_process

    def launch_and_finish(*args, **kwargs):
        tracked = real_launch(*args, **kwargs)
        tracked.emit("hello\n")
        threading.Timer(0.02, tracked.exit, args=(3,)).start()
        return tracked

    monkeypatch.setattr(runtime, "launch_tracked_process", launch_and_finish)

    outcome = processes.run_command("conversation-a", ["build"], command="build", folder=str(tmp_path))

    assert outcome.finished and outcome.exit_code == 3 and outcome.output == "hello\n"
    assert launched[0].release_on_exit  # What it opened (an app) keeps running, as before.
    assert processes.list_processes() == []
    assert not (processes.processes_root() / "conversation-a" / outcome.record.process_id).exists()


def test_a_command_past_the_inline_limit_carries_on_in_the_background(launched, tmp_path):
    started = time.monotonic()
    outcome = processes.run_command("conversation-a", ["build"], command="npm run build",
                                    folder=str(tmp_path), timeout=0.05)

    assert time.monotonic() - started < 5  # The turn is not blocked.
    assert not outcome.finished
    record = outcome.record
    assert [item.process_id for item in processes.list_processes("conversation-a")] == [record.process_id]
    assert record.kind == "command" and record.running
    assert processes.running_commands() == [record]
    assert (record.log.folder / "record.json").exists()

    launched[0].emit("done\n")
    launched[0].exit(0)

    assert record.status == "finished" and processes.running_commands() == []


def test_background_true_starts_it_as_a_background_process_at_once(launched, tmp_path):
    outcome = processes.run_command("conversation-a", ["build"], command="npm run build",
                                    folder=str(tmp_path), background=True)

    assert not outcome.finished and outcome.record.running and not outcome.record.inline


def test_past_the_limit_at_the_caps_the_command_is_stopped_instead(launched, tmp_path, monkeypatch):
    monkeypatch.setattr(processes, "MAX_RUNNING_PER_CONVERSATION", 1)
    _start("conversation-a", tmp_path)

    outcome = processes.run_command("conversation-a", ["build"], command="npm run build",
                                    folder=str(tmp_path), timeout=0.05)

    assert outcome.finished and outcome.exit_code == 130
    assert "already running in this conversation" in outcome.limit_message
    assert len(processes.list_processes("conversation-a")) == 1


# ── Stopping, quitting, restarting ──────────────────────────────────────────


def test_quitting_stops_every_process_and_records_why(launched, tmp_path):
    records = [_start("conversation-a", tmp_path), _start("conversation-b", tmp_path)]

    assert processes.stop_all(by="quit") == 2

    assert [record.status for record in records] == ["stopped", "stopped"]
    assert {record.stopped_by for record in records} == {"quit"}
    assert all(tracked.stop_requested for tracked in launched)
    assert processes.stop_all(by="quit") == 0


def test_a_restart_reads_finished_processes_and_stops_what_a_crash_left(launched, tmp_path, monkeypatch):
    finished = _start("conversation-a", tmp_path, name="Tests")
    launched[0].emit("all passed\n")
    launched[0].exit(0)
    crashed = _start("conversation-a", tmp_path, command="npm run dev", name="Dev server")
    crashed.pid, crashed.pid_birth = 4242, 123.0
    processes._save(crashed)  # As a crash leaves it: never marked as ended.
    gone = _start("conversation-b", tmp_path, command="python worker.py")
    processes._save(gone)
    killed: list[int] = []
    monkeypatch.setattr(processes, "_same_process",
                        lambda pid, birth: SimpleNamespace(pid=pid) if (pid, birth) == (4242, 123.0) else None)
    monkeypatch.setattr(processes, "_kill_tree", lambda process: killed.append(process.pid) or True)

    monkeypatch.setattr(processes, "_LOADED_ROOT", None)  # A new Row-Bot process on the same data folder.
    notice = processes.report_leftovers()

    assert killed == [4242]
    assert notice == ("1 background process Row-Bot started before it last closed was still running; "
                      "Row-Bot stopped it.")
    restored = {record.process_id: record for record in processes.list_processes()}
    assert restored[finished.process_id].status == "finished"
    assert restored[finished.process_id].log.text()[0] == "all passed\n"
    assert (restored[crashed.process_id].status, restored[crashed.process_id].stopped_by) == ("stopped", "restart")
    assert (restored[gone.process_id].status, restored[gone.process_id].stopped_by) == ("stopped", "quit")
    assert processes.report_leftovers() == ""  # Reported once.


def test_deleting_a_conversation_stops_its_processes_and_removes_their_logs(launched, tmp_path):
    mine = _start("conversation-a", tmp_path)
    other = _start("conversation-b", tmp_path)
    mine.log.write("output\n")

    assert processes.stop_conversation("conversation-a") == 1
    assert processes.delete_conversation_logs("conversation-a") == 1

    assert mine.status == "stopped" and mine.stopped_by == "conversation"
    assert other.running
    assert [record.process_id for record in processes.list_processes()] == [other.process_id]
    assert not mine.log.folder.exists()


def test_the_view_says_what_runs_where_in_plain_fields(launched, tmp_path):
    record = _start("conversation-a", tmp_path, name="  Dev   server ", env={"PORT": "3000"})
    launched[0].emit("Listening on http://localhost:3000\n")

    view = processes.view(record, conversation_title="Fix the login page")

    assert {key: view[key] for key in ("name", "kind", "status", "conversation_title", "local_urls", "env_names",
                                       "exit_code", "ended_at")} == {
        "name": "Dev server", "kind": "process", "status": "running", "conversation_title": "Fix the login page",
        "local_urls": ["http://localhost:3000"], "env_names": ["PORT"], "exit_code": None, "ended_at": None}
    assert "3000" not in str(view["env_names"])  # Names only; values stay out of every list.


def test_without_process_containment_a_command_still_runs_in_the_turn(launched, tmp_path, monkeypatch):
    """An old Linux kernel can't contain processes: run_command falls back to a plain subprocess."""
    from row_bot.process_cancellation import ProcessRunResult
    from row_bot.tools import shell_tool

    real_launch = runtime.launch_tracked_process

    def refused(*args, **kwargs):
        tracked = real_launch(*args, **kwargs)
        tracked.code = "process_containment_unavailable"
        tracked.exit(126)
        return tracked

    monkeypatch.setattr(runtime, "launch_tracked_process", refused)
    monkeypatch.setattr(shell_tool, "run_cancellable_subprocess",
                        lambda args, **_kw: ProcessRunResult(args, 0, stdout="plain"))

    result = shell_tool.ShellSession(str(tmp_path)).run_command("echo plain")

    assert (result["exit_code"], result["output"]) == (0, "plain")
