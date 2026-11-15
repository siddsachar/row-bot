"""What the person does with a moment, and the rules it writes.

- **Act**: they used it. **Dismiss**: they closed it. **Not now**: it comes back at a later break.
- **Less like this** asks which way and writes a visible, editable rule: never about this person or topic
  (``never_about``), fewer of this kind (``fewer``: at most one a day), or not at this time of day
  (``not_at_time``).
- A moment shown and left alone until it runs out is **ignored**. A kind ignored
  ``QUIET_AFTER_IGNORES`` times in a row goes quiet, with a note in the Activity log; acting on or
  dismissing one ends the streak, and turning the kind back on in Settings lets it speak again.

Rules apply before triage (``blocked``) and at delivery (``timing``). Counts are acceptance, never
engagement: nothing here is sent anywhere.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from row_bot.proactive import store
from row_bot.proactive.model import KIND_WORDS, KINDS, OPEN_STATES, Candidate
from row_bot.proactive.settings import QUIET_AFTER_IGNORES

NOT_NOW_SECONDS = 3 * 3600
IGNORED_AFTER_SECONDS = 12 * 3600  # Shown this long ago with nothing done: ignored.
RULE_TYPES = ("never_about", "fewer", "not_at_time")
LESS_SCOPES = ("topic", "kind", "time")


class FeedbackError(ValueError):
    """A step that can't be taken, said in words (with a code for the API)."""

    def __init__(self, code: str, words: str) -> None:
        super().__init__(words)
        self.code = code


def rule_words(rule: dict[str, Any]) -> str:
    """A rule as the person reads it in Settings."""
    kind = KIND_WORDS.get(rule.get("kind") or "", "anything").lower()
    if rule["type"] == "never_about":
        return f"Never about “{rule['topic']}”" + (f" ({kind})" if rule.get("kind") else "")
    if rule["type"] == "fewer":
        return f"At most one {kind} a day"
    return f"No {kind} around {int(rule['hour']):02d}:00"


def check_rule(rule: dict[str, Any]) -> dict[str, Any]:
    """A rule as the person or the client wrote it, checked."""
    kind_type = str(rule.get("type") or "")
    if kind_type not in RULE_TYPES:
        raise FeedbackError("invalid_command", f"A rule is one of: {', '.join(RULE_TYPES)}.")
    kind = str(rule.get("kind") or "")
    if kind and kind not in KINDS:
        raise FeedbackError("invalid_command", "Unknown kind.")
    topic = " ".join(str(rule.get("topic") or "").split())[:120]
    try:
        hour = int(rule.get("hour", -1))
    except (TypeError, ValueError):
        hour = -2
    if kind_type == "never_about" and not topic:
        raise FeedbackError("invalid_command", "Say what it should never be about.")
    if kind_type in ("fewer", "not_at_time") and not kind:
        raise FeedbackError("invalid_command", "Say which kind.")
    if kind_type == "not_at_time" and not 0 <= hour <= 23:
        raise FeedbackError("invalid_command", "Say which hour, 0 to 23.")
    return {"id": rule.get("id") or "", "type": kind_type, "kind": kind,
            "topic": topic if kind_type == "never_about" else "",
            "hour": hour if kind_type == "not_at_time" else -1}


def blocked(candidate: Candidate, rules: list[dict[str, Any]], *, today_start: float) -> str | None:
    """Why the person's rules keep this candidate out, before triage sees it; None when they don't."""
    text = " ".join((candidate.topic, candidate.title, candidate.summary)).casefold()
    for rule in rules:
        if rule["kind"] and rule["kind"] != candidate.kind:
            continue
        if rule["type"] == "never_about" and rule["topic"].casefold() in text:
            return rule_words(rule)
        if rule["type"] == "fewer" and store.created_since(today_start, kind=candidate.kind):
            return rule_words(rule)
    return None


def _open(moment_id: str) -> dict[str, Any]:
    moment = store.get_moment(moment_id)
    if moment is None:
        raise FeedbackError("not_found", "That moment is gone.")
    if moment["state"] not in OPEN_STATES:
        raise FeedbackError("revision_conflict", "That moment is already settled.")
    return moment


def act(moment_id: str, *, now: float, action_id: str = "") -> dict[str, Any]:
    moment = _open(moment_id)
    store.move(moment_id, "acted", from_states=OPEN_STATES, now=now)
    store.count(moment["kind"], "acted", now=now)
    store.log("moment", f"You acted on “{moment['title']}”" + (f" ({action_id})" if action_id else "") + ".",
              now=now, moment_id=moment_id)
    return store.get_moment(moment_id) or moment


def dismiss(moment_id: str, *, now: float) -> dict[str, Any]:
    moment = _open(moment_id)
    store.move(moment_id, "dismissed", from_states=OPEN_STATES, now=now)
    store.count(moment["kind"], "dismissed", now=now)
    store.log("moment", f"You dismissed “{moment['title']}”.", now=now, moment_id=moment_id)
    if moment["kind"] == "workflow_offer":  # No to "make it a workflow" is for good.
        from row_bot.proactive import offers

        offers.decline(moment, now=now)
    return store.get_moment(moment_id) or moment


def not_now(moment_id: str, *, now: float) -> dict[str, Any]:
    """Back to waiting, for a break at least three hours from now (it still runs out when it runs out)."""
    moment = _open(moment_id)
    store.move(moment_id, "pending", from_states=OPEN_STATES, now=now, surface="", shown_at=0.0,
               due_at=now + NOT_NOW_SECONDS)
    store.log("moment", f"Not now: “{moment['title']}” waits for a later break.", now=now, moment_id=moment_id)
    return store.get_moment(moment_id) or moment


def less_like_this(moment_id: str, scope: str, *, now: float) -> tuple[dict[str, Any], dict[str, Any]]:
    """Dismiss the moment and write the rule the person chose; (moment, rule)."""
    if scope not in LESS_SCOPES:
        raise FeedbackError("invalid_command", f"Less like this by one of: {', '.join(LESS_SCOPES)}.")
    moment = _open(moment_id)
    if scope == "topic":
        if not moment["topic"]:
            raise FeedbackError("invalid_command", "This one isn't about a person or topic; choose the kind or the time.")
        rule = {"type": "never_about", "topic": moment["topic"], "kind": ""}
    elif scope == "kind":
        rule = {"type": "fewer", "kind": moment["kind"]}
    else:
        rule = {"type": "not_at_time", "kind": moment["kind"], "hour": datetime.fromtimestamp(now).hour}
    saved = store.save_rule(check_rule(rule), now=now)
    dismissed = dismiss(moment_id, now=now)
    store.log("rule", f"New rule from “{moment['title']}”: {rule_words(saved)}.", now=now, moment_id=moment_id,
              detail={"rule_id": saved["id"]})
    return dismissed, saved


def sweep(now: float) -> None:
    """Moments that ran out: never shown is expired; shown and left alone is ignored (and counted)."""
    for moment in store.list_moments(OPEN_STATES, limit=500):
        if moment["state"] == "pending" and now >= moment["expires_at"]:
            if store.move(moment["id"], "expired", from_states=("pending",), now=now):
                store.log("moment", f"“{moment['title']}” ran out before a good moment came.", now=now,
                          moment_id=moment["id"])
        elif moment["state"] == "shown" and (now >= moment["expires_at"]
                                             or now - moment["shown_at"] >= IGNORED_AFTER_SECONDS):
            if moment["surface"] == "brief":  # The brief carried it: the brief itself is what was shown.
                store.move(moment["id"], "expired", from_states=("shown",), now=now)
            elif store.move(moment["id"], "ignored", from_states=("shown",), now=now):
                _ignored(moment, now)


def _ignored(moment: dict[str, Any], now: float) -> None:
    stats = store.count(moment["kind"], "ignored", now=now)
    store.log("moment", f"“{moment['title']}” was left alone.", now=now, moment_id=moment["id"])
    if stats["ignored_streak"] >= QUIET_AFTER_IGNORES and not stats["quieted_at"]:
        store.set_quieted(moment["kind"], now)
        store.log("quiet", f"{KIND_WORDS[moment['kind']]} went quiet: the last {QUIET_AFTER_IGNORES} were left "
                  "alone. Turn them back on in Settings › Proactive.", now=now, detail={"kind": moment["kind"]})


def weight(kind: str) -> float:
    """How much a kind counts when moments compete for the same break: less the more it is ignored."""
    return 1.0 / (1 + int(store.kind_stats(kind)["ignored_streak"]))
