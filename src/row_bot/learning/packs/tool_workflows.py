"""Pack: tool workflows (D30), visible split.

Errands in fake apps (``fake_apps``): a calendar, a mailbox and an issue tracker. The
verifier checks the apps' final state, including that nothing else changed (counts of
what should be untouched), so "did the right thing to the right item" is what passes.
The traps: two events with the same title, a reply that must go to the right person,
an id that must be read from a message.
"""

from __future__ import annotations

from typing import Any

from row_bot.learning.packs import PackTask, Solution, Verifier


def _event(number: int, title: str, date: str, start: str, end: str, attendees: tuple[str, ...] = (),
           location: str = "") -> dict[str, Any]:
    return {"id": f"evt_{number}", "title": title, "date": date, "start": start, "end": end,
            "attendees": list(attendees), "location": location}


def _mail(identifier: str, sender: str, subject: str, body: str, date: str, folder: str = "inbox",
          to: str = "me@example.test") -> dict[str, Any]:
    return {"id": identifier, "folder": folder, "from": sender, "to": to, "subject": subject, "body": body,
            "date": date}


def _issue(number: int, title: str, state: str = "open", labels: tuple[str, ...] = (), assignee: str = "",
           comments: tuple[str, ...] = ()) -> dict[str, Any]:
    return {"id": number, "title": title, "state": state, "labels": list(labels), "assignee": assignee,
            "comments": list(comments)}


CALENDAR = {"events": [
    _event(1, "Standup", "2026-03-04", "09:00", "09:15", ("team@example.test",)),
    _event(2, "Dentist", "2026-03-04", "11:00", "11:45", location="Smile Clinic"),
    _event(3, "1:1 with Priya", "2026-03-06", "10:00", "10:30", ("priya@example.test",)),
    _event(4, "Sync", "2026-03-11", "13:00", "13:30", ("leo@example.test",)),
    _event(5, "1:1 with Priya", "2026-03-13", "10:00", "10:30", ("priya@example.test",)),
    _event(6, "Team lunch", "2026-03-13", "12:00", "13:00", ("team@example.test",), "Canteen"),
    _event(7, "Payroll run", "2026-03-20", "09:00", "10:00"),
    _event(8, "Vendor call", "2026-03-20", "11:00", "11:30", ("orders@kestrel.example",)),
    _event(9, "Gym", "2026-03-20", "18:00", "19:00"),
    _event(10, "Dentist", "2026-05-06", "11:00", "11:45", location="Smile Clinic"),
    _event(11, "Planning", "2026-03-11", "15:00", "16:00", ("team@example.test",)),
]}

MAILBOX = {"messages": [
    _mail("msg_1", "dana@example.test", "Invoice INV-2207",
          "Hi, could you confirm whether invoice INV-2207 (purchase order PO-88412, 1,240.00 EUR) has been paid? "
          "Thanks, Dana", "2026-03-03"),
    _mail("msg_2", "news@weekly.example", "This week in logistics", "Top stories...", "2026-03-04"),
    _mail("msg_3", "leo@example.test", "Design review",
          "Let's do the design review on Thursday 2026-03-12 at 15:30, one hour. Leo", "2026-03-05"),
    _mail("msg_4", "news@weekly.example", "This week in logistics (2)", "More stories...", "2026-03-06"),
    _mail("msg_5", "mara@example.test", "Topics for Monday",
          "Topics for Monday's meeting: hiring plan, Q2 targets, office move. Mara", "2026-03-06"),
    _mail("msg_6", "dana.k@example.test", "Lunch?", "Lunch on Friday? Dana K.", "2026-03-06"),
    _mail("msg_7", "news@deals.example", "Deals for you", "Discounts...", "2026-03-07"),
]}

TRACKER = {"issues": [
    _issue(3, "Login page typo", state="closed", labels=("wontfix",)),
    _issue(7, "Crash when saving a draft", labels=("bug",), assignee="ana"),
    _issue(12, "CSV export drops the last row", labels=("bug",)),
    _issue(14, "Dark mode for settings", labels=("feature",)),
    _issue(15, "App crashes on draft save", labels=("bug",)),
    _issue(18, "Slow search on large projects", labels=("bug", "performance")),
    _issue(21, "PDF export ignores page size", labels=("bug",), assignee="leo"),
]}


def _count(where: str, count: int, **match: Any) -> dict[str, Any]:
    return {"in": where, "where": match, "count": count}


def _call(tool: str, **args: Any) -> dict[str, Any]:
    return {"tool": tool, "args": args}


def _task(number: int, prompt: str, apps: dict[str, Any], tools: tuple[str, ...], checks: list[dict[str, Any]],
          reference: tuple[dict[str, Any], ...], decoy: tuple[dict[str, Any], ...], *,
          verifier: Verifier | None = None, answer: str = "done", wrong_answer: str = "done") -> PackTask:
    return PackTask(
        id=f"tool_{number:02d}", pack="tool_workflows", split="visible",
        prompt=prompt + ("" if verifier is not None else " When you are finished, reply with: done"),
        verifier=verifier or Verifier("app_state", checks),
        reference=Solution(answer=answer, calls=reference), decoy=Solution(answer=wrong_answer, calls=decoy),
        apps=apps, tools=tools, max_steps=10,
    )


_CAL = {"calendar": CALENDAR}
_MAIL = {"mail": MAILBOX}
_ISSUES = {"issues": TRACKER}
EVENTS = "calendar.events"
MESSAGES = "mail.messages"
ISSUES = "issues.issues"

TASKS: tuple[PackTask, ...] = (
    _task(1, "Move my Dentist appointment on 2026-03-04 so it starts at 10:00, keeping its length.", _CAL,
          ("calendar",),
          [_count(EVENTS, 1, title="Dentist", date="2026-03-04", start="10:00", end="10:45"),
           _count(EVENTS, 1, title="Dentist", date="2026-05-06", start="11:00", end="11:45"),
           _count(EVENTS, 11)],
          (_call("calendar_update_event", event_id="evt_2", start="10:00", end="10:45"),),
          (_call("calendar_update_event", event_id="evt_10", date="2026-03-04", start="10:00", end="10:45"),)),
    _task(2, "Cancel my 1:1 with Priya on 2026-03-06. Leave the other one alone.", _CAL, ("calendar",),
          [_count(EVENTS, 0, title="1:1 with Priya", date="2026-03-06"),
           _count(EVENTS, 1, title="1:1 with Priya", date="2026-03-13"), _count(EVENTS, 10)],
          (_call("calendar_delete_event", event_id="evt_3"),),
          (_call("calendar_delete_event", event_id="evt_5"),)),
    _task(3, "Add a 30-minute event called Budget review on 2026-03-10 at 14:00 with dana@example.test.", _CAL,
          ("calendar",),
          [_count(EVENTS, 1, title="Budget review", date="2026-03-10", start="14:00", end="14:30",
                  attendees=["dana@example.test"]), _count(EVENTS, 12)],
          (_call("calendar_create_event", title="Budget review", date="2026-03-10", start="14:00", end="14:30",
                 attendees=["dana@example.test"]),),
          (_call("calendar_create_event", title="Budget review", date="2026-03-10", start="14:00", end="15:00",
                 attendees=["dana@example.test"]),)),
    _task(4, "Reply to Dana's email about invoice INV-2207: tell her it was paid on 2026-03-02. Use the subject "
             "Re: Invoice INV-2207.", _MAIL, ("mail",),
          [_count(MESSAGES, 1, folder="sent", to="dana@example.test", subject="Re: Invoice INV-2207",
                  body={"contains": "2026-03-02"}), _count(MESSAGES, 1, folder="sent")],
          (_call("mail_send", to="dana@example.test", subject="Re: Invoice INV-2207",
                 body="Hi Dana, invoice INV-2207 was paid on 2026-03-02."),),
          (_call("mail_send", to="dana.k@example.test", subject="Re: Invoice INV-2207",
                 body="Hi Dana, invoice INV-2207 was paid on 2026-03-02."),)),
    _task(5, "Archive every newsletter from news@weekly.example in my inbox. Leave everything else where it is.",
          _MAIL, ("mail",),
          [_count(MESSAGES, 0, folder="inbox", **{"from": "news@weekly.example"}),
           _count(MESSAGES, 2, folder="archive", **{"from": "news@weekly.example"}),
           _count(MESSAGES, 5, folder="inbox")],
          (_call("mail_archive", message_id="msg_2"), _call("mail_archive", message_id="msg_4")),
          (_call("mail_archive", message_id="msg_2"), _call("mail_archive", message_id="msg_4"),
           _call("mail_archive", message_id="msg_7"))),
    _task(6, "Leo emailed me a time for the design review. Put it in my calendar as Design review, one hour, "
             "with leo@example.test.", {**_CAL, **_MAIL}, ("calendar", "mail"),
          [_count(EVENTS, 1, title="Design review", date="2026-03-12", start="15:30", end="16:30"),
           _count(EVENTS, 12)],
          (_call("calendar_create_event", title="Design review", date="2026-03-12", start="15:30", end="16:30",
                 attendees=["leo@example.test"]),),
          (_call("calendar_create_event", title="Design review", date="2026-03-12", start="15:00", end="16:00",
                 attendees=["leo@example.test"]),)),
    _task(7, "Close issue 12 and add the label fixed to it.", _ISSUES, ("issues",),
          [_count(ISSUES, 1, id=12, state="closed", labels=["bug", "fixed"]), _count(ISSUES, 2, state="closed")],
          (_call("issues_update", issue_id="12", state="closed", add_labels=["fixed"]),),
          (_call("issues_update", issue_id="12", state="closed", add_labels=["fixed"], remove_labels=["bug"]),)),
    _task(8, "Assign every open issue labelled bug that has no assignee to sam. Don't change issues that already "
             "have someone.", _ISSUES, ("issues",),
          [_count(ISSUES, 3, assignee="sam"), _count(ISSUES, 1, id=7, assignee="ana"),
           _count(ISSUES, 1, id=21, assignee="leo"), _count(ISSUES, 1, id=14, assignee="")],
          (_call("issues_update", issue_id="12", assignee="sam"), _call("issues_update", issue_id="15", assignee="sam"),
           _call("issues_update", issue_id="18", assignee="sam")),
          (_call("issues_update", issue_id="12", assignee="sam"), _call("issues_update", issue_id="15", assignee="sam"),
           _call("issues_update", issue_id="18", assignee="sam"), _call("issues_update", issue_id="21", assignee="sam"))),
    _task(9, "Issue 15 is the same problem as issue 7. Comment 'Duplicate of #7' on issue 15, then close it with "
             "the label duplicate.", _ISSUES, ("issues",),
          [_count(ISSUES, 1, id=15, state="closed", labels=["bug", "duplicate"],
                  comments=["Duplicate of #7"]), _count(ISSUES, 1, id=7, state="open", comments=[])],
          (_call("issues_comment", issue_id="15", body="Duplicate of #7"),
           _call("issues_update", issue_id="15", state="closed", add_labels=["duplicate"])),
          (_call("issues_comment", issue_id="7", body="Duplicate of #15"),
           _call("issues_update", issue_id="7", state="closed", add_labels=["duplicate"]))),
    _task(10, "I'm off on 2026-03-20. Delete all my events that day except the Payroll run.", _CAL, ("calendar",),
          [_count(EVENTS, 1, date="2026-03-20"), _count(EVENTS, 1, title="Payroll run"), _count(EVENTS, 9)],
          (_call("calendar_delete_event", event_id="evt_8"), _call("calendar_delete_event", event_id="evt_9")),
          (_call("calendar_delete_event", event_id="evt_8"),)),
    _task(11, "Mara emailed the topics for Monday's meeting. Send them to team@example.test with the subject "
              "Agenda 2026-03-09, listing all three topics in the body.", _MAIL, ("mail",),
          [_count(MESSAGES, 1, folder="sent", to="team@example.test", subject="Agenda 2026-03-09"),
           _count(MESSAGES, 1, folder="sent", body={"contains": "hiring plan"}),
           _count(MESSAGES, 1, folder="sent", body={"contains": "q2 targets"}),
           _count(MESSAGES, 1, folder="sent", body={"contains": "office move"})],
          (_call("mail_send", to="team@example.test", subject="Agenda 2026-03-09",
                 body="Agenda for Monday:\n- Hiring plan\n- Q2 targets\n- Office move"),),
          (_call("mail_send", to="team@example.test", subject="Agenda 2026-03-09",
                 body="Agenda for Monday:\n- Hiring plan\n- Q2 targets"),)),
    _task(12, "Find the open issue about CSV export, add the label p1 to it and assign it to ana.", _ISSUES,
          ("issues",),
          [_count(ISSUES, 1, id=12, labels=["bug", "p1"], assignee="ana"), _count(ISSUES, 1, labels=["bug", "p1"]),
           _count(ISSUES, 1, id=21, labels=["bug"], assignee="leo")],
          (_call("issues_update", issue_id="12", add_labels=["p1"], assignee="ana"),),
          (_call("issues_update", issue_id="21", add_labels=["p1"], assignee="ana"),)),
    _task(13, "The Team lunch on 2026-03-13 moved to Mezze House. Update its location.", _CAL, ("calendar",),
          [_count(EVENTS, 1, title="Team lunch", location="Mezze House", start="12:00"), _count(EVENTS, 11)],
          (_call("calendar_update_event", event_id="evt_6", location="Mezze House"),),
          (_call("calendar_update_event", event_id="evt_6", title="Mezze House"),)),
    _task(14, "Reopen issue 3 and remove its wontfix label.", _ISSUES, ("issues",),
          [_count(ISSUES, 1, id=3, state="open", labels=[]), _count(ISSUES, 7)],
          (_call("issues_update", issue_id="3", state="open", remove_labels=["wontfix"]),),
          (_call("issues_update", issue_id="3", state="open"),)),
    _task(15, "Tell finance@example.test which purchase order Dana's invoice INV-2207 belongs to: send an email "
              "with the subject INV-2207 purchase order and the PO number in the body.", _MAIL, ("mail",),
          [_count(MESSAGES, 1, folder="sent", to="finance@example.test", body={"contains": "PO-88412"})],
          (_call("mail_send", to="finance@example.test", subject="INV-2207 purchase order",
                 body="Invoice INV-2207 belongs to purchase order PO-88412."),),
          (_call("mail_send", to="finance@example.test", subject="INV-2207 purchase order",
                 body="Invoice INV-2207 is for 1,240.00 EUR."),)),
    _task(16, "How many events do I have on 2026-03-11? Reply with only the number.", _CAL, ("calendar",), [],
          (_call("calendar_list_events", date="2026-03-11"),), (_call("calendar_list_events", date="2026-03-11"),),
          verifier=Verifier("answer", 2, match="number"), answer="2", wrong_answer="1"),
)
