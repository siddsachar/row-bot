"""Device sign-in and device credentials end to end (C1a): the fake provider for the browser's sign-in, the
fake model endpoint for the gateway, the server's routes and a real (throwaway) Postgres as the runtime role.

Covers the whole join (sign in, ``/auth/device``, the confirm page, the code, the token, bearer calls to the
person's API and the gateway, refresh, leave), each refusal (redirect URIs that aren't loopback, ``plain``
PKCE, a wrong verifier, a reused or expired code, a reused refresh token, a cookie with a bearer, a bearer on
admin routes, a revoked device, a suspended person, a confirm from another origin, an ingested event naming
another device), row-level security on codes and tokens, the definer lookups, the per-address limit and the
logs. Security-sensitive: these run in the pull request lane's Org job.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import re
import secrets
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest

from tests.fixtures.postgres import import_psycopg

psycopg = import_psycopg()
pytest.importorskip("joserfc", reason="joserfc is not installed: uv sync --locked --all-extras --group test --group org")

from fastapi.testclient import TestClient  # noqa: E402

from row_bot.audit import Action, Actor, ActorKind, AuditEvent, Target, TargetKind, new_event_id, to_dict  # noqa: E402
from row_bot_org.app import DEVICE_CSP, create_app  # noqa: E402
from row_bot_org.db.pool import create_pool  # noqa: E402
from row_bot_org.db.rls import principal_transaction  # noqa: E402
from row_bot_org.identity.device import REQUEST_COOKIE  # noqa: E402
from row_bot_org.identity.tokens import pkce_challenge, verifier  # noqa: E402
from row_bot_org.logging import configure_logging  # noqa: E402
from row_bot_org.settings import load_settings  # noqa: E402
from tests.fixtures.model_endpoints import KEY, running_endpoint  # noqa: E402
from tests.fixtures.oidc_idp import CLIENT_SECRET, PUBLIC_URL, FakeIdp, running_idp  # noqa: E402
from tests.helpers.org_audit import stored_events, summary  # noqa: E402

pytestmark = pytest.mark.postgres

PEPPER = "a-test-pepper-that-is-long-enough-0123456789"
ORIGIN = {"Origin": PUBLIC_URL}
GROUP_MAP = {"IT Admins": "it-admins"}
GATEWAY_KEY = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")
REDIRECT = "http://127.0.0.1:53682/callback"
STATE = "app-state-0123"
CODE_VERIFIER = "v" * 20 + "-._~" + "0123456789" * 3  # 54 characters RFC 7636 allows
LAPTOP = {"device_name": "Ada's laptop", "platform": "windows", "app_version": "5.4.0"}
CHAT = "/api/gateway/openai/v1/chat/completions"
_FORM_TOKEN = re.compile(r'name="form_token" value="([^"]+)"')


@pytest.fixture
def idp() -> Iterator[FakeIdp]:
    with running_idp("okta") as running:
        yield running


@contextmanager
def org_server(database, idp: FakeIdp, **env: str) -> Iterator[TestClient]:
    settings = load_settings({
        "ROW_BOT_ORG_IDP": json.dumps(idp.config(group_map=GROUP_MAP, admin_groups=["it-admins"])),
        "ROW_BOT_ORG_IDP_CLIENT_SECRET": CLIENT_SECRET, "ROW_BOT_ORG_PUBLIC_URL": PUBLIC_URL,
        "ROW_BOT_ORG_HMAC_PEPPER": PEPPER, "ROW_BOT_ORG_GATEWAY_KEY": GATEWAY_KEY, **env})
    pool = create_pool(database.runtime_url, max_size=4)
    with TestClient(create_app(settings, pool), base_url=PUBLIC_URL) as client:
        yield client


def browser(server: TestClient, address: str = "testclient") -> TestClient:
    """A browser of its own (its own cookies), sending the server's origin as the browser does on a same-origin POST."""
    return TestClient(server.app, base_url=PUBLIC_URL, headers=ORIGIN, client=(address, 50000))


def row_bot(server: TestClient, address: str = "testclient") -> TestClient:
    """The desktop app: no cookies, no Origin."""
    return TestClient(server.app, base_url=PUBLIC_URL, client=(address, 50000))


def sign_in(client: TestClient, idp: FakeIdp, subject: str, groups: list[str] | None = None,
            next_path: str | None = None):
    login = client.get("/auth/login" + (f"?next={next_path}" if next_path else ""), follow_redirects=False)
    assert login.status_code == 302, login.text
    done = client.get(idp.authorize(login.headers["location"], subject=subject, groups=groups or [],
                                    email=f"{subject}@example.com"), follow_redirects=False)
    assert done.status_code == 303, done.text
    return done


def person(server: TestClient, idp: FakeIdp, subject: str, groups: list[str] | None = None) -> TestClient:
    client = browser(server)
    sign_in(client, idp, subject, groups)
    return client


def me(client: TestClient) -> str:
    return client.get("/api/me").json()["id"]


def device_query(code_verifier: str = CODE_VERIFIER, **changes: str | None) -> str:
    params = {"redirect_uri": REDIRECT, "code_challenge": pkce_challenge(code_verifier),
              "code_challenge_method": "S256", "state": STATE, **LAPTOP, **changes}
    return "/auth/device?" + urlencode({name: value for name, value in params.items() if value is not None})


def form_token(page) -> str:
    assert page.status_code == 200, page.text
    (token,) = _FORM_TOKEN.findall(page.text)
    return token


def confirm(client: TestClient, token: str, decision: str = "connect", **headers: str):
    return client.post("/auth/device/confirm", data={"form_token": token, "decision": decision},
                       headers=headers, follow_redirects=False)


def back_to_app(response) -> dict[str, str]:
    """The query of the redirect to the app's loopback address."""
    assert response.status_code == 303, response.text
    location = response.headers["location"]
    assert location.startswith(REDIRECT + "?"), location
    return {name: values[0] for name, values in parse_qs(urlsplit(location).query).items()}


def code_for(client: TestClient, code_verifier: str = CODE_VERIFIER, **changes: str) -> str:
    """Connect a new device for the signed-in ``client``: its one-time code."""
    query = back_to_app(confirm(client, form_token(client.get(device_query(code_verifier, **changes)))))
    assert query["state"] == STATE
    return query["code"]


def exchange(app: TestClient, code: str, code_verifier: str = CODE_VERIFIER, redirect_uri: str = REDIRECT):
    return app.post("/api/device/token", json={"grant_type": "authorization_code", "code": code,
                                               "code_verifier": code_verifier, "redirect_uri": redirect_uri})


def refresh(app: TestClient, refresh_token: str):
    return app.post("/api/device/token", data={"grant_type": "refresh_token", "refresh_token": refresh_token})


def join(server: TestClient, client: TestClient) -> dict[str, Any]:
    """A new device for ``client``'s person, all the way to its tokens."""
    response = exchange(row_bot(server), code_for(client))
    assert response.status_code == 200, response.text
    return response.json()


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def owner_sql(database, sql: str, *args) -> list[tuple]:
    with psycopg.connect(database.owner_url, autocommit=True) as conn:
        cursor = conn.execute(sql, args)
        return cursor.fetchall() if cursor.description else []


def tool_event(**changes: Any) -> dict:
    now = datetime.now(timezone.utc)
    fields = {"id": new_event_id(), "occurred_at": now, "recorded_at": now, "org_id": "acme",
              "actor": Actor(ActorKind.PERSON, "whoever-the-app-says"), "action": Action.TOOL_CALLED,
              "outcome": "succeeded", "layer": "org", "target": Target(TargetKind.TOOL, "shell_run"),
              "decided_by": "person", "redaction": "summaries",
              "details": {"approval_level": "approve", "surface": "chat", "summary": "Listed the project folder"}}
    return to_dict(AuditEvent(**{**fields, **changes}))


def refused_as(response, status: int, error: str) -> bool:
    return response.status_code == status and response.json() == {"error": error}


# -- the whole join -----------------------------------------------------------------------------------

def test_a_row_bot_joins_calls_the_api_and_gateway_refreshes_and_leaves(org_database, idp) -> None:
    with running_endpoint("openai") as endpoint, org_server(org_database, idp) as server:
        admin = person(server, idp, "it", ["IT Admins"])
        created = admin.post("/api/admin/endpoints", json={"name": "local", "api": "openai",
                                                            "base_url": endpoint.base_url, "api_key": KEY})
        assert admin.post("/api/admin/models", json={"endpoint_id": created.json()["id"], "name": "company-chat",
                                                      "kind": "chat"}).status_code == 201
        ada = person(server, idp, "ada")
        ada_id = me(ada)

        page = ada.get(device_query())
        assert "Connect Row-Bot on Ada&#x27;s laptop to org.example.test?" in page.text
        assert "You're signed in as" in page.text and "<script" not in page.text
        assert page.headers["content-security-policy"] == DEVICE_CSP
        assert ada.get("/auth/device.css").headers["content-type"].startswith("text/css")
        code = back_to_app(confirm(ada, form_token(page)))["code"]
        assert code.startswith("rboc_")

        app = row_bot(server)
        issued = exchange(app, code)
        assert issued.status_code == 200, issued.text
        tokens = issued.json()
        assert set(tokens) == {"access_token", "token_type", "expires_in", "refresh_token", "device_id"}
        assert (tokens["token_type"], tokens["expires_in"]) == ("Bearer", 3600)
        assert tokens["access_token"].startswith("rboa_") and tokens["refresh_token"].startswith("rbor_")
        assert issued.headers["cache-control"] == "no-store"
        device_id = tokens["device_id"]

        # The person's API and the gateway, with the bearer alone: no cookie, no Origin.
        assert app.get("/api/me", headers=bearer(tokens["access_token"])).json()["id"] == ada_id
        call = app.post(CHAT, headers=bearer(tokens["access_token"]),
                        json={"model": "company-chat", "messages": [{"role": "user", "content": "Hello"}]})
        assert call.status_code == 200, call.text
        # The endpoint sees the server's key, never the device's token.
        assert endpoint.last().headers["authorization"] == f"Bearer {KEY}"

        rotated = refresh(app, tokens["refresh_token"])
        assert rotated.status_code == 200, rotated.text
        fresh = rotated.json()
        assert fresh["device_id"] == device_id
        assert fresh["access_token"] != tokens["access_token"] and fresh["refresh_token"] != tokens["refresh_token"]
        assert app.get("/api/me", headers=bearer(fresh["access_token"])).status_code == 200

        left = app.post("/api/device/leave", headers=bearer(fresh["access_token"]))
        assert left.status_code == 204
        after = app.get("/api/me", headers=bearer(fresh["access_token"]))
        assert refused_as(after, 401, "invalid_token")
        assert after.headers["www-authenticate"] == 'Bearer error="invalid_token"'
        assert refused_as(app.post("/api/device/leave", headers=bearer(fresh["access_token"])), 401, "invalid_token")
        assert refused_as(refresh(app, fresh["refresh_token"]), 400, "invalid_grant")
        (listed,) = admin.get("/api/admin/devices").json()["devices"]
        assert (listed["id"], listed["name"], listed["revoked_at"] is not None) == (device_id, "Ada's laptop", True)

    (called,) = stored_events(org_database.owner_url, "model.called")
    assert (called["actor"]["id"], called["device_id"]) == (ada_id, device_id)
    assert [summary(event) for event in stored_events(org_database.owner_url, "device.joined", "device.removed")] == [
        ("device.joined", "person", ada_id, device_id, "succeeded", {"platform": "windows", "app_version": "5.4.0"}),
        ("device.removed", "person", ada_id, device_id, "succeeded", {"reason": "left"})]
    assert owner_sql(org_database, "SELECT count(*) FROM row_bot_org.device_tokens WHERE revoked_at IS NULL") == [(0,)]


def test_someone_not_signed_in_signs_in_first_and_comes_back_to_the_same_request(org_database, idp) -> None:
    with org_server(org_database, idp) as server:
        visitor = browser(server)
        assert visitor.get("/auth/device", follow_redirects=False).status_code == 400  # Nothing waiting.

        start = visitor.get(device_query(), follow_redirects=False)
        assert (start.status_code, start.headers["location"]) == (303, "/auth/login?next=/auth/device")
        (cookie,) = [h for h in start.headers.get_list("set-cookie") if h.startswith(REQUEST_COOKIE + "=")]
        assert {"secure", "httponly", "samesite=lax", "path=/"} <= {part.strip().lower() for part in cookie.split(";")}

        done = sign_in(visitor, idp, "ada", next_path="/auth/device")
        assert done.headers["location"] == PUBLIC_URL + "/auth/device"
        page = visitor.get("/auth/device")
        assert "Connect Row-Bot on Ada&#x27;s laptop" in page.text
        assert any(h.startswith(REQUEST_COOKIE + "=") and "max-age=0" in h.lower()
                   for h in page.headers.get_list("set-cookie"))
        assert exchange(row_bot(server), back_to_app(confirm(visitor, form_token(page)))["code"]).status_code == 200


# -- refused requests ---------------------------------------------------------------------------------

@pytest.mark.parametrize("changes", [
    {"redirect_uri": "http://localhost:53682/callback"},
    {"redirect_uri": "http://192.0.2.10:53682/callback"},
    {"redirect_uri": "http://127.0.0.1.example.test:53682/callback"},
    {"redirect_uri": "https://127.0.0.1:53682/callback"},
    {"redirect_uri": "http://user@127.0.0.1:53682/callback"},
    {"redirect_uri": "http://127.0.0.1:53682/callback#fragment"},
    {"redirect_uri": "http://127.0.0.1:53682/callback?x=1"},
    {"redirect_uri": "http://127.0.0.1/callback"},
    {"redirect_uri": "http://127.0.0.1:99999/callback"},
    {"redirect_uri": None},
    {"code_challenge_method": "plain"},
    {"code_challenge_method": None},
    {"code_challenge": "too-short"},
    {"state": None},
    {"platform": "android"},
    {"device_name": " padded "},
    {"app_version": "5.4 beta"},
], ids=lambda changes: next(f"{k}={v}" for k, v in changes.items()))
def test_a_bad_device_request_gets_a_plain_error_page_and_never_a_redirect(org_database, idp, changes) -> None:
    with org_server(org_database, idp) as server:
        visitor = browser(server)
        ada = person(server, idp, "ada")
        for client in (visitor, ada):
            response = client.get(device_query(**changes), follow_redirects=False)
            assert response.status_code == 400
            assert response.headers["content-type"].startswith("text/html")
            assert "location" not in response.headers and "set-cookie" not in response.headers
            assert "can't connect Row-Bot" in response.text
        # A parameter given twice is refused too.
        twice = ada.get(device_query() + "&state=another", follow_redirects=False)
        assert twice.status_code == 400 and "location" not in twice.headers
    assert owner_sql(org_database, "SELECT count(*) FROM row_bot_org.devices") == [(0,)]


def test_both_loopback_forms_are_accepted(org_database, idp) -> None:
    with org_server(org_database, idp) as server:
        ada = person(server, idp, "ada")
        for uri in ("http://127.0.0.1:1/", "http://[::1]:65535/oauth/done"):
            response = confirm(ada, form_token(ada.get(device_query(redirect_uri=uri))))
            assert response.headers["location"].startswith(uri + "?code=rboc_")


def test_cancel_goes_back_to_the_app_with_access_denied_and_joins_nothing(org_database, idp) -> None:
    with org_server(org_database, idp) as server:
        ada = person(server, idp, "ada")
        query = back_to_app(confirm(ada, form_token(ada.get(device_query())), "cancel"))
    assert query == {"error": "access_denied", "state": STATE}
    assert owner_sql(org_database, "SELECT count(*) FROM row_bot_org.devices") == [(0,)]


def test_the_confirm_step_needs_the_same_origin_and_its_own_signed_form(org_database, idp) -> None:
    with org_server(org_database, idp) as server:
        ada, bob = person(server, idp, "ada"), person(server, idp, "bob")
        token = form_token(ada.get(device_query()))

        assert refused_as(confirm(ada, token, Origin="https://evil.example.test"), 403, "same_origin_required")
        assert refused_as(confirm(ada, token, Origin="null"), 403, "same_origin_required")
        # Bound to Ada's browser session: Bob can't use it, and nobody can change it.
        assert confirm(bob, token).status_code == 400
        payload, _, mac = token.partition(".")
        changed = payload[:-1] + ("B" if payload[-1] == "A" else "A")
        assert confirm(ada, changed + "." + mac).status_code == 400
        assert confirm(ada, token, "maybe").status_code == 400
        # Someone not signed in can't confirm at all.
        assert refused_as(confirm(browser(server), token), 401, "sign_in_required")
        # The cookie that waits for a sign-in isn't a form token either.
        waiting = browser(server).get(device_query(), follow_redirects=False).cookies.get(REQUEST_COOKIE)
        assert waiting and confirm(ada, waiting).status_code == 400
    assert owner_sql(org_database, "SELECT count(*) FROM row_bot_org.devices") == [(0,)]


# -- the code -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("wrong", [{"code_verifier": "w" * 43}, {"redirect_uri": "http://127.0.0.1:53683/callback"},
                                   {"redirect_uri": "http://[::1]:53682/callback"}],
                         ids=["verifier", "port", "host"])
def test_a_code_needs_its_verifier_and_its_exact_redirect_uri_and_is_spent_by_a_wrong_try(org_database, idp,
                                                                                          wrong) -> None:
    with org_server(org_database, idp) as server:
        code = code_for(person(server, idp, "ada"))
        app = row_bot(server)
        assert refused_as(exchange(app, code, **wrong), 400, "invalid_grant")
        assert refused_as(exchange(app, code), 400, "invalid_grant")
    assert owner_sql(org_database, "SELECT count(*) FROM row_bot_org.device_tokens") == [(0,)]


def test_a_code_used_again_revokes_what_it_gave(org_database, idp) -> None:
    with org_server(org_database, idp) as server:
        code = code_for(person(server, idp, "ada"))
        app = row_bot(server)
        tokens = exchange(app, code).json()
        assert app.get("/api/me", headers=bearer(tokens["access_token"])).status_code == 200

        assert refused_as(exchange(app, code), 400, "invalid_grant")
        assert refused_as(app.get("/api/me", headers=bearer(tokens["access_token"])), 401, "invalid_token")
        assert refused_as(refresh(app, tokens["refresh_token"]), 400, "invalid_grant")
    # The device record stays; only its tokens went.
    assert owner_sql(org_database, "SELECT revoked_at IS NULL FROM row_bot_org.devices") == [(True,)]


def test_an_expired_code_is_refused(org_database, idp) -> None:
    with org_server(org_database, idp) as server:
        code = code_for(person(server, idp, "ada"))
        ((lifetime,),) = owner_sql(org_database, "SELECT extract(epoch FROM expires_at - created_at)::int"
                                                 " FROM row_bot_org.device_codes")
        assert lifetime == 60
        # The database's clock decides: the code's end moved into the past, no waiting.
        owner_sql(org_database, "UPDATE row_bot_org.device_codes SET expires_at = now() - interval '1 second'")
        assert refused_as(exchange(row_bot(server), code), 400, "invalid_grant")


# -- tokens -------------------------------------------------------------------------------------------

def test_a_refresh_token_used_again_revokes_the_whole_family_but_not_the_device(org_database, idp) -> None:
    with org_server(org_database, idp) as server:
        ada = person(server, idp, "ada")
        app = row_bot(server)
        first = join(server, ada)
        second = refresh(app, first["refresh_token"]).json()
        assert app.get("/api/me", headers=bearer(second["access_token"])).status_code == 200

        assert refused_as(refresh(app, first["refresh_token"]), 400, "invalid_grant")
        for access in (first["access_token"], second["access_token"]):
            assert refused_as(app.get("/api/me", headers=bearer(access)), 401, "invalid_token")
        assert refused_as(refresh(app, second["refresh_token"]), 400, "invalid_grant")
        # The person joins again as usual.
        assert app.get("/api/me", headers=bearer(join(server, ada)["access_token"])).status_code == 200
    assert owner_sql(org_database, "SELECT count(*) FROM row_bot_org.devices WHERE revoked_at IS NULL") == [(2,)]


def test_expired_tokens_are_refused_and_a_refresh_never_outlives_the_join(org_database, idp) -> None:
    with org_server(org_database, idp) as server:
        app = row_bot(server)
        tokens = join(server, person(server, idp, "ada"))
        ((idle, absolute),) = owner_sql(
            org_database, "SELECT extract(epoch FROM expires_at - created_at)::int,"
                          " extract(epoch FROM absolute_expires_at - created_at)::int"
                          " FROM row_bot_org.device_tokens WHERE kind = 'refresh'")
        # 30 and 180 days (an hour either way where the database's zone changes the clock between).
        assert abs(idle - 30 * 86400) <= 3600 and abs(absolute - 180 * 86400) <= 3600

        owner_sql(org_database, "UPDATE row_bot_org.device_tokens SET expires_at = now() - interval '1 second'"
                                " WHERE kind = 'access'")
        assert refused_as(app.get("/api/me", headers=bearer(tokens["access_token"])), 401, "invalid_token")
        # Ten minutes left of the join's 180 days: the next refresh token ends then, not 30 days on.
        owner_sql(org_database, "UPDATE row_bot_org.device_tokens SET absolute_expires_at = now() + interval '10 minutes'"
                                " WHERE kind = 'refresh'")
        rotated = refresh(app, tokens["refresh_token"]).json()
        assert app.get("/api/me", headers=bearer(rotated["access_token"])).status_code == 200
        ((capped,),) = owner_sql(org_database, "SELECT expires_at = absolute_expires_at FROM row_bot_org.device_tokens"
                                               " WHERE kind = 'refresh' AND used_at IS NULL")
        assert capped is True
        owner_sql(org_database, "UPDATE row_bot_org.device_tokens SET expires_at = now() - interval '1 second'"
                                " WHERE kind = 'refresh' AND used_at IS NULL")
        assert refused_as(refresh(app, rotated["refresh_token"]), 400, "invalid_grant")


@pytest.mark.parametrize("body", [
    {"json": {"grant_type": "password", "username": "ada"}},
    {"json": {"code": "rboc_x"}},
    {"json": {"grant_type": "authorization_code", "code": "x"}},
    {"json": {"grant_type": "refresh_token", "refresh_token": 7}},
    {"json": ["grant_type", "refresh_token"]},
    {"content": "grant_type=refresh_token&grant_type=refresh_token&refresh_token=rbor_x",
     "headers": {"Content-Type": "application/x-www-form-urlencoded"}},
    {"content": "grant_type=refresh_token", "headers": {"Content-Type": "text/plain"}},
    {"json": {"grant_type": "authorization_code", "code": "not-ours", "code_verifier": CODE_VERIFIER,
              "redirect_uri": REDIRECT}},
    {"json": {"grant_type": "authorization_code", "code": "rboc_" + "A" * 43, "code_verifier": "short",
              "redirect_uri": REDIRECT}},
    {"data": {"grant_type": "refresh_token", "refresh_token": "rbor_" + "A" * 43}},
], ids=["unsupported", "no-grant", "missing-fields", "not-a-string", "not-an-object", "twice", "wrong-type",
        "foreign-code", "short-verifier", "guessed-refresh"])
def test_the_token_endpoint_answers_oauth_errors_with_nothing_more(org_database, idp, body) -> None:
    with org_server(org_database, idp) as server:
        response = row_bot(server).post("/api/device/token", **body)

    assert response.status_code == 400
    assert response.json() in ({"error": "invalid_request"}, {"error": "invalid_grant"},
                               {"error": "unsupported_grant_type"})
    if body.get("json", {}) == {"grant_type": "password", "username": "ada"}:
        assert response.json() == {"error": "unsupported_grant_type"}


# -- bearer authentication ----------------------------------------------------------------------------

def test_a_cookie_and_a_bearer_together_are_refused(org_database, idp) -> None:
    with org_server(org_database, idp) as server:
        ada = person(server, idp, "ada")
        tokens = join(server, ada)

        response = ada.get("/api/me", headers=bearer(tokens["access_token"]))
        assert refused_as(response, 400, "invalid_request")
        assert refused_as(ada.post("/api/device/leave", headers=bearer(tokens["access_token"])), 400,
                          "invalid_request")
        # Each alone works; a browser session can't leave in a device's name.
        assert ada.get("/api/me").status_code == 200
        assert refused_as(ada.post("/api/device/leave"), 403, "device_credential_required")


def test_a_bearer_never_opens_an_admin_route_the_console_or_a_browser_step(org_database, idp) -> None:
    with org_server(org_database, idp) as server:
        it = person(server, idp, "it", ["IT Admins"])
        app = row_bot(server)
        tokens = join(server, it)
        with_bearer = bearer(tokens["access_token"])
        assert app.get("/api/me", headers=with_bearer).status_code == 200

        for method, path in [("GET", "/api/admin/people"), ("GET", "/api/admin/devices"),
                             ("POST", f"/api/admin/devices/{tokens['device_id']}/revoke"), ("GET", "/api/admin/audit"),
                             ("PUT", "/api/admin/policy"), ("GET", "/console/"), ("GET", "/admin/"),
                             ("POST", "/auth/logout"), ("POST", "/auth/device/confirm")]:
            response = app.request(method, path, headers=with_bearer, json={})
            assert refused_as(response, 403, "browser_session_required"), (method, path)
        # The admin's browser session still administers.
        assert it.get("/api/admin/people").status_code == 200
    assert stored_events(org_database.owner_url, "device.removed") == []


def test_a_revoked_device_is_refused_on_its_next_request(org_database, idp) -> None:
    with org_server(org_database, idp) as server:
        it, ada = person(server, idp, "it", ["IT Admins"]), person(server, idp, "ada")
        app = row_bot(server)
        tokens = join(server, ada)
        assert app.get("/api/me", headers=bearer(tokens["access_token"])).status_code == 200

        assert it.post(f"/api/admin/devices/{tokens['device_id']}/revoke").status_code == 200
        assert refused_as(app.get("/api/me", headers=bearer(tokens["access_token"])), 401, "invalid_token")
        assert refused_as(app.post("/api/audit/events", headers=bearer(tokens["access_token"]),
                                   json={"events": [tool_event()]}), 401, "invalid_token")
        assert refused_as(refresh(app, tokens["refresh_token"]), 400, "invalid_grant")


def test_a_suspended_persons_devices_stop_and_start_again_when_resumed(org_database, idp) -> None:
    with org_server(org_database, idp) as server:
        it, ada = person(server, idp, "it", ["IT Admins"]), person(server, idp, "ada")
        app = row_bot(server)
        tokens = join(server, ada)
        ada_id = me(ada)

        assert it.post(f"/api/admin/people/{ada_id}/suspend").status_code == 200
        assert refused_as(app.get("/api/me", headers=bearer(tokens["access_token"])), 401, "invalid_token")
        assert refused_as(refresh(app, tokens["refresh_token"]), 400, "invalid_grant")

        assert it.post(f"/api/admin/people/{ada_id}/resume").status_code == 200
        assert app.get("/api/me", headers=bearer(tokens["access_token"])).status_code == 200
        assert refresh(app, tokens["refresh_token"]).status_code == 200


def test_a_device_reports_audit_events_only_for_itself(org_database, idp) -> None:
    with org_server(org_database, idp) as server:
        ada = person(server, idp, "ada")
        app = row_bot(server)
        laptop, desktop = join(server, ada), join(server, ada)

        def send(event: dict):
            return app.post("/api/audit/events", headers=bearer(laptop["access_token"]), json={"events": [event]})

        other = send(tool_event(device_id=desktop["device_id"]))
        assert (other.status_code, other.json()) == (422, {"error": "device_mismatch", "index": 0})
        assert send(tool_event(device_id=laptop["device_id"])).json() == {"accepted": 1, "duplicates": 0}
        assert send(tool_event()).json() == {"accepted": 1, "duplicates": 0}
    assert [event.get("device_id") for event in stored_events(org_database.owner_url, "tool.called")] == [
        laptop["device_id"], None]


# -- the limit, the logs ------------------------------------------------------------------------------

def test_a_flood_of_device_steps_from_one_address_is_refused_before_the_database(org_database, idp) -> None:
    with org_server(org_database, idp, ROW_BOT_ORG_SIGN_IN_RATE_LIMIT="3") as server:
        flooder = browser(server, "198.51.100.7")
        assert [flooder.get(device_query(), follow_redirects=False).status_code for _ in range(3)] == [303] * 3
        assert flooder.get(device_query(), follow_redirects=False).status_code == 429
        token = row_bot(server, "198.51.100.7").post("/api/device/token", json={"grant_type": "refresh_token",
                                                                                "refresh_token": "rbor_" + "A" * 43})
        assert (token.status_code, token.json(), token.headers["retry-after"]) == (
            429, {"error": "too_many_requests"}, "60")
        # The browser sign-in counts apart, and another address joins as usual.
        assert flooder.get("/auth/login", follow_redirects=False).status_code == 302
        assert exchange(row_bot(server, "198.51.100.8"), code_for(person(server, idp, "ada"))).status_code == 200


@pytest.fixture
def log_text() -> Iterator[Callable[[], str]]:
    root = logging.getLogger()
    saved = (root.handlers[:], root.level)
    stream = io.StringIO()
    configure_logging("DEBUG", stream)
    yield stream.getvalue
    root.handlers[:], level = saved
    root.setLevel(level)


def test_no_log_line_carries_a_code_a_token_or_the_state(org_database, idp, log_text) -> None:
    with org_server(org_database, idp) as server:
        ada = person(server, idp, "ada")
        app = row_bot(server)
        page = ada.get(device_query())
        code = back_to_app(confirm(ada, form_token(page)))["code"]
        tokens = exchange(app, code).json()
        rotated = refresh(app, tokens["refresh_token"]).json()
        app.get("/api/me", headers=bearer(rotated["access_token"]))
        exchange(app, code)  # A replay, refused.
        app.post("/api/device/leave", headers=bearer(rotated["access_token"]))

    text = log_text()
    assert '"/api/device/token"' in text and '"/auth/device/confirm"' in text
    for secret in (code, tokens["access_token"], tokens["refresh_token"], rotated["access_token"],
                   rotated["refresh_token"], STATE, form_token(page), CODE_VERIFIER):
        assert secret[5:] not in text


# -- row-level security and the definer lookups -------------------------------------------------------

@pytest.fixture
def pool(org_database):
    pool = create_pool(org_database.runtime_url, max_size=2)
    pool.open(wait=True, timeout=30)
    yield pool
    pool.close()


def test_one_person_never_reads_or_changes_anothers_codes_or_tokens(org_database, idp, pool) -> None:
    with org_server(org_database, idp) as server:
        ada, bob = person(server, idp, "ada"), person(server, idp, "bob")
        ada_id, bob_id = me(ada), me(bob)
        ada_device = join(server, ada)["device_id"]
        bob_device = join(server, bob)["device_id"]
        code_for(bob)  # A code Bob hasn't used yet.

    with principal_transaction(pool, ada_id) as conn:
        # No WHERE: row-level security alone keeps Ada to her own tokens.
        assert set(conn.execute("SELECT device_id::text FROM row_bot_org.device_tokens").fetchall()) == {(ada_device,)}
        assert conn.execute("UPDATE row_bot_org.device_tokens SET revoked_at = now() WHERE device_id = %s",
                            (bob_device,)).rowcount == 0
        assert conn.execute("UPDATE row_bot_org.devices SET revoked_at = now() WHERE id = %s",
                            (bob_device,)).rowcount == 0
    refusals = [
        "SELECT verifier FROM row_bot_org.device_tokens",
        "SELECT * FROM row_bot_org.device_codes",
        "UPDATE row_bot_org.device_tokens SET revoked_at = NULL",
        "UPDATE row_bot_org.device_tokens SET expires_at = now() + interval '1 year'",
        "UPDATE row_bot_org.device_codes SET used_at = NULL",
        "DELETE FROM row_bot_org.device_tokens",
        f"INSERT INTO row_bot_org.device_tokens (verifier, kind, principal_id, device_id, expires_at)"
        f" VALUES ('\\x{'00' * 32}', 'access', '{ada_id}', '{ada_device}', now() + interval '1 year')",
        # A code for Bob's device, or one that lasts longer than its 60 seconds.
        f"INSERT INTO row_bot_org.device_codes (device_id, verifier, code_challenge, redirect_uri)"
        f" VALUES ('{bob_device}', '\\x{'01' * 32}', '{'A' * 43}', 'http://127.0.0.1:1/')",
        f"INSERT INTO row_bot_org.device_codes (device_id, verifier, code_challenge, redirect_uri, expires_at)"
        f" VALUES ('{ada_device}', '\\x{'02' * 32}', '{'A' * 43}', 'http://127.0.0.1:1/', now() + interval '1 day')",
    ]
    for sql in refusals:
        with pytest.raises(psycopg.errors.InsufficientPrivilege), principal_transaction(pool, ada_id) as conn:
            conn.execute(sql)
    with pool.connection() as conn:
        # Outside a principal's transaction: nothing.
        assert conn.execute("SELECT count(id) FROM row_bot_org.device_tokens").fetchone() == (0,)
    # Bob's tokens and code are as they were.
    assert owner_sql(org_database, "SELECT count(*) FROM row_bot_org.device_tokens WHERE principal_id = %s"
                                   " AND revoked_at IS NULL", bob_id) == [(2,)]
    assert owner_sql(org_database, "SELECT count(*) FROM row_bot_org.device_codes WHERE principal_id = %s"
                                   " AND used_at IS NULL", bob_id) == [(1,)]


def test_the_definer_lookups_return_one_row_for_one_verifier_and_nothing_for_a_guess(org_database, idp) -> None:
    with org_server(org_database, idp) as server:
        tokens = join(server, person(server, idp, "ada"))
        join(server, person(server, idp, "bob"))
    access = verifier(PEPPER, "device-access", tokens["access_token"][len("rboa_"):])
    guess = secrets.token_bytes(32)
    week, month = timedelta(weeks=1), timedelta(days=30)

    with psycopg.connect(org_database.runtime_url, autocommit=True) as conn:
        assert [str(row[0]) for row in conn.execute(
            "SELECT device_id FROM row_bot_org.resume_device_token(%s)", (access,)).fetchall()] == [tokens["device_id"]]
        assert conn.execute("SELECT * FROM row_bot_org.resume_device_token(%s)", (guess,)).fetchall() == []
        assert conn.execute("SELECT * FROM row_bot_org.resume_device_token(NULL)").fetchall() == []
        # The refresh verifier isn't an access token.
        refresh_verifier = verifier(PEPPER, "device-refresh", tokens["refresh_token"][len("rbor_"):])
        assert conn.execute("SELECT * FROM row_bot_org.resume_device_token(%s)", (refresh_verifier,)).fetchall() == []
        assert conn.execute("SELECT * FROM row_bot_org.refresh_device_token(%s, %s, %s, %s, %s)",
                            (guess, secrets.token_bytes(32), secrets.token_bytes(32), week, month)).fetchall() == []
        assert conn.execute("SELECT * FROM row_bot_org.redeem_device_code(%s, %s, %s, %s, %s, %s, %s, %s)",
                            (guess, "A" * 43, REDIRECT, secrets.token_bytes(32), secrets.token_bytes(32), week,
                             month, month)).fetchall() == []
        # The tables themselves can't be read to try their verifiers.
        assert conn.execute("SELECT count(id) FROM row_bot_org.device_tokens").fetchone() == (0,)
    # The guesses changed nothing: Ada's and Bob's tokens are all live.
    assert owner_sql(org_database, "SELECT count(*) FROM row_bot_org.device_tokens WHERE revoked_at IS NULL"
                                   " AND used_at IS NULL") == [(4,)]
