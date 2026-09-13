"""Explicit edits of a currently bound artifact, independent of Studio selection."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from row_bot.application.client_platform import ClientPlatformError
from row_bot.runtime import admissions


def edit_artifact(service: Any, command: dict, conversation_id: str, *,
                  validate: Callable[[], None] | None = None) -> dict:
    from row_bot.conversation_resources import list_bindings
    from row_bot.designer.client_editing import apply_edit
    from row_bot.designer.client_service import ArtifactError

    payload = command["payload"]
    target = payload["target"]

    def current_authority() -> None:
        if validate is not None:
            validate()
        if admissions.deletion_state(conversation_id) != "active":
            raise ClientPlatformError("conversation_deleting")
        metadata = service._metadata(conversation_id)
        if str(metadata["client_revision"]) != command["expected_revision"]:
            raise ClientPlatformError("revision_conflict", str(metadata["client_revision"]))
        binding = next((item for item in list_bindings(conversation_id).bindings
                        if item.binding_id == target["binding_id"]), None)
        if (binding is None or binding.kind != "artifact" or target["kind"] != "artifact"
                or binding.resource_id != target["resource_id"] or binding.revision != target["binding_revision"]):
            raise ClientPlatformError("resource_binding_revoked")

    current_authority()
    try:
        project = apply_edit(target["resource_id"], expected_revision=target["resource_revision"],
                             validate=current_authority, **{key: value for key, value in payload.items() if key != "target"})
    except ArtifactError as exc:
        raise ClientPlatformError(exc.code, exc.current_revision) from exc
    service.projection.publish(conversation_id, "resource.changed", {"revision": command["expected_revision"]})
    return {"conversation_id": conversation_id, "revision": command["expected_revision"],
            "resource_id": project.id, "resource_revision": project.updated_at, "status": "completed"}
