# Thoth – Messaging channel adapters
# Each sub-module provides a channel integration (Telegram, Email, etc.)
# that bridges external messaging platforms to the Thoth agent.

import re as _re

# Matches YouTube URLs (bare, markdown-linked, or bold-wrapped)
_YT_URL_RE = _re.compile(
    r'(?:\*{0,2})'
    r'(?:\[[^\]]*\]\()?'          # optional [text](
    r'(https?://(?:www\.)?'
    r'(?:youtube\.com/(?:watch\?v=|shorts/)|youtu\.be/)'
    r'[a-zA-Z0-9_-]{11}'
    r'[^\s)\]]*)'                 # capture full URL
    r'(?:\))?'
    r'(?:\*{0,2})',
)


def extract_youtube_urls(text: str) -> tuple[str, list[str]]:
    """Extract YouTube URLs from text and return (cleaned_text, urls).

    Removes the YouTube link lines from the text so the caller can send
    them as separate messages (for native platform previews).
    """
    urls: list[str] = []
    for m in _YT_URL_RE.finditer(text):
        url = m.group(1)
        if url not in urls:
            urls.append(url)

    if not urls:
        return text, []

    # Remove lines that are purely a YouTube URL (with optional markdown wrapping)
    cleaned_lines: list[str] = []
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped and _YT_URL_RE.fullmatch(stripped):
            continue
        # For mixed lines, strip the URL portion
        cleaned = _YT_URL_RE.sub("", line)
        # Drop lines that are now just a list marker (e.g. "1." or "- ")
        residual = cleaned.strip()
        if residual and _re.fullmatch(r"[\d]+\.\s*|[-*]\s*", residual):
            continue
        cleaned_lines.append(cleaned)

    cleaned_text = "\n".join(cleaned_lines).strip()
    return cleaned_text, urls
