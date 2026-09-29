from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
import sqlite3

from fastapi import FastAPI
from starlette.testclient import TestClient

from row_bot.access.config import AccessConfig
from row_bot.access.cookies import LEGACY_HTTP_COOKIE_NAME
from row_bot.access.middleware import AccessMiddleware
from row_bot.access.models import (
    AccessDevice,
    AccessSession,
    SessionLifetime,
    TokenFormat,
)
from row_bot.access.routes import register_access_routes
from row_bot.access.service import AccessService
from row_bot.access.store import AccessStore
from row_bot.access.tokens import hash_secret


def _application(tmp_path, *, port: int = 8080):
    config = AccessConfig.build(
        deployment_mode="server",
        allowed_hosts=("localhost",),
    )
    service = AccessService(AccessStore(tmp_path / f"mobile-{port}.db"))
    app = FastAPI()
    registration = register_access_routes(app, service=service, config=config)
    app.add_middleware(
        AccessMiddleware,
        config=config,
        session_authenticator=registration.authenticator,
    )
    client = TestClient(
        app,
        base_url=f"http://localhost:{port}",
        client=("127.0.0.1", 51000),
        follow_redirects=False,
    )
    return client, service, registration


def _claim_owner(client: TestClient, service: AccessService, *, port: int = 8080):
    created = service.create_invitation(
        intended_origin=f"http://localhost:{port}",
    )
    response = client.post(
        "/api/access/invitations/claim",
        json={"invitation": created.token, "display_name": "Owner browser"},
        headers={"origin": f"http://localhost:{port}"},
    )
    assert response.status_code == 200
    return response


def _set_session_expiry(
    service: AccessService,
    session_id: str,
    expires_at: datetime,
) -> None:
    with sqlite3.connect(service.store.db_path) as connection:
        connection.execute(
            "UPDATE access_sessions SET expires_at = ? WHERE id = ?",
            (expires_at.isoformat(), session_id),
        )


def test_session_status_persists_across_refreshes(tmp_path) -> None:
    client, service, _registration = _application(tmp_path)
    claim = _claim_owner(client, service)

    first = client.get("/api/access/session")
    second = client.get("/api/access/session")

    assert claim.status_code == 200
    assert first.json()["authenticated"] is True
    assert "profile" not in first.json()
    assert first.json()["session_id"]
    assert second.json()["session_id"] == first.json()["session_id"]


def test_server_expiry_is_authoritative_even_if_cookie_survives(tmp_path) -> None:
    client, service, registration = _application(tmp_path)
    old = datetime(2020, 1, 1, tzinfo=timezone.utc)
    created = service.create_invitation(
        intended_origin="http://localhost:8080",
        now=old,
    )
    claim = service.claim_invitation(
        created.token,
        intended_origin="http://localhost:8080",
        display_name="Expired browser",
        now=old,
    )
    cookie = f"{registration.cookies.names.http}={claim.session_token}"

    response = client.get(
        "/api/access/session",
        headers={"cookie": cookie},
    )

    assert response.status_code == 200
    assert response.json()["authenticated"] is False


def test_logout_revokes_session_and_clears_current_and_legacy_cookies(
    tmp_path,
) -> None:
    client, service, registration = _application(tmp_path)
    claim = _claim_owner(client, service)
    session_id = claim.json()["session"]["id"]

    response = client.post(
        "/api/access/logout",
        headers={"origin": "http://localhost:8080"},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "revoked": True}
    assert service.store.get_session(session_id).revoked_at is not None
    set_cookies = response.headers.get_list("set-cookie")
    assert len(set_cookies) == 4
    assert any(registration.cookies.names.http in value for value in set_cookies)
    assert any("__Host-row_bot_mobile" in value for value in set_cookies)
    assert any("row_bot_mobile_lan" in value for value in set_cookies)
    assert client.get("/api/access/session").json()["authenticated"] is False


def test_revoked_session_cookie_cannot_be_reused(tmp_path) -> None:
    client, service, registration = _application(tmp_path)
    claim_response = _claim_owner(client, service)
    token = claim_response.cookies.get(registration.cookies.names.http)
    session_id = claim_response.json()["session"]["id"]
    assert token

    service.revoke_session(session_id)
    response = client.get(
        "/api/access/session",
        headers={"cookie": f"{registration.cookies.names.http}={token}"},
    )

    assert response.json()["authenticated"] is False


def test_two_instances_on_one_hostname_keep_sessions_independent(tmp_path) -> None:
    first, first_service, first_registration = _application(
        tmp_path / "first",
        port=8081,
    )
    second, second_service, second_registration = _application(
        tmp_path / "second",
        port=8082,
    )
    first_claim = _claim_owner(first, first_service, port=8081)
    second_claim = _claim_owner(second, second_service, port=8082)
    first_token = first_claim.cookies.get(first_registration.cookies.names.http)
    second_token = second_claim.cookies.get(second_registration.cookies.names.http)
    assert first_token and second_token
    assert first_registration.cookies.names != second_registration.cookies.names
    combined = (
        f"{first_registration.cookies.names.http}={first_token}; "
        f"{second_registration.cookies.names.http}={second_token}"
    )

    first_status = first.get(
        "/api/access/session",
        headers={"cookie": combined},
    )
    second_status = second.get(
        "/api/access/session",
        headers={"cookie": combined},
    )

    assert first_status.json()["authenticated"] is True
    assert second_status.json()["authenticated"] is True
    assert first_status.json()["session_id"] != second_status.json()["session_id"]


def test_session_status_never_returns_raw_cookie_or_token(tmp_path) -> None:
    client, service, registration = _application(tmp_path)
    claim = _claim_owner(client, service)
    token = claim.cookies.get(registration.cookies.names.http)
    assert token

    response = client.get("/api/access/session")

    assert token not in response.text
    assert "token_hash" not in response.text
    assert "token_salt" not in response.text
    assert response.headers["cache-control"] == "no-store"


def test_refresh_requires_authentication_and_exact_origin(tmp_path) -> None:
    client, service, _registration = _application(tmp_path)
    unauthenticated = client.post(
        "/api/access/session/refresh",
        headers={"origin": "http://localhost:8080"},
    )
    _claim_owner(client, service)

    missing_origin = client.post("/api/access/session/refresh")
    wrong_origin = client.post(
        "/api/access/session/refresh",
        headers={"origin": "https://attacker.example"},
    )
    exact_origin = client.post(
        "/api/access/session/refresh",
        headers={"origin": "http://localhost:8080"},
    )

    assert unauthenticated.status_code == 401
    assert missing_origin.status_code == 403
    assert wrong_origin.status_code == 403
    assert exact_origin.status_code == 200
    assert exact_origin.json()["authenticated"] is True


def test_due_trusted_refresh_extends_server_and_cookie_expiry(tmp_path) -> None:
    client, service, registration = _application(tmp_path)
    claim = _claim_owner(client, service)
    token = claim.cookies.get(registration.cookies.names.http)
    session_id = claim.json()["session"]["id"]
    assert token
    _set_session_expiry(
        service,
        session_id,
        datetime.now(timezone.utc) + timedelta(days=1),
    )

    response = client.post(
        "/api/access/session/refresh",
        headers={"origin": "http://localhost:8080"},
    )

    assert response.status_code == 200
    assert response.json()["renewed"] is True
    assert response.json()["lifetime"] == "trusted"
    assert set(response.json()) == {
        "ok",
        "authenticated",
        "renewed",
        "lifetime",
        "expires_at",
    }
    set_cookie = response.headers["set-cookie"]
    assert set_cookie.startswith(f"{registration.cookies.names.http}={token};")
    max_age = re.search(r"Max-Age=(\d+)", set_cookie)
    assert max_age is not None
    assert 30 * 24 * 60 * 60 - 5 <= int(max_age.group(1)) <= 30 * 24 * 60 * 60
    assert token not in response.text
    assert "token_hash" not in response.text
    assert "token_salt" not in response.text
    assert service.validate_session(token, touch=False) is not None


def test_early_and_temporary_refreshes_do_not_reissue_cookie(tmp_path) -> None:
    client, service, _registration = _application(tmp_path)
    _claim_owner(client, service)

    early = client.post(
        "/api/access/session/refresh",
        headers={"origin": "http://localhost:8080"},
    )

    assert early.status_code == 200
    assert early.json()["renewed"] is False
    assert "set-cookie" not in early.headers

    temporary_client, temporary_service, _temporary_registration = _application(
        tmp_path / "temporary",
        port=8081,
    )
    created = temporary_service.create_invitation(
        intended_origin="http://localhost:8081",
        session_lifetime=SessionLifetime.TEMPORARY,
    )
    claim = temporary_client.post(
        "/api/access/invitations/claim",
        json={"invitation": created.token, "display_name": "Temporary browser"},
        headers={"origin": "http://localhost:8081"},
    )
    assert claim.status_code == 200

    temporary = temporary_client.post(
        "/api/access/session/refresh",
        headers={"origin": "http://localhost:8081"},
    )

    assert temporary.status_code == 200
    assert temporary.json()["lifetime"] == "temporary"
    assert temporary.json()["renewed"] is False
    assert "set-cookie" not in temporary.headers


def test_migrated_cookie_refresh_is_a_non_secret_noop(tmp_path) -> None:
    client, service, _registration = _application(tmp_path)
    now = datetime.now(timezone.utc)
    device_id = "a" * 32
    secret = "reviewed-legacy-session-secret"
    verifier = hash_secret(secret)
    service.store.create_device_record(
        AccessDevice(
            id=device_id,
            display_name="Migrated browser",
            created_at=now,
            last_seen_at=now,
            revoked_at=None,
            user_agent=None,
            paired_from=None,
            access_route=None,
            legacy_source_id=device_id,
        )
    )
    service.store.create_session_record(
        AccessSession(
            id="migrated-session",
            device_id=device_id,
            token_hash=verifier.secret_hash,
            token_salt=verifier.salt,
            token_format=TokenFormat.LEGACY_RBD,
            created_at=now,
            last_seen_at=now,
            expires_at=now + timedelta(days=1),
            revoked_at=None,
            lifetime=SessionLifetime.MIGRATED,
            replaced_by_session_id=None,
        )
    )
    raw_token = f"rbd_{device_id}.{secret}"

    response = client.post(
        "/api/access/session/refresh",
        headers={
            "origin": "http://localhost:8080",
            "cookie": f"{LEGACY_HTTP_COOKIE_NAME}={raw_token}",
        },
    )

    assert response.status_code == 200
    assert response.json()["lifetime"] == "migrated"
    assert response.json()["renewed"] is False
    assert "set-cookie" not in response.headers
    assert raw_token not in response.text


def _cookie_scope(header: str) -> tuple[str, frozenset[str]]:
    """A Set-Cookie's name and attributes, without its value or lifetime."""
    name = header.split("=", 1)[0]
    attributes = frozenset(
        part.strip().lower()
        for part in header.split(";")[1:]
        if not part.strip().lower().startswith(("max-age=", "expires="))
    )
    return name, attributes


def test_refresh_renews_only_the_calling_session_and_keeps_its_scope(
    tmp_path,
) -> None:
    """B137: the React client renews its own trusted session, nothing more."""
    client, service, registration = _application(tmp_path)
    claim = _claim_owner(client, service)
    session_id = claim.json()["session"]["id"]
    device_id = claim.json()["device"]["id"]
    other_invitation = service.create_invitation(
        intended_origin="http://localhost:8080"
    )
    other = service.claim_invitation(
        other_invitation.token,
        intended_origin="http://localhost:8080",
        display_name="Other browser",
    )
    due = datetime.now(timezone.utc) + timedelta(days=1)
    for renewable in (session_id, other.session.id):
        _set_session_expiry(service, renewable, due)
    before = service.store.get_session(session_id)

    response = client.post(
        "/api/access/session/refresh",
        headers={"origin": "http://localhost:8080"},
    )

    assert response.status_code == 200
    assert response.json()["renewed"] is True
    after = service.store.get_session(session_id)
    assert after.device_id == device_id
    assert after.lifetime is SessionLifetime.TRUSTED
    assert (after.token_hash, after.token_salt) == (
        before.token_hash,
        before.token_salt,
    )
    assert after.expires_at > due
    # Another device's session, due as well, is untouched.
    assert service.store.get_session(other.session.id).expires_at == due
    # The cookie keeps its name, path and flags; only its lifetime moves.
    assert _cookie_scope(response.headers["set-cookie"]) == _cookie_scope(
        claim.headers["set-cookie"]
    )
    assert client.get("/api/access/session").json()["session_id"] == session_id


def test_refresh_never_renews_a_revoked_or_expired_session(tmp_path) -> None:
    """B137: renewal never revives a session that has ended."""
    for case in ("revoked session", "revoked device", "expired"):
        client, service, registration = _application(tmp_path / case.replace(" ", "-"))
        claim = _claim_owner(client, service)
        token = claim.cookies.get(registration.cookies.names.http)
        session_id = claim.json()["session"]["id"]
        now = datetime.now(timezone.utc)
        if case == "revoked session":
            _set_session_expiry(service, session_id, now + timedelta(days=1))
            service.revoke_session(session_id)
        elif case == "revoked device":
            _set_session_expiry(service, session_id, now + timedelta(days=1))
            service.revoke_device(claim.json()["device"]["id"])
        else:
            _set_session_expiry(service, session_id, now - timedelta(seconds=1))
        before = service.store.get_session(session_id)

        response = client.post(
            "/api/access/session/refresh",
            headers={
                "origin": "http://localhost:8080",
                "cookie": f"{registration.cookies.names.http}={token}",
            },
        )

        assert response.status_code == 401, case
        assert response.json()["error"] == "authentication_required", case
        after = service.store.get_session(session_id)
        assert after.expires_at == before.expires_at, case
        assert after.revoked_at == before.revoked_at, case
        assert service.validate_session(token, touch=False) is None, case


def test_refresh_clears_cookies_when_session_expires_after_admission(
    tmp_path,
    monkeypatch,
) -> None:
    client, service, registration = _application(tmp_path)
    claim = _claim_owner(client, service)
    session_id = claim.json()["session"]["id"]
    original_refresh = service.refresh_trusted_session

    def expire_then_refresh(current_session_id: str, **kwargs):
        _set_session_expiry(
            service,
            current_session_id,
            datetime.now(timezone.utc) - timedelta(seconds=1),
        )
        return original_refresh(current_session_id, **kwargs)

    monkeypatch.setattr(service, "refresh_trusted_session", expire_then_refresh)

    response = client.post(
        "/api/access/session/refresh",
        headers={"origin": "http://localhost:8080"},
    )

    assert response.status_code == 401
    assert response.json()["error"] == "authentication_required"
    set_cookies = response.headers.get_list("set-cookie")
    assert len(set_cookies) == 4
    assert any(registration.cookies.names.http in value for value in set_cookies)
