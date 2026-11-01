"""The server's memory (H12): a container's limit, its highest use, and restarts for lack of memory.

In a container the kernel holds Row-Bot to a memory limit (its cgroup's ``memory.max``). Row-Bot
reads it, and while it runs it keeps the highest working set it has seen in the data folder, so the
number outlives the process. The working set is the use without the file cache the kernel can drop
(``inactive_file``), as kubelet and Docker count it: the cache fills to the limit on any busy server.
The kernel stopping a process in the container for lack of memory (``oom_kill``) is kept too. Each
run is marked as running until it shuts down. A start that finds the last run still marked (it never
shut down: the kernel stopped it) after its working set came within 10% of the limit, or after the
kernel stopped something for lack of memory, takes that for a restart for lack of memory: Home and
Monitor say so (U8) until the person dismisses it, and only another such restart brings it back.

The desktop has no limit, so nothing here runs there: no file, no thread, no reading.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
import threading
from typing import Any, Callable

from row_bot.data_paths import get_row_bot_data_dir

logger = logging.getLogger(__name__)

# The container's own cgroup; only Linux has one. Tests point it at a folder of their own.
CGROUP_ROOT: Path | None = Path("/sys/fs/cgroup") if sys.platform.startswith("linux") else None
STATE_FILE = "server_memory.json"
# A run that never shut down, after its use came this close to the limit, ran out of memory.
NEAR_LIMIT = 0.9
# Under this, local speech-to-text doesn't fit beside the rest (4 GB is the recommended size).
SMALL_SERVER_BYTES = int(3.5 * 2**30)
WATCH_SECONDS = 15.0
RESIZE_GUIDE = "https://row-bot.ai/docs/operations/hosting#memory"
# What the kernel calls the limit, the use now, the cache it can drop (a key in its stat file) and
# the processes it stopped for lack of memory: cgroup v2, then v1. v1 says "no limit" with a number
# near the largest it can hold.
_FILES = (
    ("memory.max", "memory.current", "memory.stat", "inactive_file", "memory.events"),
    ("memory/memory.limit_in_bytes", "memory/memory.usage_in_bytes", "memory/memory.stat", "total_inactive_file",
     "memory/memory.oom_control"),
)
_NO_LIMIT = 1 << 60

_lock = threading.Lock()
_stop = threading.Event()
_thread: threading.Thread | None = None
_peak = 0
_kills = 0  # The kernel's oom_kill count when this run started,
_killed = 0  # and how many more it has counted since.


def _number(path: Path) -> int | None:
    try:
        text = path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        return None
    return int(text) if text.isdigit() else None


def _keyed(path: Path) -> dict[str, int]:
    """A cgroup file of "key number" lines (memory.stat, memory.events)."""
    try:
        lines = path.read_text(encoding="ascii").splitlines()
    except (OSError, UnicodeError):
        return {}
    pairs = (line.split() for line in lines)
    return {pair[0]: int(pair[1]) for pair in pairs if len(pair) == 2 and pair[1].isdigit()}


def _files() -> tuple[Path, Path, Path, str, Path] | None:
    root = CGROUP_ROOT
    if root is None:
        return None
    for limit, usage, stat, inactive, events in _FILES:
        if (root / limit).is_file():
            return root / limit, root / usage, root / stat, inactive, root / events
    return None


def limit_bytes() -> int | None:
    """The container's memory limit in bytes; None when it has none ("max"), or no cgroup (the desktop)."""
    files = _files()
    value = _number(files[0]) if files else None
    return value if value and value < _NO_LIMIT else None


def working_set_bytes() -> int | None:
    """The memory in use now without the file cache the kernel can drop (``inactive_file``)."""
    files = _files()
    usage = _number(files[1]) if files else None
    if files is None or usage is None:
        return None
    return max(0, usage - _keyed(files[2]).get(files[3], 0))


def _oom_kills() -> int:
    """How many processes the kernel has stopped in this cgroup for lack of memory."""
    files = _files()
    return _keyed(files[4]).get("oom_kill", 0) if files else 0


def size_words(value: int) -> str:
    """A memory size as people say it: "2 GB", "1.5 GB" (to the nearest half)."""
    halves = max(1, round(value / 2**30 * 2))
    return f"{halves / 2:g} GB"


def _path() -> Path:
    return get_row_bot_data_dir() / STATE_FILE


def _read() -> dict[str, Any]:
    try:
        value = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _write(state: dict[str, Any]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{STATE_FILE}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            json.dump(state, file, indent=2)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _ran_out(run: Any) -> bool:
    if not isinstance(run, dict) or run.get("running") is not True:
        return False
    limit, peak = run.get("limit_bytes"), run.get("peak_bytes")
    if not (isinstance(limit, int) and isinstance(peak, int) and limit > 0):
        return False
    return peak >= NEAR_LIMIT * limit or int(run.get("oom_kills") or 0) > 0


def started(*, tick: Callable[[], object] | None = None) -> int | None:
    """Row-Bot starts on a server with a memory limit: notice whether the last run ran out of memory,
    mark this one as running, and watch its use (and run *tick*) every ``WATCH_SECONDS``.

    Returns the limit; None where there is none (the desktop), and then nothing is read or written.
    """
    global _thread, _peak, _kills, _killed
    limit = limit_bytes()
    if limit is None:
        return None
    with _lock:
        state = _read()
        last = state.get("run")
        if _ran_out(last):
            state["notice"] = {
                "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "limit_bytes": last["limit_bytes"],
                "peak_bytes": last["peak_bytes"],
            }
            logger.warning(
                "Row-Bot restarted after running out of memory (it had used %s of %s)",
                size_words(last["peak_bytes"]), size_words(last["limit_bytes"]),
            )
        _peak, _kills, _killed = working_set_bytes() or 0, _oom_kills(), 0
        state["run"] = {"running": True, "limit_bytes": limit, "peak_bytes": _peak, "oom_kills": 0}
        _write(state)
        if _thread is None or not _thread.is_alive():
            _stop.clear()
            _thread = threading.Thread(target=_watch, args=(tick,), name="row-bot-server-memory", daemon=True)
            _thread.start()
    return limit


def sample() -> None:
    """Keep this run's highest working set, and any process the kernel stopped for lack of memory, when
    either rose since the last look."""
    global _peak, _killed
    use = working_set_bytes() or 0
    kills = max(0, _oom_kills() - _kills)
    if use <= _peak and kills <= _killed:
        return
    with _lock:
        state = _read()
        run = state.get("run")
        if not isinstance(run, dict) or run.get("running") is not True:
            return
        _peak = run["peak_bytes"] = max(use, int(run.get("peak_bytes") or 0))
        _killed = run["oom_kills"] = max(kills, int(run.get("oom_kills") or 0))
        _write(state)


def _watch(tick: Callable[[], object] | None) -> None:
    while not _stop.wait(WATCH_SECONDS):
        for step in (sample, tick):
            if step is None:
                continue
            try:
                step()
            except Exception:
                logger.debug("A server memory check failed", exc_info=True)


def stopped() -> None:
    """Row-Bot shuts down on purpose: this run didn't run out of memory."""
    global _thread
    if _thread is None:  # Not watching: no limit (the desktop).
        return
    _stop.set()
    with _lock:
        _thread = None
        state = _read()
        run = state.get("run")
        if isinstance(run, dict) and run.get("running") is True:
            run["running"] = False
            _write(state)


def notice() -> dict[str, Any] | None:
    """The restart for lack of memory to tell the person about (U8), until they dismiss it.

    It goes by itself once the server has more memory than it had then: it was resized.
    """
    limit = limit_bytes()
    found = _read().get("notice") if limit is not None else None
    if not isinstance(found, dict) or not isinstance(found.get("limit_bytes"), int):
        return None
    return found if limit <= found["limit_bytes"] else None


def dismiss() -> bool:
    """The person read the notice: it stays away until the next restart for lack of memory."""
    with _lock:
        state = _read()
        if state.pop("notice", None) is None:
            return False
        _write(state)
    return True


__all__ = [
    "CGROUP_ROOT", "NEAR_LIMIT", "RESIZE_GUIDE", "SMALL_SERVER_BYTES", "dismiss", "limit_bytes", "notice",
    "sample", "size_words", "started", "stopped", "working_set_bytes",
]
