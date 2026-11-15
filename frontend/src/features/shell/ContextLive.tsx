import { useContext, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Circle,
  ExternalLink,
  Hourglass,
  MessageSquareShare,
  Square,
  Workflow,
  X,
} from 'lucide-react';
import type { TaskSummary } from '../../api/types';
import { useLiveUpdate } from '../../live';
import { useClientState, useRuntime } from '../../runtime';
import { IconButton } from '../../ui/primitives';
import { When } from '../../ui/When';
import { runStatus, scheduleWords } from '../home/home-format';
import {
  ProcessIoContext,
  processLive,
  processName,
  useBackgroundProcesses,
} from './background-processes';
import {
  RecordingIoContext,
  recordingLive,
  recordingSubject,
  useRecordings,
} from './recordings';
import {
  VideoEditIoContext,
  editLive,
  editTitle,
  useVideoEdits,
} from './video-edits';
import { useWaits } from './Waits';

/**
 * What a conversation has going on beside its turns, in Conversation details
 * (5.3.1): what it waits for, what runs for it, its workflow, and the
 * conversations it is linked to. Each section shows only when it has
 * something; each line has its one action.
 */
export function ContextLive({ conversationId }: { conversationId: string }) {
  return (
    <>
      <WaitingSection conversationId={conversationId} />
      <RunningSection conversationId={conversationId} />
      <WorkflowSection conversationId={conversationId} />
      <LinkedSection conversationId={conversationId} />
    </>
  );
}

function WaitingSection({ conversationId }: { conversationId: string }) {
  const { waits, stop } = useWaits();
  const mine = waits.filter((wait) => wait.conversation_id === conversationId);
  if (!mine.length) return null;
  return (
    <section
      className="context-rail-section context-live"
      aria-labelledby="context-waiting"
    >
      <h3 id="context-waiting">Waiting</h3>
      <ul className="context-live-list">
        {mine.map((wait) => (
          <li key={wait.wait_id} className="context-live-item">
            <Hourglass size={14} aria-hidden />
            <span className="context-live-text">
              <span className="context-live-name">{wait.label}</span>
              <span className="context-live-meta">{wait.how}</span>
            </span>
            <IconButton
              size="sm"
              label="Stop waiting"
              onClick={() => void stop(wait.wait_id)}
            >
              <X size={14} aria-hidden />
            </IconButton>
          </li>
        ))}
      </ul>
    </section>
  );
}

type Running = {
  id: string;
  name: string;
  meta: string;
  stopLabel: string;
  stop: () => Promise<unknown>;
};

function RunningSection({ conversationId }: { conversationId: string }) {
  const processIo = useContext(ProcessIoContext);
  const recordingIo = useContext(RecordingIoContext);
  const editIo = useContext(VideoEditIoContext);
  const processes = useBackgroundProcesses(processIo, conversationId);
  const recordings = useRecordings(recordingIo, conversationId);
  const edits = useVideoEdits(editIo, conversationId);
  const running: Running[] = [
    ...processes.items.filter(processLive).map((view) => ({
      id: `process:${view.process_id}`,
      name: processName(view),
      meta: view.status === 'stopping' ? 'Stopping' : 'Running',
      stopLabel: `Stop ${processName(view)}`,
      stop: async () => {
        if (processIo) processes.merge(await processIo.stop(view.process_id));
      },
    })),
    ...recordings.items.filter(recordingLive).map((view) => ({
      id: `recording:${view.recording_id}`,
      name: recordingSubject(view),
      meta: view.status === 'saving' ? 'Saving' : 'Recording',
      stopLabel: `Stop the ${recordingSubject(view).toLowerCase()}`,
      stop: async () => {
        if (recordingIo)
          recordings.merge(await recordingIo.stop(view.recording_id));
      },
    })),
    ...edits.items.filter(editLive).map((view) => ({
      id: `edit:${view.edit_id}`,
      name: editTitle(view),
      meta: view.progress == null ? 'Working' : `${view.progress}%`,
      stopLabel: `Stop ${editTitle(view)}`,
      stop: async () => {
        if (editIo) edits.merge(await editIo.stop(view.edit_id));
      },
    })),
  ];
  if (!running.length) return null;
  return (
    <section
      className="context-rail-section context-live"
      aria-labelledby="context-running"
    >
      <h3 id="context-running">Running</h3>
      <ul className="context-live-list">
        {running.map((item) => (
          <li key={item.id} className="context-live-item">
            <Circle
              size={10}
              className="context-live-dot"
              aria-hidden
              fill="currentColor"
            />
            <span className="context-live-text">
              <span className="context-live-name">{item.name}</span>
              <span className="context-live-meta">{item.meta}</span>
            </span>
            <IconButton
              size="sm"
              label={item.stopLabel}
              onClick={() => void item.stop().catch(() => undefined)}
            >
              <Square size={13} aria-hidden />
            </IconButton>
          </li>
        ))}
      </ul>
    </section>
  );
}

/** The saved workflow whose runs this conversation holds, if any (read page by page). */
function useWorkflowOf(conversationId: string, workflow: boolean) {
  const { controller } = useRuntime();
  const [task, setTask] = useState<TaskSummary | null>(null);
  const [readAt, setReadAt] = useState(0);
  useLiveUpdate(['runs'], () => setReadAt(Date.now()));
  useEffect(() => {
    if (!workflow) {
      setTask(null);
      return;
    }
    const abort = new AbortController();
    void (async () => {
      let cursor: string | undefined;
      for (let page = 0; page < 5; page += 1) {
        const found = await controller
          .savedTasks('', undefined, cursor, abort.signal)
          .catch(() => null);
        if (!found || abort.signal.aborted) return;
        const mine = found.items.find(
          (item) => item.conversation_id === conversationId,
        );
        if (mine || !found.next_cursor) {
          setTask(mine ?? null);
          return;
        }
        cursor = found.next_cursor;
      }
    })();
    return () => abort.abort();
  }, [controller, conversationId, workflow, readAt]);
  return task;
}

function WorkflowSection({ conversationId }: { conversationId: string }) {
  const state = useClientState();
  const navigate = useNavigate();
  const row = state.conversations.find(({ id }) => id === conversationId);
  const task = useWorkflowOf(conversationId, row?.category === 'workflow');
  if (!task) return null;
  const schedule = scheduleWords(task.schedule, task.at);
  const last = runStatus(task.active_run ? 'running' : task.last_status);
  return (
    <section
      className="context-rail-section context-live"
      aria-labelledby="context-workflow"
    >
      <h3 id="context-workflow">Workflow</h3>
      <div className="context-live-item">
        <Workflow size={14} aria-hidden />
        <span className="context-live-text">
          <span className="context-live-name">{task.name}</span>
          <span className="context-live-meta">
            {schedule || 'Runs when you start it'}
            {task.enabled && task.next_run ? (
              <>
                {' · next '}
                <When value={task.next_run} />
              </>
            ) : !task.enabled && (task.schedule || task.at) ? (
              ' · paused'
            ) : null}
          </span>
          <span className="context-live-meta">
            {task.active_run ? 'Running now' : `Last run: ${last.label}`}
          </span>
        </span>
        <IconButton
          size="sm"
          label="Open its runs"
          onClick={() =>
            navigate(`/?tab=workflows&workflow=${encodeURIComponent(task.id)}`)
          }
        >
          <ExternalLink size={14} aria-hidden />
        </IconButton>
      </div>
    </section>
  );
}

function LinkedSection({ conversationId }: { conversationId: string }) {
  const state = useClientState();
  const navigate = useNavigate();
  const row = state.conversations.find(({ id }) => id === conversationId);
  const from = row?.started_from
    ? state.conversations.find(({ id }) => id === row.started_from)
    : undefined;
  const started = state.conversations.filter(
    (other) => other.started_from === conversationId,
  );
  if (!row?.started_from && !started.length) return null;
  const links = [
    ...(row?.started_from
      ? [
          {
            id: row.started_from,
            name: from?.title || 'The conversation that started this one',
            meta: 'Started this one',
          },
        ]
      : []),
    ...started.map((other) => ({
      id: other.id,
      name: other.title || 'Untitled',
      meta: 'Started from here',
    })),
  ];
  return (
    <section
      className="context-rail-section context-live"
      aria-labelledby="context-linked"
    >
      <h3 id="context-linked">Linked conversations</h3>
      <ul className="context-live-list">
        {links.map((link) => (
          <li key={link.id} className="context-live-item">
            <MessageSquareShare size={14} aria-hidden />
            <button
              type="button"
              className="context-live-link"
              onClick={() =>
                navigate(`/conversations/${encodeURIComponent(link.id)}`)
              }
            >
              <span className="context-live-name">{link.name}</span>
              <span className="context-live-meta">{link.meta}</span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
