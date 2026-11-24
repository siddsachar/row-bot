"""Private bindings (generative UI, G4): a surface's data filled in on this computer.

A surface may name sources instead of writing values: ``"sources": {"/tasks": {"@call": "rowbot.tasks"}}``.
The model writes only the call; Row-Bot reads the values here, when the person's client shows the surface,
and sends them to that client alone. They never enter the conversation, so a cloud model designs the
interface without seeing the person's data.

Every source is local and read-only: Row-Bot's own tasks, a memory search, a file or folder in the
workspace (never Row-Bot's data folder), the time. A source's arguments come from the model, so each one
checks and bounds them; nothing here writes, sends or fetches anything.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

_LOG = logging.getLogger(__name__)

MAX_FILE_BYTES = 512 * 1024
MAX_ROWS = 500
MAX_VALUE_CHARS = 256 * 1024
CACHE_SECONDS = 30.0

_NUMBER = re.compile(r"^[-+]?[£$€¥₹]?\s?[-+]?\d{1,3}(?:,\d{3})*(?:\.\d+)?$|^[-+]?[£$€¥₹]?\s?[-+]?\d+(?:\.\d+)?$")


class SourceProblem(Exception):
    """A source couldn't be read; its message is shown to the person."""


def _int(args: dict[str, Any], key: str, default: int, top: int) -> int:
    value = args.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return max(1, min(int(value), top))


def _now(_args: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now().astimezone()
    return {"iso": now.isoformat(timespec="seconds"), "date": now.date().isoformat(),
            "time": now.strftime("%H:%M"), "weekday": now.strftime("%A")}


def _tasks(args: dict[str, Any]) -> list[dict[str, Any]]:
    from row_bot import tasks

    found = []
    for task in tasks.list_tasks():
        if args.get("enabled_only") is True and not task.get("enabled"):
            continue
        found.append({
            "name": str(task.get("name") or ""), "icon": str(task.get("icon") or ""),
            "schedule": str(task.get("schedule") or task.get("at") or ""),
            "enabled": bool(task.get("enabled")), "last_run": str(task.get("last_run") or ""),
            "last_status": str(task.get("last_status") or ""),
        })
    return found[:_int(args, "limit", 20, 50)]


def _memory(args: dict[str, Any]) -> list[dict[str, Any]]:
    from row_bot import knowledge_graph

    query = str(args.get("query") or "").strip()[:200]
    if not query:
        raise SourceProblem("A memory search needs words to look for.")
    kind = str(args.get("type") or "").strip()[:40] or None
    return [
        {"subject": str(entity.get("subject") or ""), "type": str(entity.get("entity_type") or ""),
         "description": str(entity.get("description") or "")[:500]}
        for entity in knowledge_graph.search_entities(query, kind, _int(args, "limit", 10, 20))
    ]


def _workspace_path(value: Any, *, folder: bool) -> Path:
    """A path inside the workspace the file tools use, never Row-Bot's own data."""

    from row_bot.approval_rules import workspace_root
    from row_bot.data_paths import is_private_data_path
    from row_bot.tools.filesystem_tool import _absolute, _within

    root = workspace_root()
    if not root:
        raise SourceProblem("There is no workspace folder to read from.")
    given = str(value or "").strip()
    if not given and not folder:
        raise SourceProblem("No file was named.")
    path = _absolute(given or ".", root)
    if not _within(path, root) or is_private_data_path(path):
        raise SourceProblem("That isn't in your workspace folder.")
    if folder and not path.is_dir():
        raise SourceProblem("That folder isn't there.")
    if not folder and not path.is_file():
        raise SourceProblem("That file isn't there.")
    return path


def _cell(value: str) -> Any:
    text = value.strip()
    if _NUMBER.match(text):
        try:
            return float(re.sub(r"[£$€¥₹,\s]", "", text))
        except ValueError:
            return text
    return text


def _file(args: dict[str, Any]) -> Any:
    path = _workspace_path(args.get("path"), folder=False)
    if path.stat().st_size > MAX_FILE_BYTES:
        raise SourceProblem("That file is too large to show here.")
    kind = str(args.get("format") or path.suffix.lstrip(".")).lower()
    try:
        content = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        raise SourceProblem("That file couldn't be read as text.") from None
    if kind in {"csv", "tsv"}:
        reader = csv.DictReader(io.StringIO(content), delimiter="\t" if kind == "tsv" else ",")
        rows = []
        for row in reader:
            rows.append({str(key or "").strip()[:64]: _cell(str(value or "")) for key, value in row.items() if key})
            if len(rows) >= MAX_ROWS:
                break
        return rows
    if kind == "json":
        try:
            return json.loads(content)
        except ValueError:
            raise SourceProblem("That file isn't valid JSON.") from None
    return content[:MAX_VALUE_CHARS]


def _files(args: dict[str, Any]) -> list[dict[str, Any]]:
    folder = _workspace_path(args.get("folder"), folder=True)
    found = []
    for entry in sorted(folder.iterdir(), key=lambda item: item.name.lower()):
        if entry.name.startswith("."):
            continue
        try:
            stat = entry.stat()
        except OSError:
            continue
        found.append({"name": entry.name, "kind": "folder" if entry.is_dir() else "file",
                      "size": 0 if entry.is_dir() else stat.st_size,
                      "modified": datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec="seconds")})
        if len(found) >= _int(args, "limit", 50, 100):
            break
    return found


# name -> (reader, what it reads, in the person's words)
SOURCES: dict[str, tuple[Callable[[dict[str, Any]], Any], str]] = {
    "rowbot.now": (_now, "The time"),
    "rowbot.tasks": (_tasks, "Your tasks"),
    "rowbot.memory": (_memory, "Your memory"),
    "rowbot.file": (_file, "A file in your workspace"),
    "rowbot.files": (_files, "A folder in your workspace"),
}

_cache: dict[tuple[str, str, str], tuple[float, dict[str, Any]]] = {}
_cache_lock = threading.Lock()


def describe(sources: dict[str, Any]) -> list[str]:
    """What a surface's sources read, in the person's words, each once."""

    seen: list[str] = []
    for call in sources.values():
        words = SOURCES.get(call.get("@call"), (None, ""))[1]
        if call.get("@call") == "rowbot.file" and call.get("args", {}).get("path"):
            words = f"Your file {Path(str(call['args']['path'])).name}"
        if words and words not in seen:
            seen.append(words)
    return seen


def resolve(sources: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """Each source's value by its data path, and what went wrong with any that couldn't be read."""

    values: dict[str, Any] = {}
    problems: dict[str, str] = {}
    for path, call in sources.items():
        reader = SOURCES.get(call.get("@call"))
        if reader is None:
            problems[path] = "Row-Bot can't read that."
            continue
        try:
            value = reader[0](dict(call.get("args") or {}))
        except SourceProblem as problem:
            problems[path] = str(problem)
            continue
        except Exception:  # noqa: BLE001 -- a source that fails shows as unavailable, never breaks the chat.
            _LOG.warning("A surface source failed: %s", call.get("@call"), exc_info=True)
            problems[path] = "That couldn't be read just now."
            continue
        if len(json.dumps(value, ensure_ascii=False, default=str)) > MAX_VALUE_CHARS:
            problems[path] = "That is too much to show here."
            continue
        values[path] = value
    return values, problems


def surface_data(service: Any, conversation_id: str, message_id: str, surface_id: str,
                 refresh: bool = False) -> dict[str, Any]:
    """The values of a surface's sources, for the person's client only (never the conversation)."""

    from row_bot.application.client_platform import ClientPlatformError
    from row_bot.application.conversation_search import _require_readable
    from row_bot.application.surfaces import reply_surface

    _require_readable(service, conversation_id)
    key = (conversation_id, message_id, surface_id)
    with _cache_lock:
        cached = _cache.get(key)
    if cached and not refresh and time.monotonic() - cached[0] < CACHE_SECONDS:
        return cached[1]
    surface = reply_surface(conversation_id, message_id, surface_id)
    if surface is None:
        raise ClientPlatformError("invalid_command")
    sources = surface.get("sources") or {}
    values, problems = resolve(sources)
    result = {"surface_id": surface_id, "values": values, "problems": problems,
              "read_at": datetime.now().astimezone().isoformat(timespec="seconds")}
    with _cache_lock:
        _cache[key] = (time.monotonic(), result)
        if len(_cache) > 256:
            _cache.pop(next(iter(_cache)))
    return result
