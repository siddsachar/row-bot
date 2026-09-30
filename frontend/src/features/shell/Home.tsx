import { useCallback, useEffect, useRef, useState } from 'react';
import type { AttentionProblem } from '../../api/types';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  Activity,
  Brain,
  GitBranch,
  LayoutGrid,
  Lightbulb,
} from 'lucide-react';
import { clientError } from '../../api/errors';
import type {
  KnowledgeGraphSnapshot,
  MonitorSnapshot,
  OnboardingSnapshot,
  TaskSummaryPage,
} from '../../api/types';
import { useClientState, useRuntime } from '../../runtime';
import { Tabs } from '../../ui/primitives';
import { useOverlay } from '../../ui/overlays';
import TaskLibrary from '../tasks/TaskLibrary';
import KnowledgeHome, { type KnowledgeDreamState } from '../home/KnowledgeHome';
import MonitorHome from '../home/MonitorHome';
import InsightsHome from '../home/InsightsHome';
import OverviewHome from '../home/OverviewHome';
import { setupDeferred } from './FirstRun';
import KnowledgeEditorDialog from '../knowledge/KnowledgeEditorDialog';

const homeTabs = ['overview', 'workflows', 'knowledge', 'monitor', 'insights'];
/** Snapshots read in the last few seconds are reused when switching tabs. */
const REUSE_MS = 20_000;
// The knowledge graph opens with every memory up to 2,000 (a phone draws that
// in well under a second) and "Show all" reads up to the server's 5,000 (B251).
const GRAPH_DEFAULT_LIMIT = 2000;
const GRAPH_ALL_LIMIT = 5000;

export default function Home() {
  const state = useClientState();
  const { controller, knowledgeOwner, platform } = useRuntime();
  const overlay = useOverlay();
  const navigate = useNavigate();
  const [search, setSearch] = useSearchParams();
  const requestedTab = search.get('tab')?.toLowerCase() ?? '';
  const tab = homeTabs.includes(requestedTab) ? requestedTab : 'overview';
  const [knowledge, setKnowledge] = useState<KnowledgeGraphSnapshot | null>(
    null,
  );
  const [knowledgeLoading, setKnowledgeLoading] = useState(false);
  const [knowledgeError, setKnowledgeError] = useState('');
  const [knowledgeReload, setKnowledgeReload] = useState(0);
  const [graphLimit, setGraphLimit] = useState(GRAPH_DEFAULT_LIMIT);
  const knowledgeRead = useRef({ at: 0, key: '' });
  const [monitor, setMonitor] = useState<MonitorSnapshot | null>(null);
  const [monitorLoading, setMonitorLoading] = useState(false);
  const [monitorError, setMonitorError] = useState('');
  const [monitorReload, setMonitorReload] = useState(0);
  const [startupWarnings, setStartupWarnings] = useState<string[]>([]);
  const [attention, setAttention] = useState<AttentionProblem[]>([]);
  const monitorRead = useRef({ at: 0, key: '' });
  const [setup, setSetup] = useState<OnboardingSnapshot | null>(null);
  // Until a default model exists, opening Row-Bot opens Setup (decision 10).
  // Only the plain Home address does: a deep link, a Home tab or "Set up
  // later" always lands where it points, on phones and remote devices too.
  const gated = !requestedTab && !setupDeferred();
  const [gateChecked, setGateChecked] = useState(!gated);
  const [setupDismissError, setSetupDismissError] = useState('');
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
  const chooseTab = (next: string, extra: Record<string, string> = {}) =>
    setSearch({ tab: next, ...extra });
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
      (value) => {
        if (value) setSetup(value);
        if (value?.needs_model && gated) navigate('/setup', { replace: true });
        else setGateChecked(true);
      },
      () => {
        if (!abort.signal.aborted) setGateChecked(true);
      },
    );
    return () => abort.abort();
    // The gate is decided once per connection, from the address it opened on.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [controller, identity]);
  useEffect(() => {
    if (tab !== 'knowledge' || !identity) return;
    const key = `${identity}:${knowledgeReload}:${graphLimit}`;
    if (
      knowledgeRead.current.key === key &&
      Date.now() - knowledgeRead.current.at < REUSE_MS
    )
      return;
    const abort = new AbortController();
    setKnowledgeLoading(true);
    setKnowledgeError('');
    controller.knowledgeGraph(graphLimit, abort.signal).then(
      (value) => {
        knowledgeRead.current = { at: Date.now(), key };
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
  }, [controller, graphLimit, identity, knowledgeReload, tab]);
  useEffect(() => {
    if (!['overview', 'monitor', 'knowledge'].includes(tab) || !identity)
      return;
    const key = `${identity}:${monitorReload}`;
    if (
      monitorRead.current.key === key &&
      Date.now() - monitorRead.current.at < REUSE_MS
    )
      return;
    const abort = new AbortController();
    setMonitorLoading(true);
    setMonitorError('');
    // Start-up warnings are listed in Monitor as well as shown once.
    controller.notices(abort.signal).then(
      (page) => {
        if (!abort.signal.aborted) setStartupWarnings(page.startup_warnings);
      },
      () => undefined,
    );
    // What the sidebar's indicator counts is listed first (rows 12, 13).
    controller.attention(abort.signal).then(
      (value) => {
        if (!abort.signal.aborted) setAttention(value.problems);
      },
      () => undefined,
    );
    controller.monitorSnapshot(abort.signal).then(
      (value) => {
        monitorRead.current = { at: Date.now(), key };
        setMonitor(value);
        // A refresh updates availability; the outcome of a run started
        // here stays until the next run.
        setDreamState((current) => ({
          ...current,
          available: value.dream.availability === 'available',
          enabled: value.dream.enabled,
        }));
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
  const loadLogs = useCallback(
    (signal?: AbortSignal) => controller.monitorLogs(200, signal),
    [controller],
  );
  const refreshConversation = useCallback(
    (id: string, signal: AbortSignal) =>
      controller.refreshListedConversation(id, signal),
    [controller],
  );
  // One workflow read shared by Overview and Monitor for a short while.
  const tasksRead = useRef<{
    at: number;
    key: string;
    value: Promise<TaskSummaryPage>;
  } | null>(null);
  const loadTasks = useCallback(
    // Shared by several readers, so one leaving never aborts the read.
    () => {
      const key = identity ?? '';
      const cached = tasksRead.current;
      if (cached && cached.key === key && Date.now() - cached.at < REUSE_MS)
        return cached.value;
      const value = controller.savedTasks();
      tasksRead.current = { at: Date.now(), key, value };
      value.catch(() => {
        if (tasksRead.current?.value === value) tasksRead.current = null;
      });
      return value;
    },
    [controller, identity],
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

  const openConversation = (id: string) => {
    void controller.selectConversation(id);
    navigate(`/conversations/${encodeURIComponent(id)}`);
  };
  /** Open the memory in the editor, then its relations and replacement. */
  const mergeMemory = (id: string) => {
    const owner = knowledgeOwner?.get();
    if (!owner) return;
    owner.open(id);
    let tries = 0;
    const attempt = () => {
      try {
        owner.openRelations();
      } catch {
        return;
      }
      if (!owner.relations() && tries++ < 25) window.setTimeout(attempt, 200);
    };
    attempt();
  };
  /** Review one memory's deletion, confirm it, then delete it. */
  const deleteMemory = async (id: string, subject: string) => {
    let review;
    try {
      const [catalog, detail] = await Promise.all([
        controller.savedEntities(),
        controller.knowledgeEntityDetail(id),
      ]);
      review = await controller.reviewKnowledgeMaintenance({
        action: 'knowledge.delete',
        catalog_revision: catalog.revision,
        targets: [{ entity_id: id, revision: detail.revision }],
      });
    } catch (cause) {
      overlay.notify(
        `Could not prepare the deletion: ${clientError(cause).message}`,
      );
      return false;
    }
    return new Promise<boolean>((resolve) => {
      overlay.open({
        kind: 'alert',
        title: `Delete '${subject}'?`,
        description:
          'This permanently removes the memory, its connections and its search entries. It cannot be undone.',
        confirmLabel: 'Delete memory',
        onConfirm: () => {
          void controller
            .executeKnowledgeMaintenance({
              command_id: crypto.randomUUID(),
              type: review.action,
              payload: {
                catalog_revision: review.catalog_revision,
                targets: review.targets,
                action_digest: review.action_digest,
                review_id: review.review_id,
              },
            })
            .then(
              (receipt) => {
                const done = receipt.status === 'completed';
                overlay.notify(
                  done
                    ? `${subject} deleted.`
                    : 'The deletion did not complete. Nothing else changed.',
                );
                if (done) setKnowledgeReload((value) => value + 1);
                resolve(done);
              },
              (cause: unknown) => {
                overlay.notify(clientError(cause).message);
                resolve(false);
              },
            );
        },
      });
    });
  };
  // Wait for the one onboarding read only while connected; a disconnected
  // Home still shows its connection state.
  if (identity && !gateChecked)
    return <div className="home-gate" aria-busy="true" />;
  return (
    <div className="home-view" data-home-tab={tab}>
      <h1 className="visually-hidden">Home</h1>
      {!identity && (
        <p role="status" className="home-connection-status">
          {state.status === 'loading' || state.status === 'reconnecting'
            ? 'Connecting to your workspace…'
            : 'Connect to open your workflows.'}
        </p>
      )}
      <Tabs
        className="home-tabs"
        label="Home capabilities"
        value={tab}
        onChange={(next) => chooseTab(next)}
        items={[
          {
            id: 'overview',
            label: (
              <>
                <LayoutGrid size={16} aria-hidden />
                Overview
              </>
            ),
            content: (
              <OverviewHome
                conversations={state.conversations}
                setup={setup}
                monitor={monitor}
                loadTasks={identity ? loadTasks : undefined}
                refreshKey={identity ?? ''}
                onOpenConversation={openConversation}
                onOpenWorkflows={(taskId) =>
                  chooseTab('workflows', taskId ? { workflow: taskId } : {})
                }
                onOpenTab={(next) => chooseTab(next)}
                refreshConversation={refreshConversation}
                onResumeAgentWork={async (row) => {
                  await controller.intent(
                    row.id,
                    'agent.resume',
                    {},
                    row.revision,
                  );
                }}
                onDismissAgentWork={async (row) => {
                  await controller.intent(
                    row.id,
                    'agent.dismiss',
                    {},
                    row.revision,
                  );
                }}
                onHideSetup={() => void dismissSetupReminder()}
                setupError={setupDismissError}
              />
            ),
          },
          {
            id: 'workflows',
            label: (
              <>
                <GitBranch size={16} aria-hidden />
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
                <Brain size={16} aria-hidden />
                Knowledge
              </>
            ),
            content: (
              <KnowledgeHome
                snapshot={knowledge}
                loading={knowledgeLoading}
                error={knowledgeError}
                reload={() => setKnowledgeReload((value) => value + 1)}
                showingAll={graphLimit > GRAPH_DEFAULT_LIMIT}
                onShowAll={() => setGraphLimit(GRAPH_ALL_LIMIT)}
                loadDetail={loadKnowledgeDetail}
                onEdit={(id) => knowledgeOwner?.get()?.open(id)}
                onAdd={
                  knowledgeOwner?.get()
                    ? () => knowledgeOwner.get()?.open(null)
                    : undefined
                }
                onMerge={mergeMemory}
                onDelete={deleteMemory}
                onOpenConversation={openConversation}
                dream={dreamState}
                dreamLastRun={monitor?.dream.last_run ?? null}
                onDream={dream}
              />
            ),
          },
          {
            id: 'monitor',
            label: (
              <>
                <Activity size={16} aria-hidden />
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
                startupWarnings={startupWarnings}
                attention={attention}
                loadLogs={loadLogs}
                loadTasks={identity ? loadTasks : undefined}
              />
            ),
          },
          {
            id: 'insights',
            label: (
              <>
                <Lightbulb size={16} aria-hidden />
                Insights
              </>
            ),
            content: (
              <InsightsHome
                controller={controller}
                writeClipboard={platform.writeClipboard}
                openConversation={openConversation}
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
