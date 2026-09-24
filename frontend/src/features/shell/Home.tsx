import { useCallback, useEffect, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import {
  Activity,
  Brain,
  GitBranch,
  Lightbulb,
  MessageSquare,
  X,
} from 'lucide-react';
import { clientError } from '../../api/errors';
import type {
  KnowledgeGraphSnapshot,
  MonitorLogEntry,
  MonitorSnapshot,
  OnboardingSnapshot,
} from '../../api/types';
import { useClientState, useRuntime } from '../../runtime';
import { Button, CompactAction, Tabs } from '../../ui/primitives';
import { useOverlay } from '../../ui/overlays';
import TaskLibrary from '../tasks/TaskLibrary';
import KnowledgeHome, { type KnowledgeDreamState } from '../home/KnowledgeHome';
import MonitorHome from '../home/MonitorHome';
import InsightsHome from '../home/InsightsHome';
import KnowledgeEditorDialog from '../knowledge/KnowledgeEditorDialog';
import { EXAMPLE_LABELS, EXAMPLE_PROMPTS } from './welcome-prompts';

const homeTabs = ['workflows', 'knowledge', 'monitor', 'insights'];

export default function Home({
  onExamplePrompt,
  exampleBusy = false,
}: {
  onExamplePrompt?: (prompt: string) => void;
  exampleBusy?: boolean;
}) {
  const state = useClientState();
  const { controller, knowledgeOwner, platform } = useRuntime();
  const overlay = useOverlay();
  const navigate = useNavigate();
  const [search, setSearch] = useSearchParams();
  const requestedTab = search.get('tab')?.toLowerCase() ?? '';
  const [tab, setTab] = useState(
    homeTabs.includes(requestedTab) ? requestedTab : 'workflows',
  );
  useEffect(() => {
    setTab(homeTabs.includes(requestedTab) ? requestedTab : 'workflows');
  }, [requestedTab]);
  const [knowledge, setKnowledge] = useState<KnowledgeGraphSnapshot | null>(
    null,
  );
  const [knowledgeLoading, setKnowledgeLoading] = useState(false);
  const [knowledgeError, setKnowledgeError] = useState('');
  const [knowledgeReload, setKnowledgeReload] = useState(0);
  const [monitor, setMonitor] = useState<MonitorSnapshot | null>(null);
  const [monitorLoading, setMonitorLoading] = useState(false);
  const [monitorError, setMonitorError] = useState('');
  const [monitorReload, setMonitorReload] = useState(0);
  const [setup, setSetup] = useState<OnboardingSnapshot | null>(null);
  const [setupDismissError, setSetupDismissError] = useState('');
  const [fullLogsOpen, setFullLogsOpen] = useState(false);
  const [fullLogsLoading, setFullLogsLoading] = useState(false);
  const [fullLogsError, setFullLogsError] = useState('');
  const [fullLogs, setFullLogs] = useState<MonitorLogEntry[]>([]);
  const [dreamState, setDreamState] = useState<KnowledgeDreamState>({
    available: false,
    enabled: false,
    state: 'idle',
    message: '',
  });
  const identity =
    state.status === 'ready' && state.handshake
      ? `${state.handshake.instance_id}:${state.handshake.client_session_id}`
      : null;
  const chooseTab = (next: string) => {
    setTab(next);
    setSearch({ tab: next });
  };
  async function dismissSetupReminder() {
    if (!setup?.setup_complete) return;
    try {
      const receipt = await controller.onboardingCommand({
        command_id: crypto.randomUUID(),
        expected_revision: setup.revision,
        action: 'dismiss_home',
        profile: [],
        step: '',
      });
      setSetup(receipt.snapshot);
      setSetupDismissError('');
    } catch {
      setSetupDismissError(
        'Could not hide the setup reminder. Refresh this page and try again.',
      );
    }
  }
  useEffect(() => {
    if (!identity) return;
    const abort = new AbortController();
    controller.onboarding(abort.signal).then(
      (value) => setSetup(value),
      () => {},
    );
    return () => abort.abort();
  }, [controller, identity]);
  useEffect(() => {
    if (tab !== 'knowledge' || !identity) return;
    const abort = new AbortController();
    setKnowledgeLoading(true);
    setKnowledgeError('');
    controller.knowledgeGraph(250, abort.signal).then(
      (value) => {
        setKnowledge(value);
        setKnowledgeLoading(false);
      },
      (cause: unknown) => {
        if (abort.signal.aborted) return;
        setKnowledgeError(clientError(cause).message);
        setKnowledgeLoading(false);
      },
    );
    return () => abort.abort();
  }, [controller, identity, knowledgeReload, tab]);
  useEffect(() => {
    if ((tab !== 'monitor' && tab !== 'knowledge') || !identity) return;
    const abort = new AbortController();
    setMonitorLoading(true);
    setMonitorError('');
    controller.monitorSnapshot(abort.signal).then(
      (value) => {
        setMonitor(value);
        setDreamState({
          available: value.dream.availability === 'available',
          enabled: value.dream.enabled,
          state: 'idle',
          message: '',
        });
        setMonitorLoading(false);
      },
      (cause: unknown) => {
        if (abort.signal.aborted) return;
        setMonitorError(clientError(cause).message);
        setMonitorLoading(false);
      },
    );
    return () => abort.abort();
  }, [controller, identity, monitorReload, tab]);

  const loadKnowledgeDetail = useCallback(
    (id: string) => controller.knowledgeEntityDetail(id),
    [controller],
  );

  async function dream() {
    if (!monitor) return;
    setDreamState((value) => ({
      ...value,
      state: 'reviewing',
      message: 'Checking the current Dream Cycle effects…',
    }));
    let review;
    try {
      review = await controller.reviewDreamRun({
        snapshot_revision: monitor.dream_revision,
      });
    } catch (cause) {
      setDreamState((value) => ({
        ...value,
        state: 'error',
        message: clientError(cause).message,
      }));
      return;
    }
    overlay.open({
      kind: 'alert',
      title: 'Run Dream Cycle now?',
      description:
        'This can merge and enrich saved knowledge and add inferred connections. The reviewed effects are bound to the current snapshot.',
      confirmLabel: 'Run Dream Cycle',
      onConfirm: () => {
        setDreamState((value) => ({
          ...value,
          state: 'running',
          message: 'Dream Cycle is running…',
        }));
        const commandId = crypto.randomUUID();
        void controller
          .executeDreamRun({
            command_id: commandId,
            client_session_id: state.handshake!.client_session_id,
            type: 'dream.run',
            payload: {
              snapshot_revision: review.snapshot_revision,
              action_digest: review.action_digest,
              review_id: review.review_id,
            },
          })
          .then(
            (receipt) => {
              setDreamState((value) => ({
                ...value,
                state: receipt.status === 'completed' ? 'success' : 'error',
                message:
                  receipt.summary ||
                  (receipt.status === 'completed'
                    ? 'Dream Cycle completed.'
                    : 'Dream Cycle completed with errors.'),
              }));
              setKnowledgeReload((value) => value + 1);
              setMonitorReload((value) => value + 1);
            },
            (cause: unknown) => {
              setDreamState((value) => ({
                ...value,
                state: 'error',
                message: clientError(cause).message,
              }));
            },
          );
      },
    });
    setDreamState((value) => ({ ...value, state: 'idle', message: '' }));
  }

  async function loadFullLogs() {
    setFullLogsOpen(true);
    setFullLogsLoading(true);
    setFullLogsError('');
    try {
      const value = await controller.monitorLogs(200);
      setFullLogs(value.entries);
    } catch (cause) {
      setFullLogsError(clientError(cause).message);
    } finally {
      setFullLogsLoading(false);
    }
  }
  return (
    <div className="home-view">
      <h1 className="visually-hidden">Home</h1>
      {identity ? (
        <p role="status" className="home-connection-status">
          Connected · local workspace
        </p>
      ) : (
        <p role="status" className="home-connection-status">
          {state.status === 'loading' || state.status === 'reconnecting'
            ? 'Connecting to your workspace…'
            : 'Connect to open your workflows.'}
        </p>
      )}
      {identity && onExamplePrompt && (
        <section className="home-start" aria-label="Start working">
          <div>
            <span className="eyebrow">Your workspace</span>
            <h2>What would you like to work on?</h2>
            <p>
              Start with a message. You can add a code folder or design when you
              need one.
            </p>
          </div>
          <Button
            variant="primary"
            disabled={exampleBusy}
            onClick={() => onExamplePrompt('')}
          >
            Start a chat
          </Button>
        </section>
      )}
      {identity && state.conversations.length > 0 && (
        <section className="home-recent" aria-label="Recent conversations">
          <h2>Pick up where you left off</h2>
          <div className="home-recent-list">
            {state.conversations.slice(0, 3).map((conversation) => (
              <Button
                key={conversation.id}
                variant="ghost"
                onClick={() => {
                  void controller.selectConversation(conversation.id);
                  navigate(`/conversations/${conversation.id}`);
                }}
              >
                <MessageSquare size={16} aria-hidden />
                <span>{conversation.title || 'Untitled conversation'}</span>
              </Button>
            ))}
          </div>
        </section>
      )}
      {setup &&
        (!setup.setup_complete ||
          (!setup.dismissed_home_card &&
            new Set([...setup.completed_steps, ...setup.skipped_steps]).size <
              setup.steps.length)) &&
        (setup.setup_complete ? (
          <section className="home-setup-reminder" aria-label="Continue setup">
            <strong>Setup</strong>
            <span>
              {new Set([...setup.completed_steps, ...setup.skipped_steps]).size}{' '}
              of {setup.steps.length} areas complete
            </span>
            <Link className="button primary" to="/setup">
              Continue setup
            </Link>
            <CompactAction
              label="Hide setup reminder"
              onClick={() => void dismissSetupReminder()}
            >
              <X size={17} aria-hidden />
            </CompactAction>
            {setupDismissError && <p role="status">{setupDismissError}</p>}
          </section>
        ) : (
          <section
            className="capability-section stack"
            aria-label="Continue setup"
          >
            <h2>Welcome to Row-Bot</h2>
            <p>Connect one working model first. Your other choices can wait.</p>
            <Link className="button primary" to="/setup">
              Open Setup Center
            </Link>
          </section>
        ))}
      {identity && setup?.setup_complete && onExamplePrompt && (
        <section className="home-examples" aria-label="Start with an example">
          <h2>Try an example</h2>
          <div className="actions">
            {EXAMPLE_PROMPTS.slice(0, 3).map((prompt, index) => (
              <Button
                key={prompt}
                disabled={exampleBusy}
                onClick={() => onExamplePrompt(prompt)}
              >
                {EXAMPLE_LABELS[index]}
              </Button>
            ))}
          </div>
          <details>
            <summary>More ideas</summary>
            <div className="actions">
              {EXAMPLE_PROMPTS.slice(3).map((prompt, index) => (
                <Button
                  key={prompt}
                  disabled={exampleBusy}
                  onClick={() => onExamplePrompt(prompt)}
                >
                  {EXAMPLE_LABELS[index + 3]}
                </Button>
              ))}
            </div>
          </details>
        </section>
      )}
      <Tabs
        label="Home capabilities"
        value={tab}
        onChange={chooseTab}
        items={[
          {
            id: 'workflows',
            label: (
              <>
                <GitBranch size={17} aria-hidden />
                Workflows
              </>
            ),
            content: (
              <div className="home-workflows">
                <TaskLibrary />
              </div>
            ),
          },
          {
            id: 'knowledge',
            label: (
              <>
                <Brain size={17} aria-hidden />
                Knowledge
              </>
            ),
            content: (
              <KnowledgeHome
                snapshot={knowledge}
                loading={knowledgeLoading}
                error={knowledgeError}
                reload={() => setKnowledgeReload((value) => value + 1)}
                loadDetail={loadKnowledgeDetail}
                onEdit={(id) => knowledgeOwner?.get()?.open(id)}
                dream={dreamState}
                onDream={dream}
              />
            ),
          },
          {
            id: 'monitor',
            label: (
              <>
                <Activity size={17} aria-hidden />
                Monitor
              </>
            ),
            content: (
              <MonitorHome
                writeClipboard={platform.writeClipboard}
                snapshot={monitor}
                loading={monitorLoading}
                error={monitorError}
                onRefresh={() => setMonitorReload((value) => value + 1)}
                onRunDiagnosis={() => controller.systemDiagnosis()}
                onLoadFullLogs={() => void loadFullLogs()}
                fullLogsOpen={fullLogsOpen}
                fullLogsLoading={fullLogsLoading}
                fullLogsError={fullLogsError}
                fullLogEntries={fullLogs}
                onCloseFullLogs={() => setFullLogsOpen(false)}
              />
            ),
          },
          {
            id: 'insights',
            label: (
              <>
                <Lightbulb size={17} aria-hidden />
                Insights
              </>
            ),
            content: (
              <InsightsHome
                controller={controller}
                writeClipboard={platform.writeClipboard}
                openConversation={(id) => {
                  void controller.selectConversation(id);
                  navigate(`/conversations/${id}`);
                }}
              />
            ),
          },
        ]}
      />
      {knowledgeOwner?.get() && (
        <KnowledgeEditorDialog
          owner={knowledgeOwner.get()!}
          onMutation={() => setKnowledgeReload((value) => value + 1)}
        />
      )}
    </div>
  );
}
