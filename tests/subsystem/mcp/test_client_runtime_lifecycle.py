"""Canonical MCP lifecycle retains ownership through actual transport return."""
from __future__ import annotations

import asyncio
import concurrent.futures
from types import SimpleNamespace

import pytest

pytestmark = [pytest.mark.subsystem, pytest.mark.mcp_transport]


@pytest.fixture
def owner(monkeypatch):
    from row_bot.mcp_client import runtime
    monkeypatch.setattr(runtime, "_servers", {})
    monkeypatch.setattr(runtime, "_catalog", {})
    monkeypatch.setattr(runtime, "_statuses", {})
    monkeypatch.setattr(runtime, "sdk_available", lambda: True)
    monkeypatch.setattr(runtime.mcp_config, "clear_agent_cache_if_loaded", lambda: None)
    return runtime


def test_stop_timeout_never_removes_current_owner_or_allows_replacement(owner, monkeypatch):
    server = owner.McpServerRuntime("synthetic", {"command": "synthetic", "enabled": True})
    owner._servers["synthetic"] = server
    class Blocked:
        def result(self, timeout):
            raise concurrent.futures.TimeoutError()
    def schedule(coro):
        coro.close()
        return Blocked()
    monkeypatch.setattr(owner, "_schedule", schedule)
    owner.stop_server("synthetic")
    assert owner._servers.get("synthetic") is server
    monkeypatch.setattr(owner, "_get_effective_config", lambda: {"enabled": True, "servers": {
        "synthetic": {"enabled": True, "command": "synthetic"}}})
    monkeypatch.setattr(owner, "McpServerRuntime", lambda *_a, **_k: pytest.fail("Old cleanup is not complete"))
    owner.discover_enabled_servers()


def test_failed_close_retains_exit_owner_and_never_reports_stopped(owner):
    class FailedExit:
        async def aclose(self):
            raise OSError("synthetic transport cleanup failed")
    async def scenario():
        server = owner.McpServerRuntime("synthetic", {})
        owner._servers["synthetic"] = server
        stack = server.exit_stack = FailedExit()
        await server.close()
        assert server.exit_stack is stack
        assert owner._servers.get("synthetic") is server
        assert owner._statuses["synthetic"].status == "cleanup_incomplete"
    asyncio.run(scenario())


def test_stop_during_handshake_observes_owner_task_return(owner, monkeypatch):
    async def scenario():
        entered = asyncio.Event()
        allow = asyncio.Event()
        closed = []
        class Stack:
            async def aclose(self):
                closed.append(asyncio.current_task())
        async def connect(server):
            server.exit_stack = Stack()
            entered.set()
            await allow.wait()
            server.session = SimpleNamespace()
        monkeypatch.setattr(owner.McpServerRuntime, "_connect", connect)
        server = owner.McpServerRuntime("synthetic", {"connect_timeout": 1})
        owner._servers["synthetic"] = server
        task = asyncio.create_task(server.start())
        try:
            await entered.wait()
            await server.stop()
            assert task.done(), "Stop must observe the original handshake owner returning"
            assert closed and all(closer is task for closer in closed)
            assert owner._servers.get("synthetic") is not server
        finally:
            allow.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


def test_failed_start_cleanup_remains_registered(owner, monkeypatch):
    class FailedExit:
        async def aclose(self):
            raise OSError("synthetic cleanup failed")
    async def connect(server):
        server.exit_stack = FailedExit()
        raise RuntimeError("synthetic handshake failed")
    monkeypatch.setattr(owner.McpServerRuntime, "_connect", connect)
    async def scenario():
        server = owner.McpServerRuntime("synthetic", {})
        owner._servers["synthetic"] = server
        await server.start()
        assert owner._servers.get("synthetic") is server
        assert owner._statuses["synthetic"].status == "cleanup_incomplete"
    asyncio.run(scenario())


def test_repeated_stop_and_cancelled_waiter_do_not_interrupt_owner_cleanup(owner, monkeypatch):
    async def scenario():
        connected, closing, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        interruptions = []
        class Stack:
            async def aclose(self):
                closing.set()
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    interruptions.append(True)
                    raise
        async def connect(server):
            server.exit_stack = Stack()
            connected.set()
        async def discover(_server):
            return None
        monkeypatch.setattr(owner.McpServerRuntime, "_connect", connect)
        monkeypatch.setattr(owner.McpServerRuntime, "_discover_tools", discover)
        server = owner.McpServerRuntime("synthetic", {})
        owner._servers["synthetic"] = server
        task = asyncio.create_task(server.start())
        try:
            await connected.wait()
            stop = asyncio.create_task(server.stop())
            await closing.wait()
            stop.cancel()
            await asyncio.gather(stop, return_exceptions=True)
            assert owner._servers.get("synthetic") is server and not task.done()
            second = asyncio.create_task(server.stop())
            ready = asyncio.Event()
            asyncio.get_running_loop().call_soon(ready.set)
            await ready.wait()
            assert not interruptions and not second.done()
            release.set()
            await second
            assert task.done() and server.cleanup_complete and not interruptions
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


def test_stop_before_queued_start_never_enters_transport(owner, monkeypatch):
    async def scenario():
        async def connect(_server):
            pytest.fail("Stopped queued owner connected")
        monkeypatch.setattr(owner.McpServerRuntime, "_connect", connect)
        server = owner.McpServerRuntime("synthetic", {})
        server._start_admitted = True
        owner._servers["synthetic"] = server
        stop = asyncio.create_task(server.stop())
        ready = asyncio.Event()
        asyncio.get_running_loop().call_soon(ready.set)
        await ready.wait()
        assert not stop.done()
        task = asyncio.create_task(server.start())
        await asyncio.gather(stop, task)
        assert server.cleanup_complete and "synthetic" not in owner._servers
    asyncio.run(scenario())


def test_probe_cleanup_failure_retains_owner_and_refuses_second_connection(owner, monkeypatch):
    calls = []
    class Stack:
        async def aclose(self):
            raise OSError("synthetic close failure")
    async def tools():
        return SimpleNamespace(tools=[])
    async def connect(server):
        calls.append(server.runtime_id)
        server.exit_stack = Stack()
        server.session = SimpleNamespace(list_tools=tools)
    monkeypatch.setattr(owner.McpServerRuntime, "_connect", connect)
    async def scenario():
        first = await owner.probe_server_async("synthetic", {})
        assert not first["ok"] and "cleanup is unconfirmed" in first["error"]
        retained = owner._servers["synthetic"]
        second = await owner.probe_server_async("synthetic", {})
        assert not second["ok"] and len(calls) == 1
        assert owner._servers["synthetic"] is retained
    asyncio.run(scenario())


def test_shutdown_retains_loop_when_transport_cleanup_is_not_observed(owner, monkeypatch):
    server = owner.McpServerRuntime("synthetic", {})
    owner._servers["synthetic"] = server
    loop, thread = object(), object()
    monkeypatch.setattr(owner, "_loop", loop)
    monkeypatch.setattr(owner, "_thread", thread)
    monkeypatch.setattr(owner, "stop_server", lambda _name: None)
    owner.shutdown()
    assert owner._loop is loop and owner._thread is thread
    assert owner._servers["synthetic"] is server


def test_failed_cleanup_is_sticky_even_when_exit_stack_drops_its_callback(owner):
    attempts = []
    class Stack:
        async def aclose(self):
            attempts.append(True)
            if len(attempts) == 1:
                raise OSError("synthetic popped failing callback")
    async def scenario():
        server = owner.McpServerRuntime("synthetic", {})
        server.exit_stack = Stack()
        await server.close()
        await server.close()
        assert not server.cleanup_complete and len(attempts) == 1
    asyncio.run(scenario())


def test_bootstrap_deadline_covers_blocked_transport_entry(owner, monkeypatch):
    async def scenario():
        loop = asyncio.get_running_loop()
        clock = [0.0]
        monkeypatch.setattr(loop, "time", lambda: clock[0])
        entries = []
        async def connect(server):
            entries.append(server.runtime_id)
            loop.call_soon(clock.__setitem__, 0, 100.0)
            await asyncio.Event().wait()
        monkeypatch.setattr(owner.McpServerRuntime, "_connect", connect)
        server = owner.McpServerRuntime("synthetic", {"connect_timeout": 3})
        owner._servers["synthetic"] = server
        await server.start()
        assert len(entries) == 1 and server.cleanup_complete
        assert owner._statuses["synthetic"].status == "failed"
        assert "synthetic" not in owner._servers
    asyncio.run(scenario())


def test_receipt_failure_removes_only_proven_unstarted_reservation(owner, monkeypatch):
    monkeypatch.setattr(owner, "_schedule", lambda *_a: pytest.fail("No schedule before checkpoint"))
    identities = []
    def checkpoint(identity):
        identities.append(identity)
        assert owner._servers["synthetic"].runtime_id == identity
        raise OSError("synthetic receipt commit failed")
    with pytest.raises(OSError, match="receipt commit"):
        owner.launch_server_owned("synthetic", {}, before_start=checkpoint, validate=lambda: None)
    assert identities and "synthetic" not in owner._servers


def test_cancel_between_checkpoint_and_schedule_prevents_launch(owner, monkeypatch):
    monkeypatch.setattr(owner, "_schedule", lambda *_a: pytest.fail("Cancelled reservation must not schedule launch"))
    def checkpoint(_identity):
        owner._servers["synthetic"]._stop_requested.set()
    with pytest.raises(ValueError, match="mcp_runtime_cancelled"):
        owner.launch_server_owned("synthetic", {}, before_start=checkpoint, validate=lambda: None)
    assert "synthetic" not in owner._servers


def test_post_handshake_revocation_prevents_discovery_and_retains_no_live_owner(owner, monkeypatch):
    authority = [True]
    scheduled = []
    def schedule(coro):
        scheduled.append(coro)
        return concurrent.futures.Future()
    def validate():
        if not authority[0]:
            raise PermissionError("synthetic authority revoked")
    async def connect(server):
        authority[0] = False
        server.session = SimpleNamespace()
    async def forbidden(_server):
        pytest.fail("Revoked handshake cannot discover tools")
    monkeypatch.setattr(owner, "_schedule", schedule)
    monkeypatch.setattr(owner.McpServerRuntime, "_connect", connect)
    monkeypatch.setattr(owner.McpServerRuntime, "_discover_tools", forbidden)
    server = owner.launch_server_owned("synthetic", {}, before_start=lambda _identity: None, validate=validate)
    asyncio.run(scheduled.pop())
    assert server.cleanup_complete and server._finished.is_set()
    assert not server._connected_admitted and "synthetic" not in owner._servers


def test_exact_stop_rejects_newer_owner_without_revoking_its_catalog(owner, monkeypatch):
    server = owner.McpServerRuntime("synthetic", {})
    owner._servers["synthetic"] = server
    owner._catalog["synthetic"] = {"kept": object()}
    monkeypatch.setattr(owner, "_schedule", lambda *_a: pytest.fail("Cannot stop replacement owner"))
    with pytest.raises(ValueError, match="identity_changed"):
        owner.stop_server_owned("synthetic", "different-owner")
    assert "kept" in owner._catalog["synthetic"] and not server._stop_requested.is_set()


def test_new_lifecycle_owner_cannot_dispatch_before_final_admission_or_after_stop(owner, monkeypatch):
    cfg = {"enabled": True, "servers": {"synthetic": {"enabled": True, "command": "synthetic",
        "tools": {"enabled": {"read": True}}}}}
    server = owner.McpServerRuntime("synthetic", cfg["servers"]["synthetic"])
    server._start_admitted = True
    owner._servers["synthetic"] = server
    owner._catalog["synthetic"] = {"read": owner.McpToolInfo("synthetic", "read", "mcp_synthetic_read", enabled=True)}
    monkeypatch.setattr(owner, "_get_effective_config", lambda: cfg)
    bound = owner._bind_authority("synthetic", "read")
    with pytest.raises(RuntimeError, match="not admitted"):
        owner._validate_bound_runtime("synthetic", bound, tool_name="read")
    server._connected_admitted = True
    owner._validate_bound_runtime("synthetic", bound, tool_name="read")
    server._stop_requested.set()
    with pytest.raises(RuntimeError, match="not admitted"):
        owner._validate_bound_runtime("synthetic", bound, tool_name="read")


def test_temporary_owned_launch_checkpoints_before_execution_and_freezes_input(owner, monkeypatch):
    scheduled, events = [], []
    cfg = {"command": "synthetic", "args": ["original argument"]}
    async def tools():
        return SimpleNamespace(tools=[])
    async def connect(server):
        events.append("connect")
        assert server.cfg["args"] == ["original argument"]
        server.session = SimpleNamespace(list_tools=tools)
    def checkpoint(_identity):
        events.append("checkpoint")
        cfg["args"] = ["changed caller argument"]
    def schedule(coro):
        events.append("schedule")
        scheduled.append(coro)
        return concurrent.futures.Future()
    monkeypatch.setattr(owner, "_schedule", schedule)
    monkeypatch.setattr(owner.McpServerRuntime, "_connect", connect)
    server = owner.launch_server_owned("synthetic", cfg, before_start=checkpoint, validate=lambda: None, temporary=True)
    result = asyncio.run(scheduled.pop())
    assert result["ok"] and events == ["checkpoint", "schedule", "connect"]
    assert server.cleanup_complete and server._finished.is_set() and "synthetic" not in owner._servers


def test_release_receipt_failure_retains_quiescent_owner_and_retries_only_bookkeeping(owner, monkeypatch):
    scheduled, calls = [], []
    fail = [True]
    class Stack:
        async def aclose(self):
            calls.append("close")
    async def tools():
        return SimpleNamespace(tools=[])
    async def connect(server):
        calls.append("connect")
        server.exit_stack = Stack()
        server.session = SimpleNamespace(list_tools=tools)
    def receipt(server):
        calls.append("receipt")
        assert server.cleanup_complete and server._probe_result["ok"] is True
        if fail[0]:
            raise OSError("synthetic completion receipt failure")
    def schedule(coro):
        scheduled.append(coro)
        # Deliberately never publish the Future result: the owner result is authoritative.
        return concurrent.futures.Future()
    monkeypatch.setattr(owner, "_schedule", schedule)
    monkeypatch.setattr(owner.McpServerRuntime, "_connect", connect)
    server = owner.launch_server_owned("synthetic", {}, before_start=lambda _id: None,
        validate=lambda: None, temporary=True, before_release=receipt)
    result = asyncio.run(scheduled.pop())
    assert result["ok"] and not server._launch_future.done()
    assert owner._servers["synthetic"] is server
    assert owner.get_server_lifecycle("synthetic")["session_quiesced"] is True
    fail[0] = False
    proof = owner.reconcile_server_release_owned("synthetic", server.runtime_id)
    assert proof["receipt_confirmed"] and proof["session_quiesced"]
    assert "synthetic" not in owner._servers and calls == ["connect", "close", "receipt", "receipt"]


def test_new_cleanup_receipt_during_completion_cannot_use_older_confirmation(owner):
    import threading
    entered, release = threading.Event(), threading.Event()
    calls, result = [], []
    server = owner.McpServerRuntime("synthetic", {})
    server.cleanup_complete = True
    server._finished.set()
    server._release_confirmed = False
    owner._servers["synthetic"] = server
    def checkpoint(_server):
        calls.append("receipt")
        if len(calls) == 1:
            entered.set()
            assert release.wait(5)
    server._before_release = checkpoint
    worker = threading.Thread(target=lambda: result.append(server._confirm_release()))
    worker.start()
    try:
        assert entered.wait(5)
        with owner._runtime_lock:
            server._release_epoch += 1
            server._release_confirmed = False
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive() and result == [False]
    assert owner._servers["synthetic"] is server and not server._release_confirmed
    proof = owner.reconcile_server_release_owned("synthetic", server.runtime_id)
    assert proof["receipt_confirmed"] and calls == ["receipt", "receipt"]
    assert "synthetic" not in owner._servers


def test_release_rechecks_new_cleanup_admission_before_forgetting_owner(owner, monkeypatch):
    server = owner.McpServerRuntime("synthetic", {})
    server.cleanup_complete = True
    server._finished.set()
    owner._servers["synthetic"] = server
    def interleaved_confirmation():
        with owner._runtime_lock:
            server._release_epoch += 1
            server._release_confirmed = False
        return True  # Earlier callback completed before the new receipt arrived.
    monkeypatch.setattr(server, "_confirm_release", interleaved_confirmation)
    proof = owner.reconcile_server_release_owned("synthetic", server.runtime_id)
    assert not proof["receipt_confirmed"] and proof["session_quiesced"]
    assert owner._servers["synthetic"] is server
