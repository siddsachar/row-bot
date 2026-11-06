"""The retrieval arm (§4): the untrained model with the 4 most similar steps of the 1,000-row subset in its prompt.

**Similarity** is the audit vault's own novelty measure (``audit._novel``): word overlap on the request text, each
word weighted by how rare it is among the known texts (``log((n + 1) / (f + 1))``), shared weight over the union's.
The vault's tokenizer (``audit._words``, its stop words included) is read from the module itself: the seal guards the
vault's content, and the tokenizer holds none. :func:`scores` computes the measure ``_novel`` thresholds; a test
holds the two to the same verdicts. The known texts are the subset's rows, one text each (the request a row's turn
answered); ties go to the lower row id.

**A step** is a pool row (one turn of the teacher's work: its tool calls and its answer). Each example says what was
asked and what the teacher did, cut to :data:`EXAMPLE_CHARS` so all four fit the prompt section they go in.

**Into the prompt** (no product change): in the real runtime as Learning's own ways-of-working overlay, one entry
each in the ``guidance`` slot (the seam the product's evaluation worker uses to test a way of working, read once per
turn as the last stable system section, at most 2,400 characters); in replay as the arm's ``guidance`` (the same
change as the in-process runners carry it).

A row of a held-out request is never in the index: building one refuses.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

#: Each example's length, so four and the section's own heading fit Learning's 2,400-character section.
EXAMPLE_CHARS = 500
_ASKED_CHARS = 160
_VALUE_CHARS = 160


class RetrievalError(RuntimeError):
    """An index the pre-registration doesn't allow (a held-out row in it)."""


def _vault_words() -> Any:
    from row_bot.learning import audit

    return vars(audit)["_words"]  # the vault's tokenizer and stop words: no vault content


def scores(text: str, known: Sequence[str]) -> list[float]:
    """The novelty measure's overlap of ``text`` with each known text (``audit._novel``'s, unthresholded)."""

    words_of = _vault_words()
    words = words_of(text)
    sets = [words_of(item) for item in known]
    frequency: dict[str, int] = {}
    for item in sets:
        for word in item:
            frequency[word] = frequency.get(word, 0) + 1
    count = len(sets)

    def weight(word: str) -> float:
        return math.log((count + 1) / (frequency.get(word, 0) + 1)) + 1e-3

    found = []
    for other in sets:
        union = sum(weight(word) for word in words | other)
        found.append(sum(weight(word) for word in words & other) / union if union else 0.0)
    return found


@dataclass(frozen=True)
class Entry:
    id: str
    person: str
    request: str
    text: str  # the request the row's turn answered
    example: str  # what was asked and what the teacher did


def _value(text: Any, limit: int = _VALUE_CHARS) -> str:
    value = " ".join(str(text if isinstance(text, str) else json.dumps(text, ensure_ascii=False)).split())
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _step(message: Mapping[str, Any]) -> str:
    calls = []
    for call in message.get("tool_calls") or ():
        function = call.get("function") or {}
        name, arguments = str(function.get("name") or ""), function.get("arguments") or "{}"
        if name == "tool_invoke":  # the bridge carries a call: show the call it makes
            try:
                inner = json.loads(arguments) if isinstance(arguments, str) else dict(arguments)
                name, arguments = str(inner.get("name") or name), inner.get("arguments") or {}
            except (ValueError, TypeError, AttributeError):
                pass
        calls.append(f"{name}({_value(arguments)})")
    if calls:
        return "called " + ", ".join(calls)
    return f"answered \"{_value(message.get('content') or '')}\""


def example(text: str, row: Mapping[str, Any]) -> str:
    """One step as an example: the request and the turn's own moves (its targets), at most EXAMPLE_CHARS."""

    steps = [_step(message) for message in row.get("messages") or ()
             if message.get("role") == "assistant" and message.get("weight")]
    found = f"When asked \"{_value(text, _ASKED_CHARS)}\", Row-Bot " + "; then ".join(steps) + "."
    return found if len(found) <= EXAMPLE_CHARS else found[: EXAMPLE_CHARS - 1] + "…"


class Index:
    """The subset's rows, searchable by request text."""

    def __init__(self, entries: Iterable[Entry]) -> None:
        self.entries = sorted(entries, key=lambda item: item.id)

    @classmethod
    def build(cls, rows: Iterable[Mapping[str, Any]], *, held_out: Iterable[str]) -> Index:
        """From pool rows (``id``, ``person``, ``request``, ``text``, ``row``); refuses a held-out request's row."""

        held = set(held_out)
        entries = []
        for row in rows:
            if row["request"] in held:
                raise RetrievalError(f"row {row['id']} answers held-out request {row['request']}: never retrieved")
            entries.append(Entry(str(row["id"]), str(row["person"]), str(row["request"]), str(row["text"]),
                                 example(str(row["text"]), row["row"])))
        return cls(entries)

    def pick(self, text: str, k: int) -> list[Entry]:
        """The ``k`` most similar entries, most similar first (ties: the lower id)."""

        found = scores(text, [entry.text for entry in self.entries])
        ranked = sorted(range(len(self.entries)), key=lambda index: (-found[index], self.entries[index].id))
        return [self.entries[index] for index in ranked[:k]]

    def to_lines(self) -> list[str]:
        return [json.dumps(entry.__dict__, ensure_ascii=False, sort_keys=True) for entry in self.entries]

    @classmethod
    def from_lines(cls, lines: Iterable[str]) -> Index:
        return cls(Entry(**json.loads(line)) for line in lines if line.strip())


def ways(picks: Sequence[Entry]) -> list[tuple[str, str]]:
    """The examples as ways-of-working entries (the real runtime's prompt section)."""

    return [("guidance", f"Example {number}: {entry.example}") for number, entry in enumerate(picks, 1)]


def guidance(picks: Sequence[Entry]) -> tuple[str, ...]:
    """The same examples as an arm's guidance (replay)."""

    return tuple(f"Example {number}: {entry.example}" for number, entry in enumerate(picks, 1))


__all__ = ["EXAMPLE_CHARS", "Entry", "Index", "RetrievalError", "example", "guidance", "scores", "ways"]
