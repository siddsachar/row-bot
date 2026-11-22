"""Company sign-in end to end: the fake provider (tests/fixtures/oidc_idp.py), the server's routes and a real
(throwaway) Postgres as the runtime role.

Covers the sign-in of each provider shape, the landing page, the sign-in state (single use, bound to
the browser, expiry), the redirect URI, refusals that reach the callback, the cookies, the per-address
limit on the sign-in steps, forwarded headers (only from a trusted proxy), server sessions (expiry, idle
timeout, logout, rotation, revocation, suspension), row-level security on principals, groups and
sessions, and the access log. Security-sensitive: these run in the pull request lane's Org job.
"""

from __future__ import annotations

import io
import json
import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from urllib.parse import parse_qs, urlsplit

import pytest

from tests.fixtures.postgres import import_psycopg

psycopg = import_psycopg()
pytest.importorskip("joserfc", reason="joserfc is not installed: uv sync --locked --all-extras --group test --group org")

from fastapi.testclient import TestClient  # noqa: E402

from row_bot_org import cli  # noqa: E402
from row_bot_org.app import create_app  # noqa: E402
from row_bot_org.db.pool import create_pool  # noqa: E402
from row_bot_org.db.rls import principal_transaction  # noqa: E402
from row_bot_org.identity.gate import SESSION_COOKIE, SIGN_IN_COOKIE  # noqa: E402
from row_bot_org.logging import configure_logging  # noqa: E402
from row_bot_org.settings import load_settings  # noqa: E402
from tests.fixtures.oidc_idp import CLIENT_SECRET, OTHER_TENANT, PUBLIC_URL, REDIRECT_URI, FakeIdp, running_idp  # noqa: E402

pytestmark = pytest.mark.postgres

PEPPER = "a-test-pepper-that-is-long-enough-0123456789"
ORIGIN = {"Origin": PUBLIC_URL}


@contextmanager
def org_server(database, idp: FakeIdp, *, config: dict | None = None, **env: str) -> Iterator[TestClient]:
    settings = load_settings({
        "ROW_BOT_ORG_IDP": json.dumps(config or idp.config()), "ROW_BOT_ORG_IDP_CLIENT_SECRET": CLIENT_SECRET,
        "ROW_BOT_ORG_PUBLIC_URL": PUBLIC_URL, "ROW_BOT_ORG_HMAC_PEPPER": PEPPER, **env,
    })
    pool = create_pool(database.runtime_url, max_size=4)
    with TestClient(create_app(settings, pool), base_url=PUBLIC_URL) as client:
        yield client


def browser(client: TestClient, address: str = "testclient", **headers: str) -> TestClient:
    """Another browser on the same server (its own cookies), connecting from ``address``."""
    return TestClient(client.app, base_url=PUBLIC_URL, client=(address, 50000), headers=headers)


def start(client: TestClient):
    response = client.get("/auth/login", follow_redirects=False)
    assert response.status_code == 302, response.text
    return response


def sign_in(client: TestClient, idp: FakeIdp, **claims):
    callback = idp.authorize(start(client).headers["location"], **claims)
    return client.get(callback, follow_redirects=False)


def with_cookie(client: TestClient, token: str | None):
    return browser(client).get("/api/me", headers={"Cookie": f"{SESSION_COOKIE}={token}"})


def owner_sql(database, sql: str, *args) -> list[tuple]:
    with psycopg.connect(database.owner_url, autocommit=True) as conn:
        cursor = conn.execute(sql, args)
        return cursor.fetchall() if cursor.description else []


@pytest.fixture
def idp(request: pytest.FixtureRequest) -> Iterator[FakeIdp]:
    with running_idp(getattr(request, "param", "okta")) as running:
        yield running


# -- signing in -------------------------------------------------------------------------------------

@pytest.mark.parametrize("idp", ["entra", "okta", "google"], indirect=True)
def test_a_person_signs_in_and_the_server_knows_them_and_their_groups(org_database, idp: FakeIdp) -> None:
    with org_server(org_database, idp) as client:
        done = sign_in(client, idp, subject="stable-sub")
        assert done.status_code == 303 and done.headers["location"] == PUBLIC_URL + "/admin/"

        me = client.get("/api/me")
        assert me.status_code == 200
        body = me.json()
        assert (body["display_name"], body["status"]) == ("Ada Example", "active")
        assert body["groups"] == sorted(idp.claims().get("groups", []))
        assert body["email"] == (None if idp.shape == "entra" else "ada@example.com")

        # The same subject again is the same principal; another subject with the same email is not.
        assert sign_in(browser(client), idp, subject="stable-sub").status_code == 303
        other = browser(client)
        assert sign_in(other, idp, subject="another-sub").status_code == 303
        assert other.get("/api/me").json()["id"] != body["id"]
        assert owner_sql(org_database, "SELECT count(*) FROM row_bot_org.principals") == [(2,)]


def test_groups_are_mapped_and_replaced_at_each_sign_in(org_database, idp: FakeIdp) -> None:
    config = idp.config(group_map={"Engineering": "engineering", "Sales": "sales"})
    with org_server(org_database, idp, config=config) as client:
        sign_in(client, idp)
        assert client.get("/api/me").json()["groups"] == ["engineering"]
        sign_in(client, idp, groups=["Sales"])
        assert client.get("/api/me").json()["groups"] == ["sales"]


def test_a_sign_in_lands_on_the_admin_address_which_says_signed_in(org_database, idp: FakeIdp) -> None:
    with org_server(org_database, idp) as client:
        assert client.get("/admin/").status_code == 401
        landing = client.get(sign_in(client, idp).headers["location"])

    assert (landing.status_code, landing.json()) == (200, {"status": "signed_in"})


def test_the_authorization_request_has_pkce_state_nonce_and_the_exact_redirect_uri(org_database,
                                                                                   idp: FakeIdp) -> None:
    with org_server(org_database, idp) as client:
        query = {k: v[0] for k, v in parse_qs(urlsplit(start(client).headers["location"]).query).items()}

    assert query["redirect_uri"] == REDIRECT_URI
    assert query["scope"] == "openid email profile"
    assert query["code_challenge_method"] == "S256" and len(query["code_challenge"]) == 43
    assert len(query["state"]) == 43 and len(query["nonce"]) == 43 and query["state"] != query["nonce"]


def test_the_cookies_are_host_only_secure_httponly_and_lax(org_database, idp: FakeIdp) -> None:
    with org_server(org_database, idp) as client:
        login = start(client)
        done = client.get(idp.authorize(login.headers["location"]), follow_redirects=False)

    for response, name in ((login, SIGN_IN_COOKIE), (done, SESSION_COOKIE)):
        (header,) = [h for h in response.headers.get_list("set-cookie") if h.startswith(name + "=")]
        parts = [part.strip().lower() for part in header.split(";")]
        assert name.startswith("__Host-")
        assert {"secure", "httponly", "samesite=lax", "path=/"} <= set(parts), header
        assert not any(part.startswith("domain") for part in parts)
    # The sign-in cookie is cleared once the sign-in is done.
    (cleared,) = [h for h in done.headers.get_list("set-cookie") if h.startswith(SIGN_IN_COOKIE + "=")]
    assert "max-age=0" in cleared.lower()


# -- the sign-in state ------------------------------------------------------------------------------

def test_a_callback_is_used_once(org_database, idp: FakeIdp) -> None:
    with org_server(org_database, idp) as client:
        callback = idp.authorize(start(client).headers["location"])
        assert client.get(callback, follow_redirects=False).status_code == 303

        replay = client.get(callback, follow_redirects=False)
        assert (replay.status_code, replay.json()) == (400, {"error": "sign_in_failed",
                                                              "reason": "unknown_or_used_state"})


@pytest.mark.parametrize("query", ["", "?code=abc", "?state=short&code=abc", "?state=" + "A" * 43 + "&code=abc"])
def test_a_callback_without_a_known_state_is_refused(org_database, idp: FakeIdp, query: str) -> None:
    with org_server(org_database, idp) as client:
        response = client.get("/auth/callback" + query)
    assert response.status_code == 400
    assert response.json()["reason"] in {"invalid_request", "unknown_or_used_state"}


def test_a_callback_in_another_browser_is_refused_and_spends_the_state(org_database, idp: FakeIdp) -> None:
    with org_server(org_database, idp) as client:
        callback = idp.authorize(start(client).headers["location"])

        # Login CSRF: someone else's callback link opened in this browser.
        forced = browser(client).get(callback, follow_redirects=False)
        assert (forced.status_code, forced.json()["reason"]) == (400, "not_this_browser")
        assert client.get(callback, follow_redirects=False).json()["reason"] == "unknown_or_used_state"
        assert owner_sql(org_database, "SELECT count(*) FROM row_bot_org.principals") == [(0,)]


def test_an_expired_sign_in_is_refused(org_database, idp: FakeIdp) -> None:
    with org_server(org_database, idp) as client:
        callback = idp.authorize(start(client).headers["location"])
        owner_sql(org_database, "UPDATE row_bot_org.auth_attempts SET expires_at = now() - interval '1 second'")

        assert client.get(callback, follow_redirects=False).json()["reason"] == "unknown_or_used_state"
        assert owner_sql(org_database, "SELECT count(*) FROM row_bot_org.auth_attempts") == [(0,)]


def test_a_redirect_uri_the_provider_didnt_register_fails_the_exchange(org_database) -> None:
    with running_idp("okta", redirect_uri="https://elsewhere.example.test/auth/callback") as idp, \
            org_server(org_database, idp) as client:
        response = sign_in(client, idp)

    assert (response.status_code, response.json()["reason"]) == (502, "token_exchange_failed")


def test_the_provider_ending_the_sign_in_is_reported(org_database, idp: FakeIdp) -> None:
    with org_server(org_database, idp) as client:
        state = parse_qs(urlsplit(start(client).headers["location"]).query)["state"][0]
        response = client.get(f"/auth/callback?state={state}&error=access_denied")
    assert response.json()["reason"] == "idp_error"


@pytest.mark.parametrize(
    ("shape", "claims", "reason"),
    [
        ("entra", {"tid": OTHER_TENANT}, "tenant_not_allowed"),
        ("entra", {"groups": None, "_claim_names": {"groups": "src1"}}, "group_overage"),
        ("okta", {"nonce": "a-nonce-from-another-sign-in"}, "id_token_rejected"),
        ("okta", {"aud": "another-client"}, "id_token_rejected"),
    ],
)
def test_a_refused_identity_makes_no_principal(org_database, shape: str, claims: dict, reason: str) -> None:
    with running_idp(shape) as idp, org_server(org_database, idp) as client:
        response = sign_in(client, idp, **claims)

    assert (response.status_code, response.json()) == (400, {"error": "sign_in_failed", "reason": reason})
    assert owner_sql(org_database, "SELECT count(*) FROM row_bot_org.principals") == [(0,)]


# -- the per-address limit and forwarded headers ----------------------------------------------------

def test_a_flood_of_sign_in_steps_from_one_address_is_refused_before_the_database(org_database,
                                                                                  idp: FakeIdp) -> None:
    with org_server(org_database, idp, ROW_BOT_ORG_SIGN_IN_RATE_LIMIT="3") as client:
        flooder = browser(client, "198.51.100.7")
        assert [flooder.get("/auth/login", follow_redirects=False).status_code for _ in range(4)] == [302] * 3 + [429]
        refused = flooder.get("/auth/callback?state=" + "A" * 43 + "&code=abc")
        assert (refused.status_code, refused.json()) == (429, {"error": "too_many_requests"})
        assert refused.headers["retry-after"] == "60"
        # An IPv6 address counts by its /64.
        neighbours = [browser(client, f"2001:db8:1:2::{n}") for n in range(1, 5)]
        assert [b.get("/auth/login", follow_redirects=False).status_code for b in neighbours] == [302] * 3 + [429]

        # Someone else signs in as usual.
        assert sign_in(browser(client, "198.51.100.8"), idp).status_code == 303
    # Only the allowed logins left an attempt (three each from the address and the /64; one was used).
    assert owner_sql(org_database, "SELECT count(*) FROM row_bot_org.auth_attempts") == [(6,)]


def _session_addresses(database) -> list[str]:
    return [address for (address,) in owner_sql(
        database, "SELECT address FROM row_bot_org.server_sessions ORDER BY created_at")]


def test_forwarded_headers_are_ignored_without_a_trusted_proxy(org_database, idp: FakeIdp) -> None:
    with org_server(org_database, idp, ROW_BOT_ORG_SIGN_IN_RATE_LIMIT="2") as client:
        spoofing = {"X-Forwarded-For": "203.0.113.9", "X-Forwarded-Proto": "https"}
        assert sign_in(browser(client, "10.1.2.3", **spoofing), idp).status_code == 303
        # A new X-Forwarded-For each time doesn't get past the limit either.
        assert browser(client, "10.1.2.3", **{"X-Forwarded-For": "203.0.113.10"}).get(
            "/auth/login", follow_redirects=False).status_code == 429

    assert _session_addresses(org_database) == ["10.1.2.3"]


def test_forwarded_headers_count_only_from_a_trusted_proxy(org_database, idp: FakeIdp) -> None:
    with org_server(org_database, idp, ROW_BOT_ORG_TRUSTED_PROXIES="10.0.0.0/8",
                    ROW_BOT_ORG_SIGN_IN_RATE_LIMIT="2") as client:
        # Through the proxy: the client it names. A client's own X-Forwarded-For entry (the left one) isn't believed.
        via_proxy = {"X-Forwarded-For": "192.0.2.50, 203.0.113.9", "X-Forwarded-Proto": "https"}
        assert sign_in(browser(client, "10.1.2.3", **via_proxy), idp).status_code == 303
        # Straight from an address that isn't a proxy: its own address, whatever it claims.
        assert sign_in(browser(client, "192.0.2.1", **{"X-Forwarded-For": "203.0.113.77"}), idp).status_code == 303
        # Each client behind the proxy has its own count.
        assert sign_in(browser(client, "10.1.2.3", **{"X-Forwarded-For": "203.0.113.10"}), idp).status_code == 303

    assert _session_addresses(org_database) == ["203.0.113.9", "192.0.2.1", "203.0.113.10"]


# -- sessions ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("change", ["expires_at = now() - interval '1 second'",
                                    "idle_expires_at = now() - interval '1 second'",
                                    "revoked_at = now()"],
                         ids=["lifetime", "idle", "revoked"])
def test_an_ended_session_is_refused(org_database, idp: FakeIdp, change: str) -> None:
    with org_server(org_database, idp) as client:
        sign_in(client, idp)
        assert client.get("/api/me").status_code == 200
        owner_sql(org_database, f"UPDATE row_bot_org.server_sessions SET {change}")

        assert client.get("/api/me").status_code == 401


def test_each_request_pushes_the_idle_limit_back_but_never_past_the_lifetime(org_database, idp: FakeIdp) -> None:
    with org_server(org_database, idp, ROW_BOT_ORG_SESSION_LIFETIME="7200",
                    ROW_BOT_ORG_SESSION_IDLE_TIMEOUT="3600") as client:
        sign_in(client, idp)
        owner_sql(org_database, "UPDATE row_bot_org.server_sessions SET idle_expires_at = now() + interval '5 s',"
                                " expires_at = now() + interval '1 hour 30 minutes'")
        assert client.get("/api/me").status_code == 200

        ((idle_left, life_left),) = owner_sql(
            org_database, "SELECT extract(epoch FROM idle_expires_at - now()), extract(epoch FROM expires_at - now())"
                          " FROM row_bot_org.server_sessions")
        assert 3500 < idle_left <= 3600 and life_left > idle_left
        owner_sql(org_database, "UPDATE row_bot_org.server_sessions SET expires_at = now() + interval '10 minutes'")
        client.get("/api/me")
        ((capped,),) = owner_sql(org_database, "SELECT idle_expires_at = expires_at FROM row_bot_org.server_sessions")
        assert capped is True


def test_logout_revokes_the_session_and_clears_the_cookie(org_database, idp: FakeIdp) -> None:
    with org_server(org_database, idp) as client:
        sign_in(client, idp)
        token = client.cookies.get(SESSION_COOKIE)

        assert client.post("/auth/logout").status_code == 403  # not same-origin
        response = client.post("/auth/logout", headers=ORIGIN)
        assert response.status_code == 204
        assert any(h.startswith(SESSION_COOKIE + "=") and "max-age=0" in h.lower()
                   for h in response.headers.get_list("set-cookie"))
        assert with_cookie(client, token).status_code == 401
        assert owner_sql(org_database, "SELECT count(*) FROM row_bot_org.server_sessions WHERE revoked_at IS NULL") \
            == [(0,)]


def test_signing_in_again_rotates_the_session(org_database, idp: FakeIdp) -> None:
    with org_server(org_database, idp) as client:
        sign_in(client, idp)
        first = client.cookies.get(SESSION_COOKIE)
        sign_in(client, idp)
        second = client.cookies.get(SESSION_COOKIE)

        assert first != second
        assert with_cookie(client, first).status_code == 401
        assert with_cookie(client, second).status_code == 200


def test_the_operator_revokes_every_session_of_a_principal(org_database, idp: FakeIdp, monkeypatch, capsys) -> None:
    with org_server(org_database, idp) as client:
        sign_in(client, idp)
        laptop = browser(client)
        sign_in(laptop, idp)
        principal_id = client.get("/api/me").json()["id"]

        monkeypatch.setenv("ROW_BOT_ORG_MIGRATION_DATABASE_URL", org_database.owner_url)
        assert cli.main(["principals", "revoke-sessions", principal_id]) == 0
        assert f"Revoked 2 sessions of principal {principal_id}." in capsys.readouterr().out

        assert client.get("/api/me").status_code == 401
        assert laptop.get("/api/me").status_code == 401


def test_revoke_sessions_needs_a_principal_id() -> None:
    with pytest.raises(SystemExit):
        cli.main(["principals", "revoke-sessions", "not-a-uuid"])


def test_a_suspended_principal_can_neither_resume_nor_sign_in(org_database, idp: FakeIdp) -> None:
    with org_server(org_database, idp) as client:
        sign_in(client, idp)
        owner_sql(org_database, "UPDATE row_bot_org.principals SET status = 'suspended'")

        assert client.get("/api/me").status_code == 401
        again = sign_in(browser(client), idp)
        assert (again.status_code, again.json()["reason"]) == (403, "principal_suspended")


# -- row-level security -----------------------------------------------------------------------------

def test_one_principal_never_reads_anothers_sessions_groups_or_profile(org_database) -> None:
    with running_idp("okta") as idp, org_server(org_database, idp) as client:
        ada, bob = client, browser(client)
        sign_in(ada, idp, subject="ada", groups=["Engineering"])
        sign_in(bob, idp, subject="bob", groups=["Sales"])
        ada_id, bob_id = ada.get("/api/me").json()["id"], bob.get("/api/me").json()["id"]

    pool = create_pool(org_database.runtime_url, max_size=1)
    pool.open(wait=True, timeout=30)
    try:
        with principal_transaction(pool, ada_id, ["Engineering"]) as conn:
            # No WHERE: row-level security alone keeps ada to her own rows.
            assert conn.execute("SELECT principal_id::text FROM row_bot_org.server_sessions").fetchall() == [(ada_id,)]
            assert conn.execute("SELECT name FROM row_bot_org.principal_groups").fetchall() == [("Engineering",)]
            assert conn.execute("SELECT id::text FROM row_bot_org.principals").fetchall() == [(ada_id,)]
            # Ada can't revoke Bob's session, nor make or change a principal.
            assert conn.execute("UPDATE row_bot_org.server_sessions SET revoked_at = now()"
                                " WHERE principal_id = %s", (bob_id,)).rowcount == 0
            assert conn.execute("UPDATE row_bot_org.principals SET status = 'active'").rowcount == 0
        with pool.connection() as conn:
            # Outside a principal's transaction: nothing.
            assert conn.execute("SELECT count(*) FROM row_bot_org.server_sessions").fetchone() == (0,)
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute("SELECT * FROM row_bot_org.auth_attempts")
        with pool.connection() as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("INSERT INTO row_bot_org.principal_groups VALUES (%s, 'admins')", (ada_id,))
    finally:
        pool.close()


@pytest.mark.parametrize("change", ["revoked_at = NULL", "expires_at = now() + interval '100 years'",
                                    "idle_timeout = interval '100 years', idle_expires_at = now() + interval '100 years'"],
                         ids=["un-revoke", "lifetime", "idle"])
def test_the_runtime_may_revoke_its_sessions_but_never_revive_or_extend_one(org_database, change: str) -> None:
    with running_idp("okta") as idp, org_server(org_database, idp) as client:
        sign_in(client, idp)
        principal_id = client.get("/api/me").json()["id"]
    # An operator signs the person out.
    owner_sql(org_database, "UPDATE row_bot_org.server_sessions SET revoked_at = now()")

    pool = create_pool(org_database.runtime_url, max_size=1)
    pool.open(wait=True, timeout=30)
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege), \
                principal_transaction(pool, principal_id, []) as conn:
            conn.execute(f"UPDATE row_bot_org.server_sessions SET {change}")
    finally:
        pool.close()
    assert owner_sql(org_database, "SELECT count(*) FROM row_bot_org.server_sessions WHERE revoked_at IS NULL"
                                   " OR expires_at > now() + interval '1 day'") == [(0,)]


def test_the_session_lookup_returns_one_row_for_one_verifier_and_nothing_for_a_guess(org_database) -> None:
    with running_idp("okta") as idp, org_server(org_database, idp) as client:
        sign_in(client, idp)
        sign_in(browser(client), idp, subject="bob")

    with psycopg.connect(org_database.runtime_url, autocommit=True) as conn:
        assert conn.execute("SELECT * FROM row_bot_org.resume_session(%s)", (b"\x00" * 32,)).fetchall() == []
        assert conn.execute("SELECT * FROM row_bot_org.resume_session(NULL)").fetchall() == []
        # The verifiers themselves can't be listed to try them.
        assert conn.execute("SELECT count(*) FROM row_bot_org.server_sessions").fetchone() == (0,)


# -- the access log ---------------------------------------------------------------------------------

@pytest.fixture
def log_lines() -> Iterator[Callable[[], str]]:
    root = logging.getLogger()
    saved = (root.handlers[:], root.level)
    stream = io.StringIO()
    configure_logging("DEBUG", stream)
    yield stream.getvalue
    root.handlers[:], level = saved
    root.setLevel(level)


def test_the_access_log_never_has_the_code_state_cookie_or_a_query(org_database, idp: FakeIdp,
                                                                   log_lines: Callable[[], str]) -> None:
    with org_server(org_database, idp) as client:
        login = start(client)
        callback = idp.authorize(login.headers["location"])
        done = client.get(callback, follow_redirects=False)
        principal_id = client.get("/api/me").json()["id"]
        client.post("/auth/logout", headers=ORIGIN)

    text = log_lines()
    query = parse_qs(urlsplit(callback).query)
    secrets = [query["code"][0], query["state"][0], login.cookies.get(SIGN_IN_COOKIE), done.cookies.get(SESSION_COOKIE),
               parse_qs(urlsplit(login.headers["location"]).query)["nonce"][0], "?"]
    for secret in secrets:
        assert secret and secret not in text
    requests = [json.loads(line) for line in text.splitlines() if '"row_bot_org.access"' in line]
    assert [(r["method"], r["route"], r["status"], r["principal"]) for r in requests] == [
        ("GET", "/auth/login", 302, None),
        ("GET", "/auth/callback", 303, None),
        ("GET", "/api/me", 200, principal_id),
        ("POST", "/auth/logout", 204, principal_id),
    ]
