"""
Row-Bot – Channel Tool Factory
===============================
Auto-generates LangChain ``StructuredTool`` instances for each registered
channel based on its ``ChannelCapabilities``.

Every channel automatically gets ``send_{name}_message``.  If the channel
declares ``photo_out`` or ``document_out``, matching tools are generated too.
Additional channel-specific tools are pulled from ``channel.extra_tools()``.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from row_bot.channels.base import Channel
from row_bot.data_paths import get_row_bot_data_dir

log = logging.getLogger("row_bot.channels.tool_factory")


# ── Path resolution (shared) ─────────────────────────────────────────

def _resolve_file_path(file_path: str) -> str:
    """Resolve *file_path* trying workspace root → tracker exports → cwd."""
    p = Path(file_path)
    if p.is_absolute() and p.is_file():
        return str(p)

    try:
        from row_bot.tools import registry as _reg
        fs_tool = _reg.get_tool("filesystem")
        if fs_tool:
            ws_root = fs_tool.get_config("workspace_root", "")
            if ws_root:
                candidate = Path(ws_root) / p
                if candidate.is_file():
                    return str(candidate.resolve())
    except Exception:
        pass

    try:
        _data_dir = get_row_bot_data_dir()
        candidate = _data_dir / "tracker" / "exports" / p
        if candidate.is_file():
            return str(candidate.resolve())
    except Exception:
        pass

    candidate = Path.cwd() / p
    if candidate.is_file():
        return str(candidate.resolve())

    return file_path


# ── Pydantic schemas ─────────────────────────────────────────────────

class _SendMessageInput(BaseModel):
    text: str = Field(description="The text message to send.")


class _SendPhotoInput(BaseModel):
    file_path: str = Field(description="Absolute path to the image file to send.")
    caption: Optional[str] = Field(default=None, description="Optional caption.")


class _SendDocumentInput(BaseModel):
    file_path: str = Field(description="Absolute path to the file to send.")
    caption: Optional[str] = Field(default=None, description="Optional caption.")


# ── Factory ──────────────────────────────────────────────────────────

def create_channel_tools(channel: Channel) -> list[StructuredTool]:
    """Generate LangChain tools for *channel* based on its capabilities.

    Returns a list ready to register with the tool registry.
    """
    name = channel.name
    display = channel.display_name
    caps = channel.capabilities
    tools: list[StructuredTool] = []

    # ── send_message (always) ────────────────────────────────────
    def _make_send_message(ch=channel):
        def _send(text: str) -> str:
            if not ch.is_running():
                return f"Error: {ch.display_name} is not running."
            try:
                # For Telegram, resolve the target automatically
                target = _get_default_target(ch)
                ch.send_message(target, text)
                return f"Message sent via {ch.display_name} ({len(text)} chars)."
            except Exception as exc:
                log.warning("send_%s_message failed: %s", ch.name, exc)
                return f"Error sending message via {ch.display_name}: {exc}"
        return _send

    tools.append(StructuredTool.from_function(
        func=_make_send_message(),
        name=f"send_{name}_message",
        description=(
            f"Send a text message to the user via {display}. "
            f"Use this to push information, summaries, or reminders to {display}."
        ),
        args_schema=_SendMessageInput,
    ))

    # ── send_photo ───────────────────────────────────────────────
    if caps.photo_out:
        def _make_send_photo(ch=channel):
            def _send(file_path: str, caption: str | None = None) -> str:
                if not ch.is_running():
                    return f"Error: {ch.display_name} is not running."
                resolved = _resolve_file_path(file_path)
                if not os.path.isfile(resolved):
                    return f"Error: File not found: {file_path}"
                try:
                    target = _get_default_target(ch)
                    ch.send_photo(target, resolved, caption=caption)
                    return f"Photo '{os.path.basename(resolved)}' sent via {ch.display_name}."
                except Exception as exc:
                    log.warning("send_%s_photo failed: %s", ch.name, exc)
                    return f"Error sending photo via {ch.display_name}: {exc}"
            return _send

        tools.append(StructuredTool.from_function(
            func=_make_send_photo(),
            name=f"send_{name}_photo",
            description=(
                f"Send a photo/image to the user via {display}. "
                f"Provide the absolute file path to a local image file."
            ),
            args_schema=_SendPhotoInput,
        ))

    # ── send_document ────────────────────────────────────────────
    if caps.document_out:
        def _make_send_document(ch=channel):
            def _send(file_path: str, caption: str | None = None) -> str:
                if not ch.is_running():
                    return f"Error: {ch.display_name} is not running."
                resolved = _resolve_file_path(file_path)
                if not os.path.isfile(resolved):
                    return f"Error: File not found: {file_path}"
                try:
                    target = _get_default_target(ch)
                    ch.send_document(target, resolved, caption=caption)
                    return f"Document '{os.path.basename(resolved)}' sent via {ch.display_name}."
                except Exception as exc:
                    log.warning("send_%s_document failed: %s", ch.name, exc)
                    return f"Error sending document via {ch.display_name}: {exc}"
            return _send

        tools.append(StructuredTool.from_function(
            func=_make_send_document(),
            name=f"send_{name}_document",
            description=(
                f"Send a document/file to the user via {display}. "
                f"Provide the absolute file path to any local file."
            ),
            args_schema=_SendDocumentInput,
        ))

    # ── extra tools from channel ─────────────────────────────────
    tools.extend(channel.extra_tools())

    return tools


def channel_tool_names(channel: Channel) -> list[str]:
    """Return generated outbound tool names for *channel*."""

    names = [f"send_{channel.name}_message"]
    caps = channel.capabilities
    if caps.photo_out:
        names.append(f"send_{channel.name}_photo")
    if caps.document_out:
        names.append(f"send_{channel.name}_document")
    return names


def destructive_channel_tool_names(channel: Channel) -> set[str]:
    """Outbound channel sends are external side effects and need approval policy."""

    return set(channel_tool_names(channel))


def _get_default_target(channel: Channel) -> str | int:
    """Resolve the default delivery target for a channel.

    Delegates to the channel's own ``get_default_target()`` method,
    which knows how to look up the correct user/chat ID.
    """
    return channel.get_default_target()
