"""One bounded conversation-open response composed from existing owners."""
from __future__ import annotations

import json
from typing import Any

from row_bot.application.client_platform import ClientPlatformError, _COMMAND_LOCK


def read_open(service: Any, conversation_id: str, *, limit: int = 100) -> dict:
    """Read a revision-fenced public view without input admission or observation."""
    from row_bot import threads
    from row_bot.runtime import admissions
    from row_bot.application.conversation_search import history_window
    from row_bot.application.conversation_drafts import read_draft
    from row_bot.application.workspace_setup import conversation_workspace

    if type(limit) is not int or not 1 <= limit <= 100:
        raise ClientPlatformError("invalid_command")
    with _COMMAND_LOCK, threads.checkpoint_mutation(conversation_id):
        if admissions.deletion_state(conversation_id) != "active":
            raise ClientPlatformError("conversation_deleting")
        conversation = service.get_conversation(conversation_id)
        history = history_window(service, conversation_id, limit=limit)
        workspace = conversation_workspace(service, conversation_id)
        draft = read_draft(service, conversation_id)
        current = service._metadata(conversation_id)
        if admissions.deletion_state(conversation_id) != "active":
            raise ClientPlatformError("conversation_deleting")
        if conversation["revision"] != workspace["revision"] or str(current["client_revision"]) != conversation["revision"]:
            raise ClientPlatformError("revision_conflict", str(current["client_revision"]))
        result = {"conversation": conversation, "history": history, "workspace": workspace, "draft": draft}
        if len(json.dumps(result, ensure_ascii=False).encode()) > 2 * 1024 * 1024:
            raise ClientPlatformError("payload_too_large")
        return result
