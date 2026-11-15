"""Small value helpers: times to aware UTC, HTML answers to plain text, titles."""
from __future__ import annotations

import re
from datetime import UTC, date, datetime
from html.parser import HTMLParser
from typing import Any


_DIGITS = re.compile(r"^-?\d+(\.\d+)?$")
_DATE_ONLY = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


def to_utc(value: Any) -> datetime | None:
    """Epoch seconds or milliseconds, ISO 8601 text or a Mongo ``$date``, as aware UTC.

    A time without a zone is taken as UTC. Anything unreadable is ``None``.
    """
    if isinstance(value, dict):
        inner = value.get("$date", value.get("$numberLong"))
        if isinstance(inner, dict):
            inner = inner.get("$numberLong")
        if inner is None or inner is value:
            return None
        if isinstance(inner, str) and _DIGITS.match(inner.strip()):
            return _from_epoch(float(inner), assume_ms=True)
        return to_utc(inner)
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return _from_epoch(float(value))
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if _DIGITS.match(text):
            return _from_epoch(float(text))
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00") if text.endswith("Z") else text)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    return None


def _from_epoch(number: float, *, assume_ms: bool = False) -> datetime | None:
    if number <= 0:
        return None
    if assume_ms or number > 1e11:
        number /= 1000.0
    if number > 1e11:  # microseconds
        number /= 1000.0
    try:
        return datetime.fromtimestamp(number, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def iso_date(value: Any) -> str | None:
    """``YYYY-MM-DD`` for a time-like value, or ``None``."""
    if isinstance(value, str):
        match = _DATE_ONLY.match(value.strip())
        if match:
            try:
                return date(int(match[1]), int(match[2]), int(match[3])).isoformat()
            except ValueError:
                return None
    moment = value if isinstance(value, datetime) else to_utc(value)
    return moment.date().isoformat() if moment else None


def short_title(text: str, limit: int = 80) -> str:
    """A one-line title from a first question, cut at a word."""
    line = " ".join(text.split())
    if len(line) <= limit:
        return line
    cut = line[:limit].rsplit(" ", 1)[0] or line[:limit]
    return cut.rstrip(" ,.;:") + "…"


_BLOCK_TAGS = {
    "p", "div", "br", "li", "ul", "ol", "tr", "table", "pre", "blockquote",
    "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "hr",
}


class _TextCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._hidden = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "template"}:
            self._hidden += 1
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "template"}:
            self._hidden = max(0, self._hidden - 1)
        elif tag in _BLOCK_TAGS and tag != "li":
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._hidden:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    """Plain text of an HTML answer. Nothing is rendered or fetched."""
    collector = _TextCollector()
    collector.feed(html)
    collector.close()
    lines = [" ".join(line.split()) for line in "".join(collector.parts).splitlines()]
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def as_text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}
