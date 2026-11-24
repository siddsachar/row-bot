"""What Row-Bot says aloud for an answer (VD7).

The whole answer is spoken: its prose, without the markdown, code, tables, links and emoji that are
better read than heard. Those stay on screen; only an answer with nothing left to say aloud is spoken as
one short pointer to the screen.
"""
from __future__ import annotations

import re


ON_SCREEN_POINTER = "The answer is on screen."

_CODE_BLOCK_RE = re.compile(r"```[\s\S]*?```")
_INTERFACE_RE = re.compile(r"```rowbot-ui[^\n]*\n([\s\S]*?)```")
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_LINK_RE = re.compile(r"\[([^\]]+)\]\([^\)]*\)")
_URL_RE = re.compile(r"https?://\S+")
_EMAIL_RE = re.compile(r"\S+@\S+\.\S+")
_HEADER_RE = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_BLOCKQUOTE_RE = re.compile(r"^>\s?", re.MULTILINE)
_BULLET_RE = re.compile(r"^\s*[-*+•◦▪▸●○]\s+", re.MULTILINE)
_NUMBERED_RE = re.compile(r"^\s*\d+[.)]\s+", re.MULTILINE)
_TABLE_RE = re.compile(r"^\|.*\|$", re.MULTILINE)
_RULE_RE = re.compile(r"^[-=]{3,}$", re.MULTILINE)
_IMAGE_RE = re.compile(r"!\[.*?\]\(.*?\)")
_EMPHASIS_PATTERNS = (
    re.compile(r"\*{3}(.+?)\*{3}", re.DOTALL),
    re.compile(r"_{3}(.+?)_{3}", re.DOTALL),
    re.compile(r"\*{2}(.+?)\*{2}", re.DOTALL),
    re.compile(r"_{2}(.+?)_{2}", re.DOTALL),
    re.compile(r"(?<!\*)\*(?!\*)(.+?)\*(?!\*)", re.DOTALL),
    re.compile(r"~~(.+?)~~", re.DOTALL),
)
_EMOJI_RE = re.compile(r"[\U0001f000-\U0001ffff☀-➿︀-️‍]")
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


def spoken_text(text: str) -> str:
    """The words to say for *text*: all of its prose, or the on-screen pointer when it has none."""
    return clean_for_speech(text) or ON_SCREEN_POINTER


def _interface_said(match: re.Match[str]) -> str:
    """An interface Row-Bot drew (generative UI) is said by its name: it stays on screen."""
    from row_bot.application import surfaces

    found = surfaces.parse(match.group(1))
    return f"\n{found[1]['title']} is on screen.\n" if found else ""


def clean_for_speech(text: str) -> str:
    clean = str(text or "")
    clean = _INTERFACE_RE.sub(_interface_said, clean)
    clean = _CODE_BLOCK_RE.sub("", clean)
    clean = _INLINE_CODE_RE.sub(r"\1", clean)
    clean = _LINK_RE.sub(r"\1", clean)
    clean = _TABLE_RE.sub("", clean)
    clean = _RULE_RE.sub("", clean)
    clean = _IMAGE_RE.sub("", clean)
    clean = _URL_RE.sub("", clean)
    clean = _EMAIL_RE.sub("", clean)
    clean = _HEADER_RE.sub("", clean)
    clean = _BLOCKQUOTE_RE.sub("", clean)
    clean = _BULLET_RE.sub("", clean)
    clean = _NUMBERED_RE.sub("", clean)
    for pattern in _EMPHASIS_PATTERNS:
        clean = pattern.sub(r"\1", clean)
    clean = _EMOJI_RE.sub("", clean)
    clean = clean.replace("–", " ").replace("—", " ")
    clean = re.sub(r"\*{2,}", "", clean)
    clean = re.sub(r"(?<!\w)\*(?!\w)", "", clean)
    clean = re.sub(r"[ \t]{2,}", " ", clean)
    lines = [line.strip() for line in clean.splitlines() if line.strip()]
    for index, line in enumerate(lines):
        if line and line[-1] not in ".!?:;,":
            lines[index] = line + "."
    return "\n".join(lines).strip()


def speech_pieces(text: str, max_chars: int) -> list[str]:
    """*text* in pieces of at most *max_chars*, split between sentences (or words, for a longer sentence).

    Joined back with spaces, the pieces say every word of *text*: a long answer is split, never cut.
    """
    pieces: list[str] = []
    current = ""
    for sentence in _SENTENCE_RE.split(" ".join(str(text or "").split())):
        for part in _within(sentence, max_chars):
            if current and len(current) + 1 + len(part) > max_chars:
                pieces.append(current)
                current = part
            else:
                current = f"{current} {part}" if current else part
    if current:
        pieces.append(current)
    return pieces


def _within(sentence: str, max_chars: int) -> list[str]:
    """One sentence as parts of at most *max_chars*: whole words where it can, else cut inside a word."""
    if len(sentence) <= max_chars:
        return [sentence] if sentence else []
    parts: list[str] = []
    current = ""
    for word in sentence.split(" "):
        while len(word) > max_chars:
            if current:
                parts.append(current)
                current = ""
            parts.append(word[:max_chars])
            word = word[max_chars:]
        if current and len(current) + 1 + len(word) > max_chars:
            parts.append(current)
            current = word
        else:
            current = f"{current} {word}" if current else word
    if current:
        parts.append(current)
    return parts
