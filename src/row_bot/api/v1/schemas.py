"""Canonical wire DTOs. Generated clients derive from these closed commands."""
from __future__ import annotations

from typing import Annotated, Any, Literal, Union
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter, create_model, model_validator

OpaqueId = Annotated[str, StringConstraints(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9:_-]+$")]
Reference = Annotated[str, StringConstraints(min_length=1, max_length=256, pattern=r"^[A-Za-z0-9:_-]+$")]
Revision = Annotated[str, StringConstraints(pattern=r"^(0|[1-9][0-9]{0,19})$")]
PROTOCOL_VERSION = "1.0"
JSON_LIMIT = 256 * 1024
EVENT_LIMIT = 64 * 1024


class WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ModelSelection(WireModel):
    provider_id: OpaqueId
    model_ref: Annotated[str, StringConstraints(min_length=1, max_length=256)]


class WriteTarget(WireModel):
    binding_id: OpaqueId
    resource_id: OpaqueId
    kind: Literal["artifact", "workspace"]
    binding_revision: Revision
    resource_revision: str = Field(min_length=1, max_length=128)


class ReasoningSelectionValue(WireModel):
    kind: Literal["provider_default", "effort", "on", "off", "budget"]
    effort: str | None = Field(default=None, min_length=1, max_length=80)
    budget: int | None = Field(default=None, ge=1, le=2147483647)

    @model_validator(mode="after")
    def selection_fields(self) -> ReasoningSelectionValue:
        if (self.kind == "effort") != (self.effort is not None):
            raise ValueError("Only an effort selection requires an effort value.")
        if (self.kind == "budget") != (self.budget is not None):
            raise ValueError("Only a budget selection requires a budget value.")
        return self


class ReasoningControl(WireModel):
    model_ref: str = Field(min_length=1, max_length=256)
    capability_revision: str = Field(min_length=1, max_length=128)
    selection: ReasoningSelectionValue


class ReasoningChoice(WireModel):
    selection: ReasoningSelectionValue
    label: str = Field(max_length=160)


class ReasoningView(WireModel):
    model_ref: str = Field(max_length=256)
    capability_revision: str = Field(max_length=128)
    available: bool
    selection: ReasoningSelectionValue
    choices: list[ReasoningChoice] = Field(max_length=32)
    supports_budget: bool
    budget_min: int = Field(ge=0)
    budget_max: int = Field(ge=0)
    stale: bool


class ConversationControls(WireModel):
    model_selection: ModelSelection | None = None
    runtime_mode: Literal["agent", "chat_only"] = "agent"
    profile_id: str = Field(default="", max_length=128)
    approval_mode: Literal["block", "approve", "allow_all"] = "approve"
    reasoning: ReasoningControl | None = None


class SubmitPayload(WireModel):
    submission_id: UUID
    text: Annotated[str, StringConstraints(min_length=1, max_length=200000)]
    attachment_refs: list[Reference] = Field(default_factory=list, max_length=32)
    model_selection: ModelSelection
    write_targets: list[WriteTarget] | None = Field(default=None, max_length=2)


class RenamePayload(WireModel):
    title: Annotated[str, StringConstraints(min_length=1, max_length=256)]


class CreatePayload(WireModel):
    title: Annotated[str, StringConstraints(min_length=1, max_length=256)] = "New conversation"


class PinPayload(WireModel):
    pinned: bool


class EmptyPayload(WireModel):
    pass


class SteerPayload(WireModel):
    steering_id: UUID
    text: Annotated[str, StringConstraints(min_length=1, max_length=16000)]


class QueueItemCommand(WireModel):
    submission_id: UUID
    expected_queue_revision: Revision


class QueueEditPayload(QueueItemCommand):
    text: str = Field(min_length=1, max_length=16000)


class ResumePayload(WireModel):
    model_selection: ModelSelection


class BindPayload(WireModel):
    kind: Literal["workspace", "artifact"]
    resource_id: OpaqueId
    role: Literal["context", "primary", "reference", "output"] = "context"
    expected_resource_revision: str | None = Field(default=None, max_length=128)


class UnbindPayload(WireModel):
    binding_id: OpaqueId


class ApprovalPayload(WireModel):
    decision: Literal["approve", "reject"]
    nonce: Annotated[str, StringConstraints(min_length=32, max_length=256)]


class DeckSetupPayload(WireModel):
    template_id: OpaqueId = "blank_deck"
    aspect_ratio: str = Field(default="16:9", max_length=32)
    name: str = Field(default="", max_length=120)
    brief: str = Field(default="", max_length=16000)


class ResourceSetupPayload(WireModel):
    kind: Literal["artifact", "workspace"]
    intent: Literal["create", "open", "add", "repair", "new_conversation"]
    resource_id: OpaqueId | None = None
    expected_resource_revision: str | None = Field(default=None, max_length=128)
    expected_origin_id: OpaqueId | None = None
    deck: DeckSetupPayload | None = None
    folder_grant: OpaqueId | None = None


class SetupContinuePayload(WireModel):
    setup_command_id: UUID
    expected_resource_revision: str = Field(min_length=1, max_length=128)
    expected_origin_id: OpaqueId | None = None


class Command(WireModel):
    command_id: UUID
    client_session_id: UUID
    type: Literal["conversation.create", "conversation.rename", "conversation.pin",
                  "conversation.delete", "conversation.submit", "conversation.stop",
                  "conversation.steer", "conversation.resume", "conversation.bind",
                  "conversation.unbind", "approval.resolve", "conversation.controls",
                  "resource.setup", "resource.continue", "conversation.queue.edit",
                  "conversation.queue.remove", "conversation.queue.dispatch"]
    expected_revision: Revision
    payload: dict

    @model_validator(mode="after")
    def typed_payload(self) -> Command:
        # JSON validation permits UUID wire strings while Python callers remain strict.
        import json
        payload_type = COMMAND_PAYLOADS[self.type]
        supplied = self.payload
        self.payload = payload_type.model_validate_json(json.dumps(supplied)).model_dump(mode="json")
        if self.type == "conversation.submit" and "write_targets" not in supplied:
            self.payload.pop("write_targets", None)  # Preserve existing durable v1 command verifiers.
        if self.type == "conversation.controls" and "reasoning" not in supplied:
            self.payload.pop("reasoning", None)
        return self

    @classmethod
    def model_json_schema(cls, **kwargs: Any) -> dict:
        return _variant_schema(cls, COMMAND_PAYLOADS, **kwargs)


COMMAND_PAYLOADS = {
    "conversation.create": CreatePayload, "conversation.rename": RenamePayload,
    "conversation.pin": PinPayload, "conversation.delete": EmptyPayload,
    "conversation.submit": SubmitPayload, "conversation.stop": EmptyPayload,
    "conversation.steer": SteerPayload, "conversation.resume": ResumePayload,
    "conversation.bind": BindPayload, "conversation.unbind": UnbindPayload,
    "approval.resolve": ApprovalPayload,
    "conversation.controls": ConversationControls,
    "resource.setup": ResourceSetupPayload, "resource.continue": SetupContinuePayload,
    "conversation.queue.edit": QueueEditPayload,
    "conversation.queue.remove": QueueItemCommand,
    "conversation.queue.dispatch": QueueItemCommand,
}


class Handshake(WireModel):
    protocol_major: int = Field(default=1, ge=0, le=65535)
    minimum_minor: int = Field(default=0, ge=0, le=65535)
    maximum_minor: int = Field(default=0, ge=0, le=65535)
    client_build: Annotated[str, StringConstraints(max_length=128)] = "unknown"
    client_session_id: UUID | None = None
    client_group_id: UUID | None = None
    presentation_features: list[OpaqueId] = Field(default_factory=list, max_length=32)


class Problem(WireModel):
    type: str
    title: str
    status: int
    code: str
    request_id: UUID
    retryable: bool = False
    current_revision: str | None = Field(default=None, min_length=1, max_length=128)
    recovery: Literal["reload_then_review", "authenticate", "retry", "update_client", "none"] = "none"


class ResourceBinding(WireModel):
    binding_id: OpaqueId
    kind: Literal["workspace", "artifact", "browser_session", "task", "document"]
    resource_id: OpaqueId
    role: Literal["context", "primary", "reference", "output"]
    revision: Revision


class Outcome(WireModel):
    mutation_status: Literal["accepted", "committed", "rejected", "uncertain"]
    projection_status: Literal["pending", "finalizing", "ready", "degraded"]
    external_outcome: Literal["not_applicable", "known_not_sent", "sent", "uncertain"] = "not_applicable"


class GenerationState(WireModel):
    execution_id: OpaqueId
    conversation_id: OpaqueId
    generation_id: OpaqueId
    pass_id: OpaqueId
    segment_id: OpaqueId | None = None
    status: Literal["running", "stopping", "stopped", "waiting_approval", "completed", "interrupted"]
    revision: Revision
    cancel_requested: bool
    quiesced: bool
    cleanup_complete: bool
    external_outcome: Literal["known_not_sent", "sent", "uncertain", "not_applicable"]
    approval_id: OpaqueId | None
    can_stop: bool


class TranscriptDelta(WireModel):
    pass_id: OpaqueId
    segment_id: OpaqueId
    row_id: OpaqueId
    render_revision: Revision
    public_text_delta: Annotated[str, StringConstraints(max_length=60000)]


class ToolActivity(WireModel):
    state: Literal["tool_call", "tool_done"]
    tool_name: str = Field(default="", max_length=128)
    tool_call_id: str = Field(default="", max_length=256)
    message_id: str = Field(default="", max_length=256)
    pass_id: OpaqueId | None = None
    segment_id: OpaqueId | None = None


class GenerationActivity(WireModel):
    state: Literal["thinking"]


class ApprovalRequired(WireModel):
    status: Literal["waiting_approval"]
    approval_id: OpaqueId | None = None


class GenerationError(WireModel):
    code: Literal["generation_failed"]


class TranscriptCheckpoint(WireModel):
    checkpoint_revision: Annotated[str, StringConstraints(max_length=128)]


class TranscriptSettled(WireModel):
    row_id: OpaqueId
    adoption: Literal["exact", "no_adoption"]


class ResourceChanged(WireModel):
    revision: Revision


class ProjectionReset(WireModel):
    reason: Literal["content_evicted"]


class AgentActivity(WireModel):
    run_id: OpaqueId
    status: Literal["queued", "running", "waiting_approval", "waiting_user", "paused", "interrupted",
                    "completed", "completed_delivery_failed", "failed", "stopped", "stopping",
                    "blocked", "timed_out", "cancelled"]
    revision: Revision


class QueueUpdated(WireModel):
    submission_ids: list[OpaqueId] = Field(max_length=256)
    revision: Revision


class SteeringActivity(WireModel):
    generation_id: OpaqueId
    steering_ids: list[OpaqueId] = Field(max_length=256)


class ParentSteeringItem(WireModel):
    id: OpaqueId
    event_id: OpaqueId
    text: str = Field(max_length=16000)
    state: Literal["queued", "consumed"]
    editable: Literal[False] = False


class ParentSteeringView(WireModel):
    conversation_id: OpaqueId
    generation_id: str = Field(max_length=128)
    orchestration_id: str = Field(max_length=128)
    items: list[ParentSteeringItem] = Field(max_length=256)
    next_cursor: str | None = None
    has_more: bool = False


class ClientQueueItem(WireModel):
    id: OpaqueId
    submission_id: OpaqueId
    generation_id: OpaqueId
    text: str = Field(max_length=16000)
    revision: Revision
    state: Literal["queued", "dispatching", "consumed", "cancelled", "paused"]
    editable: bool
    removable: bool


class ClientQueueView(WireModel):
    conversation_id: OpaqueId
    generation_id: str = Field(max_length=128)
    items: list[ClientQueueItem] = Field(max_length=256)
    next_cursor: str | None = None
    has_more: bool = False


class QueueChanged(WireModel):
    generation_id: OpaqueId
    submission_ids: list[OpaqueId] = Field(max_length=256)


class MediaAvailable(WireModel):
    media_ref: Reference
    mime_type: Literal["image/png", "image/jpeg", "video/mp4", "application/pdf", "application/octet-stream"]
    tool_call_id: str = Field(default="", max_length=256)
    message_id: str = Field(default="", max_length=256)


class MediaError(WireModel):
    code: Literal["payload_too_large", "media_unavailable"]
    tool_call_id: str = Field(default="", max_length=256)
    message_id: str = Field(default="", max_length=256)


EVENT_PAYLOADS = {"generation.state": GenerationState, "transcript.delta": TranscriptDelta,
                  "tool.activity": ToolActivity, "generation.activity": GenerationActivity,
                  "approval.required": ApprovalRequired, "generation.error": GenerationError,
                  "transcript.checkpoint": TranscriptCheckpoint, "transcript.settled": TranscriptSettled,
                  "resource.changed": ResourceChanged, "agent.activity": AgentActivity,
                  "queue.updated": QueueUpdated, "media.available": MediaAvailable}
EVENT_PAYLOADS["projection.reset"] = ProjectionReset
EVENT_PAYLOADS["media.error"] = MediaError
EVENT_PAYLOADS["steering.queued"] = SteeringActivity
EVENT_PAYLOADS["steering.consumed"] = SteeringActivity
EVENT_PAYLOADS["queue.changed"] = QueueChanged


class Event(WireModel):
    protocol_version: Literal["1.0"] = "1.0"
    event_id: OpaqueId
    topic: Annotated[str, StringConstraints(min_length=1, max_length=160)]
    server_epoch: OpaqueId
    conversation_id: OpaqueId
    projection_revision: Revision
    source: Literal["runtime", "checkpoint", "approval", "resource"] = "runtime"
    source_stream_id: OpaqueId
    source_epoch: OpaqueId
    source_sequence_start: Revision
    source_sequence_end: Revision
    type: Literal["generation.state", "transcript.delta", "tool.activity", "generation.activity",
                  "approval.required", "generation.error", "transcript.checkpoint", "transcript.settled", "resource.changed",
                  "agent.activity", "queue.updated", "media.available", "projection.reset", "media.error",
                  "steering.queued", "steering.consumed", "queue.changed"]
    payload: dict

    @model_validator(mode="after")
    def typed_payload(self) -> Event:
        if int(self.source_sequence_start) > int(self.source_sequence_end):
            raise ValueError("Invalid source sequence range")
        self.payload = EVENT_PAYLOADS[self.type].model_validate(self.payload).model_dump(mode="json", exclude_unset=True)
        return self

    @classmethod
    def model_json_schema(cls, **kwargs: Any) -> dict:
        return _variant_schema(cls, EVENT_PAYLOADS, **kwargs)


def _variant_schema(base: type[BaseModel], payloads: dict, **kwargs) -> dict:
    """Expand the same executable payload registry to a closed tagged wire union."""
    return TypeAdapter(_variant_type(base, payloads)).json_schema(**kwargs)


def _variant_type(base: type[BaseModel], payloads: dict) -> Any:
    variants = []
    for tag, payload in payloads.items():
        name = "".join(word.title() for word in tag.replace(".", "_").split("_")) + base.__name__
        fields = {key: (field.rebuild_annotation(), field.default if not field.is_required() else ...)
                  for key, field in base.model_fields.items() if key not in {"type", "payload"}}
        fields["type"] = (Literal[tag], ...)
        fields["payload"] = (payload, ...)
        variants.append(create_model(name, __base__=WireModel, **fields))
    return Annotated[Union[tuple(variants)], Field(discriminator="type")]


class Acknowledgement(WireModel):
    cursor: Annotated[str, StringConstraints(min_length=1, max_length=2048)]


class PreviewContract(WireModel):
    """Phase 3 boundary only; this does not enable generated preview execution."""
    artifact_ref: OpaqueId
    revision: Revision
    sandbox: Literal["allow-scripts"] = "allow-scripts"
    owner_origin_access: Literal[False] = False
    message_limit_bytes: Literal[16384] = 16384
    allowed_messages: tuple[Literal["selection.proposed", "edit.proposed"], ...] = ("selection.proposed", "edit.proposed")


class CommandReceipt(WireModel):
    command_id: UUID
    status: Literal["accepted", "completed", "cancel_requested", "DeleteCompleted", "DeleteBlocked",
                    "DeleteNotFound", "AlreadyDeleting", "DeleteRejected", "admitting", "rejected", "partial"]
    conversation_id: OpaqueId | None = None
    generation_id: OpaqueId | None = None
    execution_id: OpaqueId | None = None
    pass_id: OpaqueId | None = None
    submission_id: OpaqueId | None = None
    admission_sequence: Revision | None = None
    revision: Revision | None = None
    approval_id: OpaqueId | None = None
    attachment_ref: Reference | None = None
    binding_id: OpaqueId | None = None
    code: str | None = Field(default=None, max_length=80)
    current_revision: str | None = Field(default=None, min_length=1, max_length=128)
    resource_id: OpaqueId | None = None
    resource_kind: Literal["artifact", "workspace"] | None = None
    resource_revision: str | None = Field(default=None, max_length=128)
    confirmed_stages: list[Literal["created", "conversation", "associated", "bound"]] = Field(default_factory=list, max_length=4)
    setup_command_id: UUID | None = None
    setup_intent: Literal["create", "open", "add", "repair", "new_conversation"] | None = None
    association_required: bool = False


class AttachmentView(WireModel):
    attachment_ref: Reference
    name: Annotated[str, StringConstraints(min_length=1, max_length=240)]
    mime_type: Literal["image/png", "image/jpeg", "video/mp4", "application/pdf", "application/octet-stream"]
    size_bytes: int = Field(ge=1, le=26214400)
    revision: Revision


class SessionProof(WireModel):
    client_session_id: UUID
    csrf_token: Annotated[str, StringConstraints(min_length=32, max_length=256)]


Cursor = Annotated[str, StringConstraints(max_length=2048)]


class ConversationView(WireModel):
    id: OpaqueId
    revision: Revision
    title: str = Field(max_length=256)
    pinned: bool
    generation_state: list[GenerationState] = Field(default_factory=list, max_length=32)
    resource_bindings: list[ResourceBinding] = Field(default_factory=list, max_length=200)


class ConversationPage(WireModel):
    items: list[ConversationView] = Field(max_length=200)
    has_more: bool
    next_cursor: Cursor | None = None


class TextBlock(WireModel):
    type: Literal["text"]
    text: str = Field(max_length=2097152)


class TranscriptRow(WireModel):
    id: str = Field(min_length=1, max_length=1024)
    message_id: str | None = Field(default=None, max_length=256)
    role: Literal["user", "assistant", "tool"]
    blocks: list[TextBlock] = Field(max_length=256)
    tool_call_ids: list[str] = Field(default_factory=list, max_length=256)
    tool_call_id: str = Field(default="", max_length=256)
    tool_calls_ref: str | None = Field(default=None, max_length=256)
    render_revision: Revision | None = None
    content_status: Literal["inline", "lazy", "oversized"] | None = None
    content_ref: str | None = Field(default=None, max_length=256)


class Snapshot(WireModel):
    conversation_id: OpaqueId
    server_epoch: OpaqueId
    projection_revision: Revision
    cursor: Cursor
    checkpoint_revision: str = Field(max_length=128)
    rows: list[TranscriptRow] = Field(max_length=200)
    generation: GenerationState | None


class TranscriptPage(Snapshot):
    has_more: bool
    previous_cursor: Cursor | None
    next_cursor: Cursor | None


class SearchHit(WireModel):
    conversation_id: OpaqueId
    title: str = Field(max_length=256)
    message_id: str | None = Field(default=None, max_length=256)
    row_id: str | None = Field(default=None, max_length=1024)
    excerpt: str = Field(max_length=400)
    checkpoint_revision: str = Field(max_length=128)


class SearchPage(WireModel):
    items: list[SearchHit] = Field(max_length=50)
    has_more: bool
    next_cursor: Cursor | None = None
    scanned_messages: int = Field(ge=0, le=500)
    revision: str = Field(max_length=128)


class SubscriptionView(WireModel):
    subscription_id: UUID
    snapshot: Snapshot
    cursor: Cursor


EventUnion = _variant_type(Event, EVENT_PAYLOADS)


class EventRecord(WireModel):
    cursor: Cursor
    event: EventUnion

    @model_validator(mode="after")
    def valid_sequence(self) -> EventRecord:
        if int(self.event.source_sequence_start) > int(self.event.source_sequence_end):
            raise ValueError("Invalid source sequence range")
        return self


class EventPage(WireModel):
    snapshot_required: bool
    snapshot: Snapshot | None = None
    events: list[EventRecord] = Field(max_length=4096)
    cursor: Cursor


class ModelChoice(WireModel):
    provider_id: OpaqueId
    model_ref: str = Field(min_length=1, max_length=256)
    label: str = Field(max_length=256)
    available: bool
    unavailable_reason: Literal["configuration_required", "unavailable"] | None = None


class CapabilityChoice(WireModel):
    id: OpaqueId
    available: bool
    requires_approval: bool
    unavailable_reason: Literal["configuration_required", "unavailable"] | None = None


class Choices(WireModel):
    models: list[ModelChoice] = Field(max_length=4096)
    capabilities: list[CapabilityChoice] = Field(max_length=4096)
    catalog_stale: bool = True


class NativeAdapter(WireModel):
    available: Literal[False] = False


class Limits(WireModel):
    # Ordinary inbound JSON bodies; individual response DTOs have their own
    # row/content bounds (including the combined conversation-open envelope).
    json_bytes: Literal[262144] = 262144
    event_bytes: Literal[65536] = 65536
    query_rows: Literal[200] = 200
    transcript_rows: Literal[100] = 100
    stream_connections_per_session: Literal[4] = 4
    attachment_bytes: Literal[26214400] = 26214400
    upload_chunk_bytes: Literal[1048576] = 1048576
    upload_batch_bytes: Literal[104857600] = 104857600
    upload_chunks_in_flight: Literal[4] = 4
    upload_ttl_seconds: Literal[1800] = 1800


class HandshakeView(Choices):
    protocol_version: Literal["1.0"]
    minimum_client_version: Literal["1.0"]
    instance_id: OpaqueId
    server_epoch: OpaqueId
    client_session_id: UUID
    client_group_id: UUID
    csrf_token: str = Field(min_length=32, max_length=256)
    authentication_kind: Literal["local_owner", "session"]
    policy_revision: Revision
    session_ttl_seconds: int = Field(ge=0, le=43200)
    native_adapter: NativeAdapter
    limits: Limits


class ApprovalView(WireModel):
    id: OpaqueId
    status: Literal["pending"]
    revision: Revision
    expires_at: str | None = Field(default=None, max_length=80)
    summary: str | None = Field(default=None, max_length=4096)
    policy_revision: Revision
    nonce: str = Field(min_length=32, max_length=256)


class ResourceView(WireModel):
    resource_ref: Reference
    conversation_revision: Revision
    binding: ResourceBinding
    title: str = Field(max_length=256)
    resource_revision: str = Field(max_length=128)
    available: bool


class ActionReadiness(WireModel):
    action: Literal["send", "generate", "create_deck", "bind", "preview", "register_folder"]
    ready: bool
    code: str | None = Field(default=None, max_length=80)


class ProfileChoice(WireModel):
    id: OpaqueId
    label: str = Field(max_length=256)


class ContextUsageView(WireModel):
    conversation_id: OpaqueId
    state: Literal["unknown", "saved", "stale"]
    estimated_input_tokens: int | None = Field(default=None, ge=0, le=2147483647)
    usable_input_tokens: int | None = Field(default=None, ge=0, le=2147483647)
    native_window_tokens: int | None = Field(default=None, ge=0, le=2147483647)
    last_confirmed_input_tokens: int | None = Field(default=None, ge=0, le=2147483647)
    model_ref: str | None = Field(default=None, max_length=256)


class ConversationWorkspace(WireModel):
    conversation_id: OpaqueId
    revision: Revision
    controls: ConversationControls
    profiles: list[ProfileChoice] = Field(max_length=256)
    resources: list[ResourceView] = Field(max_length=200)
    actions: list[ActionReadiness] = Field(max_length=6)
    context_usage: ContextUsageView | None = None
    reasoning: ReasoningView | None = None


class DelegatedRun(WireModel):
    run_id: OpaqueId
    parent_conversation_id: OpaqueId
    child_conversation_id: OpaqueId | None = None
    name: str = Field(max_length=256)
    status: str = Field(max_length=80)
    summary: str = Field(max_length=4096)


class DelegatedActivityView(WireModel):
    conversation_id: OpaqueId
    parent_conversation_id: OpaqueId | None = None
    items: list[DelegatedRun] = Field(max_length=50)
    next_cursor: str | None = Field(default=None, max_length=2048)
    has_more: bool


class DraftView(WireModel):
    conversation_id: OpaqueId
    revision: str = Field(max_length=128)
    text: str = Field(max_length=200000)
    attachments: list[AttachmentView] = Field(max_length=32)


class ConversationOpenView(WireModel):
    """Combined response bounded to 2 MiB; history keeps its 100-row/256-KiB bound."""

    conversation: ConversationView
    history: TranscriptPage
    workspace: ConversationWorkspace
    draft: DraftView


class DraftSave(WireModel):
    expected_revision: str = Field(min_length=1, max_length=128)
    text: str = Field(max_length=200000)
    attachment_refs: list[Reference] = Field(max_length=32)


class ResourceChoice(WireModel):
    resource_id: OpaqueId
    kind: Literal["artifact", "workspace"]
    name: str = Field(max_length=256)
    revision: str = Field(max_length=128)
    origin_conversation_id: OpaqueId | None = None
    origin_status: Literal["available", "unassociated", "repair_required"]
    available: bool


class ResourceChoicePage(WireModel):
    items: list[ResourceChoice] = Field(max_length=100)
    next_cursor: Cursor | None = None


class FolderGrantView(WireModel):
    status: Literal["selected", "cancelled", "unavailable"]
    grant_id: OpaqueId | None = None
    name: str | None = Field(default=None, max_length=256)


class DeckTemplateChoice(WireModel):
    id: OpaqueId
    label: str = Field(max_length=256)


class DeckSetupOptions(WireModel):
    mode: Literal["deck"] = "deck"
    templates: list[DeckTemplateChoice] = Field(max_length=100)
    canvases: list[DeckTemplateChoice] = Field(max_length=20)
    default_template: OpaqueId = "blank_deck"
    default_canvas: str = "16:9"
    default_name: str = Field(max_length=256)
    default_brand: str = Field(max_length=256)


class ArtifactPage(WireModel):
    id: OpaqueId
    title: str = Field(max_length=256)
    index: int = Field(ge=0)


class ArtifactPreview(WireModel):
    resource_id: OpaqueId
    resource_revision: str = Field(max_length=128)
    preview_revision: str = Field(max_length=128)
    mode: Literal["deck"]
    page_id: OpaqueId
    page_index: int = Field(ge=0)
    page_count: int = Field(ge=1)
    page_title: str = Field(max_length=256)
    canvas_width: int = Field(ge=1, le=16384)
    canvas_height: int = Field(ge=1, le=16384)
    pages: list[ArtifactPage] = Field(max_length=200)
    html: str | None = Field(default=None, max_length=2097152)
    unchanged: bool


class WorkspacePolicy(WireModel):
    execution_mode: Literal["local", "docker"]
    approval_mode: Literal["block", "approve", "allow_all"]
    sandbox_network: Literal["off", "ask", "on"]
    read_only: Literal[True] = True


class WorkspaceDiffStats(WireModel):
    files: int = Field(ge=0)
    additions: int = Field(ge=0)
    deletions: int = Field(ge=0)


class WorkspaceCommandStatus(WireModel):
    label: str = Field(max_length=4096)
    kind: str = Field(max_length=128)
    status: Literal["not_run"]


class WorkspaceProcessStatus(WireModel):
    pid: int = Field(ge=1)
    status: Literal["running", "stopped"]


class WorkspaceTodo(WireModel):
    id: str = Field(max_length=256)
    label: str = Field(max_length=4096)
    status: str = Field(max_length=80)


class WorkspaceInspector(WireModel):
    resource_id: OpaqueId
    project_workspace_id: OpaqueId
    execution_workspace_id: OpaqueId
    conversation_id: OpaqueId
    name: str = Field(max_length=256)
    policy: WorkspacePolicy
    snapshot_revision: str = Field(max_length=128)
    status: Literal["ready", "stale", "unavailable"]
    is_git: bool
    branch: str = Field(max_length=1024)
    dirty: bool
    changed_total: int = Field(ge=0)
    diff_stats: WorkspaceDiffStats | None
    commands: list[WorkspaceCommandStatus] = Field(max_length=200)
    processes: list[WorkspaceProcessStatus] = Field(max_length=200)
    todos: list[WorkspaceTodo] = Field(max_length=200)
    error: str = Field(default="", max_length=128)


class WorkspaceChangedFile(WireModel):
    path: str = Field(max_length=4096)
    status: str = Field(max_length=80)
    additions: int = Field(ge=0)
    deletions: int = Field(ge=0)


class WorkspaceChanges(WireModel):
    items: list[WorkspaceChangedFile] = Field(max_length=100)
    next_cursor: Cursor | None
    snapshot_revision: str = Field(max_length=128)
    total: int = Field(ge=0)


class WorkspaceDirectoryEntry(WireModel):
    name: str = Field(max_length=1024)
    relative_path: str = Field(max_length=4096)
    kind: Literal["file", "directory"]
    previewable: bool


class WorkspaceDirectory(WireModel):
    items: list[WorkspaceDirectoryEntry] = Field(max_length=100)
    next_cursor: Cursor | None
    directory_revision: str = Field(max_length=256)
    excluded: list[str] = Field(max_length=10)


class WorkspaceFile(WireModel):
    status: Literal["text", "binary", "missing", "denied", "stale"]
    relative_path: str = Field(max_length=4096)
    text: str = Field(default="", max_length=65536)
    revision: str = Field(default="", max_length=256)
    next_offset: int | None = Field(default=None, ge=0)
    size_bytes: int = Field(default=0, ge=0)


class WorkspaceDiff(WireModel):
    status: Literal["text", "missing", "denied", "stale", "unavailable"]
    text: str = Field(max_length=65536)
    revision: str = Field(max_length=256)
    next_offset: int | None = Field(default=None, ge=0)
    truncated: bool = False


class WorkspaceChangeSet(WireModel):
    id: str = Field(max_length=256)
    summary: str = Field(max_length=4096)
    reviewed: bool
    reverted: bool
    file_count: int = Field(ge=0)


class WorkspaceChangeSetPage(WireModel):
    items: list[WorkspaceChangeSet] = Field(max_length=100)
    next_cursor: Cursor | None
    snapshot_revision: str = Field(max_length=128)
    total: int = Field(ge=0)


class WorkspaceChangeSetFile(WireModel):
    path: str = Field(max_length=4096)
    action: str = Field(max_length=128)


class WorkspaceChangeSetFiles(WireModel):
    items: list[WorkspaceChangeSetFile] = Field(max_length=100)
    next_cursor: Cursor | None
    snapshot_revision: str = Field(max_length=128)
    total: int = Field(ge=0)
    change_set_id: str = Field(max_length=256)


class Acknowledged(WireModel):
    acknowledged: Literal[True]


class Unsubscribed(WireModel):
    unsubscribed: Literal[True]


class UploadRequest(WireModel):
    conversation_id: OpaqueId
    name: str = Field(min_length=1, max_length=240)
    size_bytes: int = Field(ge=1, le=26214400)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    batch_id: UUID


class UploadView(WireModel):
    upload_id: UUID
    size_bytes: int = Field(ge=1, le=26214400)
    received_bytes: int = Field(ge=0, le=26214400)
    chunk_bytes: Literal[1048576] = 1048576
    expires_in_seconds: int = Field(ge=0, le=1800)


class UploadCompletion(WireModel):
    command_id: UUID


class UploadCancelled(WireModel):
    cancelled: Literal[True]


class StreamReset(WireModel):
    snapshot_required: Literal[True] = True
    recovery: Literal["resubscribe"] = "resubscribe"


class LazyContent(WireModel):
    conversation_id: OpaqueId
    content_ref: str = Field(min_length=1, max_length=256)
    checkpoint_revision: str = Field(max_length=128)
    encoding: Literal["base64"]
    media_type: Literal["application/json", "text/plain"]
    data: str = Field(max_length=87384)
    has_more: bool
    next_cursor: Cursor | None
