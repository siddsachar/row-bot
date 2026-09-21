import type * as Wire from '../../../contracts/client-platform/v1/typescript/client';
import { validateWire } from '../../../contracts/client-platform/v1/typescript/client';

export function isCommandReceipt(value: unknown): value is Wire.CommandReceipt {
  try {
    validateWire('CommandReceipt', value);
    return true;
  } catch {
    return false;
  }
}

export type * from '../../../contracts/client-platform/v1/typescript/client';

export type ClientError = {
  code: string;
  message: string;
  recovery: 'authenticate' | 'update' | 'retry' | 'review' | 'none';
};
export type DictationScope = Readonly<{
  conversationId: string;
  clientSessionId: string;
  serverEpoch: string;
  selectionKey: string;
}>;
export type ClientStatus =
  | 'loading'
  | 'ready'
  | 'disconnected'
  | 'reconnecting'
  | 'incompatible'
  | 'unauthorized'
  | 'fatal';
/** Client presentation metadata only; this is not an accepted v1 wire event. */
export type PanelDescriptor = {
  panel_kind: string;
  title: string;
  resource_ref?: string;
  resource_kind?: Wire.ResourceBinding['kind'];
  resource_revision?: string;
  subresource_key?: string;
  required_capabilities?: string[];
};
export type ClientPanelSuggestion = {
  type: 'panel.suggested';
  conversation_id: string;
  conversation_revision: string;
  descriptor: PanelDescriptor;
};
export type ClientState = {
  status: ClientStatus;
  error: ClientError | null;
  connection: 'none' | 'sse' | 'poll';
  handshake: Omit<Wire.HandshakeView, 'csrf_token'> | null;
  conversations: Wire.ConversationView[];
  conversationGroup: 'all' | 'pinned' | 'artifact' | 'workspace';
  hasMoreConversations: boolean;
  loadingConversations: boolean;
  conversationListError: ClientError | null;
  selectedConversationId: string | null;
  conversation: Wire.ConversationView | null;
  projection: Wire.Snapshot | null;
  workspace: Wire.ConversationWorkspace | null;
  activity: Wire.EventRecord[];
  history: Wire.TranscriptPage | null;
  historyFocus: string | null;
  search: Wire.SearchPage | null;
  searching: boolean;
  draftStatus: 'saved' | 'saving' | 'conflict' | 'failed';
  hasMoreTranscript: boolean;
  loadingConversation: boolean;
  suggestions: ClientPanelSuggestion[];
  revision: number;
};

export function isPanelDescriptor(value: unknown): value is PanelDescriptor {
  if (!value || typeof value !== 'object') return false;
  const row = value as Partial<PanelDescriptor>;
  return (
    typeof row.panel_kind === 'string' &&
    /^[a-z][a-z0-9.:-]{0,80}$/.test(row.panel_kind) &&
    typeof row.title === 'string' &&
    row.title.length <= 160 &&
    (row.resource_ref === undefined ||
      (typeof row.resource_ref === 'string' &&
        /^[a-zA-Z0-9:_-]{1,256}$/.test(row.resource_ref))) &&
    (row.resource_kind === undefined ||
      ['workspace', 'artifact', 'browser_session', 'task', 'document'].includes(
        row.resource_kind,
      )) &&
    (row.resource_revision === undefined ||
      (typeof row.resource_revision === 'string' &&
        row.resource_revision.length <= 128)) &&
    (row.subresource_key === undefined ||
      (typeof row.subresource_key === 'string' &&
        row.subresource_key.length <= 160)) &&
    (row.required_capabilities === undefined ||
      (Array.isArray(row.required_capabilities) &&
        row.required_capabilities.length <= 20 &&
        row.required_capabilities.every(
          (item) => typeof item === 'string' && item.length <= 128,
        )))
  );
}

/** The sole network boundary. Fixtures implement this same interface. */
export interface ClientTransport {
  conversationActions?(
    conversation: string,
    signal?: AbortSignal,
  ): Promise<Wire.ConversationActionSnapshot>;
  reviewConversationAction?(
    conversation: string,
    body: Wire.ConversationActionReviewRequest,
    signal?: AbortSignal,
  ): Promise<Wire.ConversationActionReview>;
  conversationActionReceipt?(
    conversation: string,
    command: string,
    signal?: AbortSignal,
  ): Promise<Wire.ConversationActionReceipt>;
  executeConversationAction?(
    conversation: string,
    command: Wire.ConversationActionCommand,
    signal?: AbortSignal,
  ): Promise<Wire.ConversationActionReceipt>;
  browserControls?(
    conversation: string,
    signal?: AbortSignal,
  ): Promise<Wire.BrowserControlSnapshot>;
  reviewBrowserControl?(
    conversation: string,
    body: Wire.BrowserReviewRequest,
    signal?: AbortSignal,
  ): Promise<Wire.BrowserReview>;
  browserControlReceipt?(
    conversation: string,
    command: string,
    signal?: AbortSignal,
  ): Promise<Wire.BrowserReceipt>;
  executeBrowserControl?(
    conversation: string,
    command: Wire.Command,
    signal?: AbortSignal,
  ): Promise<Wire.BrowserReceipt>;
  artifactExport?(
    conversation: string,
    binding: string,
    exportId: string,
    signal?: AbortSignal,
  ): Promise<Wire.ArtifactExport>;
  artifactDownload?(
    conversation: string,
    binding: string,
    descriptor: Wire.ArtifactExport,
    signal?: AbortSignal,
  ): Promise<Blob>;
  taskGraph?(
    task: string,
    signal?: AbortSignal,
  ): Promise<Wire.TaskGraphSnapshot>;
  taskSettings?(
    task: string,
    signal?: AbortSignal,
  ): Promise<Wire.TaskSettingsSnapshot>;
  reviewTaskSettings?(
    task: string,
    fields: Wire.TaskSettingsFields,
    signal?: AbortSignal,
  ): Promise<Wire.TaskSettingsSnapshot>;
  downloadTaskWebhook?(
    task: string,
    revision: string,
    signal?: AbortSignal,
  ): Promise<Blob>;
  taskEditor?(
    task: string,
    signal?: AbortSignal,
  ): Promise<Wire.TaskEditorSnapshot>;
  workspaceImports?(
    conversation: string,
    binding: string,
    cursor?: string,
    signal?: AbortSignal,
  ): Promise<Wire.WorkspaceImportPage>;
  developerRepository?(
    conversation: string,
    binding: string,
    signal?: AbortSignal,
  ): Promise<Wire.DeveloperRepositorySnapshot>;
  reviewDeveloperRepository?(
    conversation: string,
    binding: string,
    body: Wire.DeveloperRepositoryReviewRequest,
    signal?: AbortSignal,
  ): Promise<Wire.DeveloperRepositoryReview>;
  developerRepositoryReceipt?(
    conversation: string,
    binding: string,
    command: string,
    signal?: AbortSignal,
  ): Promise<Wire.DeveloperRepositoryReceipt>;
  executeDeveloperRepository?(
    conversation: string,
    binding: string,
    command: Wire.Command,
    signal?: AbortSignal,
  ): Promise<Wire.DeveloperRepositoryReceipt>;
  workspaceImportPatch?(
    conversation: string,
    binding: string,
    pending: string,
    revision: string,
    offset: number,
    signal?: AbortSignal,
  ): Promise<Wire.WorkspaceImportPatch>;
  reviewWorkspaceImport?(
    conversation: string,
    binding: string,
    pending: string,
    signal?: AbortSignal,
  ): Promise<Wire.WorkspaceImportReview>;
  workspaceImportReceipt?(
    conversation: string,
    binding: string,
    command: string,
    signal?: AbortSignal,
  ): Promise<Wire.WorkspaceImportResult>;
  reviewWorkspaceImportRecovery?(
    conversation: string,
    binding: string,
    command: string,
    signal?: AbortSignal,
  ): Promise<Wire.WorkspaceImportReview>;
  executeWorkspaceImport?(
    conversation: string,
    binding: string,
    command: Wire.Command,
    signal?: AbortSignal,
  ): Promise<Wire.WorkspaceImportResult>;
  reviewWorkspaceUndo?(
    conversation: string,
    binding: string,
    changeSet: string,
    signal?: AbortSignal,
  ): Promise<Wire.WorkspaceUndoReview>;
  workspaceUndoReceipt?(
    conversation: string,
    binding: string,
    command: string,
    signal?: AbortSignal,
  ): Promise<Wire.WorkspaceUndoResult>;
  reviewWorkspaceUndoRecovery?(
    conversation: string,
    binding: string,
    command: string,
    signal?: AbortSignal,
  ): Promise<Wire.WorkspaceUndoReview>;
  executeWorkspaceUndo?(
    conversation: string,
    binding: string,
    command: Wire.Command,
    signal?: AbortSignal,
  ): Promise<Wire.WorkspaceUndoResult>;
  workspaceEditableFile?(
    conversation: string,
    binding: string,
    path: string,
    signal?: AbortSignal,
  ): Promise<Wire.WorkspaceEditableFile>;
  workspaceProcesses?(
    conversation: string,
    binding: string,
    signal?: AbortSignal,
  ): Promise<Wire.WorkspaceProcessSnapshot>;
  workspaceProcessRecovery?(
    conversation: string,
    binding: string,
    cursor?: string,
    signal?: AbortSignal,
  ): Promise<Wire.WorkspaceProcessRecoveryPage>;
  workspaceProcessOutput?(
    conversation: string,
    binding: string,
    process: string,
    cursor: number,
    signal?: AbortSignal,
  ): Promise<Wire.WorkspaceProcessOutput>;
  reviewWorkspaceProcess?(
    conversation: string,
    binding: string,
    body: Wire.WorkspaceProcessReviewRequest,
    signal?: AbortSignal,
  ): Promise<Wire.WorkspaceProcessReview>;
  taskRunReview?(
    task: string,
    signal?: AbortSignal,
  ): Promise<Wire.TaskRunReview>;
  taskRuns?(
    task: string,
    cursor?: string,
    signal?: AbortSignal,
  ): Promise<Wire.TaskRunPage>;
  taskRun?(
    task: string,
    run: string,
    signal?: AbortSignal,
  ): Promise<Wire.TaskRunSummary>;
  taskApprovals?(
    task: string,
    run: string,
    cursor?: string,
    signal?: AbortSignal,
  ): Promise<Wire.TaskApprovalPage>;
  dictationCapability?(signal?: AbortSignal): Promise<Wire.DictationCapability>;
  startTalk?(
    conversation: string,
    body: Wire.TalkStart,
    signal?: AbortSignal,
  ): Promise<Wire.TalkSnapshot>;
  startRealtime?(
    conversation: string,
    body: Wire.TalkStart,
    signal?: AbortSignal,
  ): Promise<Wire.RealtimeStart>;
  voiceControl?<M extends 'talk' | 'realtime'>(
    mode: M,
    handle: Wire.DictationHandle,
    action: 'stop' | 'heartbeat',
    signal?: AbortSignal,
  ): Promise<M extends 'talk' ? Wire.TalkSnapshot : Wire.RealtimeSnapshot>;
  transcribeTalk?(
    handle: Wire.DictationHandle,
    utterance: string,
    audio: Blob,
    signal?: AbortSignal,
  ): Promise<Wire.TalkResult>;
  talkOutput?(
    handle: Wire.DictationHandle,
    run: string,
    output: string,
    signal?: AbortSignal,
  ): Promise<Blob>;
  realtimeEvent?(
    handle: Wire.DictationHandle,
    event: Wire.RealtimeEvent,
    signal?: AbortSignal,
  ): Promise<Wire.RealtimeEventResult>;
  realtimeExchange?(
    handle: Wire.DictationHandle,
    sdp: string,
    signal?: AbortSignal,
  ): Promise<string>;
  voiceRun?(
    mode: 'talk' | 'realtime',
    handle: Wire.DictationHandle,
    signal?: AbortSignal,
  ): Promise<Wire.VoiceRunView>;
  startDictation?(
    conversation: string,
    requestId: string,
    signal?: AbortSignal,
  ): Promise<Wire.DictationSnapshot>;
  transcribeDictation?(
    handle: Wire.DictationHandle,
    utterance: string,
    audio: Blob,
    signal?: AbortSignal,
  ): Promise<Wire.DictationResult>;
  stopDictation?(
    handle: Wire.DictationHandle,
    signal?: AbortSignal,
  ): Promise<Wire.DictationSnapshot>;
  delegatedActivity?(
    conversation: string,
    cursor?: string,
    signal?: AbortSignal,
  ): Promise<Wire.DelegatedActivityView>;
  delegatedRun?(
    conversation: string,
    run: string,
    signal?: AbortSignal,
  ): Promise<Wire.DelegatedRun>;
  queue?(
    conversation: string,
    generation?: string,
    cursor?: string,
    signal?: AbortSignal,
  ): Promise<Wire.ClientQueueView>;
  messageText?(
    conversation: string,
    message: string,
    cursor?: string,
    signal?: AbortSignal,
  ): Promise<Wire.LazyContent>;
  steering?(
    conversation: string,
    generation?: string,
    cursor?: string,
    signal?: AbortSignal,
  ): Promise<Wire.ParentSteeringView>;
  draft?(conversation: string, signal?: AbortSignal): Promise<Wire.DraftView>;
  saveDraft?(
    conversation: string,
    body: Wire.DraftSave,
    signal?: AbortSignal,
  ): Promise<Wire.DraftView>;
  search?(
    query: string,
    conversation?: string,
    cursor?: string,
    signal?: AbortSignal,
  ): Promise<Wire.SearchPage>;
  openConversation?(
    conversation: string,
    signal?: AbortSignal,
  ): Promise<Wire.ConversationOpenView>;
  history?(
    conversation: string,
    messageId?: string,
    cursor?: string,
    signal?: AbortSignal,
  ): Promise<Wire.TranscriptPage>;
  workspace?(
    conversation: string,
    signal?: AbortSignal,
  ): Promise<Wire.ConversationWorkspace>;
  composer?(
    conversation: string,
    body: Wire.ConversationComposerQuery,
    signal?: AbortSignal,
  ): Promise<Wire.ConversationComposer>;
  composerCommand?(
    conversation: string,
    body: Wire.SlashCommandRead,
    signal?: AbortSignal,
  ): Promise<Wire.SlashCommandResult>;
  library?(
    kind: 'artifact' | 'workspace',
    cursor?: string,
    signal?: AbortSignal,
  ): Promise<Wire.ResourceChoicePage>;
  deckSetup?(signal?: AbortSignal): Promise<Wire.DeckSetupOptions>;
  providerStatus?(signal?: AbortSignal): Promise<Wire.ProviderStatusSnapshot>;
  liveProviderStatus?(signal?: AbortSignal): Promise<Wire.ProviderLiveSnapshot>;
  refreshLiveProvider?(
    provider: string,
    signal?: AbortSignal,
  ): Promise<Wire.ProviderCatalogRefresh>;
  liveProviderRefresh?(
    signal?: AbortSignal,
  ): Promise<Wire.ProviderCatalogRefresh>;
  testLiveProviderRuntime?(
    provider: string,
    signal?: AbortSignal,
  ): Promise<Wire.ProviderRuntimeProbe>;
  reviewDocumentProcessing?(
    conversation: string,
    body: Wire.DocumentProcessingReviewRequest,
    signal?: AbortSignal,
  ): Promise<Wire.DocumentProcessingReview>;
  executeDocumentProcessing?(
    conversation: string,
    command: Wire.Command,
    signal?: AbortSignal,
  ): Promise<Wire.DocumentProcessingReceipt>;
  documentProcessingReceipt?(
    conversation: string,
    command: string,
    signal?: AbortSignal,
  ): Promise<Wire.DocumentProcessingReceipt>;
  reviewDocumentUpload?(
    files: Wire.DocumentUploadReviewRequest['files'],
    signal?: AbortSignal,
  ): Promise<Wire.DocumentUploadReview>;
  uploadDocuments?(
    command: Wire.Command,
    files: readonly File[],
    signal?: AbortSignal,
  ): Promise<Wire.DocumentUploadReceipt>;
  documentUploadReceipt?(
    command: string,
    signal?: AbortSignal,
  ): Promise<Wire.DocumentUploadReceipt>;
  documentQueue?(
    kind: string,
    batch?: string,
    cursor?: string,
    signal?: AbortSignal,
  ): Promise<Wire.DocumentQueuePage>;
  reviewDocumentControl?(
    body: Wire.DocumentControlReviewRequest,
    signal?: AbortSignal,
  ): Promise<Wire.DocumentControlReview>;
  executeDocumentControl?(
    body: Wire.Command,
    signal?: AbortSignal,
  ): Promise<Wire.DocumentControlReceipt>;
  documentControlReceipt?(
    command: string,
    signal?: AbortSignal,
  ): Promise<Wire.DocumentControlReceipt>;
  runtimeInstallation?(
    runtime: string,
    signal?: AbortSignal,
  ): Promise<Wire.RuntimeInstallationSnapshot>;
  reviewRuntimeInstallation?(
    body: Wire.RuntimeInstallationReviewRequest,
    signal?: AbortSignal,
  ): Promise<Wire.RuntimeInstallationReview>;
  executeRuntimeInstallation?(
    body: Wire.Command,
    signal?: AbortSignal,
  ): Promise<Wire.RuntimeInstallationReceipt>;
  runtimeInstallationReceipt?(
    runtime: string,
    command: string,
    signal?: AbortSignal,
  ): Promise<Wire.RuntimeInstallationReceipt>;
  providerConfiguration?(
    query: string,
    cursor?: string,
    signal?: AbortSignal,
  ): Promise<Wire.ProviderConfigurationPage>;
  reviewProviderConfiguration?(
    body: Wire.ProviderConfigurationReviewRequest,
    signal?: AbortSignal,
  ): Promise<Wire.ProviderConfigurationReview>;
  providerConfigurationReceipt?(
    command: string,
    signal?: AbortSignal,
  ): Promise<Wire.ProviderConfigurationReceipt>;
  defaultModel?(signal?: AbortSignal): Promise<Wire.DefaultModelSnapshot>;
  knowledgeEditor?(
    entity: string | null,
    signal?: AbortSignal,
  ): Promise<Wire.KnowledgeEditorState>;
  knowledgeEntityDetail?(
    entity: string,
    signal?: AbortSignal,
  ): Promise<Wire.KnowledgeEntityDetail>;
  knowledgeRecalls?(signal?: AbortSignal): Promise<Wire.KnowledgeRecallPage>;
  knowledgeChangeLog?(
    signal?: AbortSignal,
  ): Promise<Wire.KnowledgeMemoryChangePage>;
  knowledgeGraph?(
    limit?: number,
    signal?: AbortSignal,
  ): Promise<Wire.KnowledgeGraphSnapshot>;
  monitorSnapshot?(signal?: AbortSignal): Promise<Wire.MonitorSnapshot>;
  monitorLogs?(limit?: number, signal?: AbortSignal): Promise<Wire.MonitorLogs>;
  reviewDreamRun?(
    body: Wire.DreamRunRequest,
    signal?: AbortSignal,
  ): Promise<Wire.DreamRunReview>;
  dreamRunReceipt?(
    command: string,
    signal?: AbortSignal,
  ): Promise<Wire.DreamRunReceipt>;
  executeDreamRun?(
    command: Wire.DreamRunCommand,
    signal?: AbortSignal,
  ): Promise<Wire.DreamRunReceipt>;
  reviewKnowledgeMaintenance?(
    body: Wire.KnowledgeMaintenanceRequest,
    signal?: AbortSignal,
  ): Promise<Wire.KnowledgeMaintenanceReview>;
  knowledgeMaintenanceReceipt?(
    command: string,
    signal?: AbortSignal,
  ): Promise<Wire.KnowledgeMaintenanceReceipt>;
  executeKnowledgeMaintenance?(
    command: Wire.KnowledgeMaintenanceCommand,
    signal?: AbortSignal,
  ): Promise<Wire.KnowledgeMaintenanceReceipt>;
  knowledgeRelations?(
    entity: string,
    cursor?: string,
    signal?: AbortSignal,
  ): Promise<Wire.KnowledgeRelationPage>;
  reviewKnowledgeRelation?(
    body: Wire.KnowledgeRelationReviewRequest,
    signal?: AbortSignal,
  ): Promise<Wire.KnowledgeRelationReview>;
  knowledgeRelationReceipt?(
    command: string,
    signal?: AbortSignal,
  ): Promise<Wire.KnowledgeRelationReceipt>;
  executeKnowledgeRelation?(
    command: Wire.Command,
    signal?: AbortSignal,
  ): Promise<Wire.KnowledgeRelationReceipt>;
  reviewKnowledge?(
    body: Wire.KnowledgeReviewRequest,
    signal?: AbortSignal,
  ): Promise<Wire.KnowledgeReview>;
  knowledgeReceipt?(
    command: string,
    signal?: AbortSignal,
  ): Promise<Wire.KnowledgeReceipt>;
  executeKnowledge?(
    command: Wire.Command,
    signal?: AbortSignal,
  ): Promise<Wire.KnowledgeReceipt>;
  wikiStatus?(
    folderGrant?: string,
    signal?: AbortSignal,
  ): Promise<Wire.WikiStatus>;
  openWikiFolder?(signal?: AbortSignal): Promise<Wire.WikiOpenFolderResult>;
  wikiArticles?(
    folderGrant: string,
    cursor?: string,
    signal?: AbortSignal,
  ): Promise<Wire.WikiArticlePage>;
  wikiArticle?(
    folderGrant: string,
    article: string,
    signal?: AbortSignal,
  ): Promise<Wire.WikiArticle>;
  reviewWiki?(
    body: Wire.WikiReviewRequest,
    signal?: AbortSignal,
  ): Promise<Wire.WikiReview>;
  wikiReceipt?(
    folderGrant: string,
    command: string,
    signal?: AbortSignal,
  ): Promise<Wire.WikiReceipt>;
  executeWiki?(
    command: Wire.Command,
    signal?: AbortSignal,
  ): Promise<Wire.WikiReceipt>;
  channels?(query: string, signal?: AbortSignal): Promise<Wire.ChannelPage>;
  reviewChannel?(
    body: Wire.ChannelActionRequest,
    signal?: AbortSignal,
  ): Promise<Wire.ChannelActionReview>;
  channelReceipt?(
    channel: string,
    command: string,
    signal?: AbortSignal,
  ): Promise<Wire.ChannelReceipt>;
  executeChannel?(
    command: Wire.Command,
    signal?: AbortSignal,
  ): Promise<Wire.ChannelReceipt>;
  plugins?(
    query: string,
    source: string,
    cursor?: string,
    signal?: AbortSignal,
  ): Promise<Wire.PluginCatalogPage>;
  plugin?(plugin: string, signal?: AbortSignal): Promise<Wire.PluginDetail>;
  reviewPlugin?(
    plugin: string,
    body: Wire.PluginReviewRequest,
    signal?: AbortSignal,
  ): Promise<Wire.PluginReview>;
  pluginReceipt?(
    plugin: string,
    command: string,
    signal?: AbortSignal,
  ): Promise<Wire.PluginReceipt>;
  executePlugin?(
    plugin: string,
    command: Wire.Command,
    signal?: AbortSignal,
  ): Promise<Wire.PluginReceipt>;
  skills?(
    query: string,
    source?: string,
    cursor?: string,
    signal?: AbortSignal,
  ): Promise<Wire.SkillPage>;
  skill?(skill: string, signal?: AbortSignal): Promise<Wire.SkillDetail>;
  skillProposals?(signal?: AbortSignal): Promise<Wire.SkillProposalPage>;
  reviewSkill?(
    body: Wire.SkillReviewRequest,
    signal?: AbortSignal,
  ): Promise<Wire.SkillReview>;
  skillReceipt?(
    command: string,
    signal?: AbortSignal,
  ): Promise<Wire.SkillReceipt>;
  executeSkill?(
    command: Wire.Command,
    signal?: AbortSignal,
  ): Promise<Wire.SkillReceipt>;
  goals?(
    conversation: string,
    query: string,
    cursor?: string,
    signal?: AbortSignal,
  ): Promise<Wire.GoalPage>;
  goal?(
    conversation: string,
    goal: string,
    signal?: AbortSignal,
  ): Promise<Wire.GoalDetail>;
  reviewGoal?(
    conversation: string,
    body: Wire.GoalCommandPayload,
    signal?: AbortSignal,
  ): Promise<Wire.GoalReview>;
  goalReceipt?(
    conversation: string,
    command: string,
    signal?: AbortSignal,
  ): Promise<Wire.GoalReceipt>;
  executeGoal?(
    conversation: string,
    command: Wire.Command,
    signal?: AbortSignal,
  ): Promise<Wire.GoalReceipt>;
  profiles?(
    query: string,
    scope?: string,
    cursor?: string,
    signal?: AbortSignal,
  ): Promise<Wire.ProfilePage>;
  profile?(profile: string, signal?: AbortSignal): Promise<Wire.ProfileDetail>;
  reviewProfile?(
    body: Wire.ProfileCommandPayload,
    signal?: AbortSignal,
  ): Promise<Wire.ProfileReview>;
  profileReceipt?(
    profile: string,
    command: string,
    signal?: AbortSignal,
  ): Promise<Wire.ProfileReceipt>;
  executeProfile?(
    command: Wire.Command,
    signal?: AbortSignal,
  ): Promise<Wire.ProfileReceipt>;
  channels?(query: string, signal?: AbortSignal): Promise<Wire.ChannelPage>;
  reviewChannel?(
    body: Wire.ChannelActionRequest,
    signal?: AbortSignal,
  ): Promise<Wire.ChannelActionReview>;
  channelReceipt?(
    channel: string,
    command: string,
    signal?: AbortSignal,
  ): Promise<Wire.ChannelReceipt>;
  executeChannel?(
    command: Wire.Command,
    signal?: AbortSignal,
  ): Promise<Wire.ChannelReceipt>;
  plugins?(
    query: string,
    source: string,
    cursor?: string,
    signal?: AbortSignal,
  ): Promise<Wire.PluginCatalogPage>;
  plugin?(plugin: string, signal?: AbortSignal): Promise<Wire.PluginDetail>;
  reviewPlugin?(
    plugin: string,
    body: Wire.PluginReviewRequest,
    signal?: AbortSignal,
  ): Promise<Wire.PluginReview>;
  pluginReceipt?(
    plugin: string,
    command: string,
    signal?: AbortSignal,
  ): Promise<Wire.PluginReceipt>;
  executePlugin?(
    plugin: string,
    command: Wire.Command,
    signal?: AbortSignal,
  ): Promise<Wire.PluginReceipt>;
  skills?(
    query: string,
    source?: string,
    cursor?: string,
    signal?: AbortSignal,
  ): Promise<Wire.SkillPage>;
  skill?(skill: string, signal?: AbortSignal): Promise<Wire.SkillDetail>;
  skillProposals?(signal?: AbortSignal): Promise<Wire.SkillProposalPage>;
  reviewSkill?(
    body: Wire.SkillReviewRequest,
    signal?: AbortSignal,
  ): Promise<Wire.SkillReview>;
  skillReceipt?(
    command: string,
    signal?: AbortSignal,
  ): Promise<Wire.SkillReceipt>;
  executeSkill?(
    command: Wire.Command,
    signal?: AbortSignal,
  ): Promise<Wire.SkillReceipt>;
  mcpTestedCatalog?(
    server: string,
    command: string,
    query: string,
    cursor?: string,
    signal?: AbortSignal,
  ): Promise<Wire.McpTestedCatalogPage>;
  reviewMcpCatalog?(
    body: Wire.McpCatalogRequest,
    signal?: AbortSignal,
  ): Promise<Wire.McpCatalogReview>;
  mcpPolicy?(
    server: string | null,
    query: string,
    cursor?: string,
    signal?: AbortSignal,
  ): Promise<Wire.McpPolicyPage>;
  reviewMcpPolicy?(
    body: Wire.McpPolicyRequest,
    signal?: AbortSignal,
  ): Promise<Wire.McpPolicyReview>;
  mcpRuntime?(
    server: string,
    signal?: AbortSignal,
  ): Promise<Wire.McpRuntimeState>;
  reviewMcpRuntime?(
    body: Wire.McpRuntimeReviewRequest,
    signal?: AbortSignal,
  ): Promise<Wire.McpRuntimeReview>;
  subscriptionAccounts?(
    signal?: AbortSignal,
  ): Promise<Wire.SubscriptionAccountsSnapshot>;
  reviewDocumentRemoval?(
    document: string | null,
    signal?: AbortSignal,
  ): Promise<Wire.DocumentRemovalReview>;
  reviewDocumentRemovalRetry?(
    command: string,
    signal?: AbortSignal,
  ): Promise<Wire.DocumentRemovalReview>;
  documentRemovalReceipt?(
    command: string,
    signal?: AbortSignal,
  ): Promise<Wire.DocumentRemovalReceipt>;
  executeDocumentRemoval?(
    command: Wire.Command,
    signal?: AbortSignal,
  ): Promise<Wire.DocumentRemovalReceipt>;
  buddy?(
    conversation: string,
    signal?: AbortSignal,
  ): Promise<Wire.BuddySnapshot>;
  buddyPacks?(
    conversation: string,
    cursor?: string,
    signal?: AbortSignal,
  ): Promise<Wire.BuddyPackPage>;
  buddyPack?(
    conversation: string,
    pack: string,
    signal?: AbortSignal,
  ): Promise<Wire.BuddyPack>;
  buddyMedia?(
    conversation: string,
    pack: string,
    asset: string,
    revision: string,
    signal?: AbortSignal,
  ): Promise<Blob>;
  reviewBuddy?(
    conversation: string,
    body: Wire.BuddyHatchRequest,
    signal?: AbortSignal,
  ): Promise<Wire.BuddyHatchReview>;
  buddyReceipt?(
    conversation: string,
    command: string,
    signal?: AbortSignal,
  ): Promise<Wire.BuddyReceipt>;
  executeBuddy?(
    conversation: string,
    command: Wire.Command,
    signal?: AbortSignal,
  ): Promise<Wire.BuddyReceipt>;
  subscriptionProbes?(
    signal?: AbortSignal,
  ): Promise<Wire.SubscriptionProbeSnapshot>;
  reviewSubscriptionProbe?(
    body: Wire.SubscriptionProbeRequest,
    signal?: AbortSignal,
  ): Promise<Wire.SubscriptionProbeReview>;
  applySubscriptionProbe?(
    command: Wire.Command,
    signal?: AbortSignal,
  ): Promise<Wire.SubscriptionProbeCommandResult>;
  subscriptionProbeStatus?(
    command: string,
    signal?: AbortSignal,
  ): Promise<Wire.SubscriptionProbeStatus>;
  cancelSubscriptionProbe?(
    command: string,
    signal?: AbortSignal,
  ): Promise<Wire.SubscriptionProbeState>;
  subscriptionProbeReceipt?(
    command: string,
    signal?: AbortSignal,
  ): Promise<Wire.SubscriptionProbeReceipt>;
  subscriptionOptions?(
    signal?: AbortSignal,
  ): Promise<Wire.SubscriptionOptionsSnapshot>;
  reviewSubscriptionOptions?(
    body: Wire.SubscriptionOptionsRequest,
    signal?: AbortSignal,
  ): Promise<Wire.SubscriptionOptionsReview>;
  subscriptionOptionsReceipt?(
    command: string,
    signal?: AbortSignal,
  ): Promise<Wire.SubscriptionOptionsReceipt>;
  applySubscriptionOptions?(
    command: Wire.Command,
    signal?: AbortSignal,
  ): Promise<Wire.SubscriptionOptionsResult>;
  cancelSubscriptionStart?(
    command: string,
    signal?: AbortSignal,
  ): Promise<Wire.SubscriptionFlowSnapshot>;
  reviewSubscriptionAction?(
    body: Wire.SubscriptionActionRequest,
    signal?: AbortSignal,
  ): Promise<Wire.SubscriptionActionReview>;
  subscriptionFlow?(
    flow: string,
    epoch: string,
    signal?: AbortSignal,
  ): Promise<Wire.SubscriptionFlowSnapshot>;
  subscriptionReceipt?(
    provider: string,
    command: string,
    signal?: AbortSignal,
  ): Promise<Wire.SubscriptionActionReceipt>;
  subscriptionAction?(
    command: Wire.Command,
    signal?: AbortSignal,
  ): Promise<Wire.SubscriptionActionResult>;
  revokeSubscriptionFlows?(
    signal?: AbortSignal,
  ): Promise<Wire.SubscriptionQuiescence>;
  mcpConfiguration?(
    query: string,
    cursor?: string,
    signal?: AbortSignal,
  ): Promise<Wire.McpConfigurationPage>;
  reviewMcpConfiguration?(
    body: Wire.McpConfigurationReviewRequest,
    signal?: AbortSignal,
  ): Promise<Wire.McpConfigurationReview>;
  reviewDefaultModel?(
    body: Wire.DefaultModelReviewRequest,
    signal?: AbortSignal,
  ): Promise<Wire.DefaultModelReview>;
  defaultModelReceipt?(
    command: string,
    signal?: AbortSignal,
  ): Promise<Wire.DefaultModelReceipt>;
  providerSettings?(
    provider: string,
    signal?: AbortSignal,
  ): Promise<Wire.ProviderSettingsSnapshot>;
  reviewProviderSettings?(
    provider: string,
    body: Wire.ProviderSettingsReviewRequest,
    signal?: AbortSignal,
  ): Promise<Wire.ProviderSettingsReview>;
  providerSettingsReceipt?(
    provider: string,
    command: string,
    signal?: AbortSignal,
  ): Promise<Wire.ProviderSettingsReceipt>;
  artifactReviewDraft?(
    conversation: string,
    binding: string,
    body: Wire.ArtifactReviewDraftRequest,
    signal?: AbortSignal,
  ): Promise<Wire.ArtifactReviewDraft>;
  reviewArtifactPreset?(
    conversation: string,
    binding: string,
    body: Wire.ArtifactPresetReviewRequest,
    signal?: AbortSignal,
  ): Promise<Wire.ArtifactPresetReview>;
  stageArtifactUpload?(
    conversation: string,
    file: File,
    commandId: string,
    signal?: AbortSignal,
  ): Promise<{ upload_id: string; sha256: string; size_bytes: number }>;
  savedEntities?(
    query?: string,
    entityType?: string,
    cursor?: string,
    signal?: AbortSignal,
  ): Promise<Wire.EntitySummaryPage>;
  knowledgeEntities?(
    query?: string,
    entityType?: string,
    status?: string,
    source?: string,
    tier?: string,
    cursor?: string,
    signal?: AbortSignal,
  ): Promise<Wire.EntitySummaryPage>;
  savedDocuments?(
    query?: string,
    status?: string,
    cursor?: string,
    signal?: AbortSignal,
  ): Promise<Wire.DocumentSummaryPage>;
  cachedTools?(
    source?: Wire.ToolCatalogPage['items'][number]['source'],
    query?: string,
    cursor?: string,
    signal?: AbortSignal,
  ): Promise<Wire.ToolCatalogPage>;
  settingsSnapshot?(signal?: AbortSignal): Promise<Wire.SettingsSnapshot>;
  reviewSettingsMutation?(
    body: Wire.SettingsMutationRequest,
    signal?: AbortSignal,
  ): Promise<Wire.SettingsMutationReview>;
  settingsMutationReceipt?(
    command: string,
    signal?: AbortSignal,
  ): Promise<Wire.SettingsMutationReceipt>;
  executeSettingsMutation?(
    command: Wire.SettingsMutationCommand,
    signal?: AbortSignal,
  ): Promise<Wire.SettingsMutationReceipt>;
  savedTasks?(
    query?: string,
    enabled?: boolean,
    cursor?: string,
    signal?: AbortSignal,
  ): Promise<Wire.TaskSummaryPage>;
  taskDeliveryDefaults?(
    signal?: AbortSignal,
  ): Promise<Wire.TaskDeliverySnapshot>;
  cachedModels?(
    providerId?: string,
    query?: string,
    cursor?: string,
    signal?: AbortSignal,
  ): Promise<Wire.CachedModelPage>;
  modelsSettings?(signal?: AbortSignal): Promise<Wire.ModelsSettingsState>;
  updateModelSurface?(
    body: Wire.ModelSurfaceMutation,
    signal?: AbortSignal,
  ): Promise<Wire.ModelsSettingsState>;
  updateModelContext?(
    body: Wire.ModelContextMutation,
    signal?: AbortSignal,
  ): Promise<Wire.ModelsSettingsState>;
  agentRuntimeSettings?(
    signal?: AbortSignal,
  ): Promise<Wire.AgentRuntimeSettingsState>;
  saveAgentRuntimeSettings?(
    body: Wire.AgentRuntimeSettingsState,
    signal?: AbortSignal,
  ): Promise<Wire.AgentRuntimeSettingsState>;
  resetAgentRuntimeSettings?(
    signal?: AbortSignal,
  ): Promise<Wire.AgentRuntimeSettingsState>;
  modelCatalogSummary?(
    surface: string,
    signal?: AbortSignal,
  ): Promise<Wire.ModelCatalogSummary>;
  modelCatalogPage?(
    surface: string,
    providerId?: string,
    query?: string,
    cursor?: string,
    signal?: AbortSignal,
  ): Promise<Wire.CachedModelPage>;
  refreshModelsCatalog?(
    signal?: AbortSignal,
  ): Promise<Wire.ProviderCatalogRefresh>;
  refreshModelCameras?(signal?: AbortSignal): Promise<Wire.ModelCameraList>;
  artifactSetup?(
    mode: NonNullable<Wire.ArtifactSetupOptions['mode']>,
    signal?: AbortSignal,
  ): Promise<Wire.ArtifactSetupOptions>;
  pickFolder?(signal?: AbortSignal): Promise<Wire.FolderGrantView>;
  artifactShareChannels?(
    cursor?: string,
    signal?: AbortSignal,
  ): Promise<Wire.ArtifactShareChannels>;
  prepareArtifactShare?(
    conversation: string,
    binding: string,
    options: Wire.ArtifactShareOptions,
    signal?: AbortSignal,
  ): Promise<Wire.ArtifactShareReview>;
  artifactPreview?(
    conversation: string,
    binding: string,
    pageId?: string,
    knownRevision?: string,
    signal?: AbortSignal,
    authoring?: Wire.ArtifactAuthoring,
  ): Promise<Wire.ArtifactPreview>;
  artifactLifecycle?(
    conversation: string,
    binding: string,
    expectedRevision: string,
    signal?: AbortSignal,
  ): Promise<Wire.ArtifactLifecycleState>;
  artifactStaticPreview?(
    conversation: string,
    binding: string,
    pageId: string,
    signal?: AbortSignal,
  ): Promise<Wire.ArtifactPreview>;
  designControls?(
    conversation: string,
    binding: string,
    options: Wire.DesignControlOptions,
    signal?: AbortSignal,
  ): Promise<Wire.DesignControlsState>;
  designReview?(
    conversation: string,
    binding: string,
    options: Wire.DesignReviewOptions,
    signal?: AbortSignal,
  ): Promise<Wire.DesignReviewState>;
  designPresentation?(
    conversation: string,
    binding: string,
    options: Wire.DesignPresentationOptions,
    signal?: AbortSignal,
  ): Promise<Wire.DesignPresentationState>;
  artifactEditing?(
    conversation: string,
    binding: string,
    pageId?: string,
    pageCursor?: string,
    elementCursor?: string,
    historyCursor?: string,
    elementId?: string,
    limit?: number,
    signal?: AbortSignal,
  ): Promise<Wire.ArtifactEditingState>;
  inspector?(
    conversation: string,
    binding: string,
    refresh?: boolean,
    signal?: AbortSignal,
  ): Promise<Wire.WorkspaceInspector>;
  changes?(
    conversation: string,
    binding: string,
    revision?: string,
    cursor?: string,
    signal?: AbortSignal,
  ): Promise<Wire.WorkspaceChanges>;
  directory?(
    conversation: string,
    binding: string,
    directory?: string,
    cursor?: string,
    revision?: string,
    signal?: AbortSignal,
  ): Promise<Wire.WorkspaceDirectory>;
  file?(
    conversation: string,
    binding: string,
    path: string,
    offset?: number,
    revision?: string,
    signal?: AbortSignal,
  ): Promise<Wire.WorkspaceFile>;
  diff?(
    conversation: string,
    binding: string,
    path: string,
    snapshot: string,
    offset?: number,
    revision?: string,
    signal?: AbortSignal,
  ): Promise<Wire.WorkspaceDiff>;
  changeSets?(
    conversation: string,
    binding: string,
    revision: string,
    cursor?: string,
    signal?: AbortSignal,
  ): Promise<Wire.WorkspaceChangeSetPage>;
  changeSetFiles?(
    conversation: string,
    binding: string,
    change: string,
    revision: string,
    cursor?: string,
    signal?: AbortSignal,
  ): Promise<Wire.WorkspaceChangeSetFiles>;
  approval?(identity: string, signal?: AbortSignal): Promise<Wire.ApprovalView>;
  content?(
    conversation: string,
    message: string,
    cursor?: string,
    signal?: AbortSignal,
  ): Promise<Wire.LazyContent>;
  connect(signal?: AbortSignal): Promise<Wire.HandshakeView>;
  listConversations(
    cursor?: string,
    signal?: AbortSignal,
    group?: ClientState['conversationGroup'],
  ): Promise<Wire.ConversationPage>;
  getConversation(
    id: string,
    signal?: AbortSignal,
  ): Promise<Wire.ConversationView>;
  getTranscript(
    id: string,
    cursor?: string,
    signal?: AbortSignal,
  ): Promise<Wire.TranscriptPage>;
  subscribe(id: string, signal?: AbortSignal): Promise<Wire.SubscriptionView>;
  observe(
    subscription: string,
    cursor: string,
    signal: AbortSignal,
  ): AsyncIterable<Wire.EventRecord | Wire.StreamReset>;
  poll(
    subscription: string,
    cursor: string,
    signal?: AbortSignal,
  ): Promise<Wire.EventPage>;
  acknowledge(
    subscription: string,
    cursor: string,
    signal?: AbortSignal,
  ): Promise<Wire.Acknowledged>;
  unsubscribe(
    subscription: string,
    signal?: AbortSignal,
    keepalive?: boolean,
  ): Promise<Wire.Unsubscribed>;
  command(
    target: string | null,
    command: Wire.Command,
    key: string,
    signal?: AbortSignal,
  ): Promise<Wire.CommandReceipt>;
  receipt(id: string, signal?: AbortSignal): Promise<Wire.CommandReceipt>;
  upload(
    conversation: string,
    file: File,
    signal?: AbortSignal,
  ): Promise<Wire.AttachmentView>;
  attachmentMetadata(
    reference: string,
    signal?: AbortSignal,
  ): Promise<Wire.AttachmentView>;
  terminalRead(
    terminal: string,
    cursor: number,
    signal?: AbortSignal,
  ): Promise<Wire.NativeTerminalOutput>;
  terminalInput(
    terminal: string,
    data: string,
    signal?: AbortSignal,
  ): Promise<Wire.NativeTerminalChanged>;
  terminalResize(
    terminal: string,
    cols: number,
    rows: number,
    signal?: AbortSignal,
  ): Promise<Wire.NativeTerminalChanged>;
  terminalDisconnect(
    terminal: string,
    signal?: AbortSignal,
  ): Promise<Wire.NativeTerminalClosed>;
  download(reference: string, signal?: AbortSignal): Promise<Blob>;
  clearSession(preserveResumeIdentity?: boolean): void;
}
