import BuddySurface from '../buddy/BuddySurface';
import {
  useCallback,
  useEffect,
  useEffectEvent,
  useRef,
  useState,
} from 'react';
import {
  Link,
  Navigate,
  useNavigate,
  useParams,
  useSearchParams,
  useLocation,
} from 'react-router-dom';
import { useClientState, useRuntime } from '../../runtime';
import type { SettingsSnapshot } from '../../api/types';
import { clientError } from '../../api/errors';
import { Button, EmptyState, ErrorState, Skeleton } from '../../ui/primitives';
import { ModalTask } from '../../ui/overlays';
import {
  AGENT_PROFILE_SETTINGS,
  legacyIntegrationHref,
  resolveSetting,
  settingsHref,
  THREAD_SETTINGS,
} from './model';
import Preferences from './Preferences';
import AppearanceSettings from './Appearance';
import ProviderStatus from './ProviderStatus';
import ToolCatalog from './ToolCatalog';
import CustomToolsSettings from './CustomToolsSettings';
import MemorySettings from './MemorySettings';
import DocumentsCatalog from './DocumentsCatalog';
import ProviderConfiguration from './ProviderConfiguration';
import ProviderSettingsPanel from './ProviderSettingsPanel';
import ModelsPanel from './ModelsPanel';
import CapabilitySettings from './CapabilitySettings';
import McpFacadeControls from './McpFacadeControls';
import SubscriptionAccounts from './SubscriptionAccounts';
import SubscriptionOptions from './SubscriptionOptions';
import DocumentRemovalsPanel from '../knowledge/DocumentRemovals';
import { DocumentQueuePanel } from '../knowledge/DocumentQueuePanel';
import { DocumentUploadPanel } from '../knowledge/DocumentUploadPanel';
import { DocumentProcessingPanel } from '../knowledge/DocumentProcessingPanel';
import ChannelSettings from './ChannelSettings';
import { SETTINGS_CHANGED } from '../shell/palette-switches';
import PluginSettings from './PluginSettings';
import SkillsSettings from './SkillsSettings';
import AppsArea from '../apps/AppsArea';
import { resolveSettingsConversation } from './SettingsConversationPicker';
import Phase4RetainedSettings, {
  type Phase4RetainedSetting,
} from './Phase4RetainedSettings';
import { SettingsDangerZone, DangerAction, SettingsGroup } from './anatomy';
import { useWorkspaceActions } from '../shell/workspace-actions';
import SettingsShell from './SettingsShell';
import AccessConnect from './AccessConnect';
import AccessDevices from './AccessDevices';
import AccessNetwork from './AccessNetwork';
import AccessTailscale from './AccessTailscale';
import { pickSettingsFolder } from './settings-folder';
import {
  DocumentEmbeddingSnapshot,
  DocumentModelSetting,
  StartPublicLink,
  ToolConfigurationSnapshot,
  TrackerDangerAction,
  UtilitiesSnapshotPanel,
  type SettingsMutationIO,
  type SettingsPage,
  SettingsDraftOwner,
} from './SettingsSnapshotPanels';

/** Saved defaults for one page, when the server reports them. */
function settingsDefaults(snapshot: SettingsSnapshot, page: SettingsPage) {
  return snapshot.defaults?.[page];
}

/** Which saved-settings page each leaf reads and writes. */
const snapshotPages: Partial<Record<string, SettingsPage>> = {
  voice: 'voice',
  system: 'system',
  access: 'system',
  tracker: 'tracker',
  documents: 'documents',
  tools: 'tools',
  accounts: 'accounts',
  preferences: 'preferences',
  updates: 'preferences',
  data: 'preferences',
  knowledge: 'knowledge',
};

/**
 * Settings › Agent profiles became the sidebar's Agents dialog (B260): an
 * old link opens that dialog over the workspace.
 */
function AgentProfilesMoved() {
  const open = useWorkspaceActions()?.openAgentProfiles;
  const openDialog = useEffectEvent(() => open?.(null));
  useEffect(() => openDialog(), []);
  return <Navigate to="/" replace />;
}

export default function SettingRoute() {
  const { setting = 'preferences', '*': item = '' } = useParams();
  const navigate = useNavigate();
  const [search] = useSearchParams();
  const location = useLocation();
  const leaf = resolveSetting(setting);
  const {
    controller,
    platform,
    providerSettingsSessions,
    providerConfigurationOwner,
    defaultModelOwner,
    capabilitySettingsOwner,
    mcpChatOwner,
    subscriptionAccountsOwner,
    subscriptionOptionsOwner,
    documentRemovalsOwner,
    documentQueueOwner,
    documentUploadOwner,
    documentProcessingOwner,
    wikiOwner,
    channelOwner,
    pluginOwner,
    skillsOwner,
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
  // A switch turned from ⌘K: show it as saved.
  useEffect(() => {
    const changed = () => setSettingsSnapshotReload((value) => value + 1);
    window.addEventListener(SETTINGS_CHANGED, changed);
    return () => window.removeEventListener(SETTINGS_CHANGED, changed);
  }, []);
  const [knowledgeRefresh, setKnowledgeRefresh] = useState(0);
  const [devicesReload, setDevicesReload] = useState(0);
  // A removal in progress (retained by its owner) keeps its Danger zone open.
  const [documentDangerOpen, setDocumentDangerOpen] = useState(() =>
    Boolean(documentRemovalsOwner?.get()?.getSnapshot().selected),
  );
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
  // Settings › Memory's "Delete all" is reviewed against this catalog (B264).
  const loadKnowledgeCatalog = useCallback(
    (signal?: AbortSignal) =>
      controller.savedEntities('', undefined, undefined, signal),
    [controller],
  );
  if (THREAD_SETTINGS.has(setting.toLowerCase())) {
    // Goals belong to one conversation: open it, where Context shows them.
    const conversation =
      resolveSettingsConversation(
        state.conversations,
        requestedConversationId,
        state.selectedConversationId,
      ) ?? '';
    return (
      <Navigate
        to={
          conversation
            ? `/conversations/${encodeURIComponent(conversation)}`
            : '/'
        }
        replace
      />
    );
  }
  if (AGENT_PROFILE_SETTINGS.has(setting.toLowerCase()))
    return <AgentProfilesMoved />;
  if (!leaf) return <Navigate to="/settings/providers" replace />;
  if (leaf.id !== setting.toLowerCase()) {
    // Legacy ids and moved pages land on their new home (and row).
    if (['integrations', 'plugins', 'mcp'].includes(setting.toLowerCase()))
      return (
        <Navigate
          to={legacyIntegrationHref(
            setting.toLowerCase(),
            search,
            location.hash,
          )}
          replace
        />
      );
    const target = settingsHref(setting, location.hash) ?? leaf.href;
    const [pathQuery, hash] = target.split('#');
    const [path, defaults] = pathQuery.split('?');
    const merged = new URLSearchParams(defaults);
    search.forEach((value, key) => merged.set(key, value));
    return (
      <Navigate
        to={{
          pathname: path,
          search: merged.size ? `?${merged.toString()}` : '',
          hash: hash ? `#${hash}` : '',
        }}
        replace
      />
    );
  }
  const mutationFor = (page: SettingsPage | undefined) =>
    settingsSnapshot && page
      ? ({
          revision: settingsSnapshot.revision,
          page,
          sessionId: session,
          review: controller.reviewSettingsMutation,
          execute: controller.executeSettingsMutation,
          receipt: controller.settingsMutationReceipt,
          refreshSnapshot: () => controller.settingsSnapshot(),
          drafts: settingsDrafts.current.owner,
          onSnapshot: (snapshot) =>
            setLoadedSettingsSnapshot({ session, snapshot }),
          defaults: settingsDefaults(settingsSnapshot, page),
        } satisfies SettingsMutationIO)
      : null;
  const mutation: SettingsMutationIO | null = mutationFor(
    snapshotPages[leaf.id],
  );
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
  /** The advanced editor for one app or skill: its raw settings, never a whole list. */
  const editor = (kind: 'app' | 'skill', id: string) => {
    if (kind === 'skill' && skillsOwner?.get())
      return (
        <SkillsSettings
          integrationId={id === 'new' ? 'create' : id.replace(/^skill:/, '')}
          session={skillsOwner.get()!}
          io={{
            list: controller.skills,
            detail: controller.skill,
            proposals: controller.skillProposals,
            review: controller.reviewSkill,
            execute: controller.executeSkill,
            receipt: controller.skillReceipt,
          }}
        />
      );
    if (id.startsWith('plugin:') && pluginOwner?.get())
      return (
        <PluginSettings
          integrationId={id.replace(/^plugin:/, '')}
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
      );
    if (
      (id === 'custom' || id.startsWith('mcp:')) &&
      capabilitySettingsOwner?.get()
    )
      return (
        <CapabilitySettings
          session={capabilitySettingsOwner.get()!}
          only={id === 'custom' ? undefined : id.replace(/^mcp:/, '')}
          startAdd={id === 'custom'}
          load={({ query, cursor }, signal) =>
            controller.mcpConfiguration(query, cursor, signal)
          }
          review={controller.reviewMcpConfiguration}
          execute={controller.executeMcpConfiguration}
          onConnection={(serverId) =>
            navigate(
              `/settings/apps/item?${new URLSearchParams({ id: 'mcp:' + serverId })}`,
            )
          }
          onRemoved={() => navigate('/settings/apps')}
        />
      );
    return <p>These settings are unavailable. Reconnect to continue.</p>;
  };
  return (
    <SettingsShell key={session} leaf={leaf}>
      <section
        className="route-surface stack capability-page"
        aria-label={leaf.label}
      >
        {leaf.id === 'buddy' ? (
          settingsConversationId ? (
            <BuddySurface
              key={settingsConversationId}
              settings
              conversationId={settingsConversationId}
              initialPrompt={settingsSnapshot?.buddy.hatch_prompt}
            />
          ) : (
            <EmptyState title="Open a conversation for Buddy">
              Buddy uses that conversation’s current profile and approvals.
            </EmptyState>
          )
        ) : leaf.id === 'appearance' ? (
          <AppearanceSettings />
        ) : leaf.id === 'preferences' ||
          leaf.id === 'updates' ||
          leaf.id === 'data' ? (
          <>
            <Preferences
              snapshot={settingsSnapshot?.preferences}
              mutation={mutation}
              snapshotState={snapshotState}
              showUpdateControls
              part={leaf.id}
            />
            {leaf.id === 'data' && settingsSnapshot && (
              <DataDangerZone
                snapshot={settingsSnapshot}
                mutation={mutationFor('tracker')}
              />
            )}
          </>
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
              hideCustom={Boolean(providerConfigurationOwner?.get())}
            />
            {selectedSubscription && subscriptionAccountsOwner?.get() && (
              <ModalTask
                open
                title="Manage subscription account"
                description="Connect, inspect or disconnect this provider account."
                ariaLabel="Manage subscription account"
                onOpenChange={(open) => {
                  if (!open) setSelectedSubscription(null);
                }}
              >
                <div className="settings-provider-dialog-content">
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
              </ModalTask>
            )}
            {selectedSubscriptionOption && subscriptionOptionsOwner?.get() && (
              <ModalTask
                open
                title="Account options"
                description="Review the account-specific model and runtime options."
                ariaLabel="Account options"
                onOpenChange={(open) => {
                  if (!open) setSelectedSubscriptionOption('');
                }}
              >
                <div className="settings-provider-dialog-content">
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
              </ModalTask>
            )}
            {providerConfigurationOwner?.get() && (
              <ProviderConfiguration
                compact
                autoAdd={search.get('add') === 'custom-endpoint'}
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
              <ModalTask
                open
                title="Custom endpoint API key"
                description="Update the credential for this custom endpoint."
                ariaLabel="Custom endpoint API key"
                onOpenChange={(open) => {
                  if (!open) setSelectedCustomCredential('');
                }}
              >
                <div className="settings-provider-dialog-content">
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
              </ModalTask>
            )}
          </>
        ) : leaf.id === 'models' && defaultModelOwner?.get() ? (
          <ModelsPanel
            controller={controller}
            session={defaultModelOwner.get()!}
            initialProvider={search.get('provider') ?? ''}
            openExternal={(url) => void platform.openExternal(url)}
          />
        ) : leaf.id === 'apps' || leaf.id === 'skills' ? (
          <AppsArea
            kind={leaf.id === 'apps' ? 'app' : 'skill'}
            item={item}
            editor={editor}
            chat={
              mcpChatOwner?.get() && (
                <McpFacadeControls
                  session={mcpChatOwner.get()!}
                  load={(signal) => controller.mcpChat(signal)}
                  review={(payload, signal) =>
                    controller.reviewMcpChat(payload, signal)
                  }
                  execute={(command, review) =>
                    controller.executeMcpChat(command, review)
                  }
                />
              )
            }
          />
        ) : leaf.id === 'tools' ? (
          <>
            {settingsSnapshot && mutation ? (
              <>
                <ToolConfigurationSnapshot
                  snapshot={settingsSnapshot.tools}
                  mutation={mutation}
                />
                <UtilitiesSnapshotPanel
                  snapshot={settingsSnapshot.utilities}
                  mutation={mutationFor('utilities')!}
                />
              </>
            ) : (
              snapshotState
            )}
            {/* Siblings need distinct keys, or React keeps one of them
                on the next page (B183). */}
            <CustomToolsSettings key={`custom-tools:${session}`} />
            <ToolCatalog
              key={`tool-catalog:${session}`}
              load={controller.cachedTools}
            />
          </>
        ) : leaf.id === 'knowledge' ? (
          // B264: settings only; memories are browsed and edited in Knowledge.
          <MemorySettings
            key={session}
            loadCatalog={loadKnowledgeCatalog}
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
            onMutation={() => {
              knowledgeOwner?.get()?.close();
              setKnowledgeRefresh((value) => value + 1);
              setSettingsSnapshotReload((value) => value + 1);
              void wikiOwner?.get()?.load();
            }}
            snapshot={settingsSnapshot?.knowledge}
            refreshToken={knowledgeRefresh}
          />
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
            loadLink={controller.channelLink}
          />
        ) : leaf.id === 'documents' ? (
          <div className="stack settings-snapshot-page settings-documents-flow">
            <SettingsGroup title="Add documents" surface={false}>
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
              {settingsSnapshot && mutation && (
                <div className="settings-group-surface">
                  <DocumentModelSetting
                    snapshot={settingsSnapshot.documents}
                    mutation={mutation}
                    models={state.handshake?.models ?? []}
                  />
                </div>
              )}
            </SettingsGroup>
            <SettingsGroup title="Your documents">
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
                              'Check processing before choosing another batch.',
                            );
                          }
                        }
                      : undefined
                  }
                />
              )}
              {processingSelectionError && (
                <p
                  role="alert"
                  className="settings-divided document-queue-note"
                >
                  {processingSelectionError}
                </p>
              )}
              {documentProcessingOwner?.get() && (
                <DocumentProcessingPanel
                  owner={documentProcessingOwner.get()!}
                  conversationTitle={(id) =>
                    state.conversations
                      .find((conversation) => conversation.id === id)
                      ?.title.trim() || undefined
                  }
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
                onRemove={(id, label) => {
                  documentRemovalsOwner?.get()?.select(id, label);
                  // The removal review lives in the Danger zone: show it.
                  setDocumentDangerOpen(true);
                  requestAnimationFrame(() =>
                    document
                      .querySelector('.settings-document-danger')
                      ?.scrollIntoView?.({ block: 'nearest' }),
                  );
                }}
              />
            </SettingsGroup>
            {settingsSnapshot && mutation ? (
              <DocumentEmbeddingSnapshot
                snapshot={settingsSnapshot.documents}
                mutation={mutation}
              />
            ) : (
              snapshotState
            )}
            {documentRemovalsOwner?.get() && (
              <SettingsDangerZone
                meta="Remove documents"
                open={documentDangerOpen}
                onOpenChange={setDocumentDangerOpen}
              >
                <div className="settings-document-danger">
                  <p className="settings-help">
                    Remove indexed source material through reviewed,
                    receipt-backed commands.
                  </p>
                  <DocumentRemovalsPanel owner={documentRemovalsOwner.get()!} />
                </div>
              </SettingsDangerZone>
            )}
          </div>
        ) : ['voice', 'accounts', 'tracker', 'system', 'access'].includes(
            leaf.id,
          ) ? (
          settingsSnapshot && mutation ? (
            <>
              {leaf.id === 'access' ? (
                // Devices & remote access: connect, your devices, then
                // Advanced (from the retained panel below).
                <>
                  <AccessConnect
                    tunnel={settingsSnapshot.system.tunnel}
                    startPublic={
                      <StartPublicLink
                        mutation={mutation}
                        description="Opens Row-Bot to the internet through your saved ngrok setup until you stop it."
                      />
                    }
                    writeClipboard={platform.writeClipboard}
                    onConnected={() => setDevicesReload((value) => value + 1)}
                  />
                  <AccessDevices reloadKey={devicesReload} />
                </>
              ) : null}
              <Phase4RetainedSettings
                setting={leaf.id as Phase4RetainedSetting}
                snapshot={settingsSnapshot}
                mutation={mutation}
                selectedConversationId={state.selectedConversationId}
                pickFolder={(signal) =>
                  pickSettingsFolder(platform, controller, signal)
                }
                showAccountActions
                writeClipboard={platform.writeClipboard}
                accessNetwork={
                  leaf.id === 'access' ? (
                    <>
                      <AccessNetwork />
                      <AccessTailscale
                        variant="line"
                        writeClipboard={platform.writeClipboard}
                      />
                    </>
                  ) : undefined
                }
              />
            </>
          ) : (
            snapshotState
          )
        ) : state.handshake ? (
          <Skeleton label={`Loading ${leaf.label}`} />
        ) : state.status === 'loading' || state.status === 'reconnecting' ? (
          <Skeleton label="Connecting to Row-Bot" />
        ) : (
          // B110: a lost connection is a retry state, never another app.
          <ErrorState
            title="Row-Bot isn't connected"
            action={
              <Button onClick={() => void controller.reconnect()}>
                Reconnect
              </Button>
            }
          >
            {leaf.label} loads once Row-Bot is connected again.
          </ErrorState>
        )}
      </section>
    </SettingsShell>
  );
}

/** Data › Danger zone: every irreversible clean-up in one place. */
function DataDangerZone({
  snapshot,
  mutation,
}: {
  snapshot: SettingsSnapshot;
  mutation: SettingsMutationIO | null;
}) {
  return (
    <SettingsDangerZone meta="Irreversible clean-up">
      <DangerAction
        title="Remove documents"
        description="Removing indexed documents is reviewed per document on the Documents page."
      >
        <Link className="button danger" to="/settings/documents#danger-zone">
          Open Documents
        </Link>
      </DangerAction>
      {mutation && snapshot.tracker.items.length > 0 ? (
        <TrackerDangerAction mutation={mutation} />
      ) : null}
    </SettingsDangerZone>
  );
}
