"""The client's views of recordings (F2) and of a conversation's local addresses (F4).

A person sees and stops every recording, whichever conversation started it; stopping one
needs no approval. A conversation's local addresses are listed for that conversation, and the
person can remove one at any time.
"""

from __future__ import annotations

from typing import Any

from row_bot import recordings


def read_recordings(conversation_id: str | None = None) -> dict[str, Any]:
    """Recordings running now first, then finished ones, newest first."""

    records = recordings.list_recordings(conversation_id or None)[:200]
    return {
        "items": [recordings.view(record) for record in records],
        "recording": sum(record.status == "recording" for record in records),
    }


def read_recording(recording_id: str) -> dict[str, Any] | None:
    record = recordings.get(recording_id)
    return None if record is None else recordings.view(record)


def stop_recording(recording_id: str) -> dict[str, Any] | None:
    """The person's Stop: no approval; waits briefly for the video to be saved."""

    record = recordings.stop(recording_id, by="you")
    if record is None:
        return None
    recordings.wait(record, 20)
    return recordings.view(record)


def _browser_manager() -> Any:
    from row_bot.tools.browser_tool import get_session_manager

    return get_session_manager()


def read_local_addresses(conversation_id: str) -> dict[str, Any]:
    """The addresses on this computer this conversation's browser may open now (never starts a browser)."""

    grants = _browser_manager().local_address_grants(conversation_id)
    return {"conversation_id": conversation_id, "items": [grant.view() for grant in grants][:64]}


def remove_local_address(conversation_id: str, grant_id: str) -> dict[str, Any] | None:
    """Remove one; None when this conversation has no such grant (any more)."""

    if not _browser_manager().revoke_local_address(conversation_id, grant_id):
        return None
    return read_local_addresses(conversation_id)
