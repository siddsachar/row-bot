from __future__ import annotations

from dataclasses import dataclass
import re


DEFAULT_MAX_SPOKEN_SENTENCES = 3
_FALLBACK_TEXT = "I've provided the response in the app."
_TRUNCATION_TEXT = "The full response is shown in the app."

_CODE_BLOCK_RE = re.compile(r"```[\s\S]*?```")
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_LINK_RE = re.compile(r"\[([^\]]+)\]\([^\)]*\)")
_URL_RE = re.compile(r"https?://\S+")
_EMAIL_RE = re.compile(r"\S+@\S+\.\S+")
_HEADER_RE = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_BLOCKQUOTE_RE = re.compile(r"^>\s?", re.MULTILINE)
_BULLET_RE = re.compile(r"^\s*[-*+\u2022\u25e6\u25aa\u25b8\u25cf\u25cb]\s+", re.MULTILINE)
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
_EMOJI_RE = re.compile(r"[\U0001f000-\U0001ffff\u2600-\u27bf\ufe00-\ufe0f\u200d]")
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class SpeakableResponse:
    text: str
    truncated: bool = False
    fallback: bool = False
    reason: str = "assistant_response"


def user_requested_read_aloud(text: str) -> bool:
    normalized = " ".join(str(text or "").lower().split())
    markers = (
        "read this aloud",
        "read it aloud",
        "read aloud",
        "say this exactly",
        "speak this exactly",
        "tell me the whole thing out loud",
    )
    return any(marker in normalized for marker in markers)


def make_speakable_response(
    text: str,
    *,
    max_sentences: int = DEFAULT_MAX_SPOKEN_SENTENCES,
    allow_long: bool = False,
    fallback_text: str = _FALLBACK_TEXT,
    truncation_text: str = _TRUNCATION_TEXT,
    reason: str = "assistant_response",
) -> SpeakableResponse:
    raw = str(text or "")
    clean = clean_for_speech(raw)
    if not clean:
        return SpeakableResponse(fallback_text, fallback=True, reason=reason)

    if _looks_code_heavy(raw, clean):
        return SpeakableResponse(fallback_text, fallback=True, reason="code_or_structured_output")

    if allow_long:
        return SpeakableResponse(clean, reason=reason)

    sentences = split_sentences(clean)
    if not sentences:
        return SpeakableResponse(fallback_text, fallback=True, reason=reason)
    if len(sentences) <= max_sentences:
        return SpeakableResponse(clean, reason=reason)

    spoken = " ".join(sentences[:max_sentences]).strip()
    if not spoken.endswith((".", "!", "?")):
        spoken += "."
    return SpeakableResponse(f"{spoken} {truncation_text}", truncated=True, reason=reason)


def clean_for_speech(text: str) -> str:
    clean = str(text or "")
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
    clean = clean.replace("\u2013", " ").replace("\u2014", " ")
    clean = re.sub(r"\*{2,}", "", clean)
    clean = re.sub(r"(?<!\w)\*(?!\w)", "", clean)
    clean = re.sub(r"[ \t]{2,}", " ", clean)
    lines = [line.strip() for line in clean.splitlines() if line.strip()]
    for index, line in enumerate(lines):
        if line and line[-1] not in ".!?:;,":
            lines[index] = line + "."
    return "\n".join(lines).strip()


def split_sentences(text: str) -> list[str]:
    return [part.strip() for part in _SENTENCE_RE.split(str(text or "")) if part.strip()]


def _looks_code_heavy(raw: str, clean: str) -> bool:
    stripped = raw.strip()
    if not stripped:
        return False
    if "```" in raw:
        return len(clean) < len(stripped) * 0.35
    code_markers = sum(raw.count(marker) for marker in ("{", "}", ";", "=>", "def ", "class ", "function "))
    return code_markers >= 6 and len(clean) < len(stripped) * 0.55
