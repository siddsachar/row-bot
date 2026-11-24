"""A space owner's company documents from the app (B3): the spaces they own and a space's documents with each
one's status; upload, replace and delete through B1a's routes with the device's bearer; a file the server would
refuse refused first; the server's 413, 415 and 429 said plainly; and nothing asked while locked down. The server
is a fake (``httpx.MockTransport``)."""

from __future__ import annotations

import json

import httpx
import pytest

from row_bot.api.v1 import schemas as dto
from row_bot.company import documents, policy
from tests.fixtures.company_server import ACCOUNTS, COMPANY, COMPANY_SPACE, HANDBOOK, SALES_SPACE

pytestmark = [pytest.mark.subsystem]

SALES = {"id": SALES_SPACE, "name": "Sales", "kind": "group", "is_owner": True}


def _wire(view: dict) -> None:
    """``view`` passes the wire record, as the client API sends it."""
    dto.CompanyDocumentsView.model_validate_json(json.dumps(view))


def _asked(server, since: int) -> list[tuple[str, str]]:
    return [(request.method, request.url.path) for request in server.requests[since:]]


def _with_bearer_only(server, since: int) -> None:
    for request in server.requests[since:]:
        assert request.headers["authorization"].startswith("Bearer rboa_") and "cookie" not in request.headers


def test_an_owner_sees_the_spaces_they_own_and_a_spaces_documents_with_their_status(company_server):
    company_server.join_here()
    since = len(company_server.requests)

    view = documents.overview()

    assert _asked(company_server, since) == [("GET", "/api/brain/spaces"),
                                             ("GET", f"/api/brain/spaces/{SALES_SPACE}/documents")]
    _with_bearer_only(company_server, since)
    assert (view["state"], view["company"], view["outcome"]) == ("ready", COMPANY, None)
    assert (view["spaces"], view["space_id"]) == ([SALES], SALES_SPACE)  # Only the space they own.
    assert (view["max_upload_bytes"], view["supported_types"]) == (25 * 1024 * 1024,
                                                                   [".docx", ".markdown", ".md", ".txt"])
    assert view["documents"] == [
        {"id": ACCOUNTS, "title": "Accounts 2026", "file_name": "accounts.md", "byte_count": 2048, "status": "ready",
         "status_reason": None, "graph_status": "done", "graph_reason": None, "version": 1,
         "uploaded_at": "2026-11-20T09:00:00+00:00"},
        {"id": "d0000000-0000-4000-8000-000000000003", "title": "Old prices", "file_name": "prices.docx",
         "byte_count": 4096, "status": "failed", "status_reason": "This Word document can't be read.",
         "graph_status": "pending", "graph_reason": None, "version": 1, "uploaded_at": "2026-11-20T09:00:00+00:00"},
    ]
    _wire(view)

    # A space they don't own is never listed for them.
    assert documents.overview(COMPANY_SPACE)["space_id"] == SALES_SPACE
    for space in company_server.brain_spaces:
        space["is_owner"] = False
    since = len(company_server.requests)
    none = documents.overview()
    assert (none["spaces"], none["space_id"], none["documents"]) == ([], None, [])
    assert _asked(company_server, since) == [("GET", "/api/brain/spaces")]


def test_upload_replace_and_delete_go_to_the_server_with_the_bearer(company_server):
    company_server.join_here()
    since = len(company_server.requests)

    added = documents.upload(SALES_SPACE, "notes.txt", b"Holiday policy: 25 days.")

    assert _asked(company_server, since)[0] == ("POST", f"/api/brain/spaces/{SALES_SPACE}/documents")
    (sent,) = company_server.uploads
    assert sent["fields"] == {"file": ("notes.txt", b"Holiday policy: 25 days.")}
    assert added["outcome"] == "uploaded"
    new = added["documents"][0]  # Newest first.
    assert (new["title"], new["file_name"], new["status"], new["byte_count"], new["version"]) == (
        "notes", "notes.txt", "queued", 24, 1)
    _wire(added)

    replaced = documents.replace(new["id"], "Documents\\notes v2.md", b"Holiday policy: 26 days.")

    assert company_server.uploads[-1]["method"] == "PUT"
    assert company_server.uploads[-1]["fields"]["file"] == ("notes v2.md", b"Holiday policy: 26 days.")  # No path.
    assert replaced["outcome"] == "replaced"
    assert (replaced["documents"][0]["id"], replaced["documents"][0]["version"]) == (new["id"], 2)

    removed = documents.remove(new["id"])

    assert ("DELETE", f"/api/brain/documents/{new['id']}") in _asked(company_server, since)
    assert removed["outcome"] == "removed"
    assert new["id"] not in [document["id"] for document in removed["documents"]]
    _with_bearer_only(company_server, since)

    # A document that isn't there, or not one of theirs: said, and nothing is changed.
    assert documents.remove(new["id"])["outcome"] == "not_found"
    assert documents.replace(HANDBOOK, "handbook.docx", b"x")["outcome"] == "not_owner"
    assert documents.upload(COMPANY_SPACE, "notes.txt", b"x")["outcome"] == "not_owner"
    assert documents.upload("not-a-space", "notes.txt", b"x")["outcome"] == "not_found"


def test_a_file_the_server_would_refuse_is_refused_first_and_its_refusals_are_said_plainly(
        company_server, monkeypatch):
    company_server.join_here()

    for name, content, outcome in (("photo.png", b"x", "unsupported_type"), ("notes", b"x", "unsupported_type"),
                                   ("bad\x01.txt", b"x", "invalid_file"), ("", b"x", "invalid_file")):
        assert documents.upload(SALES_SPACE, name, content)["outcome"] == outcome
    with monkeypatch.context() as patch:
        patch.setattr(documents, "MAX_UPLOAD_BYTES", 10)
        too_big = documents.upload(SALES_SPACE, "notes.txt", b"x" * 11)
    assert (too_big["outcome"], too_big["max_upload_bytes"]) == ("too_large", 10)
    assert company_server.uploads == []  # Nothing was sent.

    # The server's own limits (an admin may have set others) come back with its refusal.
    company_server.brain_answers.append(("POST", httpx.Response(
        413, json={"error": "file_too_large", "max_bytes": 1024 * 1024})))
    large = documents.upload(SALES_SPACE, "notes.txt", b"x" * 2048)
    assert (large["state"], large["outcome"], large["max_upload_bytes"]) == ("ready", "too_large", 1024 * 1024)
    company_server.brain_answers.append(("POST", httpx.Response(
        415, json={"error": "unsupported_type", "supported": [".md", ".txt"]})))
    odd = documents.upload(SALES_SPACE, "notes.docx", b"x")
    assert (odd["outcome"], odd["supported_types"]) == ("unsupported_type", [".md", ".txt"])
    _wire(large)
    _wire(odd)

    # Four uploads already under way on the server: busy, in C1b's words.
    company_server.brain_answers.append(("POST", httpx.Response(
        429, json={"error": "too_many_uploads"}, headers={"Retry-After": "5"})))
    busy = documents.upload(SALES_SPACE, "notes.txt", b"x")
    assert (busy["state"], busy["reason"], busy["documents"]) == ("unreachable", "busy", [])
    _wire(busy)


def test_nothing_is_asked_while_locked_down_and_a_revoked_row_bot_reads_disconnected(company_server):
    company_server.policy["offline"]["max_days"] = 0
    record = company_server.join_here()
    company_server.offline = True
    assert policy.refresh().state == "locked_down"
    company_server.offline = False
    since = len(company_server.requests)

    locked = documents.overview()
    refused = documents.upload(SALES_SPACE, "notes.txt", b"x")

    assert (locked["state"], locked["reason"], locked["spaces"]) == ("locked_down", "lease_expired", [])
    assert refused["state"] == "locked_down" and company_server.uploads == []
    assert company_server.requests[since:] == []
    _wire(locked)

    company_server.policy["offline"]["max_days"] = 7
    assert policy.refresh().state == "current"
    company_server.revoke(record.device_id)
    assert documents.overview()["state"] == "disconnected"
