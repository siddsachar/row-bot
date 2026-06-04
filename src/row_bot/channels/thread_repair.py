"""
Shared corrupt-thread detection for all channel adapters.

Detects when a LangGraph thread has orphaned tool calls that prevent
the agent from continuing, and provides repair helpers.
"""

from __future__ import annotations

import re

_THREAD_CORRUPT_PATTERNS = (
    "tool call.*without.*result",
    "tool_calls.*without.*tool_results",
    "tool_calls that do not have a corresponding",
    "tool_call_ids did not have response",
    "must be followed by tool messages",
    "expected.*tool.*message",
)


def is_corrupt_thread_error(exc: Exception) -> bool:
    """Return True if the exception indicates a stuck/corrupt thread."""
    msg = str(exc).lower()
    return any(re.search(p, msg) for p in _THREAD_CORRUPT_PATTERNS)
