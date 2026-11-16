"""The editor tools, run inside the app for a connected editor.

``ask_row_bot`` sends the message to the chat for that editor and folder ("From Claude Code ·
row-bot"), or to a chat ``conversation_id`` names, as the person's own message: the chat's
transcript shows it with the editor's name and Row-Bot's model reads exactly the editor's words,
nothing added. The turn runs at the chat's own level, never Hands-off (an editor reached the chat:
``full_autonomy.cap_for_conversation``). An approval it needs waits in Row-Bot, and the editor hears
"Waiting for your approval in Row-Bot". ``search_memory`` and ``list_conversations`` only read.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
import uuid
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from row_bot.editors import catalog, store

logger = logging.getLogger(__name__)

_CHATS_LISTED = 20
_MEMORIES_FOUND = 10
_MEMORY_CHARS = 1_000
_REPLY_CHARS = 60_000


@dataclass(frozen=True)
class ToolResult:
    text: str
    is_error: bool = False


def run_tool(editor_id: str, name: str, arguments: object, *, service: Any = None,
             wait_seconds: float = catalog.ANSWER_WAIT_SECONDS) -> ToolResult:
    """Run one tool for a connected editor."""
    editor = catalog.editor(editor_id)
    if editor is None:
        return ToolResult(catalog.NOT_CONNECTED, True)
    if not isinstance(arguments, dict):
        return ToolResult("The tool's arguments must be an object.", True)
    if name == "ask_row_bot":
        return ask_row_bot(editor, arguments, service=service, wait_seconds=wait_seconds)
    if name == "search_memory":
        return search_memory(arguments)
    if name == "list_conversations":
        return ToolResult(json.dumps(list_conversations(), ensure_ascii=False))
    return ToolResult(f"Row-Bot has no tool called {str(name)[:64]}.", True)


# --- Which chats an editor may reach ---------------------------------------------------------------------

def _continuable(thread_id: str) -> bool:
    """A chat the person has in Row-Bot: not deleted, incognito, a workflow's, a helper's, Row-Bot's own
    or on a channel."""
    return thread_id in {item["conversation_id"] for item in _chats(only=thread_id)}


def _chats(*, only: str = "") -> list[dict[str, str]]:
    from row_bot import threads
    from row_bot.tasks import get_thread_channel_ref

    threads._ensure_thread_db()
    workflows = threads.get_workflow_thread_ids()
    with closing(sqlite3.connect(threads.DB_PATH)) as connection:
        rows = connection.execute(
            "SELECT thread_id, name, COALESCE(updated_at,'') FROM thread_meta"
            " WHERE COALESCE(deleted_at,'')='' AND COALESCE(incognito,0)=0"
            " AND COALESCE(thread_type,'') NOT IN ('proactive','agent_child')"
            + (" AND thread_id=?" if only else "")
            + " ORDER BY COALESCE(updated_at,'') DESC, thread_id DESC LIMIT ?",
            ((only,) if only else ()) + (200,),
        ).fetchall()
    chats = []
    for thread_id, title, updated in rows:
        if thread_id in workflows or threads._thread_write_blocked(thread_id) or get_thread_channel_ref(thread_id):
            continue
        chats.append({"conversation_id": str(thread_id), "title": str(title or "Untitled"),
                      "updated_at": str(updated)})
        if len(chats) >= _CHATS_LISTED:
            break
    return chats


def list_conversations() -> list[dict[str, str]]:
    """The person's recent chats an editor may continue, newest first. Reads only."""
    return _chats()


# --- search_memory -----------------------------------------------------------------------------------------

def search_memory(arguments: dict) -> ToolResult:
    """Up to ten of Row-Bot's memories matching the query. Reads only: no recall is recorded."""
    query = arguments.get("query")
    if not isinstance(query, str) or not query.strip():
        return ToolResult("Say what to look for in query.", True)
    if len(query) > catalog.MAX_QUERY_CHARS:
        return ToolResult(f"The query is too long (at most {catalog.MAX_QUERY_CHARS} characters).", True)
    from row_bot import knowledge_graph

    found = knowledge_graph.retrieve_memory_candidates(query.strip(), top_k=_MEMORIES_FOUND, threshold=0.3,
                                                       max_results=_MEMORIES_FOUND)
    if not found:
        return ToolResult("Row-Bot has no memories matching that.")
    memories = []
    for item in found[:_MEMORIES_FOUND]:
        entry = {
            "subject": str(item.get("subject") or ""),
            "category": str(item.get("category") or item.get("entity_type") or ""),
            "content": str(item.get("content") or item.get("description") or "")[:_MEMORY_CHARS],
            "updated": str(item.get("updated_at") or "")[:10],
        }
        memories.append({key: value for key, value in entry.items() if value})
    return ToolResult(json.dumps(memories, ensure_ascii=False))


# --- ask_row_bot -------------------------------------------------------------------------------------------

def _folder(raw: object) -> Path | None:
    if not isinstance(raw, str) or not raw.strip() or len(raw) > catalog.MAX_FOLDER_CHARS:
        return None
    path = Path(raw.strip()).expanduser()
    if not path.is_absolute():
        return None
    try:
        return path.resolve()
    except OSError:
        return None


def _folder_key(folder: Path | None) -> str:
    return os.path.normcase(str(folder)) if folder is not None else ""


def _code_folder(folder: Path) -> str | None:
    """The Row-Bot code folder this folder is (or is inside): the nearest one, or None."""
    from row_bot.developer.storage import list_workspaces

    best: tuple[int, str] | None = None
    for workspace in list_workspaces():
        try:
            root = Path(workspace.path).resolve()
        except (OSError, TypeError, ValueError):
            continue
        if folder == root or root in folder.parents:
            depth = len(root.parts)
            if best is None or depth > best[0]:
                best = (depth, workspace.id)
    return best[1] if best else None


def _bind_code_folder(service: Any, conversation_id: str, folder: Path) -> bool:
    """Bind the chat to this folder's code folder when it has none yet; False when Row-Bot can't use it."""
    from row_bot.conversation_resources import ResourceError, bind, list_bindings

    current = list_bindings(conversation_id)
    if any(binding.kind == "workspace" for binding in current.bindings):
        return True
    workspace_id = _code_folder(folder)
    if workspace_id is None:
        return False
    from row_bot.application.client_platform import ClientPlatformError
    from row_bot.application.workspace_setup import resource_choice

    try:
        choice = resource_choice("workspace", workspace_id)
        if not choice["available"]:
            return False
        resources = bind(conversation_id, "workspace", workspace_id, expected_revision=current.revision,
                         expected_resource_revision=choice["revision"])
    except (ClientPlatformError, ResourceError):
        logger.warning("The chat %s couldn't take its code folder", conversation_id[:8], exc_info=True)
        return False
    service.projection.publish(conversation_id, "resource.changed", {"revision": str(resources.revision)})
    return True


def _chat(editor: catalog.Editor, folder: Path | None) -> str:
    """The chat for this editor and folder, made the first time ("From Claude Code · row-bot")."""
    from row_bot import threads

    key = _folder_key(folder)
    existing = store.chat_for(editor.id, key)
    if existing and threads._thread_exists(existing) and not threads._thread_write_blocked(existing):
        return existing
    title = f"From {editor.name}" + (f" · {folder.name}" if folder is not None and folder.name else "")
    conversation_id = str(uuid.uuid4())
    # Marked first: the chat never runs a turn as Hands-off, not even before the record of who made it.
    store.remember(conversation_id, editor.id, folder_key=key, made=True)
    threads.create_thread(title, thread_id=conversation_id, name_source=threads.THREAD_NAME_SOURCE_MANUAL)
    from row_bot.application.live_updates import publish

    publish("conversations")
    return conversation_id


def _final_reply(conversation_id: str, submission_id: str) -> str:
    """The text of the last answer after the editor's message."""
    from row_bot import threads

    messages = threads.get_latest_checkpoint_messages(conversation_id)
    start = next((index for index, message in enumerate(messages)
                  if getattr(message, "id", None) == submission_id), None)
    if start is None:
        return ""
    for message in reversed(messages[start + 1:]):
        if getattr(message, "type", "") != "ai" or getattr(message, "tool_calls", None):
            continue
        content = getattr(message, "content", "")
        if isinstance(content, list):
            content = "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content
                              if not isinstance(part, dict) or part.get("type", "text") == "text")
        text = str(content or "").strip()
        if text:
            return text[:_REPLY_CHARS]
    return ""


def _waiting_summary(conversation_id: str) -> str:
    from row_bot.tasks import _get_conn

    with _get_conn() as connection:
        row = connection.execute(
            "SELECT message FROM approval_requests WHERE source_thread_id=? AND status='pending'"
            " ORDER BY requested_at DESC LIMIT 1", (conversation_id,)).fetchone()
    return str(row[0] or "") if row else ""


def _model_selection(service: Any, conversation_id: str) -> dict | None:
    from row_bot.models import get_current_model
    from row_bot.providers.selection import model_choice_value, parse_model_ref

    row = service._metadata(conversation_id)
    model = model_choice_value(row.get("model_override") or get_current_model())
    parsed = parse_model_ref(model) if model else None
    return {"provider_id": parsed[0], "model_ref": model} if parsed else None


def ask_row_bot(editor: catalog.Editor, arguments: dict, *, service: Any = None,
                wait_seconds: float = catalog.ANSWER_WAIT_SECONDS) -> ToolResult:
    from row_bot.application.client_platform import ClientPlatformError

    if service is None:
        from row_bot.application.client_platform import client_platform_service as service
    message = arguments.get("message")
    if not isinstance(message, str) or not message.strip():
        return ToolResult("Say what to ask Row-Bot in message.", True)
    if len(message) > catalog.MAX_MESSAGE_CHARS:
        return ToolResult(f"The message is too long (at most {catalog.MAX_MESSAGE_CHARS} characters).", True)
    raw_folder = arguments.get("folder")
    folder = _folder(raw_folder)
    requested = arguments.get("conversation_id")
    notes: list[str] = []
    if requested not in (None, ""):
        if not isinstance(requested, str) or len(requested) > 128 or not _continuable(requested):
            return ToolResult(catalog.NOT_CONTINUABLE, True)
        conversation_id = requested
        # Reached from an editor from now on: never Hands-off.
        store.remember(conversation_id, editor.id)
    else:
        conversation_id = _chat(editor, folder)
    title = str(service._metadata(conversation_id).get("name") or "")
    if isinstance(raw_folder, str) and raw_folder.strip() and (
            folder is None or not _bind_code_folder(service, conversation_id, folder)):
        notes.append(catalog.folder_not_used(raw_folder.strip()[:300]))
    if service.registry.active(conversation_id):
        return ToolResult(catalog.BUSY, True)
    if service._approval_waiting(conversation_id):
        return ToolResult(catalog.waiting(_waiting_summary(conversation_id)))
    selection = _model_selection(service, conversation_id)
    if selection is None:
        return ToolResult(catalog.NO_MODEL, True)
    submission_id = str(uuid.uuid4())
    command = {"command_id": str(uuid.uuid4()), "client_session_id": f"editor-{editor.id}",
               "type": "conversation.submit",
               "expected_revision": str(service._metadata(conversation_id)["client_revision"]),
               "payload": {"submission_id": submission_id, "text": message, "attachment_refs": [],
                           "model_selection": selection}}
    try:
        started = service.execute(owner_id=f"editor:{editor.id}", idempotency_key=str(uuid.uuid4()),
                                  command=command, target=conversation_id, runtime_surface="remote_client",
                                  editor={"id": editor.id, "name": editor.name})
    except ClientPlatformError as exc:
        if exc.code == "generation_active":
            return ToolResult(catalog.BUSY, True)
        if exc.code in {"model_selection_required", "model_configuration_required"}:
            return ToolResult(catalog.NO_MODEL, True)
        logger.warning("An editor's message to %s wasn't admitted (%s)", conversation_id[:8], exc.code)
        return ToolResult(catalog.failed(exc.code.replace("_", " ")), True)
    handle = service.registry.get(str(started.get("execution_id") or ""))
    deadline = time.monotonic() + max(0.0, wait_seconds)
    if handle is not None:
        # The bridge keeps the editor posted meanwhile (MCP progress notifications).
        if not handle.producer_done.wait(max(0.0, deadline - time.monotonic())):
            return ToolResult(_with_notes(catalog.still_working(title, conversation_id), notes))
        settle = time.monotonic() + 5
        while handle.status in {"running", "stopping"} and time.monotonic() < settle:
            time.sleep(0.05)
        status = handle.status
    else:
        status = "completed"
    if status == "waiting_approval" or service._approval_waiting(conversation_id):
        return ToolResult(_with_notes(catalog.waiting(_waiting_summary(conversation_id)), notes))
    if status == "stopped":
        return ToolResult(_with_notes(catalog.STOPPED, notes), True)
    if status not in {"completed"}:
        reason = getattr(handle, "failure_reason", "") if handle is not None else ""
        return ToolResult(_with_notes(catalog.failed(reason), notes), True)
    reply = _final_reply(conversation_id, submission_id) or catalog.NO_ANSWER
    return ToolResult(_with_notes(reply, notes))


def _with_notes(text: str, notes: list[str]) -> str:
    return "\n\n".join([text, *notes]) if notes else text
