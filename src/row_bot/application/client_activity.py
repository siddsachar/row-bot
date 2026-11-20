"""Settings › System › Activity (D1): the activity log for the person, its Monitor line, export and clear.

Reads only: what the log already holds (``activity_log``), each row's conversation title from the
thread list, and nothing else. Export makes a CSV the person saves; Clear is theirs alone (a
mutation route, never a tool).
"""

from __future__ import annotations

import csv
import io
import sqlite3
from contextlib import closing
from datetime import datetime
from typing import Any

from row_bot import activity_log

MAX_PAGE = 200
MAX_EXPORT = 50_000
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
CSV_COLUMNS = ("Time", "Chat", "Where", "Tool", "App", "What", "Level", "Outcome", "Decided by", "Reason")

OUTCOME_WORDS = {
    "ran": "Ran", "asked": "Waiting for you", "approved_by_you": "You approved",
    "approved_by_reviewer": "Reviewer approved", "hands_off": "Hands-off", "allowed_by_rule": "Allowed by your rule",
    "allowed_in_chat": "Allowed in this chat", "denied": "You said no",
    "changed": "Changed after you approved — didn't run", "blocked": "Blocked", "failed": "Didn't work",
}
_UNANSWERED = "Not answered"
DECIDER_WORDS = {
    "none": "", "person": "You", "reviewer": "The reviewer", "hands_off": "Hands-off", "rule": "Your rule",
    "chat_grant": "Allowed in this chat", "block": "Block level", "policy": "Policy",
}
SURFACE_WORDS = {"chat": "Chat", "workflow": "Workflow", "agent": "Helper", "goal": "Goal", "channel": "Channel",
                 "proactive": "Proactive", "developer": "Developer"}
LEVEL_WORDS = {"block": "Block", "approve": "Ask", "auto": "Auto", "full": "Hands-off"}


def _when(value: float) -> str:
    return datetime.fromtimestamp(float(value)).astimezone().isoformat(timespec="seconds")


def _titles(conversation_ids: set[str]) -> dict[str, str]:
    ids = sorted(item for item in conversation_ids if item)
    if not ids:
        return {}
    try:
        from row_bot import threads

        threads._ensure_thread_db()
        with closing(sqlite3.connect(threads.DB_PATH)) as conn:
            rows = conn.execute(
                f"SELECT thread_id, name FROM thread_meta WHERE thread_id IN ({','.join('?' * len(ids))})", ids,
            ).fetchall()
    except Exception:
        return {}
    return {str(thread_id): str(name or "") for thread_id, name in rows}


def _entry(row: dict[str, Any], titles: dict[str, str]) -> dict[str, Any]:
    return {
        "entry_id": int(row["id"]),
        "at": _when(row["at"]),
        "conversation_id": str(row["conversation_id"] or "")[:256],
        "conversation_title": (f"Watch: {row['watch_label']}" if row.get("watch_label")
                               else titles.get(str(row["conversation_id"] or ""), ""))[:160],
        "surface": row["surface"] if row["surface"] in activity_log.SURFACES else "chat",
        "source_id": str(row["source_id"] or "")[:256],
        "tool": str(row["tool"] or "")[:180],
        # A title that is only the tool's own name is left to the client, which says it the way chat steps
        # do ("Read page", not "Read url") (W19).
        "title": "" if str(row["title"] or "") == activity_log.tool_words(str(row["tool"] or ""))
        else str(row["title"] or "")[:160],
        "app_id": str(row["app_id"] or "")[:256],
        "app_name": str(row["app_name"] or "")[:128],
        "summary": str(row["summary"] or "")[:activity_log.SUMMARY_MAX],
        "level": row["level"] if row["level"] in activity_log.LEVELS else "",
        "outcome": row["outcome"] if row["outcome"] in activity_log.OUTCOMES else "ran",
        "decided_by": row["decided_by"] if row["decided_by"] in activity_log.DECIDERS else "none",
        "reason": str(row["reason"] or "")[:activity_log.REASON_MAX],
        "risk": row["risk"] if row["risk"] in activity_log.RISKS else "unknown",
        "pending": row["status"] == "pending",
        "rule_id": str(row.get("rule_id") or "")[:32],
    }


def _filters(filters: dict[str, Any]) -> dict[str, Any]:
    return {key: filters.get(key) or "" for key in ("surface", "outcome", "conversation", "app", "q")} | {
        "since": filters.get("since") or None}


def read_activity(*, before: int = 0, limit: int = 50, **filters: Any) -> dict[str, Any]:
    """One page, newest first; ``next_before`` reads the older page (null at the end)."""

    rows, next_before = activity_log.query(before=before, limit=max(1, min(int(limit), MAX_PAGE)),
                                           **_filters(filters))
    rows = _watch_checks(rows)
    titles = _titles({str(row["conversation_id"] or "") for row in rows})
    return {"schema_version": 1, "items": [_entry(row, titles) for row in rows], "next_before": next_before}


def _watch_checks(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A judged watch's check runs in a hidden thread of its own (B4): its actions read "Watch: <label>" and
    link to the watch's chat, never as Proactive Row-Bot's."""
    try:
        from row_bot import waits

        owners = waits.check_thread_owners({str(row["conversation_id"] or "") for row in rows
                                            if row["surface"] == "proactive"})
    except Exception:
        return rows
    if not owners:
        return rows
    found = []
    for row in rows:
        owner = owners.get(str(row["conversation_id"] or "")) if row["surface"] == "proactive" else None
        found.append({**row, "conversation_id": owner[0], "surface": "chat", "watch_label": owner[1]}
                     if owner else row)
    return found


def read_summary(*, since: float) -> dict[str, Any]:
    """Monitor's line: actions since *since* (the person's midnight), how many asked them, how many they refused."""

    counts = activity_log.summary(since=since)
    return {"schema_version": 1, **counts}


def _cell(value: Any) -> str:
    text = str(value or "")
    # A spreadsheet runs a cell that starts like a formula: it's text here (CSV formula injection).
    return "'" + text if text.startswith(_FORMULA_START) else text


def export_csv(**filters: Any) -> bytes:
    """The filtered log as CSV, newest first, at most 50,000 rows."""

    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(CSV_COLUMNS)
    before, written = 0, 0
    while written < MAX_EXPORT:
        rows, before_next = activity_log.query(before=before, limit=min(MAX_PAGE * 5, MAX_EXPORT - written),
                                               **_filters(filters))
        rows = _watch_checks(rows)
        titles = _titles({str(row["conversation_id"] or "") for row in rows})
        for row in rows:
            entry = _entry(row, titles)
            outcome = OUTCOME_WORDS[entry["outcome"]]
            if entry["outcome"] == "asked" and not entry["pending"]:
                outcome = _UNANSWERED
            writer.writerow([_cell(item) for item in (
                entry["at"], entry["conversation_title"], SURFACE_WORDS[entry["surface"]],
                entry["title"] or activity_log.tool_words(entry["tool"]),
                entry["app_name"], entry["summary"], LEVEL_WORDS.get(entry["level"], ""), outcome,
                DECIDER_WORDS[entry["decided_by"]], entry["reason"],
            )])
        written += len(rows)
        if before_next is None:
            break
        before = before_next
    return ("﻿" + out.getvalue()).encode("utf-8")


def export_name() -> str:
    return f"row-bot-activity-{datetime.now().strftime('%Y-%m-%d')}.csv"


def clear_activity() -> dict[str, Any]:
    """The person's "Clear activity": every row goes."""

    return {"schema_version": 1, "removed": activity_log.clear()}
