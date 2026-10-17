"""The client's views of video edits (F3).

A person sees and stops every edit, whichever conversation started it; stopping one needs no
approval. The result is in the conversation's files.
"""

from __future__ import annotations

from typing import Any

from row_bot import video_edits


def read_video_edits(conversation_id: str | None = None) -> dict[str, Any]:
    """Edits running now first, then finished ones, newest first."""

    edits = video_edits.list_edits(conversation_id or None)[:200]
    return {"items": [video_edits.view(edit) for edit in edits], "running": sum(edit.active for edit in edits)}


def read_video_edit(edit_id: str) -> dict[str, Any] | None:
    edit = video_edits.get(edit_id)
    return None if edit is None else video_edits.view(edit)


def stop_video_edit(edit_id: str) -> dict[str, Any] | None:
    """The person's Stop: no approval; waits briefly for it to end."""

    edit = video_edits.stop(edit_id, by="you")
    if edit is None:
        return None
    video_edits.wait(edit, 15)
    return video_edits.view(edit)
