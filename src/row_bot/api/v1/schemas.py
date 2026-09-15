"""Canonical wire DTOs. Generated clients derive from these closed commands."""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    create_model,
    model_validator,
)

OpaqueId = Annotated[
    str, StringConstraints(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9:_-]+$")
]
Reference = Annotated[
    str, StringConstraints(min_length=1, max_length=256, pattern=r"^[A-Za-z0-9:_-]+$")
]
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
    title: Annotated[str, StringConstraints(min_length=1, max_length=256)] = (
        "New conversation"
    )


class PinPayload(WireModel):
    pinned: bool


class EmptyPayload(WireModel):
    pass


ConversationAction = Literal[
    "conversation.rename", "conversation.pin", "conversation.export"
]


class ConversationActionCapability(WireModel):
    available: bool
    code: str | None = Field(max_length=128)


class ConversationActionCapabilities(WireModel):
    rename: ConversationActionCapability
    pin: ConversationActionCapability
    archive: ConversationActionCapability
    export: ConversationActionCapability


class ConversationActionSnapshot(WireModel):
    schema_version: Literal[1]
    conversation_id: OpaqueId
    revision: Revision
    checkpoint_revision: str = Field(max_length=128)
    title: str = Field(max_length=256)
    pinned: bool
    capabilities: ConversationActionCapabilities


class ConversationActionRenameFields(WireModel):
    title: str = Field(min_length=1, max_length=120)


class ConversationActionPinFields(WireModel):
    pinned: bool


class ConversationActionExportFields(WireModel):
    pass


class ConversationActionExportReviewFields(WireModel):
    title: str = Field(max_length=256)


CONVERSATION_ACTION_REVIEW_PAYLOADS = {
    "conversation.rename": ConversationActionRenameFields,
    "conversation.pin": ConversationActionPinFields,
    "conversation.export": ConversationActionExportFields,
}


class ConversationActionReviewRequest(WireModel):
    type: ConversationAction
    expected_revision: Revision
    payload: dict

    @model_validator(mode="after")
    def typed_payload(self) -> ConversationActionReviewRequest:
        self.payload = (
            CONVERSATION_ACTION_REVIEW_PAYLOADS[self.type]
            .model_validate(self.payload)
            .model_dump(mode="json")
        )
        return self

    @classmethod
    def model_json_schema(cls, **kwargs: Any) -> dict:
        return _variant_schema(cls, CONVERSATION_ACTION_REVIEW_PAYLOADS, **kwargs)


class ConversationActionReview(WireModel):
    schema_version: Literal[1]
    conversation_id: OpaqueId
    action: ConversationAction
    revision: Revision
    checkpoint_revision: str = Field(max_length=128)
    fields: dict
    action_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    summary: str = Field(max_length=512)
    disclosures: list[str] = Field(max_length=8)
    review_id: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def typed_fields(self) -> ConversationActionReview:
        field_type = (
            ConversationActionExportReviewFields
            if self.action == "conversation.export"
            else ConversationActionRenameFields
            if self.action == "conversation.rename"
            else ConversationActionPinFields
        )
        self.fields = field_type.model_validate(self.fields).model_dump(mode="json")
        return self


class ConversationActionCommandBase(WireModel):
    checkpoint_revision: str = Field(max_length=128)
    action_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    review_id: str = Field(min_length=1, max_length=128)


class ConversationActionRenamePayload(ConversationActionCommandBase):
    title: str = Field(min_length=1, max_length=120)


class ConversationActionPinPayload(ConversationActionCommandBase):
    pinned: bool


class ConversationActionExportPayload(ConversationActionCommandBase):
    export_title: str = Field(max_length=256)


CONVERSATION_ACTION_COMMAND_PAYLOADS = {
    "conversation.rename": ConversationActionRenamePayload,
    "conversation.pin": ConversationActionPinPayload,
    "conversation.export": ConversationActionExportPayload,
}


class ConversationActionCommand(WireModel):
    command_id: UUID
    client_session_id: UUID
    type: ConversationAction
    expected_revision: Revision
    payload: dict

    @model_validator(mode="after")
    def typed_payload(self) -> ConversationActionCommand:
        self.payload = (
            CONVERSATION_ACTION_COMMAND_PAYLOADS[self.type]
            .model_validate(self.payload)
            .model_dump(mode="json")
        )
        return self

    @classmethod
    def model_json_schema(cls, **kwargs: Any) -> dict:
        return _variant_schema(cls, CONVERSATION_ACTION_COMMAND_PAYLOADS, **kwargs)


class ConversationActionConversation(WireModel):
    conversation_id: OpaqueId
    revision: Revision
    title: str = Field(max_length=256)
    pinned: bool


class ConversationActionExport(WireModel):
    attachment_ref: Reference
    file_name: Literal["conversation-export.md"]
    size_bytes: int = Field(ge=0, le=8 * 1024 * 1024)
    checkpoint_revision: str = Field(max_length=128)


class ConversationActionReceipt(WireModel):
    command_id: UUID
    status: Literal["completed", "partial", "rejected"]
    code: str | None = Field(default=None, max_length=128)
    action: ConversationAction
    conversation: ConversationActionConversation | None = None
    export: ConversationActionExport | None = None


BrowserAction = Literal[
    "browser.navigate",
    "browser.take_over",
    "browser.check",
    "browser.back",
    "browser.end",
]
BrowserRevision = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class BrowserAvailability(WireModel):
    state: Literal["available", "check_on_use", "unavailable"]
    code: str | None = Field(max_length=128)


class BrowserControlSnapshot(WireModel):
    schema_version: Literal[1]
    conversation_id: OpaqueId
    revision: BrowserRevision
    active: bool
    paused: bool
    state: Literal[
        "idle",
        "acting",
        "observing",
        "waiting_user",
        "waiting_approval",
        "needs_attention",
    ]
    site: str = Field(max_length=120)
    url: str = Field(max_length=2048)
    last_action: str = Field(max_length=160)
    availability: dict[str, BrowserAvailability] = Field(max_length=12)


class BrowserRevisionInput(WireModel):
    revision: BrowserRevision


class BrowserNavigateInput(BrowserRevisionInput):
    url: str = Field(min_length=1, max_length=8192)


BROWSER_REVIEW_PAYLOADS = {
    "browser.navigate": BrowserNavigateInput,
    "browser.take_over": BrowserRevisionInput,
    "browser.check": BrowserRevisionInput,
    "browser.back": BrowserRevisionInput,
    "browser.end": BrowserRevisionInput,
}


class BrowserReviewRequest(WireModel):
    action: BrowserAction
    type: BrowserAction
    payload: dict

    @model_validator(mode="after")
    def typed_payload(self) -> BrowserReviewRequest:
        if self.type != self.action:
            raise ValueError("Browser review type must match action.")
        self.payload = (
            BROWSER_REVIEW_PAYLOADS[self.action]
            .model_validate(self.payload)
            .model_dump(mode="json")
        )
        return self

    @classmethod
    def model_json_schema(cls, **kwargs: Any) -> dict:
        return _variant_schema(cls, BROWSER_REVIEW_PAYLOADS, **kwargs)


class BrowserReview(WireModel):
    schema_version: Literal[1]
    action: BrowserAction
    conversation_id: OpaqueId
    revision: BrowserRevision
    policy_action: str = Field(min_length=1, max_length=128)
    policy_decision: Literal["allow", "ask"]
    policy_reason: str = Field(max_length=512)
    approval_required: Literal[True]
    origin_and_path: str = Field(max_length=2048)
    query_present: bool
    disclosures: list[str] = Field(min_length=1, max_length=8)
    action_digest: BrowserRevision
    nonce: str = Field(min_length=1, max_length=128)


class BrowserCommandRevisionPayload(BrowserRevisionInput):
    nonce: str = Field(min_length=1, max_length=128)


class BrowserCommandNavigatePayload(BrowserNavigateInput):
    nonce: str = Field(min_length=1, max_length=128)


class BrowserReceipt(WireModel):
    schema_version: Literal[1]
    command_id: UUID
    action: BrowserAction
    conversation_id: OpaqueId
    status: Literal["completed", "partial", "rejected"]
    code: str | None = Field(max_length=128)
    revision: BrowserRevision | None
    browser_control: BrowserControlSnapshot | None


class StopPayload(WireModel):
    generation_id: OpaqueId | None = None


class EntitySummary(WireModel):
    id: OpaqueId
    entity_type: str = Field(max_length=64)
    subject: str = Field(max_length=256)
    description: str = Field(max_length=1000)
    updated_at: str = Field(max_length=64)
    truncated: bool
    saved_state: Literal["saved"]
    semantic_state: Literal["unknown"]


class DocumentSummary(WireModel):
    id: OpaqueId
    name: str = Field(max_length=256)
    status: str = Field(max_length=64)
    stage: str = Field(max_length=64)
    record_state: Literal["saved", "job_only", "record_only", "partial", "removed"]
    index_current: int | None = Field(ge=0, le=9007199254740991)
    index_total: int | None = Field(ge=0, le=9007199254740991)
    extraction_current: int | None = Field(ge=0, le=9007199254740991)
    extraction_total: int | None = Field(ge=0, le=9007199254740991)
    updated_at: str = Field(max_length=64)
    truncated: bool
    searchability: Literal["unknown"]


class EntitySummaryPage(WireModel):
    schema_version: Literal[1]
    revision: str = Field(min_length=64, max_length=64)
    items: list[EntitySummary] = Field(max_length=100)
    total: int | None = Field(ge=0)
    next_cursor: str | None = Field(max_length=1024)
    availability: Literal["available", "missing", "unavailable"]


class DocumentSummaryPage(WireModel):
    schema_version: Literal[1]
    revision: str = Field(min_length=64, max_length=64)
    items: list[DocumentSummary] = Field(max_length=100)
    total: int | None = Field(ge=0)
    next_cursor: str | None = Field(max_length=1024)
    availability: Literal["available", "missing", "unavailable"]


class ToolCatalogRow(WireModel):
    id: str = Field(min_length=1, max_length=256)
    label: str = Field(max_length=256)
    source: Literal["core", "mcp", "plugin", "custom"]
    parent_id: str | None = Field(max_length=256)
    plugin_id: str | None = Field(max_length=256)
    server_name: str | None = Field(max_length=256)
    enabled: bool | None
    configured: bool | None
    destructive: bool | None
    requires_approval: bool | None
    runtime_state: Literal["unknown"]


class ToolCatalogSource(WireModel):
    source: Literal["core", "mcp", "plugin", "custom"]
    state: Literal["cached", "unavailable"]
    total: int | None = Field(ge=0)


class ToolCatalogPage(WireModel):
    schema_version: Literal[1]
    revision: str = Field(min_length=64, max_length=64)
    generated_at: float | None
    freshness: Literal["cached", "unavailable"]
    sources: list[ToolCatalogSource] = Field(max_length=4)
    items: list[ToolCatalogRow] = Field(max_length=100)
    total: int = Field(ge=0)
    next_cursor: str | None = Field(max_length=2048)
    truncated: bool


class SettingsChoice(WireModel):
    value: str = Field(min_length=1, max_length=128)
    label: str = Field(min_length=1, max_length=256)


class SettingsCredentialState(WireModel):
    configured: bool
    source: str = Field(max_length=64)
    fingerprint: str = Field(max_length=128)


class BuddySettingsPack(WireModel):
    pack_id: str = Field(min_length=1, max_length=128)
    name: str = Field(max_length=256)
    version: str = Field(max_length=64)
    runtime: Literal["rive", "generated_still", "generated_motion_pack"]
    status: str = Field(max_length=64)
    message: str = Field(max_length=512)
    motion_clip_count: int = Field(ge=0, le=128)
    preview_available: bool
    selected: bool


class BuddySettingsSnapshot(WireModel):
    availability: Literal["available", "unavailable"]
    enabled: bool
    visible: bool
    placement: Literal["docked", "desktop"]
    collapsed: bool
    personality: str = Field(max_length=64)
    personality_description: str = Field(max_length=200)
    bubble_verbosity: Literal["quiet", "normal", "chatty"]
    hatch_prompt: str = Field(max_length=4096)
    pack_id: str = Field(min_length=1, max_length=128)
    personality_options: list[SettingsChoice] = Field(max_length=16)
    bubble_options: list[SettingsChoice] = Field(max_length=8)
    packs: list[BuddySettingsPack] = Field(max_length=128)


class VoiceRuntimeSettingsSnapshot(WireModel):
    talk_provider: str = Field(max_length=64)
    talk_model: str = Field(max_length=128)
    dictation_provider: str = Field(max_length=64)
    dictation_model: str = Field(max_length=128)
    speech_output_provider: str = Field(max_length=64)
    speech_output_model: str = Field(max_length=128)
    speech_output_voice: str = Field(max_length=64)
    realtime_voice: str = Field(max_length=32)
    captions_enabled: bool
    talk_auto_start: bool
    realtime_fallback_to_local: bool


class VoiceLocalSettingsSnapshot(WireModel):
    whisper_model: Literal["tiny", "base", "small", "medium"]
    sensevoice_path_configured: bool
    runtime_state: Literal["cached_unknown"]


class VoiceTtsSettingsSnapshot(WireModel):
    installed: bool
    enabled: bool
    voice: str = Field(max_length=32)
    speed: float = Field(ge=0.5, le=2.0)
    auto_speak: bool


class VoiceSettingsSnapshot(WireModel):
    availability: Literal["available", "unavailable"]
    runtime: VoiceRuntimeSettingsSnapshot
    local: VoiceLocalSettingsSnapshot
    tts: VoiceTtsSettingsSnapshot
    openai_realtime_credential: SettingsCredentialState
    talk_providers: list[SettingsChoice] = Field(max_length=8)
    dictation_providers: list[SettingsChoice] = Field(max_length=8)
    speech_output_providers: list[SettingsChoice] = Field(max_length=8)
    whisper_options: list[SettingsChoice] = Field(max_length=8)
    realtime_voice_options: list[SettingsChoice] = Field(max_length=16)
    tts_voice_options: list[SettingsChoice] = Field(max_length=32)


class SettingsWorkspaceSnapshot(WireModel):
    path: str = Field(max_length=4096)
    configured: bool
    exists: bool


class SettingsToggleSnapshot(WireModel):
    available: bool
    enabled: bool | None


class SettingsShellSnapshot(SettingsToggleSnapshot):
    blocked_patterns: str = Field(max_length=4096)


class SettingsRuntimeToggleSnapshot(SettingsToggleSnapshot):
    runtime_state: Literal["cached_unknown"]


class SettingsComputerUseSnapshot(SettingsRuntimeToggleSnapshot):
    disclosure_acknowledged: bool
    system_binary_configured: bool


class SettingsFileOperationsSnapshot(SettingsToggleSnapshot):
    selected: list[Annotated[str, StringConstraints(max_length=64)]] = Field(
        max_length=32
    )
    options: list[Annotated[str, StringConstraints(max_length=64)]] = Field(
        max_length=32
    )


class SettingsTunnelSnapshot(WireModel):
    provider: str = Field(max_length=64)
    credential: SettingsCredentialState
    runtime_state: Literal["not_checked"]
    active_count: int | None = Field(ge=0)


class SettingsRemoteAccessSnapshot(WireModel):
    listen_mode: Literal["local_only", "local_network"]
    configured_origins: list[Annotated[str, StringConstraints(max_length=256)]] = Field(
        max_length=64
    )
    host_admission_managed_externally: bool
    tailscale_state: Literal["not_checked"]


class SettingsMobileAccessSnapshot(WireModel):
    availability: Literal["available", "missing", "unavailable"]
    active_devices: int = Field(ge=0)
    active_sessions: int = Field(ge=0)


class SettingsLoggingSnapshot(WireModel):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"]
    directory: str = Field(max_length=4096)


class SystemSettingsSnapshot(WireModel):
    availability: Literal["available", "unavailable"]
    workspace: SettingsWorkspaceSnapshot
    shell: SettingsShellSnapshot
    browser: SettingsRuntimeToggleSnapshot
    computer_use: SettingsComputerUseSnapshot
    file_operations: SettingsFileOperationsSnapshot
    tunnel: SettingsTunnelSnapshot
    remote_access: SettingsRemoteAccessSnapshot
    mobile_access: SettingsMobileAccessSnapshot
    logging: SettingsLoggingSnapshot


class TrackerSettingsItem(WireModel):
    tracker_id: str = Field(min_length=1, max_length=128)
    name: str = Field(max_length=256)
    kind: str = Field(max_length=64)
    unit: str | None = Field(max_length=64)
    icon: str | None = Field(max_length=64)
    entry_count: int = Field(ge=0)
    last_event_at: str | None = Field(max_length=80)


class TrackerSettingsSnapshot(WireModel):
    availability: Literal["available", "missing", "unavailable"]
    tool_available: bool
    enabled: bool | None
    items: list[TrackerSettingsItem] = Field(max_length=128)
    total_entries: int = Field(ge=0)


class KnowledgeTypeCount(WireModel):
    kind: str = Field(max_length=64)
    count: int = Field(ge=0)


class KnowledgeStatusCounts(WireModel):
    active: int = Field(default=0, ge=0)
    needs_review: int = Field(default=0, ge=0)
    superseded: int = Field(default=0, ge=0)
    archived: int = Field(default=0, ge=0)


class KnowledgeSettingsSnapshot(WireModel):
    availability: Literal["available", "missing", "unavailable"]
    memory_available: bool
    memory_enabled: bool | None
    entities: int = Field(ge=0)
    relations: int = Field(ge=0)
    entity_types: list[KnowledgeTypeCount] = Field(max_length=128)
    connected_components: int = Field(ge=0)
    largest_component: int = Field(ge=0)
    isolated_entities: int = Field(ge=0)
    status_counts: KnowledgeStatusCounts = Field(default_factory=KnowledgeStatusCounts)


class WikiSettingsSnapshot(WireModel):
    availability: Literal["available", "unavailable"]
    enabled: bool
    vault_path: str = Field(max_length=4096)
    path_state: Literal["available", "missing", "not_local", "unavailable"]
    articles: int = Field(ge=0)
    conversations: int = Field(ge=0)


class DocumentEmbeddingSettingsSnapshot(WireModel):
    provider: Literal["local", "cloud"]
    local_model: str = Field(max_length=128)
    cloud_model: str = Field(max_length=128)
    dimension: int | None = Field(ge=1, le=100000)
    auto_unload: bool
    runtime_state: Literal["cached_unknown"]
    local_options: list[SettingsChoice] = Field(max_length=16)
    cloud_options: list[SettingsChoice] = Field(max_length=16)


class DocumentRuntimeStatus(WireModel):
    state: Literal[
        "current",
        "stale",
        "partial",
        "inactive",
        "idle",
        "loading",
        "ready",
        "cached",
        "missing",
        "pending",
        "failed",
        "not_checked",
        "unavailable",
    ]
    detail: str = Field(max_length=512)


class DocumentSettingsSnapshot(WireModel):
    availability: Literal["available", "unavailable"]
    embedding: DocumentEmbeddingSettingsSnapshot
    indexed_documents: int | None = Field(default=None, ge=0)
    active_embedding: str = Field(default="", max_length=256)
    document_vectors: DocumentRuntimeStatus = Field(
        default_factory=lambda: DocumentRuntimeStatus(
            state="unavailable",
            detail="Saved document vector health is unavailable.",
        )
    )
    local_runtime: DocumentRuntimeStatus = Field(
        default_factory=lambda: DocumentRuntimeStatus(
            state="unavailable",
            detail="Saved local model runtime state is unavailable.",
        )
    )
    memory_index: DocumentRuntimeStatus = Field(
        default_factory=lambda: DocumentRuntimeStatus(
            state="unavailable",
            detail="Saved memory index state is unavailable.",
        )
    )


class SettingsCredentialField(SettingsCredentialState):
    label: str = Field(max_length=128)
    name: str = Field(max_length=128)


class ToolSettingsItem(WireModel):
    tool_id: str = Field(min_length=1, max_length=256)
    label: str = Field(max_length=256)
    available: bool
    enabled: bool | None
    configured_fields: list[Annotated[str, StringConstraints(max_length=128)]] = Field(
        max_length=128
    )
    credentials: list[SettingsCredentialField] = Field(max_length=16)


class ToolSettingsSnapshot(WireModel):
    availability: Literal["available", "unavailable"]
    external_loading_mode: Literal["auto", "eager"]
    compression_mode: Literal["off", "deep"]
    items: list[ToolSettingsItem] = Field(max_length=32)


class AccountSettingsItem(WireModel):
    account_id: Literal["github", "gmail", "calendar", "x"]
    enabled: bool | None
    configured: bool
    authentication_state: Literal[
        "not_configured",
        "not_authenticated",
        "configured_unchecked",
        "saved_unchecked",
        "expired",
        "unavailable",
    ]
    credentials_path: str = Field(max_length=4096)
    credential: SettingsCredentialState | None
    operations: list[Annotated[str, StringConstraints(max_length=128)]] = Field(
        max_length=128
    )
    read_operations: list[Annotated[str, StringConstraints(max_length=128)]] = Field(
        max_length=128
    )
    post_operations: list[Annotated[str, StringConstraints(max_length=128)]] = Field(
        max_length=128
    )
    engage_operations: list[Annotated[str, StringConstraints(max_length=128)]] = Field(
        max_length=128
    )


class AccountSettingsSnapshot(WireModel):
    availability: Literal["available", "unavailable"]
    github: AccountSettingsItem
    gmail: AccountSettingsItem
    calendar: AccountSettingsItem
    x: AccountSettingsItem


class UtilitySettingsItem(WireModel):
    utility_id: str = Field(min_length=1, max_length=128)
    label: str = Field(max_length=256)
    description: str = Field(max_length=2048)
    available: bool
    enabled: bool | None


class UtilitySettingsSnapshot(WireModel):
    availability: Literal["available", "unavailable"]
    items: list[UtilitySettingsItem] = Field(max_length=32)


class SettingsPluginSummaryItem(WireModel):
    plugin_id: OpaqueId
    name: str = Field(max_length=256)
    version: str = Field(max_length=64)
    enabled: bool
    health: str = Field(max_length=64)


class SettingsPluginSummary(WireModel):
    availability: Literal["available", "unavailable"]
    total: int = Field(ge=0)
    installed: int = Field(ge=0)
    enabled: int = Field(ge=0)
    items: list[SettingsPluginSummaryItem] = Field(max_length=50)


class IdentitySettingsSnapshot(WireModel):
    name: str = Field(min_length=1, max_length=128)
    personality: str = Field(max_length=200)
    personality_max_length: Literal[200]
    self_improvement_enabled: bool


class DreamCycleSettingsSnapshot(WireModel):
    enabled: bool
    window_start: int = Field(ge=0, le=23)
    window_end: int = Field(ge=0, le=23)
    last_run: str | None = Field(max_length=80)
    last_summary: str = Field(max_length=2048)


class UpdateSettingsSnapshot(WireModel):
    current_version: str = Field(max_length=64)
    channel: Literal["stable", "beta"]
    last_check: str | None = Field(max_length=80)
    last_success: str | None = Field(max_length=80)
    skipped_versions: list[Annotated[str, StringConstraints(max_length=256)]] = Field(
        max_length=128
    )
    runtime_state: Literal["cached"]


class MigrationSettingsSnapshot(WireModel):
    available: bool
    sources: list[Annotated[str, StringConstraints(max_length=128)]] = Field(
        max_length=16
    )


class PreferenceSettingsSnapshot(WireModel):
    availability: Literal["available", "unavailable"]
    identity: IdentitySettingsSnapshot
    window_mode: Literal["ask", "native", "browser"]
    dream_cycle: DreamCycleSettingsSnapshot
    updates: UpdateSettingsSnapshot
    migration: MigrationSettingsSnapshot


class SettingsSnapshot(WireModel):
    schema_version: Literal[1]
    revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    buddy: BuddySettingsSnapshot
    voice: VoiceSettingsSnapshot
    system: SystemSettingsSnapshot
    tracker: TrackerSettingsSnapshot
    knowledge: KnowledgeSettingsSnapshot
    wiki: WikiSettingsSnapshot
    documents: DocumentSettingsSnapshot
    tools: ToolSettingsSnapshot
    accounts: AccountSettingsSnapshot
    utilities: UtilitySettingsSnapshot
    plugins: SettingsPluginSummary
    preferences: PreferenceSettingsSnapshot


SettingsMutationPage = Literal[
    "voice",
    "system",
    "tracker",
    "documents",
    "tools",
    "accounts",
    "utilities",
    "preferences",
]
SettingsMutationValue = str | bool | int | float | list[str] | None


class SettingsMutationRequest(WireModel):
    settings_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    page: SettingsMutationPage
    field: str = Field(min_length=1, max_length=128)
    value: SettingsMutationValue


class SettingsMutationReview(WireModel):
    schema_version: Literal[1]
    operation: Literal["settings.update"]
    settings_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    page: SettingsMutationPage
    field: str = Field(min_length=1, max_length=128)
    value_summary: str = Field(max_length=256)
    secret: bool
    action_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    review_id: str = Field(min_length=32, max_length=256)


class SettingsMutationPayload(SettingsMutationRequest):
    action_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    review_id: str = Field(min_length=32, max_length=256)


class SettingsMutationCommand(WireModel):
    command_id: UUID
    client_session_id: UUID
    type: Literal["settings.update"]
    payload: SettingsMutationPayload


class SettingsMutationReceipt(WireModel):
    command_id: UUID
    status: Literal["completed", "rejected", "partial"]
    code: str | None = Field(default=None, max_length=128)
    settings_revision: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    snapshot: SettingsSnapshot | None = None


class TaskSummary(WireModel):
    id: OpaqueId
    name: str = Field(max_length=256)
    description: str = Field(max_length=2048)
    icon: str = Field(max_length=32)
    enabled: bool
    notify_only: bool
    schedule: str | None = Field(max_length=256)
    at: str | None = Field(max_length=80)
    last_run: str | None = Field(max_length=80)
    last_status: str | None = Field(max_length=80)
    conversation_id: OpaqueId | None


class TaskSummaryPage(WireModel):
    schema_version: Literal[1]
    revision: str = Field(min_length=64, max_length=64)
    items: list[TaskSummary] = Field(max_length=100)
    total: int = Field(ge=0)
    next_cursor: str | None = Field(max_length=1024)


class ProviderStatusRow(WireModel):
    provider_id: str = Field(min_length=1, max_length=160)
    display_name: str = Field(max_length=256)
    group: Literal["local", "subscription", "api", "custom"]
    auth_methods: list[Annotated[str, StringConstraints(max_length=80)]] = Field(
        max_length=16
    )
    catalog_state: Literal["cached", "verified_empty", "unavailable", "error"]
    model_count: int | None = Field(ge=0)
    runtime_state: Literal["unknown"] = "unknown"
    enabled: bool | None = None


class ProviderEndpointFields(WireModel):
    endpoint_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    display_name: str = Field(min_length=1, max_length=160)
    base_url: str = Field(min_length=1, max_length=2048)
    profile: str = Field(default="generic_openai", min_length=1, max_length=128)
    execution_location: Literal["local", "remote"] = "local"
    enabled: bool = True
    auth_required: bool = False
    vision_mode: Literal["auto", "on", "off"] = "auto"
    tool_mode: Literal["auto", "on", "off"] = "auto"
    context_window: int | None = Field(default=None, ge=1, le=10000000)
    reasoning_mode: Literal["auto", "on", "off"] = "auto"
    thinking_budget: int | None = Field(default=None, ge=1, le=10000000)
    supports_reasoning_content: bool = False
    supports_reasoning_replay: bool = False
    extra_body_json: str = Field(default="{}", max_length=16384)


class ProviderProbeComponent(WireModel):
    name: str = Field(max_length=80)
    status: str = Field(max_length=80)


class ProviderEndpointSnapshot(WireModel):
    provider_id: str = Field(min_length=1, max_length=160)
    fields: ProviderEndpointFields
    probe_state: Literal["agent_ready", "chat_only", "unavailable", "unknown"]
    model_count: int | None = Field(ge=0)
    probe_components: list[ProviderProbeComponent] = Field(default_factory=list, max_length=20)
    transport: str = Field(default="openai_chat", max_length=80)
    runtime_state: Literal["unknown"] = "unknown"


class ProviderConfigurationPage(WireModel):
    schema_version: Literal[1]
    revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    items: list[ProviderEndpointSnapshot] = Field(max_length=20)
    total: int = Field(ge=0)
    next_cursor: str | None = Field(max_length=1024)
    profiles: list[Annotated[str, StringConstraints(max_length=128)]] = Field(
        max_length=32
    )


ProviderConfigurationOperation = Literal[
    "provider.endpoint.create",
    "provider.endpoint.save",
    "provider.endpoint.delete",
    "provider.endpoint.probe",
    "provider.endpoint.refresh",
    "provider.model.pin",
    "provider.model.unpin",
]


class ProviderEndpointIdentity(WireModel):
    endpoint_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")


class ProviderModelPin(WireModel):
    provider_id: OpaqueId
    model_id: str = Field(min_length=1, max_length=512)
    surface: str = Field(min_length=1, max_length=80)


class ProviderConfigurationReviewRequest(WireModel):
    operation: ProviderConfigurationOperation
    configuration_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    fields: ProviderEndpointFields | ProviderEndpointIdentity | ProviderModelPin


class ProviderConfigurationReview(WireModel):
    operation: ProviderConfigurationOperation
    configuration_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    action_digest: str = Field(min_length=1, max_length=128)
    nonce: str = Field(min_length=1, max_length=128)


class ProviderConfigurationPayload(WireModel):
    configuration_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    fields: ProviderEndpointFields | ProviderEndpointIdentity | ProviderModelPin
    nonce: str = Field(min_length=1, max_length=128)


class ProviderConfigurationReceipt(WireModel):
    command_id: UUID
    status: Literal["completed", "rejected", "uncertain"]
    configuration_revision: str = Field(pattern=r"^[0-9a-f]{64}$")


class DefaultModelSnapshot(WireModel):
    schema_version: Literal[1] = 1
    revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    selection_ref: str | None = Field(max_length=647)
    provider_id: str | None = Field(max_length=128)
    model_id: str | None = Field(max_length=512)
    saved_state: Literal["saved", "missing", "unavailable"]
    runtime_state: Literal["unknown"]


class DefaultModelReviewRequest(WireModel):
    settings_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    provider_id: OpaqueId
    model_id: str = Field(min_length=1, max_length=512)


class DefaultModelReview(DefaultModelReviewRequest):
    operation: Literal["provider.default_model.save"]
    action_digest: str = Field(min_length=1, max_length=128)
    nonce: str = Field(min_length=1, max_length=128)
    snapshot: DefaultModelSnapshot


class DefaultModelPayload(DefaultModelReviewRequest):
    nonce: str = Field(min_length=1, max_length=128)


class DefaultModelReceipt(WireModel):
    command_id: UUID
    status: Literal["completed", "rejected", "uncertain"]
    published: bool
    selection: DefaultModelSnapshot


SubscriptionProvider = Literal["codex", "claude_subscription", "xai_oauth"]
SubscriptionOperation = Literal[
    "start", "check", "submit", "cancel", "disconnect", "restore", "import_token"
]


class SubscriptionAccountSnapshot(WireModel):
    provider_id: SubscriptionProvider
    revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    saved_state: Literal["disconnected", "saved", "metadata_only", "unavailable"]
    credential_storage: Literal["durable", "session", "unknown"]
    has_recovery: bool
    expires_at: str | None = Field(max_length=80)
    runtime_state: Literal["unknown"]


class SubscriptionAccountsSnapshot(WireModel):
    schema_version: Literal[1]
    revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    accounts: list[SubscriptionAccountSnapshot] = Field(min_length=3, max_length=3)


class SubscriptionFlowSnapshot(WireModel):
    flow_id: UUID
    server_epoch: UUID
    provider_id: SubscriptionProvider
    provider_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    state: Literal[
        "starting",
        "waiting",
        "checking",
        "exchanging",
        "publishing",
        "connected",
        "cancelled",
        "expired",
        "uncertain",
        "draining",
    ]
    method: Literal["device_code", "authorization_code", "loopback"]
    authorization_url: str | None = Field(max_length=8192)
    device_code: str | None = Field(max_length=128)
    expires_at: str | None = Field(max_length=80)
    quiescent: bool


class SubscriptionActionReview(WireModel):
    provider_id: SubscriptionProvider
    provider_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    operation: SubscriptionOperation
    flow_id: UUID | None
    server_epoch: UUID | None
    action_digest: str = Field(min_length=1, max_length=128)
    nonce: str = Field(min_length=1, max_length=128)


class SubscriptionActionRequest(WireModel):
    provider_id: SubscriptionProvider
    provider_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    operation: SubscriptionOperation
    value: str | None = Field(default=None, min_length=1, max_length=16384)
    flow_id: UUID | None = None
    server_epoch: UUID | None = None


class SubscriptionActionPayload(WireModel):
    provider_id: SubscriptionProvider
    provider_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    value: str | None = Field(default=None, min_length=1, max_length=16384)
    flow_id: UUID | None = None
    server_epoch: UUID | None = None
    nonce: str = Field(min_length=1, max_length=128)


class SubscriptionActionResult(WireModel):
    command_id: UUID
    status: Literal["completed"]
    accounts: SubscriptionAccountsSnapshot
    flow: SubscriptionFlowSnapshot | None = None


class SubscriptionActionReceipt(WireModel):
    command_id: UUID
    status: Literal["completed", "rejected", "uncertain"]
    published: bool
    accounts: SubscriptionAccountsSnapshot


class SubscriptionQuiescence(WireModel):
    quiescent: bool


class SubscriptionProbeResult(WireModel):
    provider_id: SubscriptionProvider
    kind: Literal["tokens", "runtime", "vision"]
    model_ref: str | None = Field(max_length=647)
    status: Literal["passed", "failed", "missing", "expired", "unavailable"]
    chat_ok: bool | None
    tool_calling: bool | None
    tool_round_trip: bool | None
    vision_ok: bool | None
    checked_at: str | None = Field(max_length=80)


class SubscriptionProbeSnapshot(WireModel):
    schema_version: Literal[1]
    revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    items: list[SubscriptionProbeResult] = Field(min_length=6, max_length=6)


class SubscriptionProbeRequest(WireModel):
    provider_id: SubscriptionProvider
    provider_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    kind: Literal["tokens", "runtime", "vision"]
    model_ref: str | None = Field(max_length=647)


class SubscriptionProbeReview(SubscriptionProbeRequest):
    action_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    nonce: str = Field(min_length=1, max_length=128)


class SubscriptionProbeState(WireModel):
    command_id: UUID
    provider_id: SubscriptionProvider
    state: Literal["running", "draining", "completed", "cancelled", "uncertain"]
    quiescent: bool
    result: SubscriptionProbeResult | None


class SubscriptionProbePayload(SubscriptionProbeRequest):
    nonce: str = Field(min_length=1, max_length=128)


class SubscriptionProbeCommandResult(WireModel):
    command_id: UUID
    status: Literal["completed"]
    result: SubscriptionProbeResult


class SubscriptionProbeReceipt(WireModel):
    command_id: UUID
    provider_id: SubscriptionProvider
    status: Literal["completed", "rejected", "uncertain"]
    published: bool


class SubscriptionProbeStatus(WireModel):
    operation: SubscriptionProbeState | None


DocumentTarget = Annotated[
    str, StringConstraints(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
]


class DocumentRemovalReviewRequest(WireModel):
    document_id: DocumentTarget | None


class DocumentRemovalReview(WireModel):
    schema_version: Literal[1]
    document_id: DocumentTarget | None
    source_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_count: int = Field(ge=0, le=9007199254740991)
    retains_copies: Literal[True]
    review_id: str = Field(min_length=1, max_length=128)
    source_command_id: UUID | None = None
    removal_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    status: Literal["complete", "pending", "partial"] | None = None


class DocumentRemovalPayload(DocumentRemovalReviewRequest):
    source_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    review_id: str = Field(min_length=1, max_length=128)


class DocumentRemovalRetryPayload(WireModel):
    source_command_id: UUID
    review_id: str = Field(min_length=1, max_length=128)


class DocumentRemovalStage(WireModel):
    stage: Literal[
        "worker",
        "derived_snapshot",
        "index",
        "source",
        "raw_copy",
        "derived_knowledge",
        "markers",
        "record",
        "legacy_index",
        "bulk_removal",
    ]
    status: Literal["complete", "pending", "partial"]


class DocumentRemovalStageCounts(WireModel):
    complete: int = Field(ge=0, le=9007199254740991)
    pending: int = Field(ge=0, le=9007199254740991)
    partial: int = Field(ge=0, le=9007199254740991)


class DocumentRemovalOutcome(WireModel):
    removal_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    document_id: DocumentTarget | None
    status: Literal["complete", "pending", "partial"]
    removed: bool
    derived_entities_removed: int = Field(ge=0, le=9007199254740991)
    retained_copy_count: int = Field(ge=0, le=9007199254740991)
    retained_kinds: list[
        Literal[
            "raw_copy",
            "document_source",
            "document_index",
            "legacy_index",
            "externally_edited_raw",
            "wiki_external_and_recovery_copies",
            "interrupted_source_copies",
            "legacy_index_created_after_request",
        ]
    ] = Field(max_length=8)
    stages: list[DocumentRemovalStage] = Field(max_length=10)
    stage_counts: DocumentRemovalStageCounts
    failure_codes: list[Annotated[str, StringConstraints(max_length=64)]] = Field(
        max_length=12
    )


class DocumentRemovalReceipt(WireModel):
    command_id: UUID
    status: Literal["completed", "partial", "rejected"]
    code: Literal["document_outcome_uncertain", "document_action_rejected"] | None = (
        None
    )
    removal: DocumentRemovalOutcome | None = None


class SubscriptionReferenceState(WireModel):
    provider_id: Literal["codex", "claude_subscription"]
    metadata_saved: bool


class SubscriptionOptionsSnapshot(WireModel):
    schema_version: Literal[1]
    revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    references: list[SubscriptionReferenceState] = Field(min_length=2, max_length=2)
    xai_client_id_source: Literal["environment", "override", "default", "missing"]
    xai_saved_client_id: str | None = Field(max_length=512)
    xai_default_available: bool
    runtime_state: Literal["unknown"]


class SubscriptionOptionsRequest(WireModel):
    provider_id: SubscriptionProvider
    provider_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    operation: Literal["reference", "client_id_save", "client_id_reset"]
    value: str | None = Field(max_length=512)


class SubscriptionOptionsPayload(WireModel):
    provider_id: SubscriptionProvider
    provider_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    value: str | None = Field(default=None, max_length=512)
    nonce: str = Field(min_length=1, max_length=128)


class SubscriptionOptionsResult(WireModel):
    command_id: UUID
    status: Literal["completed"]
    options: SubscriptionOptionsSnapshot


class SubscriptionOptionsReceipt(WireModel):
    command_id: UUID
    status: Literal["completed", "rejected", "uncertain"]
    published: bool
    options: SubscriptionOptionsSnapshot


class SubscriptionOptionsReview(SubscriptionOptionsRequest):
    reference_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    action_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    nonce: str = Field(min_length=1, max_length=128)


class McpServerSummary(WireModel):
    server_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    name: str = Field(max_length=128)
    transport: Literal["stdio", "streamable_http", "sse", "unknown"]
    enabled: bool | None
    runtime_status: str | None = Field(max_length=64)
    configured_fields: list[
        Literal["command", "args", "cwd", "url", "env", "headers"]
    ] = Field(max_length=6)
    tool_count: int | None = Field(ge=0, le=10000)
    connection_present: bool | None


class McpConfigurationPage(WireModel):
    schema_version: Literal[1]
    revision: str | None = Field(pattern=r"^[0-9a-f]{64}$")
    availability: Literal["available", "missing", "unavailable", "recovery_required"]
    enabled: bool | None
    items: list[McpServerSummary] = Field(max_length=50)
    total: int | None = Field(ge=0, le=10000)
    next_cursor: str | None = Field(max_length=2048)


class McpConfigurationFields(WireModel):
    name: str | None = Field(default=None, max_length=128)
    transport: Literal["stdio", "streamable_http", "sse"] | None = None
    command: str | None = Field(default=None, max_length=16384)
    args: list[Annotated[str, StringConstraints(max_length=16384)]] | None = Field(
        default=None, max_length=128
    )
    cwd: str | None = Field(default=None, max_length=16384)
    url: str | None = Field(default=None, max_length=16384)
    env: (
        dict[
            Annotated[str, StringConstraints(min_length=1, max_length=256)],
            Annotated[str, StringConstraints(max_length=16384)],
        ]
        | None
    ) = Field(default=None, max_length=128)
    headers: (
        dict[
            Annotated[str, StringConstraints(min_length=1, max_length=256)],
            Annotated[str, StringConstraints(max_length=16384)],
        ]
        | None
    ) = Field(default=None, max_length=128)
    connect_timeout: float | int | None = Field(default=None, ge=1, le=3600)
    tool_timeout: float | int | None = Field(default=None, ge=1, le=3600)
    output_limit: int | None = Field(default=None, ge=1, le=1000000)


class McpConfigurationIntent(WireModel):
    operation: Literal["add", "edit", "rename", "import"]
    server_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    fields: McpConfigurationFields | None = None
    import_json: str | None = Field(default=None, max_length=131072)


class McpConfigurationReviewRequest(WireModel):
    configuration_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    intent: McpConfigurationIntent


class McpConfigurationPayload(McpConfigurationReviewRequest):
    nonce: str = Field(min_length=1, max_length=128)


class McpConfigurationReview(WireModel):
    configuration_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    operation: Literal["add", "edit", "rename", "import"]
    action_digest: str = Field(min_length=1, max_length=128)
    server_ids: list[Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]] = (
        Field(max_length=64)
    )
    saved_disabled: Literal[True]
    nonce: str = Field(min_length=1, max_length=128)


class McpConfigurationOutcome(WireModel):
    schema_version: Literal[1] = 1
    status: Literal["saved", "partial"]
    revision: str | None = Field(pattern=r"^[0-9a-f]{64}$")
    server_ids: list[Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]] = (
        Field(max_length=64)
    )
    saved_disabled: Literal[True] | None
    runtime_cleanup: Literal["not_requested"]
    code: Literal["mcp_configuration_unconfirmed"] | None


KnowledgeAction = Literal[
    "knowledge.create",
    "knowledge.edit",
    "knowledge.archive",
    "knowledge.restore",
    "knowledge.resolve",
]


class KnowledgeFields(WireModel):
    entity_type: Literal[
        "concept",
        "event",
        "fact",
        "media",
        "organisation",
        "person",
        "place",
        "preference",
        "project",
        "self_knowledge",
        "skill",
    ]
    subject: str = Field(max_length=256)
    description: str = Field(max_length=32768)
    aliases: str = Field(max_length=4096)
    tags: str = Field(max_length=4096)


class KnowledgeEntity(WireModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    fields: KnowledgeFields
    status: Literal["active", "archived", "needs_review", "superseded"]
    created_at: str = Field(max_length=128)
    updated_at: str = Field(max_length=128)
    saved_state: Literal["saved"]
    projection_state: Literal["unknown"]


class KnowledgeEditorState(WireModel):
    schema_version: Literal[1]
    entity: KnowledgeEntity | None
    entity_types: list[str] = Field(max_length=11)


class KnowledgeDraftInput(WireModel):
    entity_id: str | None = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    revision: str = Field(pattern=r"^([0-9a-f]{64})?$")
    fields: KnowledgeFields | None = None


class KnowledgeReviewRequest(WireModel):
    action: KnowledgeAction
    payload: KnowledgeDraftInput


class KnowledgeReview(WireModel):
    schema_version: Literal[1]
    action: KnowledgeAction
    entity_id: str | None = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    fields_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    reuse_entity_id: str | None = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    review_id: str = Field(min_length=1, max_length=128)


class KnowledgeLifecyclePayload(WireModel):
    entity_id: str | None = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    review_id: str = Field(min_length=1, max_length=128)


class KnowledgeWritePayload(KnowledgeLifecyclePayload):
    fields: KnowledgeFields


class KnowledgeReceipt(WireModel):
    command_id: UUID
    status: Literal["completed", "partial", "rejected"]
    code: Literal["knowledge_changed", "knowledge_outcome_uncertain"] | None = None
    entity_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,128}$")
    revision: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    saved_state: Literal["saved"] | None = None
    projection_state: Literal["pending", "unknown"] | None = None
    reused: bool | None = None


KnowledgeId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,128}$")]
KnowledgeRevision = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
KnowledgeRelationAction = Literal[
    "knowledge.relation.add", "knowledge.relation.remove", "knowledge.supersede"
]


class KnowledgeRelation(WireModel):
    id: KnowledgeId
    source_id: KnowledgeId
    target_id: KnowledgeId
    relation_type: str = Field(max_length=64)
    confidence: float = Field(ge=0, le=1)
    revision: KnowledgeRevision
    peer_id: KnowledgeId
    peer_subject: str = Field(max_length=256)
    truncated: bool


class KnowledgeRelationPage(WireModel):
    schema_version: Literal[1]
    revision: str = Field(max_length=128)
    items: list[KnowledgeRelation] = Field(max_length=50)
    total: int | None = Field(ge=0, le=9007199254740991)
    next_cursor: str | None = Field(max_length=2048)
    availability: Literal["available", "missing", "unavailable"]


class KnowledgeRelationAddInput(WireModel):
    source_id: KnowledgeId
    target_id: KnowledgeId
    source_revision: KnowledgeRevision
    target_revision: KnowledgeRevision
    relation_type: str = Field(min_length=1, max_length=64)


class KnowledgeRelationRemoveInput(WireModel):
    relation_id: KnowledgeId
    relation_revision: KnowledgeRevision
    source_revision: KnowledgeRevision
    target_revision: KnowledgeRevision


class KnowledgeSupersedeInput(WireModel):
    old_id: KnowledgeId
    new_id: KnowledgeId
    old_revision: KnowledgeRevision
    new_revision: KnowledgeRevision


class KnowledgeRelationReviewRequest(WireModel):
    action: KnowledgeRelationAction
    payload: (
        KnowledgeRelationAddInput
        | KnowledgeRelationRemoveInput
        | KnowledgeSupersedeInput
    )


class KnowledgeRelationReview(WireModel):
    schema_version: Literal[1]
    action: KnowledgeRelationAction
    entity_ids: list[KnowledgeId] = Field(min_length=2, max_length=2)
    entity_revisions: list[KnowledgeRevision] = Field(min_length=2, max_length=2)
    relation_id: KnowledgeId | None
    relation_revision: KnowledgeRevision
    intent_digest: KnowledgeRevision
    review_id: str = Field(min_length=1, max_length=128)


class KnowledgeRelationAddPayload(KnowledgeRelationAddInput):
    review_id: str = Field(min_length=1, max_length=128)


class KnowledgeRelationRemovePayload(KnowledgeRelationRemoveInput):
    review_id: str = Field(min_length=1, max_length=128)


class KnowledgeSupersedePayload(KnowledgeSupersedeInput):
    review_id: str = Field(min_length=1, max_length=128)


class KnowledgeRelationReceipt(WireModel):
    command_id: UUID
    status: Literal["completed", "partial", "rejected"]
    code: (
        Literal["relation_changed_or_invalid", "knowledge_outcome_uncertain"] | None
    ) = None
    outcome: Literal["saved", "removed", "superseded"] | None = None
    entity_ids: list[KnowledgeId] | None = Field(
        default=None, min_length=2, max_length=2
    )
    relation_id: KnowledgeId | None = None
    projection_state: Literal["pending", "unknown"] | None = None


DocumentControlAction = Literal[
    "document.batch.pause",
    "document.batch.resume",
    "document.batch.cancel",
    "document.job.cancel",
    "document.job.retry",
    "document.jobs.clear_finished",
]


class DocumentProcessingReviewRequest(WireModel):
    batch_id: KnowledgeId
    revision: KnowledgeRevision


class DocumentProcessingChat(WireModel):
    provider_id: str = Field(min_length=1, max_length=128)
    model_ref: str = Field(min_length=1, max_length=512)
    execution_location: str = Field(min_length=1, max_length=32)


class DocumentProcessingEmbedding(WireModel):
    provider: str = Field(min_length=1, max_length=128)
    execution_location: Literal["local", "remote"]


class DocumentProcessingReview(DocumentProcessingReviewRequest):
    schema_version: Literal[1]
    action: Literal["document.batch.process"]
    conversation_id: KnowledgeId
    policy_digest: KnowledgeRevision
    provider_work: Literal[True]
    knowledge_projection_scope: Literal["saved_knowledge"]
    chat: DocumentProcessingChat
    embedding: DocumentProcessingEmbedding
    review_id: str = Field(min_length=1, max_length=128)


class DocumentProcessingPayload(DocumentProcessingReviewRequest):
    conversation_id: KnowledgeId
    review_id: str = Field(min_length=1, max_length=128)


class DocumentProcessingReceipt(WireModel):
    command_id: UUID
    status: Literal["completed", "partial"]
    code: Literal["document_processing_uncertain"] | None = None
    batch_id: KnowledgeId | None = None
    processing: Literal["admitted"] | None = None


class DocumentUploadFile(WireModel):
    name: str = Field(min_length=1, max_length=256)
    size_bytes: int = Field(ge=1, le=256 * 1024**2)


class DocumentUploadReviewRequest(WireModel):
    files: list[DocumentUploadFile] = Field(min_length=1, max_length=50)


class DocumentUploadPayload(DocumentUploadReviewRequest):
    review_id: str = Field(min_length=1, max_length=128)


class DocumentUploadReview(DocumentUploadPayload):
    schema_version: Literal[1]
    action: Literal["document.upload"]
    file_count: int = Field(ge=1, le=50)
    total_bytes: int = Field(ge=1, le=50 * 256 * 1024**2)
    intent_digest: KnowledgeRevision
    processing: Literal["paused"]
    provider_work: Literal[False]


class DocumentUploadedFile(DocumentUploadFile):
    id: KnowledgeId
    status: Literal["queued", "skipped_duplicate"]


class DocumentUploadReceipt(WireModel):
    command_id: UUID
    status: Literal["completed", "partial", "rejected"]
    code: Literal["document_upload_uncertain"] | None = None
    batch_id: KnowledgeId | None = None
    processing: Literal["paused"] | None = None
    files: list[DocumentUploadedFile] | None = Field(
        default=None, min_length=1, max_length=50
    )


class DocumentQueueItem(WireModel):
    id: KnowledgeId
    batch_id: KnowledgeId | None
    name: str = Field(max_length=256)
    status: str = Field(min_length=1, max_length=64)
    stage: str | None = Field(max_length=64)
    pause_requested: bool
    cancel_requested: bool
    attempt: int | None = Field(ge=0, le=2**53 - 1)
    index_current: int | None = Field(ge=0, le=2**53 - 1)
    index_total: int | None = Field(ge=0, le=2**53 - 1)
    extraction_current: int | None = Field(ge=0, le=2**53 - 1)
    extraction_total: int | None = Field(ge=0, le=2**53 - 1)
    error_code: str | None = Field(max_length=128)
    revision: KnowledgeRevision


class DocumentQueuePage(WireModel):
    schema_version: Literal[1]
    revision: KnowledgeRevision
    items: list[DocumentQueueItem] = Field(max_length=50)
    total: int | None = Field(ge=0, le=2**53 - 1)
    next_cursor: str | None = Field(max_length=2048)
    availability: Literal["available", "missing", "unavailable"]


WikiAction = Literal[
    "wiki.configure", "wiki.publish", "wiki.rebuild", "wiki.import", "wiki.sync"
]


class WikiStatus(WireModel):
    schema_version: Literal[1]
    revision: KnowledgeRevision
    enabled: bool
    availability: Literal["available", "scope_required", "unavailable"]
    scope_id: str | None = Field(max_length=256)
    articles: int | None = Field(ge=0, le=100000)
    edited: int | None = Field(ge=0, le=100000)
    conflicts: int | None = Field(ge=0, le=100000)


class WikiArticleSummary(WireModel):
    article_id: KnowledgeRevision
    entity_id: KnowledgeId | None
    title: str = Field(max_length=256)
    status: Literal[
        "unmanaged", "conflict", "unchanged", "edited", "legacy_review", "missing"
    ]
    vault_hash: KnowledgeRevision | None
    db_revision: KnowledgeRevision | None


class WikiArticlePage(WireModel):
    schema_version: Literal[1]
    revision: KnowledgeRevision
    scope_id: str = Field(min_length=1, max_length=256)
    items: list[WikiArticleSummary] = Field(max_length=50)
    total: int = Field(ge=0, le=100000)
    next_cursor: str | None = Field(max_length=2048)


class WikiArticle(WireModel):
    article_id: KnowledgeRevision
    entity_id: KnowledgeId | None
    title: str = Field(max_length=256)
    vault_text: str = Field(max_length=192 * 1024)
    vault_hash: KnowledgeRevision
    database_text: str | None = Field(max_length=192 * 1024)
    db_revision: KnowledgeRevision | None


class WikiConfigureInput(WireModel):
    revision: KnowledgeRevision
    enabled: bool


class WikiPublishInput(WireModel):
    revision: KnowledgeRevision
    entity_id: KnowledgeId


class WikiRebuildInput(WireModel):
    revision: KnowledgeRevision


class WikiImportInput(WireModel):
    revision: KnowledgeRevision
    article_ids: list[KnowledgeRevision] = Field(min_length=1, max_length=50)


class WikiReviewRequest(WireModel):
    action: WikiAction
    folder_grant: str = Field(min_length=1, max_length=256)
    payload: WikiConfigureInput | WikiPublishInput | WikiRebuildInput | WikiImportInput


class WikiReview(WireModel):
    schema_version: Literal[1]
    action: WikiAction
    scope_id: str = Field(min_length=1, max_length=256)
    revision: KnowledgeRevision
    source_revision: KnowledgeRevision | None
    vault_revision: KnowledgeRevision | None
    articles: list[WikiArticle] = Field(max_length=50)
    enabled: bool | None
    entity_id: KnowledgeId | None = None
    entity_revision: KnowledgeRevision | None = None
    action_digest: KnowledgeRevision
    review_id: str = Field(min_length=1, max_length=128)


class WikiConfigurePayload(WikiConfigureInput):
    review_id: str = Field(min_length=1, max_length=128)
    folder_grant: str = Field(min_length=1, max_length=256)


class WikiPublishPayload(WikiPublishInput):
    review_id: str = Field(min_length=1, max_length=128)
    folder_grant: str = Field(min_length=1, max_length=256)


class WikiRebuildPayload(WikiRebuildInput):
    review_id: str = Field(min_length=1, max_length=128)
    folder_grant: str = Field(min_length=1, max_length=256)


class WikiImportPayload(WikiImportInput):
    review_id: str = Field(min_length=1, max_length=128)
    folder_grant: str = Field(min_length=1, max_length=256)


class WikiReceipt(WireModel):
    command_id: UUID
    status: Literal["completed", "partial"]
    action: WikiAction
    count: int = Field(ge=0, le=100000)
    conflicts: int = Field(ge=0, le=100000)
    code: Literal["wiki_conflict", "wiki_outcome_uncertain"] | None


ChannelOperation = Literal["configure", "start", "stop", "pair", "revoke"]


class ChannelSource(WireModel):
    kind: Literal["core", "plugin", "unknown"]
    label: str = Field(max_length=160)


class ChannelFieldStatus(WireModel):
    key: OpaqueId
    label: str = Field(max_length=160)
    field_type: Literal["text", "password", "number", "slider"]
    storage: Literal["env", "config"]
    help_text: str = Field(max_length=512)
    configured: bool | None
    source: str = Field(max_length=64)
    fingerprint: str = Field(max_length=128)
    externally_managed: bool
    writable: bool


class PairedChannelIdentity(WireModel):
    identity_id: KnowledgeRevision
    display_name: str = Field(max_length=160)
    hint: str = Field(max_length=32)


class ChannelActivity(WireModel):
    kind: Literal["last_inbound"]
    recency: Literal["recent", "within_hour", "within_day", "older", "none", "unknown"]


class ChannelAvailability(WireModel):
    configuration: Literal["available", "limited"]
    lifecycle: Literal["available", "configuration_required"]
    pairing: Literal["available", "unsupported", "unavailable"]
    monitor: Literal["available", "unavailable"]


class ChannelStatus(WireModel):
    schema_version: Literal[1]
    channel_id: OpaqueId
    display_name: str = Field(max_length=160)
    source: ChannelSource
    revision: KnowledgeRevision
    configured: bool | None
    running: bool | None
    activity: Literal["recent", "within_hour", "within_day", "older", "none", "unknown"]
    activity_history: list[ChannelActivity] = Field(max_length=1)
    fields: list[ChannelFieldStatus] = Field(max_length=64)
    paired_identities: list[PairedChannelIdentity] = Field(max_length=128)
    capabilities: list[str] = Field(max_length=16)
    availability: ChannelAvailability


class ChannelPage(WireModel):
    schema_version: Literal[1]
    total: int = Field(ge=0, le=128)
    items: list[ChannelStatus] = Field(max_length=50)
    truncated: bool


class ChannelActionRequest(WireModel):
    channel_id: OpaqueId
    revision: KnowledgeRevision
    operation: ChannelOperation
    field_key: OpaqueId | None
    value: str | int | float | None
    identity_id: KnowledgeRevision | None


class ChannelActionReview(WireModel):
    channel_id: OpaqueId
    revision: KnowledgeRevision
    operation: ChannelOperation
    field_key: OpaqueId | None
    identity_id: KnowledgeRevision | None
    action_digest: KnowledgeRevision
    review_id: str = Field(min_length=1, max_length=128)


class ChannelActionPayload(ChannelActionRequest):
    review_id: str = Field(min_length=1, max_length=128)


class ChannelReceipt(WireModel):
    command_id: UUID
    status: Literal["completed", "partial", "rejected"]
    operation: ChannelOperation | None = None
    code: str | None = Field(default=None, max_length=128)
    pairing_code: str | None = Field(default=None, max_length=32)
    channel: ChannelStatus | None = None


class PluginCapability(WireModel):
    available: bool
    code: str | None = Field(max_length=128)


class PluginCatalogItem(WireModel):
    plugin_id: OpaqueId
    name: str = Field(max_length=256)
    version: str = Field(max_length=64)
    description: str = Field(max_length=2048)
    source: Literal["installed", "marketplace"]
    installed: bool
    enabled: bool
    setup_complete: bool
    health: str = Field(max_length=64)
    update_version: str | None = Field(max_length=64)
    permissions: list[str] = Field(max_length=64)
    provides: dict[str, int]
    manifest_revision: KnowledgeRevision | None
    capabilities: dict[str, PluginCapability]


class PluginCatalogPage(WireModel):
    schema_version: Literal[1]
    revision: KnowledgeRevision
    availability: Literal["available", "unavailable"]
    items: list[PluginCatalogItem] = Field(max_length=50)
    total: int = Field(ge=0, le=2000)
    next_cursor: str | None = Field(max_length=2048)


class PluginField(WireModel):
    name: OpaqueId
    label: str = Field(max_length=128)
    type: str = Field(max_length=64)
    required: bool
    options: list[str] = Field(max_length=128)
    configured: bool
    value: Any = None
    minimum: int | float | None = None
    maximum: int | float | None = None


class PluginHealthCheck(WireModel):
    label: str = Field(max_length=128)
    status: str = Field(max_length=64)


class PluginHealth(WireModel):
    status: str = Field(max_length=64)
    checks: list[PluginHealthCheck] = Field(max_length=64)


class PluginDetail(WireModel):
    schema_version: Literal[1]
    plugin_id: OpaqueId
    revision: KnowledgeRevision
    name: str = Field(max_length=256)
    version: str = Field(max_length=64)
    description: str = Field(max_length=2048)
    enabled: bool
    settings: list[PluginField] = Field(max_length=128)
    secrets: list[PluginField] = Field(max_length=128)
    health: PluginHealth
    permissions: list[str] = Field(max_length=64)
    capabilities: dict[str, PluginCapability]


PluginAction = Literal[
    "plugin.enable",
    "plugin.disable",
    "plugin.configure",
    "plugin.install",
    "plugin.update",
    "plugin.remove",
]


class PluginReviewRequest(WireModel):
    action: PluginAction
    payload: dict[str, Any]


class PluginReview(WireModel):
    schema_version: Literal[1]
    plugin_id: OpaqueId
    action: PluginAction
    revision: KnowledgeRevision
    action_digest: KnowledgeRevision
    changes: dict[str, Any]
    disclosures: list[str] = Field(max_length=8)
    review_id: str = Field(min_length=1, max_length=128)


class PluginTogglePayload(WireModel):
    plugin_id: OpaqueId
    revision: KnowledgeRevision
    action_digest: KnowledgeRevision
    review_id: str = Field(min_length=1, max_length=128)


class PluginConfigurePayload(PluginTogglePayload):
    settings: dict[str, Any]
    secrets: dict[str, Any]


class PluginReceiptItem(WireModel):
    plugin_id: OpaqueId
    action: PluginAction
    enabled: bool | None = None
    revision: KnowledgeRevision | None = None


class PluginReceipt(WireModel):
    command_id: UUID
    status: Literal["accepted", "completed", "partial", "rejected"]
    code: str | None = Field(default=None, max_length=128)
    plugin: PluginReceiptItem | None = None


SkillAction = Literal[
    "skill.preference",
    "skill.create",
    "skill.import",
    "skill.edit",
    "skill.duplicate",
    "skill.delete",
    "skill.proposal.apply",
    "skill.proposal.reject",
]


class SkillSummary(WireModel):
    id: OpaqueId
    display_name: str = Field(max_length=128)
    icon: str = Field(max_length=32)
    description: str = Field(max_length=1024)
    source: Literal["user", "bundled"]
    version: str = Field(max_length=32)
    tags: list[str] = Field(max_length=32)
    activation: dict[str, list[str]]
    available: bool
    pinned: bool
    editable: bool
    tool_guide: bool
    revision: KnowledgeRevision
    instructions_preview: str = Field(max_length=320)
    truncated: bool


class SkillPage(WireModel):
    schema_version: Literal[1]
    revision: str = Field(min_length=1, max_length=128)
    availability: Literal["available", "missing", "unavailable"]
    items: list[SkillSummary] = Field(max_length=50)
    total: int | None = Field(ge=0, le=100000)
    next_cursor: str | None = Field(max_length=2048)


class SkillDetailSummary(SkillSummary):
    instructions: str = Field(max_length=48 * 1024)


class SkillDetail(WireModel):
    schema_version: Literal[1]
    library_revision: str = Field(min_length=1, max_length=128)
    skill: SkillDetailSummary


class SkillProposal(WireModel):
    id: str = Field(min_length=1, max_length=128)
    type: Literal["create_skill", "patch_skill", "consolidate_skills"]
    title: str = Field(max_length=256)
    rationale: str = Field(max_length=4096)
    risk: Literal["low", "medium", "high"]
    status: str = Field(max_length=64)
    preview: dict[str, Any]


class SkillProposalPage(WireModel):
    schema_version: Literal[1]
    revision: str = Field(min_length=1, max_length=128)
    items: list[SkillProposal] = Field(max_length=100)
    truncated: bool


class SkillReviewRequest(WireModel):
    action: SkillAction
    payload: dict[str, Any]


class SkillReview(WireModel):
    schema_version: Literal[1]
    action: SkillAction
    revision: str = Field(min_length=1, max_length=128)
    target: str = Field(min_length=1, max_length=128)
    before_revision: KnowledgeRevision | None
    after: dict[str, Any] | None
    action_digest: KnowledgeRevision
    review_id: str = Field(min_length=1, max_length=128)


class SkillPreferencePayload(WireModel):
    revision: str = Field(min_length=1, max_length=128)
    name: OpaqueId
    preference: Literal["availability", "pin_defaults"]
    value: bool
    review_id: str = Field(min_length=1, max_length=128)


class SkillCreatePayload(WireModel):
    revision: str = Field(min_length=1, max_length=128)
    name: OpaqueId
    fields: dict[str, Any]
    review_id: str = Field(min_length=1, max_length=128)


class SkillImportPayload(WireModel):
    revision: str = Field(min_length=1, max_length=128)
    content: str = Field(min_length=1, max_length=256 * 1024)
    review_id: str = Field(min_length=1, max_length=128)


class SkillEditPayload(WireModel):
    revision: str = Field(min_length=1, max_length=128)
    name: OpaqueId
    skill_revision: KnowledgeRevision
    fields: dict[str, Any]
    review_id: str = Field(min_length=1, max_length=128)


class SkillDuplicatePayload(WireModel):
    revision: str = Field(min_length=1, max_length=128)
    name: OpaqueId
    new_name: OpaqueId
    review_id: str = Field(min_length=1, max_length=128)


class SkillDeletePayload(WireModel):
    revision: str = Field(min_length=1, max_length=128)
    name: OpaqueId
    skill_revision: KnowledgeRevision
    review_id: str = Field(min_length=1, max_length=128)


class SkillProposalPayload(WireModel):
    revision: str = Field(min_length=1, max_length=128)
    proposal_id: str = Field(min_length=1, max_length=128)
    reason: str = Field(max_length=1024)
    review_id: str = Field(min_length=1, max_length=128)


class SkillReceipt(WireModel):
    command_id: UUID
    status: Literal["completed", "partial"]
    action: SkillAction
    skill_id: str | None = Field(max_length=128)
    revision: str | None = Field(max_length=128)
    code: str | None = Field(max_length=128)


GoalStatus = Literal[
    "active",
    "paused",
    "waiting_approval",
    "blocked",
    "completed",
    "cleared",
]
GoalOperation = Literal["start", "pause", "resume", "complete", "clear"]


class GoalSummary(WireModel):
    id: str = Field(min_length=1, max_length=128)
    scope: Literal["conversation"]
    conversation_id: str = Field(min_length=1, max_length=256)
    objective: str = Field(max_length=4096)
    status: GoalStatus
    revision: str = Field(pattern=r"^(0|[1-9][0-9]{0,19})$")
    turns_used: int = Field(ge=0)
    max_turns: int = Field(ge=0, le=1000)
    token_budget: int = Field(ge=0)
    tokens_used: int = Field(ge=0)
    last_progress: str = Field(max_length=2049)
    last_reason: str = Field(max_length=2049)
    evidence: list[str] = Field(max_length=10)
    blockers: list[str] = Field(max_length=10)
    active_profile_id: str = Field(max_length=256)


class GoalPage(WireModel):
    schema_version: Literal[1]
    scope: Literal["conversation"]
    conversation_id: str = Field(min_length=1, max_length=256)
    revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    current_goal_id: str | None = Field(max_length=128)
    current_revision: str = Field(max_length=20)
    items: list[GoalSummary] = Field(max_length=50)
    total: int = Field(ge=0, le=500)
    next_cursor: str | None = Field(max_length=80)


class GoalDetail(WireModel):
    schema_version: Literal[1]
    goal: GoalSummary


class GoalCommandPayload(WireModel):
    conversation_id: str = Field(min_length=1, max_length=256)
    goal_id: str | None = Field(max_length=128)
    revision: str = Field(max_length=20)
    operation: GoalOperation
    objective: str | None = Field(max_length=4096)
    max_turns: int | None = Field(ge=1, le=1000)
    reason: str | None = Field(max_length=1024)


class GoalReview(GoalCommandPayload):
    schema_version: Literal[1]
    action_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    disclosures: list[str] = Field(max_length=8)
    review_id: str = Field(min_length=1, max_length=128)


class GoalCommandWirePayload(GoalCommandPayload):
    review_id: str = Field(min_length=1, max_length=128)


class GoalReceipt(WireModel):
    command_id: UUID
    status: Literal["completed", "partial"]
    operation: GoalOperation | None = None
    goal: GoalSummary | None = None
    code: str | None = Field(default=None, max_length=128)


ProfileScope = Literal["system", "user", "workspace", "plugin", "imported"]
ProfileOperation = Literal["create", "edit", "duplicate", "delete", "enable", "disable"]


class ProfileInstructionEdit(WireModel):
    mode: Literal["replace_only"]
    stored: bool


class ProfileSummary(WireModel):
    id: str = Field(min_length=1, max_length=256)
    slug: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=160)
    description: str = Field(max_length=1024)
    when_to_use: str = Field(max_length=1024)
    scope: ProfileScope
    surface_scope: Literal["global"]
    source: str = Field(max_length=128)
    enabled: bool
    editable: bool
    revision: str = Field(pattern=r"^[1-9][0-9]{0,19}$")
    capability: Literal["read_only", "write_capable", "orchestrator"]
    allow_tools: list[str] = Field(max_length=100)
    skills: list[str] = Field(max_length=100)
    context_mode: Literal["auto", "focused", "recent", "full", "empty", "resume"]
    workspace_mode: Literal["auto", "read_only", "single_writer", "worktree"]
    approval_mode: Literal["inherit", "block", "approve", "allow_all"]
    instructions_preview: Literal[""]
    instructions_truncated: bool
    instruction_edit: ProfileInstructionEdit | None = None


class ProfilePage(WireModel):
    schema_version: Literal[1]
    scope: Literal["global"]
    revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    items: list[ProfileSummary] = Field(max_length=50)
    total: int = Field(ge=0, le=500)
    next_cursor: str | None = Field(max_length=80)


class ProfileDetail(WireModel):
    schema_version: Literal[1]
    profile: ProfileSummary


class ProfileFields(WireModel):
    slug: str = Field(min_length=2, max_length=64, pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    display_name: str = Field(min_length=1, max_length=160)
    description: str = Field(max_length=2048)
    when_to_use: str = Field(max_length=2048)
    instructions: str | None = Field(max_length=49152)
    capability: Literal["read_only", "write_capable", "orchestrator"]
    allow_tools: list[str] = Field(max_length=100)
    skills: list[str] = Field(max_length=100)
    context_mode: Literal["auto", "focused", "recent", "full", "empty", "resume"]
    workspace_mode: Literal["auto", "read_only", "single_writer", "worktree"]
    approval_mode: Literal["inherit", "block", "approve", "allow_all"]
    enabled: bool


class ProfileCommandPayload(WireModel):
    profile_id: str | None = Field(max_length=256)
    revision: str = Field(max_length=20)
    operation: ProfileOperation
    fields: ProfileFields | None
    target_slug: str | None = Field(max_length=64)
    target_name: str | None = Field(max_length=160)


class ProfileReview(ProfileCommandPayload):
    schema_version: Literal[1]
    changes: dict[str, Any]
    action_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    disclosures: list[str] = Field(max_length=8)
    review_id: str = Field(min_length=1, max_length=128)


class ProfileCommandWirePayload(ProfileCommandPayload):
    review_id: str = Field(min_length=1, max_length=128)


class ProfileReceipt(WireModel):
    command_id: UUID
    status: Literal["completed", "partial"]
    operation: ProfileOperation | None = None
    profile: ProfileSummary | None = None
    profile_id: str | None = Field(default=None, max_length=256)
    code: str | None = Field(default=None, max_length=128)


DeveloperRepositoryAction = Literal[
    "developer.repository.branch.create",
    "developer.repository.branch.switch",
    "developer.repository.commit",
    "developer.repository.push",
    "developer.repository.pull_request",
    "developer.repository.worktree.create",
    "developer.repository.worktree.preserve",
    "developer.repository.sandbox.configure",
    "developer.repository.sandbox.rebuild",
    "developer.repository.sandbox.cleanup",
]
DeveloperRepositoryRevision = Annotated[
    str, StringConstraints(pattern=r"^[0-9a-f]{64}$")
]


class DeveloperRepositoryCapability(WireModel):
    available: bool
    code: str | None = Field(max_length=128)


class DeveloperGitState(WireModel):
    state: Literal["ready", "plain_folder"]
    is_git: bool
    is_root: bool
    branch: str = Field(max_length=256)
    detached: bool
    dirty: bool
    remote_configured: bool
    tracking_summary: str = Field(max_length=512)


class DeveloperWorktreeState(WireModel):
    worktree_id: str = Field(min_length=1, max_length=128)
    branch: str = Field(max_length=256)
    status: Literal["active", "failed", "preserved", "archived"]
    cleanup_state: Literal["preserve", "requested", "completed", "failed"]
    current: bool
    owned_by_conversation: bool
    source_dirty: bool
    seeded_current_changes: bool
    has_error: bool


class DeveloperSandboxState(WireModel):
    execution_mode: Literal["local", "docker"]
    network: Literal["off", "ask", "on"]
    image: str = Field(max_length=256)
    pending_imports: int = Field(ge=0, le=100)
    owned_processes: int = Field(ge=0, le=256)
    runtime_status: Literal["not_probed"]


class DeveloperRepositorySnapshot(WireModel):
    schema_version: Literal[1]
    resource_id: str = Field(min_length=1, max_length=128)
    conversation_id: str = Field(min_length=1, max_length=256)
    binding_id: str = Field(min_length=1, max_length=128)
    binding_revision: str = Field(min_length=1, max_length=128)
    resource_revision: str = Field(min_length=1, max_length=128)
    revision: DeveloperRepositoryRevision
    workspace_name: str = Field(max_length=256)
    trusted: bool
    repository: DeveloperGitState
    worktrees: list[DeveloperWorktreeState] = Field(max_length=32)
    sandbox: DeveloperSandboxState
    availability: dict[str, DeveloperRepositoryCapability] = Field(max_length=20)


class DeveloperRepositoryRevisionInput(WireModel):
    revision: DeveloperRepositoryRevision


class DeveloperRepositoryBranchInput(DeveloperRepositoryRevisionInput):
    branch: str = Field(min_length=1, max_length=120)


class DeveloperRepositoryCommitInput(DeveloperRepositoryRevisionInput):
    message: str = Field(min_length=1, max_length=512)
    paths: list[str] = Field(max_length=50)


class DeveloperRepositoryPullRequestInput(DeveloperRepositoryRevisionInput):
    title: str = Field(max_length=256)
    body: str = Field(max_length=16384)
    draft: bool


class DeveloperRepositoryWorktreeCreateInput(DeveloperRepositoryRevisionInput):
    objective: str = Field(max_length=512)
    seed_mode: Literal["current_changes"]


class DeveloperRepositoryWorktreePreserveInput(DeveloperRepositoryRevisionInput):
    reason: str = Field(max_length=512)


class DeveloperRepositorySandboxInput(DeveloperRepositoryRevisionInput):
    execution_mode: Literal["local", "docker"]
    sandbox_network: Literal["off", "ask", "on"]
    sandbox_image: str = Field(min_length=1, max_length=256)


DeveloperRepositoryReviewInput = (
    DeveloperRepositoryRevisionInput
    | DeveloperRepositoryBranchInput
    | DeveloperRepositoryCommitInput
    | DeveloperRepositoryPullRequestInput
    | DeveloperRepositoryWorktreeCreateInput
    | DeveloperRepositoryWorktreePreserveInput
    | DeveloperRepositorySandboxInput
)


class DeveloperRepositoryReviewRequest(WireModel):
    action: DeveloperRepositoryAction
    payload: DeveloperRepositoryReviewInput


class DeveloperRepositoryReview(WireModel):
    schema_version: Literal[1]
    action: DeveloperRepositoryAction
    resource_id: str = Field(min_length=1, max_length=128)
    conversation_id: str = Field(min_length=1, max_length=256)
    binding_id: str = Field(min_length=1, max_length=128)
    binding_revision: str = Field(min_length=1, max_length=128)
    resource_revision: str = Field(min_length=1, max_length=128)
    revision: DeveloperRepositoryRevision
    policy_action: str = Field(min_length=1, max_length=128)
    policy_decision: Literal["allow", "ask", "block"]
    approval_required: bool
    disclosures: list[str] = Field(min_length=1, max_length=8)
    action_digest: DeveloperRepositoryRevision
    review_id: str = Field(min_length=1, max_length=128)


class DeveloperRepositoryRevisionPayload(DeveloperRepositoryRevisionInput):
    nonce: str = Field(min_length=1, max_length=128)


class DeveloperRepositoryBranchPayload(DeveloperRepositoryBranchInput):
    nonce: str = Field(min_length=1, max_length=128)


class DeveloperRepositoryCommitPayload(DeveloperRepositoryCommitInput):
    nonce: str = Field(min_length=1, max_length=128)


class DeveloperRepositoryPullRequestPayload(DeveloperRepositoryPullRequestInput):
    nonce: str = Field(min_length=1, max_length=128)


class DeveloperRepositoryWorktreeCreatePayload(DeveloperRepositoryWorktreeCreateInput):
    nonce: str = Field(min_length=1, max_length=128)


class DeveloperRepositoryWorktreePreservePayload(
    DeveloperRepositoryWorktreePreserveInput
):
    nonce: str = Field(min_length=1, max_length=128)


class DeveloperRepositorySandboxPayload(DeveloperRepositorySandboxInput):
    nonce: str = Field(min_length=1, max_length=128)


class DeveloperRepositoryReceipt(WireModel):
    schema_version: Literal[1]
    command_id: UUID
    action: DeveloperRepositoryAction
    resource_id: str = Field(min_length=1, max_length=128)
    conversation_id: str = Field(min_length=1, max_length=256)
    status: Literal["completed", "partial", "rejected"]
    code: str | None = Field(max_length=128)
    revision: DeveloperRepositoryRevision | None
    worktree_id: str | None = Field(max_length=128)
    external_url: str | None = Field(max_length=2048)


class DocumentControlInput(WireModel):
    target_id: KnowledgeId
    revision: KnowledgeRevision


class DocumentControlTarget(WireModel):
    id: KnowledgeId
    revision: KnowledgeRevision


class DocumentClearInput(WireModel):
    targets: list[DocumentControlTarget] = Field(min_length=1, max_length=50)


class DocumentControlReviewRequest(WireModel):
    action: DocumentControlAction
    payload: DocumentControlInput | DocumentClearInput


class DocumentControlReview(WireModel):
    schema_version: Literal[1]
    action: DocumentControlAction
    target_id: KnowledgeId | None
    batch_ids: list[KnowledgeId] = Field(min_length=1, max_length=50)
    revision: KnowledgeRevision
    intent_digest: KnowledgeRevision
    provider_work: bool
    retains_work: bool
    review_id: str = Field(min_length=1, max_length=128)


class DocumentControlPayload(DocumentControlInput):
    review_id: str = Field(min_length=1, max_length=128)


class DocumentClearPayload(DocumentClearInput):
    review_id: str = Field(min_length=1, max_length=128)


class DocumentControlReceipt(WireModel):
    command_id: UUID
    status: Literal["completed", "rejected", "partial"]
    code: Literal["document_outcome_uncertain"] | None = None
    outcome: (
        Literal["paused", "resumed", "cancellation_requested", "retried", "cleared"]
        | None
    ) = None
    target_id: KnowledgeId | None = None
    batch_ids: list[KnowledgeId] | None = Field(
        default=None, min_length=1, max_length=50
    )
    saved_status: str | None = Field(default=None, max_length=64)
    count: int | None = Field(default=None, ge=0, le=50)
    retained_work: bool | None = None


class RuntimeArchive(WireModel):
    version: str = Field(min_length=1, max_length=128)
    url: str = Field(min_length=1, max_length=2048)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=1, le=2 * 1024**3)
    system: str = Field(max_length=32)
    arch: str = Field(max_length=32)
    asset_name: str = Field(max_length=256)


class RuntimeInstallationSnapshot(WireModel):
    schema_version: Literal[1]
    runtime_id: Literal["node", "uv"]
    resource_revision: str | None = Field(pattern=r"^[0-9a-f]{64}$")
    availability: Literal["available", "unavailable", "missing", "recovery_required"]
    installed: bool | None
    active_command_id: UUID | None
    quiesced: bool | None


class RuntimeInstallationReviewRequest(WireModel):
    runtime_id: Literal["node", "uv"]
    operation: Literal["resolve", "install"]
    resource_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_command_id: UUID | None


class RuntimeInstallationReview(RuntimeInstallationReviewRequest):
    schema_version: Literal[1]
    action_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    plan: RuntimeArchive | None
    disclosures: list[str] = Field(max_length=8)
    network_required: Literal[True]
    executes_runtime: Literal[False]
    nonce: str = Field(min_length=1, max_length=128)


class RuntimeInstallationPayload(WireModel):
    runtime_id: Literal["node", "uv"]
    source_command_id: UUID | None
    resource_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    action_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    nonce: str = Field(min_length=1, max_length=128)


class RuntimeInstallationCancelPayload(WireModel):
    runtime_id: Literal["node", "uv"]
    source_command_id: UUID


class RuntimeInstallationOutcome(WireModel):
    runtime_id: Literal["node", "uv"]
    operation: Literal["resolve", "install"]
    stage: str = Field(min_length=1, max_length=64)
    cancel_requested: bool
    quiesced: bool | None
    installed: bool | None
    plan: RuntimeArchive | None


class RuntimeInstallationReceipt(WireModel):
    command_id: UUID
    status: Literal["accepted", "completed", "partial", "rejected"]
    code: str | None = Field(default=None, max_length=128)
    installation: RuntimeInstallationOutcome


class McpCatalogTool(WireModel):
    tool_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    name: str = Field(max_length=128)
    enabled_after_accept: bool | None
    requires_approval: bool
    destructive: bool


class McpTestedCatalogPage(WireModel):
    schema_version: Literal[1]
    configuration_revision: str | None = Field(pattern=r"^[0-9a-f]{64}$")
    server_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    test_command_id: UUID
    availability: Literal["available", "recovery_required", "stale", "unavailable"]
    manual_selection_required: bool | None
    items: list[McpCatalogTool] = Field(max_length=50)
    total: int | None = Field(ge=0, le=1000)
    next_cursor: str | None = Field(max_length=2048)


class McpCatalogRequest(WireModel):
    configuration_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    server_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    test_command_id: UUID


class McpCatalogPayload(McpCatalogRequest):
    nonce: str = Field(min_length=1, max_length=128)


class McpCatalogReview(McpCatalogRequest):
    operation: Literal["accept_catalog"]
    action_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    tool_count: int = Field(ge=0, le=1000)
    manual_selection_required: bool
    saved_disabled: None
    nonce: str = Field(min_length=1, max_length=128)


class McpPolicyTool(WireModel):
    tool_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    name: str = Field(max_length=128)
    enabled: bool | None
    requires_approval: bool | None
    approval_locked: bool
    destructive: bool | None


class McpPolicyPage(WireModel):
    schema_version: Literal[1]
    revision: str | None = Field(pattern=r"^[0-9a-f]{64}$")
    server_id: str | None = Field(pattern=r"^[0-9a-f]{64}$")
    availability: Literal[
        "available",
        "missing",
        "unavailable",
        "recovery_required",
        "not_found",
        "partial",
    ]
    global_enabled: bool | None
    server_enabled: bool | None
    resources_enabled: bool | None
    prompts_enabled: bool | None
    items: list[McpPolicyTool] = Field(max_length=50)
    total: int | None = Field(ge=0, le=10000)
    next_cursor: str | None = Field(max_length=2048)


class McpGlobalPolicyIntent(WireModel):
    operation: Literal["global_enabled"]
    enabled: bool


class McpServerPolicyIntent(WireModel):
    operation: Literal["server_enabled"]
    server_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    enabled: bool


class McpToolPolicyIntent(WireModel):
    operation: Literal["tool_enabled", "tool_approval"]
    server_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    tool_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    enabled: bool


class McpUtilityPolicyIntent(WireModel):
    operation: Literal["utility_enabled"]
    server_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    utility: Literal["resources", "prompts"]
    enabled: bool


class McpPolicyRequest(WireModel):
    configuration_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    intent: (
        McpGlobalPolicyIntent
        | McpServerPolicyIntent
        | McpToolPolicyIntent
        | McpUtilityPolicyIntent
    )


class McpPolicyPayload(McpPolicyRequest):
    nonce: str = Field(min_length=1, max_length=128)


class McpPolicyReview(WireModel):
    configuration_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    operation: Literal[
        "global_enabled",
        "server_enabled",
        "tool_enabled",
        "tool_approval",
        "utility_enabled",
    ]
    action_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    server_ids: list[Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]] = (
        Field(max_length=1)
    )
    saved_disabled: None
    nonce: str = Field(min_length=1, max_length=128)


class McpRuntimeState(WireModel):
    schema_version: Literal[1]
    server_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    configuration_revision: str | None = Field(pattern=r"^[0-9a-f]{64}$")
    cleanup_revision: str | None = Field(pattern=r"^[0-9a-f]{64}$")
    availability: Literal["available", "missing", "unavailable", "recovery_required"]
    runtime_id: UUID | None
    state: Literal[
        "not_started",
        "connecting",
        "connected",
        "stopping",
        "stopped",
        "failed",
        "dependency_missing",
        "cleanup_incomplete",
        "unknown",
        "missing",
    ]
    session_quiesced: bool | None


class McpRuntimeReviewRequest(WireModel):
    resource_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    server_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    operation: Literal["connect", "test", "disconnect"]
    expected_runtime_id: UUID | None


class McpRuntimePayload(McpRuntimeReviewRequest):
    nonce: str = Field(min_length=1, max_length=128)


class McpRuntimeReview(WireModel):
    resource_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    server_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    operation: Literal["connect", "test", "disconnect"]
    runtime_id: UUID | None
    action_digest: str = Field(min_length=1, max_length=128)
    nonce: str = Field(min_length=1, max_length=128)


class McpRuntimeOutcome(WireModel):
    schema_version: Literal[1]
    server_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    operation: Literal["connect", "test", "disconnect"]
    runtime_id: UUID | None
    state: Literal[
        "not_started",
        "connecting",
        "connected",
        "stopping",
        "stopped",
        "failed",
        "dependency_missing",
        "cleanup_incomplete",
        "unknown",
        "tested",
    ]
    session_quiesced: bool | None
    code: (
        Literal[
            "mcp_cleanup_incomplete", "mcp_runtime_unconfirmed", "mcp_connection_failed"
        ]
        | None
    )


class ProviderCredentialState(WireModel):
    schema_version: Literal[1] = 1
    provider_id: str = Field(min_length=1, max_length=160)
    revision: str = Field(min_length=64, max_length=64)
    configured: bool
    source: Literal[
        "environment",
        "secret_file",
        "conflict",
        "session",
        "keyring",
        "encrypted_file",
        "api_keys",
        "unknown",
    ]
    externally_managed: bool
    storage_unavailable: bool
    recovery_available: bool
    runtime_state: Literal["unknown"] = "unknown"


class ProviderSettingsSnapshot(ProviderCredentialState):
    display_name: str = Field(max_length=256)


class ProviderSettingsReviewRequest(WireModel):
    provider_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    operation: Literal["save", "clear", "restore"]
    value: str | None = Field(default=None, max_length=16384)


class ProviderSettingsReview(WireModel):
    provider_id: OpaqueId
    provider_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    operation: Literal["save", "clear", "restore"]
    action_digest: str = Field(min_length=1, max_length=128)
    nonce: str = Field(min_length=1, max_length=128)
    snapshot: ProviderSettingsSnapshot


class ProviderSettingsReceipt(WireModel):
    command_id: UUID
    status: Literal["completed", "rejected", "uncertain"]
    published: bool
    credential: ProviderSettingsSnapshot


class ProviderCredentialPayload(WireModel):
    provider_id: OpaqueId
    provider_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    nonce: str = Field(min_length=1, max_length=128)


class ProviderCredentialSavePayload(ProviderCredentialPayload):
    value: str = Field(min_length=1, max_length=16384)


class ProviderStatusSnapshot(WireModel):
    schema_version: Literal[1] = 1
    revision: str = Field(min_length=1, max_length=128)
    generated_at: float | None = None
    freshness: Literal["fresh", "stale", "unavailable"]
    refresh_running: bool
    providers: list[ProviderStatusRow] = Field(max_length=512)
    total_models: int = Field(ge=0)


class ProviderLiveCard(WireModel):
    provider_id: OpaqueId
    display_name: str = Field(max_length=256)
    group: Literal["local", "subscription", "api", "custom"]
    icon: str = Field(max_length=32)
    configured: bool
    source: str = Field(max_length=80)
    runtime_enabled: bool
    model_count: int | None = Field(default=None, ge=0)
    model_count_source: str = Field(max_length=80)
    chat_count: int = Field(ge=0)
    media_count: int = Field(ge=0)
    plan_type: str = Field(max_length=80)
    fingerprint: str = Field(max_length=80)
    account_id_hash: str = Field(max_length=80)
    user_hash: str = Field(max_length=80)
    oauth_client_id_fingerprint: str = Field(max_length=80)
    oauth_client_id_configured: bool
    external_reference_exists: bool
    reconnect_required: bool = False
    risk_label: str = Field(max_length=80)
    last_runtime_probe_ok: bool | None = None


class ProviderLiveSnapshot(WireModel):
    schema_version: Literal[1] = 1
    providers: list[ProviderLiveCard] = Field(max_length=512)


class ProviderCatalogRefresh(WireModel):
    running: bool
    started: bool
    provider_id: str = Field(default="", max_length=80)
    ok: bool | None = None
    model_count: int | None = Field(default=None, ge=0)
    message: str = Field(default="", max_length=256)


class ProviderRuntimeProbe(WireModel):
    provider_id: Literal["claude_subscription", "xai_oauth"]
    ok: bool
    detail: str = Field(max_length=256)


class CachedReasoning(WireModel):
    supported_efforts: list[Annotated[str, StringConstraints(max_length=80)]] = Field(
        max_length=32
    )
    default_effort: str | None = Field(default=None, max_length=80)
    default_enabled: bool | None = None
    can_disable: bool
    mandatory: bool
    supports_budget: bool
    budget_min: int | None = Field(default=None, ge=0)
    budget_max: int | None = Field(default=None, ge=0)
    thinking_mode: Literal["none", "toggle", "manual"]


class CachedModelRow(WireModel):
    provider_id: str = Field(min_length=1, max_length=160)
    model_id: str = Field(min_length=1, max_length=512)
    selection_ref: str = Field(min_length=1, max_length=647)
    display_name: str = Field(max_length=256)
    provider_display_name: str = Field(max_length=256)
    categories: list[Annotated[str, StringConstraints(max_length=80)]] = Field(
        max_length=32
    )
    input_modalities: list[Annotated[str, StringConstraints(max_length=80)]] = Field(
        max_length=32
    )
    output_modalities: list[Annotated[str, StringConstraints(max_length=80)]] = Field(
        max_length=32
    )
    tool_calling: bool | None = None
    reasoning: CachedReasoning | None = None
    context_window: int | None = Field(default=None, ge=1)
    installed: bool | None = None
    pinned_surfaces: list[Annotated[str, StringConstraints(max_length=80)]] = Field(
        max_length=32
    )
    runtime_state: Literal["unknown"] = "unknown"


class CachedModelPage(WireModel):
    schema_version: Literal[1] = 1
    revision: str = Field(min_length=1, max_length=128)
    generated_at: float | None = None
    freshness: Literal["fresh", "stale", "unavailable"]
    items: list[CachedModelRow] = Field(max_length=100)
    total: int = Field(ge=0)
    next_cursor: str | None = Field(default=None, max_length=2048)


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


ArtifactMode = Literal["deck", "document", "landing", "app_mockup", "storyboard"]


class ArtifactSetupPayload(WireModel):
    mode: ArtifactMode = "deck"
    template_id: str = Field(default="", max_length=128)
    aspect_ratio: str = Field(default="", max_length=32)
    name: str = Field(default="", max_length=120)
    brief: str = Field(default="", max_length=16000)


class EmptyWorkspaceSetupPayload(WireModel):
    folder_name: str = Field(min_length=1, max_length=120)


class ResourceSetupPayload(WireModel):
    kind: Literal["artifact", "workspace"]
    intent: Literal["create", "open", "add", "repair", "new_conversation"]
    resource_id: OpaqueId | None = None
    expected_resource_revision: str | None = Field(default=None, max_length=128)
    expected_origin_id: OpaqueId | None = None
    deck: DeckSetupPayload | None = None
    artifact: ArtifactSetupPayload | None = None
    empty_workspace: EmptyWorkspaceSetupPayload | None = None
    folder_grant: OpaqueId | None = None

    @model_validator(mode="after")
    def typed_setup(self) -> ResourceSetupPayload:
        if self.artifact is not None and (
            self.kind != "artifact"
            or self.intent != "create"
            or self.deck is not None
            or self.folder_grant is not None
            or self.resource_id is not None
            or self.empty_workspace is not None
        ):
            raise ValueError(
                "Artifact creation has one typed setup and no existing resource."
            )
        if self.empty_workspace is not None and (
            self.kind != "workspace"
            or self.intent != "create"
            or self.folder_grant is None
            or self.resource_id is not None
            or self.deck is not None
            or self.artifact is not None
        ):
            raise ValueError(
                "Empty workspace creation requires an explicit parent grant and name."
            )
        return self


class SetupContinuePayload(WireModel):
    setup_command_id: UUID
    expected_resource_revision: str | None = Field(
        default=None, min_length=1, max_length=128
    )
    expected_origin_id: OpaqueId | None = None
    folder_grant: OpaqueId | None = None

    @model_validator(mode="after")
    def recovery_target(self) -> SetupContinuePayload:
        if self.expected_resource_revision is None and self.folder_grant is None:
            raise ValueError(
                "Continuation requires a resource revision or renewed folder grant."
            )
        return self


class ArtifactEditPayload(WireModel):
    target: WriteTarget
    operation: Literal["project_properties", "page_properties", "text", "restore"]
    page_id: OpaqueId | None = None
    name: str | None = Field(default=None, max_length=256)
    title: str | None = Field(default=None, max_length=256)
    notes: str | None = Field(default=None, max_length=32768)
    element_id: str | None = Field(default=None, min_length=1, max_length=256)
    text: str | None = Field(default=None, max_length=32768)
    snapshot_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=40,
        pattern=r"^[0-9]{1,20}(\.[0-9]{1,12})?$",
    )

    @model_validator(mode="after")
    def artifact_target(self) -> ArtifactEditPayload:
        if self.target.kind != "artifact":
            raise ValueError("Artifact edits require an artifact target.")
        return self


class TaskEditableFields(WireModel):
    name: str = Field(min_length=1, max_length=256)
    description: str = Field(max_length=4096)
    icon: str = Field(max_length=32)
    prompts: list[str] = Field(max_length=100)
    enabled: bool
    schedule: str | None = Field(max_length=256)
    at: str | None = Field(max_length=80)
    notify_only: bool
    notify_label: str = Field(max_length=4096)
    channels: list[OpaqueId] | None = Field(max_length=32)

    @model_validator(mode="after")
    def prompt_budget(self) -> TaskEditableFields:
        if (
            any(not text.strip() or len(text) > 16384 for text in self.prompts)
            or sum(map(len, self.prompts)) > 65536
        ):
            raise ValueError("Task prompts exceed the editing budget.")
        return self


class TaskEditorSnapshot(WireModel):
    id: OpaqueId
    revision: str = Field(min_length=1, max_length=128)
    fields: TaskEditableFields
    advanced: bool
    agent_profile_id: str = Field(max_length=128)
    approval_mode: str = Field(max_length=64)
    conversation_id: OpaqueId | None
    legacy_delivery: bool


class TaskSaveResult(WireModel):
    task: TaskEditorSnapshot
    created: bool
    replayed: bool


class TaskRunReview(WireModel):
    task_id: OpaqueId
    task_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    policy_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    agent_profile_id: str = Field(max_length=128)
    approval_mode: str = Field(max_length=64)
    notify_only: bool
    steps_total: int = Field(ge=0)
    conversation_id: OpaqueId | None


class TaskRunSummary(WireModel):
    id: OpaqueId
    task_id: OpaqueId
    conversation_id: str = Field(max_length=128)
    status: str = Field(max_length=80)
    started_at: str = Field(max_length=80)
    finished_at: str | None = Field(max_length=80)
    steps_total: int = Field(ge=0)
    steps_done: int = Field(ge=0)


class TaskRunPage(WireModel):
    task_id: OpaqueId
    revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    items: list[TaskRunSummary] = Field(max_length=100)
    next_cursor: str | None = Field(max_length=2048)
    total: int = Field(ge=0)


class TaskRunResult(WireModel):
    run: TaskRunSummary
    replayed: bool


class TaskApprovalReview(WireModel):
    id: OpaqueId
    task_id: OpaqueId
    run_id: OpaqueId
    revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    message: str = Field(max_length=16384)
    requested_at: str = Field(max_length=80)
    expires_at: str | None = Field(max_length=80)
    approval_mode: str = Field(max_length=64)
    message_truncated: bool
    response_available: bool
    nonce: str | None = Field(default=None, max_length=128)


class TaskApprovalPage(WireModel):
    items: list[TaskApprovalReview] = Field(max_length=100)
    total: int = Field(ge=0)
    revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    next_cursor: str | None = Field(max_length=2048)


class TaskApprovalResult(WireModel):
    approval_id: OpaqueId
    decision: Literal["approved", "denied"]
    run: TaskRunSummary


class TaskStopResult(WireModel):
    run: TaskRunSummary
    stop_requested: bool
    quiesced: bool


class DesignBrand(WireModel):
    primary_color: str = Field(max_length=128)
    secondary_color: str = Field(max_length=128)
    accent_color: str = Field(max_length=128)
    bg_color: str = Field(max_length=128)
    text_color: str = Field(max_length=128)
    heading_font: str = Field(max_length=128)
    body_font: str = Field(max_length=128)
    logo_asset_id: str = Field(max_length=128)
    logo_mode: Literal["auto", "manual"]
    logo_scope: Literal["all", "first"]
    logo_position: Literal["top_left", "top_right", "bottom_left", "bottom_right"]
    logo_max_height: int = Field(ge=0, le=16384)
    logo_padding: int = Field(ge=0, le=16384)


class DesignElement(WireModel):
    id: str = Field(max_length=256)
    tag: str = Field(max_length=128)
    styles: dict[
        Annotated[str, StringConstraints(max_length=128)],
        Annotated[str, StringConstraints(max_length=256)],
    ] = Field(max_length=32)
    action: str = Field(max_length=256)


class DesignControlItem(WireModel):
    id: str = Field(max_length=256)
    label: str = Field(max_length=256)
    kind: str = Field(max_length=128)
    detail: str = Field(max_length=2048)
    available: bool


class DesignControlsState(WireModel):
    resource_id: OpaqueId
    resource_revision: str = Field(max_length=128)
    mode: ArtifactMode
    page_id: OpaqueId
    brand: DesignBrand
    element: DesignElement | None
    section: Literal["elements", "assets", "fonts", "presets", "interactions"]
    items: list[DesignControlItem] = Field(max_length=50)
    item_count: int = Field(ge=0)
    next_cursor: str | None = Field(max_length=2048)


class DesignReviewFinding(WireModel):
    id: str = Field(max_length=256)
    source: str = Field(max_length=128)
    category: str = Field(max_length=128)
    severity: str = Field(max_length=128)
    message: str = Field(max_length=2048)
    suggested_fix: str = Field(max_length=2048)
    page_id: OpaqueId
    auto_fixable: bool


class DesignReviewState(WireModel):
    resource_id: OpaqueId
    resource_revision: str = Field(max_length=128)
    page_id: OpaqueId
    scope: Literal["page", "project"]
    heuristic: bool
    score: int = Field(ge=0, le=100)
    findings: list[DesignReviewFinding] = Field(max_length=50)
    finding_count: int = Field(ge=0)
    next_cursor: str | None = Field(max_length=2048)


class DesignPresentationPage(WireModel):
    id: OpaqueId
    title: str = Field(max_length=256)
    index: int = Field(ge=0)


class ArtifactDesignControlPayload(WireModel):
    target: WriteTarget
    operation: Literal[
        "brand",
        "preset",
        "style",
        "hotspot",
        "review_fix",
        "asset_insert",
        "asset_remove",
        "asset_forget",
    ]
    parameters: dict[str, Any]
    page_id: OpaqueId | None = None
    element_id: OpaqueId | None = None


class ArtifactAssetUploadPayload(WireModel):
    target: WriteTarget
    upload_id: UUID
    filename: str = Field(min_length=1, max_length=240, pattern=r"^[^/\\:\x00\r\n]+$")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=1, le=26214400)


class ArtifactPresetIntent(WireModel):
    target: WriteTarget
    action: Literal["save", "delete"]
    name: str | None = Field(default=None, min_length=1, max_length=256)
    preset_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class ArtifactPresetPayload(ArtifactPresetIntent):
    nonce: str = Field(min_length=1, max_length=128)


class ArtifactPresetReviewRequest(ArtifactPresetIntent):
    command_id: UUID


class ArtifactPresetReview(WireModel):
    nonce: str = Field(min_length=1, max_length=128)


class ArtifactReviewDraftRequest(WireModel):
    expected_revision: str = Field(min_length=1, max_length=128)
    page_id: OpaqueId
    finding_id: str = Field(min_length=1, max_length=256)


class ArtifactReviewDraft(WireModel):
    text: str = Field(max_length=8192)


class ArtifactDesignOutcome(WireModel):
    resource_id: OpaqueId
    resource_revision: str = Field(max_length=128)
    operation: str = Field(max_length=64)
    status: Literal["saved", "unchanged", "partial"]
    code: str = Field(max_length=80)
    asset_id: OpaqueId | None = None
    preset_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class DesignPresentationState(WireModel):
    resource_id: OpaqueId
    resource_revision: str = Field(max_length=128)
    page_id: OpaqueId
    title: str = Field(max_length=256)
    notes: str = Field(max_length=32768)
    page_index: int = Field(ge=0)
    page_count: int = Field(ge=0)
    pages: list[DesignPresentationPage] = Field(max_length=50)
    next_cursor: str | None = Field(max_length=2048)


class ArtifactExport(WireModel):
    export_id: UUID
    resource_id: OpaqueId
    resource_revision: str = Field(max_length=128)
    format: Literal["pdf", "html", "png", "pptx"]
    pptx_mode: Literal["screenshot", "structured"] | None
    page_count: int = Field(ge=1)
    filename: str = Field(min_length=1, max_length=240)
    media_type: str = Field(max_length=128)
    size_bytes: int = Field(ge=1, le=67108864)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    status: Literal["ready"]
    warnings: list[Literal["external_assets_unavailable"]] = Field(max_length=1)
    expires_at: float


class TaskCreatePayload(WireModel):
    fields: TaskEditableFields


class TaskGraphFields(WireModel):
    prompt: str | None = Field(default=None, max_length=16384)
    condition: str | None = Field(default=None, max_length=16384)
    message: str | None = Field(default=None, max_length=16384)
    next: str | None = Field(default=None, max_length=16384)
    if_true: str | None = Field(default=None, max_length=16384)
    if_false: str | None = Field(default=None, max_length=16384)
    if_approved: str | None = Field(default=None, max_length=16384)
    if_denied: str | None = Field(default=None, max_length=16384)
    on_error: str | None = Field(default=None, max_length=16384)
    task_id: str | None = Field(default=None, max_length=16384)
    channel: str | None = Field(default=None, max_length=16384)
    objective: str | None = Field(default=None, max_length=16384)
    profile: str | None = Field(default=None, max_length=16384)
    developer_workspace_id: str | None = Field(default=None, max_length=16384)
    editing_safety: str | None = Field(default=None, max_length=16384)
    return_mode: str | None = Field(default=None, max_length=16384)
    context: str | None = Field(default=None, max_length=16384)
    max_retries: int | None = Field(default=None, ge=1, le=10)
    retry_delay_seconds: int | None = Field(default=None, ge=0, le=300)
    timeout_minutes: int | None = Field(default=None, ge=0, le=1440)
    timeout_seconds: int | None = Field(default=None, ge=1, le=7200)
    pass_output: bool | None = None
    run_ids: list[Annotated[str, StringConstraints(max_length=128)]] | None = Field(
        default=None, max_length=100
    )


class TaskGraphStepEdit(WireModel):
    id: str = Field(min_length=1, max_length=128)
    type: str = Field(min_length=1, max_length=128)
    fields: TaskGraphFields


class TaskGraphStep(TaskGraphStepEdit):
    editable: bool
    retained_fields: bool


class TaskGraphSnapshot(WireModel):
    task_id: OpaqueId
    revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    steps: list[TaskGraphStep] = Field(max_length=100)
    notify_only: bool


class TaskGraphUpdatePayload(WireModel):
    task_id: OpaqueId
    task_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    steps: list[TaskGraphStepEdit] = Field(min_length=1, max_length=100)


class TaskSettingsFields(WireModel):
    concurrency_group: str | None = Field(max_length=128)
    trigger_type: Literal["none", "task_complete", "webhook"]
    trigger_task_id: str | None = Field(max_length=128)
    model_override: str | None = Field(max_length=1024)
    agent_profile_id: str = Field(min_length=1, max_length=128)
    approval_mode: Literal["block", "approve", "allow_all"]
    persistent_enabled: bool


class TaskSettingsSnapshot(WireModel):
    task_id: OpaqueId
    revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    fields: TaskSettingsFields
    profile_revision: str | None = Field(pattern=r"^[a-f0-9]{64}$")
    effective_approval_mode: Literal["block", "approve", "allow_all"] | None
    profile_available: bool
    webhook_configured: bool
    conversation_id: OpaqueId | None


class TaskSettingsUpdatePayload(WireModel):
    task_id: OpaqueId
    task_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    profile_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    fields: TaskSettingsFields


class TaskWebhookRotatePayload(WireModel):
    task_id: OpaqueId
    task_revision: str = Field(pattern=r"^[a-f0-9]{64}$")


class WorkspaceProcessInfo(WireModel):
    process_id: UUID
    command_id: UUID
    run_id: OpaqueId
    command: str = Field(max_length=4096)
    state: Literal[
        "starting", "running", "stopping", "exited", "failed", "cleanup_incomplete"
    ]
    exit_code: int | None
    quiesced: bool
    code: str = Field(default="", max_length=80)


class WorkspaceProcessSnapshot(WireModel):
    resource_id: OpaqueId
    conversation_id: OpaqueId
    resource_revision: str = Field(max_length=128)
    binding_id: OpaqueId
    binding_revision: Revision
    processes: list[WorkspaceProcessInfo] = Field(max_length=32)
    schema_version: Literal[1] = 1


class WorkspaceProcessRecoveryPage(WireModel):
    items: list[WorkspaceProcessInfo] = Field(max_length=32)
    next_cursor: str | None = Field(max_length=64)


class WorkspaceProcessOutputEntry(WireModel):
    sequence: int = Field(ge=1)
    channel: Literal["stdout", "stderr"]
    text: str = Field(max_length=8192)


class WorkspaceProcessOutput(WireModel):
    process_id: UUID
    entries: list[WorkspaceProcessOutputEntry] = Field(max_length=1024)
    next_cursor: int = Field(ge=0)
    truncated: bool
    quiesced: bool
    schema_version: Literal[1] = 1


class WorkspaceProcessReviewRequest(WireModel):
    command_id: UUID
    command: str = Field(min_length=1, max_length=4096)


class WorkspaceProcessReview(WorkspaceProcessReviewRequest):
    resource_id: OpaqueId
    conversation_id: OpaqueId
    resource_revision: str = Field(max_length=128)
    binding_id: OpaqueId
    binding_revision: Revision
    conversation_revision: Revision
    policy_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    policy_decision: Literal["allow", "ask", "block"]
    approval_required: bool
    action_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    nonce: str = Field(min_length=1, max_length=128)


class WorkspaceProcessStartPayload(WireModel):
    target: WriteTarget
    command: str = Field(min_length=1, max_length=4096)
    policy_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    action_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    nonce: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def workspace_target(self) -> WorkspaceProcessStartPayload:
        if self.target.kind != "workspace":
            raise ValueError("Invalid workspace process target.")
        return self


class WorkspaceProcessCleanupPayload(WireModel):
    target: WriteTarget
    process_id: UUID

    @model_validator(mode="after")
    def workspace_target(self) -> WorkspaceProcessCleanupPayload:
        if self.target.kind != "workspace":
            raise ValueError("Invalid workspace process target.")
        return self


class WorkspaceImportSummary(WireModel):
    pending_change_id: OpaqueId
    revision: KnowledgeRevision
    file_count: int = Field(ge=0, le=100)
    imported: bool
    created_at: str = Field(max_length=80)


class WorkspaceImportPage(WireModel):
    items: list[WorkspaceImportSummary] = Field(max_length=100)
    snapshot_revision: KnowledgeRevision
    next_cursor: str | None = Field(max_length=2048)
    total: int = Field(ge=0, le=9007199254740991)


class WorkspaceImportPatch(WireModel):
    pending_change_id: OpaqueId
    revision: KnowledgeRevision
    text: str = Field(max_length=16384)
    next_offset: int | None = Field(ge=0, le=1048576)


class WorkspaceImportReviewRequest(WireModel):
    pending_change_id: OpaqueId


class WorkspaceImportReviewState(WireModel):
    resource_id: OpaqueId
    conversation_id: OpaqueId
    resource_revision: str = Field(min_length=1, max_length=128)
    binding_id: OpaqueId
    binding_revision: Revision
    pending_change_id: OpaqueId
    pending_revision: KnowledgeRevision
    patch_digest: KnowledgeRevision
    host_revision: KnowledgeRevision
    git_policy_revision: KnowledgeRevision
    policy_revision: KnowledgeRevision
    policy_decision: Literal["allow", "ask", "block"]
    approval_required: bool
    files: list[Annotated[str, StringConstraints(min_length=1, max_length=4096)]] = (
        Field(max_length=100)
    )
    directories: list[
        Annotated[str, StringConstraints(min_length=1, max_length=4096)]
    ] = Field(default_factory=list, max_length=100)
    action_digest: KnowledgeRevision


class WorkspaceImportReview(WorkspaceImportReviewState):
    nonce: str = Field(min_length=1, max_length=128)


class WorkspaceImportPayload(WireModel):
    review: WorkspaceImportReviewState
    nonce: str = Field(min_length=1, max_length=128)


class WorkspaceImportResult(WireModel):
    command_id: UUID
    resource_id: OpaqueId
    conversation_id: OpaqueId
    pending_change_id: OpaqueId
    status: Literal["imported", "partial", "conflict", "denied"]
    files_applied: list[str] = Field(max_length=100)
    change_set_id: OpaqueId | None
    ledger_saved: bool
    imported: bool
    code: str = Field(default="", max_length=128)


class WorkspaceUndoReviewRequest(WireModel):
    change_set_id: OpaqueId


class WorkspaceUndoReviewState(WireModel):
    resource_id: OpaqueId
    conversation_id: OpaqueId
    resource_revision: str = Field(min_length=1, max_length=128)
    binding_id: OpaqueId
    binding_revision: Revision
    change_set_id: OpaqueId
    change_set_revision: KnowledgeRevision
    host_revision: KnowledgeRevision
    policy_revision: KnowledgeRevision
    policy_decision: Literal["allow", "ask", "block"]
    approval_required: bool
    files: list[Annotated[str, StringConstraints(min_length=1, max_length=4096)]] = (
        Field(max_length=100)
    )
    directories_retained: list[
        Annotated[str, StringConstraints(min_length=1, max_length=4096)]
    ] = Field(max_length=100)
    action_digest: KnowledgeRevision


class WorkspaceUndoReview(WorkspaceUndoReviewState):
    nonce: str = Field(min_length=1, max_length=128)


class WorkspaceUndoPayload(WireModel):
    review: WorkspaceUndoReviewState
    nonce: str = Field(min_length=1, max_length=128)


class WorkspaceUndoResult(WireModel):
    command_id: UUID
    resource_id: OpaqueId
    conversation_id: OpaqueId
    change_set_id: OpaqueId
    status: Literal["undone", "partial", "conflict", "denied"]
    files_restored: list[str] = Field(max_length=100)
    ledger_saved: bool
    reverted: bool
    code: str = Field(default="", max_length=128)


class WorkspaceEditableFile(WireModel):
    review_token: str = Field(pattern=r"^([0-9a-f]{64})?$")
    resource_id: OpaqueId
    conversation_id: OpaqueId
    relative_path: str = Field(min_length=1, max_length=4096)
    resource_revision: str = Field(max_length=128)
    binding_id: OpaqueId
    binding_revision: Revision
    target: Literal["workspace", "sandbox_shadow"]
    status: Literal[
        "text", "missing", "binary", "too_large", "denied", "sandbox_unprepared"
    ]
    content: str = Field(default="", max_length=204800)
    digest: str = Field(default="", max_length=64)
    size_bytes: int = Field(default=0, ge=0)
    code: str = Field(default="", max_length=80)
    schema_version: Literal[1] = 1


class WorkspaceEditResult(WireModel):
    resource_id: OpaqueId
    conversation_id: OpaqueId
    relative_path: str = Field(min_length=1, max_length=4096)
    resource_revision: str = Field(max_length=128)
    binding_id: OpaqueId
    binding_revision: Revision
    target: Literal["workspace", "sandbox_shadow"]
    status: Literal[
        "saved", "unchanged", "pending_import", "conflict", "denied", "partial"
    ]
    digest: str = Field(default="", max_length=64)
    change_set_id: OpaqueId | None = None
    pending_change_id: OpaqueId | None = None
    file_saved: bool = False
    ledger_saved: bool = False
    code: str = Field(default="", max_length=80)
    schema_version: Literal[1] = 1


class WorkspaceEditPayload(WireModel):
    review_token: str = Field(pattern=r"^[0-9a-f]{64}$")
    target: WriteTarget
    relative_path: str = Field(min_length=1, max_length=4096)
    content: str = Field(max_length=204800)
    file_digest: str = Field(pattern=r"^(missing|[a-f0-9]{64})$")

    @model_validator(mode="after")
    def workspace_target(self) -> WorkspaceEditPayload:
        if self.target.kind != "workspace":
            raise ValueError("Invalid workspace edit target.")
        return self


class ArtifactExportPayload(WireModel):
    target: WriteTarget
    format: Literal["pdf", "html", "png", "pptx"]
    pages: str = Field(default="all", min_length=1, max_length=256)
    pptx_mode: Literal["screenshot", "structured"] | None = None

    @model_validator(mode="after")
    def export_target(self) -> ArtifactExportPayload:
        if self.target.kind != "artifact" or (
            self.format != "pptx" and self.pptx_mode is not None
        ):
            raise ValueError("Invalid artifact export target or mode.")
        if self.format == "pptx" and self.pptx_mode is None:
            self.pptx_mode = "screenshot"
        return self


class ArtifactShareOptions(WireModel):
    action: Literal["publish", "channel", "x"]
    channel_name: str | None = Field(default=None, max_length=128)
    target: str | None = Field(default=None, max_length=1024)
    delivery: Literal["link", "slides", "pdf", "pptx", "html"] = "link"
    pages: str = Field(default="all", min_length=1, max_length=256)
    text: str = Field(default="", max_length=10000)
    pptx_mode: Literal["screenshot", "structured"] = "screenshot"
    remote: bool = False


class ArtifactShareChannel(WireModel):
    name: str = Field(min_length=1, max_length=128)
    label: str = Field(max_length=256)
    available: bool


class ArtifactShareChannels(WireModel):
    items: list[ArtifactShareChannel] = Field(max_length=50)
    revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    next_cursor: str | None = Field(max_length=256)


class ArtifactShareReview(WireModel):
    review_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    resource_id: OpaqueId
    resource_revision: str = Field(max_length=128)
    action: Literal["publish", "channel", "x"]
    channel_name: str | None = Field(max_length=128)
    recipient: str | None = Field(max_length=1024)
    delivery: Literal["link", "slides", "pdf", "pptx", "html"]
    pages: str = Field(max_length=256)
    page_count: int = Field(ge=0, le=200)
    remote: bool
    requires_pairing: bool
    nonce: str = Field(max_length=256)


class ArtifactShareOutcome(WireModel):
    status: Literal["published", "submitted", "partial", "uncertain", "denied"]
    code: str | None = Field(max_length=128)
    resource_id: OpaqueId
    resource_revision: str = Field(max_length=128)
    url: str | None = Field(max_length=4096)
    link_kind: Literal["local", "remote_access"] | None
    submitted_count: int = Field(ge=0, le=200)
    total_count: int = Field(ge=0, le=200)


class ArtifactShareProgress(WireModel):
    stage: Literal[
        "publication_prepared",
        "publish_file_started",
        "publish_file_completed",
        "publish_metadata_completed",
        "tunnel_started",
        "tunnel_completed",
        "tunnel_unavailable",
        "send_started",
        "send_submitted",
        "send_not_started",
        "send_uncertain",
    ]
    index: int = Field(ge=0, le=200)
    count: int = Field(ge=0, le=200)


class ArtifactSharePayload(WireModel):
    target: WriteTarget
    options: ArtifactShareOptions
    review_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    nonce: str = Field(max_length=256)

    @model_validator(mode="after")
    def share_target(self) -> ArtifactSharePayload:
        if self.target.kind != "artifact":
            raise ValueError("Invalid artifact sharing target.")
        return self


class TaskUpdatePayload(TaskCreatePayload):
    task_id: OpaqueId
    task_revision: str = Field(pattern=r"^[0-9a-f]{64}$")


class TaskRunPayload(WireModel):
    task_id: OpaqueId
    task_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_revision: str = Field(pattern=r"^[0-9a-f]{64}$")


class TaskStopPayload(WireModel):
    task_id: OpaqueId
    run_id: OpaqueId


class TaskApprovalPayload(TaskStopPayload):
    approval_id: OpaqueId
    approval_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    approved: bool
    nonce: str = Field(min_length=1, max_length=128)


class BuddyPreferences(WireModel):
    visible: bool
    collapsed: bool
    display_name: str = Field(min_length=1, max_length=128)
    personality: Literal[
        "warm_mystical",
        "calm_focus",
        "playful_helper",
        "quiet_guardian",
        "curious_scholar",
    ]
    bubble_verbosity: Literal["quiet", "normal", "chatty"]
    animation_intensity: Literal["quiet", "normal", "expressive"]
    pack_id: str = Field(min_length=1, max_length=128)


class BuddyPreferenceChanges(WireModel):
    visible: bool | None = None
    collapsed: bool | None = None
    display_name: str | None = Field(default=None, min_length=1, max_length=128)
    personality: (
        Literal[
            "warm_mystical",
            "calm_focus",
            "playful_helper",
            "quiet_guardian",
            "curious_scholar",
        ]
        | None
    ) = None
    bubble_verbosity: Literal["quiet", "normal", "chatty"] | None = None
    animation_intensity: Literal["quiet", "normal", "expressive"] | None = None
    pack_id: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def nonempty_values(self) -> BuddyPreferenceChanges:
        if not self.model_fields_set or any(
            getattr(self, name) is None for name in self.model_fields_set
        ):
            raise ValueError("Invalid Buddy preferences")
        return self


class BuddyStatus(WireModel):
    mood: str = Field(max_length=80)
    animation: str = Field(max_length=80)
    energy: int = Field(ge=0, le=100)
    focus: int = Field(ge=0, le=100)
    alert: int = Field(ge=0, le=100)
    event_id: int = Field(ge=0)
    label: str = Field(max_length=256)


class BuddySnapshot(WireModel):
    schema_version: Literal[1]
    revision: str = Field(max_length=128)
    preferences: BuddyPreferences
    status: BuddyStatus
    placement: Literal["docked"]
    native_placement_retained: bool


class BuddyAsset(WireModel):
    id: str = Field(min_length=1, max_length=128)
    content_type: Literal["image/png", "video/mp4", "application/octet-stream"]


class BuddyPack(WireModel):
    id: str = Field(min_length=1, max_length=128)
    name: str = Field(max_length=256)
    revision: str = Field(max_length=128)
    runtime: str = Field(max_length=80)
    available: bool
    generated: bool
    assets: list[BuddyAsset] = Field(max_length=16)
    animation_map: dict[str, str]


class BuddyPackPage(WireModel):
    revision: str = Field(max_length=128)
    total: int = Field(ge=0, le=8192)
    packs: list[BuddyPack] = Field(max_length=50)
    next_cursor: str | None = Field(max_length=2048)


class BuddyHatchRequest(WireModel):
    action: Literal["full", "motion", "still", "remove"]
    prompt: str = Field(min_length=1, max_length=4000)
    config_revision: str = Field(min_length=1, max_length=128)
    source_pack_id: str | None = Field(default=None, min_length=1, max_length=128)
    source_pack_revision: str | None = Field(default=None, min_length=1, max_length=128)
    source_command_id: UUID | None = None


class BuddyHatchReview(WireModel):
    review_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    action: Literal["full", "motion", "still", "remove"]
    config_revision: str = Field(max_length=128)
    image_model: str | None = Field(max_length=256)
    video_model: str | None = Field(max_length=256)
    provider_calls: int = Field(ge=0, le=7)
    nonce: str = Field(min_length=1, max_length=128)


class BuddyHatchResult(WireModel):
    schema_version: Literal[1]
    command_id: UUID
    job_id: str = Field(max_length=128)
    status: str = Field(max_length=64)
    stage: str = Field(max_length=80)
    pack_id: str | None = Field(max_length=128)
    selected: bool
    completed_clips: int = Field(ge=0, le=6)
    total_clips: int = Field(ge=0, le=6)
    code: str = Field(max_length=80)
    retained_copy: bool
    has_still: bool


class BuddyRemoval(WireModel):
    status: Literal["removed"]
    pack_id: str = Field(max_length=128)
    retained_copy: Literal[True]
    config_changed: bool


class BuddyReceipt(WireModel):
    command_id: UUID
    status: Literal["admitting", "accepted", "completed", "partial", "rejected"]
    code: str | None = Field(default=None, max_length=80)
    buddy_revision: str | None = Field(default=None, max_length=128)
    hatch: BuddyHatchResult | None = None
    removal: BuddyRemoval | None = None
    cancel_requested: bool | None = None


class BuddyUpdatePayload(WireModel):
    changes: BuddyPreferenceChanges
    config_revision: str = Field(min_length=1, max_length=128)


class BuddyHatchPayload(WireModel):
    request: BuddyHatchRequest
    review_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    nonce: str = Field(min_length=1, max_length=128)


class BuddyCancelPayload(WireModel):
    source_command_id: UUID
    job_id: str = Field(min_length=1, max_length=128)


class Command(WireModel):
    command_id: UUID
    client_session_id: UUID
    type: Literal[
        "conversation.create",
        "conversation.rename",
        "conversation.pin",
        "conversation.delete",
        "conversation.submit",
        "conversation.stop",
        "conversation.steer",
        "conversation.resume",
        "conversation.bind",
        "conversation.unbind",
        "approval.resolve",
        "conversation.controls",
        "resource.setup",
        "resource.continue",
        "conversation.queue.edit",
        "conversation.queue.remove",
        "conversation.queue.dispatch",
        "artifact.edit",
        "task.create",
        "task.update",
        "task.graph.update",
        "task.settings.update",
        "task.webhook.rotate",
        "task.run",
        "task.stop",
        "task.approval",
        "artifact.export",
        "artifact.share",
        "workspace.edit",
        "workspace.import",
        "workspace.undo",
        "workspace.process.start",
        "workspace.process.stop",
        "workspace.process.recover",
        "provider.credential.save",
        "provider.credential.clear",
        "provider.credential.restore",
        "provider.custom_credential.save",
        "provider.custom_credential.clear",
        "provider.custom_credential.restore",
        "provider.default_model.save",
        "mcp.configuration.save",
        "mcp.runtime.control",
        "mcp.runtime.resolve",
        "mcp.runtime.install",
        "mcp.runtime.install.cancel",
        "document.batch.pause",
        "document.batch.resume",
        "document.batch.cancel",
        "document.job.cancel",
        "document.job.retry",
        "document.jobs.clear_finished",
        "document.upload",
        "document.batch.process",
        "wiki.configure",
        "wiki.publish",
        "wiki.rebuild",
        "wiki.import",
        "wiki.sync",
        "channel.control",
        "plugin.enable",
        "plugin.disable",
        "plugin.configure",
        "skill.preference",
        "skill.create",
        "skill.import",
        "skill.edit",
        "skill.duplicate",
        "skill.delete",
        "skill.proposal.apply",
        "skill.proposal.reject",
        "goal.control",
        "profile.mutate",
        "developer.repository.branch.create",
        "developer.repository.branch.switch",
        "developer.repository.commit",
        "developer.repository.push",
        "developer.repository.pull_request",
        "developer.repository.worktree.create",
        "developer.repository.worktree.preserve",
        "developer.repository.sandbox.configure",
        "developer.repository.sandbox.rebuild",
        "developer.repository.sandbox.cleanup",
        "browser.navigate",
        "browser.take_over",
        "browser.check",
        "browser.back",
        "browser.end",
        "mcp.catalog.accept",
        "knowledge.create",
        "knowledge.edit",
        "knowledge.archive",
        "knowledge.restore",
        "knowledge.resolve",
        "knowledge.relation.add",
        "knowledge.relation.remove",
        "knowledge.supersede",
        "provider.subscription.start",
        "provider.subscription.check",
        "provider.subscription.submit",
        "provider.subscription.cancel",
        "provider.subscription.disconnect",
        "provider.subscription.restore",
        "provider.subscription.import_token",
        "provider.subscription.reference",
        "provider.subscription.client_id.save",
        "provider.subscription.client_id.reset",
        "provider.subscription.probe",
        "document.remove",
        "document.removal.retry",
        "buddy.update",
        "buddy.hatch",
        "buddy.remove",
        "buddy.cancel",
        "mcp.configuration.control",
        "provider.endpoint.create",
        "provider.endpoint.save",
        "provider.endpoint.delete",
        "provider.endpoint.probe",
        "provider.endpoint.refresh",
        "provider.model.pin",
        "provider.model.unpin",
        "artifact.design.control",
        "artifact.asset.upload",
        "artifact.preset.mutate",
    ]
    expected_revision: Revision
    payload: dict

    @model_validator(mode="after")
    def typed_payload(self) -> Command:
        # JSON validation permits UUID wire strings while Python callers remain strict.
        import json

        payload_type = COMMAND_PAYLOADS[self.type]
        supplied = self.payload
        self.payload = payload_type.model_validate_json(
            json.dumps(supplied)
        ).model_dump(mode="json", exclude_unset=self.type == "mcp.configuration.save")
        if self.type == "conversation.submit" and "write_targets" not in supplied:
            self.payload.pop(
                "write_targets", None
            )  # Preserve existing durable v1 command verifiers.
        if self.type == "conversation.controls" and "reasoning" not in supplied:
            self.payload.pop("reasoning", None)
        if self.type == "resource.setup":
            for field in ("artifact", "empty_workspace"):
                if field not in supplied:
                    self.payload.pop(field, None)
        if self.type == "resource.continue" and "folder_grant" not in supplied:
            self.payload.pop("folder_grant", None)
        return self

    @classmethod
    def model_json_schema(cls, **kwargs: Any) -> dict:
        return _variant_schema(cls, COMMAND_PAYLOADS, **kwargs)


COMMAND_PAYLOADS = {
    "buddy.update": BuddyUpdatePayload,
    "buddy.hatch": BuddyHatchPayload,
    "buddy.remove": BuddyHatchPayload,
    "buddy.cancel": BuddyCancelPayload,
    "conversation.create": CreatePayload,
    "conversation.rename": RenamePayload,
    "conversation.pin": PinPayload,
    "conversation.delete": EmptyPayload,
    "conversation.submit": SubmitPayload,
    "conversation.stop": StopPayload,
    "conversation.steer": SteerPayload,
    "conversation.resume": ResumePayload,
    "conversation.bind": BindPayload,
    "conversation.unbind": UnbindPayload,
    "approval.resolve": ApprovalPayload,
    "conversation.controls": ConversationControls,
    "resource.setup": ResourceSetupPayload,
    "resource.continue": SetupContinuePayload,
    "conversation.queue.edit": QueueEditPayload,
    "conversation.queue.remove": QueueItemCommand,
    "conversation.queue.dispatch": QueueItemCommand,
    "artifact.edit": ArtifactEditPayload,
    "artifact.export": ArtifactExportPayload,
    "artifact.share": ArtifactSharePayload,
    "task.graph.update": TaskGraphUpdatePayload,
    "task.create": TaskCreatePayload,
    "task.update": TaskUpdatePayload,
    "task.settings.update": TaskSettingsUpdatePayload,
    "task.webhook.rotate": TaskWebhookRotatePayload,
    "task.run": TaskRunPayload,
    "task.stop": TaskStopPayload,
    "task.approval": TaskApprovalPayload,
    "workspace.edit": WorkspaceEditPayload,
    "workspace.import": WorkspaceImportPayload,
    "workspace.undo": WorkspaceUndoPayload,
    "workspace.process.start": WorkspaceProcessStartPayload,
    "workspace.process.stop": WorkspaceProcessCleanupPayload,
    "workspace.process.recover": WorkspaceProcessCleanupPayload,
    "provider.credential.save": ProviderCredentialSavePayload,
    "provider.credential.clear": ProviderCredentialPayload,
    "provider.credential.restore": ProviderCredentialPayload,
    "provider.custom_credential.save": ProviderCredentialSavePayload,
    "provider.custom_credential.clear": ProviderCredentialPayload,
    "provider.custom_credential.restore": ProviderCredentialPayload,
    "provider.default_model.save": DefaultModelPayload,
    "mcp.configuration.save": McpConfigurationPayload,
    "mcp.configuration.control": McpPolicyPayload,
    "mcp.runtime.control": McpRuntimePayload,
    "mcp.runtime.resolve": RuntimeInstallationPayload,
    "mcp.runtime.install": RuntimeInstallationPayload,
    "mcp.runtime.install.cancel": RuntimeInstallationCancelPayload,
    "document.batch.pause": DocumentControlPayload,
    "document.batch.resume": DocumentControlPayload,
    "document.batch.cancel": DocumentControlPayload,
    "document.job.cancel": DocumentControlPayload,
    "document.job.retry": DocumentControlPayload,
    "document.jobs.clear_finished": DocumentClearPayload,
    "document.upload": DocumentUploadPayload,
    "document.batch.process": DocumentProcessingPayload,
    "wiki.configure": WikiConfigurePayload,
    "wiki.publish": WikiPublishPayload,
    "wiki.rebuild": WikiRebuildPayload,
    "wiki.import": WikiImportPayload,
    "wiki.sync": WikiImportPayload,
    "channel.control": ChannelActionPayload,
    "plugin.enable": PluginTogglePayload,
    "plugin.disable": PluginTogglePayload,
    "plugin.configure": PluginConfigurePayload,
    "skill.preference": SkillPreferencePayload,
    "skill.create": SkillCreatePayload,
    "skill.import": SkillImportPayload,
    "skill.edit": SkillEditPayload,
    "skill.duplicate": SkillDuplicatePayload,
    "skill.delete": SkillDeletePayload,
    "skill.proposal.apply": SkillProposalPayload,
    "skill.proposal.reject": SkillProposalPayload,
    "goal.control": GoalCommandWirePayload,
    "profile.mutate": ProfileCommandWirePayload,
    "developer.repository.branch.create": DeveloperRepositoryBranchPayload,
    "developer.repository.branch.switch": DeveloperRepositoryBranchPayload,
    "developer.repository.commit": DeveloperRepositoryCommitPayload,
    "developer.repository.push": DeveloperRepositoryRevisionPayload,
    "developer.repository.pull_request": DeveloperRepositoryPullRequestPayload,
    "developer.repository.worktree.create": DeveloperRepositoryWorktreeCreatePayload,
    "developer.repository.worktree.preserve": DeveloperRepositoryWorktreePreservePayload,
    "developer.repository.sandbox.configure": DeveloperRepositorySandboxPayload,
    "developer.repository.sandbox.rebuild": DeveloperRepositoryRevisionPayload,
    "developer.repository.sandbox.cleanup": DeveloperRepositoryRevisionPayload,
    "browser.navigate": BrowserCommandNavigatePayload,
    "browser.take_over": BrowserCommandRevisionPayload,
    "browser.check": BrowserCommandRevisionPayload,
    "browser.back": BrowserCommandRevisionPayload,
    "browser.end": BrowserCommandRevisionPayload,
    "mcp.catalog.accept": McpCatalogPayload,
    "knowledge.create": KnowledgeWritePayload,
    "knowledge.edit": KnowledgeWritePayload,
    "knowledge.archive": KnowledgeLifecyclePayload,
    "knowledge.restore": KnowledgeLifecyclePayload,
    "knowledge.resolve": KnowledgeLifecyclePayload,
    "knowledge.relation.add": KnowledgeRelationAddPayload,
    "knowledge.relation.remove": KnowledgeRelationRemovePayload,
    "knowledge.supersede": KnowledgeSupersedePayload,
    "provider.subscription.start": SubscriptionActionPayload,
    "provider.subscription.check": SubscriptionActionPayload,
    "provider.subscription.submit": SubscriptionActionPayload,
    "provider.subscription.cancel": SubscriptionActionPayload,
    "provider.subscription.disconnect": SubscriptionActionPayload,
    "provider.subscription.restore": SubscriptionActionPayload,
    "provider.subscription.import_token": SubscriptionActionPayload,
    "provider.subscription.reference": SubscriptionOptionsPayload,
    "provider.subscription.probe": SubscriptionProbePayload,
    "document.remove": DocumentRemovalPayload,
    "document.removal.retry": DocumentRemovalRetryPayload,
    "provider.subscription.client_id.save": SubscriptionOptionsPayload,
    "provider.subscription.client_id.reset": SubscriptionOptionsPayload,
    "provider.endpoint.save": ProviderConfigurationPayload,
    "provider.endpoint.create": ProviderConfigurationPayload,
    "provider.endpoint.delete": ProviderConfigurationPayload,
    "provider.endpoint.probe": ProviderConfigurationPayload,
    "provider.endpoint.refresh": ProviderConfigurationPayload,
    "provider.model.pin": ProviderConfigurationPayload,
    "provider.model.unpin": ProviderConfigurationPayload,
    "artifact.design.control": ArtifactDesignControlPayload,
    "artifact.asset.upload": ArtifactAssetUploadPayload,
    "artifact.preset.mutate": ArtifactPresetPayload,
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
    recovery: Literal[
        "reload_then_review", "authenticate", "retry", "update_client", "none"
    ] = "none"


class ResourceBinding(WireModel):
    binding_id: OpaqueId
    kind: Literal["workspace", "artifact", "browser_session", "task", "document"]
    resource_id: OpaqueId
    role: Literal["context", "primary", "reference", "output"]
    revision: Revision


class Outcome(WireModel):
    mutation_status: Literal["accepted", "committed", "rejected", "uncertain"]
    projection_status: Literal["pending", "finalizing", "ready", "degraded"]
    external_outcome: Literal[
        "not_applicable", "known_not_sent", "sent", "uncertain"
    ] = "not_applicable"


class GenerationState(WireModel):
    execution_id: OpaqueId
    conversation_id: OpaqueId
    generation_id: OpaqueId
    pass_id: OpaqueId
    segment_id: OpaqueId | None = None
    status: Literal[
        "running", "stopping", "stopped", "waiting_approval", "completed", "interrupted"
    ]
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
    status: Literal[
        "queued",
        "running",
        "waiting_approval",
        "waiting_user",
        "paused",
        "interrupted",
        "completed",
        "completed_delivery_failed",
        "failed",
        "stopped",
        "stopping",
        "blocked",
        "timed_out",
        "cancelled",
    ]
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
    mime_type: Literal[
        "image/png",
        "image/jpeg",
        "video/mp4",
        "application/pdf",
        "application/octet-stream",
    ]
    tool_call_id: str = Field(default="", max_length=256)
    message_id: str = Field(default="", max_length=256)


class MediaError(WireModel):
    code: Literal["payload_too_large", "media_unavailable"]
    tool_call_id: str = Field(default="", max_length=256)
    message_id: str = Field(default="", max_length=256)


EVENT_PAYLOADS = {
    "generation.state": GenerationState,
    "transcript.delta": TranscriptDelta,
    "tool.activity": ToolActivity,
    "generation.activity": GenerationActivity,
    "approval.required": ApprovalRequired,
    "generation.error": GenerationError,
    "transcript.checkpoint": TranscriptCheckpoint,
    "transcript.settled": TranscriptSettled,
    "resource.changed": ResourceChanged,
    "agent.activity": AgentActivity,
    "queue.updated": QueueUpdated,
    "media.available": MediaAvailable,
}
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
    type: Literal[
        "generation.state",
        "transcript.delta",
        "tool.activity",
        "generation.activity",
        "approval.required",
        "generation.error",
        "transcript.checkpoint",
        "transcript.settled",
        "resource.changed",
        "agent.activity",
        "queue.updated",
        "media.available",
        "projection.reset",
        "media.error",
        "steering.queued",
        "steering.consumed",
        "queue.changed",
    ]
    payload: dict

    @model_validator(mode="after")
    def typed_payload(self) -> Event:
        if int(self.source_sequence_start) > int(self.source_sequence_end):
            raise ValueError("Invalid source sequence range")
        self.payload = (
            EVENT_PAYLOADS[self.type]
            .model_validate(self.payload)
            .model_dump(mode="json", exclude_unset=True)
        )
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
        name = (
            "".join(word.title() for word in tag.replace(".", "_").split("_"))
            + base.__name__
        )
        fields = {
            key: (
                field.rebuild_annotation(),
                field.default if not field.is_required() else ...,
            )
            for key, field in base.model_fields.items()
            if key not in {"type", "payload"}
        }
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
    allowed_messages: tuple[Literal["selection.proposed", "edit.proposed"], ...] = (
        "selection.proposed",
        "edit.proposed",
    )


class CommandReceipt(WireModel):
    configuration_revision: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    artifact_design: ArtifactDesignOutcome | None = None
    credential: ProviderCredentialState | None = None
    selection: DefaultModelSnapshot | None = None
    mcp_configuration: McpConfigurationOutcome | None = None
    mcp_runtime: McpRuntimeOutcome | None = None
    removal: DocumentRemovalOutcome | None = None
    accounts: SubscriptionAccountsSnapshot | None = None
    options: SubscriptionOptionsSnapshot | None = None
    result: SubscriptionProbeResult | None = None
    flow: SubscriptionFlowSnapshot | None = None
    share_outcome: ArtifactShareOutcome | None = None
    share_progress: ArtifactShareProgress | None = None
    command_id: UUID
    workspace_edit: WorkspaceEditResult | None = None
    workspace_process: WorkspaceProcessInfo | None = None
    workspace_process_id: UUID | None = None
    workspace_process_run_id: OpaqueId | None = None
    workspace_process_phase: Literal["start_reserved", "cleanup_requested"] | None = (
        None
    )
    binding_revision: Revision | None = None
    export_id: UUID | None = None
    task_id: OpaqueId | None = None
    task_saved: bool = False
    task_created: bool = False
    task_run_id: OpaqueId | None = None
    task_run_reserved: bool = False
    task_stop_requested: bool = False
    task_run_quiesced: bool = False
    task_approval_recorded: bool = False
    task_approval_decision: Literal["approved", "denied"] | None = None
    task_revision: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    status: Literal[
        "accepted",
        "completed",
        "cancel_requested",
        "DeleteCompleted",
        "DeleteBlocked",
        "DeleteNotFound",
        "AlreadyDeleting",
        "DeleteRejected",
        "admitting",
        "rejected",
        "partial",
    ]
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
    confirmed_stages: list[
        Literal["created", "conversation", "associated", "bound"]
    ] = Field(default_factory=list, max_length=4)
    setup_command_id: UUID | None = None
    setup_intent: (
        Literal["create", "open", "add", "repair", "new_conversation"] | None
    ) = None
    association_required: bool = False
    folder_reselection_required: bool = False


class AttachmentView(WireModel):
    attachment_ref: Reference
    name: Annotated[str, StringConstraints(min_length=1, max_length=240)]
    mime_type: Literal[
        "image/png",
        "image/jpeg",
        "video/mp4",
        "application/pdf",
        "application/octet-stream",
    ]
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
    resource_bindings: list[ResourceBinding] = Field(
        default_factory=list, max_length=200
    )


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
    action: Literal[
        "send", "generate", "create_deck", "bind", "preview", "register_folder"
    ]
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


class ArtifactSetupOptions(DeckSetupOptions):
    mode: ArtifactMode = "deck"


class ArtifactPage(WireModel):
    id: OpaqueId
    title: str = Field(max_length=256)
    index: int = Field(ge=0)


class ArtifactPreview(WireModel):
    resource_id: OpaqueId
    resource_revision: str = Field(max_length=128)
    preview_revision: str = Field(max_length=128)
    mode: ArtifactMode
    page_id: OpaqueId
    page_index: int = Field(ge=0)
    page_count: int = Field(ge=1)
    page_title: str = Field(max_length=256)
    canvas_width: int = Field(ge=1, le=16384)
    canvas_height: int = Field(ge=1, le=16384)
    pages: list[ArtifactPage] = Field(max_length=200)
    html: str | None = Field(default=None, max_length=2097152)
    unchanged: bool
    scripts_allowed: bool = False


class ArtifactTextElement(WireModel):
    id: str = Field(min_length=1, max_length=256)
    tag: str = Field(max_length=64)
    text: str = Field(max_length=32768)
    editable: bool


class ArtifactHistoryItem(WireModel):
    id: str = Field(
        min_length=1, max_length=40, pattern=r"^[0-9]{1,20}(\.[0-9]{1,12})?$"
    )
    label: str = Field(max_length=256)
    author: str = Field(max_length=256)
    page_count: int = Field(ge=0)
    available: bool


class ArtifactEditingState(WireModel):
    resource_id: OpaqueId
    resource_revision: str = Field(min_length=1, max_length=128)
    mode: ArtifactMode
    name: str = Field(max_length=256)
    canvas_width: int = Field(ge=1, le=16384)
    canvas_height: int = Field(ge=1, le=16384)
    page_id: OpaqueId
    page_title: str = Field(max_length=256)
    page_notes: str = Field(max_length=32768)
    pages: list[ArtifactPage] = Field(max_length=50)
    page_count: int = Field(ge=0)
    page_next_cursor: str | None = Field(default=None, max_length=2048)
    elements: list[ArtifactTextElement] = Field(max_length=50)
    element_count: int = Field(ge=0)
    element_next_cursor: str | None = Field(default=None, max_length=2048)
    history: list[ArtifactHistoryItem] = Field(max_length=50)
    history_count: int = Field(ge=0)
    history_next_cursor: str | None = Field(default=None, max_length=2048)


class ArtifactLifecycleCapability(WireModel):
    id: Literal[
        "presentation",
        "thumbnails",
        "export.html",
        "export.pdf",
        "export.png",
        "export.pptx",
        "publish.local",
        "publish.remote",
        "share.channel",
        "share.x",
    ]
    label: str = Field(min_length=1, max_length=128)
    state: Literal["ready", "check_on_use", "unavailable"]
    detail: str = Field(max_length=512)
    review_required: bool


class ArtifactLifecycleState(WireModel):
    resource_id: OpaqueId
    resource_revision: str = Field(min_length=1, max_length=128)
    mode: ArtifactMode
    page_count: int = Field(ge=1, le=200)
    capabilities: list[ArtifactLifecycleCapability] = Field(
        min_length=10, max_length=10
    )


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


class DictationCapability(WireModel):
    schema_version: Literal[1] = 1
    browser_dictation_available: bool
    native_capture_available: Literal[False] = False
    reason: Literal["available", "host_unavailable"]


class DictationStart(WireModel):
    request_id: UUID


class DictationIdentity(WireModel):
    voice_session_id: int = Field(ge=1, le=9007199254740991)
    server_epoch: OpaqueId


class DictationHandle(DictationIdentity):
    lease_id: UUID
    conversation_id: OpaqueId


class DictationSnapshot(WireModel):
    schema_version: Literal[1] = 1
    handle: DictationHandle
    state: Literal[
        "capturing", "receiving", "transcribing", "completed", "stopping", "stopped"
    ]
    quiesced: bool
    expires_in_ms: int = Field(ge=0, le=120000)
    max_audio_bytes: Literal[8388608] = 8388608
    max_utterance_ms: Literal[30000] = 30000


class DictationResult(WireModel):
    snapshot: DictationSnapshot
    utterance_id: UUID
    outcome: Literal["transcribed", "no_speech"]
    text: str = Field(max_length=4000)


class TalkStart(WireModel):
    request_id: UUID
    conversation_revision: Revision
    model_selection: ModelSelection
    write_targets: list[WriteTarget] = Field(max_length=2)


class TalkSnapshot(WireModel):
    schema_version: Literal[1]
    handle: DictationHandle
    state: Literal[
        "listening",
        "receiving",
        "transcribing",
        "thinking",
        "speaking",
        "stopping",
        "stopped",
    ]
    quiesced: bool
    expires_in_ms: int = Field(ge=0, le=120000)
    run_id: OpaqueId | None
    transport: Literal["browser_local"]
    max_audio_bytes: Literal[8388608]
    max_utterance_ms: Literal[30000]


class TalkResult(WireModel):
    snapshot: TalkSnapshot
    utterance_id: UUID
    outcome: Literal["submitted", "no_speech"]
    text: str = Field(max_length=4000)
    run_id: OpaqueId | None


class VoiceRunView(WireModel):
    handle: DictationHandle
    run_id: OpaqueId | None
    state: Literal[
        "idle", "running", "stopping", "completed", "stopped", "failed", "interrupted"
    ]
    output_id: OpaqueId | None
    text: str | None = Field(max_length=4000)


class TalkOutputRequest(DictationIdentity):
    run_id: OpaqueId
    output_id: OpaqueId


class RealtimeSnapshot(WireModel):
    schema_version: Literal[1]
    handle: DictationHandle
    state: str = Field(max_length=80)
    quiesced: bool
    expires_in_ms: int = Field(ge=0, le=120000)
    run_id: OpaqueId | None
    transport: Literal["openai_realtime"]


class RealtimeStart(WireModel):
    snapshot: RealtimeSnapshot
    client_secret: str | None = Field(max_length=4096)
    secret_expires_in_ms: int = Field(ge=0, le=60000)
    exchange_available: bool = False


class RealtimeEvent(WireModel):
    event_id: UUID
    type: Literal[
        "connected",
        "disconnected",
        "fatal_error",
        "speech_started",
        "speech_stopped",
        "transcript_final",
        "assistant_transcript_final",
        "function_call_ready",
        "consult_fallback_needed",
        "output_started",
        "output_item_started",
        "response_done",
        "response_cancelled",
        "output_audio_done",
        "barge_in_cancelled",
    ]
    generation_id: str = Field(default="", max_length=128)
    response_id: str = Field(default="", max_length=128)
    output_item_id: str = Field(default="", max_length=128)
    item_id: str = Field(default="", max_length=128)
    call_id: str = Field(default="", max_length=128)
    name: str = Field(default="", max_length=128)
    arguments: str = Field(default="", max_length=8192)
    text: str = Field(default="", max_length=4000)


class RealtimeEventRequest(DictationIdentity):
    event: RealtimeEvent


class RealtimeEventResult(WireModel):
    snapshot: RealtimeSnapshot
    event_id: UUID
    accepted: bool
    caption: str = Field(max_length=4000)
    call_id: str = Field(max_length=128)
    function_output: str = Field(max_length=8192)
    silent: bool


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
