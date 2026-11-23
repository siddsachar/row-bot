"""The gate in front of every route, the sign-in steps' per-address limit, and the access log, without a
database or sign-in configured.

Security-sensitive: deny by default and the same-origin rule run in the pull request lane.
"""

from __future__ import annotations

import io
import json
import logging
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from row_bot_org.app import DEVICE_CSP, create_app
from row_bot_org.identity.limits import MAX_ADDRESSES, WINDOW_SECONDS, AddressRateLimit
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


BEARER = {"Authorization": "Bearer rboa_" + "A" * 43}


@pytest.mark.parametrize("authorization", ["Bearer rboa_" + "A" * 43, "Bearer", "bearer rboa_x",
                                           "Bearer two tokens"])
def test_a_bearer_that_isnt_live_is_refused_with_a_challenge(client: TestClient, authorization: str) -> None:
    response = client.get("/api/me", headers={"Authorization": authorization})

    assert response.status_code == 401
    assert response.json() == {"error": "invalid_token"}
    assert response.headers["www-authenticate"] == 'Bearer error="invalid_token"'


def test_a_bearer_request_is_outside_the_same_origin_rule(client: TestClient) -> None:
    # No Origin, no Sec-Fetch-Site: refused for the token, never for the origin.
    response = client.post("/api/audit/events", headers=BEARER, json={"events": []})
    assert (response.status_code, response.json()) == (401, {"error": "invalid_token"})
    # Without a bearer, the same request is refused for its origin first.
    assert client.post("/api/audit/events", json={"events": []}).json() == {"error": "same_origin_required"}


def test_a_session_cookie_and_a_bearer_together_are_refused(client: TestClient) -> None:
    client.cookies.set("__Host-row_bot_org_session", "rbos_" + "A" * 43)
    response = client.get("/api/me", headers=BEARER)

    assert (response.status_code, response.json()) == (400, {"error": "invalid_request"})


@pytest.mark.parametrize("authorization", ["Basic dXNlcjpwYXNz", "Negotiate YIIBhwYGKwYBBQUCoIIBezCC"])
def test_another_authorization_scheme_is_ignored_and_the_cookie_decides(client: TestClient,
                                                                       authorization: str) -> None:
    # A proxy in front (IIS, F5) may add one; it is neither a bearer nor refused with the cookie.
    client.cookies.set("__Host-row_bot_org_session", "rbos_" + "A" * 43)
    response = client.get("/api/me", headers={"Authorization": authorization})

    assert (response.status_code, response.json()) == (401, {"error": "sign_in_required"})


@pytest.mark.parametrize(("method", "path"), [("GET", "/api/admin/people"), ("POST", "/api/admin/devices/x/revoke"),
                                              ("GET", "/api/admin"),
                                              ("GET", "/console/"), ("GET", "/admin/"), ("POST", "/auth/logout"),
                                              ("POST", "/auth/device/confirm"), ("GET", "/no/such/route")])
def test_a_bearer_is_never_taken_outside_the_persons_api(client: TestClient, method: str, path: str) -> None:
    response = client.request(method, path, headers=BEARER)

    assert (response.status_code, response.json()) == (403, {"error": "browser_session_required"})


@pytest.mark.parametrize("path", ["/auth/device", "/auth/device/confirm", "/auth/device.css"])
def test_the_device_pages_send_their_origin_with_their_form_and_load_only_their_stylesheet(client: TestClient,
                                                                                          path: str) -> None:
    # Under no-referrer a browser posts the confirm form with "Origin: null", which the origin rule refuses.
    response = client.get(path)

    assert response.headers["referrer-policy"] == "same-origin"
    assert response.headers["content-security-policy"] == DEVICE_CSP
    assert "[::1]" not in DEVICE_CSP and "form-action 'self' http://127.0.0.1:*;" in DEVICE_CSP
    assert client.get("/api/me").headers["referrer-policy"] == "no-referrer"


def test_the_token_limit_counts_only_what_it_is_told_to() -> None:
    limit = AddressRateLimit(2)
    assert [limit.spent("198.51.100.7") for _ in range(5)] == [False] * 5  # Asking counts nothing.
    limit.allow("198.51.100.7")
    limit.allow("198.51.100.7")
    assert limit.spent("198.51.100.7") is True and limit.spent("198.51.100.8") is False


def test_the_device_token_endpoint_is_open_and_outside_the_same_origin_rule(client: TestClient) -> None:
    # Sign-in isn't configured here, so there is no such route: past the gate, a plain 404.
    assert client.post("/api/device/token", json={"grant_type": "refresh_token"}).status_code == 404


def test_without_a_public_url_nothing_may_change_state() -> None:
    with TestClient(create_app(load_settings({})), base_url=PUBLIC_URL) as client:
        assert client.post("/auth/logout", headers={"Origin": PUBLIC_URL}).status_code == 403


def test_the_sign_in_limit_counts_per_address_and_starts_again_each_minute() -> None:
    now = [0.0]
    limit = AddressRateLimit(2, clock=lambda: now[0])

    assert [limit.allow("198.51.100.7") for _ in range(3)] == [True, True, False]
    assert limit.allow("198.51.100.8") is True
    # An IPv6 address counts by its /64; an IPv4-mapped one as its IPv4 address.
    assert [limit.allow(f"2001:db8::{n}") for n in (1, 2, 3)] == [True, True, False]
    assert limit.allow("::ffff:198.51.100.7") is False
    now[0] += WINDOW_SECONDS
    assert limit.allow("198.51.100.7") is True


def test_the_sign_in_limit_tracks_a_bounded_number_of_addresses() -> None:
    limit = AddressRateLimit(1)
    for n in range(MAX_ADDRESSES):
        assert limit.allow(f"10.{n >> 16 & 255}.{n >> 8 & 255}.{n & 255}")
    # Past the cap, new addresses share one count.
    assert [limit.allow("192.0.2.1"), limit.allow("192.0.2.2")] == [True, False]


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
