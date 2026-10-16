"""One lease for the heavy local work (L2a): who has this computer now.

Heavy local work is a chat generating on a local model, Dream Cycle, reading documents
(embeddings and indexing) and personal-model training. They don't all wait for each
other:

- **The person always comes first.** A chat (on any model), a running workflow, reading
  documents, or the person sending something in the last few minutes is *use*: training
  pauses at its next step (D12) and never makes them wait. A chat on a **local** model
  needs the same graphics card, so training stops at once instead and continues later
  from its newest checkpoint.
- **Dream Cycle runs first in quiet hours**; training starts after it, and Dream waits
  while training holds the lease (:func:`training_holds`, read by ``dream_cycle``
  through ``sys.modules`` so it costs nothing while Learning never loaded).
- **Training holds the lease** while its worker runs (``claim`` / ``release``).

:class:`ResourceLease` reads what is running from the app's own records (the
generation registry, the workflow runs, the document jobs, the person's last activity)
through probes that tests replace. Nothing here starts, stops or imports a model.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# After the person's last message or conversation switch, Row-Bot counts as in use this long.
RECENT_USE_SECONDS = 5 * 60.0

_holder_lock = threading.Lock()
_training_holds = False


def training_holds() -> bool:
    """Whether training holds the lease now (Dream Cycle waits while it does)."""

    return _training_holds


def claim_training() -> None:
    global _training_holds
    with _holder_lock:
        _training_holds = True


def release_training() -> None:
    global _training_holds
    with _holder_lock:
        _training_holds = False


@dataclass(frozen=True)
class LeaseState:
    """What wants this computer now, besides training."""

    chat_local: bool = False  # a chat generating on a local model (the same graphics card)
    chat_cloud: bool = False  # a chat generating on a cloud model
    workflow: bool = False  # a workflow running
    indexing: bool = False  # documents being read (embeddings, indexing)
    dream: bool = False  # Dream Cycle running
    # Seconds since the person last sent something or switched conversations (None: unknown).
    since_activity: float | None = None

    @property
    def busy(self) -> bool:
        """Something heavy is running: training doesn't start (Train now waits for it)."""

        return self.chat_local or self.chat_cloud or self.workflow or self.indexing or self.dream

    def used_since(self, seconds: float) -> bool:
        """The person did something in the last ``seconds`` (since training started, say)."""

        return self.since_activity is not None and self.since_activity < seconds

    @property
    def recently_used(self) -> bool:
        return self.used_since(RECENT_USE_SECONDS)

    @property
    def free(self) -> bool:
        """Free for training to start or continue by itself: nothing running, nobody around."""

        return not self.busy and not self.recently_used

    @property
    def holder(self) -> str | None:
        if self.chat_local or self.chat_cloud:
            return "chat"
        if self.workflow:
            return "workflow"
        if self.indexing:
            return "indexing"
        if self.dream:
            return "dream"
        return None


# ── Probes of the app's own records ─────────────────────────────────────────


def _generations() -> Iterable[Any]:
    from row_bot.runtime.executions import generation_registry

    return generation_registry.active()


def _default_model_ref() -> str:
    try:
        from row_bot.models import get_current_model

        return str(get_current_model() or "")
    except Exception:
        return ""


def model_is_local(model_ref: str) -> bool:
    """Whether a chat's model runs on this computer (Ollama, a loopback endpoint)."""

    ref = str(model_ref or "") or _default_model_ref()
    if not ref:
        return False
    try:
        from row_bot.learning.spending import is_local_provider
        from row_bot.providers.selection import parse_model_ref

        parsed = parse_model_ref(ref)
        if parsed:
            return is_local_provider(parsed[0])
        from row_bot.models import is_model_local

        return bool(is_model_local(ref))
    except Exception:
        logger.debug("Learning could not tell where a chat's model runs; it counts as local", exc_info=True)
        return True


def _workflows_running() -> bool:
    tasks = sys.modules.get("row_bot.tasks")
    try:
        return bool(tasks.get_running_tasks()) if tasks is not None else False
    except Exception:
        return False


def _indexing() -> bool:
    try:
        from row_bot.document_jobs import DocumentJobService

        return DocumentJobService().active_summary().get("active") is not None
    except Exception:
        return False


def _dreaming() -> bool:
    dream = sys.modules.get("row_bot.dream_cycle")
    try:
        return bool(dream.is_dreaming()) if dream is not None else False
    except Exception:
        return False


def _since_activity() -> float | None:
    extraction = sys.modules.get("row_bot.memory_extraction")
    try:
        return float(extraction.idle_seconds()) if extraction is not None else None
    except Exception:
        return None


class ResourceLease:
    """Reads who has this computer now. Each probe is replaceable (tests pass fakes)."""

    def __init__(
        self,
        *,
        generations: Callable[[], Iterable[Any]] = _generations,
        is_local: Callable[[str], bool] = model_is_local,
        workflows: Callable[[], bool] = _workflows_running,
        indexing: Callable[[], bool] = _indexing,
        dreaming: Callable[[], bool] = _dreaming,
        since_activity: Callable[[], float | None] = _since_activity,
        indexing_every: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._generations = generations
        self._is_local = is_local
        self._workflows = workflows
        self._indexing = indexing
        self._dreaming = dreaming
        self._since_activity = since_activity
        self._indexing_every = indexing_every
        self._clock = clock
        self._indexing_seen: tuple[float, bool] | None = None

    def _indexing_now(self) -> bool:
        # A database read: at most once every few seconds.
        now = self._clock()
        if self._indexing_seen is None or now - self._indexing_seen[0] >= self._indexing_every:
            self._indexing_seen = (now, bool(self._indexing()))
        return self._indexing_seen[1]

    def read(self) -> LeaseState:
        local = cloud = False
        try:
            for handle in self._generations():
                if self._is_local(str(getattr(handle, "model_ref", "") or "")):
                    local = True
                else:
                    cloud = True
        except Exception:
            logger.debug("Learning could not read the chats in flight", exc_info=True)
        return LeaseState(
            chat_local=local,
            chat_cloud=cloud,
            workflow=bool(self._workflows()),
            indexing=self._indexing_now(),
            dream=bool(self._dreaming()),
            since_activity=self._since_activity(),
        )


__all__ = [
    "LeaseState",
    "RECENT_USE_SECONDS",
    "ResourceLease",
    "claim_training",
    "model_is_local",
    "release_training",
    "training_holds",
]
