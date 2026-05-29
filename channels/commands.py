"""
Thoth – Shared Channel Commands
==================================
Provides a common set of slash-command handlers that every channel adapter
can delegate to.  Each handler takes a callback for sending the response
back to the user on that platform.

Supported commands
------------------
``/new``    — start a new conversation thread
``/status`` — show current model and channel status
``/model``  — show or switch the active LLM model
``/help``   — list available commands
``/tools``  — list enabled tools
``/stop``   — stop the current generation (if supported)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

log = logging.getLogger("thoth.channels.commands")


@dataclass
class ChannelCommand:
    """Describes one slash command available across all channels."""
    name: str                # "/new"
    description: str         # "Start a new conversation"
    handler_key: str         # "cmd_new"  → used to look up the handler


# ── Canonical command list ───────────────────────────────────────────

COMMANDS: list[ChannelCommand] = [
    ChannelCommand("/new",    "Start a new conversation thread", "cmd_new"),
    ChannelCommand("/status", "Show agent status",               "cmd_status"),
    ChannelCommand("/model",  "Show or switch the active model", "cmd_model"),
    ChannelCommand("/help",   "List available commands",         "cmd_help"),
    ChannelCommand("/tools",  "List enabled tools",              "cmd_tools"),
    ChannelCommand("/stop",   "Stop the current generation",     "cmd_stop"),
]


# ── Command handlers ────────────────────────────────────────────────

def cmd_new(channel_name: str) -> str:
    """Handle ``/new`` — create a new conversation thread."""
    try:
        from threads import create_thread
        tid = create_thread(title=f"{channel_name} conversation")
        return f"✅ New conversation started (thread {tid[:8]}…)"
    except Exception as exc:
        log.warning("/new failed: %s", exc)
        return f"❌ Could not start new conversation: {exc}"


def cmd_status(channel_name: str) -> str:
    """Handle ``/status`` — return agent status summary."""
    lines = [f"🤖 **Thoth Status** ({channel_name})"]

    # Active model
    try:
        from models import get_active_model_name
        model = get_active_model_name()
        lines.append(f"• Model: `{model}`")
    except Exception:
        lines.append("• Model: unknown")

    # Running channels
    try:
        from channels.registry import running_channels
        running = [ch.display_name for ch in running_channels()]
        lines.append(f"• Channels: {', '.join(running) if running else 'none'}")
    except Exception:
        pass

    # Enabled tools count
    try:
        from tools.registry import get_enabled_tools
        count = len(get_enabled_tools())
        lines.append(f"• Tools: {count} enabled")
    except Exception:
        pass

    return "\n".join(lines)


def cmd_model(channel_name: str, arg: str = "") -> str:
    """Handle ``/model [name]`` with provider-aware, read-only semantics."""
    try:
        from models import get_current_model
        current = get_current_model()
    except Exception:
        current = "unknown"

    try:
        from providers.selection import (
            ModelSelectionError,
            canonicalize_model_selection,
            list_model_choice_options,
        )
    except Exception as exc:
        return f"Could not inspect provider models: {exc}"

    if not arg.strip():
        lines = [f"Current model: `{current}`"]
        choices = list_model_choice_options("channels", include_values=[current], include_inactive=True)
        if choices:
            lines.append("")
            lines.append("Channel Quick Choices:")
            for choice in choices[:12]:
                value = str(choice.get("value") or "")
                label = str(choice.get("label") or value)
                lines.append(f"- `{value}` ({label})")
        lines.append("")
        lines.append(
            f"{channel_name} uses the model configured in Thoth unless this adapter "
            "implements per-conversation model storage."
        )
        return "\n".join(lines)

    raw = arg.strip()
    if raw.lower() == "default":
        return (
            f"{channel_name} cannot reset a per-conversation model here because "
            "the shared command path has no storage hook. Configure the channel "
            "model in Thoth."
        )
    try:
        canonical = canonicalize_model_selection(raw, "channels")
    except ModelSelectionError as exc:
        return f"Could not resolve `{raw}`: {exc}"
    return (
        f"Recognized `{canonical.ref}`, but {channel_name} cannot persist "
        "per-conversation model overrides through the shared command path. "
        "Configure the model in Thoth."
    )


def cmd_help(channel_name: str) -> str:
    """Handle ``/help`` — list available commands."""
    lines = ["**Available Commands:**"]
    for cmd in COMMANDS:
        lines.append(f"• `{cmd.name}` — {cmd.description}")
    return "\n".join(lines)


def cmd_tools(channel_name: str) -> str:
    """Handle ``/tools`` — list enabled tools."""
    try:
        from tools.registry import get_enabled_tools
        tools = get_enabled_tools()
        if not tools:
            return "No tools enabled."
        lines = [f"**Enabled Tools ({len(tools)}):**"]
        for t in tools:
            lines.append(f"• {t.display_name}")
        return "\n".join(lines)
    except Exception as exc:
        return f"Error listing tools: {exc}"


def cmd_stop(channel_name: str) -> str:
    """Handle ``/stop`` — attempt to stop the current generation."""
    try:
        from agent import cancel_current_generation
        cancel_current_generation()
        return "⏹️ Generation stopped."
    except (ImportError, AttributeError):
        return "⏹️ Stop not available in this context."
    except Exception as exc:
        return f"Error stopping generation: {exc}"


# ── Dispatch helper ──────────────────────────────────────────────────

_HANDLER_MAP = {
    "/new":    lambda ch, _arg: cmd_new(ch),
    "/status": lambda ch, _arg: cmd_status(ch),
    "/model":  cmd_model,
    "/help":   lambda ch, _arg: cmd_help(ch),
    "/tools":  lambda ch, _arg: cmd_tools(ch),
    "/stop":   lambda ch, _arg: cmd_stop(ch),
}


def dispatch(channel_name: str, text: str) -> str | None:
    """Try to dispatch *text* as a slash command.

    Returns the response string if *text* matches a known command,
    or ``None`` if it is not a command (so the caller should treat it
    as a regular message).
    """
    text = text.strip()
    if not text.startswith("/"):
        return None

    parts = text.split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""

    handler = _HANDLER_MAP.get(cmd)
    if handler is None:
        return None  # not a known command — treat as regular message

    try:
        return handler(channel_name, arg)
    except Exception as exc:
        log.warning("Command %s failed: %s", cmd, exc)
        return f"❌ Command error: {exc}"
