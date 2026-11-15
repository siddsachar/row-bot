"""App-wide live updates: which background state changed, never what (B407).

Background work (a workflow run, a helper, an approval raised in another
chat) changes what the sidebar, Home and Monitor show without the person
asking. Each of those states is a *topic* with a revision. Whatever changes
one calls :func:`publish` from any thread, and every open client hears it on
one authenticated stream (``GET /api/v1/live``, or ``/api/v1/live/poll`` where
a proxy holds a stream back) and reads the state again through the normal
authenticated APIs at once.

The stream carries only topic names and revision numbers, never content, so
it adds nothing a signed-in client could not already read. Bursts coalesce:
a client gets at most one frame per stream tick, with each topic's latest
revision. Revisions live in memory; a new server process starts a new
``epoch``, and a client seeing a new epoch reads every topic again.

Topics (add one here, then publish it where its state changes):

- ``conversations``: the conversation list: one created (a workflow run's or a
  helper's included), renamed, pinned, deleted or restored, and a conversation
  starting, waiting on the person or settling.
- ``runs``: workflow runs started, finished, failed, stopped or waiting.
- ``approvals``: an approval or question raised, answered, cancelled or expired,
  anywhere.
- ``helpers``: helper (agent) runs started, progressing to a new state or ended.
- ``processes``: background processes started, stopped or ended, or a local
  address they opened.
- ``monitor``: what Monitor lists as needing the person (a failed run, a
  start-up warning, a channel, an app or a plugin that stopped or started
  needing them).
- ``models``: the saved model catalog: refreshed (at start-up, every six hours,
  on Refresh, after a provider sign-in), or a provider disconnected (B411).
- ``notices``: a new background notice ("Row-Bot sent you report.pdf", a
  workflow finished), for windows with no conversation stream to carry it.
- ``learning``: Row-Bot Learning (preview): a setting or command, a job or
  version changing state, a comparison starting, moving on or ending, the
  vault taking new work, a personal model's use (``learning.changed``).
- ``proactive``: Proactive Row-Bot: a moment made, shown, acted on, dismissed or
  run out, a rule changed, or a new line in its Activity log.
"""
from __future__ import annotations

import functools
import threading
import uuid
from collections.abc import Callable
from typing import Literal, ParamSpec, TypeVar, get_args

P = ParamSpec("P")
R = TypeVar("R")

Topic = Literal["conversations", "runs", "approvals", "helpers", "processes", "monitor", "models", "notices",
                "learning", "proactive"]
TOPICS: tuple[str, ...] = get_args(Topic)


class LiveUpdates:
    """Thread-safe topic revisions for one server process."""

    def __init__(self) -> None:
        self.epoch = uuid.uuid4().hex
        self._lock = threading.Lock()
        self._topics = dict.fromkeys(TOPICS, 0)
        # Moves on with every publish, so a stream or a poll can tell at a
        # glance whether anything changed since it last looked.
        self._revision = 0

    def publish(self, *topics: Topic) -> None:
        unknown = [topic for topic in topics if topic not in self._topics]
        if unknown:
            raise ValueError(f"Unknown live topic: {', '.join(unknown)}")
        with self._lock:
            for topic in set(topics):
                self._topics[topic] += 1
            self._revision += 1

    @property
    def revision(self) -> int:
        with self._lock:
            return self._revision

    def frame(self) -> dict:
        """The current revisions: only names and numbers."""
        with self._lock:
            return {"epoch": self.epoch, "revision": self._revision, "topics": dict(self._topics)}


live_updates = LiveUpdates()


def publish(*topics: Topic) -> None:
    """Say these topics changed; every open client reads them again."""
    live_updates.publish(*topics)


def publishes(*topics: Topic) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Mark a state writer: once it has run (even when it raised), its topics changed."""
    unknown = [topic for topic in topics if topic not in TOPICS]
    if unknown:
        raise ValueError(f"Unknown live topic: {', '.join(unknown)}")

    def decorate(writer: Callable[P, R]) -> Callable[P, R]:
        @functools.wraps(writer)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            try:
                return writer(*args, **kwargs)
            finally:
                publish(*topics)

        return wrapper

    return decorate
