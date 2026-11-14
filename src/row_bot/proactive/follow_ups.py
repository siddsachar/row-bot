"""Follow-ups: mail that got no reply, mail waiting on the person, promises in chats, and repeated asks.

- **Mail** (Gmail, Outlook): a thread the person wrote last, ``follow_up_days`` or more ago (up to a week
  more), with no reply; and an important inbox thread someone else wrote last that the person hasn't
  answered. One moment per thread, ever.
- **Promises** in the person's own chat messages: "I'll send it Friday", "remind me to call Ana tomorrow".
  Questions are never promises ("Should I book it for 8?" is not a booking), and a promise needs a when.
- **Repeated asks**: the same ask in three different chats becomes one "make it a workflow?" offer. Once
  offered (taken or declined) it is never offered again.

Chats are read from the first pass on: what was already there when Proactive started is not news.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from row_bot.proactive import sources as proactive_sources
from row_bot.proactive.model import Candidate, Evidence
from row_bot.proactive.watchers import Watcher

FOLLOW_UP_WINDOW_DAYS = 7
EMITTED_KEPT = 1000
ASK_DAYS = 30
ASKS_KEPT = 300
ASK_SIMILARITY = 0.6
ASK_MIN_WORDS = 3
ASKS_FOR_OFFER = 3  # In this many different chats.

_DAYS = "(?:mon|tues|wednes|thurs|fri|satur|sun)day"
_WHEN = (rf"(?P<when>today|tonight|tomorrow|this (?:morning|afternoon|evening|week)|next week|"
         rf"(?:on |by |this |next )?{_DAYS}|by (?:the )?end of (?:the )?(?:day|week)|by eod|by eow)")
_PROMISE = re.compile(
    r"\bI(?:'|’| wi)ll\s+(?P<what>(?:send|share|email|mail|call|ring|text|finish|get back to|follow up|reply|"
    r"review|check|look into|pay|book|submit|write|draft|prepare|update|fix|sort out|chase)\b[^.!?\n]{0,80}?)"
    rf"\s*\b{_WHEN}\b", re.IGNORECASE)
_REMIND = re.compile(rf"\bremind me (?:to|about)\s+(?P<what>[^.!?\n]{{3,80}}?)(?:\s+\b{_WHEN}\b|[.!\n]|$)",
                     re.IGNORECASE)
_QUESTION_START = re.compile(r"^\s*(?:can|could|would|should|shall|will|do|does|did|is|are|am|was|were|what|when|"
                             r"where|why|how|which|who|any idea|is it|maybe)\b", re.IGNORECASE)
_SENTENCE = re.compile(r"[^.!?\n]+[.!?]?")
_WORD = re.compile(r"[a-z][a-z'’-]{2,}")
_STOP = frozenset("""the and for you your with that this from have has had are was were will would could should
can into about there their them they then than what when where which while who whom why how all any each
our out over some such only also just like more most much very please thanks thank can't don't i'm i've
it's let's me my mine it its get got make made want need know think today tomorrow again""".split())


def find_promise(text: str, now: float) -> tuple[str, float] | None:
    """(what was promised, when it is due) for the first promise in *text* that isn't a question."""
    for sentence in _SENTENCE.findall(text or ""):
        if "?" in sentence or _QUESTION_START.match(sentence):
            continue
        found = _PROMISE.search(sentence) or _REMIND.search(sentence)
        if not found:
            continue
        what = " ".join(found.group("what").split()).rstrip(",;: ")
        if found.re is _PROMISE:
            what = re.sub(r"^(\w+)", lambda verb: verb.group(1).lower(), what)
        return what, due_at(found.group("when"), now)
    return None


def due_at(when: str | None, now: float) -> float:
    """When a promise is due: that morning (09:00) for a day, that afternoon for today."""
    today = datetime.fromtimestamp(now).replace(minute=0, second=0, microsecond=0)
    words = (when or "").lower()
    if not words or words == "tomorrow":
        return (today.replace(hour=9) + timedelta(days=1)).timestamp()
    if words in ("today", "this afternoon", "by eod") or "end of the day" in words or "end of day" in words:
        return max(now, today.replace(hour=16).timestamp())
    if words in ("tonight", "this evening"):
        return max(now, today.replace(hour=18).timestamp())
    if words == "this morning":
        return now
    if words == "next week":
        return (today.replace(hour=9) + timedelta(days=7 - today.weekday())).timestamp()
    if words in ("this week", "by eow") or "end of the week" in words or "end of week" in words:
        friday = today.replace(hour=9) + timedelta(days=(4 - today.weekday()) % 7)
        return max(now, friday.timestamp())
    names = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    day = next(index for index, name in enumerate(names) if name in words)
    ahead = (day - today.weekday()) % 7 + (7 if words.startswith("next ") else 0)
    return max(now, (today.replace(hour=9) + timedelta(days=ahead)).timestamp())


def ask_words(text: str) -> frozenset[str]:
    return frozenset(word.strip("'’-") for word in _WORD.findall((text or "").lower()) if word not in _STOP)


def _similar(first: frozenset[str] | list[str], second: frozenset[str] | list[str]) -> bool:
    a, b = set(first), set(second)
    return bool(a and b) and len(a & b) / len(a | b) >= ASK_SIMILARITY


def _when_words(due: float, now: float) -> str:
    moment, today = datetime.fromtimestamp(due), datetime.fromtimestamp(now).date()
    if moment.date() == today:
        return "today"
    if moment.date() == today + timedelta(days=1):
        return "tomorrow"
    return moment.strftime("%A")


class MailWatcher(Watcher):
    name = "mail"
    source = "mail"
    every_seconds = 30 * 60

    def __init__(self, mail: list[Any] | None = None) -> None:
        self.mail = proactive_sources.mail_sources() if mail is None else mail

    def _ready(self) -> list[Any]:
        return [source for source in self.mail if source.available()]

    def available(self) -> bool:
        return bool(self._ready())

    def scan(self, cursor: dict[str, Any], now: float) -> tuple[list[Candidate], dict[str, Any]]:
        from row_bot.proactive import settings as proactive_settings

        days = proactive_settings.load()["follow_up_days"]
        before = now - days * 86400
        since = before - FOLLOW_UP_WINDOW_DAYS * 86400
        emitted: list[str] = list(cursor.get("emitted") or [])
        found: list[Candidate] = []
        for source in self._ready():
            for thread in source.unanswered_sent(since, before):
                key = f"follow_up:{source.name}:{thread.thread_id}"
                if key in emitted:
                    continue
                emitted.append(key)
                waited = int((now - thread.at) // 86400)
                found.append(Candidate(
                    kind="follow_up", key=key, title=f"No reply from {thread.other} about “{thread.subject}”",
                    summary=f"The person wrote to {thread.other} {waited} days ago and has had no reply.",
                    evidence=(Evidence("thread", thread.thread_id, thread.subject, source.name,
                                       seen=thread.last_message_id),),
                    topic=thread.other, untrusted=f"Subject: {thread.subject}\nTheir own last message: {thread.snippet}",
                    expires_at=now + 3 * 86400, actions=({"id": "open_draft", "label": "Open draft"},)))
            for thread in source.waiting_on_me(since, before):
                key = f"waiting:{source.name}:{thread.thread_id}"
                if key in emitted:
                    continue
                emitted.append(key)
                waited = int((now - thread.at) // 86400)
                found.append(Candidate(
                    kind="follow_up", key=key, title=f"{thread.other} is waiting on your reply about “{thread.subject}”",
                    summary=f"{thread.other} wrote {waited} days ago; the person hasn't replied.",
                    evidence=(Evidence("thread", thread.thread_id, thread.subject, source.name,
                                       seen=thread.last_message_id),),
                    topic=thread.other, untrusted=f"Subject: {thread.subject}\nTheir last message: {thread.snippet}",
                    expires_at=now + 3 * 86400, actions=({"id": "open_draft", "label": "Open draft"},)))
        return found, {"emitted": emitted[-EMITTED_KEPT:]}

    def recheck(self, moment: dict[str, Any], now: float) -> bool | None:
        evidence = next((item for item in moment["evidence"] if item["type"] == "thread"), None)
        source = next((item for item in self.mail if evidence and item.name == evidence["source"]), None)
        if evidence is None or source is None or not source.available():
            return None
        return source.still_last(evidence["id"], evidence["seen"])


class ChatsWatcher(Watcher):
    name = "chats"
    source = "chats"
    every_seconds = 10 * 60

    def __init__(self, chats: Any | None = None) -> None:
        self.chats = proactive_sources.ChatSource() if chats is None else chats

    def scan(self, cursor: dict[str, Any], now: float) -> tuple[list[Candidate], dict[str, Any]]:
        first = "since" not in cursor
        counts: dict[str, int] = dict(cursor.get("counts") or {})
        asks: list[dict[str, Any]] = [ask for ask in cursor.get("asks") or [] if now - ask["at"] < ASK_DAYS * 86400]
        offered: list[list[str]] = list(cursor.get("offered") or [])
        found: list[Candidate] = []
        since = float(cursor.get("since") or now - ASK_DAYS * 86400)
        for conversation_id, title in self.chats.conversations(since):
            lines = self.chats.lines(conversation_id, title)
            seen = counts.get(conversation_id, len(lines) if first else 0)
            new = lines[seen:] if seen <= len(lines) else []  # Shorter (compacted): nothing is new.
            counts[conversation_id] = len(lines)
            for line in new:
                found.extend(self._promise(line, now))
                found.extend(self._ask(line, asks, offered, now))
        kept = dict(list(counts.items())[-500:])
        return found, {"since": now, "counts": kept, "asks": asks[-ASKS_KEPT:], "offered": offered[-200:]}

    @staticmethod
    def _promise(line: Any, now: float) -> list[Candidate]:
        promise = find_promise(line.text, now)
        if promise is None:
            return []
        what, due = promise
        return [Candidate(
            kind="follow_up", key=f"promise:{line.conversation_id}:{line.id}", title=f"You said you'd {what}",
            summary=f"In the chat “{line.conversation_title}”, the person said they'd {what}, "
                    f"due {_when_words(due, now)}.",
            evidence=(Evidence("conversation", line.conversation_id, line.conversation_title, "chats", seen=line.id),),
            topic=what, due_at=due, expires_at=due + 86400,
            actions=({"id": "continue_in_chat", "label": "Carry on in that chat"},))]

    @staticmethod
    def _ask(line: Any, asks: list[dict[str, Any]], offered: list[list[str]], now: float) -> list[Candidate]:
        words = ask_words(line.text)
        if len(words) < ASK_MIN_WORDS:
            return []
        alike = [ask for ask in asks if _similar(ask["words"], words)]
        asks.append({"words": sorted(words), "conversation": line.conversation_id, "at": now})
        chats = {ask["conversation"] for ask in alike} | {line.conversation_id}
        if len(chats) < ASKS_FOR_OFFER or any(_similar(previous, words) for previous in offered):
            return []
        offered.append(sorted(words))
        return [Candidate(
            kind="workflow_offer", key=f"offer:{'-'.join(sorted(words))[:200]}", title="Make this a workflow?",
            summary=f"The person has asked for much the same thing in {len(chats)} different chats: "
                    f"“{line.text[:160]}”. A workflow could do it on a schedule.",
            evidence=tuple(Evidence("ask", conversation, "", "chats") for conversation in sorted(chats)),
            topic=" ".join(sorted(words))[:120], expires_at=now + 7 * 86400,
            actions=({"id": "continue_in_chat", "label": "Set it up"},))]

    def recheck(self, moment: dict[str, Any], now: float) -> bool | None:
        if moment["kind"] != "follow_up":
            return True  # An offer doesn't go stale.
        evidence = moment["evidence"][0]
        return self.chats.still_there(evidence["id"], evidence["seen"])
