"""People, groups and roles end to end: who is an admin (a stored role or a mapped admin group), the first-admin
CLI, the people admin API (list, suspend and resume, set and clear the admin role), their audit events, and
row-level security for a route that forgets the admin check. The fake provider for sign-in and a real
(throwaway) Postgres as the runtime role. Security-sensitive: these run in the pull request lane's Org job.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from tests.fixtures.postgres import import_psycopg

psycopg = import_psycopg()
pytest.importorskip("joserfc", reason="joserfc is not installed: uv sync --locked --all-extras --group test --group org")

from fastapi.testclient import TestClient  # noqa: E402

from row_bot_org import cli  # noqa: E402
from row_bot_org.app import create_app  # noqa: E402
from row_bot_org.db.pool import create_pool  # noqa: E402
from row_bot_org.db.rls import principal_transaction  # noqa: E402
from row_bot_org.settings import load_settings  # noqa: E402
from tests.fixtures.oidc_idp import CLIENT_SECRET, PUBLIC_URL, FakeIdp, running_idp  # noqa: E402
from tests.helpers.org_audit import stored_events, summary  # noqa: E402

pytestmark = pytest.mark.postgres

PEPPER = "a-test-pepper-that-is-long-enough-0123456789"
ORIGIN = {"Origin": PUBLIC_URL}
GATEWAY_KEY = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")
# "IT Admins" (the provider's) maps to the admin group "it-admins"; Sales maps to itself.
GROUP_MAP = {"IT Admins": "it-admins", "Sales": "Sales"}
ADMIN_REQUIRED = {"error": "admin_required"}


@pytest.fixture(autouse=True)
def org_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.startswith("ROW_BOT_ORG_") or name == "ROW_BOT_SECRETS_DIR":
            monkeypatch.delenv(name)


@pytest.fixture
def idp() -> Iterator[FakeIdp]:
    with running_idp("okta") as running:
        yield running


def idp_env(idp: FakeIdp, admin_groups: list[str]) -> dict[str, str]:
    return {"ROW_BOT_ORG_IDP": json.dumps(idp.config(group_map=GROUP_MAP, admin_groups=admin_groups)),
            "ROW_BOT_ORG_IDP_CLIENT_SECRET": CLIENT_SECRET, "ROW_BOT_ORG_PUBLIC_URL": PUBLIC_URL,
            "ROW_BOT_ORG_HMAC_PEPPER": PEPPER}


@contextmanager
def org_server(database, idp: FakeIdp, admin_groups: list[str] | None = None) -> Iterator[TestClient]:
    settings = load_settings({**idp_env(idp, ["it-admins"] if admin_groups is None else admin_groups),
                              "ROW_BOT_ORG_GATEWAY_KEY": GATEWAY_KEY})
    pool = create_pool(database.runtime_url, max_size=4)
    with TestClient(create_app(settings, pool), base_url=PUBLIC_URL) as client:
        yield client


def person(server: TestClient, idp: FakeIdp, subject: str, groups: list[str] | None = None,
           email: str | None = None) -> TestClient:
    """A browser of its own, signed in as ``subject`` with the provider's ``groups``."""
    browser = TestClient(server.app, base_url=PUBLIC_URL, headers=ORIGIN)
    location = browser.get("/auth/login", follow_redirects=False).headers["location"]
    done = browser.get(idp.authorize(location, subject=subject, groups=groups or [],
                                     email=email or f"{subject}@example.com"), follow_redirects=False)
    assert done.status_code == 303, done.text
    return browser


def sign_in_again(browser: TestClient, idp: FakeIdp, subject: str) -> int:
    location = browser.get("/auth/login", follow_redirects=False).headers["location"]
    return browser.get(idp.authorize(location, subject=subject, email=f"{subject}@example.com"),
                       follow_redirects=False).status_code


def me(browser: TestClient) -> str:
    return browser.get("/api/me").json()["id"]


def grant_admin(database, monkeypatch: pytest.MonkeyPatch, who: str, *, action: str = "grant-admin",
                env: dict[str, str] | None = None) -> int:
    monkeypatch.setenv("ROW_BOT_ORG_MIGRATION_DATABASE_URL", database.owner_url)
    for name, value in (env or {}).items():
        monkeypatch.setenv(name, value)
    return cli.main(["principals", action, who])


def audit_events(database, *actions: str) -> list[tuple]:
    return [summary(event) for event in stored_events(database.owner_url, *actions)]


# -- who is an admin ------------------------------------------------------------------------------

def test_a_stored_admin_and_a_mapped_admin_group_make_admins_and_everyone_else_is_a_member(
        org_database, idp, monkeypatch) -> None:
    with org_server(org_database, idp) as server:
        sam = person(server, idp, "sam", ["Sales"])
        it = person(server, idp, "it", ["IT Admins"])
        # The provider sends a group literally named like the admin group: unmapped, it is dropped.
        mallory = person(server, idp, "mallory", ["it-admins", "Sales"])
        owner = person(server, idp, "owner")
        assert grant_admin(org_database, monkeypatch, "owner@example.com") == 0

        for browser in (sam, mallory):
            refused = browser.get("/api/admin/people")
            assert (refused.status_code, refused.json()) == (403, ADMIN_REQUIRED)
        assert it.get("/api/admin/people").status_code == 200
        listed = {p["email"]: p for p in owner.get("/api/admin/people").json()["people"]}

    assert {email: (p["role"], p["admin"], p["groups"], p["suspended"]) for email, p in listed.items()} == {
        "sam@example.com": ("member", False, ["Sales"], False),
        "it@example.com": ("member", True, ["it-admins"], False),
        "mallory@example.com": ("member", False, ["Sales"], False),
        "owner@example.com": ("admin", True, [], False),
    }
    assert listed["owner@example.com"]["last_sign_in_at"]


def test_without_admin_groups_a_mapped_group_is_just_a_group(org_database, idp) -> None:
    with org_server(org_database, idp, admin_groups=[]) as server:
        it = person(server, idp, "it", ["IT Admins"])
        assert it.get("/api/admin/people").status_code == 403


def test_a_non_admin_is_refused_on_every_admin_route(org_database, idp) -> None:
    with org_server(org_database, idp) as server:
        sam = person(server, idp, "sam", ["Sales"])
        target = me(person(server, idp, "ada"))
        routes = [(method.upper(), path) for path, operations in server.app.openapi()["paths"].items()
                  if path.startswith("/api/admin/") for method in operations]
        # Policy, gateway, people, audit, devices, the company brain and its connectors: every admin route the
        # server has.
        assert {path.split("/")[3] for _, path in routes} == {"policy", "endpoints", "models", "budgets", "usage",
                                                             "people", "audit", "devices", "brain", "connectors"}
        for method, path in routes:
            url = path.replace("{principal_id}", target).replace("{group}", "Sales")
            for name in ("{endpoint_id}", "{model_id}", "{budget_id}", "{device_id}", "{space_id}", "{connector_id}"):
                url = url.replace(name, str(uuid.uuid4()))
            refused = sam.request(method, url, json={"role": "admin", "version": 0})
            assert (refused.status_code, refused.json()) == (403, ADMIN_REQUIRED), (method, path)
    with psycopg.connect(org_database.owner_url) as conn:
        assert conn.execute("SELECT count(*) FROM row_bot_org.principals WHERE role = 'admin'").fetchone() == (0,)
        assert conn.execute("SELECT count(*) FROM row_bot_org.policies").fetchone() == (0,)


# -- the people API -------------------------------------------------------------------------------

def test_an_admin_sets_and_clears_the_admin_role_and_each_change_is_audited(org_database, idp) -> None:
    with org_server(org_database, idp) as server:
        it = person(server, idp, "it", ["IT Admins"])
        ada = person(server, idp, "ada")
        ada_id = me(ada)
        granted = it.put(f"/api/admin/people/{ada_id}/role", json={"role": "admin"})
        assert (granted.status_code, granted.json()["role"], granted.json()["admin"]) == (200, "admin", True)
        assert ada.get("/api/admin/people").status_code == 200
        assert it.put(f"/api/admin/people/{ada_id}/role", json={"role": "admin"}).status_code == 200  # no change
        cleared = it.put(f"/api/admin/people/{ada_id}/role", json={"role": "member"})
        assert (cleared.status_code, cleared.json()["role"]) == (200, "member")
        assert ada.get("/api/admin/people").status_code == 403

        assert it.put(f"/api/admin/people/{uuid.uuid4()}/role", json={"role": "admin"}).json() == {
            "error": "person_not_found"}
        assert it.put("/api/admin/people/not-an-id/role", json={"role": "admin"}).status_code == 404
        assert it.put(f"/api/admin/people/{ada_id}/role", json={"role": "owner"}).status_code == 422
        it_id = me(it)

    assert audit_events(org_database, "role.changed") == [
        ("role.changed", "admin", it_id, ada_id, "succeeded", {"role": "admin", "change": "granted"}),
        ("role.changed", "admin", it_id, ada_id, "succeeded", {"role": "admin", "change": "removed"}),
    ]


def test_an_admin_can_neither_suspend_themself_nor_change_their_own_role(org_database, idp, monkeypatch) -> None:
    with org_server(org_database, idp) as server:
        owner = person(server, idp, "owner")
        assert grant_admin(org_database, monkeypatch, "owner@example.com") == 0
        owner_id = me(owner)

        demoted = owner.put(f"/api/admin/people/{owner_id}/role", json={"role": "member"})
        suspended = owner.post(f"/api/admin/people/{owner_id}/suspend")
        assert (demoted.status_code, demoted.json()) == (409, {"error": "cannot_change_your_own_role"})
        assert (suspended.status_code, suspended.json()) == (409, {"error": "cannot_suspend_yourself"})
        assert owner.get("/api/admin/people").json()["people"][0]["role"] == "admin"

        # An admin through the provider group can't store a role the provider couldn't take back.
        it = person(server, idp, "it", groups=["IT Admins"])
        promoted = it.put(f"/api/admin/people/{me(it)}/role", json={"role": "admin"})
        assert (promoted.status_code, promoted.json()) == (409, {"error": "cannot_change_your_own_role"})
        listed = {p["id"]: p for p in owner.get("/api/admin/people").json()["people"]}
        assert (listed[me(it)]["role"], listed[me(it)]["admin"]) == ("member", True)


def test_suspending_revokes_every_session_and_refuses_sign_in_until_resumed(org_database, idp) -> None:
    with org_server(org_database, idp) as server:
        it = person(server, idp, "it", ["IT Admins"])
        ada = person(server, idp, "ada", ["Sales"])
        laptop = person(server, idp, "ada", ["Sales"])
        ada_id = me(ada)

        suspended = it.post(f"/api/admin/people/{ada_id}/suspend")
        assert (suspended.status_code, suspended.json()["suspended"]) == (200, True)
        assert ada.get("/api/me").status_code == 401 and laptop.get("/api/me").status_code == 401
        assert sign_in_again(ada, idp, "ada") == 403
        resumed = it.post(f"/api/admin/people/{ada_id}/resume")
        assert (resumed.status_code, resumed.json()["suspended"]) == (200, False)
        assert sign_in_again(ada, idp, "ada") == 303
        assert ada.get("/api/me").json()["id"] == ada_id
        it_id = me(it)

    # Suspending signs both of Ada's browsers out; her sign-in while suspended is refused, then she's back.
    assert audit_events(org_database, "person.suspended", "person.resumed", "session.revoked") == [
        ("person.suspended", "admin", it_id, ada_id, "succeeded", {}),
        ("session.revoked", "admin", it_id, ada_id, "succeeded", {}),
        ("person.resumed", "admin", it_id, ada_id, "succeeded", {})]
    assert [(event[2], event[4]) for event in audit_events(org_database, "session.signed_in")
            if event[2] == ada_id] == [(ada_id, "succeeded")] * 2 + [(ada_id, "denied"), (ada_id, "succeeded")]
    with psycopg.connect(org_database.owner_url) as conn:
        assert conn.execute("SELECT count(*) FROM row_bot_org.server_sessions WHERE principal_id = %s"
                            " AND revoked_at IS NULL", (ada_id,)).fetchone() == (1,)  # the new one


# -- the first admin: the CLI ---------------------------------------------------------------------

def test_the_operator_grants_and_revokes_admin_by_email_or_id(org_database, idp, monkeypatch, capsys) -> None:
    with org_server(org_database, idp) as server:
        owner, second = person(server, idp, "owner", email="Owner@Example.com"), person(server, idp, "second")
        owner_id, second_id = me(owner), me(second)
        assert owner.get("/api/admin/people").status_code == 403

        assert grant_admin(org_database, monkeypatch, "owner@example.com") == 0
        assert f"Principal {owner_id} (Owner@Example.com) is now an admin." in capsys.readouterr().out
        assert owner.get("/api/admin/people").status_code == 200
        assert grant_admin(org_database, monkeypatch, owner_id) == 0
        assert "is already an admin" in capsys.readouterr().out
        assert grant_admin(org_database, monkeypatch, second_id) == 0
        assert grant_admin(org_database, monkeypatch, "OWNER@example.com", action="revoke-admin") == 0
        assert "is no longer an admin" in capsys.readouterr().out
        assert owner.get("/api/admin/people").status_code == 403

    # The CLI has no principal: its events are the operator's.
    assert audit_events(org_database, "role.changed") == [
        ("role.changed", "operator", "cli", owner_id, "succeeded", {"role": "admin", "change": "granted"}),
        ("role.changed", "operator", "cli", second_id, "succeeded", {"role": "admin", "change": "granted"}),
        ("role.changed", "operator", "cli", owner_id, "succeeded", {"role": "admin", "change": "removed"}),
    ]


def test_the_cli_refuses_someone_who_hasnt_signed_in_and_an_email_two_people_share(org_database, idp,
                                                                                   monkeypatch, capsys) -> None:
    with org_server(org_database, idp) as server:
        person(server, idp, "one", email="shared@example.com")
        person(server, idp, "two", email="shared@example.com")

    assert grant_admin(org_database, monkeypatch, "nobody@example.com") == 1
    assert "Nobody with the id or email nobody@example.com has signed in" in capsys.readouterr().err
    assert grant_admin(org_database, monkeypatch, str(uuid.uuid4())) == 1
    assert grant_admin(org_database, monkeypatch, "shared@example.com") == 1
    assert "2 principals have the email shared@example.com: name one by its id" in capsys.readouterr().err
    with psycopg.connect(org_database.owner_url) as conn:
        assert conn.execute("SELECT count(*) FROM row_bot_org.principals WHERE role = 'admin'").fetchone() == (0,)


def test_the_last_stored_admin_stays_unless_admin_groups_are_set(org_database, idp, monkeypatch, capsys) -> None:
    with org_server(org_database, idp) as server:
        owner = person(server, idp, "owner")
        assert grant_admin(org_database, monkeypatch, "owner@example.com") == 0
        capsys.readouterr()

        assert grant_admin(org_database, monkeypatch, "owner@example.com", action="revoke-admin") == 1
        assert ("is the last admin, and ROW_BOT_ORG_IDP sets no admin_groups: grant another admin first"
                in capsys.readouterr().err)
        assert owner.get("/api/admin/people").status_code == 200

        assert grant_admin(org_database, monkeypatch, "owner@example.com", action="revoke-admin",
                           env=idp_env(idp, ["it-admins"])) == 0
        assert owner.get("/api/admin/people").status_code == 403


def test_a_suspended_admin_doesnt_count_as_the_one_left(org_database, idp, monkeypatch, capsys) -> None:
    with org_server(org_database, idp, admin_groups=[]) as server:
        owner, deputy = person(server, idp, "owner"), person(server, idp, "deputy")
        for who in ("owner@example.com", "deputy@example.com"):
            assert grant_admin(org_database, monkeypatch, who) == 0
        assert owner.post(f"/api/admin/people/{me(deputy)}/suspend").status_code == 200
        capsys.readouterr()

        assert grant_admin(org_database, monkeypatch, "owner@example.com", action="revoke-admin") == 1
        assert "is the last admin" in capsys.readouterr().err
        assert grant_admin(org_database, monkeypatch, "deputy@example.com", action="revoke-admin") == 0


# -- row-level security ---------------------------------------------------------------------------

def test_a_route_that_skips_the_admin_check_acts_as_a_member(org_database, idp, monkeypatch) -> None:
    with org_server(org_database, idp) as server:
        owner, ada = person(server, idp, "owner"), person(server, idp, "ada")
        owner_id, ada_id = me(owner), me(ada)
    assert grant_admin(org_database, monkeypatch, owner_id) == 0

    pool = create_pool(org_database.runtime_url, max_size=1)
    pool.open(wait=True, timeout=30)
    try:
        # The stored admin, without enter_admin's flag: their own row only, and no change to anyone's.
        with principal_transaction(pool, owner_id) as conn:
            assert conn.execute("SELECT id::text FROM row_bot_org.principals").fetchall() == [(owner_id,)]
            assert conn.execute("SELECT count(*) FROM row_bot_org.server_sessions").fetchone() == (1,)
            assert conn.execute("UPDATE row_bot_org.principals SET role = 'admin'").rowcount == 0
            assert conn.execute("UPDATE row_bot_org.principals SET status = 'suspended'").rowcount == 0
            assert conn.execute("UPDATE row_bot_org.server_sessions SET revoked_at = now()"
                                " WHERE principal_id = %s", (ada_id,)).rowcount == 0
        # With the flag (after the check) they see and change people, but only their role and status.
        with principal_transaction(pool, owner_id) as conn:
            conn.execute("SELECT set_config('row_bot.admin', 'on', true)")
            assert conn.execute("SELECT count(*) FROM row_bot_org.principals").fetchone() == (2,)
            assert conn.execute("UPDATE row_bot_org.principals SET status = 'suspended' WHERE id = %s",
                                (ada_id,)).rowcount == 1
            conn.execute("UPDATE row_bot_org.principals SET status = 'active' WHERE id = %s", (ada_id,))
        with pytest.raises(psycopg.errors.InsufficientPrivilege), principal_transaction(pool, owner_id) as conn:
            conn.execute("SELECT set_config('row_bot.admin', 'on', true)")
            conn.execute("UPDATE row_bot_org.principals SET email = 'someone@else.example' WHERE id = %s", (ada_id,))
        with pytest.raises(psycopg.errors.InsufficientPrivilege), principal_transaction(pool, owner_id) as conn:
            conn.execute("SELECT set_config('row_bot.admin', 'on', true)")
            conn.execute("UPDATE row_bot_org.server_sessions SET revoked_at = NULL")
    finally:
        pool.close()
