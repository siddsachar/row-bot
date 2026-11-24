import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { AttentionProblem, PanelDescriptor } from '../../api/types';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { HomeBoard } from './surfaces/HomeBoard';
import {
  Activity,
  Brain,
  ChevronLeft,
  GitBranch,
  GraduationCap,
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
import { useLiveUpdate } from '../../live';
import { Tabs } from '../../ui/primitives';
import { useOverlay } from '../../ui/overlays';
import TaskLibrary from '../tasks/TaskLibrary';
import KnowledgeHome, {
  type KnowledgeDreamState,
  type KnowledgeMemoryQuery,
} from '../home/KnowledgeHome';
import type { KnowledgeLifecycleAction } from '../home/KnowledgeReview';
import MonitorHome from '../home/MonitorHome';
import type { BackgroundProcessesIo } from '../home/BackgroundProcesses';
import InsightsHome from '../home/InsightsHome';
import OverviewHome from '../home/OverviewHome';
import PhoneNotificationsOffer from '../home/PhoneNotificationsOffer';
import { BuddyPortrait } from '../buddy/BuddySurface';
import { AppLink } from '../../ui/app-link';
import { OnYourComputer, usePhone } from '../../ui/on-your-computer';
import '../../ui/styles/phone-home.css';
import ForYou from '../proactive/ForYou';
import ResourceSetup from './ResourceSetup';
import { setupDeferred } from './FirstRun';
import KnowledgeEditorDialog from '../knowledge/KnowledgeEditorDialog';
import MemoryImportDialog from '../knowledge/MemoryImportDialog';
import { memoryImportIO } from '../knowledge/memory-import-io';
import MemoryReviewDialog from '../knowledge/MemoryReviewDialog';
import { memoryReviewIO } from '../knowledge/memory-review-io';
import { neverUsed, type HomeVoice } from './new-chat';
import { useAttention } from './needs';
import LearningHome, {
  LEARNING_VIEWS,
  type LearningView,
} from '../learning/LearningHome';
import { learningIsOn, useLearning } from '../learning/learning-state';

const homeTabs = [
  'overview',
  'workflows',
  'knowledge',
  'monitor',
  'insights',
  'learning',
];
/** Snapshots read in the last few seconds are reused when switching tabs. */
const REUSE_MS = 20_000;
// The knowledge graph opens with every memory up to 2,000 (a phone draws that
// in well under a second) and "Show all" reads up to the server's 5,000 (B251).
const GRAPH_DEFAULT_LIMIT = 2000;
const GRAPH_ALL_LIMIT = 5000;
const NO_PROBLEMS: AttentionProblem[] = [];

export default function Home({
  onAsk,
  asking = false,
  onPanel,
}: {
  /** Start a new chat from Overview's Ask box (the shell's New chat owner). */
  onAsk?: (text: string, options?: { send: false; voice?: HomeVoice }) => void;
  /** A new chat is being made. */
  asking?: boolean;
  /** Show a resource's panel: a new design or code folder opens there. */
  onPanel?: (panel: PanelDescriptor, options?: { wide?: boolean }) => void;
} = {}) {
  const state = useClientState();
  const { controller, knowledgeOwner, platform, taskEditSessions } =
    useRuntime();
  // A chat started and never used has nothing to continue.
  const usedConversations = useMemo(
    () => state.conversations.filter((row) => !neverUsed(row)),
    [state.conversations],
  );
  const overlay = useOverlay();
  const navigate = useNavigate();
  // A phone has no Home sub-tabs (D2): each still opens from its address.
  const phone = usePhone();
  const processes = useMemo<BackgroundProcessesIo>(
    () => ({
      load: (conversation, signal) =>
        controller.backgroundProcesses(conversation, signal),
      output: (process, after, limit, signal) =>
        controller.backgroundProcessOutput(process, after, limit, signal),
      stop: (process) => controller.stopBackgroundProcess(process),
      openConversation: (conversation) =>
        navigate(`/conversations/${encodeURIComponent(conversation)}`),
      openLearning: () => navigate('/?tab=learning&view=model'),
    }),
    [controller, navigate],
  );
  const [search, setSearch] = useSearchParams();
  // Learning's tab shows only while Learning is on (D23).
  const learning = useLearning();
  const learningTab = learningIsOn(learning);
  const tabs = learningTab
    ? homeTabs
    : homeTabs.filter((item) => item !== 'learning');
  const requestedTab = search.get('tab')?.toLowerCase() ?? '';
  const tab = tabs.includes(requestedTab) ? requestedTab : 'overview';
  const requestedView = search.get('view') ?? '';
  const learningView: LearningView =
    LEARNING_VIEWS.find((item) => item.value === requestedView)?.value ??
    'overview';
  const [knowledge, setKnowledge] = useState<KnowledgeGraphSnapshot | null>(
    null,
  );
  const [knowledgeLoading, setKnowledgeLoading] = useState(false);
  const [knowledgeError, setKnowledgeError] = useState('');
  const [knowledgeReload, setKnowledgeReload] = useState(0);
  const [graphLimit, setGraphLimit] = useState(GRAPH_DEFAULT_LIMIT);
  const [importing, setImporting] = useState(false);
  const importIO = useMemo(() => memoryImportIO(controller), [controller]);
  // Review memory: the whole memory, or one import's (from its done step).
  const [reviewing, setReviewing] = useState<{ importBatch?: string } | null>(
    null,
  );
  const reviewIO = useMemo(() => memoryReviewIO(controller), [controller]);
  const knowledgeRead = useRef({ at: 0, key: '' });
  const [monitor, setMonitor] = useState<MonitorSnapshot | null>(null);
  const [monitorLoading, setMonitorLoading] = useState(false);
  const [monitorError, setMonitorError] = useState('');
  const [monitorReload, setMonitorReload] = useState(0);
  const [startupWarnings, setStartupWarnings] = useState<string[]>([]);
  const monitorRead = useRef({ at: 0, key: '' });
  // What Monitor lists as needing the person changed (a failed run, a
  // notice; B407): read it again at once, at most once a second, without the
  // loading state.
  const liveMonitor = useRef({ at: 0, timer: 0, quiet: false });
  useLiveUpdate(['monitor'], () => {
    const pending = liveMonitor.current;
    if (pending.timer) return;
    pending.timer = window.setTimeout(
      () => {
        pending.timer = 0;
        pending.at = Date.now();
        pending.quiet = true;
        setMonitorReload((value) => value + 1);
      },
      Math.max(0, pending.at + 1000 - Date.now()),
    );
  });
  useEffect(() => () => window.clearTimeout(liveMonitor.current.timer), []);
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
  // What the sidebar's indicator counts, listed first in Monitor (rows 12,
  // 13): the sidebar's own shared read, not a second one.
  const loadAttention = identity ? controller.attention : undefined;
  // Home's Ask box offers dictation and Talk where this host has them (D3, V7).
  const [voice, setVoice] = useState<{ dictate: boolean; talk: boolean }>();
  useEffect(() => {
    if (!identity || tab !== 'overview') return;
    const abort = new AbortController();
    controller.dictationCapability(abort.signal).then(
      (value) => {
        if (!abort.signal.aborted)
          setVoice({
            dictate: value.browser_dictation_available,
            talk: Boolean(value.talk_available || value.realtime_available),
          });
      },
      () => undefined,
    );
    return () => abort.abort();
  }, [controller, identity, tab]);
  const { value: attention, refresh: refreshAttention } =
    useAttention(loadAttention);
  const reloadMonitor = () => {
    setMonitorReload((value) => value + 1);
    refreshAttention();
  };
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
    // A phone's Home lists nothing from Monitor (its Today and Monitor stay on the computer).
    if (
      !['overview', 'monitor', 'knowledge'].includes(tab) ||
      !identity ||
      phone
    )
      return;
    const key = `${identity}:${monitorReload}`;
    if (
      monitorRead.current.key === key &&
      Date.now() - monitorRead.current.at < REUSE_MS
    )
      return;
    const abort = new AbortController();
    if (!liveMonitor.current.quiet) setMonitorLoading(true);
    liveMonitor.current.quiet = false;
    setMonitorError('');
    // Start-up warnings are listed in Monitor as well as shown once.
    controller.notices(abort.signal).then(
      (page) => {
        if (!abort.signal.aborted) setStartupWarnings(page.startup_warnings);
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
  }, [controller, identity, monitorReload, phone, tab]);

  const loadKnowledgeDetail = useCallback(
    (id: string) => controller.knowledgeEntityDetail(id),
    [controller],
  );
  // The whole saved library (search, the review queue), not only the map.
  const listMemories = useCallback(
    (query: KnowledgeMemoryQuery, cursor?: string, signal?: AbortSignal) =>
      controller.knowledgeEntities(
        query.query ?? '',
        undefined,
        query.status,
        undefined,
        undefined,
        cursor,
        signal,
      ),
    [controller],
  );
  const loadLogs = useCallback(
    (signal?: AbortSignal) => controller.monitorLogs(200, signal),
    [controller],
  );
  // The last result of every Monitor check, kept by the server (B252).
  const loadHealth = useCallback(
    (signal?: AbortSignal) => controller.systemHealth(signal),
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
  // A run started or ended: the next workflow read is fresh (B407).
  useLiveUpdate(['runs'], () => {
    tasksRead.current = null;
  });
  /** A new design or code folder: set up in a new chat, like its + menu. */
  const newResource = (kind: 'artifact' | 'workspace') =>
    onPanel &&
    overlay.open({
      title: kind === 'artifact' ? 'New design' : 'New code folder',
      description: 'Row-Bot opens it in a new chat.',
      content: (
        <ResourceSetup
          conversationId={null}
          onPanel={onPanel}
          initialEntry={{ kind, mode: 'create' }}
        />
      ),
    });

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
  /**
   * Review the deletion of one memory or up to 100 at their current
   * revisions, confirm it, then delete them.
   */
  const deleteMemories = async (
    memories: { id: string; subject: string }[],
  ) => {
    const one = memories.length === 1 ? memories[0] : null;
    let review;
    try {
      const [catalog, ...details] = await Promise.all([
        controller.savedEntities(),
        ...memories.map((memory) =>
          controller.knowledgeEntityDetail(memory.id),
        ),
      ]);
      review = await controller.reviewKnowledgeMaintenance({
        action: one ? 'knowledge.delete' : 'knowledge.delete.bulk',
        catalog_revision: catalog.revision,
        targets: memories.map((memory, index) => ({
          entity_id: memory.id,
          revision: details[index].revision,
        })),
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
        title: one
          ? `Delete '${one.subject}'?`
          : `Delete ${memories.length} memories?`,
        description: one
          ? 'This permanently removes the memory, its connections and its search entries. It cannot be undone.'
          : 'This permanently removes these memories, their connections and their search entries. It cannot be undone.',
        confirmLabel: one
          ? 'Delete memory'
          : `Delete ${memories.length} memories`,
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
                  !done
                    ? 'The deletion did not complete. Nothing else changed.'
                    : one
                      ? `${one.subject} deleted.`
                      : `${memories.length} memories deleted.`,
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
  /** Archive, restore or mark reviewed, reviewed at the revision shown. */
  const changeMemory = async (
    id: string,
    revision: string,
    action: KnowledgeLifecycleAction,
    subject: string,
  ) => {
    try {
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
    } catch (cause) {
      overlay.notify(clientError(cause).message, 'danger');
      return false;
    }
    overlay.notify(
      action === 'knowledge.archive'
        ? `${subject} archived.`
        : action === 'knowledge.restore'
          ? `${subject} restored.`
          : `${subject} marked as reviewed.`,
    );
    setKnowledgeReload((value) => value + 1);
    return true;
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
      {phone && tab === 'knowledge' && (
        // Your memories open from Settings › Memory on a phone (B4): the way back.
        <header className="phone-home-back">
          <AppLink to="/settings/knowledge" className="button ghost">
            <ChevronLeft size={20} aria-hidden />
            Memory
          </AppLink>
        </header>
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
                board={<HomeBoard order={1} />}
                conversations={usedConversations}
                setup={setup}
                monitor={monitor}
                loadTasks={identity ? loadTasks : undefined}
                loadHealth={identity ? loadHealth : undefined}
                onRunDiagnosis={() => controller.systemDiagnosis()}
                loadApprovals={
                  identity ? controller.pendingApprovals : undefined
                }
                loadAttention={loadAttention}
                loadScheduledPosts={
                  identity ? controller.scheduledPosts : undefined
                }
                onCancelScheduledPost={controller.cancelScheduledPost}
                onRetryScheduledPost={controller.retryScheduledPost}
                onEditScheduledPost={controller.editScheduledPost}
                buddy={<BuddyPortrait />}
                notificationsOffer={
                  identity
                    ? (waiting) => <PhoneNotificationsOffer waiting={waiting} />
                    : undefined
                }
                processes={identity ? processes : undefined}
                voice={voice}
                forYou={({ rows, ahead, footer }) => (
                  <ForYou today={rows} ahead={ahead} footer={footer} />
                )}
                onAsk={onAsk}
                asking={asking}
                onNewResource={onPanel && newResource}
                onNewWorkflow={
                  taskEditSessions
                    ? () => {
                        taskEditSessions.open('task');
                        chooseTab('workflows');
                      }
                    : undefined
                }
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
                onImport={() => setImporting(true)}
                onReview={() => setReviewing({})}
                onMerge={mergeMemory}
                onDelete={(id, subject) => deleteMemories([{ id, subject }])}
                onDeleteMany={deleteMemories}
                onLifecycle={changeMemory}
                listMemories={listMemories}
                loadRecalls={controller.knowledgeRecalls}
                loadChangeLog={controller.knowledgeChangeLog}
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
            content: phone ? (
              <OnYourComputer what="System Monitor" />
            ) : (
              <MonitorHome
                writeClipboard={platform.writeClipboard}
                snapshot={monitor}
                loading={monitorLoading}
                error={monitorError}
                onRefresh={reloadMonitor}
                loadHealth={identity ? loadHealth : undefined}
                onRunDiagnosis={() => controller.systemDiagnosis()}
                onSetHourlyChecks={(enabled) =>
                  controller.setHourlyConnectionChecks(enabled)
                }
                startupWarnings={startupWarnings}
                attention={attention?.problems ?? NO_PROBLEMS}
                onFixed={reloadMonitor}
                loadLogs={loadLogs}
                loadTasks={identity ? loadTasks : undefined}
                processes={identity ? processes : undefined}
                learning={learning}
                loadActivitySummary={
                  identity ? controller.activitySummary : undefined
                }
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
            content: phone ? (
              <OnYourComputer what="Insights" />
            ) : (
              <InsightsHome
                controller={controller}
                writeClipboard={platform.writeClipboard}
                openConversation={openConversation}
              />
            ),
          },
          ...(learning && learningTab
            ? [
                {
                  id: 'learning',
                  label: (
                    <>
                      <GraduationCap size={16} aria-hidden />
                      Learning
                    </>
                  ),
                  content: (
                    <LearningHome
                      io={learning}
                      view={learningView}
                      onViewChange={(view) =>
                        chooseTab(
                          'learning',
                          view === 'overview' ? {} : { view },
                        )
                      }
                      onOpenSettings={(anchor) =>
                        navigate(
                          `/settings/learning${anchor ? `#${anchor}` : ''}`,
                        )
                      }
                    />
                  ),
                },
              ]
            : []),
        ]}
      />
      {knowledgeOwner?.get() && (
        <KnowledgeEditorDialog
          owner={knowledgeOwner.get()!}
          onMutation={() => setKnowledgeReload((value) => value + 1)}
        />
      )}
      <MemoryImportDialog
        open={importing}
        onOpenChange={setImporting}
        io={importIO}
        onReview={(importBatch) => {
          setImporting(false);
          setReviewing({ importBatch });
        }}
        onImported={() => setKnowledgeReload((value) => value + 1)}
      />
      <MemoryReviewDialog
        open={reviewing !== null}
        onOpenChange={(open) => {
          if (!open) setReviewing(null);
        }}
        io={reviewIO}
        importBatch={reviewing?.importBatch}
        onChanged={() => setKnowledgeReload((value) => value + 1)}
      />
    </div>
  );
}
