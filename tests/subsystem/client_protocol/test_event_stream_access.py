"""An open page's event stream checks access often enough to end within a second of a revocation, and no
more: it no longer reads the access store eight times a second while nothing happens (5.3.1)."""
from __future__ import annotations

import asyncio
import threading
import time
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

from row_bot.api.v1.routes import create_client_platform_app
from row_bot.api.v1.security import ClientSecurity
from tests.helpers.access import person_window
from tests.subsystem.client_protocol.test_protocol_security import Service, bootstrap

pytestmark = pytest.mark.subsystem


def test_an_idle_stream_checks_access_twice_a_second_and_ends_soon_after_a_revocation():
    checks: list[float] = []
    revoked = threading.Event()

    def gate(scope, provenance):
        checks.append(time.monotonic())
        return None if revoked.is_set() else person_window(scope, provenance)

    service = Service()
    service.snapshot = lambda conversation: {"conversation_id": conversation, "server_epoch": service.server_epoch,
        "projection_revision": "0", "cursor": "0", "checkpoint_revision": "", "rows": [], "generation": None}
    service.events_since = lambda *_: {"server_epoch": service.server_epoch, "snapshot_required": False, "events": []}
    app = create_client_platform_app(service, security=ClientSecurity("fixture"),
                                     choices=lambda: {"models": [], "capabilities": []}, session_authenticator=gate)
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 1234)) as client:
        _, headers = bootstrap(client)
        sub = client.post("/api/v1/conversations/idle/subscriptions", headers=headers).json()

        async def stream() -> tuple[float, float]:
            async def receive():
                await asyncio.sleep(3600)

            async def send(message):
                pass

            scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
                     "http_version": "1.1", "method": "GET", "scheme": "http", "path": "/api/v1/events",
                     "raw_path": b"/api/v1/events", "root_path": "",
                     "query_string": urlencode({"subscription_id": sub["subscription_id"],
                                                "cursor": sub["cursor"]}).encode(),
                     "headers": [(b"host", b"localhost"), *((key.lower().encode(), value.encode())
                                                            for key, value in headers.items())],
                     "client": ("127.0.0.1", 1234), "server": ("localhost", 80)}

            async def revoke_later() -> float:
                await asyncio.sleep(1.0)
                revoked.set()
                return time.monotonic()

            revoking = asyncio.create_task(revoke_later())
            await asyncio.wait_for(app(scope, receive, send), timeout=5)
            return await revoking, time.monotonic()

        before = len(checks)
        opened = time.monotonic()
        revoked_at, ended_at = asyncio.run(stream())
        idle = [at for at in checks[before:] if opened <= at < revoked_at]

    # Opening it checks once, then about every half second: before, two checks every quarter second (8).
    assert len(idle) <= 4, f"{len(idle)} checks in 1 s of an idle stream"
    assert ended_at - revoked_at < 1.0, "a revoked session's stream ends within a second"
