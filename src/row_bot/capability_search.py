"""Small deterministic lexical search shared by tool and skill discovery."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
import math
import re
from typing import Iterable, Sequence


_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_ALNUM = re.compile(r"[^A-Za-z0-9]+")
_VOWELS = "aeiou"
_NOT_PLURAL = frozenset({"news"})
# Marks a word's stem, so it never meets a word written that way.
_STEM = "~"


@dataclass(frozen=True, slots=True)
class CapabilityDocument:
    """Provider-neutral searchable metadata for one authorized capability."""

    canonical_id: str
    name: str
    source: str
    description: str = ""
    tags: tuple[str, ...] = ()
    parameter_names: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()


def normalize_tokens(value: str) -> tuple[str, ...]:
    """Split camelCase, snake_case, punctuation, and case into stable tokens."""

    expanded = _CAMEL_BOUNDARY.sub(" ", str(value or ""))
    return tuple(token.lower() for token in _NON_ALNUM.split(expanded) if token)


def _is_vowel(word: str, index: int) -> bool:
    """A vowel, or a "y" after a consonant (the "y" of "typing")."""
    letter = word[index]
    return letter in _VOWELS or (letter == "y" and index > 0 and word[index - 1] not in _VOWELS)


def _without(word: str, suffix: str, *, undouble: bool = False) -> str | None:
    """*word* without *suffix*, when what stays is a stem (three letters or more, with a vowel); else None."""
    base = word[: -len(suffix)]
    if not word.endswith(suffix) or len(base) < 3 or not any(_is_vowel(base, index) for index in range(len(base))):
        return None
    last = len(base) - 1
    if undouble and len(base) >= 4 and base[-1] == base[-2] and base[-1] not in "lsz" and not _is_vowel(base, last):
        return base[:-1]  # "running" -> "run"
    if len(base) == 3 and not _is_vowel(base, 0) and _is_vowel(base, 1) and not _is_vowel(base, 2) \
            and base[2] not in "wxy":
        return base + "e"  # "noted" -> "note", never "not"
    return base


@lru_cache(maxsize=8192)
def _stem(token: str) -> str:
    """The stem a word shares with its other forms: a light rule-based stemmer (plural, -ing, -ed, -er, -or,
    -ion after t or s, a final -e), so "calculator", "calculates", "calculating" and "calculation" all give
    "calculat". Short words, numbers and ids stay as they are."""

    if len(token) <= 3 or not token.isalpha() or token in _NOT_PLURAL:
        return token
    word = token
    if word.endswith("ies"):
        word = word[:-3] + "y"
    elif word.endswith(("sses", "xes", "ches", "shes")):
        word = word[:-2]
    elif word.endswith("s") and not word.endswith(("ss", "us", "is")):
        word = word[:-1]
    if word.endswith("ied") and len(word) > 4:
        word = word[:-3] + "y"
    else:
        word = _without(word, "ing", undouble=True) or _without(word, "ed", undouble=True) or word
    if word.endswith(("tion", "sion")):
        word = _without(word, "ion") or word
    else:
        word = _without(word, "er") or _without(word, "or") or word
    if len(word) >= 5 and word.endswith("e"):
        word = word[:-1]
    return word


def _with_word_forms(tokens: tuple[str, ...]) -> tuple[str, ...]:
    """Each token and its stem: the word as written scores twice, another form of it once."""
    return (*tokens, *(_STEM + _stem(token) for token in tokens))


def _document_tokens(document: CapabilityDocument) -> tuple[str, ...]:
    fields: Iterable[str] = (
        document.canonical_id,
        document.name,
        document.source,
        document.description,
        *document.tags,
        *document.parameter_names,
        *document.aliases,
    )
    return _with_word_forms(tuple(token for field in fields for token in normalize_tokens(field)))


def _name_match_tier(document: CapabilityDocument, query: str) -> int:
    query_folded = query.casefold()
    names = (document.name, document.canonical_id, *document.aliases)
    folded = tuple(str(name or "").casefold() for name in names if str(name or "").strip())
    if query_folded in folded:
        return 0

    primary_names = (document.canonical_id.casefold(), document.name.casefold())
    if any(name.startswith(query_folded) for name in primary_names):
        return 1
    if any(query_folded in name for name in primary_names):
        return 2
    return 3


def search_capabilities(
    documents: Sequence[CapabilityDocument],
    query: str,
    *,
    limit: int = 3,
) -> list[CapabilityDocument]:
    """Rank documents locally using explicit name tiers then BM25-style text score over words and their
    stems, so another form of a word ("calculator" for "calculate") still matches."""

    query_text = str(query or "").strip()
    if not query_text:
        return []
    bounded_limit = min(5, max(1, int(limit or 1)))
    query_tokens = _with_word_forms(normalize_tokens(query_text))
    if not query_tokens:
        return []

    tokenized = [_document_tokens(document) for document in documents]
    document_count = len(tokenized)
    if not document_count:
        return []
    average_length = sum(len(tokens) for tokens in tokenized) / document_count or 1.0
    document_frequency = Counter(
        token
        for tokens in tokenized
        for token in set(tokens)
    )

    ranked: list[tuple[tuple[object, ...], CapabilityDocument]] = []
    for document, tokens in zip(documents, tokenized, strict=True):
        tier = _name_match_tier(document, query_text)
        frequencies = Counter(tokens)
        score = 0.0
        for token in query_tokens:
            frequency = frequencies.get(token, 0)
            if not frequency:
                continue
            inverse_frequency = math.log(
                1.0 + (document_count - document_frequency[token] + 0.5)
                / (document_frequency[token] + 0.5)
            )
            denominator = frequency + 1.2 * (
                0.25 + 0.75 * (len(tokens) / average_length)
            )
            score += inverse_frequency * (frequency * 2.2) / denominator

        if tier == 3 and score <= 0:
            continue
        rank_key = (
            tier,
            -score if tier == 3 else 0.0,
            document.canonical_id.casefold(),
            document.canonical_id,
        )
        ranked.append((rank_key, document))

    ranked.sort(key=lambda item: item[0])
    return [document for _rank, document in ranked[:bounded_limit]]
