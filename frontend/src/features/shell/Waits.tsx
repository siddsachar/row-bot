import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  CalendarPlus,
  Eye,
  Hourglass,
  Pause,
  Play,
  RefreshCw,
  Workflow,
  X,
} from 'lucide-react';
import type {
  TaskEditableFields,
  WaitView,
  WatchControl,
} from '../../api/types';
import { clientError } from '../../api/errors';
import { useLiveUpdate } from '../../live';
import { useRuntime } from '../../runtime';
import { clockTime } from '../../ui/format';
import { IconButton } from '../../ui/primitives';

/**
 * What conversations wait for (5.3.1): a process to end, a check to show
 * something, a time, another conversation, or a watch (B4). Read again when
 * the server says waits changed (the `waits` topic). `waits` are the live
 * ones; `ended` are watches that ran out or reached their change cap and keep
 * their card until put away.
 */
export function useWaits(): {
  waits: WaitView[];
  ended: WaitView[];
  stop: (waitId: string) => Promise<void>;
  control: (waitId: string, action: WatchControl['action']) => Promise<string>;
} {
  const { controller } = useRuntime();
  const [items, setItems] = useState<WaitView[]>([]);
  const [readAt, setReadAt] = useState(0);
  useLiveUpdate(['waits'], () => setReadAt(Date.now()));

  useEffect(() => {
    const abort = new AbortController();
    controller.waits(abort.signal).then(
      (page) => {
        if (!abort.signal.aborted && page) setItems(page.items);
      },
      () => undefined,
    );
    return () => abort.abort();
  }, [controller, readAt]);

  const stop = useCallback(
    async (waitId: string) => {
      const page = await controller.stopWait(waitId).catch(() => null);
      if (page) setItems(page.items);
      else setItems((current) => current.filter((w) => w.wait_id !== waitId));
    },
    [controller],
  );
  /** Pause, Resume, Check now or Extend; the error code ('' when done). */
  const control = useCallback(
    async (waitId: string, action: WatchControl['action']) => {
      try {
        const page = await controller.controlWatch(waitId, { action });
        if (page) setItems(page.items);
        return '';
      } catch (cause) {
        return clientError(cause).code;
      }
    },
    [controller],
  );
  return {
    waits: items.filter((wait) => wait.status !== 'ended'),
    ended: items.filter((wait) => wait.status === 'ended'),
    stop,
    control,
  };
}

/**
 * How a wait looks: "checks every 5 min", "every 10 min", or a time wait's
 * "at 4:25 PM", written in this device's zone and clock style (M5).
 */
export function waitHow(wait: WaitView) {
  return wait.at ? `at ${clockTime(wait.at)}` : wait.how;
}

function tokens(count: number) {
  if (count < 1000) return `${count} tokens`;
  return `${(count / 1000).toFixed(count < 10_000 ? 1 : 0)}k tokens`;
}

/** "every 10 min · 2 of 20 changes · 12k tokens · $0.04". */
function watchMeta(wait: WaitView) {
  const watch = wait.watch!;
  const parts = wait.status === 'ended' ? [] : [wait.how];
  if (watch.changes)
    parts.push(`${watch.changes} of ${watch.max_changes} changes`);
  if (watch.tokens) parts.push(tokens(watch.tokens));
  if (watch.cost_usd != null && watch.cost_usd > 0)
    parts.push(`$${watch.cost_usd.toFixed(2)}`);
  return parts.join(' · ');
}

/** The watch's last check in words: "Checked 14:20: No change". */
function watchResult(wait: WaitView) {
  const watch = wait.watch!;
  if (wait.status === 'ended')
    return `${
      watch.ended === 'capped'
        ? `Stopped after ${watch.max_changes} changes.`
        : 'Its time ran out.'
    } A workflow can keep checking.`;
  if (watch.checking) return 'Checking now…';
  if (!watch.checked_at) return 'First check soon.';
  return `Checked ${clockTime(watch.checked_at)}: ${watch.result || 'No change'}`;
}

/** The workflow editor's fields for "Make this a workflow". */
export function workflowFields(wait: WaitView): TaskEditableFields {
  const draft = wait.watch!.workflow;
  return {
    name: draft.name,
    description: '',
    icon: '⚡',
    prompts: [draft.prompt],
    enabled: true,
    schedule: `interval_minutes:${draft.every_minutes}`,
    at: null,
    notify_only: false,
    notify_label: '',
    channels: null,
  };
}

export function WatchCard({
  wait,
  stop,
  control,
}: {
  wait: WaitView;
  stop: (waitId: string) => Promise<void>;
  control: (waitId: string, action: WatchControl['action']) => Promise<string>;
}) {
  const { taskEditSessions } = useRuntime();
  const navigate = useNavigate();
  const watch = wait.watch!;
  const ended = wait.status === 'ended';
  const paused = wait.status === 'paused';
  const [atLimit, setAtLimit] = useState(false);
  const [problem, setProblem] = useState('');
  const act = async (action: WatchControl['action']) => {
    if (action === 'extend' && !watch.can_extend) {
      setAtLimit(true);
      return;
    }
    const code = await control(wait.wait_id, action);
    if (code === 'watch_lifetime_max') setAtLimit(true);
    else setProblem(code ? clientError({ code }).message : '');
  };
  const makeWorkflow = () => {
    // The person reviews it in the workflow editor; nothing is made until
    // they save, and saving stops the watch.
    const opened = taskEditSessions?.open('task', '', watch.workflow.name, {
      key: `watch:${wait.wait_id}`,
      fields: workflowFields(wait),
      afterCreate: () => void stop(wait.wait_id),
    });
    if (opened) navigate('/?tab=workflows');
  };
  const offerWorkflow = (ended || atLimit) && !!taskEditSessions;
  const note = atLimit
    ? 'A watch lasts at most 7 days. Make it a workflow to keep checking.'
    : problem;
  return (
    <li
      className={`waits-item watch-card${watch.trouble ? ' is-trouble' : ''}${ended ? ' is-ended' : ''}`}
    >
      {paused ? <Pause size={14} aria-hidden /> : <Eye size={14} aria-hidden />}
      <span className="watch-body">
        <span className="waits-label">
          {ended ? 'Stopped watching ' : paused ? 'Paused: ' : 'Watching '}
          <strong>{wait.label}</strong>
        </span>
        <span className="watch-meta">{watchMeta(wait)}</span>
        <span className="watch-result" title={watch.result || undefined}>
          {watchResult(wait)}
        </span>
        {note && (
          <span className="watch-note" role="status">
            {note}
          </span>
        )}
      </span>
      <span className="watch-actions">
        {!ended && !paused && (
          <IconButton
            size="sm"
            label="Check now"
            disabled={watch.checking || wait.status === 'due'}
            onClick={() => void act('check_now')}
          >
            <RefreshCw size={14} aria-hidden />
          </IconButton>
        )}
        {!ended && (
          <IconButton
            size="sm"
            label={paused ? 'Resume watching' : 'Pause watching'}
            onClick={() => void act(paused ? 'resume' : 'pause')}
          >
            {paused ? (
              <Play size={14} aria-hidden />
            ) : (
              <Pause size={14} aria-hidden />
            )}
          </IconButton>
        )}
        {!ended && !atLimit && (
          <IconButton
            size="sm"
            label="Watch 24 hours longer"
            onClick={() => void act('extend')}
          >
            <CalendarPlus size={14} aria-hidden />
          </IconButton>
        )}
        {offerWorkflow && (
          <IconButton
            size="sm"
            label="Make this a workflow"
            onClick={makeWorkflow}
          >
            <Workflow size={14} aria-hidden />
          </IconButton>
        )}
        <IconButton
          size="sm"
          label={ended ? 'Dismiss' : 'Stop watching'}
          onClick={() => void stop(wait.wait_id)}
        >
          <X size={14} aria-hidden />
        </IconButton>
      </span>
    </li>
  );
}

/**
 * Above the composer, one quiet line per wait: "Waiting for CI on PR 590 is
 * green · checks every 2 min" with a Stop. Nothing runs while it waits; when
 * it happens the conversation carries on by itself. A watch shows as a card:
 * its last check, its changes and what it used, with Check now, Pause,
 * Extend and Stop, and "Make this a workflow" once it ends.
 */
export function WaitsStrip({ conversationId }: { conversationId: string }) {
  const { waits, ended, stop, control } = useWaits();
  const mine = [...waits, ...ended].filter(
    (wait) => wait.conversation_id === conversationId,
  );
  if (!conversationId || mine.length === 0) return null;
  return (
    <section className="waits-strip" aria-label="Waiting">
      <ul>
        {mine.map((wait) =>
          wait.kind === 'watch' && wait.watch ? (
            <WatchCard
              key={wait.wait_id}
              wait={wait}
              stop={stop}
              control={control}
            />
          ) : (
            <li key={wait.wait_id} className="waits-item">
              <Hourglass size={14} aria-hidden />
              <span className="waits-label">
                Waiting for <strong>{wait.label}</strong>
              </span>
              <span className="waits-how">{waitHow(wait)}</span>
              <IconButton
                size="sm"
                label="Stop waiting"
                onClick={() => void stop(wait.wait_id)}
              >
                <X size={14} aria-hidden />
              </IconButton>
            </li>
          ),
        )}
      </ul>
    </section>
  );
}
