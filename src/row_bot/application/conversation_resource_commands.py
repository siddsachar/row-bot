"""Undo and Rename for the designs and code folders a conversation created.

The card the assistant shows after ``create_design`` / ``create_code_folder``
offers Undo and Rename. Undo removes only what this conversation created and
nothing else uses: a design whose owning conversation is this one, or a code
folder inside the configured Drafts folder that this conversation created.
Anything else is left alone (``resource_not_discardable``); it can still be
removed from the conversation in Context. Rename changes the design's name or
the code folder's display name; the folder on disk keeps its name.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import stat
from contextlib import closing
from pathlib import Path
from typing import Any

_LOG = logging.getLogger(__name__)


def _binding(conversation_id: str, binding_id: str) -> Any:
    from row_bot.application.client_platform import ClientPlatformError
    from row_bot.conversation_resources import list_bindings

    binding = next((item for item in list_bindings(conversation_id).bindings
                    if item.binding_id == binding_id), None)
    if binding is None:
        raise ClientPlatformError("resource_binding_revoked")
    return binding


def _bound_elsewhere(conversation_id: str, resource_id: str) -> bool:
    from row_bot import threads

    with closing(sqlite3.connect(threads.DB_PATH)) as connection:
        rows = connection.execute(
            "SELECT thread_id, resource_bindings_json FROM thread_meta WHERE thread_id != ? "
            "AND COALESCE(resource_bindings_json, '') LIKE ?",
            (conversation_id, f"%{resource_id}%"),
        ).fetchall()
    for _, raw in rows:
        try:
            if any(item.get("resource_id") == resource_id for item in json.loads(raw or "[]")):
                return True
        except (TypeError, ValueError, AttributeError):
            return True
    return False


def _created_design(conversation_id: str, resource_id: str) -> bool:
    from row_bot.designer.storage import load_project

    project = load_project(resource_id)
    return bool(project is not None and project.thread_id == conversation_id
                and project.thread_ownership == "resume")


def _created_draft(conversation_id: str, resource_id: str) -> Path | None:
    from row_bot.application.conversation_creation import configured_workspace_root
    from row_bot.developer.storage import get_workspace

    workspace = get_workspace(resource_id)
    if workspace is None or workspace.origin_conversation_id != conversation_id:
        return None
    try:
        drafts = (configured_workspace_root() / "Drafts").resolve(strict=True)
        folder = Path(workspace.path)
        if folder.is_symlink() or not folder.is_dir():
            return None
        resolved = folder.resolve(strict=True)
    except (OSError, ValueError):
        return None
    return resolved if resolved.parent == drafts else None


def _remove_tree(folder: Path) -> None:
    def writable(function: Any, path: str, _error: Any) -> None:
        os.chmod(path, stat.S_IWRITE)
        function(path)

    shutil.rmtree(folder, onexc=writable)


def discard(service: Any, conversation_id: str, binding_id: str, *, expected_revision: str) -> dict:
    """Undo a design or code folder this conversation created."""
    from row_bot.application.client_platform import ClientPlatformError
    from row_bot.conversation_resources import ResourceError, unbind

    if service.registry.active(conversation_id):
        raise ClientPlatformError("generation_active")
    binding = _binding(conversation_id, binding_id)
    folder: Path | None = None
    if binding.kind == "artifact":
        allowed = _created_design(conversation_id, binding.resource_id)
    else:
        folder = _created_draft(conversation_id, binding.resource_id)
        allowed = folder is not None
    if not allowed or _bound_elsewhere(conversation_id, binding.resource_id):
        raise ClientPlatformError("resource_not_discardable")
    try:
        resources = unbind(conversation_id, binding_id, expected_revision=int(expected_revision))
    except ResourceError as exc:
        raise ClientPlatformError(exc.code, exc.current_revision) from exc
    if binding.kind == "artifact":
        from row_bot.designer.storage import delete_project

        delete_project(binding.resource_id)
    else:
        from row_bot.developer.storage import delete_workspace_record

        delete_workspace_record(binding.resource_id)
        try:
            _remove_tree(folder)  # type: ignore[arg-type]
        except OSError:
            _LOG.warning("Undo left the draft folder in place", exc_info=True)
    from row_bot.application.conversation_followups import discard as discard_followup

    discard_followup(conversation_id, "resource")
    service.projection.publish(conversation_id, "resource.changed", {"revision": str(resources.revision)})
    return {"conversation_id": conversation_id, "revision": str(resources.revision), "status": "completed"}


def rename(service: Any, conversation_id: str, binding_id: str, name: str) -> dict:
    """Rename a bound design, or a code folder's display name."""
    from row_bot.application.client_platform import ClientPlatformError

    title = " ".join(str(name or "").split())[:120]
    if not title:
        raise ClientPlatformError("invalid_command")
    binding = _binding(conversation_id, binding_id)
    if binding.kind == "artifact":
        from row_bot.designer.storage import load_project, save_project

        project = load_project(binding.resource_id)
        if project is None:
            raise ClientPlatformError("resource_unavailable")
        project.name = title
        try:
            save_project(project)
        except Exception as exc:  # A newer copy was saved meanwhile.
            raise ClientPlatformError("resource_revision_conflict") from exc
    else:
        from row_bot.developer.storage import get_workspace, save_workspace

        workspace = get_workspace(binding.resource_id)
        if workspace is None:
            raise ClientPlatformError("resource_unavailable")
        workspace.name = title
        workspace.touch()
        save_workspace(workspace)
    revision = str(service._metadata(conversation_id)["client_revision"])
    service.projection.publish(conversation_id, "resource.changed", {"revision": revision})
    return {"conversation_id": conversation_id, "revision": revision, "status": "completed"}
