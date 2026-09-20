import { useCallback, useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Activity, Brain, GitBranch } from 'lucide-react';
import { clientError } from '../../api/errors';
import type {
  DreamRunReview,
  KnowledgeGraphSnapshot,
  MonitorLogEntry,
  MonitorSnapshot,
} from '../../api/types';
import { useClientState, useRuntime } from '../../runtime';
import { Tabs } from '../../ui/primitives';
import { useOverlay } from '../../ui/overlays';
import TaskLibrary from '../tasks/TaskLibrary';
import KnowledgeHome, { type KnowledgeDreamState } from '../home/KnowledgeHome';
import MonitorHome from '../home/MonitorHome';
import KnowledgeEditorDialog from '../knowledge/KnowledgeEditorDialog';

const homeTabs = ['workflows', 'knowledge', 'monitor'];

export default function Home() {
  const state = useClientState();
  const { controller, knowledgeOwner } = useRuntime();
  const overlay = useOverlay();
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
  const [fullLogsOpen, setFullLogsOpen] = useState(false);
  const [fullLogsLoading, setFullLogsLoading] = useState(false);
  const [fullLogsError, setFullLogsError] = useState('');
  const [fullLogs, setFullLogs] = useState<MonitorLogEntry[]>([]);
  const [dreamReview, setDreamReview] = useState<DreamRunReview | null>(null);
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
    if (tab !== 'monitor' || !identity) return;
    const abort = new AbortController();
    setMonitorLoading(true);
    setMonitorError('');
    controller.monitorSnapshot(abort.signal).then(
      (value) => {
        setMonitor(value);
        setDreamReview(null);
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
    if (!dreamReview) {
      setDreamState((value) => ({
        ...value,
        state: 'reviewing',
        message: 'Reviewing the current Dream Cycle effects…',
      }));
      try {
        const review = await controller.reviewDreamRun({
          snapshot_revision: monitor.dream_revision,
        });
        setDreamReview(review);
        setDreamState((value) => ({
          ...value,
          state: 'reviewing',
          message:
            'Review ready. Run Dream Cycle to update saved knowledge and its journal.',
        }));
      } catch (cause) {
        setDreamState((value) => ({
          ...value,
          state: 'error',
          message: clientError(cause).message,
        }));
        throw cause;
      }
      return;
    }
    overlay.open({
      kind: 'alert',
      title: 'Run Dream Cycle now?',
      description:
        'This can merge and enrich saved knowledge and add inferred connections. The reviewed effects are bound to the current snapshot.',
      confirmLabel: 'Run Dream Cycle',
      onConfirm: () => {
        const review = dreamReview;
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
              setDreamReview(null);
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
              setDreamReview(null);
              setDreamState((value) => ({
                ...value,
                state: 'error',
                message: clientError(cause).message,
              }));
            },
          );
      },
    });
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
                snapshot={monitor}
                loading={monitorLoading}
                error={monitorError}
                onRefresh={() => setMonitorReload((value) => value + 1)}
                onLoadFullLogs={() => void loadFullLogs()}
                fullLogsOpen={fullLogsOpen}
                fullLogsLoading={fullLogsLoading}
                fullLogsError={fullLogsError}
                fullLogEntries={fullLogs}
                onCloseFullLogs={() => setFullLogsOpen(false)}
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
