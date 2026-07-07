from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from row_bot.mobile.access_gate import MobileAccessGate, is_true_local_scope
from row_bot.mobile.auth import confirm_pairing, create_pairing_ticket
from row_bot.mobile.cookies import HTTP_LAN_COOKIE_NAME
from row_bot.mobile.store import MobileAuthStore


async def _ok_app(scope, receive, send) -> None:
    if scope["type"] == "websocket":
        await send({"type": "websocket.accept"})
        await send({"type": "websocket.close", "code": 1000})
        return
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"application/json")],
        }
    )
    await send({"type": "http.response.body", "body": b'{"ok":true}'})


def _run_http(gate: MobileAccessGate, *, path: str, client: str, method: str = "GET", headers=None):
    messages = []
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "query_string": b"",
        "headers": headers or [],
        "client": (client, 50000),
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    asyncio.run(gate(scope, receive, send))
    return messages


def _run_websocket(gate: MobileAccessGate, *, path: str, client: str, headers=None):
    messages = []
    scope = {
        "type": "websocket",
        "scheme": "ws",
        "path": path,
        "query_string": b"EIO=4&transport=websocket",
        "headers": headers or [],
        "client": (client, 50000),
        "subprotocols": [],
    }

    async def receive():
        return {"type": "websocket.connect"}

    async def send(message):
        messages.append(message)

    asyncio.run(gate(scope, receive, send))
    return messages


def _status(messages) -> int:
    return next(message["status"] for message in messages if message["type"] == "http.response.start")


def _header(messages, name: bytes) -> bytes:
    start = next(message for message in messages if message["type"] == "http.response.start")
    headers = dict(start["headers"])
    return headers.get(name, b"")


def _valid_cookie(store: MobileAuthStore) -> tuple[str, str]:
    now = datetime(2026, 7, 5, 9, 0, tzinfo=timezone.utc)
    ticket = create_pairing_ticket(store, now=now)
    confirmation = confirm_pairing(store, code=ticket.code, display_name="Phone", now=now)
    return confirmation.device.id, f"{HTTP_LAN_COOKIE_NAME}={confirmation.token}"


def test_true_local_scope_requires_loopback_without_forwarded_headers() -> None:
    assert is_true_local_scope({"client": ("127.0.0.1", 1), "headers": []}) is True
    assert is_true_local_scope({"client": ("::1", 1), "headers": []}) is True
    assert is_true_local_scope({"client": ("192.168.1.20", 1), "headers": []}) is False
    assert (
        is_true_local_scope(
            {"client": ("127.0.0.1", 1), "headers": [(b"x-forwarded-for", b"203.0.113.9")]}
        )
        is False
    )


def test_local_http_bypasses_gate_for_sensitive_paths(tmp_path) -> None:
    gate = MobileAccessGate(_ok_app, store=MobileAuthStore(tmp_path / "mobile.db"))

    messages = _run_http(gate, path="/_media/thread/file.png", client="127.0.0.1")

    assert _status(messages) == 200


def test_forwarded_localhost_is_gated(tmp_path) -> None:
    gate = MobileAccessGate(_ok_app, store=MobileAuthStore(tmp_path / "mobile.db"))

    messages = _run_http(
        gate,
        path="/api/voice/realtime/client-secret",
        method="POST",
        client="127.0.0.1",
        headers=[(b"x-forwarded-for", b"203.0.113.9")],
    )

    assert _status(messages) == 401


def test_remote_root_redirects_to_pairing_page(tmp_path) -> None:
    gate = MobileAccessGate(_ok_app, store=MobileAuthStore(tmp_path / "mobile.db"))

    messages = _run_http(
        gate,
        path="/",
        client="192.168.1.20",
        headers=[(b"accept", b"text/html")],
    )

    assert _status(messages) == 303
    assert _header(messages, b"location").startswith(b"/mobile/pair")


def test_remote_pairing_and_session_routes_are_allowed_without_cookie(tmp_path) -> None:
    gate = MobileAccessGate(_ok_app, store=MobileAuthStore(tmp_path / "mobile.db"))

    pair = _run_http(gate, path="/mobile/pair", client="192.168.1.20")
    session = _run_http(gate, path="/api/mobile/session", client="192.168.1.20")

    assert _status(pair) == 200
    assert _status(session) == 200


def test_valid_mobile_cookie_allows_remote_http_and_revocation_blocks_it(tmp_path) -> None:
    store = MobileAuthStore(tmp_path / "mobile.db")
    gate = MobileAccessGate(_ok_app, store=store)
    device_id, cookie = _valid_cookie(store)

    allowed = _run_http(
        gate,
        path="/published/page.html",
        client="192.168.1.20",
        headers=[(b"cookie", cookie.encode("latin-1"))],
    )
    assert _status(allowed) == 200

    store.revoke_device(device_id)
    denied = _run_http(
        gate,
        path="/published/page.html",
        client="192.168.1.20",
        headers=[(b"cookie", cookie.encode("latin-1"))],
    )
    assert _status(denied) == 401


def test_websocket_scope_is_gated_without_cookie(tmp_path) -> None:
    gate = MobileAccessGate(_ok_app, store=MobileAuthStore(tmp_path / "mobile.db"))

    messages = _run_websocket(gate, path="/_nicegui_ws/socket.io", client="192.168.1.20")

    assert messages == [{"type": "websocket.close", "code": 1008}]


def test_valid_cookie_allows_remote_websocket_scope(tmp_path) -> None:
    store = MobileAuthStore(tmp_path / "mobile.db")
    gate = MobileAccessGate(_ok_app, store=store)
    _device_id, cookie = _valid_cookie(store)

    messages = _run_websocket(
        gate,
        path="/_nicegui_ws/socket.io",
        client="192.168.1.20",
        headers=[(b"cookie", cookie.encode("latin-1"))],
    )

    assert messages[0]["type"] == "websocket.accept"
