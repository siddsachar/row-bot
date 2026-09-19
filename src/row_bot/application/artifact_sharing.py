"""Bound explicit sharing with durable progress; uncertain delivery never repeats."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
import threading
import hashlib
import json
from typing import Any

from row_bot.application.client_platform import ClientPlatformError
from row_bot.runtime import admissions

_LOCK = threading.Lock()
STAGES = frozenset({"publication_prepared", "publish_file_started", "publish_file_completed",
    "publish_metadata_completed", "tunnel_started", "tunnel_completed", "tunnel_unavailable",
    "send_started", "send_submitted", "send_not_started", "send_uncertain"})


def sharing_channels(cursor: str | None = None) -> dict:
    """Read registered adapter status without discovery or channel startup."""
    from row_bot.channels.registry import all_channels
    items = []
    for channel in all_channels():
        name, label = channel.name, channel.display_name
        if not isinstance(name, str) or not 1 <= len(name) <= 128 or not isinstance(label, str) or len(label) > 256:
            raise ClientPlatformError("channel_unavailable")
        try:
            available = bool(channel.is_running() and channel.is_configured())
        except Exception:
            available = False
        items.append({"name": name, "label": label, "available": available})
    items.sort(key=lambda item: item["name"])
    revision = hashlib.sha256(json.dumps(items, sort_keys=True).encode()).hexdigest()
    offset = 0
    if cursor:
        try:
            prior, raw = cursor.split(":")
            offset = int(raw)
            if prior != revision or not 0 < offset < len(items) or offset % 50 or len(cursor) > 256:
                raise ValueError
        except (ValueError, TypeError):
            raise ClientPlatformError("cursor_expired") from None
    return {"items": items[offset:offset + 50], "revision": revision,
            "next_cursor": f"{revision}:{offset + 50}" if offset + 50 < len(items) else None}


def execute_sharing(service: Any, command: dict, conversation_id: str, *, owner_id: str,
                    key: str, validate: Callable[[], None]) -> dict:
    from row_bot.conversation_resources import list_bindings
    from row_bot.designer.client_sharing import execute_share
    from row_bot.designer.client_service import ArtifactError

    payload, target = command["payload"], command["payload"]["target"]

    def authority() -> None:
        validate()
        if admissions.deletion_state(conversation_id) != "active":
            raise ClientPlatformError("conversation_deleting")
        metadata = service._metadata(conversation_id)
        if str(metadata["client_revision"]) != command["expected_revision"]:
            raise ClientPlatformError("revision_conflict", str(metadata["client_revision"]))
        binding = next((item for item in list_bindings(conversation_id).bindings
                        if item.binding_id == target["binding_id"]), None)
        if (binding is None or binding.kind != "artifact" or binding.resource_id != target["resource_id"]
                or binding.revision != target["binding_revision"]):
            raise ClientPlatformError("resource_binding_revoked")

    authority()
    if not _LOCK.acquire(blocking=False):
        raise ClientPlatformError("sharing_busy")
    try:
        authority()
        try:
            prior = admissions.claim_command(owner_id, key, command, conversation_id)
        except admissions.AdmissionError as exc:
            if str(exc) != "operation_uncertain":
                raise ClientPlatformError(str(exc), exc.current_revision) from exc
            prior = admissions.receipt(owner_id, command["command_id"])
            # No resend/render/tunnel attempt, even if the transport vanished
            # before the owner could durably record its last effect.
            return {**(prior or {}), "command_id": command["command_id"],
                    "status": "partial", "code": "sharing_outcome_uncertain"}
        if prior is not None:
            return prior
        result = {"command_id": command["command_id"], "status": "admitting",
                  "conversation_id": conversation_id, "resource_id": target["resource_id"],
                  "resource_revision": target["resource_revision"]}

        def checkpoint(progress: dict) -> None:
            stage = progress.get("stage")
            if stage not in STAGES:
                raise ClientPlatformError("sharing_incomplete")
            result["share_progress"] = {"stage": stage, "index": progress["index"], "count": progress["count"]}
            admissions.command_progress(owner_id, key, result)

        try:
            from row_bot.designer.client_service import read_artifact
            if read_artifact(target["resource_id"]).updated_at != target["resource_revision"]:
                raise ClientPlatformError("resource_revision_conflict")
            outcome = execute_share(target["resource_id"], review_id=payload["review_id"],
                command_id=command["command_id"], validate=authority, checkpoint=checkpoint, **payload["options"])
        except (ArtifactError, ClientPlatformError) as exc:
            if result.get("share_progress"):
                result.update(status="partial", code="sharing_outcome_uncertain")
                admissions.command_progress(owner_id, key, result)
                return result
            admissions.reject_command(owner_id, key, exc.code)
            raise ClientPlatformError(exc.code) from exc
        result.update(status="completed" if outcome.status in {"published", "submitted", "denied"} else "partial",
                      share_outcome=asdict(outcome))
        if outcome.resource_revision != target["resource_revision"]:
            service.projection.publish(conversation_id, "resource.changed", {"revision": command["expected_revision"]})
        if result["status"] == "completed":
            return admissions.complete_command(owner_id, key, result)
        admissions.command_progress(owner_id, key, result)
        return result
    finally:
        _LOCK.release()
