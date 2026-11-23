"""The audit store and the device registry end to end: the hash chain (it links up, verifies, and shows an owner's
tampering), events stored in the change's own transaction, concurrent appends, row-level security (members read
nothing, nobody updates or deletes), the admins' query, verify and export routes, ingest from Row-Bots, and the
devices people register and admins revoke. The fake provider for sign-in and a real (throwaway) Postgres as the
runtime role. Security-sensitive: these run in the pull request lane's Org job.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest

from tests.fixtures.postgres import import_psycopg

psycopg = import_psycopg()
pytest.importorskip("joserfc", reason="joserfc is not installed: uv sync --locked --all-extras --group test --group org")

from fastapi.testclient import TestClient  # noqa: E402

from row_bot.audit import (  # noqa: E402
    Action, Actor, ActorKind, AuditEvent, Target, TargetKind, genesis_hash, new_event_id, to_dict, verify_chain,
)
from row_bot_org import audit  # noqa: E402
from row_bot_org.app import create_app  # noqa: E402
from row_bot_org.audit import web as audit_web  # noqa: E402
from row_bot_org.db.pool import create_pool  # noqa: E402
from row_bot_org.db.rls import principal_transaction  # noqa: E402
from row_bot_org.settings import load_settings  # noqa: E402
from tests.fixtures.oidc_idp import CLIENT_SECRET, PUBLIC_URL, FakeIdp, running_idp  # noqa: E402
from tests.helpers.org_audit import stored_events, summary  # noqa: E402

pytestmark = pytest.mark.postgres

PEPPER = "a-test-pepper-that-is-long-enough-0123456789"
ORIGIN = {"Origin": PUBLIC_URL}
GROUP_MAP = {"IT Admins": "it-admins", "Sales": "Sales"}
# The chain the server keeps, by the contract's own genesis rule.
GENESIS = genesis_hash("company-server")
LAPTOP = {"name": "Ada's laptop", "platform": "windows", "app_version": "5.4.0"}


@pytest.fixture(autouse=True)
def org_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.startswith("ROW_BOT_ORG_") or name == "ROW_BOT_SECRETS_DIR":
            monkeypatch.delenv(name)


@pytest.fixture
def idp() -> Iterator[FakeIdp]:
    with running_idp("okta") as running:
        yield running


@contextmanager
def org_server(database, idp: FakeIdp) -> Iterator[TestClient]:
    settings = load_settings({
        "ROW_BOT_ORG_IDP": json.dumps(idp.config(group_map=GROUP_MAP, admin_groups=["it-admins"])),
        "ROW_BOT_ORG_IDP_CLIENT_SECRET": CLIENT_SECRET, "ROW_BOT_ORG_PUBLIC_URL": PUBLIC_URL,
        "ROW_BOT_ORG_HMAC_PEPPER": PEPPER})
    pool = create_pool(database.runtime_url, max_size=4)
    with TestClient(create_app(settings, pool), base_url=PUBLIC_URL) as client:
        yield client


@pytest.fixture
def pool(org_database):
    pool = create_pool(org_database.runtime_url, max_size=10)
    pool.open(wait=True)
    yield pool
    pool.close()


def person(server: TestClient, idp: FakeIdp, subject: str, groups: list[str] | None = None) -> TestClient:
    browser = TestClient(server.app, base_url=PUBLIC_URL, headers=ORIGIN)
    location = browser.get("/auth/login", follow_redirects=False).headers["location"]
    done = browser.get(idp.authorize(location, subject=subject, groups=groups or [], email=f"{subject}@example.com"),
                       follow_redirects=False)
    assert done.status_code == 303, done.text
    return browser


def me(browser: TestClient) -> str:
    return browser.get("/api/me").json()["id"]


def owner_sql(database, sql: str, *args) -> list[tuple]:
    with psycopg.connect(database.owner_url, autocommit=True) as conn:
        cursor = conn.execute(sql, args)
        return cursor.fetchall() if cursor.description else []


def operator_event(conn, ref: str = "someone") -> None:
    audit.emit(conn, Action.SESSION_REVOKED, actor=audit.OPERATOR, target=Target(TargetKind.PERSON, ref))


def tool_event(**changes) -> dict:
    """A tool action as a Row-Bot's sender (C4) would report it."""
    now = datetime.now(timezone.utc)
    fields = {"id": new_event_id(), "occurred_at": now, "recorded_at": now, "org_id": "acme",
              "actor": Actor(ActorKind.PERSON, "whoever-the-app-says"), "action": Action.TOOL_CALLED,
              "outcome": "succeeded", "layer": "org", "target": Target(TargetKind.TOOL, "shell_run"),
              "decided_by": "person", "redaction": "summaries",
              "details": {"approval_level": "approve", "surface": "chat", "summary": "Listed the project folder"}}
    return to_dict(AuditEvent(**{**fields, **changes}))


# -- the chain ----------------------------------------------------------------------------------------

def test_appends_link_up_and_verify_reports_the_chain_sound(org_database, idp) -> None:
    with org_server(org_database, idp) as server:
        it = person(server, idp, "it", ["IT Admins"])
        person(server, idp, "ada")
        person(server, idp, "bo")
        report = it.get("/api/admin/audit/verify")

    links = owner_sql(org_database, "SELECT seq, prev_hash, hash, event FROM row_bot_org.audit_events ORDER BY seq")
    assert [seq for seq, *_ in links] == [0, 1, 2]
    assert links[0][1] == GENESIS and all(links[i][1] == links[i - 1][2] for i in (1, 2))
    # The contract's own verifier agrees with the hashes the database computed.
    local = verify_chain([{"seq": s, "prev_hash": p, "hash": h, "event": e} for s, p, h, e in links],
                         start_hash=GENESIS)
    assert local.ok and local.checked == 3
    assert report.status_code == 200
    assert report.json() == {"ok": True, "checked": 3, "head": links[-1][2], "broken_at": None, "reason": None}


def test_a_row_the_owner_changes_or_deletes_is_reported_broken(org_database, idp) -> None:
    with org_server(org_database, idp) as server:
        it = person(server, idp, "it", ["IT Admins"])
        for name in ("ada", "bo", "cy"):
            person(server, idp, name)
        # While row-level security is forced, not even the owner's statements change a row.
        assert owner_sql(org_database, "WITH changed AS (UPDATE row_bot_org.audit_events SET action = 'x'"
                                       " RETURNING 1) SELECT count(*) FROM changed") == [(0,)]
        assert owner_sql(org_database, "WITH gone AS (DELETE FROM row_bot_org.audit_events RETURNING 1)"
                                       " SELECT count(*) FROM gone") == [(0,)]
        assert it.get("/api/admin/audit/verify").json()["ok"] is True

        # An owner who turns it off can rewrite history, and verify finds where.
        owner_sql(org_database, "ALTER TABLE row_bot_org.audit_events NO FORCE ROW LEVEL SECURITY")
        owner_sql(org_database, "UPDATE row_bot_org.audit_events SET event = jsonb_set(event, '{outcome}',"
                                " '\"denied\"') WHERE seq = 2")
        changed = it.get("/api/admin/audit/verify").json()
        owner_sql(org_database, "DELETE FROM row_bot_org.audit_events WHERE seq = 2")
        deleted = it.get("/api/admin/audit/verify").json()

    assert (changed["ok"], changed["checked"], changed["broken_at"], changed["reason"]) == (False, 2, 2, "hash")
    assert (deleted["ok"], deleted["broken_at"], deleted["reason"]) == (False, 2, "prev_hash")


def test_an_event_is_stored_with_its_change_or_not_at_all(org_database, pool) -> None:
    with pytest.raises(RuntimeError, match="the change failed"):
        with pool.connection() as conn, conn.transaction():
            operator_event(conn)
            raise RuntimeError("the change failed")
    assert stored_events(org_database.owner_url) == []

    # Outside a transaction there is no change to store it with: refused.
    with pool.connection() as conn, pytest.raises(RuntimeError, match="transaction of the change"):
        operator_event(conn)

    with pool.connection() as conn, conn.transaction():
        operator_event(conn)
    assert len(stored_events(org_database.owner_url)) == 1


def test_concurrent_appends_keep_one_unbroken_chain(org_database, pool) -> None:
    writers, each = 8, 5
    start = threading.Barrier(writers)
    failures: list[BaseException] = []

    def write(number: int) -> None:
        try:
            start.wait()
            for index in range(each):
                with pool.connection() as conn, conn.transaction():
                    operator_event(conn, f"writer-{number}-{index}")
        except BaseException as error:  # noqa: BLE001 - reported below
            failures.append(error)

    threads = [threading.Thread(target=write, args=(number,)) for number in range(writers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert failures == []
    links = owner_sql(org_database, "SELECT seq, prev_hash, hash, event FROM row_bot_org.audit_events ORDER BY seq")
    assert [seq for seq, *_ in links] == list(range(writers * each))
    report = verify_chain([{"seq": s, "prev_hash": p, "hash": h, "event": e} for s, p, h, e in links],
                          start_hash=GENESIS)
    assert report.ok and report.checked == writers * each


# -- row-level security -------------------------------------------------------------------------------

def test_a_member_reads_no_audit_rows_and_nobody_updates_or_deletes_them(org_database, idp, pool) -> None:
    with org_server(org_database, idp) as server:
        ada = person(server, idp, "ada")
        ada_id = me(ada)
        assert (ada.get("/api/admin/audit").status_code, ada.get("/api/admin/audit/verify").status_code,
                ada.get("/api/admin/audit/export").status_code) == (403, 403, 403)
    assert stored_events(org_database.owner_url, "session.signed_in")  # Ada's own sign-in is there.

    with principal_transaction(pool, ada_id) as conn:
        assert conn.execute("SELECT count(*) FROM row_bot_org.audit_events").fetchone() == (0,)
    for statement in ("UPDATE row_bot_org.audit_events SET action = 'x'", "DELETE FROM row_bot_org.audit_events",
                      "INSERT INTO row_bot_org.audit_events SELECT * FROM row_bot_org.audit_events"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege), principal_transaction(pool, ada_id) as conn:
            conn.execute("SELECT set_config('row_bot.admin', 'on', true)")  # Even an admin's transaction.
            assert conn.execute("SELECT count(*) FROM row_bot_org.audit_events").fetchone()[0] > 0
            conn.execute(statement)


def test_a_person_sees_and_refreshes_only_their_own_devices(org_database, idp, pool) -> None:
    with org_server(org_database, idp) as server:
        ada, bo = person(server, idp, "ada"), person(server, idp, "bo")
        ada_id, bo_id = me(ada), me(bo)
        laptop = ada.post("/api/devices", json=LAPTOP).json()["id"]
        assert bo.post(f"/api/devices/{laptop}/seen").json() == {"error": "device_not_found"}
        assert bo.post("/api/devices", json={**LAPTOP, "id": laptop}).status_code == 404

    with principal_transaction(pool, bo_id) as conn:
        assert conn.execute("SELECT count(*) FROM row_bot_org.devices").fetchone() == (0,)
        assert conn.execute("UPDATE row_bot_org.devices SET last_seen_at = now()").rowcount == 0
    with pytest.raises(psycopg.errors.InsufficientPrivilege), principal_transaction(pool, ada_id) as conn:
        conn.execute("DELETE FROM row_bot_org.devices")
    with pytest.raises(psycopg.errors.InsufficientPrivilege), principal_transaction(pool, ada_id) as conn:
        conn.execute("UPDATE row_bot_org.devices SET principal_id = %s", (bo_id,))
    # Nobody registers a device as someone else, or revokes their own through the person's rules.
    with pytest.raises(psycopg.errors.InsufficientPrivilege), principal_transaction(pool, bo_id) as conn:
        conn.execute("INSERT INTO row_bot_org.devices (principal_id, name, platform, app_version)"
                     " VALUES (%s, 'x', 'linux', '1')", (ada_id,))
    with pytest.raises(psycopg.errors.InsufficientPrivilege), principal_transaction(pool, ada_id) as conn:
        conn.execute("UPDATE row_bot_org.devices SET revoked_at = now()")


# -- the server's own events --------------------------------------------------------------------------

def test_query_filters_and_pages_newest_first(org_database, idp, monkeypatch) -> None:
    with org_server(org_database, idp) as server:
        it = person(server, idp, "it", ["IT Admins"])
        ada = person(server, idp, "ada")
        ada_id, it_id = me(ada), me(it)
        assert it.post(f"/api/admin/people/{ada_id}/suspend").status_code == 200
        assert it.post(f"/api/admin/people/{ada_id}/resume").status_code == 200

        def search(**query) -> dict:
            response = it.get("/api/admin/audit", params=query)
            assert response.status_code == 200, response.text
            return response.json()

        everything = search()["events"]
        assert [link["event"]["action"] for link in everything] == [
            "person.resumed", "session.revoked", "person.suspended", "session.signed_in", "session.signed_in"]
        assert [link["seq"] for link in everything] == [4, 3, 2, 1, 0]
        assert [e["event"]["action"] for e in search(action="person.suspended")["events"]] == ["person.suspended"]
        assert {e["event"]["action"] for e in search(actor=it_id)["events"]} == {
            "person.resumed", "session.revoked", "person.suspended", "session.signed_in"}
        assert len(search(target=ada_id)["events"]) == 3
        assert search(action="model.called")["events"] == []

        page = search(limit=2)
        assert [e["seq"] for e in page["events"]] == [4, 3] and page["next_before"] == 3
        page = search(limit=2, before=page["next_before"])
        assert [e["seq"] for e in page["events"]] == [2, 1] and page["next_before"] == 1
        assert search(limit=2, before=1) == {"events": [everything[-1]], "next_before": None}

        recorded = datetime.fromisoformat(everything[2]["event"]["recorded_at"])
        later = search(**{"from": recorded.isoformat()})["events"]
        assert [e["seq"] for e in later] == [4, 3, 2]
        earlier = search(**{"to": recorded.isoformat()})["events"]
        assert [e["seq"] for e in earlier] == [1, 0]
        # A time without a zone is UTC.
        naive = recorded.astimezone(timezone.utc).replace(tzinfo=None) + timedelta(days=1)
        assert search(**{"from": naive.isoformat()})["events"] == []

        assert it.get("/api/admin/audit", params={"action": "made.up"}).json() == {"error": "unknown_action"}
        assert it.get("/api/admin/audit", params={"limit": 201}).status_code == 422


def test_an_export_is_json_lines_of_links_and_is_itself_recorded(org_database, idp, monkeypatch) -> None:
    with org_server(org_database, idp) as server:
        it = person(server, idp, "it", ["IT Admins"])
        for name in ("ada", "bo", "cy"):
            person(server, idp, name)
        it_id = me(it)
        exported = it.get("/api/admin/audit/export")
        monkeypatch.setattr(audit_web, "EXPORT_MAX", 2)
        first = it.get("/api/admin/audit/export")
        rest = it.get("/api/admin/audit/export", params={"after": first.headers[audit_web.NEXT_HEADER]})
        only = it.get("/api/admin/audit/export", params={"action": "data.exported"})

    assert exported.status_code == 200
    assert exported.headers["content-type"].startswith("application/x-ndjson")
    assert exported.headers["content-disposition"] == 'attachment; filename="audit.jsonl"'
    links = [json.loads(line) for line in exported.text.splitlines()]
    assert [link["seq"] for link in links] == [0, 1, 2, 3]
    # The whole chain verifies from the file alone.
    assert verify_chain(links, start_hash=GENESIS).ok
    assert [link["seq"] for link in (json.loads(line) for line in first.text.splitlines())] == [0, 1]
    assert first.headers[audit_web.NEXT_HEADER] == "1"
    assert [json.loads(line)["seq"] for line in rest.text.splitlines()] == [2, 3]
    assert rest.headers[audit_web.NEXT_HEADER] == "3"  # The earlier exports' own events follow.
    assert [json.loads(line)["event"]["action"] for line in only.text.splitlines()] == ["data.exported"] * 2  # Of 3, at 2 a file

    recorded = stored_events(org_database.owner_url, "data.exported")
    assert len(recorded) == 4
    assert summary(recorded[0])[:3] == ("data.exported", "admin", it_id)
    assert recorded[0]["target"]["kind"] == "export"
    assert recorded[0]["details"] == {"scope": "audit", "item_count": 4, "byte_count": len(exported.content)}


# -- ingest from Row-Bots -----------------------------------------------------------------------------

def test_ingest_takes_the_actor_from_the_session_and_keeps_only_metadata(org_database, idp) -> None:
    with org_server(org_database, idp) as server:
        ada = person(server, idp, "ada")
        ada_id = me(ada)
        device = ada.post("/api/devices", json=LAPTOP).json()["id"]
        sent = tool_event(actor=Actor(ActorKind.PERSON, "someone-else"), device_id=device)
        exported = to_dict(AuditEvent(
            id=new_event_id(), occurred_at=datetime.now(timezone.utc), recorded_at=datetime.now(timezone.utc),
            org_id="acme", actor=Actor(ActorKind.ADMIN, "it"), action=Action.DATA_EXPORTED, outcome="succeeded",
            layer="org", target=Target(TargetKind.EXPORT, "exp-1"), details={"scope": "person", "item_count": 3}))
        answer = ada.post("/api/audit/events", json={"events": [sent, exported]})
        again = ada.post("/api/audit/events", json={"events": [sent]})

    assert answer.json() == {"accepted": 2, "duplicates": 0}
    assert again.json() == {"accepted": 0, "duplicates": 1}  # A retry stores nothing twice.
    tool, export = stored_events(org_database.owner_url, "tool.called", "data.exported")
    assert (tool["id"], tool["actor"], tool["org_id"], tool["device_id"]) == (
        sent["id"], {"kind": "person", "id": ada_id}, "company", device)
    assert (tool["redaction"], tool["details"]) == ("metadata", {"approval_level": "approve", "surface": "chat"})
    assert tool["occurred_at"] == sent["occurred_at"] and tool["recorded_at"] != sent["recorded_at"]
    assert export["actor"] == {"kind": "person", "id": ada_id}


def test_ingest_refuses_other_actions_bad_events_unknown_devices_and_big_batches(org_database, idp) -> None:
    with org_server(org_database, idp) as server:
        it = person(server, idp, "it", ["IT Admins"])
        ada = person(server, idp, "ada")
        device = ada.post("/api/devices", json=LAPTOP).json()["id"]
        other = it.post("/api/devices", json={**LAPTOP, "name": "IT's desktop"}).json()["id"]
        before = len(stored_events(org_database.owner_url))

        def send(*events, **body) -> tuple[int, dict]:
            response = ada.post("/api/audit/events", json=body or {"events": list(events)})
            return response.status_code, response.json()

        signed_in = tool_event()
        signed_in.update(action="session.signed_in", category="session", target=None, decided_by=None,
                         redaction="metadata", details={})
        assert send(tool_event(), signed_in) == (422, {"error": "action_not_accepted", "index": 1})
        broken = {**tool_event(), "layer": "personal"}
        assert send(broken)[0] == 422 and send(broken)[1]["error"] == "invalid_event"
        assert send(tool_event(device_id=other)) == (422, {"error": "unknown_device", "index": 0})
        assert send(events=[]) == (422, {"error": "invalid_batch"})
        assert send(**{"events": [tool_event()], "actor": "x"}) == (422, {"error": "invalid_batch"})
        assert send(*[tool_event() for _ in range(audit_web.MAX_BATCH + 1)]) == (413, {"error": "batch_too_large"})
        assert ada.post("/api/audit/events", content=b"{" + b" " * audit_web.MAX_BATCH_BYTES + b"}").json() == {
            "error": "batch_too_large"}

        assert it.post(f"/api/admin/devices/{device}/revoke").status_code == 200
        assert send(tool_event(device_id=device)) == (403, {"error": "device_revoked", "index": 0})
        assert ada.post("/api/audit/events", json={"events": [tool_event()]}, headers={"Origin": "https://evil.example"}
                        ).status_code == 403

    # Nothing refused was stored, not even a batch's good events.
    assert [e["action"] for e in stored_events(org_database.owner_url)][before:] == ["device.removed"]


# -- devices ------------------------------------------------------------------------------------------

def test_a_person_registers_a_device_and_an_admin_lists_and_revokes_it(org_database, idp) -> None:
    with org_server(org_database, idp) as server:
        it = person(server, idp, "it", ["IT Admins"])
        ada = person(server, idp, "ada")
        ada_id, it_id = me(ada), me(it)

        registered = ada.post("/api/devices", json=LAPTOP)
        assert registered.status_code == 201
        device = registered.json()
        assert (device["principal_id"], device["name"], device["platform"], device["app_version"],
                device["revoked_at"]) == (ada_id, "Ada's laptop", "windows", "5.4.0", None)
        seen = ada.post(f"/api/devices/{device['id']}/seen", json={"app_version": "5.4.1"})
        assert seen.status_code == 200 and seen.json()["app_version"] == "5.4.1"
        assert seen.json()["last_seen_at"] >= device["last_seen_at"]
        assert ada.post(f"/api/devices/{device['id']}/seen").status_code == 200
        again = ada.post("/api/devices", json={**LAPTOP, "name": "Ada's new laptop", "id": device["id"]})
        assert (again.status_code, again.json()["id"], again.json()["name"]) == (200, device["id"], "Ada's new laptop")
        desktop = it.post("/api/devices", json={**LAPTOP, "name": "IT desk", "platform": "linux"}).json()

        assert ada.post("/api/devices", json={**LAPTOP, "platform": "android"}).status_code == 422
        assert ada.post("/api/devices", json={**LAPTOP, "app_version": "5.4 beta"}).status_code == 422
        assert ada.post("/api/devices", json={**LAPTOP, "name": "x\n"}).status_code == 422
        assert ada.get("/api/admin/devices").status_code == 403

        listed = it.get("/api/admin/devices").json()["devices"]
        assert {d["id"] for d in listed} == {device["id"], desktop["id"]}
        assert [d["id"] for d in it.get("/api/admin/devices", params={"person": ada_id}).json()["devices"]] == [
            device["id"]]
        revoked = it.post(f"/api/admin/devices/{device['id']}/revoke")
        assert revoked.status_code == 200 and revoked.json()["revoked_at"] is not None
        assert it.post(f"/api/admin/devices/{device['id']}/revoke").json() == revoked.json()  # Changes nothing.
        assert it.post(f"/api/admin/devices/{uuid.uuid4()}/revoke").json() == {"error": "device_not_found"}
        assert [d["id"] for d in it.get("/api/admin/devices", params={"revoked": "true"}).json()["devices"]] == [
            device["id"]]
        assert [d["id"] for d in it.get("/api/admin/devices", params={"revoked": "false"}).json()["devices"]] == [
            desktop["id"]]

        # A revoked device is refused: its seen calls and registering it again.
        assert ada.post(f"/api/devices/{device['id']}/seen").json() == {"error": "device_revoked"}
        assert ada.post("/api/devices", json={**LAPTOP, "id": device["id"]}).status_code == 403
        assert ada.post(f"/api/devices/{uuid.uuid4()}/seen").status_code == 404

    events = [summary(e) for e in stored_events(org_database.owner_url, "device.joined", "device.removed")]
    assert events == [
        ("device.joined", "person", ada_id, device["id"], "succeeded", {"platform": "windows", "app_version": "5.4.0"}),
        ("device.joined", "person", it_id, desktop["id"], "succeeded", {"platform": "linux", "app_version": "5.4.0"}),
        ("device.removed", "admin", it_id, device["id"], "succeeded", {"reason": "revoked"})]
