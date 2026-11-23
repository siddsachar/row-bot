"""The policy service end to end: people signed in through the fake provider (tests/fixtures/oidc_idp.py),
the server's policy routes and a real (throwaway) Postgres as the runtime role.

Covers the effective policy (merged from the person's groups, its version, ETag and 304), admin-only
reads and writes (an admin by stored role), strict validation, versions, history and
conflicts, the policy.changed audit event, and row-level security on the policies table.
Security-sensitive: these run in the pull request lane's Org job.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from tests.fixtures.postgres import import_psycopg

psycopg = import_psycopg()
pytest.importorskip("joserfc", reason="joserfc is not installed: uv sync --locked --all-extras --group test --group org")

from fastapi.testclient import TestClient  # noqa: E402
from psycopg.types.json import Jsonb  # noqa: E402

from row_bot.policy import (  # noqa: E402
    DEFAULT_POLICY, MAX_DOCUMENT_BYTES, ONLY_WIDEN, parse_policy, policy_to_dict,
)
from row_bot_org import cli  # noqa: E402
from row_bot_org.app import create_app  # noqa: E402
from row_bot_org.db.pool import create_pool  # noqa: E402
from row_bot_org.db.rls import principal_transaction  # noqa: E402
from row_bot_org.policy import store  # noqa: E402
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


def make_admin(database, browser: TestClient) -> None:
    """Store the admin role, as ``row-bot-org principals grant-admin`` does."""
    with psycopg.connect(database.owner_url) as conn:
        conn.execute("UPDATE row_bot_org.principals SET role = 'admin' WHERE id = %s", (principal_id(browser),))


@pytest.fixture
def server(org_database, idp: FakeIdp):
    with org_server(org_database, idp) as client:
        admin = person(client, idp, "admin-1", ["IT"])
        make_admin(org_database, admin)
        yield client, admin


def newest_version(browser: TestClient, group: str | None = None) -> int:
    """The scope's newest version as the admin view shows it (0 when there is none, or for a non-admin)."""
    view = browser.get("/api/admin/policy")
    if view.status_code != 200:
        return 0
    scope = view.json()["company"] if group is None else view.json()["groups"].get(group, {})
    return scope.get("version", 0)


def put(browser: TestClient, document: object, group: str | None = None):
    """Write as a console would: naming the version it replaces, unless the document names one."""
    if isinstance(document, dict) and "version" not in document:
        document = {"version": newest_version(browser, group), **document}
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
    cleared = put(admin, {}, "Engineering").json()["policy"]

    engineer = person(client, idp, "engineer", ["Engineering"])
    assert engineer.get("/api/policy/effective").json()["approvals"]["max_level"] == "auto"
    # The admin view keeps the cleared scope's newest version, so writing it again names that version.
    assert admin.get("/api/admin/policy").json()["groups"] == {"Engineering": {"version": cleared["version"]}}
    assert put(admin, {"version": 0, "approvals": {"max_level": "full"}}, "Engineering").status_code == 409
    assert put(admin, {"version": cleared["version"], "approvals": {"max_level": "full"}},
               "Engineering").status_code == 200


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


def test_a_group_alone_makes_nobody_an_admin(org_database, idp: FakeIdp) -> None:
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


def test_a_write_must_name_the_version_it_replaces(server, org_database) -> None:
    _, admin = server
    for path in ("/api/admin/policy", "/api/admin/policy/groups/Engineering"):
        response = admin.put(path, content='{"approvals": {"max_level": "full"}}', headers=ORIGIN)
        assert (response.status_code, response.json()) == (428, {"error": "version_required"})
    assert _rows(org_database) == []


def test_a_policy_nested_too_deeply_is_refused(server) -> None:
    _, admin = server
    response = admin.put("/api/admin/policy", content="[" * 100_000 + "]" * 100_000, headers=ORIGIN)
    assert (response.status_code, response.json()) == (422, {"error": "invalid_policy",
                                                              "detail": "policy: nested too deeply"})


def test_a_restriction_written_as_a_group_override_is_refused(server, idp: FakeIdp, org_database) -> None:
    client, admin = server
    response = put(admin, {"data": {"share_to_company": False, "chat_export": False},
                           "approvals": {"max_level": "block"}, "apps": {"mcp_servers": "none"},
                           "offline": {"max_days": 0}}, "Contractors")

    assert response.status_code == 422
    assert response.json()["detail"].endswith(": " + ONLY_WIDEN)
    assert _rows(org_database) == []
    contractor = person(client, idp, "contractor", ["Contractors"]).get("/api/policy/effective").json()
    assert contractor == DEFAULTS


def test_an_override_must_add_something_to_the_company_policy(server, org_database) -> None:
    _, admin = server
    assert put(admin, {"approvals": {"max_level": "full"}, "models": {"allowed": ["a", "b"]},
                       "tools": {"disabled_groups": ["browser"]}}).status_code == 200

    # It restates what the company gives and gives back a tool the company doesn't switch off.
    response = put(admin, {"approvals": {"max_level": "auto"}, "models": {"allowed": ["a", "b", "c"]},
                           "tools": {"enabled_groups": ["shell"]}, "offline": {"max_days": 9}}, "Engineering")

    settings = ["models.allowed: a", "models.allowed: b", "tools.enabled_groups: shell", "approvals.max_level"]
    assert (response.status_code, response.json()["settings"]) == (422, settings)
    assert response.json()["detail"] == f"{ONLY_WIDEN}: {', '.join(settings)} add nothing to the company policy"
    assert len(_rows(org_database)) == 1
    assert put(admin, {"models": {"allowed": ["c"]}, "tools": {"enabled_groups": ["browser"]},
                       "offline": {"max_days": 9}}, "Engineering").status_code == 200


def test_a_company_restriction_says_which_overrides_still_grant_what_it_took(server) -> None:
    _, admin = server
    put(admin, {"models": {"allowed": ["a"]}, "approvals": {"max_level": "approve"}})
    put(admin, {"models": {"allowed": ["b"]}, "approvals": {"max_level": "full"}}, "Engineering")
    put(admin, {"approvals": {"max_level": "auto"}}, "Design")

    widened = put(admin, {"models": {"allowed": ["a", "b"]}, "approvals": {"max_level": "full"}}).json()
    assert widened["still_granted_by"] == {}
    narrowed = put(admin, {"models": {"allowed": ["a"]}, "approvals": {"max_level": "approve"}}).json()
    assert narrowed["still_granted_by"] == {"Design": ["approvals.max_level: auto"],
                                            "Engineering": ["models.allowed: b", "approvals.max_level: full"]}
    assert "still_granted_by" not in put(admin, {"offline": {"max_days": 9}}, "Design").json()


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

    first = put(admin, {"offline": {"max_days": 3}}).json()["policy"]
    second = put(admin, {"approvals": {"max_level": "full"}}, "Engineering").json()["policy"]
    third = put(admin, {"offline": {"max_days": 1}, "models": {"allowed": ["b", "a"]}}).json()["policy"]
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
    stored = put(admin, {"offline": {"max_days": 3}}).json()["policy"]
    group = put(admin, {"approvals": {"max_level": "full"}}, "Engineering").json()["policy"]

    # Another admin's write came in between: the version this one read is no longer the newest.
    newer = put(admin, {**stored, "offline": {"max_days": 5}}).json()["policy"]
    stale = put(admin, {**stored, "offline": {"max_days": 9}})
    assert (stale.status_code, stale.json()) == (409, {"error": "version_conflict", "current": newer["version"]})
    # Each scope has its own newest version.
    assert put(admin, {**group, "offline": {"max_days": 30}}, "Engineering").status_code == 200
    assert put(admin, {"version": 0}, "Design").status_code == 200
    assert put(admin, {"version": 0}, "Design").status_code == 409
    assert len(_rows(org_database)) == 5


def test_each_change_emits_policy_changed(server, caplog: pytest.LogCaptureFixture) -> None:
    _, admin = server
    with caplog.at_level(logging.INFO, logger="row_bot_org.audit"):
        refused = put(admin, {"data": {"share_to_company": False}}, "Contractors")
        stored = put(admin, {"data": {"share_to_company": False}}).json()["policy"]
        put(admin, {"bad": {}})

    # A restriction belongs in the company policy: as a group override it is refused, and nothing changes.
    assert refused.status_code == 422 and refused.json()["detail"].endswith(ONLY_WIDEN)
    events = [record for record in caplog.records if record.name == "row_bot_org.audit"]
    assert [(record.getMessage(), record.fields) for record in events] == [
        ("policy.changed", {"action": "policy.changed", "actor": principal_id(admin), "group": None,
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


def _repeatable_read_by_default(database) -> None:
    with psycopg.connect(database.superuser_url, autocommit=True) as conn:
        conn.execute(f"ALTER DATABASE {database.name} SET default_transaction_isolation = 'repeatable read'")


def test_pooled_transactions_are_read_committed_whatever_the_database_default(org_database) -> None:
    _repeatable_read_by_default(org_database)
    with psycopg.connect(org_database.runtime_url) as conn:  # without the pool's options
        assert conn.execute("SHOW transaction_isolation").fetchone() == ("repeatable read",)
    pool = create_pool(org_database.runtime_url, max_size=1)
    pool.open()
    try:
        with principal_transaction(pool, "00000000-0000-4000-8000-000000000001") as conn:
            assert conn.execute("SHOW transaction_isolation").fetchone() == ("read committed",)
    finally:
        pool.close()


def test_two_admins_writing_the_same_version_at_once_give_one_write_and_one_conflict(server, org_database) -> None:
    _, admin = server
    admin_id = principal_id(admin)
    _repeatable_read_by_default(org_database)  # the pool's READ COMMITTED must hold even so
    pool = create_pool(org_database.runtime_url, max_size=2)
    pool.open()
    document = parse_policy({"version": 0, "offline": {"max_days": 3}})
    first_written, release = threading.Event(), threading.Event()
    results: dict[str, object] = {}

    def write(name: str, hold: bool) -> None:
        try:
            with principal_transaction(pool, admin_id) as conn:
                conn.execute("SELECT set_config('row_bot.admin', 'on', true)")  # the snapshot, under RR
                written = store.put(conn, None, document, changed_by=admin_id)
                if hold:
                    first_written.set()
                    release.wait(10)
                results[name] = written.revision.version
        except store.VersionConflict as conflict:
            results[name] = f"conflict at {conflict.current}"

    first = threading.Thread(target=write, args=("first", True))
    second = threading.Thread(target=write, args=("second", False))
    try:
        first.start()
        assert first_written.wait(10)
        second.start()
        # The second waits on the write lock while the first's transaction is still open.
        with psycopg.connect(org_database.owner_url, autocommit=True) as watcher:
            deadline = time.monotonic() + 10
            while not watcher.execute("SELECT count(*) FROM pg_catalog.pg_locks"
                                      " WHERE locktype = 'advisory' AND NOT granted").fetchone()[0]:
                assert time.monotonic() < deadline, "the second write never waited on the lock"
                time.sleep(0.02)
        release.set()
        first.join(10)
        second.join(10)
    finally:
        release.set()
        pool.close()

    assert results == {"first": results["first"], "second": f"conflict at {results['first']}"}
    assert len(_rows(org_database)) == 1


# -- check-config -------------------------------------------------------------------------------------

@pytest.mark.parametrize("group_map,warned", [({}, True), ({"okta-group-00g1": "Engineering"}, False)])
def test_check_config_warns_when_overrides_exist_without_a_group_map(server, org_database, idp: FakeIdp,
                                                                     monkeypatch: pytest.MonkeyPatch, capsys,
                                                                     group_map: dict, warned: bool) -> None:
    _, admin = server
    for name in list(os.environ):
        if name.startswith("ROW_BOT_ORG_") or name == "ROW_BOT_SECRETS_DIR":
            monkeypatch.delenv(name)
    idp_config = json.dumps(idp.config(**({"group_map": group_map} if group_map else {})))

    def check_config() -> str:
        # The settings take their secrets out of the environment as they read them, so set them each time.
        for name, value in {"ROW_BOT_ORG_DATABASE_URL": org_database.runtime_url, "ROW_BOT_ORG_IDP": idp_config,
                            "ROW_BOT_ORG_IDP_CLIENT_SECRET": CLIENT_SECRET, "ROW_BOT_ORG_PUBLIC_URL": PUBLIC_URL,
                            "ROW_BOT_ORG_HMAC_PEPPER": PEPPER}.items():
            monkeypatch.setenv(name, value)
        assert cli.main(["check-config"]) == 0
        return capsys.readouterr().err

    assert "group policy overrides" not in check_config()  # none yet
    put(admin, {"approvals": {"max_level": "full"}}, "Engineering")
    assert ("Warning: group policy overrides exist but ROW_BOT_ORG_IDP has no group_map" in check_config()) is warned
    put(admin, {}, "Engineering")  # cleared: nothing to warn about
    assert "group policy overrides" not in check_config()
