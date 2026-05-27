"""Helpers for bounded, stale-safe chat transcript rendering."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Iterable


LARGE_TRANSCRIPT_THRESHOLD = 80
TRANSCRIPT_WINDOW_SIZE = 60
TRANSCRIPT_CHUNK_TARGET_MS = 16.0
TRANSCRIPT_MAX_CHUNK_MESSAGES = 10


@dataclass(frozen=True)
class TranscriptWindow:
    start: int
    end: int
    total: int

    @property
    def older_count(self) -> int:
        return max(0, self.start)

    @property
    def visible_count(self) -> int:
        return max(0, self.end - self.start)


def message_key(index: int, msg: dict) -> str:
    """Return a compact stable key for one rendered message position."""
    role = str(msg.get("role", ""))
    timestamp = str(msg.get("timestamp", ""))
    content = msg.get("content", "")
    if isinstance(content, list):
        content_text = " ".join(str(item) for item in content)
    else:
        content_text = str(content or "")
    shape = "|".join(
        [
            role,
            timestamp,
            content_text[:512],
            str(len(content_text)),
            str(len(msg.get("tool_results") or [])),
            str(len(msg.get("images") or [])),
            str(len(msg.get("videos") or [])),
            str(len(msg.get("charts") or [])),
            str(len(str(msg.get("thinking") or ""))),
        ]
    )
    digest = hashlib.sha1(shape.encode("utf-8", errors="replace")).hexdigest()[:12]
    return f"{index}:{role}:{digest}"


def message_keys(messages: Iterable[dict], *, start: int = 0) -> list[str]:
    return [message_key(idx, msg) for idx, msg in enumerate(messages, start=start)]


def choose_transcript_window(
    total: int,
    *,
    requested_start: int | None = None,
    threshold: int = LARGE_TRANSCRIPT_THRESHOLD,
    window_size: int = TRANSCRIPT_WINDOW_SIZE,
) -> TranscriptWindow:
    """Choose the initial visible transcript window.

    Small transcripts render fully. Large transcripts render the latest window
    unless the user has explicitly requested older history.
    """
    if total <= threshold:
        return TranscriptWindow(start=0, end=total, total=total)

    default_start = max(0, total - window_size)
    if requested_start is None:
        start = default_start
    else:
        start = max(0, min(requested_start, default_start))
    return TranscriptWindow(start=start, end=total, total=total)


def reset_transcript_request(p: object, thread_id: str | None) -> None:
    if getattr(p, "transcript_requested_thread_id", None) != thread_id:
        setattr(p, "transcript_requested_thread_id", thread_id)
        setattr(p, "transcript_requested_start", None)


def rendered_window_matches(
    rendered_keys: list[str],
    all_keys: list[str],
    *,
    start: int,
) -> bool:
    end = start + len(rendered_keys)
    if start < 0 or end > len(all_keys):
        return False
    return rendered_keys == all_keys[start:end]
