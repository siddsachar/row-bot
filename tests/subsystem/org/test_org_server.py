"""The Org server's HTTP surface: health, readiness, security headers, and nothing else by default."""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from row_bot_org.app import SECURITY_HEADERS, create_app
from row_bot_org.settings import OrgSettings


def _client(**settings: object) -> TestClient:
    return TestClient(create_app(OrgSettings(**settings)))


def _assert_security_headers(response) -> None:
    for name, value in SECURITY_HEADERS.items():
        assert response.headers.get(name) == value, name


def test_healthz_answers_ok_with_security_headers() -> None:
    response = _client().get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    _assert_security_headers(response)


def test_readyz_is_ready_once_settings_loaded() -> None:
    response = _client().get("/readyz")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "checks": {}}
    _assert_security_headers(response)


def test_readyz_reports_a_failing_check_without_its_error(caplog: pytest.LogCaptureFixture) -> None:
    app = create_app(OrgSettings())

    async def passing() -> bool:
        return True

    async def broken() -> bool:
        raise ConnectionError("postgresql://org:hunter2@db/org refused")

    app.state.readiness.add("cache", passing)
    app.state.readiness.add("database", broken)
    with caplog.at_level(logging.WARNING, logger="row_bot_org.health"):
        response = TestClient(app).get("/readyz")

    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "checks": {"cache": "ok", "database": "failing"}}
    _assert_security_headers(response)
    assert "database" in caplog.text and "ConnectionError" in caplog.text
    assert "hunter2" not in caplog.text and "hunter2" not in response.text


def test_a_readiness_check_name_is_registered_once() -> None:
    app = create_app(OrgSettings())

    async def check() -> bool:
        return True

    app.state.readiness.add("database", check)
    with pytest.raises(ValueError, match="already registered"):
        app.state.readiness.add("database", check)


@pytest.mark.parametrize("path", ["/", "/brain", "/admin", "/docs", "/redoc", "/openapi.json", "/healthz/extra"])
def test_every_other_path_needs_a_session_and_answers_json_with_security_headers(path: str) -> None:
    response = _client().get(path)

    assert response.status_code == 401
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {"error": "sign_in_required"}
    _assert_security_headers(response)


def test_a_wrong_method_on_an_open_path_is_a_json_405_with_security_headers() -> None:
    response = _client().options("/healthz")

    assert response.status_code == 405
    assert response.headers["content-type"] == "application/json"
    _assert_security_headers(response)


def test_the_openapi_setting_publishes_the_schema_but_never_a_docs_page() -> None:
    client = _client(openapi=True)

    schema = client.get("/openapi.json")
    assert schema.status_code == 200
    assert set(schema.json()["paths"]) == {"/healthz", "/readyz"}
    _assert_security_headers(schema)
    # No docs page: like any route that isn't there, behind the gate.
    assert client.get("/docs").status_code == 401
    assert client.get("/redoc").status_code == 401
