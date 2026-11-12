"""Where readers put what they find while a scan runs."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from row_bot.memory_import.hygiene import fact_lines, has_broken_characters, looks_secret
from row_bot.memory_import.models import ENTITY_TYPES, ImportedFact, SkippedItem, SourceCounts


PROVIDER_NAMES = {
    "claude": "Claude",
    "chatgpt": "ChatGPT",
    "gemini": "Gemini",
    "copilot": "Microsoft Copilot",
    "perplexity": "Perplexity",
    "grok": "Grok",
    "deepseek": "DeepSeek",
    "mem0": "mem0",
    "letta": "Letta",
    "pasted": "Pasted text",
}

_HEADING_CATEGORIES: tuple[tuple[re.Pattern[str], str, tuple[str, ...]], ...] = tuple(
    (re.compile(pattern, re.IGNORECASE), category, tags) for pattern, category, tags in (
        (r"instruction|how (?:to|you should) (?:respond|answer)|rules|corrections", "preference", ("instruction",)),
        (r"relationship|people|family|friends", "person", ()),
        (r"preference|interest|likes|dislikes|hobbies|tastes", "preference", ()),
        (r"project|top of mind|goals?\b|plans?\b|work in progress", "project", ()),
        (r"event|history|timeline|dated", "event", ()),
        (r"skill|tools|languages|frameworks|tech", "skill", ()),
        (r"place|location|travel", "place", ()),
    )
)


def category_for_heading(heading: str | None) -> tuple[str, tuple[str, ...]]:
    """An entity type (and tags) for a memory section heading; ``fact`` when unsure."""
    if heading:
        for pattern, category, tags in _HEADING_CATEGORIES:
            if pattern.search(heading):
                return category, tags
    return "fact", ()


@dataclass
class _Latest:
    when: float
    text: str


class Sink:
    """Facts (cleaned, deduplicated, secrets dropped), counts, warnings and skips."""

    def __init__(self) -> None:
        self.facts: list[ImportedFact] = []
        self.counts: dict[str, SourceCounts] = {}
        self.warnings: list[str] = []
        self.skipped: list[SkippedItem] = []
        self.secrets_dropped = 0
        self.broken_text = 0
        self.latest: dict[str, _Latest] = {}
        self._seen_facts: set[str] = set()
        self._seen_warnings: set[str] = set()

    def warn(self, message: str) -> None:
        if message not in self._seen_warnings:
            self._seen_warnings.add(message)
            self.warnings.append(message)

    def skip(self, name: str, reason: str) -> None:
        self.skipped.append(SkippedItem(name, reason))

    def count(self, provider: str) -> SourceCounts:
        if provider not in self.counts:
            self.counts[provider] = SourceCounts(PROVIDER_NAMES.get(provider, provider))
        return self.counts[provider]

    def offer_latest(self, key: str, when: float, text: str) -> None:
        """Keep ``text`` for ``key`` if it is the newest seen (custom instructions)."""
        current = self.latest.get(key)
        if text and (current is None or when >= current.when):
            self.latest[key] = _Latest(when, text)

    def add_facts(self, text: str, *, category: str, source: str, source_label: str, ref: str,
                  date: str | None = None, subject_hint: str | None = None,
                  tags: tuple[str, ...] = (), sentences: bool = True) -> int:
        """Add each line (and, with ``sentences``, each sentence) of ``text`` as a fact; the number added."""
        if category not in ENTITY_TYPES:
            category = "fact"
        added = 0
        for line in fact_lines(text, sentences=sentences):
            if looks_secret(line):
                self.secrets_dropped += 1
                continue
            key = line.casefold().rstrip(".!")
            if key in self._seen_facts:
                continue
            self._seen_facts.add(key)
            if has_broken_characters(line):
                self.broken_text += 1
            digest = hashlib.sha256(key.encode("utf-8", "replace")).hexdigest()[:16]
            self.facts.append(ImportedFact(
                text=line, category=category, source=source, source_label=source_label,
                source_ref=f"{ref}:{digest}", subject_hint=subject_hint, date=date, tags=tags,
            ))
            self.count(source).facts += 1
            added += 1
        return added
