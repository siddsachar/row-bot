from __future__ import annotations

from dataclasses import replace

import pytest

from row_bot.terminal_bridge import (
    TerminalAccessError,
    TerminalBridge,
    TerminalClientAuthority,
)

pytestmark = pytest.mark.subsystem


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
