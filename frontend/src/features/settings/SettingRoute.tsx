import BuddySurface from '../buddy/BuddySurface';
import { useState } from 'react';
import {
  Navigate,
  useNavigate,
  useParams,
  useSearchParams,
} from 'react-router-dom';
import { useClientState, useRuntime } from '../../runtime';
import { EmptyState } from '../../ui/primitives';
import { resolveSetting } from './model';
import Preferences from './Preferences';
import ProviderStatus from './ProviderStatus';
import ModelCatalog from './ModelCatalog';
import ToolCatalog from './ToolCatalog';
import KnowledgeCatalog from './KnowledgeCatalog';
import DocumentsCatalog from './DocumentsCatalog';
import ProviderConfiguration from './ProviderConfiguration';
import DefaultModelSettings from './DefaultModelSettings';
import CapabilitySettings from './CapabilitySettings';
import SubscriptionAccounts from './SubscriptionAccounts';
import SubscriptionProbes from './SubscriptionProbes';
import SubscriptionOptions from './SubscriptionOptions';
import McpConnectionsPanel from './McpConnections';
import RuntimeInstallations from '../mcp/RuntimeInstallations';
import DocumentRemovalsPanel from '../knowledge/DocumentRemovals';
import KnowledgeEditors from '../knowledge/KnowledgeEditors';
import { DocumentQueuePanel } from '../knowledge/DocumentQueuePanel';
import { DocumentUploadPanel } from '../knowledge/DocumentUploadPanel';
import { DocumentProcessingPanel } from '../knowledge/DocumentProcessingPanel';
import WikiSettings from '../knowledge/WikiSettings';
import ChannelSettings from './ChannelSettings';
import PluginSettings from './PluginSettings';
import SkillsSettings from './SkillsSettings';
import GoalProfileSettings from './GoalProfileSettings';
import Phase4RetainedSettings, {
  type Phase4RetainedSetting,
} from './Phase4RetainedSettings';

export default function SettingRoute() {
  const { setting = 'preferences' } = useParams();
  const [search] = useSearchParams();
  const navigate = useNavigate();
  const {
    controller,
    providerSettingsSessions,
    providerConfigurationOwner,
    defaultModelOwner,
    capabilitySettingsOwner,
    subscriptionAccountsOwner,
    subscriptionOptionsOwner,
    subscriptionProbesOwner,
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
  const [processingSelectionError, setProcessingSelectionError] = useState('');
  const session = state.handshake?.client_session_id ?? '';
  const leaf = resolveSetting(setting);
  if (!leaf) return <Navigate to="/settings" replace />;
  return (
    <section
      className="route-surface"
      aria-label={leaf?.label ?? 'Unknown setting'}
    >
      {leaf?.id === 'buddy' ? (
        <>
          <h1>Buddy</h1>
          <BuddySurface settings />
        </>
      ) : leaf?.id === 'preferences' ? (
        <>
          <h1>Preferences</h1>
          <Preferences />
        </>
      ) : leaf.id === 'providers' ? (
        <>
          <ProviderStatus
            key={session}
            load={controller.providerStatus}
            owner={providerSettingsSessions}
          />
          {subscriptionAccountsOwner?.get() && (
            <SubscriptionAccounts
              session={subscriptionAccountsOwner.get()}
              load={controller.subscriptionAccounts}
              review={controller.reviewSubscriptionAction}
              apply={controller.applySubscriptionAction}
              readFlow={controller.subscriptionFlow}
              cancel={controller.cancelSubscriptionFlow}
              cancelStart={controller.cancelSubscriptionStart}
              receipt={controller.subscriptionReceipt}
              onSaved={() => {}}
            />
          )}
          {subscriptionOptionsOwner?.get() && (
            <SubscriptionOptions
              session={subscriptionOptionsOwner.get()}
              load={controller.subscriptionOptions}
              review={controller.reviewSubscriptionOptions}
              apply={controller.applySubscriptionOptions}
              receipt={controller.subscriptionOptionsReceipt}
              onSaved={() => {}}
            />
          )}
          {subscriptionProbesOwner?.get() && (
            <SubscriptionProbes
              session={subscriptionProbesOwner.get()}
              load={controller.subscriptionProbes}
              review={controller.reviewSubscriptionProbe}
              apply={controller.applySubscriptionProbe}
              status={controller.subscriptionProbeStatus}
              cancel={controller.cancelSubscriptionProbe}
              receipt={controller.subscriptionProbeReceipt}
              onSaved={() => {}}
              onBrowseModels={() => navigate('/settings/models')}
            />
          )}
          {defaultModelOwner?.get() && (
            <DefaultModelSettings
              session={defaultModelOwner.get()}
              load={controller.defaultModel}
              review={(settings_revision, provider_id, model_id, signal) =>
                controller.reviewDefaultModel(
                  { settings_revision, provider_id, model_id },
                  signal,
                )
              }
              apply={controller.executeDefaultModel}
              receipt={controller.defaultModelReceipt}
              onSaved={() => {}}
              onBrowseModels={() => navigate('/settings/models')}
            />
          )}
          {providerConfigurationOwner?.get() && (
            <ProviderConfiguration
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
                (await controller.providerConfigurationReceipt(command, signal))
                  .status
              }
              onSaved={() => {}}
              onCredentials={() =>
                document
                  .getElementById('provider-saved-status')
                  ?.scrollIntoView()
              }
            />
          )}
        </>
      ) : leaf.id === 'models' ? (
        <ModelCatalog
          key={`${session}:${search.get('provider') ?? ''}`}
          initialProvider={search.get('provider') ?? ''}
          load={controller.cachedModels}
          loadProviders={controller.providerStatus}
          onChooseDefault={
            defaultModelOwner
              ? async (model) => {
                  const editor = defaultModelOwner.get();
                  if (
                    !editor?.active ||
                    editor.get('busy', '') ||
                    editor.get('pending', null)
                  )
                    throw { code: 'operation_uncertain' };
                  if (!editor.get('snapshot', null)) {
                    const snapshot = await controller.defaultModel();
                    if (!editor.active || defaultModelOwner.get() !== editor)
                      throw { code: 'session_expired' };
                    editor.set('snapshot', snapshot);
                  }
                  editor.set('provider', model.provider_id);
                  editor.set('model', model.model_id);
                  editor.set('dirty', true);
                  editor.set('reviewed', null);
                  navigate('/settings/providers');
                }
              : undefined
          }
        />
      ) : leaf.id === 'mcp' && capabilitySettingsOwner?.get() ? (
        <>
          <RuntimeInstallations />
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
        </>
      ) : leaf.id === 'tools' ? (
        <ToolCatalog key={session} load={controller.cachedTools} />
      ) : leaf.id === 'knowledge' ? (
        <>
          <KnowledgeCatalog
            key={session}
            load={controller.savedEntities}
            onOpen={(id) => knowledgeOwner?.get()?.open(id)}
          />
          {knowledgeOwner?.get() && (
            <KnowledgeEditors owner={knowledgeOwner.get()!} />
          )}
        </>
      ) : leaf.id === 'wiki' && wikiOwner?.get() ? (
        <WikiSettings session={wikiOwner.get()!} />
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
        state.selectedConversationId ? (
          <GoalProfileSettings
            conversationId={state.selectedConversationId}
            session={goalProfileOwner.get()!}
            loadGoals={({ conversation_id, query, cursor }, signal) =>
              controller.goals(conversation_id, query, cursor, signal)
            }
            loadProfiles={({ query, scope, cursor }, signal) =>
              controller.profiles(query, scope, cursor, signal)
            }
            loadProfile={controller.profile}
            reviewGoal={(payload, signal) =>
              controller.reviewGoal(
                state.selectedConversationId!,
                payload,
                signal,
              )
            }
            executeGoal={(command, review) =>
              controller.executeGoal(state.selectedConversationId!, {
                ...command,
                payload: { ...command.payload, review_id: review.review_id },
              })
            }
            reviewProfile={controller.reviewProfile}
            executeProfile={(command, review) =>
              controller.executeProfile({
                ...command,
                payload: { ...command.payload, review_id: review.review_id },
              })
            }
          />
        ) : (
          <EmptyState title="Open a conversation">
            Goals belong to one conversation. Open or create a conversation,
            then return here. Agent Profiles remain available from the Goals
            view.
          </EmptyState>
        )
      ) : leaf.id === 'documents' ? (
        <>
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
            <DocumentRemovalsPanel owner={documentRemovalsOwner.get()!} />
          )}
        </>
      ) : ['voice', 'accounts', 'tracker', 'utilities', 'system'].includes(
          leaf.id,
        ) ? (
        <Phase4RetainedSettings setting={leaf.id as Phase4RetainedSetting} />
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
  );
}
