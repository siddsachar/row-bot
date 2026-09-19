"""Authenticated JSON commands and observational SSE over application services."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime, timezone
import json
import logging
import threading
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import ValidationError
from starlette.responses import JSONResponse, Response, StreamingResponse

from row_bot.access.middleware import AccessMiddleware
from row_bot.access.request_context import (
    ACCESS_CONTEXT_SCOPE_KEY,
    AccessContext,
    request_origin_matches,
)
from row_bot.api.v1.schemas import (
    Acknowledgement,
    Command,
    EVENT_LIMIT,
    Event,
    Handshake,
    JSON_LIMIT,
    Problem,
    PROTOCOL_VERSION,
)
from row_bot.api.v1.security import (
    ClientSecurity,
    ProtocolError,
    current_policy_snapshot,
)
from row_bot.api.v1 import schemas as dto

HEADERS = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    "X-Content-Type-Options": "nosniff",
}
logger = logging.getLogger(__name__)
_STATUS = {
    "not_found": 404,
    "cursor_expired": 410,
    "invalid_command": 422,
    "invalid_task_query": 422,
    "task_metadata_unavailable": 503,
    "invalid_knowledge_query": 422,
    "checkpoint_unavailable": 503,
    "model_selection_required": 422,
    "authentication_required": 401,
    "action_denied": 403,
    "upload_expired": 410,
    "upload_incomplete": 409,
    "payload_too_large": 413,
    "rate_limited": 429,
    "model_selection_mismatch": 422,
    "invalid_resource": 422,
}
_STATUS.update(
    {
        "approval_required": 409,
        "conversation_deleting": 409,
        "reasoning_model_mismatch": 422,
        "invalid_reasoning_selection": 422,
        "reasoning_capabilities_changed": 409,
        "reasoning_snapshot_unavailable": 409,
        "resource_state_invalid": 503,
        "capability_unavailable": 403,
        "resource_unavailable": 404,
        "resource_revision_conflict": 409,
        "resource_ambiguous": 409,
        "resource_limit": 413,
        "resource_binding_revoked": 403,
    }
)
_STATUS.update(
    {
        "invalid_settings_command": 422,
        "settings_action_unavailable": 409,
        "settings_changed": 409,
        "settings_review_changed": 409,
        "settings_unavailable": 503,
        "settings_save_unconfirmed": 409,
    }
)


_STATUS.update(
    dict.fromkeys(
        (
            "origin_repair_required",
            "workspace_identity_conflict",
            "cursor_revision_conflict",
            "directory_revision_conflict",
            "snapshot_revision_conflict",
            "draft_revision_conflict",
        ),
        409,
    )
)
_STATUS.update(
    dict.fromkeys(
        (
            "workspace_path_denied",
            "folder_selection_denied",
            "path_denied",
            "workspace_read_hooks_unavailable",
        ),
        403,
    )
)
_STATUS.update(
    dict.fromkeys(
        (
            "invalid_limit",
            "invalid_cursor",
            "invalid_template",
            "invalid_canvas",
            "invalid_setup",
            "folder_selection_required",
        ),
        422,
    )
)
_STATUS.update(
    dict.fromkeys(
        (
            "conversation_unavailable",
            "change_set_unavailable",
            "page_unavailable",
            "artifact_type_unavailable",
        ),
        404,
    )
)
_STATUS.update(
    dict.fromkeys(
        ("resource_too_large", "preview_too_large", "preview_page_limit"), 413
    )
)
_STATUS.update(dict.fromkeys(("inspector_unavailable", "draft_save_failed"), 503))
_STATUS.update(
    dict.fromkeys(
        ("queue_revision_conflict", "queue_pending", "queue_requires_resume"), 409
    )
)
_STATUS.update({"queue_content_unavailable": 503, "queue_capacity": 413})
_STATUS.update({"profile_unavailable": 409, "profile_snapshot_unavailable": 503})
_STATUS.update(dict.fromkeys(("invalid_fields", "invalid_query"), 422))
_STATUS.update(
    dict.fromkeys(
        (
            "goal_revision_conflict",
            "goal_review_stale",
            "goal_action_unavailable",
            "profile_revision_conflict",
            "profile_review_stale",
            "profile_slug_conflict",
            "profile_read_only",
        ),
        409,
    )
)
_STATUS.update(
    {
        "goal_library_too_large": 413,
        "profile_library_too_large": 413,
        "receipt_unavailable": 404,
        "goal_operation_unconfirmed": 409,
        "profile_operation_unconfirmed": 409,
        "profile_mutation_rejected": 409,
    }
)
_STATUS.update(
    {
        "invalid_conversation_action": 422,
        "conversation_archive_unavailable": 409,
        "conversation_review_changed": 409,
        "conversation_transcript_changed": 409,
        "conversation_action_unconfirmed": 409,
        "conversation_export_unconfirmed": 409,
        "conversation_export_too_large": 413,
        "conversation_state_unavailable": 503,
        "conversation_transcript_unavailable": 503,
    }
)
_STATUS.update(
    dict.fromkeys(
        (
            "developer_repository_revision_conflict",
            "developer_repository_outcome_uncertain",
            "developer_repository_action_unavailable",
            "git_status_unavailable",
            "developer_runtime_status_unavailable",
            "developer_worktree_status_unavailable",
            "sandbox_history_unavailable",
            "git_root_required",
            "clean_git_root_required",
            "dirty_git_root_required",
            "git_remote_required",
            "worktree_exists",
            "worktree_unavailable",
            "sandbox_busy_or_pending_import",
            "docker_sandbox_required",
            "git_push_failed",
            "pull_request_failed",
            "worktree_create_failed",
        ),
        409,
    )
)
_STATUS.update(
    {
        "invalid_developer_repository_command": 422,
        "invalid_branch_name": 422,
        "developer_repository_action_denied": 403,
        "git_read_hooks_unavailable": 403,
        "developer_repository_receipt_unavailable": 404,
        "no_recoverable_repository_delete_owner": 409,
        "use_workspace_setup": 409,
        "use_workspace_process_review": 409,
    }
)
_STATUS.update(
    {
        "invalid_browser_command": 422,
        "invalid_browser_url": 422,
        "browser_action_denied": 403,
        "browser_navigation_denied": 403,
        "browser_receipt_unavailable": 404,
        "browser_revision_conflict": 409,
        "browser_session_inactive": 409,
        "browser_window_unavailable": 409,
        "browser_action_failed": 409,
        "browser_outcome_uncertain": 409,
        "browser_status_unavailable": 503,
        "exact_page_target_contract_required": 409,
        "exact_page_target_and_hidden_text_contract_required": 409,
        "semantic_page_observation_contract_required": 409,
        "owned_tab_identity_contract_required": 409,
        "private_preview_export_unavailable": 409,
        "use_computer_use_for_external_browser": 409,
    }
)
_STATUS.update(
    {"invalid_edit": 422, "element_unavailable": 409, "history_unavailable": 409}
)
_STATUS["invalid_preview_identity"] = 422
_STATUS.update(
    {
        "invalid_design_control": 422,
        "design_catalog_unavailable": 409,
        "design_catalog_too_large": 413,
        "design_review_unavailable": 409,
        "design_review_too_large": 413,
        "font_unavailable": 409,
    }
)
_STATUS["editing_record_too_large"] = 413
_STATUS.update(
    {
        "artifact_busy": 429,
        "artifact_design_unconfirmed": 409,
        "invalid_design_checkpoint": 409,
        "upload_unavailable": 409,
        "upload_identity_conflict": 409,
        "asset_unavailable": 404,
        "asset_type_unavailable": 415,
        "asset_content_unsafe": 422,
        "asset_too_large": 413,
        "asset_still_referenced": 409,
        "asset_already_on_page": 409,
        "asset_identity_conflict": 409,
        "design_preset_unavailable": 409,
        "design_preset_exists": 409,
        "design_preset_builtin": 409,
        "design_preset_logo_unavailable": 409,
        "design_finding_unavailable": 409,
    }
)
_STATUS.update(
    {
        "invalid_export": 422,
        "invalid_page_range": 422,
        "export_busy": 429,
        "export_conflict": 409,
        "export_incomplete": 409,
        "export_expired": 410,
        "export_unavailable": 404,
        "export_storage_unavailable": 503,
        "export_capacity_reached": 413,
        "export_size_limit": 413,
    }
)
_STATUS.update(
    dict.fromkeys(
        (
            "invalid_task_fields",
            "invalid_task_schedule",
            "invalid_task_identity",
            "invalid_task_revision",
        ),
        422,
    )
)
_STATUS.update(
    dict.fromkeys(
        (
            "task_revision_conflict",
            "task_creation_conflict",
            "task_advanced_edit_required",
            "task_delivery_review_required",
            "task_review_unsupported_fields",
        ),
        409,
    )
)
_STATUS.update(
    {
        "task_not_found": 404,
        "task_metadata_too_large": 413,
        "task_schedule_unconfirmed": 503,
        "task_saved_read_unconfirmed": 503,
    }
)
_STATUS.update(
    {
        "invalid_task_graph": 422,
        "task_graph_invalid_reference": 422,
        "task_graph_cycle": 422,
        "task_graph_missing_subtask": 422,
        "task_graph_too_large": 413,
        "task_graph_unavailable": 409,
        "task_graph_read_unconfirmed": 503,
    }
)
_STATUS.update(
    {
        "invalid_task_settings": 422,
        "task_settings_profile_conflict": 409,
        "task_settings_profile_unavailable": 409,
        "task_settings_model_unavailable": 409,
        "task_settings_unavailable": 409,
        "task_settings_too_large": 413,
        "task_settings_read_unconfirmed": 503,
        "task_webhook_unavailable": 409,
    }
)
_STATUS.update(
    {
        "invalid_share": 422,
        "share_review_changed": 409,
        "sharing_busy": 429,
        "sharing_incomplete": 409,
        "sharing_outcome_uncertain": 409,
        "channel_unavailable": 409,
        "recipient_unavailable": 409,
        "delivery_unavailable": 409,
        "sharing_media_limit": 413,
        "interactive_publish_requires_all_pages": 422,
    }
)
_STATUS.update(
    {
        "task_run_not_found": 404,
        "task_run_metadata_unavailable": 503,
        "task_policy_unavailable": 409,
        "task_policy_revision_conflict": 409,
        "task_approval_unconfirmed": 409,
        "task_run_unconfirmed": 409,
        "invalid_task_approval": 422,
    }
)
_STATUS["invalid_catalog_query"] = 422
_STATUS.update(
    dict.fromkeys(
        ("subscription_flow_unavailable", "subscription_recovery_unavailable"), 409
    )
)
_STATUS.update(dict.fromkeys(("subscription_busy", "subscription_capacity"), 429))
_STATUS.update(
    dict.fromkeys(
        (
            "subscription_uncertain",
            "subscription_review_invalid",
            "subscription_listener_active",
            "subscription_cancelled",
            "subscription_expired",
        ),
        409,
    )
)
_STATUS.update(
    dict.fromkeys(
        (
            "subscription_response_invalid",
            "subscription_endpoint_invalid",
            "subscription_listener_unavailable",
        ),
        503,
    )
)
_STATUS.update(
    dict.fromkeys(
        (
            "mcp_runtime_identity_changed",
            "mcp_runtime_missing",
            "mcp_runtime_disabled",
            "mcp_runtime_recovery_required",
            "mcp_runtime_review_stale",
            "mcp_runtime_unconfirmed",
            "mcp_cleanup_incomplete",
        ),
        409,
    )
)
_STATUS["mcp_runtime_unavailable"] = 503
_STATUS.update(
    dict.fromkeys(
        (
            "runtime_installation_unavailable",
            "runtime_installation_unconfirmed",
            "runtime_installation_owner_unavailable",
            "runtime_installation_policy_changed",
            "runtime_plan_unavailable",
            "runtime_plan_changed",
            "runtime_review_changed",
            "runtime_installation_pending",
        ),
        409,
    )
)
_STATUS["runtime_installation_command_unavailable"] = 404
_STATUS["mcp_policy_unavailable"] = 409
_STATUS.update(dict.fromkeys(("mcp_catalog_unavailable", "mcp_catalog_stale"), 409))
_STATUS.update(dict.fromkeys(("knowledge_changed", "knowledge_outcome_uncertain"), 409))
_STATUS.update(
    dict.fromkeys(("knowledge_missing", "knowledge_operation_unavailable"), 404)
)
_STATUS["knowledge_unavailable"] = 503
_STATUS.update(
    dict.fromkeys(
        (
            "wiki_changed",
            "wiki_disabled",
            "wiki_conflict_review_required",
            "wiki_outcome_uncertain",
            "wiki_conflict",
            "wiki_cancelled",
            "wiki_enumeration_incomplete",
        ),
        409,
    )
)
_STATUS.update(
    dict.fromkeys(("wiki_article_unavailable", "wiki_operation_unavailable"), 404)
)
_STATUS.update(dict.fromkeys(("invalid_wiki_query", "invalid_wiki_command"), 422))
_STATUS["wiki_scope_unavailable"] = 403
_STATUS.update(
    {
        "wiki_unavailable": 503,
        "wiki_source_unavailable": 503,
        "wiki_review_too_large": 413,
    }
)
_STATUS.update(
    dict.fromkeys(
        (
            "channel_review_stale",
            "channel_operation_unconfirmed",
            "channel_pairing_unconfirmed",
            "channel_revoke_unconfirmed",
            "channel_start_failed",
        ),
        409,
    )
)
_STATUS.update(
    dict.fromkeys(("channel_operation_unavailable", "action_unavailable"), 404)
)
_STATUS["channel_status_unavailable"] = 503
_STATUS.update(dict.fromkeys(("invalid_plugin_query", "invalid_plugin_command"), 422))
_STATUS.update(
    dict.fromkeys(
        (
            "plugin_changed",
            "plugin_review_changed",
            "plugin_operation_unconfirmed",
            "plugin_configuration_unconfirmed",
            "plugin_enablement_unconfirmed",
            "plugin_runtime_unavailable",
            "plugin_setup_or_test_required",
            "disable_plugin_to_configure",
            "plugin_already_disabled",
            "plugin_lifecycle_worker_unavailable",
        ),
        409,
    )
)
_STATUS.update({"plugin_not_found": 404, "plugin_catalog_unavailable": 503})
_STATUS.update(
    dict.fromkeys(
        (
            "invalid_skill_query",
            "invalid_skill_command",
            "invalid_skill_fields",
            "invalid_skill_import",
            "invalid_skill_target",
        ),
        422,
    )
)
_STATUS.update(
    dict.fromkeys(
        (
            "skill_revision_conflict",
            "skill_exists",
            "skill_proposal_changed",
            "skill_outcome_uncertain",
        ),
        409,
    )
)
_STATUS.update(dict.fromkeys(("skill_missing", "skill_operation_unavailable"), 404))
_STATUS.update(
    {
        "skill_unavailable": 503,
        "skill_proposals_unavailable": 503,
        "skills_response_too_large": 413,
    }
)
_STATUS["relation_changed"] = 409
_STATUS.update(
    dict.fromkeys(
        (
            "invalid_relation_page",
            "invalid_relation_command",
            "invalid_relation_type",
            "invalid_relation_target",
        ),
        422,
    )
)
_STATUS.update(
    dict.fromkeys(
        (
            "invalid_knowledge_target",
            "invalid_knowledge_command",
            "invalid_knowledge_fields",
        ),
        422,
    )
)
_STATUS["invalid_subscription_options"] = 422
_STATUS.update(
    dict.fromkeys(
        ("invalid_buddy_command", "invalid_buddy_preferences", "invalid_hatch_request"),
        422,
    )
)
_STATUS.update(
    dict.fromkeys(
        (
            "buddy_config_unavailable",
            "buddy_revision_conflict",
            "buddy_cursor_changed",
            "buddy_pack_unavailable",
            "buddy_review_changed",
            "buddy_outcome_uncertain",
            "buddy_policy_changed",
            "buddy_policy_unavailable",
            "buddy_account_unavailable",
            "buddy_action_blocked",
            "buddy_tool_unavailable",
            "buddy_tool_policy_denied",
            "buddy_media_capability_unavailable",
            "buddy_data_policy_denied",
            "hatch_source_changed",
            "hatch_job_unavailable",
            "hatch_worker_unavailable",
        ),
        409,
    )
)
_STATUS.update(
    {
        "buddy_command_unavailable": 404,
        "buddy_asset_unavailable": 404,
        "buddy_pack_limit": 413,
    }
)
_STATUS["file_review_conflict"] = 409
_STATUS.update({"file_revision_conflict": 409, "edit_recovery_conflict": 409})
_STATUS.update(
    {
        "workspace_undo_not_admitted": 404,
        "workspace_undo_unavailable": 404,
        "workspace_undo_review_changed": 409,
        "change_set_unavailable": 404,
        "change_set_already_reverted": 409,
        "change_set_revision_conflict": 409,
        "workspace_undo_proof_unavailable": 409,
        "workspace_undo_too_large": 413,
        "workspace_undo_approval_required": 409,
        "workspace_undo_unconfirmed": 409,
    }
)
_STATUS.update(
    {
        "workspace_import_unavailable": 404,
        "workspace_import_not_admitted": 404,
        "workspace_import_review_changed": 409,
        "sandbox_change_unavailable": 404,
        "sandbox_change_revision_conflict": 409,
        "sandbox_change_already_imported": 409,
        "sandbox_patch_unavailable": 409,
        "sandbox_import_format_unavailable": 409,
        "sandbox_import_too_large": 413,
    }
)
_STATUS["invalid_subscription_probe"] = 422
_STATUS.update(
    dict.fromkeys(
        ("subscription_probe_review_invalid", "subscription_probe_unconfirmed"), 409
    )
)
_STATUS["subscription_probe_limit"] = 413
_STATUS.update(
    dict.fromkeys(("invalid_document_target", "invalid_document_command"), 422)
)
_STATUS.update(
    dict.fromkeys(("invalid_document_queue", "invalid_document_control"), 422)
)
_STATUS["invalid_document_upload"] = 422
_STATUS.update({"document_upload_timeout": 408, "document_upload_closed": 409})
_STATUS["invalid_document_processing"] = 422
_STATUS["document_processing_denied"] = 403
_STATUS.update(
    dict.fromkeys(
        (
            "document_processing_unavailable",
            "document_processing_uncertain",
            "document_processing_authority_unavailable",
            "document_processing_authority_changed",
            "document_processing_proof_unavailable",
            "document_processing_policy_changed",
            "document_processing_policy_unavailable",
            "document_processing_model_unavailable",
            "document_processing_reasoning_unavailable",
            "document_processing_worker_unavailable",
        ),
        409,
    )
)
_STATUS.update(
    dict.fromkeys(("document_upload_unavailable", "document_upload_uncertain"), 409)
)
_STATUS.update(
    dict.fromkeys(
        (
            "document_queue_unavailable",
            "document_queue_changed",
            "document_control_unavailable",
            "document_removal_pending",
        ),
        409,
    )
)
_STATUS.update(
    dict.fromkeys(
        (
            "document_review_unavailable",
            "document_review_changed",
            "document_operation_unavailable",
            "document_outcome_uncertain",
            "document_action_rejected",
        ),
        409,
    )
)
_STATUS.update(
    dict.fromkeys(
        (
            "subscription_options_review_invalid",
            "subscription_options_unconfirmed",
            "subscription_reference_unavailable",
            "subscription_reference_changed",
        ),
        409,
    )
)
_STATUS.update(
    {
        "invalid_model_selection": 422,
        "model_configuration_unavailable": 409,
        "model_settings_unavailable": 503,
        "command_metadata_unavailable": 503,
        "provider_settings_unavailable": 503,
        "provider_credential_unconfirmed": 409,
        "provider_recovery_unavailable": 409,
        "invalid_api_key": 422,
        "provider_unavailable": 404,
    }
)
_STATUS.update(
    {
        "mcp_configuration_unavailable": 503,
        "mcp_configuration_unconfirmed": 409,
        "mcp_configuration_recovery_required": 409,
        "mcp_server_collision": 409,
        "invalid_query": 422,
        "provider_configuration_unavailable": 503,
        "invalid_provider_configuration": 422,
        "invalid_provider_endpoint_url": 422,
        "invalid_provider_extra_body": 422,
        "provider_endpoint_identity_conflict": 409,
    }
)
_STATUS.update(
    {
        "voice_session_busy": 429,
        "voice_service_busy": 503,
        "voice_session_expired": 410,
        "whisper_model_missing": 409,
        "ffmpeg_unavailable": 503,
        "unsupported_audio_type": 415,
        "empty_audio": 422,
        "malformed_audio": 422,
        "audio_duration_invalid": 422,
        "audio_decode_timeout": 408,
        "audio_receive_timeout": 408,
        "transcript_too_large": 413,
        "voice_operation_failed": 503,
    }
)
_STATUS.update(
    dict.fromkeys(
        (
            "voice_run_changed",
            "voice_output_consumed",
            "voice_output_unavailable",
            "voice_event_consumed",
            "voice_transcript_changed",
            "realtime_credential_expired",
        ),
        409,
    )
)
_STATUS.update(
    {
        "voice_exchange_consumed": 409,
        "voice_policy_changed": 409,
        "invalid_voice_sdp": 422,
        "voice_sdp_too_large": 413,
        "realtime_exchange_timeout": 504,
        "invalid_voice_event": 422,
        "voice_event_too_large": 413,
        "voice_output_too_large": 413,
        "voice_session_limit": 429,
        "realtime_auth_unavailable": 409,
        "realtime_quota_or_rate_limit": 429,
        "realtime_provider_unavailable": 503,
        "realtime_credential_unavailable": 503,
    }
)
_STATUS.update(
    {
        "workspace_name_invalid": 422,
        "workspace_creation_denied": 403,
        "workspace_registration_failed": 503,
    }
)
_STATUS.update(
    dict.fromkeys(("process_command_invalid", "process_cursor_invalid"), 422)
)
_STATUS.update(
    dict.fromkeys(
        (
            "process_review_stale",
            "process_start_unconfirmed",
            "process_cleanup_unconfirmed",
            "process_cleanup_incomplete",
            "process_history_incomplete",
            "process_owner_lost",
            "process_recovery_unavailable",
            "process_containment_unavailable",
            "sandbox_process_unavailable",
            "process_admission_failed",
        ),
        409,
    )
)
_STATUS.update(
    {
        "process_policy_denied": 403,
        "process_unavailable": 404,
        "process_history_unavailable": 503,
        "process_limit": 429,
    }
)
_STATUS.update(
    dict.fromkeys(
        (
            "workspace_destination_exists",
            "workspace_destination_not_empty",
            "workspace_recovery_conflict",
            "workspace_creation_unconfirmed",
        ),
        409,
    )
)
_STATUS.update(
    dict.fromkeys(
        (
            "invalid_queue_text",
            "invalid_submission_id",
            "invalid_queue_page",
            "invalid_queue_cursor",
        ),
        422,
    )
)


class ProtocolRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Awaitable[Response]]:
        original = super().get_route_handler()

        async def safe_handler(request: Request) -> Response:
            try:
                return await original(request)
            except (RequestValidationError, ValidationError):
                return problem(ProtocolError("invalid_command", 422))
            except Exception as exc:
                return problem(exc)

        return safe_handler


def problem(exc: Exception) -> JSONResponse:
    code = getattr(exc, "code", "dependency_unavailable")
    # Retained readers use exact ValueError codes; never expose arbitrary messages.
    if isinstance(exc, ValueError) and str(exc) in _STATUS:
        code = str(exc)
    # Only codes are public. Never interpolate exceptions or validator input.
    known = {
        "revision_conflict",
        "idempotency_mismatch",
        "idempotency_expired",
        "approval_expired",
        "approval_already_resolved",
        "cursor_expired",
        "not_found",
        "invalid_command",
        "payload_too_large",
        "rate_limited",
        "protocol_incompatible",
        "authentication_required",
        "session_expired",
        "origin_rejected",
        "action_denied",
        "capability_revoked",
        "dependency_unavailable",
        "operation_uncertain",
        "generation_active",
        "generation_not_steerable",
        "model_selection_required",
        "subscription_in_use",
        "checkpoint_unavailable",
        "upload_expired",
        "upload_incomplete",
        "model_selection_mismatch",
        "invalid_resource",
    }
    if code not in known and code not in _STATUS:
        code = "dependency_unavailable"
    status = getattr(
        exc,
        "status",
        _STATUS.get(code, 503 if code == "dependency_unavailable" else 409),
    )
    revision = getattr(exc, "revision", getattr(exc, "current_revision", None))
    revision = str(revision) if revision is not None else None
    if revision is not None and not 1 <= len(revision) <= 128:
        revision = None
    envelope = Problem(
        type=f"urn:row-bot:error:{code}",
        title=code.replace("_", " ").capitalize(),
        status=status,
        code=code,
        request_id=uuid4(),
        current_revision=revision,
        retryable=status in {429, 503},
        recovery="reload_then_review"
        if status == 409
        else "authenticate"
        if status == 401
        else "update_client"
        if status == 426
        else "retry"
        if status in {429, 503}
        else "none",
    )
    return JSONResponse(
        envelope.model_dump(mode="json", exclude_none=True),
        status_code=status,
        media_type="application/problem+json",
        headers=HEADERS,
    )


def _wire(model: Any, value: Any, *, status_code: int = 200) -> JSONResponse:
    # Validate the *outgoing* closed record, including UUID wire strings. Unknown
    # internal fields fail closed rather than leaking through JSONResponse.
    try:
        record = model.model_validate_json(json.dumps(value))
    except (ValueError, ValidationError, TypeError):
        raise ProtocolError("dependency_unavailable", 503) from None
    return JSONResponse(
        record.model_dump(mode="json", exclude_unset=True),
        status_code=status_code,
        headers=HEADERS,
    )


async def _body(request: Request, model, maximum: int = JSON_LIMIT):
    if (
        request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        != "application/json"
    ):
        raise ProtocolError("invalid_command", 422)
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > maximum:
            raise ProtocolError("payload_too_large", 413)
        data.extend(chunk)
    try:
        return model.model_validate_json(bytes(data))
    except (ValidationError, ValueError):
        raise ProtocolError("invalid_command", 422) from None


async def _context(request: Request) -> AccessContext:
    context = request.scope.get(ACCESS_CONTEXT_SCOPE_KEY)
    validate = request.scope.get("row_bot_revalidate_access")
    if validate is not None:
        context = await validate()
    if not isinstance(context, AccessContext) or not context.authenticated:
        raise ProtocolError("authentication_required", 401)
    if request.headers.get("origin") is not None and not request_origin_matches(
        context, request.scope
    ):
        raise ProtocolError("origin_rejected", 403)
    return context


def cached_choices() -> dict:
    """Read existing caches/catalogues; opening a client never refreshes a provider."""
    from row_bot.providers.model_catalog_cache import read_model_catalog_cache
    from row_bot.providers.selection import list_model_choice_options
    from row_bot.tools import registry as tool_registry
    from row_bot.plugins import registry as plugin_registry, state as plugin_state
    from row_bot.mcp_client.runtime import get_catalog_snapshot

    snapshot = read_model_catalog_cache()
    models = [
        {
            "provider_id": row["provider_id"],
            "model_ref": row["value"],
            "label": row["label"],
            "available": bool(row.get("active")),
            "unavailable_reason": "configuration_required"
            if not row.get("active")
            else None,
        }
        for row in list_model_choice_options(include_inactive=True)
    ]
    capabilities = []

    def add(identifier: str, enabled: bool, destructive: bool) -> None:
        capabilities.append(
            {
                "id": identifier,
                "available": enabled,
                "requires_approval": destructive,
                "unavailable_reason": None if enabled else "unavailable",
            }
        )

    for tool in tool_registry.get_all_tools():
        add(
            tool.name,
            tool_registry.is_enabled(tool.name),
            bool(tool.destructive_tool_names),
        )
    for manifest in plugin_registry.get_loaded_manifests():
        for tool in plugin_registry.get_plugin_tools(manifest.id):
            # Parent capability IDs preserve native aliases in the execution
            # registry; metadata reads never invoke plugin as_langchain_tools.
            add(
                tool.name,
                plugin_state.is_plugin_enabled(manifest.id),
                bool(tool.destructive_tool_names),
            )
    for tools in get_catalog_snapshot().values():
        for tool in tools:
            add(
                tool["prefixed_name"],
                bool(tool["enabled"]),
                bool(tool["requires_approval"]),
            )
    return {
        "models": models,
        "capabilities": capabilities,
        "catalog_stale": snapshot.is_stale,
    }


def create_router(
    service: Any,
    security: ClientSecurity,
    *,
    choices: Callable[[], dict] = cached_choices,
    folder_selections: Any = None,
) -> APIRouter:
    from row_bot.application.attachments import AttachmentUploads

    uploads = AttachmentUploads(clock=security.clock)
    from row_bot.application.mcp_runtime_installation import (
        McpRuntimeInstallationService,
    )

    runtime_installations = McpRuntimeInstallationService()
    voice_workers: set[asyncio.Task] = set()
    document_workers: set[asyncio.Task] = set()

    def voice_worker_finished(worker: asyncio.Task) -> None:
        voice_workers.discard(worker)
        try:
            worker.result()
        except BaseException:
            # The request owns the safe response; observe abandoned failures
            # without logging private transcription or service error content.
            pass

    if folder_selections is None:
        from row_bot.application.folder_selections import FolderSelections

        folder_selections = FolderSelections(clock=security.clock)

    @asynccontextmanager
    async def upload_lifespan(app: FastAPI) -> Any:
        async def expire_uploads() -> None:
            while True:
                await asyncio.sleep(30)
                await asyncio.to_thread(uploads.expire)

        cleanup = asyncio.create_task(expire_uploads(), name="client-upload-expiry")
        try:
            yield
        finally:
            cleanup.cancel()
            security.clear_worker_validations()
            try:
                await cleanup
            except asyncio.CancelledError:
                pass
            uploads.close()
            if not await asyncio.to_thread(runtime_installations.close):
                logger.warning(
                    "Managed runtime shutdown has admitted work still stopping"
                )
            transport = getattr(service, "dictation", None)
            if transport is not None:
                transport.close()
            if voice_workers:
                await asyncio.wait(tuple(voice_workers), timeout=5.0)
            if document_workers:
                await asyncio.wait(tuple(document_workers), timeout=5.0)
                if document_workers:
                    logger.warning(
                        "Document upload shutdown has admitted work still stopping"
                    )
            if transport is not None and not transport.close():
                logger.warning(
                    "Client dictation shutdown has admitted work still stopping"
                )

    router = APIRouter(
        prefix="/api/v1", route_class=ProtocolRoute, lifespan=upload_lifespan
    )

    async def session(request: Request, *, lane: str = "query") -> Any:
        context = await _context(request)
        current = security.session(
            context,
            request.headers.get("x-client-session", ""),
            request.headers.get("x-csrf-token", ""),
        )
        security.rate(current, lane)
        return current

    async def call(method: Callable, *args: Any, **kwargs: Any) -> Any:
        return await asyncio.to_thread(method, *args, **kwargs)

    async def readable_conversation(conversation_id: str) -> None:
        await call(service._metadata, conversation_id)
        from row_bot.runtime import admissions

        if admissions.deletion_state(conversation_id) != "active":
            raise ProtocolError("conversation_deleting", 409)

    def dispatch_validation(request: Request, current: Any) -> Callable[[], None]:
        loop = asyncio.get_running_loop()

        def validate() -> None:
            if not loop.is_running():
                raise ProtocolError("authentication_required", 401)
            pending = _context(request)
            try:
                future = asyncio.run_coroutine_threadsafe(pending, loop)
            except BaseException:
                pending.close()
                raise
            try:
                latest = future.result(timeout=5)
            except BaseException:
                future.cancel()
                raise
            security.session(latest, current.id, current.csrf)

        return validate

    async def respond(
        request: Request, model: Any, value: Any, *, status_code: int = 200
    ) -> JSONResponse:
        # Query work can wait on a store lock too. Revoke delivery if authority
        # changed while the worker was reading, even for an idempotent replay.
        latest = await _context(request)
        if request.headers.get("x-client-session"):
            security.session(
                latest,
                request.headers["x-client-session"],
                request.headers.get("x-csrf-token", ""),
            )
        return _wire(model, value, status_code=status_code)

    @router.post("/handshake")
    async def handshake(request: Request) -> JSONResponse:
        context = await _context(request)
        if not request_origin_matches(context, request.scope):
            raise ProtocolError("origin_rejected", 403)
        body = await _body(request, Handshake, 16384)
        if (
            body.protocol_major != 1
            or body.minimum_minor > 0
            or body.maximum_minor < body.minimum_minor
        ):
            raise ProtocolError("protocol_incompatible", 426)
        current = security.handshake(
            context,
            session_id=str(body.client_session_id) if body.client_session_id else None,
            group_id=str(body.client_group_id) if body.client_group_id else None,
        )
        security.rate(current, "query")
        discovery = await call(choices)
        return await respond(
            request,
            dto.HandshakeView,
            {
                "protocol_version": PROTOCOL_VERSION,
                "minimum_client_version": "1.0",
                "instance_id": security.instance_id,
                "server_epoch": service.server_epoch,
                "client_session_id": current.id,
                "client_group_id": current.group_id,
                "csrf_token": current.csrf,
                "authentication_kind": context.authentication_kind,
                "policy_revision": security.policy_revision,
                "session_ttl_seconds": max(0, int(current.expires - security.clock())),
                "native_adapter": {"available": False},
                "limits": dto.Limits().model_dump(),
                **discovery,
            },
        )

    def dictation_transport() -> Any:
        transport = getattr(service, "dictation", None)
        if transport is None:
            raise ProtocolError("capability_unavailable", 403)
        return transport

    async def dictation_owner(
        request: Request, current: Any, conversation_id: str
    ) -> Any:
        from row_bot.voice.client_transport import DictationOwner

        latest = await _context(request)
        security.session(latest, current.id, current.csrf)
        if latest.scheme != "https" and not latest.direct_loopback:
            raise ProtocolError("action_denied", 403)
        await readable_conversation(conversation_id)
        return DictationOwner(
            current.id, current.binding, conversation_id, service.server_epoch
        )

    def dictation_validation(
        request: Request, current: Any, conversation_id: str
    ) -> Callable[[], None]:
        auth = dispatch_validation(request, current)

        def validate() -> None:
            auth()
            service._metadata(conversation_id)
            from row_bot.runtime import admissions

            if admissions.deletion_state(conversation_id) != "active":
                raise ProtocolError("conversation_deleting", 409)

        return validate

    def dictation_handle(
        conversation_id: str, lease_id: str, identity: dto.DictationIdentity
    ) -> Any:
        from row_bot.voice.client_transport import DictationHandle

        value = dto.DictationHandle.model_validate_json(
            json.dumps(
                {
                    "conversation_id": conversation_id,
                    "lease_id": lease_id,
                    **identity.model_dump(mode="json"),
                }
            )
        )
        return DictationHandle(
            lease_id=str(value.lease_id),
            voice_session_id=value.voice_session_id,
            conversation_id=value.conversation_id,
            server_epoch=value.server_epoch,
        )

    def voice_header(request: Request, name: str, maximum: int) -> str:
        values = request.headers.getlist(name)
        if len(values) != 1 or not 1 <= len(values[0]) <= maximum:
            raise ProtocolError("invalid_command", 422)
        return values[0]

    def dictation_headers(request: Request) -> dto.DictationIdentity:
        session_id = voice_header(request, "x-voice-session-id", 16)
        if not session_id.isascii() or not session_id.isdecimal():
            raise ProtocolError("invalid_command", 422)
        return dto.DictationIdentity.model_validate(
            {
                "voice_session_id": int(session_id),
                "server_epoch": voice_header(request, "x-server-epoch", 128),
            }
        )

    @router.get("/voice/dictation/capability")
    async def dictation_capability(request: Request) -> JSONResponse:
        await session(request)
        transport = getattr(service, "dictation", None)
        value = (
            asdict(transport.capability())
            if transport is not None
            else {
                "schema_version": 1,
                "browser_dictation_available": False,
                "native_capture_available": False,
                "reason": "host_unavailable",
            }
        )
        return await respond(request, dto.DictationCapability, value)

    @router.post("/conversations/{conversation_id}/voice/dictation")
    @router.post("/conversations/{conversation_id}/voice/talk")
    @router.post("/conversations/{conversation_id}/voice/realtime")
    async def start_dictation(conversation_id: str, request: Request) -> JSONResponse:
        current = await session(request, lane="mutation")
        mode, transport = browser_voice_transport(request)
        body = await _body(
            request,
            dto.DictationStart if mode == "dictation" else dto.TalkStart,
            1024 if mode == "dictation" else 8192,
        )
        chat_context = (
            body.model_dump(mode="json", exclude={"request_id"})
            if mode != "dictation"
            else None
        )
        owner = await dictation_owner(request, current, conversation_id)
        cancelled = threading.Event()
        validate_owner = dictation_validation(request, current, conversation_id)
        if chat_context is not None:
            chat_context = await call(
                service.voice_admission.prepare_context,
                owner,
                chat_context,
                validate_owner,
            )
        if (
            mode == "talk"
            and await call(service.voice_admission.active, owner) is not None
        ):
            raise ProtocolError("generation_active", 409)

        def validate_start() -> None:
            if cancelled.is_set():
                raise ProtocolError("voice_session_expired", 410)
            validate_owner()
            if chat_context is not None:
                service.voice_admission.validate_context(
                    owner, chat_context, validate_owner
                )
            if cancelled.is_set():
                raise ProtocolError("voice_session_expired", 410)

        async def watch_start_disconnect() -> None:
            while True:
                if (await request.receive())["type"] == "http.disconnect":
                    cancelled.set()
                    return

        worker = asyncio.create_task(
            call(
                transport.start,
                owner,
                request_id=str(body.request_id),
                validate=validate_start,
                **({"chat_context": chat_context} if chat_context is not None else {}),
            ),
            name="client-voice-start",
        )
        voice_workers.add(worker)
        worker.add_done_callback(voice_worker_finished)
        disconnect_watch: asyncio.Task | None = None
        try:
            disconnect_watch = asyncio.create_task(
                watch_start_disconnect(), name="client-dictation-start-disconnect"
            )
            disconnect_watch.add_done_callback(voice_worker_finished)
            result = await asyncio.shield(worker)
            if cancelled.is_set():
                raise ProtocolError("voice_session_expired", 410)
            value = asdict(result)
            if mode == "realtime":
                value.update(
                    client_secret=None, exchange_available=bool(result.client_secret)
                )
            return await respond(
                request,
                dto.RealtimeStart if mode == "realtime" else voice_snapshot_dto(mode),
                value,
            )
        except BaseException:
            cancelled.set()

            # A cancelled HTTP task cannot cancel its readiness thread. Keep
            # ownership until return, then retire only its exact returned lease.
            def discard_start(completed: asyncio.Task) -> None:
                try:
                    snapshot = completed.result()
                    if mode == "realtime":
                        snapshot = snapshot.snapshot
                    transport.stop(owner, snapshot.handle, validate=lambda: None)
                except BaseException:
                    pass

            worker.add_done_callback(discard_start)
            raise
        finally:
            if disconnect_watch is not None:
                disconnect_watch.cancel()

    def browser_voice_transport(request: Request) -> tuple[str, Any]:
        mode = request.url.path.split("/voice/", 1)[1].split("/", 1)[0]
        if mode == "dictation":
            return mode, dictation_transport()
        if mode not in {"talk", "realtime"} or getattr(service, mode, None) is None:
            raise ProtocolError("dependency_unavailable", 503)
        return mode, getattr(service, mode)

    def voice_snapshot_dto(
        mode: str,
    ) -> (
        type[dto.DictationSnapshot]
        | type[dto.TalkSnapshot]
        | type[dto.RealtimeSnapshot]
    ):
        return {
            "dictation": dto.DictationSnapshot,
            "talk": dto.TalkSnapshot,
            "realtime": dto.RealtimeSnapshot,
        }[mode]

    @router.get("/conversations/{conversation_id}/voice/dictation/{lease_id}")
    @router.get("/conversations/{conversation_id}/voice/talk/{lease_id}")
    @router.get("/conversations/{conversation_id}/voice/realtime/{lease_id}")
    async def inspect_dictation(
        conversation_id: str, lease_id: str, request: Request
    ) -> JSONResponse:
        current = await session(request)
        handle = dictation_handle(conversation_id, lease_id, dictation_headers(request))
        owner = await dictation_owner(request, current, conversation_id)
        mode, transport = browser_voice_transport(request)
        result = await call(
            transport.snapshot,
            owner,
            handle,
            validate=dictation_validation(request, current, conversation_id),
        )
        return await respond(request, voice_snapshot_dto(mode), asdict(result))

    @router.post("/conversations/{conversation_id}/voice/dictation/{lease_id}/stop")
    @router.post("/conversations/{conversation_id}/voice/talk/{lease_id}/stop")
    @router.post("/conversations/{conversation_id}/voice/realtime/{lease_id}/stop")
    async def stop_dictation(
        conversation_id: str, lease_id: str, request: Request
    ) -> JSONResponse:
        current = await session(request, lane="control")
        identity = await _body(request, dto.DictationIdentity, 1024)
        handle = dictation_handle(conversation_id, lease_id, identity)
        owner = await dictation_owner(request, current, conversation_id)
        mode, transport = browser_voice_transport(request)
        result = await call(
            transport.stop,
            owner,
            handle,
            validate=dictation_validation(request, current, conversation_id),
        )
        return await respond(request, voice_snapshot_dto(mode), asdict(result))

    @router.post(
        "/conversations/{conversation_id}/voice/dictation/{lease_id}/transcribe"
    )
    @router.post("/conversations/{conversation_id}/voice/talk/{lease_id}/transcribe")
    async def transcribe_dictation(
        conversation_id: str, lease_id: str, request: Request
    ) -> JSONResponse:
        current = await session(request, lane="mutation")
        handle = dictation_handle(conversation_id, lease_id, dictation_headers(request))
        utterance = voice_header(request, "x-dictation-utterance", 36)
        mime = voice_header(request, "content-type", 128)
        length_headers = request.headers.getlist("content-length")
        declared_size = None
        if length_headers:
            length = length_headers[0]
            if (
                len(length_headers) != 1
                or not 1 <= len(length) <= 20
                or not length.isascii()
                or not length.isdecimal()
            ):
                raise ProtocolError("invalid_command", 422)
            declared_size = int(length)
        owner = await dictation_owner(request, current, conversation_id)
        mode, transport = browser_voice_transport(request)
        validate = dictation_validation(request, current, conversation_id)
        started = asyncio.get_running_loop().time()
        before = await call(transport.snapshot, owner, handle, validate=validate)
        remaining = max(
            0.0,
            before.expires_in_ms / 1000 - (asyncio.get_running_loop().time() - started),
        )
        operation = transport.begin_receive(
            owner,
            handle,
            utterance_id=utterance,
            content_type=mime,
            declared_size=declared_size,
        )
        handed_off = False
        disconnect_watch: asyncio.Task | None = None
        try:
            data = bytearray()
            async with asyncio.timeout(min(30.0, remaining)):
                iterator = request.stream().__aiter__()
                while True:
                    transport.check_receive(operation)
                    try:
                        chunk = await anext(iterator)
                    except StopAsyncIteration:
                        break
                    transport.check_receive(operation)
                    security.session(await _context(request), current.id, current.csrf)
                    if len(data) + len(chunk) > 8388608:
                        raise ProtocolError("payload_too_large", 413)
                    data.extend(chunk)
            await dictation_owner(request, current, conversation_id)
            transport.check_receive(operation)
            audio = bytes(data)
            del data
            worker = asyncio.create_task(
                call(
                    transport.complete_receive,
                    operation,
                    audio=audio,
                    validate=validate,
                ),
                name="client-dictation",
            )
            handed_off = True
            voice_workers.add(worker)
            worker.add_done_callback(voice_worker_finished)

            async def watch_disconnect() -> None:
                # The body reader is finished. An ASGI host need not cancel a
                # handler on TCP close, so explicitly revoke this operation.
                while True:
                    message = await request.receive()
                    if message["type"] == "http.disconnect":
                        transport.cancel_operation(operation)
                        return

            disconnect_watch = asyncio.create_task(
                watch_disconnect(), name="client-dictation-disconnect"
            )
            disconnect_watch.add_done_callback(voice_worker_finished)
            result = await asyncio.shield(worker)
            return await respond(
                request,
                dto.DictationResult if mode == "dictation" else dto.TalkResult,
                asdict(result),
            )
        except TimeoutError:
            transport.cancel_operation(operation)
            raise ProtocolError("audio_receive_timeout", 408) from None
        except BaseException:
            transport.cancel_operation(operation)
            raise
        finally:
            if disconnect_watch is not None:
                disconnect_watch.cancel()
            if not handed_off:
                transport.abort_receive(operation)

    @router.post("/conversations/{conversation_id}/voice/talk/{lease_id}/heartbeat")
    @router.post("/conversations/{conversation_id}/voice/realtime/{lease_id}/heartbeat")
    async def voice_heartbeat(
        conversation_id: str, lease_id: str, request: Request
    ) -> JSONResponse:
        current = await session(request, lane="acknowledgement")
        identity = await _body(request, dto.DictationIdentity, 1024)
        handle = dictation_handle(conversation_id, lease_id, identity)
        owner = await dictation_owner(request, current, conversation_id)
        mode, transport = browser_voice_transport(request)
        result = await call(
            transport.snapshot,
            owner,
            handle,
            heartbeat=True,
            validate=dictation_validation(request, current, conversation_id),
        )
        return await respond(request, voice_snapshot_dto(mode), asdict(result))

    async def voice_work(
        request: Request, operation: Callable[[], Any], cancel: Callable[[], None]
    ) -> Any:
        """Keep admitted work owned until return even if its HTTP peer leaves."""
        worker = asyncio.create_task(call(operation), name="client-voice-operation")
        voice_workers.add(worker)
        worker.add_done_callback(voice_worker_finished)

        async def watch_disconnect() -> None:
            while True:
                if (await request.receive())["type"] == "http.disconnect":
                    cancel()
                    return

        watcher = asyncio.create_task(
            watch_disconnect(), name="client-voice-operation-disconnect"
        )
        watcher.add_done_callback(voice_worker_finished)
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            cancel()
            raise
        finally:
            watcher.cancel()

    @router.get("/conversations/{conversation_id}/voice/talk/{lease_id}/run")
    @router.get("/conversations/{conversation_id}/voice/realtime/{lease_id}/run")
    async def voice_run(
        conversation_id: str, lease_id: str, request: Request
    ) -> JSONResponse:
        current = await session(request)
        handle = dictation_handle(conversation_id, lease_id, dictation_headers(request))
        owner = await dictation_owner(request, current, conversation_id)
        _, transport = browser_voice_transport(request)
        validate = dictation_validation(request, current, conversation_id)
        await call(transport.snapshot, owner, handle, validate=validate)
        result = await call(service.voice_admission.run_view, owner, handle, validate)
        return await respond(
            request, dto.VoiceRunView, {"handle": asdict(handle), **result}
        )

    @router.post("/conversations/{conversation_id}/voice/talk/{lease_id}/output")
    async def talk_output(
        conversation_id: str, lease_id: str, request: Request
    ) -> Response:
        current = await session(request, lane="mutation")
        body = await _body(request, dto.TalkOutputRequest, 2048)
        handle = dictation_handle(
            conversation_id,
            lease_id,
            dto.DictationIdentity(
                voice_session_id=body.voice_session_id, server_epoch=body.server_epoch
            ),
        )
        owner = await dictation_owner(request, current, conversation_id)
        _, transport = browser_voice_transport(request)
        validate = dictation_validation(request, current, conversation_id)
        result = await voice_work(
            request,
            lambda: transport.output(
                owner,
                handle,
                run_id=body.run_id,
                output_id=body.output_id,
                validate=validate,
            ),
            lambda: transport.stop(owner, handle, validate=lambda: None),
        )
        await call(validate)
        return Response(
            result.audio,
            media_type=result.content_type,
            headers={
                **HEADERS,
                "X-Voice-Run": result.run_id,
                "X-Voice-Output": result.output_id,
            },
        )

    @router.post("/conversations/{conversation_id}/voice/realtime/{lease_id}/exchange")
    async def realtime_exchange(
        conversation_id: str, lease_id: str, request: Request
    ) -> Response:
        current = await session(request, lane="mutation")
        handle = dictation_handle(conversation_id, lease_id, dictation_headers(request))
        if (
            voice_header(request, "content-type", 128).split(";", 1)[0].lower()
            != "application/sdp"
        ):
            raise ProtocolError("invalid_command", 422)
        owner = await dictation_owner(request, current, conversation_id)
        _, transport = browser_voice_transport(request)
        validate = dictation_validation(request, current, conversation_id)
        await call(transport.snapshot, owner, handle, validate=validate)
        data = bytearray()
        try:
            async with asyncio.timeout(30):
                async for chunk in request.stream():
                    if len(data) + len(chunk) > 1048576:
                        raise ProtocolError("payload_too_large", 413)
                    security.session(await _context(request), current.id, current.csrf)
                    data.extend(chunk)
        except TimeoutError:
            raise ProtocolError("audio_receive_timeout", 408) from None
        if not data:
            raise ProtocolError("invalid_command", 422)
        offer = bytes(data)
        del data
        result = await voice_work(
            request,
            lambda: transport.exchange(owner, handle, offer=offer, validate=validate),
            lambda: transport.stop(owner, handle, validate=lambda: None),
        )
        await call(validate)
        return Response(result, media_type="application/sdp", headers=HEADERS)

    @router.post("/conversations/{conversation_id}/voice/realtime/{lease_id}/event")
    async def realtime_event(
        conversation_id: str, lease_id: str, request: Request
    ) -> JSONResponse:
        current = await session(request, lane="observation")
        body = await _body(request, dto.RealtimeEventRequest, 18432)
        if body.event.type in {"function_call_ready", "consult_fallback_needed"}:
            security.rate(current, "mutation")
        handle = dictation_handle(
            conversation_id,
            lease_id,
            dto.DictationIdentity(
                voice_session_id=body.voice_session_id, server_epoch=body.server_epoch
            ),
        )
        owner = await dictation_owner(request, current, conversation_id)
        _, transport = browser_voice_transport(request)
        from row_bot.voice.client_realtime import RealtimeEvent

        event = RealtimeEvent(**body.event.model_dump(mode="json"))
        validate = dictation_validation(request, current, conversation_id)
        result = await voice_work(
            request,
            lambda: asyncio.run(
                transport.event(owner, handle, event, validate=validate)
            ),
            lambda: transport.stop(owner, handle, validate=lambda: None),
        )
        return await respond(request, dto.RealtimeEventResult, asdict(result))

    @router.get("/conversations")
    async def conversations(
        request: Request, limit: int = 50, cursor: str | None = None, group: str = "all"
    ) -> JSONResponse:
        await session(request)
        if not 1 <= limit <= 200 or (cursor and len(cursor) > 2048):
            raise ProtocolError("invalid_command", 422)
        return await respond(
            request,
            dto.ConversationPage,
            await call(service.list_conversations, limit, cursor, group),
        )

    @router.get("/conversations/{conversation_id}")
    async def conversation(conversation_id: str, request: Request) -> JSONResponse:
        await session(request, lane="view")
        return await respond(
            request,
            dto.ConversationView,
            await call(service.get_conversation, conversation_id),
        )

    @router.get("/conversations/{conversation_id}/actions")
    async def conversation_actions(
        conversation_id: str, request: Request
    ) -> JSONResponse:
        current = await session(request, lane="view")
        from row_bot.application.conversation_actions import read_conversation_actions

        result = await call(
            read_conversation_actions,
            service,
            conversation_id,
            validate=dispatch_validation(request, current),
        )
        return await respond(request, dto.ConversationActionSnapshot, result)

    @router.post("/conversations/{conversation_id}/actions/review")
    async def conversation_action_review(
        conversation_id: str, request: Request
    ) -> JSONResponse:
        current = await session(request, lane="mutation")
        body = await _body(request, dto.ConversationActionReviewRequest, 16 * 1024)
        from row_bot.application.conversation_actions import review_conversation_action

        result = await call(
            review_conversation_action,
            service,
            conversation_id,
            body.type,
            body.expected_revision,
            body.payload,
            validate=dispatch_validation(request, current),
        )
        result["review_id"] = security.approval_nonce(
            current,
            "conversation-actions:" + conversation_id,
            result["revision"],
            result["action_digest"],
        )
        return await respond(request, dto.ConversationActionReview, result)

    @router.get("/conversations/{conversation_id}/actions/commands/{command_id}")
    async def conversation_action_receipt(
        conversation_id: str, command_id: UUID, request: Request
    ) -> JSONResponse:
        current = await session(request, lane="view")
        from row_bot.application.conversation_actions import (
            read_conversation_action_receipt,
        )

        result = await call(
            read_conversation_action_receipt,
            service,
            conversation_id,
            str(command_id),
            owner_id=current.id,
            validate=dispatch_validation(request, current),
        )
        if result is None:
            raise ProtocolError("not_found", 404)
        return await respond(request, dto.ConversationActionReceipt, result)

    @router.post("/conversations/{conversation_id}/actions/commands")
    async def conversation_action_command(
        conversation_id: str, request: Request
    ) -> JSONResponse:
        current = await session(request, lane="mutation")
        body = await _body(request, dto.ConversationActionCommand, 32 * 1024)
        if str(body.client_session_id) != current.id:
            raise ProtocolError("invalid_conversation_action", 422)
        key = request.headers.get("idempotency-key", "")
        if key != str(body.command_id):
            raise ProtocolError("idempotency_mismatch", 409)
        wire = body.model_dump(mode="json")
        review_id = wire["payload"].pop("review_id")
        validate = dispatch_validation(request, current)

        def validate_review(review: dict) -> None:
            validate()
            security.consume_nonce(
                current,
                "conversation-actions:" + conversation_id,
                review["revision"],
                review["action_digest"],
                review_id,
                str(body.command_id),
            )

        from row_bot.application.conversation_actions import execute_conversation_action

        result = await call(
            execute_conversation_action,
            service,
            conversation_id,
            wire,
            owner_id=current.id,
            key=key,
            validate=validate,
            validate_review=validate_review,
        )
        return await respond(request, dto.ConversationActionReceipt, result)

    async def browser_control_authority(
        conversation_id: str, request: Request, current: Any
    ) -> tuple[Callable[[], None], Callable[[str], None], str]:
        await readable_conversation(conversation_id)
        access = dispatch_validation(request, current)

        def validate() -> None:
            from row_bot.runtime import admissions

            access()
            service._metadata(conversation_id)
            if admissions.deletion_state(conversation_id) != "active":
                raise ProtocolError("conversation_deleting", 409)

        def validate_action(kind: str) -> None:
            from row_bot.application.profile_controls import freeze_profile
            from row_bot.tools import registry as tool_registry
            from row_bot.tools.profile_policy import dispatch_refusal

            validate()
            if not tool_registry.is_enabled(kind):
                raise ProtocolError("browser_action_denied", 403)
            row = service._metadata(conversation_id)
            context = {
                "thread_id": conversation_id,
                "approval_mode": row.get("approval_mode"),
                "agent_profile_id": row.get("agent_profile_id") or "",
            }
            if "tool_allowlist" in row:
                context["tool_allowlist"] = row.get("tool_allowlist")
            freeze_profile(context)
            allowed = context.get("tool_allowlist")
            refusal = dispatch_refusal(
                context["agent_profile_snapshot"],
                kind,
                {},
                source="core",
                parent="browser",
                allowlist=tuple(allowed) if allowed is not None else None,
            )
            if refusal:
                raise ProtocolError("browser_action_denied", 403)

        from row_bot.runtime import admissions

        authority_id = admissions.keyed_digest(
            {
                "session": current.id,
                "conversation": conversation_id,
                "server_epoch": service.server_epoch,
            },
            read_only=True,
        )
        return validate, validate_action, authority_id

    @router.get("/conversations/{conversation_id}/browser")
    async def browser_control_snapshot(
        conversation_id: str, request: Request
    ) -> JSONResponse:
        current = await session(request, lane="view")
        validate, _validate_action, _authority_id = await browser_control_authority(
            conversation_id, request, current
        )
        from row_bot.application.client_browser_controls import read_browser_controls

        result = await call(read_browser_controls, conversation_id, validate=validate)
        return await respond(request, dto.BrowserControlSnapshot, result)

    @router.post("/conversations/{conversation_id}/browser/review")
    async def browser_control_review(
        conversation_id: str, request: Request
    ) -> JSONResponse:
        current = await session(request, lane="mutation")
        body = await _body(request, dto.BrowserReviewRequest, 16 * 1024)
        validate, validate_action, _authority_id = await browser_control_authority(
            conversation_id, request, current
        )
        from row_bot.application.client_browser_controls import review_browser_command

        result = await call(
            review_browser_command,
            body.action,
            body.payload,
            conversation_id,
            validate=validate,
        )
        await call(validate_action, result["policy_action"])
        result["nonce"] = security.approval_nonce(
            current,
            "browser-control:" + conversation_id,
            result["revision"],
            result["action_digest"],
        )
        return await respond(request, dto.BrowserReview, result)

    @router.get("/conversations/{conversation_id}/browser/commands/{command_id}")
    async def browser_control_receipt(
        conversation_id: str, command_id: UUID, request: Request
    ) -> JSONResponse:
        current = await session(request, lane="view")
        validate, _validate_action, authority_id = await browser_control_authority(
            conversation_id, request, current
        )
        from row_bot.application.client_browser_controls import read_browser_receipt

        result = await call(
            read_browser_receipt,
            owner_id=current.id,
            authority_id=authority_id,
            conversation_id=conversation_id,
            command_id=str(command_id),
            validate=validate,
        )
        return await respond(request, dto.BrowserReceipt, result)

    @router.post("/conversations/{conversation_id}/browser/commands")
    async def browser_control_command(
        conversation_id: str, request: Request
    ) -> JSONResponse:
        current = await session(request, lane="mutation")
        body = await _body(request, dto.Command, 32 * 1024)
        if not body.type.startswith("browser."):
            raise ProtocolError("invalid_browser_command", 422)
        if str(body.client_session_id) != current.id:
            raise ProtocolError("invalid_browser_command", 422)
        key = request.headers.get("idempotency-key", "")
        if key != str(body.command_id):
            raise ProtocolError("idempotency_mismatch", 409)
        wire = body.model_dump(mode="json")
        nonce = wire["payload"]["nonce"]
        validate, validate_action, authority_id = await browser_control_authority(
            conversation_id, request, current
        )

        def validate_review(command: dict[str, Any], review: dict[str, Any]) -> None:
            validate()
            if review.get("conversation_id") != conversation_id:
                raise ProtocolError("browser_action_denied", 403)
            security.consume_nonce(
                current,
                "browser-control:" + conversation_id,
                review["revision"],
                review["action_digest"],
                nonce,
                str(body.command_id),
            )

        from row_bot.application.client_browser_controls import (
            execute_browser_command,
        )

        result = await call(
            execute_browser_command,
            wire,
            conversation_id,
            owner_id=current.id,
            authority_id=authority_id,
            key=key,
            validate=validate,
            validate_action=validate_action,
            validate_review=validate_review,
        )
        return await respond(request, dto.BrowserReceipt, result)

    @router.get("/conversations/{conversation_id}/open")
    async def open_conversation(conversation_id: str, request: Request) -> JSONResponse:
        await session(request, lane="view")
        from row_bot.application.conversation_open import read_open

        result = await call(read_open, service, conversation_id)
        await readable_conversation(conversation_id)
        return await respond(request, dto.ConversationOpenView, result)

    @router.get("/conversations/{conversation_id}/transcript")
    async def transcript(
        conversation_id: str,
        request: Request,
        limit: int = 100,
        cursor: str | None = None,
    ) -> JSONResponse:
        await session(request)
        if not 1 <= limit <= 100 or (cursor and len(cursor) > 2048):
            raise ProtocolError("invalid_command", 422)
        return await respond(
            request,
            dto.TranscriptPage,
            await call(service.transcript, conversation_id, limit, cursor),
        )

    @router.get("/conversations/{conversation_id}/content/{message_id}")
    async def lazy_content(
        conversation_id: str,
        message_id: str,
        request: Request,
        limit_bytes: int = 65536,
        cursor: str | None = None,
    ) -> JSONResponse:
        await session(request)
        if not 1 <= limit_bytes <= 65536 or (cursor and len(cursor) > 2048):
            raise ProtocolError("invalid_command", 422)
        result = await call(
            service.lazy_content, conversation_id, message_id, limit_bytes, cursor
        )
        await _context(request)
        return await respond(request, dto.LazyContent, result)

    @router.get("/search")
    async def search_library(
        request: Request,
        query: str,
        conversation_id: str | None = None,
        cursor: str | None = None,
        limit: int = 25,
    ) -> JSONResponse:
        await session(request)
        if not 1 <= limit <= 50 or len(query) > 200 or (cursor and len(cursor) > 2048):
            raise ProtocolError("invalid_command", 422)
        from row_bot.application.conversation_search import search
        from row_bot.application.client_platform import ClientPlatformError

        result = await call(
            search,
            service,
            query,
            conversation_id=conversation_id,
            cursor=cursor,
            limit=limit,
        )
        if conversation_id:
            await readable_conversation(conversation_id)
        readable = set()
        for identity in {item["conversation_id"] for item in result["items"]}:
            try:
                await readable_conversation(identity)
                readable.add(identity)
            except (ClientPlatformError, ProtocolError) as exc:
                if exc.code not in {"not_found", "conversation_deleting"}:
                    raise
        # Delivery filtering never refills a bounded scan or changes its cursor.
        result = {
            **result,
            "items": [
                item for item in result["items"] if item["conversation_id"] in readable
            ],
        }
        return await respond(request, dto.SearchPage, result)

    @router.get("/conversations/{conversation_id}/text/{message_id}")
    async def message_text(
        conversation_id: str,
        message_id: str,
        request: Request,
        cursor: str | None = None,
    ) -> JSONResponse:
        await session(request)
        if len(message_id) > 128 or (cursor and len(cursor) > 2048):
            raise ProtocolError("invalid_command", 422)
        from row_bot.application.message_text import read_text

        return await respond(
            request,
            dto.LazyContent,
            await call(read_text, service, conversation_id, message_id, cursor=cursor),
        )

    @router.get("/conversations/{conversation_id}/history")
    async def history_window(
        conversation_id: str,
        request: Request,
        message_id: str | None = None,
        cursor: str | None = None,
        limit: int = 100,
    ) -> JSONResponse:
        await session(request)
        if (
            not 1 <= limit <= 100
            or (cursor and len(cursor) > 2048)
            or (message_id and len(message_id) > 256)
        ):
            raise ProtocolError("invalid_command", 422)
        from row_bot.application.conversation_search import history_window as read

        result = await call(
            read,
            service,
            conversation_id,
            message_id=message_id,
            cursor=cursor,
            limit=limit,
        )
        await readable_conversation(conversation_id)
        return await respond(request, dto.TranscriptPage, result)

    @router.get("/conversations/{conversation_id}/workspace")
    async def workspace_view(conversation_id: str, request: Request) -> JSONResponse:
        await session(request, lane="view")
        from row_bot.application.workspace_setup import conversation_workspace

        result = await call(conversation_workspace, service, conversation_id)
        await readable_conversation(conversation_id)
        return await respond(request, dto.ConversationWorkspace, result)

    @router.get("/conversations/{conversation_id}/delegated")
    async def delegated_activity(
        conversation_id: str, request: Request, cursor: str | None = None
    ) -> JSONResponse:
        await session(request, lane="view")
        if cursor and len(cursor) > 2048:
            raise ProtocolError("invalid_command", 422)
        from row_bot.application.delegated_activity import read_activity

        result = await call(read_activity, service, conversation_id, cursor=cursor)
        await call(service._metadata, conversation_id)
        return await respond(request, dto.DelegatedActivityView, result)

    @router.get("/conversations/{conversation_id}/delegated/{run_id}")
    async def delegated_run(
        conversation_id: str, run_id: str, request: Request
    ) -> JSONResponse:
        await session(request)
        if len(run_id) > 128:
            raise ProtocolError("invalid_command", 422)
        from row_bot.application.delegated_activity import read_run

        result = await call(read_run, service, conversation_id, run_id)
        await call(service._metadata, conversation_id)
        return await respond(request, dto.DelegatedRun, result)

    @router.get("/conversations/{conversation_id}/draft")
    async def draft_view(conversation_id: str, request: Request) -> JSONResponse:
        await session(request)
        from row_bot.application.conversation_drafts import read_draft

        result = await call(read_draft, service, conversation_id)
        await readable_conversation(conversation_id)
        return await respond(request, dto.DraftView, result)

    @router.get("/conversations/{conversation_id}/queue")
    async def queue_view(
        conversation_id: str,
        request: Request,
        generation_id: str = "",
        cursor: str | None = None,
        limit: int = 100,
    ) -> JSONResponse:
        await session(request)
        if (
            not 1 <= limit <= 256
            or len(generation_id) > 128
            or (cursor and len(cursor) > 2048)
        ):
            raise ProtocolError("invalid_command", 422)
        from row_bot.application.client_queue import read_queue

        result = await call(
            read_queue,
            service,
            conversation_id,
            generation_id=generation_id,
            cursor=cursor,
            limit=limit,
        )
        await call(service._metadata, conversation_id)
        return await respond(request, dto.ClientQueueView, asdict(result))

    @router.get("/conversations/{conversation_id}/steering")
    async def steering_view(
        conversation_id: str,
        request: Request,
        generation_id: str = "",
        cursor: str | None = None,
        limit: int = 100,
    ) -> JSONResponse:
        await session(request)
        if (
            not 1 <= limit <= 256
            or len(generation_id) > 128
            or (cursor and len(cursor) > 2048)
        ):
            raise ProtocolError("invalid_command", 422)
        from row_bot.agent_orchestrator import read_parent_steering, OrchestrationError

        await call(service._metadata, conversation_id)
        try:
            result = await call(
                read_parent_steering,
                conversation_id,
                generation_id=generation_id,
                cursor=cursor,
                limit=limit,
            )
        except OrchestrationError:
            raise ProtocolError(
                "cursor_expired" if cursor else "invalid_command",
                410 if cursor else 422,
            ) from None
        await call(service._metadata, conversation_id)
        return await respond(request, dto.ParentSteeringView, asdict(result))

    @router.put("/conversations/{conversation_id}/draft")
    async def draft_save(conversation_id: str, request: Request) -> JSONResponse:
        current = await session(request, lane="mutation")
        body = await _body(request, dto.DraftSave)
        from row_bot.application.conversation_drafts import save_draft

        return await respond(
            request,
            dto.DraftView,
            await call(
                save_draft,
                service,
                conversation_id,
                body.model_dump(mode="json"),
                dispatch_validation(request, current),
            ),
        )

    async def command(
        target: str,
        request: Request,
        *,
        approval: bool = False,
        create: bool = False,
        resource_setup: bool = False,
        task_command: bool = False,
        provider_command: bool = False,
        mcp_command: bool = False,
        document_command: bool = False,
    ) -> JSONResponse:
        body = await _body(request, Command, 16384 if approval else JSON_LIMIT)
        current = await session(
            request,
            lane="control"
            if body.type
            in {
                "conversation.stop",
                "task.stop",
                "task.approval",
                "approval.resolve",
                "workspace.process.stop",
                "workspace.process.recover",
                "provider.subscription.cancel",
            }
            or body.type == "mcp.runtime.control"
            and body.payload["operation"] == "disconnect"
            else "mutation",
        )
        if str(body.client_session_id) != current.id:
            raise ProtocolError("action_denied", 403)
        if approval != (body.type == "approval.resolve") or create != (
            body.type == "conversation.create"
        ):
            raise ProtocolError("invalid_command", 422)
        if task_command != (
            body.type
            in {
                "task.create",
                "task.update",
                "task.graph.update",
                "task.settings.update",
                "task.webhook.rotate",
                "task.run",
                "task.stop",
                "task.approval",
            }
        ):
            raise ProtocolError("invalid_command", 422)
        if provider_command != body.type.startswith("provider."):
            raise ProtocolError("invalid_command", 422)
        if mcp_command != body.type.startswith("mcp."):
            raise ProtocolError("invalid_command", 422)
        if document_command != body.type.startswith("document."):
            raise ProtocolError("invalid_command", 422)
        if resource_setup and body.type not in {"resource.setup", "resource.continue"}:
            raise ProtocolError("invalid_command", 422)
        try:
            key = str(UUID(request.headers.get("idempotency-key", "")))
        except ValueError:
            raise ProtocolError("invalid_command", 422) from None
        wire = body.model_dump(mode="json")
        if body.type == "task.approval":
            # The presentation nonce binds this session to the exact reviewed
            # approval; command storage keeps only the semantic decision.
            from row_bot.application.task_run_commands import approval_digest

            security.consume_nonce(
                current,
                body.payload["approval_id"],
                body.payload["approval_revision"],
                approval_digest(
                    body.payload["task_id"],
                    body.payload["run_id"],
                    body.payload["approval_id"],
                    body.payload["approval_revision"],
                ),
                body.payload["nonce"],
                str(body.command_id),
            )
            wire["payload"] = {k: v for k, v in wire["payload"].items() if k != "nonce"}
        if body.type == "artifact.share":
            security.consume_nonce(
                current,
                body.payload["target"]["resource_id"],
                body.payload["target"]["resource_revision"],
                body.payload["review_id"],
                body.payload["nonce"],
                str(body.command_id),
            )
            wire["payload"] = {k: v for k, v in wire["payload"].items() if k != "nonce"}
        if body.type == "workspace.process.start":
            wire["payload"] = {k: v for k, v in wire["payload"].items() if k != "nonce"}
        if provider_command or mcp_command:
            wire["payload"] = {k: v for k, v in wire["payload"].items() if k != "nonce"}
        if body.type == "artifact.preset.mutate":
            wire["payload"] = {k: v for k, v in wire["payload"].items() if k != "nonce"}
        if approval:
            try:
                previous = await call(
                    service.receipt, security.instance_id, str(body.command_id)
                )
            except Exception as exc:
                if getattr(exc, "code", None) != "not_found":
                    raise
                previous = None
            if not previous or previous.get("status") not in {"completed", "accepted"}:
                view = await call(service.get_approval, target)
                security.consume_nonce(
                    current,
                    target,
                    body.expected_revision,
                    str(view["action_digest"]),
                    body.payload["nonce"],
                    str(body.command_id),
                )
            wire["payload"] = {k: v for k, v in wire["payload"].items() if k != "nonce"}
        # Revalidate after asynchronous reads, before entering the durable command owner.
        await _context(request)
        validate_access = dispatch_validation(request, current)
        folder = None
        if (
            body.type == "resource.setup"
            and body.payload.get("intent") == "new_conversation"
            and body.payload.get("folder_grant") is not None
        ):
            # A saved-workspace conversation never consumes a folder capability.
            raise ProtocolError("invalid_command", 422)
        if body.type in {"resource.setup", "resource.continue"} and body.payload.get(
            "folder_grant"
        ):
            context = await _context(request)
            if (
                context.authentication_kind != "local_owner"
                or not context.direct_loopback
            ):
                raise ProtocolError("action_denied", 403)
            folder = await call(
                folder_selections.resolve, body.payload["folder_grant"], current.id
            )

        def validate_dispatch() -> None:
            validate_access()
            if body.type == "artifact.share":
                security.consume_nonce(
                    current,
                    body.payload["target"]["resource_id"],
                    body.payload["target"]["resource_revision"],
                    body.payload["review_id"],
                    body.payload["nonce"],
                    str(body.command_id),
                )
            if folder is not None:
                folder_selections.resolve(body.payload["folder_grant"], current.id)
            if approval and (
                not previous or previous.get("status") not in {"completed", "accepted"}
            ):
                # Recheck the exact current stored action, policy and nonce deadline
                # after the admission lock wait, immediately before CAS/effect.
                latest = service.get_approval(target)
                security.consume_nonce(
                    current,
                    target,
                    body.expected_revision,
                    str(latest["action_digest"]),
                    body.payload["nonce"],
                    str(body.command_id),
                )

        def validate_task_approval() -> None:
            validate_access()
            security.consume_nonce(
                current,
                body.payload["approval_id"],
                body.payload["approval_revision"],
                approval_digest(
                    body.payload["task_id"],
                    body.payload["run_id"],
                    body.payload["approval_id"],
                    body.payload["approval_revision"],
                ),
                body.payload["nonce"],
                str(body.command_id),
            )

        def validate_process_approval(review: dict) -> None:
            validate_access()
            security.consume_nonce(
                current,
                review["resource_id"],
                review["resource_revision"],
                review["action_digest"],
                body.payload["nonce"],
                str(body.command_id),
            )

        if document_command:
            from row_bot.application.document_commands import (
                execute_document_command,
                public_receipt,
            )
            from row_bot.runtime.admissions import keyed_digest

            def validate_document_review(original: dict, review: dict) -> None:
                validate_access()
                security.consume_nonce(
                    current,
                    "documents:" + (review["document_id"] or "all"),
                    review["source_revision"],
                    keyed_digest(review),
                    original["payload"]["review_id"],
                    original["command_id"],
                )

            def validate_document_action(kind: str) -> None:
                validate_access()
                if kind not in {"document.remove", "document.removal.retry"}:
                    raise ProtocolError("action_denied", 403)
                # The explicit settings confirmation binds current saved policy;
                # the review callback rechecks it at every canonical effect.

            result = await call(
                execute_document_command,
                wire,
                owner_id=current.id,
                authority_id=current.id,
                key=key,
                validate=validate_access,
                validate_action=validate_document_action,
                validate_review=validate_document_review,
            )
            return await respond(
                request, dto.DocumentRemovalReceipt, public_receipt(result)
            )
        if body.type == "mcp.runtime.control":
            from row_bot.application.capability_runtime_controls import (
                execute_mcp_runtime_command,
                review_mcp_runtime_command,
                public_receipt,
            )
            from row_bot.runtime.admissions import read_command_metadata

            def validate_runtime_review(review: dict) -> None:
                validate_access()
                security.consume_nonce(
                    current,
                    "settings:mcp-runtime:" + review["server_id"],
                    review["resource_revision"],
                    review["action_digest"],
                    body.payload["nonce"],
                    str(body.command_id),
                )

            def validate_admitted_runtime_review(review: dict, runtime_id: str) -> None:
                validate_access()
                security.consume_admitted_mcp_nonce(
                    current,
                    "settings:mcp-runtime:" + review["server_id"],
                    review["resource_revision"],
                    review["action_digest"],
                    body.payload["nonce"],
                    str(body.command_id),
                    server_id=review["server_id"],
                    runtime_id=runtime_id,
                )

            original = await call(
                read_command_metadata, security.instance_id, str(body.command_id)
            )
            if original is None:
                reviewed = await call(
                    review_mcp_runtime_command,
                    **wire["payload"],
                    validate=validate_access,
                )
                await call(validate_runtime_review, reviewed)
            result = await call(
                execute_mcp_runtime_command,
                owner_id=security.instance_id,
                key=key,
                command=wire,
                validate=validate_access,
                validate_review=validate_runtime_review,
                validate_admitted_review=validate_admitted_runtime_review,
            )
            return await respond(request, dto.CommandReceipt, public_receipt(result))
        if body.type == "mcp.catalog.accept":
            from row_bot.application.capability_catalog_controls import (
                execute_mcp_catalog_command,
                review_mcp_catalog_command,
                public_receipt,
            )
            from row_bot.runtime.admissions import read_command_metadata

            def validate_catalog_review(review: dict) -> None:
                validate_access()
                security.consume_nonce(
                    current,
                    "settings:mcp",
                    review["configuration_revision"],
                    review["action_digest"],
                    body.payload["nonce"],
                    str(body.command_id),
                )

            original = await call(
                read_command_metadata, security.instance_id, str(body.command_id)
            )
            if original is None:
                reviewed = await call(
                    review_mcp_catalog_command,
                    owner_id=security.instance_id,
                    **wire["payload"],
                    validate=validate_access,
                )
                await call(validate_catalog_review, reviewed)
            result = await call(
                execute_mcp_catalog_command,
                owner_id=security.instance_id,
                key=key,
                command=wire,
                validate=validate_access,
                validate_review=validate_catalog_review,
            )
            return await respond(request, dto.CommandReceipt, public_receipt(result))
        if body.type in {"mcp.configuration.save", "mcp.configuration.control"}:
            from row_bot.application.capability_configuration_controls import (
                execute_mcp_configuration_command,
                public_receipt,
            )
            from row_bot.application.capability_policy_controls import (
                execute_mcp_policy_command,
            )
            from row_bot.runtime.admissions import keyed_digest, read_command_metadata

            def validate_mcp_review(review: dict) -> None:
                validate_access()
                security.consume_nonce(
                    current,
                    "settings:mcp",
                    review["configuration_revision"],
                    review["action_digest"],
                    body.payload["nonce"],
                    str(body.command_id),
                )

            reviewed = {
                "configuration_revision": body.payload["configuration_revision"],
                "action_digest": await call(
                    keyed_digest,
                    {
                        "revision": body.payload["configuration_revision"],
                        "intent": body.payload["intent"],
                    },
                ),
            }
            original = await call(
                read_command_metadata, security.instance_id, str(body.command_id)
            )
            # Existing exact commands only reconcile private publication proof.
            # Their canonical verifier still rejects any changed intent/key;
            # expired effect approval must not prevent read-only recovery.
            if original is None:
                await call(validate_mcp_review, reviewed)
            execute_configuration = (
                execute_mcp_policy_command
                if body.type == "mcp.configuration.control"
                else execute_mcp_configuration_command
            )
            result = await call(
                execute_configuration,
                owner_id=security.instance_id,
                key=key,
                command=wire,
                validate=validate_access,
                validate_review=validate_mcp_review,
            )
            return await respond(request, dto.CommandReceipt, public_receipt(result))
        if body.type == "provider.default_model.save":
            from row_bot.application.provider_default_model import execute_default_model
            from row_bot.runtime.admissions import keyed_digest

            def validate_default_review(review: dict) -> None:
                validate_access()
                security.consume_nonce(
                    current,
                    "default_model",
                    review["settings_revision"],
                    review["action_digest"],
                    body.payload["nonce"],
                    str(body.command_id),
                )

            intent = {
                name: body.payload[name]
                for name in ("settings_revision", "provider_id", "model_id")
            }
            await call(
                validate_default_review,
                {**intent, "action_digest": await call(keyed_digest, intent)},
            )
            result = await call(
                execute_default_model,
                owner_id=security.instance_id,
                key=key,
                command=wire,
                validate=validate_access,
                validate_review=validate_default_review,
            )
            return await respond(request, dto.CommandReceipt, result)
        if body.type in {
            "provider.subscription.reference",
            "provider.subscription.client_id.save",
            "provider.subscription.client_id.reset",
        }:
            from row_bot.application.subscription_options import (
                COMMANDS,
                execute_options,
                review_options,
            )
            from row_bot.runtime.admissions import read_command_metadata

            operation = next(
                name for name, kind in COMMANDS.items() if kind == body.type
            )

            def validate_options_review(review: dict) -> None:
                validate_access()
                security.consume_nonce(
                    current,
                    "subscription-options:" + review["provider_id"],
                    review["provider_revision"],
                    review["action_digest"],
                    body.payload["nonce"],
                    str(body.command_id),
                )

            original = await call(
                read_command_metadata, current.id, str(body.command_id)
            )
            if original is None:
                reviewed = await call(
                    review_options,
                    body.payload["provider_id"],
                    body.payload["provider_revision"],
                    operation,
                    body.payload.get("value"),
                    validate=validate_access,
                )
                await call(validate_options_review, reviewed)
            result = await call(
                execute_options,
                owner_id=current.id,
                key=key,
                command=wire,
                validate=validate_access,
                validate_review=validate_options_review,
            )
            return await respond(request, dto.SubscriptionOptionsResult, result)
        if body.type == "provider.subscription.probe":
            from row_bot.application.subscription_probes import (
                execute_probe,
                review_probe,
            )
            from row_bot.runtime.admissions import read_command_metadata

            def validate_probe_review(review: dict) -> None:
                validate_access()
                security.consume_nonce(
                    current,
                    "subscription-probe:" + review["provider_id"],
                    review["provider_revision"],
                    review["action_digest"],
                    body.payload["nonce"],
                    str(body.command_id),
                )

            original = await call(
                read_command_metadata, current.id, str(body.command_id)
            )
            approved = None
            if original is None:
                approved = await call(
                    review_probe, **wire["payload"], validate=validate_access
                )
                await call(validate_probe_review, approved)

            def validate_probe_access() -> None:
                validate_access()
                if approved is not None:
                    validate_probe_review(approved)

            result = await call(
                execute_probe,
                flows=service.subscription_flows,
                owner_id=current.id,
                key=key,
                command=wire,
                validate=validate_probe_access,
                validate_review=validate_probe_review,
            )
            return await respond(request, dto.SubscriptionProbeCommandResult, result)
        if body.type.startswith("provider.subscription."):
            from row_bot.runtime.admissions import keyed_digest, read_command_metadata

            operation = body.type.removeprefix("provider.subscription.")

            def validate_subscription_review(review: dict) -> None:
                validate_access()
                security.consume_nonce(
                    current,
                    "subscription:" + review["provider_id"],
                    review["provider_revision"],
                    review["action_digest"],
                    body.payload["nonce"],
                    str(body.command_id),
                )

            intent = {
                name: body.payload.get(name)
                for name in (
                    "provider_id",
                    "provider_revision",
                    "value",
                    "flow_id",
                    "server_epoch",
                )
            }
            intent["operation"] = operation
            original = await call(
                read_command_metadata, current.id, str(body.command_id)
            )
            if original is None:
                await call(
                    validate_subscription_review,
                    {**intent, "action_digest": await call(keyed_digest, intent)},
                )
            result = await call(
                service.subscription_flows.execute,
                owner_id=current.id,
                key=key,
                command=wire,
                validate=validate_access,
                validate_review=validate_subscription_review,
            )
            return await respond(request, dto.SubscriptionActionResult, result)
        if provider_command and not body.type.startswith(
            ("provider.credential.", "provider.custom_credential.")
        ):
            from row_bot.application.provider_configuration_controls import (
                execute_provider_configuration,
            )
            from row_bot.runtime.admissions import keyed_digest

            def validate_configuration_review(review: dict) -> None:
                validate_access()
                security.consume_nonce(
                    current,
                    "provider_configuration",
                    review["configuration_revision"],
                    review["action_digest"],
                    body.payload["nonce"],
                    str(body.command_id),
                )

            reviewed = {
                "configuration_revision": body.payload["configuration_revision"],
                "action_digest": await call(
                    keyed_digest,
                    {
                        "operation": body.type,
                        "revision": body.payload["configuration_revision"],
                        "payload": body.payload["fields"],
                    },
                ),
            }
            await call(validate_configuration_review, reviewed)
            result = await call(
                execute_provider_configuration,
                owner_id=security.instance_id,
                key=key,
                command=wire,
                validate=validate_access,
                validate_review=validate_configuration_review,
            )
            return await respond(request, dto.CommandReceipt, result)
        if provider_command:
            from row_bot.application.provider_settings_commands import (
                execute_provider_settings_command,
            )

            if body.type.startswith("provider.custom_credential."):
                from row_bot.application.provider_custom_credentials import (
                    execute_custom_provider_credentials,
                )

                execute_provider_settings_command = execute_custom_provider_credentials

            def validate_provider_review(review: dict) -> None:
                validate_access()
                security.consume_nonce(
                    current,
                    review["provider_id"],
                    review["provider_revision"],
                    review["action_digest"],
                    body.payload["nonce"],
                    str(body.command_id),
                )

            from row_bot.runtime.admissions import keyed_digest

            intent = {
                "provider_id": body.payload["provider_id"],
                "provider_revision": body.payload["provider_revision"],
                "operation": body.type.rsplit(".", 1)[1],
                "value": body.payload.get("value"),
            }
            await call(
                validate_provider_review,
                {**intent, "action_digest": await call(keyed_digest, intent)},
            )
            result = await call(
                execute_provider_settings_command,
                owner_id=security.instance_id,
                key=key,
                command=wire,
                validate=validate_access,
                validate_review=validate_provider_review,
            )
            if "credential" in result:
                result = {
                    **result,
                    "credential": {
                        name: value
                        for name, value in result["credential"].items()
                        if name != "display_name"
                    },
                }
            return await respond(request, dto.CommandReceipt, result)

        def validate_preset_confirmation(review: dict) -> None:
            from row_bot.runtime.admissions import keyed_digest

            validate_access()
            digest = keyed_digest(
                {"command_id": review["command_id"], "payload": review["payload"]}
            )
            identity = review["payload"]["target"]
            security.consume_nonce(
                current,
                identity["resource_id"],
                identity["resource_revision"],
                digest,
                body.payload["nonce"],
                str(body.command_id),
            )

        def resolve_asset_upload(upload_id: str) -> bytes:
            return uploads.read_staged(
                current.id,
                upload_id,
                conversation_id=target,
                name=body.payload["filename"],
                validate=validate_access,
            )

        if body.type == "artifact.preset.mutate":
            # Reject forged review material before claiming its command identity;
            # the canonical owner repeats this check under the project lock.
            await call(validate_preset_confirmation, wire)
        result = await call(
            service.execute,
            owner_id=security.instance_id,
            idempotency_key=key,
            command=wire,
            target=target,
            validate=validate_dispatch,
            **(
                {"validate_approval": validate_task_approval}
                if body.type == "task.approval"
                else {}
            ),
            **(
                {"validate_approval": validate_process_approval}
                if body.type == "workspace.process.start"
                else {}
            ),
            **(
                {"validate_approval": validate_preset_confirmation}
                if body.type == "artifact.preset.mutate"
                else {}
            ),
            **(
                {"resolve_upload": resolve_asset_upload}
                if body.type == "artifact.asset.upload"
                else {}
            ),
            **({"authorized_folder": folder} if folder is not None else {}),
        )
        accepted = result.get("status") == "accepted"
        return await respond(
            request, dto.CommandReceipt, result, status_code=202 if accepted else 200
        )

    @router.post("/conversations/commands")
    async def create_conversation(request: Request) -> JSONResponse:
        return await command("conversations", request, create=True)

    @router.post("/conversations/{conversation_id}/commands")
    async def conversation_command(
        conversation_id: str, request: Request
    ) -> JSONResponse:
        return await command(conversation_id, request)

    @router.post("/resources/commands")
    async def setup_resource(request: Request) -> JSONResponse:
        return await command("resources", request, resource_setup=True)

    @router.post("/tasks/commands")
    async def task_mutation(request: Request) -> JSONResponse:
        return await command("tasks", request, task_command=True)

    @router.get("/tasks/{task_id}/editing")
    async def task_editing(task_id: str, request: Request) -> JSONResponse:
        await session(request)
        from row_bot.application.task_controls import TaskControlError, get_task_editor

        try:
            result = await call(get_task_editor, task_id)
        except TaskControlError as exc:
            raise ProtocolError(exc.code, _STATUS.get(exc.code, 422)) from exc
        return await respond(request, dto.TaskEditorSnapshot, asdict(result))

    @router.get("/tasks/{task_id}/graph")
    async def task_graph(task_id: str, request: Request) -> JSONResponse:
        await session(request)
        from row_bot.application.task_graph_controls import (
            TaskGraphError,
            get_task_graph,
        )

        try:
            result = await call(get_task_graph, task_id)
        except TaskGraphError as exc:
            raise ProtocolError(exc.code, _STATUS.get(exc.code, 422)) from exc
        return await respond(request, dto.TaskGraphSnapshot, asdict(result))

    async def settings_query(
        request: Request, task_id: str, *, review: bool = False
    ) -> JSONResponse:
        await session(request)
        from row_bot.application.task_settings_controls import (
            TaskSettingsError,
            TaskSettingsFields,
            get_task_settings,
            review_task_settings,
        )

        fields = await _body(request, dto.TaskSettingsFields) if review else None
        try:
            result = (
                await call(
                    review_task_settings,
                    task_id,
                    TaskSettingsFields(**fields.model_dump()),
                )
                if fields
                else await call(get_task_settings, task_id)
            )
        except TaskSettingsError as exc:
            raise ProtocolError(exc.code, _STATUS.get(exc.code, 422)) from exc
        return await respond(request, dto.TaskSettingsSnapshot, asdict(result))

    @router.get("/tasks/{task_id}/settings")
    async def task_settings(task_id: str, request: Request) -> JSONResponse:
        return await settings_query(request, task_id)

    @router.post("/tasks/{task_id}/settings-review")
    async def task_settings_review(task_id: str, request: Request) -> JSONResponse:
        return await settings_query(request, task_id, review=True)

    @router.get("/tasks/{task_id}/webhook-configuration")
    async def task_webhook_configuration(
        task_id: str, revision: str, request: Request
    ) -> Response:
        current = await session(request, lane="view")
        from row_bot.application.task_settings_controls import (
            TaskSettingsError,
            export_webhook_configuration,
        )

        validate = dispatch_validation(request, current)
        try:
            payload = await call(
                export_webhook_configuration,
                task_id,
                expected_revision=revision,
                validate=validate,
            )
        except TaskSettingsError as exc:
            raise ProtocolError(exc.code, _STATUS.get(exc.code, 422)) from exc
        await call(validate)
        return Response(
            payload,
            media_type="application/octet-stream",
            headers={
                **HEADERS,
                "Content-Disposition": 'attachment; filename="workflow-webhook.json"',
                "Content-Security-Policy": "sandbox; default-src 'none'",
            },
        )

    async def task_query(
        request: Request, model: Any, operation: Callable, *args: Any, **kwargs: Any
    ) -> JSONResponse:
        await session(request)
        from row_bot.application.task_execution import TaskExecutionError

        try:
            result = await call(operation, *args, **kwargs)
        except TaskExecutionError as exc:
            raise ProtocolError(exc.code, _STATUS.get(exc.code, 409)) from exc
        return await respond(request, model, asdict(result))

    @router.get("/tasks/{task_id}/run-review")
    async def task_run_review(task_id: str, request: Request) -> JSONResponse:
        from row_bot.application.task_execution import (
            TaskRunReview,
            get_task_run_review,
        )
        from row_bot.tools.registry import get_enabled_tools

        def read() -> TaskRunReview:
            return get_task_run_review(
                task_id, enabled_tool_names=[tool.name for tool in get_enabled_tools()]
            )

        return await task_query(request, dto.TaskRunReview, read)

    @router.get("/tasks/{task_id}/runs")
    async def task_runs(
        task_id: str, request: Request, cursor: str | None = None, limit: int = 50
    ) -> JSONResponse:
        from row_bot.application.task_execution import list_task_runs

        return await task_query(
            request,
            dto.TaskRunPage,
            list_task_runs,
            task_id,
            cursor=cursor,
            limit=limit,
        )

    @router.get("/tasks/{task_id}/runs/{run_id}")
    async def task_run(task_id: str, run_id: str, request: Request) -> JSONResponse:
        from row_bot.application.task_execution import get_task_run

        return await task_query(
            request, dto.TaskRunSummary, get_task_run, task_id, run_id
        )

    @router.get("/tasks/{task_id}/runs/{run_id}/approvals")
    async def task_approvals(
        task_id: str,
        run_id: str,
        request: Request,
        cursor: str | None = None,
        limit: int = 8,
    ) -> JSONResponse:
        current = await session(request)
        from row_bot.application.task_execution import (
            TaskExecutionError,
            list_task_approvals,
        )
        from row_bot.application.task_run_commands import approval_digest

        try:
            result = asdict(
                await call(
                    list_task_approvals, task_id, run_id, cursor=cursor, limit=limit
                )
            )
        except TaskExecutionError as exc:
            raise ProtocolError(exc.code, _STATUS.get(exc.code, 409)) from exc
        for item in result["items"]:
            if item["response_available"]:
                item["nonce"] = security.approval_nonce(
                    current,
                    item["id"],
                    item["revision"],
                    approval_digest(task_id, run_id, item["id"], item["revision"]),
                )
        return await respond(request, dto.TaskApprovalPage, result)

    @router.post("/resources/folder-selection")
    async def select_folder(request: Request) -> JSONResponse:
        current = await session(request, lane="mutation")
        context = await _context(request)
        if context.authentication_kind != "local_owner" or not context.direct_loopback:
            raise ProtocolError("action_denied", 403)
        result = await call(
            folder_selections.pick, current.id, dispatch_validation(request, current)
        )
        return await respond(request, dto.FolderGrantView, result)

    async def wiki_scope(
        request: Request, current: Any, grant_id: str
    ) -> tuple[Any, Callable[[], None]]:
        context = await _context(request)
        if context.authentication_kind != "local_owner" or not context.direct_loopback:
            raise ProtocolError("action_denied", 403)
        folder = await call(folder_selections.resolve, grant_id, current.id)
        from row_bot.application.wiki_commands import WikiScope
        from row_bot.file_ownership import directory_identity
        import hashlib
        import os

        identity = await call(directory_identity, folder.path, parent=True)
        scope_id = hashlib.sha256(
            f"{os.path.normcase(str(folder.path))}\0{identity}".encode("utf-8")
        ).hexdigest()
        scope = WikiScope(scope_id, folder.path, identity)
        validate_access = dispatch_validation(request, current)

        def validate() -> None:
            validate_access()
            selected = folder_selections.resolve(grant_id, current.id)
            if (
                selected.path != scope.vault
                or directory_identity(selected.path, parent=True) != scope.identity
            ):
                raise ProtocolError("capability_revoked", 403)

        return scope, validate

    @router.get("/settings/wiki")
    async def wiki_status(
        request: Request, folder_grant: str | None = None
    ) -> JSONResponse:
        current = await session(request)
        from row_bot.application.wiki_commands import read_wiki_status

        if folder_grant:
            scope, validate = await wiki_scope(request, current, folder_grant)
        else:
            scope, validate = None, dispatch_validation(request, current)
        result = await call(read_wiki_status, scope=scope, validate=validate)
        return await respond(request, dto.WikiStatus, result)

    @router.post("/settings/wiki/open-folder")
    async def wiki_open_folder(request: Request) -> JSONResponse:
        current = await session(request, lane="mutation")
        context = await _context(request)
        if context.authentication_kind != "local_owner" or not context.direct_loopback:
            raise ProtocolError("action_denied", 403)
        from row_bot.application.wiki_commands import open_configured_wiki_folder

        result = await call(
            open_configured_wiki_folder,
            validate=dispatch_validation(request, current),
        )
        return await respond(request, dto.WikiOpenFolderResult, result)

    @router.get("/settings/wiki/articles")
    async def wiki_articles(
        request: Request, folder_grant: str, cursor: str | None = None, limit: int = 50
    ) -> JSONResponse:
        current = await session(request)
        from row_bot.application.wiki_commands import read_wiki_articles

        scope, validate = await wiki_scope(request, current, folder_grant)
        result = await call(
            read_wiki_articles,
            scope=scope,
            cursor=cursor,
            limit=limit,
            validate=validate,
        )
        return await respond(request, dto.WikiArticlePage, result)

    @router.get("/settings/wiki/articles/{article_id}")
    async def wiki_article(
        article_id: str, request: Request, folder_grant: str
    ) -> JSONResponse:
        current = await session(request)
        from row_bot.application.wiki_commands import read_wiki_article

        scope, validate = await wiki_scope(request, current, folder_grant)
        result = await call(
            read_wiki_article, scope=scope, article_id=article_id, validate=validate
        )
        return await respond(request, dto.WikiArticle, result)

    @router.post("/settings/wiki/review")
    async def wiki_review(request: Request) -> JSONResponse:
        current = await session(request, lane="mutation")
        body = await _body(request, dto.WikiReviewRequest, 256 * 1024)
        from row_bot.application.wiki_commands import review_wiki_command

        scope, validate = await wiki_scope(request, current, body.folder_grant)
        payload = body.payload.model_dump(mode="json")
        result = await call(
            review_wiki_command, body.action, payload, scope=scope, validate=validate
        )
        result["review_id"] = security.approval_nonce(
            current,
            "wiki:" + result["scope_id"],
            result["revision"],
            result["action_digest"],
        )
        return await respond(request, dto.WikiReview, result)

    @router.get("/settings/wiki/commands/{command_id}")
    async def wiki_receipt(
        command_id: UUID, request: Request, folder_grant: str
    ) -> JSONResponse:
        current = await session(request)
        from row_bot.application.wiki_commands import read_wiki_receipt

        scope, validate = await wiki_scope(request, current, folder_grant)
        result = await call(
            read_wiki_receipt,
            owner_id=current.id,
            authority_id=current.id,
            command_id=str(command_id),
            scope=scope,
            validate=validate,
        )
        return await respond(request, dto.WikiReceipt, result)

    @router.post("/settings/wiki/commands")
    async def wiki_command(request: Request) -> JSONResponse:
        current = await session(request, lane="mutation")
        body = await _body(request, dto.Command, 256 * 1024)
        kinds = {
            "wiki.configure",
            "wiki.publish",
            "wiki.rebuild",
            "wiki.import",
            "wiki.sync",
        }
        if body.type not in kinds or str(body.client_session_id) != current.id:
            raise ProtocolError("invalid_command", 422)
        key = request.headers.get("idempotency-key", "")
        if key != str(body.command_id):
            raise ProtocolError("idempotency_mismatch", 409)
        grant_id = body.payload["folder_grant"]
        scope, validate = await wiki_scope(request, current, grant_id)
        wire = body.model_dump(mode="json")
        wire["payload"] = {
            name: value
            for name, value in wire["payload"].items()
            if name != "folder_grant"
        }
        from row_bot.application.wiki_commands import execute_wiki_command

        def validate_review(original: dict, review: dict) -> None:
            validate()
            security.consume_nonce(
                current,
                "wiki:" + review["scope_id"],
                review["revision"],
                review["action_digest"],
                original["payload"]["review_id"],
                original["command_id"],
            )

        def validate_action(kind: str) -> None:
            validate()
            if kind not in kinds:
                raise ProtocolError("action_denied", 403)

        result = await call(
            execute_wiki_command,
            wire,
            owner_id=current.id,
            authority_id=current.id,
            key=key,
            scope=scope,
            validate=validate,
            validate_action=validate_action,
            validate_review=validate_review,
        )
        return await respond(request, dto.WikiReceipt, result)

    @router.get("/settings/channels")
    async def channels(
        request: Request, query: str = "", limit: int = 50
    ) -> JSONResponse:
        current = await session(request)
        from row_bot.application.channel_controls import read_channels

        result = await call(
            read_channels,
            query=query,
            limit=limit,
            validate=dispatch_validation(request, current),
        )
        return await respond(request, dto.ChannelPage, result)

    @router.post("/settings/channels/review")
    async def channel_review(request: Request) -> JSONResponse:
        current = await session(request, lane="mutation")
        body = await _body(request, dto.ChannelActionRequest, 64 * 1024)
        from row_bot.application.channel_controls import review_channel_command

        result = await call(
            review_channel_command,
            body.channel_id,
            body.revision,
            body.operation,
            field_key=body.field_key,
            value=body.value,
            identity_id=body.identity_id,
            validate=dispatch_validation(request, current),
        )
        result["review_id"] = security.approval_nonce(
            current,
            "channel:" + result["channel_id"],
            result["revision"],
            result["action_digest"],
        )
        return await respond(request, dto.ChannelActionReview, result)

    @router.get("/settings/channels/{channel_id}/commands/{command_id}")
    async def channel_receipt(
        channel_id: str, command_id: UUID, request: Request
    ) -> JSONResponse:
        current = await session(request)
        from row_bot.application.channel_controls import read_channel_receipt

        result = await call(
            read_channel_receipt,
            channel_id,
            owner_id=current.id,
            command_id=str(command_id),
            validate=dispatch_validation(request, current),
        )
        if result is None:
            raise ProtocolError("not_found", 404)
        return await respond(request, dto.ChannelReceipt, result)

    @router.post("/settings/channels/commands")
    async def channel_command(request: Request) -> JSONResponse:
        current = await session(request, lane="mutation")
        body = await _body(request, dto.Command, 64 * 1024)
        if body.type != "channel.control" or str(body.client_session_id) != current.id:
            raise ProtocolError("invalid_command", 422)
        key = request.headers.get("idempotency-key", "")
        if key != str(body.command_id):
            raise ProtocolError("idempotency_mismatch", 409)
        accepted = body.model_dump(mode="json")
        review_id = accepted["payload"].pop("review_id")
        wire = {
            "command_id": accepted["command_id"],
            "type": accepted["type"],
            "payload": accepted["payload"],
        }
        from row_bot.application.channel_controls import execute_channel_command

        validate = dispatch_validation(request, current)

        def validate_review(review: dict) -> None:
            validate()
            security.consume_nonce(
                current,
                "channel:" + review["channel_id"],
                review["revision"],
                review["action_digest"],
                review_id,
                wire["command_id"],
            )

        result = await call(
            lambda: asyncio.run(
                execute_channel_command(
                    owner_id=current.id,
                    key=key,
                    command=wire,
                    validate=validate,
                    validate_review=validate_review,
                )
            )
        )
        return await respond(request, dto.ChannelReceipt, result)

    @router.get("/settings/plugins")
    async def plugins(
        request: Request,
        query: str = "",
        source: str = "all",
        cursor: str | None = None,
        limit: int = 50,
    ) -> JSONResponse:
        current = await session(request)
        from row_bot.application.plugin_commands import read_plugin_catalog

        result = await call(
            read_plugin_catalog,
            query=query,
            source=source,
            cursor=cursor,
            limit=limit,
            validate=dispatch_validation(request, current),
        )
        return await respond(request, dto.PluginCatalogPage, result)

    @router.get("/settings/plugins/{plugin_id}")
    async def plugin_detail(plugin_id: str, request: Request) -> JSONResponse:
        current = await session(request)
        from row_bot.application.plugin_commands import read_plugin_detail

        result = await call(
            read_plugin_detail,
            plugin_id,
            validate=dispatch_validation(request, current),
        )
        return await respond(request, dto.PluginDetail, result)

    @router.post("/settings/plugins/{plugin_id}/review")
    async def plugin_review(plugin_id: str, request: Request) -> JSONResponse:
        current = await session(request, lane="mutation")
        body = await _body(request, dto.PluginReviewRequest, 128 * 1024)
        if body.payload.get("plugin_id") != plugin_id:
            raise ProtocolError("invalid_plugin_command", 422)
        from row_bot.application.plugin_commands import review_plugin_command

        result = await call(
            review_plugin_command,
            body.action,
            body.payload,
            validate=dispatch_validation(request, current),
        )
        result["review_id"] = security.approval_nonce(
            current,
            "plugin:" + result["plugin_id"],
            result["revision"],
            result["action_digest"],
        )
        return await respond(request, dto.PluginReview, result)

    @router.get("/settings/plugins/{plugin_id}/receipts/{command_id}")
    async def plugin_receipt(
        plugin_id: str, command_id: UUID, request: Request
    ) -> JSONResponse:
        current = await session(request)
        from row_bot.application.plugin_commands import read_plugin_receipt

        result = await call(
            read_plugin_receipt,
            plugin_id,
            str(command_id),
            owner_id=current.id,
            validate=dispatch_validation(request, current),
        )
        if result is None:
            raise ProtocolError("not_found", 404)
        return await respond(request, dto.PluginReceipt, result)

    @router.post("/settings/plugins/{plugin_id}/commands")
    async def plugin_command(plugin_id: str, request: Request) -> JSONResponse:
        current = await session(request, lane="mutation")
        body = await _body(request, dto.Command, 128 * 1024)
        kinds = {"plugin.enable", "plugin.disable", "plugin.configure"}
        if (
            body.type not in kinds
            or str(body.client_session_id) != current.id
            or body.payload.get("plugin_id") != plugin_id
        ):
            raise ProtocolError("invalid_plugin_command", 422)
        key = request.headers.get("idempotency-key", "")
        if key != str(body.command_id):
            raise ProtocolError("idempotency_mismatch", 409)
        wire = body.model_dump(mode="json")
        review_id = wire["payload"].pop("review_id")
        from row_bot.application.plugin_commands import execute_plugin_command

        def validate_review(review: dict) -> None:
            security.consume_nonce(
                current,
                "plugin:" + review["plugin_id"],
                review["revision"],
                review["action_digest"],
                review_id,
                wire["command_id"],
            )

        result = await call(
            execute_plugin_command,
            owner_id=current.id,
            key=key,
            command=wire,
            validate=dispatch_validation(request, current),
            validate_review=validate_review,
        )
        return await respond(request, dto.PluginReceipt, result)

    @router.get("/settings/skills")
    async def skill_library(
        request: Request,
        query: str = "",
        source: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> JSONResponse:
        current = await session(request)
        from row_bot.application.skill_commands import read_skill_library

        result = await call(
            read_skill_library,
            query=query,
            source=source,
            cursor=cursor,
            limit=limit,
            validate=dispatch_validation(request, current),
        )
        return await respond(request, dto.SkillPage, result)

    @router.get("/settings/skills/items/{skill_id}")
    async def skill_detail(skill_id: str, request: Request) -> JSONResponse:
        current = await session(request)
        from row_bot.application.skill_commands import read_skill_detail

        result = await call(
            read_skill_detail, skill_id, validate=dispatch_validation(request, current)
        )
        return await respond(request, dto.SkillDetail, result)

    @router.get("/settings/skill-proposals")
    async def skill_proposals(request: Request) -> JSONResponse:
        current = await session(request)
        from row_bot.application.skill_commands import read_skill_proposals

        result = await call(
            read_skill_proposals, validate=dispatch_validation(request, current)
        )
        return await respond(request, dto.SkillProposalPage, result)

    @router.post("/settings/skills/review")
    async def skill_review(request: Request) -> JSONResponse:
        current = await session(request, lane="mutation")
        body = await _body(request, dto.SkillReviewRequest, 256 * 1024)
        from row_bot.application.skill_commands import review_skill_command

        result = await call(
            review_skill_command,
            body.action,
            body.payload,
            validate=dispatch_validation(request, current),
        )
        result["review_id"] = security.approval_nonce(
            current,
            "skills:" + result["target"],
            result["revision"],
            result["action_digest"],
        )
        return await respond(request, dto.SkillReview, result)

    @router.get("/settings/skills/commands/{command_id}")
    async def skill_receipt(command_id: UUID, request: Request) -> JSONResponse:
        current = await session(request)
        from row_bot.application.skill_commands import read_skill_command

        result = await call(
            read_skill_command,
            owner_id=current.id,
            authority_id=current.id,
            command_id=str(command_id),
            validate=dispatch_validation(request, current),
        )
        return await respond(request, dto.SkillReceipt, result)

    @router.post("/settings/skills/commands")
    async def skill_command(request: Request) -> JSONResponse:
        current = await session(request, lane="mutation")
        body = await _body(request, dto.Command, 256 * 1024)
        kinds = {
            "skill.preference",
            "skill.create",
            "skill.import",
            "skill.edit",
            "skill.duplicate",
            "skill.delete",
            "skill.proposal.apply",
            "skill.proposal.reject",
        }
        if body.type not in kinds or str(body.client_session_id) != current.id:
            raise ProtocolError("invalid_skill_command", 422)
        key = request.headers.get("idempotency-key", "")
        if key != str(body.command_id):
            raise ProtocolError("idempotency_mismatch", 409)
        wire = body.model_dump(mode="json")
        from row_bot.application.skill_commands import execute_skill_command

        validate = dispatch_validation(request, current)

        def validate_action(kind: str) -> None:
            if kind not in kinds:
                raise ProtocolError("action_denied", 403)
            validate()

        def validate_review(original: dict, review: dict) -> None:
            validate()
            security.consume_nonce(
                current,
                "skills:" + review["target"],
                review["revision"],
                review["action_digest"],
                original["payload"]["review_id"],
                original["command_id"],
            )

        result = await call(
            execute_skill_command,
            wire,
            owner_id=current.id,
            authority_id=current.id,
            key=key,
            validate=validate,
            validate_action=validate_action,
            validate_review=validate_review,
        )
        return await respond(request, dto.SkillReceipt, result)

    @router.get("/conversations/{conversation_id}/goals")
    async def goal_page(
        conversation_id: str,
        request: Request,
        query: str = "",
        cursor: str | None = None,
        limit: int = 50,
    ) -> JSONResponse:
        current = await session(request, lane="view")
        await call(service._metadata, conversation_id)
        from row_bot.application.client_goal_profile_commands import read_goals

        result = await call(
            read_goals,
            conversation_id,
            query=query,
            cursor=cursor,
            limit=limit,
            validate=dispatch_validation(request, current),
        )
        return await respond(request, dto.GoalPage, result)

    @router.get("/conversations/{conversation_id}/goals/items/{goal_id}")
    async def goal_detail(
        conversation_id: str, goal_id: str, request: Request
    ) -> JSONResponse:
        current = await session(request, lane="view")
        await call(service._metadata, conversation_id)
        from row_bot.application.client_goal_profile_commands import read_goal

        result = await call(
            read_goal,
            conversation_id,
            goal_id,
            validate=dispatch_validation(request, current),
        )
        return await respond(request, dto.GoalDetail, result)

    @router.post("/conversations/{conversation_id}/goals/review")
    async def goal_review(conversation_id: str, request: Request) -> JSONResponse:
        current = await session(request, lane="mutation")
        await call(service._metadata, conversation_id)
        body = await _body(request, dto.GoalCommandPayload, 16 * 1024)
        if body.conversation_id != conversation_id:
            raise ProtocolError("invalid_command", 422)
        from row_bot.application.client_goal_profile_commands import review_goal_command

        result = await call(
            review_goal_command,
            body.model_dump(mode="json"),
            validate=dispatch_validation(request, current),
        )
        result["review_id"] = security.approval_nonce(
            current,
            "settings:goal:" + conversation_id,
            result["revision"],
            result["action_digest"],
        )
        return await respond(request, dto.GoalReview, result)

    @router.get("/conversations/{conversation_id}/goals/commands/{command_id}")
    async def goal_receipt(
        conversation_id: str, command_id: UUID, request: Request
    ) -> JSONResponse:
        current = await session(request, lane="view")
        await call(service._metadata, conversation_id)
        from row_bot.application.client_goal_profile_commands import read_goal_receipt

        result = await call(
            read_goal_receipt,
            conversation_id,
            owner_id=current.id,
            command_id=str(command_id),
            validate=dispatch_validation(request, current),
        )
        if result is None:
            raise ProtocolError("not_found", 404)
        return await respond(request, dto.GoalReceipt, result)

    @router.post("/conversations/{conversation_id}/goals/commands")
    async def goal_command(conversation_id: str, request: Request) -> JSONResponse:
        current = await session(request, lane="mutation")
        await call(service._metadata, conversation_id)
        body = await _body(request, dto.Command, 32 * 1024)
        if (
            body.type != "goal.control"
            or str(body.client_session_id) != current.id
            or body.payload.get("conversation_id") != conversation_id
        ):
            raise ProtocolError("invalid_command", 422)
        key = request.headers.get("idempotency-key", "")
        if key != str(body.command_id):
            raise ProtocolError("idempotency_mismatch", 409)
        accepted = body.model_dump(mode="json")
        review_id = accepted["payload"].pop("review_id")
        wire = {
            "command_id": accepted["command_id"],
            "type": accepted["type"],
            "payload": accepted["payload"],
        }
        validate = dispatch_validation(request, current)

        def validate_review(review: dict) -> None:
            validate()
            security.consume_nonce(
                current,
                "settings:goal:" + conversation_id,
                review["revision"],
                review["action_digest"],
                review_id,
                str(body.command_id),
            )

        from row_bot.application.client_goal_profile_commands import (
            execute_goal_command,
        )

        result = await call(
            execute_goal_command,
            owner_id=current.id,
            key=key,
            command=wire,
            validate=validate,
            validate_review=validate_review,
        )
        return await respond(request, dto.GoalReceipt, result)

    @router.get("/settings/profiles")
    async def profile_page(
        request: Request,
        query: str = "",
        scope: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> JSONResponse:
        current = await session(request, lane="view")
        from row_bot.application.client_goal_profile_commands import read_profiles

        result = await call(
            read_profiles,
            query=query,
            scope=scope,
            cursor=cursor,
            limit=limit,
            validate=dispatch_validation(request, current),
        )
        return await respond(request, dto.ProfilePage, result)

    @router.get("/settings/profiles/items/{profile_id}")
    async def profile_detail(profile_id: str, request: Request) -> JSONResponse:
        current = await session(request, lane="view")
        from row_bot.application.client_goal_profile_commands import read_profile

        result = await call(
            read_profile, profile_id, validate=dispatch_validation(request, current)
        )
        return await respond(request, dto.ProfileDetail, result)

    @router.post("/settings/profiles/review")
    async def profile_review(request: Request) -> JSONResponse:
        current = await session(request, lane="mutation")
        body = await _body(request, dto.ProfileCommandPayload, 128 * 1024)
        from row_bot.application.client_goal_profile_commands import (
            review_profile_command,
        )

        result = await call(
            review_profile_command,
            body.model_dump(mode="json"),
            validate=dispatch_validation(request, current),
        )
        target = result.get("profile_id") or result.get("target_slug")
        if target is None and isinstance(result.get("fields"), dict):
            target = result["fields"].get("slug")
        if not isinstance(target, str) or not target:
            raise ProtocolError("invalid_command", 422)
        result["review_id"] = security.approval_nonce(
            current,
            "settings:profile:" + target,
            result["revision"],
            result["action_digest"],
        )
        return await respond(request, dto.ProfileReview, result)

    @router.get("/settings/profiles/{profile_ref}/receipts/{command_id}")
    async def profile_receipt(
        profile_ref: str, command_id: UUID, request: Request
    ) -> JSONResponse:
        current = await session(request, lane="view")
        from row_bot.application.client_goal_profile_commands import (
            read_profile_receipt,
        )

        result = await call(
            read_profile_receipt,
            profile_ref,
            owner_id=current.id,
            command_id=str(command_id),
            validate=dispatch_validation(request, current),
        )
        if result is None:
            raise ProtocolError("not_found", 404)
        return await respond(request, dto.ProfileReceipt, result)

    @router.post("/settings/profiles/commands")
    async def profile_command(request: Request) -> JSONResponse:
        current = await session(request, lane="mutation")
        body = await _body(request, dto.Command, 128 * 1024)
        if body.type != "profile.mutate" or str(body.client_session_id) != current.id:
            raise ProtocolError("invalid_command", 422)
        key = request.headers.get("idempotency-key", "")
        if key != str(body.command_id):
            raise ProtocolError("idempotency_mismatch", 409)
        accepted = body.model_dump(mode="json")
        review_id = accepted["payload"].pop("review_id")
        wire = {
            "command_id": accepted["command_id"],
            "type": accepted["type"],
            "payload": accepted["payload"],
        }
        payload = wire["payload"]
        target = payload.get("profile_id") or payload.get("target_slug")
        if target is None and isinstance(payload.get("fields"), dict):
            target = payload["fields"].get("slug")
        if not isinstance(target, str) or not target:
            raise ProtocolError("invalid_command", 422)
        validate = dispatch_validation(request, current)

        def validate_review(review: dict) -> None:
            validate()
            security.consume_nonce(
                current,
                "settings:profile:" + target,
                review["revision"],
                review["action_digest"],
                review_id,
                str(body.command_id),
            )

        from row_bot.application.client_goal_profile_commands import (
            execute_profile_command,
        )

        result = await call(
            execute_profile_command,
            owner_id=current.id,
            key=key,
            command=wire,
            validate=validate,
            validate_review=validate_review,
        )
        return await respond(request, dto.ProfileReceipt, result)

    @router.get("/knowledge/entities/editor")
    async def knowledge_editor(
        request: Request, entity_id: str | None = None
    ) -> JSONResponse:
        current = await session(request)
        from row_bot.application.knowledge_commands import read_entity_editor

        result = await call(
            read_entity_editor,
            entity_id,
            validate=dispatch_validation(request, current),
        )
        return await respond(request, dto.KnowledgeEditorState, result)

    @router.post("/knowledge/entities/review")
    async def knowledge_review(request: Request) -> JSONResponse:
        current = await session(request, lane="mutation")
        body = await _body(request, dto.KnowledgeReviewRequest, 256 * 1024)
        from row_bot.application.knowledge_commands import read_knowledge_review
        from row_bot.runtime.admissions import keyed_digest

        payload = body.payload.model_dump(mode="json", exclude_none=True)
        payload["entity_id"] = body.payload.entity_id
        result = await call(
            read_knowledge_review,
            body.action,
            payload,
            validate=dispatch_validation(request, current),
        )
        digest = await call(keyed_digest, result)
        result["review_id"] = security.approval_nonce(
            current,
            "knowledge:" + (result["entity_id"] or "new"),
            result["revision"],
            digest,
        )
        return await respond(request, dto.KnowledgeReview, result)

    @router.get("/knowledge/entities/commands/{command_id}")
    async def knowledge_receipt(command_id: UUID, request: Request) -> JSONResponse:
        current = await session(request)
        from row_bot.application.knowledge_commands import read_knowledge_command

        result = await call(
            read_knowledge_command,
            owner_id=current.id,
            authority_id=current.id,
            command_id=str(command_id),
            validate=dispatch_validation(request, current),
        )
        return await respond(request, dto.KnowledgeReceipt, result)

    @router.post("/knowledge/entities/commands")
    async def knowledge_command(request: Request) -> JSONResponse:
        current = await session(request, lane="mutation")
        body = await _body(request, dto.Command, 256 * 1024)
        kinds = {
            "knowledge.create",
            "knowledge.edit",
            "knowledge.archive",
            "knowledge.restore",
            "knowledge.resolve",
        }
        if body.type not in kinds or str(body.client_session_id) != current.id:
            raise ProtocolError("invalid_command", 422)
        key = request.headers.get("idempotency-key", "")
        if key != str(body.command_id):
            raise ProtocolError("idempotency_mismatch", 409)
        from row_bot.application.knowledge_commands import execute_knowledge_command
        from row_bot.runtime.admissions import keyed_digest

        validate_access = dispatch_validation(request, current)

        def validate_review(original: dict, review: dict) -> None:
            validate_access()
            security.consume_nonce(
                current,
                "knowledge:" + (review["entity_id"] or "new"),
                review["revision"],
                keyed_digest(review),
                original["payload"]["review_id"],
                original["command_id"],
            )

        def validate_action(kind: str) -> None:
            validate_access()
            if kind not in kinds:
                raise ProtocolError("action_denied", 403)

        result = await call(
            execute_knowledge_command,
            body.model_dump(mode="json"),
            owner_id=current.id,
            authority_id=current.id,
            key=key,
            validate=validate_access,
            validate_action=validate_action,
            validate_review=validate_review,
        )
        return await respond(request, dto.KnowledgeReceipt, result)

    @router.post("/knowledge/maintenance/review")
    async def knowledge_maintenance_review(request: Request) -> JSONResponse:
        current = await session(request, lane="mutation")
        body = await _body(request, dto.KnowledgeMaintenanceRequest, 64 * 1024)
        from row_bot.application.knowledge_commands import read_knowledge_maintenance_review

        payload = {
            "catalog_revision": body.catalog_revision,
            "targets": [item.model_dump(mode="json") for item in body.targets],
        }
        result = await call(
            read_knowledge_maintenance_review,
            body.action,
            payload,
            validate=dispatch_validation(request, current),
        )
        result["review_id"] = security.approval_nonce(
            current,
            "knowledge:maintenance",
            result["catalog_revision"],
            result["action_digest"],
        )
        return await respond(request, dto.KnowledgeMaintenanceReview, result)

    @router.get("/knowledge/maintenance/commands/{command_id}")
    async def knowledge_maintenance_receipt(command_id: UUID, request: Request) -> JSONResponse:
        current = await session(request)
        from row_bot.application.knowledge_commands import read_knowledge_maintenance_command

        result = await call(
            read_knowledge_maintenance_command,
            owner_id=current.id,
            command_id=str(command_id),
            validate=dispatch_validation(request, current),
        )
        return await respond(request, dto.KnowledgeMaintenanceReceipt, result)

    @router.post("/knowledge/maintenance/commands")
    async def knowledge_maintenance_command(request: Request) -> JSONResponse:
        current = await session(request, lane="mutation")
        body = await _body(request, dto.KnowledgeMaintenanceCommand, 64 * 1024)
        if str(body.client_session_id) != current.id:
            raise ProtocolError("action_denied", 403)
        key = request.headers.get("idempotency-key", "")
        if key != str(body.command_id):
            raise ProtocolError("idempotency_mismatch", 409)
        from row_bot.application.knowledge_commands import execute_knowledge_maintenance_command

        validate_access = dispatch_validation(request, current)

        def validate_review(_command: dict, review: dict) -> None:
            validate_access()
            security.consume_nonce(
                current,
                "knowledge:maintenance",
                review["catalog_revision"],
                review["action_digest"],
                body.payload.review_id,
                str(body.command_id),
            )

        result = await call(
            execute_knowledge_maintenance_command,
            body.model_dump(mode="json"),
            owner_id=current.id,
            key=key,
            validate=validate_access,
            validate_review=validate_review,
        )
        return await respond(request, dto.KnowledgeMaintenanceReceipt, result)

    @router.get("/knowledge/relations")
    async def knowledge_relations(
        request: Request, entity_id: str, cursor: str | None = None, limit: int = 50
    ) -> JSONResponse:
        current = await session(request)
        from row_bot.application.knowledge_relations import read_entity_relations

        result = await call(
            read_entity_relations,
            entity_id,
            cursor=cursor,
            limit=limit,
            validate=dispatch_validation(request, current),
        )
        return await respond(request, dto.KnowledgeRelationPage, asdict(result))

    @router.post("/knowledge/relations/review")
    async def knowledge_relation_review(request: Request) -> JSONResponse:
        current = await session(request, lane="mutation")
        body = await _body(request, dto.KnowledgeRelationReviewRequest, 16 * 1024)
        from row_bot.application.knowledge_relations import read_relation_review
        from row_bot.runtime.admissions import keyed_digest

        result = await call(
            read_relation_review,
            body.action,
            body.payload.model_dump(mode="json"),
            validate=dispatch_validation(request, current),
        )
        result["review_id"] = security.approval_nonce(
            current,
            "knowledge-relations",
            result["intent_digest"],
            await call(keyed_digest, result),
        )
        return await respond(request, dto.KnowledgeRelationReview, result)

    @router.get("/knowledge/relations/commands/{command_id}")
    async def knowledge_relation_receipt(
        command_id: UUID, request: Request
    ) -> JSONResponse:
        current = await session(request)
        from row_bot.application.knowledge_relations import read_relation_command

        result = await call(
            read_relation_command,
            command_id=str(command_id),
            owner_id=current.id,
            authority_id=current.id,
            validate=dispatch_validation(request, current),
        )
        return await respond(request, dto.KnowledgeRelationReceipt, result)

    @router.post("/knowledge/relations/commands")
    async def knowledge_relation_command(request: Request) -> JSONResponse:
        current = await session(request, lane="mutation")
        body = await _body(request, dto.Command, 16 * 1024)
        kinds = {
            "knowledge.relation.add",
            "knowledge.relation.remove",
            "knowledge.supersede",
        }
        if body.type not in kinds or str(body.client_session_id) != current.id:
            raise ProtocolError("invalid_command", 422)
        key = request.headers.get("idempotency-key", "")
        if key != str(body.command_id):
            raise ProtocolError("idempotency_mismatch", 409)
        from row_bot.application.knowledge_relations import execute_relation_command
        from row_bot.runtime.admissions import keyed_digest

        validate_access = dispatch_validation(request, current)

        def validate_review(original: dict, review: dict) -> None:
            validate_access()
            security.consume_nonce(
                current,
                "knowledge-relations",
                review["intent_digest"],
                keyed_digest(review),
                original["payload"]["review_id"],
                original["command_id"],
            )

        def validate_action(kind: str) -> None:
            validate_access()
            if kind not in kinds:
                raise ProtocolError("action_denied", 403)

        result = await call(
            execute_relation_command,
            body.model_dump(mode="json"),
            owner_id=current.id,
            authority_id=current.id,
            key=key,
            validate=validate_access,
            validate_action=validate_action,
            validate_review=validate_review,
        )
        return await respond(request, dto.KnowledgeRelationReceipt, result)

    @router.get("/knowledge/entities")
    async def saved_entities(
        request: Request,
        query: str = "",
        entity_type: str | None = None,
        status: str | None = None,
        source: str | None = None,
        tier: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> JSONResponse:
        await session(request)
        from row_bot.knowledge_views import list_saved_entities

        return await respond(
            request,
            dto.EntitySummaryPage,
            asdict(
                await call(
                    list_saved_entities,
                    query=query,
                    entity_type=entity_type,
                    status=status,
                    source=source,
                    tier=tier,
                    cursor=cursor,
                    limit=limit,
                )
            ),
        )

    @router.get("/knowledge/entities/{entity_id}")
    async def saved_entity_detail(entity_id: str, request: Request) -> JSONResponse:
        await session(request)
        from row_bot.knowledge_views import read_saved_entity_detail

        return await respond(
            request,
            dto.KnowledgeEntityDetail,
            asdict(await call(read_saved_entity_detail, entity_id)),
        )

    @router.get("/knowledge/recalls")
    async def knowledge_recalls(request: Request) -> JSONResponse:
        await session(request)
        from row_bot.knowledge_views import read_recent_recall_decisions

        return await respond(
            request,
            dto.KnowledgeRecallPage,
            asdict(await call(read_recent_recall_decisions)),
        )

    @router.get("/knowledge/change-log")
    async def knowledge_change_log(request: Request) -> JSONResponse:
        await session(request)
        from row_bot.knowledge_views import read_memory_change_log

        return await respond(
            request,
            dto.KnowledgeMemoryChangePage,
            asdict(await call(read_memory_change_log)),
        )

    @router.get("/knowledge/documents")
    async def saved_documents(
        request: Request,
        query: str = "",
        status: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> JSONResponse:
        await session(request)
        from row_bot.knowledge_views import list_saved_documents

        return await respond(
            request,
            dto.DocumentSummaryPage,
            asdict(
                await call(
                    list_saved_documents,
                    query=query,
                    status=status,
                    cursor=cursor,
                    limit=limit,
                )
            ),
        )

    @router.post("/knowledge/documents/removal-review")
    async def document_removal_review(request: Request) -> JSONResponse:
        current = await session(request, lane="mutation")
        body = await _body(request, dto.DocumentRemovalReviewRequest, 2048)
        from row_bot.application.document_commands import read_document_removal_review
        from row_bot.runtime.admissions import keyed_digest

        result = await call(
            read_document_removal_review,
            body.document_id,
            validate=dispatch_validation(request, current),
        )
        result["review_id"] = security.approval_nonce(
            current,
            "documents:" + (result["document_id"] or "all"),
            result["source_revision"],
            await call(keyed_digest, result),
        )
        return await respond(request, dto.DocumentRemovalReview, result)

    @router.post("/knowledge/documents/removals/{command_id}/retry-review")
    async def document_retry_review(command_id: UUID, request: Request) -> JSONResponse:
        current = await session(request, lane="mutation")
        await _body(request, dto.EmptyPayload, 2048)
        from row_bot.application.document_commands import read_document_retry_review
        from row_bot.runtime.admissions import keyed_digest

        result = await call(
            read_document_retry_review,
            owner_id=current.id,
            authority_id=current.id,
            source_command_id=str(command_id),
            validate=dispatch_validation(request, current),
        )
        result["review_id"] = security.approval_nonce(
            current,
            "documents:" + (result["document_id"] or "all"),
            result["source_revision"],
            await call(keyed_digest, result),
        )
        return await respond(request, dto.DocumentRemovalReview, result)

    @router.get("/knowledge/documents/removals/{command_id}")
    async def document_removal_receipt(
        command_id: UUID, request: Request
    ) -> JSONResponse:
        current = await session(request)
        from row_bot.application.document_commands import read_document_command

        result = await call(
            read_document_command,
            owner_id=current.id,
            authority_id=current.id,
            command_id=str(command_id),
            validate=dispatch_validation(request, current),
        )
        return await respond(request, dto.DocumentRemovalReceipt, result)

    @router.post("/knowledge/documents/commands")
    async def document_commands(request: Request) -> JSONResponse:
        return await command("documents", request, document_command=True)

    @router.get("/settings/tools")
    async def cached_tools(
        request: Request,
        source: str | None = None,
        query: str = "",
        cursor: str | None = None,
        limit: int = 50,
    ) -> JSONResponse:
        await session(request)
        from row_bot.agent_tool_catalog import list_cached_tools

        return await respond(
            request,
            dto.ToolCatalogPage,
            asdict(
                await call(
                    list_cached_tools,
                    source=source,
                    query=query,
                    cursor=cursor,
                    limit=limit,
                )
            ),
        )

    @router.get("/settings/snapshot")
    async def settings_snapshot(request: Request) -> JSONResponse:
        current = await session(request)
        from row_bot.application.settings_snapshot import read_settings_snapshot

        result = await call(
            read_settings_snapshot,
            validate=dispatch_validation(request, current),
        )
        return await respond(request, dto.SettingsSnapshot, result)

    @router.post("/settings/snapshot/review")
    async def settings_snapshot_review(request: Request) -> JSONResponse:
        current = await session(request, lane="mutation")
        body = await _body(request, dto.SettingsMutationRequest, 32768)
        from row_bot.application.settings_commands import review_settings_update

        result = await call(
            review_settings_update,
            body.settings_revision,
            body.page,
            body.field,
            body.value,
            validate=dispatch_validation(request, current),
            resolve_folder_grant=lambda grant_id: str(
                folder_selections.resolve(grant_id, current.id).path
            ),
        )
        result["review_id"] = security.approval_nonce(
            current,
            f"settings:snapshot:{result['page']}:{result['field']}",
            result["settings_revision"],
            result["action_digest"],
        )
        return await respond(request, dto.SettingsMutationReview, result)

    @router.get("/settings/snapshot/commands/{command_id}")
    async def settings_snapshot_receipt(
        command_id: UUID, request: Request
    ) -> JSONResponse:
        current = await session(request)
        from row_bot.application.settings_commands import read_settings_receipt

        result = await call(
            read_settings_receipt,
            str(command_id),
            owner_id=current.id,
            validate=dispatch_validation(request, current),
        )
        if result is None:
            raise ProtocolError("not_found", 404)
        return await respond(request, dto.SettingsMutationReceipt, result)

    @router.post("/settings/snapshot/commands")
    async def settings_snapshot_command(request: Request) -> JSONResponse:
        current = await session(request, lane="mutation")
        body = await _body(request, dto.SettingsMutationCommand, 32768)
        if request.headers.get("idempotency-key") != str(body.command_id):
            raise ProtocolError("idempotency_mismatch", 409)
        if str(body.client_session_id) != current.id:
            raise ProtocolError("action_denied", 403)
        from row_bot.application.settings_commands import execute_settings_update

        wire = body.model_dump(mode="json")
        validate = dispatch_validation(request, current)

        def validate_review(review: dict[str, Any]) -> None:
            validate()
            security.consume_nonce(
                current,
                f"settings:snapshot:{review['page']}:{review['field']}",
                review["settings_revision"],
                review["action_digest"],
                body.payload.review_id,
                str(body.command_id),
            )

        result = await call(
            execute_settings_update,
            owner_id=current.id,
            key=str(body.command_id),
            command=wire,
            validate=validate,
            validate_review=validate_review,
            resolve_folder_grant=lambda grant_id: str(
                folder_selections.resolve(grant_id, current.id).path
            ),
        )
        return await respond(request, dto.SettingsMutationReceipt, result)

    @router.get("/tasks")
    async def saved_tasks(
        request: Request,
        query: str = "",
        enabled: bool | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> JSONResponse:
        await session(request)
        from row_bot.task_views import list_saved_tasks

        return await respond(
            request,
            dto.TaskSummaryPage,
            asdict(
                await call(
                    list_saved_tasks,
                    query=query,
                    enabled=enabled,
                    cursor=cursor,
                    limit=limit,
                )
            ),
        )

    @router.get("/settings/providers")
    async def provider_status(request: Request) -> JSONResponse:
        await session(request)
        from row_bot.providers.client_status import read_provider_snapshot

        return await respond(
            request,
            dto.ProviderStatusSnapshot,
            asdict(await call(read_provider_snapshot)),
        )

    @router.get("/settings/providers/live")
    async def live_provider_status(request: Request) -> JSONResponse:
        current = await session(request)
        from row_bot.providers.live_settings import read_live_provider_cards

        result = await call(read_live_provider_cards)
        await call(dispatch_validation(request, current))
        return await respond(request, dto.ProviderLiveSnapshot, result)

    @router.post("/settings/providers/live/{provider_id}/refresh")
    async def refresh_live_provider(provider_id: str, request: Request) -> JSONResponse:
        current = await session(request, lane="mutation")
        from row_bot.providers.catalog import get_provider_definition
        from row_bot.providers.custom import get_custom_endpoint
        from row_bot.providers.model_catalog_cache import start_model_catalog_refresh_background

        if get_provider_definition(provider_id) is None and get_custom_endpoint(provider_id) is None:
            raise ProtocolError("not_found", 404)
        await call(dispatch_validation(request, current))
        started = await call(start_model_catalog_refresh_background, reason="manual", provider_id=provider_id, force=True)
        return await respond(request, dto.ProviderCatalogRefresh, {
            "running": True, "started": started, "provider_id": provider_id if started else "",
        })

    @router.get("/settings/providers/live/refresh")
    async def live_provider_refresh_state(request: Request) -> JSONResponse:
        current = await session(request)
        from row_bot.providers.model_catalog_cache import model_catalog_refresh_state

        state = await call(model_catalog_refresh_state)
        result = state.get("last_result") if isinstance(state.get("last_result"), dict) else {}
        provider_id = str(result.get("provider_id") or "")
        provider_statuses = result.get("provider_status") if isinstance(result.get("provider_status"), dict) else {}
        provider = provider_statuses.get(provider_id) if isinstance(provider_statuses.get(provider_id), dict) else {}
        count = provider.get("count")
        await call(dispatch_validation(request, current))
        return await respond(request, dto.ProviderCatalogRefresh, {
            "running": bool(state.get("running")), "started": False,
            "provider_id": provider_id,
            "ok": bool(result.get("ok")) if result else None,
            "model_count": count if type(count) is int and count >= 0 else None,
            "message": "",
        })

    @router.post("/settings/providers/live/{provider_id}/runtime-test")
    async def live_provider_runtime_test(provider_id: str, request: Request) -> JSONResponse:
        current = await session(request, lane="mutation")
        if provider_id == "claude_subscription":
            from row_bot.providers.claude_subscription import run_claude_subscription_runtime_probe as probe
        elif provider_id == "xai_oauth":
            from row_bot.providers.xai_oauth import run_xai_oauth_runtime_probe as probe
        else:
            raise ProtocolError("not_found", 404)
        await call(dispatch_validation(request, current))
        try:
            result = await call(probe)
        except Exception:
            result = {"ok": False}
        await call(dispatch_validation(request, current))
        ok = bool(result.get("ok")) if isinstance(result, dict) else False
        return await respond(request, dto.ProviderRuntimeProbe, {
            "provider_id": provider_id, "ok": ok,
            "detail": "Runtime and tool calls work" if ok else "Runtime test failed",
        })

    @router.post("/settings/providers/commands")
    async def provider_mutation(request: Request) -> JSONResponse:
        return await command("providers", request, provider_command=True)

    @router.get("/settings/providers/configuration")
    async def provider_configuration(
        request: Request, query: str = "", cursor: str | None = None
    ) -> JSONResponse:
        current = await session(request)
        from row_bot.application.provider_configuration_controls import (
            read_provider_configuration,
        )

        validate = dispatch_validation(request, current)
        result = await call(read_provider_configuration, query=query, cursor=cursor)
        await call(validate)
        return await respond(request, dto.ProviderConfigurationPage, asdict(result))

    @router.post("/settings/providers/configuration/review")
    async def provider_configuration_review(request: Request) -> JSONResponse:
        current = await session(request, lane="mutation")
        body = await _body(request, dto.ProviderConfigurationReviewRequest, 32768)
        from row_bot.application.provider_configuration_controls import (
            review_provider_configuration,
        )

        result = await call(
            review_provider_configuration,
            body.operation,
            body.configuration_revision,
            body.fields.model_dump(mode="json"),
            validate=dispatch_validation(request, current),
        )
        result["nonce"] = security.approval_nonce(
            current,
            "provider_configuration",
            result["configuration_revision"],
            result["action_digest"],
        )
        return await respond(request, dto.ProviderConfigurationReview, result)

    @router.get("/settings/providers/configuration/receipts/{command_id}")
    async def provider_configuration_receipt(
        command_id: str, request: Request
    ) -> JSONResponse:
        current = await session(request)
        try:
            identity = str(UUID(command_id))
        except ValueError:
            raise ProtocolError("invalid_command", 422) from None
        from row_bot.application.provider_configuration_controls import (
            read_provider_configuration_receipt,
        )

        result = await call(
            read_provider_configuration_receipt,
            owner_id=security.instance_id,
            command_id=identity,
            validate=dispatch_validation(request, current),
        )
        if result is None:
            raise ProtocolError("not_found", 404)
        return await respond(request, dto.ProviderConfigurationReceipt, result)

    def document_service() -> Any:
        from row_bot import document_jobs

        # Mutations borrow the existing worker's service; passive reads never
        # initialize it or start a supervisor.
        supervisor = document_jobs._supervisor
        return (
            supervisor.service
            if supervisor is not None
            else document_jobs.get_document_job_service()
        )

    def processing_policy(owner: str, conversation: str) -> Any:
        from row_bot.application.document_processing import DocumentProcessingPolicy
        from row_bot.application.profile_controls import freeze_profile
        from row_bot.application.reasoning_controls import freeze_reasoning
        from row_bot.runtime import admissions
        from row_bot.approval_policy import normalize_approval_mode

        def validate() -> None:
            security.validate_worker(owner)
            service._metadata(conversation)
            if admissions.deletion_state(conversation) != "active":
                raise ProtocolError("conversation_deleting", 409)

        def context() -> dict:
            validate()
            row = service._metadata(conversation)
            selection = row.get("model_override")
            if not selection:
                from row_bot.application.provider_default_model import (
                    read_default_model,
                )

                selection = read_default_model(validate=validate).selection_ref
            if not isinstance(selection, str) or not selection.startswith("model:"):
                raise ProtocolError("document_processing_model_unavailable", 409)
            value = {
                "thread_id": conversation,
                "model_override": selection,
                "approval_mode": normalize_approval_mode(row.get("approval_mode")),
                "agent_profile_id": row.get("agent_profile_id") or "",
                "policy_revision": security.policy_revision,
            }
            freeze_profile(value)
            freeze_reasoning(value, conversation)
            return value

        def action(kind: str) -> None:
            if kind != "document.batch.process":
                raise ProtocolError("action_denied", 403)
            from row_bot.tools.profile_policy import dispatch_refusal

            current = context()
            allowed = current.get("tool_allowlist")
            refusal = dispatch_refusal(
                current["agent_profile_snapshot"],
                kind,
                {},
                source="core",
                parent="documents",
                allowlist=tuple(allowed) if allowed is not None else None,
            )
            if refusal:
                raise ProtocolError("document_processing_denied", 403)

        authority = admissions.keyed_digest(
            {
                "session": owner,
                "conversation": conversation,
                "server_epoch": service.server_epoch,
            },
            read_only=True,
        )
        return DocumentProcessingPolicy(
            read_context=context,
            validate=validate,
            validate_action=action,
            owner_id=owner,
            authority_id=authority,
            conversation_id=conversation,
        )

    def resolve_processing_policy(proof: dict) -> Any:
        policy = processing_policy(
            proof["processing_owner_id"], proof["conversation_id"]
        )
        policy.validate_admission(proof)
        return policy

    @router.post("/conversations/{conversation_id}/documents/processing/review")
    async def document_processing_review(
        conversation_id: str, request: Request
    ) -> JSONResponse:
        current = await session(request, lane="mutation")
        body = await _body(request, dto.DocumentProcessingReviewRequest, 4096)
        security.bind_worker_validation(current, dispatch_validation(request, current))
        from row_bot.application.document_job_commands import _snapshot, common
        from row_bot.runtime.admissions import keyed_digest

        def review() -> dict:
            policy = processing_policy(current.id, conversation_id)
            policy.validate()
            snapshot = _snapshot([body.batch_id])
            if not any(
                row["id"] == body.batch_id and common._digest(row) == body.revision
                for row in snapshot["batches"]
            ):
                raise ProtocolError("document_queue_changed", 409)
            return policy.review(body.batch_id, common._digest(snapshot))

        value = await call(review)
        value["review_id"] = security.approval_nonce(
            current,
            "document-processing:" + conversation_id,
            value["revision"],
            keyed_digest(value),
        )
        return await respond(request, dto.DocumentProcessingReview, value)

    @router.get(
        "/conversations/{conversation_id}/documents/processing/commands/{command_id}"
    )
    async def document_processing_receipt(
        conversation_id: str, command_id: UUID, request: Request
    ) -> JSONResponse:
        current = await session(request)
        security.bind_worker_validation(current, dispatch_validation(request, current))
        from row_bot.application.document_processing import (
            read_document_processing_command,
        )

        def read() -> dict:
            return read_document_processing_command(
                command_id=str(command_id),
                policy=processing_policy(current.id, conversation_id),
            )

        return await respond(request, dto.DocumentProcessingReceipt, await call(read))

    @router.post("/conversations/{conversation_id}/documents/processing/commands")
    async def document_processing_command(
        conversation_id: str, request: Request
    ) -> JSONResponse:
        current = await session(request, lane="mutation")
        body = await _body(request, Command, 4096)
        if (
            body.type != "document.batch.process"
            or body.payload.get("conversation_id") != conversation_id
            or request.headers.get("idempotency-key") != str(body.command_id)
        ):
            raise ProtocolError("invalid_command", 422)
        if str(body.client_session_id) != current.id:
            raise ProtocolError("action_denied", 403)
        security.bind_worker_validation(current, dispatch_validation(request, current))
        from row_bot.application.document_processing import execute_document_processing
        from row_bot.runtime.admissions import keyed_digest, read_command_metadata

        wire = body.model_dump(mode="json")

        def execute() -> dict:
            policy = processing_policy(current.id, conversation_id)

            def validate_review(original: dict, review: dict) -> None:
                policy.validate()
                security.consume_nonce(
                    current,
                    "document-processing:" + conversation_id,
                    review["revision"],
                    keyed_digest(review),
                    original["payload"]["review_id"],
                    original["command_id"],
                )

            previous = read_command_metadata(current.id, str(body.command_id))
            jobs = None
            if previous is None:
                reviewed = policy.review(
                    body.payload["batch_id"], body.payload["revision"]
                )
                validate_review(wire, reviewed)
                jobs = document_service()
                jobs.processing_policy_resolver = resolve_processing_policy
            value = execute_document_processing(
                wire,
                service=jobs,
                key=str(body.command_id),
                policy=policy,
                validate_review=validate_review,
            )
            if previous is None and value.get("processing") == "admitted":
                from row_bot.document_jobs import ensure_document_supervisor

                ensure_document_supervisor(jobs)
            return value

        return await respond(
            request, dto.DocumentProcessingReceipt, await call(execute)
        )

    @router.post("/documents/uploads/review")
    async def document_upload_review(request: Request) -> JSONResponse:
        current = await session(request, lane="mutation")
        body = await _body(request, dto.DocumentUploadReviewRequest, 16384)
        from row_bot.application.document_upload_commands import (
            read_document_upload_review,
        )
        from row_bot.runtime.admissions import keyed_digest

        value = await call(
            read_document_upload_review,
            body.model_dump(mode="json")["files"],
            validate=dispatch_validation(request, current),
        )
        value["review_id"] = security.approval_nonce(
            current, "document-uploads", value["intent_digest"], keyed_digest(value)
        )
        return await respond(request, dto.DocumentUploadReview, value)

    @router.post("/documents/uploads/commands")
    async def document_upload_command(request: Request) -> JSONResponse:
        if (
            request.headers.get("content-type")
            != "application/vnd.row-bot.document-upload-v1"
        ):
            raise ProtocolError("invalid_document_upload", 422)
        current = await session(request, lane="mutation")
        from row_bot.api.v1.document_upload_stream import DocumentUploadStream
        from row_bot.application.document_upload_commands import (
            execute_document_upload,
            read_document_upload_review,
        )
        from row_bot.runtime.admissions import keyed_digest, read_command_metadata

        uploads.enter_transfer(current.id)
        reader = DocumentUploadStream(request.stream())
        handed_off = False
        try:
            body = Command.model_validate_json(json.dumps(await reader.read_command()))
            if body.type != "document.upload":
                raise ProtocolError("invalid_command", 422)
            if str(body.client_session_id) != current.id:
                raise ProtocolError("action_denied", 403)
            if request.headers.get("idempotency-key") != str(body.command_id):
                raise ProtocolError("invalid_command", 422)
            validate = dispatch_validation(request, current)

            def validate_action(kind: str) -> None:
                validate()
                if kind != "document.upload":
                    raise ProtocolError("action_denied", 403)

            def validate_review(original: dict, review: dict) -> None:
                validate()
                security.consume_nonce(
                    current,
                    "document-uploads",
                    review["intent_digest"],
                    keyed_digest(review),
                    original["payload"]["review_id"],
                    original["command_id"],
                )

            wire = body.model_dump(mode="json")
            previous = await call(
                read_command_metadata, current.id, str(body.command_id)
            )
            jobs, streams = None, ()
            if previous is None:
                reviewed = await call(
                    read_document_upload_review,
                    body.payload["files"],
                    validate=validate,
                )
                await call(validate_review, wire, reviewed)
                streams = reader.files(body.payload["files"])
                jobs = await call(document_service)

            async def run() -> dict:
                try:

                    def execute() -> dict:
                        return asyncio.run(
                            execute_document_upload(
                                wire,
                                streams,
                                service=jobs,
                                owner_id=current.id,
                                authority_id=current.id,
                                key=str(body.command_id),
                                validate=validate,
                                validate_action=validate_action,
                                validate_review=validate_review,
                            )
                        )

                    return await call(execute)
                finally:
                    try:
                        await reader.close()
                    finally:
                        uploads.leave_chunk(current.id)

            worker = asyncio.create_task(run(), name="reviewed-document-upload")
            document_workers.add(worker)
            handed_off = True

            def finished(task: asyncio.Task) -> None:
                document_workers.discard(task)
                try:
                    task.result()
                except BaseException:
                    pass  # Request/receipt owns a content-free error response.

            worker.add_done_callback(finished)
            result = await asyncio.shield(worker)
            return await respond(request, dto.DocumentUploadReceipt, result)
        finally:
            if not handed_off:
                try:
                    await reader.close()
                finally:
                    uploads.leave_chunk(current.id)

    @router.get("/documents/uploads/commands/{command_id}")
    async def document_upload_receipt(
        command_id: UUID, request: Request
    ) -> JSONResponse:
        current = await session(request)
        from row_bot.application.document_upload_commands import (
            read_document_upload_command,
        )

        value = await call(
            read_document_upload_command,
            owner_id=current.id,
            authority_id=current.id,
            command_id=str(command_id),
            validate=dispatch_validation(request, current),
        )
        return await respond(request, dto.DocumentUploadReceipt, value)

    @router.get("/documents/queue")
    async def document_queue(
        request: Request,
        kind: str = "batches",
        batch_id: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> JSONResponse:
        current = await session(request)
        from row_bot.application.document_job_commands import read_document_queue

        value = await call(
            read_document_queue,
            kind=kind,
            batch_id=batch_id,
            cursor=cursor,
            limit=limit,
            validate=dispatch_validation(request, current),
        )
        return await respond(request, dto.DocumentQueuePage, asdict(value))

    @router.post("/documents/queue/review")
    async def document_control_review(request: Request) -> JSONResponse:
        current = await session(request, lane="mutation")
        body = await _body(request, dto.DocumentControlReviewRequest, 16384)
        from row_bot.application.document_job_commands import (
            read_document_control_review,
        )
        from row_bot.runtime.admissions import keyed_digest

        value = await call(
            read_document_control_review,
            body.action,
            body.payload.model_dump(mode="json"),
            validate=dispatch_validation(request, current),
        )
        value["review_id"] = security.approval_nonce(
            current, "document-queue", value["revision"], keyed_digest(value)
        )
        return await respond(request, dto.DocumentControlReview, value)

    @router.get("/documents/queue/commands/{command_id}")
    async def document_control_receipt(
        command_id: UUID, request: Request
    ) -> JSONResponse:
        current = await session(request)
        from row_bot.application.document_job_commands import (
            read_document_control_command,
        )

        value = await call(
            read_document_control_command,
            owner_id=current.id,
            authority_id=current.id,
            command_id=str(command_id),
            validate=dispatch_validation(request, current),
        )
        return await respond(request, dto.DocumentControlReceipt, value)

    @router.post("/documents/queue/commands")
    async def document_control_command(request: Request) -> JSONResponse:
        body = await _body(request, Command, 16384)
        actions = {
            "document.batch.pause",
            "document.batch.resume",
            "document.batch.cancel",
            "document.job.cancel",
            "document.job.retry",
            "document.jobs.clear_finished",
        }
        if body.type not in actions:
            raise ProtocolError("invalid_command", 422)
        current = await session(
            request,
            lane="control"
            if body.type
            in {"document.batch.pause", "document.batch.cancel", "document.job.cancel"}
            else "mutation",
        )
        if str(body.client_session_id) != current.id:
            raise ProtocolError("action_denied", 403)
        if request.headers.get("idempotency-key") != str(body.command_id):
            raise ProtocolError("invalid_command", 422)
        from row_bot.application.document_job_commands import execute_document_control
        from row_bot.runtime.admissions import keyed_digest

        validate = dispatch_validation(request, current)

        def validate_action(kind: str) -> None:
            validate()
            if kind not in actions:
                raise ProtocolError("action_denied", 403)

        def validate_review(original: dict, review: dict) -> None:
            validate()
            security.consume_nonce(
                current,
                "document-queue",
                review["revision"],
                keyed_digest(review),
                original["payload"]["review_id"],
                original["command_id"],
            )

        from row_bot.application.document_job_commands import (
            read_document_control_review,
        )
        from row_bot.runtime.admissions import read_command_metadata

        wire = body.model_dump(mode="json")
        previous = await call(read_command_metadata, current.id, str(body.command_id))
        jobs = None
        if previous is None:
            reviewed = await call(
                read_document_control_review, body.type, body.payload, validate=validate
            )
            await call(validate_review, wire, reviewed)
            jobs = await call(document_service)
        value = await call(
            execute_document_control,
            wire,
            service=jobs,
            owner_id=current.id,
            authority_id=current.id,
            key=str(body.command_id),
            validate=validate,
            validate_action=validate_action,
            validate_review=validate_review,
        )
        return await respond(request, dto.DocumentControlReceipt, value)

    def installation_policy(operation: str) -> dict:
        if operation not in {"resolve", "install"}:
            raise ProtocolError("action_denied", 403)
        return {
            "operation": operation,
            "policy_revision": security.policy_revision,
            "policy": current_policy_snapshot(),
        }

    @router.get("/settings/mcp/installations/{runtime_id}")
    async def runtime_installation_snapshot(
        runtime_id: str, request: Request
    ) -> JSONResponse:
        current = await session(request)
        result = await call(
            runtime_installations.snapshot,
            runtime_id,
            owner_id=current.id,
            validate=dispatch_validation(request, current),
        )
        return await respond(request, dto.RuntimeInstallationSnapshot, asdict(result))

    @router.post("/settings/mcp/installations/review")
    async def runtime_installation_review(request: Request) -> JSONResponse:
        current = await session(request, lane="mutation")
        body = await _body(request, dto.RuntimeInstallationReviewRequest, 4096)
        result = asdict(
            await call(
                runtime_installations.review,
                owner_id=current.id,
                **body.model_dump(mode="json"),
                validate=dispatch_validation(request, current),
                read_policy=installation_policy,
            )
        )
        result["nonce"] = security.approval_nonce(
            current,
            "settings:mcp-runtime:" + result["runtime_id"],
            result["resource_revision"],
            result["action_digest"],
        )
        return await respond(request, dto.RuntimeInstallationReview, result)

    @router.get("/settings/mcp/installations/{runtime_id}/commands/{command_id}")
    async def runtime_installation_receipt(
        runtime_id: str, command_id: UUID, request: Request
    ) -> JSONResponse:
        current = await session(request)
        result = await call(
            runtime_installations.receipt,
            owner_id=current.id,
            runtime_id=runtime_id,
            command_id=str(command_id),
            validate=dispatch_validation(request, current),
        )
        return await respond(request, dto.RuntimeInstallationReceipt, result)

    @router.post("/settings/mcp/installations/commands")
    async def runtime_installation_command(request: Request) -> JSONResponse:
        body = await _body(request, Command, 8192)
        if body.type not in {
            "mcp.runtime.resolve",
            "mcp.runtime.install",
            "mcp.runtime.install.cancel",
        }:
            raise ProtocolError("invalid_command", 422)
        current = await session(
            request, lane="control" if body.type.endswith(".cancel") else "mutation"
        )
        if str(body.client_session_id) != current.id:
            raise ProtocolError("action_denied", 403)
        if request.headers.get("idempotency-key") != str(body.command_id):
            raise ProtocolError("invalid_command", 422)
        validate = dispatch_validation(request, current)

        def validate_review(review: Any) -> None:
            validate()
            security.consume_nonce(
                current,
                "settings:mcp-runtime:" + review.runtime_id,
                review.resource_revision,
                review.action_digest,
                body.payload["nonce"],
                str(body.command_id),
            )

        wire = body.model_dump(mode="json")
        wire["payload"].pop("nonce", None)
        result = await call(
            runtime_installations.execute,
            wire,
            owner_id=current.id,
            key=str(body.command_id),
            validate=validate,
            read_policy=installation_policy,
            validate_review=validate_review,
        )
        return await respond(request, dto.RuntimeInstallationReceipt, result)

    @router.get("/settings/mcp/configuration")
    async def mcp_configuration(
        request: Request, query: str = "", cursor: str | None = None, limit: int = 25
    ) -> JSONResponse:
        current = await session(request)
        from row_bot.application.capability_configuration_controls import (
            read_mcp_configuration,
        )

        result = await call(
            read_mcp_configuration,
            query=query,
            cursor=cursor,
            limit=limit,
            validate=dispatch_validation(request, current),
        )
        return await respond(request, dto.McpConfigurationPage, asdict(result))

    @router.get("/settings/mcp/catalog")
    async def mcp_tested_catalog(
        request: Request,
        server_id: str,
        test_command_id: UUID,
        query: str = "",
        cursor: str | None = None,
        limit: int = 25,
    ) -> JSONResponse:
        current = await session(request)
        from row_bot.application.capability_catalog_controls import (
            read_tested_mcp_catalog,
        )

        result = await call(
            read_tested_mcp_catalog,
            owner_id=security.instance_id,
            server_id=server_id,
            test_command_id=str(test_command_id),
            query=query,
            cursor=cursor,
            limit=limit,
            validate=dispatch_validation(request, current),
        )
        return await respond(request, dto.McpTestedCatalogPage, asdict(result))

    @router.post("/settings/mcp/catalog/review")
    async def mcp_catalog_review(request: Request) -> JSONResponse:
        current = await session(request, lane="mutation")
        body = await _body(request, dto.McpCatalogRequest, 4096)
        from row_bot.application.capability_catalog_controls import (
            review_mcp_catalog_command,
        )

        result = await call(
            review_mcp_catalog_command,
            owner_id=security.instance_id,
            **body.model_dump(mode="json"),
            validate=dispatch_validation(request, current),
        )
        result["nonce"] = security.approval_nonce(
            current,
            "settings:mcp",
            result["configuration_revision"],
            result["action_digest"],
        )
        return await respond(request, dto.McpCatalogReview, result)

    @router.get("/settings/mcp/policy")
    async def mcp_policy(
        request: Request,
        server_id: str | None = None,
        query: str = "",
        cursor: str | None = None,
        limit: int = 25,
    ) -> JSONResponse:
        current = await session(request)
        from row_bot.application.capability_policy_controls import read_mcp_policy

        result = await call(
            read_mcp_policy,
            server_id=server_id,
            query=query,
            cursor=cursor,
            limit=limit,
            validate=dispatch_validation(request, current),
        )
        return await respond(request, dto.McpPolicyPage, asdict(result))

    @router.post("/settings/mcp/policy/review")
    async def mcp_policy_review(request: Request) -> JSONResponse:
        current = await session(request, lane="mutation")
        body = await _body(request, dto.McpPolicyRequest, 4096)
        from row_bot.application.capability_policy_controls import (
            review_mcp_policy_command,
        )

        result = await call(
            review_mcp_policy_command,
            **body.model_dump(mode="json"),
            validate=dispatch_validation(request, current),
        )
        result["nonce"] = security.approval_nonce(
            current,
            "settings:mcp",
            result["configuration_revision"],
            result["action_digest"],
        )
        return await respond(request, dto.McpPolicyReview, result)

    @router.post("/settings/mcp/configuration/review")
    async def mcp_configuration_review(request: Request) -> JSONResponse:
        current = await session(request, lane="mutation")
        body = await _body(request, dto.McpConfigurationReviewRequest, 135168)
        from row_bot.application.capability_configuration_controls import (
            review_mcp_configuration_command,
        )

        result = await call(
            review_mcp_configuration_command,
            body.configuration_revision,
            body.intent.model_dump(mode="json", exclude_unset=True),
            validate=dispatch_validation(request, current),
        )
        result["nonce"] = security.approval_nonce(
            current,
            "settings:mcp",
            result["configuration_revision"],
            result["action_digest"],
        )
        return await respond(request, dto.McpConfigurationReview, result)

    @router.post("/settings/mcp/commands")
    async def mcp_mutation(request: Request) -> JSONResponse:
        return await command("settings:mcp", request, mcp_command=True)

    @router.get("/settings/mcp/runtime/{server_id}")
    async def mcp_runtime_state(server_id: str, request: Request) -> JSONResponse:
        current = await session(request)
        from row_bot.application.capability_runtime_controls import (
            read_mcp_runtime_state,
        )

        result = await call(
            read_mcp_runtime_state,
            server_id,
            validate=dispatch_validation(request, current),
        )
        return await respond(request, dto.McpRuntimeState, asdict(result))

    @router.post("/settings/mcp/runtime/review")
    async def mcp_runtime_review(request: Request) -> JSONResponse:
        body = await _body(request, dto.McpRuntimeReviewRequest, 4096)
        current = await session(
            request, lane="control" if body.operation == "disconnect" else "mutation"
        )
        from row_bot.application.capability_runtime_controls import (
            review_mcp_runtime_command,
        )

        result = await call(
            review_mcp_runtime_command,
            **body.model_dump(mode="json"),
            validate=dispatch_validation(request, current),
        )
        result["nonce"] = security.approval_nonce(
            current,
            "settings:mcp-runtime:" + result["server_id"],
            result["resource_revision"],
            result["action_digest"],
            mcp_server_id=result["server_id"]
            if result["operation"] != "disconnect"
            else None,
        )
        return await respond(request, dto.McpRuntimeReview, result)

    @router.get("/settings/providers/default-model")
    async def default_model(request: Request) -> JSONResponse:
        current = await session(request)
        from row_bot.application.provider_default_model import read_default_model

        return await respond(
            request,
            dto.DefaultModelSnapshot,
            asdict(
                await call(
                    read_default_model, validate=dispatch_validation(request, current)
                )
            ),
        )

    @router.post("/settings/providers/default-model/review")
    async def default_model_review(request: Request) -> JSONResponse:
        current = await session(request, lane="mutation")
        body = await _body(request, dto.DefaultModelReviewRequest, 2048)
        from row_bot.application.provider_default_model import review_default_model

        result = await call(
            review_default_model,
            body.settings_revision,
            body.provider_id,
            body.model_id,
            validate=dispatch_validation(request, current),
        )
        result["nonce"] = security.approval_nonce(
            current,
            "default_model",
            result["settings_revision"],
            result["action_digest"],
        )
        return await respond(request, dto.DefaultModelReview, result)

    @router.get("/settings/providers/default-model/receipts/{command_id}")
    async def default_model_receipt(command_id: str, request: Request) -> JSONResponse:
        current = await session(request)
        try:
            identity = str(UUID(command_id))
        except ValueError:
            raise ProtocolError("invalid_command", 422) from None
        from row_bot.application.provider_default_model import (
            read_default_model_receipt,
        )

        result = await call(
            read_default_model_receipt,
            owner_id=security.instance_id,
            command_id=identity,
            validate=dispatch_validation(request, current),
        )
        if result is None:
            raise ProtocolError("not_found", 404)
        return await respond(request, dto.DefaultModelReceipt, result)

    def buddy_policy(
        request: Request, current: Any, conversation_id: str, confirmation: Callable
    ) -> Any:
        from row_bot.application.buddy_policy import create_buddy_policy
        from row_bot.application.profile_controls import freeze_profile

        validate = dictation_validation(request, current, conversation_id)

        def read_context() -> dict:
            row = service._metadata(conversation_id)
            context = {
                "thread_id": conversation_id,
                "approval_mode": row.get("approval_mode"),
                "agent_profile_id": row.get("agent_profile_id") or "",
            }
            freeze_profile(context)
            return context

        return create_buddy_policy(
            read_context=read_context,
            validate=validate,
            validate_confirmation=confirmation,
        )

    @router.get("/conversations/{conversation_id}/buddy")
    async def buddy_snapshot(conversation_id: str, request: Request) -> JSONResponse:
        current = await session(request)
        from row_bot.buddy.client_service import read_buddy

        result = await call(
            read_buddy, validate=dictation_validation(request, current, conversation_id)
        )
        return await respond(request, dto.BuddySnapshot, asdict(result))

    @router.get("/conversations/{conversation_id}/buddy/packs")
    async def buddy_packs(
        conversation_id: str, request: Request, cursor: str | None = None
    ) -> JSONResponse:
        current = await session(request)
        from row_bot.buddy.client_service import list_buddy_packs

        result = await call(
            list_buddy_packs,
            cursor=cursor,
            validate=dictation_validation(request, current, conversation_id),
        )
        return await respond(request, dto.BuddyPackPage, asdict(result))

    @router.get("/conversations/{conversation_id}/buddy/packs/{pack_id}")
    async def buddy_pack(
        conversation_id: str, pack_id: str, request: Request
    ) -> JSONResponse:
        current = await session(request)
        from row_bot.buddy.client_service import _pack

        validate = dictation_validation(request, current, conversation_id)

        def read() -> dict:
            validate()
            result = asdict(_pack(pack_id)[0])
            validate()
            return result

        return await respond(request, dto.BuddyPack, await call(read))

    @router.get(
        "/conversations/{conversation_id}/buddy/packs/{pack_id}/media/{asset_id}"
    )
    async def buddy_media(
        conversation_id: str,
        pack_id: str,
        asset_id: str,
        revision: str,
        request: Request,
    ) -> Response:
        current = await session(request)
        from row_bot.buddy.client_service import read_buddy_media

        data, content_type = await call(
            read_buddy_media,
            pack_id,
            asset_id,
            expected_revision=revision,
            validate=dictation_validation(request, current, conversation_id),
        )

        async def chunks() -> Any:
            for offset in range(0, len(data), EVENT_LIMIT):
                security.session(await _context(request), current.id, current.csrf)
                await readable_conversation(conversation_id)
                yield data[offset : offset + EVENT_LIMIT]

        return StreamingResponse(
            chunks(),
            media_type=content_type,
            headers={
                **HEADERS,
                "Content-Disposition": "inline",
                "Content-Security-Policy": "default-src 'none'; sandbox",
            },
        )

    @router.post("/conversations/{conversation_id}/buddy/review")
    async def buddy_review(conversation_id: str, request: Request) -> JSONResponse:
        current = await session(request, lane="mutation")
        body = await _body(request, dto.BuddyHatchRequest, 16384)
        from row_bot.application.buddy_commands import review_buddy_action

        policy = buddy_policy(request, current, conversation_id, lambda *_: None)
        result = await call(
            review_buddy_action,
            owner_id=current.id,
            authority_id=conversation_id,
            request=body.model_dump(mode="json", exclude_none=True),
            validate=dictation_validation(request, current, conversation_id),
            capture_provider_policy=policy.capture_provider_policy,
        )
        result["nonce"] = security.approval_nonce(
            current,
            "buddy:" + conversation_id,
            result["config_revision"],
            result["review_id"],
        )
        return await respond(request, dto.BuddyHatchReview, result)

    @router.get("/conversations/{conversation_id}/buddy/commands/{command_id}")
    async def buddy_receipt(
        conversation_id: str, command_id: UUID, request: Request
    ) -> JSONResponse:
        current = await session(request)
        from row_bot.application.buddy_commands import read_buddy_command

        result = await call(
            read_buddy_command,
            owner_id=current.id,
            authority_id=conversation_id,
            command_id=str(command_id),
            validate=dictation_validation(request, current, conversation_id),
        )
        return await respond(request, dto.BuddyReceipt, result)

    @router.post("/conversations/{conversation_id}/buddy/commands")
    async def buddy_command(conversation_id: str, request: Request) -> JSONResponse:
        # Cancellation keeps its reserved control lane, independent of ordinary edits.
        body = await _body(request, dto.Command, 65536)
        current = await session(
            request, lane="control" if body.type == "buddy.cancel" else "mutation"
        )
        if (
            body.type
            not in {"buddy.update", "buddy.hatch", "buddy.remove", "buddy.cancel"}
            or str(body.client_session_id) != current.id
        ):
            raise ProtocolError("invalid_command", 422)
        key = request.headers.get("idempotency-key", "")
        if key != str(body.command_id):
            raise ProtocolError("invalid_command", 422)
        from row_bot.application.buddy_commands import execute_buddy_command
        from row_bot.runtime.admissions import read_command_metadata

        validate = dictation_validation(request, current, conversation_id)
        wire = body.model_dump(mode="json")
        wire["payload"].pop("nonce", None)
        if body.type == "buddy.update":
            wire["payload"]["changes"] = {
                k: v for k, v in body.payload["changes"].items() if v is not None
            }
        if body.type in {"buddy.hatch", "buddy.remove"}:
            wire["payload"]["request"] = {
                k: v for k, v in body.payload["request"].items() if v is not None
            }

        def confirmation(*_args: Any) -> None:
            validate()
            if body.type not in {"buddy.hatch", "buddy.remove"}:
                raise ProtocolError("approval_expired", 409)
            security.consume_nonce(
                current,
                "buddy:" + conversation_id,
                body.payload["request"]["config_revision"],
                body.payload["review_id"],
                body.payload["nonce"],
                str(body.command_id),
            )

        original = await call(read_command_metadata, current.id, str(body.command_id))
        if original is None and body.type in {"buddy.hatch", "buddy.remove"}:
            await call(confirmation)
        policy = buddy_policy(request, current, conversation_id, confirmation)
        result = await call(
            execute_buddy_command,
            wire,
            owner_id=current.id,
            authority_id=conversation_id,
            key=key,
            validate=validate,
            capture_provider_policy=policy.capture_provider_policy,
            validate_action=policy.validate_action,
            validate_provider=policy.validate_provider,
        )
        return await respond(request, dto.BuddyReceipt, result)

    @router.get("/settings/providers/subscriptions/probes")
    async def subscription_probes(request: Request) -> JSONResponse:
        current = await session(request)
        from row_bot.application.subscription_probes import read_probes

        result = await call(read_probes, validate=dispatch_validation(request, current))
        return await respond(request, dto.SubscriptionProbeSnapshot, asdict(result))

    @router.post("/settings/providers/subscriptions/probes/review")
    async def subscription_probe_review(request: Request) -> JSONResponse:
        current = await session(request, lane="mutation")
        body = await _body(request, dto.SubscriptionProbeRequest, 4096)
        from row_bot.application.subscription_probes import review_probe

        result = await call(
            review_probe,
            **body.model_dump(mode="json"),
            validate=dispatch_validation(request, current),
        )
        result["nonce"] = security.approval_nonce(
            current,
            "subscription-probe:" + result["provider_id"],
            result["provider_revision"],
            result["action_digest"],
        )
        return await respond(request, dto.SubscriptionProbeReview, result)

    @router.get("/settings/providers/subscriptions/probes/{command_id}/status")
    async def subscription_probe_status(
        command_id: UUID, request: Request
    ) -> JSONResponse:
        current = await session(request)
        result = await call(
            service.subscription_flows.probe_status,
            owner_id=current.id,
            command_id=str(command_id),
            validate=dispatch_validation(request, current),
        )
        return await respond(
            request, dto.SubscriptionProbeStatus, {"operation": result}
        )

    @router.post("/settings/providers/subscriptions/probes/{command_id}/cancel")
    async def subscription_probe_cancel(
        command_id: UUID, request: Request
    ) -> JSONResponse:
        current = await session(request, lane="control")
        await _body(request, dto.EmptyPayload, 2048)
        result = await call(
            service.subscription_flows.cancel_probe,
            owner_id=current.id,
            command_id=str(command_id),
            validate=dispatch_validation(request, current),
        )
        return await respond(request, dto.SubscriptionProbeState, result)

    @router.get("/settings/providers/subscriptions/probes/{command_id}/receipt")
    async def subscription_probe_receipt(
        command_id: UUID, request: Request
    ) -> JSONResponse:
        current = await session(request)
        from row_bot.application.subscription_probes import read_probe_receipt

        result = await call(
            read_probe_receipt,
            owner_id=current.id,
            command_id=str(command_id),
            validate=dispatch_validation(request, current),
        )
        if result is None:
            raise ProtocolError("not_found", 404)
        return await respond(request, dto.SubscriptionProbeReceipt, result)

    @router.get("/settings/providers/subscriptions/options")
    async def subscription_options(request: Request) -> JSONResponse:
        current = await session(request)
        from row_bot.application.subscription_options import read_options

        result = await call(
            read_options, validate=dispatch_validation(request, current)
        )
        return await respond(request, dto.SubscriptionOptionsSnapshot, asdict(result))

    @router.post("/settings/providers/subscriptions/options/review")
    async def subscription_options_review(request: Request) -> JSONResponse:
        current = await session(request, lane="mutation")
        body = await _body(request, dto.SubscriptionOptionsRequest, 4096)
        from row_bot.application.subscription_options import review_options

        result = await call(
            review_options,
            **body.model_dump(mode="json"),
            validate=dispatch_validation(request, current),
        )
        result["nonce"] = security.approval_nonce(
            current,
            "subscription-options:" + result["provider_id"],
            result["provider_revision"],
            result["action_digest"],
        )
        return await respond(request, dto.SubscriptionOptionsReview, result)

    @router.get("/settings/providers/subscriptions/options/receipts/{command_id}")
    async def subscription_options_receipt(
        command_id: UUID, request: Request
    ) -> JSONResponse:
        current = await session(request)
        from row_bot.application.subscription_options import read_options_receipt

        result = await call(
            read_options_receipt,
            owner_id=current.id,
            command_id=str(command_id),
            validate=dispatch_validation(request, current),
        )
        if result is None:
            raise ProtocolError("not_found", 404)
        return await respond(request, dto.SubscriptionOptionsReceipt, result)

    @router.get("/settings/providers/subscriptions")
    async def subscription_accounts(request: Request) -> JSONResponse:
        current = await session(request)
        from row_bot.application.subscription_controls import read_accounts

        result = await call(
            read_accounts, validate=dispatch_validation(request, current)
        )
        return await respond(request, dto.SubscriptionAccountsSnapshot, asdict(result))

    @router.post("/settings/providers/subscriptions/review")
    async def subscription_review(request: Request) -> JSONResponse:
        current = await session(request, lane="mutation")
        body = await _body(request, dto.SubscriptionActionRequest, 24576)
        from row_bot.application.subscription_controls import review_action

        result = await call(
            review_action,
            **body.model_dump(mode="json"),
            validate=dispatch_validation(request, current),
        )
        result["nonce"] = security.approval_nonce(
            current,
            "subscription:" + result["provider_id"],
            result["provider_revision"],
            result["action_digest"],
        )
        return await respond(request, dto.SubscriptionActionReview, result)

    @router.get("/settings/providers/subscriptions/flows/{flow_id}")
    async def subscription_flow(
        flow_id: str, request: Request, server_epoch: str
    ) -> JSONResponse:
        current = await session(request)
        if len(flow_id) != 36 or len(server_epoch) != 36:
            raise ProtocolError("invalid_command", 422)
        result = await call(
            service.subscription_flows.read,
            owner_id=current.id,
            flow_id=flow_id,
            server_epoch=server_epoch,
            validate=dispatch_validation(request, current),
        )
        return await respond(request, dto.SubscriptionFlowSnapshot, asdict(result))

    @router.get("/settings/providers/subscriptions/{provider_id}/receipts/{command_id}")
    async def subscription_receipt(
        provider_id: str, command_id: str, request: Request
    ) -> JSONResponse:
        current = await session(request)
        from row_bot.application.subscription_controls import read_receipt

        result = await call(
            read_receipt,
            provider_id=provider_id,
            owner_id=current.id,
            command_id=command_id,
            validate=dispatch_validation(request, current),
        )
        if result is None:
            raise ProtocolError("not_found", 404)
        return await respond(request, dto.SubscriptionActionReceipt, result)

    @router.delete("/settings/providers/subscriptions/flows")
    async def subscription_revoke(request: Request) -> JSONResponse:
        current = await session(request, lane="control")
        quiescent = await call(service.subscription_flows.revoke_owner, current.id)
        return await respond(
            request, dto.SubscriptionQuiescence, {"quiescent": quiescent}
        )

    @router.post("/settings/providers/subscriptions/starts/{command_id}/cancel")
    async def subscription_cancel_start(
        command_id: UUID, request: Request
    ) -> JSONResponse:
        current = await session(request, lane="control")
        await _body(request, dto.EmptyPayload, 1024)
        result = await call(
            service.subscription_flows.cancel_start,
            owner_id=current.id,
            command_id=str(command_id),
            validate=dispatch_validation(request, current),
        )
        return await respond(request, dto.SubscriptionFlowSnapshot, asdict(result))

    @router.get("/settings/providers/{provider_id}/credential")
    async def provider_credential(provider_id: str, request: Request) -> JSONResponse:
        current = await session(request)
        from row_bot.application.provider_settings_controls import (
            read_provider_settings,
        )

        validate = dispatch_validation(request, current)
        if provider_id.startswith("custom_openai_"):
            from row_bot.application.provider_custom_credentials import (
                read_custom_provider_credentials,
            )

            result = await call(
                read_custom_provider_credentials, provider_id, validate=validate
            )
        else:
            result = await call(read_provider_settings, provider_id)
        await call(validate)
        return await respond(request, dto.ProviderSettingsSnapshot, asdict(result))

    @router.post("/settings/providers/{provider_id}/credential-review")
    async def provider_credential_review(
        provider_id: str, request: Request
    ) -> JSONResponse:
        current = await session(request, lane="mutation")
        body = await _body(request, dto.ProviderSettingsReviewRequest, 20000)
        from row_bot.application.provider_settings_commands import (
            review_provider_settings_command,
        )

        if provider_id.startswith("custom_openai_"):
            from row_bot.application.provider_custom_credentials import (
                review_custom_provider_credentials,
            )

            review_provider_settings_command = review_custom_provider_credentials
        result = await call(
            review_provider_settings_command,
            provider_id,
            body.provider_revision,
            body.operation,
            body.value,
            validate=dispatch_validation(request, current),
        )
        result["nonce"] = security.approval_nonce(
            current, provider_id, result["provider_revision"], result["action_digest"]
        )
        return await respond(request, dto.ProviderSettingsReview, result)

    @router.get("/settings/providers/{provider_id}/receipts/{command_id}")
    async def provider_credential_receipt(
        provider_id: str, command_id: str, request: Request
    ) -> JSONResponse:
        current = await session(request)
        try:
            identity = str(UUID(command_id))
        except ValueError:
            raise ProtocolError("invalid_command", 422) from None
        from row_bot.application.provider_settings_commands import (
            read_provider_settings_receipt,
        )
        from row_bot.providers.catalog import PROVIDER_DEFINITIONS

        if provider_id.startswith("custom_openai_"):
            from row_bot.application.provider_custom_credentials import (
                read_custom_provider_credentials_receipt,
            )

            read_provider_settings_receipt = read_custom_provider_credentials_receipt
        result = await call(
            read_provider_settings_receipt,
            provider_id,
            owner_id=security.instance_id,
            command_id=identity,
            validate=dispatch_validation(request, current),
        )
        if result is None:
            raise ProtocolError("not_found", 404)
        definition = PROVIDER_DEFINITIONS.get(provider_id)
        result["credential"] = {
            **result["credential"],
            "display_name": result["credential"].get(
                "display_name", definition.display_name if definition else provider_id
            ),
        }
        return await respond(request, dto.ProviderSettingsReceipt, result)

    @router.get("/settings/models/state")
    async def models_settings_state(request: Request) -> JSONResponse:
        current = await session(request)
        from row_bot.application.client_models_settings import read_models_settings
        return await respond(request, dto.ModelsSettingsState, await call(
            read_models_settings, validate=dispatch_validation(request, current)))

    @router.post("/settings/models/surface")
    async def models_surface_update(request: Request) -> JSONResponse:
        current = await session(request, lane="mutation")
        body = await _body(request, dto.ModelSurfaceMutation, 2048)
        from row_bot.application.client_models_settings import update_model_surface
        try:
            result = await call(update_model_surface, body.surface, body.action,
                selection_ref=body.selection_ref, enabled=body.enabled,
                camera_index=body.camera_index,
                validate=dispatch_validation(request, current))
        except ValueError as exc:
            raise ProtocolError(str(exc), 422) from None
        return await respond(request, dto.ModelsSettingsState, result)

    @router.post("/settings/models/context")
    async def models_context_update(request: Request) -> JSONResponse:
        current = await session(request, lane="mutation")
        body = await _body(request, dto.ModelContextMutation, 2048)
        from row_bot.application.client_models_settings import update_model_context
        try:
            result = await call(update_model_context, body.policy_kind, body.cap,
                validate=dispatch_validation(request, current))
        except ValueError as exc:
            raise ProtocolError(str(exc), 422) from None
        return await respond(request, dto.ModelsSettingsState, result)

    @router.get("/settings/models/agents")
    async def models_agent_settings(request: Request) -> JSONResponse:
        current = await session(request)
        from row_bot.application.client_models_settings import read_agent_settings
        return await respond(request, dto.AgentRuntimeSettingsState, await call(
            read_agent_settings, validate=dispatch_validation(request, current)))

    @router.post("/settings/models/agents")
    async def models_agent_settings_save(request: Request) -> JSONResponse:
        current = await session(request, lane="mutation")
        body = await _body(request, dto.AgentRuntimeSettingsState, 2048)
        from row_bot.application.client_models_settings import save_agent_settings
        try:
            result = await call(save_agent_settings, body.model_dump(mode="json"),
                validate=dispatch_validation(request, current))
        except ValueError as exc:
            raise ProtocolError("invalid_agent_settings", 422) from exc
        return await respond(request, dto.AgentRuntimeSettingsState, result)

    @router.post("/settings/models/agents/reset")
    async def models_agent_settings_reset(request: Request) -> JSONResponse:
        current = await session(request, lane="mutation")
        from row_bot.application.client_models_settings import save_agent_settings
        result = await call(save_agent_settings, None,
            validate=dispatch_validation(request, current))
        return await respond(request, dto.AgentRuntimeSettingsState, result)

    @router.get("/settings/models/catalog-summary")
    async def models_catalog_summary(request: Request, surface: str = "chat") -> JSONResponse:
        current = await session(request)
        from row_bot.application.client_models_settings import catalog_provider_summary
        try:
            result = await call(catalog_provider_summary, surface,
                validate=dispatch_validation(request, current))
        except ValueError:
            raise ProtocolError("invalid_model_surface", 422) from None
        return await respond(request, dto.ModelCatalogSummary, result)

    @router.post("/settings/models/refresh")
    async def models_catalog_refresh(request: Request) -> JSONResponse:
        current = await session(request, lane="mutation")
        from row_bot.providers.model_catalog_cache import start_model_catalog_refresh_background
        await call(dispatch_validation(request, current))
        started = await call(start_model_catalog_refresh_background,
            reason="manual", force=True)
        return await respond(request, dto.ProviderCatalogRefresh, {
            "running": True, "started": started, "provider_id": ""})

    @router.post("/settings/models/cameras/refresh")
    async def models_cameras_refresh(request: Request) -> JSONResponse:
        current = await session(request, lane="mutation")
        from row_bot.vision import list_cameras
        result = await call(list_cameras)
        await call(dispatch_validation(request, current))
        return await respond(request, dto.ModelCameraList, {
            "cameras": [camera for camera in result if type(camera) is int and 0 <= camera <= 64][:65]})

    @router.get("/settings/models/catalog")
    async def models_catalog_page(request: Request, surface: str = "chat",
                                  provider_id: str | None = None, query: str = "",
                                  cursor: str | None = None) -> JSONResponse:
        current = await session(request)
        from row_bot.providers.client_status import ProviderStatusError, list_cached_models
        try:
            result = await call(list_cached_models, surface=surface,
                provider_id=provider_id, query=query, cursor=cursor, limit=80,
                readiness=True)
        except ProviderStatusError as exc:
            raise ProtocolError(exc.code, 410 if exc.code == "cursor_expired" else 422) from None
        await call(dispatch_validation(request, current))
        return await respond(request, dto.CachedModelPage, asdict(result))

    @router.get("/settings/models")
    async def cached_models(
        request: Request,
        provider_id: str | None = None,
        query: str = "",
        cursor: str | None = None,
        limit: int = 50,
    ) -> JSONResponse:
        await session(request)
        if (
            not 1 <= limit <= 100
            or len(query) > 256
            or (provider_id and len(provider_id) > 160)
            or (cursor and len(cursor) > 2048)
        ):
            raise ProtocolError("invalid_catalog_query", 422)
        from row_bot.providers.client_status import list_cached_models

        return await respond(
            request,
            dto.CachedModelPage,
            asdict(
                await call(
                    list_cached_models,
                    provider_id=provider_id,
                    query=query,
                    cursor=cursor,
                    limit=limit,
                )
            ),
        )

    @router.get("/resources/setup/deck")
    async def deck_options(request: Request) -> JSONResponse:
        await session(request)
        from row_bot.designer.client_service import deck_setup_options

        return await respond(
            request, dto.DeckSetupOptions, asdict(await call(deck_setup_options))
        )

    @router.get("/resources/setup/artifact/{mode}")
    async def artifact_options(mode: str, request: Request) -> JSONResponse:
        await session(request)
        from row_bot.designer.client_service import artifact_setup_options

        return await respond(
            request,
            dto.ArtifactSetupOptions,
            asdict(await call(artifact_setup_options, mode)),
        )

    @router.get("/resources/library/{kind}")
    async def resource_library(
        kind: str, request: Request, cursor: str | None = None, limit: int = 50
    ) -> JSONResponse:
        await session(request)
        if (
            kind not in {"artifact", "workspace"}
            or not 1 <= limit <= 100
            or (cursor and len(cursor) > 2048)
        ):
            raise ProtocolError("invalid_command", 422)
        from row_bot.application.workspace_setup import resource_choice

        if kind == "artifact":
            from row_bot.designer.client_service import list_artifacts

            page = await call(list_artifacts, cursor=cursor, limit=limit)
            items = [
                {
                    "resource_id": item.id,
                    "kind": kind,
                    "name": item.name,
                    "revision": item.resource_revision,
                    "origin_conversation_id": item.origin_conversation_id,
                    "origin_status": "repair_required"
                    if item.origin_missing
                    else "available"
                    if item.origin_conversation_id
                    else "unassociated",
                    "available": item.available,
                }
                for item in page.items
            ]
        else:
            from row_bot.developer.client_workspace import list_workspace_choices

            page = await call(list_workspace_choices, cursor=cursor, limit=limit)
            items = [
                await call(resource_choice, kind, item.resource_id, item.revision)
                for item in page.items
            ]
        return await respond(
            request,
            dto.ResourceChoicePage,
            {"items": items, "next_cursor": page.next_cursor},
        )

    def bound_resource(conversation_id: str, binding_id: str, kind: str) -> str:
        from row_bot.conversation_resources import list_bindings
        from row_bot.runtime import admissions

        if admissions.deletion_state(conversation_id) != "active":
            raise ProtocolError("conversation_deleting", 409)
        service._metadata(conversation_id)
        binding = next(
            (
                b
                for b in list_bindings(conversation_id).bindings
                if b.binding_id == binding_id and b.kind == kind
            ),
            None,
        )
        if binding is None:
            raise ProtocolError("resource_binding_revoked", 403)
        return binding.resource_id

    @router.get("/conversations/{conversation_id}/artifacts/{binding_id}/preview")
    async def artifact_preview(
        conversation_id: str,
        binding_id: str,
        request: Request,
        page_id: str | None = None,
        known_revision: str | None = None,
        authoring: bool = False,
        preview_id: str = "",
        capability: str = "",
        static_page: bool = False,
    ) -> JSONResponse:
        await session(request)
        identity = await call(bound_resource, conversation_id, binding_id, "artifact")
        if (
            (page_id and len(page_id) > 128)
            or (known_revision and len(known_revision) > 128)
            or len(preview_id) > 128
            or len(capability) > 128
        ):
            raise ProtocolError("invalid_command", 422)
        from row_bot.designer.client_service import read_preview

        result = await call(
            read_preview,
            identity,
            page_id=page_id,
            known_revision=known_revision,
            authoring=authoring,
            preview_id=preview_id,
            capability=capability,
            static_page=static_page,
        )
        await call(bound_resource, conversation_id, binding_id, "artifact")
        return await respond(request, dto.ArtifactPreview, asdict(result))

    @router.get("/conversations/{conversation_id}/artifacts/{binding_id}/editing")
    async def artifact_editing(
        conversation_id: str,
        binding_id: str,
        request: Request,
        page_id: str | None = None,
        page_cursor: str | None = None,
        element_cursor: str | None = None,
        history_cursor: str | None = None,
        limit: int = 25,
        element_id: str | None = None,
    ) -> JSONResponse:
        await session(request, lane="view")
        if (
            not 1 <= limit <= 50
            or (page_id and len(page_id) > 128)
            or any(
                value and len(value) > 2048
                for value in (page_cursor, element_cursor, history_cursor)
            )
            or (element_id and len(element_id) > 256)
        ):
            raise ProtocolError("invalid_command", 422)
        identity = await call(bound_resource, conversation_id, binding_id, "artifact")
        from row_bot.designer.client_editing import read_editing

        result = await call(
            read_editing,
            identity,
            page_id=page_id,
            page_cursor=page_cursor,
            element_cursor=element_cursor,
            history_cursor=history_cursor,
            limit=limit,
            element_id=element_id,
        )
        if (
            await call(bound_resource, conversation_id, binding_id, "artifact")
            != identity
        ):
            raise ProtocolError("resource_binding_revoked", 403)
        return await respond(request, dto.ArtifactEditingState, asdict(result))

    @router.get("/conversations/{conversation_id}/artifacts/{binding_id}/lifecycle")
    async def artifact_lifecycle(
        conversation_id: str,
        binding_id: str,
        request: Request,
        expected_revision: str,
    ) -> JSONResponse:
        await session(request, lane="view")
        if not 1 <= len(expected_revision) <= 128:
            raise ProtocolError("invalid_command", 422)
        identity = await call(bound_resource, conversation_id, binding_id, "artifact")
        from row_bot.application.artifact_lifecycle_commands import (
            read_artifact_lifecycle,
        )

        result = await call(
            read_artifact_lifecycle,
            identity,
            expected_revision=expected_revision,
        )
        if (
            await call(bound_resource, conversation_id, binding_id, "artifact")
            != identity
        ):
            raise ProtocolError("resource_binding_revoked", 403)
        return await respond(request, dto.ArtifactLifecycleState, asdict(result))

    async def design_view(
        conversation_id: str,
        binding_id: str,
        request: Request,
        reader: Callable,
        model: Any,
        **options: Any,
    ) -> JSONResponse:
        await session(request, lane="view")
        if not 1 <= options.get("limit", 25) <= 50 or any(
            value is not None and len(value) > maximum
            for value, maximum in (
                (options.get("page_id"), 128),
                (options.get("element_id"), 256),
                (options.get("cursor"), 2048),
            )
        ):
            raise ProtocolError("invalid_command", 422)
        identity = await call(bound_resource, conversation_id, binding_id, "artifact")
        result = await call(reader, identity, **options)
        if (
            await call(bound_resource, conversation_id, binding_id, "artifact")
            != identity
        ):
            raise ProtocolError("resource_binding_revoked", 403)
        return await respond(request, model, asdict(result))

    @router.get(
        "/conversations/{conversation_id}/artifacts/{binding_id}/design-controls"
    )
    async def design_controls(
        conversation_id: str,
        binding_id: str,
        request: Request,
        page_id: str | None = None,
        element_id: str | None = None,
        section: str = "elements",
        cursor: str | None = None,
        limit: int = 25,
    ) -> JSONResponse:
        from row_bot.designer.client_design_controls import read_controls

        if section not in {"elements", "assets", "fonts", "presets", "interactions"}:
            raise ProtocolError("invalid_design_control", 422)
        return await design_view(
            conversation_id,
            binding_id,
            request,
            read_controls,
            dto.DesignControlsState,
            page_id=page_id,
            element_id=element_id,
            section=section,
            cursor=cursor,
            limit=limit,
        )

    @router.get("/conversations/{conversation_id}/artifacts/{binding_id}/design-review")
    async def design_review(
        conversation_id: str,
        binding_id: str,
        request: Request,
        page_id: str | None = None,
        scope: str = "page",
        cursor: str | None = None,
        limit: int = 25,
    ) -> JSONResponse:
        from row_bot.designer.client_design_controls import read_review

        if scope not in {"page", "project"}:
            raise ProtocolError("invalid_design_control", 422)
        return await design_view(
            conversation_id,
            binding_id,
            request,
            read_review,
            dto.DesignReviewState,
            page_id=page_id,
            scope=scope,
            cursor=cursor,
            limit=limit,
        )

    @router.get("/conversations/{conversation_id}/artifacts/{binding_id}/presentation")
    async def design_presentation(
        conversation_id: str,
        binding_id: str,
        request: Request,
        page_index: int | None = None,
        cursor: str | None = None,
        limit: int = 25,
    ) -> JSONResponse:
        from row_bot.designer.client_design_controls import read_presentation

        return await design_view(
            conversation_id,
            binding_id,
            request,
            read_presentation,
            dto.DesignPresentationState,
            page_index=page_index,
            cursor=cursor,
            limit=limit,
        )

    @router.post("/conversations/{conversation_id}/artifacts/{binding_id}/review-draft")
    async def artifact_review_draft(
        conversation_id: str, binding_id: str, request: Request
    ) -> JSONResponse:
        current = await session(request, lane="view")
        body = await _body(request, dto.ArtifactReviewDraftRequest, 4096)
        identity = await call(bound_resource, conversation_id, binding_id, "artifact")
        from row_bot.designer.client_design_controls import draft_review_fix

        text = await call(draft_review_fix, identity, **body.model_dump())
        await call(dispatch_validation(request, current))
        if (
            await call(bound_resource, conversation_id, binding_id, "artifact")
            != identity
        ):
            raise ProtocolError("resource_binding_revoked", 403)
        return await respond(request, dto.ArtifactReviewDraft, {"text": text})

    @router.post(
        "/conversations/{conversation_id}/artifacts/{binding_id}/preset-review"
    )
    async def artifact_preset_review(
        conversation_id: str, binding_id: str, request: Request
    ) -> JSONResponse:
        current = await session(request, lane="view")
        body = await _body(request, dto.ArtifactPresetReviewRequest, 8192)
        identity = await call(bound_resource, conversation_id, binding_id, "artifact")
        from row_bot.conversation_resources import list_bindings
        from row_bot.designer.client_service import read_artifact
        from row_bot.runtime.admissions import keyed_digest

        def reviewed() -> str:
            dispatch = dispatch_validation_value
            dispatch()
            binding = next(
                (
                    item
                    for item in list_bindings(conversation_id).bindings
                    if item.binding_id == binding_id
                ),
                None,
            )
            if (
                body.target.kind != "artifact"
                or body.target.resource_id != identity
                or body.target.binding_id != binding_id
                or binding is None
                or binding.revision != body.target.binding_revision
            ):
                raise ProtocolError("resource_binding_revoked", 403)
            if read_artifact(identity).updated_at != body.target.resource_revision:
                raise ProtocolError("resource_revision_conflict", 409)
            dispatch()
            return keyed_digest(
                {
                    "command_id": str(body.command_id),
                    "payload": body.model_dump(mode="json", exclude={"command_id"}),
                }
            )

        dispatch_validation_value = dispatch_validation(request, current)
        digest = await call(reviewed)
        if (
            await call(bound_resource, conversation_id, binding_id, "artifact")
            != identity
        ):
            raise ProtocolError("resource_binding_revoked", 403)
        return await respond(
            request,
            dto.ArtifactPresetReview,
            {
                "nonce": security.approval_nonce(
                    current, identity, body.target.resource_revision, digest
                )
            },
        )

    @router.post(
        "/conversations/{conversation_id}/artifacts/{binding_id}/sharing-review"
    )
    async def artifact_share_review(
        conversation_id: str, binding_id: str, request: Request
    ) -> JSONResponse:
        current = await session(request, lane="view")
        options = await _body(request, dto.ArtifactShareOptions)
        identity = await call(bound_resource, conversation_id, binding_id, "artifact")
        from row_bot.designer.client_sharing import prepare_share

        review = await call(prepare_share, identity, **options.model_dump())
        if (
            await call(bound_resource, conversation_id, binding_id, "artifact")
            != identity
        ):
            raise ProtocolError("resource_binding_revoked", 403)
        result = asdict(review)
        result["nonce"] = security.approval_nonce(
            current, identity, review.resource_revision, review.review_id
        )
        return await respond(request, dto.ArtifactShareReview, result)

    @router.get("/sharing/channels")
    async def share_channels(
        request: Request, cursor: str | None = None
    ) -> JSONResponse:
        await session(request, lane="view")
        if cursor and len(cursor) > 256:
            raise ProtocolError("invalid_command", 422)
        from row_bot.application.artifact_sharing import sharing_channels

        return await respond(
            request, dto.ArtifactShareChannels, await call(sharing_channels, cursor)
        )

    async def bound_export(
        conversation_id: str,
        binding_id: str,
        export_id: str,
        request: Request,
        *,
        download: bool = False,
    ) -> Response:
        current = await session(request, lane="view")
        try:
            if str(UUID(export_id)) != export_id:
                raise ValueError
        except ValueError:
            raise ProtocolError("invalid_export", 422) from None
        identity = await call(bound_resource, conversation_id, binding_id, "artifact")
        auth = dispatch_validation(request, current)

        def validate() -> None:
            auth()
            if bound_resource(conversation_id, binding_id, "artifact") != identity:
                raise ProtocolError("resource_binding_revoked", 403)

        from row_bot.designer.client_exports import read_export, read_export_payload

        if not download:
            result = await call(
                read_export,
                identity,
                export_id,
                binding_id=binding_id,
                validate=validate,
            )
            return await respond(request, dto.ArtifactExport, asdict(result))
        descriptor, data = await call(
            read_export_payload,
            identity,
            export_id,
            binding_id=binding_id,
            validate=validate,
        )
        # Validate closed metadata and current access before releasing immutable
        # bytes. Never reopen a filesystem path after the ownership check.
        dto.ArtifactExport.model_validate_json(json.dumps(asdict(descriptor)))
        await call(validate)
        from urllib.parse import quote

        return Response(
            data,
            media_type=descriptor.media_type,
            headers={
                **HEADERS,
                "Content-Security-Policy": "sandbox; default-src 'none'",
                "Content-Disposition": "attachment; filename=export; filename*=UTF-8''"
                + quote(descriptor.filename, safe=""),
            },
        )

    @router.get(
        "/conversations/{conversation_id}/artifacts/{binding_id}/exports/{export_id}"
    )
    async def artifact_export(
        conversation_id: str, binding_id: str, export_id: str, request: Request
    ) -> Response:
        return await bound_export(conversation_id, binding_id, export_id, request)

    @router.get(
        "/conversations/{conversation_id}/artifacts/{binding_id}/exports/{export_id}/download"
    )
    async def artifact_export_download(
        conversation_id: str, binding_id: str, export_id: str, request: Request
    ) -> Response:
        return await bound_export(
            conversation_id, binding_id, export_id, request, download=True
        )

    @router.get("/conversations/{conversation_id}/workspaces/{binding_id}/inspector")
    async def workspace_inspector(
        conversation_id: str, binding_id: str, request: Request, refresh: bool = False
    ) -> JSONResponse:
        await session(request)
        identity = await call(bound_resource, conversation_id, binding_id, "workspace")
        from row_bot.developer.client_workspace import get_workspace_inspector

        result = await get_workspace_inspector(
            identity, conversation_id, refresh=refresh
        )
        await call(bound_resource, conversation_id, binding_id, "workspace")
        return await respond(request, dto.WorkspaceInspector, asdict(result))

    @router.get("/conversations/{conversation_id}/workspaces/{binding_id}/processes")
    async def workspace_processes(
        conversation_id: str, binding_id: str, request: Request
    ) -> JSONResponse:
        await session(request, lane="view")
        identity = await call(bound_resource, conversation_id, binding_id, "workspace")
        from row_bot.developer.client_processes import list_workspace_processes

        result = await call(list_workspace_processes, identity, conversation_id)
        if (
            await call(bound_resource, conversation_id, binding_id, "workspace")
            != identity
            or result.binding_id != binding_id
        ):
            raise ProtocolError("resource_binding_revoked", 403)
        return await respond(request, dto.WorkspaceProcessSnapshot, asdict(result))

    @router.get(
        "/conversations/{conversation_id}/workspaces/{binding_id}/processes/recovery"
    )
    async def workspace_process_recovery(
        conversation_id: str,
        binding_id: str,
        request: Request,
        cursor: str | None = None,
    ) -> JSONResponse:
        current = await session(request, lane="view")
        if cursor and len(cursor) > 64:
            raise ProtocolError("invalid_command", 422)
        identity = await call(bound_resource, conversation_id, binding_id, "workspace")
        from row_bot.application.workspace_process_commands import (
            list_durable_workspace_processes,
        )
        from row_bot.developer.client_processes import list_workspace_processes

        snapshot = await call(list_workspace_processes, identity, conversation_id)
        result = await call(
            list_durable_workspace_processes,
            identity,
            conversation_id,
            binding_id=binding_id,
            binding_revision=snapshot.binding_revision,
            validate=dispatch_validation(request, current),
            cursor=cursor,
        )
        return await respond(request, dto.WorkspaceProcessRecoveryPage, result)

    @router.get(
        "/conversations/{conversation_id}/workspaces/{binding_id}/processes/{process_id}/output"
    )
    async def workspace_process_output(
        conversation_id: str,
        binding_id: str,
        process_id: str,
        request: Request,
        cursor: int = 0,
    ) -> JSONResponse:
        await session(request, lane="view")
        if not 0 <= cursor <= 2**53 - 1 or len(process_id) > 36:
            raise ProtocolError("invalid_command", 422)
        identity = await call(bound_resource, conversation_id, binding_id, "workspace")
        from row_bot.developer.client_processes import get_workspace_process_output

        result = await call(
            get_workspace_process_output, identity, conversation_id, process_id, cursor
        )
        if (
            await call(bound_resource, conversation_id, binding_id, "workspace")
            != identity
        ):
            raise ProtocolError("resource_binding_revoked", 403)
        return await respond(request, dto.WorkspaceProcessOutput, asdict(result))

    @router.post(
        "/conversations/{conversation_id}/workspaces/{binding_id}/processes/review"
    )
    async def workspace_process_review(
        conversation_id: str, binding_id: str, request: Request
    ) -> JSONResponse:
        current = await session(request, lane="view")
        body = await _body(request, dto.WorkspaceProcessReviewRequest, 16384)
        identity = await call(bound_resource, conversation_id, binding_id, "workspace")
        from row_bot.application.workspace_process_commands import (
            get_workspace_process_review,
        )

        result = await call(
            get_workspace_process_review,
            identity,
            conversation_id,
            str(body.command_id),
            body.command,
            validate=dispatch_validation(request, current),
        )
        if result["binding_id"] != binding_id:
            raise ProtocolError("resource_binding_revoked", 403)
        result["nonce"] = security.approval_nonce(
            current, identity, result["resource_revision"], result["action_digest"]
        )
        return await respond(request, dto.WorkspaceProcessReview, result)

    async def import_authority(
        conversation_id: str, binding_id: str, request: Request, current: Any
    ) -> tuple[str, Callable[[], None]]:
        identity = await call(bound_resource, conversation_id, binding_id, "workspace")
        access = dispatch_validation(request, current)

        def validate() -> None:
            access()
            if bound_resource(conversation_id, binding_id, "workspace") != identity:
                raise ProtocolError("resource_binding_revoked", 403)

        return identity, validate

    async def developer_repository_authority(
        conversation_id: str, binding_id: str, request: Request, current: Any
    ) -> tuple[str, Callable[[], None], Callable[[str], None], str]:
        identity = await call(bound_resource, conversation_id, binding_id, "workspace")
        access = dispatch_validation(request, current)

        def validate() -> None:
            access()
            if bound_resource(conversation_id, binding_id, "workspace") != identity:
                raise ProtocolError("resource_binding_revoked", 403)

        def validate_action(kind: str) -> None:
            from row_bot.application.profile_controls import freeze_profile
            from row_bot.tools.profile_policy import dispatch_refusal

            validate()
            row = service._metadata(conversation_id)
            context = {
                "thread_id": conversation_id,
                "approval_mode": row.get("approval_mode"),
                "agent_profile_id": row.get("agent_profile_id") or "",
            }
            if "tool_allowlist" in row:
                context["tool_allowlist"] = row.get("tool_allowlist")
            freeze_profile(context)
            allowed = context.get("tool_allowlist")
            refusal = dispatch_refusal(
                context["agent_profile_snapshot"],
                kind,
                {},
                source="core",
                parent="developer",
                allowlist=tuple(allowed) if allowed is not None else None,
            )
            if refusal:
                raise ProtocolError("developer_repository_action_denied", 403)

        from row_bot.runtime import admissions

        authority_id = admissions.keyed_digest(
            {
                "session": current.id,
                "conversation": conversation_id,
                "binding": binding_id,
                "resource": identity,
                "server_epoch": service.server_epoch,
            },
            read_only=True,
        )
        return identity, validate, validate_action, authority_id

    @router.get("/conversations/{conversation_id}/workspaces/{binding_id}/repository")
    async def developer_repository_snapshot(
        conversation_id: str, binding_id: str, request: Request
    ) -> JSONResponse:
        current = await session(request)
        (
            identity,
            validate,
            _validate_action,
            _authority,
        ) = await developer_repository_authority(
            conversation_id, binding_id, request, current
        )
        from row_bot.application.developer_repository_commands import (
            read_developer_repository,
        )

        result = await call(
            read_developer_repository,
            identity,
            conversation_id,
            validate=validate,
        )
        if result["binding_id"] != binding_id:
            raise ProtocolError("resource_binding_revoked", 403)
        return await respond(request, dto.DeveloperRepositorySnapshot, result)

    @router.post(
        "/conversations/{conversation_id}/workspaces/{binding_id}/repository/review"
    )
    async def developer_repository_review(
        conversation_id: str, binding_id: str, request: Request
    ) -> JSONResponse:
        current = await session(request, lane="mutation")
        body = await _body(request, dto.DeveloperRepositoryReviewRequest, 32 * 1024)
        (
            identity,
            validate,
            _validate_action,
            _authority,
        ) = await developer_repository_authority(
            conversation_id, binding_id, request, current
        )
        from row_bot.application.developer_repository_commands import (
            review_developer_repository_command,
        )

        result = await call(
            review_developer_repository_command,
            body.action,
            body.payload.model_dump(mode="json"),
            identity,
            conversation_id,
            validate=validate,
        )
        if result["binding_id"] != binding_id:
            raise ProtocolError("resource_binding_revoked", 403)
        result["review_id"] = security.approval_nonce(
            current, identity, result["revision"], result["action_digest"]
        )
        return await respond(request, dto.DeveloperRepositoryReview, result)

    @router.get(
        "/conversations/{conversation_id}/workspaces/{binding_id}/repository/commands/{command_id}"
    )
    async def developer_repository_receipt(
        conversation_id: str, binding_id: str, command_id: UUID, request: Request
    ) -> JSONResponse:
        current = await session(request)
        (
            identity,
            validate,
            _validate_action,
            authority_id,
        ) = await developer_repository_authority(
            conversation_id, binding_id, request, current
        )
        from row_bot.application.developer_repository_commands import (
            read_developer_repository_receipt,
        )

        result = await call(
            read_developer_repository_receipt,
            owner_id=current.id,
            authority_id=authority_id,
            resource_id=identity,
            conversation_id=conversation_id,
            command_id=str(command_id),
            validate=validate,
        )
        return await respond(request, dto.DeveloperRepositoryReceipt, result)

    @router.post(
        "/conversations/{conversation_id}/workspaces/{binding_id}/repository/commands"
    )
    async def developer_repository_command(
        conversation_id: str, binding_id: str, request: Request
    ) -> JSONResponse:
        current = await session(request, lane="mutation")
        body = await _body(request, dto.Command, 64 * 1024)
        if (
            not body.type.startswith("developer.repository.")
            or str(body.client_session_id) != current.id
        ):
            raise ProtocolError("invalid_command", 422)
        key = request.headers.get("idempotency-key", "")
        if key != str(body.command_id):
            raise ProtocolError("idempotency_mismatch", 409)
        (
            identity,
            validate,
            validate_action,
            authority_id,
        ) = await developer_repository_authority(
            conversation_id, binding_id, request, current
        )
        from row_bot.application.developer_repository_commands import (
            execute_developer_repository_command,
        )

        command = body.model_dump(mode="json")

        def validate_review(wire_command: dict, review: dict) -> None:
            validate()
            if (
                review["resource_id"] != identity
                or review["conversation_id"] != conversation_id
                or review["binding_id"] != binding_id
            ):
                raise ProtocolError("resource_binding_revoked", 403)
            security.consume_nonce(
                current,
                identity,
                review["revision"],
                review["action_digest"],
                wire_command["payload"]["nonce"],
                wire_command["command_id"],
            )

        result = await call(
            execute_developer_repository_command,
            command,
            identity,
            conversation_id,
            owner_id=current.id,
            authority_id=authority_id,
            key=key,
            validate=validate,
            validate_action=validate_action,
            validate_review=validate_review,
        )
        return await respond(request, dto.DeveloperRepositoryReceipt, result)

    @router.get("/conversations/{conversation_id}/workspaces/{binding_id}/imports")
    async def workspace_imports(
        conversation_id: str,
        binding_id: str,
        request: Request,
        cursor: str | None = None,
        limit: int = 50,
    ) -> JSONResponse:
        current = await session(request)
        identity, validate = await import_authority(
            conversation_id, binding_id, request, current
        )
        from row_bot.developer.client_imports import list_workspace_imports

        result = await call(
            list_workspace_imports,
            identity,
            conversation_id,
            cursor=cursor,
            limit=limit,
            validate=validate,
        )
        return await respond(request, dto.WorkspaceImportPage, asdict(result))

    @router.get(
        "/conversations/{conversation_id}/workspaces/{binding_id}/imports/patch"
    )
    async def workspace_import_patch(
        conversation_id: str,
        binding_id: str,
        request: Request,
        pending_change_id: str,
        revision: str,
        offset: int = 0,
    ) -> JSONResponse:
        current = await session(request)
        identity, validate = await import_authority(
            conversation_id, binding_id, request, current
        )
        from row_bot.developer.client_imports import read_workspace_import_patch

        if len(pending_change_id) > 128 or len(revision) > 128:
            raise ProtocolError("invalid_command", 422)
        result = await call(
            read_workspace_import_patch,
            identity,
            conversation_id,
            pending_change_id,
            expected_revision=revision,
            offset=offset,
            validate=validate,
        )
        return await respond(request, dto.WorkspaceImportPatch, asdict(result))

    @router.post(
        "/conversations/{conversation_id}/workspaces/{binding_id}/imports/review"
    )
    async def workspace_import_review(
        conversation_id: str, binding_id: str, request: Request
    ) -> JSONResponse:
        current = await session(request, lane="view")
        body = await _body(request, dto.WorkspaceImportReviewRequest, 4096)
        identity, validate = await import_authority(
            conversation_id, binding_id, request, current
        )
        from row_bot.developer.client_imports import review_workspace_import

        result = asdict(
            await call(
                review_workspace_import,
                identity,
                conversation_id,
                body.pending_change_id,
                validate=validate,
            )
        )
        result["nonce"] = security.approval_nonce(
            current, identity, result["resource_revision"], result["action_digest"]
        )
        return await respond(request, dto.WorkspaceImportReview, result)

    @router.get(
        "/conversations/{conversation_id}/workspaces/{binding_id}/imports/commands/{command_id}"
    )
    async def workspace_import_receipt(
        conversation_id: str, binding_id: str, command_id: UUID, request: Request
    ) -> JSONResponse:
        current = await session(request)
        identity, validate = await import_authority(
            conversation_id, binding_id, request, current
        )
        from row_bot.application.workspace_import_commands import (
            read_workspace_import_command,
        )

        result = await call(
            read_workspace_import_command,
            current.id,
            str(command_id),
            conversation_id,
            binding_id,
            validate=validate,
        )
        if result["resource_id"] != identity:
            raise ProtocolError("resource_binding_revoked", 403)
        return await respond(request, dto.WorkspaceImportResult, result)

    @router.post(
        "/conversations/{conversation_id}/workspaces/{binding_id}/imports/commands/{command_id}/review"
    )
    async def workspace_import_recovery_review(
        conversation_id: str, binding_id: str, command_id: UUID, request: Request
    ) -> JSONResponse:
        current = await session(request, lane="view")
        await _body(request, dto.EmptyPayload, 1024)
        identity, validate = await import_authority(
            conversation_id, binding_id, request, current
        )
        from row_bot.application.workspace_import_commands import (
            review_workspace_import_recovery,
        )

        result = await call(
            review_workspace_import_recovery,
            current.id,
            str(command_id),
            conversation_id,
            binding_id,
            validate=validate,
        )
        if result["resource_id"] != identity:
            raise ProtocolError("resource_binding_revoked", 403)
        result["nonce"] = security.approval_nonce(
            current, identity, result["resource_revision"], result["action_digest"]
        )
        return await respond(request, dto.WorkspaceImportReview, result)

    @router.post(
        "/conversations/{conversation_id}/workspaces/{binding_id}/imports/commands"
    )
    async def workspace_import_command(
        conversation_id: str, binding_id: str, request: Request
    ) -> JSONResponse:
        current = await session(request, lane="mutation")
        body = await _body(request, dto.Command, 64 * 1024)
        if body.type != "workspace.import" or str(body.client_session_id) != current.id:
            raise ProtocolError("invalid_command", 422)
        identity, validate = await import_authority(
            conversation_id, binding_id, request, current
        )
        if body.payload["review"]["resource_id"] != identity:
            raise ProtocolError("resource_binding_revoked", 403)
        key = request.headers.get("idempotency-key", "")
        if key != str(body.command_id):
            raise ProtocolError("idempotency_mismatch", 409)
        from row_bot.application.workspace_import_commands import (
            execute_workspace_import,
        )

        def approval(review: dict, nonce: str, command_id: str) -> None:
            validate()
            security.consume_nonce(
                current,
                identity,
                review["resource_revision"],
                review["action_digest"],
                nonce,
                command_id,
            )

        result = await call(
            execute_workspace_import,
            body.model_dump(mode="json"),
            conversation_id,
            binding_id,
            owner=current.id,
            key=key,
            validate=validate,
            validate_review=approval,
        )
        return await respond(request, dto.WorkspaceImportResult, result)

    @router.post("/conversations/{conversation_id}/workspaces/{binding_id}/undo/review")
    async def workspace_undo_review(
        conversation_id: str, binding_id: str, request: Request
    ) -> JSONResponse:
        current = await session(request, lane="view")
        body = await _body(request, dto.WorkspaceUndoReviewRequest, 4096)
        identity, validate = await import_authority(
            conversation_id, binding_id, request, current
        )
        from row_bot.developer.client_undo import review_workspace_undo

        result = asdict(
            await call(
                review_workspace_undo,
                identity,
                conversation_id,
                body.change_set_id,
                validate=validate,
            )
        )
        result["nonce"] = security.approval_nonce(
            current, identity, result["resource_revision"], result["action_digest"]
        )
        return await respond(request, dto.WorkspaceUndoReview, result)

    @router.get(
        "/conversations/{conversation_id}/workspaces/{binding_id}/undo/commands/{command_id}"
    )
    async def workspace_undo_receipt(
        conversation_id: str, binding_id: str, command_id: UUID, request: Request
    ) -> JSONResponse:
        current = await session(request)
        identity, validate = await import_authority(
            conversation_id, binding_id, request, current
        )
        from row_bot.application.workspace_undo_commands import (
            read_workspace_undo_command,
        )

        result = await call(
            read_workspace_undo_command,
            current.id,
            str(command_id),
            conversation_id,
            binding_id,
            validate=validate,
        )
        if result["resource_id"] != identity:
            raise ProtocolError("resource_binding_revoked", 403)
        return await respond(request, dto.WorkspaceUndoResult, result)

    @router.post(
        "/conversations/{conversation_id}/workspaces/{binding_id}/undo/commands/{command_id}/review"
    )
    async def workspace_undo_recovery_review(
        conversation_id: str, binding_id: str, command_id: UUID, request: Request
    ) -> JSONResponse:
        current = await session(request, lane="view")
        await _body(request, dto.EmptyPayload, 1024)
        identity, validate = await import_authority(
            conversation_id, binding_id, request, current
        )
        from row_bot.application.workspace_undo_commands import (
            review_workspace_undo_recovery,
        )

        result = await call(
            review_workspace_undo_recovery,
            current.id,
            str(command_id),
            conversation_id,
            binding_id,
            validate=validate,
        )
        if result["resource_id"] != identity:
            raise ProtocolError("resource_binding_revoked", 403)
        result["nonce"] = security.approval_nonce(
            current, identity, result["resource_revision"], result["action_digest"]
        )
        return await respond(request, dto.WorkspaceUndoReview, result)

    @router.post(
        "/conversations/{conversation_id}/workspaces/{binding_id}/undo/commands"
    )
    async def workspace_undo_command(
        conversation_id: str, binding_id: str, request: Request
    ) -> JSONResponse:
        current = await session(request, lane="mutation")
        body = await _body(request, dto.Command, 64 * 1024)
        if body.type != "workspace.undo" or str(body.client_session_id) != current.id:
            raise ProtocolError("invalid_command", 422)
        identity, validate = await import_authority(
            conversation_id, binding_id, request, current
        )
        review = body.payload["review"]
        if (
            review["resource_id"] != identity
            or review["conversation_id"] != conversation_id
            or review["binding_id"] != binding_id
        ):
            raise ProtocolError("resource_binding_revoked", 403)
        key = request.headers.get("idempotency-key", "")
        if key != str(body.command_id):
            raise ProtocolError("idempotency_mismatch", 409)
        from row_bot.application.workspace_undo_commands import execute_workspace_undo

        def approval(review: dict, nonce: str, command_id: str) -> None:
            validate()
            security.consume_nonce(
                current,
                identity,
                review["resource_revision"],
                review["action_digest"],
                nonce,
                command_id,
            )

        result = await call(
            execute_workspace_undo,
            body.model_dump(mode="json"),
            conversation_id,
            binding_id,
            owner=current.id,
            key=key,
            validate=validate,
            validate_review=approval,
        )
        return await respond(request, dto.WorkspaceUndoResult, result)

    @router.get("/conversations/{conversation_id}/workspaces/{binding_id}/editing")
    async def workspace_editing(
        conversation_id: str, binding_id: str, request: Request, path: str
    ) -> JSONResponse:
        await session(request)
        if not 1 <= len(path) <= 4096:
            raise ProtocolError("invalid_command", 422)
        identity = await call(bound_resource, conversation_id, binding_id, "workspace")
        from row_bot.developer.client_edits import get_workspace_editable_file

        result = await call(
            get_workspace_editable_file, identity, conversation_id, path
        )
        latest = await call(bound_resource, conversation_id, binding_id, "workspace")
        if latest != identity or result.binding_id != binding_id:
            raise ProtocolError("resource_binding_revoked", 403)
        return await respond(request, dto.WorkspaceEditableFile, asdict(result))

    @router.get("/conversations/{conversation_id}/workspaces/{binding_id}/changes")
    async def workspace_changes(
        conversation_id: str,
        binding_id: str,
        request: Request,
        cursor: str | None = None,
        revision: str | None = None,
        limit: int = 50,
    ) -> JSONResponse:
        await session(request)
        identity = await call(bound_resource, conversation_id, binding_id, "workspace")
        if not 1 <= limit <= 100 or (cursor and len(cursor) > 2048):
            raise ProtocolError("invalid_command", 422)
        from row_bot.developer.client_workspace import list_inspector_changes

        result = await call(
            list_inspector_changes, identity, conversation_id, cursor, limit, revision
        )
        await call(bound_resource, conversation_id, binding_id, "workspace")
        return await respond(request, dto.WorkspaceChanges, asdict(result))

    @router.get("/conversations/{conversation_id}/workspaces/{binding_id}/directory")
    async def workspace_directory(
        conversation_id: str,
        binding_id: str,
        request: Request,
        directory: str = "",
        cursor: str | None = None,
        revision: str | None = None,
    ) -> JSONResponse:
        await session(request)
        identity = await call(bound_resource, conversation_id, binding_id, "workspace")
        if len(directory) > 4096 or (cursor and len(cursor) > 2048):
            raise ProtocolError("invalid_command", 422)
        from row_bot.developer.client_workspace import list_workspace_directory

        result = await call(
            list_workspace_directory, identity, directory, cursor, 50, revision
        )
        await call(bound_resource, conversation_id, binding_id, "workspace")
        return await respond(request, dto.WorkspaceDirectory, asdict(result))

    @router.get("/conversations/{conversation_id}/workspaces/{binding_id}/file")
    async def workspace_file(
        conversation_id: str,
        binding_id: str,
        request: Request,
        path: str,
        offset: int = 0,
        revision: str | None = None,
    ) -> JSONResponse:
        await session(request)
        identity = await call(bound_resource, conversation_id, binding_id, "workspace")
        if len(path) > 4096 or offset < 0:
            raise ProtocolError("invalid_command", 422)
        from row_bot.developer.client_workspace import read_workspace_file

        result = await call(
            read_workspace_file, identity, path, offset, 32768, revision
        )
        await call(bound_resource, conversation_id, binding_id, "workspace")
        return await respond(request, dto.WorkspaceFile, asdict(result))

    @router.get("/conversations/{conversation_id}/workspaces/{binding_id}/diff")
    async def workspace_diff(
        conversation_id: str,
        binding_id: str,
        request: Request,
        path: str,
        snapshot_revision: str,
        offset: int = 0,
        revision: str | None = None,
    ) -> JSONResponse:
        await session(request)
        identity = await call(bound_resource, conversation_id, binding_id, "workspace")
        if len(path) > 4096 or offset < 0 or len(snapshot_revision) > 128:
            raise ProtocolError("invalid_command", 422)
        from row_bot.developer.client_workspace import read_workspace_diff

        result = await call(
            read_workspace_diff,
            identity,
            conversation_id,
            path,
            snapshot_revision,
            offset,
            32768,
            revision,
        )
        await call(bound_resource, conversation_id, binding_id, "workspace")
        return await respond(request, dto.WorkspaceDiff, asdict(result))

    @router.get("/conversations/{conversation_id}/workspaces/{binding_id}/change-sets")
    async def workspace_change_sets(
        conversation_id: str,
        binding_id: str,
        request: Request,
        revision: str | None = None,
        cursor: str | None = None,
    ) -> JSONResponse:
        await session(request)
        identity = await call(bound_resource, conversation_id, binding_id, "workspace")
        if (cursor and len(cursor) > 2048) or (revision and len(revision) > 128):
            raise ProtocolError("invalid_command", 422)
        from row_bot.developer.client_workspace import list_inspector_change_sets

        result = await call(
            list_inspector_change_sets, identity, conversation_id, cursor, 50, revision
        )
        await call(bound_resource, conversation_id, binding_id, "workspace")
        return await respond(request, dto.WorkspaceChangeSetPage, asdict(result))

    @router.get(
        "/conversations/{conversation_id}/workspaces/{binding_id}/change-sets/{change_set_id}"
    )
    async def workspace_change_set_files(
        conversation_id: str,
        binding_id: str,
        change_set_id: str,
        request: Request,
        revision: str | None = None,
        cursor: str | None = None,
    ) -> JSONResponse:
        await session(request)
        identity = await call(bound_resource, conversation_id, binding_id, "workspace")
        if (
            len(change_set_id) > 256
            or (cursor and len(cursor) > 2048)
            or (revision and len(revision) > 128)
        ):
            raise ProtocolError("invalid_command", 422)
        from row_bot.developer.client_workspace import list_inspector_change_set_files

        result = await call(
            list_inspector_change_set_files,
            identity,
            conversation_id,
            change_set_id,
            cursor,
            50,
            revision,
        )
        await call(bound_resource, conversation_id, binding_id, "workspace")
        return await respond(request, dto.WorkspaceChangeSetFiles, asdict(result))

    @router.get("/commands/{command_id}")
    async def receipt(command_id: str, request: Request) -> JSONResponse:
        await session(request)
        return await respond(
            request,
            dto.CommandReceipt,
            await call(service.receipt, security.instance_id, command_id),
        )

    @router.get("/approvals/{approval_id}")
    async def approval_view(
        approval_id: str, request: Request, include_summary: bool = False
    ) -> JSONResponse:
        current = await session(request)
        view = await call(service.get_approval, approval_id)
        ttl = 300.0
        if view.get("expires_at"):
            expiry = datetime.fromisoformat(view["expires_at"].replace("Z", "+00:00"))
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            ttl = min(ttl, (expiry - datetime.now(timezone.utc)).total_seconds())
        if view.get("status") != "pending" or ttl <= 0:
            raise ProtocolError("approval_expired", 409)
        nonce = security.approval_nonce(
            current, approval_id, view["revision"], view["action_digest"], ttl=ttl
        )
        public = {
            k: view[k]
            for k in (
                "id",
                "status",
                "revision",
                "expires_at",
                "summary",
                "policy_revision",
            )
            if k in view
        }
        if not include_summary:
            public.pop("summary", None)
        public["policy_revision"] = security.policy_revision
        return await respond(request, dto.ApprovalView, {**public, "nonce": nonce})

    @router.post("/approvals/{approval_id}/commands")
    async def approval_command(approval_id: str, request: Request) -> JSONResponse:
        return await command(approval_id, request, approval=True)

    @router.post("/conversations/{conversation_id}/subscriptions")
    async def subscribe(conversation_id: str, request: Request) -> JSONResponse:
        current = await session(request, lane="observation")
        snap = await call(service.snapshot, conversation_id)
        sub = security.subscribe(current, conversation_id, snap["server_epoch"])
        # The exact snapshot cut is signed. A later event remains in the suffix.
        cursor = security.cursor(sub, snap["cursor"])
        return await respond(
            request,
            dto.SubscriptionView,
            {
                "subscription_id": sub.id,
                "snapshot": {**snap, "cursor": cursor},
                "cursor": cursor,
            },
        )

    async def replay(sub: Any, cursor: str) -> dict:
        revision = security.decode_cursor(sub, cursor)
        result = await call(service.events_since, sub.conversation_id, revision)
        if result["server_epoch"] != sub.epoch or result["snapshot_required"]:
            snap = await call(service.snapshot, sub.conversation_id)
            sub.epoch = snap["server_epoch"]
            sub.delivered = sub.acknowledged = 0
            cut = security.cursor(sub, snap["cursor"])
            return {
                "snapshot_required": True,
                "snapshot": {**snap, "cursor": cut},
                "events": [],
                "cursor": cut,
            }
        events = []
        encoded_bytes = 0
        delivered_revision = revision
        for item in result["events"]:
            event = Event.model_validate(
                {"topic": f"conversation.{sub.conversation_id}", **item}
            ).model_dump(mode="json")
            encoded = json.dumps(
                event, ensure_ascii=False, separators=(",", ":")
            ).encode()
            if len(encoded) > EVENT_LIMIT:
                raise ProtocolError("payload_too_large", 413)
            record = {
                "cursor": security.cursor(sub, event["projection_revision"]),
                "event": event,
            }
            size = len(
                json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode()
            )
            if events and encoded_bytes + size > JSON_LIMIT - 4096:
                break
            events.append(record)
            encoded_bytes += size
            delivered_revision = event["projection_revision"]
        # Remaining retained events stay after this exact delivered cut. Polling
        # and SSE share this bound and never skip a suffix when paginating.
        return {
            "snapshot_required": False,
            "events": events,
            "cursor": security.cursor(sub, delivered_revision),
        }

    @router.get("/events/poll")
    async def poll(request: Request, subscription_id: str, cursor: str) -> JSONResponse:
        current = await session(request, lane="observation")
        sub = security.subscription(current, subscription_id)
        if sub.streaming:
            raise ProtocolError("subscription_in_use", 409)
        result = await replay(sub, cursor)
        await _context(request)
        return await respond(request, dto.EventPage, result)

    @router.put("/subscriptions/{subscription_id}/ack")
    async def acknowledge(subscription_id: str, request: Request) -> JSONResponse:
        current = await session(request, lane="acknowledgement")
        body = await _body(request, Acknowledgement, 4096)
        sub = security.subscription(current, subscription_id)
        security.acknowledge(sub, body.cursor)
        return await respond(request, dto.Acknowledged, {"acknowledged": True})

    @router.delete("/subscriptions/{subscription_id}")
    async def unsubscribe(subscription_id: str, request: Request) -> JSONResponse:
        current = await session(request, lane="observation")
        sub = security.subscription(current, subscription_id)
        security.unsubscribe(sub)
        return await respond(request, dto.Unsubscribed, {"unsubscribed": True})

    @router.get("/events")
    async def events(
        request: Request, subscription_id: str, cursor: str
    ) -> StreamingResponse:
        current = await session(request, lane="observation")
        sub = security.subscription(current, subscription_id)
        security.decode_cursor(sub, cursor)
        security.enter_stream(sub)

        async def stream() -> Any:
            position = cursor
            heartbeat = security.clock()
            try:
                # Flush the accepted stream even when replay is empty; an idle
                # connection need not wait for the periodic heartbeat.
                yield ": connected\n\n"
                while sub.streaming and not await request.is_disconnected():
                    await _context(request)
                    security.session(await _context(request), current.id, current.csrf)
                    result = await replay(sub, position)
                    if result["snapshot_required"]:
                        await _context(request)
                        # A snapshot may exceed one event's limit. Transfer the new
                        # atomic snapshot/cut through subscription JSON, never an
                        # oversized SSE event or a silently advanced client cursor.
                        yield (
                            "event: snapshot_required\ndata: "
                            + dto.StreamReset().model_dump_json()
                            + "\n\n"
                        )
                        return
                    else:
                        for item in result["events"]:
                            await _context(request)
                            yield (
                                f"id: {item['cursor']}\nevent: domain\ndata: "
                                + json.dumps(item["event"], separators=(",", ":"))
                                + "\n\n"
                            )
                    position = result["cursor"]
                    if security.clock() - heartbeat >= 15:
                        yield ": heartbeat\n\n"
                        heartbeat = security.clock()
                    await asyncio.sleep(0.25)
            except ProtocolError:
                return
            finally:
                security.leave_stream(sub)

        return StreamingResponse(
            stream(), media_type="text/event-stream", headers=HEADERS
        )

    @router.get("/choices")
    async def model_choices(request: Request) -> JSONResponse:
        await session(request)
        return await respond(request, dto.Choices, await call(choices))

    @router.get("/resources/{reference}")
    async def resource(reference: str, request: Request) -> JSONResponse:
        await session(request)
        from row_bot.conversation_resources import list_bindings, describe

        try:
            conversation_id, binding_id = reference.rsplit(":", 1)
        except ValueError:
            raise ProtocolError("not_found", 404) from None
        snapshot = await call(list_bindings, conversation_id)
        binding = next(
            (item for item in snapshot.bindings if item.binding_id == binding_id), None
        )
        if binding is None:
            raise ProtocolError("not_found", 404)
        descriptor = await call(describe, binding)
        return await respond(
            request,
            dto.ResourceView,
            {
                "resource_ref": reference,
                "conversation_revision": snapshot.revision,
                **asdict(descriptor),
            },
        )

    @router.post("/uploads")
    async def upload(request: Request, conversation_id: str, name: str) -> JSONResponse:
        current = await session(request, lane="mutation")
        from row_bot.application.attachments import UPLOAD_CHUNK_BYTES
        import hashlib

        try:
            key = str(UUID(request.headers.get("idempotency-key", "")))
            command_id = str(UUID(request.headers.get("x-command-id", "")))
        except ValueError:
            raise ProtocolError("invalid_command", 422) from None
        await call(service.get_conversation, conversation_id)
        # The convenience atomic upload is one chunk. Larger files use the
        # resumable session surface, with the same bounded transfer admission.
        uploads.enter_transfer(current.id)
        try:
            data = bytearray()
            async for chunk in request.stream():
                if len(data) + len(chunk) > UPLOAD_CHUNK_BYTES:
                    raise ProtocolError("payload_too_large", 413)
                await _context(request)
                data.extend(chunk)
        finally:
            uploads.leave_chunk(current.id)
        digest = hashlib.sha256(data).hexdigest()
        expected = request.headers.get("x-content-sha256")
        if expected is not None and expected != digest:
            raise ProtocolError("invalid_command", 422)
        await _context(request)
        result = await call(
            service.register_attachment,
            owner_id=security.instance_id,
            idempotency_key=key,
            command_id=command_id,
            client_session_id=current.id,
            conversation_id=conversation_id,
            name=name,
            data=bytes(data),
            mime_type=request.headers.get("content-type", "application/octet-stream"),
            validate=dispatch_validation(request, current),
        )
        return await respond(request, dto.AttachmentView, result)

    @router.post("/uploads/sessions")
    async def begin_upload(request: Request) -> JSONResponse:
        current = await session(request, lane="mutation")
        body = await _body(request, dto.UploadRequest, 4096)
        return await respond(
            request,
            dto.UploadView,
            await call(uploads.create, current.id, **body.model_dump(mode="json")),
        )

    @router.get("/uploads/{upload_id}")
    async def upload_status(upload_id: str, request: Request) -> JSONResponse:
        current = await session(request)
        return await respond(
            request, dto.UploadView, await call(uploads.status, current.id, upload_id)
        )

    @router.put("/uploads/{upload_id}/chunks")
    async def upload_chunk(
        upload_id: str, request: Request, offset: int
    ) -> JSONResponse:
        # Chunk flow has its own four-request admission bound; token rate limits
        # would otherwise prevent completion of a legitimate 25-chunk file.
        current = security.session(
            await _context(request),
            request.headers.get("x-client-session", ""),
            request.headers.get("x-csrf-token", ""),
        )
        from row_bot.application.attachments import UPLOAD_CHUNK_BYTES

        await call(uploads.enter_chunk, current.id, upload_id)
        try:
            data = bytearray()
            async for chunk in request.stream():
                if len(data) + len(chunk) > UPLOAD_CHUNK_BYTES:
                    raise ProtocolError("payload_too_large", 413)
                security.session(await _context(request), current.id, current.csrf)
                data.extend(chunk)
            return await respond(
                request,
                dto.UploadView,
                await call(uploads.write, current.id, upload_id, offset, bytes(data)),
            )
        finally:
            uploads.leave_chunk(current.id)

    @router.post("/uploads/{upload_id}/complete")
    async def complete_upload(upload_id: str, request: Request) -> JSONResponse:
        current = await session(request, lane="mutation")
        body = await _body(request, dto.UploadCompletion, 4096)
        try:
            key = str(UUID(request.headers.get("idempotency-key", "")))
        except ValueError:
            raise ProtocolError("invalid_command", 422) from None
        validate = dispatch_validation(request, current)

        def commit(**kwargs: Any) -> dict:
            return service.register_attachment(
                owner_id=security.instance_id,
                idempotency_key=key,
                command_id=str(body.command_id),
                client_session_id=current.id,
                validate=validate,
                **kwargs,
            )

        result = await call(uploads.complete, current.id, upload_id, commit)
        return await respond(request, dto.AttachmentView, result)

    @router.delete("/uploads/{upload_id}")
    async def cancel_upload(upload_id: str, request: Request) -> JSONResponse:
        current = await session(request, lane="control")
        await call(uploads.cancel, current.id, upload_id)
        return await respond(request, dto.UploadCancelled, {"cancelled": True})

    @router.get("/attachments/{reference}")
    async def attachment(reference: str, request: Request) -> Response:
        current = await session(request)
        from row_bot.application.attachments import read_attachment
        from urllib.parse import quote

        metadata, data = await call(read_attachment, reference)
        await _context(request)

        async def chunks() -> Any:
            try:
                for offset in range(0, len(data), EVENT_LIMIT):
                    security.session(await _context(request), current.id, current.csrf)
                    yield data[offset : offset + EVENT_LIMIT]
            except ProtocolError:
                return

        return StreamingResponse(
            chunks(),
            media_type=metadata["mime_type"],
            headers={
                **HEADERS,
                "Content-Disposition": "attachment; filename*=UTF-8''"
                + quote(metadata["name"], safe=""),
            },
        )

    return router


def install_client_platform(
    app: FastAPI,
    service: Any,
    *,
    instance_id: str,
    security: ClientSecurity | None = None,
    choices: Callable[[], dict] = cached_choices,
    folder_selections: Any = None,
) -> ClientSecurity:
    security = security or ClientSecurity(instance_id, policy=current_policy_snapshot)
    app.include_router(
        create_router(
            service, security, choices=choices, folder_selections=folder_selections
        )
    )

    return security


def create_client_platform_app(
    service: Any,
    *,
    access_config: Any = None,
    session_authenticator: Any = None,
    instance_id: str | None = None,
    security: ClientSecurity | None = None,
    choices: Callable[[], dict] = cached_choices,
    folder_selections: Any = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> Any:
        lifecycle = None
        if hasattr(service, "registry"):
            from row_bot.application.lifecycle import ApplicationLifecycle

            lifecycle = ApplicationLifecycle(
                registry=service.registry,
                close_voice=getattr(service, "close_voice", lambda: True),
                close_settings=getattr(service, "close_settings", lambda: True),
            )
            await lifecycle.startup()
        try:
            yield
        finally:
            if lifecycle is not None:
                await lifecycle.shutdown()

    app = FastAPI(
        title="Row-Bot client protocol", version=PROTOCOL_VERSION, lifespan=lifespan
    )
    install_client_platform(
        app,
        service,
        instance_id=instance_id or service.instance_id,
        security=security,
        choices=choices,
        folder_selections=folder_selections,
    )
    app.add_middleware(
        AccessMiddleware,
        config=access_config,
        session_authenticator=session_authenticator,
    )
    return app
