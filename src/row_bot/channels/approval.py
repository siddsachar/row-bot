"""
Row-Bot – Shared Approval / Interrupt Helpers
=============================================
Centralised logic used by *all* channels to:

* **Resume** a paused agent graph after user approval/denial
* **Format** interrupt details as plain text
* **Extract** interrupt IDs for multi-interrupt resume
* **Classify** user text as approval / denial / unrelated

Any new channel adapter only needs to import these helpers rather than
re-implementing the resume logic.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("row_bot.channels.approval")


# ── Agent resume ─────────────────────────────────────────────────────

def resume_agent_sync(
    config: dict,
    approved: bool,
    *,
    interrupt_ids: list[str] | None = None,
) -> tuple[str, Any, list]:
    """Resume a paused agent graph after interrupt approval/denial.

    Mirrors the event-collection logic of each channel's
    ``_run_agent_sync`` but calls ``agent_mod.resume_stream_agent``
    instead of ``stream_agent``.

    Returns ``(answer_text, new_interrupt_data_or_None, captured_images)``.
    """
    import row_bot.agent as agent_mod
    from row_bot.channels.runtime import approval_mode_for_config
    from row_bot.tools import registry as tool_registry

    enabled = [t.name for t in tool_registry.get_enabled_tools()]
    config = {
        **config,
        "configurable": {
            **(config.get("configurable") or {}),
            "runtime_surface": "approval",
            "runtime_mode": "agent",
            "approval_mode": approval_mode_for_config(config),
        },
    }
    answer_tokens: list[str] = []
    interrupt_data: Any = None

    for event_type, payload in agent_mod.resume_stream_agent(
        enabled, config, approved, interrupt_ids=interrupt_ids
    ):
        if event_type == "token":
            answer_tokens.append(payload)
        elif event_type == "interrupt":
            interrupt_data = payload
        elif event_type == "done":
            if payload and not answer_tokens:
                answer_tokens.append(payload)

    answer = "".join(answer_tokens).strip()
    return answer, interrupt_data, []


# ── Interrupt formatting ─────────────────────────────────────────────

def format_interrupt_text(data: Any) -> str:
    """Format interrupt data as **plain text** suitable for any channel.

    Telegram has its own HTML formatter; use this for text-based channels
    (Slack, Discord, WhatsApp, SMS).
    """
    items = data if isinstance(data, list) else [data]
    parts: list[str] = []

    for item in items:
        if not isinstance(item, dict):
            parts.append(str(item))
            continue
        tool_name = item.get("tool", item.get("name", "Unknown tool"))
        desc = item.get("description", "")
        args = item.get("args", {})

        parts.append(f"⚠️ {tool_name} needs your approval:")
        if desc:
            parts.append(f"  {desc}")
        elif args:
            for k, v in args.items():
                parts.append(f"  • {k}: {v}")

    return "\n".join(parts)


# ── Interrupt ID extraction ──────────────────────────────────────────

def extract_interrupt_ids(data: Any) -> list[str] | None:
    """Extract ``__interrupt_id`` values from interrupt data.

    Returns a list when there are ≥ 2 IDs (multi-interrupt),
    or ``None`` for single interrupts (the default resume path).
    """
    if data is None:
        return None
    items = data if isinstance(data, list) else [data]
    ids = [
        item.get("__interrupt_id")
        for item in items
        if isinstance(item, dict) and item.get("__interrupt_id")
    ]
    return ids if len(ids) > 1 else None


# ── Text classification ─────────────────────────────────────────────

_YES_WORDS = frozenset({"yes", "y", "approve", "ok", "continue"})
_NO_WORDS = frozenset({"no", "n", "deny", "reject", "cancel"})


def is_approval_text(text: str) -> bool | None:
    """Classify *text* as approval, denial, or unrelated.

    Returns ``True`` for approval, ``False`` for denial,
    ``None`` if the text is not a clear yes/no.
    """
    t = text.strip().lower()
    if t in _YES_WORDS:
        return True
    if t in _NO_WORDS:
        return False
    return None
