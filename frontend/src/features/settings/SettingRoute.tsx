import BuddySurface from '../buddy/BuddySurface';
import { Trash2 } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { Navigate, useParams, useSearchParams } from 'react-router-dom';
import { useClientState, useRuntime } from '../../runtime';
import type { SettingsSnapshot } from '../../api/types';
import { clientError } from '../../api/errors';
import { Button, EmptyState, ErrorState, Skeleton } from '../../ui/primitives';
import { resolveSetting } from './model';
import Preferences from './Preferences';
import ProviderStatus from './ProviderStatus';
import ToolCatalog from './ToolCatalog';
import KnowledgeCatalog from './KnowledgeCatalog';
import DocumentsCatalog from './DocumentsCatalog';
import ProviderConfiguration from './ProviderConfiguration';
import ProviderSettingsPanel from './ProviderSettingsPanel';
import ModelsPanel from './ModelsPanel';
import CapabilitySettings from './CapabilitySettings';
import SubscriptionAccounts from './SubscriptionAccounts';
import SubscriptionOptions from './SubscriptionOptions';
import McpConnectionsPanel from './McpConnections';
import RuntimeInstallations from '../mcp/RuntimeInstallations';
import DocumentRemovalsPanel from '../knowledge/DocumentRemovals';
import KnowledgeEditorDialog from '../knowledge/KnowledgeEditorDialog';
import { DocumentQueuePanel } from '../knowledge/DocumentQueuePanel';
import { DocumentUploadPanel } from '../knowledge/DocumentUploadPanel';
import { DocumentProcessingPanel } from '../knowledge/DocumentProcessingPanel';
import ChannelSettings from './ChannelSettings';
import PluginSettings from './PluginSettings';
import SkillsSettings from './SkillsSettings';
import GoalProfileSettings from './GoalProfileSettings';
import SettingsConversationPicker, {
  resolveSettingsConversation,
} from './SettingsConversationPicker';
import Phase4RetainedSettings, {
  type Phase4RetainedSetting,
} from './Phase4RetainedSettings';
import SettingsShell from './SettingsShell';
import {
  DocumentEmbeddingSnapshot,
  ToolConfigurationSnapshot,
  type SettingsMutationIO,
  type SettingsPage,
  SettingsDraftOwner,
} from './SettingsSnapshotPanels';

export default function SettingRoute() {
  const { setting = 'preferences' } = useParams();
  const [search, setSearch] = useSearchParams();
  const leaf = resolveSetting(setting);
  const {
    controller,
    providerSettingsSessions,
    providerConfigurationOwner,
    defaultModelOwner,
    capabilitySettingsOwner,
    subscriptionAccountsOwner,
    subscriptionOptionsOwner,
    mcpConnectionsOwner,
    documentRemovalsOwner,
    documentQueueOwner,
    documentUploadOwner,
    documentProcessingOwner,
    wikiOwner,
    channelOwner,
    pluginOwner,
    skillsOwner,
    goalProfileOwner,
    knowledgeOwner,
  } = useRuntime();
  const state = useClientState();
  const session = state.handshake?.client_session_id ?? '';
  const settingsDrafts = useRef({
    session,
    owner: new SettingsDraftOwner(),
  });
  if (settingsDrafts.current.session !== session)
    settingsDrafts.current = {
      session,
      owner: new SettingsDraftOwner(),
    };
  const [processingSelectionError, setProcessingSelectionError] = useState('');
  const [selectedSubscription, setSelectedSubscription] = useState<{
    provider: string;
    action: 'connect' | 'manage' | 'disconnect';
  } | null>(null);
  const [selectedSubscriptionOption, setSelectedSubscriptionOption] =
    useState('');
  const [selectedCustomCredential, setSelectedCustomCredential] = useState('');
  const [endpointCredentialRefresh, setEndpointCredentialRefresh] = useState<
    { providerId: string; token: number; session: string } | undefined
  >();
  const [providerReload, setProviderReload] = useState(0);
  const [loadedSettingsSnapshot, setLoadedSettingsSnapshot] = useState<{
    session: string;
    snapshot: SettingsSnapshot;
  } | null>(null);
  const settingsSnapshot =
    loadedSettingsSnapshot?.session === session
      ? loadedSettingsSnapshot.snapshot
      : null;
  const [settingsSnapshotLoading, setSettingsSnapshotLoading] = useState(true);
  const [settingsSnapshotError, setSettingsSnapshotError] = useState('');
  const [settingsSnapshotReload, setSettingsSnapshotReload] = useState(0);
  const [knowledgeRefresh, setKnowledgeRefresh] = useState(0);
  const requestedConversationId = search.get('conversation');
  const settingsConversationId = resolveSettingsConversation(
    state.conversations,
    requestedConversationId,
    state.selectedConversationId,
  );

  useEffect(() => {
    const abort = new AbortController();
    setSettingsSnapshotLoading(true);
    setSettingsSnapshotError('');
    void controller.settingsSnapshot(abort.signal).then(
      (snapshot) => {
        if (!abort.signal.aborted) {
          setLoadedSettingsSnapshot({ session, snapshot });
          setSettingsSnapshotLoading(false);
        }
      },
      (cause) => {
        if (!abort.signal.aborted) {
          setSettingsSnapshotError(clientError(cause).message);
          setSettingsSnapshotLoading(false);
        }
      },
    );
    return () => abort.abort();
  }, [
    controller,
    session,
    settingsSnapshotReload,
    state.handshake?.server_epoch,
  ]);
  useEffect(() => {
    if (
      !leaf ||
      !['buddy', 'goals'].includes(leaf.id) ||
      !settingsConversationId ||
      requestedConversationId === settingsConversationId
    )
      return;
    const next = new URLSearchParams(search);
    next.set('conversation', settingsConversationId);
    setSearch(next, { replace: true });
  }, [
    leaf,
    requestedConversationId,
    search,
    setSearch,
    settingsConversationId,
  ]);
  if (!leaf) return <Navigate to="/settings/providers" replace />;
  if (leaf.id !== setting.toLowerCase())
    return (
      <Navigate
        to={`${leaf.href}${search.size ? `?${search.toString()}` : ''}`}
        replace
      />
    );
  const settingsPages: SettingsPage[] = [
    'voice',
    'system',
    'tracker',
    'documents',
    'tools',
    'accounts',
    'utilities',
    'preferences',
    'knowledge',
  ];
  const snapshotPage = settingsPages.includes(leaf.id as SettingsPage)
    ? (leaf.id as SettingsPage)
    : null;
  const mutation: SettingsMutationIO | null =
    settingsSnapshot && snapshotPage
      ? {
          revision: settingsSnapshot.revision,
          page: snapshotPage,
          review: controller.reviewSettingsMutation,
          execute: controller.executeSettingsMutation,
          receipt: controller.settingsMutationReceipt,
          drafts: settingsDrafts.current.owner,
          onSnapshot: (snapshot) =>
            setLoadedSettingsSnapshot({ session, snapshot }),
        }
      : null;
  const snapshotState = !settingsSnapshot ? (
    settingsSnapshotLoading || loadedSettingsSnapshot?.session !== session ? (
      <Skeleton label="Loading saved Settings" />
    ) : (
      <ErrorState
        title="Saved Settings unavailable"
        action={
          <Button
            onClick={() => setSettingsSnapshotReload((value) => value + 1)}
          >
            Retry
          </Button>
        }
      >
        {settingsSnapshotError ||
          'The saved Settings snapshot could not be loaded.'}
      </ErrorState>
    )
  ) : null;
  return (
    <SettingsShell key={session} leaf={leaf}>
      <section
        className="route-surface stack capability-page"
        aria-label={leaf.label}
      >
        {leaf?.id === 'buddy' ? (
          settingsConversationId ? (
            <>
              <SettingsConversationPicker
                conversations={state.conversations}
                conversationId={settingsConversationId}
                onChange={(conversationId) => {
                  const next = new URLSearchParams(search);
                  next.set('conversation', conversationId);
                  setSearch(next, { replace: true });
                }}
              />
              <BuddySurface
                key={settingsConversationId}
                settings
                initialPrompt={settingsSnapshot?.buddy.hatch_prompt}
              />
            </>
          ) : (
            <EmptyState title="Open a conversation for Buddy">
              Buddy uses that conversation’s current profile and approvals.
            </EmptyState>
          )
        ) : leaf?.id === 'preferences' ? (
          <Preferences
            snapshot={settingsSnapshot?.preferences}
            mutation={mutation}
            snapshotState={snapshotState}
          />
        ) : leaf.id === 'providers' ? (
          <>
            <ProviderStatus
              key={`${session}:${providerReload}`}
              load={controller.liveProviderStatus}
              refresh={controller.refreshLiveProvider}
              refreshState={controller.liveProviderRefresh}
              testRuntime={controller.testLiveProviderRuntime}
              owner={providerSettingsSessions}
              onSubscription={(provider, action) =>
                setSelectedSubscription({ provider, action })
              }
              onSubscriptionOption={setSelectedSubscriptionOption}
            />
            {selectedSubscription && subscriptionAccountsOwner?.get() && (
              <div className="settings-provider-dialog-backdrop">
                <div
                  className="settings-provider-dialog"
                  role="dialog"
                  aria-modal="true"
                  aria-label="Manage subscription account"
                >
                  <SubscriptionAccounts
                    compact
                    initialProvider={
                      selectedSubscription.provider as
                        'codex' | 'claude_subscription' | 'xai_oauth'
                    }
                    initialAction={selectedSubscription.action}
                    onClose={() => setSelectedSubscription(null)}
                    session={subscriptionAccountsOwner.get()}
                    load={controller.subscriptionAccounts}
                    review={controller.reviewSubscriptionAction}
                    apply={controller.applySubscriptionAction}
                    readFlow={controller.subscriptionFlow}
                    cancel={controller.cancelSubscriptionFlow}
                    cancelStart={controller.cancelSubscriptionStart}
                    receipt={controller.subscriptionReceipt}
                    onSaved={() => setProviderReload((value) => value + 1)}
                  />
                </div>
              </div>
            )}
            {selectedSubscriptionOption && subscriptionOptionsOwner?.get() && (
              <div className="settings-provider-dialog-backdrop">
                <div
                  className="settings-provider-dialog"
                  role="dialog"
                  aria-modal="true"
                  aria-label="Account options"
                >
                  <SubscriptionOptions
                    compact
                    initialProvider={
                      selectedSubscriptionOption as
                        'codex' | 'claude_subscription' | 'xai_oauth'
                    }
                    onClose={() => setSelectedSubscriptionOption('')}
                    session={subscriptionOptionsOwner.get()}
                    load={controller.subscriptionOptions}
                    review={controller.reviewSubscriptionOptions}
                    apply={controller.applySubscriptionOptions}
                    receipt={controller.subscriptionOptionsReceipt}
                    onSaved={() => setProviderReload((value) => value + 1)}
                  />
                </div>
              </div>
            )}
            {providerConfigurationOwner?.get() && (
              <ProviderConfiguration
                compact
                credentialRefreshRequest={
                  endpointCredentialRefresh?.session === session
                    ? endpointCredentialRefresh
                    : undefined
                }
                key={`configuration:${session}`}
                session={providerConfigurationOwner.get()}
                load={controller.providerConfiguration}
                review={(operation, configuration_revision, fields, signal) =>
                  controller.reviewProviderConfiguration(
                    { operation, configuration_revision, fields },
                    signal,
                  )
                }
                apply={(operation, revision, fields, command, review) => {
                  if (!review.nonce)
                    return Promise.reject({ code: 'approval_expired' });
                  return controller.executeProviderConfiguration(
                    operation,
                    revision,
                    fields,
                    command,
                    review.nonce,
                  );
                }}
                receipt={async (command, signal) =>
                  (
                    await controller.providerConfigurationReceipt(
                      command,
                      signal,
                    )
                  ).status
                }
                onSaved={() => setProviderReload((value) => value + 1)}
                onCredentials={(providerId) => {
                  if (providerId) setSelectedCustomCredential(providerId);
                }}
              />
            )}
            {selectedCustomCredential && providerSettingsSessions && (
              <div className="settings-provider-dialog-backdrop">
                <div
                  className="settings-provider-dialog"
                  role="dialog"
                  aria-modal="true"
                  aria-label="Custom endpoint API key"
                >
                  <ProviderSettingsPanel
                    compact
                    owner={providerSettingsSessions}
                    providerId={selectedCustomCredential}
                    onSaved={(saved) => {
                      const providerId = selectedCustomCredential;
                      setSelectedCustomCredential('');
                      setProviderReload((value) => value + 1);
                      if (saved.configured)
                        setEndpointCredentialRefresh((current) => ({
                          providerId,
                          session,
                          token: (current?.token ?? 0) + 1,
                        }));
                    }}
                    onCancel={() => setSelectedCustomCredential('')}
                  />
                </div>
              </div>
            )}
          </>
        ) : leaf.id === 'models' && defaultModelOwner?.get() ? (
          <ModelsPanel
            controller={controller}
            session={defaultModelOwner.get()!}
            initialProvider={search.get('provider') ?? ''}
          />
        ) : leaf.id === 'mcp' && capabilitySettingsOwner?.get() ? (
          <>
            <CapabilitySettings
              session={capabilitySettingsOwner.get()!}
              load={({ query, cursor }, signal) =>
                controller.mcpConfiguration(query, cursor, signal)
              }
              review={controller.reviewMcpConfiguration}
              execute={controller.executeMcpConfiguration}
              onConnection={(id, name) =>
                mcpConnectionsOwner?.get()?.select(id, name)
              }
            />
            {mcpConnectionsOwner?.get() && (
              <McpConnectionsPanel
                catalog={{
                  load: controller.mcpTestedCatalog,
                  review: controller.reviewMcpCatalog,
                  execute: controller.executeMcpConfiguration,
                }}
                owner={mcpConnectionsOwner.get()!}
                load={controller.mcpRuntime}
                review={controller.reviewMcpRuntime}
                execute={controller.executeMcpRuntime}
                policy={{
                  load: controller.mcpPolicy,
                  review: controller.reviewMcpPolicy,
                  execute: controller.executeMcpConfiguration,
                }}
              />
            )}
            <RuntimeInstallations />
          </>
        ) : leaf.id === 'tools' ? (
          <>
            {settingsSnapshot && mutation ? (
              <ToolConfigurationSnapshot
                snapshot={settingsSnapshot.tools}
                mutation={mutation}
              />
            ) : (
              snapshotState
            )}
            <ToolCatalog key={session} load={controller.cachedTools} />
          </>
        ) : leaf.id === 'knowledge' ? (
          <>
            <KnowledgeCatalog
              key={session}
              loadFiltered={(filters, cursor, signal) =>
                controller.knowledgeEntities(
                  filters.query,
                  filters.entityType || undefined,
                  filters.status || undefined,
                  filters.source || undefined,
                  filters.tier || undefined,
                  cursor,
                  signal,
                )
              }
              loadDetail={controller.knowledgeEntityDetail}
              loadRecalls={controller.knowledgeRecalls}
              loadChangeLog={controller.knowledgeChangeLog}
              maintenance={{
                review: (action, catalogRevision, targets, signal) =>
                  controller.reviewKnowledgeMaintenance(
                    {
                      action,
                      catalog_revision: catalogRevision,
                      targets,
                    },
                    signal,
                  ),
                execute: (review, commandId) =>
                  controller.executeKnowledgeMaintenance({
                    command_id: commandId,
                    type: review.action,
                    payload: {
                      catalog_revision: review.catalog_revision,
                      targets: review.targets,
                      action_digest: review.action_digest,
                      review_id: review.review_id,
                    },
                  }),
                receipt: controller.knowledgeMaintenanceReceipt,
              }}
              settingsMutation={mutation}
              wikiSession={wikiOwner?.get()}
              wikiSnapshot={settingsSnapshot?.wiki}
              onOpen={(id) => knowledgeOwner?.get()?.open(id)}
              onLifecycle={async (id, revision, action) => {
                const review = await controller.reviewKnowledge(action, {
                  entity_id: id,
                  revision,
                });
                const receipt = await controller.executeKnowledge({
                  command_id: crypto.randomUUID(),
                  type: action,
                  payload: {
                    entity_id: id,
                    revision: review.revision,
                    review_id: review.review_id,
                  },
                });
                if (receipt.status !== 'completed')
                  throw { code: receipt.code ?? 'knowledge_outcome_uncertain' };
                setKnowledgeRefresh((value) => value + 1);
                setSettingsSnapshotReload((value) => value + 1);
                void wikiOwner?.get()?.load();
              }}
              onMutation={() => {
                knowledgeOwner?.get()?.close();
                setKnowledgeRefresh((value) => value + 1);
                setSettingsSnapshotReload((value) => value + 1);
                void wikiOwner?.get()?.load();
              }}
              snapshot={settingsSnapshot?.knowledge}
              refreshToken={knowledgeRefresh}
            />
            {knowledgeOwner?.get() && (
              <KnowledgeEditorDialog
                owner={knowledgeOwner.get()!}
                onMutation={() => {
                  setKnowledgeRefresh((value) => value + 1);
                  setSettingsSnapshotReload((value) => value + 1);
                  void wikiOwner?.get()?.load();
                }}
              />
            )}
          </>
        ) : leaf.id === 'channels' && channelOwner?.get() ? (
          <ChannelSettings
            session={channelOwner.get()!}
            load={controller.channels}
            review={controller.reviewChannel}
            execute={(command, review) =>
              controller.executeChannel({
                ...command,
                payload: { ...command.payload, review_id: review.review_id },
              })
            }
          />
        ) : leaf.id === 'plugins' && pluginOwner?.get() ? (
          <PluginSettings
            session={pluginOwner.get()!}
            load={({ query, source, cursor }, signal) =>
              controller.plugins(query, source, cursor, signal)
            }
            open={controller.plugin}
            review={(action, payload, signal) =>
              controller.reviewPlugin(
                String(payload.plugin_id ?? ''),
                action,
                payload,
                signal,
              )
            }
            execute={(command, review) =>
              controller.executePlugin(String(command.payload.plugin_id), {
                ...command,
                payload: { ...command.payload, review_id: review.review_id },
              })
            }
          />
        ) : leaf.id === 'skills' && skillsOwner?.get() ? (
          <SkillsSettings
            session={skillsOwner.get()!}
            io={{
              list: controller.skills,
              detail: controller.skill,
              proposals: controller.skillProposals,
              review: controller.reviewSkill,
              execute: (command) => controller.executeSkill(command),
              receipt: controller.skillReceipt,
            }}
          />
        ) : leaf.id === 'goals' && goalProfileOwner?.get() ? (
          settingsConversationId ? (
            <>
              <SettingsConversationPicker
                conversations={state.conversations}
                conversationId={settingsConversationId}
                onChange={(conversationId) => {
                  const next = new URLSearchParams(search);
                  next.set('conversation', conversationId);
                  setSearch(next, { replace: true });
                }}
              />
              <GoalProfileSettings
                key={settingsConversationId}
                conversationId={settingsConversationId}
                session={goalProfileOwner.get()!}
                loadGoals={({ conversation_id, query, cursor }, signal) =>
                  controller.goals(conversation_id, query, cursor, signal)
                }
                loadProfiles={({ query, scope, cursor }, signal) =>
                  controller.profiles(query, scope, cursor, signal)
                }
                loadProfile={controller.profile}
                reviewGoal={(payload, signal) =>
                  controller.reviewGoal(settingsConversationId, payload, signal)
                }
                executeGoal={(command, review) =>
                  controller.executeGoal(settingsConversationId, {
                    ...command,
                    payload: {
                      ...command.payload,
                      review_id: review.review_id,
                    },
                  })
                }
                reviewProfile={controller.reviewProfile}
                executeProfile={(command, review) =>
                  controller.executeProfile({
                    ...command,
                    payload: {
                      ...command.payload,
                      review_id: review.review_id,
                    },
                  })
                }
              />
            </>
          ) : (
            <EmptyState title="Open a conversation">
              Goals belong to one conversation. Open or create a conversation,
              then return here. Agent Profiles remain available from the Goals
              view.
            </EmptyState>
          )
        ) : leaf.id === 'documents' ? (
          <div className="stack settings-documents-flow">
            {settingsSnapshot && mutation ? (
              <DocumentEmbeddingSnapshot
                snapshot={settingsSnapshot.documents}
                mutation={mutation}
              />
            ) : (
              snapshotState
            )}
            {documentUploadOwner?.get() && (
              <DocumentUploadPanel
                owner={documentUploadOwner.get()!}
                onStaged={() => {
                  const queue = documentQueueOwner?.get();
                  if (queue && !queue.hasRetained())
                    void queue.session.load().catch(() => undefined);
                }}
              />
            )}
            {documentQueueOwner?.get() && (
              <DocumentQueuePanel
                owner={documentQueueOwner.get()!}
                onProcess={
                  documentProcessingOwner?.get()
                    ? (batch) => {
                        if (!state.selectedConversationId) {
                          setProcessingSelectionError(
                            'Open or create a conversation first, then return here to review its document processing policy.',
                          );
                          return;
                        }
                        try {
                          documentProcessingOwner
                            .get()!
                            .select(
                              state.selectedConversationId,
                              batch.id,
                              batch.revision,
                            );
                          setProcessingSelectionError('');
                        } catch {
                          setProcessingSelectionError(
                            'Check the original processing receipt before selecting another batch.',
                          );
                        }
                      }
                    : undefined
                }
              />
            )}
            {processingSelectionError && (
              <p role="alert">{processingSelectionError}</p>
            )}
            {documentProcessingOwner?.get() && (
              <DocumentProcessingPanel
                owner={documentProcessingOwner.get()!}
                onAdmitted={() => {
                  const queue = documentQueueOwner?.get();
                  if (queue && !queue.hasRetained())
                    void queue.session.load().catch(() => undefined);
                }}
              />
            )}
            <DocumentsCatalog
              key={session}
              load={controller.savedDocuments}
              onRemove={(id, label) =>
                documentRemovalsOwner?.get()?.select(id, label)
              }
            />
            {documentRemovalsOwner?.get() && (
              <section
                className="settings-snapshot-section stack is-danger settings-document-danger"
                aria-labelledby="settings-document-danger"
              >
                <header className="settings-snapshot-heading">
                  <Trash2 size={18} aria-hidden />
                  <div>
                    <h3 id="settings-document-danger">Danger Zone</h3>
                    <p>
                      Remove indexed source material through reviewed,
                      receipt-backed commands.
                    </p>
                  </div>
                </header>
                <DocumentRemovalsPanel owner={documentRemovalsOwner.get()!} />
              </section>
            )}
          </div>
        ) : ['voice', 'accounts', 'tracker', 'utilities', 'system'].includes(
            leaf.id,
          ) ? (
          settingsSnapshot && mutation ? (
            <Phase4RetainedSettings
              setting={leaf.id as Phase4RetainedSetting}
              snapshot={settingsSnapshot}
              mutation={mutation}
              selectedConversationId={state.selectedConversationId}
            />
          ) : (
            snapshotState
          )
        ) : (
          <EmptyState
            title={leaf?.label ?? 'Setting not found'}
            action={
              <a className="button" href="/">
                Open current application
              </a>
            }
          >
            This setting is available in the current application.
          </EmptyState>
        )}
      </section>
    </SettingsShell>
  );
}
