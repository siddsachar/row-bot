"""Next-message corrections (O12) and the redo that shows a corrected action (O87).

**Next-message corrections.** Capture marks every turn the person started right after a reply with
``Episode.follows_decision_id`` (L0b); whether that message corrected the reply is decided here,
later, by a fixed rule over the message's opening words (no model call). False corrections are
worse than missed ones (a good reply would be left out of training and drafted against), so the
rule is conservative:

- a **correction** opens by saying the reply was wrong ("No, …", "That's not right", "Wrong file",
  "You used the old policy", "I asked for …", "Hmm, not quite: …");
- an opening that thanks, agrees or moves on ("No worries", "No thanks", "Great, now …"), a
  question, or anything else is **not** a correction;
- an opening that may or may not correct ("Actually, …", "Wait, …", "Are you sure?") is
  **ambiguous** and stays out, like a correction the person **withdrew** in the message after it
  ("Never mind, you were right").

Each confirmed one becomes a ``next_message_correction`` Signal on the corrected reply, dated when
Learning confirmed it (so the held-out count, which walks corrections by that date, never skips one,
``holdout``). It is judged only after the correcting turn ended and the conversation had time to say
more (:data:`SETTLE_SECONDS`), and only when the message came within :data:`MAX_GAP_SECONDS` of the
reply. Imported past chats (partial) get them too: they are evidence for diagnosis, never hidden
problems (O89).

**Messages read mid-turn (5.3.1).** A message the person sends while a turn works is read by that turn
at its next step. Capture starts the next episode there, following the step before it, so it is judged
here exactly as it was when it started a turn of its own; one read before the turn's first step joins
the message the turn started with (``opening_texts``), and can still take its correction back.

**Redos.** A corrected decision's *corrected action* is what Row-Bot did next, once the person was
happy: the first model call of the turn that redid it (after a confirmed next-message correction or
the app's Retry), when that turn finished and nothing corrected, stopped or retried it in turn
(:func:`accepted_redo`). A Teach with no redo has no corrected action yet.

This module reads only ``learning.db``; it never imports the audit vault (E10).
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from row_bot import learning as gate
from row_bot.learning.contracts import Decision, Episode, Signal, new_id
from row_bot.learning.store import LearningStore

logger = logging.getLogger(__name__)

NEXT_MESSAGE = "next_message_correction"
# Signals that say a decision's outcome wasn't what the person wanted.
CORRECTING_KINDS = frozenset({"teach", NEXT_MESSAGE})
NOT_ACCEPTED_KINDS = frozenset({"teach", NEXT_MESSAGE, "stop", "retry", "approval_denied"})
# A message is judged once its turn ended this long ago, or once the person said something after it.
SETTLE_SECONDS = 30 * 60.0
# A message long after the reply starts something new: it isn't judged as a correction.
MAX_GAP_SECONDS = 6 * 3600.0
# A running turn this old was cut short (Row-Bot closed): it counts as ended.
STALE_RUNNING_SECONDS = 24 * 3600.0
# How many corrections in a row a redo may follow (a person corrects at most a few times).
MAX_REDO_HOPS = 3
OPENING_WORDS = 12
_HORIZON = "next_message.horizon"

Verdict = Literal["correction", "not", "ambiguous"]

# ── The rule ────────────────────────────────────────────────────────────────

_NEGATIVE_OPENINGS = re.compile(
    r"^(?:no (?:worries|problem|probs|rush|need|thanks|thank you|matter|biggie|pressure)|no,? (?:that'?s|it'?s) "
    r"(?:fine|ok|okay|great|perfect|right|correct)|nothing else|not bad|not now|not yet|nope,? (?:that'?s|it'?s) "
    r"(?:fine|all|it))\b"
)
_RETRACTIONS = re.compile(
    r"\b(?:never ?mind|nvm|ignore (?:that|my last|what i said)|forget (?:it|that|what i said)|my (?:mistake|bad)"
    r"|sorry,? (?:my|i was) (?:mistake|wrong)|you(?:'re| are| were) (?:right|correct)|that(?:'s| is| was) "
    r"(?:right|correct|fine) after all|it was (?:right|correct)|scratch that)\b"
)
_CORRECTION_OPENINGS = re.compile(
    r"^(?:"
    r"(?:no|nope|nah)\b(?! (?:worries|problem|rush|need|thanks|thank you|matter))"
    r"|wrong\b|incorrect\b|not (?:quite|right|correct|exactly|what i|the right|the one|that one)\b"
    r"|still (?:wrong|not right|incorrect|not what i)\b"
    r"|(?:that|this|it|which|those|these)(?:'s| is| was|'re| are| were) (?:not|wrong|incorrect|outdated|"
    r"the wrong|the old|the previous|the draft|an old|a draft|out of date|still wrong|off)\b"
    r"|(?:that|this|it) (?:isn'?t|wasn'?t|aren'?t|doesn'?t|didn'?t|won'?t)\b"
    r"|(?:that|this|it)(?:'s| is) not\b"
    r"|you (?:got|used|picked|read|opened|chose|took|checked|looked at|sent|added|put|moved|archived|closed|"
    r"labell?ed|wrote|missed|forgot|left out|skipped|ignored|didn'?t|did not|misread|mixed up|confused)\b"
    r"|you(?:'ve| have) (?:got|used|picked|read|missed|forgotten|left|mixed)\b"
    r"|i (?:said|asked|meant|wanted|needed|told you)\b"
    r"|(?:hmm+|um+|uh+|erm+|ah+|oh+|hm+)[,.!]* (?:no\b|nope\b|not (?:quite|right|correct|exactly)\b|that(?:'s| is| was) "
    r"(?:not|wrong|incorrect)\b|wrong\b|that isn'?t\b|you (?:got|used|missed|picked|read)\b)"
    r"|(?:sorry|actually|wait)[,.!]* (?:no\b|that(?:'s| is| was) (?:not|wrong|incorrect)\b|wrong\b|not (?:quite|right)\b"
    r"|you (?:got|used|missed|picked|read)\b)"
    r"|(?:should|it should|that should|this should) (?:be|have been|have|say|read)\b"
    r"|(?:try|check|look) again\b|that(?:'s| is) not what i\b|not what i (?:asked|wanted|meant|needed)\b"
    r"|(?:the|that|this) (?:answer|total|number|figure|amount|date|file|document|result) (?:is|was|looks) "
    r"(?:wrong|off|incorrect|not right)\b"
    r")"
)
# Said anywhere in the first two sentences, these name a defect in what was just done.
_DEFECTS = re.compile(
    r"\b(?:(?:is|was|are|were|'s|'re) (?:missing|wrong|incorrect|outdated|out of date|still wrong|not right|"
    r"not correct)"
    r"|(?:wasn'?t|weren'?t|isn'?t|aren'?t|hasn'?t|haven'?t|didn'?t get|did not get) (?:written|saved|created|added|"
    r"done|sent|moved|included|booked|scheduled|updated|changed|fixed|archived|closed|labell?ed|right|correct)"
    r"|not the (?:unapproved|draft|old|older|previous|outdated|superseded|wrong)"
    r"|you (?:missed|forgot|left out|skipped|ignored|misread|mixed up|used the wrong|picked the wrong|read the wrong|"
    r"opened the wrong|got the wrong|gave the wrong)"
    r"|(?:that|it|this|those|these)(?:'s| is|'re| are| was| were) (?:the |a )?wrong)\b"
)
# A sentence that asks or asks for something ("Which entries are missing?", "Find the wrong date") names
# no defect in the reply.
_ASKING = re.compile(r"^(?:find|check|list|show|tell|which|what|who|whose|where|when|why|how|can|could|would|"
                     r"will|please|is|are|was|were|does|do|did|has|have|should|make|create|write|add|look)\b")
_AMBIGUOUS_OPENINGS = re.compile(
    r"^(?:actually|wait|hold on|hang on|are you sure|is that right|is that correct|really|hmm+|um+|hm+|"
    r"double[- ]check|can you (?:check|confirm|double)|did you (?:check|use|read|look))\b"
)
_LEAD = re.compile(r"^[\s\"'“‘(*_>-]+")


def _opening(text: str) -> str:
    """The message's first sentence, lower-case, quotes straightened, at most :data:`OPENING_WORDS` words."""

    value = str(text or "").replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    value = _LEAD.sub("", value.strip().lower())
    first = re.split(r"(?<=[.!?])\s|\n", value, maxsplit=1)[0]
    words = first.split()
    return " ".join(words[:OPENING_WORDS])


def _first_sentences(text: str, count: int = 2) -> list[str]:
    value = str(text or "").replace("’", "'").replace("‘", "'").lower()
    cut = value.find("<row_bot_turn_context")
    value = value[:cut] if cut >= 0 else value
    parts = re.split(r"(?<=[.!?])\s+|\n+", value.strip())
    return [_LEAD.sub("", part)[:300] for part in parts[:count] if part.strip()]


def _names_a_defect(text: str) -> bool:
    return any(_DEFECTS.search(sentence) for sentence in _first_sentences(text)
               if not sentence.rstrip().endswith("?") and not _ASKING.match(sentence))


def judge(text: str) -> Verdict:
    """Whether a message sent right after a reply corrects it. Conservative: see the module's rules."""

    opening = _opening(text)
    if not opening:
        return "not"
    whole = str(text or "").replace("’", "'").lower()
    if _RETRACTIONS.search(whole[:400]):
        return "ambiguous"
    if _NEGATIVE_OPENINGS.match(opening):
        return "not"
    if _CORRECTION_OPENINGS.match(opening):
        return "correction"
    if _names_a_defect(text):
        return "correction"
    if _AMBIGUOUS_OPENINGS.match(opening):
        return "ambiguous"
    return "not"


def withdraws(text: str) -> bool:
    """Whether a message takes back the correction before it ("Never mind, you were right")."""

    return bool(_RETRACTIONS.search(str(text or "").replace("’", "'").lower()[:400]))


# ── Reading the store ───────────────────────────────────────────────────────


_TURN_CONTEXT_KEY = "row_bot_turn_context"
_NOTE_KEY = "row_bot_note"  # Row-Bot's own note to the model (5.3.1's progress nudge), never the person's
_MID_TURN_KEY = "mid_turn"  # 5.3.1: what the person sent while a turn worked, read at its next step
_REPLIES = frozenset({"ai", "AIMessageChunk"})


def _kwargs(message: Any) -> Mapping[str, Any]:
    if isinstance(message, Mapping):
        data = message.get("data") if isinstance(message.get("data"), Mapping) else message
        kwargs = data.get("additional_kwargs")
    else:
        kwargs = getattr(message, "additional_kwargs", None)
    return kwargs if isinstance(kwargs, Mapping) else {}


def is_row_bot_note(message: Any) -> bool:
    """Whether a message (captured, ``message_to_dict``, or as sent) is a note Row-Bot sent the model itself:
    kept in the request exactly as sent, but never the person's words."""

    return bool(_kwargs(message).get(_NOTE_KEY))


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = " ".join(str(part.get("text") or "") for part in content
                        if isinstance(part, Mapping) and part.get("type") == "text")
    else:
        return ""
    cut = text.find("<" + _TURN_CONTEXT_KEY)
    return (text[:cut] if cut >= 0 else text).strip()


def _persons(request: Mapping[str, Any] | None) -> list[tuple[int, str, bool]]:
    """Each message the person sent in a captured request: (where it is, its text, whether they sent it while
    the turn worked). Human messages without Row-Bot's turn context (its own message, or a block it
    appended), its notes or the page's picture after an action."""

    from row_bot.learning.pictures import is_page_picture

    found: list[tuple[int, str, bool]] = []
    for index, item in enumerate((request or {}).get("messages") or ()):
        if (not isinstance(item, Mapping) or item.get("type") != "human" or is_row_bot_note(item)
                or is_page_picture(item)):
            continue
        kwargs = _kwargs(item)
        if kwargs.get(_TURN_CONTEXT_KEY):
            continue
        data = item.get("data") or {}
        text = _content_text(data.get("content") if isinstance(data, Mapping) else None)
        if text:
            found.append((index, text, bool(kwargs.get(_MID_TURN_KEY))))
    return found


def person_texts(request: Mapping[str, Any] | None) -> list[str]:
    """What the person sent, oldest first, from a captured request (``capture.read_request``)."""

    return [text for _index, text, _mid in _persons(request)]


def person_text(request: Mapping[str, Any] | None) -> str:
    """The newest message the person sent, from a captured request."""

    texts = person_texts(request)
    return texts[-1] if texts else ""


def opening_texts(request: Mapping[str, Any] | None) -> list[str]:
    """What the person sent that a captured request's work starts from, in the order they sent it: their
    messages since its last reply (their newest when there are none since).

    A message they sent while the turn worked (5.3.1, read at its next step) comes after the one the turn
    started with. When the request's work starts with such messages (the steps after one), it starts with
    the first of them, as the turn of its own it used to be would have."""

    messages = list((request or {}).get("messages") or ())
    found = _persons(request)
    last = max((index for index, item in enumerate(messages)
                if isinstance(item, Mapping) and item.get("type") in _REPLIES), default=-1)
    since = [(text, mid) for index, text, mid in found if index > last]
    if not since:
        return [found[-1][1]] if found else []
    own = [text for text, mid in since if not mid]
    if own:
        return [own[-1], *(text for text, mid in since if mid)]
    return [text for text, _mid in since]


class Book:
    """One pass's view of the episodes, their model calls and their signals (read once)."""

    def __init__(self, store: LearningStore) -> None:
        self.store = store
        self.episodes: dict[str, Episode] = {}
        self.by_thread: dict[str, list[Episode]] = defaultdict(list)
        self.followers: dict[str, list[Episode]] = defaultdict(list)
        for item in store.find("episode", limit=100_000):
            if isinstance(item, Episode):
                self.episodes[item.id] = item
                self.by_thread[item.thread_id].append(item)
                if item.follows_decision_id:
                    self.followers[item.follows_decision_id].append(item)
        for items in self.by_thread.values():
            items.sort(key=lambda item: (item.started_at, item.id))
        self.decisions: dict[str, Decision] = {}
        self.calls: dict[str, list[Decision]] = defaultdict(list)  # model calls by episode, in order
        self.by_episode: dict[str, list[Decision]] = defaultdict(list)
        for item in store.find("decision", limit=100_000):
            if isinstance(item, Decision):
                self.decisions[item.id] = item
                self.by_episode[item.episode_id].append(item)
                if item.kind == "model_call":
                    self.calls[item.episode_id].append(item)
        for items in (*self.calls.values(), *self.by_episode.values()):
            items.sort(key=lambda item: (item.sequence, item.created_at, item.id))
        self.signals: list[Signal] = [item for item in store.find("signal", limit=100_000)
                                      if isinstance(item, Signal) and item.withdrawn_at is None]
        self.signalled: dict[str, list[Signal]] = defaultdict(list)  # by decision
        self.signalled_episodes: dict[str, list[Signal]] = defaultdict(list)
        for item in self.signals:
            if item.decision_id:
                self.signalled[item.decision_id].append(item)
            if item.episode_id:
                self.signalled_episodes[item.episode_id].append(item)
        self._openings: dict[str, list[str]] = {}

    def opening_of(self, episode: Episode) -> list[str]:
        """What the person sent to start ``episode``, in order (its first model call's ``opening_texts``)."""

        if episode.id in self._openings:
            return self._openings[episode.id]
        calls = self.calls.get(episode.id) or []
        found: list[str] = []
        if calls:
            from row_bot.learning import capture

            found = opening_texts(capture.read_request(self.store, calls[0]))
        self._openings[episode.id] = found
        return found

    def text_of(self, episode: Episode) -> str:
        """What the person wrote to start ``episode`` (the first of ``opening_of``)."""

        found = self.opening_of(episode)
        return found[0] if found else ""

    def next_episode(self, episode: Episode) -> Episode | None:
        """The turn the person started right after ``episode``'s reply, if any."""

        calls = self.calls.get(episode.id) or []
        if not calls:
            return None
        found = [item for item in self.followers.get(calls[-1].id, ()) if item.thread_id == episode.thread_id]
        return min(found, key=lambda item: (item.started_at, item.id)) if found else None

    def ended(self, episode: Episode, now: float) -> bool:
        if episode.status != "running":
            return True
        return now - episode.started_at >= STALE_RUNNING_SECONDS


# ── Confirming next-message corrections ─────────────────────────────────────


@dataclass(frozen=True)
class ConfirmReport:
    """What one pass found (counts only)."""

    considered: int = 0
    confirmed: tuple[str, ...] = ()  # the corrected decisions
    ambiguous: int = 0
    withdrawn: int = 0
    late: int = 0
    waiting: int = 0
    by_verdict: Mapping[str, int] = field(default_factory=dict)


def _existing(book: Book) -> set[str]:
    return {item.decision_id for item in book.signals if item.kind == NEXT_MESSAGE and item.decision_id}


def confirm(store: LearningStore, *, clock: Callable[[], float] | None = None,
            book: Book | None = None) -> ConfirmReport:
    """Turn the confirmed next-message corrections since the last pass into Signals (only while
    Learning is on and not paused; nothing is recorded otherwise). ``clock`` defaults to the store's."""

    if not store.exists() or not gate.is_enabled(store.root):
        return ConfirmReport()
    now = float(clock()) if clock is not None else store.now()
    horizon = float(store.meta_value(_HORIZON, "0") or 0)
    book = book or Book(store)
    known = _existing(book)
    candidates = sorted((item for item in book.episodes.values()
                         if item.follows_decision_id and item.started_at > horizon),
                        key=lambda item: (item.started_at, item.id))
    verdicts: dict[str, int] = defaultdict(int)
    confirmed: list[tuple[Episode, Decision]] = []
    ambiguous = withdrawn = late = waiting = considered = 0
    advanced = horizon
    for episode in candidates:
        after = book.next_episode(episode)
        settled = book.ended(episode, now) and (
            after is not None or now - float(episode.ended_at or episode.started_at) >= SETTLE_SECONDS)
        if not settled:
            waiting = sum(1 for item in candidates if item.started_at >= episode.started_at)
            break  # judged on a later pass; nothing after it is judged before it
        advanced = episode.started_at
        considered += 1
        reply = book.decisions.get(str(episode.follows_decision_id))
        if reply is None or reply.kind != "model_call" or reply.thread_id != episode.thread_id:
            verdicts["no_reply"] += 1
            continue
        if episode.started_at - reply.created_at > MAX_GAP_SECONDS:
            late += 1
            verdicts["late"] += 1
            continue
        verdict = judge(book.text_of(episode))
        verdicts[verdict] += 1
        if verdict == "ambiguous":
            ambiguous += 1
            continue
        if verdict != "correction":
            continue
        # Taken back in the next message, or in one sent with it before Row-Bot's next step (5.3.1).
        later = [*book.opening_of(episode)[1:], *([book.text_of(after)] if after is not None else [])]
        if any(withdraws(text) for text in later):
            withdrawn += 1
            continue
        if reply.id in known:
            continue
        known.add(reply.id)
        confirmed.append((episode, reply))
    # Dated just before now, in the order the messages came (anything made after this pass is newer).
    records = [Signal(id=new_id("sig"), kind=NEXT_MESSAGE, thread_id=reply.thread_id,
                      created_at=now - (len(confirmed) - index) * 1e-6, conversation_id=reply.conversation_id,
                      episode_id=reply.episode_id, decision_id=reply.id)
               for index, (_episode, reply) in enumerate(confirmed)]
    if records:
        store.put_many(records)
        logger.info("Learning found %d correction(s) in the person's next message", len(records))
    if advanced > horizon:
        store.set_meta_values({_HORIZON: repr(advanced)})
    return ConfirmReport(considered, tuple(reply.id for _, reply in confirmed), ambiguous, withdrawn, late,
                         waiting, dict(verdicts))


# ── Redos: what Row-Bot did once the person was happy ───────────────────────


def _corrected_by_next_message(book: Book, reply: Decision) -> Episode | None:
    """The turn whose opening corrected ``reply`` (a confirmed next-message correction)."""

    if not any(item.kind == NEXT_MESSAGE for item in book.signalled.get(reply.id, ())):
        return None
    found = [item for item in book.followers.get(reply.id, ()) if item.thread_id == reply.thread_id]
    return min(found, key=lambda item: (item.started_at, item.id)) if found else None


def _retried(book: Book, episode: Episode) -> Episode | None:
    """The turn the app's Retry ran in place of ``episode``."""

    retries = [item for item in book.signalled_episodes.get(episode.id, ()) if item.kind == "retry"]
    if not retries:
        return None
    after = min(item.created_at for item in retries)
    later = [item for item in book.by_thread.get(episode.thread_id, ()) if item.started_at >= after
             and item.id != episode.id]
    return later[0] if later else None


def _accepted(book: Book, episode: Episode, now: float) -> bool:
    if episode.status != "completed" or episode.incomplete:
        return False
    if any(item.kind in NOT_ACCEPTED_KINDS for item in book.signalled_episodes.get(episode.id, ())):
        return False
    for decision in book.by_episode.get(episode.id, ()):
        if any(item.kind in NOT_ACCEPTED_KINDS for item in book.signalled.get(decision.id, ())):
            return False
        if decision.status != "ok":
            return False
    after = book.next_episode(episode)
    if after is not None:
        return judge(book.text_of(after)) == "not"
    return now - float(episode.ended_at or episode.started_at) >= SETTLE_SECONDS


def _last_call(book: Book, decision: Decision) -> Decision | None:
    calls = book.calls.get(decision.episode_id) or []
    return calls[-1] if calls else None


def accepted_redo(store: LearningStore, decision: Decision, *, clock: Callable[[], float] | None = None,
                  book: Book | None = None) -> Episode | None:
    """The turn that redid ``decision`` and that the person accepted, or None (no redo yet).

    A redo follows a confirmed next-message correction of the decision's reply, or the app's
    Retry of its turn. A redo that was itself corrected leads on to its own redo."""

    book = book or Book(store)
    now = float(clock()) if clock is not None else store.now()
    current = decision
    for _ in range(MAX_REDO_HOPS):
        episode = book.episodes.get(current.episode_id)
        if episode is None:
            return None
        reply = current if current.kind == "model_call" and current is _last_call(book, current) else _last_call(
            book, current)
        redo = (_corrected_by_next_message(book, reply) if reply is not None else None) or _retried(book, episode)
        if redo is None:
            return None
        if _accepted(book, redo, now):
            return redo
        calls = book.calls.get(redo.id) or []
        if not calls:
            return None
        current = calls[-1]
    return None


def first_call(book: Book, episode: Episode) -> Decision | None:
    """The first model call of ``episode`` that finished (its first move)."""

    for item in book.calls.get(episode.id) or ():
        if item.status == "ok":
            return item
    return None


def problem_call(book: Book, decision: Decision) -> Decision | None:
    """The model call a correction is about: the decision itself, or for a step (a tool call) the
    model call that made it."""

    if decision.kind == "model_call":
        return decision
    before = [item for item in book.calls.get(decision.episode_id) or () if item.sequence < decision.sequence]
    return before[-1] if before else None


def corrected_decisions(signals: Iterable[Signal]) -> set[str]:
    """Decisions a confirmed correction (Teach or next message) points at."""

    return {str(item.decision_id) for item in signals
            if item.kind in CORRECTING_KINDS and item.decision_id and item.withdrawn_at is None}


def precision(labels: Sequence[tuple[str, bool]]) -> dict[str, float | int]:
    """The rule against labelled messages ((text, is a correction)): counts, precision and recall."""

    tp = fp = fn = tn = ambiguous = 0
    for text, truth in labels:
        verdict = judge(text)
        if verdict == "ambiguous":
            ambiguous += 1
        if verdict == "correction":
            tp, fp = (tp + 1, fp) if truth else (tp, fp + 1)
        else:
            fn, tn = (fn + 1, tn) if truth else (fn, tn + 1)
    return {"true_positive": tp, "false_positive": fp, "false_negative": fn, "true_negative": tn,
            "ambiguous": ambiguous, "precision": tp / (tp + fp) if tp + fp else 1.0,
            "recall": tp / (tp + fn) if tp + fn else 1.0}


__all__ = [
    "Book",
    "CORRECTING_KINDS",
    "ConfirmReport",
    "MAX_GAP_SECONDS",
    "NEXT_MESSAGE",
    "SETTLE_SECONDS",
    "accepted_redo",
    "confirm",
    "corrected_decisions",
    "first_call",
    "is_row_bot_note",
    "judge",
    "opening_texts",
    "person_text",
    "precision",
    "problem_call",
    "withdraws",
]
