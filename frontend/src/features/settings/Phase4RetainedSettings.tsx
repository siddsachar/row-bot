import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useClientState, useRuntime } from '../../runtime';
import SubscriptionAccounts from './SubscriptionAccounts';
import SubscriptionOptions from './SubscriptionOptions';
import SubscriptionProbes from './SubscriptionProbes';

export type Phase4RetainedSetting =
  'voice' | 'accounts' | 'tracker' | 'utilities' | 'system';

type CapabilityState = 'checking' | 'available' | 'current_app' | 'unsupported';

const labels: Record<CapabilityState, string> = {
  checking: 'Checking',
  available: 'Available here',
  current_app: 'Current application',
  unsupported: 'Unsupported in this client',
};

const utilityIds = [
  'task',
  'timer',
  'url_reader',
  'calculator',
  'weather',
  'chart',
  'system_info',
  'conversation_search',
  'custom_tool_builder',
] as const;

function CapabilityCard({
  state,
  title,
  children,
}: {
  state: CapabilityState;
  title: string;
  children: ReactNode;
}) {
  return (
    <li className="surface" data-capability-state={state}>
      <p className="eyebrow">{labels[state]}</p>
      <h2>{title}</h2>
      {children}
    </li>
  );
}

function CurrentApplicationLink({ label }: { label: string }) {
  return (
    <a className="button" href="/">
      {label}
    </a>
  );
}

function VoiceSurface({
  selectedConversationId,
  state,
}: {
  selectedConversationId: string | null;
  state: 'checking' | 'available' | 'unavailable';
}) {
  return (
    <>
      <h2>Voice capabilities</h2>
      <p>
        Voice sessions stay attached to one conversation. Opening this page
        never starts a microphone, provider session, or audio output.
      </p>
      <ul className="settings-results">
        <CapabilityCard
          state={
            state === 'checking'
              ? 'checking'
              : state === 'available'
                ? 'available'
                : 'unsupported'
          }
          title="Conversation voice"
        >
          <p>
            {state === 'checking'
              ? 'Checking the local browser voice capability.'
              : state === 'available'
                ? 'Talk, Realtime Talk, and dictation are available from the conversation composer. Provider and microphone readiness are checked again when used.'
                : 'This browser host does not currently expose local dictation, so conversation voice controls remain unavailable.'}
          </p>
          <Link
            className="button"
            to={
              selectedConversationId
                ? `/conversations/${selectedConversationId}`
                : '/'
            }
          >
            {selectedConversationId
              ? 'Open conversation voice'
              : 'Open or create a conversation'}
          </Link>
        </CapabilityCard>
        <CapabilityCard state="current_app" title="Voice configuration">
          <p>
            Speech providers, synthesis voices, wake phrase settings, and
            operating-system audio choices remain in the current application.
          </p>
          <CurrentApplicationLink label="Open current Voice settings" />
        </CapabilityCard>
        <CapabilityCard
          state="unsupported"
          title="No background voice configuration here"
        >
          <p>
            This client does not change persistent voice configuration or
            request microphone permission from Settings.
          </p>
        </CapabilityCard>
      </ul>
    </>
  );
}

function AccountsSurface() {
  const {
    controller,
    subscriptionAccountsOwner,
    subscriptionOptionsOwner,
    subscriptionProbesOwner,
  } = useRuntime();
  const navigate = useNavigate();
  const accounts = subscriptionAccountsOwner?.get();
  const options = subscriptionOptionsOwner?.get();
  const probes = subscriptionProbesOwner?.get();
  const hasSubscriptionOwner = Boolean(accounts || options || probes);

  return (
    <>
      <h2>Account connections</h2>
      <p>
        Account status is read from saved local metadata. A provider is
        contacted only after an explicit reviewed sign-in or check.
      </p>
      <ul className="settings-results">
        <CapabilityCard
          state={hasSubscriptionOwner ? 'available' : 'unsupported'}
          title="Provider subscription accounts"
        >
          <p>
            {hasSubscriptionOwner
              ? 'Saved subscription status and reviewed sign-in controls are available below.'
              : 'The authenticated subscription account owner is unavailable in this session.'}
          </p>
        </CapabilityCard>
        <CapabilityCard state="current_app" title="Integration accounts">
          <p>
            Google, Gmail, Calendar, and other tool-specific account connections
            remain in the current application.
          </p>
          <CurrentApplicationLink label="Open current Accounts settings" />
        </CapabilityCard>
        <CapabilityCard
          state="unsupported"
          title="No generic account connection"
        >
          <p>
            This client has no generic OAuth action. Only the typed subscription
            owners shown below can start a reviewed connection flow.
          </p>
        </CapabilityCard>
      </ul>
      {accounts && (
        <SubscriptionAccounts
          session={accounts}
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
      {options && (
        <SubscriptionOptions
          session={options}
          load={controller.subscriptionOptions}
          review={controller.reviewSubscriptionOptions}
          apply={controller.applySubscriptionOptions}
          receipt={controller.subscriptionOptionsReceipt}
          onSaved={() => {}}
        />
      )}
      {probes && (
        <SubscriptionProbes
          session={probes}
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
    </>
  );
}

function TrackerSurface({ available }: { available: boolean | undefined }) {
  return (
    <>
      <h2>Tracker capabilities</h2>
      <p>
        Tracker records are local data. A hosted provider sees tracker details
        only when a conversation or tool request explicitly includes them.
      </p>
      <ul className="settings-results">
        <CapabilityCard
          state={
            available === true
              ? 'available'
              : available === false
                ? 'current_app'
                : 'unsupported'
          }
          title="Tracker tool in conversations"
        >
          <p>
            {available === true
              ? 'The saved Tracker tool is enabled and can be used through the normal reviewed conversation flow.'
              : available === false
                ? 'The Tracker tool is installed but disabled. Enable it in the current application before using it in chat.'
                : 'The active capability catalogue does not contain a Tracker tool.'}
          </p>
          <Link className="button" to="/settings/tools">
            Browse saved tools
          </Link>
        </CapabilityCard>
        <CapabilityCard state="current_app" title="Tracker data and charts">
          <p>
            Category setup, raw records, charts, imports, and destructive data
            cleanup remain in the current application.
          </p>
          <CurrentApplicationLink label="Open current Tracker settings" />
        </CapabilityCard>
        <CapabilityCard
          state="unsupported"
          title="No direct tracker record editor"
        >
          <p>
            This client does not expose direct record mutation or bulk deletion
            from Settings.
          </p>
        </CapabilityCard>
      </ul>
    </>
  );
}

function UtilitiesSurface({
  enabled,
  known,
}: {
  enabled: number;
  known: number;
}) {
  return (
    <>
      <h2>Utility capabilities</h2>
      <p>
        Utility availability comes from the saved capability catalogue. Opening
        this page never refreshes a provider or runs a utility.
      </p>
      <ul className="settings-results">
        <CapabilityCard
          state={known ? 'available' : 'unsupported'}
          title="Saved utility catalogue"
        >
          <p>
            {known
              ? `${enabled} of ${known} detected utility tools are enabled for conversations.`
              : 'No built-in utility entries are present in the active capability catalogue.'}
          </p>
          <Link className="button" to="/settings/tools">
            Browse utility tools
          </Link>
        </CapabilityCard>
        <CapabilityCard state="current_app" title="Utility configuration">
          <p>
            Enabling utilities and changing their saved defaults remain in the
            current application.
          </p>
          <CurrentApplicationLink label="Open current Utilities settings" />
        </CapabilityCard>
        <CapabilityCard
          state="unsupported"
          title="No settings-side utility execution"
        >
          <p>
            Utilities run only through their normal conversation tool and
            approval boundaries; this Settings surface cannot invoke them.
          </p>
        </CapabilityCard>
      </ul>
    </>
  );
}

function SystemSurface({
  connected,
  localOwner,
  runtimeSetupAvailable,
}: {
  connected: boolean;
  localOwner: boolean;
  runtimeSetupAvailable: boolean;
}) {
  return (
    <>
      <h2>System boundaries</h2>
      <p>
        Host access controls are security-sensitive and stay with their current
        owner. This page does not broaden filesystem, shell, browser, network,
        or remote access.
      </p>
      <ul className="settings-results">
        <CapabilityCard
          state={connected ? 'available' : 'unsupported'}
          title="Client connection"
        >
          <p>
            {connected
              ? `The authenticated client is connected${localOwner ? ' as the local owner' : ''}.`
              : 'The authenticated client is not currently connected.'}
          </p>
          <Link className="button" to="/settings/preferences">
            Open client preferences
          </Link>
        </CapabilityCard>
        <CapabilityCard
          state={runtimeSetupAvailable ? 'available' : 'unsupported'}
          title="Typed runtime setup"
        >
          <p>
            {runtimeSetupAvailable
              ? 'Managed MCP runtime readiness and reviewed installation controls are available in the MCP settings surface.'
              : 'The authenticated managed-runtime owner is unavailable in this session.'}
          </p>
          {runtimeSetupAvailable && (
            <Link className="button" to="/settings/mcp">
              Open MCP runtime settings
            </Link>
          )}
        </CapabilityCard>
        <CapabilityCard state="current_app" title="Host access and diagnostics">
          <p>
            Workspace boundaries, shell and browser tools, Computer Use, Remote
            Access, tunnels, window mode, logs, and diagnostics remain in the
            current application.
          </p>
          <CurrentApplicationLink label="Open current System settings" />
        </CapabilityCard>
        <CapabilityCard
          state="unsupported"
          title="No duplicated host authority"
        >
          <p>
            This client does not mirror or infer privileged host configuration.
          </p>
        </CapabilityCard>
      </ul>
    </>
  );
}

export default function Phase4RetainedSettings({
  setting,
}: {
  setting: Phase4RetainedSetting;
}) {
  const { controller, capabilitySettingsOwner, runtimeInstallationsOwner } =
    useRuntime();
  const state = useClientState();
  const [voice, setVoice] = useState<'checking' | 'available' | 'unavailable'>(
    'checking',
  );
  const capability = useMemo(
    () =>
      new Map(
        (state.handshake?.capabilities ?? []).map((item) => [item.id, item]),
      ),
    [state.handshake?.capabilities],
  );

  useEffect(() => {
    if (setting !== 'voice') return;
    const abort = new AbortController();
    setVoice('checking');
    void controller.dictationCapability(abort.signal).then(
      (result) => {
        if (!abort.signal.aborted)
          setVoice(
            result.browser_dictation_available ? 'available' : 'unavailable',
          );
      },
      () => {
        if (!abort.signal.aborted) setVoice('unavailable');
      },
    );
    return () => abort.abort();
  }, [controller, setting, state.handshake?.server_epoch]);

  if (setting === 'accounts') return <AccountsSurface />;
  if (setting === 'voice')
    return (
      <VoiceSurface
        selectedConversationId={state.selectedConversationId}
        state={voice}
      />
    );
  if (setting === 'tracker')
    return <TrackerSurface available={capability.get('tracker')?.available} />;
  if (setting === 'utilities') {
    const detected = utilityIds.flatMap((id) => {
      const item = capability.get(id);
      return item ? [item] : [];
    });
    return (
      <UtilitiesSurface
        known={detected.length}
        enabled={detected.filter((item) => item.available).length}
      />
    );
  }
  return (
    <SystemSurface
      connected={state.status === 'ready' && state.connection !== 'none'}
      localOwner={state.handshake?.authentication_kind === 'local_owner'}
      runtimeSetupAvailable={Boolean(
        capabilitySettingsOwner?.get() && runtimeInstallationsOwner?.get(),
      )}
    />
  );
}
