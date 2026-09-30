from __future__ import annotations

from dataclasses import replace

import pytest

from row_bot.terminal_bridge import (
    TerminalAccessError,
    TerminalBridge,
    TerminalClientAuthority,
)

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]


class FakePty:
    def __init__(self, *, cols: int, rows: int, cwd: str | None = None) -> None:
        self.alive = True
        self.cols = cols
        self.rows = rows
        self.cwd = cwd
        self.writes: list[str] = []
        self.resizes: list[tuple[int, int]] = []
        self.close_count = 0

    def is_alive(self) -> bool:
        return self.alive

    def write(self, data: str) -> None:
        self.writes.append(data)

    def resize(self, cols: int, rows: int) -> None:
        self.cols, self.rows = cols, rows
        self.resizes.append((cols, rows))

    def read(self, _size: int) -> str:
        return ""

    def close(self) -> None:
        self.close_count += 1
        self.alive = False


def _authority() -> TerminalClientAuthority:
    return TerminalClientAuthority(
        instance_id="instance-1",
        session_id="session-1",
        window_id="window-1",
        window_epoch=4,
        policy_revision="policy-1",
        authority_grant="native-grant-1",
        conversation_id="conversation-1",
    )


def _running_bridge(*, output_buffer_bytes: int = 256 * 1024):
    created: list[FakePty] = []

    def factory(**kwargs):
        pty = FakePty(**kwargs)
        created.append(pty)
        return pty

    bridge = TerminalBridge(pty_factory=factory, output_buffer_bytes=output_buffer_bytes)
    bridge.start()
    return bridge, created[0]


def test_native_terminal_requires_local_owner_loopback_and_exact_authority() -> None:
    bridge, _ = _running_bridge()
    authority = _authority()
    for local_owner, direct_loopback in ((False, True), (True, False), (False, False)):
        with pytest.raises(TerminalAccessError, match="native_terminal_denied"):
            bridge.open_native_client(
                authority, authorize=lambda _: True,
                local_owner=local_owner, direct_loopback=direct_loopback,
            )
    with pytest.raises(TerminalAccessError, match="native_terminal_denied"):
        bridge.open_native_client(
            replace(authority, authority_grant=""), authorize=lambda _: True,
            local_owner=True, direct_loopback=True,
        )
    with pytest.raises(TerminalAccessError, match="capability_revoked"):
        bridge.open_native_client(
            authority, authorize=lambda _: False,
            local_owner=True, direct_loopback=True,
        )


def test_native_terminal_input_resize_and_revocation_use_existing_pty() -> None:
    bridge, pty = _running_bridge()
    current = {"allowed": True}
    authority = _authority()
    client = bridge.open_native_client(
        authority,
        authorize=lambda candidate: current["allowed"] and candidate == authority,
        local_owner=True,
        direct_loopback=True,
    )
    client.input("echo fixture\r")
    client.resize(132, 44)
    assert pty.writes == ["echo fixture\r"]
    assert pty.resizes == [(132, 44)]

    current["allowed"] = False
    with pytest.raises(TerminalAccessError, match="capability_revoked"):
        client.input("must-not-run\r")
    assert pty.writes == ["echo fixture\r"]
    assert client.closed


@pytest.mark.parametrize(
    ("method", "args"),
    [
        ("input", ("x" * (16 * 1024 + 1),)),
        ("resize", (19, 30)),
        ("resize", (501, 30)),
        ("resize", (120, 4)),
        ("resize", (120, 201)),
        ("resize", (True, 30)),
    ],
)
def test_native_terminal_rejects_unbounded_input_and_resize(method: str, args: tuple) -> None:
    bridge, pty = _running_bridge()
    client = bridge.open_native_client(
        _authority(), authorize=lambda _: True,
        local_owner=True, direct_loopback=True,
    )
    with pytest.raises(ValueError):
        getattr(client, method)(*args)
    assert pty.writes == [] and pty.resizes == []


def test_output_cursor_is_bounded_and_reports_truncation() -> None:
    bridge, _ = _running_bridge(output_buffer_bytes=8192)
    client = bridge.open_native_client(
        _authority(), authorize=lambda _: True,
        local_owner=True, direct_loopback=True,
    )
    bridge._publish_output("a" * 4096)
    bridge._publish_output("b" * 4096)
    bridge._publish_output("c" * 4096)

    first = client.read(0)
    assert first["truncated"] is True
    assert [frame["sequence"] for frame in first["frames"]] == [2, 3]
    assert "a" not in "".join(frame["data"] for frame in first["frames"])
    tail = client.read(2, 4096)
    assert tail == {
        "cursor": 3,
        "latest": 3,
        "truncated": False,
        "frames": [{"sequence": 3, "data": "c" * 4096}],
        "status": "running",
    }
    with pytest.raises(ValueError, match="invalid_terminal_cursor"):
        client.read(4)


def test_read_rechecks_authority_before_returning_output() -> None:
    bridge, _ = _running_bridge()
    bridge._publish_output("private terminal fixture")
    checks = 0

    def authorize(_authority) -> bool:
        nonlocal checks
        checks += 1
        return checks < 3

    client = bridge.open_native_client(
        _authority(), authorize=authorize,
        local_owner=True, direct_loopback=True,
    )
    with pytest.raises(TerminalAccessError, match="capability_revoked"):
        client.read()
    assert client.closed


def test_disconnect_detaches_client_but_global_pty_lives_until_shutdown() -> None:
    bridge, pty = _running_bridge()
    client = bridge.open_native_client(
        _authority(), authorize=lambda _: True,
        local_owner=True, direct_loopback=True,
    )
    client.disconnect()
    with pytest.raises(TerminalAccessError, match="terminal_disconnected"):
        client.read()
    assert bridge.is_running
    assert pty.close_count == 0
    bridge._shutdown()
    assert pty.close_count == 1
    assert not bridge.is_running


def test_a_shell_that_exits_reads_as_stopped_and_opening_again_starts_a_new_one() -> None:
    """``exit`` ends the shell: reads say so and the next open starts afresh (B248)."""
    created: list[FakePty] = []

    def factory(**kwargs):
        created.append(FakePty(**kwargs))
        return created[-1]

    bridge = TerminalBridge(pty_factory=factory)
    bridge.start()
    client = bridge.open_native_client(
        _authority(), authorize=lambda _: True,
        local_owner=True, direct_loopback=True,
    )
    assert client.read()["status"] == "running"
    created[0].alive = False
    assert client.read()["status"] == "stopped"
    bridge.start()
    assert len(created) == 2 and created[0].close_count == 1
    assert client.read()["status"] == "running"


def test_the_reader_stops_when_the_shell_has_ended() -> None:
    import asyncio

    bridge, pty = _running_bridge()
    pty.alive = False
    reads = []

    def read(_size: int) -> str:
        reads.append(_size)
        if len(reads) == 3:
            bridge._running = False  # a reader that keeps going ends here
        return ""

    pty.read = read
    asyncio.run(bridge._reader_loop())
    assert len(reads) == 1


def test_output_callback_can_disconnect_itself_without_deadlock() -> None:
    bridge, _ = _running_bridge()
    seen = []

    def callback(data: str) -> None:
        seen.append(data)
        bridge.unregister_output_callback(callback)

    bridge.register_output_callback(callback)
    bridge._publish_output("first")
    bridge._publish_output("second")
    assert seen == ["first"]


def test_stop_interrupts_the_running_command_instead_of_typing_it() -> None:
    """Stop (Ctrl+C) is an interrupt, not a keystroke ConPTY may ignore (B172)."""
    bridge, pty = _running_bridge()
    interrupts: list[int] = []
    pty.interrupt = lambda: interrupts.append(1)  # type: ignore[attr-defined]
    bridge.on_input("ping -n 60 127.0.0.1\r")
    bridge.on_input("\x03")
    assert pty.writes == ["ping -n 60 127.0.0.1\r"]
    assert interrupts == [1]
    # A Ctrl+C inside other text is ordinary input.
    bridge.on_input("a\x03b")
    assert pty.writes[-1] == "a\x03b"


class _Proc:
    def __init__(self, pid: int, name: str, children: list[_Proc] | None = None) -> None:
        self.pid, self._name, self._children = pid, name, children or []
        self.terminated = False

    def name(self) -> str:
        return self._name

    def children(self, recursive: bool = False) -> list[_Proc]:
        if not recursive:
            return list(self._children)
        found: list[_Proc] = []
        for child in self._children:
            found += [child, *child.children(recursive=True)]
        return found

    def terminate(self) -> None:
        self.terminated = True


def test_windows_interrupt_stops_the_shells_running_command_but_not_the_shell(monkeypatch) -> None:
    import psutil

    from row_bot import terminal_pty

    grandchild = _Proc(30, "python.exe")
    command = _Proc(20, "PING.EXE", [grandchild])
    console = _Proc(21, "conhost.exe")
    shell = _Proc(10, "powershell.exe", [command, console])
    monkeypatch.setattr(psutil, "Process", lambda pid: {10: shell}[pid])
    monkeypatch.setattr(psutil, "wait_procs", lambda procs, timeout=None: (procs, []))
    session = terminal_pty.PtySession.__new__(terminal_pty.PtySession)
    session._closed = False
    session._lock = __import__("threading").Lock()
    written: list[str] = []

    class _Process:
        pid = 10

        def write(self, data: str) -> None:
            written.append(data)

    session._process = _Process()
    session._fd = None
    monkeypatch.setattr(terminal_pty, "_IS_WINDOWS", True)
    assert session.interrupt() == 2
    assert written == ["\x03"]
    assert command.terminated and grandchild.terminated
    assert not console.terminated and not shell.terminated
