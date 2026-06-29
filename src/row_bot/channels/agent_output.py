"""Helpers for assembling channel-facing agent output."""

from __future__ import annotations

from collections.abc import Sequence


def assemble_agent_answer(answer: str, tool_reports: Sequence[str]) -> str:
    """Return final channel text, favoring the model's answer over tool logs."""
    if str(answer or "").strip():
        return answer
    reports = [str(report) for report in tool_reports if str(report).strip()]
    if reports:
        return "\n".join(reports)
    return ""
