import { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { Plus } from 'lucide-react';
import type { TaskSummary, TaskSummaryPage } from '../../api/types';
import { clientError } from '../../api/errors';
import { useLiveUpdate } from '../../live';
import { useOverlay } from '../../ui/overlays';
import {
  Button,
  EmptyState,
  ErrorState,
  IconButton,
  Segmented,
  Skeleton,
  Toggle,
} from '../../ui/primitives';
import { useWorkspaceActions } from '../shell/workspace-actions';
import PhoneWorkflowSheet, { type WorkflowRunIo } from './PhoneWorkflowSheet';
import {
  WorkflowGlyph,
  WorkflowLine,
  isRunning,
  lastRunFailed,
  ranOnce,
  workflowState,
} from './workflow-status';
import '../../ui/styles/phone-chats.css';
import '../../ui/styles/phone-workflows.css';

type Chip = 'all' | 'running' | 'failed' | 'off';
const CHIPS: {
  value: Chip;
  label: string;
  has: (task: TaskSummary) => boolean;
}[] = [
  { value: 'all', label: 'All', has: () => true },
  { value: 'running', label: 'Running', has: isRunning },
  { value: 'failed', label: 'Failed', has: lastRunFailed },
  {
    value: 'off',
    label: 'Off',
    has: (task) => !task.enabled && !ranOnce(task),
  },
];
const EMPTY: Record<Chip, string> = {
  all: '',
  running: 'Nothing is running',
  failed: 'Nothing has failed',
  off: 'Every workflow is on',
};
/** The list reads every saved workflow, page by page, up to this many pages. */
const MAX_PAGES = 20;
/** How often the list reads again while a workflow runs. */
const FOLLOW_MS = 3000;
/** What + leaves in a new chat's composer, never sent. */
export const NEW_WORKFLOW_DRAFT = 'Set up a workflow that ';

/**
 * The phone's Workflows tab (C2, D11 A): a row per workflow with its status
 * line and switch, filter chips, and a sheet per workflow. + starts a chat
 * that sets one up; the editor stays on the computer.
 */
export default function PhoneWorkflows({
  load,
  onToggleEnabled,
  io,
  openId,
  openName,
  onOpen,
  onClose,
  refreshToken = 0,
}: {
  load: (
    query?: string,
    enabled?: boolean,
    cursor?: string,
    signal?: AbortSignal,
  ) => Promise<TaskSummaryPage>;
  onToggleEnabled: (id: string, enabled: boolean) => Promise<void>;
  io: WorkflowRunIo;
  /** The workflow whose sheet is open (a row tapped, or a link's `workflow`). */
  openId: string | null;
  openName: string;
  onOpen: (id: string, name: string) => void;
  onClose: () => void;
  refreshToken?: number;
}) {
  const overlay = useOverlay();
  const slot = useWorkspaceActions()?.phoneHeaderSlot;
  const newChat = useWorkspaceActions()?.newChat;
  const [items, setItems] = useState<TaskSummary[] | null>(null);
  const [error, setError] = useState('');
  const [chip, setChip] = useState<Chip>('all');
  const [reads, setReads] = useState(0);
  const [toggling, setToggling] = useState('');
  const reread = () => setReads((value) => value + 1);
  useLiveUpdate(['runs'], reread);
  useEffect(() => {
    const abort = new AbortController();
    (async () => {
      const first = await load('', undefined, undefined, abort.signal);
      const all = [...first.items];
      let cursor = first.next_cursor;
      for (let count = 1; cursor && count < MAX_PAGES; count += 1) {
        const next = await load('', undefined, cursor, abort.signal);
        all.push(...next.items);
        cursor = next.next_cursor;
      }
      return all;
    })().then(
      (all) => {
        if (abort.signal.aborted) return;
        setItems(all);
        setError('');
      },
      (cause: unknown) => {
        if (!abort.signal.aborted) setError(clientError(cause).message);
      },
    );
    return () => abort.abort();
  }, [load, reads, refreshToken]);
  // A running workflow's line follows its run until it ends.
  const running = Boolean(items?.some(isRunning));
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(reread, FOLLOW_MS);
    return () => window.clearInterval(timer);
  }, [running]);
  async function toggle(task: TaskSummary, enabled: boolean) {
    if (toggling) return;
    setToggling(task.id);
    try {
      await onToggleEnabled(task.id, enabled);
      setItems(
        (current) =>
          current?.map((item) =>
            item.id === task.id ? { ...item, enabled } : item,
          ) ?? current,
      );
      overlay.notify(`${task.name} turned ${enabled ? 'on' : 'off'}.`);
      reread();
    } catch (cause) {
      overlay.notify(clientError(cause).message, 'danger');
    } finally {
      setToggling('');
    }
  }
  const counts = Object.fromEntries(
    CHIPS.map(({ value, has }) => [value, items?.filter(has).length ?? 0]),
  ) as Record<Chip, number>;
  const has = CHIPS.find((option) => option.value === chip)!.has;
  const shown = items?.filter(has) ?? [];
  const open = openId
    ? (items?.find((item) => item.id === openId) ?? null)
    : null;
  return (
    <section
      className="phone-workflows"
      aria-label="Workflows"
      aria-busy={items === null && !error}
    >
      {/* The header names the place; the page title is for screen readers. */}
      <h1 className="visually-hidden">Workflows</h1>
      <Segmented
        size="sm"
        className="phone-chips"
        label="Show workflows"
        value={chip}
        onChange={setChip}
        options={CHIPS.map(({ value, label }) => ({
          value,
          label: items ? `${label} ${counts[value]}` : label,
        }))}
      />
      {error ? (
        <ErrorState
          title="Workflows didn't load"
          action={<Button onClick={reread}>Try again</Button>}
        >
          {error}
        </ErrorState>
      ) : items === null ? (
        <Skeleton label="Loading workflows" />
      ) : !items.length ? (
        <EmptyState title="No workflows yet">
          Tap + to set one up in a chat. Row-Bot can run it on a schedule or
          when you ask.
        </EmptyState>
      ) : !shown.length ? (
        <EmptyState title={EMPTY[chip]}>
          Workflows show here when they are.
        </EmptyState>
      ) : (
        <ul
          className="phone-chat-list-rows phone-workflow-list"
          aria-label="Workflows"
        >
          {shown.map((task) => {
            const state = workflowState(task);
            return (
              <li
                key={task.id}
                className="phone-row phone-workflow-row"
                data-state={state}
              >
                <button
                  type="button"
                  className="phone-row-main"
                  aria-haspopup="dialog"
                  onClick={() => onOpen(task.id, task.name)}
                >
                  <span className="phone-row-icon" aria-hidden>
                    <WorkflowGlyph task={task} size={18} />
                  </span>
                  <span className="phone-row-text">
                    <span className="phone-row-title">{task.name}</span>
                    <span className="phone-row-meta">
                      {state === 'running' && (
                        <span className="phone-workflow-live" aria-hidden />
                      )}
                      <WorkflowLine task={task} />
                    </span>
                  </span>
                </button>
                {!ranOnce(task) && (
                  <Toggle
                    className="phone-workflow-switch"
                    label={`${task.enabled ? 'Turn off' : 'Turn on'} workflow: ${task.name}`}
                    checked={task.enabled}
                    disabled={Boolean(toggling)}
                    onChange={(event) =>
                      void toggle(task, event.target.checked)
                    }
                  />
                )}
              </li>
            );
          })}
        </ul>
      )}
      {items !== null && items.length > 0 && (
        <p className="phone-workflows-note">
          Tap one to run it, see its runs or answer what it asks. + starts a
          chat that sets one up.
        </p>
      )}
      <PhoneWorkflowSheet
        taskId={openId}
        task={open}
        name={openName}
        onClose={() => {
          onClose();
          reread();
        }}
        onToggle={(task, enabled) => void toggle(task, enabled)}
        toggling={Boolean(toggling)}
        io={io}
        onFinished={reread}
      />
      {slot &&
        newChat &&
        createPortal(
          <IconButton
            size="sm"
            label="New workflow"
            onClick={() => newChat(NEW_WORKFLOW_DRAFT)}
          >
            <Plus size={22} aria-hidden />
          </IconButton>,
          slot,
        )}
    </section>
  );
}
