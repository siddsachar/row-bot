"""Structured logging helpers for MCP client operations."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger("thoth.mcp")

_SECRET_KEY_RE = re.compile(r"(key|token|secret|password|authorization|cookie)", re.I)


def mask_secret(value: Any) -> Any:
    """Return a redacted representation of a potentially sensitive value."""
    if value is None:
        return None
    text = str(value)
    if not text:
        return ""
    if len(text) <= 8:
        return "***"
    return f"{text[:3]}…{text[-3:]}"


def mask_mapping(data: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    masked: dict[str, Any] = {}
    for key, value in data.items():
        if _SECRET_KEY_RE.search(str(key)):
            masked[str(key)] = mask_secret(value)
        elif isinstance(value, dict):
            masked[str(key)] = mask_mapping(value)
        elif isinstance(value, list):
            masked[str(key)] = [mask_mapping(v) if isinstance(v, dict) else v for v in value]
        else:
            masked[str(key)] = value
    return masked


def log_event(event: str, level: int = logging.INFO, **fields: Any) -> None:
    """Emit a compact structured MCP log event with secret masking."""
    payload = {"event": event, **mask_mapping(fields)}
    try:
        rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        rendered = f"{event} {payload!r}"
    logger.log(level, rendered)