import type { createBuddySessions } from './features/buddy/buddy-sessions';
import type { KnowledgeSessions } from './features/knowledge/knowledge-sessions';
import { createContext, useContext, useSyncExternalStore } from 'react';
import type { ClientController } from './api';
import type { ClientPlatform } from './platform';
import type { createWorkspaceEditSessions } from './features/panels/workspace-edit-sessions';
import type { createWorkspaceProcessSessions } from './features/panels/workspace-process-sessions';
import type { createWorkspaceImportSessions } from './features/panels/workspace-import-sessions';
import type { createWorkspaceUndoSessions } from './features/panels/workspace-undo-sessions';
import type { createTaskEditSessions } from './features/tasks/task-edit-sessions';
import type { createProviderSettingsSessions } from './features/settings/provider-settings-sessions';
import type { createControllerDesignSessions } from './features/panels/artifact-design-controller';
import type { createProviderConfigurationOwner } from './features/settings/provider-configuration-owner';
import type { createAuthenticatedEditorOwner } from './features/settings/authenticated-editor-owner';
import type { DefaultModelSession } from './features/settings/DefaultModelSettings';
import type { CapabilitySettingsSession } from './features/settings/CapabilitySettings';
import type { SubscriptionAccountsSession } from './features/settings/SubscriptionAccounts';
import type { SubscriptionProbesSession } from './features/settings/SubscriptionProbes';
import type { SubscriptionOptionsSession } from './features/settings/SubscriptionOptions';
import type { McpConnections } from './features/settings/McpConnections';
import type { createRuntimeInstallations } from './features/mcp/RuntimeInstallations';
import type { DocumentRemovals } from './features/knowledge/DocumentRemovals';
import type { DocumentQueueOwner } from './features/knowledge/DocumentQueuePanel';
import type { DocumentUploadOwner } from './features/knowledge/DocumentUploadPanel';
import type { DocumentProcessingOwner } from './features/knowledge/DocumentProcessingPanel';
import type { WikiSettingsSession } from './features/knowledge/WikiSettings';
import type { ChannelSettingsSession } from './features/settings/ChannelSettings';
import type { PluginSettingsSession } from './features/settings/PluginSettings';
import type { SkillsSettingsSession } from './features/settings/SkillsSettings';
import type { GoalProfileSettingsSession } from './features/settings/GoalProfileSettings';
import type { ConversationActionsSession } from './features/settings/ConversationActions';
import type { BrowserControlSession } from './features/browser/BrowserLiveControls';

type ConversationActionSessions = {
  get(conversationId: string): ConversationActionsSession | undefined;
  hasRetained(): boolean;
  dispose(): void;
};
type BrowserControlSessions = {
  get(conversationId: string): BrowserControlSession | undefined;
  hasRetained(): boolean;
  dispose(): void;
};
export const RuntimeContext = createContext<{
  controller: ClientController;
  documentProcessingOwner?: ReturnType<
    typeof createAuthenticatedEditorOwner<DocumentProcessingOwner>
  >;
  wikiOwner?: ReturnType<
    typeof createAuthenticatedEditorOwner<WikiSettingsSession>
  >;
  channelOwner?: ReturnType<
    typeof createAuthenticatedEditorOwner<ChannelSettingsSession>
  >;
  pluginOwner?: ReturnType<
    typeof createAuthenticatedEditorOwner<PluginSettingsSession>
  >;
  skillsOwner?: ReturnType<
    typeof createAuthenticatedEditorOwner<SkillsSettingsSession>
  >;
  goalProfileOwner?: ReturnType<
    typeof createAuthenticatedEditorOwner<GoalProfileSettingsSession>
  >;
  conversationActionsOwner?: ReturnType<
    typeof createAuthenticatedEditorOwner<ConversationActionSessions>
  >;
  browserControlOwner?: ReturnType<
    typeof createAuthenticatedEditorOwner<BrowserControlSessions>
  >;
  documentUploadOwner?: ReturnType<
    typeof createAuthenticatedEditorOwner<DocumentUploadOwner>
  >;
  knowledgeOwner?: ReturnType<
    typeof createAuthenticatedEditorOwner<KnowledgeSessions>
  >;
  platform: ClientPlatform;
  workspaceEditSessions?: ReturnType<typeof createWorkspaceEditSessions>;
  workspaceProcessSessions?: ReturnType<typeof createWorkspaceProcessSessions>;
  workspaceImportSessions?: ReturnType<typeof createWorkspaceImportSessions>;
  workspaceUndoSessions?: ReturnType<typeof createWorkspaceUndoSessions>;
  taskEditSessions?: ReturnType<typeof createTaskEditSessions>;
  providerSettingsSessions?: ReturnType<typeof createProviderSettingsSessions>;
  defaultModelOwner?: ReturnType<
    typeof createAuthenticatedEditorOwner<DefaultModelSession>
  >;
  capabilitySettingsOwner?: ReturnType<
    typeof createAuthenticatedEditorOwner<CapabilitySettingsSession>
  >;
  buddyOwner?: ReturnType<
    typeof createAuthenticatedEditorOwner<
      ReturnType<typeof createBuddySessions>
    >
  >;
  subscriptionAccountsOwner?: ReturnType<
    typeof createAuthenticatedEditorOwner<SubscriptionAccountsSession>
  >;
  subscriptionProbesOwner?: ReturnType<
    typeof createAuthenticatedEditorOwner<SubscriptionProbesSession>
  >;
  subscriptionOptionsOwner?: ReturnType<
    typeof createAuthenticatedEditorOwner<SubscriptionOptionsSession>
  >;
  mcpConnectionsOwner?: ReturnType<
    typeof createAuthenticatedEditorOwner<McpConnections>
  >;
  runtimeInstallationsOwner?: ReturnType<
    typeof createAuthenticatedEditorOwner<
      ReturnType<typeof createRuntimeInstallations>
    >
  >;
  documentRemovalsOwner?: ReturnType<
    typeof createAuthenticatedEditorOwner<DocumentRemovals>
  >;
  documentQueueOwner?: ReturnType<
    typeof createAuthenticatedEditorOwner<DocumentQueueOwner>
  >;
  providerConfigurationOwner?: ReturnType<
    typeof createProviderConfigurationOwner
  >;
  artifactDesignSessions?: ReturnType<typeof createControllerDesignSessions>;
} | null>(null);
export function useRuntime() {
  const runtime = useContext(RuntimeContext);
  if (!runtime) throw new Error('RuntimeContext is required');
  return runtime;
}
export function useClientState() {
  const { controller } = useRuntime();
  return useSyncExternalStore(controller.subscribe, controller.getSnapshot);
}

/** Select stable store fields so unrelated token updates do not wake panels. */
export function useClientSelector<T>(
  selector: (
    state: ReturnType<
      ReturnType<typeof useRuntime>['controller']['getSnapshot']
    >,
  ) => T,
): T {
  const { controller } = useRuntime();
  return useSyncExternalStore(controller.subscribe, () =>
    selector(controller.getSnapshot()),
  );
}
