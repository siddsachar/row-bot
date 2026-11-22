"""The gate in front of every route, and the access log, without a database or sign-in configured.

Security-sensitive: deny by default and the same-origin rule run in the pull request lane.
"""

from __future__ import annotations

import io
import json
import logging
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from row_bot_org.app import create_app
from row_bot_org.logging import configure_logging
from row_bot_org.settings import load_settings

PUBLIC_URL = "https://org.example.test"


@pytest.fixture
def client() -> Iterator[TestClient]:
    settings = load_settings({"ROW_BOT_ORG_PUBLIC_URL": PUBLIC_URL})
    with TestClient(create_app(settings), base_url=PUBLIC_URL) as test_client:
        yield test_client


@pytest.fixture
def access_log() -> Iterator[io.StringIO]:
    root = logging.getLogger()
    saved = (root.handlers[:], root.level)
    stream = io.StringIO()
    configure_logging("INFO", stream)
    yield stream
    root.handlers[:], level = saved
    root.setLevel(level)


@pytest.mark.parametrize("path", ["/api/me", "/no/such/route", "/auth/../api/me", "/", "/docs"])
def test_without_a_session_every_route_but_health_is_refused(client: TestClient, path: str) -> None:
    response = client.get(path)

    assert response.status_code == 401
    assert response.json() == {"error": "sign_in_required"}
    assert response.headers["cache-control"] == "no-store"


def test_health_and_readiness_stay_open(client: TestClient) -> None:
    assert client.get("/healthz").status_code == 200
    assert client.get("/readyz").status_code == 200


def test_a_session_cookie_the_server_didnt_issue_is_refused(client: TestClient) -> None:
    client.cookies.set("__Host-row_bot_org_session", "rbos_" + "A" * 43)
    assert client.get("/api/me").status_code == 401


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Origin": "https://evil.example.test"},
        {"Origin": "null"},
        {"Sec-Fetch-Site": "cross-site"},
        {"Sec-Fetch-Site": "same-site"},
        {"Origin": PUBLIC_URL, "Sec-Fetch-Site": "cross-site"},
        {"Origin": PUBLIC_URL + ":8443"},
    ],
)
def test_a_state_changing_request_from_another_origin_is_refused(client: TestClient, headers: dict) -> None:
    response = client.post("/auth/logout", headers=headers)

    assert response.status_code == 403
    assert response.json() == {"error": "same_origin_required"}


@pytest.mark.parametrize("headers", [{"Origin": PUBLIC_URL}, {"Sec-Fetch-Site": "same-origin"}])
def test_a_same_origin_request_gets_past_the_origin_rule_to_the_session_rule(client: TestClient,
                                                                            headers: dict) -> None:
    assert client.post("/auth/logout", headers=headers).status_code == 401


def test_without_a_public_url_nothing_may_change_state() -> None:
    with TestClient(create_app(load_settings({})), base_url=PUBLIC_URL) as client:
        assert client.post("/auth/logout", headers={"Origin": PUBLIC_URL}).status_code == 403


def test_the_access_log_has_the_route_template_and_never_the_query(client: TestClient,
                                                                   access_log: io.StringIO) -> None:
    # Sign-in isn't configured here, so the callback is no route (the sign-in tests log a real one).
    client.get("/auth/callback?code=secret-code-123&state=secret-state-456")
    client.get("/private/path-777?token=secret-token-888", headers={"Cookie": "__Host-row_bot_org_session=rbos_x"})
    client.get("/healthz?probe=secret-probe-999")

    records = [json.loads(line) for line in access_log.getvalue().splitlines()]
    requests = [record for record in records if record["logger"] == "row_bot_org.access"]
    assert [(r["method"], r["route"], r["status"], r["principal"]) for r in requests] == [
        ("GET", "unmatched", 404, None),
        ("GET", "unmatched", 401, None),
        ("GET", "/healthz", 200, None),
    ]
    assert all(isinstance(r["duration_ms"], float) for r in requests)
    text = access_log.getvalue()
    for secret in ("secret-code-123", "secret-state-456", "path-777", "secret-token-888", "rbos_x",
                   "secret-probe-999", "?"):
        assert secret not in text
