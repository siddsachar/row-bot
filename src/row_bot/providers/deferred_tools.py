"""The tools this run offers only through ``tool_search`` / ``tool_invoke``.

A small context window (A5) binds core tools directly and offers the rest on demand through the
discovery bridge. The Agent records those deferred names when it activates its tool set, so a
provider transport can use them: a local Ollama model that calls one by its own name has the call
dropped by Ollama's parser (it keeps only calls to tools in the request), and the transport can ask
again with the names bound and route the call through ``tool_invoke``.
"""

from __future__ import annotations

from collections.abc import Iterable
from contextvars import ContextVar

_OFFERED: ContextVar[frozenset[str]] = ContextVar("row_bot_deferred_tool_names", default=frozenset())


def offer(names: Iterable[str]) -> None:
    """Record the tools this run offers only through the discovery bridge."""
    _OFFERED.set(frozenset(str(name) for name in names if str(name or "").strip()))


def offered() -> frozenset[str]:
    """The tools this run offers only through the discovery bridge (empty when nothing is deferred)."""
    return _OFFERED.get()
