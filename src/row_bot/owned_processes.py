"""Programs Row-Bot starts (an ngrok agent, a local app's server), recorded so a later Row-Bot can stop
the ones it provably owned after a crash or a forced stop.

A ledger (``<data>/runtime/<file>``) holds each program's pid, creation time and name, and the pid and
creation time of the Row-Bot process that started it. ``cleanup`` stops a recorded program (with its
process tree when asked) only when its Row-Bot has ended (gone, or its pid now belongs to another
process) and the pid still has the recorded creation time and name. Anything else (a reused pid, a
program that already ended, one whose Row-Bot still runs) is left alone; stale records are dropped.
"""
from __future__ import annotations

from collections.abc import Iterable
import json
import logging
import os
from pathlib import Path
import threading

logger = logging.getLogger(__name__)
CREATE_TIME_SLACK = 2.0
_lock = threading.Lock()
_FIELDS = ("pid", "created", "owner_pid", "owner_created")


def ledger_path(file: str) -> Path:
    from row_bot.data_paths import get_row_bot_data_dir
    return get_row_bot_data_dir(create=False) / "runtime" / file


def _read(path: Path) -> list[dict]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    entries = value.get("agents") if isinstance(value, dict) else None
    return [dict(item) for item in entries or []
            if isinstance(item, dict) and all(isinstance(item.get(key), (int, float)) for key in _FIELDS)]


def _write(path: Path, entries: list[dict]) -> None:
    from row_bot.integrations.safe import write_atomic
    if entries:
        write_atomic(path, json.dumps({"agents": entries}, indent=2))
    else:
        path.unlink(missing_ok=True)


def started(pid: int) -> float | None:
    """Creation time of a running process, or None when it is gone."""
    try:
        import psutil

        return float(psutil.Process(int(pid)).create_time())
    except Exception:
        return None


def same_process(pid: int, created: float) -> bool:
    found = started(pid)
    return found is not None and abs(found - float(created)) <= CREATE_TIME_SLACK


def _name(pid: int) -> str:
    try:
        import psutil

        return str(psutil.Process(int(pid)).name())
    except Exception:
        return ""


def record(path: Path, pid: int, **labels: str) -> bool:
    """Remember a program this process started (``labels``: what it is, for the logs). One whose creation
    time or name cannot be read is not recorded: cleanup could not tell it from a later program."""
    created, owner_created, name = started(pid), started(os.getpid()), _name(pid)
    if not created or owner_created is None or not name:
        return False
    with _lock:
        entries = [entry for entry in _read(path) if int(entry["pid"]) != int(pid)]
        entries.append({"pid": int(pid), "created": created, "name": name, "owner_pid": os.getpid(),
                        "owner_created": owner_created, **{key: str(value)[:128] for key, value in labels.items()}})
        _write(path, entries)
    return True


def forget(path: Path, pid: int | None = None) -> None:
    """Drop this process's record of ``pid`` (or all of its records) once that program has ended."""
    with _lock:
        _write(path, [entry for entry in _read(path) if int(entry["owner_pid"]) != os.getpid()
                      or (pid is not None and int(entry["pid"]) != int(pid))])


def cleanup(path: Path, *, dead_owner: int | Iterable[int] | None = None, name: str = "", tree: bool = False) -> int:
    """Stop recorded programs whose Row-Bot process has ended; returns how many were stopped.

    ``dead_owner`` is a server process (or tree) the launcher has just stopped by force, which may still
    look alive. ``name`` also requires the program's name to contain it (``ngrok``); a recorded name must
    match either way. A record with neither a recorded name nor ``name`` to check, or without a creation
    time, is dropped and never acted on. ``tree`` stops the program's own child processes too."""
    dead = {dead_owner} if isinstance(dead_owner, int) else set(dead_owner or ())
    stopped = 0
    with _lock:
        kept = []
        for entry in _read(path):
            owner = int(entry["owner_pid"])
            if owner == os.getpid() or (owner not in dead and same_process(owner, float(entry["owner_created"]))):
                kept.append(entry)
                continue
            if _stop(int(entry["pid"]), float(entry["created"]), str(entry.get("name") or ""), name, tree):
                stopped += 1
        _write(path, kept)
    return stopped


def _stop(pid: int, created: float, recorded: str, name: str, tree: bool) -> bool:
    if not created or not (recorded or name):
        return False  # No name to check (5.0.0's ngrok records have none, but ngrok's cleanup names it).
    try:
        import psutil

        process = psutil.Process(pid)
        if abs(float(process.create_time()) - created) > CREATE_TIME_SLACK:
            return False  # The pid belongs to another program now.
        actual = process.name()
        if (recorded and actual.lower() != recorded.lower()) or (name and name not in actual.lower()):
            return False
        family = []
        if tree:
            try:
                family = list(process.children(recursive=True))
            except Exception:
                family = []
        for member in [process, *family]:
            try:
                member.terminate()
            except Exception:
                continue
        for member in [process, *family]:
            try:
                member.wait(timeout=3)
            except psutil.TimeoutExpired:
                member.kill()
                member.wait(timeout=3)
            except Exception:
                continue
        return True
    except Exception:
        return False
