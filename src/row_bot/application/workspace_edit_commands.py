"""Bound inline file editing with private recovery in canonical command receipts."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from typing import Any

from row_bot.application.client_platform import ClientPlatformError
from row_bot.runtime import admissions


def public_receipt(value: dict) -> dict:
    return {key: item for key, item in value.items() if key != "_workspace_edit"}


def execute_workspace_edit(service: Any, command: dict, conversation_id: str, *,
                           owner_id: str, key: str, validate: Callable[[], None]) -> dict:
    """Called under the existing command lock; file writer ownership stays native."""
    from row_bot.conversation_resources import list_bindings
    from row_bot.developer.client_edits import WorkspaceEditRecovery, save_workspace_text
    from row_bot.developer.edits import FileEditRecovery
    from row_bot.thread_cleanup import is_thread_deleting
    payload, recovery = command["payload"], None
    target = payload["target"]
    validate()
    previous = None
    try:
        previous = admissions.claim_command(owner_id, key, command, conversation_id)
    except admissions.AdmissionError as exc:
        if str(exc) != "operation_uncertain":
            raise ClientPlatformError(str(exc), exc.current_revision) from exc
        previous = admissions.receipt(owner_id, command["command_id"])
        if not previous or not previous.get("_workspace_edit"):
            raise ClientPlatformError("operation_uncertain") from exc
    if previous and previous.get("status") == "completed":
        return public_receipt(previous)
    if previous:
        raw = previous["_workspace_edit"]
        try:
            recovery = WorkspaceEditRecovery(**{**raw, "file": FileEditRecovery(**raw["file"])})
        except (ValueError, TypeError, KeyError):
            raise ClientPlatformError("operation_uncertain") from None
    progress = {"command_id": command["command_id"], "conversation_id": conversation_id,
                "resource_id": target["resource_id"], "status": "admitting",
                **({"_workspace_edit": asdict(recovery)} if recovery else {})}

    def authority() -> None:
        validate()
        if is_thread_deleting(conversation_id, initialized_read=True):
            raise ClientPlatformError("conversation_deleting")
        service._metadata(conversation_id)
        binding = next((item for item in list_bindings(conversation_id).bindings
                        if item.binding_id == target["binding_id"]), None)
        if (binding is None or binding.kind != "workspace" or binding.resource_id != target["resource_id"]
                or binding.revision != target["binding_revision"]):
            raise ClientPlatformError("resource_binding_revoked")

    def persist(value: WorkspaceEditRecovery) -> None:
        authority()
        progress["_workspace_edit"] = asdict(value)
        admissions.command_progress(owner_id, key, progress)

    authority()
    if previous is None:
        current = service._metadata(conversation_id)["client_revision"]
        if str(current) != command["expected_revision"]:
            admissions.reject_command(owner_id, key, "revision_conflict", str(current))
            raise ClientPlatformError("revision_conflict", str(current))
    result = save_workspace_text(target["resource_id"], conversation_id, payload["relative_path"], payload["content"],
        expected_resource_revision=target["resource_revision"], expected_binding_id=target["binding_id"],
        expected_binding_revision=target["binding_revision"], expected_digest=payload["file_digest"], expected_review_token=payload["review_token"],
        command_id=command["command_id"], persist_recovery=persist, recovery=recovery, validate=authority)
    progress.update(status="partial" if result.status == "partial" else "completed", workspace_edit=asdict(result))
    if result.status == "partial":
        admissions.command_progress(owner_id, key, progress)
    else:
        admissions.complete_command(owner_id, key, progress)
    return public_receipt(progress)
