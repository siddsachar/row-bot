"""The review queue end to end: a person shares to a space they read, the space's owner (or an admin) approves it,
edited or moved, into a fact with its source, or rejects it with a reason; the submitter withdraws one while it
waits; the limit on waiting shares; Share to company turned off by policy; a device may share but never review; and
the audit events, which never carry what was shared. The fake provider for sign-in and a real (throwaway) Postgres
as the runtime role. Security-sensitive (a share holds what someone wrote in a chat, and before approval nobody but
its submitter and its space's managers may see it): these run in the pull request lane's Org job.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest

from tests.fixtures.postgres import import_psycopg

psycopg = import_psycopg()
pytest.importorskip("joserfc", reason="joserfc is not installed: uv sync --locked --all-extras --group test --group org")

from fastapi.testclient import TestClient  # noqa: E402

from row_bot_org.db.rls import principal_transaction  # noqa: E402
from tests.fixtures.oidc_idp import FakeIdp, running_idp  # noqa: E402
from tests.helpers.org_audit import stored_events, summary  # noqa: E402
from tests.subsystem.org.test_org_brain import (  # noqa: E402
    admin_of, cited, company_space, me, new_space, org_server, person, put_policy, recall, runtime_pool, superuser_sql,
)
from tests.subsystem.org.test_org_device_sign_in import bearer, join, row_bot  # noqa: E402

pytestmark = pytest.mark.postgres

SHARE = {"title": "Commission timing", "text": "Commission is paid on the 5th of each month.",
         "source_note": "From a chat on 12 Nov"}
NOT_FOUND = {"error": "submission_not_found"}


@pytest.fixture
def idp() -> Iterator[FakeIdp]:
    with running_idp("okta") as running:
        yield running


def submit(client: TestClient, space_id: str, **changes: str):
    return client.post(f"/api/brain/spaces/{space_id}/submissions", json={**SHARE, **changes})


def submitted(client: TestClient, space_id: str, **changes: str) -> dict:
    response = submit(client, space_id, **changes)
    assert response.status_code == 201, response.text
    return response.json()


def mine(client: TestClient, **headers: str) -> list[dict]:
    response = client.get("/api/brain/submissions?mine=1", headers=headers or None)
    assert response.status_code == 200, response.text
    return response.json()["submissions"]


def queue(client: TestClient, query: str = "") -> dict:
    response = client.get(f"/api/brain/review{query}")
    assert response.status_code == 200, response.text
    return response.json()


def ids(submissions: list[dict]) -> list[str]:
    return [submission["id"] for submission in submissions]


def shares_said(database) -> list[str]:
    """Every stored event as JSON text, to show that no title, text, note or reason ever reaches the audit log."""
    return [json.dumps(event) for event in stored_events(database.owner_url)]


# -- submit, review, approve with edits -------------------------------------------------------------

def test_a_share_waits_for_its_spaces_owner_who_approves_it_edited_into_a_fact_with_its_source(org_database,
                                                                                               idp) -> None:
    with org_server(org_database, idp) as server:
        admin = admin_of(server, idp)
        priya, sam = person(server, idp, "priya"), person(server, idp, "sam", ["Sales"])
        priya_id, sam_id = me(priya), me(sam)
        sales = new_space(admin, "Sales team", reader_groups=["Sales"], owner_principals=[priya_id])

        share = submitted(sam, sales)
        assert {key: share[key] for key in ("kind", "space_id", "space_name", "title", "text", "source_note",
                                            "submitted_by", "status", "reviewed_by", "decided_at", "reason",
                                            "fact_id")} == {
            "kind": "share", "space_id": sales, "space_name": "Sales team", **SHARE, "submitted_by": sam_id,
            "status": "pending", "reviewed_by": None, "decided_at": None, "reason": None, "fact_id": None}
        assert ids(mine(sam)) == [share["id"]]
        # Nothing is in the space yet: its readers find nothing of it.
        assert sam.get(f"/api/brain/spaces/{sales}/facts").json()["facts"] == []
        assert recall(sam, "commission paid")["hits"] == []

        # The space's owner (not an admin) reviews it: only the spaces she owns, and how many wait.
        waiting = queue(priya)
        assert (ids(waiting["submissions"]), waiting["pending_count"], waiting["truncated"]) == ([share["id"]], 1, False)
        assert waiting["spaces"] == [{"id": sales, "name": "Sales team", "kind": "group"}]
        assert queue(priya, "?status=approved")["submissions"] == []

        approved = priya.post(f"/api/brain/review/{share['id']}/approve",
                              json={"title": "When commission is paid",
                                    "text": "Commission is paid on the 5th working day of each month."})
        assert approved.status_code == 200, approved.text
        decided, fact = approved.json()["submission"], approved.json()["fact"]
        assert (decided["status"], decided["reviewed_by"], decided["title"], decided["fact_id"]) == (
            "approved", priya_id, "When commission is paid", fact["id"])
        assert decided["decided_at"] is not None
        # The fact is the reviewer's, with the share and the person who offered it as its source.
        assert {key: fact[key] for key in ("space_id", "text", "written_by", "submitted_by",
                                           "source_submission_id", "retired_at")} == {
            "space_id": sales, "text": "Commission is paid on the 5th working day of each month.",
            "written_by": priya_id, "submitted_by": sam_id, "source_submission_id": share["id"], "retired_at": None}
        # Now everyone in the space reads it and finds it.
        assert ids(sam.get(f"/api/brain/spaces/{sales}/facts").json()["facts"]) == [fact["id"]]
        assert cited(recall(sam, "commission working day")) == [
            ("fact", "Commission is paid on the 5th working day of each month.")]
        (own,) = mine(sam)
        assert (own["status"], own["fact_id"], own["reviewed_by"]) == ("approved", fact["id"], priya_id)

        # Decided once: again is refused, and nothing more is made.
        again = priya.post(f"/api/brain/review/{share['id']}/approve", json={})
        assert (again.status_code, again.json()) == (409, {"error": "submission_decided", "status": "approved"})
        assert queue(priya)["pending_count"] == 0
        assert ids(queue(priya, "?status=all")["submissions"]) == [share["id"]]

        # The fact deleted, the share stays approved without one; the space deleted, both go.
        assert priya.delete(f"/api/brain/facts/{fact['id']}").status_code == 204
        (own,) = mine(sam)
        assert (own["status"], own["fact_id"]) == ("approved", None)
        second = submitted(sam, sales, title="Quota", text="The quota is set in January.")
        assert priya.post(f"/api/brain/review/{second['id']}/approve", json={}).status_code == 200
        assert admin.delete(f"/api/admin/brain/spaces/{sales}").status_code == 204
        assert superuser_sql(org_database, "SELECT count(*) FROM row_bot_org.brain_submissions") == [(0,)]
        assert superuser_sql(org_database, "SELECT count(*) FROM row_bot_org.brain_facts") == [(0,)]

    events = [summary(event)[:5] + (event["layer"], event["details"]) for event in
              stored_events(org_database.owner_url, "share.submitted", "share.approved", "fact.changed")]
    assert events[:3] == [
        ("share.submitted", "person", sam_id, share["id"], "succeeded", "team",
         {"item_kind": "fact", "space_id": sales}),
        ("fact.changed", "person", priya_id, fact["id"], "succeeded", "team",
         {"change": "created", "space_id": sales}),
        ("share.approved", "person", priya_id, share["id"], "succeeded", "team",
         {"item_kind": "fact", "space_id": sales, "fact_id": fact["id"], "edited": "title,text"}),
    ]
    # Approved as sent: nothing edited.
    assert events[-1][-1] == {"item_kind": "fact", "space_id": sales, "fact_id": events[-2][3]}
    said = "\n".join(shares_said(org_database))
    for words in (*SHARE.values(), "When commission is paid", "5th working day", "The quota is set in January."):
        assert words not in said


# -- who sees a share -------------------------------------------------------------------------------

def test_nobody_but_its_submitter_and_the_spaces_managers_ever_sees_a_share(org_database, idp) -> None:
    with org_server(org_database, idp) as server:
        admin = admin_of(server, idp)
        priya, sam = person(server, idp, "priya"), person(server, idp, "sam", ["Sales"])
        lin, eve = person(server, idp, "lin", ["Sales"]), person(server, idp, "eve", ["Engineering"])
        olga = person(server, idp, "olga", ["Engineering"])
        priya_id, sam_id, lin_id, eve_id, olga_id = me(priya), me(sam), me(lin), me(eve), me(olga)
        sales = new_space(admin, "Sales team", reader_groups=["Sales"], owner_principals=[priya_id])
        engineering = new_space(admin, "Engineering", reader_groups=["Engineering"], owner_principals=[olga_id])
        share = submitted(sam, sales)

        # Another member, a reader of the space who doesn't own it, and the owner of another space: nothing.
        for other in (eve, lin, olga):
            assert mine(other) == []
            assert queue(other)["submissions"] == []
            assert queue(other)["pending_count"] == 0
            for step, body in (("approve", {}), ("reject", {"reason": "No."})):
                refused = other.post(f"/api/brain/review/{share['id']}/{step}", json=body)
                assert (refused.status_code, refused.json()) == (404, NOT_FOUND)
            assert other.post(f"/api/brain/submissions/{share['id']}/withdraw").json() == NOT_FOUND
        assert queue(olga)["spaces"] == [{"id": engineering, "name": "Engineering", "kind": "group"}]
        reader = lin.get(f"/api/brain/review?space={sales}")
        assert (reader.status_code, reader.json()) == (403, {"error": "space_owner_required"})
        assert eve.get(f"/api/brain/review?space={sales}").json() == {"error": "space_not_found"}
        # Its submitter sees it but doesn't decide it.
        own = sam.post(f"/api/brain/review/{share['id']}/approve", json={})
        assert (own.status_code, own.json()) == (403, {"error": "space_owner_required"})
        assert queue(sam)["submissions"] == []
        # The owner and an admin (in the console) do.
        assert ids(queue(priya)["submissions"]) == ids(queue(admin)["submissions"]) == [share["id"]]
        assert [space["name"] for space in queue(admin)["spaces"]] == ["Company", "Engineering", "Sales team"]

        # Behind the API, row-level security alone decides who reads it.
        def visible(principal_id: str, groups: list[str]) -> int:
            with principal_transaction(pool, principal_id, groups) as conn:
                return conn.execute("SELECT count(*) FROM row_bot_org.brain_submissions").fetchone()[0]

        with runtime_pool(org_database) as pool:
            assert [visible(*who) for who in ((eve_id, ["Engineering"]), (lin_id, ["Sales"]),
                                              (olga_id, ["Engineering"]), (sam_id, ["Sales"]), (priya_id, []))] == [
                0, 0, 0, 1, 1]
            refusals = [
                # Its submitter can't decide it, even in SQL.
                (sam_id, ["Sales"], "UPDATE row_bot_org.brain_submissions SET status = 'rejected', reason = 'x',"
                                    " reviewed_by = %s, decided_at = now() WHERE id = %s", (sam_id, share["id"])),
                # Nobody shares in another's name, nor to a space they don't read.
                (eve_id, ["Engineering"], "INSERT INTO row_bot_org.brain_submissions (space_id, title, body,"
                                          " submitter_groups, submitted_by) VALUES (%s, 't', 'b', '{Engineering}',"
                                          " %s)", (engineering, sam_id)),
                (eve_id, ["Engineering"], "INSERT INTO row_bot_org.brain_submissions (space_id, title, body,"
                                          " submitter_groups) VALUES (%s, 't', 'b', '{Engineering}')", (sales,)),
                # Its owner can't decide it in another's name, or withdraw it for its submitter.
                (priya_id, [], "UPDATE row_bot_org.brain_submissions SET status = 'rejected', reason = 'x',"
                               " reviewed_by = %s, decided_at = now() WHERE id = %s", (olga_id, share["id"])),
                (priya_id, [], "UPDATE row_bot_org.brain_submissions SET status = 'withdrawn', decided_at = now()"
                               " WHERE id = %s", (share["id"],)),
                # Nor write a fact that says it came from a share without approving it.
                (priya_id, [], "INSERT INTO row_bot_org.brain_facts (space_id, body, submitted_by,"
                               " source_submission_id) VALUES (%s, 'b', %s, NULL)", (sales, sam_id)),
            ]
            for principal_id, groups, sql, args in refusals:
                with principal_transaction(pool, principal_id, groups) as conn, \
                        pytest.raises(psycopg.errors.InsufficientPrivilege):
                    conn.execute(sql, args)
        assert superuser_sql(org_database, "SELECT status, count(*) FROM row_bot_org.brain_submissions"
                                           " GROUP BY status") == [("pending", 1)]


# -- what may be shared, and how much ---------------------------------------------------------------

def test_a_person_shares_only_to_a_space_they_read_and_only_while_policy_allows_it(org_database, idp) -> None:
    with org_server(org_database, idp) as server:
        admin = admin_of(server, idp)
        priya, eve = person(server, idp, "priya"), person(server, idp, "eve", ["Engineering"])
        sales = new_space(admin, "Sales team", reader_groups=["Sales"], owner_principals=[me(priya)])
        company = company_space(eve)

        # A space they can't read doesn't exist for them, any more than one that doesn't.
        assert submit(eve, sales).json() == {"error": "space_not_found"}
        assert submit(eve, "not-a-space").json() == {"error": "space_not_found"}
        # An admin shares as a member: their groups, not their admin powers.
        assert submit(admin, sales).json() == {"error": "space_not_found"}
        for bad in ({"title": "  "}, {"title": "x" * 201}, {"text": "x" * 8001}, {"source_note": "x" * 501},
                    {"title": "Two\nlines"}):
            assert submit(eve, company, **bad).status_code == 422, bad
        assert eve.post(f"/api/brain/spaces/{company}/submissions",
                        json={**SHARE, "kind": "skill"}).status_code == 422
        assert superuser_sql(org_database, "SELECT count(*) FROM row_bot_org.brain_submissions") == [(0,)]

        longest = submitted(eve, company, text="x" * 8000, source_note="  ")
        assert (len(longest["text"]), longest["source_note"]) == (8000, None)

        put_policy(admin, {"data": {"share_to_company": False}})
        off = submit(eve, company)
        assert (off.status_code, off.json()) == (403, {"error": "share_to_company_off"})
        assert len(mine(eve)) == 1


def test_twenty_shares_wait_at_most_and_a_withdrawn_one_frees_a_place(org_database, idp) -> None:
    with org_server(org_database, idp) as server:
        admin = admin_of(server, idp)
        sam = person(server, idp, "sam", ["Sales"])
        sam_id = me(sam)
        company = company_space(sam)
        first = submitted(sam, company, title="Share 1")
        for number in range(2, 21):
            submitted(sam, company, title=f"Share {number}")
        over = submit(sam, company, title="Share 21")
        assert (over.status_code, over.json()) == (429, {"error": "too_many_pending", "max_pending": 20})

        # Only its submitter takes it back, while it waits.
        assert admin.post(f"/api/brain/submissions/{first['id']}/withdraw").json() == NOT_FOUND
        withdrawn = sam.post(f"/api/brain/submissions/{first['id']}/withdraw")
        assert withdrawn.status_code == 200, withdrawn.text
        assert (withdrawn.json()["status"], withdrawn.json()["reviewed_by"]) == ("withdrawn", None)
        again = sam.post(f"/api/brain/submissions/{first['id']}/withdraw")
        assert (again.status_code, again.json()) == (409, {"error": "submission_decided", "status": "withdrawn"})
        refused = admin.post(f"/api/brain/review/{first['id']}/approve", json={})
        assert (refused.status_code, refused.json()) == (409, {"error": "submission_decided", "status": "withdrawn"})
        assert submit(sam, company, title="Share 21").status_code == 201

        # The admin's queue: the 20 waiting, oldest first, and the withdrawn one apart.
        waiting = queue(admin, f"?space={company}")
        assert waiting["pending_count"] == 20
        assert [s["title"] for s in waiting["submissions"]] == [f"Share {n}" for n in range(2, 22)]
        assert ids(queue(admin, "?status=withdrawn")["submissions"]) == [first["id"]]
        assert sam.get("/api/brain/submissions").status_code == 422
        assert sam.get("/api/brain/review?status=everything").status_code == 422

    (event,) = [summary(event)[:5] + (event["layer"], event["details"])
                for event in stored_events(org_database.owner_url, "share.withdrawn")]
    assert event == ("share.withdrawn", "person", sam_id, first["id"], "succeeded", "company",
                     {"item_kind": "fact", "space_id": company})


# -- reject, move, too long, and devices ------------------------------------------------------------

def test_a_reviewer_rejects_with_a_reason_or_moves_a_share_and_a_device_may_share_but_never_review(org_database,
                                                                                                    idp) -> None:
    with org_server(org_database, idp) as server:
        admin = admin_of(server, idp)
        priya, sam = person(server, idp, "priya"), person(server, idp, "sam", ["Sales"])
        olga = person(server, idp, "olga", ["Engineering"])
        priya_id, sam_id, admin_id = me(priya), me(sam), me(admin)
        sales = new_space(admin, "Sales team", reader_groups=["Sales"], owner_principals=[priya_id])
        engineering = new_space(admin, "Engineering", reader_groups=["Engineering"], owner_principals=[me(olga)])
        company = company_space(admin)

        # The desktop app shares with its device's bearer, and lists its person's own; it never reviews.
        app = row_bot(server)
        sams_device = join(server, sam)
        device = bearer(sams_device["access_token"])
        shared = app.post(f"/api/brain/spaces/{sales}/submissions", json=SHARE, headers=device)
        assert shared.status_code == 201, shared.text
        share = shared.json()
        assert ids(mine(app, **device)) == [share["id"]]
        priyas_device = bearer(join(server, priya)["access_token"])
        for headers in (device, priyas_device):
            assert app.get("/api/brain/review", headers=headers).json() == {"error": "browser_session_required"}
            for step, body in (("approve", {}), ("reject", {"reason": "No."})):
                refused = app.post(f"/api/brain/review/{share['id']}/{step}", json=body, headers=headers)
                assert (refused.status_code, refused.json()) == (403, {"error": "browser_session_required"})

        # A rejection needs a reason, which its submitter then reads.
        assert priya.post(f"/api/brain/review/{share['id']}/reject", json={}).status_code == 422
        assert priya.post(f"/api/brain/review/{share['id']}/reject", json={"reason": " "}).status_code == 422
        rejected = priya.post(f"/api/brain/review/{share['id']}/reject",
                              json={"reason": "We already have this in the handbook."})
        assert rejected.status_code == 200, rejected.text
        (own,) = mine(sam)
        assert (own["status"], own["reason"], own["reviewed_by"], own["fact_id"]) == (
            "rejected", "We already have this in the handbook.", priya_id, None)
        assert priya.post(f"/api/brain/review/{share['id']}/reject", json={"reason": "Again."}).status_code == 409
        assert sam.get(f"/api/brain/spaces/{sales}/facts").json()["facts"] == []

        # A reviewer moves a share only to another space they manage.
        moved = submitted(sam, sales, title="Holidays", text="The office closes between Christmas and New Year.")
        unread = priya.post(f"/api/brain/review/{moved['id']}/approve", json={"space_id": engineering})
        assert unread.json() == {"error": "space_not_found"}
        unowned = priya.post(f"/api/brain/review/{moved['id']}/approve", json={"space_id": company})
        assert (unowned.status_code, unowned.json()) == (403, {"error": "space_owner_required"})
        by_admin = admin.post(f"/api/brain/review/{moved['id']}/approve", json={"space_id": company})
        assert by_admin.status_code == 200, by_admin.text
        assert (by_admin.json()["submission"]["space_id"], by_admin.json()["fact"]["space_id"]) == (company, company)
        assert (by_admin.json()["fact"]["written_by"], by_admin.json()["fact"]["submitted_by"]) == (admin_id, sam_id)

        # A fact holds up to 2,000 characters: a longer share is approved only once shortened.
        long = submitted(sam, sales, title="Pricing notes", text="Pricing. " * 300)
        too_long = priya.post(f"/api/brain/review/{long['id']}/approve", json={})
        assert (too_long.status_code, too_long.json()) == (
            422, {"error": "text_too_long_for_a_fact", "max_length": 2000})
        shortened = priya.post(f"/api/brain/review/{long['id']}/approve", json={"text": "List prices rise in April."})
        assert shortened.status_code == 200, shortened.text

    events = [summary(event)[:5] + (event["layer"], event["details"], event.get("device_id"))
              for event in stored_events(org_database.owner_url, "share.submitted", "share.rejected", "share.approved")]
    assert events[:2] == [
        ("share.submitted", "person", sam_id, share["id"], "succeeded", "team",
         {"item_kind": "fact", "space_id": sales}, sams_device["device_id"]),
        ("share.rejected", "person", priya_id, share["id"], "succeeded", "team",
         {"item_kind": "fact", "space_id": sales}, None),
    ]
    (moved_event,) = [event for event in events if event[0] == "share.approved" and event[3] == moved["id"]]
    assert moved_event[1:3] == ("admin", admin_id)
    assert (moved_event[5], moved_event[6]["space_id"], moved_event[6]["from_space_id"]) == ("company", company, sales)
    (shortened_event,) = [event for event in events if event[0] == "share.approved" and event[3] == long["id"]]
    assert shortened_event[6]["edited"] == "text"
    assert "We already have this in the handbook." not in "\n".join(shares_said(org_database))
