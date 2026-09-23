import * as wire from '../../../contracts/client-platform/v1/typescript/client';
import type { ClientTransport } from './types';

/** Same-origin generated transport; only the in-memory owner holds the CSRF proof. */
export class HttpTransport implements ClientTransport {
  private proof: wire.SessionProof | undefined;
  private resumeSessionId: string | undefined;
  private subscriptionProof: wire.SessionProof | undefined;
  constructor(private readonly base = '') {
    if (base && new URL(base, location.origin).origin !== location.origin)
      throw new Error('Cross-origin transport is unavailable');
  }

  private session(): wire.SessionProof {
    if (!this.proof) throw { code: 'authentication_required', status: 401 };
    return this.proof;
  }

  async connect(signal?: AbortSignal): Promise<wire.HandshakeView> {
    const view = await wire.handshake(
      this.base,
      {
        protocol_major: 1,
        minimum_minor: 0,
        maximum_minor: 0,
        client_build: 'row-bot-client-v2',
        presentation_features: ['panels', 'responsive'],
        ...(this.proof || this.resumeSessionId
          ? {
              client_session_id:
                this.proof?.client_session_id ?? this.resumeSessionId,
            }
          : {}),
      },
      signal,
    );
    signal?.throwIfAborted();
    this.proof = {
      client_session_id: view.client_session_id,
      csrf_token: view.csrf_token,
    };
    return view;
  }
  listConversations(cursor?: string, signal?: AbortSignal, group = 'all') {
    return wire.listConversations(
      this.base,
      this.session(),
      50,
      cursor,
      signal,
      group,
    );
  }
  conversationActions(conversation: string, signal?: AbortSignal) {
    return wire.getConversationActions(
      this.base,
      this.session(),
      conversation,
      signal,
    );
  }
  reviewConversationAction(
    conversation: string,
    body: wire.ConversationActionReviewRequest,
    signal?: AbortSignal,
  ) {
    return wire.reviewConversationAction(
      this.base,
      this.session(),
      conversation,
      body,
      signal,
    );
  }
  conversationActionReceipt(
    conversation: string,
    command: string,
    signal?: AbortSignal,
  ) {
    return wire.getConversationActionReceipt(
      this.base,
      this.session(),
      conversation,
      command,
      signal,
    );
  }
  executeConversationAction(
    conversation: string,
    command: wire.ConversationActionCommand,
    signal?: AbortSignal,
  ) {
    return wire.sendConversationAction(
      this.base,
      this.session(),
      conversation,
      command,
      signal,
    );
  }
  browserControls(conversation: string, signal?: AbortSignal) {
    return wire.getBrowserControls(
      this.base,
      this.session(),
      conversation,
      signal,
    );
  }
  reviewBrowserControl(
    conversation: string,
    body: wire.BrowserReviewRequest,
    signal?: AbortSignal,
  ) {
    return wire.reviewBrowserControl(
      this.base,
      this.session(),
      conversation,
      body,
      signal,
    );
  }
  browserControlReceipt(
    conversation: string,
    command: string,
    signal?: AbortSignal,
  ) {
    return wire.getBrowserControlReceipt(
      this.base,
      this.session(),
      conversation,
      command,
      signal,
    );
  }
  executeBrowserControl(
    conversation: string,
    command: wire.Command,
    signal?: AbortSignal,
  ) {
    return wire.sendBrowserControl(
      this.base,
      this.session(),
      conversation,
      command,
      signal,
    );
  }
  dictationCapability(signal?: AbortSignal) {
    return wire.dictationCapability(this.base, this.session(), signal);
  }
  defaultModel(signal?: AbortSignal) {
    return wire.getDefaultModel(this.base, this.session(), signal);
  }
  knowledgeEditor(entity: string | null, signal?: AbortSignal) {
    return wire.getKnowledgeEditor(this.base, this.session(), entity, signal);
  }
  knowledgeEntityDetail(entity: string, signal?: AbortSignal) {
    return wire.getSavedEntityDetail(this.base, this.session(), entity, signal);
  }
  knowledgeRecalls(signal?: AbortSignal) {
    return wire.getKnowledgeRecalls(this.base, this.session(), signal);
  }
  knowledgeChangeLog(signal?: AbortSignal) {
    return wire.getKnowledgeChangeLog(this.base, this.session(), signal);
  }
  knowledgeGraph(limit = 250, signal?: AbortSignal) {
    return wire.getKnowledgeGraph(this.base, this.session(), limit, signal);
  }
  monitorSnapshot(signal?: AbortSignal) {
    return wire.getMonitorSnapshot(this.base, this.session(), signal);
  }
  monitorLogs(limit = 200, signal?: AbortSignal) {
    return wire.getMonitorLogs(this.base, this.session(), limit, signal);
  }
  reviewDreamRun(body: wire.DreamRunRequest, signal?: AbortSignal) {
    return wire.reviewDreamRun(this.base, this.session(), body, signal);
  }
  dreamRunReceipt(command: string, signal?: AbortSignal) {
    return wire.getDreamRunReceipt(this.base, this.session(), command, signal);
  }
  executeDreamRun(command: wire.DreamRunCommand, signal?: AbortSignal) {
    return wire.sendDreamRun(this.base, this.session(), command, signal);
  }
  reviewKnowledgeMaintenance(
    body: wire.KnowledgeMaintenanceRequest,
    signal?: AbortSignal,
  ) {
    return wire.reviewKnowledgeMaintenance(
      this.base,
      this.session(),
      body,
      signal,
    );
  }
  knowledgeMaintenanceReceipt(command: string, signal?: AbortSignal) {
    return wire.getKnowledgeMaintenanceReceipt(
      this.base,
      this.session(),
      command,
      signal,
    );
  }
  executeKnowledgeMaintenance(
    command: wire.KnowledgeMaintenanceCommand,
    signal?: AbortSignal,
  ) {
    return wire.sendKnowledgeMaintenance(
      this.base,
      this.session(),
      command,
      signal,
    );
  }
  knowledgeRelations(entity: string, cursor?: string, signal?: AbortSignal) {
    return wire.getKnowledgeRelations(
      this.base,
      this.session(),
      entity,
      cursor,
      signal,
    );
  }
  reviewKnowledgeRelation(
    body: wire.KnowledgeRelationReviewRequest,
    signal?: AbortSignal,
  ) {
    return wire.reviewKnowledgeRelation(
      this.base,
      this.session(),
      body,
      signal,
    );
  }
  knowledgeRelationReceipt(command: string, signal?: AbortSignal) {
    return wire.getKnowledgeRelationReceipt(
      this.base,
      this.session(),
      command,
      signal,
    );
  }
  executeKnowledgeRelation(command: wire.Command, signal?: AbortSignal) {
    return wire.sendKnowledgeRelation(
      this.base,
      this.session(),
      command,
      signal,
    );
  }
  reviewKnowledge(body: wire.KnowledgeReviewRequest, signal?: AbortSignal) {
    return wire.reviewKnowledge(this.base, this.session(), body, signal);
  }
  knowledgeReceipt(command: string, signal?: AbortSignal) {
    return wire.getKnowledgeReceipt(this.base, this.session(), command, signal);
  }
  executeKnowledge(command: wire.Command, signal?: AbortSignal) {
    return wire.sendKnowledge(this.base, this.session(), command, signal);
  }
  wikiStatus(folderGrant?: string, signal?: AbortSignal) {
    return wire.getWikiStatus(this.base, this.session(), folderGrant, signal);
  }
  openWikiFolder(signal?: AbortSignal) {
    return wire.openWikiFolder(this.base, this.session(), signal);
  }
  wikiArticles(folderGrant: string, cursor?: string, signal?: AbortSignal) {
    return wire.getWikiArticles(
      this.base,
      this.session(),
      folderGrant,
      cursor,
      signal,
    );
  }
  wikiArticle(folderGrant: string, article: string, signal?: AbortSignal) {
    return wire.getWikiArticle(
      this.base,
      this.session(),
      folderGrant,
      article,
      signal,
    );
  }
  reviewWiki(body: wire.WikiReviewRequest, signal?: AbortSignal) {
    return wire.reviewWiki(this.base, this.session(), body, signal);
  }
  wikiReceipt(folderGrant: string, command: string, signal?: AbortSignal) {
    return wire.getWikiReceipt(
      this.base,
      this.session(),
      folderGrant,
      command,
      signal,
    );
  }
  executeWiki(command: wire.Command, signal?: AbortSignal) {
    return wire.sendWiki(this.base, this.session(), command, signal);
  }
  channels(query: string, signal?: AbortSignal) {
    return wire.getChannels(this.base, this.session(), query, signal);
  }
  reviewChannel(body: wire.ChannelActionRequest, signal?: AbortSignal) {
    return wire.reviewChannel(this.base, this.session(), body, signal);
  }
  channelReceipt(channel: string, command: string, signal?: AbortSignal) {
    return wire.getChannelReceipt(
      this.base,
      this.session(),
      channel,
      command,
      signal,
    );
  }
  executeChannel(command: wire.Command, signal?: AbortSignal) {
    return wire.sendChannel(this.base, this.session(), command, signal);
  }
  plugins(
    query: string,
    source: string,
    cursor?: string,
    signal?: AbortSignal,
  ) {
    return wire.getPlugins(
      this.base,
      this.session(),
      query,
      source,
      cursor,
      signal,
    );
  }
  plugin(plugin: string, signal?: AbortSignal) {
    return wire.getPlugin(this.base, this.session(), plugin, signal);
  }
  reviewPlugin(
    plugin: string,
    body: wire.PluginReviewRequest,
    signal?: AbortSignal,
  ) {
    return wire.reviewPlugin(this.base, this.session(), plugin, body, signal);
  }
  pluginReceipt(plugin: string, command: string, signal?: AbortSignal) {
    return wire.getPluginReceipt(
      this.base,
      this.session(),
      plugin,
      command,
      signal,
    );
  }
  executePlugin(plugin: string, command: wire.Command, signal?: AbortSignal) {
    return wire.sendPlugin(this.base, this.session(), plugin, command, signal);
  }
  skills(
    query: string,
    source?: string,
    cursor?: string,
    signal?: AbortSignal,
  ) {
    return wire.getSkills(
      this.base,
      this.session(),
      query,
      source,
      cursor,
      signal,
    );
  }
  skill(skill: string, signal?: AbortSignal) {
    return wire.getSkill(this.base, this.session(), skill, signal);
  }
  skillProposals(signal?: AbortSignal) {
    return wire.getSkillProposals(this.base, this.session(), signal);
  }
  reviewSkill(body: wire.SkillReviewRequest, signal?: AbortSignal) {
    return wire.reviewSkill(this.base, this.session(), body, signal);
  }
  skillReceipt(command: string, signal?: AbortSignal) {
    return wire.getSkillReceipt(this.base, this.session(), command, signal);
  }
  executeSkill(command: wire.Command, signal?: AbortSignal) {
    return wire.sendSkill(this.base, this.session(), command, signal);
  }
  goals(
    conversation: string,
    query: string,
    cursor?: string,
    signal?: AbortSignal,
  ) {
    return wire.getGoals(
      this.base,
      this.session(),
      conversation,
      query,
      cursor,
      signal,
    );
  }
  goal(conversation: string, goal: string, signal?: AbortSignal) {
    return wire.getGoal(this.base, this.session(), conversation, goal, signal);
  }
  reviewGoal(
    conversation: string,
    body: wire.GoalCommandPayload,
    signal?: AbortSignal,
  ) {
    return wire.reviewGoal(
      this.base,
      this.session(),
      conversation,
      body,
      signal,
    );
  }
  goalReceipt(conversation: string, command: string, signal?: AbortSignal) {
    return wire.getGoalReceipt(
      this.base,
      this.session(),
      conversation,
      command,
      signal,
    );
  }
  executeGoal(
    conversation: string,
    command: wire.Command,
    signal?: AbortSignal,
  ) {
    return wire.sendGoal(
      this.base,
      this.session(),
      conversation,
      command,
      signal,
    );
  }
  profiles(
    query: string,
    scope?: string,
    cursor?: string,
    signal?: AbortSignal,
  ) {
    return wire.getProfiles(
      this.base,
      this.session(),
      query,
      scope,
      cursor,
      signal,
    );
  }
  profile(profile: string, signal?: AbortSignal) {
    return wire.getProfile(this.base, this.session(), profile, signal);
  }
  reviewProfile(body: wire.ProfileCommandPayload, signal?: AbortSignal) {
    return wire.reviewProfile(this.base, this.session(), body, signal);
  }
  profileReceipt(profile: string, command: string, signal?: AbortSignal) {
    return wire.getProfileReceipt(
      this.base,
      this.session(),
      profile,
      command,
      signal,
    );
  }
  executeProfile(command: wire.Command, signal?: AbortSignal) {
    return wire.sendProfile(this.base, this.session(), command, signal);
  }
  subscriptionAccounts(signal?: AbortSignal) {
    return wire.getSubscriptionAccounts(this.base, this.session(), signal);
  }
  mcpPolicy(
    server: string | null,
    query: string,
    cursor?: string,
    signal?: AbortSignal,
  ) {
    return wire.getMcpPolicy(
      this.base,
      this.session(),
      server,
      query,
      cursor,
      signal,
    );
  }
  mcpTestedCatalog(
    server: string,
    command: string,
    query: string,
    cursor?: string,
    signal?: AbortSignal,
  ) {
    return wire.getMcpTestedCatalog(
      this.base,
      this.session(),
      server,
      command,
      query,
      cursor,
      signal,
    );
  }
  reviewMcpCatalog(body: wire.McpCatalogRequest, signal?: AbortSignal) {
    return wire.reviewMcpCatalog(this.base, this.session(), body, signal);
  }
  documentQueue(
    kind: string,
    batch?: string,
    cursor?: string,
    signal?: AbortSignal,
  ) {
    return wire.getDocumentQueue(
      this.base,
      this.session(),
      kind,
      batch,
      cursor,
      signal,
    );
  }
  reviewDocumentControl(
    body: wire.DocumentControlReviewRequest,
    signal?: AbortSignal,
  ) {
    return wire.reviewDocumentControl(this.base, this.session(), body, signal);
  }
  executeDocumentControl(body: wire.Command, signal?: AbortSignal) {
    return wire.executeDocumentControl(this.base, this.session(), body, signal);
  }
  documentControlReceipt(command: string, signal?: AbortSignal) {
    return wire.getDocumentControlReceipt(
      this.base,
      this.session(),
      command,
      signal,
    );
  }
  reviewDocumentUpload(
    files: wire.DocumentUploadReviewRequest['files'],
    signal?: AbortSignal,
  ) {
    return wire.reviewDocumentUpload(
      this.base,
      this.session(),
      { files },
      signal,
    );
  }
  uploadDocuments(
    command: wire.Command,
    files: readonly File[],
    signal?: AbortSignal,
  ) {
    return wire.uploadDocuments(
      this.base,
      this.session(),
      command,
      files,
      signal,
    );
  }
  documentUploadReceipt(command: string, signal?: AbortSignal) {
    return wire.getDocumentUploadReceipt(
      this.base,
      this.session(),
      command,
      signal,
    );
  }
  reviewDocumentProcessing(
    conversation: string,
    body: wire.DocumentProcessingReviewRequest,
    signal?: AbortSignal,
  ) {
    return wire.reviewDocumentProcessing(
      this.base,
      this.session(),
      conversation,
      body,
      signal,
    );
  }
  executeDocumentProcessing(
    conversation: string,
    command: wire.Command,
    signal?: AbortSignal,
  ) {
    return wire.executeDocumentProcessing(
      this.base,
      this.session(),
      conversation,
      command,
      signal,
    );
  }
  documentProcessingReceipt(
    conversation: string,
    command: string,
    signal?: AbortSignal,
  ) {
    return wire.getDocumentProcessingReceipt(
      this.base,
      this.session(),
      conversation,
      command,
      signal,
    );
  }
  runtimeInstallation(runtime: string, signal?: AbortSignal) {
    return wire.getRuntimeInstallation(
      this.base,
      this.session(),
      runtime,
      signal,
    );
  }
  reviewRuntimeInstallation(
    body: wire.RuntimeInstallationReviewRequest,
    signal?: AbortSignal,
  ) {
    return wire.reviewRuntimeInstallation(
      this.base,
      this.session(),
      body,
      signal,
    );
  }
  executeRuntimeInstallation(body: wire.Command, signal?: AbortSignal) {
    return wire.executeRuntimeInstallation(
      this.base,
      this.session(),
      body,
      signal,
    );
  }
  runtimeInstallationReceipt(
    runtime: string,
    command: string,
    signal?: AbortSignal,
  ) {
    return wire.getRuntimeInstallationReceipt(
      this.base,
      this.session(),
      runtime,
      command,
      signal,
    );
  }
  reviewMcpPolicy(body: wire.McpPolicyRequest, signal?: AbortSignal) {
    return wire.reviewMcpPolicy(this.base, this.session(), body, signal);
  }
  reviewDocumentRemoval(document: string | null, signal?: AbortSignal) {
    return wire.reviewDocumentRemoval(
      this.base,
      this.session(),
      document,
      signal,
    );
  }
  reviewDocumentRemovalRetry(command: string, signal?: AbortSignal) {
    return wire.reviewDocumentRemovalRetry(
      this.base,
      this.session(),
      command,
      signal,
    );
  }
  documentRemovalReceipt(command: string, signal?: AbortSignal) {
    return wire.getDocumentRemovalReceipt(
      this.base,
      this.session(),
      command,
      signal,
    );
  }
  executeDocumentRemoval(command: wire.Command, signal?: AbortSignal) {
    return wire.sendDocumentRemoval(this.base, this.session(), command, signal);
  }
  buddy(conversation: string, signal?: AbortSignal) {
    return wire.getBuddy(this.base, this.session(), conversation, signal);
  }
  globalBuddy(signal?: AbortSignal) {
    return wire.getGlobalBuddy(this.base, this.session(), signal);
  }
  globalBuddyPacks(cursor?: string, signal?: AbortSignal) {
    return wire.getGlobalBuddyPacks(this.base, this.session(), cursor, signal);
  }
  globalBuddyPack(pack: string, signal?: AbortSignal) {
    return wire.getGlobalBuddyPack(this.base, this.session(), pack, signal);
  }
  globalBuddyMedia(
    pack: string,
    asset: string,
    revision: string,
    signal?: AbortSignal,
  ) {
    return wire.getGlobalBuddyMedia(
      this.base,
      this.session(),
      pack,
      asset,
      revision,
      signal,
    );
  }
  buddyPacks(conversation: string, cursor?: string, signal?: AbortSignal) {
    return wire.getBuddyPacks(
      this.base,
      this.session(),
      conversation,
      cursor,
      signal,
    );
  }
  buddyPack(conversation: string, pack: string, signal?: AbortSignal) {
    return wire.getBuddyPack(
      this.base,
      this.session(),
      conversation,
      pack,
      signal,
    );
  }
  buddyMedia(
    conversation: string,
    pack: string,
    asset: string,
    revision: string,
    signal?: AbortSignal,
  ) {
    return wire.getBuddyMedia(
      this.base,
      this.session(),
      conversation,
      pack,
      asset,
      revision,
      signal,
    );
  }
  reviewBuddy(
    conversation: string,
    body: wire.BuddyHatchRequest,
    signal?: AbortSignal,
  ) {
    return wire.reviewBuddy(
      this.base,
      this.session(),
      conversation,
      body,
      signal,
    );
  }
  buddyReceipt(conversation: string, command: string, signal?: AbortSignal) {
    return wire.getBuddyReceipt(
      this.base,
      this.session(),
      conversation,
      command,
      signal,
    );
  }
  executeBuddy(
    conversation: string,
    command: wire.Command,
    signal?: AbortSignal,
  ) {
    return wire.sendBuddy(
      this.base,
      this.session(),
      conversation,
      command,
      signal,
    );
  }
  subscriptionProbes(signal?: AbortSignal) {
    return wire.getSubscriptionProbes(this.base, this.session(), signal);
  }
  reviewSubscriptionProbe(
    body: wire.SubscriptionProbeRequest,
    signal?: AbortSignal,
  ) {
    return wire.reviewSubscriptionProbe(
      this.base,
      this.session(),
      body,
      signal,
    );
  }
  applySubscriptionProbe(command: wire.Command, signal?: AbortSignal) {
    const proof = this.session();
    this.subscriptionProof = proof;
    return wire.sendSubscriptionProbe(this.base, proof, command, signal);
  }
  subscriptionProbeStatus(command: string, signal?: AbortSignal) {
    return wire.getSubscriptionProbeStatus(
      this.base,
      this.session(),
      command,
      signal,
    );
  }
  cancelSubscriptionProbe(command: string, signal?: AbortSignal) {
    return wire.cancelSubscriptionProbe(
      this.base,
      this.session(),
      command,
      signal,
    );
  }
  subscriptionProbeReceipt(command: string, signal?: AbortSignal) {
    return wire.getSubscriptionProbeReceipt(
      this.base,
      this.session(),
      command,
      signal,
    );
  }
  subscriptionOptions(signal?: AbortSignal) {
    return wire.getSubscriptionOptions(this.base, this.session(), signal);
  }
  reviewSubscriptionOptions(
    body: wire.SubscriptionOptionsRequest,
    signal?: AbortSignal,
  ) {
    return wire.reviewSubscriptionOptions(
      this.base,
      this.session(),
      body,
      signal,
    );
  }
  subscriptionOptionsReceipt(command: string, signal?: AbortSignal) {
    return wire.getSubscriptionOptionsReceipt(
      this.base,
      this.session(),
      command,
      signal,
    );
  }
  applySubscriptionOptions(command: wire.Command, signal?: AbortSignal) {
    return wire.sendSubscriptionOptions(
      this.base,
      this.session(),
      command,
      signal,
    );
  }
  cancelSubscriptionStart(command: string, signal?: AbortSignal) {
    return wire.cancelSubscriptionStart(
      this.base,
      this.session(),
      command,
      signal,
    );
  }
  reviewSubscriptionAction(
    body: wire.SubscriptionActionRequest,
    signal?: AbortSignal,
  ) {
    return wire.reviewSubscriptionAction(
      this.base,
      this.session(),
      body,
      signal,
    );
  }
  subscriptionFlow(flow: string, epoch: string, signal?: AbortSignal) {
    return wire.getSubscriptionFlow(
      this.base,
      this.session(),
      flow,
      epoch,
      signal,
    );
  }
  subscriptionReceipt(provider: string, command: string, signal?: AbortSignal) {
    return wire.getSubscriptionReceipt(
      this.base,
      this.session(),
      provider,
      command,
      signal,
    );
  }
  subscriptionAction(command: wire.Command, signal?: AbortSignal) {
    const proof = this.session();
    if (command.type === 'provider.subscription.start')
      this.subscriptionProof = proof;
    return wire.sendSubscriptionAction(this.base, proof, command, signal);
  }
  revokeSubscriptionFlows(signal?: AbortSignal) {
    return wire.revokeSubscriptionFlows(this.base, this.session(), signal);
  }
  mcpConfiguration(query: string, cursor?: string, signal?: AbortSignal) {
    return wire.getMcpConfiguration(
      this.base,
      this.session(),
      query,
      cursor,
      signal,
    );
  }
  mcpRuntime(server: string, signal?: AbortSignal) {
    return wire.getMcpRuntime(this.base, this.session(), server, signal);
  }
  reviewMcpRuntime(body: wire.McpRuntimeReviewRequest, signal?: AbortSignal) {
    return wire.reviewMcpRuntime(this.base, this.session(), body, signal);
  }
  reviewMcpConfiguration(
    body: wire.McpConfigurationReviewRequest,
    signal?: AbortSignal,
  ) {
    return wire.reviewMcpConfiguration(this.base, this.session(), body, signal);
  }
  reviewDefaultModel(
    body: wire.DefaultModelReviewRequest,
    signal?: AbortSignal,
  ) {
    return wire.reviewDefaultModel(this.base, this.session(), body, signal);
  }
  defaultModelReceipt(command: string, signal?: AbortSignal) {
    return wire.getDefaultModelReceipt(
      this.base,
      this.session(),
      command,
      signal,
    );
  }
  providerSettings(provider: string, signal?: AbortSignal) {
    return wire.getProviderSettings(
      this.base,
      this.session(),
      provider,
      signal,
    );
  }
  providerConfiguration(query = '', cursor?: string, signal?: AbortSignal) {
    return wire.getProviderConfiguration(
      this.base,
      this.session(),
      query,
      cursor,
      signal,
    );
  }
  reviewProviderConfiguration(
    body: wire.ProviderConfigurationReviewRequest,
    signal?: AbortSignal,
  ) {
    return wire.reviewProviderConfiguration(
      this.base,
      this.session(),
      body,
      signal,
    );
  }
  providerConfigurationReceipt(command: string, signal?: AbortSignal) {
    return wire.getProviderConfigurationReceipt(
      this.base,
      this.session(),
      command,
      signal,
    );
  }
  reviewProviderSettings(
    provider: string,
    body: wire.ProviderSettingsReviewRequest,
    signal?: AbortSignal,
  ) {
    return wire.reviewProviderSettings(
      this.base,
      this.session(),
      provider,
      body,
      signal,
    );
  }
  providerSettingsReceipt(
    provider: string,
    command: string,
    signal?: AbortSignal,
  ) {
    return wire.getProviderSettingsReceipt(
      this.base,
      this.session(),
      provider,
      command,
      signal,
    );
  }
  artifactReviewDraft(
    conversation: string,
    binding: string,
    body: wire.ArtifactReviewDraftRequest,
    signal?: AbortSignal,
  ) {
    return wire.getArtifactReviewDraft(
      this.base,
      this.session(),
      conversation,
      binding,
      body,
      signal,
    );
  }
  reviewArtifactPreset(
    conversation: string,
    binding: string,
    body: wire.ArtifactPresetReviewRequest,
    signal?: AbortSignal,
  ) {
    return wire.reviewArtifactPreset(
      this.base,
      this.session(),
      conversation,
      binding,
      body,
      signal,
    );
  }
  async stageArtifactUpload(
    conversation: string,
    file: File,
    commandId: string,
    signal?: AbortSignal,
  ) {
    if (file.size < 1 || file.size > 26214400)
      throw { code: 'payload_too_large' };
    const proof = this.session();
    signal?.throwIfAborted();
    const digest = await crypto.subtle.digest(
      'SHA-256',
      await file.arrayBuffer(),
    );
    const sha256 = [...new Uint8Array(digest)]
      .map((byte) => byte.toString(16).padStart(2, '0'))
      .join('');
    const upload = await wire.beginUpload(
      this.base,
      proof,
      {
        conversation_id: conversation,
        name: file.name,
        size_bytes: file.size,
        sha256,
        batch_id: commandId,
      },
      signal,
    );
    try {
      for (let offset = 0; offset < file.size; offset += 1048576)
        await wire.uploadChunk(
          this.base,
          proof,
          upload.upload_id,
          offset,
          file.slice(offset, offset + 1048576),
          signal,
        );
      signal?.throwIfAborted();
      return { upload_id: upload.upload_id, sha256, size_bytes: file.size };
    } catch (error) {
      await wire
        .cancelUpload(this.base, proof, upload.upload_id)
        .catch(() => undefined);
      throw error;
    }
  }
  startTalk(conversation: string, body: wire.TalkStart, signal?: AbortSignal) {
    return wire.startTalk(
      this.base,
      this.session(),
      conversation,
      body,
      signal,
    );
  }
  startRealtime(
    conversation: string,
    body: wire.TalkStart,
    signal?: AbortSignal,
  ) {
    return wire.startRealtime(
      this.base,
      this.session(),
      conversation,
      body,
      signal,
    );
  }
  voiceControl<M extends 'talk' | 'realtime'>(
    mode: M,
    handle: wire.DictationHandle,
    action: 'stop' | 'heartbeat',
    signal?: AbortSignal,
  ) {
    return wire.voiceControl(
      this.base,
      this.session(),
      mode,
      handle,
      action,
      signal,
    );
  }
  transcribeTalk(
    handle: wire.DictationHandle,
    utterance: string,
    audio: Blob,
    signal?: AbortSignal,
  ) {
    return wire.transcribeTalk(
      this.base,
      this.session(),
      handle,
      utterance,
      audio,
      signal,
    );
  }
  talkOutput(
    handle: wire.DictationHandle,
    run: string,
    output: string,
    signal?: AbortSignal,
  ) {
    return wire.talkOutput(
      this.base,
      this.session(),
      handle,
      run,
      output,
      signal,
    );
  }
  realtimeEvent(
    handle: wire.DictationHandle,
    event: wire.RealtimeEvent,
    signal?: AbortSignal,
  ) {
    return wire.realtimeEvent(this.base, this.session(), handle, event, signal);
  }
  realtimeExchange(
    handle: wire.DictationHandle,
    sdp: string,
    signal?: AbortSignal,
  ) {
    return wire.realtimeExchange(
      this.base,
      this.session(),
      handle,
      sdp,
      signal,
    );
  }
  voiceRun(
    mode: 'talk' | 'realtime',
    handle: wire.DictationHandle,
    signal?: AbortSignal,
  ) {
    return wire.voiceRun(this.base, this.session(), mode, handle, signal);
  }
  startDictation(
    conversation: string,
    requestId: string,
    signal?: AbortSignal,
  ) {
    return wire.startDictation(
      this.base,
      this.session(),
      conversation,
      requestId,
      signal,
    );
  }
  transcribeDictation(
    handle: wire.DictationHandle,
    utterance: string,
    audio: Blob,
    signal?: AbortSignal,
  ) {
    return wire.transcribeDictation(
      this.base,
      this.session(),
      handle,
      utterance,
      audio,
      signal,
    );
  }
  stopDictation(handle: wire.DictationHandle, signal?: AbortSignal) {
    return wire.stopDictation(this.base, this.session(), handle, signal);
  }
  messageText(
    conversation: string,
    message: string,
    cursor?: string,
    signal?: AbortSignal,
  ) {
    return wire.getMessageText(
      this.base,
      this.session(),
      conversation,
      message,
      cursor,
      signal,
    );
  }
  getConversation(id: string, signal?: AbortSignal) {
    return wire.getConversation(this.base, this.session(), id, signal);
  }
  openConversation(id: string, signal?: AbortSignal) {
    return wire.openConversation(this.base, this.session(), id, signal);
  }
  search(
    text: string,
    conversation?: string,
    cursor?: string,
    signal?: AbortSignal,
  ) {
    return wire.searchLibrary(
      this.base,
      this.session(),
      text,
      conversation,
      cursor,
      signal,
    );
  }
  history(
    conversation: string,
    message?: string,
    cursor?: string,
    signal?: AbortSignal,
  ) {
    return wire.getHistory(
      this.base,
      this.session(),
      conversation,
      message,
      cursor,
      signal,
    );
  }
  workspace(conversation: string, signal?: AbortSignal) {
    return wire.getWorkspace(this.base, this.session(), conversation, signal);
  }
  composer(
    conversation: string,
    body: wire.ConversationComposerQuery,
    signal?: AbortSignal,
  ) {
    return wire.queryComposer(
      this.base,
      this.session(),
      conversation,
      body,
      signal,
    );
  }
  composerCommand(
    conversation: string,
    body: wire.SlashCommandRead,
    signal?: AbortSignal,
  ) {
    return wire.readComposerCommand(
      this.base,
      this.session(),
      conversation,
      body,
      signal,
    );
  }
  draft(conversation: string, signal?: AbortSignal) {
    return wire.getDraft(this.base, this.session(), conversation, signal);
  }
  delegatedActivity(
    conversation: string,
    cursor?: string,
    signal?: AbortSignal,
  ) {
    return wire.getDelegatedActivity(
      this.base,
      this.session(),
      conversation,
      cursor,
      signal,
    );
  }
  delegatedRun(conversation: string, run: string, signal?: AbortSignal) {
    return wire.getDelegatedRun(
      this.base,
      this.session(),
      conversation,
      run,
      signal,
    );
  }
  queue(
    conversation: string,
    generation?: string,
    cursor?: string,
    signal?: AbortSignal,
  ) {
    return wire.getQueue(
      this.base,
      this.session(),
      conversation,
      generation,
      cursor,
      signal,
    );
  }
  steering(
    conversation: string,
    generation?: string,
    cursor?: string,
    signal?: AbortSignal,
  ) {
    return wire.getSteering(
      this.base,
      this.session(),
      conversation,
      generation,
      cursor,
      signal,
    );
  }
  saveDraft(conversation: string, body: wire.DraftSave, signal?: AbortSignal) {
    return wire.saveDraft(
      this.base,
      this.session(),
      conversation,
      body,
      signal,
    );
  }
  library(
    kind: 'artifact' | 'workspace',
    cursor?: string,
    signal?: AbortSignal,
  ) {
    return wire.getResourceLibrary(
      this.base,
      this.session(),
      kind,
      cursor,
      signal,
    );
  }
  deckSetup(signal?: AbortSignal) {
    return wire.getDeckSetup(this.base, this.session(), signal);
  }
  providerStatus(signal?: AbortSignal) {
    return wire.getProviderStatus(this.base, this.session(), signal);
  }
  liveProviderStatus(signal?: AbortSignal) {
    return wire.getLiveProviderStatus(this.base, this.session(), signal);
  }
  refreshLiveProvider(provider: string, signal?: AbortSignal) {
    return wire.refreshLiveProvider(
      this.base,
      this.session(),
      provider,
      signal,
    );
  }
  liveProviderRefresh(signal?: AbortSignal) {
    return wire.getLiveProviderRefresh(this.base, this.session(), signal);
  }
  testLiveProviderRuntime(provider: string, signal?: AbortSignal) {
    return wire.testLiveProviderRuntime(
      this.base,
      this.session(),
      provider,
      signal,
    );
  }
  savedEntities(
    query = '',
    entityType?: string,
    cursor?: string,
    signal?: AbortSignal,
  ) {
    return wire.getSavedEntities(
      this.base,
      this.session(),
      query,
      entityType,
      undefined,
      undefined,
      undefined,
      25,
      cursor,
      signal,
    );
  }
  knowledgeEntities(
    query = '',
    entityType?: string,
    status?: string,
    source?: string,
    tier?: string,
    cursor?: string,
    signal?: AbortSignal,
  ) {
    return wire.getSavedEntities(
      this.base,
      this.session(),
      query,
      entityType,
      status,
      source,
      tier,
      25,
      cursor,
      signal,
    );
  }
  savedDocuments(
    query = '',
    status?: string,
    cursor?: string,
    signal?: AbortSignal,
  ) {
    return wire.getSavedDocuments(
      this.base,
      this.session(),
      query,
      status,
      cursor,
      signal,
    );
  }
  cachedTools(
    source?: wire.ToolCatalogPage['items'][number]['source'],
    query = '',
    cursor?: string,
    signal?: AbortSignal,
  ) {
    return wire.getCachedTools(
      this.base,
      this.session(),
      source,
      query,
      cursor,
      signal,
    );
  }
  settingsSnapshot(signal?: AbortSignal) {
    return wire.getSettingsSnapshot(this.base, this.session(), signal);
  }
  reviewSettingsMutation(
    body: wire.SettingsMutationRequest,
    signal?: AbortSignal,
  ) {
    return wire.reviewSettingsMutation(this.base, this.session(), body, signal);
  }
  settingsMutationReceipt(command: string, signal?: AbortSignal) {
    return wire.getSettingsMutationReceipt(
      this.base,
      this.session(),
      command,
      signal,
    );
  }
  executeSettingsMutation(
    command: wire.SettingsMutationCommand,
    signal?: AbortSignal,
  ) {
    return wire.sendSettingsMutation(
      this.base,
      this.session(),
      command,
      signal,
    );
  }
  savedTasks(
    query = '',
    enabled?: boolean,
    cursor?: string,
    signal?: AbortSignal,
  ) {
    return wire.getSavedTasks(
      this.base,
      this.session(),
      query,
      enabled,
      cursor,
      signal,
    );
  }
  taskDeliveryDefaults(signal?: AbortSignal) {
    return wire.getTaskDeliveryDefaults(this.base, this.session(), signal);
  }
  taskEditor(task: string, signal?: AbortSignal) {
    return wire.getTaskEditor(this.base, this.session(), task, signal);
  }
  taskGraph(task: string, signal?: AbortSignal) {
    return wire.getTaskGraph(this.base, this.session(), task, signal);
  }
  taskSettings(task: string, signal?: AbortSignal) {
    return wire.getTaskSettings(this.base, this.session(), task, signal);
  }
  workspaceProcesses(
    conversation: string,
    binding: string,
    signal?: AbortSignal,
  ) {
    return wire.getWorkspaceProcesses(
      this.base,
      this.session(),
      conversation,
      binding,
      signal,
    );
  }
  workspaceProcessRecovery(
    conversation: string,
    binding: string,
    cursor?: string,
    signal?: AbortSignal,
  ) {
    return wire.getWorkspaceProcessRecovery(
      this.base,
      this.session(),
      conversation,
      binding,
      cursor,
      signal,
    );
  }
  workspaceProcessOutput(
    conversation: string,
    binding: string,
    process: string,
    cursor: number,
    signal?: AbortSignal,
  ) {
    return wire.getWorkspaceProcessOutput(
      this.base,
      this.session(),
      conversation,
      binding,
      process,
      cursor,
      signal,
    );
  }
  reviewWorkspaceProcess(
    conversation: string,
    binding: string,
    body: wire.WorkspaceProcessReviewRequest,
    signal?: AbortSignal,
  ) {
    return wire.reviewWorkspaceProcess(
      this.base,
      this.session(),
      conversation,
      binding,
      body,
      signal,
    );
  }
  reviewTaskSettings(
    task: string,
    fields: wire.TaskSettingsFields,
    signal?: AbortSignal,
  ) {
    return wire.reviewTaskSettings(
      this.base,
      this.session(),
      task,
      fields,
      signal,
    );
  }
  downloadTaskWebhook(task: string, revision: string, signal?: AbortSignal) {
    return wire.downloadTaskWebhook(
      this.base,
      this.session(),
      task,
      revision,
      signal,
    );
  }
  workspaceImports(
    conversation: string,
    binding: string,
    cursor?: string,
    signal?: AbortSignal,
  ) {
    return wire.getWorkspaceImports(
      this.base,
      this.session(),
      conversation,
      binding,
      cursor,
      signal,
    );
  }
  developerRepository(
    conversation: string,
    binding: string,
    signal?: AbortSignal,
  ) {
    return wire.getDeveloperRepository(
      this.base,
      this.session(),
      conversation,
      binding,
      signal,
    );
  }
  reviewDeveloperRepository(
    conversation: string,
    binding: string,
    body: wire.DeveloperRepositoryReviewRequest,
    signal?: AbortSignal,
  ) {
    return wire.reviewDeveloperRepository(
      this.base,
      this.session(),
      conversation,
      binding,
      body,
      signal,
    );
  }
  developerRepositoryReceipt(
    conversation: string,
    binding: string,
    command: string,
    signal?: AbortSignal,
  ) {
    return wire.getDeveloperRepositoryReceipt(
      this.base,
      this.session(),
      conversation,
      binding,
      command,
      signal,
    );
  }
  executeDeveloperRepository(
    conversation: string,
    binding: string,
    command: wire.Command,
    signal?: AbortSignal,
  ) {
    return wire.sendDeveloperRepository(
      this.base,
      this.session(),
      conversation,
      binding,
      command,
      signal,
    );
  }
  workspaceImportPatch(
    conversation: string,
    binding: string,
    pending: string,
    revision: string,
    offset: number,
    signal?: AbortSignal,
  ) {
    return wire.getWorkspaceImportPatch(
      this.base,
      this.session(),
      conversation,
      binding,
      pending,
      revision,
      offset,
      signal,
    );
  }
  reviewWorkspaceImport(
    conversation: string,
    binding: string,
    pending: string,
    signal?: AbortSignal,
  ) {
    return wire.reviewWorkspaceImport(
      this.base,
      this.session(),
      conversation,
      binding,
      pending,
      signal,
    );
  }
  workspaceImportReceipt(
    conversation: string,
    binding: string,
    command: string,
    signal?: AbortSignal,
  ) {
    return wire.getWorkspaceImportReceipt(
      this.base,
      this.session(),
      conversation,
      binding,
      command,
      signal,
    );
  }
  reviewWorkspaceImportRecovery(
    conversation: string,
    binding: string,
    command: string,
    signal?: AbortSignal,
  ) {
    return wire.reviewWorkspaceImportRecovery(
      this.base,
      this.session(),
      conversation,
      binding,
      command,
      signal,
    );
  }
  executeWorkspaceImport(
    conversation: string,
    binding: string,
    command: wire.Command,
    signal?: AbortSignal,
  ) {
    return wire.sendWorkspaceImport(
      this.base,
      this.session(),
      conversation,
      binding,
      command,
      signal,
    );
  }
  reviewWorkspaceUndo(
    conversation: string,
    binding: string,
    changeSet: string,
    signal?: AbortSignal,
  ) {
    return wire.reviewWorkspaceUndo(
      this.base,
      this.session(),
      conversation,
      binding,
      changeSet,
      signal,
    );
  }
  workspaceUndoReceipt(
    conversation: string,
    binding: string,
    command: string,
    signal?: AbortSignal,
  ) {
    return wire.getWorkspaceUndoReceipt(
      this.base,
      this.session(),
      conversation,
      binding,
      command,
      signal,
    );
  }
  reviewWorkspaceUndoRecovery(
    conversation: string,
    binding: string,
    command: string,
    signal?: AbortSignal,
  ) {
    return wire.reviewWorkspaceUndoRecovery(
      this.base,
      this.session(),
      conversation,
      binding,
      command,
      signal,
    );
  }
  executeWorkspaceUndo(
    conversation: string,
    binding: string,
    command: wire.Command,
    signal?: AbortSignal,
  ) {
    return wire.sendWorkspaceUndo(
      this.base,
      this.session(),
      conversation,
      binding,
      command,
      signal,
    );
  }
  workspaceEditableFile(
    conversation: string,
    binding: string,
    path: string,
    signal?: AbortSignal,
  ) {
    return wire.getWorkspaceEditableFile(
      this.base,
      this.session(),
      conversation,
      binding,
      path,
      signal,
    );
  }
  taskRunReview(task: string, signal?: AbortSignal) {
    return wire.getTaskRunReview(this.base, this.session(), task, signal);
  }
  taskRuns(task: string, cursor?: string, signal?: AbortSignal) {
    return wire.getTaskRuns(this.base, this.session(), task, cursor, signal);
  }
  taskRun(task: string, run: string, signal?: AbortSignal) {
    return wire.getTaskRun(this.base, this.session(), task, run, signal);
  }
  taskApprovals(
    task: string,
    run: string,
    cursor?: string,
    signal?: AbortSignal,
  ) {
    return wire.getTaskApprovals(
      this.base,
      this.session(),
      task,
      run,
      cursor,
      signal,
    );
  }
  artifactExport(
    conversation: string,
    binding: string,
    exportId: string,
    signal?: AbortSignal,
  ) {
    return wire.getArtifactExport(
      this.base,
      this.session(),
      conversation,
      binding,
      exportId,
      signal,
    );
  }
  artifactDownload(
    conversation: string,
    binding: string,
    descriptor: wire.ArtifactExport,
    signal?: AbortSignal,
  ) {
    return wire.downloadArtifactExport(
      this.base,
      this.session(),
      conversation,
      binding,
      descriptor,
      signal,
    );
  }
  cachedModels(
    providerId?: string,
    query = '',
    cursor?: string,
    signal?: AbortSignal,
  ) {
    return wire.getCachedModels(
      this.base,
      this.session(),
      providerId,
      query,
      cursor,
      signal,
    );
  }
  modelsSettings(signal?: AbortSignal) {
    return wire.getModelsSettings(this.base, this.session(), signal);
  }
  updateModelSurface(body: wire.ModelSurfaceMutation, signal?: AbortSignal) {
    return wire.updateModelSurface(this.base, this.session(), body, signal);
  }
  updateModelContext(body: wire.ModelContextMutation, signal?: AbortSignal) {
    return wire.updateModelContext(this.base, this.session(), body, signal);
  }
  agentRuntimeSettings(signal?: AbortSignal) {
    return wire.getAgentRuntimeSettings(this.base, this.session(), signal);
  }
  saveAgentRuntimeSettings(
    body: wire.AgentRuntimeSettingsState,
    signal?: AbortSignal,
  ) {
    return wire.saveAgentRuntimeSettings(
      this.base,
      this.session(),
      body,
      signal,
    );
  }
  resetAgentRuntimeSettings(signal?: AbortSignal) {
    return wire.resetAgentRuntimeSettings(this.base, this.session(), signal);
  }
  modelCatalogSummary(surface: string, signal?: AbortSignal) {
    return wire.getModelCatalogSummary(
      this.base,
      this.session(),
      surface,
      signal,
    );
  }
  modelCatalogPage(
    surface: string,
    providerId?: string,
    query = '',
    cursor?: string,
    signal?: AbortSignal,
  ) {
    return wire.getModelCatalogPage(
      this.base,
      this.session(),
      surface,
      providerId,
      query,
      cursor,
      signal,
    );
  }
  refreshModelsCatalog(signal?: AbortSignal) {
    return wire.refreshModelsCatalog(this.base, this.session(), signal);
  }
  refreshModelCameras(signal?: AbortSignal) {
    return wire.refreshModelCameras(this.base, this.session(), signal);
  }
  artifactSetup(
    mode: NonNullable<wire.ArtifactSetupOptions['mode']>,
    signal?: AbortSignal,
  ) {
    return wire.getArtifactSetup(this.base, mode, this.session(), signal);
  }
  pickFolder(signal?: AbortSignal) {
    return wire.pickFolder(this.base, this.session(), signal);
  }
  prepareArtifactShare(
    conversation: string,
    binding: string,
    options: wire.ArtifactShareOptions,
    signal?: AbortSignal,
  ) {
    return wire.prepareArtifactShare(
      this.base,
      this.session(),
      conversation,
      binding,
      options,
      signal,
    );
  }
  artifactShareChannels(cursor?: string, signal?: AbortSignal) {
    return wire.getArtifactShareChannels(
      this.base,
      this.session(),
      cursor,
      signal,
    );
  }
  artifactPreview(
    conversation: string,
    binding: string,
    pageId?: string,
    knownRevision?: string,
    signal?: AbortSignal,
    authoring?: wire.ArtifactAuthoring,
  ) {
    return wire.getArtifactPreview(
      this.base,
      this.session(),
      conversation,
      binding,
      pageId,
      knownRevision,
      signal,
      authoring,
    );
  }
  artifactLifecycle(
    conversation: string,
    binding: string,
    expectedRevision: string,
    signal?: AbortSignal,
  ) {
    return wire.getArtifactLifecycle(
      this.base,
      this.session(),
      conversation,
      binding,
      expectedRevision,
      signal,
    );
  }
  artifactStaticPreview(
    conversation: string,
    binding: string,
    pageId: string,
    signal?: AbortSignal,
  ) {
    return wire.getArtifactStaticPreview(
      this.base,
      this.session(),
      conversation,
      binding,
      pageId,
      signal,
    );
  }
  designControls(
    conversation: string,
    binding: string,
    options: wire.DesignControlOptions,
    signal?: AbortSignal,
  ) {
    return wire.getDesignControls(
      this.base,
      this.session(),
      conversation,
      binding,
      options,
      signal,
    );
  }
  designReview(
    conversation: string,
    binding: string,
    options: wire.DesignReviewOptions,
    signal?: AbortSignal,
  ) {
    return wire.getDesignReview(
      this.base,
      this.session(),
      conversation,
      binding,
      options,
      signal,
    );
  }
  designPresentation(
    conversation: string,
    binding: string,
    options: wire.DesignPresentationOptions,
    signal?: AbortSignal,
  ) {
    return wire.getDesignPresentation(
      this.base,
      this.session(),
      conversation,
      binding,
      options,
      signal,
    );
  }
  artifactEditing(
    conversation: string,
    binding: string,
    pageId?: string,
    pageCursor?: string,
    elementCursor?: string,
    historyCursor?: string,
    elementId?: string,
    limit = 25,
    signal?: AbortSignal,
  ) {
    return wire.getArtifactEditing(
      this.base,
      this.session(),
      conversation,
      binding,
      pageId,
      pageCursor,
      elementCursor,
      historyCursor,
      elementId,
      limit,
      signal,
    );
  }
  inspector(
    conversation: string,
    binding: string,
    refresh = false,
    signal?: AbortSignal,
  ) {
    return wire.getWorkspaceInspector(
      this.base,
      this.session(),
      conversation,
      binding,
      refresh,
      signal,
    );
  }
  changes(
    conversation: string,
    binding: string,
    revision?: string,
    cursor?: string,
    signal?: AbortSignal,
  ) {
    return wire.getWorkspaceChanges(
      this.base,
      this.session(),
      conversation,
      binding,
      revision,
      cursor,
      signal,
    );
  }
  directory(
    conversation: string,
    binding: string,
    directory = '',
    cursor?: string,
    revision?: string,
    signal?: AbortSignal,
  ) {
    return wire.getWorkspaceDirectory(
      this.base,
      this.session(),
      conversation,
      binding,
      directory,
      cursor,
      revision,
      signal,
    );
  }
  file(
    conversation: string,
    binding: string,
    path: string,
    offset = 0,
    revision?: string,
    signal?: AbortSignal,
  ) {
    return wire.getWorkspaceFile(
      this.base,
      this.session(),
      conversation,
      binding,
      path,
      offset,
      revision,
      signal,
    );
  }
  approval(identity: string, signal?: AbortSignal) {
    return wire.getApproval(this.base, this.session(), identity, signal);
  }
  diff(
    conversation: string,
    binding: string,
    path: string,
    snapshot: string,
    offset = 0,
    revision?: string,
    signal?: AbortSignal,
  ) {
    return wire.getWorkspaceDiff(
      this.base,
      this.session(),
      conversation,
      binding,
      path,
      snapshot,
      offset,
      revision,
      signal,
    );
  }
  changeSets(
    conversation: string,
    binding: string,
    revision: string,
    cursor?: string,
    signal?: AbortSignal,
  ) {
    return wire.getWorkspaceChangeSets(
      this.base,
      this.session(),
      conversation,
      binding,
      revision,
      cursor,
      signal,
    );
  }
  changeSetFiles(
    conversation: string,
    binding: string,
    change: string,
    revision: string,
    cursor?: string,
    signal?: AbortSignal,
  ) {
    return wire.getWorkspaceChangeSetFiles(
      this.base,
      this.session(),
      conversation,
      binding,
      change,
      revision,
      cursor,
      signal,
    );
  }
  content(
    conversation: string,
    message: string,
    cursor?: string,
    signal?: AbortSignal,
  ) {
    return wire.getLazyContent(
      this.base,
      this.session(),
      conversation,
      message,
      65536,
      cursor,
      signal,
    );
  }
  getTranscript(id: string, cursor?: string, signal?: AbortSignal) {
    return wire.getTranscript(
      this.base,
      this.session(),
      id,
      100,
      cursor,
      signal,
    );
  }
  subscribe(id: string, signal?: AbortSignal) {
    return wire.subscribe(this.base, this.session(), id, signal);
  }
  observe(subscription: string, cursor: string, signal: AbortSignal) {
    return wire.observeEvents(
      this.base,
      this.session(),
      subscription,
      cursor,
      signal,
    );
  }
  poll(subscription: string, cursor: string, signal?: AbortSignal) {
    return wire.poll(this.base, this.session(), subscription, cursor, signal);
  }
  acknowledge(subscription: string, cursor: string, signal?: AbortSignal) {
    return wire.acknowledge(
      this.base,
      this.session(),
      subscription,
      cursor,
      signal,
    );
  }
  unsubscribe(subscription: string, signal?: AbortSignal, keepalive = false) {
    return wire.unsubscribe(
      this.base,
      this.session(),
      subscription,
      signal,
      keepalive,
    );
  }
  command(
    target: string | null,
    command: wire.Command,
    key: string,
    signal?: AbortSignal,
  ) {
    return wire.sendConversationCommand(
      this.base,
      target,
      command,
      this.session(),
      key,
      signal,
    );
  }
  receipt(id: string, signal?: AbortSignal) {
    return wire.getReceipt(this.base, this.session(), id, signal);
  }
  clearSession(preserveResumeIdentity = false) {
    if (!preserveResumeIdentity && this.subscriptionProof) {
      const proof = this.subscriptionProof;
      this.subscriptionProof = undefined;
      // The captured old proof can only cancel that exact account-flow owner.
      // Provider work remains admitted on the host until cancellation drains.
      void wire.revokeSubscriptionFlows(this.base, proof).catch(() => {});
    }
    this.resumeSessionId = preserveResumeIdentity
      ? (this.proof?.client_session_id ?? this.resumeSessionId)
      : undefined;
    this.proof = undefined;
  }

  async upload(
    conversation: string,
    file: File,
    signal?: AbortSignal,
  ): Promise<wire.AttachmentView> {
    if (file.size < 1 || file.size > 26214400)
      throw { code: 'payload_too_large' };
    signal?.throwIfAborted();
    const hash = await crypto.subtle.digest(
      'SHA-256',
      await file.arrayBuffer(),
    );
    const sha256 = [...new Uint8Array(hash)]
      .map((byte) => byte.toString(16).padStart(2, '0'))
      .join('');
    const session = await wire.beginUpload(
      this.base,
      this.session(),
      {
        conversation_id: conversation,
        name: file.name,
        size_bytes: file.size,
        sha256,
        batch_id: crypto.randomUUID(),
      },
      signal,
    );
    try {
      for (let offset = 0; offset < file.size; offset += 1048576) {
        await wire.uploadChunk(
          this.base,
          this.session(),
          session.upload_id,
          offset,
          file.slice(offset, offset + 1048576),
          signal,
        );
      }
      return await wire.completeUpload(
        this.base,
        this.session(),
        session.upload_id,
        { command_id: crypto.randomUUID() },
        crypto.randomUUID(),
        signal,
      );
    } catch (error) {
      // Cancel only the staging session created by this call; never replay completion.
      await wire
        .cancelUpload(this.base, this.session(), session.upload_id)
        .catch(() => undefined);
      throw error;
    }
  }
  download(reference: string, signal?: AbortSignal) {
    return wire.readAttachment(this.base, this.session(), reference, signal);
  }
  attachmentMetadata(reference: string, signal?: AbortSignal) {
    return wire.getAttachmentMetadata(
      this.base,
      this.session(),
      reference,
      signal,
    );
  }
  terminalRead(terminal: string, cursor: number, signal?: AbortSignal) {
    return wire.readNativeTerminal(
      this.base,
      this.session(),
      terminal,
      cursor,
      65536,
      signal,
    );
  }
  terminalInput(terminal: string, data: string, signal?: AbortSignal) {
    return wire.writeNativeTerminal(
      this.base,
      this.session(),
      terminal,
      { data },
      signal,
    );
  }
  terminalResize(
    terminal: string,
    cols: number,
    rows: number,
    signal?: AbortSignal,
  ) {
    return wire.resizeNativeTerminal(
      this.base,
      this.session(),
      terminal,
      { cols, rows },
      signal,
    );
  }
  terminalDisconnect(terminal: string, signal?: AbortSignal) {
    return wire.disconnectNativeTerminal(
      this.base,
      this.session(),
      terminal,
      signal,
    );
  }
}
