"""Google's, X's and OpenRouter's sign-in callbacks reach their route without a session.

A sign-in started on another device returns through Row-Bot's public address
in whatever browser the provider sends back: no Row-Bot session, no Origin.
The access gate leaves exactly these two GET routes (as it does MCP's) to their
handler, which accepts only a waiting sign-in's one-time state. Every other
account route still needs a session, and the access log never records a
callback's code.
"""
from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from row_bot.access.config import AccessConfig
from row_bot.api.v1.routes import create_client_platform_app


class NoSessions:
    def authenticate_scope(self, scope, provenance):  # noqa: ARG002
        return None


class Service:
    instance_id = "fixture-instance"
    server_epoch = "fixture-epoch"


def _server() -> TestClient:
    app = create_client_platform_app(
        Service(),
        access_config=AccessConfig.build(
            deployment_mode="server",
            allowed_hosts=("localhost",),
            public_origins=("https://rowbot.example.test",),
        ),
        session_authenticator=NoSessions(),
        choices=lambda: {"models": [], "capabilities": [], "catalog_stale": True},
    )
    return TestClient(app, base_url="http://localhost:8080", client=("203.0.113.9", 51000),
                      follow_redirects=False)


@pytest.mark.parametrize("account", ["google", "x"])
def test_a_providers_callback_reaches_its_route_without_a_session(account) -> None:
    with _server() as client:
        response = client.get(f"/api/v1/accounts/{account}/auth/callback",
                              params={"state": "s" * 43, "code": "fixture-secret-code"})

    # The route itself answers (no sign-in waits for this state), not the gate's 401.
    assert response.status_code == 400
    assert response.headers["content-type"].startswith("text/plain")
    assert "isn't valid" in response.text and "fixture-secret-code" not in response.text
    assert response.headers["referrer-policy"] == "no-referrer"


def test_openrouters_callback_reaches_its_route_without_a_session() -> None:
    with _server() as client:
        response = client.get("/api/v1/settings/providers/openrouter/sign-in/callback",
                              params={"state": "s" * 43, "code": "fixture-secret-code"})

    assert response.status_code == 400
    assert "isn't valid" in response.text and "fixture-secret-code" not in response.text
    assert response.headers["referrer-policy"] == "no-referrer"


def test_starting_or_reading_an_openrouter_sign_in_needs_a_session() -> None:
    flow = "00000000-0000-4000-8000-000000000000"
    with _server() as client:
        assert client.post("/api/v1/settings/providers/openrouter/sign-in", json={}).status_code == 401
        assert client.get(f"/api/v1/settings/providers/openrouter/sign-in/{flow}").status_code == 401
        assert client.post("/api/v1/settings/providers/openrouter/sign-in/callback").status_code == 401


def test_every_other_account_route_still_needs_a_session() -> None:
    with _server() as client:
        assert client.get("/api/v1/accounts/google/auth").status_code == 401
        assert client.get("/api/v1/accounts/x/auth/commands/00000000-0000-4000-8000-000000000000").status_code == 401
        assert client.post("/api/v1/accounts/google/auth/callback").status_code == 401
        assert client.get("/api/v1/accounts/github/auth/callback").status_code == 401
        assert client.get("/api/v1/accounts/google/auth/callback/more").status_code == 401


def test_the_access_log_never_records_a_callbacks_code(caplog) -> None:
    with _server() as client:
        client.get("/healthz")  # The access gate is in place.
        caplog.set_level(logging.INFO, logger="uvicorn.access")
        logging.getLogger("uvicorn.access").info(
            '%s - "%s %s HTTP/%s" %d', "203.0.113.9:51000", "GET",
            "/api/v1/accounts/x/auth/callback?state=fixture-state&code=fixture-secret-code", "1.1", 400,
        )

    assert "GET /api/v1/accounts/x/auth/callback HTTP/1.1" in caplog.text
    assert "fixture-secret-code" not in caplog.text and "fixture-state" not in caplog.text
