"""Company connectors end to end: a SharePoint/OneDrive folder (fake Microsoft Graph) and a Google shared drive (fake
Drive API), both served through ``httpx.MockTransport`` (tests/fixtures/drive_sources.py: no real Microsoft or Google
host is ever called), synced by the brain's worker into a space, listed in full and then followed by their change
feeds (create, change, delete, a move out of scope and one into it), with each file's upstream permissions mirrored:
a restricted file is invisible to anyone not listed through recall, the document lists, facts, the knowledge graph
and entity details; a group-mapped reader sees it; an unmappable principal makes it admins only, until an admin maps
it. A refused sign-in is audited and stops the connector after three; a 429 is waited out with no call meanwhile;
no secret or token is ever in an answer, a log line or the audit log; deleting a connector deletes its documents.

Security-sensitive (who reads a company document): these run in the pull request lane's Org job, on a real
(throwaway) Postgres as the runtime role. As in test_org_brain.py the worker's thread is off: each test runs its
passes with ``process_pending``.
"""

from __future__ import annotations

import base64
import json
import logging
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest

from tests.fixtures.postgres import import_psycopg

psycopg = import_psycopg()
pytest.importorskip("joserfc", reason="joserfc is not installed: uv sync --locked --all-extras --group test --group org")

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from row_bot_org.app import create_app  # noqa: E402
from row_bot_org.brain.store import NOT_IN_GRAPH  # noqa: E402
from row_bot_org.db.pool import create_pool  # noqa: E402
from row_bot_org.db.rls import principal_transaction  # noqa: E402
from row_bot_org.settings import load_settings  # noqa: E402
from tests.fixtures.drive_sources import (  # noqa: E402
    ACCESS_TOKEN, CLIENT_ID, CLIENT_SECRET, GOOGLE_DOC, GOOGLE_EMAIL, TENANT, FakeDrive, FakeGraph, google_domain,
    google_group, google_user, group_permission, link_permission, site_group_permission, user_permission,
)
from tests.fixtures.model_endpoints import running_endpoint  # noqa: E402
from tests.fixtures.oidc_idp import CLIENT_SECRET as IDP_SECRET  # noqa: E402
from tests.fixtures.oidc_idp import PUBLIC_URL, FakeIdp, running_idp  # noqa: E402
from tests.helpers.org_audit import stored_events, summary  # noqa: E402
from tests.subsystem.org.test_org_brain import (  # noqa: E402
    company_space, me, process, recall, runtime_pool, superuser_sql, uploaded,
)
from tests.subsystem.org.test_org_brain_graph import (  # noqa: E402
    add_models, answering, chat_calls, details, entity, graph_of,
)

pytestmark = pytest.mark.postgres

PEPPER = "a-test-pepper-that-is-long-enough-0123456789"
ORIGIN = {"Origin": PUBLIC_URL}
GATEWAY_KEY = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")
IT_ADMINS = "IT Admins"
# Engineering's group in Entra (what Graph names it by) and in Google (its email), both mapped to the Row-Bot group the
# identity provider's own "Engineering" group maps to.
ENGINEERING_ID = "5d3b2a10-0000-4000-8000-00000000e001"
ENGINEERING_EMAIL = "eng@example.com"
GROUP_MAP = {IT_ADMINS: "it-admins", "Sales": "Sales", "Engineering": "Engineering", ENGINEERING_ID: "Engineering",
             ENGINEERING_EMAIL: "Engineering"}
ENGINEERING = [group_permission(ENGINEERING_ID, "Engineering")]
NOT_FOUND = {"error": "connector_not_found"}
REFUSED = "Microsoft refused the connector's sign-in. Check its credentials and their consent."


@pytest.fixture
def idp() -> Iterator[FakeIdp]:
    with running_idp("okta") as running:
        yield running


@contextmanager
def org_server(database, idp: FakeIdp, **env: str) -> Iterator[TestClient]:
    settings = load_settings({
        "ROW_BOT_ORG_IDP": json.dumps(idp.config(group_map=GROUP_MAP, admin_groups=["it-admins"])),
        "ROW_BOT_ORG_IDP_CLIENT_SECRET": IDP_SECRET, "ROW_BOT_ORG_PUBLIC_URL": PUBLIC_URL,
        "ROW_BOT_ORG_HMAC_PEPPER": PEPPER, "ROW_BOT_ORG_GATEWAY_KEY": GATEWAY_KEY, **env})
    pool = create_pool(database.runtime_url, max_size=4)
    with TestClient(create_app(settings, pool, brain_worker=False), base_url=PUBLIC_URL) as client:
        yield client


def person(server: TestClient, idp: FakeIdp, subject: str, groups: list[str] | None = None) -> TestClient:
    """A signed-in browser of its own, whose email (verified) is ``<subject>@example.com``."""
    client = TestClient(server.app, base_url=PUBLIC_URL, headers=ORIGIN)
    login = client.get("/auth/login", follow_redirects=False)
    done = client.get(idp.authorize(login.headers["location"], subject=subject, groups=groups or [],
                                    email=f"{subject}@example.com"), follow_redirects=False)
    assert done.status_code == 303, done.text
    return client


def admin_of(server: TestClient, idp: FakeIdp) -> TestClient:
    return person(server, idp, "it", [IT_ADMINS])


def microsoft(graph: FakeGraph, space_id: str, **fields: Any) -> dict[str, Any]:
    return {"kind": "microsoft", "space_id": space_id, "name": "HR drive", "drive_id": graph.drive,
            "tenant_id": TENANT, "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET, **fields}


def connect(server: TestClient, admin: TestClient, source: Any, body: dict[str, Any]) -> dict[str, Any]:
    """A new connector, its calls answered by ``source``."""
    server.app.state.connectors.transport = httpx.MockTransport(source.handler)
    response = admin.post("/api/admin/connectors", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def connector_of(admin: TestClient, connector_id: str) -> dict[str, Any]:
    response = admin.get(f"/api/admin/connectors/{connector_id}")
    assert response.status_code == 200, response.text
    return response.json()


def sync_now(admin: TestClient, connector_id: str) -> None:
    response = admin.post(f"/api/admin/connectors/{connector_id}/sync")
    assert response.status_code == 202, response.text


def due_now(database, connector_id: str) -> None:
    """The connector's wait is over (as if its time had come)."""
    superuser_sql(database, "UPDATE row_bot_org.connectors SET next_sync_at = now() WHERE id = %s", connector_id)


def documents(client: TestClient, space_id: str) -> dict[str, dict]:
    response = client.get(f"/api/brain/spaces/{space_id}/documents")
    assert response.status_code == 200, response.text
    return {document["title"]: document for document in response.json()["documents"]}


def titles(client: TestClient, space_id: str) -> set[str]:
    return set(documents(client, space_id))


def recalled(client: TestClient, query: str) -> set[str]:
    """The titles of the documents, and the text of the facts, recall finds for ``query``."""
    return {hit["citation"]["document"]["title"] if hit["citation"]["type"] == "document"
            else hit["citation"]["fact"]["text"] for hit in recall(client, query)["hits"]}


def downloads(source: FakeGraph) -> list[str]:
    """The files SharePoint was asked for, in order."""
    return [request.url.path.rsplit("/", 1)[-1] for request in source.to("contoso.sharepoint.com")]


def changes(database, *actions: str) -> list[tuple]:
    return [summary(event)[:5] + (event["layer"], event["details"])
            for event in stored_events(database.owner_url, *(actions or ("connector.changed",)))]


# -- a Microsoft folder: listed in full, then followed ------------------------------------------------

def test_a_microsoft_folder_is_listed_in_full_then_followed_by_its_change_feed(org_database, idp) -> None:
    graph = FakeGraph()
    graph.folder("F-hr", "HR", "root-1")
    graph.folder("F-other", "Other", "root-1")
    graph.file("leave", "leave.md", "F-hr", b"Everyone gets 25 days of annual leave.", ENGINEERING)
    graph.file("notes", "notes.txt", "F-hr", b"Payroll runs on the last Friday.", ENGINEERING)
    graph.file("diagram", "diagram.png", "F-hr", b"\x89PNG", ENGINEERING, mime="image/png")
    graph.file("big", "big.txt", "F-hr", b"tiny", ENGINEERING, size=2 * 1024 * 1024)
    graph.folder("F-forms", "Forms", "F-hr")
    graph.file("expenses", "expenses.md", "F-forms", b"Claim expenses with every receipt.", ENGINEERING)
    graph.file("secret", "secret.md", "F-other", b"Out of scope.", ENGINEERING)
    graph.folder("F-travel", "Travel", "F-other")
    graph.file("travel", "travel.md", "F-travel", b"Book travel through the portal.", ENGINEERING)

    with org_server(org_database, idp, ROW_BOT_ORG_BRAIN_MAX_UPLOAD_MB="1") as server:
        admin = admin_of(server, idp)
        eve, sam = person(server, idp, "eve", ["Engineering"]), person(server, idp, "sam", ["Sales"])
        admin_id, company = me(admin), company_space(admin)
        connector = connect(server, admin, graph, microsoft(graph, company, folder_id="F-hr"))
        connector_id = connector["id"]
        assert {key: connector[key] for key in ("kind", "space_id", "name", "drive_id", "folder_id", "tenant_id",
                                                "client_id", "has_secret", "state", "files", "documents",
                                                "warning", "run_as")} == {
            "kind": "microsoft", "space_id": company, "name": "HR drive", "drive_id": graph.drive,
            "folder_id": "F-hr", "tenant_id": TENANT, "client_id": CLIENT_ID, "has_secret": True,
            "state": "active", "files": 0, "documents": 0, "warning": None, "run_as": admin_id}
        assert "client_secret" not in connector and CLIENT_SECRET not in json.dumps(connector)

        # The first sync lists everything under the folder (page by page) and reads each file once; the same pass
        # chunks the three it could take.
        assert process(server) == 3
        assert titles(eve, company) == {"leave", "notes", "expenses"}
        assert titles(sam, company) == set()
        leave = documents(eve, company)["leave"]
        assert {key: leave[key] for key in ("file_name", "media_type", "connector_id", "restricted", "graph_status",
                                            "graph_reason", "uploaded_by")} == {
            "file_name": "leave.md", "media_type": "text/markdown", "connector_id": connector_id, "restricted": True,
            "graph_status": "done", "graph_reason": NOT_IN_GRAPH, "uploaded_by": admin_id}
        assert recalled(eve, "annual leave") == {"leave"}
        assert recalled(sam, "annual leave") == set()
        shown = connector_of(admin, connector_id)
        assert (shown["files"], shown["documents"], shown["skipped"], shown["unmapped_files"]) == (5, 3, 2, 0)
        assert shown["last_sync_at"] is not None and shown["state_reason"] is None
        # Only what is in scope and readable was fetched; SharePoint's downloads never carry the access token.
        assert sorted(downloads(graph)) == ["expenses", "leave", "notes"]
        assert all("authorization" not in request.headers for request in graph.to("contoso.sharepoint.com"))
        assert all(request.headers["authorization"] == f"Bearer {ACCESS_TOKEN}"
                   for request in graph.to("graph.microsoft.com"))
        assert len(graph.to("login.microsoftonline.com")) == 1

        # Caught up: nothing is called again before its time.
        seen = len(graph.requests)
        assert process(server) == 0
        assert len(graph.requests) == seen

        # Upstream: a change, a delete, a file moved out of scope and a folder moved into it (whose file the feed
        # doesn't name: its folder is listed).
        graph.change("leave", b"Everyone gets 30 days of annual leave now.")
        graph.remove("notes")
        graph.move("expenses", "F-other")
        graph.move("F-travel", "F-hr")
        sync_now(admin, connector_id)
        assert process(server) == 2
        assert titles(eve, company) == {"leave", "travel"}
        assert documents(eve, company)["leave"]["version"] == 2
        assert recalled(eve, "30 days") == {"leave"}
        assert recalled(eve, "receipt payroll") == set()
        assert downloads(graph)[3:] == ["leave", "travel"]
        shown = connector_of(admin, connector_id)
        assert (shown["files"], shown["documents"], shown["skipped"]) == (4, 2, 2)

        # Its connector keeps a document as its source has it.
        leave_id = documents(admin, company)["leave"]["id"]
        for refused in (admin.delete(f"/api/brain/documents/{leave_id}"),
                        admin.put(f"/api/brain/documents/{leave_id}", files={"file": ("leave.md", b"Mine now.")})):
            assert (refused.status_code, refused.json()) == (409, {"error": "document_from_connector"})

        # Deleting the connector deletes its documents.
        assert admin.delete(f"/api/admin/connectors/{connector_id}").status_code == 204
        assert titles(eve, company) == set() and titles(admin, company) == set()
        assert recalled(eve, "annual leave") == set()
        assert admin.get(f"/api/admin/connectors/{connector_id}").json() == NOT_FOUND
        assert admin.get("/api/admin/connectors").json() == {"connectors": []}
        assert superuser_sql(org_database, "SELECT count(*) FROM row_bot_org.brain_documents") == [(0,)]
        assert superuser_sql(org_database, "SELECT count(*) FROM row_bot_org.brain_chunks") == [(0,)]
        assert superuser_sql(org_database, "SELECT count(*) FROM row_bot_org.connector_items") == [(0,)]

    detail = {"kind": "microsoft", "space_id": company}
    assert changes(org_database) == [
        ("connector.changed", "admin", admin_id, connector_id, "succeeded", "company", {"change": "created", **detail}),
        ("connector.changed", "admin", admin_id, connector_id, "succeeded", "company",
         {"change": "sync_requested", **detail}),
        ("connector.changed", "admin", admin_id, connector_id, "succeeded", "company",
         {"change": "deleted", **detail, "documents_removed": 2}),
    ]
    # Sync results go to the log, not the audit log: no upload or removal event for a connector's files.
    assert changes(org_database, "document.uploaded", "document.removed") == []


# -- permissions mirrored --------------------------------------------------------------------------------

def test_a_restricted_file_is_invisible_to_everyone_not_listed_by_any_route_and_mirrors_follow_upstream(
        org_database, idp) -> None:
    graph = FakeGraph()
    graph.file("salaries", "salaries.md", "root-1", b"Salary bands for 2027.", [user_permission("u-bo", "Bo",
                                                                                                "Bo@Example.com")])
    graph.file("roadmap", "roadmap.md", "root-1", b"Orion follows Atlas next year.", ENGINEERING)
    graph.file("board", "board.md", "root-1", b"Board minutes.",
               [link_permission("organization"), user_permission("u-bo", "Bo", "bo@example.com")])
    graph.file("handbook", "handbook.md", "root-1", b"The staff handbook.", [site_group_permission("5",
                                                                                                  "Contoso Members")])

    with running_endpoint("openai") as endpoint, org_server(org_database, idp) as server:
        answering(endpoint, [("Orion", [entity("project", "Orion", "The search project."), entity("project", "Atlas")]),
                             ("Atlas", [entity("project", "Atlas", "The payments project.")])])
        admin = admin_of(server, idp)
        add_models(admin, endpoint.base_url)
        eve, bo = person(server, idp, "eve", ["Engineering"]), person(server, idp, "bo")
        sam = person(server, idp, "sam", ["Sales"])
        admin_id, sam_id, company = me(admin), me(sam), company_space(admin)
        uploaded(admin, company, "atlas.md", b"Atlas is the payments project.")
        connector_id = connect(server, admin, graph, microsoft(graph, company))["id"]

        assert process(server) == 5
        # Each person lists, and recalls (all they may read: the fake embeds every text alike), only what they may
        # open upstream; the admin's management view lists everything, but recall is a member's view for admins too.
        readable = {eve: {"atlas", "roadmap"}, bo: {"atlas", "salaries"}, sam: {"atlas"}, admin: {"atlas"}}
        for client, expected in readable.items():
            assert recalled(client, "salary bands board minutes roadmap handbook") == expected
            if client is not admin:
                assert titles(client, company) == expected
        assert titles(admin, company) == {"atlas", "salaries", "roadmap", "board", "handbook"}
        roadmap = documents(eve, company)["roadmap"]
        assert eve.get(f"/api/brain/documents/{roadmap['id']}").json()["title"] == "roadmap"
        assert sam.get(f"/api/brain/documents/{roadmap['id']}").json() == {"error": "document_not_found"}

        # Synced files never reach the knowledge graph: nobody sees Orion, not even a listed reader, and Atlas is
        # mentioned only by the upload.
        assert chat_calls(endpoint) == ["Atlas is the payments project."]
        assert (roadmap["graph_status"], roadmap["graph_reason"]) == ("done", NOT_IN_GRAPH)
        for client in (eve, bo, sam):
            found = graph_of(client)
            assert [node["subject"] for node in found["nodes"]] == ["Atlas"]
            atlas = details(client, found["nodes"][0]["id"])
            assert atlas["mention_count"] == 1
            assert [mention["document"]["title"] for mention in atlas["mentions"]] == ["atlas"]

        # Behind the API, row-level security alone hides a restricted file and its parts.
        with runtime_pool(org_database) as pool:
            with principal_transaction(pool, sam_id, ["Sales"]) as conn:
                for table, count in (("brain_documents", 1), ("brain_chunks", 1), ("connectors", 0),
                                     ("connector_items", 0)):
                    assert conn.execute(f"SELECT count(*) FROM row_bot_org.{table}").fetchone() == (count,), table
                # A principal's transaction is never the worker's.
                conn.execute("SELECT set_config('row_bot.brain_worker', 'on', true)")
                assert conn.execute("SELECT count(*) FROM row_bot_org.brain_documents").fetchone() == (1,)
            # An admin's transaction without the admin flag is a member's.
            with principal_transaction(pool, admin_id, ["it-admins"]) as conn:
                assert conn.execute("SELECT count(*) FROM row_bot_org.brain_documents").fetchone() == (1,)
                assert conn.execute("SELECT count(*) FROM row_bot_org.connectors").fetchone() == (0,)

        # What couldn't be mapped makes a file admins only, and the connector says what it was.
        shown = connector_of(admin, connector_id)
        assert shown["unmapped_files"] == 2
        assert shown["unmapped"] == [
            {"key": "link:organization", "name": "Anyone in the organisation with the link", "files": 1},
            {"key": "sharepoint-group:5", "name": "Contoso Members", "files": 1}]
        assert shown["warning"] == ("2 files are shared with people or groups Row-Bot can't match, so only admins can"
                                    " read them. Map them to Row-Bot groups in the connector's principal map.")

        # A fact citing a restricted file is read with it only, and isn't read for the graph.
        fact = admin.post(f"/api/brain/spaces/{company}/facts",
                          json={"text": "Orion ships in May.", "source_document_id": roadmap["id"]})
        assert fact.status_code == 201, fact.text
        assert [f["text"] for f in eve.get(f"/api/brain/spaces/{company}/facts").json()["facts"]] == [
            "Orion ships in May."]
        assert sam.get(f"/api/brain/spaces/{company}/facts").json()["facts"] == []
        assert process(server) == 0
        assert "Orion ships in May." in recalled(eve, "Orion ships")
        assert "Orion ships in May." not in recalled(sam, "Orion ships")
        assert len(chat_calls(endpoint)) == 1

        # Upstream sharing changes: the roadmap moves from Engineering to Sam, and nothing is downloaded again.
        graph.share("roadmap", [user_permission("u-sam", "Sam", "sam@example.com")])
        sync_now(admin, connector_id)
        assert process(server) == 0
        assert titles(eve, company) == {"atlas"}
        assert titles(sam, company) == {"atlas", "roadmap"}
        assert [f["text"] for f in sam.get(f"/api/brain/spaces/{company}/facts").json()["facts"]] == [
            "Orion ships in May."]
        assert sorted(downloads(graph)) == ["board", "handbook", "roadmap", "salaries"]

        # An admin maps the SharePoint group to a Row-Bot group: its file is read by that group.
        mapped = admin.patch(f"/api/admin/connectors/{connector_id}",
                             json={"principal_map": {"sharepoint-group:5": "Sales"}})
        assert mapped.status_code == 200, mapped.text
        assert mapped.json()["principal_map"] == {"sharepoint-group:5": "Sales"}
        assert process(server) == 0
        assert titles(sam, company) == {"atlas", "roadmap", "handbook"}
        assert titles(bo, company) == {"atlas", "salaries"}
        shown = connector_of(admin, connector_id)
        assert (shown["unmapped_files"], [entry["key"] for entry in shown["unmapped"]]) == (1, ["link:organization"])
        # Saving the same again changes nothing and records nothing.
        assert admin.patch(f"/api/admin/connectors/{connector_id}",
                           json={"principal_map": {"sharepoint-group:5": "Sales"}}).status_code == 200

    detail = {"kind": "microsoft", "space_id": company}
    assert [entry[6] for entry in changes(org_database)] == [
        {"change": "created", **detail}, {"change": "sync_requested", **detail},
        {"change": "updated", **detail, "fields": "principal_map"}]


# -- refusals, secrets and backoff -----------------------------------------------------------------------

def test_a_refused_sign_in_is_audited_and_stops_the_connector_after_three_and_no_secret_is_ever_shown(
        org_database, idp, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    graph = FakeGraph()
    graph.file("leave", "leave.md", "root-1", b"Everyone gets 25 days of annual leave.", ENGINEERING)
    graph.refuse_sign_in = True
    answers: list[str] = []

    with org_server(org_database, idp) as server:
        admin = admin_of(server, idp)
        admin_id, company = me(admin), company_space(admin)
        connector = connect(server, admin, graph, microsoft(graph, company))
        connector_id = connector["id"]
        answers.append(json.dumps(connector))

        for attempt in (1, 2, 3):
            if attempt > 1:
                due_now(org_database, connector_id)
            assert process(server) == 0
            shown = connector_of(admin, connector_id)
            answers.append(json.dumps(shown))
            assert (shown["state"], shown["documents"]) == ("error" if attempt == 3 else "active", 0)
        assert shown["state_reason"] == (f"{REFUSED} The connector stopped after 3 refusals in a row: put it right, "
                                         "then save or resume the connector.")
        assert len(graph.to("login.microsoftonline.com")) == 3
        assert len(graph.to("graph.microsoft.com")) == 0

        # Stopped: nothing is called again, and it can't be asked to sync.
        due_now(org_database, connector_id)
        assert process(server) == 0
        assert len(graph.requests) == 3
        refused = admin.post(f"/api/admin/connectors/{connector_id}/sync")
        assert (refused.status_code, refused.json()) == (409, {"error": "connector_not_active"})

        # The connection test says why, then that it works once it does; neither is a change.
        tested = admin.post(f"/api/admin/connectors/{connector_id}/test")
        answers.append(tested.text)
        assert tested.json() == {"ok": False, "problem": REFUSED}
        graph.refuse_sign_in = False
        assert admin.post(f"/api/admin/connectors/{connector_id}/test").json() == {"ok": True, "problem": None}

        resumed = admin.post(f"/api/admin/connectors/{connector_id}/resume")
        answers.append(resumed.text)
        assert (resumed.json()["state"], resumed.json()["state_reason"]) == ("active", None)
        assert admin.post(f"/api/admin/connectors/{connector_id}/resume").status_code == 200  # again: no change
        assert process(server) == 1
        assert titles(admin, company) == {"leave"}

        # A new secret is saved, never shown; a member can't see or touch connectors at all.
        saved = admin.patch(f"/api/admin/connectors/{connector_id}", json={"client_secret": "a-new-secret-value"})
        answers.append(saved.text)
        assert saved.json()["has_secret"] is True
        rejected = admin.patch(f"/api/admin/connectors/{connector_id}", json={"tenant_id": CLIENT_ID})
        assert (rejected.status_code, rejected.json()) == (422, {"error": "secret_required"})
        sam = person(server, idp, "sam", ["Sales"])
        assert sam.get("/api/admin/connectors").json() == {"error": "admin_required"}
        answers.append(admin.get("/api/admin/connectors").text)

    service = ("connector.changed", "service", "company-server", connector_id, "failed", "company")
    detail = {"kind": "microsoft", "space_id": company}
    assert changes(org_database) == [
        ("connector.changed", "admin", admin_id, connector_id, "succeeded", "company", {"change": "created", **detail}),
        *[(*service, {"change": "auth_failed", **detail, "attempts": attempt}) for attempt in (1, 2, 3)],
        ("connector.changed", "admin", admin_id, connector_id, "succeeded", "company", {"change": "resumed", **detail}),
        ("connector.changed", "admin", admin_id, connector_id, "succeeded", "company",
         {"change": "updated", **detail, "fields": "credentials"}),
    ]
    stored = json.dumps(stored_events(org_database.owner_url))
    for secret in (CLIENT_SECRET, "a-new-secret-value", ACCESS_TOKEN):
        assert secret not in caplog.text
        assert secret not in stored
        assert all(secret not in answer for answer in answers)
    for (sealed,) in superuser_sql(org_database, "SELECT secret_sealed FROM row_bot_org.connectors"):
        assert b"a-new-secret-value" not in bytes(sealed)


def test_a_throttled_source_is_waited_for_as_it_asks_with_no_call_meanwhile(org_database, idp) -> None:
    graph = FakeGraph()
    graph.file("leave", "leave.md", "root-1", b"Everyone gets 25 days of annual leave.", ENGINEERING)
    graph.throttle = 120

    with org_server(org_database, idp) as server:
        admin = admin_of(server, idp)
        company = company_space(admin)
        connector_id = connect(server, admin, graph, microsoft(graph, company))["id"]
        assert process(server) == 0
        # Signed in, asked for the feed's cursor, told to wait two minutes: it waits that long, still active.
        assert [request.url.host for request in graph.requests] == ["login.microsoftonline.com", "graph.microsoft.com"]
        assert superuser_sql(
            org_database, "SELECT next_sync_at > now() + interval '100 seconds',"
            " next_sync_at < now() + interval '130 seconds', failures, state FROM row_bot_org.connectors") == [
            (True, True, 1, "active")]
        for _ in range(3):
            assert process(server) == 0
        assert len(graph.requests) == 2

        # Its wait over and the source answering again, it syncs.
        graph.throttle = None
        due_now(org_database, connector_id)
        assert process(server) == 1
        assert titles(admin, company) == {"leave"}
        assert superuser_sql(org_database, "SELECT failures FROM row_bot_org.connectors") == [(0,)]


# -- a Google shared drive ---------------------------------------------------------------------------------

def test_a_google_shared_drive_syncs_files_and_docs_follows_its_changes_and_mirrors_its_sharing(
        org_database, idp) -> None:
    drive = FakeDrive()
    engineering = [google_group("Eng@Example.com")]
    drive.file("g-policy", "policy.md", drive.root, b"Laptops are replaced every three years.", engineering)
    drive.file("g-doc", "Onboarding", drive.root, b"Welcome aboard: your first week.", [google_user("bo@example.com")],
               mime=GOOGLE_DOC)
    drive.file("g-wide", "wide.md", drive.root, b"Everyone at the company reads this.", [google_domain("example.com")])
    drive.folder("g-team", "Team", drive.root)
    drive.file("g-plan", "plan.md", "g-team", b"The plan for the quarter.", engineering)
    drive.file("g-sheet", "Budget", "g-team", b"", engineering, mime="application/vnd.google-apps.spreadsheet")

    with org_server(org_database, idp) as server:
        admin = admin_of(server, idp)
        eve, bo = person(server, idp, "eve", ["Engineering"]), person(server, idp, "bo")
        sam = person(server, idp, "sam", ["Sales"])
        company = company_space(admin)
        key_file = drive.key_file()
        connector = connect(server, admin, drive, {"kind": "google", "space_id": company, "name": "Team drive",
                                                   "drive_id": drive.root, "service_account": key_file})
        connector_id = connector["id"]
        assert (connector["client_id"], connector["tenant_id"], connector["has_secret"]) == (GOOGLE_EMAIL, None, True)
        assert drive.private_key_pem() not in json.dumps(connector)

        assert process(server) == 4
        assert titles(eve, company) == {"policy", "plan"}
        assert titles(bo, company) == {"Onboarding"}
        assert titles(sam, company) == set()
        onboarding = documents(bo, company)["Onboarding"]
        assert (onboarding["file_name"], onboarding["media_type"]) == ("Onboarding.txt", "text/plain")
        assert recalled(bo, "first week") == {"Onboarding"}
        shown = connector_of(admin, connector_id)
        assert (shown["files"], shown["documents"], shown["skipped"], shown["unmapped_files"]) == (5, 4, 1, 1)
        assert shown["unmapped"] == [{"key": "google-domain:example.com", "name": "Everyone at example.com",
                                      "files": 1}]
        # Signed in as the service account itself (no domain-wide delegation).
        assert drive.subjects == [None]

        # A change, a file trashed, and one moved to another shared drive.
        drive.change("g-policy", b"Laptops are replaced every four years.")
        drive.remove("g-plan")
        drive.move("g-doc", "0AanotherDrive")
        sync_now(admin, connector_id)
        assert process(server) == 1
        assert titles(eve, company) == {"policy"}
        assert titles(bo, company) == set()
        assert recalled(eve, "four years") == {"policy"}
        assert recalled(eve, "three quarter") == set()
        shown = connector_of(admin, connector_id)
        assert (shown["files"], shown["documents"]) == (3, 2)

        # A service account's key file is checked when it is saved; its key is never shown.
        refused = admin.patch(f"/api/admin/connectors/{connector_id}", json={"service_account": "{}"})
        assert refused.status_code == 422 and key_file not in refused.text
        wrong = admin.patch(f"/api/admin/connectors/{connector_id}", json={"client_secret": "x"})
        assert (wrong.status_code, wrong.json()) == (422, {"error": "not_for_this_kind"})
