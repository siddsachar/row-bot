import { createBuddySessions } from './features/buddy/buddy-sessions';
import { createKnowledgeSessions } from './features/knowledge/knowledge-sessions';
import { Component, lazy, Suspense, type ReactNode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { createClientController } from './api';
import { selectClientPlatform } from './platform';
import { RuntimeContext } from './runtime';
import { bindPageLifecycle } from './page-lifecycle';
import { PwaStatus } from './pwa';
import { ThemeProvider } from './ui/theme';
import { OverlayProvider } from './ui/overlays';
import { EmptyState, ErrorState, Skeleton } from './ui/primitives';
import Workspace, { panelMetrics } from './features/shell/Workspace';
import { resourcePanelMetrics } from './features/panels/ResourcePanel';
import { createWorkspaceEditSessions } from './features/panels/workspace-edit-sessions';
import { createWorkspaceProcessSessions } from './features/panels/workspace-process-sessions';
import { createWorkspaceImportSessions } from './features/panels/workspace-import-sessions';
import { createWorkspaceUndoSessions } from './features/panels/workspace-undo-sessions';
import { createTaskEditSessions } from './features/tasks/task-edit-sessions';
import {
  createProviderSettingsSessions,
  providerSettingsCallbacks,
} from './features/settings/provider-settings-sessions';
import { createControllerDesignSessions } from './features/panels/artifact-design-controller';
import { createProviderConfigurationOwner } from './features/settings/provider-configuration-owner';
import { createAuthenticatedEditorOwner } from './features/settings/authenticated-editor-owner';
import { DefaultModelSession } from './features/settings/DefaultModelSettings';
import { createCapabilitySettingsSession } from './features/settings/CapabilitySettings';
import { SubscriptionAccountsSession } from './features/settings/SubscriptionAccounts';
import { SubscriptionProbesSession } from './features/settings/SubscriptionProbes';
import { SubscriptionOptionsSession } from './features/settings/SubscriptionOptions';
import { createDocumentRemovals } from './features/knowledge/DocumentRemovals';
import { createMcpConnections } from './features/settings/McpConnections';
import { createRuntimeInstallations } from './features/mcp/RuntimeInstallations';
import { createDocumentQueueSession } from './features/knowledge/DocumentQueuePanel';
import { createDocumentUploadSession } from './features/knowledge/DocumentUploadPanel';
import { createDocumentProcessingSession } from './features/knowledge/DocumentProcessingPanel';
import { createWikiSettingsSession } from './features/knowledge/WikiSettings';
import { createChannelSettingsSession } from './features/settings/ChannelSettings';
import { createPluginSettingsSession } from './features/settings/PluginSettings';
import { createSkillsSettingsSession } from './features/settings/SkillsSettings';
import { createGoalProfileSettingsSession } from './features/settings/GoalProfileSettings';
import { createConversationActionsSession } from './features/settings/ConversationActions';
import { createBrowserControlSession } from './features/browser/BrowserLiveControls';
import {
  bindActiveConversationSession,
  browserSessionStorage,
} from './active-conversation-session';
import './ui/styles.css';

const Gallery = lazy(() => import('./features/shell/Gallery'));
const SettingRoute = lazy(() => import('./features/settings/SettingRoute'));
const SettingsIndex = lazy(() => import('./features/settings/SettingsIndex'));

function createConversationActionSessions() {
  const sessions = new Map<
    string,
    ReturnType<typeof createConversationActionsSession>
  >();
  let disposed = false;
  return {
    get(conversationId: string) {
      if (disposed || !conversationId) return undefined;
      const existing = sessions.get(conversationId);
      if (existing) {
        sessions.delete(conversationId);
        sessions.set(conversationId, existing);
        return existing;
      }
      if (sessions.size >= 8) {
        const disposable = [...sessions].find(
          ([, session]) => !session.hasRetained(),
        );
        if (!disposable) return undefined;
        disposable[1].dispose();
        sessions.delete(disposable[0]);
      }
      const session = createConversationActionsSession(conversationId);
      sessions.set(conversationId, session);
      return session;
    },
    hasRetained() {
      return (
        !disposed &&
        [...sessions.values()].some((session) => session.hasRetained())
      );
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      sessions.forEach((session) => session.dispose());
      sessions.clear();
    },
  };
}
function createBrowserControlSessions() {
  const sessions = new Map<
    string,
    ReturnType<typeof createBrowserControlSession>
  >();
  let disposed = false;
  return {
    get(conversationId: string) {
      if (disposed || !conversationId) return undefined;
      const existing = sessions.get(conversationId);
      if (existing) {
        sessions.delete(conversationId);
        sessions.set(conversationId, existing);
        return existing;
      }
      if (sessions.size >= 8) {
        const disposable = [...sessions].find(
          ([, session]) => !session.hasRetained(),
        );
        if (!disposable) return undefined;
        disposable[1].dispose();
        sessions.delete(disposable[0]);
      }
      const session = createBrowserControlSession(conversationId);
      sessions.set(conversationId, session);
      return session;
    },
    hasRetained() {
      return (
        !disposed &&
        [...sessions.values()].some((session) => session.hasRetained())
      );
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      sessions.forEach((session) => session.dispose());
      sessions.clear();
    },
  };
}
class RenderBoundary extends Component<
  { children: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    return this.state.failed ? (
      <main className="startup-error">
        <ErrorState
          title="The workspace could not open"
          action={
            <a className="button" href="/app-v2/">
              Reload workspace
            </a>
          }
        >
          Try reloading, or return to the <a href="/">current application</a>.
        </ErrorState>
      </main>
    ) : (
      this.props.children
    );
  }
}
async function start() {
  const query = new URLSearchParams(location.search).get('fixture');
  const fixture = [
    'normal',
    'incompatible',
    'unauthorized',
    'disconnected',
  ].includes(query ?? '')
    ? (query as 'normal' | 'incompatible' | 'unauthorized' | 'disconnected')
    : undefined;
  let fixtureTransport: unknown;
  const controller = await createClientController({
    fixture,
    onFixture: (transport) => {
      fixtureTransport = transport;
    },
  });
  const disposeActiveConversationSession = bindActiveConversationSession(
    controller,
    location.pathname.replace(/^\/app-v2(?:\/|$)/, '/') || '/',
    browserSessionStorage(),
  );
  await controller.start();
  let platform = await selectClientPlatform(
    controller,
    controller.getSnapshot().handshake,
  );
  const workspaceEditSessions = createWorkspaceEditSessions(controller, {
    capacity: 8,
  });
  const workspaceProcessSessions = createWorkspaceProcessSessions(controller);
  const workspaceImportSessions = createWorkspaceImportSessions(controller);
  const workspaceUndoSessions = createWorkspaceUndoSessions(controller);
  const taskEditSessions = createTaskEditSessions(controller, { capacity: 8 });
  const providerSettingsSessions = createProviderSettingsSessions(
    controller,
    () => providerSettingsCallbacks(controller),
    {
      capacity: 4,
      allowProviderId: (id) =>
        /^[a-z0-9_-]{1,64}$/.test(id) ||
        /^custom_openai_[a-z0-9][a-z0-9_-]{0,63}$/.test(id),
    },
  );
  const artifactDesignSessions = createControllerDesignSessions(controller);
  const providerConfigurationOwner =
    createProviderConfigurationOwner(controller);
  const defaultModelOwner = createAuthenticatedEditorOwner(
    controller,
    () => new DefaultModelSession(),
  );
  const capabilitySettingsOwner = createAuthenticatedEditorOwner(
    controller,
    createCapabilitySettingsSession,
  );
  const buddyOwner = createAuthenticatedEditorOwner(controller, () =>
    createBuddySessions(controller),
  );
  const knowledgeOwner = createAuthenticatedEditorOwner(controller, () =>
    createKnowledgeSessions(controller),
  );
  const subscriptionAccountsOwner = createAuthenticatedEditorOwner(
    controller,
    () => new SubscriptionAccountsSession(),
  );
  const subscriptionProbesOwner = createAuthenticatedEditorOwner(
    controller,
    () => new SubscriptionProbesSession(),
  );
  const subscriptionOptionsOwner = createAuthenticatedEditorOwner(
    controller,
    () => new SubscriptionOptionsSession(),
  );
  const mcpConnectionsOwner = createAuthenticatedEditorOwner(
    controller,
    createMcpConnections,
  );
  const runtimeInstallationsOwner = createAuthenticatedEditorOwner(
    controller,
    createRuntimeInstallations,
  );
  const documentRemovalsOwner = createAuthenticatedEditorOwner(controller, () =>
    createDocumentRemovals({
      review: controller.reviewDocumentRemoval,
      reviewRetry: controller.reviewDocumentRemovalRetry,
      execute: controller.executeDocumentRemoval,
      receipt: controller.documentRemovalReceipt,
    }),
  );
  const documentQueueOwner = createAuthenticatedEditorOwner(controller, () =>
    createDocumentQueueSession(controller),
  );
  const documentUploadOwner = createAuthenticatedEditorOwner(controller, () =>
    createDocumentUploadSession(controller),
  );
  const documentProcessingOwner = createAuthenticatedEditorOwner(
    controller,
    () => createDocumentProcessingSession(controller),
  );
  const wikiOwner = createAuthenticatedEditorOwner(controller, () =>
    createWikiSettingsSession(controller),
  );
  const channelOwner = createAuthenticatedEditorOwner(
    controller,
    createChannelSettingsSession,
  );
  const pluginOwner = createAuthenticatedEditorOwner(
    controller,
    createPluginSettingsSession,
  );
  const skillsOwner = createAuthenticatedEditorOwner(
    controller,
    createSkillsSettingsSession,
  );
  const goalProfileOwner = createAuthenticatedEditorOwner(
    controller,
    createGoalProfileSettingsSession,
  );
  const conversationActionsOwner = createAuthenticatedEditorOwner(
    controller,
    createConversationActionSessions,
  );
  const browserControlOwner = createAuthenticatedEditorOwner(
    controller,
    createBrowserControlSessions,
  );
  if (import.meta.env.VITE_ENABLE_FIXTURES === '1') {
    Object.defineProperty(window, '__ROW_BOT_WORKSPACE_METRICS__', {
      value: () => ({ ...resourcePanelMetrics }),
      configurable: true,
    });
  }
  if (import.meta.env.VITE_ENABLE_FIXTURES === '1' && fixtureTransport) {
    if (
      new URLSearchParams(location.search).get('fixturePlatform') === 'fake'
    ) {
      const { createFakePlatform } = await import('./platform/fake');
      platform = createFakePlatform();
    }
    Object.defineProperty(window, '__ROW_BOT_FIXTURE__', {
      value: {
        controller,
        transport: fixtureTransport,
        platform,
        panelMetrics,
      },
      configurable: true,
    });
  }
  createRoot(document.getElementById('root')!).render(
    <ThemeProvider>
      <PwaStatus />
      <RenderBoundary>
        <RuntimeContext.Provider
          value={{
            controller,
            platform,
            workspaceEditSessions,
            workspaceProcessSessions,
            workspaceImportSessions,
            workspaceUndoSessions,
            taskEditSessions,
            providerSettingsSessions,
            defaultModelOwner,
            capabilitySettingsOwner,
            buddyOwner,
            subscriptionAccountsOwner,
            subscriptionOptionsOwner,
            subscriptionProbesOwner,
            mcpConnectionsOwner,
            runtimeInstallationsOwner,
            documentRemovalsOwner,
            documentQueueOwner,
            documentUploadOwner,
            documentProcessingOwner,
            wikiOwner,
            channelOwner,
            pluginOwner,
            skillsOwner,
            goalProfileOwner,
            conversationActionsOwner,
            browserControlOwner,
            knowledgeOwner,
            providerConfigurationOwner,
            artifactDesignSessions,
          }}
        >
          <BrowserRouter basename="/app-v2">
            <OverlayProvider>
              <Suspense fallback={<Skeleton label="Opening workspace" />}>
                <Routes>
                  <Route path="/" element={<Workspace />}>
                    <Route
                      path="conversations/:conversationId"
                      element={null}
                    />
                    <Route path="primitives" element={<Gallery />} />
                    <Route path="settings" element={<SettingsIndex />} />
                    <Route
                      path="tasks"
                      element={<Navigate to="/?tab=workflows" replace />}
                    />
                    <Route
                      path="settings/:setting"
                      element={<SettingRoute />}
                    />
                    <Route
                      path="*"
                      element={
                        <EmptyState
                          title="View not found"
                          action={
                            <a className="button" href="/app-v2/">
                              Return to conversation
                            </a>
                          }
                        >
                          This view is not available.
                        </EmptyState>
                      }
                    />
                  </Route>
                </Routes>
              </Suspense>
            </OverlayProvider>
          </BrowserRouter>
        </RuntimeContext.Provider>
      </RenderBoundary>
    </ThemeProvider>,
  );
  bindPageLifecycle({
    setOnline: (value) => controller.setOnline(value),
    hasUnsavedDraft: () =>
      controller.hasUnsavedDraft() ||
      workspaceEditSessions.hasRetained() ||
      workspaceProcessSessions.hasRetained() ||
      workspaceImportSessions.hasRetained() ||
      workspaceUndoSessions.hasRetained() ||
      taskEditSessions.hasRetained() ||
      providerSettingsSessions.hasRetained() ||
      defaultModelOwner.hasRetained() ||
      capabilitySettingsOwner.hasRetained() ||
      buddyOwner.hasRetained() ||
      subscriptionAccountsOwner.hasRetained() ||
      subscriptionOptionsOwner.hasRetained() ||
      subscriptionProbesOwner.hasRetained() ||
      mcpConnectionsOwner.hasRetained() ||
      runtimeInstallationsOwner.hasRetained() ||
      documentRemovalsOwner.hasRetained() ||
      documentQueueOwner.hasRetained() ||
      documentUploadOwner.hasRetained() ||
      documentProcessingOwner.hasRetained() ||
      wikiOwner.hasRetained() ||
      channelOwner.hasRetained() ||
      pluginOwner.hasRetained() ||
      skillsOwner.hasRetained() ||
      goalProfileOwner.hasRetained() ||
      conversationActionsOwner.hasRetained() ||
      browserControlOwner.hasRetained() ||
      knowledgeOwner.hasRetained() ||
      providerConfigurationOwner.hasRetained() ||
      artifactDesignSessions.hasRetained(),
    dispose: () => {
      workspaceEditSessions.dispose();
      workspaceProcessSessions.dispose();
      workspaceImportSessions.dispose();
      workspaceUndoSessions.dispose();
      taskEditSessions.dispose();
      providerSettingsSessions.dispose();
      defaultModelOwner.dispose();
      capabilitySettingsOwner.dispose();
      buddyOwner.dispose();
      subscriptionAccountsOwner.dispose();
      subscriptionOptionsOwner.dispose();
      subscriptionProbesOwner.dispose();
      mcpConnectionsOwner.dispose();
      runtimeInstallationsOwner.dispose();
      documentRemovalsOwner.dispose();
      documentQueueOwner.dispose();
      documentUploadOwner.dispose();
      documentProcessingOwner.dispose();
      wikiOwner.dispose();
      channelOwner.dispose();
      pluginOwner.dispose();
      skillsOwner.dispose();
      goalProfileOwner.dispose();
      conversationActionsOwner.dispose();
      browserControlOwner.dispose();
      knowledgeOwner.dispose();
      providerConfigurationOwner.dispose();
      artifactDesignSessions.dispose();
      disposeActiveConversationSession();
      controller.dispose();
    },
  });
}
void start().catch(() => {
  const root = document.getElementById('root')!;
  root.replaceChildren();
  const message = document.createElement('p');
  message.textContent =
    'The workspace could not start. Reload the page to try again.';
  const link = document.createElement('a');
  link.href = '/';
  link.textContent = 'Open current application';
  root.append(message, link);
});
