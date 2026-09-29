"""HTTP routes and the React event stream share one session gate.

The event stream re-checks access on every tick, so a revoked session ends
an open stream within the tick's bound, as it did for the NiceGUI socket.
"""
from __future__ import annotations

import asyncio
import threading
import time

from fastapi import FastAPI, Request
from starlette.responses import StreamingResponse
from starlette.testclient import TestClient

from row_bot.access.config import AccessConfig
from row_bot.access.middleware import AccessMiddleware
from row_bot.access.request_context import SessionIdentity
from row_bot.api.v1 import routes as v1_routes
from row_bot.api.v1.security import ProtocolError


class FakeSessionAuthenticator:
    def __init__(self) -> None:
        self.active = {"owner-token": "owner-session"}

    def revoke(self, token: str) -> None:
        self.active.pop(token, None)

    def authenticate_scope(self, scope, provenance):  # noqa: ARG002
        headers = {
            bytes(name).lower(): bytes(value).decode("latin-1")
            for name, value in scope.get("headers", [])
        }
        cookie = headers.get(b"cookie", "")
        token = next(
            (
                item.split("=", 1)[1]
                for item in cookie.split(";")
                if item.strip().startswith("row_bot_test=")
            ),
            "",
        ).strip()
        result = self.active.get(token)
        if result is None:
            return None
        return SessionIdentity(
            device_id=f"{result}-device",
            session_id=result,
        )


def _build_app(authenticator: FakeSessionAuthenticator) -> TestClient:
    app = FastAPI()

    @app.get("/")
    async def root():
        return {"surface": "desktop"}

    @app.get("/api/private")
    async def private():
        return {"ok": True}

    @app.get("/api/access/devices")
    async def devices():
        return {"devices": []}

    @app.get("/api/v1/events")
    async def events(request: Request) -> StreamingResponse:
        # As the React event stream does: every tick re-checks access.
        async def stream():
            try:
                while True:
                    await v1_routes._context(request)
                    yield ": heartbeat\n\n"
                    await asyncio.sleep(0.02)
            except ProtocolError:
                return

        return StreamingResponse(stream(), media_type="text/event-stream")

    middleware = AccessMiddleware(
        app,
        config=AccessConfig.build(
            deployment_mode="server",
            allowed_hosts=("localhost",),
        ),
        session_authenticator=authenticator,
        websocket_revalidation_seconds=0.05,
    )
    return TestClient(
        middleware,
        base_url="http://localhost:8080",
        client=("127.0.0.1", 51000),
        follow_redirects=False,
    )


def test_server_http_and_the_event_stream_use_the_same_session_gate() -> None:
    authenticator = FakeSessionAuthenticator()
    client = _build_app(authenticator)
    cookie_header = {"cookie": "row_bot_test=owner-token"}
    stream_headers = {"accept": "text/event-stream", "origin": "http://localhost:8080"}

    assert client.get("/api/private").status_code == 401
    assert client.get("/api/private", headers=cookie_header).status_code == 200

    unpaired = client.get("/api/v1/events", headers=stream_headers)
    assert unpaired.status_code == 401
    assert unpaired.json()["code"] == "authentication_required"


def test_revocation_blocks_http_immediately_and_ends_an_open_stream_within_bound() -> None:
    authenticator = FakeSessionAuthenticator()
    client = _build_app(authenticator)
    cookie_header = {"cookie": "row_bot_test=owner-token"}
    revoked_at: list[float] = []

    def revoke_soon() -> None:
        time.sleep(0.2)
        authenticator.revoke("owner-token")
        revoked_at.append(time.monotonic())

    threading.Thread(target=revoke_soon, daemon=True).start()
    stream = client.get("/api/v1/events", headers={
        "accept": "text/event-stream", "origin": "http://localhost:8080", **cookie_header})
    ended_at = time.monotonic()

    assert stream.status_code == 200
    assert ": heartbeat" in stream.text  # it streamed while the session was valid
    assert revoked_at and ended_at - revoked_at[0] < 1.0
    assert client.get("/api/private", headers=cookie_header).status_code == 401


def test_phone_session_can_use_owner_management_routes() -> None:
    authenticator = FakeSessionAuthenticator()
    authenticator.active["phone-token"] = "phone-session"
    client = _build_app(authenticator)

    response = client.get(
        "/api/access/devices",
        headers={"cookie": "row_bot_test=phone-token"},
    )

    assert response.status_code == 200
    assert response.json() == {"devices": []}
