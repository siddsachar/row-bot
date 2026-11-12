"""A pasted answer: the universal prompt's ``category | date | fact`` lines, or a vendor's format.

Also read: Anthropic's ``[date] - memory`` lines, Google's "The user …" entries
with "Evidence … Date: [YYYY-MM-DD]" lines, and plain lists or Markdown.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from row_bot.memory_import.catalogue import PROMPT_CATEGORIES
from row_bot.memory_import.markdown import heading_of
from row_bot.memory_import.sink import Sink, category_for_heading
from row_bot.memory_import.values import iso_date


_FENCE = re.compile(r"```[^\n`]*\n(.*?)(?:```|\Z)", re.DOTALL)
_ANTHROPIC = re.compile(r"^\s*(?:[-*•]\s*)?\[([^\]]{0,40})\]\s*[-–—:]\s*(.+)$")
_EVIDENCE = re.compile(r"^\s*[-–—•*]?\s*Evidence\s*:", re.IGNORECASE)
_EVIDENCE_DATE = re.compile(r"Date\s*:\s*\[?\s*(\d{4}-\d{2}-\d{2})")
_ANY_DATE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_HEADER_ROW = re.compile(r"^\s*\|?\s*category\s*\|\s*date\s*\|\s*fact\s*\|?\s*$", re.IGNORECASE)
_MORE = re.compile(r"\b(?:more (?:remain|memories|entries|to come|items)|not (?:the |a )?complete|incomplete|"
                   r"truncated|remaining|remain\b|cut off)", re.IGNORECASE)
_CLOSING_LINE = re.compile(r"\b(?:complete (?:set|list)|more (?:remain|memories remain|entries remain)|"
                           r"that(?: i|'| wa)s (?:everything|all)|all (?:the |of the )?(?:memories|entries))\b",
                           re.IGNORECASE)
_NO_MORE = re.compile(r"\b(?:no|none|nothing)\b[^.\n]{0,40}\b(?:remain|more|left)|\bis (?:the )?complete\b|"
                      r"\bcomplete set\b(?![^.\n]{0,20}\bnot\b)", re.IGNORECASE)
_GOOGLE_SECTIONS = (
    (re.compile(r"demographic", re.IGNORECASE), "fact", (), "User"),
    (re.compile(r"interests?\s*(?:&|and)\s*preferences", re.IGNORECASE), "preference", (), None),
    (re.compile(r"relationships", re.IGNORECASE), "person", (), None),
    (re.compile(r"dated events|projects?\s*(?:&|and)\s*plans", re.IGNORECASE), "event", (), None),
    (re.compile(r"^instructions$", re.IGNORECASE), "preference", ("instruction",), None),
)
_LOOSE_FORMATS = ("%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y", "%Y/%m/%d", "%B %Y")


@dataclass
class _Pending:
    text: str
    category: str
    date: str | None
    tags: tuple[str, ...] = ()
    subject: str | None = None


def loose_date(text: str) -> str | None:
    """An ISO date from a date as an assistant may write it; ``None`` for "unknown"."""
    text = text.strip().strip("[]()").strip()
    found = _ANY_DATE.search(text)
    if found:
        return iso_date(found.group(1))
    for pattern in _LOOSE_FORMATS:
        try:
            return datetime.strptime(text, pattern).date().isoformat()
        except ValueError:
            continue
    return None


def _section(heading: str) -> tuple[str, tuple[str, ...], str | None]:
    for pattern, category, tags, subject in _GOOGLE_SECTIONS:
        if pattern.search(heading):
            return category, tags, subject
    category, tags = category_for_heading(heading)
    return category, tags, None


def body_and_trailer(text: str) -> tuple[str, str]:
    """The first fenced block (or the whole text) and what follows it."""
    match = _FENCE.search(text)
    if match:
        return match.group(1), text[match.end():]
    lines = text.rstrip().splitlines()
    if lines and "|" not in lines[-1] and _CLOSING_LINE.search(lines[-1]):
        return "\n".join(lines[:-1]), lines[-1]
    return text, ""


def says_more_remain(trailer: str) -> bool:
    return bool(_MORE.search(trailer)) and not _NO_MORE.search(trailer)


def parse(text: str, sink: Sink, *, source: str = "pasted", label: str = "Pasted answer", ref: str = "pasted") -> bool:
    """Add the answer's facts to ``sink``; whether the answer says more remain."""
    body, trailer = body_and_trailer(text)
    pending: list[_Pending] = []
    section: tuple[str, tuple[str, ...], str | None] = ("fact", (), None)
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or _HEADER_ROW.match(stripped):
            continue
        if _EVIDENCE.match(stripped):
            found = _EVIDENCE_DATE.search(stripped)
            if pending and found and pending[-1].date is None:
                pending[-1].date = iso_date(found.group(1))
            continue
        piped = stripped.strip("|").strip() if stripped.startswith("|") else stripped
        if piped.count("|") >= 2:
            category_text, date_text, fact = (part.strip() for part in piped.split("|", 2))
            if set(category_text) <= set("-: "):
                continue  # a Markdown table rule
            name = category_text.lower().strip("*` ")
            category, tags = PROMPT_CATEGORIES.get(name, PROMPT_CATEGORIES["other"])
            pending.append(_Pending(fact.rstrip("|").strip(), category, loose_date(date_text), tags,
                                    "User" if name == "identity" else None))
            continue
        heading = heading_of(stripped)
        if heading is not None:
            section = _section(heading)
            continue
        anthropic = _ANTHROPIC.match(stripped)
        if anthropic:
            category, tags, subject = section
            pending.append(_Pending(anthropic.group(2), category, loose_date(anthropic.group(1)), tags, subject))
            continue
        category, tags, subject = section
        pending.append(_Pending(stripped, category, None, tags, subject))
    for item in pending:
        sink.add_facts(item.text, category=item.category, source=source, source_label=label,
                       ref=f"{ref}:{item.category}", date=item.date, subject_hint=item.subject,
                       tags=item.tags, sentences=False)
    return says_more_remain(trailer)
