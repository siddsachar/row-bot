"""Deterministic weighted search for public skill entries."""

from __future__ import annotations

import math
import re
from collections import Counter
from difflib import SequenceMatcher
from typing import Iterable

from .models import SkillHubEntry

_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def normalize_text(value: str) -> str:
    text = _CAMEL_RE.sub(" ", str(value or ""))
    text = re.sub(r"[-_/\\.]+", " ", text)
    text = re.sub(r"[^A-Za-z0-9\s]+", " ", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def tokenize(value: str) -> list[str]:
    return _TOKEN_RE.findall(normalize_text(value))


def entry_identifier(entry: SkillHubEntry) -> str:
    parts = [
        entry.id,
        entry.source_id,
        entry.install_ref,
        entry.url,
        str(entry.metadata.get("repository") or ""),
        str(entry.metadata.get("path") or ""),
        str(entry.metadata.get("canonical_url") or ""),
        str(entry.metadata.get("content_hash") or ""),
    ]
    return " ".join(part for part in parts if part)


def entry_search_text(entry: SkillHubEntry) -> str:
    metadata_terms = " ".join(
        str(entry.metadata.get(key) or "")
        for key in ("publisher", "source_name", "category", "slug", "detail_url")
    )
    return " ".join([
        entry.name,
        entry.description,
        " ".join(entry.tags or []),
        entry.author,
        entry.source,
        entry.source_id,
        metadata_terms,
    ])


def canonical_entry_key(entry: SkillHubEntry) -> str:
    for value in (
        entry.metadata.get("canonical_url"),
        entry.metadata.get("skill_url"),
        entry.metadata.get("raw_url"),
        entry.install_ref,
        entry.url,
    ):
        text = str(value or "").strip().lower()
        if text:
            return re.sub(r"#.*$", "", text)
    repo = str(entry.metadata.get("repository") or "").strip().lower()
    path = str(entry.metadata.get("path") or "").strip().lower().strip("/")
    if repo:
        return f"github:{repo}:{path}"
    content_hash = str(entry.metadata.get("content_hash") or "").strip().lower()
    if content_hash:
        return f"hash:{content_hash}"
    return f"{entry.source}:{entry.source_id}:{normalize_text(entry.name)}"


def dedupe_entries(entries: Iterable[SkillHubEntry]) -> list[SkillHubEntry]:
    deduped: dict[str, SkillHubEntry] = {}
    for entry in entries:
        if not entry.name.strip():
            continue
        key = canonical_entry_key(entry)
        existing = deduped.get(key)
        if existing is None or _entry_preference(entry) > _entry_preference(existing):
            deduped[key] = entry
    return list(deduped.values())


def search_entries(entries: Iterable[SkillHubEntry], query: str, *, limit: int = 50) -> list[SkillHubEntry]:
    candidates = dedupe_entries(entries)
    normalized_query = normalize_text(query)
    if not normalized_query:
        return sorted(candidates, key=_browse_sort_key)[:limit]

    scored: list[tuple[float, str, SkillHubEntry]] = []
    for entry in candidates:
        score = score_entry(entry, normalized_query)
        if score > 0:
            scored.append((score, normalize_text(entry.name), entry))
    scored.sort(key=lambda item: (-item[0], item[1], item[2].source, item[2].id))
    return [entry for _score, _name, entry in scored[:limit]]


def score_entry(entry: SkillHubEntry, query: str) -> float:
    q = normalize_text(query)
    if not q:
        return 1.0

    query_tokens = tokenize(q)
    if not query_tokens:
        return 0.0

    name = normalize_text(entry.name)
    identifier = normalize_text(entry_identifier(entry))
    description = normalize_text(entry.description)
    author = normalize_text(entry.author)
    tags = [normalize_text(tag) for tag in entry.tags or []]
    source = normalize_text(entry.source)
    search_text = normalize_text(entry_search_text(entry))
    name_tokens = tokenize(entry.name)
    desc_tokens = tokenize(entry.description)
    tag_tokens = [token for tag in tags for token in tokenize(tag)]
    text_tokens = set(tokenize(search_text))

    score = 0.0
    if name == q:
        score += 100
    if identifier == q or q in identifier.split():
        score += 90
    if name.startswith(q):
        score += 60
    if q in tags:
        score += 45
    if q and q in description:
        score += 25
    if q and q in search_text:
        score += 12

    query_counts = Counter(query_tokens)
    for token, count in query_counts.items():
        token_score = 0.0
        if token in name_tokens:
            token_score = max(token_score, 35)
        if any(name_token.startswith(token) for name_token in name_tokens):
            token_score = max(token_score, 28)
        if token in tag_tokens:
            token_score = max(token_score, 45)
        if any(tag_token.startswith(token) for tag_token in tag_tokens):
            token_score = max(token_score, 32)
        if token in desc_tokens:
            token_score = max(token_score, 10)
        if token in author.split():
            token_score = max(token_score, 10)
        if token == source:
            token_score = max(token_score, 8)
        if token_score == 0 and len(token) >= 4:
            fuzzy = _fuzzy_name_score(token, name_tokens)
            token_score = max(token_score, fuzzy)
        if token_score == 0 and token not in text_tokens:
            return 0.0
        score += token_score * count

    score += _trust_boost(entry)
    score += _popularity_boost(entry)
    score -= _risk_penalty(entry)
    return max(0.0, score)


def _fuzzy_name_score(token: str, name_tokens: list[str]) -> float:
    best = 0.0
    for name_token in name_tokens:
        if abs(len(token) - len(name_token)) > 3:
            continue
        ratio = SequenceMatcher(None, token, name_token).ratio()
        if ratio >= 0.86:
            best = max(best, 30)
        elif ratio >= 0.76:
            best = max(best, 20)
        elif ratio >= 0.68:
            best = max(best, 15)
    return best


def _trust_boost(entry: SkillHubEntry) -> float:
    trust = str(entry.trust_level or "").lower()
    if trust in {"trusted", "trusted_publisher", "verified", "official"}:
        return 5
    if trust in {"high_risk", "high-risk", "high-risk community"}:
        return -4
    return 0


def _popularity_boost(entry: SkillHubEntry) -> float:
    raw = (
        entry.metadata.get("install_count")
        or entry.metadata.get("downloads")
        or entry.metadata.get("stars")
        or entry.metadata.get("popularity")
        or 0
    )
    try:
        value = float(raw)
    except Exception:
        return 0
    if value <= 0:
        return 0
    return min(10.0, math.log10(value + 1) * 2.5)


def _risk_penalty(entry: SkillHubEntry) -> float:
    scan = entry.metadata.get("scan_summary")
    if not isinstance(scan, dict):
        return 0
    try:
        return min(30.0, float(scan.get("blocks") or 0) * 20 + float(scan.get("warnings") or 0) * 2)
    except Exception:
        return 0


def _entry_preference(entry: SkillHubEntry) -> tuple[int, float, int]:
    trust_rank = {
        "official": 4,
        "trusted": 3,
        "trusted_publisher": 3,
        "verified": 3,
        "community": 2,
        "high_risk": 1,
        "high-risk community": 1,
    }.get(str(entry.trust_level or "community").lower(), 2)
    return (
        trust_rank,
        _popularity_boost(entry),
        len(entry.description or ""),
    )


def _browse_sort_key(entry: SkillHubEntry) -> tuple[int, float, str]:
    trust_rank = -_entry_preference(entry)[0]
    popularity = -_popularity_boost(entry)
    return (trust_rank, popularity, normalize_text(entry.name))
