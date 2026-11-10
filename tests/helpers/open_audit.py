"""Record every file this process opens under given paths (``sys.addaudithook``'s "open" event).

    with watch_opens(home / ".claude") as opened:
        ...  # code under test
    assert opened == []

An audit hook can't be removed, so one hook is added the first time and does nothing outside ``watch_opens``.
Opens by child processes (a fake Claude Code writing its own credential file) are theirs, not this process's.
"""

from __future__ import annotations

import os
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

_lock = threading.Lock()
_watched: list[tuple[tuple[str, ...], list[str]]] = []
_installed = False


def _normal(path: Any) -> str:
    return os.path.normcase(os.path.abspath(os.fsdecode(path)))


def _hook(event: str, args: tuple[Any, ...]) -> None:
    if event != "open" or not _watched or not args:
        return
    path = args[0]
    if not isinstance(path, (str, bytes, os.PathLike)):
        return
    try:
        opened = _normal(path)
    except (TypeError, ValueError):
        return
    for roots, record in list(_watched):
        if any(opened == root or opened.startswith(root + os.sep) for root in roots):
            record.append(opened)


@contextmanager
def watch_opens(*paths: Path) -> Iterator[list[str]]:
    global _installed
    with _lock:
        if not _installed:
            sys.addaudithook(_hook)
            _installed = True
    record: list[str] = []
    entry = (tuple(_normal(path) for path in paths), record)
    _watched.append(entry)
    try:
        yield record
    finally:
        _watched.remove(entry)
