"""Library › Media for the agent (5.3.1 B3): ``media_find``, ``media_tag`` and ``media_bring``.

Every picture, video and sound in Row-Bot's chats (recordings, edits, voice lines, music, pictures), found
by name, tag or kind. ``media_bring`` copies one into this chat's files, which is how a workflow hands a
clip or a screenshot to another chat. Nothing here leaves Row-Bot, so none of them asks.
"""

from __future__ import annotations

from typing import Literal

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from row_bot.tools import registry
from row_bot.tools.base import BaseTool


def _conversation() -> str:
    from row_bot.tool_outputs import current_thread_id

    return str(current_thread_id() or "")


def _size(size: int) -> str:
    return f"{size / 1_048_576:.1f} MB" if size >= 1_048_576 else f"{max(1, round(size / 1024))} KB"


def _media_find(query: str = "", kind: Literal["", "image", "video", "audio"] = "", limit: int = 20) -> str:
    from row_bot import media_library

    found = media_library.items(query=query, kind=kind, limit=max(1, min(limit, 50)))
    if not found:
        return "Nothing in the media library matches." if query or kind else "The media library is empty."
    lines = [f"{len(found)} item(s), newest first:"]
    for item in found:
        tags = f" · tags: {', '.join(item['tags'])}" if item["tags"] else ""
        lines.append(f"- {item['name']} ({item['kind']}, {_size(item['size'])}) · {item['ref']} · from "
                     f"“{item['conversation_title']}” · {item['added_at'][:16].replace('T', ' ')}{tags}")
    return "\n".join(lines)


def _media_tag(ref: str, tags: list[str]) -> str:
    from row_bot import media_library

    try:
        kept = media_library.set_tags(ref, tags)
    except media_library.MediaError as exc:
        return str(exc)
    return f"Tags for {ref}: {', '.join(kept)}." if kept else f"{ref} has no tags now."


def _media_bring(ref: str) -> str:
    from row_bot import media_library

    conversation = _conversation()
    if not conversation:
        return "Bringing media needs a conversation."
    try:
        copied = media_library.bring(ref, conversation)
    except media_library.MediaError as exc:
        return str(exc)
    return (f"{copied['name']} is in this chat's files ({copied['attachment_ref']}); use it by that name, as "
            "any file the person added.")


class _FindInput(BaseModel):
    query: str = Field(default="", description="Words in the name, a tag or the chat's title; empty lists the newest.")
    kind: Literal["", "image", "video", "audio"] = Field(default="", description="Only this kind.")
    limit: int = Field(default=20, description="How many to list (up to 50).")


class _TagInput(BaseModel):
    ref: str = Field(description="The item's reference from media_find.")
    tags: list[str] = Field(description="Its tags, replacing the old ones (up to 12; [] clears them).")


class _BringInput(BaseModel):
    ref: str = Field(description="The item's reference from media_find.")


class MediaTool(BaseTool):

    @property
    def name(self) -> str:
        return "media"

    @property
    def display_name(self) -> str:
        return "🎞️ Media"

    @property
    def description(self) -> str:
        return ("Find, tag and reuse every picture, video and sound in Row-Bot's chats: recordings, edits, voice "
                "lines, music and pictures (Library › Media).")

    @property
    def enabled_by_default(self) -> bool:
        return True

    def as_langchain_tools(self) -> list:
        return [
            StructuredTool.from_function(
                func=_media_find, name="media_find",
                description=("Find media from any chat (Library › Media) by words, a tag or kind: its name, "
                             "reference, chat, date and tags."),
                args_schema=_FindInput),
            StructuredTool.from_function(
                func=_media_tag, name="media_tag",
                description="Set an item's tags in the media library, so it's found again (e.g. 'x post', 'demo').",
                args_schema=_TagInput),
            StructuredTool.from_function(
                func=_media_bring, name="media_bring",
                description="Copy an item from the media library into this chat's files, to use it here.",
                args_schema=_BringInput),
        ]

    def execute(self, query: str) -> str:
        return _media_find(query)


registry.register(MediaTool())
