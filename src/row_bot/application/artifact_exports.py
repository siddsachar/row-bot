"""Explicit bound artifact exports, independent of the conversation command lock."""
from __future__ import annotations

from collections.abc import Callable
import threading
from typing import Any

from row_bot.application.client_platform import ClientPlatformError
from row_bot.runtime import admissions

_LOCK = threading.Lock()


def execute_export(service: Any, command: dict, conversation_id: str, *, owner_id: str,
                   key: str, validate: Callable[[], None]) -> dict:
    from row_bot.conversation_resources import list_bindings
    from row_bot.designer.client_exports import create_export
    from row_bot.designer.client_service import ArtifactError
    payload = command["payload"]
    target = payload["target"]

    def authority() -> None:
        validate()
        if admissions.deletion_state(conversation_id) != "active":
            raise ClientPlatformError("conversation_deleting")
        service._metadata(conversation_id)
        binding = next((item for item in list_bindings(conversation_id).bindings
                        if item.binding_id == target["binding_id"]), None)
        if (binding is None or binding.kind != "artifact" or binding.resource_id != target["resource_id"]
                or binding.revision != target["binding_revision"]):
            raise ClientPlatformError("resource_binding_revoked")

    authority()
    # There is no unbounded queue of renderer threads, and stop/chat commands
    # retain their own admission while one renderer owns this operation.
    if not _LOCK.acquire(blocking=False):
        raise ClientPlatformError("export_busy")
    try:
        authority()
        replaying = False
        try:
            prior = admissions.claim_command(owner_id, key, command, conversation_id)
        except admissions.AdmissionError as exc:
            if str(exc) != "operation_uncertain":
                raise ClientPlatformError(str(exc), exc.current_revision) from exc
            prior = None
            replaying = True
        if prior is not None:
            return prior
        if not replaying:
            metadata = service._metadata(conversation_id)
            if str(metadata["client_revision"]) != command["expected_revision"]:
                admissions.reject_command(owner_id, key, "revision_conflict", str(metadata["client_revision"]))
                raise ClientPlatformError("revision_conflict", str(metadata["client_revision"]))
        result = {"command_id": command["command_id"], "status": "admitting",
                  "conversation_id": conversation_id, "resource_id": target["resource_id"],
                  "resource_revision": target["resource_revision"], "export_id": command["command_id"]}
        admissions.command_progress(owner_id, key, result)
        try:
            exported = create_export(target["resource_id"], expected_revision=target["resource_revision"],
                export_id=command["command_id"], binding_id=target["binding_id"],
                format=payload["format"], pages=payload["pages"], pptx_mode=payload["pptx_mode"], validate=authority)
        except ArtifactError as exc:
            # An existing incomplete attempt is retained and is never rendered
            # again under the same ID. The user can review then start a new one.
            if exc.code in {"export_incomplete", "export_unavailable", "export_size_limit"}:
                result.update(status="partial", code=exc.code)
                admissions.command_progress(owner_id, key, result)
                return result
            admissions.reject_command(owner_id, key, exc.code, exc.current_revision)
            raise ClientPlatformError(exc.code, exc.current_revision) from exc
        authority()
        result.update(status="completed", export_id=exported.export_id)
        return admissions.complete_command(owner_id, key, result)
    finally:
        _LOCK.release()
