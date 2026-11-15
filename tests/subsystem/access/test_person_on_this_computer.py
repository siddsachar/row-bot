"""A0: on this computer only a window or browser a person signed in may change anything.

A program Row-Bot starts (a shell command, its own browser, a page reader) connects from
this computer too. Being on this computer used to be enough for the owner's full
authority, so such a program could approve Row-Bot's own actions. Now it can only look.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from row_bot.access.config import AccessConfig, DeploymentMode
from row_bot.access.middleware import AccessMiddleware
from row_bot.access.policy import AccessPolicy
from row_bot.access.request_context import AccessContext, AuthenticationKind, PresentationMode
from row_bot.access.routes import register_access_routes
from row_bot.access.service import AccessService
from row_bot.access.store import AccessStore
from tests.helpers.access import sign_in_this_computer

ORIGIN = "http://localhost:8080"
INVITE = {"layout": "desktop", "session_lifetime": "trusted", "origin": ORIGIN}


def _desktop(tmp_path, *, peer: str = "127.0.0.1"):
    config = AccessConfig.build(deployment_mode="desktop", allowed_hosts=("localhost",))
    service = AccessService(AccessStore(tmp_path / "mobile.db"))
    app = FastAPI()
    registration = register_access_routes(app, service=service, config=config)
    app.add_middleware(AccessMiddleware, config=config, session_authenticator=registration.authenticator)
    client = TestClient(app, base_url=ORIGIN, client=(peer, 51000), follow_redirects=False)
    return client, service, registration


def test_a_bare_connection_on_this_computer_can_look_but_not_change_anything(tmp_path) -> None:
    client, _service, _registration = _desktop(tmp_path)

    assert client.get("/api/access/devices").status_code == 200
    refused = client.post("/api/access/invitations", json=INVITE, headers={"origin": ORIGIN})

    assert refused.status_code == 403
    assert refused.json()["error"] == "person_required"
    assert client.get("/api/access/session").json()["may_act"] is False


def test_a_bare_window_is_told_it_isnt_signed_in_not_that_it_isnt_allowed(tmp_path) -> None:
    """The client says "This window isn't signed in, so it can only look", with how to sign in (5.3.1)."""
    client, _service, _registration = _desktop(tmp_path)
    client.app.add_api_route("/api/v1/settings/fixture", lambda: {"ok": True}, methods=["POST"])

    refused = client.post("/api/v1/settings/fixture", json={}, headers={"origin": ORIGIN})

    assert refused.status_code == 403
    assert refused.json()["code"] == "sign_in_required"


def test_a_window_signed_in_by_the_launcher_may_act_and_renews_like_a_device(tmp_path) -> None:
    client, service, registration = _desktop(tmp_path)
    session_id = sign_in_this_computer(client, service, registration, origin=ORIGIN)

    created = client.post("/api/access/invitations", json=INVITE, headers={"origin": ORIGIN})
    current = client.get("/api/access/session").json()

    assert created.status_code == 201
    assert current["authentication_kind"] == "local_owner"
    assert current["may_act"] is True
    assert current["session_id"] == session_id
    renewed = client.post("/api/access/session/refresh", headers={"origin": ORIGIN})
    assert renewed.status_code == 200


def test_a_forged_or_revoked_sign_in_is_only_a_bare_connection(tmp_path) -> None:
    client, service, registration = _desktop(tmp_path)
    session_id = sign_in_this_computer(client, service, registration, origin=ORIGIN)
    service.revoke_session(session_id)

    assert client.post("/api/access/invitations", json=INVITE, headers={"origin": ORIGIN}).status_code == 403
    client.cookies.set(registration.cookies.names.http, "rbs_v1.forged.secret")
    assert client.post("/api/access/invitations", json=INVITE, headers={"origin": ORIGIN}).status_code == 403


def test_an_invitation_is_claimed_once(tmp_path) -> None:
    client, service, registration = _desktop(tmp_path)
    created = service.create_invitation(intended_origin=ORIGIN)
    first = client.post(
        "/api/access/invitations/claim",
        json={"invitation": created.token, "display_name": "Row-Bot window"},
        headers={"origin": ORIGIN, "accept": "application/json"},
    )
    other = TestClient(client.app, base_url=ORIGIN, client=("127.0.0.1", 51001))
    second = other.post(
        "/api/access/invitations/claim",
        json={"invitation": created.token, "display_name": "Program"},
        headers={"origin": ORIGIN, "accept": "application/json"},
    )

    assert first.status_code == 200
    assert second.status_code == 409
    assert other.get("/api/access/session").json()["may_act"] is False


def test_a_paired_device_elsewhere_keeps_the_owners_full_authority(tmp_path) -> None:
    host, service, registration = _desktop(tmp_path)
    created = service.create_invitation(intended_origin=ORIGIN)
    claimed = service.claim_invitation(created.token, intended_origin=ORIGIN, display_name="Phone")
    phone = TestClient(host.app, base_url=ORIGIN, client=("100.101.102.103", 52000), follow_redirects=False)
    phone.cookies.set(registration.cookies.names.http, claimed.session_token)

    response = phone.post("/api/access/invitations", json=INVITE, headers={"origin": ORIGIN})

    assert response.status_code == 201
    assert phone.get("/api/access/session").json()["may_act"] is True


def _context(kind: AuthenticationKind, *, person: bool, mode: DeploymentMode = DeploymentMode.DESKTOP):
    return AccessContext(
        deployment_mode=mode,
        authentication_kind=kind,
        transport_peer="127.0.0.1",
        effective_client="127.0.0.1",
        forwarding_headers=(),
        trusted_proxy=False,
        trusted_proxy_peer=None,
        proxy_chain=(),
        scheme="http",
        host="localhost:8080",
        origin=ORIGIN,
        presentation=PresentationMode.DESKTOP,
        direct_loopback=True,
        person=person,
    )


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/v1/approvals/approval-1/commands"),
        ("POST", "/api/v1/conversations/c1/commands"),
        ("POST", "/api/v1/settings/mcp/commands"),
        ("PUT", "/api/v1/uploads/u1/chunks"),
        ("POST", "/api/v1/native/terminals/t1/input"),
        ("POST", "/api/mobile/pair/start"),
    ],
)
def test_every_change_needs_a_person_on_this_computer(method: str, path: str) -> None:
    policy = AccessPolicy()
    route = policy.classify({"type": "http", "method": method, "path": path, "headers": []})

    bare = policy.authorize(_context(AuthenticationKind.LOCAL_OWNER, person=False), route)
    signed_in = policy.authorize(_context(AuthenticationKind.LOCAL_OWNER, person=True), route)

    assert (bare.allowed, bare.reason) == (False, "person_required")
    assert signed_in.allowed


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/v1/conversations"),
        ("POST", "/api/v1/handshake"),
        ("POST", "/api/v1/conversations/c1/subscriptions"),
        ("PUT", "/api/v1/subscriptions/s1/ack"),
        ("DELETE", "/api/v1/subscriptions/s1"),
        ("POST", "/api/v1/native/attest"),
    ],
)
def test_looking_and_the_native_windows_token_checked_routes_need_no_sign_in(method: str, path: str) -> None:
    policy = AccessPolicy()
    route = policy.classify({"type": "http", "method": method, "path": path, "headers": []})

    assert policy.authorize(_context(AuthenticationKind.LOCAL_OWNER, person=False), route).allowed


def test_a_signed_in_window_is_checked_quickly_and_a_revoked_one_at_once(tmp_path, monkeypatch) -> None:
    """A window makes many requests a second; each paid 200,000 PBKDF2 rounds and a database write.
    The slow check now runs once per token, and revoking still takes effect on the next request."""
    from row_bot.access import service as access_service

    client, service, registration = _desktop(tmp_path)
    session_id = sign_in_this_computer(client, service, registration, origin=ORIGIN)
    checks: list[str] = []
    real = access_service.verify_secret
    monkeypatch.setattr(access_service, "verify_secret", lambda *a, **k: checks.append("slow") or real(*a, **k))

    for _ in range(5):
        assert client.get("/api/access/session").json()["may_act"] is True
    assert checks == ["slow"]

    service.revoke_session(session_id)
    assert client.get("/api/access/session").json()["may_act"] is False
