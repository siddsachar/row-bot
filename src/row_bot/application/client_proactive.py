"""The client's side of Proactive Row-Bot: For you, "Why this?", feedback, the Activity log and settings.

- ``for_you``: the open moments, urgent first: what Home's For you and Buddy show.
- ``read_moment``: one moment with its evidence, why it got through and its history ("Why this?").
- ``moment_feedback``: act / dismiss / not now / less like this. Acting with ``continue_in_chat``
  carries on in the chat the moment came from, as a Row-Bot continuation note; acting on a "make it a
  workflow" offer with ``make_workflow`` drafts the workflow (off, unscheduled) and names it for the client
  to open for review.
- ``list_drafts`` / ``read_draft``: what Row-Bot prepared (briefs, drafted replies), never sent.
- ``read_activity``: every proactive pass, silent ones too, and each moment's steps, newest first.
- ``read_settings`` / ``update_settings``: the level, quiet hours, sources, kinds, desktop context, urgent
  kinds and the person's rules, each with its plain privacy line.
- ``save_rule`` / ``delete_rule``: the person's rules, visible and editable.
- ``note_boundary``: the client says the app just opened (a natural break).

Times are local ISO 8601 with their offset. Evidence ids are the sources' own ids (an event, a message);
nothing here carries a mail's body or a token.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from row_bot.application.client_platform import ClientPlatformError

MAX_MOMENTS = 200
MAX_ACTIVITY = 200
# Said once, plainly, wherever Proactive's settings are shown (decision D3).
PRIVACY_LINE = ("Proactive Row-Bot reads your sources on this computer. When something changes, a short summary "
                "of the change goes to your main chat model, which decides whether it's worth a mention. "
                "Preparing a brief or a draft also runs on your main model, and only reads: nothing is sent "
                "without your approval. Nothing about proactivity is sent to Row-Bot.")


def _when(value: float) -> str:
    return datetime.fromtimestamp(value).astimezone().isoformat(timespec="seconds") if value else ""


NOW_WINDOW_SECONDS = 3600  # Within this of its time (or past it), a moment is "Now".
_NOT_NOW_KEPT = 500


def _event_start(moment: dict[str, Any]) -> float | None:
    """The start of the meeting a moment is about, as it was seen (meeting prep, a moved meeting)."""
    for item in moment["evidence"]:
        seen = str(item.get("seen") or "")
        if item.get("type") == "event" and seen.isdigit():
            return float(seen)
    return None


def moment_group(moment: dict[str, Any], now: float, not_now: frozenset[str] = frozenset()) -> str:
    """For you's group, decided here from what the moment is about, never from when it was shown (the morning
    brief marks what it carries as shown, and that mustn't move them): "now" (within the hour, happening or
    urgent), "later_today" (later today) or "ready" (no time of its own, another day, or put off with Not now).
    """
    if moment["kind"] == "brief":
        return "now"
    if moment["id"] in not_now:
        return "ready"
    start = _event_start(moment)
    if moment["urgency"] == "urgent" and (start is None or start - now <= NOW_WINDOW_SECONDS):
        return "now"
    # A moment due when it was made has no time of its own (a follow-up): it's ready when the person is.
    at = start if start is not None else (moment["due_at"] if moment["due_at"] - moment["created_at"] > 60 else None)
    if at is None:
        return "ready"
    if at - now <= NOW_WINDOW_SECONDS:
        return "now"
    tomorrow = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0).timestamp() + 86400
    return "later_today" if at < tomorrow else "ready"


def _not_now() -> frozenset[str]:
    from row_bot.proactive import store

    return frozenset(store.get_state("not_now") or [])


def moment_view(moment: dict[str, Any], *, now: float | None = None,
                not_now: frozenset[str] | None = None) -> dict[str, Any]:
    import time

    now = time.time() if now is None else now
    return {
        "moment_id": moment["id"],
        "kind": moment["kind"],
        "title": moment["title"][:200],
        "body": moment["body"][:4000],
        "urgency": moment["urgency"],
        "state": moment["state"],
        "surface": moment["surface"],
        "created_at": _when(moment["created_at"]),
        "due_at": _when(moment["due_at"]),
        "expires_at": _when(moment["expires_at"]),
        "shown_at": _when(moment["shown_at"]),
        "actions": [{"id": str(action.get("id") or "")[:40], "label": str(action.get("label") or "")[:60]}
                    for action in moment["actions"][:2]],
        "prepared_ref": moment["prepared_ref"][:64],
        "evidence_count": len(moment["evidence"]),
        "group": moment_group(moment, now, _not_now() if not_now is None else not_now),
        "starts_at": _when(_event_start(moment) or 0.0),
    }


def _activity_view(entry: dict[str, Any]) -> dict[str, Any]:
    return {"entry_id": int(entry["id"]), "at": _when(entry["at"]), "kind": entry["kind"],
            "summary": entry["summary"][:500], "moment_id": entry["moment_id"]}


def for_you() -> dict[str, Any]:
    from row_bot.proactive import store
    from row_bot.proactive.model import OPEN_STATES

    items = store.list_moments(OPEN_STATES, limit=MAX_MOMENTS)
    items.sort(key=lambda moment: (moment["urgency"] != "urgent", moment["state"] != "shown",
                                   -moment["created_at"]))
    not_now = _not_now()
    return {"schema_version": 1, "items": [moment_view(moment, not_now=not_now) for moment in items]}


def read_moment(moment_id: str, *, conversation_id: str = "", workflow_id: str = "") -> dict[str, Any]:
    from row_bot.proactive import store

    moment = store.get_moment(moment_id)
    if moment is None:
        raise ClientPlatformError("not_found")
    return {
        "schema_version": 1,
        "moment": moment_view(moment),
        "conversation_id": conversation_id,
        "workflow_id": workflow_id,
        "summary": moment["summary"][:2000],
        "evidence": [{key: str(item.get(key) or "")[:300] for key in ("type", "id", "label", "source")}
                     for item in moment["evidence"][:20]],
        "why": [str(line)[:300] for line in moment["why"][:8]],
        "history": [_activity_view(entry) for entry in store.activity(moment_id=moment_id, limit=50)],
    }


def _carry_on(moment: dict[str, Any]) -> str:
    """The person chose to carry on in the chat a moment came from: Row-Bot starts that turn, shown there as a
    continuation note (never as the person's message). Anything with a side effect in it still asks."""
    from row_bot.application import conversation_followups as followups
    from row_bot.application.client_platform import client_platform_service as service

    conversation = next((item["id"] for item in reversed(moment["evidence"])
                         if item["type"] in ("conversation", "ask")), "")
    if not conversation:
        raise ClientPlatformError("invalid_command")
    service._metadata(conversation)  # Gone: not_found.
    prompt = ("This is Row-Bot, not the person: the person chose to carry on with something Row-Bot noticed.\n"
              f"{moment['title']}. {moment['summary']}\n\nHelp them with it now. Anything that sends, books, buys "
              "or changes something still goes through their approval as usual.")
    receipt = followups.start(service, conversation, followups.Followup(
        kind="proactive", prompt=prompt, note=f"Following up: {moment['title']}"[:160]))
    if receipt is None:
        raise ClientPlatformError("generation_active")
    return conversation


def moment_feedback(moment_id: str, action: str, *, scope: str = "", action_id: str = "") -> dict[str, Any]:
    import time

    from row_bot.proactive import feedback, store

    now = time.time()
    conversation_id = workflow_id = ""
    if action == "act":
        moment = store.get_moment(moment_id)
        open_moment = moment is not None and moment["state"] in ("pending", "shown")
        if action_id == "continue_in_chat" and open_moment:
            conversation_id = _carry_on(moment)
        elif action_id == "make_workflow" and open_moment and moment["kind"] == "workflow_offer":
            from row_bot.proactive import offers

            workflow_id = offers.make_workflow(moment, now=now)
        feedback.act(moment_id, now=now, action_id=action_id)
    elif action == "dismiss":
        feedback.dismiss(moment_id, now=now)
    elif action == "not_now":
        feedback.not_now(moment_id, now=now)
        # It waits under Ready when you are, whatever its new time (For you's group).
        store.set_state("not_now", [*[item for item in store.get_state("not_now") or [] if item != moment_id],
                                    moment_id][-_NOT_NOW_KEPT:])
    elif action == "less_like_this":
        feedback.less_like_this(moment_id, scope, now=now)
    else:
        raise ClientPlatformError("invalid_command")
    return read_moment(moment_id, conversation_id=conversation_id, workflow_id=workflow_id)


def _draft_view(draft: dict[str, Any]) -> dict[str, Any]:
    """A draft as the person last saved it: who it replies to and in which thread, whether it can be sent (and
    why not), and the approval card when sending it waits for their answer."""
    from row_bot.proactive import send, store

    moment = store.get_moment(draft["moment_id"]) if draft["moment_id"] else None
    edit = store.draft_edit(draft["id"]) or {}
    reply = send.reply_to(draft, moment) if draft["kind"] == "reply" else {}
    asking = send.pending(draft["id"]) if draft["kind"] == "reply" else None
    return {
        "draft_id": draft["id"], "moment_id": draft["moment_id"], "kind": draft["kind"],
        "title": draft["title"][:200], "body": send.body_of(draft)[:20_000], "created_at": _when(draft["created_at"]),
        "reply_to": str(reply.get("to") or "")[:320], "subject": str(reply.get("subject") or "")[:300],
        "thread": str(reply.get("thread") or "")[:300], "app": send.APPS.get(str(reply.get("source") or ""), ""),
        "updated_at": _when(float(edit.get("updated_at") or 0)), "sent_at": _when(float(edit.get("sent_at") or 0)),
        "send_blocked": send.why_not(draft, moment)[:200] if draft["kind"] == "reply" else "",
        "send": ({key: str(asking.get(key) or "")[:20_000 if key == "text" else 300]
                  for key in ("binding", "action", "destination", "account", "subject", "text", "reason")}
                 | {"asked_at": _when(float(asking.get("asked_at") or 0))}) if asking else None,
    }


def _reply_draft(draft_id: str) -> dict[str, Any]:
    from row_bot.proactive import store

    draft = store.get_draft(draft_id)
    if draft is None:
        raise ClientPlatformError("not_found")
    if draft["kind"] != "reply":
        raise ClientPlatformError("invalid_command")  # A brief is for reading.
    return draft


def save_draft(draft_id: str, *, body: str, to: str | None = None, subject: str | None = None) -> dict[str, Any]:
    """The person's edits to a drafted reply, saved in Row-Bot (never to the mail account). A card shown for
    other words no longer stands: sending asks again."""
    import time

    from row_bot.proactive import send, store

    draft = _reply_draft(draft_id)
    moment = store.get_moment(draft["moment_id"]) if draft["moment_id"] else None
    if (store.draft_edit(draft_id) or {}).get("sent_at"):
        raise ClientPlatformError("revision_conflict")  # Sent: it can't change any more.
    current = send.reply_to(draft, moment)
    store.save_draft_edit(draft_id, to=(current["to"] if to is None else to).strip()[:320],
                          subject=(current["subject"] if subject is None else subject).strip()[:300],
                          body=body[:20_000], now=time.time())
    send.forget(draft_id)
    return read_draft(draft_id)


def ask_to_send(draft_id: str) -> dict[str, Any]:
    """Send a drafted reply: shows its approval card and sends nothing, under every approval level (Row-Bot
    wrote it, so only the person sends it)."""
    import time

    from row_bot.proactive import send, store

    draft = _reply_draft(draft_id)
    moment = store.get_moment(draft["moment_id"]) if draft["moment_id"] else None
    try:
        send.ask(draft, moment, now=time.time())
    except send.SendError as error:
        raise ClientPlatformError(error.code) from None
    return read_draft(draft_id)


def answer_send(draft_id: str, *, binding: str, approved: bool) -> dict[str, Any]:
    """The person's answer on a drafted reply's card: only their approval of exactly that card sends it."""
    import time

    from row_bot.proactive import send, store

    draft = _reply_draft(draft_id)
    moment = store.get_moment(draft["moment_id"]) if draft["moment_id"] else None
    outcome, words = send.answer(draft, moment, binding=binding, approved=approved, now=time.time())
    return {"schema_version": 1, "outcome": outcome, "words": words[:300], "draft": read_draft(draft_id)}


def list_drafts() -> dict[str, Any]:
    """Drafts: what Row-Bot prepared (meeting briefs, drafted replies), newest first. None was sent."""
    from row_bot.proactive import store

    return {"schema_version": 1, "items": [_draft_view(draft) for draft in store.drafts(limit=100)]}


def read_draft(draft_id: str) -> dict[str, Any]:
    from row_bot.proactive import store

    draft = store.get_draft(draft_id)
    if draft is None:
        raise ClientPlatformError("not_found")
    return {"schema_version": 1, **_draft_view(draft)}


def read_activity(*, before: int = 0, limit: int = 100) -> dict[str, Any]:
    from row_bot.proactive import store

    limit = max(1, min(int(limit), MAX_ACTIVITY))
    entries = store.activity(before=before, limit=limit + 1)
    more = len(entries) > limit
    entries = entries[:limit]
    return {"schema_version": 1, "items": [_activity_view(entry) for entry in entries],
            "next_before": int(entries[-1]["id"]) if more and entries else None}


COUNT_DAYS = 30


def connected_sources() -> dict[str, bool]:
    """Which sources Row-Bot can read now: mail and the calendar need one of the person's apps on and signed
    in; chats, memory, workflows and Monitor are Row-Bot's own."""
    from row_bot.proactive import sources

    def ready(items: list[Any]) -> bool:
        for item in items:
            try:
                if item.available():
                    return True
            except Exception:  # An app whose sign-in can't be read counts as not connected.
                continue
        return False

    return {"mail": ready(sources.mail_sources()), "calendar": ready(sources.calendar_sources()),
            "chats": True, "memory": True, "workflows": True, "monitor": True}


def read_settings() -> dict[str, Any]:
    import time

    from row_bot.proactive import feedback, store
    from row_bot.proactive import settings as proactive
    from row_bot.proactive.model import KIND_WORDS, KINDS

    saved = proactive.load()
    connected = connected_sources()
    counts = store.kind_counts(time.time() - COUNT_DAYS * 86400)
    none = {"shown": 0, "acted": 0, "dismissed": 0, "ignored": 0}
    return {
        "schema_version": 1,
        "level": saved["level"],
        "levels": [{"id": level, "label": proactive.LEVEL_WORDS[level], "example": proactive.LEVEL_EXAMPLES[level]}
                   for level in proactive.LEVELS],
        "quiet_start": saved["quiet_start"],
        "quiet_end": saved["quiet_end"],
        "day_end": saved["day_end"],
        "follow_up_days": saved["follow_up_days"],
        "desktop_context": saved["desktop_context"],
        "desktop_context_privacy": proactive.PRIVACY["desktop_context"],
        "sources": [{"id": source, "label": proactive.SOURCE_WORDS[source], "on": saved["sources"][source],
                     "privacy": proactive.PRIVACY[source], "connected": connected[source]}
                    for source in proactive.SOURCES],
        "kinds": [{"id": kind, "label": KIND_WORDS[kind], "on": saved["kinds"][kind],
                   "urgent": kind in saved["urgent_kinds"],
                   "quieted": bool(store.kind_stats(kind)["quieted_at"]),
                   "last_30_days": counts.get(kind, none)} for kind in KINDS],
        "channel": saved["channel"],
        "wrap": proactive.wrap_on(saved),
        "introduced": saved["introduced"],
        "rules": [{"rule_id": rule["id"], "type": rule["type"], "kind": rule["kind"], "topic": rule["topic"],
                   "hour": rule["hour"], "words": feedback.rule_words(rule)} for rule in store.rules()],
        "privacy": PRIVACY_LINE,
    }


def update_settings(changes: dict[str, Any]) -> dict[str, Any]:
    from row_bot.proactive import settings as proactive

    try:
        proactive.save(changes)
    except proactive.SettingsError:
        raise ClientPlatformError("invalid_command") from None
    return read_settings()


def save_rule(rule: dict[str, Any], *, rule_id: str = "") -> dict[str, Any]:
    import time

    from row_bot.proactive import feedback, store

    if rule_id and store.get_rule(rule_id) is None:
        raise ClientPlatformError("not_found")
    checked = feedback.check_rule({**rule, "id": rule_id})
    existing = store.get_rule(rule_id) if rule_id else None
    store.save_rule({**checked, "created_at": existing["created_at"] if existing else 0}, now=time.time())
    return read_settings()


def read_preview() -> dict[str, Any]:
    """What each level would have brought up today, from today's real moments ("Today at Quiet you'd have
    seen…"); ``example`` when nothing was checked yet today."""
    import time

    from row_bot.proactive import preview

    return {"schema_version": 1, **preview.preview(time.time())}


def delete_rule(rule_id: str) -> dict[str, Any]:
    from row_bot.proactive import store

    if not store.delete_rule(rule_id):
        raise ClientPlatformError("not_found")
    return read_settings()


def note_boundary(kind: str) -> dict[str, Any]:
    from row_bot.proactive import engine

    if kind != "app_open":
        raise ClientPlatformError("invalid_command")
    engine.note_boundary(kind)
    return {"schema_version": 1, "noted": kind}
