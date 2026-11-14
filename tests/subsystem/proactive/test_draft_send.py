"""Sending a reply Row-Bot drafted in the background: the approval card always shows, Hands-off included, and
only the person's approval of exactly what it showed sends anything (over HTTP, with a recorded mail app)."""
from __future__ import annotations

import pytest

from tests.subsystem.client_protocol.test_protocol_security import bootstrap, client_app
from tests.subsystem.proactive.conftest import at

pytestmark = pytest.mark.subsystem


@pytest.fixture
def mail(proactive, monkeypatch):
    """A follow-up to Priya with a drafted reply; Gmail on and signed in, its sends recorded; Hands-off."""
    from row_bot.proactive import send
    from row_bot.proactive.model import Candidate, Evidence
    from row_bot.tools import approval_gate

    sent: list[tuple[dict, str]] = []
    monkeypatch.setattr(send, "connected", lambda source: source == "gmail")
    monkeypatch.setitem(send.SENDERS, "gmail", lambda arguments, thread: sent.append((arguments, thread)) or "Sent")
    monkeypatch.setattr(approval_gate, "current_approval_mode", lambda: "full")
    moment = proactive.store.add_moment(Candidate(
        kind="follow_up", key="follow_up:gmail:t1", title="No reply from priya@example.com about “Q3 numbers”",
        summary="No reply in 4 days.", topic="priya@example.com",
        evidence=(Evidence("thread", "t1", "Q3 numbers", "gmail", seen="m9"),)),
        title="No reply from Priya about “Q3 numbers”", body="", now=at("2026-11-16 09:00"))
    draft = proactive.store.add_draft(moment["id"], "reply", "Re: Q3 numbers", "Hi Priya, any news on Q3?",
                                      thread_id="", now=at("2026-11-16 09:01"))
    client, _service, _active = client_app()
    with client:
        _data, headers = bootstrap(client)
        yield client, headers, draft, moment, sent


def test_the_draft_says_who_it_replies_to_and_edits_are_saved_in_row_bot(mail):
    client, headers, draft, _moment, sent = mail
    url = f"/api/v1/proactive/drafts/{draft['id']}"

    view = client.get(url, headers=headers).json()
    assert (view["reply_to"], view["subject"], view["thread"], view["app"]) == (
        "priya@example.com", "Re: Q3 numbers", "Q3 numbers", "Gmail")
    assert view["send_blocked"] == "" and view["send"] is None

    saved = client.put(url, json={"body": "Hi Priya, could you send Q3 by Friday?"}, headers=headers)
    assert saved.status_code == 200, saved.text
    assert saved.json()["body"] == "Hi Priya, could you send Q3 by Friday?" and saved.json()["updated_at"]
    assert client.get(url, headers=headers).json()["body"] == "Hi Priya, could you send Q3 by Friday?"
    assert client.get("/api/v1/proactive/drafts", headers=headers).json()["items"][0]["body"].startswith("Hi Priya, could")
    assert sent == []


def test_hands_off_still_shows_the_card_and_only_approving_exactly_that_card_sends(mail):
    client, headers, draft, moment, sent = mail
    url = f"/api/v1/proactive/drafts/{draft['id']}"

    asked = client.post(f"{url}/send", headers=headers)
    assert asked.status_code == 200, asked.text
    card = asked.json()["send"]
    assert card["destination"] == "priya@example.com" and card["text"] == "Hi Priya, any news on Q3?"
    assert card["reason"] == "Row-Bot wrote this reply on its own, so only you can send it."
    assert sent == [], "Hands-off didn't skip the card"

    wrong = client.post(f"{url}/send/answer", json={"binding": "0" * 64, "approved": True}, headers=headers)
    assert wrong.json()["outcome"] == "changed" and sent == []

    client.post(f"{url}/send", headers=headers)
    card = client.get(url, headers=headers).json()["send"]
    done = client.post(f"{url}/send/answer", json={"binding": card["binding"], "approved": True}, headers=headers)
    assert done.status_code == 200, done.text
    assert done.json()["outcome"] == "sent" and done.json()["draft"]["sent_at"]
    assert sent == [({"to": ["priya@example.com"], "subject": "Re: Q3 numbers",
                      "message": "Hi Priya, any news on Q3?"}, "t1")]
    assert client.get(f"/api/v1/proactive/moments/{moment['id']}", headers=headers).json()["moment"]["state"] == "acted"
    again = client.post(f"{url}/send", headers=headers)
    assert again.status_code == 422 and len(sent) == 1, "sent once, never again"


def test_a_refusal_sends_nothing_and_an_edit_after_the_card_asks_again(mail):
    client, headers, draft, _moment, sent = mail
    url = f"/api/v1/proactive/drafts/{draft['id']}"

    card = client.post(f"{url}/send", headers=headers).json()["send"]
    refused = client.post(f"{url}/send/answer", json={"binding": card["binding"], "approved": False}, headers=headers)
    assert refused.json()["outcome"] == "denied" and refused.json()["draft"]["send"] is None
    assert sent == []

    card = client.post(f"{url}/send", headers=headers).json()["send"]
    client.put(url, json={"body": "Something else entirely."}, headers=headers)
    stale = client.post(f"{url}/send/answer", json={"binding": card["binding"], "approved": True}, headers=headers)
    assert stale.json()["outcome"] == "changed" and sent == [], "an approval for other words never sends"


def test_a_draft_that_cannot_go_says_why_and_its_send_is_refused(mail, monkeypatch):
    from row_bot.proactive import send

    client, headers, draft, _moment, sent = mail
    monkeypatch.setattr(send, "connected", lambda source: False)
    url = f"/api/v1/proactive/drafts/{draft['id']}"

    assert client.get(url, headers=headers).json()["send_blocked"] == "Sign in to Gmail to send."
    assert client.post(f"{url}/send", headers=headers).status_code == 422
    assert sent == []
