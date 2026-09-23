"""Generate v1 schemas and TypeScript from Python DTOs, without dependencies.

Run with the already locked Python environment. --check never writes output.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from row_bot.api.v1 import schemas  # noqa: E402 -- load checkout source after path bootstrap

MODELS = {name: getattr(schemas, name) for name in (
    "Command", "Event", "Handshake", "Problem", "Outcome", "ResourceBinding",
    "PreviewContract", "CommandReceipt", "AttachmentView", "SessionProof",
    "ConversationView", "ConversationPage", "ConversationActionSnapshot", "ConversationActionReviewRequest",
    "ConversationActionReview", "ConversationActionCommand", "ConversationActionReceipt",
    "BrowserControlSnapshot", "BrowserReviewRequest", "BrowserReview", "BrowserReceipt",
    "Snapshot", "TranscriptPage", "SubscriptionView",
    "EventPage", "Choices", "HandshakeView", "ApprovalView", "ResourceView", "Acknowledgement",
    "Acknowledged", "Unsubscribed", "UploadRequest", "UploadView", "UploadCompletion", "UploadCancelled",
    "NativeBootstrapView", "NativeAttestationRequest", "NativeAttestationView",
    "NativeGrantRequest", "NativeRevocationView", "NativeSelectionCompleteRequest", "NativeSelectionView",
    "NativeTerminalOpenRequest", "NativeTerminalView", "NativeTerminalInput", "NativeTerminalResize",
    "NativeTerminalFrame", "NativeTerminalOutput", "NativeTerminalChanged", "NativeTerminalClosed",
    "StreamReset", "LazyContent", "SearchPage", "ConversationWorkspace", "ConversationComposer", "ConversationComposerQuery", "SlashCommandRead", "SlashCommandResult", "ResourceChoicePage",
    "DelegatedRun", "DelegatedActivityView",
    "ContextUsageView", "ConversationOpenView", "ProviderStatusSnapshot", "ProviderLiveSnapshot", "ProviderCatalogRefresh", "ProviderRuntimeProbe", "CachedModelPage", "ModelsSettingsState", "ModelSurfaceMutation", "ModelContextMutation", "AgentRuntimeSettingsState", "ModelCatalogSummary", "ModelCameraList", "TaskSummaryPage", "TaskDeliverySnapshot", "ToolCatalogPage",
    "SettingsSnapshot", "SettingsMutationRequest", "SettingsMutationReview", "SettingsMutationCommand", "SettingsMutationReceipt",
    "EntitySummaryPage", "KnowledgeEntityDetail", "KnowledgeRecallPage", "KnowledgeMemoryChangePage", "DocumentSummaryPage", "TaskEditableFields", "TaskEditorSnapshot", "TaskSaveResult",
    "TaskSettingsFields", "TaskSettingsSnapshot", "ProviderSettingsSnapshot",
    "ProviderCredentialState", "ProviderSettingsReviewRequest", "ProviderSettingsReview", "ProviderSettingsReceipt",
    "ProviderEndpointFields", "ProviderEndpointSnapshot", "ProviderConfigurationPage",
    "ProviderConfigurationReviewRequest", "ProviderConfigurationReview", "ProviderConfigurationReceipt",
    "DefaultModelReviewRequest", "DefaultModelReview", "DefaultModelReceipt",
    "McpConfigurationPage", "McpConfigurationReviewRequest", "McpConfigurationReview", "McpConfigurationOutcome",
    "McpRuntimeState", "McpRuntimeReviewRequest", "McpRuntimeReview", "McpRuntimeOutcome",
    "McpPolicyPage", "McpPolicyRequest", "McpPolicyReview",
    "McpTestedCatalogPage", "McpCatalogRequest", "McpCatalogReview",
    "RuntimeInstallationSnapshot", "RuntimeInstallationReviewRequest", "RuntimeInstallationReview", "RuntimeInstallationReceipt",
    "DocumentQueuePage", "DocumentControlReviewRequest", "DocumentControlReview", "DocumentControlReceipt",
    "DocumentUploadReviewRequest", "DocumentUploadReview", "DocumentUploadReceipt",
    "DocumentProcessingReviewRequest", "DocumentProcessingReview", "DocumentProcessingReceipt",
    "WikiStatus", "WikiArticlePage", "WikiArticle", "WikiReviewRequest", "WikiReview", "WikiReceipt", "WikiOpenFolderResult",
    "ChannelPage", "ChannelActionRequest", "ChannelActionReview", "ChannelReceipt",
    "PluginCatalogPage", "PluginDetail", "PluginReviewRequest", "PluginReview", "PluginReceipt",
    "SkillPage", "SkillDetail", "SkillProposalPage", "SkillReviewRequest", "SkillReview", "SkillReceipt",
    "GoalPage", "GoalDetail", "GoalCommandPayload", "GoalReview", "GoalReceipt",
    "ProfilePage", "ProfileDetail", "ProfileCommandPayload", "ProfileReview", "ProfileReceipt",
    "DeveloperRepositorySnapshot", "DeveloperRepositoryReviewRequest", "DeveloperRepositoryReview", "DeveloperRepositoryReceipt",
    "KnowledgeEditorState", "KnowledgeReviewRequest", "KnowledgeReview", "KnowledgeReceipt",
    "KnowledgeGraphSnapshot", "MonitorSnapshot", "MonitorLogs", "DreamRunRequest", "DreamRunReview", "DreamRunCommand", "DreamRunReceipt",
    "KnowledgeMaintenanceRequest", "KnowledgeMaintenanceReview", "KnowledgeMaintenanceCommand", "KnowledgeMaintenanceReceipt",
    "KnowledgeRelationPage", "KnowledgeRelationReviewRequest", "KnowledgeRelationReview", "KnowledgeRelationReceipt",
    "SubscriptionAccountsSnapshot", "SubscriptionFlowSnapshot", "SubscriptionActionReview",
    "SubscriptionActionRequest", "SubscriptionActionResult", "SubscriptionActionReceipt", "SubscriptionQuiescence",
    "SubscriptionOptionsSnapshot", "SubscriptionOptionsReview",
    "SubscriptionOptionsRequest", "SubscriptionOptionsResult", "SubscriptionOptionsReceipt",
    "SubscriptionProbeResult", "SubscriptionProbeSnapshot", "SubscriptionProbeRequest", "SubscriptionProbeReview", "SubscriptionProbeState",
    "BuddySnapshot", "BuddyPack", "BuddyPackPage", "BuddyHatchRequest", "BuddyHatchReview", "BuddyReceipt",
    "SubscriptionProbeCommandResult", "SubscriptionProbeReceipt", "SubscriptionProbeStatus",
    "DocumentRemovalReviewRequest", "DocumentRemovalReview", "DocumentRemovalOutcome", "DocumentRemovalReceipt",
    "WorkspaceProcessInfo", "WorkspaceProcessSnapshot", "WorkspaceProcessRecoveryPage",
    "WorkspaceProcessOutput", "WorkspaceProcessReviewRequest", "WorkspaceProcessReview",
    "DesignBrand", "DesignControlsState", "DesignReviewState", "DesignPresentationState",
    "ArtifactDesignOutcome", "ArtifactPresetReviewRequest", "ArtifactPresetReview", "ArtifactReviewDraftRequest", "ArtifactReviewDraft",
    "ArtifactShareOptions", "ArtifactShareReview", "ArtifactShareOutcome",
    "ArtifactShareChannels",
    "TaskRunReview", "TaskRunSummary", "TaskRunPage", "TaskRunResult", "TaskApprovalReview",
    "TaskApprovalPage", "TaskApprovalResult", "TaskStopResult", "ArtifactExport", "WorkspaceEditableFile", "WorkspaceEditResult",
    "WorkspaceImportPage", "WorkspaceImportPatch", "WorkspaceImportReviewRequest", "WorkspaceImportReview", "WorkspaceImportResult",
    "WorkspaceUndoReviewRequest", "WorkspaceUndoReview", "WorkspaceUndoResult",
    "TaskGraphFields", "TaskGraphStepEdit", "TaskGraphSnapshot",
    "DictationCapability", "DictationStart", "DictationIdentity", "DictationHandle", "DictationSnapshot", "DictationResult",
    "TalkStart", "TalkSnapshot", "TalkResult", "TalkOutputRequest", "RealtimeSnapshot", "RealtimeStart", "RealtimeEvent", "RealtimeEventRequest", "RealtimeEventResult", "DefaultModelSnapshot", "VoiceRunView",
    "FolderGrantView", "DeckSetupOptions", "ArtifactSetupOptions", "ArtifactPreview", "ArtifactEditingState", "ArtifactLifecycleState", "WorkspaceInspector", "WorkspaceChanges",
    "WorkspaceDirectory", "WorkspaceFile", "WorkspaceDiff", "WorkspaceChangeSetPage", "WorkspaceChangeSetFiles", "DraftView", "DraftSave", "ParentSteeringView", "ClientQueueView")}

# Method, path, request DTO (binary uses bytes), response DTO. This table also
# drives OpenAPI and is checked against the actual router in the contract tests.
OPERATIONS = (
    ("post", "/handshake", "Handshake", "HandshakeView"),
    ("get", "/conversations", None, "ConversationPage"),
    ("get", "/conversations/{conversation_id}", None, "ConversationView"),
    ("get", "/conversations/{conversation_id}/actions", None, "ConversationActionSnapshot"),
    ("post", "/conversations/{conversation_id}/actions/review", "ConversationActionReviewRequest", "ConversationActionReview"),
    ("get", "/conversations/{conversation_id}/actions/commands/{command_id}", None, "ConversationActionReceipt"),
    ("post", "/conversations/{conversation_id}/actions/commands", "ConversationActionCommand", "ConversationActionReceipt"),
    ("get", "/conversations/{conversation_id}/browser", None, "BrowserControlSnapshot"),
    ("post", "/conversations/{conversation_id}/browser/review", "BrowserReviewRequest", "BrowserReview"),
    ("get", "/conversations/{conversation_id}/browser/commands/{command_id}", None, "BrowserReceipt"),
    ("post", "/conversations/{conversation_id}/browser/commands", "Command", "BrowserReceipt"),
    ("get", "/conversations/{conversation_id}/transcript", None, "TranscriptPage"),
    ("get", "/conversations/{conversation_id}/content/{message_id}", None, "LazyContent"),
    ("get", "/conversations/{conversation_id}/text/{message_id}", None, "LazyContent"),
    ("post", "/conversations/commands", "Command", "CommandReceipt"),
    ("post", "/conversations/{conversation_id}/commands", "Command", "CommandReceipt"),
    ("get", "/commands/{command_id}", None, "CommandReceipt"),
    ("get", "/approvals/{approval_id}", None, "ApprovalView"),
    ("post", "/approvals/{approval_id}/commands", "Command", "CommandReceipt"),
    ("post", "/conversations/{conversation_id}/subscriptions", None, "SubscriptionView"),
    ("get", "/events/poll", None, "EventPage"),
    ("get", "/events", None, "Event"),
    ("put", "/subscriptions/{subscription_id}/ack", "Acknowledgement", "Acknowledged"),
    ("delete", "/subscriptions/{subscription_id}", None, "Unsubscribed"),
    ("get", "/choices", None, "Choices"),
    ("get", "/voice/dictation/capability", None, "DictationCapability"),
    ("post", "/conversations/{conversation_id}/voice/dictation", "DictationStart", "DictationSnapshot"),
    ("get", "/conversations/{conversation_id}/voice/dictation/{lease_id}", None, "DictationSnapshot"),
    ("post", "/conversations/{conversation_id}/voice/dictation/{lease_id}/transcribe", "bytes", "DictationResult"),
    ("post", "/conversations/{conversation_id}/voice/dictation/{lease_id}/stop", "DictationIdentity", "DictationSnapshot"),
    ("post", "/conversations/{conversation_id}/voice/talk", "TalkStart", "TalkSnapshot"),
    ("get", "/conversations/{conversation_id}/voice/talk/{lease_id}", None, "TalkSnapshot"),
    ("post", "/conversations/{conversation_id}/voice/talk/{lease_id}/transcribe", "bytes", "TalkResult"),
    ("post", "/conversations/{conversation_id}/voice/talk/{lease_id}/stop", "DictationIdentity", "TalkSnapshot"),
    ("post", "/conversations/{conversation_id}/voice/talk/{lease_id}/heartbeat", "DictationIdentity", "TalkSnapshot"),
    ("post", "/conversations/{conversation_id}/voice/talk/{lease_id}/output", "TalkOutputRequest", "bytes"),
    ("post", "/conversations/{conversation_id}/voice/realtime", "TalkStart", "RealtimeStart"),
    ("get", "/conversations/{conversation_id}/voice/realtime/{lease_id}", None, "RealtimeSnapshot"),
    ("post", "/conversations/{conversation_id}/voice/realtime/{lease_id}/stop", "DictationIdentity", "RealtimeSnapshot"),
    ("post", "/conversations/{conversation_id}/voice/realtime/{lease_id}/heartbeat", "DictationIdentity", "RealtimeSnapshot"),
    ("post", "/conversations/{conversation_id}/voice/realtime/{lease_id}/event", "RealtimeEventRequest", "RealtimeEventResult"),
    ("post", "/conversations/{conversation_id}/voice/realtime/{lease_id}/exchange", "bytes", "bytes"),
    ("get", "/conversations/{conversation_id}/voice/talk/{lease_id}/run", None, "VoiceRunView"),
    ("get", "/conversations/{conversation_id}/voice/realtime/{lease_id}/run", None, "VoiceRunView"),
    ("get", "/settings/providers", None, "ProviderStatusSnapshot"),
    ("get", "/settings/providers/live", None, "ProviderLiveSnapshot"),
    ("post", "/settings/providers/live/{provider_id}/refresh", None, "ProviderCatalogRefresh"),
    ("get", "/settings/providers/live/refresh", None, "ProviderCatalogRefresh"),
    ("post", "/settings/providers/live/{provider_id}/runtime-test", None, "ProviderRuntimeProbe"),
    ("post", "/settings/providers/commands", "Command", "CommandReceipt"),
    ("post", "/settings/mcp/commands", "Command", "CommandReceipt"),
    ("get", "/settings/mcp/configuration", None, "McpConfigurationPage"),
    ("get", "/settings/mcp/runtime/{server_id}", None, "McpRuntimeState"),
    ("get", "/settings/mcp/policy", None, "McpPolicyPage"),
    ("get", "/settings/mcp/catalog", None, "McpTestedCatalogPage"),
    ("get", "/knowledge/entities/editor", None, "KnowledgeEditorState"),
    ("get", "/knowledge/graph", None, "KnowledgeGraphSnapshot"),
    ("get", "/monitor", None, "MonitorSnapshot"),
    ("get", "/monitor/logs", None, "MonitorLogs"),
    ("post", "/monitor/dream/review", "DreamRunRequest", "DreamRunReview"),
    ("get", "/monitor/dream/commands/{command_id}", None, "DreamRunReceipt"),
    ("post", "/monitor/dream/commands", "DreamRunCommand", "DreamRunReceipt"),
    ("post", "/knowledge/entities/review", "KnowledgeReviewRequest", "KnowledgeReview"),
    ("get", "/knowledge/entities/commands/{command_id}", None, "KnowledgeReceipt"),
    ("post", "/knowledge/entities/commands", "Command", "KnowledgeReceipt"),
    ("post", "/knowledge/maintenance/review", "KnowledgeMaintenanceRequest", "KnowledgeMaintenanceReview"),
    ("get", "/knowledge/maintenance/commands/{command_id}", None, "KnowledgeMaintenanceReceipt"),
    ("post", "/knowledge/maintenance/commands", "KnowledgeMaintenanceCommand", "KnowledgeMaintenanceReceipt"),
    ("get", "/knowledge/relations", None, "KnowledgeRelationPage"),
    ("post", "/knowledge/relations/review", "KnowledgeRelationReviewRequest", "KnowledgeRelationReview"),
    ("get", "/knowledge/relations/commands/{command_id}", None, "KnowledgeRelationReceipt"),
    ("post", "/knowledge/relations/commands", "Command", "KnowledgeRelationReceipt"),
    ("post", "/settings/mcp/catalog/review", "McpCatalogRequest", "McpCatalogReview"),
    ("get", "/settings/mcp/installations/{runtime_id}", None, "RuntimeInstallationSnapshot"),
    ("post", "/settings/mcp/installations/review", "RuntimeInstallationReviewRequest", "RuntimeInstallationReview"),
    ("get", "/settings/mcp/installations/{runtime_id}/commands/{command_id}", None, "RuntimeInstallationReceipt"),
    ("post", "/settings/mcp/installations/commands", "Command", "RuntimeInstallationReceipt"),
    ("get", "/documents/queue", None, "DocumentQueuePage"),
    ("post", "/documents/queue/review", "DocumentControlReviewRequest", "DocumentControlReview"),
    ("get", "/documents/queue/commands/{command_id}", None, "DocumentControlReceipt"),
    ("post", "/documents/queue/commands", "Command", "DocumentControlReceipt"),
    ("post", "/documents/uploads/review", "DocumentUploadReviewRequest", "DocumentUploadReview"),
    ("get", "/documents/uploads/commands/{command_id}", None, "DocumentUploadReceipt"),
    ("post", "/documents/uploads/commands", "bytes", "DocumentUploadReceipt"),
    ("post", "/conversations/{conversation_id}/documents/processing/review", "DocumentProcessingReviewRequest", "DocumentProcessingReview"),
    ("get", "/conversations/{conversation_id}/documents/processing/commands/{command_id}", None, "DocumentProcessingReceipt"),
    ("post", "/conversations/{conversation_id}/documents/processing/commands", "Command", "DocumentProcessingReceipt"),
    ("get", "/settings/wiki", None, "WikiStatus"),
    ("post", "/settings/wiki/open-folder", None, "WikiOpenFolderResult"),
    ("get", "/settings/wiki/articles", None, "WikiArticlePage"),
    ("get", "/settings/wiki/articles/{article_id}", None, "WikiArticle"),
    ("post", "/settings/wiki/review", "WikiReviewRequest", "WikiReview"),
    ("get", "/settings/wiki/commands/{command_id}", None, "WikiReceipt"),
    ("post", "/settings/wiki/commands", "Command", "WikiReceipt"),
    ("get", "/settings/channels", None, "ChannelPage"),
    ("post", "/settings/channels/review", "ChannelActionRequest", "ChannelActionReview"),
    ("get", "/settings/channels/{channel_id}/commands/{command_id}", None, "ChannelReceipt"),
    ("post", "/settings/channels/commands", "Command", "ChannelReceipt"),
    ("get", "/settings/plugins", None, "PluginCatalogPage"),
    ("get", "/settings/plugins/{plugin_id}", None, "PluginDetail"),
    ("post", "/settings/plugins/{plugin_id}/review", "PluginReviewRequest", "PluginReview"),
    ("get", "/settings/plugins/{plugin_id}/receipts/{command_id}", None, "PluginReceipt"),
    ("post", "/settings/plugins/{plugin_id}/commands", "Command", "PluginReceipt"),
    ("get", "/settings/skills", None, "SkillPage"),
    ("get", "/settings/skills/items/{skill_id}", None, "SkillDetail"),
    ("get", "/settings/skill-proposals", None, "SkillProposalPage"),
    ("post", "/settings/skills/review", "SkillReviewRequest", "SkillReview"),
    ("get", "/settings/skills/commands/{command_id}", None, "SkillReceipt"),
    ("post", "/settings/skills/commands", "Command", "SkillReceipt"),
    ("get", "/conversations/{conversation_id}/goals", None, "GoalPage"),
    ("get", "/conversations/{conversation_id}/goals/items/{goal_id}", None, "GoalDetail"),
    ("post", "/conversations/{conversation_id}/goals/review", "GoalCommandPayload", "GoalReview"),
    ("get", "/conversations/{conversation_id}/goals/commands/{command_id}", None, "GoalReceipt"),
    ("post", "/conversations/{conversation_id}/goals/commands", "Command", "GoalReceipt"),
    ("get", "/settings/profiles", None, "ProfilePage"),
    ("get", "/settings/profiles/items/{profile_id}", None, "ProfileDetail"),
    ("post", "/settings/profiles/review", "ProfileCommandPayload", "ProfileReview"),
    ("get", "/settings/profiles/{profile_ref}/receipts/{command_id}", None, "ProfileReceipt"),
    ("post", "/settings/profiles/commands", "Command", "ProfileReceipt"),
    ("get", "/conversations/{conversation_id}/workspaces/{binding_id}/repository", None, "DeveloperRepositorySnapshot"),
    ("post", "/conversations/{conversation_id}/workspaces/{binding_id}/repository/review", "DeveloperRepositoryReviewRequest", "DeveloperRepositoryReview"),
    ("get", "/conversations/{conversation_id}/workspaces/{binding_id}/repository/commands/{command_id}", None, "DeveloperRepositoryReceipt"),
    ("post", "/conversations/{conversation_id}/workspaces/{binding_id}/repository/commands", "Command", "DeveloperRepositoryReceipt"),
    ("post", "/settings/mcp/policy/review", "McpPolicyRequest", "McpPolicyReview"),
    ("post", "/settings/mcp/runtime/review", "McpRuntimeReviewRequest", "McpRuntimeReview"),
    ("post", "/settings/mcp/configuration/review", "McpConfigurationReviewRequest", "McpConfigurationReview"),
    ("get", "/settings/providers/{provider_id}/credential", None, "ProviderSettingsSnapshot"),
    ("post", "/settings/providers/{provider_id}/credential-review", "ProviderSettingsReviewRequest", "ProviderSettingsReview"),
    ("get", "/settings/providers/{provider_id}/receipts/{command_id}", None, "ProviderSettingsReceipt"),
    ("get", "/settings/providers/configuration", None, "ProviderConfigurationPage"),
    ("post", "/settings/providers/configuration/review", "ProviderConfigurationReviewRequest", "ProviderConfigurationReview"),
    ("get", "/settings/providers/configuration/receipts/{command_id}", None, "ProviderConfigurationReceipt"),
    ("get", "/settings/providers/default-model", None, "DefaultModelSnapshot"),
    ("get", "/settings/providers/subscriptions", None, "SubscriptionAccountsSnapshot"),
    ("get", "/settings/providers/subscriptions/options", None, "SubscriptionOptionsSnapshot"),
    ("get", "/conversations/{conversation_id}/buddy", None, "BuddySnapshot"),
    ("get", "/buddy", None, "BuddySnapshot"),
    ("get", "/buddy/packs", None, "BuddyPackPage"),
    ("get", "/buddy/packs/{pack_id}", None, "BuddyPack"),
    ("get", "/buddy/packs/{pack_id}/media/{asset_id}", None, "bytes"),
    ("get", "/conversations/{conversation_id}/buddy/packs", None, "BuddyPackPage"),
    ("get", "/conversations/{conversation_id}/buddy/packs/{pack_id}", None, "BuddyPack"),
    ("get", "/conversations/{conversation_id}/buddy/packs/{pack_id}/media/{asset_id}", None, "bytes"),
    ("post", "/conversations/{conversation_id}/buddy/review", "BuddyHatchRequest", "BuddyHatchReview"),
    ("get", "/conversations/{conversation_id}/buddy/commands/{command_id}", None, "BuddyReceipt"),
    ("post", "/conversations/{conversation_id}/buddy/commands", "Command", "BuddyReceipt"),
    ("get", "/settings/providers/subscriptions/probes", None, "SubscriptionProbeSnapshot"),
    ("post", "/settings/providers/subscriptions/probes/review", "SubscriptionProbeRequest", "SubscriptionProbeReview"),
    ("get", "/settings/providers/subscriptions/probes/{command_id}/status", None, "SubscriptionProbeStatus"),
    ("post", "/settings/providers/subscriptions/probes/{command_id}/cancel", "EmptyPayload", "SubscriptionProbeState"),
    ("get", "/settings/providers/subscriptions/probes/{command_id}/receipt", None, "SubscriptionProbeReceipt"),
    ("post", "/knowledge/documents/removal-review", "DocumentRemovalReviewRequest", "DocumentRemovalReview"),
    ("post", "/knowledge/documents/removals/{command_id}/retry-review", "EmptyPayload", "DocumentRemovalReview"),
    ("get", "/knowledge/documents/removals/{command_id}", None, "DocumentRemovalReceipt"),
    ("post", "/knowledge/documents/commands", "Command", "DocumentRemovalReceipt"),
    ("post", "/settings/providers/subscriptions/options/review", "SubscriptionOptionsRequest", "SubscriptionOptionsReview"),
    ("get", "/settings/providers/subscriptions/options/receipts/{command_id}", None, "SubscriptionOptionsReceipt"),
    ("post", "/settings/providers/subscriptions/review", "SubscriptionActionRequest", "SubscriptionActionReview"),
    ("get", "/settings/providers/subscriptions/flows/{flow_id}", None, "SubscriptionFlowSnapshot"),
    ("delete", "/settings/providers/subscriptions/flows", None, "SubscriptionQuiescence"),
    ("post", "/settings/providers/subscriptions/starts/{command_id}/cancel", "EmptyPayload", "SubscriptionFlowSnapshot"),
    ("get", "/settings/providers/subscriptions/{provider_id}/receipts/{command_id}", None, "SubscriptionActionReceipt"),
    ("post", "/settings/providers/default-model/review", "DefaultModelReviewRequest", "DefaultModelReview"),
    ("get", "/settings/providers/default-model/receipts/{command_id}", None, "DefaultModelReceipt"),
    ("get", "/conversations/{conversation_id}/artifacts/{binding_id}/editing", None, "ArtifactEditingState"),
    ("get", "/conversations/{conversation_id}/artifacts/{binding_id}/lifecycle", None, "ArtifactLifecycleState"),
    ("get", "/conversations/{conversation_id}/artifacts/{binding_id}/design-controls", None, "DesignControlsState"),
    ("get", "/conversations/{conversation_id}/artifacts/{binding_id}/design-review", None, "DesignReviewState"),
    ("get", "/conversations/{conversation_id}/artifacts/{binding_id}/presentation", None, "DesignPresentationState"),
    ("post", "/conversations/{conversation_id}/artifacts/{binding_id}/preset-review", "ArtifactPresetReviewRequest", "ArtifactPresetReview"),
    ("post", "/conversations/{conversation_id}/artifacts/{binding_id}/review-draft", "ArtifactReviewDraftRequest", "ArtifactReviewDraft"),
    ("get", "/conversations/{conversation_id}/workspaces/{binding_id}/editing", None, "WorkspaceEditableFile"),
    ("get", "/conversations/{conversation_id}/workspaces/{binding_id}/imports", None, "WorkspaceImportPage"),
    ("get", "/conversations/{conversation_id}/workspaces/{binding_id}/imports/patch", None, "WorkspaceImportPatch"),
    ("post", "/conversations/{conversation_id}/workspaces/{binding_id}/imports/review", "WorkspaceImportReviewRequest", "WorkspaceImportReview"),
    ("get", "/conversations/{conversation_id}/workspaces/{binding_id}/imports/commands/{command_id}", None, "WorkspaceImportResult"),
    ("post", "/conversations/{conversation_id}/workspaces/{binding_id}/imports/commands/{command_id}/review", "EmptyPayload", "WorkspaceImportReview"),
    ("post", "/conversations/{conversation_id}/workspaces/{binding_id}/imports/commands", "Command", "WorkspaceImportResult"),
    ("post", "/conversations/{conversation_id}/workspaces/{binding_id}/undo/review", "WorkspaceUndoReviewRequest", "WorkspaceUndoReview"),
    ("get", "/conversations/{conversation_id}/workspaces/{binding_id}/undo/commands/{command_id}", None, "WorkspaceUndoResult"),
    ("post", "/conversations/{conversation_id}/workspaces/{binding_id}/undo/commands/{command_id}/review", "EmptyPayload", "WorkspaceUndoReview"),
    ("post", "/conversations/{conversation_id}/workspaces/{binding_id}/undo/commands", "Command", "WorkspaceUndoResult"),
    ("get", "/conversations/{conversation_id}/workspaces/{binding_id}/processes", None, "WorkspaceProcessSnapshot"),
    ("get", "/conversations/{conversation_id}/workspaces/{binding_id}/processes/recovery", None, "WorkspaceProcessRecoveryPage"),
    ("get", "/conversations/{conversation_id}/workspaces/{binding_id}/processes/{process_id}/output", None, "WorkspaceProcessOutput"),
    ("post", "/conversations/{conversation_id}/workspaces/{binding_id}/processes/review", "WorkspaceProcessReviewRequest", "WorkspaceProcessReview"),
    ("get", "/tasks", None, "TaskSummaryPage"),
    ("get", "/tasks/delivery-defaults", None, "TaskDeliverySnapshot"),
    ("get", "/tasks/{task_id}/editing", None, "TaskEditorSnapshot"),
    ("get", "/tasks/{task_id}/graph", None, "TaskGraphSnapshot"),
    ("get", "/tasks/{task_id}/settings", None, "TaskSettingsSnapshot"),
    ("get", "/tasks/{task_id}/webhook-configuration", None, "bytes"),
    ("post", "/tasks/{task_id}/settings-review", "TaskSettingsFields", "TaskSettingsSnapshot"),
    ("post", "/conversations/{conversation_id}/artifacts/{binding_id}/sharing-review", "ArtifactShareOptions", "ArtifactShareReview"),
    ("get", "/sharing/channels", None, "ArtifactShareChannels"),
    ("get", "/tasks/{task_id}/run-review", None, "TaskRunReview"),
    ("get", "/tasks/{task_id}/runs", None, "TaskRunPage"),
    ("get", "/tasks/{task_id}/runs/{run_id}", None, "TaskRunSummary"),
    ("get", "/tasks/{task_id}/runs/{run_id}/approvals", None, "TaskApprovalPage"),
    ("get", "/conversations/{conversation_id}/artifacts/{binding_id}/exports/{export_id}", None, "ArtifactExport"),
    ("get", "/conversations/{conversation_id}/artifacts/{binding_id}/exports/{export_id}/download", None, "bytes"),
    ("post", "/tasks/commands", "Command", "CommandReceipt"),
    ("get", "/knowledge/entities", None, "EntitySummaryPage"),
    ("get", "/knowledge/entities/{entity_id}", None, "KnowledgeEntityDetail"),
    ("get", "/knowledge/recalls", None, "KnowledgeRecallPage"),
    ("get", "/knowledge/change-log", None, "KnowledgeMemoryChangePage"),
    ("get", "/knowledge/documents", None, "DocumentSummaryPage"),
    ("get", "/settings/tools", None, "ToolCatalogPage"),
    ("get", "/settings/snapshot", None, "SettingsSnapshot"),
    ("post", "/settings/snapshot/review", "SettingsMutationRequest", "SettingsMutationReview"),
    ("get", "/settings/snapshot/commands/{command_id}", None, "SettingsMutationReceipt"),
    ("post", "/settings/snapshot/commands", "SettingsMutationCommand", "SettingsMutationReceipt"),
    ("get", "/settings/models", None, "CachedModelPage"),
    ("get", "/settings/models/state", None, "ModelsSettingsState"),
    ("post", "/settings/models/surface", "ModelSurfaceMutation", "ModelsSettingsState"),
    ("post", "/settings/models/context", "ModelContextMutation", "ModelsSettingsState"),
    ("get", "/settings/models/agents", None, "AgentRuntimeSettingsState"),
    ("post", "/settings/models/agents", "AgentRuntimeSettingsState", "AgentRuntimeSettingsState"),
    ("post", "/settings/models/agents/reset", None, "AgentRuntimeSettingsState"),
    ("get", "/settings/models/catalog-summary", None, "ModelCatalogSummary"),
    ("get", "/settings/models/catalog", None, "CachedModelPage"),
    ("post", "/settings/models/refresh", None, "ProviderCatalogRefresh"),
    ("post", "/settings/models/cameras/refresh", None, "ModelCameraList"),
    ("get", "/resources/{reference}", None, "ResourceView"),
    ("post", "/uploads", "bytes", "AttachmentView"),
    ("post", "/uploads/sessions", "UploadRequest", "UploadView"),
    ("get", "/uploads/{upload_id}", None, "UploadView"),
    ("put", "/uploads/{upload_id}/chunks", "bytes", "UploadView"),
    ("post", "/uploads/{upload_id}/complete", "UploadCompletion", "AttachmentView"),
    ("delete", "/uploads/{upload_id}", None, "UploadCancelled"),
    ("get", "/attachments/{reference}/metadata", None, "AttachmentView"),
    ("get", "/attachments/{reference}", None, "bytes"),
    ("get", "/native/bootstrap", None, "NativeBootstrapView"),
    ("post", "/native/attest", "NativeAttestationRequest", "NativeAttestationView"),
    ("post", "/native/authorize", "NativeGrantRequest", "NativeTerminalChanged"),
    ("post", "/native/revoke", "NativeGrantRequest", "NativeRevocationView"),
    ("post", "/native/selections/complete", "NativeSelectionCompleteRequest", "NativeSelectionView"),
    ("post", "/native/terminal/open", "NativeTerminalOpenRequest", "NativeTerminalView"),
    ("post", "/native/attachments/{reference}", "NativeGrantRequest", "bytes"),
    ("get", "/native/terminals/{terminal_id}", None, "NativeTerminalOutput"),
    ("post", "/native/terminals/{terminal_id}/input", "NativeTerminalInput", "NativeTerminalChanged"),
    ("post", "/native/terminals/{terminal_id}/resize", "NativeTerminalResize", "NativeTerminalChanged"),
    ("delete", "/native/terminals/{terminal_id}", None, "NativeTerminalClosed"),
    ("get", "/search", None, "SearchPage"),
    ("get", "/conversations/{conversation_id}/history", None, "TranscriptPage"),
    ("get", "/conversations/{conversation_id}/workspace", None, "ConversationWorkspace"),
    ("post", "/conversations/{conversation_id}/composer/query", "ConversationComposerQuery", "ConversationComposer"),
    ("post", "/conversations/{conversation_id}/composer/command", "SlashCommandRead", "SlashCommandResult"),
    ("get", "/conversations/{conversation_id}/open", None, "ConversationOpenView"),
    ("get", "/conversations/{conversation_id}/delegated", None, "DelegatedActivityView"),
    ("get", "/conversations/{conversation_id}/delegated/{run_id}", None, "DelegatedRun"),
    ("get", "/conversations/{conversation_id}/draft", None, "DraftView"),
    ("get", "/conversations/{conversation_id}/steering", None, "ParentSteeringView"),
    ("get", "/conversations/{conversation_id}/queue", None, "ClientQueueView"),
    ("put", "/conversations/{conversation_id}/draft", "DraftSave", "DraftView"),
    ("post", "/resources/commands", "Command", "CommandReceipt"),
    ("post", "/resources/folder-selection", None, "FolderGrantView"),
    ("get", "/resources/setup/deck", None, "DeckSetupOptions"),
    ("get", "/resources/setup/artifact/{mode}", None, "ArtifactSetupOptions"),
    ("get", "/resources/library/{kind}", None, "ResourceChoicePage"),
    ("get", "/conversations/{conversation_id}/artifacts/{binding_id}/preview", None, "ArtifactPreview"),
    ("get", "/conversations/{conversation_id}/workspaces/{binding_id}/inspector", None, "WorkspaceInspector"),
    ("get", "/conversations/{conversation_id}/workspaces/{binding_id}/changes", None, "WorkspaceChanges"),
    ("get", "/conversations/{conversation_id}/workspaces/{binding_id}/directory", None, "WorkspaceDirectory"),
    ("get", "/conversations/{conversation_id}/workspaces/{binding_id}/file", None, "WorkspaceFile"),
    ("get", "/conversations/{conversation_id}/workspaces/{binding_id}/diff", None, "WorkspaceDiff"),
    ("get", "/conversations/{conversation_id}/workspaces/{binding_id}/change-sets", None, "WorkspaceChangeSetPage"),
    ("get", "/conversations/{conversation_id}/workspaces/{binding_id}/change-sets/{change_set_id}", None, "WorkspaceChangeSetFiles"),
)


def schema_bundle() -> dict:
    """Return deterministic JSON schemas, with no environment or runtime reads."""
    return {name: model.model_json_schema() for name, model in MODELS.items()}


def ts_type(schema: dict) -> str:
    """Translate the closed DTO subset of JSON Schema to TypeScript."""
    if "$ref" in schema:
        return schema["$ref"].split("/")[-1]
    if "const" in schema:
        return json.dumps(schema["const"])
    if "enum" in schema:
        return " | ".join(json.dumps(value) for value in schema["enum"])
    if "oneOf" in schema or "anyOf" in schema:
        return " | ".join(ts_type(value) for value in schema.get("oneOf", schema.get("anyOf")))
    kind = schema.get("type")
    if kind == "array":
        return f"Array<{ts_type(schema.get('items', {}))}>"
    if kind == "object" or "properties" in schema:
        required = schema.get("required", [])
        fields = [f"{json.dumps(name)}{'' if name in required else '?'}: {ts_type(value)}"
                  for name, value in schema.get("properties", {}).items()]
        if schema.get("additionalProperties") is not False:
            fields.append("[key: string]: " + (ts_type(schema["additionalProperties"]) if isinstance(schema.get("additionalProperties"), dict) else "unknown"))
        return "{ " + "; ".join(fields) + " }"
    return {"string": "string", "boolean": "boolean", "number": "number", "integer": "number", "null": "null"}.get(kind, "unknown")


def typescript(bundle: dict) -> str:
    definitions = {}
    for schema in bundle.values():
        for name, value in schema.get("$defs", {}).items():
            if name in definitions and definitions[name] != value:
                raise ValueError(f"Conflicting schema definition: {name}")
            definitions[name] = value
    lines = ["// Generated by scripts/generate_client_platform_contracts.py. Do not edit."]
    for name, value in sorted(definitions.items()):
        lines.append(f"export type {name} = {ts_type(value)};")
    for name, schema in bundle.items():
        if name not in definitions:
            lines.append(f"export type {name} = {ts_type(schema)};")
    lines.append("const wireSchemas: Record<string, any> = " + json.dumps(bundle, sort_keys=True, separators=(",", ":")) + ";")
    lines.append(r'''
function matches(schema: any, value: unknown, root: any): boolean {
  if (schema.$ref) return matches(root.$defs[schema.$ref.split('/').pop()], value, root);
  if (schema.const !== undefined && value !== schema.const) return false;
  if (schema.enum && !schema.enum.includes(value)) return false;
  if (schema.oneOf && schema.oneOf.filter((s: any) => matches(s, value, root)).length !== 1) return false;
  if (schema.anyOf && !schema.anyOf.some((s: any) => matches(s, value, root))) return false;
  if (schema.type === 'null') return value === null;
  if (schema.type === 'string') {
    if (typeof value !== 'string') return false;
    if (schema.minLength && [...value].length < schema.minLength) return false;
    if (schema.maxLength && [...value].length > schema.maxLength) return false;
    if (schema.pattern && !new RegExp(schema.pattern).test(value)) return false;
    if (schema.format === 'uuid' && !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value)) return false;
  }
  if (schema.type === 'boolean' && typeof value !== 'boolean') return false;
  if (schema.type === 'integer' || schema.type === 'number') {
    if (typeof value !== 'number' || !Number.isFinite(value)) return false;
    if (schema.type === 'integer' && !Number.isInteger(value)) return false;
    if (schema.minimum !== undefined && value < schema.minimum) return false;
    if (schema.maximum !== undefined && value > schema.maximum) return false;
  }
  if (schema.type === 'array') {
    if (!Array.isArray(value) || (schema.maxItems !== undefined && value.length > schema.maxItems)) return false;
    if (!value.every(v => matches(schema.items || {}, v, root))) return false;
  }
  if (schema.type === 'object') {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
    const object = value as Record<string, unknown>;
    if ((schema.required || []).some((key: string) => !(key in object))) return false;
    for (const [key, item] of Object.entries(object)) {
      const child = (schema.properties || {})[key];
      if (!child && schema.additionalProperties === false) return false;
      if (!child && typeof schema.additionalProperties === 'object' && !matches(schema.additionalProperties, item, root)) return false;
      if (child && !matches(child, item, root)) return false;
    }
  }
  return true;
}
export function isEvent(value: unknown): value is Event {
  if (!matches(wireSchemas.Event, value, wireSchemas.Event)) return false;
  const event = value as Event;
  return BigInt(event.source_sequence_start) <= BigInt(event.source_sequence_end);
}
export function isCommand(value: unknown): value is Command {
  return matches(wireSchemas.Command, value, wireSchemas.Command);
}
export function validateWire<T>(name: string, value: unknown): T {
  if (!wireSchemas[name] || !matches(wireSchemas[name], value, wireSchemas[name]))
    throw new Error('protocol_incompatible');
  if (name === 'Event' && !isEvent(value)) throw new Error('protocol_incompatible');
  if (name === 'EventPage' && !(value as EventPage).events.every(record => isEvent(record.event)))
    throw new Error('protocol_incompatible');
  return value as T;
}
function proofHeaders(proof?: SessionProof): Record<string, string> {
  return proof ? {'X-Client-Session': proof.client_session_id, 'X-CSRF-Token': proof.csrf_token} : {};
}
// Client pacing leaves headroom under the server's independently enforced
// budgets. Pending work is bounded and abortable; no mutation is replayed.
const budgets = new WeakMap<SessionProof, Map<string, {tokens:number; at:number; waiting:number}>>();
async function pace(proof: SessionProof | undefined, path: string, method: string, signal?: AbortSignal, body?: unknown): Promise<void> {
  if (!proof) return;
  const view = /^\/conversations\/[^/?]+(?:\/(?:open|workspace|delegated))?(?:\?|$)/.test(path);
  const observation = path.startsWith('/events') || /^\/conversations\/[^/]+\/subscriptions$/.test(path) || /^\/subscriptions\/[^/]+$/.test(path);
  const voiceEvent = /\/voice\/realtime\/[^/]+\/event$/.test(path);
  const eventType = body && typeof body === 'object' && 'event' in body && body.event && typeof body.event === 'object' && 'type' in body.event ? body.event.type : undefined;
  const type = body && typeof body === 'object' && 'type' in body ? body.type : undefined;
  const intent = body && typeof body === 'object' && 'payload' in body ? body.payload : body;
  const operation = intent && typeof intent === 'object' && 'operation' in intent ? intent.operation : undefined;
  const mcpCleanup = operation === 'disconnect' && (type === 'mcp.runtime.control' || path === '/settings/mcp/runtime/review');
  const control = method === 'POST' && path.endsWith('/commands') && ['conversation.stop', 'approval.resolve', 'mcp.runtime.install.cancel', 'document.batch.pause', 'document.batch.cancel', 'document.job.cancel'].includes(String(type))
    || method === 'DELETE' && /^\/uploads\/[^/]+$/.test(path);
  // Draft autosaves, uploads and ordinary commands consume one server bucket.
  // Stop/approval/cancel and ACK retain their independent admission paths.
  if (control || mcpCleanup || type === 'provider.subscription.cancel' || type === 'buddy.cancel' || /\/settings\/providers\/subscriptions\/(?:starts|probes)\/[^/]+\/cancel$/.test(path) || method === 'DELETE' && path === '/settings/providers/subscriptions/flows' || /^\/subscriptions\/[^/]+\/ack$/.test(path) || /\/voice\/(?:talk|realtime)\/[^/]+\/(?:stop|heartbeat)$/.test(path)) return;
  const lane = observation || voiceEvent && eventType !== 'function_call_ready' && eventType !== 'consult_fallback_needed' ? 'observation' : method === 'GET' ? (view ? 'view' : 'query') : 'mutation';
  let lanes = budgets.get(proof);
  if (!lanes) { lanes = new Map(); budgets.set(proof, lanes); }
  const [capacity, rate] = lane === 'query' ? [20, 2] : lane === 'view' ? [50, 4] : lane === 'observation' ? [100, 10] : [8, 1];
  let bucket = lanes.get(lane);
  if (!bucket) { bucket = {tokens:capacity,at:performance.now(),waiting:0}; lanes.set(lane,bucket); }
  if (bucket.waiting >= 128) throw {code:'rate_limited'};
  bucket.waiting++;
  try {
    while (true) {
      signal?.throwIfAborted();
      const now = performance.now();
      bucket.tokens = Math.min(capacity, bucket.tokens + Math.max(0, now - bucket.at) * rate / 1000);
      bucket.at = now;
      if (bucket.tokens >= 1) { bucket.tokens--; return; }
      await new Promise<void>((resolve,reject) => {
        const abort = () => { clearTimeout(timer); reject(new DOMException('Cancelled','AbortError')); };
        const timer = setTimeout(() => { signal?.removeEventListener('abort',abort); resolve(); }, Math.ceil((1-bucket.tokens)*1000/rate));
        signal?.addEventListener('abort',abort,{once:true});
      });
    }
  } finally { bucket.waiting--; }
}
async function jsonRequest<T>(baseUrl: string, path: string, schema: string,
  proof?: SessionProof, method = 'GET', body?: unknown, key?: string, signal?: AbortSignal, keepalive = false): Promise<T> {
  const encoded = body === undefined ? undefined : JSON.stringify(body);
  await pace(proof,path,method,signal,body);
  signal?.throwIfAborted();
  const response = await fetch(`${baseUrl}/api/v1${path}`, {
    method, credentials: 'same-origin', cache: 'no-store', signal, ...(keepalive ? {keepalive: true} : {}),
    headers: {...proofHeaders(proof), ...(body === undefined ? {} : {'Content-Type': 'application/json'}),
      ...(key ? {'Idempotency-Key': key} : {})},
    body: encoded,
  });
  const value: unknown = await response.json();
  if (!response.ok) throw validateWire<Problem>('Problem', value);
  return validateWire<T>(schema, value);
}
const id = encodeURIComponent;
const query = (values: Record<string, string | number | undefined>) => '?' + new URLSearchParams(
  Object.entries(values).filter(([,v]) => v !== undefined).map(([k,v]) => [k, String(v)]));

export async function handshake(baseUrl: string, body: Handshake, signal?: AbortSignal): Promise<HandshakeView> {
  validateWire<Handshake>('Handshake', body);
  return jsonRequest(baseUrl, '/handshake', 'HandshakeView', undefined, 'POST', body, undefined, signal);
}
export const listConversations = (base: string, proof: SessionProof, limit = 50, cursor?: string, signal?: AbortSignal, group = 'all'): Promise<ConversationPage> =>
  jsonRequest(base, '/conversations' + query({limit,cursor,group:group === 'all' ? undefined : group}), 'ConversationPage', proof, 'GET', undefined, undefined, signal);
export const getConversation = (base: string, proof: SessionProof, conversation: string, signal?: AbortSignal): Promise<ConversationView> =>
  jsonRequest(base, `/conversations/${id(conversation)}`, 'ConversationView', proof, 'GET', undefined, undefined, signal);
export const getTranscript = (base: string, proof: SessionProof, conversation: string, limit = 100, cursor?: string, signal?: AbortSignal): Promise<TranscriptPage> =>
  jsonRequest(base, `/conversations/${id(conversation)}/transcript` + query({limit,cursor}), 'TranscriptPage', proof, 'GET', undefined, undefined, signal);
export const getMessageText = (base: string, proof: SessionProof, conversation: string, message: string, cursor?: string, signal?: AbortSignal): Promise<LazyContent> =>
  jsonRequest(base, `/conversations/${id(conversation)}/text/${id(message)}` + query({cursor}), 'LazyContent', proof, 'GET', undefined, undefined, signal);
export const getChoices = (base: string, proof: SessionProof, signal?: AbortSignal): Promise<Choices> =>
  jsonRequest(base, '/choices', 'Choices', proof, 'GET', undefined, undefined, signal);
export const searchLibrary = (base: string, proof: SessionProof, text: string, conversation_id?: string, cursor?: string, signal?: AbortSignal): Promise<SearchPage> =>
  jsonRequest(base, '/search' + query({query:text,conversation_id,cursor}), 'SearchPage', proof, 'GET', undefined, undefined, signal);
export const getHistory = (base: string, proof: SessionProof, conversation: string, message_id?: string, cursor?: string, signal?: AbortSignal): Promise<TranscriptPage> =>
  jsonRequest(base, `/conversations/${id(conversation)}/history` + query({message_id,cursor}), 'TranscriptPage', proof, 'GET', undefined, undefined, signal);
export const getWorkspace = (base: string, proof: SessionProof, conversation: string, signal?: AbortSignal): Promise<ConversationWorkspace> =>
  jsonRequest(base, `/conversations/${id(conversation)}/workspace`, 'ConversationWorkspace', proof, 'GET', undefined, undefined, signal);
export const queryComposer = (base: string, proof: SessionProof, conversation: string, body: ConversationComposerQuery, signal?: AbortSignal): Promise<ConversationComposer> =>
  jsonRequest(base, `/conversations/${id(conversation)}/composer/query`, 'ConversationComposer', proof, 'POST', body, undefined, signal);
export const readComposerCommand = (base: string, proof: SessionProof, conversation: string, body: SlashCommandRead, signal?: AbortSignal): Promise<SlashCommandResult> =>
  jsonRequest(base, `/conversations/${id(conversation)}/composer/command`, 'SlashCommandResult', proof, 'POST', body, undefined, signal);
export const openConversation = (base: string, proof: SessionProof, conversation: string, signal?: AbortSignal): Promise<ConversationOpenView> =>
  jsonRequest(base, `/conversations/${id(conversation)}/open`, 'ConversationOpenView', proof, 'GET', undefined, undefined, signal);
export const getDraft = (base: string, proof: SessionProof, conversation: string, signal?: AbortSignal): Promise<DraftView> =>
  jsonRequest(base, `/conversations/${id(conversation)}/draft`, 'DraftView', proof, 'GET', undefined, undefined, signal);
export const getDelegatedActivity = (base: string, proof: SessionProof, conversation: string, cursor?: string, signal?: AbortSignal): Promise<DelegatedActivityView> =>
  jsonRequest(base, `/conversations/${id(conversation)}/delegated` + query({cursor}), 'DelegatedActivityView', proof, 'GET', undefined, undefined, signal);
export const getDelegatedRun = (base: string, proof: SessionProof, conversation: string, run: string, signal?: AbortSignal): Promise<DelegatedRun> =>
  jsonRequest(base, `/conversations/${id(conversation)}/delegated/${id(run)}`, 'DelegatedRun', proof, 'GET', undefined, undefined, signal);
export const getQueue = (base: string, proof: SessionProof, conversation: string, generation_id?: string, cursor?: string, signal?: AbortSignal): Promise<ClientQueueView> =>
  jsonRequest(base, `/conversations/${id(conversation)}/queue` + query({generation_id,cursor}), 'ClientQueueView', proof, 'GET', undefined, undefined, signal);
export const getSteering = (base: string, proof: SessionProof, conversation: string, generation_id?: string, cursor?: string, signal?: AbortSignal): Promise<ParentSteeringView> =>
  jsonRequest(base, `/conversations/${id(conversation)}/steering` + query({generation_id,cursor}), 'ParentSteeringView', proof, 'GET', undefined, undefined, signal);
export const saveDraft = (base: string, proof: SessionProof, conversation: string, body: DraftSave, signal?: AbortSignal): Promise<DraftView> =>
  jsonRequest(base, `/conversations/${id(conversation)}/draft`, 'DraftView', proof, 'PUT', body, undefined, signal);
export const getResourceLibrary = (base: string, proof: SessionProof, kind: 'artifact'|'workspace', cursor?: string, signal?: AbortSignal): Promise<ResourceChoicePage> =>
  jsonRequest(base, `/resources/library/${kind}` + query({cursor}), 'ResourceChoicePage', proof, 'GET', undefined, undefined, signal);
export const getDeckSetup = (base: string, proof: SessionProof, signal?: AbortSignal): Promise<DeckSetupOptions> =>
  jsonRequest(base, '/resources/setup/deck', 'DeckSetupOptions', proof, 'GET', undefined, undefined, signal);
export const getArtifactSetup = (base: string, mode: ArtifactSetupOptions['mode'], proof: SessionProof, signal?: AbortSignal): Promise<ArtifactSetupOptions> =>
  jsonRequest(base, '/resources/setup/artifact/' + id(mode ?? 'deck'), 'ArtifactSetupOptions', proof, 'GET', undefined, undefined, signal);
export const getProviderStatus = (base: string, proof: SessionProof, signal?: AbortSignal): Promise<ProviderStatusSnapshot> =>
  jsonRequest(base, '/settings/providers', 'ProviderStatusSnapshot', proof, 'GET', undefined, undefined, signal);
export const getLiveProviderStatus = (base: string, proof: SessionProof, signal?: AbortSignal): Promise<ProviderLiveSnapshot> =>
  jsonRequest(base, '/settings/providers/live', 'ProviderLiveSnapshot', proof, 'GET', undefined, undefined, signal);
export const refreshLiveProvider = (base: string, proof: SessionProof, provider: string, signal?: AbortSignal): Promise<ProviderCatalogRefresh> =>
  jsonRequest(base, `/settings/providers/live/${id(provider)}/refresh`, 'ProviderCatalogRefresh', proof, 'POST', undefined, undefined, signal);
export const getLiveProviderRefresh = (base: string, proof: SessionProof, signal?: AbortSignal): Promise<ProviderCatalogRefresh> =>
  jsonRequest(base, '/settings/providers/live/refresh', 'ProviderCatalogRefresh', proof, 'GET', undefined, undefined, signal);
export const testLiveProviderRuntime = (base: string, proof: SessionProof, provider: string, signal?: AbortSignal): Promise<ProviderRuntimeProbe> =>
  jsonRequest(base, `/settings/providers/live/${id(provider)}/runtime-test`, 'ProviderRuntimeProbe', proof, 'POST', undefined, undefined, signal);
export const getSavedEntities = (base: string, proof: SessionProof, search = '', entity_type?: string, status?: string, source?: string, tier?: string, limit = 25, cursor?: string, signal?: AbortSignal): Promise<EntitySummaryPage> =>
  jsonRequest(base, '/knowledge/entities' + query({query:search, entity_type, status, source, tier, limit, cursor}), 'EntitySummaryPage', proof, 'GET', undefined, undefined, signal);
export const getKnowledgeGraph = (base: string, proof: SessionProof, limit = 250, signal?: AbortSignal): Promise<KnowledgeGraphSnapshot> =>
  jsonRequest(base, '/knowledge/graph' + query({limit}), 'KnowledgeGraphSnapshot', proof, 'GET', undefined, undefined, signal);
export const getMonitorSnapshot = (base: string, proof: SessionProof, signal?: AbortSignal): Promise<MonitorSnapshot> =>
  jsonRequest(base, '/monitor', 'MonitorSnapshot', proof, 'GET', undefined, undefined, signal);
export const getMonitorLogs = (base: string, proof: SessionProof, limit = 200, signal?: AbortSignal): Promise<MonitorLogs> =>
  jsonRequest(base, '/monitor/logs' + query({limit}), 'MonitorLogs', proof, 'GET', undefined, undefined, signal);
export const reviewDreamRun = (base: string, proof: SessionProof, body: DreamRunRequest, signal?: AbortSignal): Promise<DreamRunReview> =>
  jsonRequest(base, '/monitor/dream/review', 'DreamRunReview', proof, 'POST', validateWire('DreamRunRequest', body), undefined, signal);
export const getDreamRunReceipt = (base: string, proof: SessionProof, command: string, signal?: AbortSignal): Promise<DreamRunReceipt> =>
  jsonRequest(base, `/monitor/dream/commands/${id(command)}`, 'DreamRunReceipt', proof, 'GET', undefined, undefined, signal);
export const sendDreamRun = (base: string, proof: SessionProof, command: DreamRunCommand, signal?: AbortSignal): Promise<DreamRunReceipt> =>
  jsonRequest(base, '/monitor/dream/commands', 'DreamRunReceipt', proof, 'POST', validateWire('DreamRunCommand', command), command.command_id, signal);
export const getSavedEntityDetail = (base: string, proof: SessionProof, entity: string, signal?: AbortSignal): Promise<KnowledgeEntityDetail> =>
  jsonRequest(base, `/knowledge/entities/${id(entity)}`, 'KnowledgeEntityDetail', proof, 'GET', undefined, undefined, signal);
export const getKnowledgeRecalls = (base: string, proof: SessionProof, signal?: AbortSignal): Promise<KnowledgeRecallPage> =>
  jsonRequest(base, '/knowledge/recalls', 'KnowledgeRecallPage', proof, 'GET', undefined, undefined, signal);
export const getKnowledgeChangeLog = (base: string, proof: SessionProof, signal?: AbortSignal): Promise<KnowledgeMemoryChangePage> =>
  jsonRequest(base, '/knowledge/change-log', 'KnowledgeMemoryChangePage', proof, 'GET', undefined, undefined, signal);
export const getSavedDocuments = (base: string, proof: SessionProof, search = '', status?: string, cursor?: string, signal?: AbortSignal): Promise<DocumentSummaryPage> =>
  jsonRequest(base, '/knowledge/documents' + query({query:search, status, cursor}), 'DocumentSummaryPage', proof, 'GET', undefined, undefined, signal);
export const getCachedTools = (base: string, proof: SessionProof, source?: 'core' | 'mcp' | 'plugin' | 'custom', search = '', cursor?: string, signal?: AbortSignal): Promise<ToolCatalogPage> =>
  jsonRequest(base, '/settings/tools' + query({source, query:search, cursor}), 'ToolCatalogPage', proof, 'GET', undefined, undefined, signal);
export const getSettingsSnapshot = (base: string, proof: SessionProof, signal?: AbortSignal): Promise<SettingsSnapshot> =>
  jsonRequest(base, '/settings/snapshot', 'SettingsSnapshot', proof, 'GET', undefined, undefined, signal);
export const reviewSettingsMutation = (base: string, proof: SessionProof, body: SettingsMutationRequest, signal?: AbortSignal): Promise<SettingsMutationReview> =>
  jsonRequest(base, '/settings/snapshot/review', 'SettingsMutationReview', proof, 'POST', body, undefined, signal);
export const getSettingsMutationReceipt = (base: string, proof: SessionProof, command: string, signal?: AbortSignal): Promise<SettingsMutationReceipt> =>
  jsonRequest(base, `/settings/snapshot/commands/${id(command)}`, 'SettingsMutationReceipt', proof, 'GET', undefined, undefined, signal);
export const sendSettingsMutation = (base: string, proof: SessionProof, command: SettingsMutationCommand, signal?: AbortSignal): Promise<SettingsMutationReceipt> =>
  jsonRequest(base, '/settings/snapshot/commands', 'SettingsMutationReceipt', proof, 'POST', command, command.command_id, signal);
export const getSavedTasks = (base: string, proof: SessionProof, search = '', enabled?: boolean, cursor?: string, signal?: AbortSignal): Promise<TaskSummaryPage> =>
  jsonRequest(base, '/tasks' + query({query:search, enabled: enabled === undefined ? undefined : String(enabled), cursor}), 'TaskSummaryPage', proof, 'GET', undefined, undefined, signal);
export const getTaskDeliveryDefaults = (base: string, proof: SessionProof, signal?: AbortSignal): Promise<TaskDeliverySnapshot> =>
  jsonRequest(base, '/tasks/delivery-defaults', 'TaskDeliverySnapshot', proof, 'GET', undefined, undefined, signal);
export const getTaskEditor = (base: string, proof: SessionProof, task: string, signal?: AbortSignal): Promise<TaskEditorSnapshot> =>
  jsonRequest(base, `/tasks/${id(task)}/editing`, 'TaskEditorSnapshot', proof, 'GET', undefined, undefined, signal);
export const getTaskGraph = (base: string, proof: SessionProof, task: string, signal?: AbortSignal): Promise<TaskGraphSnapshot> =>
  jsonRequest(base, `/tasks/${id(task)}/graph`, 'TaskGraphSnapshot', proof, 'GET', undefined, undefined, signal);
export const getTaskSettings = (base: string, proof: SessionProof, task: string, signal?: AbortSignal): Promise<TaskSettingsSnapshot> =>
  jsonRequest(base, `/tasks/${id(task)}/settings`, 'TaskSettingsSnapshot', proof, 'GET', undefined, undefined, signal);
export const reviewTaskSettings = (base: string, proof: SessionProof, task: string, fields: TaskSettingsFields, signal?: AbortSignal): Promise<TaskSettingsSnapshot> =>
  jsonRequest(base, `/tasks/${id(task)}/settings-review`, 'TaskSettingsSnapshot', proof, 'POST', fields, undefined, signal);
export async function downloadTaskWebhook(base: string, proof: SessionProof, task: string, revision: string, signal?: AbortSignal): Promise<Blob> {
  if (!/^[a-f0-9]{64}$/.test(revision)) throw new Error('protocol_incompatible');
  const response = await fetch(`${base}/api/v1/tasks/${id(task)}/webhook-configuration` + query({revision}), {
    credentials: 'same-origin', cache: 'no-store', headers: proofHeaders(proof), signal,
  });
  if (!response.ok) throw validateWire<Problem>('Problem', await response.json());
  if (!response.body || response.headers.get('Content-Type')?.split(';')[0] !== 'application/octet-stream' ||
      response.headers.get('Cache-Control') !== 'no-store') {
    try { await response.body?.cancel(); } catch { /* Closed response. */ }
    throw new Error('protocol_incompatible');
  }
  const reader = response.body.getReader();
  const parts: Uint8Array<ArrayBuffer>[] = [];
  let size = 0;
  try {
    while (true) {
      const part = await reader.read();
      if (part.done) break;
      size += part.value.byteLength;
      if (size > 65536) throw new Error('protocol_incompatible');
      parts.push(part.value);
    }
  } finally {
    try { await reader.cancel(); } catch { /* Closed response. */ }
    reader.releaseLock();
  }
  if (!size) throw new Error('protocol_incompatible');
  return new Blob(parts, {type: 'application/json'});
}
export const prepareArtifactShare = (base: string, proof: SessionProof, conversation: string, binding: string, options: ArtifactShareOptions, signal?: AbortSignal): Promise<ArtifactShareReview> =>
  jsonRequest(base, `/conversations/${id(conversation)}/artifacts/${id(binding)}/sharing-review`, 'ArtifactShareReview', proof, 'POST', options, undefined, signal);
export const getArtifactShareChannels = (base: string, proof: SessionProof, cursor?: string, signal?: AbortSignal): Promise<ArtifactShareChannels> =>
  jsonRequest(base, `/sharing/channels${cursor ? '?cursor=' + encodeURIComponent(cursor) : ''}`, 'ArtifactShareChannels', proof, 'GET', undefined, undefined, signal);
export const getTaskRunReview = (base: string, proof: SessionProof, task: string, signal?: AbortSignal): Promise<TaskRunReview> =>
  jsonRequest(base, `/tasks/${id(task)}/run-review`, 'TaskRunReview', proof, 'GET', undefined, undefined, signal);
export const getWorkspaceEditableFile = (base: string, proof: SessionProof, conversation: string, binding: string, path: string, signal?: AbortSignal): Promise<WorkspaceEditableFile> =>
  jsonRequest(base, `/conversations/${id(conversation)}/workspaces/${id(binding)}/editing` + query({path}), 'WorkspaceEditableFile', proof, 'GET', undefined, undefined, signal);
export const getWorkspaceImports = (base: string, proof: SessionProof, conversation: string, binding: string, cursor?: string, signal?: AbortSignal): Promise<WorkspaceImportPage> =>
  jsonRequest(base, `/conversations/${id(conversation)}/workspaces/${id(binding)}/imports` + query({cursor}), 'WorkspaceImportPage', proof, 'GET', undefined, undefined, signal);
export const getWorkspaceImportPatch = (base: string, proof: SessionProof, conversation: string, binding: string, pending: string, revision: string, offset: number, signal?: AbortSignal): Promise<WorkspaceImportPatch> =>
  jsonRequest(base, `/conversations/${id(conversation)}/workspaces/${id(binding)}/imports/patch` + query({pending_change_id:pending, revision, offset}), 'WorkspaceImportPatch', proof, 'GET', undefined, undefined, signal);
export const reviewWorkspaceImport = (base: string, proof: SessionProof, conversation: string, binding: string, pending: string, signal?: AbortSignal): Promise<WorkspaceImportReview> =>
  jsonRequest(base, `/conversations/${id(conversation)}/workspaces/${id(binding)}/imports/review`, 'WorkspaceImportReview', proof, 'POST', {pending_change_id:pending}, undefined, signal);
export const getWorkspaceImportReceipt = (base: string, proof: SessionProof, conversation: string, binding: string, command: string, signal?: AbortSignal): Promise<WorkspaceImportResult> =>
  jsonRequest(base, `/conversations/${id(conversation)}/workspaces/${id(binding)}/imports/commands/${id(command)}`, 'WorkspaceImportResult', proof, 'GET', undefined, undefined, signal);
export const reviewWorkspaceImportRecovery = (base: string, proof: SessionProof, conversation: string, binding: string, command: string, signal?: AbortSignal): Promise<WorkspaceImportReview> =>
  jsonRequest(base, `/conversations/${id(conversation)}/workspaces/${id(binding)}/imports/commands/${id(command)}/review`, 'WorkspaceImportReview', proof, 'POST', {}, undefined, signal);
export const sendWorkspaceImport = (base: string, proof: SessionProof, conversation: string, binding: string, command: Command, signal?: AbortSignal): Promise<WorkspaceImportResult> =>
  jsonRequest(base, `/conversations/${id(conversation)}/workspaces/${id(binding)}/imports/commands`, 'WorkspaceImportResult', proof, 'POST', command, command.command_id, signal);
export const reviewWorkspaceUndo = (base: string, proof: SessionProof, conversation: string, binding: string, changeSet: string, signal?: AbortSignal): Promise<WorkspaceUndoReview> =>
  jsonRequest(base, `/conversations/${id(conversation)}/workspaces/${id(binding)}/undo/review`, 'WorkspaceUndoReview', proof, 'POST', {change_set_id:changeSet}, undefined, signal);
export const getWorkspaceUndoReceipt = (base: string, proof: SessionProof, conversation: string, binding: string, command: string, signal?: AbortSignal): Promise<WorkspaceUndoResult> =>
  jsonRequest(base, `/conversations/${id(conversation)}/workspaces/${id(binding)}/undo/commands/${id(command)}`, 'WorkspaceUndoResult', proof, 'GET', undefined, undefined, signal);
export const reviewWorkspaceUndoRecovery = (base: string, proof: SessionProof, conversation: string, binding: string, command: string, signal?: AbortSignal): Promise<WorkspaceUndoReview> =>
  jsonRequest(base, `/conversations/${id(conversation)}/workspaces/${id(binding)}/undo/commands/${id(command)}/review`, 'WorkspaceUndoReview', proof, 'POST', {}, undefined, signal);
export const sendWorkspaceUndo = (base: string, proof: SessionProof, conversation: string, binding: string, command: Command, signal?: AbortSignal): Promise<WorkspaceUndoResult> =>
  jsonRequest(base, `/conversations/${id(conversation)}/workspaces/${id(binding)}/undo/commands`, 'WorkspaceUndoResult', proof, 'POST', command, command.command_id, signal);
export const getWorkspaceProcesses = (base: string, proof: SessionProof, conversation: string, binding: string, signal?: AbortSignal): Promise<WorkspaceProcessSnapshot> =>
  jsonRequest(base, `/conversations/${id(conversation)}/workspaces/${id(binding)}/processes`, 'WorkspaceProcessSnapshot', proof, 'GET', undefined, undefined, signal);
export const getWorkspaceProcessRecovery = (base: string, proof: SessionProof, conversation: string, binding: string, cursor?: string, signal?: AbortSignal): Promise<WorkspaceProcessRecoveryPage> =>
  jsonRequest(base, `/conversations/${id(conversation)}/workspaces/${id(binding)}/processes/recovery` + query({cursor}), 'WorkspaceProcessRecoveryPage', proof, 'GET', undefined, undefined, signal);
export const getWorkspaceProcessOutput = (base: string, proof: SessionProof, conversation: string, binding: string, process: string, cursor = 0, signal?: AbortSignal): Promise<WorkspaceProcessOutput> =>
  jsonRequest(base, `/conversations/${id(conversation)}/workspaces/${id(binding)}/processes/${id(process)}/output` + query({cursor}), 'WorkspaceProcessOutput', proof, 'GET', undefined, undefined, signal);
export const reviewWorkspaceProcess = (base: string, proof: SessionProof, conversation: string, binding: string, body: WorkspaceProcessReviewRequest, signal?: AbortSignal): Promise<WorkspaceProcessReview> =>
  jsonRequest(base, `/conversations/${id(conversation)}/workspaces/${id(binding)}/processes/review`, 'WorkspaceProcessReview', proof, 'POST', body, undefined, signal);
export const getTaskRuns = (base: string, proof: SessionProof, task: string, cursor?: string, signal?: AbortSignal): Promise<TaskRunPage> =>
  jsonRequest(base, `/tasks/${id(task)}/runs` + query({cursor}), 'TaskRunPage', proof, 'GET', undefined, undefined, signal);
export const getTaskRun = (base: string, proof: SessionProof, task: string, run: string, signal?: AbortSignal): Promise<TaskRunSummary> =>
  jsonRequest(base, `/tasks/${id(task)}/runs/${id(run)}`, 'TaskRunSummary', proof, 'GET', undefined, undefined, signal);
export const getTaskApprovals = (base: string, proof: SessionProof, task: string, run: string, cursor?: string, signal?: AbortSignal): Promise<TaskApprovalPage> =>
  jsonRequest(base, `/tasks/${id(task)}/runs/${id(run)}/approvals` + query({cursor}), 'TaskApprovalPage', proof, 'GET', undefined, undefined, signal);
export const getCachedModels = (base: string, proof: SessionProof, provider_id?: string, search = '', cursor?: string, signal?: AbortSignal): Promise<CachedModelPage> =>
  jsonRequest(base, '/settings/models' + query({provider_id, query:search, cursor}), 'CachedModelPage', proof, 'GET', undefined, undefined, signal);
export const getModelsSettings = (base: string, proof: SessionProof, signal?: AbortSignal): Promise<ModelsSettingsState> =>
  jsonRequest(base, '/settings/models/state', 'ModelsSettingsState', proof, 'GET', undefined, undefined, signal);
export const updateModelSurface = (base: string, proof: SessionProof, body: ModelSurfaceMutation, signal?: AbortSignal): Promise<ModelsSettingsState> =>
  jsonRequest(base, '/settings/models/surface', 'ModelsSettingsState', proof, 'POST', body, undefined, signal);
export const updateModelContext = (base: string, proof: SessionProof, body: ModelContextMutation, signal?: AbortSignal): Promise<ModelsSettingsState> =>
  jsonRequest(base, '/settings/models/context', 'ModelsSettingsState', proof, 'POST', body, undefined, signal);
export const getAgentRuntimeSettings = (base: string, proof: SessionProof, signal?: AbortSignal): Promise<AgentRuntimeSettingsState> =>
  jsonRequest(base, '/settings/models/agents', 'AgentRuntimeSettingsState', proof, 'GET', undefined, undefined, signal);
export const saveAgentRuntimeSettings = (base: string, proof: SessionProof, body: AgentRuntimeSettingsState, signal?: AbortSignal): Promise<AgentRuntimeSettingsState> =>
  jsonRequest(base, '/settings/models/agents', 'AgentRuntimeSettingsState', proof, 'POST', body, undefined, signal);
export const resetAgentRuntimeSettings = (base: string, proof: SessionProof, signal?: AbortSignal): Promise<AgentRuntimeSettingsState> =>
  jsonRequest(base, '/settings/models/agents/reset', 'AgentRuntimeSettingsState', proof, 'POST', undefined, undefined, signal);
export const getModelCatalogSummary = (base: string, proof: SessionProof, surface: string, signal?: AbortSignal): Promise<ModelCatalogSummary> =>
  jsonRequest(base, '/settings/models/catalog-summary' + query({surface}), 'ModelCatalogSummary', proof, 'GET', undefined, undefined, signal);
export const getModelCatalogPage = (base: string, proof: SessionProof, surface: string, provider_id?: string, search = '', cursor?: string, signal?: AbortSignal): Promise<CachedModelPage> =>
  jsonRequest(base, '/settings/models/catalog' + query({surface, provider_id, query:search, cursor}), 'CachedModelPage', proof, 'GET', undefined, undefined, signal);
export const refreshModelsCatalog = (base: string, proof: SessionProof, signal?: AbortSignal): Promise<ProviderCatalogRefresh> =>
  jsonRequest(base, '/settings/models/refresh', 'ProviderCatalogRefresh', proof, 'POST', undefined, undefined, signal);
export const refreshModelCameras = (base: string, proof: SessionProof, signal?: AbortSignal): Promise<ModelCameraList> =>
  jsonRequest(base, '/settings/models/cameras/refresh', 'ModelCameraList', proof, 'POST', undefined, undefined, signal);
export const pickFolder = (base: string, proof: SessionProof, signal?: AbortSignal): Promise<FolderGrantView> =>
  jsonRequest(base, '/resources/folder-selection', 'FolderGrantView', proof, 'POST', undefined, undefined, signal);
export const getWikiStatus = (base: string, proof: SessionProof, folder_grant?: string, signal?: AbortSignal): Promise<WikiStatus> =>
  jsonRequest(base, '/settings/wiki' + query({folder_grant}), 'WikiStatus', proof, 'GET', undefined, undefined, signal);
export const openWikiFolder = (base: string, proof: SessionProof, signal?: AbortSignal): Promise<WikiOpenFolderResult> =>
  jsonRequest(base, '/settings/wiki/open-folder', 'WikiOpenFolderResult', proof, 'POST', undefined, undefined, signal);
export const getWikiArticles = (base: string, proof: SessionProof, folder_grant: string, cursor?: string, signal?: AbortSignal): Promise<WikiArticlePage> =>
  jsonRequest(base, '/settings/wiki/articles' + query({folder_grant,cursor}), 'WikiArticlePage', proof, 'GET', undefined, undefined, signal);
export const getWikiArticle = (base: string, proof: SessionProof, folder_grant: string, article: string, signal?: AbortSignal): Promise<WikiArticle> =>
  jsonRequest(base, `/settings/wiki/articles/${id(article)}` + query({folder_grant}), 'WikiArticle', proof, 'GET', undefined, undefined, signal);
export const reviewWiki = (base: string, proof: SessionProof, body: WikiReviewRequest, signal?: AbortSignal): Promise<WikiReview> =>
  jsonRequest(base, '/settings/wiki/review', 'WikiReview', proof, 'POST', validateWire('WikiReviewRequest', body), undefined, signal);
export const getWikiReceipt = (base: string, proof: SessionProof, folder_grant: string, command: string, signal?: AbortSignal): Promise<WikiReceipt> =>
  jsonRequest(base, `/settings/wiki/commands/${id(command)}` + query({folder_grant}), 'WikiReceipt', proof, 'GET', undefined, undefined, signal);
export const sendWiki = (base: string, proof: SessionProof, command: Command, signal?: AbortSignal): Promise<WikiReceipt> =>
  jsonRequest(base, '/settings/wiki/commands', 'WikiReceipt', proof, 'POST', validateWire('Command', command), command.command_id, signal);
export const getChannels = (base: string, proof: SessionProof, search = '', signal?: AbortSignal): Promise<ChannelPage> =>
  jsonRequest(base, '/settings/channels' + query({query:search}), 'ChannelPage', proof, 'GET', undefined, undefined, signal);
export const reviewChannel = (base: string, proof: SessionProof, body: ChannelActionRequest, signal?: AbortSignal): Promise<ChannelActionReview> =>
  jsonRequest(base, '/settings/channels/review', 'ChannelActionReview', proof, 'POST', validateWire('ChannelActionRequest', body), undefined, signal);
export const getChannelReceipt = (base: string, proof: SessionProof, channel: string, command: string, signal?: AbortSignal): Promise<ChannelReceipt> =>
  jsonRequest(base, `/settings/channels/${id(channel)}/commands/${id(command)}`, 'ChannelReceipt', proof, 'GET', undefined, undefined, signal);
export const sendChannel = (base: string, proof: SessionProof, command: Command, signal?: AbortSignal): Promise<ChannelReceipt> =>
  jsonRequest(base, '/settings/channels/commands', 'ChannelReceipt', proof, 'POST', validateWire('Command', command), command.command_id, signal);
export const getPlugins = (base: string, proof: SessionProof, search = '', source = 'all', cursor?: string, signal?: AbortSignal): Promise<PluginCatalogPage> =>
  jsonRequest(base, '/settings/plugins' + query({query:search,source,cursor}), 'PluginCatalogPage', proof, 'GET', undefined, undefined, signal);
export const getPlugin = (base: string, proof: SessionProof, plugin: string, signal?: AbortSignal): Promise<PluginDetail> =>
  jsonRequest(base, `/settings/plugins/${id(plugin)}`, 'PluginDetail', proof, 'GET', undefined, undefined, signal);
export const reviewPlugin = (base: string, proof: SessionProof, plugin: string, body: PluginReviewRequest, signal?: AbortSignal): Promise<PluginReview> =>
  jsonRequest(base, `/settings/plugins/${id(plugin)}/review`, 'PluginReview', proof, 'POST', validateWire('PluginReviewRequest', body), undefined, signal);
export const getPluginReceipt = (base: string, proof: SessionProof, plugin: string, command: string, signal?: AbortSignal): Promise<PluginReceipt> =>
  jsonRequest(base, `/settings/plugins/${id(plugin)}/receipts/${id(command)}`, 'PluginReceipt', proof, 'GET', undefined, undefined, signal);
export const sendPlugin = (base: string, proof: SessionProof, plugin: string, command: Command, signal?: AbortSignal): Promise<PluginReceipt> =>
  jsonRequest(base, `/settings/plugins/${id(plugin)}/commands`, 'PluginReceipt', proof, 'POST', validateWire('Command', command), command.command_id, signal);
export const getSkills = (base: string, proof: SessionProof, search = '', source?: string, cursor?: string, signal?: AbortSignal): Promise<SkillPage> =>
  jsonRequest(base, '/settings/skills' + query({query:search,source,cursor}), 'SkillPage', proof, 'GET', undefined, undefined, signal);
export const getSkill = (base: string, proof: SessionProof, skill: string, signal?: AbortSignal): Promise<SkillDetail> =>
  jsonRequest(base, `/settings/skills/items/${id(skill)}`, 'SkillDetail', proof, 'GET', undefined, undefined, signal);
export const getSkillProposals = (base: string, proof: SessionProof, signal?: AbortSignal): Promise<SkillProposalPage> =>
  jsonRequest(base, '/settings/skill-proposals', 'SkillProposalPage', proof, 'GET', undefined, undefined, signal);
export const reviewSkill = (base: string, proof: SessionProof, body: SkillReviewRequest, signal?: AbortSignal): Promise<SkillReview> =>
  jsonRequest(base, '/settings/skills/review', 'SkillReview', proof, 'POST', validateWire('SkillReviewRequest', body), undefined, signal);
export const getSkillReceipt = (base: string, proof: SessionProof, command: string, signal?: AbortSignal): Promise<SkillReceipt> =>
  jsonRequest(base, `/settings/skills/commands/${id(command)}`, 'SkillReceipt', proof, 'GET', undefined, undefined, signal);
export const sendSkill = (base: string, proof: SessionProof, command: Command, signal?: AbortSignal): Promise<SkillReceipt> =>
  jsonRequest(base, '/settings/skills/commands', 'SkillReceipt', proof, 'POST', validateWire('Command', command), command.command_id, signal);
export const getGoals = (base: string, proof: SessionProof, conversation: string, search = '', cursor?: string, signal?: AbortSignal): Promise<GoalPage> =>
  jsonRequest(base, `/conversations/${id(conversation)}/goals` + query({query:search,cursor}), 'GoalPage', proof, 'GET', undefined, undefined, signal);
export const getGoal = (base: string, proof: SessionProof, conversation: string, goal: string, signal?: AbortSignal): Promise<GoalDetail> =>
  jsonRequest(base, `/conversations/${id(conversation)}/goals/items/${id(goal)}`, 'GoalDetail', proof, 'GET', undefined, undefined, signal);
export const reviewGoal = (base: string, proof: SessionProof, conversation: string, body: GoalCommandPayload, signal?: AbortSignal): Promise<GoalReview> =>
  jsonRequest(base, `/conversations/${id(conversation)}/goals/review`, 'GoalReview', proof, 'POST', validateWire('GoalCommandPayload', body), undefined, signal);
export const getGoalReceipt = (base: string, proof: SessionProof, conversation: string, command: string, signal?: AbortSignal): Promise<GoalReceipt> =>
  jsonRequest(base, `/conversations/${id(conversation)}/goals/commands/${id(command)}`, 'GoalReceipt', proof, 'GET', undefined, undefined, signal);
export const sendGoal = (base: string, proof: SessionProof, conversation: string, command: Command, signal?: AbortSignal): Promise<GoalReceipt> =>
  jsonRequest(base, `/conversations/${id(conversation)}/goals/commands`, 'GoalReceipt', proof, 'POST', validateWire('Command', command), command.command_id, signal);
export const getProfiles = (base: string, proof: SessionProof, search = '', scope?: string, cursor?: string, signal?: AbortSignal): Promise<ProfilePage> =>
  jsonRequest(base, '/settings/profiles' + query({query:search,scope,cursor}), 'ProfilePage', proof, 'GET', undefined, undefined, signal);
export const getProfile = (base: string, proof: SessionProof, profile: string, signal?: AbortSignal): Promise<ProfileDetail> =>
  jsonRequest(base, `/settings/profiles/items/${id(profile)}`, 'ProfileDetail', proof, 'GET', undefined, undefined, signal);
export const reviewProfile = (base: string, proof: SessionProof, body: ProfileCommandPayload, signal?: AbortSignal): Promise<ProfileReview> =>
  jsonRequest(base, '/settings/profiles/review', 'ProfileReview', proof, 'POST', validateWire('ProfileCommandPayload', body), undefined, signal);
export const getProfileReceipt = (base: string, proof: SessionProof, profile: string, command: string, signal?: AbortSignal): Promise<ProfileReceipt> =>
  jsonRequest(base, `/settings/profiles/${id(profile)}/receipts/${id(command)}`, 'ProfileReceipt', proof, 'GET', undefined, undefined, signal);
export const sendProfile = (base: string, proof: SessionProof, command: Command, signal?: AbortSignal): Promise<ProfileReceipt> =>
  jsonRequest(base, '/settings/profiles/commands', 'ProfileReceipt', proof, 'POST', validateWire('Command', command), command.command_id, signal);
export const getDeveloperRepository = (base: string, proof: SessionProof, conversation: string, binding: string, signal?: AbortSignal): Promise<DeveloperRepositorySnapshot> =>
  jsonRequest(base, `/conversations/${id(conversation)}/workspaces/${id(binding)}/repository`, 'DeveloperRepositorySnapshot', proof, 'GET', undefined, undefined, signal);
export const reviewDeveloperRepository = (base: string, proof: SessionProof, conversation: string, binding: string, body: DeveloperRepositoryReviewRequest, signal?: AbortSignal): Promise<DeveloperRepositoryReview> =>
  jsonRequest(base, `/conversations/${id(conversation)}/workspaces/${id(binding)}/repository/review`, 'DeveloperRepositoryReview', proof, 'POST', validateWire('DeveloperRepositoryReviewRequest', body), undefined, signal);
export const getDeveloperRepositoryReceipt = (base: string, proof: SessionProof, conversation: string, binding: string, command: string, signal?: AbortSignal): Promise<DeveloperRepositoryReceipt> =>
  jsonRequest(base, `/conversations/${id(conversation)}/workspaces/${id(binding)}/repository/commands/${id(command)}`, 'DeveloperRepositoryReceipt', proof, 'GET', undefined, undefined, signal);
export const sendDeveloperRepository = (base: string, proof: SessionProof, conversation: string, binding: string, command: Command, signal?: AbortSignal): Promise<DeveloperRepositoryReceipt> =>
  jsonRequest(base, `/conversations/${id(conversation)}/workspaces/${id(binding)}/repository/commands`, 'DeveloperRepositoryReceipt', proof, 'POST', validateWire('Command', command), command.command_id, signal);
export const getConversationActions = (base: string, proof: SessionProof, conversation: string, signal?: AbortSignal): Promise<ConversationActionSnapshot> =>
  jsonRequest(base, `/conversations/${id(conversation)}/actions`, 'ConversationActionSnapshot', proof, 'GET', undefined, undefined, signal);
export const reviewConversationAction = (base: string, proof: SessionProof, conversation: string, body: ConversationActionReviewRequest, signal?: AbortSignal): Promise<ConversationActionReview> =>
  jsonRequest(base, `/conversations/${id(conversation)}/actions/review`, 'ConversationActionReview', proof, 'POST', validateWire('ConversationActionReviewRequest', body), undefined, signal);
export const getConversationActionReceipt = (base: string, proof: SessionProof, conversation: string, command: string, signal?: AbortSignal): Promise<ConversationActionReceipt> =>
  jsonRequest(base, `/conversations/${id(conversation)}/actions/commands/${id(command)}`, 'ConversationActionReceipt', proof, 'GET', undefined, undefined, signal);
export const sendConversationAction = (base: string, proof: SessionProof, conversation: string, command: ConversationActionCommand, signal?: AbortSignal): Promise<ConversationActionReceipt> =>
  jsonRequest(base, `/conversations/${id(conversation)}/actions/commands`, 'ConversationActionReceipt', proof, 'POST', validateWire('ConversationActionCommand', command), command.command_id, signal);
export const getBrowserControls = (base: string, proof: SessionProof, conversation: string, signal?: AbortSignal): Promise<BrowserControlSnapshot> =>
  jsonRequest(base, `/conversations/${id(conversation)}/browser`, 'BrowserControlSnapshot', proof, 'GET', undefined, undefined, signal);
export const reviewBrowserControl = (base: string, proof: SessionProof, conversation: string, body: BrowserReviewRequest, signal?: AbortSignal): Promise<BrowserReview> =>
  jsonRequest(base, `/conversations/${id(conversation)}/browser/review`, 'BrowserReview', proof, 'POST', validateWire('BrowserReviewRequest', body), undefined, signal);
export const getBrowserControlReceipt = (base: string, proof: SessionProof, conversation: string, command: string, signal?: AbortSignal): Promise<BrowserReceipt> =>
  jsonRequest(base, `/conversations/${id(conversation)}/browser/commands/${id(command)}`, 'BrowserReceipt', proof, 'GET', undefined, undefined, signal);
export const sendBrowserControl = (base: string, proof: SessionProof, conversation: string, command: Command, signal?: AbortSignal): Promise<BrowserReceipt> =>
  jsonRequest(base, `/conversations/${id(conversation)}/browser/commands`, 'BrowserReceipt', proof, 'POST', validateWire('Command', command), command.command_id, signal);
export type ArtifactAuthoring = { previewId: string; capability: string };
export const getArtifactPreview = (base: string, proof: SessionProof, conversation: string, binding: string, page_id?: string, known_revision?: string, signal?: AbortSignal, authoring?: ArtifactAuthoring): Promise<ArtifactPreview> =>
  jsonRequest(base, `/conversations/${id(conversation)}/artifacts/${id(binding)}/preview` + query({page_id,known_revision,...(authoring ? {authoring:'true',preview_id:authoring.previewId,capability:authoring.capability} : {})}), 'ArtifactPreview', proof, 'GET', undefined, undefined, signal);
export const getArtifactEditing = (base: string, proof: SessionProof, conversation: string, binding: string, page_id?: string, page_cursor?: string, element_cursor?: string, history_cursor?: string, element_id?: string, limit = 25, signal?: AbortSignal): Promise<ArtifactEditingState> =>
  jsonRequest(base, `/conversations/${id(conversation)}/artifacts/${id(binding)}/editing` + query({page_id,page_cursor,element_cursor,history_cursor,element_id,limit}), 'ArtifactEditingState', proof, 'GET', undefined, undefined, signal);
export const getArtifactLifecycle = (base: string, proof: SessionProof, conversation: string, binding: string, expected_revision: string, signal?: AbortSignal): Promise<ArtifactLifecycleState> =>
  jsonRequest(base, `/conversations/${id(conversation)}/artifacts/${id(binding)}/lifecycle` + query({expected_revision}), 'ArtifactLifecycleState', proof, 'GET', undefined, undefined, signal);
export const getArtifactStaticPreview = (base: string, proof: SessionProof, conversation: string, binding: string, page_id: string, signal?: AbortSignal): Promise<ArtifactPreview> =>
  jsonRequest(base, `/conversations/${id(conversation)}/artifacts/${id(binding)}/preview` + query({page_id,static_page:'true'}), 'ArtifactPreview', proof, 'GET', undefined, undefined, signal);
export type DesignControlOptions = {page_id?: string; element_id?: string; section?: DesignControlsState['section']; cursor?: string; limit?: number};
export const getDesignControls = (base: string, proof: SessionProof, conversation: string, binding: string, options: DesignControlOptions, signal?: AbortSignal): Promise<DesignControlsState> =>
  jsonRequest(base, `/conversations/${id(conversation)}/artifacts/${id(binding)}/design-controls` + query(options), 'DesignControlsState', proof, 'GET', undefined, undefined, signal);
export type DesignReviewOptions = {page_id?: string; scope?: DesignReviewState['scope']; cursor?: string; limit?: number};
export const getDesignReview = (base: string, proof: SessionProof, conversation: string, binding: string, options: DesignReviewOptions, signal?: AbortSignal): Promise<DesignReviewState> =>
  jsonRequest(base, `/conversations/${id(conversation)}/artifacts/${id(binding)}/design-review` + query(options), 'DesignReviewState', proof, 'GET', undefined, undefined, signal);
export type DesignPresentationOptions = {page_index?: number; cursor?: string; limit?: number};
export const getDesignPresentation = (base: string, proof: SessionProof, conversation: string, binding: string, options: DesignPresentationOptions, signal?: AbortSignal): Promise<DesignPresentationState> =>
  jsonRequest(base, `/conversations/${id(conversation)}/artifacts/${id(binding)}/presentation` + query(options), 'DesignPresentationState', proof, 'GET', undefined, undefined, signal);
export const getArtifactReviewDraft = (base: string, proof: SessionProof, conversation: string, binding: string, body: ArtifactReviewDraftRequest, signal?: AbortSignal): Promise<ArtifactReviewDraft> =>
  jsonRequest(base, `/conversations/${id(conversation)}/artifacts/${id(binding)}/review-draft`, 'ArtifactReviewDraft', proof, 'POST', body, undefined, signal);
export const reviewArtifactPreset = (base: string, proof: SessionProof, conversation: string, binding: string, body: ArtifactPresetReviewRequest, signal?: AbortSignal): Promise<ArtifactPresetReview> =>
  jsonRequest(base, `/conversations/${id(conversation)}/artifacts/${id(binding)}/preset-review`, 'ArtifactPresetReview', proof, 'POST', body, undefined, signal);
export const getWorkspaceInspector = (base: string, proof: SessionProof, conversation: string, binding: string, refresh = false, signal?: AbortSignal): Promise<WorkspaceInspector> =>
  jsonRequest(base, `/conversations/${id(conversation)}/workspaces/${id(binding)}/inspector` + query({refresh:refresh?'true':'false'}), 'WorkspaceInspector', proof, 'GET', undefined, undefined, signal);
export const getWorkspaceChanges = (base: string, proof: SessionProof, conversation: string, binding: string, revision?: string, cursor?: string, signal?: AbortSignal): Promise<WorkspaceChanges> =>
  jsonRequest(base, `/conversations/${id(conversation)}/workspaces/${id(binding)}/changes` + query({revision,cursor}), 'WorkspaceChanges', proof, 'GET', undefined, undefined, signal);
export const getWorkspaceDirectory = (base: string, proof: SessionProof, conversation: string, binding: string, directory = '', cursor?: string, revision?: string, signal?: AbortSignal): Promise<WorkspaceDirectory> =>
  jsonRequest(base, `/conversations/${id(conversation)}/workspaces/${id(binding)}/directory` + query({directory,cursor,revision}), 'WorkspaceDirectory', proof, 'GET', undefined, undefined, signal);
export const getWorkspaceFile = (base: string, proof: SessionProof, conversation: string, binding: string, path: string, offset = 0, revision?: string, signal?: AbortSignal): Promise<WorkspaceFile> =>
  jsonRequest(base, `/conversations/${id(conversation)}/workspaces/${id(binding)}/file` + query({path,offset,revision}), 'WorkspaceFile', proof, 'GET', undefined, undefined, signal);
export const getWorkspaceDiff = (base: string, proof: SessionProof, conversation: string, binding: string, path: string, snapshot_revision: string, offset = 0, revision?: string, signal?: AbortSignal): Promise<WorkspaceDiff> =>
  jsonRequest(base, `/conversations/${id(conversation)}/workspaces/${id(binding)}/diff` + query({path,snapshot_revision,offset,revision}), 'WorkspaceDiff', proof, 'GET', undefined, undefined, signal);
export const getWorkspaceChangeSets = (base: string, proof: SessionProof, conversation: string, binding: string, revision: string, cursor?: string, signal?: AbortSignal): Promise<WorkspaceChangeSetPage> =>
  jsonRequest(base, `/conversations/${id(conversation)}/workspaces/${id(binding)}/change-sets` + query({revision,cursor}), 'WorkspaceChangeSetPage', proof, 'GET', undefined, undefined, signal);
export const getWorkspaceChangeSetFiles = (base: string, proof: SessionProof, conversation: string, binding: string, change: string, revision: string, cursor?: string, signal?: AbortSignal): Promise<WorkspaceChangeSetFiles> =>
  jsonRequest(base, `/conversations/${id(conversation)}/workspaces/${id(binding)}/change-sets/${id(change)}` + query({revision,cursor}), 'WorkspaceChangeSetFiles', proof, 'GET', undefined, undefined, signal);
export const getLazyContent = (base: string, proof: SessionProof, conversation: string, message: string,
  limit_bytes = 65536, cursor?: string, signal?: AbortSignal): Promise<LazyContent> =>
  jsonRequest(base, `/conversations/${id(conversation)}/content/${id(message)}` + query({limit_bytes,cursor}), 'LazyContent', proof, 'GET', undefined, undefined, signal);
export const getReceipt = (base: string, proof: SessionProof, command: string, signal?: AbortSignal): Promise<CommandReceipt> =>
  jsonRequest(base, `/commands/${id(command)}`, 'CommandReceipt', proof, 'GET', undefined, undefined, signal);
export const getProviderSettings = (base: string, proof: SessionProof, provider: string, signal?: AbortSignal): Promise<ProviderSettingsSnapshot> =>
  jsonRequest(base, `/settings/providers/${id(provider)}/credential`, 'ProviderSettingsSnapshot', proof, 'GET', undefined, undefined, signal);
export const getKnowledgeEditor = (base: string, proof: SessionProof, entity: string | null, signal?: AbortSignal): Promise<KnowledgeEditorState> =>
  jsonRequest(base, '/knowledge/entities/editor' + query({entity_id:entity ?? undefined}), 'KnowledgeEditorState', proof, 'GET', undefined, undefined, signal);
export const reviewKnowledge = (base: string, proof: SessionProof, body: KnowledgeReviewRequest, signal?: AbortSignal): Promise<KnowledgeReview> =>
  jsonRequest(base, '/knowledge/entities/review', 'KnowledgeReview', proof, 'POST', validateWire('KnowledgeReviewRequest', body), undefined, signal);
export const getKnowledgeReceipt = (base: string, proof: SessionProof, command: string, signal?: AbortSignal): Promise<KnowledgeReceipt> =>
  jsonRequest(base, `/knowledge/entities/commands/${id(command)}`, 'KnowledgeReceipt', proof, 'GET', undefined, undefined, signal);
export const sendKnowledge = (base: string, proof: SessionProof, command: Command, signal?: AbortSignal): Promise<KnowledgeReceipt> =>
  jsonRequest(base, '/knowledge/entities/commands', 'KnowledgeReceipt', proof, 'POST', command, command.command_id, signal);
export const reviewKnowledgeMaintenance = (base: string, proof: SessionProof, body: KnowledgeMaintenanceRequest, signal?: AbortSignal): Promise<KnowledgeMaintenanceReview> =>
  jsonRequest(base, '/knowledge/maintenance/review', 'KnowledgeMaintenanceReview', proof, 'POST', validateWire('KnowledgeMaintenanceRequest', body), undefined, signal);
export const getKnowledgeMaintenanceReceipt = (base: string, proof: SessionProof, command: string, signal?: AbortSignal): Promise<KnowledgeMaintenanceReceipt> =>
  jsonRequest(base, `/knowledge/maintenance/commands/${id(command)}`, 'KnowledgeMaintenanceReceipt', proof, 'GET', undefined, undefined, signal);
export const sendKnowledgeMaintenance = (base: string, proof: SessionProof, command: KnowledgeMaintenanceCommand, signal?: AbortSignal): Promise<KnowledgeMaintenanceReceipt> =>
  jsonRequest(base, '/knowledge/maintenance/commands', 'KnowledgeMaintenanceReceipt', proof, 'POST', validateWire('KnowledgeMaintenanceCommand', command), command.command_id, signal);
export const getKnowledgeRelations = (base: string, proof: SessionProof, entity: string, cursor?: string, signal?: AbortSignal): Promise<KnowledgeRelationPage> =>
  jsonRequest(base, '/knowledge/relations' + query({entity_id:entity, cursor}), 'KnowledgeRelationPage', proof, 'GET', undefined, undefined, signal);
export const reviewKnowledgeRelation = (base: string, proof: SessionProof, body: KnowledgeRelationReviewRequest, signal?: AbortSignal): Promise<KnowledgeRelationReview> =>
  jsonRequest(base, '/knowledge/relations/review', 'KnowledgeRelationReview', proof, 'POST', validateWire('KnowledgeRelationReviewRequest', body), undefined, signal);
export const getKnowledgeRelationReceipt = (base: string, proof: SessionProof, command: string, signal?: AbortSignal): Promise<KnowledgeRelationReceipt> =>
  jsonRequest(base, `/knowledge/relations/commands/${id(command)}`, 'KnowledgeRelationReceipt', proof, 'GET', undefined, undefined, signal);
export const sendKnowledgeRelation = (base: string, proof: SessionProof, command: Command, signal?: AbortSignal): Promise<KnowledgeRelationReceipt> =>
  jsonRequest(base, '/knowledge/relations/commands', 'KnowledgeRelationReceipt', proof, 'POST', command, command.command_id, signal);
export const getMcpTestedCatalog = (base: string, proof: SessionProof, server: string, command: string, search: string, cursor?: string, signal?: AbortSignal): Promise<McpTestedCatalogPage> =>
  jsonRequest(base, '/settings/mcp/catalog' + query({server_id:server,test_command_id:command,query:search,cursor}), 'McpTestedCatalogPage', proof, 'GET', undefined, undefined, signal);
export const getRuntimeInstallation = (base: string, proof: SessionProof, runtime: string, signal?: AbortSignal): Promise<RuntimeInstallationSnapshot> =>
  jsonRequest(base, `/settings/mcp/installations/${encodeURIComponent(runtime)}`, 'RuntimeInstallationSnapshot', proof, 'GET', undefined, undefined, signal);
export const getDocumentQueue = (base: string, proof: SessionProof, kind: string, batch_id?: string, cursor?: string, signal?: AbortSignal): Promise<DocumentQueuePage> =>
  jsonRequest(base, '/documents/queue' + query({kind,batch_id,cursor}), 'DocumentQueuePage', proof, 'GET', undefined, undefined, signal);
export const reviewDocumentUpload = (base: string, proof: SessionProof, body: DocumentUploadReviewRequest, signal?: AbortSignal): Promise<DocumentUploadReview> =>
  jsonRequest(base, '/documents/uploads/review', 'DocumentUploadReview', proof, 'POST', validateWire('DocumentUploadReviewRequest', body), undefined, signal);
export const getDocumentUploadReceipt = (base: string, proof: SessionProof, command: string, signal?: AbortSignal): Promise<DocumentUploadReceipt> =>
  jsonRequest(base, `/documents/uploads/commands/${encodeURIComponent(command)}`, 'DocumentUploadReceipt', proof, 'GET', undefined, undefined, signal);
export const reviewDocumentProcessing = (base: string, proof: SessionProof, conversation: string, body: DocumentProcessingReviewRequest, signal?: AbortSignal): Promise<DocumentProcessingReview> =>
  jsonRequest(base, `/conversations/${id(conversation)}/documents/processing/review`, 'DocumentProcessingReview', proof, 'POST', validateWire('DocumentProcessingReviewRequest', body), undefined, signal);
export const executeDocumentProcessing = (base: string, proof: SessionProof, conversation: string, body: Command, signal?: AbortSignal): Promise<DocumentProcessingReceipt> =>
  jsonRequest(base, `/conversations/${id(conversation)}/documents/processing/commands`, 'DocumentProcessingReceipt', proof, 'POST', validateWire('Command', body), body.command_id, signal);
export const getDocumentProcessingReceipt = (base: string, proof: SessionProof, conversation: string, command: string, signal?: AbortSignal): Promise<DocumentProcessingReceipt> =>
  jsonRequest(base, `/conversations/${id(conversation)}/documents/processing/commands/${id(command)}`, 'DocumentProcessingReceipt', proof, 'GET', undefined, undefined, signal);
export async function uploadDocuments(base: string, proof: SessionProof, command: Command, files: readonly File[], signal?: AbortSignal): Promise<DocumentUploadReceipt> {
  validateWire('Command', command);
  if (command.type !== 'document.upload') throw new Error('invalid_command');
  const metadata = command.payload.files as {name:string;size_bytes:number}[];
  if (files.length !== metadata.length || files.some((file, index) => file.name !== metadata[index].name || file.size !== metadata[index].size_bytes)) throw new Error('invalid_document_upload');
  const encoded = new TextEncoder().encode(JSON.stringify(command));
  if (encoded.byteLength > 16384) throw new Error('payload_too_large');
  const prefix = new ArrayBuffer(4);
  new DataView(prefix).setUint32(0, encoded.byteLength, false);
  await pace(proof, '/documents/uploads/commands', 'POST', signal, command);
  signal?.throwIfAborted();
  const response = await fetch(`${base}/api/v1/documents/uploads/commands`, {method:'POST', credentials:'same-origin', cache:'no-store', signal,
    headers:{...proofHeaders(proof), 'Content-Type':'application/vnd.row-bot.document-upload-v1', 'Idempotency-Key':command.command_id},
    body:new Blob([prefix, encoded, ...files])});
  const value: unknown = await response.json();
  if (!response.ok) throw validateWire<Problem>('Problem', value);
  return validateWire<DocumentUploadReceipt>('DocumentUploadReceipt', value);
}
export const reviewDocumentControl = (base: string, proof: SessionProof, body: DocumentControlReviewRequest, signal?: AbortSignal): Promise<DocumentControlReview> =>
  jsonRequest(base, '/documents/queue/review', 'DocumentControlReview', proof, 'POST', validateWire('DocumentControlReviewRequest', body), undefined, signal);
export const executeDocumentControl = (base: string, proof: SessionProof, body: Command, signal?: AbortSignal): Promise<DocumentControlReceipt> =>
  jsonRequest(base, '/documents/queue/commands', 'DocumentControlReceipt', proof, 'POST', validateWire('Command', body), body.command_id, signal);
export const getDocumentControlReceipt = (base: string, proof: SessionProof, command: string, signal?: AbortSignal): Promise<DocumentControlReceipt> =>
  jsonRequest(base, `/documents/queue/commands/${encodeURIComponent(command)}`, 'DocumentControlReceipt', proof, 'GET', undefined, undefined, signal);
export const reviewRuntimeInstallation = (base: string, proof: SessionProof, body: RuntimeInstallationReviewRequest, signal?: AbortSignal): Promise<RuntimeInstallationReview> =>
  jsonRequest(base, '/settings/mcp/installations/review', 'RuntimeInstallationReview', proof, 'POST', validateWire('RuntimeInstallationReviewRequest', body), undefined, signal);
export const executeRuntimeInstallation = (base: string, proof: SessionProof, body: Command, signal?: AbortSignal): Promise<RuntimeInstallationReceipt> =>
  jsonRequest(base, '/settings/mcp/installations/commands', 'RuntimeInstallationReceipt', proof, 'POST', validateWire('Command', body), body.command_id, signal);
export const getRuntimeInstallationReceipt = (base: string, proof: SessionProof, runtime: string, command: string, signal?: AbortSignal): Promise<RuntimeInstallationReceipt> =>
  jsonRequest(base, `/settings/mcp/installations/${encodeURIComponent(runtime)}/commands/${encodeURIComponent(command)}`, 'RuntimeInstallationReceipt', proof, 'GET', undefined, undefined, signal);
export const reviewMcpCatalog = (base: string, proof: SessionProof, body: McpCatalogRequest, signal?: AbortSignal): Promise<McpCatalogReview> =>
  jsonRequest(base, '/settings/mcp/catalog/review', 'McpCatalogReview', proof, 'POST', validateWire('McpCatalogRequest', body), undefined, signal);
export const getMcpPolicy = (base: string, proof: SessionProof, server: string | null, search: string, cursor?: string, signal?: AbortSignal): Promise<McpPolicyPage> =>
  jsonRequest(base, '/settings/mcp/policy' + query({server_id:server ?? undefined,query:search,cursor}), 'McpPolicyPage', proof, 'GET', undefined, undefined, signal);
export const reviewMcpPolicy = (base: string, proof: SessionProof, body: McpPolicyRequest, signal?: AbortSignal): Promise<McpPolicyReview> =>
  jsonRequest(base, '/settings/mcp/policy/review', 'McpPolicyReview', proof, 'POST', validateWire('McpPolicyRequest', body), undefined, signal);
export const getMcpRuntime = (base: string, proof: SessionProof, server: string, signal?: AbortSignal): Promise<McpRuntimeState> =>
  jsonRequest(base, `/settings/mcp/runtime/${id(server)}`, 'McpRuntimeState', proof, 'GET', undefined, undefined, signal);
export const reviewMcpRuntime = (base: string, proof: SessionProof, body: McpRuntimeReviewRequest, signal?: AbortSignal): Promise<McpRuntimeReview> =>
  jsonRequest(base, '/settings/mcp/runtime/review', 'McpRuntimeReview', proof, 'POST', validateWire('McpRuntimeReviewRequest', body), undefined, signal);
export const getMcpConfiguration = (base: string, proof: SessionProof, search: string, cursor?: string, signal?: AbortSignal): Promise<McpConfigurationPage> =>
  jsonRequest(base, '/settings/mcp/configuration' + query({query:search,cursor}), 'McpConfigurationPage', proof, 'GET', undefined, undefined, signal);
export const reviewMcpConfiguration = (base: string, proof: SessionProof, body: McpConfigurationReviewRequest, signal?: AbortSignal): Promise<McpConfigurationReview> =>
  jsonRequest(base, '/settings/mcp/configuration/review', 'McpConfigurationReview', proof, 'POST', validateWire('McpConfigurationReviewRequest',body), undefined, signal);
export const cancelSubscriptionStart = (base: string, proof: SessionProof, command: string, signal?: AbortSignal): Promise<SubscriptionFlowSnapshot> =>
  jsonRequest(base, `/settings/providers/subscriptions/starts/${id(command)}/cancel`, 'SubscriptionFlowSnapshot', proof, 'POST', {}, undefined, signal);
export const reviewDocumentRemoval = (base: string, proof: SessionProof, document_id: string | null, signal?: AbortSignal): Promise<DocumentRemovalReview> =>
  jsonRequest(base, '/knowledge/documents/removal-review', 'DocumentRemovalReview', proof, 'POST', validateWire('DocumentRemovalReviewRequest', {document_id}), undefined, signal);
export const reviewDocumentRemovalRetry = (base: string, proof: SessionProof, command: string, signal?: AbortSignal): Promise<DocumentRemovalReview> =>
  jsonRequest(base, `/knowledge/documents/removals/${id(command)}/retry-review`, 'DocumentRemovalReview', proof, 'POST', {}, undefined, signal);
export const getDocumentRemovalReceipt = (base: string, proof: SessionProof, command: string, signal?: AbortSignal): Promise<DocumentRemovalReceipt> =>
  jsonRequest(base, `/knowledge/documents/removals/${id(command)}`, 'DocumentRemovalReceipt', proof, 'GET', undefined, undefined, signal);
export const sendDocumentRemoval = (base: string, proof: SessionProof, command: Command, signal?: AbortSignal): Promise<DocumentRemovalReceipt> =>
  jsonRequest(base, '/knowledge/documents/commands', 'DocumentRemovalReceipt', proof, 'POST', command, command.command_id, signal);
export const getBuddy = (base: string, proof: SessionProof, conversation: string, signal?: AbortSignal): Promise<BuddySnapshot> =>
  jsonRequest(base, `/conversations/${id(conversation)}/buddy`, 'BuddySnapshot', proof, 'GET', undefined, undefined, signal);
export const getGlobalBuddy = (base: string, proof: SessionProof, signal?: AbortSignal): Promise<BuddySnapshot> =>
  jsonRequest(base, '/buddy', 'BuddySnapshot', proof, 'GET', undefined, undefined, signal);
export const getGlobalBuddyPacks = (base: string, proof: SessionProof, cursor?: string, signal?: AbortSignal): Promise<BuddyPackPage> =>
  jsonRequest(base, `/buddy/packs${query({cursor})}`, 'BuddyPackPage', proof, 'GET', undefined, undefined, signal);
export const getGlobalBuddyPack = (base: string, proof: SessionProof, pack: string, signal?: AbortSignal): Promise<BuddyPack> =>
  jsonRequest(base, `/buddy/packs/${id(pack)}`, 'BuddyPack', proof, 'GET', undefined, undefined, signal);
export const getBuddyPacks = (base: string, proof: SessionProof, conversation: string, cursor?: string, signal?: AbortSignal): Promise<BuddyPackPage> =>
  jsonRequest(base, `/conversations/${id(conversation)}/buddy/packs${query({cursor})}`, 'BuddyPackPage', proof, 'GET', undefined, undefined, signal);
export const getBuddyPack = (base: string, proof: SessionProof, conversation: string, pack: string, signal?: AbortSignal): Promise<BuddyPack> =>
  jsonRequest(base, `/conversations/${id(conversation)}/buddy/packs/${id(pack)}`, 'BuddyPack', proof, 'GET', undefined, undefined, signal);
export const reviewBuddy = (base: string, proof: SessionProof, conversation: string, body: BuddyHatchRequest, signal?: AbortSignal): Promise<BuddyHatchReview> =>
  jsonRequest(base, `/conversations/${id(conversation)}/buddy/review`, 'BuddyHatchReview', proof, 'POST', body, undefined, signal);
export const getBuddyReceipt = (base: string, proof: SessionProof, conversation: string, command: string, signal?: AbortSignal): Promise<BuddyReceipt> =>
  jsonRequest(base, `/conversations/${id(conversation)}/buddy/commands/${id(command)}`, 'BuddyReceipt', proof, 'GET', undefined, undefined, signal);
export const sendBuddy = (base: string, proof: SessionProof, conversation: string, command: Command, signal?: AbortSignal): Promise<BuddyReceipt> =>
  jsonRequest(base, `/conversations/${id(conversation)}/buddy/commands`, 'BuddyReceipt', proof, 'POST', command, command.command_id, signal);
export async function getBuddyMedia(base: string, proof: SessionProof, conversation: string, pack: string, asset: string, revision: string, signal?: AbortSignal): Promise<Blob> {
  const response = await fetch(`${base}/api/v1/conversations/${id(conversation)}/buddy/packs/${id(pack)}/media/${id(asset)}${query({revision})}`, {
    credentials: 'same-origin', cache: 'no-store', headers: proofHeaders(proof), signal,
  });
  if (!response.ok) throw validateWire<Problem>('Problem', await response.json());
  const reader = response.body?.getReader();
  if (!reader) throw new Error('protocol_incompatible');
  const chunks: Uint8Array<ArrayBuffer>[] = [];
  let size = 0;
  try {
    for (;;) {
      const {done, value} = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > 67108864) throw new Error('payload_too_large');
      chunks.push(new Uint8Array(value));
    }
  } finally { await reader.cancel(); reader.releaseLock(); }
  return new Blob(chunks, {type: response.headers.get('content-type') ?? 'application/octet-stream'});
}
export async function getGlobalBuddyMedia(base: string, proof: SessionProof, pack: string, asset: string, revision: string, signal?: AbortSignal): Promise<Blob> {
  const response = await fetch(`${base}/api/v1/buddy/packs/${id(pack)}/media/${id(asset)}${query({revision})}`, {
    credentials: 'same-origin', cache: 'no-store', headers: proofHeaders(proof), signal,
  });
  if (!response.ok) throw validateWire<Problem>('Problem', await response.json());
  const reader = response.body?.getReader();
  if (!reader) throw new Error('protocol_incompatible');
  const chunks: Uint8Array<ArrayBuffer>[] = [];
  let size = 0;
  try {
    while (true) {
      const {done, value} = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > 67108864) throw new Error('response_too_large');
      chunks.push(value);
    }
  } finally { reader.releaseLock(); }
  return new Blob(chunks, {type: response.headers.get('content-type') || 'application/octet-stream'});
}
export const getSubscriptionProbes = (base: string, proof: SessionProof, signal?: AbortSignal): Promise<SubscriptionProbeSnapshot> =>
  jsonRequest(base, '/settings/providers/subscriptions/probes', 'SubscriptionProbeSnapshot', proof, 'GET', undefined, undefined, signal);
export const reviewSubscriptionProbe = (base: string, proof: SessionProof, body: SubscriptionProbeRequest, signal?: AbortSignal): Promise<SubscriptionProbeReview> =>
  jsonRequest(base, '/settings/providers/subscriptions/probes/review', 'SubscriptionProbeReview', proof, 'POST', validateWire('SubscriptionProbeRequest',body), undefined, signal);
export const getSubscriptionProbeStatus = (base: string, proof: SessionProof, command: string, signal?: AbortSignal): Promise<SubscriptionProbeStatus> =>
  jsonRequest(base, `/settings/providers/subscriptions/probes/${id(command)}/status`, 'SubscriptionProbeStatus', proof, 'GET', undefined, undefined, signal);
export const cancelSubscriptionProbe = (base: string, proof: SessionProof, command: string, signal?: AbortSignal): Promise<SubscriptionProbeState> =>
  jsonRequest(base, `/settings/providers/subscriptions/probes/${id(command)}/cancel`, 'SubscriptionProbeState', proof, 'POST', {}, undefined, signal);
export const getSubscriptionProbeReceipt = (base: string, proof: SessionProof, command: string, signal?: AbortSignal): Promise<SubscriptionProbeReceipt> =>
  jsonRequest(base, `/settings/providers/subscriptions/probes/${id(command)}/receipt`, 'SubscriptionProbeReceipt', proof, 'GET', undefined, undefined, signal);
export const sendSubscriptionProbe = (base: string, proof: SessionProof, command: Command, signal?: AbortSignal): Promise<SubscriptionProbeCommandResult> =>
  jsonRequest(base, '/settings/providers/commands', 'SubscriptionProbeCommandResult', proof, 'POST', command, command.command_id, signal);
export const getSubscriptionOptions = (base: string, proof: SessionProof, signal?: AbortSignal): Promise<SubscriptionOptionsSnapshot> =>
  jsonRequest(base, '/settings/providers/subscriptions/options', 'SubscriptionOptionsSnapshot', proof, 'GET', undefined, undefined, signal);
export const reviewSubscriptionOptions = (base: string, proof: SessionProof, body: SubscriptionOptionsRequest, signal?: AbortSignal): Promise<SubscriptionOptionsReview> =>
  jsonRequest(base, '/settings/providers/subscriptions/options/review', 'SubscriptionOptionsReview', proof, 'POST', validateWire('SubscriptionOptionsRequest', body), undefined, signal);
export const getSubscriptionOptionsReceipt = (base: string, proof: SessionProof, command: string, signal?: AbortSignal): Promise<SubscriptionOptionsReceipt> =>
  jsonRequest(base, `/settings/providers/subscriptions/options/receipts/${id(command)}`, 'SubscriptionOptionsReceipt', proof, 'GET', undefined, undefined, signal);
export const sendSubscriptionOptions = (base: string, proof: SessionProof, command: Command, signal?: AbortSignal): Promise<SubscriptionOptionsResult> =>
  jsonRequest(base, '/settings/providers/commands', 'SubscriptionOptionsResult', proof, 'POST', command, command.command_id, signal);
export const getSubscriptionAccounts = (base: string, proof: SessionProof, signal?: AbortSignal): Promise<SubscriptionAccountsSnapshot> =>
  jsonRequest(base, '/settings/providers/subscriptions', 'SubscriptionAccountsSnapshot', proof, 'GET', undefined, undefined, signal);
export const reviewSubscriptionAction = (base: string, proof: SessionProof, body: SubscriptionActionRequest, signal?: AbortSignal): Promise<SubscriptionActionReview> =>
  jsonRequest(base, '/settings/providers/subscriptions/review', 'SubscriptionActionReview', proof, 'POST', validateWire('SubscriptionActionRequest',body), undefined, signal);
export const getSubscriptionFlow = (base: string, proof: SessionProof, flow: string, epoch: string, signal?: AbortSignal): Promise<SubscriptionFlowSnapshot> =>
  jsonRequest(base, `/settings/providers/subscriptions/flows/${id(flow)}` + query({server_epoch:epoch}), 'SubscriptionFlowSnapshot', proof, 'GET', undefined, undefined, signal);
export const getSubscriptionReceipt = (base: string, proof: SessionProof, provider: string, command: string, signal?: AbortSignal): Promise<SubscriptionActionReceipt> =>
  jsonRequest(base, `/settings/providers/subscriptions/${id(provider)}/receipts/${id(command)}`, 'SubscriptionActionReceipt', proof, 'GET', undefined, undefined, signal);
export const sendSubscriptionAction = (base: string, proof: SessionProof, command: Command, signal?: AbortSignal): Promise<SubscriptionActionResult> =>
  jsonRequest(base, '/settings/providers/commands', 'SubscriptionActionResult', proof, 'POST', command, command.command_id, signal);
export const revokeSubscriptionFlows = (base: string, proof: SessionProof, signal?: AbortSignal): Promise<SubscriptionQuiescence> =>
  jsonRequest(base, '/settings/providers/subscriptions/flows', 'SubscriptionQuiescence', proof, 'DELETE', undefined, undefined, signal, true);
export const getDefaultModel = (base: string, proof: SessionProof, signal?: AbortSignal): Promise<DefaultModelSnapshot> =>
  jsonRequest(base, '/settings/providers/default-model', 'DefaultModelSnapshot', proof, 'GET', undefined, undefined, signal);
export const reviewDefaultModel = (base: string, proof: SessionProof, body: DefaultModelReviewRequest, signal?: AbortSignal): Promise<DefaultModelReview> =>
  jsonRequest(base, '/settings/providers/default-model/review', 'DefaultModelReview', proof, 'POST', validateWire('DefaultModelReviewRequest',body), undefined, signal);
export const getDefaultModelReceipt = (base: string, proof: SessionProof, command: string, signal?: AbortSignal): Promise<DefaultModelReceipt> =>
  jsonRequest(base, `/settings/providers/default-model/receipts/${id(command)}`, 'DefaultModelReceipt', proof, 'GET', undefined, undefined, signal);
export const getProviderConfiguration = (base: string, proof: SessionProof, search: string, cursor?: string, signal?: AbortSignal): Promise<ProviderConfigurationPage> =>
  jsonRequest(base, '/settings/providers/configuration' + query({query:search,cursor}), 'ProviderConfigurationPage', proof, 'GET', undefined, undefined, signal);
export const reviewProviderConfiguration = (base: string, proof: SessionProof, body: ProviderConfigurationReviewRequest, signal?: AbortSignal): Promise<ProviderConfigurationReview> =>
  jsonRequest(base, '/settings/providers/configuration/review', 'ProviderConfigurationReview', proof, 'POST', body, undefined, signal);
export const getProviderConfigurationReceipt = (base: string, proof: SessionProof, command: string, signal?: AbortSignal): Promise<ProviderConfigurationReceipt> =>
  jsonRequest(base, `/settings/providers/configuration/receipts/${id(command)}`, 'ProviderConfigurationReceipt', proof, 'GET', undefined, undefined, signal);
export const reviewProviderSettings = (base: string, proof: SessionProof, provider: string, body: ProviderSettingsReviewRequest, signal?: AbortSignal): Promise<ProviderSettingsReview> =>
  jsonRequest(base, `/settings/providers/${id(provider)}/credential-review`, 'ProviderSettingsReview', proof, 'POST', body, undefined, signal);
export const getProviderSettingsReceipt = (base: string, proof: SessionProof, provider: string, command: string, signal?: AbortSignal): Promise<ProviderSettingsReceipt> =>
  jsonRequest(base, `/settings/providers/${id(provider)}/receipts/${id(command)}`, 'ProviderSettingsReceipt', proof, 'GET', undefined, undefined, signal);
export const getApproval = (base: string, proof: SessionProof, approval: string, signal?: AbortSignal): Promise<ApprovalView> =>
  jsonRequest(base, `/approvals/${id(approval)}?include_summary=true`, 'ApprovalView', proof, 'GET', undefined, undefined, signal);
export const getResource = (base: string, proof: SessionProof, reference: string, signal?: AbortSignal): Promise<ResourceView> =>
  jsonRequest(base, `/resources/${id(reference)}`, 'ResourceView', proof, 'GET', undefined, undefined, signal);
export const subscribe = (base: string, proof: SessionProof, conversation: string, signal?: AbortSignal): Promise<SubscriptionView> =>
  jsonRequest(base, `/conversations/${id(conversation)}/subscriptions`, 'SubscriptionView', proof, 'POST', undefined, undefined, signal);
export const poll = (base: string, proof: SessionProof, subscription_id: string, cursor: string, signal?: AbortSignal): Promise<EventPage> =>
  jsonRequest(base, '/events/poll' + query({subscription_id,cursor}), 'EventPage', proof, 'GET', undefined, undefined, signal);
export const acknowledge = (base: string, proof: SessionProof, subscription: string, cursor: string, signal?: AbortSignal): Promise<Acknowledged> =>
  jsonRequest(base, `/subscriptions/${id(subscription)}/ack`, 'Acknowledged', proof, 'PUT', {cursor}, undefined, signal);
export const unsubscribe = (base: string, proof: SessionProof, subscription: string, signal?: AbortSignal, keepalive = false): Promise<Unsubscribed> =>
  jsonRequest(base, `/subscriptions/${id(subscription)}`, 'Unsubscribed', proof, 'DELETE', undefined, undefined, signal, keepalive);
export const beginUpload = (base: string, proof: SessionProof, body: UploadRequest, signal?: AbortSignal): Promise<UploadView> => {
  validateWire<UploadRequest>('UploadRequest', body);
  return jsonRequest(base, '/uploads/sessions', 'UploadView', proof, 'POST', body, undefined, signal);
};
export const uploadStatus = (base: string, proof: SessionProof, upload: string, signal?: AbortSignal): Promise<UploadView> =>
  jsonRequest(base, `/uploads/${id(upload)}`, 'UploadView', proof, 'GET', undefined, undefined, signal);
export const cancelUpload = (base: string, proof: SessionProof, upload: string, signal?: AbortSignal): Promise<UploadCancelled> =>
  jsonRequest(base, `/uploads/${id(upload)}`, 'UploadCancelled', proof, 'DELETE', undefined, undefined, signal);
export const completeUpload = (base: string, proof: SessionProof, upload: string, body: UploadCompletion, key: string, signal?: AbortSignal): Promise<AttachmentView> => {
  validateWire<UploadCompletion>('UploadCompletion', body);
  return jsonRequest(base, `/uploads/${id(upload)}/complete`, 'AttachmentView', proof, 'POST', body, key, signal);
};
export async function uploadChunk(base: string, proof: SessionProof, upload: string, offset: number, data: Blob, signal?: AbortSignal): Promise<UploadView> {
  if (data.size > 1048576) throw new Error('payload_too_large');
  const response = await fetch(`${base}/api/v1/uploads/${id(upload)}/chunks` + query({offset}), {
    method: 'PUT', credentials: 'same-origin', cache: 'no-store', headers: proofHeaders(proof), body: data, signal,
  });
  const value = await response.json();
  if (!response.ok) throw validateWire<Problem>('Problem', value);
  return validateWire<UploadView>('UploadView', value);
}
export async function readAttachment(base: string, proof: SessionProof, reference: string, signal?: AbortSignal): Promise<Blob> {
  const response = await fetch(`${base}/api/v1/attachments/${id(reference)}`, {
    credentials: 'same-origin', cache: 'no-store', headers: proofHeaders(proof), signal,
  });
  if (!response.ok) throw validateWire<Problem>('Problem', await response.json());
  const data = await response.blob();
  if (data.size > 26214400) throw new Error('protocol_incompatible');
  return data;
}
export const getAttachmentMetadata = (base: string, proof: SessionProof, reference: string, signal?: AbortSignal): Promise<AttachmentView> =>
  jsonRequest(base, `/attachments/${id(reference)}/metadata`, 'AttachmentView', proof, 'GET', undefined, undefined, signal);
export const readNativeTerminal = (base: string, proof: SessionProof, terminal: string, cursor = 0, maxBytes = 65536, signal?: AbortSignal): Promise<NativeTerminalOutput> =>
  jsonRequest(base, `/native/terminals/${id(terminal)}` + query({cursor,max_bytes:maxBytes}), 'NativeTerminalOutput', proof, 'GET', undefined, undefined, signal);
export const writeNativeTerminal = (base: string, proof: SessionProof, terminal: string, body: NativeTerminalInput, signal?: AbortSignal): Promise<NativeTerminalChanged> =>
  jsonRequest(base, `/native/terminals/${id(terminal)}/input`, 'NativeTerminalChanged', proof, 'POST', body, undefined, signal);
export const resizeNativeTerminal = (base: string, proof: SessionProof, terminal: string, body: NativeTerminalResize, signal?: AbortSignal): Promise<NativeTerminalChanged> =>
  jsonRequest(base, `/native/terminals/${id(terminal)}/resize`, 'NativeTerminalChanged', proof, 'POST', body, undefined, signal);
export const disconnectNativeTerminal = (base: string, proof: SessionProof, terminal: string, signal?: AbortSignal): Promise<NativeTerminalClosed> =>
  jsonRequest(base, `/native/terminals/${id(terminal)}`, 'NativeTerminalClosed', proof, 'DELETE', undefined, undefined, signal);
export const getArtifactExport = (base: string, proof: SessionProof, conversation: string, binding: string, exportId: string, signal?: AbortSignal): Promise<ArtifactExport> =>
  jsonRequest(base, `/conversations/${id(conversation)}/artifacts/${id(binding)}/exports/${id(exportId)}`, 'ArtifactExport', proof, 'GET', undefined, undefined, signal);
export async function downloadArtifactExport(base: string, proof: SessionProof, conversation: string, binding: string, descriptor: ArtifactExport, signal?: AbortSignal): Promise<Blob> {
  validateWire<ArtifactExport>('ArtifactExport', descriptor);
  const response = await fetch(`${base}/api/v1/conversations/${id(conversation)}/artifacts/${id(binding)}/exports/${id(descriptor.export_id)}/download`, {
    credentials: 'same-origin', cache: 'no-store', headers: proofHeaders(proof), signal,
  });
  if (!response.ok) throw validateWire<Problem>('Problem', await response.json());
  const encoding = response.headers.get('Content-Encoding')?.trim().toLowerCase();
  const length = response.headers.get('Content-Length');
  // Fetch exposes decoded bytes while Content-Length describes the encoded
  // transfer. The immutable descriptor bounds and authenticates decoded data.
  if (!response.body || (encoding && !['identity', 'gzip', 'br', 'deflate'].includes(encoding)) ||
      ((!encoding || encoding === 'identity') && length !== null && Number(length) !== descriptor.size_bytes)) {
    try { await response.body?.cancel(); } catch { /* Closed response. */ }
    throw new Error('protocol_incompatible');
  }
  const reader = response.body.getReader();
  const parts: Uint8Array<ArrayBuffer>[] = [];
  let size = 0;
  try {
    while (true) {
      const part = await reader.read();
      if (part.done) break;
      size += part.value.byteLength;
      if (size > descriptor.size_bytes || size > 67108864) throw new Error('protocol_incompatible');
      parts.push(part.value);
    }
  } finally {
    try { await reader.cancel(); } catch { /* Closed response. */ }
    reader.releaseLock();
  }
  if (size !== descriptor.size_bytes) throw new Error('protocol_incompatible');
  const blob = new Blob(parts, {type: descriptor.media_type});
  const digest = await crypto.subtle.digest('SHA-256', await blob.arrayBuffer());
  if (Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('') !== descriptor.sha256)
    throw new Error('protocol_incompatible');
  return blob;
}
export async function* observeEvents(base: string, proof: SessionProof, subscription_id: string,
  cursor: string, signal?: AbortSignal): AsyncGenerator<EventRecord | StreamReset> {
  signal?.throwIfAborted();
  const response = await fetch(`${base}/api/v1/events` + query({subscription_id,cursor}), {
    credentials: 'same-origin', cache: 'no-store', headers: proofHeaders(proof), signal,
  });
  if (!response.ok) throw validateWire<Problem>('Problem', await response.json());
  if (!response.body) throw new Error('protocol_incompatible');
  const reader = response.body.getReader();
  const decoder = new TextDecoder('utf-8', {fatal: true});
  let buffer = '';
  try {
    while (true) {
      const part = await reader.read();
      buffer += decoder.decode(part.value, {stream: !part.done});
      let cut: number;
      while ((cut = buffer.indexOf('\n\n')) >= 0) {
        const frame = buffer.slice(0,cut); buffer = buffer.slice(cut+2);
        if (new TextEncoder().encode(frame).length > 69632) throw new Error('protocol_incompatible');
        const fields = Object.fromEntries(frame.split('\n').filter(l => l && !l.startsWith(':')).map(l => {
          const at = l.indexOf(':'); return [l.slice(0,at),l.slice(at+1).trimStart()];
        }));
        if (!fields.event) continue;
        if (fields.event === 'snapshot_required') {
          yield validateWire<StreamReset>('StreamReset', JSON.parse(fields.data)); return;
        }
        if (fields.event !== 'domain') throw new Error('protocol_incompatible');
        const event = JSON.parse(fields.data);
        if (!isEvent(event) || !fields.id) throw new Error('protocol_incompatible');
        yield {event, cursor: fields.id};
      }
      if (new TextEncoder().encode(buffer).length > 69632) throw new Error('protocol_incompatible');
      if (part.done) return;
    }
  } finally {
    // Cancellation is cleanup; a failed transport must not mask the read or
    // protocol error, prevent lock release, or reject a consumer's return.
    try { await reader.cancel(); } catch { /* The stream is already retired. */ }
    finally { reader.releaseLock(); }
  }
}
export async function sendConversationCommand(baseUrl: string, conversationId: string | null,
  command: Command, proof: SessionProof, idempotencyKey: string, signal?: AbortSignal): Promise<CommandReceipt> {
  if (!isCommand(command)) throw new Error('invalid_command');
  const suffix = command.type === 'approval.resolve' ? `/approvals/${id(conversationId || '')}/commands`
    : command.type.startsWith('provider.') ? '/settings/providers/commands'
    : command.type.startsWith('mcp.') ? '/settings/mcp/commands'
    : ['task.create', 'task.update', 'task.graph.update', 'task.settings.update', 'task.webhook.rotate', 'task.run', 'task.stop', 'task.approval'].includes(command.type) ? '/tasks/commands'
    : conversationId === null && (command.type === 'resource.setup' || command.type === 'resource.continue') ? '/resources/commands'
    : conversationId === null ? '/conversations/commands'
    : `/conversations/${encodeURIComponent(conversationId)}/commands`;
  return jsonRequest(baseUrl, suffix, 'CommandReceipt', proof, 'POST', command, idempotencyKey, signal);
}
export const dictationCapability = (base: string, proof: SessionProof, signal?: AbortSignal): Promise<DictationCapability> =>
  jsonRequest(base, '/voice/dictation/capability', 'DictationCapability', proof, 'GET', undefined, undefined, signal);
export const startDictation = (base: string, proof: SessionProof, conversation: string, request_id: string, signal?: AbortSignal): Promise<DictationSnapshot> =>
  jsonRequest(base, `/conversations/${id(conversation)}/voice/dictation`, 'DictationSnapshot', proof, 'POST', validateWire<DictationStart>('DictationStart', {request_id}), undefined, signal);
export async function getDictation(base: string, proof: SessionProof, handle: DictationHandle, signal?: AbortSignal): Promise<DictationSnapshot> {
  validateWire<DictationHandle>('DictationHandle', handle);
  const response = await fetch(`${base}/api/v1/conversations/${id(handle.conversation_id)}/voice/dictation/${id(handle.lease_id)}`, {
    credentials: 'same-origin', cache: 'no-store', signal,
    headers: {...proofHeaders(proof), 'X-Voice-Session-Id': String(handle.voice_session_id), 'X-Server-Epoch': handle.server_epoch},
  });
  const value = await response.json();
  if (!response.ok) throw validateWire<Problem>('Problem', value);
  return validateWire<DictationSnapshot>('DictationSnapshot', value);
}
export const stopDictation = (base: string, proof: SessionProof, handle: DictationHandle, signal?: AbortSignal): Promise<DictationSnapshot> => {
  validateWire<DictationHandle>('DictationHandle', handle);
  return jsonRequest(base, `/conversations/${id(handle.conversation_id)}/voice/dictation/${id(handle.lease_id)}/stop`, 'DictationSnapshot', proof, 'POST',
    {voice_session_id: handle.voice_session_id, server_epoch: handle.server_epoch}, undefined, signal);
};
export async function transcribeDictation(base: string, proof: SessionProof, handle: DictationHandle, utterance: string, audio: Blob, signal?: AbortSignal): Promise<DictationResult> {
  validateWire<DictationHandle>('DictationHandle', handle);
  validateWire<DictationStart>('DictationStart', {request_id: utterance});
  if (!audio.size || audio.size > 8388608) throw new Error(audio.size ? 'payload_too_large' : 'empty_audio');
  const response = await fetch(`${base}/api/v1/conversations/${id(handle.conversation_id)}/voice/dictation/${id(handle.lease_id)}/transcribe`, {
    method: 'POST', credentials: 'same-origin', cache: 'no-store', signal, body: audio,
    headers: {...proofHeaders(proof), 'Content-Type': audio.type,
      'X-Voice-Session-Id': String(handle.voice_session_id), 'X-Server-Epoch': handle.server_epoch,
      'X-Dictation-Utterance': utterance},
  });
  const value = await response.json();
  if (!response.ok) throw validateWire<Problem>('Problem', value);
  return validateWire<DictationResult>('DictationResult', value);
}
export const startTalk = (base: string, proof: SessionProof, conversation: string, body: TalkStart, signal?: AbortSignal): Promise<TalkSnapshot> =>
  jsonRequest(base, `/conversations/${id(conversation)}/voice/talk`, 'TalkSnapshot', proof, 'POST', validateWire('TalkStart', body), undefined, signal);
export const startRealtime = (base: string, proof: SessionProof, conversation: string, body: TalkStart, signal?: AbortSignal): Promise<RealtimeStart> =>
  jsonRequest(base, `/conversations/${id(conversation)}/voice/realtime`, 'RealtimeStart', proof, 'POST', validateWire('TalkStart', body), undefined, signal);
export function voiceControl<M extends 'talk' | 'realtime'>(base: string, proof: SessionProof, mode: M, handle: DictationHandle, action: 'stop' | 'heartbeat', signal?: AbortSignal): Promise<M extends 'talk' ? TalkSnapshot : RealtimeSnapshot> {
  validateWire('DictationHandle', handle);
  if (!['talk','realtime'].includes(mode) || !['stop','heartbeat'].includes(action)) throw new Error('invalid_command');
  return jsonRequest(base, `/conversations/${id(handle.conversation_id)}/voice/${mode}/${id(handle.lease_id)}/${action}`,
    mode === 'talk' ? 'TalkSnapshot' : 'RealtimeSnapshot', proof, 'POST',
    {voice_session_id: handle.voice_session_id, server_epoch: handle.server_epoch}, undefined, signal);
}
export const realtimeEvent = (base: string, proof: SessionProof, handle: DictationHandle, event: RealtimeEvent, signal?: AbortSignal): Promise<RealtimeEventResult> => {
  validateWire('DictationHandle', handle);
  return jsonRequest(base, `/conversations/${id(handle.conversation_id)}/voice/realtime/${id(handle.lease_id)}/event`,
    'RealtimeEventResult', proof, 'POST', validateWire('RealtimeEventRequest',
    {voice_session_id: handle.voice_session_id, server_epoch: handle.server_epoch, event}), undefined, signal);
};
export async function voiceRun(base: string, proof: SessionProof, mode: 'talk' | 'realtime', handle: DictationHandle, signal?: AbortSignal): Promise<VoiceRunView> {
  validateWire('DictationHandle', handle);
  if (!['talk','realtime'].includes(mode)) throw new Error('invalid_command');
  const response = await fetch(`${base}/api/v1/conversations/${id(handle.conversation_id)}/voice/${mode}/${id(handle.lease_id)}/run`, {
    credentials:'same-origin', cache:'no-store', signal,
    headers:{...proofHeaders(proof), 'X-Voice-Session-Id':String(handle.voice_session_id), 'X-Server-Epoch':handle.server_epoch}});
  const value = await response.json();
  if (!response.ok) throw validateWire<Problem>('Problem', value);
  return validateWire('VoiceRunView', value);
}
export async function realtimeExchange(base: string, proof: SessionProof, handle: DictationHandle, sdp: string, signal?: AbortSignal): Promise<string> {
  validateWire('DictationHandle', handle);
  const bytes = new TextEncoder().encode(sdp);
  if (!bytes.byteLength || bytes.byteLength > 1048576) throw new Error('invalid_realtime_exchange');
  const response = await fetch(`${base}/api/v1/conversations/${id(handle.conversation_id)}/voice/realtime/${id(handle.lease_id)}/exchange`, {
    method:'POST', credentials:'same-origin', redirect:'error', cache:'no-store', signal, body:bytes,
    headers:{...proofHeaders(proof), 'Content-Type':'application/sdp', 'X-Voice-Session-Id':String(handle.voice_session_id), 'X-Server-Epoch':handle.server_epoch}});
  if (!response.ok) throw validateWire<Problem>('Problem', await response.json());
  if (response.headers.get('Content-Type')?.split(';')[0] !== 'application/sdp') throw new Error('protocol_incompatible');
  const reader = response.body?.getReader();
  if (!reader) throw new Error('realtime_connection_failed');
  let size = 0, text = '';
  const decoder = new TextDecoder('utf-8', {fatal:true});
  try {
    while (true) {
      const next = await reader.read();
      if (next.done) break;
      size += next.value.byteLength;
      if (size > 1048576) throw new Error('payload_too_large');
      text += decoder.decode(next.value, {stream:true});
    }
    if (!size) throw new Error('realtime_connection_failed');
    return text + decoder.decode();
  } finally { await reader.cancel(); reader.releaseLock(); }
}
export async function transcribeTalk(base: string, proof: SessionProof, handle: DictationHandle, utterance: string, audio: Blob, signal?: AbortSignal): Promise<TalkResult> {
  validateWire('DictationHandle', handle);
  validateWire('DictationStart', {request_id: utterance});
  if (!audio.size || audio.size > 8388608) throw new Error(audio.size ? 'payload_too_large' : 'empty_audio');
  const response = await fetch(`${base}/api/v1/conversations/${id(handle.conversation_id)}/voice/talk/${id(handle.lease_id)}/transcribe`, {
    method:'POST', credentials:'same-origin', cache:'no-store', signal, body:audio,
    headers:{...proofHeaders(proof), 'Content-Type':audio.type, 'X-Voice-Session-Id':String(handle.voice_session_id),
      'X-Server-Epoch':handle.server_epoch, 'X-Dictation-Utterance':utterance}});
  const value = await response.json();
  if (!response.ok) throw validateWire<Problem>('Problem', value);
  return validateWire('TalkResult', value);
}
export async function talkOutput(base: string, proof: SessionProof, handle: DictationHandle, run_id: string, output_id: string, signal?: AbortSignal): Promise<Blob> {
  validateWire('DictationHandle', handle);
  const body = validateWire('TalkOutputRequest', {voice_session_id:handle.voice_session_id, server_epoch:handle.server_epoch, run_id, output_id});
  const response = await fetch(`${base}/api/v1/conversations/${id(handle.conversation_id)}/voice/talk/${id(handle.lease_id)}/output`, {
    method:'POST', credentials:'same-origin', cache:'no-store', signal, body:JSON.stringify(body), headers:{...proofHeaders(proof),'Content-Type':'application/json'}});
  if (!response.ok) throw validateWire<Problem>('Problem', await response.json());
  if (response.headers.get('X-Voice-Run') !== run_id || response.headers.get('X-Voice-Output') !== output_id || response.headers.get('Content-Type')?.split(';')[0] !== 'audio/wav') throw new Error('protocol_incompatible');
  const reader = response.body?.getReader();
  if (!reader) throw new Error('voice_output_unavailable');
  const chunks: Uint8Array<ArrayBuffer>[] = [];
  let size = 0;
  try {
    while (true) {
      const next = await reader.read();
      if (next.done) break;
      size += next.value.byteLength;
      if (size > 8388608) throw new Error('payload_too_large');
      chunks.push(new Uint8Array(next.value));
    }
    if (!size) throw new Error('voice_output_unavailable');
    return new Blob(chunks, {type:'audio/wav'});
  } finally { await reader.cancel(); reader.releaseLock(); }
}
export async function sendApprovalCommand(base: string, approval: string, command: Command,
  proof: SessionProof, key: string, signal?: AbortSignal): Promise<CommandReceipt> {
  if (!isCommand(command) || command.type !== 'approval.resolve') throw new Error('invalid_command');
  return jsonRequest(base, `/approvals/${id(approval)}/commands`, 'CommandReceipt', proof, 'POST', command, key, signal);
}
'''.strip())
    return "\n".join(lines) + "\n"


def outputs() -> dict[Path, str]:
    """Return all generated artifacts for check mode and deterministic tests."""
    bundle = schema_bundle()
    destination = ROOT / "contracts/client-platform/v1"
    result = {destination / "schema" / f"{name}.schema.json": json.dumps(value, indent=2, sort_keys=True) + "\n"
              for name, value in bundle.items()}
    result[destination / "typescript/client.ts"] = typescript(bundle)
    paths = {}
    for method, suffix, request, response in OPERATIONS:
        parameters = [{"name": name, "in": "path", "required": True,
                       "schema": {"type": "string", "minLength": 1, "maxLength": 256}}
                      for name in re.findall(r"\{([^}]+)\}", suffix)]
        if suffix not in {
            "/handshake",
            "/native/bootstrap",
            "/native/attest",
            "/native/authorize",
            "/native/revoke",
            "/native/selections/complete",
            "/native/terminal/open",
            "/native/attachments/{reference}",
        }:
            parameters += [{"name": name, "in": "header", "required": True,
                            "schema": {"type": "string", "maxLength": 256}}
                           for name in ("X-Client-Session", "X-CSRF-Token")]
        query_parameters = []
        if suffix == "/conversations" or suffix.endswith("/transcript"):
            query_parameters = [("limit", False, {"type": "integer", "minimum": 1,
                                 "maximum": 100 if suffix.endswith("/transcript") else 200}),
                                ("cursor", False, {"type": "string", "maxLength": 2048})]
        elif suffix == "/settings/providers/subscriptions/flows/{flow_id}":
            query_parameters = [("server_epoch", True, {"type": "string", "format": "uuid"})]
        elif suffix == "/settings/mcp/configuration":
            query_parameters = [("query", False, {"type": "string", "maxLength": 128}),
                                ("limit", False, {"type": "integer", "minimum": 1, "maximum": 50}),
                                ("cursor", False, {"type": "string", "maxLength": 2048})]
        elif suffix in {"/tasks", "/settings/models", "/settings/tools"}:
            query_parameters = [("query", False, {"type": "string", "maxLength": 256}),
                                ("limit", False, {"type": "integer", "minimum": 1, "maximum": 100}),
                                ("cursor", False, {"type": "string", "maxLength": 1024 if suffix == "/tasks" else 2048})]
            query_parameters.append(("enabled", False, {"type": "boolean"}) if suffix == "/tasks"
                                    else ("source", False, {"type": "string", "enum": ["core", "mcp", "plugin", "custom"]})
                                    if suffix == "/settings/tools"
                                    else ("provider_id", False, {"type": "string", "maxLength": 160}))
        elif suffix in {"/knowledge/entities", "/knowledge/documents"}:
            query_parameters = [("query", False, {"type": "string", "maxLength": 256}),
                                ("limit", False, {"type": "integer", "minimum": 1, "maximum": 100}),
                                ("cursor", False, {"type": "string", "maxLength": 1024}),
                                ("entity_type" if suffix.endswith("/entities") else "status", False,
                                 {"type": "string", "minLength": 1, "maxLength": 64})]
            if suffix.endswith("/entities"):
                query_parameters += [
                    ("status", False, {"type": "string", "enum": ["active", "needs_review", "superseded", "archived"]}),
                    ("source", False, {"type": "string", "enum": ["manual", "extraction", "document", "wiki", "other"]}),
                    ("tier", False, {"type": "string", "enum": ["core", "semantic", "episodic", "resource"]}),
                ]
        elif suffix == "/knowledge/graph":
            query_parameters = [("limit", False, {"type": "integer", "minimum": 1, "maximum": 250})]
        elif suffix == "/monitor/logs":
            query_parameters = [("limit", False, {"type": "integer", "minimum": 1, "maximum": 200})]
        elif "/content/" in suffix:
            query_parameters = [("limit_bytes", False, {"type": "integer", "minimum": 1, "maximum": 65536}),
                                ("cursor", False, {"type": "string", "maxLength": 2048})]
        elif suffix in {"/events", "/events/poll"}:
            query_parameters = [(name, True, {"type": "string", "maxLength": 2048})
                                for name in ("subscription_id", "cursor")]
        elif suffix.endswith("/chunks"):
            query_parameters = [("offset", True, {"type": "integer", "minimum": 0, "maximum": 26214400})]
        elif suffix == "/uploads":
            query_parameters = [(name, True, {"type": "string", "maxLength": 240})
                                for name in ("conversation_id", "name")]
            parameters.append({"name": "X-Command-Id", "in": "header", "required": True,
                               "schema": {"type": "string", "format": "uuid"}})
        parameters += [{"name": name, "in": "query", "required": required, "schema": schema}
                       for name, required, schema in query_parameters]
        if "/voice/" in suffix and "{lease_id}" in suffix and (method == "get" or suffix.endswith(("/transcribe", "/exchange"))):
            parameters += [{"name": "X-Voice-Session-Id", "in": "header", "required": True,
                            "schema": {"type": "integer", "minimum": 1, "maximum": 9007199254740991}},
                           {"name": "X-Server-Epoch", "in": "header", "required": True,
                            "schema": {"type": "string", "minLength": 1, "maxLength": 128}}]
            if suffix.endswith("/transcribe"):
                parameters.append({"name": "X-Dictation-Utterance", "in": "header", "required": True,
                                   "schema": {"type": "string", "format": "uuid"}})
        if request in {"Command", "DreamRunCommand", "UploadCompletion"} or suffix == "/uploads":
            parameters.append({"name": "Idempotency-Key", "in": "header", "required": True,
                               "schema": {"type": "string", "format": "uuid"}})
        response_schema = ({"type": "string", "format": "binary", "maxLength":
                            67108864 if suffix.endswith("/exports/{export_id}/download") else
                            65536 if suffix.endswith("/webhook-configuration") else
                            8388608 if suffix.endswith("/voice/talk/{lease_id}/output") else
                            1048576 if suffix.endswith("/voice/realtime/{lease_id}/exchange") else 26214400} if response == "bytes"
                           else {"$ref": f"./{response}.schema.json"})
        mime = "text/event-stream" if suffix == "/events" else "application/sdp" if suffix.endswith("/voice/realtime/{lease_id}/exchange") else "audio/wav" if suffix.endswith("/voice/talk/{lease_id}/output") else "application/octet-stream" if response == "bytes" else "application/json"
        operation = {"parameters": parameters, "responses": {
            "200": {"description": "Authenticated result", "content": {mime: {"schema": response_schema}}},
            "default": {"description": "Safe problem", "content": {"application/problem+json": {
                "schema": {"$ref": "./Problem.schema.json"}}}}}}
        if response == "CommandReceipt":
            operation["responses"]["202"] = {"description": "Durable acceptance", "content": {
                "application/json": {"schema": response_schema}}}
        if request:
            request_schema = ({"type": "string", "format": "binary",
                               "maxLength": 1048576}
                              if request == "bytes" else {"$ref": f"./{request}.schema.json"})
            operation["requestBody"] = {"required": True, "content": {
                "application/sdp" if suffix.endswith("/voice/realtime/{lease_id}/exchange") else "application/octet-stream" if request == "bytes" else "application/json": {"schema": request_schema}}}
        if suffix == "/events":
            operation["description"] = "SSE domain data validates Event; snapshot_required data validates StreamReset. IDs are signed cursors."
        if suffix == "/documents/uploads/commands":
            operation["requestBody"] = {"required": True, "content": {
                "application/vnd.row-bot.document-upload-v1": {"schema": {"type": "string", "format": "binary",
                    "maxLength": 4 + 16384 + 50 * 256 * 1024**2}}}}
            operation["description"] = "Four-byte big-endian JSON byte count (1..16384), closed Command JSON, then ordered file bytes of exactly the reviewed sizes. Up to50 files,256MiB each;1MiB receive/read chunks,300s whole receive and30s per receive. Authentication and review precede staging; files remain paused. Original command replay reads no file bodies."
        if "/voice/" in suffix and suffix.endswith("/transcribe"):
            operation["requestBody"] = {"required": True, "content": {
                mime: {"schema": {"type": "string", "format": "binary", "minLength": 1, "maxLength": 8388608}}
                for mime in ("audio/webm", "audio/ogg", "audio/wav", "audio/x-wav", "audio/mp4", "audio/mpeg")}}
            operation["description"] = "One admitted utterance per lease; receive deadline at most30 seconds and remaining lease lifetime. No audio is persisted. Stop retains admission until actual worker quiescence."
        paths.setdefault("/api/v1" + suffix, {})[method] = operation
    openapi = {"openapi": "3.1.0", "info": {"title": "Row-Bot client protocol", "version": "1.0"},
               "paths": paths}
    result[destination / "schema/openapi.json"] = json.dumps(openapi, indent=2, sort_keys=True) + "\n"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    changed = []
    for path, content in outputs().items():
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != content:
                changed.append(path.relative_to(ROOT).as_posix())
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Preserve clean checkout line endings and timestamps on unchanged files.
            if not path.exists() or path.read_text(encoding="utf-8") != content:
                path.write_text(content, encoding="utf-8", newline="\n")
    if changed:
        print("Generated contract mismatch: " + ", ".join(changed))
        return 1
    print("Client platform contracts " + ("verified" if args.check else "generated"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
