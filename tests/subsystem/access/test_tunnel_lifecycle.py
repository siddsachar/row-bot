"""Tunnel lifecycle: every exit closes what Row-Bot opened (decision 15).

Uses a fake pyngrok and a fake process table; no real ngrok agent, tunnel
or process is ever started, inspected or stopped.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import types
from pathlib import Path

import pytest

import row_bot.tunnel as tunnel
from row_bot.access.config import AccessConfig
from row_bot.access.runtime_policy import RuntimeAccessPolicy
from row_bot.tunnel import NgrokProvider, TunnelError, TunnelManager

OWN_CREATED = 1000.0
AGENT_PID = 7001


class FakeProcesses:
    """A process table standing in for psutil."""

    def __init__(self) -> None:
        self.table: dict[int, dict] = {os.getpid(): {"created": OWN_CREATED, "name": "python.exe"}}
        self.terminated: list[int] = []

    def add(self, pid: int, created: float, name: str = "ngrok.exe") -> None:
        self.table[pid] = {"created": created, "name": name}

    def module(self) -> types.ModuleType:
        processes = self

        class NoSuchProcess(Exception):
            pass

        class TimeoutExpired(Exception):
            pass

        class Process:
            def __init__(self, pid: int) -> None:
                if pid not in processes.table:
                    raise NoSuchProcess(pid)
                self.pid = pid

            def create_time(self) -> float:
                return processes.table[self.pid]["created"]

            def name(self) -> str:
                return processes.table[self.pid]["name"]

            def terminate(self) -> None:
                processes.terminated.append(self.pid)
                processes.table.pop(self.pid, None)

            def wait(self, timeout: float | None = None) -> int:  # noqa: ARG002
                return 0

            def kill(self) -> None:
                processes.table.pop(self.pid, None)

        module = types.ModuleType("psutil")
        module.Process = Process
        module.NoSuchProcess = NoSuchProcess
        module.TimeoutExpired = TimeoutExpired
        return module


class FakeNgrok:
    """The slice of pyngrok Row-Bot uses; connect starts a fake agent."""

    def __init__(self, processes: FakeProcesses) -> None:
        self.processes = processes
        self.failure: Exception | None = None
        self.kills = 0
        self.disconnected: list[str] = []

    def connect(self, port: int, bind_tls: bool = True):  # noqa: ARG002
        self.processes.add(AGENT_PID, 5000.0)
        if self.failure is not None:
            raise self.failure
        return types.SimpleNamespace(public_url=f"https://managed-{port}.ngrok-free.app")

    def disconnect(self, url: str) -> None:
        self.disconnected.append(url)

    def kill(self) -> None:
        self.kills += 1
        self.processes.table.pop(AGENT_PID, None)

    def get_ngrok_process(self):
        return types.SimpleNamespace(proc=types.SimpleNamespace(pid=AGENT_PID))


@pytest.fixture
def processes(monkeypatch) -> FakeProcesses:
    fake = FakeProcesses()
    monkeypatch.setitem(sys.modules, "psutil", fake.module())
    return fake


@pytest.fixture
def owned(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "runtime" / "ngrok-agents.json"
    monkeypatch.setattr(tunnel, "owned_agents_path", lambda: path)
    return path


@pytest.fixture
def contained(monkeypatch) -> list[int]:
    # Never hand a (fake) pid to a real Windows job object.
    calls: list[int] = []
    monkeypatch.setattr(tunnel, "_contain_agent", lambda pid: calls.append(pid) or True)
    return calls


@pytest.fixture
def ngrok(monkeypatch, processes, owned, contained) -> FakeNgrok:  # noqa: ARG001
    fake = FakeNgrok(processes)
    package = types.ModuleType("pyngrok")
    package.ngrok = fake
    package.conf = types.SimpleNamespace(get_default=lambda: types.SimpleNamespace(auth_token=None))
    monkeypatch.setitem(sys.modules, "pyngrok", package)
    monkeypatch.setattr(tunnel, "_ngrok_authtoken", lambda: "test-token")
    monkeypatch.setattr(tunnel, "ngrok_configuration_status", lambda: ("ok", "ngrok available"))
    return fake


def _manager(provider=None) -> TunnelManager:
    policy = RuntimeAccessPolicy(AccessConfig.build(deployment_mode="server", allowed_hosts=("localhost",)))
    manager = TunnelManager(managed_origin_registrar=policy)
    manager.set_provider(provider or NgrokProvider())
    return manager


def _record(path: Path, *agents: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"agents": list(agents)}), encoding="utf-8")


def _agent(pid: int, created: float, owner_pid: int, owner_created: float) -> dict:
    return {"pid": pid, "created": created, "owner_pid": owner_pid, "owner_created": owner_created}


def test_started_agent_is_recorded_and_the_last_close_stops_it(ngrok, owned, contained):
    manager = _manager()

    origin = manager.start_tunnel(8080, label="main_app")

    assert origin == "https://managed-8080.ngrok-free.app"
    assert json.loads(owned.read_text())["agents"] == [
        _agent(AGENT_PID, 5000.0, os.getpid(), OWN_CREATED)
    ]
    assert contained == [AGENT_PID]

    manager.stop_tunnel(8080)

    # An idle agent still holds one of the account's sessions.
    assert ngrok.kills == 1
    assert AGENT_PID not in ngrok.processes.table
    assert not owned.exists()
    assert manager.runtime_state() == {"runtime_state": "idle", "active_count": 0, "last_error": None}


def test_exit_closes_tunnels_even_while_running_work_has_not_stopped(ngrok, owned, monkeypatch):
    import row_bot.app as app
    from row_bot.application.lifecycle import application_lifecycle

    manager = _manager()
    manager.start_tunnel(8080, label="main_app")
    monkeypatch.setattr(tunnel, "tunnel_manager", manager)
    monkeypatch.setattr(app, "_shutdown_cleanup_started", False)

    async def still_running():
        return {"status": "cancelling"}

    monkeypatch.setattr(application_lifecycle, "shutdown", still_running)

    finished = asyncio.run(app._cleanup_runtime("test"))

    assert finished is False  # other resources wait for the work to stop
    assert manager.active_tunnels() == {}
    assert ngrok.kills == 1
    assert not owned.exists()


def test_closing_on_exit_twice_or_with_nothing_open_is_harmless(ngrok, monkeypatch):
    manager = _manager()
    monkeypatch.setattr(tunnel, "tunnel_manager", manager)

    tunnel.close_tunnels_on_exit("nothing open")
    assert ngrok.kills == 0

    manager.start_tunnel(8080)
    tunnel.close_tunnels_on_exit("quit")
    tunnel.close_tunnels_on_exit("interpreter exit")

    assert ngrok.kills == 1
    assert manager.active_tunnels() == {}


def test_next_start_stops_the_agent_a_crashed_run_left(processes, owned):
    processes.add(AGENT_PID, 5000.0)
    _record(owned, _agent(AGENT_PID, 5000.0, owner_pid=9999, owner_created=400.0))

    assert tunnel.cleanup_owned_agents() == 1

    assert processes.terminated == [AGENT_PID]
    assert not owned.exists()


def test_agents_of_a_running_row_bot_are_kept(processes, owned):
    processes.add(9998, 400.0, name="python.exe")  # another Row-Bot still running
    processes.add(AGENT_PID, 5000.0)
    processes.add(7002, 5100.0)
    running = _agent(AGENT_PID, 5000.0, owner_pid=9998, owner_created=400.0)
    mine = _agent(7002, 5100.0, owner_pid=os.getpid(), owner_created=OWN_CREATED)
    _record(owned, running, mine)

    assert tunnel.cleanup_owned_agents() == 0

    assert processes.terminated == []
    assert json.loads(owned.read_text())["agents"] == [running, mine]


def test_agents_row_bot_did_not_start_are_never_touched(processes, owned):
    processes.add(7000, 4000.0)  # someone else's ngrok, never recorded
    processes.add(7002, 900.0)  # the recorded pid now belongs to a newer ngrok
    processes.add(7003, 700.0, name="python.exe")  # recorded pid reused by another program
    _record(
        owned,
        _agent(7002, 600.0, owner_pid=9999, owner_created=400.0),
        _agent(7003, 700.0, owner_pid=9999, owner_created=400.0),
    )

    assert tunnel.cleanup_owned_agents() == 0

    assert processes.terminated == []
    assert set(processes.table) >= {7000, 7002, 7003}
    assert not owned.exists()  # stale records are dropped


def test_forced_stop_matches_the_server_child_that_recorded_the_agent(processes, owned):
    # The launcher started the venv shim (4242); the interpreter it runs
    # (4243) recorded the agent and may still look alive for a moment.
    processes.add(4243, 401.0, name="python.exe")
    processes.add(AGENT_PID, 5000.0)
    _record(owned, _agent(AGENT_PID, 5000.0, owner_pid=4243, owner_created=401.0))

    assert tunnel.cleanup_owned_agents(dead_owner={4242, 4243}) == 1

    assert processes.terminated == [AGENT_PID]
    assert not owned.exists()


def test_forced_stop_cleans_up_the_agent_of_the_server_it_killed(processes, owned):
    # The killed server may not be reaped yet, so it still looks alive.
    processes.add(4242, 400.0, name="python.exe")
    processes.add(AGENT_PID, 5000.0)
    _record(owned, _agent(AGENT_PID, 5000.0, owner_pid=4242, owner_created=400.0))

    assert tunnel.cleanup_owned_agents(dead_owner=4242) == 1

    assert processes.terminated == [AGENT_PID]


@pytest.mark.parametrize("graceful", [False, True])
def test_launcher_forced_stop_asks_for_owned_agent_cleanup(monkeypatch, tmp_path, graceful):
    from row_bot import launcher

    cleaned: list[int | None] = []

    class FakePopen:
        pid = 4242

        def __init__(self) -> None:
            self.alive = True

        def poll(self):
            return None if self.alive else 0

        def terminate(self) -> None:
            self.alive = False

        def wait(self, timeout=None):  # noqa: ARG002
            self.alive = False
            return 0

        def kill(self) -> None:
            self.alive = False

    monkeypatch.setattr(launcher.subprocess, "Popen", lambda cmd, **kwargs: FakePopen())  # noqa: ARG005
    monkeypatch.setattr(launcher.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(launcher._RowBotProcess, "_request_graceful_shutdown", lambda self: graceful)
    monkeypatch.setattr(
        launcher._RowBotProcess,
        "_terminate_process",
        lambda self, proc, **kwargs: proc.kill(),  # noqa: ARG005
    )
    monkeypatch.setattr(tunnel, "cleanup_owned_agents", lambda *, dead_owner=None: cleaned.append(dead_owner))
    # A venv python.exe runs the real server as its child: the whole tree is
    # named before the stop (never a real process table here).
    monkeypatch.setattr(launcher, "_process_tree", lambda pid: {pid, pid + 1})

    process = launcher._RowBotProcess(port=8125, host="127.0.0.1")
    process.start()
    process.stop()

    assert cleaned == ([] if graceful else [{4242, 4243}])


def test_session_limit_refusal_is_reported_in_words_and_frees_the_agent(ngrok, owned):
    manager = _manager()
    ngrok.failure = RuntimeError(
        "ngrok error: your account is limited to 1 simultaneous ngrok agent sessions. ERR_NGROK_108"
    )

    with pytest.raises(TunnelError) as refused:
        manager.start_tunnel(8080, label="main_app")

    message = str(refused.value)
    assert "as many agents running as it allows" in message
    assert "ERR_NGROK_108" not in message
    assert ngrok.kills == 1 and not owned.exists()
    assert manager.last_error == message
    assert manager.status() == ("error", f"Not running: {message}")
    assert manager.runtime_state() == {"runtime_state": "failed", "active_count": 0, "last_error": message}

    ngrok.failure = None
    manager.start_tunnel(8080, label="main_app")

    assert manager.last_error is None
    assert manager.runtime_state()["runtime_state"] == "active"
    assert manager.status()[0] == "ok"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("failed to start: ERR_NGROK_108 limited to 1 simultaneous ngrok agent sessions", "as many agents"),
        ("The authtoken you specified is not valid. ERR_NGROK_107", "didn't accept the saved authtoken"),
        ("NGROK_AUTHTOKEN not set", "No ngrok authtoken is saved"),
        ("pyngrok is not installed. Run: pip install pyngrok", "isn't installed"),
        ("tunnel session failed: ERR_NGROK_334 endpoint is already online", "already serving this address"),
        ("secret-looking detail 0123456789abcdef", "couldn't open the tunnel"),
    ],
)
def test_tunnel_errors_are_described_without_raw_detail(raw, expected):
    described = tunnel.describe_tunnel_error(RuntimeError(raw))

    assert expected in described
    assert "0123456789abcdef" not in described


def test_check_tunnel_setup_returns_what_it_found(ngrok, monkeypatch):
    from row_bot.application import settings_commands

    manager = _manager()
    monkeypatch.setattr(tunnel, "tunnel_manager", manager)

    idle = settings_commands._run_system_action("tunnel.check")
    assert idle == {
        "code": "inactive",
        "message": "The tunnel is set up and not running.",
        "remediation": "Start app tunnel opens it.",
    }

    ngrok.failure = RuntimeError("ERR_NGROK_108")
    with pytest.raises(TunnelError):
        manager.start_tunnel(8080)
    failed = settings_commands._run_system_action("tunnel.check")
    assert failed["code"] == "error"
    assert failed["message"] == "The tunnel isn't running."
    assert "as many agents" in failed["remediation"]

    ngrok.failure = None
    manager.start_tunnel(8080)
    assert settings_commands._run_system_action("tunnel.check")["message"] == "The tunnel is running (1 active)."


def test_running_sms_without_a_public_address_is_not_reported_ok(monkeypatch):
    from row_bot import status_checks
    from row_bot.channels import registry

    class ReachableChannel:
        display_name = "Telegram"

        def is_configured(self) -> bool:
            return True

        def is_running(self) -> bool:
            return True

    class UnreachableChannel(ReachableChannel):
        display_name = "SMS"

        def reachability_problem(self) -> str | None:
            return "Twilio can't reach it: the public tunnel isn't running."

    monkeypatch.setattr(registry, "all_channels", lambda: [ReachableChannel(), UnreachableChannel()])

    results = {result.name: result for result in status_checks.check_channels()}

    assert (results["Telegram"].status, results["Telegram"].detail) == ("ok", "Running")
    assert results["SMS"].status == "warn"
    assert results["SMS"].detail == "Running. Twilio can't reach it: the public tunnel isn't running."


def test_sms_reachability_uses_the_tunnel_failure(monkeypatch):
    from row_bot.channels import sms

    manager = _manager(provider=types.SimpleNamespace())
    manager._last_error = "ngrok refused a new tunnel."
    monkeypatch.setattr(tunnel, "tunnel_manager", manager)
    monkeypatch.setattr(sms.ch_config, "get", lambda section, key, default=None: default)
    monkeypatch.setattr(sms, "_running", True)
    monkeypatch.setattr(sms, "_webhook_public_url", None)

    assert sms.reachability_problem() == "ngrok refused a new tunnel."

    monkeypatch.setattr(sms, "_webhook_public_url", "https://managed-8080.ngrok-free.app")
    assert sms.reachability_problem() is None
