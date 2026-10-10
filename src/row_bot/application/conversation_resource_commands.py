"""Undo, Rename and Delete for the designs and code folders in a conversation.

The card the assistant shows after ``create_design`` / ``create_code_folder``
offers Undo and Rename. Undo removes only what this conversation created and
nothing else uses: a design whose owning conversation is this one, or a code
folder inside the configured Drafts folder that this conversation created and
that is still empty. Anything else is left alone (``resource_not_discardable``,
or ``resource_not_empty`` once the folder holds files); it can still be
removed from the conversation in Context. Rename changes the design's name or
the code folder's display name; the folder on disk keeps its name.

Delete (the design panel's "Delete design…") removes a design for good, after
the person confirmed it: it leaves every conversation that uses it, then its
files go. Conversations and their messages always stay.
"""
from __future__ import annotations

import json
import logging
import sqlite3
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


def _holds_anything(folder: Path) -> bool:
    """Setup made the folder empty, so anything in it now is someone's work."""
    try:
        return next(folder.iterdir(), None) is not None
    except OSError:
        return True


def discard(service: Any, conversation_id: str, binding_id: str, *, expected_revision: str) -> dict:
    """Undo a design or code folder this conversation created.

    A code folder goes only while it is still empty: Undo never deletes files
    added after setup (``resource_not_empty``), and its removal is never
    recursive, so a file that arrives meanwhile stays with its folder.
    """
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
    if folder is not None and _holds_anything(folder):
        _LOG.info("Undo kept code folder %s: it has files in it now", binding.resource_id)
        raise ClientPlatformError("resource_not_empty")
    try:
        resources = unbind(conversation_id, binding_id, expected_revision=int(expected_revision))
    except ResourceError as exc:
        raise ClientPlatformError(exc.code, exc.current_revision) from exc
    if binding.kind == "artifact":
        from row_bot.designer.storage import delete_project

        delete_project(binding.resource_id)
    else:
        from row_bot.developer.storage import delete_workspace_record

        try:
            folder.rmdir()  # type: ignore[union-attr]  # Only an empty folder; the OS refuses otherwise.
        except OSError:
            # Still on disk (something arrived, or it is locked): it stays a
            # saved code folder, so it can be opened again.
            _LOG.warning("Undo left code folder %s in place", binding.resource_id, exc_info=True)
        else:
            delete_workspace_record(binding.resource_id)
            _LOG.info("Undo removed empty code folder %s", binding.resource_id)
    from row_bot.application.conversation_followups import discard as discard_followup

    discard_followup(conversation_id, "resource")
    service.projection.publish(conversation_id, "resource.changed", {"revision": str(resources.revision)})
    return {"conversation_id": conversation_id, "revision": str(resources.revision), "status": "completed"}


def _conversations_using(resource_id: str) -> dict[str, list[str]]:
    """Each conversation binding this design, with those binding IDs.

    A conversation whose saved bindings cannot be read but mention the design
    counts as using it, so the delete is refused rather than guessed.
    """
    from row_bot import threads
    from row_bot.application.client_platform import ClientPlatformError
    from row_bot.conversation_resources import ResourceError, list_bindings

    with closing(sqlite3.connect(threads.DB_PATH)) as connection:
        rows = connection.execute(
            "SELECT thread_id FROM thread_meta WHERE COALESCE(project_id, '') = ? "
            "OR COALESCE(resource_bindings_json, '') LIKE ?",
            (resource_id, f"%{resource_id}%"),
        ).fetchall()
    using: dict[str, list[str]] = {}
    for (conversation,) in rows:
        try:
            bindings = list_bindings(str(conversation)).bindings
        except ResourceError as exc:
            if exc.code == "invalid_resource":
                continue  # An ID bindings cannot use; only its old-style link is cleared below.
            raise ClientPlatformError("resource_state_invalid") from None
        bound = [item.binding_id for item in bindings
                 if item.kind == "artifact" and item.resource_id == resource_id]
        if bound:
            using[str(conversation)] = bound
    return using


def delete_design(service: Any, conversation_id: str, binding_id: str, *,
                  expected_resource_revision: str) -> dict:
    """Delete a design for good, after the person confirmed it in its panel.

    Refused while a conversation using it is running or being deleted, and
    when the design changed since it was shown. It first leaves every
    conversation that uses it (each open page hears ``resource.changed``);
    only then do its pages, versions and files go. No conversation is ever
    deleted, whatever the design's legacy ownership (``delete_project`` would
    delete linked conversations; ``delete_project_files`` never does).

    When some files cannot be deleted the receipt is ``partial`` with
    ``design_files_remain``: the design has left its conversations, but its
    record stays, so it is still in Open saved and deleting it again finishes.
    """
    from row_bot import threads
    from row_bot.application.client_platform import ClientPlatformError
    from row_bot.application.conversation_followups import discard as discard_followup
    from row_bot.conversation_resources import ResourceError, describe, list_bindings, unbind
    from row_bot.designer.storage import delete_project_files

    binding = _binding(conversation_id, binding_id)
    if binding.kind != "artifact":
        raise ClientPlatformError("invalid_resource")
    design = binding.resource_id
    descriptor = describe(binding)
    if not descriptor.available:
        raise ClientPlatformError("resource_unavailable")
    if descriptor.resource_revision != expected_resource_revision:
        raise ClientPlatformError("resource_revision_conflict")
    using = _conversations_using(design)
    if any(service.registry.active(conversation) for conversation in using):
        raise ClientPlatformError("generation_active")
    if any(threads._thread_write_blocked(conversation) for conversation in using):
        raise ClientPlatformError("conversation_deleting")
    revision = ""
    for conversation, bound in using.items():
        for item in bound:
            try:
                snapshot = unbind(conversation, item,
                                  expected_revision=list_bindings(conversation).revision)
            except ResourceError as exc:
                raise ClientPlatformError(exc.code, exc.current_revision) from exc
        if conversation == conversation_id:
            revision = snapshot.revision
        discard_followup(conversation, "resource")
        service.projection.publish(conversation, "resource.changed", {"revision": str(snapshot.revision)})
    # A conversation still naming the design as its old-style design would be
    # swept away at the next start once the design's file is gone.
    with closing(sqlite3.connect(threads.DB_PATH)) as connection, connection:
        connection.execute("UPDATE thread_meta SET project_id = '' WHERE project_id = ?", (design,))
    receipt = {"conversation_id": conversation_id,
               "revision": str(revision or service._metadata(conversation_id)["client_revision"]),
               "resource_id": design, "resource_kind": "artifact", "status": "completed"}
    try:
        delete_project_files(design)
    except Exception:
        # It already left every conversation. Its record goes last, so it is
        # still in Open saved, and deleting it again from there finishes.
        _LOG.warning("Deleting a design left some of its files in place", exc_info=True)
        return {**receipt, "status": "partial", "code": "design_files_remain"}
    return receipt


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
