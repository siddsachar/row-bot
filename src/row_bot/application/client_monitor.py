"""Passive, bounded Monitor projections for authenticated clients.

The projection reads canonical persisted status without importing background
workers, starting work, creating directories, or returning filesystem paths.
Raw logs remain a separate, explicit read.
"""

from __future__ import annotations

import json
import hashlib
import re
import sqlite3
import stat
from collections.abc import Callable
from contextlib import closing
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from row_bot.brand import STRUCTURED_LOG_FILENAME
from row_bot.data_paths import get_row_bot_data_dir
from row_bot.application.client_platform import ClientPlatformError
from row_bot.runtime import admissions


_JSON_BYTES = 512 * 1024
_LOG_BYTES = 8 * 1024 * 1024
_TEXT = 1024
Availability = Literal["available", "missing", "unavailable", "corrupt"]


def _bounded(value: Any, limit: int = _TEXT) -> str:
    return str(value if value is not None else "").replace("\0", "")[:limit]


def _number(value: Any, maximum: int = 2**31 - 1) -> int:
    return value if type(value) is int and 0 <= value <= maximum else 0


def _float(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return max(0.0, min(1.0, float(value)))
    return None


def _safe_file(root: Path, relative: str, maximum: int) -> tuple[Availability, bytes]:
    path = root / relative
    try:
        root_resolved = root.resolve()
        candidate = root
        for component in Path(relative).parts:
            candidate = candidate / component
            try:
                part_metadata = candidate.lstat()
            except FileNotFoundError:
                break
            if stat.S_ISLNK(part_metadata.st_mode) or getattr(
                part_metadata, "st_file_attributes", 0
            ) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
                return "unavailable", b""
        metadata = path.lstat()
    except FileNotFoundError:
        return "missing", b""
    except OSError:
        return "unavailable", b""
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or getattr(metadata, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        or metadata.st_size > maximum
    ):
        return "unavailable", b""
    try:
        if not path.resolve().is_relative_to(root_resolved):
            return "unavailable", b""
        data = path.read_bytes()
        return ("available", data) if len(data) <= maximum else ("unavailable", b"")
    except OSError:
        return "unavailable", b""


def _json(root: Path, relative: str, expected: type) -> tuple[Availability, Any]:
    availability, data = _safe_file(root, relative, _JSON_BYTES)
    if availability != "available":
        return availability, expected()
    try:
        value = json.loads(data.decode("utf-8") or ("[]" if expected is list else "{}"))
        if not isinstance(value, expected):
            raise ValueError
        return "available", value
    except (UnicodeError, json.JSONDecodeError, ValueError, RecursionError):
        return "corrupt", expected()


def _items(value: Any, limit: int) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value[-limit:] if isinstance(item, dict)]


def _extraction_entry(item: dict[str, Any]) -> dict[str, Any]:
    threads = []
    for value in _items(item.get("thread_details"), 50):
        threads.append(
            {
                "label": _bounded(value.get("thread") or value.get("thread_name"), 256),
                "extracted": _number(value.get("extracted")),
                "saved": _number(value.get("saved")),
            }
        )
    errors = [_bounded(value, 512) for value in (item.get("errors") or [])[:20]]
    return {
        "timestamp": _bounded(item.get("timestamp"), 128),
        "summary": _bounded(item.get("summary"), 512),
        "contradictions_blocked": _number(item.get("contradictions_blocked")),
        "low_confidence_skipped": _number(item.get("low_confidence_skipped")),
        "islands_repaired": _number(item.get("islands_repaired")),
        "threads": threads,
        "errors": errors,
    }


def _dream_entry(item: dict[str, Any]) -> dict[str, Any]:
    merges = [
        {
            "duplicate_subject": _bounded(value.get("duplicate_subject"), 256),
            "survivor_subject": _bounded(value.get("survivor_subject"), 256),
            "score": _float(value.get("score")),
        }
        for value in _items(item.get("merges"), 50)
    ]
    enrichments = [
        {
            "subject": _bounded(value.get("subject"), 256),
            "old_length": _number(value.get("old_length"), 1_000_000),
            "new_length": _number(value.get("new_length"), 1_000_000),
            "new_description": _bounded(value.get("new_description"), 512),
        }
        for value in _items(item.get("enrichments"), 50)
    ]
    inferred = [
        {
            "source_subject": _bounded(value.get("source_subject"), 256),
            "target_subject": _bounded(value.get("target_subject"), 256),
            "relation_type": _bounded(value.get("relation_type"), 64),
            "confidence": _float(value.get("confidence")),
            "evidence": _bounded(value.get("evidence"), 512),
        }
        for value in _items(item.get("inferred_relations"), 50)
    ]
    return {
        "timestamp": _bounded(item.get("timestamp"), 128),
        "summary": _bounded(item.get("summary"), 512),
        "merges": merges,
        "enrichments": enrichments,
        "inferred_relations": inferred,
        "errors": [_bounded(value, 512) for value in (item.get("errors") or [])[:20]],
    }


_CREDENTIAL = re.compile(
    r"(?i)\b(api[_ -]?key|authorization|bearer|password|secret|token|webhook)\b\s*[:=]?\s*\S*"
)
_WINDOWS_PATH = re.compile(r"(?i)\b[A-Z]:\\(?:[^\s\\]+\\)*[^\s]*")
_POSIX_PRIVATE = re.compile(r"(?i)(?:/Users/|/home/|/var/folders/)[^\s]+")
_SENSITIVE_DETAIL = re.compile(
    r"(?i)\b(prompt|channel content|message body|tool arguments?|request headers?|cookie)\b"
)


def _redact(value: Any, limit: int) -> str:
    text = _bounded(value, limit * 2)
    if _SENSITIVE_DETAIL.search(text):
        return "[sensitive log detail redacted]"
    text = _CREDENTIAL.sub("[credential redacted]", text)
    text = _WINDOWS_PATH.sub("[private path]", text)
    text = _POSIX_PRIVATE.sub("[private path]", text)
    return text[:limit]


def read_monitor_logs(*, limit: int = 15) -> dict[str, Any]:
    """Read a bounded redacted log tail; the route owns local authority."""
    if type(limit) is not int or not 1 <= limit <= 200:
        raise ValueError("invalid_monitor_query")
    root = get_row_bot_data_dir(create=False).absolute()
    availability, data = _safe_file(root, f"logs/{STRUCTURED_LOG_FILENAME}", _LOG_BYTES)
    if availability != "available":
        return {
            "availability": availability,
            "authorized": True,
            "entries": [],
            "full_available": False,
        }
    lines = data.splitlines()[-limit:]
    entries = []
    for line in reversed(lines):
        try:
            item = json.loads(line.decode("utf-8", errors="replace"))
        except (json.JSONDecodeError, UnicodeError):
            item = {"level": "?", "msg": line.decode("utf-8", errors="replace")}
        if not isinstance(item, dict):
            continue
        entries.append(
            {
                "timestamp": _bounded(item.get("ts"), 128),
                "level": _bounded(item.get("level"), 32) or "?",
                "logger": _redact(item.get("logger"), 128),
                "message": _redact(item.get("msg"), 512),
                "exception": _redact(item.get("exc"), 1024),
            }
        )
    return {
        "availability": "available",
        "authorized": True,
        "entries": entries,
        "full_available": bool(entries),
    }


def read_monitor_snapshot(*, include_logs: bool) -> dict[str, Any]:
    """Return a passive status/journal snapshot with independent availability."""
    root = get_row_bot_data_dir(create=False).absolute()
    extraction_availability, extraction_state = _json(
        root, "memory_extraction_state.json", dict
    )
    extraction_journal_availability, extraction_rows = _json(
        root, "extraction_journal.json", list
    )
    dream_config_availability, dream_config = _json(root, "dream_config.json", dict)
    dream_journal_availability, dream_rows = _json(root, "dream_journal.json", list)
    extraction_availability = (
        "available" if extraction_availability == "missing" else extraction_availability
    )
    dream_config_availability = (
        "available"
        if dream_config_availability == "missing"
        else dream_config_availability
    )
    extraction = {
        "availability": extraction_availability,
        "last_run": _bounded(extraction_state.get("last_extraction"), 128) or None,
        "interval_hours": 2,
        "threads_scanned": _number(extraction_state.get("threads_scanned")),
        "entities_saved": _number(extraction_state.get("entities_saved")),
        "islands_repaired": _number(extraction_state.get("islands_repaired")),
    }
    enabled = dream_config.get("enabled", True) is not False
    start = dream_config.get("window_start", 1)
    end = dream_config.get("window_end", 5)
    start = start if type(start) is int and 0 <= start <= 23 else 1
    end = end if type(end) is int and 0 <= end <= 23 and end != start else 5
    dream_entries = [_dream_entry(item) for item in reversed(_items(dream_rows, 20))]
    last = dream_entries[0] if dream_entries else None
    dream = {
        "availability": dream_config_availability
        if dream_journal_availability != "corrupt"
        else "corrupt",
        "enabled": enabled,
        "window": f"{start}:00 – {end}:00",
        "last_run": last["timestamp"] if last else None,
        "last_summary": last["summary"] if last else None,
        "recent": [
            {"timestamp": item["timestamp"], "summary": item["summary"]}
            for item in dream_entries[:3]
        ],
    }
    logs = (
        read_monitor_logs(limit=15)
        if include_logs
        else {
            "availability": "unavailable",
            "authorized": False,
            "entries": [],
            "full_available": False,
        }
    )
    result = {
        "schema_version": 1,
        "extraction": extraction,
        "extraction_journal": [
            _extraction_entry(item) for item in reversed(_items(extraction_rows, 20))
        ]
        if extraction_journal_availability != "corrupt"
        else [],
        "extraction_journal_availability": extraction_journal_availability,
        "dream": dream,
        "dream_journal": dream_entries,
        "dream_journal_availability": dream_journal_availability,
        "logs": logs,
    }
    result["dream_revision"] = _digest(
        {"dream": result["dream"], "journal": result["dream_journal"][:1]}
    )
    return result


# ── Attention (NiceGUI parity rows 12 and 13) ──────────────────────────────
# What needs the person, one list for the sidebar's indicator and Home's Needs
# you, so both count the same things (B383), and an update (it opens Updates).
# Quiet when everything is healthy. Reads are passive: Python state, saved
# workflows and Monitor's kept check results only, no probes, nothing started.

_MAX_PROBLEMS = 20
_MODULE = __import__("sys").modules


def _problem(problem_id: str, title: str, detail: str, place: str,
             fix: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": _bounded(problem_id, 64),
        "title": _bounded(title, 160),
        "detail": _bounded(detail, 512),
        "place": place,
        # Its one fix (Phase 18): restart the channel, or open the exact place.
        "fix": fix,
    }


def _open(href: str, name: str) -> dict[str, Any]:
    return {"kind": "open", "href": href, "target": None, "name": _bounded(name, 128)}


def _channel_problems() -> list[dict[str, str]]:
    """Channels set to start with Row-Bot that aren't running, or that run
    without being reachable (a stopped channel someone stopped is fine)."""
    registry = _MODULE.get("row_bot.channels.registry")
    config = _MODULE.get("row_bot.channels.config")
    if registry is None:
        return []
    problems = []
    try:
        channels = list(registry.all_channels())
    except Exception:
        return []
    for channel in channels:
        try:
            name = str(channel.name)
            label = str(getattr(channel, "display_name", name))
            if not channel.is_configured():
                continue
            reader = getattr(channel, "link_status", None)
            link = reader() if callable(reader) else None
            sheet = _open(f"/settings/apps/{name}", label)
            if isinstance(link, dict) and link.get("state") in {"starting", "scan"}:
                problems.append(_problem(f"channel:{name}", f"{label} is waiting for a scan",
                    "Scan its code on its page in Settings › Apps to link your phone.", "channels", sheet))
                continue
            if channel.is_running():
                check = getattr(channel, "reachability_problem", None)
                problem = check() if callable(check) else None
                if problem:
                    problems.append(_problem(f"channel:{name}", f"{label} can't be reached",
                                             str(problem), "channels", sheet))
                continue
            wanted = config.get(name, "auto_start", False) is True if config is not None else False
            if wanted:
                problems.append(_problem(f"channel:{name}", f"{label} stopped",
                    "It is set to start with Row-Bot but isn't running.", "channels",
                    {**sheet, "kind": "restart_channel", "target": name}))
        except Exception:
            continue
    return problems


def _tunnel_problems() -> list[dict[str, str]]:
    tunnel = _MODULE.get("row_bot.tunnel")
    if tunnel is None:
        return []
    try:
        status, detail = tunnel.tunnel_manager.status()
    except Exception:
        return []
    if status not in {"warn", "error"}:
        return []
    return [_problem("tunnel", "Your public tunnel isn't running", str(detail), "access",
                     _open("/settings/access#tunnel", "Public link"))]


def _plugin_problems() -> list[dict[str, str]]:
    loader = _MODULE.get("row_bot.plugins.loader")
    state = _MODULE.get("row_bot.plugins.state")
    if loader is None:
        return []
    problems = []
    try:
        results = list(loader.get_load_results())
    except Exception:
        return []
    for result in results:
        if result.success or getattr(result, "stale", False):
            continue
        try:
            if state is not None and not state.is_plugin_enabled(result.plugin_id):
                continue
        except Exception:
            pass
        from urllib.parse import urlencode
        problems.append(_problem(f"plugin:{result.plugin_id}", f"{result.plugin_id} didn't load",
            "Open it in Apps to see what it needs.", "plugins",
            _open("/settings/apps/item?" + urlencode({"id": "plugin:" + result.plugin_id}), "Apps")))
    return problems


def _sandbox_problems() -> list[dict[str, str]]:
    """A running sandbox with no model yet: its chats can't answer until the person signs it in once (W12)."""
    sandboxes = _MODULE.get("row_bot.sandboxes")
    if sandboxes is None:
        return []
    problems = []
    for sandbox in sandboxes.list_sandboxes():
        if sandbox.status != "running" or sandboxes.has_model(sandbox.sandbox_id):
            continue
        problems.append(_problem(f"sandbox:{sandbox.sandbox_id}", f"{sandbox.name} needs a model",
                                 "Open it and sign in to a provider once, so its chats can answer.", "data",
                                 _open("/settings/data#sandboxes", "Sandboxes")))
    return problems


def _mcp_problems() -> list[dict[str, str]]:
    runtime = _MODULE.get("row_bot.mcp_client.runtime")
    if runtime is None:
        return []
    try:
        status = runtime.get_status_summary()
    except Exception:
        return []
    enabled = int(status.get("enabled_server_count") or 0)
    # Only an app that tried and failed: one still starting or connecting (every app, just after Row-Bot
    # starts) isn't a problem yet (B400).
    failed = sum(1 for server in (status.get("servers") or {}).values()
                 if server.get("enabled") and server.get("status") in _MCP_FAILED)
    if not status.get("enabled") or not enabled or not failed:
        return []
    return [_problem("mcp", "An app isn't connected" if failed == 1 else f"{failed} apps aren't connected",
                     f"{failed} of {enabled} apps that are on couldn't connect.", "mcp",
                     _open("/settings/apps", "Apps"))]


_MCP_FAILED = {"failed", "error", "sdk_missing", "dependency_missing", "cleanup_incomplete"}


# An app that is on but can't be used until the person acts, in words.
_APP_NEEDS = {"needs_sign_in": "Sign in to {}", "needs_key": "{} needs its key",
              "attention": "{} needs attention"}
_APP_ID = re.compile(r"[A-Za-z0-9._:-]{1,120}")


def _app_problems() -> list[dict[str, Any]]:
    """Your apps that are on but signed out, missing a key or broken. Channels
    are left to their own reader."""
    facts = _MODULE.get("row_bot.integrations.facts")
    builtin = _MODULE.get("row_bot.integrations.builtin")
    if facts is None:
        return []
    rows, _errors = facts.inventory()
    if builtin is not None:
        rows = rows + [row for row in builtin.rows(wait=False)
                       if not str(row.get("owner_ref") or "").startswith("channel:")]
    problems = []
    for item in (item for row in rows for item in (row, *row.get("children", ()))):
        words = _APP_NEEDS.get(item.get("readiness") or "")
        if item.get("lifecycle") != "installed" or words is None:
            continue
        name = str((item.get("app") or {}).get("name") or item["name"])
        detail = next((str(b["message"]) for b in item.get("blockers", ()) if b.get("message")),
                      "It can't be used until this is fixed.")
        item_id = str(item["id"])
        # Its page with its fix (a sign-in, its key) started at once.
        href = f"/settings/apps/item?id={item_id}&fix=1" if _APP_ID.fullmatch(item_id) else "/settings/apps"
        problems.append(_problem(f"app:{item_id}", words.format(name), detail, "mcp", _open(href, name)))
    return problems


# A workflow whose newest run ended like this needs the person.
_FAILED_RUNS = {
    "failed": "{} failed",
    "blocked": "{} was blocked",
    "timed_out": "{} timed out",
    "completed_delivery_failed": "{} couldn't deliver its result",
}
_TASK_ID = re.compile(r"[A-Za-z0-9_-]{1,100}")


def _workflow_problems() -> list[dict[str, Any]]:
    """Workflows whose last run failed and that aren't running again."""
    tasks = _MODULE.get("row_bot.tasks")
    if tasks is None:
        return []
    problems = []
    for row in tasks.iter_task_summary_snapshot():
        words = _FAILED_RUNS.get(str(row.get("last_status") or "").lower())
        if words is None or row.get("active_run_json"):
            continue
        task_id, name = str(row["id"]), str(row.get("name") or "A workflow")
        href = f"/?tab=workflows&workflow={task_id}" if _TASK_ID.fullmatch(task_id) else "/?tab=workflows"
        blocked = str(row.get("last_status") or "").lower() == "blocked"
        if blocked and _TASK_ID.fullmatch(task_id) and tasks.newest_run_fix(task_id) == "choose_folder":
            # Its code folder was removed (N9): the fix is choosing another, in its settings.
            problems.append(_problem(
                f"workflow:{task_id}", f"{name} needs a code folder",
                "Its code folder isn't a code folder any more, so it didn't run. Choose another.", "workflows",
                {"kind": "choose_folder", "href": f"{href}&fix=folder", "target": None, "name": _bounded(name, 128)}))
            continue
        problems.append(_problem(f"workflow:{task_id}", words.format(name),
                                 "Run it again, or open it to see why.", "workflows", _open(href, name)))
    return problems


def _available_update() -> Any:
    updater = _MODULE.get("row_bot.updater")
    if updater is None:
        return None
    try:
        return updater.get_update_state().available
    except Exception:
        return None


def _ready_update() -> str | None:
    """A version prepared in the background that a restart installs."""
    updater = _MODULE.get("row_bot.updater")
    if updater is None:
        return None
    try:
        return updater.ready_update()
    except Exception:
        return None


def _health_problems() -> list[dict[str, str]]:
    """Monitor checks whose kept result is an error (B252): a file read."""
    from row_bot.application.client_diagnosis import attention_problems

    return attention_problems()


def _memory_problems() -> list[dict[str, Any]]:
    """Row-Bot restarted because the server ran out of memory (H12, U8): its size, what needs more, and
    how to resize, with the host's own page when a known host runs it. Only where there is a limit."""
    from row_bot import server_memory
    from row_bot.hosting import hosted_by

    notice = server_memory.notice()
    if notice is None:
        return []
    host = hosted_by()
    size = notice["limit_bytes"]
    target = "4 GB" if size < server_memory.SMALL_SERVER_BYTES else "bigger"
    resize = f"Make the pod {target} in {host.name}" if host else (
        "Give the server 4 GB" if target == "4 GB" else "Give the server more memory")
    links = [{"label": "How to resize", "url": server_memory.RESIZE_GUIDE}]
    if host is not None and host.manage_url:
        links.append({"label": f"Manage your pod in {host.name}", "url": host.manage_url})
    return [{
        **_problem("memory:restarted", "Row-Bot restarted because the server ran out of memory",
                   f"Your server has {server_memory.size_words(size)}. Local speech-to-text together with the "
                   f"search model and browser needs about 3.2 GB. {resize}, or use cloud speech-to-text.",
                   "memory"),
        "links": links,
    }]


def read_attention(*, include_update: bool) -> dict[str, Any]:
    """What needs the person now, for the sidebar's one indicator."""
    problems: list[dict[str, Any]] = []
    for reader in (_memory_problems, _channel_problems, _tunnel_problems, _plugin_problems,
                   _app_problems, _mcp_problems, _workflow_problems, _sandbox_problems, _health_problems):
        try:
            found = reader()
        except Exception:
            continue
        # The apps that need attention, by name, say more than "An app isn't connected".
        if reader is _mcp_problems and any(p["id"].startswith("app:") for p in problems):
            continue
        problems.extend(found)
    update = None
    if include_update:
        ready = _bounded(_ready_update() or "", 64)
        available = _available_update()
        version = ready or (_bounded(getattr(available, "version", ""), 64) if available else "")
        update = {"version": version, "ready": bool(ready)} if version else None
    return {"schema_version": 1, "problems": problems[:_MAX_PROBLEMS], "update": update}


_MAX_APPROVALS = 50


def _conversation_titles(conversation_ids: set[str]) -> dict[str, str]:
    if not conversation_ids:
        return {}
    from row_bot import threads

    threads._ensure_thread_db()
    placeholders = ",".join("?" for _ in conversation_ids)
    with closing(sqlite3.connect(threads.DB_PATH)) as conn:
        rows = conn.execute(
            f"SELECT thread_id, name FROM thread_meta WHERE thread_id IN ({placeholders})",
            sorted(conversation_ids),
        ).fetchall()
    return {str(thread_id): str(name or "") for thread_id, name in rows}


def read_pending_approvals() -> dict[str, Any]:
    """Every approval waiting for the person, from a workflow, a conversation
    or a delegated agent, newest first (B255).

    Only what the list shows and the ids to answer or open one: never a resume
    token or the stored action. An expired approval can no longer be answered,
    so it is left out.
    """
    from row_bot.tasks import get_pending_approvals

    now = datetime.now().isoformat()
    rows = [row for row in get_pending_approvals()
            if not row.get("timeout_at") or row["timeout_at"] >= now]
    shown = rows[:_MAX_APPROVALS]
    titles = _conversation_titles({
        str(row.get("source_thread_id") or row.get("parent_thread_id") or "")
        for row in shown if row.get("resume_kind") in {"conversation", "parent_orchestration", "mcp_app"}
    } - {""})
    items = []
    for row in shown:
        kind = str(row.get("resume_kind") or "")
        conversation_id = str(row.get("source_thread_id") or row.get("parent_thread_id") or "") or None
        # A helper's approval also belongs to the chat that started it (B348).
        parent_id = str(row.get("parent_thread_id") or "") if kind == "agent_run" else ""
        task_id = None
        if kind in {"conversation", "mcp_app"}:  # A view's call waits in its chat.
            source, title = "conversation", titles.get(conversation_id or "") or "Untitled conversation"
        elif kind == "agent_run":
            source, title = "agent", str(row.get("source_label") or "") or "Agent"
        elif kind == "parent_orchestration":
            source, title = "agent", titles.get(conversation_id or "") or "Agent"
        else:
            source, title = "workflow", str(row.get("task_name") or "") or "Workflow"
            conversation_id, task_id = None, str(row.get("task_id") or "") or None
        from row_bot.questions import stored_question
        goes_out = _goes_out(row.get("approval_payload_json"))
        items.append({
            "id": str(row["id"]),
            "source": source,
            # Row-Bot's question (N4) is answered on its card, not approved.
            "kind": "question" if stored_question(row.get("approval_payload_json")) is not None else "approval",
            "title": _bounded(title, 160),
            "what": _bounded(str(row.get("message") or "").strip(), 512),
            "requested_at": _bounded(row.get("requested_at"), 80),
            "expires_at": _bounded(row["timeout_at"], 80) if row.get("timeout_at") else None,
            "conversation_id": conversation_id,
            "parent_conversation_id": parent_id if parent_id and parent_id != conversation_id else None,
            "task_id": task_id,
            **({"goes_out": goes_out} if goes_out else {}),
        })
    return {"schema_version": 1, "items": items, "total": len(rows)}


_EMAIL_TOOLS = frozenset({"send_gmail_message", "outlook_send_mail", "outlook_reply_mail", "outlook_forward_mail"})


def _goes_out(raw: Any) -> str:
    """"post" or "email" when the approval sends one (Home's Review shows it in full, G3); else ""."""
    try:
        context = json.loads(str(raw or "{}"))
    except (TypeError, ValueError):
        return ""
    interrupt = context.get("interrupt") if isinstance(context, dict) else None
    items = interrupt if isinstance(interrupt, list) else [interrupt]
    if len(items) != 1 or not isinstance(items[0], dict):
        return ""
    tool = str(items[0].get("tool") or "")
    if tool == "x_post" and isinstance(items[0].get("x_post"), dict) and items[0]["x_post"].get("action") != "delete":
        return "post"
    return "email" if tool in _EMAIL_TOOLS else ""


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def _dream_revision() -> str:
    return read_monitor_snapshot(include_logs=False)["dream_revision"]


def review_dream_run(
    snapshot_revision: str, *, validate: Callable[[], None]
) -> dict[str, Any]:
    """Review one explicit Dream run against the current persisted state."""
    validate()
    current = _dream_revision()
    if snapshot_revision != current:
        raise ClientPlatformError("dream_changed")
    snapshot = read_monitor_snapshot(include_logs=False)
    if snapshot["dream"]["availability"] != "available":
        raise ClientPlatformError("dream_unavailable")
    if not snapshot["dream"]["enabled"]:
        raise ClientPlatformError("dream_disabled")
    review = {
        "schema_version": 1,
        "snapshot_revision": current,
        "effects": ["knowledge_entities", "knowledge_relations", "dream_journal"],
    }
    review["action_digest"] = _digest(review)
    validate()
    return review


def _command_id(value: Any) -> str:
    try:
        if not isinstance(value, str) or str(UUID(value)) != value:
            raise ValueError
        return value
    except (TypeError, ValueError, AttributeError):
        raise ClientPlatformError("invalid_dream_command") from None


def _public_dream_receipt(value: dict[str, Any]) -> dict[str, Any]:
    status = value.get("status")
    if status not in {"completed", "partial", "rejected"}:
        raise ClientPlatformError("dream_operation_unavailable")
    return {
        "command_id": _command_id(value.get("command_id")),
        "status": status,
        "code": value.get("code")
        if value.get("code")
        in {None, "dream_outcome_uncertain", "dream_failed", "dream_disabled"}
        else "dream_failed",
        "summary": _bounded(value.get("summary"), 512),
        "merges": _number(value.get("merges")),
        "enrichments": _number(value.get("enrichments")),
        "inferred_relations": _number(value.get("inferred_relations")),
        "errors": _number(value.get("errors")),
    }


def read_dream_command(
    *, owner_id: str, command_id: str, validate: Callable[[], None]
) -> dict[str, Any]:
    validate()
    command_id = _command_id(command_id)
    metadata = admissions.read_command_metadata(owner_id, command_id)
    saved = admissions.read_command_receipt(owner_id, command_id)
    if (
        not metadata
        or metadata.get("target") != "monitor:dream"
        or metadata.get("type") != "dream.run"
        or not saved
    ):
        raise ClientPlatformError("dream_operation_unavailable")
    if saved.get("status") not in {"completed", "partial", "rejected"}:
        saved = {
            "command_id": command_id,
            "status": "partial",
            "code": "dream_outcome_uncertain",
        }
    validate()
    return _public_dream_receipt(saved)


def execute_dream_command(
    command: dict[str, Any],
    *,
    owner_id: str,
    key: str,
    validate: Callable[[], None],
    validate_review: Callable[[dict[str, Any], dict[str, Any]], None],
) -> dict[str, Any]:
    """Execute one reviewed, idempotent Dream run through canonical ownership."""
    validate()
    command = deepcopy(command)
    command_id = _command_id(command.get("command_id"))
    if command.get("type") != "dream.run" or not isinstance(
        command.get("payload"), dict
    ):
        raise ClientPlatformError("invalid_dream_command")
    payload = command["payload"]
    if set(payload) != {"snapshot_revision", "action_digest", "review_id"}:
        raise ClientPlatformError("invalid_dream_command")
    if admissions.read_command_metadata(owner_id, command_id) is not None:
        try:
            admissions.claim_command(owner_id, key, command, "monitor:dream")
        except admissions.AdmissionError as error:
            if str(error) != "operation_uncertain":
                raise ClientPlatformError(str(error)) from error
        return read_dream_command(
            owner_id=owner_id, command_id=command_id, validate=validate
        )
    review = review_dream_run(payload["snapshot_revision"], validate=validate)
    if payload.get("action_digest") != review["action_digest"]:
        raise ClientPlatformError("dream_changed")
    validate_review(command, review)
    initial = {
        "command_id": command_id,
        "status": "partial",
        "code": "dream_outcome_uncertain",
        "summary": "",
        "merges": 0,
        "enrichments": 0,
        "inferred_relations": 0,
        "errors": 0,
    }
    try:
        replay = admissions.claim_command(
            owner_id,
            key,
            command,
            "monitor:dream",
            exclusive_target=True,
            initial_result=initial,
        )
    except admissions.AdmissionError as error:
        raise ClientPlatformError(str(error)) from error
    if replay is not None:
        return _public_dream_receipt(replay)
    try:
        from row_bot.dream_cycle import run_dream_cycle

        validate()
        outcome = run_dream_cycle()
        validate()
        errors = outcome.get("errors") if isinstance(outcome, dict) else []
        result = {
            "command_id": command_id,
            "status": "partial" if errors else "completed",
            "code": "dream_failed" if errors else None,
            "summary": _bounded(
                outcome.get("summary") if isinstance(outcome, dict) else "", 512
            ),
            "merges": len(outcome.get("merges") or [])
            if isinstance(outcome, dict)
            else 0,
            "enrichments": len(outcome.get("enrichments") or [])
            if isinstance(outcome, dict)
            else 0,
            "inferred_relations": len(outcome.get("inferred_relations") or [])
            if isinstance(outcome, dict)
            else 0,
            "errors": len(errors or []),
        }
    except Exception:
        result = {**initial, "status": "partial", "code": "dream_failed"}
    admissions.complete_command(owner_id, key, result)
    validate()
    return _public_dream_receipt(result)
