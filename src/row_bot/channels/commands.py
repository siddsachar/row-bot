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
    ChannelCommand("/approval", "Show or switch approval mode",  "cmd_approval"),
    ChannelCommand("/help",   "List available commands",         "cmd_help"),
    ChannelCommand("/tools",  "List enabled tools",              "cmd_tools"),
    ChannelCommand("/skill",  "Use a skill in this conversation", "cmd_skill"),
    ChannelCommand("/skills", "Show active and suggested skills", "cmd_skill"),
    ChannelCommand("/skill-reset", "Reset skills in this conversation", "cmd_skill"),
    ChannelCommand("/noskill", "Disable a skill in this conversation", "cmd_skill"),
    ChannelCommand("/stop",   "Stop the current generation",     "cmd_stop"),
]

SKILL_COMMAND_TOKENS = {
    "/skill",
    "/skills",
    "/skill-reset",
    "/skillreset",
    "/skill_reset",
    "/noskill",
}

RESET_COMMAND_TOKENS = {"/skill-reset", "/skillreset", "/skill_reset"}
APPROVAL_COMMAND_TOKENS = {"/approval", "/approvals"}


def command_token(text: str) -> str:
    """Return the lower-cased leading command token from text."""
    value = str(text or "").strip()
    if not value:
        return ""
    return value.split(maxsplit=1)[0].lower()


def is_thread_scoped_command(text: str) -> bool:
    """Return True when a channel command needs the conversation thread id."""
    return command_token(text) in SKILL_COMMAND_TOKENS | APPROVAL_COMMAND_TOKENS


def normalize_skill_command_text(text: str) -> str:
    """Normalize channel reset aliases to the Smart Skills command form."""
    value = str(text or "").strip()
    token = command_token(value)
    if token in RESET_COMMAND_TOKENS:
        return "/skill reset"
    return value


# ── Command handlers ────────────────────────────────────────────────

def cmd_new(channel_name: str) -> str:
    """Handle ``/new`` — create a new conversation thread."""
    try:
        from row_bot.threads import create_thread
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
        from row_bot.models import get_active_model_name
        model = get_active_model_name()
        lines.append(f"• Model: `{model}`")
    except Exception:
        lines.append("• Model: unknown")

    # Running channels
    try:
        from row_bot.channels.registry import running_channels
        running = [ch.display_name for ch in running_channels()]
        lines.append(f"• Channels: {', '.join(running) if running else 'none'}")
    except Exception:
        pass

    # Enabled tools count
    try:
        from row_bot.tools.registry import get_enabled_tools
        count = len(get_enabled_tools())
        lines.append(f"• Tools: {count} enabled")
    except Exception:
        pass

    return "\n".join(lines)


def cmd_model(channel_name: str, arg: str = "") -> str:
    """Handle ``/model [name]`` with provider-aware, read-only semantics."""
    try:
        from row_bot.models import get_current_model
        current = get_current_model()
    except Exception:
        current = "unknown"

    try:
        from row_bot.providers.selection import (
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


def cmd_approval(channel_name: str, arg: str = "", *, thread_id: str | None = None) -> str:
    """Handle ``/approval [block|ask|auto]`` for channel conversations."""
    if not thread_id:
        return f"{channel_name} could not identify the current conversation thread."
    try:
        from row_bot.approval_policy import approval_label, normalize_approval_mode
        from row_bot.threads import _get_thread_approval_mode, _set_thread_approval_mode
        from row_bot.agent import clear_agent_cache
    except Exception as exc:
        return f"Could not inspect approval mode: {exc}"

    raw = str(arg or "").strip().lower()
    aliases = {
        "ask": "approve",
        "approve": "approve",
        "approval": "approve",
        "block": "block",
        "blocked": "block",
        "auto": "allow_all",
        "allow": "allow_all",
        "allow_all": "allow_all",
        "allow-all": "allow_all",
    }
    if not raw:
        mode = _get_thread_approval_mode(thread_id)
        return f"Current approval mode: **{approval_label(mode)}** (`{mode}`)."
    mode = normalize_approval_mode(aliases.get(raw, raw), "")
    if mode not in {"block", "approve", "allow_all"}:
        return "Usage: `/approval block`, `/approval ask`, or `/approval auto`."
    _set_thread_approval_mode(thread_id, mode)
    clear_agent_cache()
    return f"Approval mode set to **{approval_label(mode)}** (`{mode}`)."


def cmd_help(channel_name: str) -> str:
    """Handle ``/help`` — list available commands."""
    lines = ["**Available Commands:**"]
    for cmd in COMMANDS:
        lines.append(f"• `{cmd.name}` — {cmd.description}")
    return "\n".join(lines)


def _cmd_help_legacy(channel_name: str) -> str:
    """Handle ``/help`` by listing channel-safe commands."""
    lines = ["**Available Commands:**"]
    for cmd in COMMANDS:
        lines.append(f"- `{cmd.name}` - {cmd.description}")
    lines.append("")
    lines.append("Skill discovery:")
    lines.append("- `/skills` lists available skills")
    lines.append("- `/skills <query>` filters skills")
    lines.append("- `/skill <query>` activates one matching skill")
    lines.append("- `/noskill [query]` disables an active or matching skill")
    lines.append("- `/skillreset` also works where hyphenated bot commands are not supported")
    return "\n".join(lines)


cmd_help = _cmd_help_legacy


def cmd_tools(channel_name: str) -> str:
    """Handle ``/tools`` — list enabled tools."""
    try:
        from row_bot.tools.registry import get_enabled_tools
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
        from row_bot.agent import cancel_current_generation
        cancel_current_generation()
        return "⏹️ Generation stopped."
    except (ImportError, AttributeError):
        return "⏹️ Stop not available in this context."
    except Exception as exc:
        return f"Error stopping generation: {exc}"


# ── Dispatch helper ──────────────────────────────────────────────────

def cmd_skill(
    channel_name: str,
    text: str,
    *,
    thread_id: str | None = None,
    enabled_tool_names: list[str] | None = None,
) -> str:
    """Handle Smart Skills commands for channel conversations."""
    result = cmd_skill_result(
        channel_name,
        text,
        thread_id=thread_id,
        enabled_tool_names=enabled_tool_names,
    )
    return result.text


def cmd_skill_result(
    channel_name: str,
    text: str,
    *,
    thread_id: str | None = None,
    enabled_tool_names: list[str] | None = None,
):
    """Handle Smart Skills commands and return a structured result."""
    if not thread_id:
        from row_bot.skills_activation import SkillCommandResult
        return SkillCommandResult(
            "error",
            f"{channel_name} could not identify the current conversation thread, "
            "so Smart Skills were not changed.",
        )
    try:
        from row_bot.skills_activation import SkillCommandResult, apply_channel_skill_command
        if enabled_tool_names is None:
            try:
                from row_bot.tools.registry import get_enabled_tools
                enabled_tool_names = [t.name for t in get_enabled_tools()]
            except Exception:
                enabled_tool_names = []

        result = apply_channel_skill_command(
            thread_id,
            normalize_skill_command_text(text),
            enabled_tool_names=enabled_tool_names or [],
        )
        return result or SkillCommandResult("error", "Smart Skills command not recognized.")
    except Exception as exc:
        log.warning("/skill failed: %s", exc)
        from row_bot.skills_activation import SkillCommandResult
        return SkillCommandResult("error", f"Could not update Smart Skills: {exc}")


_HANDLER_MAP = {
    "/new":    lambda ch, _arg: cmd_new(ch),
    "/status": lambda ch, _arg: cmd_status(ch),
    "/model":  cmd_model,
    "/help":   lambda ch, _arg: cmd_help(ch),
    "/tools":  lambda ch, _arg: cmd_tools(ch),
    "/stop":   lambda ch, _arg: cmd_stop(ch),
}


def dispatch(
    channel_name: str,
    text: str,
    *,
    thread_id: str | None = None,
    enabled_tool_names: list[str] | None = None,
) -> str | None:
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

    if cmd in SKILL_COMMAND_TOKENS:
        return cmd_skill(
            channel_name,
            text,
            thread_id=thread_id,
            enabled_tool_names=enabled_tool_names,
        )

    if cmd in APPROVAL_COMMAND_TOKENS:
        return cmd_approval(channel_name, arg, thread_id=thread_id)

    handler = _HANDLER_MAP.get(cmd)
    if handler is None:
        return None  # not a known command — treat as regular message

    try:
        return handler(channel_name, arg)
    except Exception as exc:
        log.warning("Command %s failed: %s", cmd, exc)
        return f"❌ Command error: {exc}"
