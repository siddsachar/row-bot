"""Fact text hygiene: one clean sentence per fact, no secrets, nothing empty."""
from __future__ import annotations

import re


MAX_FACT_CHARS = 500

_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_SURROGATE = re.compile(r"[\ud800-\udfff]")
_CITATION = re.compile(r"[^]*|[-]")
_BULLET = re.compile(r"^\s*(?:[-*•·–—+>]+|\d{1,3}[.)]|\[[ xX]\])\s+")
_EMPHASIS = re.compile(r"(\*\*|__|`)")
_SENTENCE_END = re.compile(r"(?<=[.!?。！？])\s+")

_SECRET_PATTERNS = tuple(re.compile(pattern, re.IGNORECASE) for pattern in (
    r"\b(?:pass(?:word|phrase|code)?|pwd|pin(?: code)?)\b\s*(?:is|was|:|=)\s*\S+",
    r"\b(?:api[ _-]?key|secret(?: key)?|access[ _-]?token|auth[ _-]?token|token|private key)\b\s*(?:is|:|=)\s*['\"]?[A-Za-z0-9_\-./+=]{12,}",
    r"\bbearer\s+[A-Za-z0-9_\-./+=]{16,}",
    r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_\-]{16,}",
    r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}|\bgithub_pat_[A-Za-z0-9_]{20,}",
    r"\bxox[abprs]-[A-Za-z0-9-]{10,}",
    r"\bAKIA[0-9A-Z]{16}\b",
    r"\bAIza[0-9A-Za-z_\-]{30,}",
    r"\bhf_[A-Za-z0-9]{30,}",
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}",
    r"\b(?:account|acct|routing|card|passport|tax id|national insurance|membership)\s*(?:number|num|no\.?|#)"
    r"[^0-9\n]{0,12}\d[\d -]{4,}",
    r"\b(?:iban|ssn|social security(?: number)?|sort code|swift|bic)\b[^0-9\n]{0,12}\d[\d -]{4,}",
    r"\b\d{3}-\d{2}-\d{4}\b",
)) + (re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){3,7}(?: ?[A-Z0-9]{1,4})?\b"),)
_CARD_CANDIDATE = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")


def _luhn_ok(digits: str) -> bool:
    total = 0
    for index, char in enumerate(reversed(digits)):
        number = int(char)
        if index % 2:
            number *= 2
            if number > 9:
                number -= 9
        total += number
    return total % 10 == 0


def looks_secret(text: str) -> bool:
    """True for text that looks like a password, key, token, card or account number."""
    if any(pattern.search(text) for pattern in _SECRET_PATTERNS):
        return True
    for match in _CARD_CANDIDATE.finditer(text):
        digits = re.sub(r"\D", "", match.group())
        if 13 <= len(digits) <= 19 and _luhn_ok(digits):
            return True
    return False


def clean_line(text: str) -> str:
    """One line of fact text: no controls, citations, bullets, emphasis or extra spaces."""
    text = _SURROGATE.sub("�", text)
    text = _CITATION.sub("", text)
    text = _CONTROL.sub(" ", text)
    text = " ".join(text.split())
    text = _BULLET.sub("", text)
    text = _EMPHASIS.sub("", text)
    return " ".join(text.split()).strip()


def _hard_cut(sentence: str, limit: int) -> list[str]:
    pieces: list[str] = []
    while len(sentence) > limit:
        cut = sentence[:limit].rsplit(" ", 1)[0]
        if len(cut) < limit // 2:
            cut = sentence[:limit]
        pieces.append(cut.strip())
        sentence = sentence[len(cut):].strip()
    if sentence:
        pieces.append(sentence)
    return pieces


def split_long(text: str, limit: int = MAX_FACT_CHARS) -> list[str]:
    """Text over ``limit`` characters split on sentences, packed back up to ``limit``."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""
    for sentence in _SENTENCE_END.split(text):
        for piece in _hard_cut(sentence.strip(), limit):
            if current and len(current) + 1 + len(piece) > limit:
                chunks.append(current)
                current = piece
            else:
                current = f"{current} {piece}".strip()
    if current:
        chunks.append(current)
    return chunks


_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+(?=[\"'(\[]?[A-Z0-9À-ɏ])")
_ABBREVIATION = re.compile(r"(?:\b(?:e\.g|i\.e|etc|vs|approx|Dr|Mr|Mrs|Ms|Prof|St|Mt|No|Jr|Sr)|\b[A-Z])\.$")


def split_sentences(text: str) -> list[str]:
    """``text`` split into sentences, keeping common abbreviations whole."""
    pieces: list[str] = []
    for piece in _SENTENCE_BREAK.split(text):
        if pieces and _ABBREVIATION.search(pieces[-1]):
            pieces[-1] = f"{pieces[-1]} {piece}"
        else:
            pieces.append(piece)
    return [piece.strip() for piece in pieces if piece.strip()]


def fact_lines(text: str, *, sentences: bool = False) -> list[str]:
    """A block of memory text (lines, bullets, paragraphs) as clean fact texts.

    With ``sentences`` each line is also split into its sentences. Secrets are
    not removed here; the scan drops and counts them.
    """
    out: list[str] = []
    for raw in text.splitlines():
        line = clean_line(raw)
        if not line or set(line) <= set("-=_*#|:~` "):
            continue
        for piece in split_sentences(line) if sentences else [line]:
            out.extend(split_long(piece))
    return out


def has_broken_characters(text: str) -> bool:
    return bool(_SURROGATE.search(text)) or "�" in text
