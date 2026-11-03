import {
  useEffect,
  useId,
  useRef,
  useState,
  useSyncExternalStore,
} from 'react';
import { ArrowUp, Check, GripVertical, Plus, X } from 'lucide-react';
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
import ScheduleBuilder, { onceAt } from './ScheduleBuilder';
import { defaultZone } from './schedule-zones';
import { approvalLevel } from '../shell/approval-levels';

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
  if (!mode) return 'Default';
  return {
    approve: 'Ask before actions',
    block: 'Block actions',
    auto: 'Auto, reviewed within the profile',
    full: 'Hands-off',
  }[approvalLevel(mode)];
}

export default function TaskEditor({
  taskId,
  load,
  create,
  save,
  onSaved,
  onCancel,
  deliveryChannels = [],
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

  // B122: leaving for the model settings with unsaved
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
      (onceAt(fields.at, fields.timezone || defaultZone())?.getTime() ??
        Date.now()) <= Date.now() &&
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
      const submitted = fields.notify_only
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
                onChange={(event) =>
                  change('notify_only', event.target.value === 'reminder')
                }
              >
                <option value="workflow">Workflow</option>
                <option value="reminder">Reminder</option>
              </Select>
            </Field>
          </div>
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
              readOnly={false}
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
              timezone={fields.timezone ?? null}
              enabled={fields.enabled}
              onChange={(next) =>
                setFields((current) => ({
                  ...current,
                  ...next,
                  // Giving a workflow that had none a time to run turns it on: a
                  // schedule nobody switched on would never run (U20).
                  enabled:
                    current.enabled ||
                    (!current.schedule &&
                      !current.at &&
                      !!(next.schedule || next.at)),
                }))
              }
            />
            <div className="task-rail-switch">
              <div>
                <label
                  id={`${formId}-enabled`}
                  htmlFor={`${formId}-enabled-switch`}
                >
                  Enabled
                </label>
                <small id={`${formId}-enabled-help`}>
                  Scheduled runs happen only while this is on. Saving never
                  starts a run.
                </small>
              </div>
              <Toggle
                id={`${formId}-enabled-switch`}
                label="Enabled"
                aria-describedby={`${formId}-enabled-help`}
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
                  {/* Always on, so a fact with a tick, not a pale locked checkbox (U15). */}
                  <p className="task-delivery-fixed">
                    <Check size={14} aria-hidden />
                    Always in this app
                  </p>
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
                      ? 'Set up a channel in Settings › Apps to also send results there.'
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
              : 'Save workflow'}
        </Button>
      </footer>
    </form>
  );
}

/**
 * The messages each run sends, in order, as chat turns in the workflow's own
 * conversation (F31). Drag a handle, use its arrow keys, or the move buttons;
 * each keeps its accessible name ("Message 2").
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
    setAnnouncement(`Message ${from + 1} moved to position ${to + 1}.`);
  };
  return (
    <div className="task-steps" role="group" aria-label="Workflow messages">
      <div className="task-steps-head">
        <h3>Messages</h3>
        <span className="home-caption">
          Each run sends these to its own conversation, like you would in a
          chat. Say what to do if there is nothing to do.
          {!readOnly && ' Type {{ to add the date.'}
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
                aria-label={`Reorder message ${index + 1}`}
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
            <Field label={`Message ${index + 1}`}>
              <PromptTextarea
                className="input"
                rows={3}
                value={prompt}
                required={!readOnly}
                readOnly={readOnly}
                maxLength={16384}
                placeholder={
                  index === 0
                    ? 'What should Row-Bot do?'
                    : 'Then what? It sees the reply to the message before.'
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
                  label={`Move message ${index + 1} up`}
                  disabled={index === 0}
                  onClick={() => move(index, index - 1)}
                >
                  <ArrowUp size={14} aria-hidden />
                </IconButton>
                <IconButton
                  size="sm"
                  label={`Remove message ${index + 1}`}
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
          <Plus size={14} aria-hidden /> Add message
        </Button>
      )}
      <p className="visually-hidden" aria-live="polite">
        {announcement}
      </p>
    </div>
  );
}
