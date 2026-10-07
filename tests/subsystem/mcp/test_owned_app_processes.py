"""After a crash, a later Row-Bot stops the local app programs it provably started, and nothing else.

A fake process table stands in for psutil in the deterministic tests; the one test that starts a real
program is marked slow."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import types

import pytest

from row_bot import owned_processes
from row_bot.mcp_client import runtime
from tests.subsystem.access.test_tunnel_lifecycle import OWN_CREATED, FakeProcesses

pytestmark = [pytest.mark.subsystem, pytest.mark.platform, pytest.mark.mcp_transport]

SERVER, CHILD, GRANDCHILD = 8101, 8102, 8103


class FamilyProcesses(FakeProcesses):
    """The fake process table, with each program's children."""

    def __init__(self) -> None:
        super().__init__()
        self.parents: dict[int, int] = {}

    def module(self) -> types.ModuleType:
        module = super().module()
        processes, base = self, module.Process

        class Process(base):
            def children(self, recursive: bool = False):
                found = [pid for pid, parent in processes.parents.items() if parent == self.pid and pid in processes.table]
                if recursive:
                    found += [grandchild.pid for pid in list(found) for grandchild in Process(pid).children(True)]
                return [Process(pid) for pid in found]
        module.Process = Process
        return module


@pytest.fixture
def table(monkeypatch) -> FamilyProcesses:
    fake = FamilyProcesses()
    monkeypatch.setitem(sys.modules, "psutil", fake.module())
    return fake


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    path = tmp_path / "runtime" / runtime.APP_PROCESSES
    monkeypatch.setattr(owned_processes, "ledger_path", lambda file: tmp_path / "runtime" / file)
    return path


def _entry(pid, created, *, name="node.exe", owner_pid=9999, owner_created=400.0):
    return {"pid": pid, "created": created, "name": name, "owner_pid": owner_pid, "owner_created": owner_created,
            "server": "Notes"}


def _write(path, *entries):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"agents": list(entries)}), encoding="utf-8")


def test_a_program_a_crashed_row_bot_started_is_stopped_with_its_tree(table, ledger):
    table.add(SERVER, 5000.0, name="node.exe")
    table.add(CHILD, 5001.0, name="node.exe")
    table.add(GRANDCHILD, 5002.0, name="python.exe")
    table.parents.update({CHILD: SERVER, GRANDCHILD: CHILD})
    _write(ledger, _entry(SERVER, 5000.0))
    assert runtime.cleanup_app_processes() == 1
    assert set(table.terminated) == {SERVER, CHILD, GRANDCHILD}
    assert not ledger.exists()


def test_only_provably_owned_programs_are_ever_stopped(table, ledger):
    table.add(9998, 400.0, name="python.exe")  # Another Row-Bot, still running.
    table.add(SERVER, 5000.0)  # Started by that running Row-Bot.
    table.add(8201, 900.0)  # The recorded pid now belongs to a newer program.
    table.add(8202, 700.0, name="explorer.exe")  # Same pid and time, but not the program recorded.
    table.add(8203, 5100.0)  # This Row-Bot's own, running.
    table.add(8300, 100.0, name="node.exe")  # Never recorded at all.
    running = _entry(SERVER, 5000.0, owner_pid=9998, owner_created=400.0)
    mine = _entry(8203, 5100.0, owner_pid=os.getpid(), owner_created=OWN_CREATED)
    _write(ledger, running, _entry(8201, 600.0), _entry(8202, 700.0), mine, _entry(8204, 800.0))
    assert runtime.cleanup_app_processes() == 0
    assert table.terminated == []
    assert set(table.table) >= {SERVER, 8201, 8202, 8203, 8300}
    assert json.loads(ledger.read_text(encoding="utf-8"))["agents"] == [running, mine]  # Stale records dropped.


def test_a_record_without_a_name_is_never_acted_on_or_written(table, ledger):
    """Nothing tells a nameless record's program from another one that started at the same moment: start-up
    cleanup stops nothing (not its tree either) and drops the record; a program whose name cannot be read is
    never recorded."""
    table.add(SERVER, 5000.0, name="explorer.exe")
    table.add(CHILD, 5001.0, name="python.exe")
    table.parents[CHILD] = SERVER
    _write(ledger, _entry(SERVER, 5000.0, name=""), _entry(CHILD, 0, name="python.exe"))
    assert runtime.cleanup_app_processes() == 0
    assert table.terminated == [] and not ledger.exists()
    table.add(8400, 5200.0, name="")
    assert owned_processes.record(ledger, 8400, server="Notes") is False
    assert not ledger.exists()


def test_each_connection_records_its_program_and_forgets_it_once_ended(table, ledger, monkeypatch):
    """The runtime notes the program the SDK starts for a connection (and only one it starts for a
    connection), and forgets it when the connection has ended it."""
    from mcp.client import stdio as sdk_stdio

    assert getattr(sdk_stdio._create_platform_compatible_process, "row_bot_records", False)
    table.add(SERVER, 5000.0, name="node.exe")

    async def spawn(*_args, **_kwargs):
        return types.SimpleNamespace(pid=SERVER)
    monkeypatch.setattr(sdk_stdio, "_create_platform_compatible_process", spawn)
    runtime._record_app_processes()  # Wraps whatever the SDK has, as at import.
    connection = runtime.McpServerRuntime("Notes", {"transport": "stdio"})

    async def start(owner):
        token = runtime._spawning.set(owner)
        try:
            return await sdk_stdio._create_platform_compatible_process("node", [])
        finally:
            runtime._spawning.reset(token)
    asyncio.run(start(None))  # Not for a connection (Row-Bot's own adapters): not recorded here.
    assert not ledger.exists()
    asyncio.run(start(connection))
    recorded = json.loads(ledger.read_text(encoding="utf-8"))["agents"]
    assert [(item["pid"], item["owner_pid"], item["server"]) for item in recorded] == [(SERVER, os.getpid(), "Notes")]
    connection.exit_stack = None
    asyncio.run(connection.close())
    assert not ledger.exists()


@pytest.mark.slow
def test_a_real_orphaned_program_is_stopped_at_the_next_start(tmp_path, monkeypatch):
    """A real child recorded by a Row-Bot that has since ended is stopped; its pid and time are checked."""
    monkeypatch.setattr(owned_processes, "ledger_path", lambda file: tmp_path / "runtime" / file)
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        assert owned_processes.record(owned_processes.ledger_path(runtime.APP_PROCESSES), child.pid, server="Notes")
        path = owned_processes.ledger_path(runtime.APP_PROCESSES)
        entries = json.loads(path.read_text(encoding="utf-8"))["agents"]
        entries[0].update(owner_pid=4_000_000, owner_created=1.0)  # The Row-Bot that recorded it has ended.
        path.write_text(json.dumps({"agents": entries}), encoding="utf-8")
        assert runtime.cleanup_app_processes() == 1
        assert child.wait(timeout=10) is not None
    finally:
        if child.poll() is None:
            child.kill()
