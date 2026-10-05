"""Passive, bounded Monitor projections for authenticated clients.

The projection reads canonical persisted status without importing background
workers, starting work, creating directories, or returning filesystem paths.
Raw logs remain a separately authorized local-owner read.
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
# One sidebar indicator: problems that need the person (it opens Monitor) and
# an update (it opens Updates). Quiet when everything is healthy. Reads are
# passive: Python state and Monitor's kept check results only, no probes,
# nothing started.

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


def _mcp_problems() -> list[dict[str, str]]:
    runtime = _MODULE.get("row_bot.mcp_client.runtime")
    if runtime is None:
        return []
    try:
        status = runtime.get_status_summary()
    except Exception:
        return []
    enabled = int(status.get("enabled_server_count") or 0)
    connected = int(status.get("connected_server_count") or 0)
    if not status.get("enabled") or not enabled or connected >= enabled:
        return []
    return [_problem("mcp", "An app isn't connected",
                     f"{connected} of {enabled} apps that are on are connected.", "mcp",
                     _open("/settings/apps", "Apps"))]


def _available_update() -> Any:
    updater = _MODULE.get("row_bot.updater")
    if updater is None:
        return None
    try:
        return updater.get_update_state().available
    except Exception:
        return None


def _health_problems() -> list[dict[str, str]]:
    """Monitor checks whose kept result is an error (B252): a file read."""
    from row_bot.application.client_diagnosis import attention_problems

    return attention_problems()


def read_attention(*, include_update: bool) -> dict[str, Any]:
    """What needs the person now, for the sidebar's one indicator."""
    problems: list[dict[str, str]] = []
    for reader in (_channel_problems, _tunnel_problems, _plugin_problems,
                   _mcp_problems, _health_problems):
        try:
            problems.extend(reader())
        except Exception:
            continue
    update = None
    if include_update:
        available = _available_update()
        version = _bounded(getattr(available, "version", ""), 64) if available else ""
        update = {"version": version} if version else None
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
        for row in shown if row.get("resume_kind") in {"conversation", "parent_orchestration"}
    } - {""})
    items = []
    for row in shown:
        kind = str(row.get("resume_kind") or "")
        conversation_id = str(row.get("source_thread_id") or row.get("parent_thread_id") or "") or None
        task_id = None
        if kind == "conversation":
            source, title = "conversation", titles.get(conversation_id or "") or "Untitled conversation"
        elif kind == "agent_run":
            source, title = "agent", str(row.get("source_label") or "") or "Agent"
        elif kind == "parent_orchestration":
            source, title = "agent", titles.get(conversation_id or "") or "Agent"
        else:
            source, title = "workflow", str(row.get("task_name") or "") or "Workflow"
            conversation_id, task_id = None, str(row.get("task_id") or "") or None
        items.append({
            "id": str(row["id"]),
            "source": source,
            "title": _bounded(title, 160),
            "what": _bounded(str(row.get("message") or "").strip(), 512),
            "requested_at": _bounded(row.get("requested_at"), 80),
            "expires_at": _bounded(row["timeout_at"], 80) if row.get("timeout_at") else None,
            "conversation_id": conversation_id,
            "task_id": task_id,
        })
    return {"schema_version": 1, "items": items, "total": len(rows)}


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
