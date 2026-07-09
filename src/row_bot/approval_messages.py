from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

_SECRET_VALUE_RE = re.compile(
    r"(?i)(api[_-]?key|token|secret|password|passwd|authorization)\s*[:=]\s*([^\s,;]+)"
)


def _clean_text(value: object) -> str:
    return " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split())


def _truncate(text: str, max_chars: int) -> str:
    clean = _clean_text(text)
    if len(clean) <= max_chars:
        return clean
    return clean[: max(0, max_chars - 3)].rstrip() + "..."


def _redact(text: str) -> str:
    return _SECRET_VALUE_RE.sub(lambda m: f"{m.group(1)}=<redacted>", str(text or ""))


def sanitize_approval_reason(value: object, *, max_chars: int = 180) -> str:
    """Return compact, display-safe model-authored approval copy."""

    return _truncate(_redact(value), max_chars)


def _first_mapping(value: object) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if isinstance(value, list):
        for item in value:
            if isinstance(item, Mapping):
                return item
    return {}


def _interrupt_items(interrupts: object) -> list[dict[str, Any]]:
    items = interrupts if isinstance(interrupts, list) else [interrupts]
    result: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, Mapping):
            result.append(dict(item))
        elif item is not None:
            result.append({"description": str(item)})
    return result


def _tool_action(tool: str, label: str) -> str:
    tool_name = str(tool or "").strip()
    label_text = str(label or "").strip()
    if tool_name in {"run_command", "shell"}:
        return "run a command"
    if tool_name == "developer_run_command":
        return "run a Developer command"
    if tool_name in {"developer_apply_patch"}:
        return "apply a patch"
    if tool_name in {"developer_write_file"}:
        return "write a file"
    if tool_name in {
        "developer_create_branch",
        "developer_switch_branch",
        "developer_commit_changes",
        "developer_push_current_branch",
        "developer_fast_forward_merge",
    }:
        return "update Git"
    if label_text:
        lowered = label_text[:1].lower() + label_text[1:]
        return lowered
    return "continue"


def _reason_from_item(item: Mapping[str, Any]) -> str:
    for key in ("approval_reason", "reason", "summary"):
        reason = sanitize_approval_reason(item.get(key))
        if reason:
            return reason
    args = item.get("args")
    if isinstance(args, Mapping):
        for key in ("approval_reason", "reason", "summary"):
            reason = sanitize_approval_reason(args.get(key))
            if reason:
                return reason
    desc = sanitize_approval_reason(item.get("description"), max_chars=220)
    if desc:
        return desc
    return ""


def _raw_action_from_item(item: Mapping[str, Any]) -> str:
    args = item.get("args")
    if isinstance(args, Mapping):
        command = args.get("command")
        if command:
            return _truncate(_redact(command), 500)
        path = args.get("path")
        if path:
            return _truncate(_redact(path), 500)
        branch = args.get("branch_name")
        if branch:
            return _truncate(_redact(branch), 500)
        message = args.get("message")
        if message:
            return _truncate(_redact(message), 500)
        summary = args.get("summary")
        if summary:
            return _truncate(_redact(summary), 500)
    desc = item.get("description")
    if desc:
        return _truncate(_redact(desc), 500)
    return ""


def normalize_interrupts(
    interrupts: object,
    *,
    source_label: str = "",
    agent_run_id: str = "",
    parent_thread_id: str = "",
) -> dict[str, Any]:
    """Normalize LangGraph interrupt payloads for durable approval display."""

    items = _interrupt_items(interrupts)
    first = _first_mapping(items)
    source = _truncate(source_label or "Child Agent", 80)
    tool = str(first.get("tool") or first.get("name") or "").strip()
    label = str(first.get("label") or "").strip()
    if len(items) > 1:
        action = f"continue with {len(items)} actions"
    else:
        action = _tool_action(tool, label)
    title = f"{source} needs approval to {action}."
    reason = _reason_from_item(first)
    raw_action = _raw_action_from_item(first)
    return {
        "kind": "agent_run",
        "source_label": source,
        "title": title,
        "reason": reason,
        "tool": tool,
        "action_label": label,
        "raw_action": raw_action,
        "agent_run_id": str(agent_run_id or ""),
        "parent_thread_id": str(parent_thread_id or ""),
        "interrupts": items,
    }


def payload_from_row(row: Mapping[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {}
    raw = row.get("approval_payload_json")
    if isinstance(raw, Mapping):
        payload = dict(raw)
    elif isinstance(raw, str) and raw.strip():
        try:
            loaded = json.loads(raw)
            payload = dict(loaded) if isinstance(loaded, Mapping) else {}
        except Exception:
            payload = {}
    else:
        payload = {}
    if not payload:
        payload = {
            "title": _clean_text(row.get("message") or "Approval required."),
            "reason": "",
            "raw_action": "",
            "source_label": _clean_text(row.get("source_label") or row.get("task_name") or "Approval"),
        }
    return payload


def compact_message(payload: Mapping[str, Any] | None, *, max_chars: int = 420) -> str:
    data = dict(payload or {})
    title = _truncate(data.get("title") or "Approval required.", 160)
    reason = sanitize_approval_reason(data.get("reason"), max_chars=180)
    raw_action = _truncate(_redact(data.get("raw_action") or ""), 180)
    parts = [title]
    if reason and reason.lower() not in title.lower():
        parts.append(f"Reason: {reason}")
    if raw_action:
        tool = str(data.get("tool") or "").strip()
        label = "Command" if tool in {"run_command", "developer_run_command", "shell"} else "Action"
        parts.append(f"{label}: {raw_action}")
    return _truncate("\n".join(parts), max_chars)


def channel_message(payload: Mapping[str, Any] | None, *, max_chars: int = 420) -> str:
    return compact_message(payload, max_chars=max_chars)
