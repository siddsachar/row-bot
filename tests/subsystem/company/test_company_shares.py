"""Share to company from the app (B4b): a share goes to a company space's review queue (B4a) with the device's
bearer; the person's shares show what became of each (waiting, approved, rejected with the reason, moved); a
waiting one can be withdrawn; every refusal is said in plain words; and with no company joined, sharing turned
off by the company or the Row-Bot locked down, nothing is sent. The server is a fake (``httpx.MockTransport``)."""

from __future__ import annotations

import json

import httpx
import pytest

from row_bot.api.v1 import schemas as dto
from row_bot.company import enforce, policy, shares
from tests.fixtures.company_server import COMPANY, COMPANY_SPACE, PERSON, SALES_SPACE

pytestmark = [pytest.mark.subsystem]

SPACES = [{"id": COMPANY_SPACE, "name": "Company", "kind": "company", "is_owner": False},
          {"id": SALES_SPACE, "name": "Sales", "kind": "group", "is_owner": True}]
ENGINEERING = "5a000000-0000-4000-8000-000000000003"
FACT = "c0000000-0000-4000-8000-000000000009"


def _wire(view: dict) -> None:
    """``view`` passes the wire record, as the client API sends it."""
    dto.CompanySharesView.model_validate_json(json.dumps(view))


def _asked(server, since: int) -> list[tuple[str, str]]:
    return [(request.method, request.url.path) for request in server.requests[since:]]


def _submission(number: int, **changes) -> dict:
    """A share as B4a's server answers its submitter."""
    return {"id": f"b0000000-0000-4000-8000-00000000000{number}", "kind": "share", "space_id": SALES_SPACE,
            "space_name": "Sales", "sent_space_id": SALES_SPACE, "title": f"Share {number}", "text": "Some text.",
            "source_note": None, "submitted_by": PERSON["id"], "submitted_at": "2026-11-20T09:00:00+00:00",
            "status": "pending", "reviewed_by": None, "decided_at": None, "reason": None, "fact_id": None,
            **changes}


def test_with_no_company_joined_nothing_is_asked(company_server):
    assert shares.mine() is None
    assert shares.submit(SALES_SPACE, "Renewals", "Orion renews in March.") is None
    assert shares.withdraw("b0000000-0000-4000-8000-000000000001") is None
    assert company_server.requests == []
    assert not enforce.allows_share_to_company()
    _wire(shares.empty("not_joined"))


def test_a_share_goes_to_the_spaces_review_queue_with_the_bearer_and_is_listed(company_server):
    company_server.join_here()
    since = len(company_server.requests)

    view = shares.submit(SALES_SPACE, "  Renewal\n timing ", "Orion Foods renews every March.\nAsk early.\n",
                         "From a chat on 24 Nov 2026")

    assert _asked(company_server, since) == [("POST", f"/api/brain/spaces/{SALES_SPACE}/submissions"),
                                             ("GET", "/api/brain/spaces"), ("GET", "/api/brain/submissions")]
    for request in company_server.requests[since:]:
        assert request.headers["authorization"].startswith("Bearer rboa_") and "cookie" not in request.headers
    assert dict(company_server.requests[-1].url.params) == {"mine": "1", "status": "all"}
    assert company_server.submission_bodies == [{"title": "Renewal timing", "text": (
        "Orion Foods renews every March.\nAsk early."), "source_note": "From a chat on 24 Nov 2026"}]
    assert (view["state"], view["company"], view["outcome"], view["words"]) == ("ready", COMPANY, "sent", None)
    assert (view["allowed"], view["spaces"], view["truncated"]) == (True, SPACES, False)
    (sent,) = company_server.submissions
    assert view["shares"] == [{
        "id": sent["id"], "title": "Renewal timing", "text": "Orion Foods renews every March.\nAsk early.",
        "source_note": "From a chat on 24 Nov 2026", "space_id": SALES_SPACE, "space_name": "Sales",
        "moved": False, "status": "pending", "submitted_at": "2026-11-24T10:00:00+00:00", "decided_at": None,
        "reason": None, "fact_id": None}]
    _wire(view)

    # A blank note isn't sent at all.
    shares.submit(COMPANY_SPACE, "Holidays", "Everyone gets 25 days.", "   ")
    assert company_server.submission_bodies[-1] == {"title": "Holidays", "text": "Everyone gets 25 days."}


def test_your_shares_show_what_became_of_each_including_a_move(company_server):
    company_server.join_here()
    company_server.submissions = [
        _submission(1, status="approved", space_id=ENGINEERING, space_name="Engineering", reviewed_by=PERSON["id"],
                    decided_at="2026-11-23T10:00:00+00:00", fact_id=FACT),
        _submission(2, status="rejected", reviewed_by=PERSON["id"], decided_at="2026-11-22T10:00:00+00:00",
                    reason="We already have this in the handbook."),
        _submission(3, source_note="From a chat on 20 Nov 2026"),
        {"id": "not-an-id", "status": "pending", "title": "Broken"},
    ]

    view = shares.mine()

    approved, rejected, waiting = view["shares"]  # The malformed one is left out.
    assert (approved["status"], approved["moved"], approved["space_id"], approved["space_name"],
            approved["fact_id"], approved["decided_at"]) == (
        "approved", True, ENGINEERING, "Engineering", FACT, "2026-11-23T10:00:00+00:00")
    assert (rejected["status"], rejected["moved"], rejected["reason"], rejected["fact_id"]) == (
        "rejected", False, "We already have this in the handbook.", None)
    assert (waiting["status"], waiting["moved"], waiting["decided_at"], waiting["source_note"]) == (
        "pending", False, None, "From a chat on 20 Nov 2026")
    _wire(view)

    # One status, or all of them for anything else.
    pending = shares.mine("pending")
    assert dict(company_server.requests[-1].url.params) == {"mine": "1", "status": "pending"}
    assert [share["title"] for share in pending["shares"]] == ["Share 3"]
    shares.mine("withdrawn")
    assert dict(company_server.requests[-1].url.params) == {"mine": "1", "status": "all"}


def test_withdraw_deletes_a_waiting_share_and_says_why_when_it_cant(company_server):
    company_server.join_here()
    first = shares.submit(SALES_SPACE, "Renewals", "Orion renews in March.")["shares"][0]["id"]
    since = len(company_server.requests)

    gone = shares.withdraw(first)

    assert _asked(company_server, since)[0] == ("POST", f"/api/brain/submissions/{first}/withdraw")
    assert (gone["outcome"], gone["words"], gone["shares"]) == ("withdrawn", None, [])
    assert company_server.submissions == []
    _wire(gone)

    again = shares.withdraw(first)
    assert (again["outcome"], again["words"]) == ("not_found", "That share isn't there any more.")

    company_server.submissions = [_submission(1, status="approved", reviewed_by=PERSON["id"], fact_id=FACT,
                                              decided_at="2026-11-23T10:00:00+00:00"),
                                  _submission(2, status="rejected", reviewed_by=PERSON["id"], reason="No.",
                                              decided_at="2026-11-23T10:00:00+00:00")]
    approved = shares.withdraw(company_server.submissions[0]["id"])
    rejected = shares.withdraw(company_server.submissions[1]["id"])
    assert (approved["outcome"], approved["words"]) == ("decided", "It was already approved, so it can't be withdrawn.")
    assert rejected["words"] == "It was already rejected, so it can't be withdrawn."
    assert len(company_server.submissions) == 2
    _wire(approved)

    # Not a share's id: nothing is withdrawn.
    since = len(company_server.requests)
    assert shares.withdraw("not-an-id")["outcome"] == "not_found"
    assert not any(path.endswith("/withdraw") for _, path in _asked(company_server, since))


def test_each_refusal_is_said_in_plain_words(company_server):
    company_server.join_here()

    # What can't be sent is refused before anything is asked.
    since = len(company_server.requests)
    for title, text, note in (("", "Text.", ""), ("x" * 201, "Text.", ""), ("Title", "   ", ""),
                              ("Title", "x" * 8001, ""), ("Title", "Text.", "x" * 501)):
        refused = shares.submit(SALES_SPACE, title, text, note)
        assert (refused["outcome"], refused["words"]) == ("invalid", shares.INVALID)
        _wire(refused)
    assert company_server.requests[since:] == []
    assert shares.INVALID == ("A share needs a title of up to 200 characters and its text, up to 8,000 characters. "
                              "The note is up to 500 characters.")
    assert shares.submit(SALES_SPACE, "Title", "x" * 8000)["outcome"] == "sent"

    unread = shares.submit(ENGINEERING, "Title", "Text.")
    assert (unread["outcome"], unread["words"]) == (
        "space_not_found", "You can't share to that space any more. Choose another one.")
    assert shares.submit("not-a-space", "Title", "Text.")["outcome"] == "space_not_found"

    company_server.submissions += [_submission(number) for number in range(1, 10)]
    company_server.submissions += [_submission(number, id=f"b0000000-0000-4000-8000-0000000001{number:02d}")
                                   for number in range(10, 20)]
    waiting = shares.submit(SALES_SPACE, "Title", "Text.")
    assert (waiting["outcome"], waiting["words"]) == ("too_many_pending", (
        f"You have 20 shares waiting for review, the most {COMPANY} allows. Withdraw one, or wait until its "
        "owners review some."))
    _wire(waiting)
    # The person's own limit doesn't make Row-Bot wait before its next call to the company.
    assert shares.mine()["state"] == "ready"
    assert shares.withdraw(company_server.submissions[0]["id"])["outcome"] == "withdrawn"

    company_server.shares_today = 50
    today = shares.submit(SALES_SPACE, "Title", "Text.")
    assert (today["outcome"], today["words"]) == ("too_many_shares_today", (
        f"You've shared 50 times today, the most {COMPANY} allows in a day. Try again tomorrow."))
    company_server.shares_today = 0

    company_server.brain_answers.append(("POST", httpx.Response(422, json={"detail": []})))
    assert shares.submit(SALES_SPACE, "Title", "Text.")["outcome"] == "invalid"

    # The company turned sharing off since Row-Bot last read its policy: the server refuses it, said the same way.
    company_server.policy["data"]["share_to_company"] = False
    late = shares.submit(SALES_SPACE, "Title", "Text.")
    assert (late["outcome"], late["words"]) == (
        "share_to_company_off", "Your company turned off Share to company, so this wasn't sent.")

    # The server can't be used: B8's state.
    company_server.brain_answers.append(("GET", httpx.Response(429, json={"error": "too_many_requests"},
                                                               headers={"Retry-After": "5"})))
    busy = shares.mine()
    assert (busy["state"], busy["reason"], busy["shares"]) == ("unreachable", "busy", [])
    _wire(busy)


def test_with_sharing_turned_off_or_locked_down_nothing_is_sent(company_server):
    company_server.policy["data"]["share_to_company"] = False
    company_server.join_here()
    since = len(company_server.requests)

    off = shares.submit(SALES_SPACE, "Renewals", "Orion renews in March.")

    assert company_server.requests[since:] == []
    assert (off["outcome"], off["words"], off["allowed"]) == (
        "share_to_company_off", "Your company turned off Share to company, so this wasn't sent.", False)
    _wire(off)
    # The person's shares are still theirs to see and withdraw.
    listed = shares.mine()
    assert (listed["state"], listed["allowed"], listed["spaces"]) == ("ready", False, SPACES)
    # Settings shows the lock (C3a's words) and the client hides the action.
    assert enforce.limits(policy.current())["share_to_company"] is False
    assert {lock["setting"]: lock["words"] for lock in policy.locked(policy.current())}[
        "data.share_to_company"] == "Your company turned off Share to company"

    company_server.policy["data"]["share_to_company"] = True
    company_server.policy["offline"]["max_days"] = 0
    assert policy.refresh().state == "current"
    assert enforce.allows_share_to_company() and enforce.limits(policy.current())["share_to_company"] is True
    company_server.offline = True
    assert policy.refresh().state == "locked_down"
    company_server.offline = False
    since = len(company_server.requests)

    locked = shares.submit(SALES_SPACE, "Renewals", "Orion renews in March.")

    assert (locked["state"], locked["reason"], locked["allowed"]) == ("locked_down", "lease_expired", False)
    assert company_server.requests[since:] == [] and company_server.submission_bodies == []
    _wire(locked)
