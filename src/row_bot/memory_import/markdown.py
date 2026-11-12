"""Memory text written as Markdown: headings, bullets and paragraphs."""
from __future__ import annotations

import re
from collections.abc import Iterator


_HASH_HEADING = re.compile(r"^\s*#{1,6}\s+(.+?)\s*#*\s*$")
_BOLD_HEADING = re.compile(r"^\s*(?:\*\*|__)(.+?)(?:\*\*|__)\s*:?\s*$")
_LABEL_HEADING = re.compile(r"^\s*(?:\d{1,2}[.)]\s*)?([^.!?:|]{2,60}):\s*$")


def heading_of(line: str) -> str | None:
    """The heading text when ``line`` is a heading, else ``None``."""
    for pattern in (_HASH_HEADING, _BOLD_HEADING, _LABEL_HEADING):
        match = pattern.match(line)
        if match:
            return match.group(1).strip(" *_:")
    return None


def memory_sections(text: str) -> Iterator[tuple[str | None, str]]:
    """Each content line with the heading it sits under."""
    heading: str | None = None
    for line in text.splitlines():
        if not line.strip():
            continue
        found = heading_of(line)
        if found is not None:
            heading = found
            continue
        yield heading, line
