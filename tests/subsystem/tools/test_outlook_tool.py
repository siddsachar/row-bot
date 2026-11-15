"""Outlook's mail and calendar tools on Microsoft Graph v1.0 (N10): each request's shape, plain results with
Outlook links, and approvals that mirror Gmail's (reading never asks; sending, replying, forwarding, deleting,
cancelling, answering and inviting do, with a card naming who it reaches and what it says). Microsoft's
sign-in and Graph are fakes on loopback; no token is ever in a result."""

from __future__ import annotations

import base64
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from row_bot import account_tokens, microsoft_account, secret_store
from row_bot.tools import approval_gate, outlook_tool
from tests.helpers.fake_microsoft import ADDRESS, CLIENT_ID, fake_microsoft
from tests.subsystem.plugins.conftest import MemoryKeyring

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]
LINK = "https://outlook.office365.com/owa/?ItemID=AAMk1&exvsurl=1"
MESSAGE = {"id": "AAMk1/x+y=", "subject": "Q3 numbers", "from": {"emailAddress": {"name": "Amy Lee", "address": "amy@example.test"}},
           "toRecipients": [{"emailAddress": {"address": ADDRESS}}], "ccRecipients": [{"emailAddress": {"address": "bob@example.test"}}],
           "receivedDateTime": "2026-10-21T14:02:00Z", "isRead": False, "flag": {"flagStatus": "notFlagged"},
           "hasAttachments": True, "bodyPreview": "Here are the numbers for the quarter.", "webLink": LINK}
EVENT = {"id": "AAMkEv1", "subject": "Team sync", "start": {"dateTime": "2026-10-23T10:00:00.0000000", "timeZone": "Europe/London"},
         "end": {"dateTime": "2026-10-23T10:30:00.0000000", "timeZone": "Europe/London"},
         "location": {"displayName": "Room 4"}, "isOrganizer": True, "isOnlineMeeting": True,
         "onlineMeeting": {"joinUrl": "https://teams.microsoft.com/l/meetup-join/1"},
         "attendees": [{"emailAddress": {"name": "Amy Lee", "address": "amy@example.test"}}],
         "organizer": {"emailAddress": {"address": ADDRESS}}, "webLink": "https://outlook.office365.com/calendar/item/1"}


@pytest.fixture
def outlook(tmp_path, monkeypatch):
    """Signed in to Outlook (a saved refresh token only), with Graph faked, the person asked through a fake
    card (``asked``; ``answer`` is their reply) and the computer in London."""
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "profile"))
    secret_store._set_backend_for_tests(MemoryKeyring())
    monkeypatch.setattr(outlook_tool, "_zone", lambda: "Europe/London")
    asked: list[dict] = []
    state = {"answer": True}
    monkeypatch.setattr(approval_gate, "current_approval_mode", lambda: "approve")
    monkeypatch.setattr(approval_gate, "_interrupt", lambda payload: asked.append(payload) or state["answer"])
    try:
        with fake_microsoft(monkeypatch) as fake:
            microsoft_account.save_client(CLIENT_ID)
            account_tokens.write("microsoft", {"refresh_token": fake.refresh_token, "client_id": CLIENT_ID,
                                               "tenant": "common", "address": ADDRESS, "name": "You"})
            fake.asked, fake.state = asked, state
            yield fake
    finally:
        secret_store._set_backend_for_tests(None)


def _no_token(text: str, fake) -> bool:
    return not any(token in text for token in (fake.access_token, fake.refresh_token, "refresh-1"))


def test_reading_mail_lists_and_reads_with_outlook_links_and_never_asks(outlook):
    outlook.graph[("GET", "/me/mailFolders/inbox/messages")] = {"value": [MESSAGE]}
    found = outlook_tool.search_mail(query='numbers "Q3"', max_results=40)
    (request,) = outlook.seen("GET", "/v1.0/me/mailFolders/inbox/messages")
    assert request.query["$search"] == '"numbers  Q3"' and request.query["$top"] == "25"
    assert found.startswith("1 message in Inbox matching")
    assert "Q3 numbers — from Amy Lee <amy@example.test>" in found and "unread · attachments" in found
    assert f"id: {MESSAGE['id']} · Open in Outlook: {LINK}" in found
    outlook.graph[("GET", "/me/messages/AAMk1%2Fx%2By%3D")] = {**MESSAGE, "body": {"content": "Hello " * 10_000}}
    outlook.graph[("GET", "/me/messages/AAMk1%2Fx%2By%3D/attachments")] = {"value": [{"name": "q3.xlsx", "size": 245_760}]}
    read = outlook_tool.read_mail(message_id=MESSAGE["id"])
    (opened,) = outlook.seen("GET", "/v1.0/me/messages/AAMk1%2Fx%2By%3D")  # The id is one path segment.
    assert opened.headers["prefer"] == 'outlook.body-content-type="text"'
    assert "Attachments: q3.xlsx (240 KB)" in read and f"Open in Outlook: {LINK}" in read
    assert len(read) < 35_000  # A long body is bounded; the rest stays readable.
    assert outlook.asked == [] and _no_token(found + read, outlook)
    outlook.graph[("GET", "/me/mailFolders/inbox/messages")] = {"value": []}
    assert outlook_tool.search_mail() == "No messages in Inbox."  # Said plainly, never left empty.


def test_a_draft_is_listed_as_a_draft_from_this_mailbox_not_from_unknown(outlook):
    """U22: Graph gives a draft no sender until it is sent."""
    draft = {**MESSAGE, "id": "AAMkDraft", "subject": "Launch note", "from": None, "isDraft": True, "isRead": True,
             "hasAttachments": False}
    outlook.graph[("GET", "/me/mailFolders/drafts/messages")] = {"value": [draft]}
    found = outlook_tool.search_mail(folder="drafts")
    assert f"Launch note — Draft · from {ADDRESS}," in found and "unknown" not in found


def test_sending_mail_sends_what_its_card_showed(outlook, tmp_path):
    from row_bot.tools import registry

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "report.pdf").write_bytes(b"%PDF-1.7 numbers")
    registry.set_tool_config("filesystem", "workspace_root", str(workspace))
    outlook.graph[("POST", "/me/sendMail")] = 202
    message = {"to": ["amy@example.test"], "cc": ["bob@example.test"], "subject": "Q3 numbers",
               "body": "Hi Amy,\n\nThe numbers are attached.", "attachments": ["report.pdf"]}
    question, card = outlook_tool.approval_card("outlook_send_mail", message)
    assert question == f"Send this email from {ADDRESS}?"
    assert card.splitlines()[:5] == [f"From: {ADDRESS}", "To: amy@example.test", "Cc: bob@example.test",
                                     "Subject: Q3 numbers", "Attachments: report.pdf (1 KB)"]
    assert card.endswith("Body:\nHi Amy,\n\nThe numbers are attached.")
    sent = outlook_tool.send_mail(**message)
    assert sent == "Sent “Q3 numbers” to amy@example.test (cc bob@example.test) with 1 attachment. A copy is in Sent Items."
    (request,) = outlook.seen("POST", "/v1.0/me/sendMail")
    body = request.body["message"]
    assert [r["emailAddress"]["address"] for r in body["toRecipients"]] == ["amy@example.test"]
    assert body["body"] == {"contentType": "Text", "content": message["body"]} and request.body["saveToSentItems"]
    (attached,) = body["attachments"]
    assert (attached["name"], base64.b64decode(attached["contentBytes"])) == ("report.pdf", b"%PDF-1.7 numbers")
    refused = outlook_tool.send_mail(to=["amy@example.test\r\nBcc: eve@example.test"], subject="x", body="y")
    assert refused.startswith("Outlook couldn't do that:") and len(outlook.seen("POST", "/v1.0/me/sendMail")) == 1


def test_a_file_over_3_mb_goes_up_in_pieces_onto_a_draft_that_is_then_sent(outlook, tmp_path):
    """5.3.1 A4: Graph takes 3 MB in the message itself and up to 150 MB through an upload session."""
    from row_bot.tools import registry

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    video = bytes(range(256)) * (4 * 1024 * 1024 // 256 + 7)  # Just over 4 MB: two pieces.
    (workspace / "demo.mp4").write_bytes(video)
    (workspace / "notes.txt").write_bytes(b"small")
    registry.set_tool_config("filesystem", "workspace_root", str(workspace))
    outlook.graph[("POST", "/me/messages")] = {"id": "AAMkDraft", "webLink": LINK}
    outlook.graph[("POST", "/me/messages/AAMkDraft/attachments/createUploadSession")] = {
        "uploadUrl": outlook.url + "/upload/session-1?authtoken=secret"}
    outlook.graph[("POST", "/me/messages/AAMkDraft/send")] = 202

    sent = outlook_tool.send_mail(to=["amy@example.test"], subject="Demo", body="Here it is.",
                                  attachments=["demo.mp4", "notes.txt"])

    assert sent.startswith("Sent “Demo” to amy@example.test with 2 attachments.")
    (draft,) = outlook.seen("POST", "/v1.0/me/messages")
    assert [item["name"] for item in draft.body["attachments"]] == ["notes.txt"]  # Small files ride along.
    (session,) = outlook.seen("POST", "/v1.0/me/messages/AAMkDraft/attachments/createUploadSession")
    assert session.body == {"AttachmentItem": {"attachmentType": "file", "name": "demo.mp4", "size": len(video),
                                               "contentType": "video/mp4"}}
    pieces = outlook.seen("PUT", "/upload/session-1")
    assert len(pieces) == 2 and all("authorization" not in piece.headers for piece in pieces)
    assert bytes(outlook.uploaded["/upload/session-1"]) == video
    assert outlook.seen("POST", "/v1.0/me/sendMail") == [] and len(outlook.seen("POST", "/v1.0/me/messages/AAMkDraft/send")) == 1
    assert _no_token(sent, outlook)


def test_an_upload_address_off_microsoft_s_hosts_is_never_used(outlook, tmp_path):
    from row_bot.tools import registry

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "big.bin").write_bytes(b"x" * (3 * 1024 * 1024 + 1))
    registry.set_tool_config("filesystem", "workspace_root", str(workspace))
    outlook.graph[("POST", "/me/messages")] = {"id": "AAMkDraft"}
    outlook.graph[("POST", "/me/messages/AAMkDraft/attachments/createUploadSession")] = {
        "uploadUrl": "https://attacker.example/upload"}

    refused = outlook_tool.draft_mail(to=["amy@example.test"], subject="x", body="y", attachments=["big.bin"])

    assert refused.startswith("Outlook couldn't do that:") and outlook.uploaded == {}


def test_reply_forward_file_and_delete_send_graphs_own_requests(outlook):
    path = "/me/messages/AAMk1%2Fx%2By%3D"
    outlook.graph[("GET", path)] = MESSAGE
    for action in ("reply", "replyAll", "forward"):
        outlook.graph[("POST", f"{path}/{action}")] = 202
    outlook.graph[("PATCH", path)] = {"id": MESSAGE["id"]}
    outlook.graph[("POST", f"{path}/move")] = lambda seen: {"id": "AAMk-moved-" + seen.body["destinationId"]}
    assert outlook_tool.reply_mail(message_id=MESSAGE["id"], body="Thanks!") == (
        "Replied to Amy Lee <amy@example.test>: “Q3 numbers”. A copy is in Sent Items.")
    assert outlook.seen("POST", f"/v1.0{path}/reply")[0].body == {"comment": "Thanks!"}
    outlook_tool.reply_mail(message_id=MESSAGE["id"], body="All: thanks", reply_all=True)
    assert outlook.seen("POST", f"/v1.0{path}/replyAll")[0].body == {"comment": "All: thanks"}
    outlook_tool.forward_mail(message_id=MESSAGE["id"], to=["carol@example.test"], comment="FYI")
    assert outlook.seen("POST", f"/v1.0{path}/forward")[0].body == {
        "toRecipients": [{"emailAddress": {"address": "carol@example.test"}}], "comment": "FYI"}
    filed = outlook_tool.update_mail(message_id=MESSAGE["id"], move_to="archive", flag="flagged", read=True)
    assert filed == "“Q3 numbers”: marked read, flagged, moved to Archive.\nIts new id: AAMk-moved-archive"
    assert outlook.seen("PATCH", f"/v1.0{path}")[0].body == {"isRead": True, "flag": {"flagStatus": "flagged"}}
    # Deleting asks first, so a move to Deleted Items never stands in for it.
    refused = outlook_tool.update_mail(message_id=MESSAGE["id"], move_to="deleteditems")
    assert refused.startswith("To delete it, use outlook_delete_mail") and len(outlook.seen("POST", f"/v1.0{path}/move")) == 1
    deleted = outlook_tool.delete_mail(message_id=MESSAGE["id"])
    assert deleted.startswith("Moved “Q3 numbers” to Deleted Items.")
    assert [seen.body["destinationId"] for seen in outlook.seen("POST", f"/v1.0{path}/move")] == ["archive", "deleteditems"]


def test_the_cards_for_a_reply_a_forward_and_a_delete_name_the_message_and_who_it_reaches(outlook):
    outlook.graph[("GET", "/me/messages/AAMk1%2Fx%2By%3D")] = MESSAGE
    question, card = outlook_tool.approval_card("outlook_reply_mail", {"message_id": MESSAGE["id"], "body": "Thanks!",
                                                                      "reply_all": True})
    assert question == "Reply to everyone on “Q3 numbers” from Amy Lee <amy@example.test>?"
    assert "To: Amy Lee <amy@example.test>, bob@example.test" in card and card.endswith("Body: Thanks!")
    question, card = outlook_tool.approval_card("outlook_forward_mail", {"message_id": MESSAGE["id"],
                                                                        "to": ["carol@example.test"]})
    assert question == "Forward “Q3 numbers” from Amy Lee <amy@example.test> to carol@example.test?"
    assert "Attachments: the original's attachments go with it" in card
    question, card = outlook_tool.approval_card("outlook_delete_mail", {"message_id": MESSAGE["id"]})
    assert question == "Delete “Q3 numbers” from Amy Lee <amy@example.test>?"
    assert "Where it goes: Deleted Items, where it can be restored" in card


def test_the_agents_gate_asks_before_sending_with_outlooks_own_card(outlook):
    import row_bot.agent as agent
    from row_bot.tools.outlook_tool import OutlookMailTool

    outlook.graph[("POST", "/me/sendMail")] = 202
    tools = {tool.name: tool for tool in OutlookMailTool().as_langchain_tools()}
    assert set(tools) == set(outlook_tool.MAIL_OPERATIONS)
    assert f"for “email me”, is {ADDRESS}" in tools["outlook_send_mail"].description  # "Email me the result".
    assert OutlookMailTool().destructive_tool_names == {"outlook_send_mail", "outlook_reply_mail",
                                                        "outlook_forward_mail", "outlook_delete_mail"}
    asked = []
    send = tools["outlook_send_mail"]
    agent._wrap_with_interrupt_gate(send)
    token = agent._approval_mode_var.set("approve")
    try:
        original = agent.interrupt
        agent.interrupt = lambda payload: asked.append(payload) or False  # The person says no.
        refused = send.invoke({"to": ["amy@example.test"], "subject": "Hello", "body": "Hi"})
        agent.interrupt = lambda payload: asked.append(payload) or True
        sent = send.invoke({"to": ["amy@example.test"], "subject": "Hello", "body": "Hi"})
    finally:
        agent.interrupt = original
        agent._approval_mode_var.reset(token)
    assert "cancelled" in refused.lower() and "Sent “Hello” to amy@example.test" in sent
    assert len(outlook.seen("POST", "/v1.0/me/sendMail")) == 1  # Only the approved one went.
    assert asked[0]["question"] == f"Send this email from {ADDRESS}?"
    assert asked[0]["description"] == f"From: {ADDRESS}\nTo: amy@example.test\nSubject: Hello\nBody: Hi"


def test_listing_events_and_free_times_use_the_computers_zone(outlook):
    outlook.graph[("GET", "/me/calendarView")] = {"value": [EVENT]}
    listed = outlook_tool.list_events(start="2026-10-23", end="2026-10-23")
    (request,) = outlook.seen("GET", "/v1.0/me/calendarView")
    assert (request.query["startDateTime"], request.query["endDateTime"]) == ("2026-10-22T23:00:00Z", "2026-10-23T23:00:00Z")
    assert request.headers["prefer"] == 'outlook.timezone="Europe/London"'
    assert "1. Team sync — Fri 23 Oct 2026 10:00–10:30 · Room 4 · Teams · 1 attendee · you organise" in listed
    assert "Join: https://teams.microsoft.com/l/meetup-join/1" in listed
    assert "Open in Outlook: https://outlook.office365.com/calendar/item/1" in listed
    free = outlook_tool.find_free_times(start="2026-10-23", end="2026-10-23", duration_minutes=60)
    assert free.splitlines()[1:] == ["- Fri 23 Oct 2026 09:00–10:00", "- Fri 23 Oct 2026 10:30–17:00"]
    assert outlook.asked == []


def test_free_times_skip_weekends_and_gaps_too_short():
    london = ZoneInfo("Europe/London")
    saturday, tuesday = datetime(2026, 10, 24, tzinfo=london), datetime(2026, 10, 27, tzinfo=london)
    busy = [(datetime(2026, 10, 26, 9, 20, tzinfo=london), datetime(2026, 10, 26, 16, 45, tzinfo=london))]
    assert outlook_tool.free_times(busy, saturday, tuesday, 30, True) == []
    assert outlook_tool.free_times(busy, saturday, tuesday, 15, True) == [
        (datetime(2026, 10, 26, 9, 0, tzinfo=london), busy[0][0]), (busy[0][1], datetime(2026, 10, 26, 17, 0, tzinfo=london))]


def test_an_event_without_attendees_is_added_at_once_and_one_with_attendees_asks_first(outlook):
    made = {**EVENT, "attendees": []}
    outlook.graph[("POST", "/me/events")] = made
    added = outlook_tool.create_event(subject="Focus time", start="2026-10-23 14:00", end="2026-10-23 15:00")
    assert added.startswith("Added “Focus time”") and outlook.asked == []
    (request,) = outlook.seen("POST", "/v1.0/me/events")
    assert request.body["start"] == {"dateTime": "2026-10-23T14:00:00", "timeZone": "Europe/London"}
    assert "attendees" not in request.body
    outlook.state["answer"] = False
    refused = outlook_tool.create_event(subject="Team sync", start="2026-10-23 10:00", end="2026-10-23 10:30",
                                        location="Room 4", attendees=["amy@example.test"], online_meeting=True)
    assert "nothing was changed in Outlook" in refused and len(outlook.seen("POST", "/v1.0/me/events")) == 1
    (asked,) = outlook.asked
    assert asked["question"] == "Send invitations for “Team sync”?"
    assert asked["description"].splitlines() == ["Event: Team sync", "When: Fri 23 Oct 2026 10:00–10:30 (Europe/London)",
                                                 "Where: Room 4", "Teams meeting: Yes", "Attendees: amy@example.test"]
    outlook.state["answer"] = True
    outlook.graph[("POST", "/me/events")] = EVENT
    invited = outlook_tool.create_event(subject="Team sync", start="2026-10-23 10:00", end="2026-10-23 10:30",
                                        location="Room 4", attendees=["amy@example.test"], online_meeting=True)
    assert "Invitations went to amy@example.test." in invited and "Teams link: https://teams.microsoft.com" in invited
    body = outlook.seen("POST", "/v1.0/me/events")[-1].body
    assert body["isOnlineMeeting"] is True and body["attendees"][0]["type"] == "required"


def test_changing_cancelling_and_answering_events(outlook):
    path = "/me/events/AAMkEv1"
    outlook.graph[("GET", path)] = EVENT
    outlook.graph[("PATCH", path)] = {**EVENT, "start": {"dateTime": "2026-10-23T11:00:00", "timeZone": "Europe/London"},
                                      "end": {"dateTime": "2026-10-23T11:30:00", "timeZone": "Europe/London"}}
    for action in ("cancel", "accept", "decline", "tentativelyAccept"):
        outlook.graph[("POST", f"{path}/{action}")] = 202
    moved = outlook_tool.update_event(event_id="AAMkEv1", start="2026-10-23 11:00", end="2026-10-23 11:30")
    assert outlook.asked[-1]["question"] == "Change “Team sync” and update its attendees?"  # It has attendees.
    assert moved.startswith("Changed “Team sync” (Fri 23 Oct 2026 11:00–11:30). Its attendees get the update.")
    question, card = outlook_tool.approval_card("outlook_cancel_event", {"event_id": "AAMkEv1", "comment": "Moved"})
    assert question == "Cancel “Team sync” and tell its attendees?"
    assert card.splitlines()[:4] == ["Event: Team sync", "When: Fri 23 Oct 2026 10:00–10:30", "Where: Room 4 · Teams",
                                     "Attendees: Amy Lee <amy@example.test>"]
    assert outlook_tool.cancel_event(event_id="AAMkEv1", comment="Moved") == (
        "Cancelled “Team sync” (Fri 23 Oct 2026 10:00–10:30) and told its attendee.")
    assert outlook.seen("POST", f"/v1.0{path}/cancel")[0].body == {"comment": "Moved"}
    answered = outlook_tool.respond_event(event_id="AAMkEv1", response="tentative", comment="Maybe")
    assert answered.startswith("Tentatively accepted “Team sync”")
    assert outlook.seen("POST", f"/v1.0{path}/tentativelyAccept")[0].body == {"comment": "Maybe", "sendResponse": True}
    from row_bot.tools.outlook_tool import OutlookCalendarTool
    assert OutlookCalendarTool().destructive_tool_names == {"outlook_cancel_event", "outlook_respond_event"}


def test_a_failure_is_said_plainly_and_a_sign_in_that_stopped_working_says_how_to_fix_it(outlook):
    missing = outlook_tool.read_mail(message_id="gone")
    assert missing == "Outlook couldn't do that: Outlook couldn't find that. It may have been moved or deleted."
    outlook.revoked.add(outlook.refresh_token)
    microsoft_account._ACCESS.clear()
    stopped = outlook_tool.search_mail()
    assert "sign in again in Settings › Apps › Outlook" in stopped and _no_token(missing + stopped, outlook)


def test_without_a_sign_in_the_tools_are_not_offered(outlook):
    from row_bot.tools.outlook_tool import OutlookCalendarTool, OutlookMailTool

    assert OutlookMailTool().as_langchain_tools() and OutlookCalendarTool().as_langchain_tools()
    microsoft_account.forget()
    assert OutlookMailTool().as_langchain_tools() == [] and OutlookCalendarTool().as_langchain_tools() == []
    assert outlook.requests == []  # Offering them never contacts Microsoft.
