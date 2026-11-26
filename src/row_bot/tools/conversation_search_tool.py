"""Conversation Search tool — search past conversations by keyword.

Exposes two sub-tools:
  • search_conversations — keyword search across all thread messages
  • list_conversations  — list all saved threads with names and dates

An incognito conversation (N11) is seen only from itself; one that was
incognito is seen from elsewhere only after incognito was turned off.
Hits from a chat other people may have written in (any channel chat, a
delegated Agent's chat, or one that can't be told) come inside the
``<EXTERNAL_CONTENT>`` fence, scanned for injection.
"""

from __future__ import annotations

import logging
import math
import re
from datetime import datetime
from functools import lru_cache

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from row_bot.external_content import KNOWN_CATEGORIES, fence, wrap
from row_bot.tools.base import BaseTool
from row_bot.tools import registry

logger = logging.getLogger(__name__)


# ── Pydantic schemas ─────────────────────────────────────────────────────────

class _SearchConversationsInput(BaseModel):
    query: str = Field(
        description=(
            "Keywords or phrase to search for in past conversations. "
            "All words must appear in a message, or in a message and the one next to it, for it to match."
        )
    )
    max_results: int = Field(
        default=15,
        description="Maximum number of matching messages to return (default 15).",
    )


class _ListConversationsInput(BaseModel):
    pass  # no arguments needed


# ── Helpers ──────────────────────────────────────────────────────────────────

# About 4 characters a token: a message is shown whole up to ~1,500 tokens, and a search's
# matches together take about a tenth of the model's window, at most ~6,000 tokens (far below
# the 100K tool-result cap). A longer message is cut around where its keywords are.
_MESSAGE_CHARS = 6_000  # one message, cut markers included
_RESULTS_CEILING = 24_000  # every match shown, headers and fences included
_MIN_MATCH_CHARS = 1_000  # a match with less room left than this is not shown
_MARKER_ROOM = 80  # the two cut markers of one message
_SEPARATOR = "\n\n---\n\n"
_FENCE_SOURCE = "search_conversations"
_FENCE_ROOM = len(fence(_FENCE_SOURCE, "", tuple(KNOWN_CATEGORIES)))  # the fence and its longest warning
_FENCE_TAG = re.compile(r"</?\s*EXTERNAL_CONTENT\b[^>\n]*>?", re.IGNORECASE)  # as developer/project_notes.py
_MAX_THREADS = 200  # safety cap on how many threads to scan
_LIST_CHARS = 20_000  # list_conversations beyond this is read on with read_tool_output
# When no message holds every keyword, the messages holding at least half of them come back as the closest,
# most keywords first, then rarer ones (a model often asks with several alternative words at once).
_CLOSEST_MIN_SHARE = 0.5


def _get_thread_messages(thread_id: str, *, own: bool = False) -> list[dict]:
    """Load human + assistant messages from a thread's checkpoint.

    Uses the SqliteSaver checkpointer directly — no dependency on the
    agent graph or app.py. Another conversation (not *own*) gives only what
    may be read outside it: nothing while it is incognito, and only what was
    said after incognito was turned off.
    """
    from row_bot.threads import checkpointer, incognito_state
    from row_bot.turn_context import is_turn_context_message

    config = {"configurable": {"thread_id": thread_id}}
    try:
        checkpoint_tuple = checkpointer.get_tuple(config)
        if not checkpoint_tuple or not checkpoint_tuple.checkpoint:
            return []

        channel_values = checkpoint_tuple.checkpoint.get("channel_values", {})
        messages = channel_values.get("messages", [])
        if not own:
            messages = messages[incognito_state(thread_id).readable_from(messages):]

        result: list[dict] = []
        for m in messages:
            if is_turn_context_message(m):
                continue  # Row-Bot's per-turn context, not a message of the conversation
            mtype = getattr(m, "type", None)
            content = getattr(m, "content", None)
            if not content or not isinstance(content, str):
                continue
            if mtype == "human":
                result.append({"role": "user", "content": content})
            elif mtype == "ai":
                result.append({"role": "assistant", "content": content})
        return result
    except Exception:
        logger.debug("Failed to load thread messages for %s", thread_id, exc_info=True)
        return []


_CONTRACTIONS = frozenset({"it's", "let's", "that's", "what's", "there's", "here's", "he's", "she's", "who's",
                           "where's", "how's"})
_WHOLE_WORDS = frozenset({"news", "series", "species", "evening"})  # look inflected, aren't
_VOWELS = frozenset("aeiouy")


def _keyword(word: str) -> str:
    """A query word as it is matched: lower case, and a name's possessive 's dropped ("Melanie's").

    Other punctuation stays: a quoted "comments" still asks for the quotes, as a model often means it to."""
    word = word.lower()
    plain = word.replace("\u2019", "'")
    if plain.endswith("'s") and plain[:-2].isalpha() and len(plain) > 3 and plain not in _CONTRACTIONS:
        return plain[:-2]
    return word


def _bases(word: str) -> set[str]:
    """The plain forms *word* may be an inflection of: hikes -> hike, classes -> class, movies -> movie and
    movy, hiking -> hik and hike, planned -> plann, plan and plane. A word that isn't inflected is its own."""
    if word in _WHOLE_WORDS:
        return {word}
    if word.endswith("ies") and len(word) >= 5:
        return {word[:-3] + "y", word[:-3] + "ie"}
    if word.endswith("es") and len(word) >= 5 and word[:-2].endswith(("s", "x", "z", "ch", "sh")):
        return {word[:-2]}
    if word.endswith("s") and len(word) >= 4 and not word.endswith(("ss", "us", "is")):
        return {word[:-1]}
    for suffix, shortest in (("ing", 6), ("ed", 5)):
        stem = word[: -len(suffix)]
        if word.endswith(suffix) and len(word) >= shortest and _VOWELS & set(stem) and not word.endswith("eed"):
            found = {stem, stem + "e"}
            if len(stem) >= 4 and stem[-1] == stem[-2]:
                found.add(stem[:-1])
            return found
    return {word}


def _inflections(base: str) -> set[str]:
    forms = {base, base + "s", base + "es", base + "ed", base + "ing"}
    if base.endswith("e"):
        forms |= {base + "d", base[:-1] + "ing"}
    if base.endswith("y") and len(base) >= 3 and base[-2] not in _VOWELS:
        forms |= {base[:-1] + "ies", base[:-1] + "ied"}
    if len(base) <= 4 and base[-1] not in "aeiouwxy" and base[-2:-1] in tuple("aeiou"):
        forms |= {base + base[-1] + "ed", base + base[-1] + "ing"}  # plan: planned, planning
    return forms


@lru_cache(maxsize=512)
def _pattern(keyword: str) -> re.Pattern[str]:
    """*keyword* anywhere, as before, or one of its other word forms as a whole word: "hikes" finds hiking,
    "stories" story, "movies" movie, while "notes" never finds "not" nor "string" "strong". Only a plain
    word has other forms: one with punctuation or digits is matched as typed."""
    derived = sorted({form for base in _bases(keyword) for form in _inflections(base)} - {keyword},
                     key=len, reverse=True) if keyword.isalpha() else []
    if not derived:
        return re.compile(re.escape(keyword))
    return re.compile(re.escape(keyword) + r"|\b(?:" + "|".join(map(re.escape, derived)) + r")\b")


def _has(lower: str, keyword: str) -> bool:
    return _pattern(keyword).search(lower) is not None


def _match_span(lower: str, keywords: list[str]) -> tuple[int, int]:
    """The shortest stretch of *lower* that holds each of the *keywords* it contains."""
    hits = sorted((m.start(), m.end(), kw) for kw in set(keywords) for m in _pattern(kw).finditer(lower))
    wanted = len({kw for _pos, _end, kw in hits})
    best: tuple[int, int] | None = None
    counts: dict[str, int] = {}
    left = 0
    for pos, end, kw in hits:
        counts[kw] = counts.get(kw, 0) + 1
        while len(counts) == wanted:
            first, _first_end, first_kw = hits[left]
            if best is None or end - first < best[1] - best[0]:
                best = (first, end)
            counts[first_kw] -= 1
            if not counts[first_kw]:
                del counts[first_kw]
            left += 1
    return best or (0, 0)


def _excerpt(text: str, keywords: list[str], limit: int) -> str:
    """*text* whole if it fits in *limit* characters, else the part around its keywords, marked as cut."""
    if len(text) <= limit:
        return text
    window = max(1, limit - _MARKER_ROOM)
    start, end = _match_span(text.lower(), keywords)
    begin = max(0, min((start + end - window) // 2, len(text) - window))
    stop = begin + window
    before = f"[… {begin:,} characters before]\n" if begin else ""
    after = f"\n[{len(text) - stop:,} characters after …]" if stop < len(text) else ""
    return before + text[begin:stop] + after


def _with_reply(messages: list[dict], last: int, matched: set[int] = frozenset()) -> dict:
    """The message after index *last*, which often holds what was asked for ("Any favourite recipes?" is
    answered by the next message), as a match's ``after``; none when that message is a match of its own."""
    nxt = last + 1
    return {"after": messages[nxt]} if nxt < len(messages) and nxt not in matched else {}


def _matches_in(messages: list[dict], keywords: list[str]) -> tuple[list[dict], list[dict]]:
    """A thread's messages holding every keyword, then neighbouring pairs that hold them between them,
    each with the message after it."""
    lowered = [msg["content"].lower() for msg in messages]
    alone = [all(_has(text, kw) for kw in keywords) for text in lowered]
    starts: list[int] = []
    i = 0
    while i < len(messages) - 1:
        if not (alone[i] or alone[i + 1]) and all(_has(lowered[i], kw) or _has(lowered[i + 1], kw)
                                                  for kw in keywords):
            starts.append(i)
            i += 2
        else:
            i += 1
    matched = {i for i, hit in enumerate(alone) if hit} | {j for i in starts for j in (i, i + 1)}
    singles = [{"messages": [msg], **_with_reply(messages, i, matched)}
               for i, (msg, hit) in enumerate(zip(messages, alone)) if hit]
    pairs = [{"messages": messages[i:i + 2], **_with_reply(messages, i + 1, matched)} for i in starts]
    return singles, pairs


def _closest_in(scanned: list[tuple[dict, list[dict]]], keywords: list[str], limit: int) -> list[dict]:
    """Single messages holding the most of *keywords* (at least ``_CLOSEST_MIN_SHARE`` of them), for a search
    that no message or neighbouring pair matches whole. Ties go to the rarer keywords, then the newer chat."""
    distinct = list(dict.fromkeys(keywords))
    if len(distinct) < 2:
        return []
    need = max(1, math.ceil(len(distinct) * _CLOSEST_MIN_SHARE))
    lowered = [(seen, messages, i, msg["content"].lower()) for seen, messages in scanned
               for i, msg in enumerate(messages)]
    holding = {kw: sum(1 for *_rest, text in lowered if _has(text, kw)) for kw in distinct}
    found = []
    for order, (seen, messages, i, text) in enumerate(lowered):
        held = [kw for kw in distinct if _has(text, kw)]
        if len(held) >= need:
            found.append((order, seen, messages, i, held))
    hits = {(id(messages), i) for _order, _seen, messages, i, _held in found}
    ranked = []
    for order, seen, messages, i, held in found:
        rarity = sum(math.log((1 + len(lowered)) / (1 + holding[kw])) for kw in held)
        matched = {j for j in (i + 1,) if (id(messages), j) in hits}
        ranked.append((-len(held), -rarity, order,
                       {**seen, "messages": [messages[i]], **_with_reply(messages, i, matched), "held": held}))
    ranked.sort(key=lambda item: item[:3])
    return [item[3] for item in ranked[:limit]]


def _render(number: int, match: dict, keywords: list[str], room: int, message_cap: int) -> str:
    """One match in at most *room* characters, each message in at most *message_cap*.

    A match from a chat others may have written in comes fenced, its own fence tags removed first.
    """
    fenced = match["fenced"]
    if fenced:
        room -= _FENCE_ROOM
    after = [match["after"]] if match.get("after") else []
    msgs = [{**msg, "content": _FENCE_TAG.sub("[fence tag removed]", msg["content"])} if fenced else msg
            for msg in [*match["messages"], *after]]
    matched = len(match["messages"])
    roles = " + ".join(msg["role"].title() for msg in msgs[:matched])
    held = f" | Has: {', '.join(match['held'])}" if match.get("held") else ""
    head = f"[Match {number}] Thread: \"{match['thread']}\" | Date: {match['date']} | {roles}{held}\n"
    if len(msgs) == 1:
        text = head + _excerpt(msgs[0]["content"], keywords, min(message_cap, room - len(head)))
    else:
        labels = [f"{msg['role'].title()}: " for msg in msgs[:matched]]
        labels += [f"Next, {msg['role'].title()}: " for msg in msgs[matched:]]
        limit = min(message_cap, (room - len(head) - sum(map(len, labels)) - 2 * (len(msgs) - 1)) // len(msgs))
        text = head + "\n\n".join(
            label + _excerpt(msg["content"], [kw for kw in keywords if _has(msg["content"].lower(), kw)], limit)
            for label, msg in zip(labels, msgs)
        )
    return wrap(_FENCE_SOURCE, text) if fenced else text


def _others_may_have_written(thread_id: str) -> bool:
    """Whether a chat's messages may be other people's.

    Any channel chat is (a shared one, a group, or a direct chat whose sender Row-Bot can't
    always tell apart, as on Slack or Discord), and so is a delegated Agent's chat or one whose
    origin can't be read. The person's own chats in Row-Bot are not.
    """
    try:
        from row_bot.agent_runs import get_agent_run_for_thread
        from row_bot.tasks import get_thread_channel_ref

        return get_thread_channel_ref(thread_id) is not None or get_agent_run_for_thread(thread_id) is not None
    except Exception:
        logger.debug("Could not read where conversation %s comes from", thread_id, exc_info=True)
        return True


def _results_cap() -> int:
    """All matches' characters: a tenth of the active model's window (~4 characters a token),
    at most ``_RESULTS_CEILING``, which is also the cap when the window isn't known."""
    try:
        from row_bot.models import get_context_policy

        window = int(get_context_policy(allow_probe=False).effective_limit_tokens or 0)
    except Exception:
        logger.debug("Could not read the model's context window", exc_info=True)
        window = 0
    return min(_RESULTS_CEILING, window * 4 // 10) if window > 0 else _RESULTS_CEILING


# ── Tool functions ───────────────────────────────────────────────────────────

def _visible_threads() -> tuple[list, str]:
    """The conversations this one may look through: every one but those incognito, except itself."""
    from row_bot.threads import _list_threads, incognito_thread_ids
    from row_bot.tool_outputs import current_thread_id

    own = current_thread_id()
    hidden = incognito_thread_ids() - {own}
    return [row for row in _list_threads() if row[0] not in hidden], own


def _search_conversations(query: str, max_results: int = 15) -> str:
    """Search all past conversations for messages containing *query* keywords.

    A message holding every keyword ranks before two neighbouring messages that hold them
    between them; within each, newer conversations come first. A message is shown whole up
    to ``_MESSAGE_CHARS``, and all matches together up to ``_results_cap()``.
    """
    threads, own = _visible_threads()
    if not threads:
        return "No saved conversations found."

    # Split query into individual keywords for AND matching
    keywords = [kw for kw in (_keyword(word) for word in query.split()) if kw]
    if not keywords:
        return "Please provide at least one search keyword."
    max_results = max(1, int(max_results))

    singles: list[dict] = []
    pairs: list[dict] = []
    scanned: list[tuple[dict, list[dict]]] = []
    threads_scanned = 0

    for tid, name, _created_at, updated_at, *_cs_rest in threads[:_MAX_THREADS]:
        messages = _get_thread_messages(tid, own=bool(own) and tid == own)
        threads_scanned += 1
        found_alone, found_paired = _matches_in(messages, keywords)
        if found_alone or found_paired:
            seen = {"thread": name, "date": (updated_at or "")[:10], "fenced": _others_may_have_written(tid),
                    "tid": tid}
            singles += [{**seen, **match} for match in found_alone]
            pairs += [{**seen, **match} for match in found_paired]
        elif not singles and not pairs and messages:
            scanned.append(({"thread": name, "date": (updated_at or "")[:10], "tid": tid}, messages))
        if len(singles) >= max_results:
            break

    matches = (singles + pairs)[:max_results]
    closest = False
    if not matches:
        matches = _closest_in(scanned, keywords, max_results)
        for match in matches:
            match["fenced"] = _others_may_have_written(match["tid"])
        closest = bool(matches)
    if not matches:
        return (
            f"No messages matching '{query}' found "
            f"across {threads_scanned} conversation(s)."
        )

    cap = _results_cap()
    message_cap = min(_MESSAGE_CHARS, cap)
    parts: list[str] = []
    used = 0
    for match in matches:
        gap = len(_SEPARATOR) if parts else 0
        room = cap - used - gap
        if room < min(_MIN_MATCH_CHARS, cap):
            break
        part = _render(len(parts) + 1, match, keywords, room, message_cap)
        used += gap + len(part)
        parts.append(part)
    # The chats the shown matches quote, kept with the result for Learning (never shown to the model).
    from row_bot.tool_outputs import note_quoted_conversations

    note_quoted_conversations(match["tid"] for match in matches[:len(parts)])

    header = (
        f"No message holds every keyword of '{query}'. The {len(matches)} closest (most keywords first) "
        f"across {threads_scanned} conversation(s):\n\n"
    ) if closest else (
        f"Found {len(matches)} match(es) for '{query}' "
        f"across {threads_scanned} conversation(s):\n\n"
    )
    footer = ""
    if len(parts) < len(matches):
        footer = (
            f"\n\n[{len(matches) - len(parts)} more match(es) not shown: results are limited to "
            f"{cap:,} characters. Add keywords to narrow the search.]"
        )
    return header + _SEPARATOR.join(parts) + footer


def _list_conversations() -> str:
    """List all saved conversations with names and dates."""
    threads, _own = _visible_threads()
    if not threads:
        return "No saved conversations found."

    lines: list[str] = []
    for tid, name, created_at, updated_at, *_cs_rest2 in threads:
        date_str = (updated_at or created_at or "")[:16].replace("T", " ")
        lines.append(f"• \"{name}\" — last used: {date_str}")

    from row_bot.tool_outputs import bounded

    # Thousands of conversations keep the newest and oldest here; the rest stays readable by handle.
    listing = f"{len(threads)} conversation(s):\n" + "\n".join(lines)
    return bounded(listing, tool_name="list_conversations", max_chars=_LIST_CHARS)


# ── Tool class ───────────────────────────────────────────────────────────────

class ConversationSearchTool(BaseTool):

    @property
    def name(self) -> str:
        return "conversation_search"

    @property
    def display_name(self) -> str:
        return "🔍 Conversation Search"

    @property
    def description(self) -> str:
        return (
            "Search past conversations by keyword to find what was "
            "discussed previously, or list all saved conversations."
        )

    @property
    def enabled_by_default(self) -> bool:
        return True

    @property
    def required_api_keys(self) -> dict[str, str]:
        return {}

    def as_langchain_tools(self) -> list:
        return [
            StructuredTool.from_function(
                func=_search_conversations,
                name="search_conversations",
                description=(
                    "Search past conversations by keyword. Use when the user "
                    "references something discussed previously, e.g. 'What did "
                    "I ask about taxes?', 'When did we talk about Python?', "
                    "'Find where I mentioned that recipe'. All keywords must "
                    "appear in a message, or in a message and the one next to it."
                ),
                args_schema=_SearchConversationsInput,
            ),
            StructuredTool.from_function(
                func=_list_conversations,
                name="list_conversations",
                description=(
                    "List all saved conversations with names and dates. Use "
                    "when the user asks 'What conversations do I have?', "
                    "'Show me my chat history', or similar."
                ),
                args_schema=_ListConversationsInput,
            ),
        ]

    def execute(self, query: str) -> str:
        return _search_conversations(query)


registry.register(ConversationSearchTool())
