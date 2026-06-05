"""Shared tool-result protocol markers for provider transports."""

from __future__ import annotations

from typing import Any


VALIDATION_RETRY_MARKER = "ROW_BOT_TOOL_VALIDATION_RETRY_REQUIRED"


def format_validation_retry_result(
    *,
    tool_name: str,
    detail: str,
    fields: list[str],
) -> str:
    marker = f"<!--{VALIDATION_RETRY_MARKER}-->"
    return (
        f"{marker}\n"
        f"Invalid tool call for {tool_name}: {detail}. "
        "Retry the same tool with valid JSON arguments inferred from the user request; "
        "ask only if the missing value is genuinely unknown."
    )


def tool_result_requires_validation_retry(content: Any) -> bool:
    text = str(content or "").lstrip()
    return text.startswith(f"<!--{VALIDATION_RETRY_MARKER}-->")
