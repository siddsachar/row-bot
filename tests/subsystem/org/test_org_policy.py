"""The policy service end to end: people signed in through the fake provider (tests/fixtures/oidc_idp.py),
the server's policy routes and a real (throwaway) Postgres as the runtime role.

Covers the effective policy (merged from the person's groups, its version, ETag and 304), admin-only
reads and writes (the placeholder admin check, S2's to replace), strict validation, versions, history and
conflicts, the policy.changed audit event, and row-level security on the policies table.
Security-sensitive: these run in the pull request lane's Org job.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from tests.fixtures.postgres import import_psycopg

psycopg = import_psycopg()
pytest.importorskip("joserfc", reason="joserfc is not installed: uv sync --locked --all-extras --group test --group org")

from fastapi.testclient import TestClient  # noqa: E402
from psycopg.types.json import Jsonb  # noqa: E402

from row_bot.policy import DEFAULT_POLICY, MAX_DOCUMENT_BYTES, policy_to_dict  # noqa: E402
from row_bot_org.app import create_app  # noqa: E402
from row_bot_org.db.pool import create_pool  # noqa: E402
from row_bot_org.db.rls import principal_transaction  # noqa: E402
from row_bot_org.identity import roles  # noqa: E402
from row_bot_org.settings import load_settings  # noqa: E402
from tests.fixtures.oidc_idp import CLIENT_SECRET, PUBLIC_URL, FakeIdp, running_idp  # noqa: E402

pytestmark = pytest.mark.postgres

PEPPER = "a-test-pepper-that-is-long-enough-0123456789"
ORIGIN = {"Origin": PUBLIC_URL}
DEFAULTS = policy_to_dict(DEFAULT_POLICY)


@contextmanager
def org_server(database, idp: FakeIdp) -> Iterator[TestClient]:
    settings = load_settings({
        "ROW_BOT_ORG_IDP": json.dumps(idp.config()), "ROW_BOT_ORG_IDP_CLIENT_SECRET": CLIENT_SECRET,
        "ROW_BOT_ORG_PUBLIC_URL": PUBLIC_URL, "ROW_BOT_ORG_HMAC_PEPPER": PEPPER,
    })
    pool = create_pool(database.runtime_url, max_size=4)
    with TestClient(create_app(settings, pool), base_url=PUBLIC_URL) as client:
        yield client


def person(client: TestClient, idp: FakeIdp, subject: str, groups: list[str]) -> TestClient:
    """A browser of its own, signed in as ``subject``, a member of ``groups``."""
    browser = TestClient(client.app, base_url=PUBLIC_URL)
    location = browser.get("/auth/login", follow_redirects=False).headers["location"]
    done = browser.get(idp.authorize(location, subject=subject, groups=groups), follow_redirects=False)
    assert done.status_code == 303, done.text
    return browser


def principal_id(browser: TestClient) -> str:
    return browser.get("/api/me").json()["id"]


@pytest.fixture
def idp() -> Iterator[FakeIdp]:
    with running_idp("okta") as running:
        yield running


@pytest.fixture
def admins(monkeypatch: pytest.MonkeyPatch) -> set[str]:
    """Principal ids the admin check says yes to (standing in for S2's roles)."""
    chosen: set[str] = set()
    monkeypatch.setattr(roles, "is_admin", lambda principal: principal.principal_id in chosen)
    return chosen


@pytest.fixture
def server(org_database, idp: FakeIdp, admins: set[str]):
    with org_server(org_database, idp) as client:
        admin = person(client, idp, "admin-1", ["IT"])
        admins.add(principal_id(admin))
        yield client, admin


def put(browser: TestClient, document: object, group: str | None = None):
    path = "/api/admin/policy" + ("" if group is None else f"/groups/{group}")
    return browser.put(path, content=json.dumps(document), headers=ORIGIN)


# -- the effective policy -----------------------------------------------------------------------------

def test_with_no_policy_a_person_gets_the_defaults_with_an_etag_and_a_304(server, idp: FakeIdp) -> None:
    client, _ = server
    member = person(client, idp, "member-1", ["Engineering"])

    first = member.get("/api/policy/effective")
    assert first.status_code == 200 and first.json() == DEFAULTS
    etag = first.headers["etag"]
    assert etag.startswith('"') and etag.endswith('"')

    for header in (etag, f"W/{etag}", f'"other", {etag}', "*"):
        again = member.get("/api/policy/effective", headers={"If-None-Match": header})
        assert (again.status_code, again.content, again.headers["etag"]) == (304, b"", etag), header
    assert member.get("/api/policy/effective", headers={"If-None-Match": '"other"'}).status_code == 200


def test_a_change_gives_a_new_version_and_etag(server, idp: FakeIdp) -> None:
    client, admin = server
    member = person(client, idp, "member-1", ["Engineering"])
    before = member.get("/api/policy/effective")

    assert put(admin, {"approvals": {"max_level": "approve"}}).status_code == 200

    after = member.get("/api/policy/effective", headers={"If-None-Match": before.headers["etag"]})
    assert after.status_code == 200
    assert after.headers["etag"] != before.headers["etag"]
    assert after.json()["approvals"]["max_level"] == "approve"
    assert after.json()["version"] > before.json()["version"] == 0


def test_a_person_gets_the_company_policy_widened_by_their_own_groups_only(server, idp: FakeIdp) -> None:
    client, admin = server
    assert put(admin, {"tools": {"disabled_groups": ["shell", "browser"]},
                       "models": {"allowed": ["base-model"]}}).status_code == 200
    assert put(admin, {"tools": {"enabled_groups": ["shell"]}, "models": {"allowed": ["code-model"]}},
               "Engineering").status_code == 200
    assert put(admin, {"tools": {"enabled_groups": ["browser"]}, "approvals": {"max_level": "full"}},
               "Design").status_code == 200

    nobody = person(client, idp, "no-groups", []).get("/api/policy/effective").json()
    engineer = person(client, idp, "engineer", ["Engineering"]).get("/api/policy/effective").json()
    both = person(client, idp, "both", ["Engineering", "Design"]).get("/api/policy/effective").json()
    sales = person(client, idp, "sales", ["Sales"]).get("/api/policy/effective").json()

    assert nobody["tools"]["disabled_groups"] == ["browser", "shell"]
    assert nobody["models"]["allowed"] == ["base-model"] and nobody["approvals"]["max_level"] == "auto"
    assert sales == nobody
    assert engineer["tools"]["disabled_groups"] == ["browser"]
    assert engineer["models"]["allowed"] == ["base-model", "code-model"]
    assert engineer["approvals"]["max_level"] == "auto"
    assert both["tools"]["disabled_groups"] == [] and both["approvals"]["max_level"] == "full"
    assert both["version"] > engineer["version"] > nobody["version"]


def test_an_empty_override_takes_the_groups_override_away(server, idp: FakeIdp) -> None:
    client, admin = server
    put(admin, {"approvals": {"max_level": "full"}}, "Engineering")
    put(admin, {}, "Engineering")

    engineer = person(client, idp, "engineer", ["Engineering"])
    assert engineer.get("/api/policy/effective").json()["approvals"]["max_level"] == "auto"
    assert "Engineering" not in admin.get("/api/admin/policy").json()["groups"]


def test_the_effective_policy_needs_a_sign_in(server) -> None:
    client, _ = server
    anonymous = TestClient(client.app, base_url=PUBLIC_URL)
    assert anonymous.get("/api/policy/effective").status_code == 401


# -- admin only ---------------------------------------------------------------------------------------

def test_a_member_can_neither_read_nor_change_the_policies(server, idp: FakeIdp, org_database) -> None:
    client, _ = server
    member = person(client, idp, "member-1", ["Engineering"])

    assert member.get("/api/admin/policy").json() == {"error": "admin_required"}
    for response in (member.get("/api/admin/policy"), member.get("/api/admin/policy/history"),
                     put(member, {"approvals": {"max_level": "full"}}),
                     put(member, {"approvals": {"max_level": "full"}}, "Engineering"),
                     put(member, "not even a policy")):
        assert response.status_code == 403
    assert _rows(org_database) == []


def test_until_s2_the_placeholder_makes_nobody_an_admin(org_database, idp: FakeIdp) -> None:
    with org_server(org_database, idp) as client:
        someone = person(client, idp, "it-person", ["IT", "Admins"])
        assert someone.get("/api/admin/policy").status_code == 403
        assert put(someone, {}).status_code == 403


def test_a_write_from_another_site_is_refused_before_anything_else(server) -> None:
    _, admin = server
    response = admin.put("/api/admin/policy", content="{}", headers={"Origin": "https://elsewhere.example"})
    assert (response.status_code, response.json()) == (403, {"error": "same_origin_required"})


# -- validation ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("body,detail", [
    ('{"models": {"allowed": ["ok", "not ok"]}}', "models.allowed[1]: not a valid entry"),
    ('{"models": {"surprise": true}}', "models.surprise: not a company policy setting"),
    ('{"tools": {"enabled_groups": ["shell"]}}', "tools.enabled_groups: not a company policy setting"),
    ('{"approvals": {}, "approvals": {}}', "approvals: appears twice"),
    ('[]', "policy: expected an object"),
])
def test_an_invalid_company_policy_is_refused_with_the_setting_at_fault(server, org_database, body: str,
                                                                         detail: str) -> None:
    _, admin = server
    response = admin.put("/api/admin/policy", content=body, headers=ORIGIN)
    assert (response.status_code, response.json()) == (422, {"error": "invalid_policy", "detail": detail})
    assert _rows(org_database) == []


def test_an_invalid_override_a_bad_group_name_and_an_oversize_body_are_refused(server, org_database) -> None:
    _, admin = server

    response = put(admin, {"tools": {"disabled_groups": ["shell"]}}, "Engineering")
    assert response.status_code == 422 and response.json()["detail"] == (
        "tools.disabled_groups: not a group override setting")
    assert put(admin, {}, " padded").json() == {"error": "invalid_group"}
    assert put(admin, {}, "x" * 257).status_code == 422
    oversize = admin.put("/api/admin/policy", content=b" " * (MAX_DOCUMENT_BYTES + 1), headers=ORIGIN)
    assert (oversize.status_code, oversize.json()) == (413, {"error": "policy_too_large"})
    assert _rows(org_database) == []


def test_a_group_name_may_hold_a_slash(server, idp: FakeIdp) -> None:
    client, admin = server
    assert put(admin, {"data": {"chat_export": False}}).status_code == 200
    assert put(admin, {"data": {"chat_export": True}}, "Eng/Platform").status_code == 200

    platform = person(client, idp, "platform", ["Eng/Platform"])
    assert platform.get("/api/policy/effective").json()["data"]["chat_export"] is True


# -- versions and history -----------------------------------------------------------------------------

def test_each_write_is_a_new_version_kept_in_the_history(server) -> None:
    _, admin = server
    admin_id = principal_id(admin)

    first = put(admin, {"offline": {"max_days": 3}}).json()
    second = put(admin, {"approvals": {"max_level": "full"}}, "Engineering").json()
    third = put(admin, {"offline": {"max_days": 1}, "models": {"allowed": ["b", "a"]}}).json()
    assert first == {"version": first["version"], "offline": {"max_days": 3}}
    assert first["version"] < second["version"] < third["version"]
    assert third == {"version": third["version"], "models": {"allowed": ["a", "b"]}, "offline": {"max_days": 1}}

    current = admin.get("/api/admin/policy").json()
    assert current == {"company": third, "groups": {"Engineering": second}}

    entries = admin.get("/api/admin/policy/history").json()["entries"]
    assert [(entry["version"], entry["group"]) for entry in entries] == [
        (third["version"], None), (second["version"], "Engineering"), (first["version"], None)]
    assert {entry["changed_by"] for entry in entries} == {admin_id}
    assert entries[2]["policy"] == first and entries[2]["changed_at"]

    page = admin.get("/api/admin/policy/history", params={"limit": 1, "before": third["version"]}).json()
    assert [entry["version"] for entry in page["entries"]] == [second["version"]]
    assert admin.get("/api/admin/policy/history", params={"limit": 0}).status_code == 422


def test_with_no_policy_the_company_policy_is_version_0(server) -> None:
    _, admin = server
    assert admin.get("/api/admin/policy").json() == {"company": {"version": 0}, "groups": {}}
    assert put(admin, {"version": 0, "offline": {"max_days": 2}}).status_code == 200


def test_a_write_naming_an_old_version_is_refused_as_a_conflict(server, org_database) -> None:
    _, admin = server
    stored = put(admin, {"offline": {"max_days": 3}}).json()
    group = put(admin, {"approvals": {"max_level": "full"}}, "Engineering").json()

    # Another admin's write came in between: the version this one read is no longer the newest.
    newer = put(admin, {**stored, "offline": {"max_days": 5}}).json()
    stale = put(admin, {**stored, "offline": {"max_days": 9}})
    assert (stale.status_code, stale.json()) == (409, {"error": "version_conflict", "current": newer["version"]})
    # Each scope has its own newest version.
    assert put(admin, {**group, "approvals": {"max_level": "auto"}}, "Engineering").status_code == 200
    assert put(admin, {"version": 0}, "Design").status_code == 200
    assert put(admin, {"version": 0}, "Design").status_code == 409
    assert len(_rows(org_database)) == 5


def test_each_change_emits_policy_changed(server, caplog: pytest.LogCaptureFixture) -> None:
    _, admin = server
    with caplog.at_level(logging.INFO, logger="row_bot_org.audit"):
        stored = put(admin, {"data": {"share_to_company": False}}, "Contractors").json()
        put(admin, {"bad": {}})

    events = [record for record in caplog.records if record.name == "row_bot_org.audit"]
    assert [(record.getMessage(), record.fields) for record in events] == [
        ("policy.changed", {"action": "policy.changed", "actor": principal_id(admin), "group": "Contractors",
                            "version": stored["version"]})]


# -- row-level security -------------------------------------------------------------------------------

def _rows(database) -> list[tuple]:
    with psycopg.connect(database.owner_url) as conn:
        return conn.execute("SELECT version, group_name FROM row_bot_org.policies ORDER BY version").fetchall()


def test_row_level_security_keeps_a_member_to_the_company_and_its_own_groups(server, org_database) -> None:
    _, admin = server
    admin_id = principal_id(admin)
    put(admin, {"offline": {"max_days": 3}})
    put(admin, {"approvals": {"max_level": "full"}}, "Engineering")
    put(admin, {"approvals": {"max_level": "full"}}, "Finance")
    pool = create_pool(org_database.runtime_url, max_size=1)
    pool.open()
    try:
        everything = "SELECT group_name FROM row_bot_org.policies ORDER BY version"
        with principal_transaction(pool, "00000000-0000-4000-8000-000000000001", ["Engineering"]) as conn:
            assert conn.execute(everything).fetchall() == [(None,), ("Engineering",)]
        with principal_transaction(pool, "00000000-0000-4000-8000-000000000001") as conn:
            assert conn.execute(everything).fetchall() == [(None,)]
        with pool.connection() as conn:  # no principal at all
            assert conn.execute(everything).fetchall() == []

        # A member (no admin flag) can't write, even naming itself.
        with pytest.raises(psycopg.errors.InsufficientPrivilege), \
                principal_transaction(pool, admin_id) as conn:
            conn.execute("INSERT INTO row_bot_org.policies (group_name, document, changed_by)"
                         " VALUES (NULL, %s, %s)", (Jsonb({}), admin_id))

        # An admin's transaction reads every row, and writes only as itself.
        with principal_transaction(pool, admin_id) as conn:
            conn.execute("SELECT set_config('row_bot.admin', 'on', true)")
            assert len(conn.execute(everything).fetchall()) == 3
        with pytest.raises(psycopg.errors.InsufficientPrivilege), \
                principal_transaction(pool, admin_id) as conn:
            conn.execute("SELECT set_config('row_bot.admin', 'on', true)")
            conn.execute("INSERT INTO row_bot_org.policies (group_name, document, changed_by)"
                         " VALUES (NULL, %s, %s)", (Jsonb({}), "00000000-0000-4000-8000-000000000002"))

        # Nobody rewrites or deletes the history, an admin included.
        for statement in ("UPDATE row_bot_org.policies SET document = '{}'", "DELETE FROM row_bot_org.policies"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege), \
                    principal_transaction(pool, admin_id) as conn:
                conn.execute("SELECT set_config('row_bot.admin', 'on', true)")
                conn.execute(statement)
        # The admin flag lasts only for its transaction.
        with principal_transaction(pool, admin_id) as conn:
            assert conn.execute(everything).fetchall() == [(None,)]
    finally:
        pool.close()
    assert len(_rows(org_database)) == 3
