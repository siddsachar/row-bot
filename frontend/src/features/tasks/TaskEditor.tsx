import { useEffect, useRef, useSyncExternalStore, type FormEvent } from 'react';
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
  Input,
  Select,
  Skeleton,
  Toggle,
} from '../../ui/primitives';

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

export default function TaskEditor({
  taskId,
  load,
  create,
  save,
  onSaved,
  onCancel,
  session: injectedSession,
}: TaskEditorProps) {
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
  const [channelText, setChannelText] = useTaskEditValue(
    session,
    'channelText',
    '',
  );
  const [delivery, setDelivery] = useTaskEditValue(
    session,
    'delivery',
    'inherit',
  );
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
    setChannelText('');
    setDelivery('inherit');
    setLoading(!!taskId);
    if (taskId) {
      load(taskId, abort.signal).then(
        (next) => {
          if (abort.signal.aborted || !session.getMeta().active) return;
          setSnapshot(next);
          setFields(next.fields);
          setChannelText(next.fields.channels?.join(', ') ?? '');
          setDelivery(
            next.fields.channels === null
              ? 'inherit'
              : next.fields.channels.length === 0
                ? 'app'
                : 'selected',
          );
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
    setChannelText,
    setDelivery,
    setLoading,
  ]);

  function change<K extends keyof TaskEditableFields>(
    key: K,
    value: TaskEditableFields[K],
  ) {
    setFields((current) => ({ ...current, [key]: value }));
  }

  async function submit(event: FormEvent) {
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
    if (delivery === 'selected' && !fields.channels?.length) {
      setError(
        'Enter at least one registered channel name, or choose In app only.',
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
      if (epoch.current === ticket) onSaved(next.task);
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
  const scheduleKind =
    fields.at !== null
      ? 'once'
      : fields.schedule !== null
        ? 'recurring'
        : 'manual';
  if (loading) return <Skeleton label="Loading saved task" />;
  if (!meta.active)
    return (
      <p role="status">
        Workflow access changed. Reopen the editor in the current session.
      </p>
    );
  return (
    <form
      className="task-editor stack capability-section"
      onSubmit={(event) => void submit(event)}
      aria-label={taskId ? 'Edit task' : 'Create task'}
    >
      <header className="capability-header">
        <div>
          <h2>{taskId ? 'Edit task' : 'Create task'}</h2>
          <p>Define the saved workflow before reviewing any changes.</p>
        </div>
      </header>
      {error && <p role="alert">{error}</p>}
      {meta.limit && (
        <p role="alert">
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
          disabled={saving || meta.uncertain}
          onClick={() => setReload((value) => value + 1)}
        >
          Reload saved task
        </Button>
      )}
      <fieldset
        className="stack"
        disabled={
          saving ||
          loading ||
          meta.uncertain ||
          stale ||
          (!!taskId && !snapshot)
        }
      >
        <div className="field-row">
          <Field label="Name">
            <Input
              value={fields.name}
              maxLength={256}
              required
              onChange={(event) => change('name', event.target.value)}
            />
          </Field>
          <Field label="Icon">
            <Input
              value={fields.icon}
              maxLength={32}
              onChange={(event) => change('icon', event.target.value)}
            />
          </Field>
        </div>
        <Field label="Description">
          <textarea
            className="input"
            value={fields.description}
            maxLength={4096}
            rows={2}
            onChange={(event) => change('description', event.target.value)}
          />
        </Field>
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
        {advanced && (
          <p className="muted">
            This task uses an advanced workflow. Its steps, branches, and
            approvals are preserved. Use the existing workflow editor to change
            them.
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
          <div role="group" aria-label="Workflow prompts">
            {fields.prompts.map((prompt, index) => (
              <div key={index}>
                <Field label={`Prompt ${index + 1}`}>
                  <textarea
                    className="input"
                    rows={3}
                    value={prompt}
                    required={!advanced}
                    readOnly={advanced}
                    maxLength={16384}
                    onChange={(event) =>
                      change(
                        'prompts',
                        fields.prompts.map((value, position) =>
                          position === index ? event.target.value : value,
                        ),
                      )
                    }
                  />
                </Field>
                {!advanced && (
                  <div className="actions action-cluster">
                    <Button
                      disabled={index === 0}
                      aria-label={`Move prompt ${index + 1} up`}
                      onClick={() => {
                        const prompts = [...fields.prompts];
                        [prompts[index - 1], prompts[index]] = [
                          prompts[index],
                          prompts[index - 1],
                        ];
                        change('prompts', prompts);
                      }}
                    >
                      Move up
                    </Button>
                    <Button
                      disabled={fields.prompts.length === 1}
                      aria-label={`Remove prompt ${index + 1}`}
                      onClick={() =>
                        change(
                          'prompts',
                          fields.prompts.filter(
                            (_, position) => position !== index,
                          ),
                        )
                      }
                    >
                      Remove
                    </Button>
                  </div>
                )}
              </div>
            ))}
            {!advanced && (
              <Button
                disabled={fields.prompts.length >= 100}
                onClick={() => change('prompts', [...fields.prompts, ''])}
              >
                Add prompt
              </Button>
            )}
          </div>
        )}
        <div className="field-row">
          <Field label="Schedule">
            <Select
              value={scheduleKind}
              onChange={(event) =>
                setFields((current) => ({
                  ...current,
                  schedule:
                    event.target.value === 'recurring' ? 'daily:09:00' : null,
                  at: event.target.value === 'once' ? '' : null,
                }))
              }
            >
              <option value="manual">No schedule</option>
              <option value="recurring">Recurring</option>
              <option value="once">Once</option>
            </Select>
          </Field>
          {scheduleKind === 'recurring' && (
            <Field
              label="Recurring schedule"
              hint="Examples: daily:09:00, weekly:mon:09:00, interval:2, interval_minutes:30, cron:0 9 * * mon"
            >
              <Input
                required
                maxLength={256}
                value={fields.schedule ?? ''}
                onChange={(event) => change('schedule', event.target.value)}
              />
            </Field>
          )}
          {scheduleKind === 'once' && (
            <Field
              label="Date and time"
              hint="Local time. A past date that has not run is scheduled immediately when enabled."
            >
              <Input
                type="datetime-local"
                required
                value={fields.at ?? ''}
                onChange={(event) => change('at', event.target.value)}
              />
            </Field>
          )}
        </div>
        <Field
          label="Delivery"
          hint={
            legacyDelivery
              ? 'This task retains a destination from the existing app. It is preserved when you save.'
              : 'Channel choices use their configured destinations and approval settings.'
          }
        >
          <Select
            disabled={legacyDelivery}
            value={delivery}
            onChange={(event) => {
              setDelivery(event.target.value);
              if (event.target.value === 'selected') {
                change(
                  'channels',
                  channelText
                    .split(',')
                    .map((value) => value.trim())
                    .filter(Boolean),
                );
              } else
                change(
                  'channels',
                  event.target.value === 'inherit' ? null : [],
                );
            }}
          >
            <option value="inherit">
              {legacyDelivery ? 'Saved destination' : 'Use workflow defaults'}
            </option>
            <option value="app">In app only</option>
            <option value="selected">Selected channels</option>
          </Select>
        </Field>
        {delivery === 'selected' && (
          <Field
            label="Channel names"
            hint="Comma-separated registered channel IDs, for example telegram, slack. Availability is checked when delivery runs."
          >
            <Input
              disabled={legacyDelivery}
              value={channelText}
              maxLength={2048}
              onChange={(event) => {
                setChannelText(event.target.value);
                change(
                  'channels',
                  event.target.value
                    .split(',')
                    .map((value) => value.trim())
                    .filter(Boolean),
                );
              }}
            />
          </Field>
        )}
        <div className="field">
          <span>Enabled</span>
          <Toggle
            label="Enabled"
            checked={fields.enabled}
            onChange={(event) => change('enabled', event.target.checked)}
          />
          <small>
            Enabled scheduled tasks use the existing scheduler. Saving does not
            manually run a workflow.
          </small>
        </div>
        {snapshot && (
          <p className="muted">
            Agent profile: {snapshot.agent_profile_id || 'Unspecified'}.
            Approval policy: {snapshot.approval_mode || 'Unspecified'}.
          </p>
        )}
      </fieldset>
      <div className="actions action-cluster">
        <Button
          type="submit"
          variant="primary"
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
        <Button disabled={saving} onClick={onCancel}>
          Cancel
        </Button>
      </div>
      {saving && (
        <p role="status">
          Saving this task. Waiting for its confirmed outcome.
        </p>
      )}
    </form>
  );
}
