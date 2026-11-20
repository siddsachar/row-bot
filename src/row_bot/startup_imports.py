"""Start-up's shared heavy imports: made once, in one order, one thread at a time (RM4).

Several start-up workers want ``row_bot.tools`` early (the approval monitor's X posts, the agent and
Apps prewarms, the connection checks), and with it the knowledge graph and networkx, while the channel
adapters import. On a slow, uncached disk (a Mac app run from its translocated copy) first imports made
from several threads at once broke: a module file that is there was reported missing ("No module named
'row_bot.channels.auth_store'"), and networkx came back half-made ("cannot import name 'tournament'
from partially initialized module"). So start-up makes these imports here, before it starts those
workers, and the channel adapters import under the same lock, after them: one thread at a time, in the
same order on every start, whichever thread comes first.
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

logger = logging.getLogger(__name__)

# The knowledge graph first (networkx and its circular package), then the MCP runtime (its SDK), then the tools.
SHARED_MODULES: tuple[str, ...] = ("row_bot.knowledge_graph", "row_bot.mcp_client.runtime", "row_bot.tools")

_lock = threading.RLock()
_warmed = False


def package_missing(exc: ImportError) -> bool:
    """Whether an import failed because a package it needs isn't installed.

    Not when installed code failed to load: one of Row-Bot's own modules, or a package that is there. Only a
    missing package may be reported as one to install; any other failure is worth one more try.
    """
    root = str(getattr(exc, "name", None) or "").partition(".")[0]
    if not root or root == "row_bot":
        return False
    try:
        return importlib.util.find_spec(root) is None
    except (ImportError, ValueError):
        return False


@contextmanager
def serially() -> Iterator[None]:
    """First imports made here come after the shared ones, whichever thread gets here first, and never
    alongside another caller's."""
    with _lock:
        warm()
        yield


def warm() -> None:
    """Import the shared modules once; a caller that comes while they import waits for them.

    A module that fails is tried once more, then logged and left out: whatever uses it imports it again.
    """
    global _warmed
    with _lock:
        if _warmed:
            return
        started = time.perf_counter()
        for name in SHARED_MODULES:
            for attempt in (1, 2):
                try:
                    importlib.import_module(name)
                    break
                except Exception:
                    logger.warning("Start-up couldn't import %s (try %d of 2)", name, attempt, exc_info=True)
        _warmed = True
        logger.info("startup.shared_imports duration_ms=%.1f", (time.perf_counter() - started) * 1000.0)
