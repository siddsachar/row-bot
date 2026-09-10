"""Bounded, complete text paging over the retained public checkpoint reader."""
from __future__ import annotations

import base64
from typing import Any

from row_bot.application.client_platform import ClientPlatformError
from row_bot.application.conversation_search import _cursor, _decode


def read_text(service: Any, conversation_id: str, message_id: str, *, cursor: str | None = None) -> dict:
    from row_bot.runtime.checkpoint_reader import open_checkpoint
    service._metadata(conversation_id)
    if message_id.startswith("live:"):
        from row_bot.application.live_content import read_text_page
        result = read_text_page(conversation_id, message_id, cursor)
        service._metadata(conversation_id)
        return result
    with open_checkpoint(conversation_id) as reader:
        if reader is None:
            raise ClientPlatformError("not_found")
        offset = 0
        if cursor:
            values = _decode(cursor)
            if len(values) != 3 or values[:2] != [reader.revision, message_id] or type(values[2]) is not int or values[2] < 0:
                raise ClientPlatformError("cursor_expired")
            offset = values[2]
        record = next((record for _, record in reader.records() if record["message_id"] == message_id), None)
        if record is None:
            raise ClientPlatformError("not_found")
        text, position, more = "", 0, False
        for chunk in reader.public_text_chunks(record):
            end = position + len(chunk)
            if end > offset:
                remaining = chunk[max(0, offset-position):]
                taken = min(16384-len(text), len(remaining))
                text += remaining[:taken]
                if taken < len(remaining):
                    more = True
                    break
            position = end
        revision = reader.revision
    service._metadata(conversation_id)
    return {"conversation_id": conversation_id, "content_ref": message_id,
            "checkpoint_revision": revision, "encoding": "base64", "media_type": "text/plain",
            "data": base64.b64encode(text.encode()).decode(), "has_more": more,
            "next_cursor": _cursor([revision, message_id, offset+len(text)]) if more else None}
