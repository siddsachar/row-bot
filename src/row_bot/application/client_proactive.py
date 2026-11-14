"""The client's side of Proactive Row-Bot: For you, "Why this?", feedback, the Activity log and settings.

- ``for_you``: the open moments, urgent first: what Home's For you and Buddy show.
- ``read_moment``: one moment with its evidence, why it got through and its history ("Why this?").
- ``moment_feedback``: act / dismiss / not now / less like this. Acting with ``continue_in_chat``
  carries on in the chat the moment came from, as a Row-Bot continuation note.
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


def moment_view(moment: dict[str, Any]) -> dict[str, Any]:
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
    return {"schema_version": 1, "items": [moment_view(moment) for moment in items]}


def read_moment(moment_id: str, *, conversation_id: str = "") -> dict[str, Any]:
    from row_bot.proactive import store

    moment = store.get_moment(moment_id)
    if moment is None:
        raise ClientPlatformError("not_found")
    return {
        "schema_version": 1,
        "moment": moment_view(moment),
        "conversation_id": conversation_id,
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
    conversation_id = ""
    if action == "act":
        moment = store.get_moment(moment_id)
        if action_id == "continue_in_chat" and moment is not None and moment["state"] in ("pending", "shown"):
            conversation_id = _carry_on(moment)
        feedback.act(moment_id, now=now, action_id=action_id)
    elif action == "dismiss":
        feedback.dismiss(moment_id, now=now)
    elif action == "not_now":
        feedback.not_now(moment_id, now=now)
    elif action == "less_like_this":
        feedback.less_like_this(moment_id, scope, now=now)
    else:
        raise ClientPlatformError("invalid_command")
    return read_moment(moment_id, conversation_id=conversation_id)


def _draft_view(draft: dict[str, Any]) -> dict[str, Any]:
    return {"draft_id": draft["id"], "moment_id": draft["moment_id"], "kind": draft["kind"],
            "title": draft["title"][:200], "body": draft["body"][:20_000], "created_at": _when(draft["created_at"])}


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


def read_settings() -> dict[str, Any]:
    from row_bot.proactive import feedback, store
    from row_bot.proactive import settings as proactive
    from row_bot.proactive.model import KIND_WORDS, KINDS

    saved = proactive.load()
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
                     "privacy": proactive.PRIVACY[source]} for source in proactive.SOURCES],
        "kinds": [{"id": kind, "label": KIND_WORDS[kind], "on": saved["kinds"][kind],
                   "urgent": kind in saved["urgent_kinds"],
                   "quieted": bool(store.kind_stats(kind)["quieted_at"])} for kind in KINDS],
        "channel": saved["channel"],
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
