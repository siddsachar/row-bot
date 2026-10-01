import {
  useEffect,
  useId,
  useRef,
  useState,
  useSyncExternalStore,
} from 'react';
import { ArrowUp, GitBranch, GripVertical, Plus, X } from 'lucide-react';
import {
  useTaskEditSession,
  useTaskEditValue,
  type TaskEditSession,
} from './task-edit-sessions';
import type {
  TaskEditableFields,
  TaskEditorSnapshot,
  TaskSaveResult,
} from '../../api/types';
import { clientError } from '../../api/errors';
import {
  Button,
  Field,
  IconButton,
  Input,
  Select,
  Skeleton,
  Toggle,
} from '../../ui/primitives';
import { humanizeToken } from '../../ui/format';
import PromptTextarea from './PromptTextarea';
import ScheduleBuilder from './ScheduleBuilder';

export interface TaskEditorProps {
  session?: TaskEditSession;
  taskId?: string;
  load: (taskId: string, signal?: AbortSignal) => Promise<TaskEditorSnapshot>;
  create: (fields: TaskEditableFields) => Promise<TaskSaveResult>;
  save: (
    taskId: string,
    expectedRevision: string,
    fields: TaskEditableFields,
  ) => Promise<TaskSaveResult>;
  onSaved: (task: TaskEditorSnapshot) => void;
  onCancel: () => void;
  /** The configured channels; `selected` marks the workflow defaults. */
  deliveryChannels?: ReadonlyArray<{
    id: string;
    label: string;
    selected: boolean;
  }>;
  /** Open the step graph for this saved workflow. */
  onAdvancedSteps?: () => void;
  /** Open the saved workflow's model and approval settings. */
  onTaskSettings?: () => void;
}

const emptyFields = (): TaskEditableFields => ({
  name: '',
  description: '',
  icon: '⚡',
  prompts: [''],
  enabled: false,
  schedule: null,
  at: null,
  notify_only: false,
  notify_label: '',
  channels: null,
});

function profileLabel(id: string) {
  if (!id) return 'Default';
  const name = id.replace(/^builtin:/, '').replace(/^row_bot_/, '');
  return humanizeToken(name) || 'Default';
}

function approvalLabel(mode: string) {
  if (mode === 'approve') return 'Ask before actions';
  if (mode === 'block') return 'Block actions';
  if (mode === 'allow_all') return 'Auto, within the profile';
  return mode ? humanizeToken(mode) : 'Default';
}

export default function TaskEditor({
  taskId,
  load,
  create,
  save,
  onSaved,
  onCancel,
  deliveryChannels = [],
  onAdvancedSteps,
  onTaskSettings,
  session: injectedSession,
}: TaskEditorProps) {
  const formId = useId();
  const session = useTaskEditSession(injectedSession, 'task', taskId);
  const meta = useSyncExternalStore(session.subscribe, session.getMeta);
  const [fields, setFields] = useTaskEditValue<TaskEditableFields>(
    session,
    'fields',
    emptyFields(),
    true,
  );
  const [snapshot, setSnapshot] = useTaskEditValue<TaskEditorSnapshot | null>(
    session,
    'snapshot',
    null,
  );
  const [loading, setLoading] = useTaskEditValue(session, 'loading', !!taskId);
  const [saving, setSaving] = useTaskEditValue(session, 'saving', false);
  const [error, setError] = useTaskEditValue(session, 'error', '');
  const [stale, setStale] = useTaskEditValue(session, 'stale', false);
  const [reload, setReload] = useTaskEditValue(session, 'reload', 0);
  const epoch = useRef(0);
  const pending = useRef(false);

  useEffect(() => {
    const abort = session.beginRead(reload);
    if (!abort)
      return () => {
        epoch.current += 1;
      };
    ++epoch.current;
    setSnapshot(null);
    setFields(emptyFields());
    setError('');
    setStale(false);
    setLoading(!!taskId);
    if (taskId) {
      load(taskId, abort.signal).then(
        (next) => {
          if (abort.signal.aborted || !session.getMeta().active) return;
          setSnapshot(next);
          setFields(next.fields);
          setLoading(false);
          session.clean();
          session.finishRead(abort);
        },
        (cause: unknown) => {
          if (abort.signal.aborted || !session.getMeta().active) return;
          setError(clientError(cause).message);
          setLoading(false);
          session.finishRead(abort);
        },
      );
    } else {
      session.clean();
      session.finishRead(abort);
    }
    return () => {
      if (!injectedSession) {
        abort.abort();
        session.finishRead(abort);
      }
      epoch.current += 1;
    };
  }, [
    taskId,
    load,
    reload,
    session,
    injectedSession,
    setSnapshot,
    setFields,
    setError,
    setStale,
    setLoading,
  ]);

  function change<K extends keyof TaskEditableFields>(
    key: K,
    value: TaskEditableFields[K],
  ) {
    setFields((current) => ({ ...current, [key]: value }));
  }

  // B122: leaving for the step graph or the model settings with unsaved
  // changes asks to save first instead of dropping them silently.
  const [leaving, setLeaving] = useState<null | (() => void)>(null);
  const leaveTo = (go: () => void) => () => {
    if (session.getMeta().dirty) setLeaving(() => go);
    else go();
  };

  async function submit(event: { preventDefault(): void }, after?: () => void) {
    event.preventDefault();
    if (
      pending.current ||
      meta.busy ||
      loading ||
      (stale && !meta.uncertain) ||
      !meta.active ||
      (taskId && !snapshot)
    )
      return;
    // Saving never starts a run (B133): a one-off in the past is refused
    // unless it is unchanged (the server knows whether it already ran).
    if (
      fields.enabled &&
      fields.at &&
      new Date(fields.at).getTime() <= Date.now() &&
      (!snapshot?.fields.enabled || snapshot.fields.at !== fields.at)
    ) {
      setError(
        'That time has passed. Pick a later time, or switch the workflow off.',
      );
      return;
    }
    if (
      fields.prompts.reduce((total, prompt) => total + prompt.length, 0) > 65536
    ) {
      setError(
        'The combined prompts exceed the supported limit of 65,536 characters.',
      );
      return;
    }
    pending.current = true;
    const ticket = epoch.current;
    setSaving(true);
    setError('');
    try {
      const submitted =
        fields.notify_only && !snapshot?.advanced
          ? {
              ...fields,
              prompts: fields.prompts.filter((prompt) => prompt.trim()),
            }
          : fields;
      const next = meta.uncertain
        ? await session.retryOperation<TaskSaveResult>()
        : await session.run(() =>
            snapshot
              ? save(snapshot.id, snapshot.revision, submitted)
              : create(submitted),
          );
      if (!session.getMeta().active) return;
      setSnapshot(next.task);
      setFields(next.task.fields);
      session.clean('Saved task. No workflow was run.');
      if (epoch.current === ticket) {
        onSaved(next.task);
        after?.();
      }
    } catch (cause) {
      if (!session.getMeta().active) return;
      const failure = clientError(cause);
      setStale(failure.code === 'task_revision_conflict');
      setError(
        failure.code === 'task_revision_conflict'
          ? 'This task changed elsewhere. Your edits are still here. Reload the saved task before making another change.'
          : failure.message,
      );
    } finally {
      pending.current = false;
      setSaving(false);
    }
  }

  const advanced = snapshot?.advanced ?? false;
  const legacyDelivery = snapshot?.legacy_delivery ?? false;
  const locked =
    saving || loading || meta.uncertain || stale || (!!taskId && !snapshot);
  if (loading) return <Skeleton label="Loading saved task" />;
  if (!meta.active)
    return (
      <p role="status">
        Workflow access changed. Reopen the editor in the current session.
      </p>
    );
  // Decision 18: results always stay in the app; the checklist adds the
  // configured channels. Untouched it follows the defaults (null); a tick
  // makes this workflow's own list ([] = in this app only).
  const defaultIds = deliveryChannels
    .filter((channel) => channel.selected)
    .map((channel) => channel.id);
  const following = fields.channels === null;
  const ticked = new Set(following ? defaultIds : fields.channels);
  const channelRows = [
    ...deliveryChannels.map((channel) => ({
      id: channel.id,
      label: channel.label,
    })),
    // A saved channel that is no longer set up can still be unticked.
    ...(fields.channels ?? [])
      .filter((id) => !deliveryChannels.some((channel) => channel.id === id))
      .map((id) => ({ id, label: `${id} (not set up)` })),
  ];
  const tick = (id: string, on: boolean) => {
    const next = new Set(ticked);
    if (on) next.add(id);
    else next.delete(id);
    change(
      'channels',
      channelRows.map((row) => row.id).filter((row) => next.has(row)),
    );
  };
  return (
    <form
      className="task-builder"
      onSubmit={(event) => void submit(event)}
      aria-label={taskId ? 'Edit task' : 'Create task'}
    >
      <h2 className="visually-hidden">
        {taskId ? 'Edit task' : 'Create task'}
      </h2>
      {leaving && (
        <div
          role="group"
          aria-label="Save your changes first?"
          className="task-builder-leave"
        >
          <span>Your changes to this workflow aren't saved yet.</span>
          <Button
            variant="primary"
            className="small"
            disabled={saving}
            onClick={(event) => {
              const go = leaving;
              setLeaving(null);
              void submit(event, go);
            }}
          >
            Save and continue
          </Button>
          <Button
            variant="ghost"
            className="small"
            onClick={() => setLeaving(null)}
          >
            Keep editing
          </Button>
        </div>
      )}
      {error && (
        <p className="task-builder-alert" role="alert">
          {error}
        </p>
      )}
      {meta.limit && (
        <p className="task-builder-alert" role="alert">
          This retained draft reached its size limit. Shorten a field before
          adding more content.
        </p>
      )}
      {meta.notice && <p role="status">{meta.notice}</p>}
      {meta.uncertain && (
        <p role="status">
          The original save is unconfirmed. Your draft is locked until its
          receipt is resolved.
        </p>
      )}
      {taskId && (!snapshot || stale) && (
        <Button
          className="small task-builder-reload"
          disabled={saving || meta.uncertain}
          onClick={() => setReload((value) => value + 1)}
        >
          Reload saved task
        </Button>
      )}
      <fieldset className="task-builder-body" disabled={locked}>
        <div className="task-builder-main">
          <div className="task-builder-identity">
            <Field label="Icon">
              <Input
                className="task-builder-icon"
                value={fields.icon}
                maxLength={32}
                onChange={(event) => change('icon', event.target.value)}
              />
            </Field>
            <Field label="Name">
              <Input
                value={fields.name}
                maxLength={256}
                required
                placeholder="Morning briefing"
                onChange={(event) => change('name', event.target.value)}
              />
            </Field>
          </div>
          <Field label="Description">
            <textarea
              className="input"
              value={fields.description}
              maxLength={4096}
              rows={2}
              placeholder="What it does, in a sentence"
              onChange={(event) => change('description', event.target.value)}
            />
          </Field>
          <div className="task-builder-kind">
            <Field label="Task type">
              <Select
                value={fields.notify_only ? 'reminder' : 'workflow'}
                disabled={advanced}
                onChange={(event) =>
                  change('notify_only', event.target.value === 'reminder')
                }
              >
                <option value="workflow">Workflow</option>
                <option value="reminder">Reminder</option>
              </Select>
            </Field>
            {taskId && onAdvancedSteps && !fields.notify_only && (
              <Button
                variant="ghost"
                className="small task-builder-graph"
                onClick={leaveTo(onAdvancedSteps)}
              >
                <GitBranch size={14} aria-hidden /> Open step graph
              </Button>
            )}
          </div>
          {advanced && (
            <p className="task-builder-note">
              This workflow uses branches, approvals or agents. Its steps are
              kept as they are; change them in the step graph.
            </p>
          )}
          {fields.notify_only ? (
            <Field label="Reminder text">
              <textarea
                className="input"
                rows={3}
                value={fields.notify_label}
                maxLength={4096}
                onChange={(event) => change('notify_label', event.target.value)}
              />
            </Field>
          ) : (
            <StepList
              prompts={fields.prompts}
              readOnly={advanced}
              onChange={(prompts) => change('prompts', prompts)}
            />
          )}
        </div>
        <aside
          className="task-builder-rail"
          aria-label="Schedule, delivery and policy"
        >
          <section className="task-rail-section">
            <h3>Schedule</h3>
            <ScheduleBuilder
              key={`${snapshot?.revision ?? 'new'}:${reload}`}
              schedule={fields.schedule}
              at={fields.at}
              enabled={fields.enabled}
              onChange={(next) =>
                setFields((current) => ({ ...current, ...next }))
              }
            />
            <div className="task-rail-switch">
              <div>
                <span id={`${formId}-enabled`}>Enabled</span>
                <small>
                  Scheduled runs happen only while this is on. Saving never
                  starts a run.
                </small>
              </div>
              <Toggle
                label="Enabled"
                checked={fields.enabled}
                onChange={(event) => change('enabled', event.target.checked)}
              />
            </div>
          </section>
          <section className="task-rail-section">
            <h3>Delivery</h3>
            <fieldset className="task-delivery">
              <legend>Send results to</legend>
              {legacyDelivery ? (
                <p className="field-hint">
                  This task keeps a destination from the earlier app. It is
                  preserved when you save.
                </p>
              ) : (
                <>
                  <label className="checkbox-row">
                    <input type="checkbox" checked disabled />
                    In this app
                  </label>
                  {channelRows.map((row) => (
                    <label className="checkbox-row" key={row.id}>
                      <input
                        type="checkbox"
                        checked={ticked.has(row.id)}
                        onChange={(event) => tick(row.id, event.target.checked)}
                      />
                      {row.label}
                    </label>
                  ))}
                  <p className="field-hint">
                    {!channelRows.length
                      ? 'Set up a channel in Settings › Channels to also send results there.'
                      : following
                        ? 'Following your defaults.'
                        : ticked.size
                          ? 'Only for this workflow.'
                          : 'Only in this app.'}{' '}
                    {!following && defaultIds.length > 0 && (
                      <Button
                        variant="ghost"
                        className="small"
                        onClick={() => change('channels', null)}
                      >
                        Use my defaults
                      </Button>
                    )}
                  </p>
                </>
              )}
            </fieldset>
          </section>
          <section className="task-rail-section">
            <h3>Model and approvals</h3>
            {snapshot ? (
              <dl className="task-rail-facts">
                <div>
                  <dt>Agent profile</dt>
                  <dd>{profileLabel(snapshot.agent_profile_id)}</dd>
                </div>
                <div>
                  <dt>Approvals</dt>
                  <dd>{approvalLabel(snapshot.approval_mode)}</dd>
                </div>
              </dl>
            ) : (
              <p className="task-rail-note">
                New workflows use the default agent profile and model, and block
                actions until you allow them. Change this after the first save.
              </p>
            )}
            {snapshot && onTaskSettings && (
              <Button
                variant="ghost"
                className="small"
                onClick={leaveTo(onTaskSettings)}
              >
                Change model and approvals
              </Button>
            )}
          </section>
        </aside>
      </fieldset>
      <footer className="task-builder-actions">
        {saving && (
          <p role="status" className="home-caption">
            Saving this task. Waiting for its confirmed outcome.
          </p>
        )}
        <Button className="small" disabled={saving} onClick={onCancel}>
          Cancel
        </Button>
        <Button
          type="submit"
          variant="primary"
          className="small"
          disabled={
            saving || (stale && !meta.uncertain) || (!!taskId && !snapshot)
          }
        >
          {saving
            ? 'Saving…'
            : meta.uncertain
              ? 'Retry original save'
              : 'Save task'}
        </Button>
      </footer>
    </form>
  );
}

/**
 * Prompt steps in run order. Drag a handle, use its arrow keys, or the move
 * buttons; each step keeps its accessible name ("Prompt 2").
 */
function StepList({
  prompts,
  readOnly,
  onChange,
}: {
  prompts: string[];
  readOnly: boolean;
  onChange: (prompts: string[]) => void;
}) {
  const [dragging, setDragging] = useState<number | null>(null);
  const [over, setOver] = useState<number | null>(null);
  const [announcement, setAnnouncement] = useState('');
  const move = (from: number, to: number) => {
    if (to < 0 || to >= prompts.length || from === to) return;
    const next = [...prompts];
    const [item] = next.splice(from, 1);
    next.splice(to, 0, item);
    onChange(next);
    setAnnouncement(`Step ${from + 1} moved to position ${to + 1}.`);
  };
  return (
    <div className="task-steps" role="group" aria-label="Workflow prompts">
      <div className="task-steps-head">
        <h3>Steps</h3>
        <span className="home-caption">
          Steps run in order; each sees the previous output.
          {!readOnly && ' Type {{ in a step to add the date or its result.'}
        </span>
      </div>
      <ol className="task-step-list">
        {prompts.map((prompt, index) => (
          <li
            key={index}
            className="task-step"
            data-dragging={dragging === index ? 'true' : undefined}
            data-over={
              over === index && dragging !== index ? 'true' : undefined
            }
            onDragOver={(event) => {
              if (dragging === null) return;
              event.preventDefault();
              setOver(index);
            }}
            onDragLeave={() =>
              setOver((value) => (value === index ? null : value))
            }
            onDrop={(event) => {
              event.preventDefault();
              if (dragging !== null) move(dragging, index);
              setDragging(null);
              setOver(null);
            }}
          >
            {!readOnly && (
              <button
                type="button"
                className="task-step-handle"
                draggable
                aria-label={`Reorder step ${index + 1}`}
                title="Drag, or use the arrow keys, to reorder"
                onDragStart={(event) => {
                  event.dataTransfer.effectAllowed = 'move';
                  event.dataTransfer.setData('text/plain', String(index));
                  setDragging(index);
                }}
                onDragEnd={() => {
                  setDragging(null);
                  setOver(null);
                }}
                onKeyDown={(event) => {
                  const target =
                    event.key === 'ArrowUp'
                      ? index - 1
                      : event.key === 'ArrowDown'
                        ? index + 1
                        : null;
                  if (target === null) return;
                  event.preventDefault();
                  move(index, target);
                  const list = event.currentTarget.closest('.task-step-list');
                  const position = Math.max(
                    0,
                    Math.min(target, prompts.length - 1),
                  );
                  requestAnimationFrame(() => {
                    const handles =
                      list?.querySelectorAll<HTMLButtonElement>(
                        '.task-step-handle',
                      );
                    handles?.[position]?.focus();
                  });
                }}
              >
                <GripVertical size={14} aria-hidden />
              </button>
            )}
            <span className="task-step-number" aria-hidden>
              {index + 1}
            </span>
            <Field label={`Prompt ${index + 1}`}>
              <PromptTextarea
                className="input"
                rows={3}
                value={prompt}
                required={!readOnly}
                readOnly={readOnly}
                maxLength={16384}
                placeholder={
                  index === 0
                    ? 'What should Row-Bot do first?'
                    : 'Then what? It can use the previous output.'
                }
                onChange={(next) =>
                  onChange(
                    prompts.map((value, position) =>
                      position === index ? next : value,
                    ),
                  )
                }
              />
            </Field>
            {!readOnly && (
              <div className="task-step-actions">
                <IconButton
                  size="sm"
                  label={`Move prompt ${index + 1} up`}
                  disabled={index === 0}
                  onClick={() => move(index, index - 1)}
                >
                  <ArrowUp size={14} aria-hidden />
                </IconButton>
                <IconButton
                  size="sm"
                  label={`Remove prompt ${index + 1}`}
                  disabled={prompts.length === 1}
                  onClick={() =>
                    onChange(
                      prompts.filter((_, position) => position !== index),
                    )
                  }
                >
                  <X size={14} aria-hidden />
                </IconButton>
              </div>
            )}
          </li>
        ))}
      </ol>
      {!readOnly && (
        <Button
          variant="ghost"
          className="small task-step-add"
          disabled={prompts.length >= 100}
          onClick={() => onChange([...prompts, ''])}
        >
          <Plus size={14} aria-hidden /> Add step
        </Button>
      )}
      <p className="visually-hidden" aria-live="polite">
        {announcement}
      </p>
    </div>
  );
}
