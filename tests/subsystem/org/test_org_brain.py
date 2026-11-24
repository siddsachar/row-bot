"""The company brain end to end: spaces and their owners, uploads and the worker that processes them (through the
gateway, metered, with the fake embeddings endpoint of tests/fixtures/model_endpoints.py), replace and delete,
refusals, hybrid and text-only recall with citations, a device's bearer recalling, facts, and their audit events.
The fake provider for sign-in and a real (throwaway) Postgres as the runtime role. Security-sensitive (nobody
recalls or lists what a space they can't read holds): these run in the pull request lane's Org job.

The worker's thread is off here (``create_app(..., brain_worker=False)``): each test runs its passes itself with
``process_pending``, so nothing races it.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import secrets
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest

from tests.fixtures.postgres import import_psycopg

psycopg = import_psycopg()
pytest.importorskip("joserfc", reason="joserfc is not installed: uv sync --locked --all-extras --group test --group org")

from fastapi.testclient import TestClient  # noqa: E402

from row_bot.brain import RecallHit, from_dict  # noqa: E402
from row_bot_org.app import create_app  # noqa: E402
from row_bot_org.db.pool import create_pool  # noqa: E402
from row_bot_org.db.rls import principal_transaction  # noqa: E402
from row_bot_org.settings import load_settings  # noqa: E402
from tests.fixtures.model_endpoints import INPUT_TOKENS, KEY, running_endpoint  # noqa: E402
from tests.fixtures.oidc_idp import CLIENT_SECRET, PUBLIC_URL, FakeIdp, running_idp  # noqa: E402
from tests.helpers.org_audit import stored_events, summary  # noqa: E402
from tests.subsystem.org.test_org_device_sign_in import bearer, join, row_bot  # noqa: E402

pytestmark = pytest.mark.postgres

PEPPER = "a-test-pepper-that-is-long-enough-0123456789"
ORIGIN = {"Origin": PUBLIC_URL}
GATEWAY_KEY = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")
IT_ADMINS = "IT Admins"
GROUP_MAP = {IT_ADMINS: "it-admins", "Sales": "Sales", "Engineering": "Engineering"}
MODEL_NEEDED = "Add an embeddings model in Models"
SPACE_NOT_FOUND = {"error": "space_not_found"}

HOLIDAYS = b"# Annual leave\nEveryone gets 25 days of annual leave a year. Book leave in the HR portal before you go."
EXPENSES = b"# Expenses\nClaim an expense within 30 days and attach every receipt."
LAPTOPS = b"IT replaces each laptop every three years. Ask IT for a new laptop."
CARRY_OVER = "Staff may carry over 5 days of leave into the next year."
# The fake endpoint's embeddings: one dimension per topic (and a small constant, so no vector is zero).
TOPICS = (("leave", "holiday", "vacation"), ("expense", "receipt"), ("laptop", "computer"))


def topic_vector(text: str) -> list[float]:
    lowered = text.lower()
    return [1.0 if any(word in lowered for word in words) else 0.0 for words in TOPICS] + [0.05]


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
    with TestClient(create_app(settings, pool, brain_worker=False), base_url=PUBLIC_URL) as client:
        yield client


def person(server: TestClient, idp: FakeIdp, subject: str, groups: list[str] | None = None) -> TestClient:
    """A signed-in browser of its own (its own cookies), sending the server's origin."""
    client = TestClient(server.app, base_url=PUBLIC_URL, headers=ORIGIN)
    login = client.get("/auth/login", follow_redirects=False)
    done = client.get(idp.authorize(login.headers["location"], subject=subject, groups=groups or []),
                      follow_redirects=False)
    assert done.status_code == 303, done.text
    return client


def admin_of(server: TestClient, idp: FakeIdp) -> TestClient:
    return person(server, idp, "it-admin", [IT_ADMINS])


def me(client: TestClient) -> str:
    return client.get("/api/me").json()["id"]


def process(server: TestClient) -> int:
    """One pass of the brain's worker."""
    return server.app.state.brain.process_pending()


def company_space(client: TestClient) -> str:
    (space,) = [space for space in client.get("/api/brain/spaces").json()["spaces"] if space["kind"] == "company"]
    return space["id"]


def new_space(admin: TestClient, name: str, **access: Any) -> str:
    response = admin.post("/api/admin/brain/spaces", json={"name": name, **access})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def upload(client: TestClient, space_id: str, file_name: str, content: bytes, **form: str):
    return client.post(f"/api/brain/spaces/{space_id}/documents", files={"file": (file_name, content)},
                       data=form or None)


def uploaded(client: TestClient, space_id: str, file_name: str, content: bytes, **form: str) -> dict:
    response = upload(client, space_id, file_name, content, **form)
    assert response.status_code == 201, response.text
    return response.json()


def recall(client: TestClient, query: str, **body: Any) -> dict:
    response = client.post("/api/brain/recall", json={"query": query, **body})
    assert response.status_code == 200, response.text
    return response.json()


def cited(result: dict) -> list[tuple[str, str]]:
    """Each hit as (``document`` and its title, or ``fact`` and its text), best first."""
    return [(hit["citation"]["type"], hit["citation"]["document"]["title"] if hit["citation"]["type"] == "document"
             else hit["citation"]["fact"]["text"]) for hit in result["hits"]]


def add_embeddings(admin: TestClient, base_url: str, **fields: Any) -> None:
    endpoint = admin.post("/api/admin/endpoints", json={"name": f"embed-{secrets.token_hex(3)}", "api": "openai",
                                                         "base_url": base_url, "api_key": KEY})
    assert endpoint.status_code == 201, endpoint.text
    model = admin.post("/api/admin/models", json={"endpoint_id": endpoint.json()["id"], "name": "company-embed",
                                                   "kind": "embeddings", "is_default": True, **fields})
    assert model.status_code == 201, model.text


def superuser_sql(database, sql: str, *args) -> list[tuple]:
    with psycopg.connect(database.superuser_url, autocommit=True) as conn:
        cursor = conn.execute(sql, args)
        return cursor.fetchall() if cursor.description else []


def word_document(*paragraphs: str) -> bytes:
    body = "".join(f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" for text in paragraphs)
    xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           f"<w:body>{body}</w:body></w:document>")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", xml)
    return buffer.getvalue()


@contextmanager
def runtime_pool(database) -> Iterator[Any]:
    pool = create_pool(database.runtime_url, max_size=1)
    pool.open(wait=True, timeout=30)
    try:
        yield pool
    finally:
        pool.close()


# -- spaces, owners and who reads what --------------------------------------------------------------

def test_spaces_follow_groups_and_owners_and_nobody_reads_a_space_they_are_not_in(org_database, idp) -> None:
    with org_server(org_database, idp) as server:
        admin = admin_of(server, idp)
        priya, sam, eve = (person(server, idp, "priya"), person(server, idp, "sam", ["Sales"]),
                           person(server, idp, "eve", ["Engineering"]))
        priya_id, eve_id, admin_id = me(priya), me(eve), me(admin)

        # One company space from the start.
        (company,) = admin.get("/api/admin/brain/spaces").json()["spaces"]
        assert (company["name"], company["kind"], company["reader_groups"]) == ("Company", "company", [])
        sales = new_space(admin, "Sales team", reader_groups=["Sales"], owner_principals=[priya_id])
        assert admin.post("/api/admin/brain/spaces", json={"name": "sales TEAM"}).json() == {"error": "name_taken"}
        unknown = admin.post("/api/admin/brain/spaces", json={"name": "X", "owner_principals": [company["id"]]})
        assert (unknown.status_code, unknown.json()) == (422, {"error": "unknown_person"})

        def seen(client: TestClient) -> list[tuple[str, bool]]:
            return [(space["name"], space["is_owner"]) for space in client.get("/api/brain/spaces").json()["spaces"]]

        assert seen(eve) == [("Company", False)]
        assert seen(sam) == [("Company", False), ("Sales team", False)]
        assert seen(priya) == [("Company", False), ("Sales team", True)]
        # An admin reads what their groups give them, like anyone; the admin routes see every space.
        assert seen(admin) == [("Company", False)]

        # Only owners (and admins) add documents; a space you can't read doesn't exist for you.
        document = uploaded(priya, sales, "pipeline.md", b"The sales pipeline review is every Monday.")
        refused = upload(sam, sales, "mine.md", b"Sam's notes")
        assert (refused.status_code, refused.json()) == (403, {"error": "space_owner_required"})
        assert upload(eve, sales, "mine.md", b"Eve's notes").json() == SPACE_NOT_FOUND
        assert eve.get(f"/api/brain/spaces/{sales}/documents").json() == SPACE_NOT_FOUND
        assert eve.get(f"/api/brain/documents/{document['id']}").json() == {"error": "document_not_found"}
        assert [d["id"] for d in sam.get(f"/api/brain/spaces/{sales}/documents").json()["documents"]] == [
            document["id"]]
        assert admin.get(f"/api/brain/spaces/{sales}/documents").status_code == 200
        assert process(server) == 1

        # Nor through recall, by name or not.
        assert eve.post("/api/brain/recall", json={"query": "pipeline", "spaces": [sales]}).json() == SPACE_NOT_FOUND
        assert recall(eve, "sales pipeline review")["hits"] == []
        assert recall(admin, "sales pipeline review")["hits"] == []
        assert cited(recall(sam, "sales pipeline review", spaces=[sales])) == [("document", "pipeline")]

        # Owners rename their space, and nothing else about it.
        assert sam.patch(f"/api/brain/spaces/{sales}", json={"name": "Sales"}).status_code == 403
        assert priya.patch(f"/api/brain/spaces/{sales}", json={"name": "Sales", "reader_groups": []}).status_code == 422
        renamed = priya.patch(f"/api/brain/spaces/{sales}", json={"name": "Sales"})
        assert renamed.json() == {"id": sales, "name": "Sales", "kind": "group", "is_owner": True}

        # Admins set who reads; the company space stays and is read by everyone.
        assert admin.patch(f"/api/admin/brain/spaces/{sales}",
                           json={"reader_groups": ["Sales", "Engineering"]}).status_code == 200
        assert [name for name, _ in seen(eve)] == ["Company", "Sales"]
        assert admin.patch(f"/api/admin/brain/spaces/{sales}", json={"reader_groups": ["Sales"]}).status_code == 200
        assert admin.patch(f"/api/admin/brain/spaces/{company['id']}",
                           json={"reader_groups": ["Sales"]}).json() == {"error": "company_space_is_read_by_everyone"}
        assert admin.delete(f"/api/admin/brain/spaces/{company['id']}").json() == {"error": "company_space_stays"}
        assert sam.get("/api/admin/brain/spaces").status_code == 403

        # Behind the API, row-level security alone keeps Eve out, and the worker's flag gives a principal nothing.
        with runtime_pool(org_database) as pool:
            with principal_transaction(pool, eve_id, ["Engineering"]) as conn:
                assert conn.execute("SELECT count(*) FROM row_bot_org.spaces").fetchone() == (1,)
                assert conn.execute("SELECT count(*) FROM row_bot_org.brain_documents").fetchone() == (0,)
                assert conn.execute("SELECT count(*) FROM row_bot_org.brain_chunks").fetchone() == (0,)
                conn.execute("SELECT set_config('row_bot.brain_worker', 'on', true)")
                assert conn.execute("SELECT count(*) FROM row_bot_org.brain_chunks").fetchone() == (0,)
            with principal_transaction(pool, eve_id, ["Engineering"]) as conn, \
                    pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute("INSERT INTO row_bot_org.brain_documents (space_id, title, file_name, media_type,"
                             " byte_count, sha256, content) VALUES (%s, 't', 't.md', 'text/markdown', 0, %s, '')",
                             (company["id"], "0" * 64))
            # An owner's transaction can't hand the space to others: only an admin's changes who reads or owns it.
            with principal_transaction(pool, priya_id, []) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute("UPDATE row_bot_org.spaces SET reader_groups = '{Engineering}' WHERE id = %s", (sales,))

        assert admin.delete(f"/api/admin/brain/spaces/{sales}").status_code == 204
        assert superuser_sql(org_database, "SELECT count(*) FROM row_bot_org.brain_documents") == [(0,)]
        assert superuser_sql(org_database, "SELECT count(*) FROM row_bot_org.brain_chunks") == [(0,)]

    changes = [summary(event)[:5] + (event["layer"], event["details"])
               for event in stored_events(org_database.owner_url, "space.changed")]
    assert changes == [
        ("space.changed", "admin", admin_id, sales, "succeeded", "team", {"change": "created"}),
        ("space.changed", "person", priya_id, sales, "succeeded", "team", {"change": "updated"}),
        ("space.changed", "admin", admin_id, sales, "succeeded", "team", {"change": "updated"}),
        ("space.changed", "admin", admin_id, sales, "succeeded", "team", {"change": "updated"}),
        ("space.changed", "admin", admin_id, sales, "succeeded", "team", {"change": "deleted"}),
    ]


# -- uploads and the worker -------------------------------------------------------------------------

def test_an_upload_is_processed_to_ready_through_the_gateway_and_metered_to_its_uploader(org_database, idp) -> None:
    with running_endpoint("openai") as endpoint, org_server(org_database, idp) as server:
        admin = admin_of(server, idp)
        add_embeddings(admin, endpoint.base_url, input_price="0.02")
        priya = person(server, idp, "priya", ["Sales"])
        priya_id = me(priya)
        sales = new_space(admin, "Sales team", reader_groups=["Sales"], owner_principals=[priya_id])

        document = uploaded(priya, sales, "pipeline.md", b"The sales pipeline review is every Monday.",
                            title="Pipeline review")
        assert {key: document[key] for key in ("title", "file_name", "media_type", "byte_count", "status", "version",
                                               "chunk_count", "uploaded_by")} == {
            "title": "Pipeline review", "file_name": "pipeline.md", "media_type": "text/markdown", "byte_count": 42,
            "status": "queued", "version": 1, "chunk_count": 0, "uploaded_by": priya_id}
        assert document["sha256"] == hashlib.sha256(b"The sales pipeline review is every Monday.").hexdigest()
        assert endpoint.requests == []

        assert process(server) == 1
        ready = priya.get(f"/api/brain/documents/{document['id']}").json()
        assert (ready["status"], ready["status_reason"], ready["chunk_count"]) == ("ready", None, 1)
        assert ready["processed_at"] is not None
        assert endpoint.last().path == "/v1/embeddings"
        assert endpoint.last().body["input"] == ["The sales pipeline review is every Monday."]
        assert process(server) == 0

        # Metered to the uploader and their group, like any gateway call.
        by_person = admin.get("/api/admin/usage?by=person").json()["usage"]
        assert by_person == [{"person": priya_id, "calls": 1, "input_tokens": INPUT_TOKENS, "output_tokens": 0,
                              "cost": 0.000024}]
        assert [row["group"] for row in admin.get("/api/admin/usage?by=group").json()["usage"]] == ["Sales"]
        called = [summary(event) for event in stored_events(org_database.owner_url, "model.called")]
        assert [(c[1], c[2], c[5]["model"], c[5]["input_tokens"]) for c in called] == [
            ("person", priya_id, "company-embed", INPUT_TOKENS)]

        # ...and against their budget: with it spent, the next version fails and says why.
        assert admin.put("/api/admin/budgets", json={"scope": "person", "subject": priya_id,
                                                      "hard_limit": 0}).status_code == 200
        replaced = priya.put(f"/api/brain/documents/{document['id']}",
                             files={"file": ("pipeline.md", b"The review moved to Tuesday.")})
        assert replaced.status_code == 200, replaced.text
        assert process(server) == 1
        failed = priya.get(f"/api/brain/documents/{document['id']}").json()
        assert (failed["status"], failed["status_reason"], failed["version"]) == (
            "failed", "Your monthly model budget is used up.", 2)
        assert len(endpoint.requests) == 1
        assert recall(priya, "review Tuesday")["hits"] == []

    uploads = [summary(event)[:5] + (event["layer"], event["details"])
               for event in stored_events(org_database.owner_url, "document.uploaded")]
    assert uploads == [
        ("document.uploaded", "person", priya_id, document["id"], "succeeded", "team",
         {"space_id": sales, "byte_count": 42}),
        ("document.uploaded", "person", priya_id, document["id"], "succeeded", "team",
         {"space_id": sales, "byte_count": 28}),
    ]


def test_uploads_of_an_unknown_type_too_large_or_unreadable_are_refused_or_fail(org_database, idp) -> None:
    with org_server(org_database, idp, ROW_BOT_ORG_BRAIN_MAX_UPLOAD_MB="1") as server:
        admin = admin_of(server, idp)
        company = company_space(admin)

        unknown = upload(admin, company, "plan.pdf", b"%PDF-1.7")
        assert (unknown.status_code, unknown.json()) == (
            415, {"error": "unsupported_type", "supported": [".docx", ".markdown", ".md", ".txt"]})
        too_large = upload(admin, company, "big.txt", b"x" * (1024 * 1024 + 1))
        assert (too_large.status_code, too_large.json()) == (413, {"error": "file_too_large", "max_bytes": 1024 * 1024})
        not_a_form = admin.post(f"/api/brain/spaces/{company}/documents", json={"file": "notes"})
        assert (not_a_form.status_code, not_a_form.json()) == (400, {"error": "invalid_upload"})
        no_file = admin.post(f"/api/brain/spaces/{company}/documents", files={"other": ("a.txt", b"x")})
        assert (no_file.status_code, no_file.json()) == (400, {"error": "file_required"})
        assert superuser_sql(org_database, "SELECT count(*) FROM row_bot_org.brain_documents") == [(0,)]

        broken = uploaded(admin, company, "broken.docx", b"not a zip")
        latin = uploaded(admin, company, "latin.txt", "café".encode("latin-1"))
        empty = uploaded(admin, company, "empty.md", b"")
        word = uploaded(admin, company, "handbook.docx", word_document("Welcome to Acme.", "Parking is free."))
        assert process(server) == 4

        def state(document: dict) -> tuple:
            found = admin.get(f"/api/brain/documents/{document['id']}").json()
            return found["status"], found["status_reason"], found["chunk_count"]

        assert state(broken) == ("failed", "This Word document can't be read.", 0)
        assert state(latin) == ("failed", "This file isn't UTF-8 text.", 0)
        assert state(empty) == ("failed", "This file has no text.", 0)
        # Without an embeddings model a readable document is in chunks, found by text, and waits for one.
        assert state(word) == ("queued", MODEL_NEEDED, 1)
        assert word["title"] == "handbook"
        assert cited(recall(admin, "parking")) == [("document", "handbook")]
        assert process(server) == 0


def test_a_replace_keeps_the_id_and_a_delete_removes_the_chunks(org_database, idp) -> None:
    with org_server(org_database, idp) as server:
        admin = admin_of(server, idp)
        admin_id = me(admin)
        company = company_space(admin)
        document = uploaded(admin, company, "office.md", b"The office opens at eight.")
        process(server)
        assert cited(recall(admin, "office opens eight")) == [("document", "office")]

        replaced = admin.put(f"/api/brain/documents/{document['id']}",
                             files={"file": ("office.txt", b"The office moved to the harbour.")},
                             data={"title": "Office"})
        assert replaced.status_code == 200, replaced.text
        assert {key: replaced.json()[key] for key in ("id", "version", "status", "chunk_count", "title",
                                                      "file_name", "media_type")} == {
            "id": document["id"], "version": 2, "status": "queued", "chunk_count": 0, "title": "Office",
            "file_name": "office.txt", "media_type": "text/plain"}
        # The old text is gone at once, and the new one is found once processed.
        assert recall(admin, "eight")["hits"] == []
        process(server)
        (hit,) = recall(admin, "harbour")["hits"]
        assert hit["citation"]["document"] == {"id": document["id"], "title": "Office", "file_name": "office.txt"}

        assert admin.delete(f"/api/brain/documents/{document['id']}").status_code == 204
        assert admin.get(f"/api/brain/documents/{document['id']}").json() == {"error": "document_not_found"}
        assert admin.delete(f"/api/brain/documents/{document['id']}").status_code == 404
        assert superuser_sql(org_database, "SELECT count(*) FROM row_bot_org.brain_chunks") == [(0,)]
        assert recall(admin, "harbour")["hits"] == []

    events = [summary(event)[:5] + (event["layer"], event["details"]) for event in
              stored_events(org_database.owner_url, "document.uploaded", "document.removed")]
    assert events == [
        ("document.uploaded", "admin", admin_id, document["id"], "succeeded", "company",
         {"space_id": company, "byte_count": 26}),
        ("document.uploaded", "admin", admin_id, document["id"], "succeeded", "company",
         {"space_id": company, "byte_count": 32}),
        ("document.removed", "admin", admin_id, document["id"], "succeeded", "company", {"space_id": company}),
    ]


# -- recall -----------------------------------------------------------------------------------------

def test_recall_ranks_by_text_without_an_embeddings_model_and_fuses_vectors_once_there_is_one(org_database,
                                                                                               idp) -> None:
    with running_endpoint("openai") as endpoint, org_server(org_database, idp) as server:
        endpoint.embed = topic_vector
        admin = admin_of(server, idp)
        company = company_space(admin)
        holidays = uploaded(admin, company, "holidays.md", HOLIDAYS)
        uploaded(admin, company, "expenses.md", EXPENSES)
        uploaded(admin, company, "laptops.txt", LAPTOPS)
        fact = admin.post(f"/api/brain/spaces/{company}/facts", json={"text": CARRY_OVER})
        assert fact.status_code == 201, fact.text
        assert process(server) == 3

        # No embeddings model: text only, by how much of the query each holds.
        text = recall(admin, "annual leave days")
        assert text["search"] == "text"
        assert cited(text) == [("document", "holidays"), ("fact", CARRY_OVER), ("document", "expenses")]
        assert text["hits"][0]["hit"]["score"] == 1.0
        assert [hit["hit"]["score"] for hit in text["hits"]] == sorted(
            (hit["hit"]["score"] for hit in text["hits"]), reverse=True)
        assert cited(recall(admin, "receipt for an expense")) == [("document", "expenses")]
        assert recall(admin, "vacation")["hits"] == []
        assert endpoint.requests == []

        # With one, the waiting documents and the fact are embedded, and similar meaning is found too.
        add_embeddings(admin, endpoint.base_url)
        assert process(server) == 3
        statuses = {d["file_name"]: d["status"]
                    for d in admin.get(f"/api/brain/spaces/{company}/documents").json()["documents"]}
        assert statuses == {"holidays.md": "ready", "expenses.md": "ready", "laptops.txt": "ready"}
        assert superuser_sql(org_database, "SELECT count(*) FROM row_bot_org.brain_facts"
                                            " WHERE embedding_model = 'company-embed' AND embedding_dim = 4") == [(1,)]

        meaning = recall(admin, "vacation", k=2)
        assert meaning["search"] == "hybrid"
        assert cited(meaning) == [("document", "holidays"), ("fact", CARRY_OVER)]
        both = recall(admin, "annual leave days")
        assert both["search"] == "hybrid"
        assert cited(both)[:2] == [("document", "holidays"), ("fact", CARRY_OVER)]
        assert both["hits"][0]["hit"]["score"] == 1.0

        # A query embedded by another model, or at another dimension, is never compared: text only finds it.
        superuser_sql(org_database, "UPDATE row_bot_org.catalog_models SET name = 'other-embed'"
                                    " WHERE name = 'company-embed'")
        assert cited(recall(admin, "vacation")) == []
        assert recall(admin, "vacation")["search"] == "hybrid"
        assert cited(recall(admin, "receipt"))[0] == ("document", "expenses")

    citation = text["hits"][0]["citation"]
    assert citation == {"type": "document", "space": {"id": company, "name": "Company"},
                        "document": {"id": holidays["id"], "title": "holidays", "file_name": "holidays.md"},
                        "chunk": 0, "excerpt": " ".join(HOLIDAYS.decode().split())}
    hit = text["hits"][0]["hit"]
    assert (hit["label"], hit["layer"], hit["owner"]) == ("From Company", "company", "organisation")
    assert (hit["item"]["kind"], hit["item"]["title"], hit["item"]["content_hash"]) == (
        "source", "holidays", "sha256:" + hashlib.sha256(HOLIDAYS).hexdigest())
    # Every hit is a contract RecallHit.
    for result in (text, meaning, both):
        for entry in result["hits"]:
            assert from_dict(RecallHit, entry["hit"]).label == "From Company"


def test_a_device_recalls_with_its_bearer_and_gets_no_more_than_its_person_reads(org_database, idp) -> None:
    with org_server(org_database, idp) as server:
        admin = admin_of(server, idp)
        priya = person(server, idp, "priya", ["Sales"])
        sales = new_space(admin, "Sales team", reader_groups=["Sales"], owner_principals=[me(priya)])
        uploaded(priya, sales, "quota.md", b"The quarterly sales quota is set in January.")
        uploaded(admin, company_space(admin), "quota-policy.md", b"Quota changes need a director's sign-off.")
        process(server)

        app = row_bot(server)
        priya_device = bearer(join(server, priya)["access_token"])
        admin_device = bearer(join(server, admin)["access_token"])
        found = app.post("/api/brain/recall", json={"query": "quota"}, headers=priya_device)
        assert found.status_code == 200, found.text
        assert sorted(cited(found.json())) == [("document", "quota"), ("document", "quota-policy")]
        # An admin's device is a member's: their groups, no admin powers, no admin routes.
        assert cited(app.post("/api/brain/recall", json={"query": "quota"}, headers=admin_device).json()) == [
            ("document", "quota-policy")]
        assert app.post(f"/api/brain/spaces/{sales}/documents", files={"file": ("x.md", b"x")},
                        headers=admin_device).json() == SPACE_NOT_FOUND
        assert app.get("/api/admin/brain/spaces", headers=admin_device).json() == {
            "error": "browser_session_required"}
        # A space's owner uploads from the app with the device's bearer.
        assert upload_with(app, sales, priya_device).status_code == 201
        assert app.post("/api/brain/recall", json={"query": "quota"}).status_code == 401


def upload_with(app: TestClient, space_id: str, headers: dict[str, str]):
    return app.post(f"/api/brain/spaces/{space_id}/documents", files={"file": ("territories.md", b"Territories")},
                    headers=headers)


# -- facts ------------------------------------------------------------------------------------------

def test_owners_write_edit_retire_and_delete_facts_and_each_change_is_audited(org_database, idp) -> None:
    with org_server(org_database, idp) as server:
        admin = admin_of(server, idp)
        priya, sam, eve = (person(server, idp, "priya"), person(server, idp, "sam", ["Sales"]),
                           person(server, idp, "eve", ["Engineering"]))
        priya_id = me(priya)
        sales = new_space(admin, "Sales team", reader_groups=["Sales"], owner_principals=[priya_id])
        source = uploaded(priya, sales, "commission.md", b"Commission plans for the year.")
        elsewhere = uploaded(admin, company_space(admin), "other.md", b"Something else.")
        process(server)

        wrong_source = priya.post(f"/api/brain/spaces/{sales}/facts",
                                  json={"text": "Commission is paid quarterly.", "source_document_id": elsewhere["id"]})
        assert (wrong_source.status_code, wrong_source.json()) == (422, {"error": "unknown_source_document"})
        refused = sam.post(f"/api/brain/spaces/{sales}/facts", json={"text": "Commission is paid daily."})
        assert (refused.status_code, refused.json()) == (403, {"error": "space_owner_required"})
        assert eve.post(f"/api/brain/spaces/{sales}/facts", json={"text": "x"}).json() == SPACE_NOT_FOUND
        assert priya.post(f"/api/brain/spaces/{sales}/facts", json={"text": "   "}).status_code == 422

        created = priya.post(f"/api/brain/spaces/{sales}/facts",
                             json={"text": "Commission is paid quarterly.", "source_document_id": source["id"]})
        assert created.status_code == 201, created.text
        fact = created.json()
        assert {key: fact[key] for key in ("space_id", "text", "source_document_id", "written_by", "retired_at")} == {
            "space_id": sales, "text": "Commission is paid quarterly.", "source_document_id": source["id"],
            "written_by": priya_id, "retired_at": None}
        assert [f["id"] for f in sam.get(f"/api/brain/spaces/{sales}/facts").json()["facts"]] == [fact["id"]]
        assert eve.get(f"/api/brain/spaces/{sales}/facts").json() == SPACE_NOT_FOUND

        (hit,) = [hit for hit in recall(sam, "commission paid")["hits"] if hit["citation"]["type"] == "fact"]
        assert hit["citation"] == {"type": "fact", "space": {"id": sales, "name": "Sales team"},
                                   "fact": {"id": fact["id"], "text": "Commission is paid quarterly."},
                                   "document": {"id": source["id"], "title": "commission",
                                                "file_name": "commission.md"}}
        assert (hit["hit"]["label"], hit["hit"]["layer"], hit["hit"]["owner_principal"]) == (
            "From Sales team", "team", priya_id)
        assert hit["hit"]["item"]["kind"] == "fact"
        assert recall(eve, "commission paid")["hits"] == []

        edited = priya.patch(f"/api/brain/facts/{fact['id']}", json={"text": "Commission is paid monthly."})
        assert (edited.status_code, edited.json()["text"]) == (200, "Commission is paid monthly.")
        unsourced = priya.patch(f"/api/brain/facts/{fact['id']}", json={"source_document_id": None})
        assert (unsourced.json()["text"], unsourced.json()["source_document_id"]) == (
            "Commission is paid monthly.", None)
        assert sam.patch(f"/api/brain/facts/{fact['id']}", json={"text": "Never."}).status_code == 403

        retired = priya.post(f"/api/brain/facts/{fact['id']}/retire")
        assert retired.status_code == 200 and retired.json()["retired_at"] is not None
        assert priya.post(f"/api/brain/facts/{fact['id']}/retire").json() == retired.json()
        assert [hit for hit in recall(sam, "commission paid monthly")["hits"]
                if hit["citation"]["type"] == "fact"] == []
        assert sam.get(f"/api/brain/spaces/{sales}/facts").json()["facts"] == []
        assert [f["id"] for f in sam.get(f"/api/brain/spaces/{sales}/facts?retired=true").json()["facts"]] == [
            fact["id"]]

        assert sam.delete(f"/api/brain/facts/{fact['id']}").status_code == 403
        assert priya.delete(f"/api/brain/facts/{fact['id']}").status_code == 204
        assert priya.delete(f"/api/brain/facts/{fact['id']}").json() == {"error": "fact_not_found"}

    events = [summary(event)[:5] + (event["layer"], event["details"])
              for event in stored_events(org_database.owner_url, "fact.changed")]
    assert events == [("fact.changed", "person", priya_id, fact["id"], "succeeded", "team",
                       {"change": change, "space_id": sales})
                      for change in ("created", "updated", "updated", "retired", "deleted")]
