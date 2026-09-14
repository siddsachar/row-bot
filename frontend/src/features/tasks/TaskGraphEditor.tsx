import { useEffect, useRef, useSyncExternalStore } from 'react';
import {
  useTaskEditSession,
  useTaskEditValue,
  type TaskEditSession,
} from './task-edit-sessions';
import type {
  TaskGraphFields,
  TaskGraphSnapshot,
  TaskGraphStepEdit,
} from '../../api/types';
import { clientError } from '../../api/errors';
import { Button, Field, Input, Select, Skeleton } from '../../ui/primitives';

export interface TaskGraphEditorProps {
  session?: TaskEditSession;
  taskId: string;
  load: (taskId: string, signal?: AbortSignal) => Promise<TaskGraphSnapshot>;
  save: (
    taskId: string,
    expectedRevision: string,
    steps: TaskGraphStepEdit[],
  ) => Promise<TaskGraphSnapshot>;
  onSaved: (snapshot: TaskGraphSnapshot) => void;
  onCancel: () => void;
  onTaskSettings?: () => void;
}

const kinds = {
  prompt: 'Prompt',
  condition: 'Condition',
  approval: 'Approval',
  subtask: 'Run workflow',
  delegate_agent: 'Delegate agent',
  wait_for_agents: 'Wait for agents',
  notify: 'Notification',
};

const emptyFields = (): TaskGraphFields => ({
  prompt: null,
  condition: null,
  message: null,
  next: null,
  if_true: null,
  if_false: null,
  if_approved: null,
  if_denied: null,
  on_error: null,
  task_id: null,
  channel: null,
  objective: null,
  profile: null,
  developer_workspace_id: null,
  editing_safety: null,
  return_mode: null,
  context: null,
  max_retries: null,
  retry_delay_seconds: null,
  timeout_minutes: null,
  timeout_seconds: null,
  pass_output: null,
  run_ids: null,
});

function initialFields(kind: string): TaskGraphFields {
  const fields = emptyFields();
  switch (kind) {
    case 'prompt':
      return {
        ...fields,
        prompt: '',
        on_error: 'stop',
        max_retries: 2,
        retry_delay_seconds: 5,
      };
    case 'condition':
      return {
        ...fields,
        condition: 'not_empty',
        if_true: 'end',
        if_false: 'end',
      };
    case 'approval':
      return { ...fields, message: '', timeout_minutes: 30, if_denied: 'end' };
    case 'subtask':
      return { ...fields, task_id: '', pass_output: true, on_error: 'stop' };
    case 'notify':
      return { ...fields, message: '', channel: 'desktop' };
    case 'delegate_agent':
      return {
        ...fields,
        objective: '',
        profile: 'worker',
        editing_safety: 'profile_default',
        return_mode: 'wait',
        timeout_seconds: 300,
        on_error: 'stop',
      };
    case 'wait_for_agents':
      return { ...fields, run_ids: [], timeout_seconds: 300, on_error: 'stop' };
    default:
      return fields;
  }
}

export default function TaskGraphEditor({
  taskId,
  load,
  save,
  onSaved,
  onCancel,
  onTaskSettings,
  session: injectedSession,
}: TaskGraphEditorProps) {
  const session = useTaskEditSession(injectedSession, 'graph', taskId);
  const meta = useSyncExternalStore(session.subscribe, session.getMeta);
  const [snapshot, setSnapshot] = useTaskEditValue<TaskGraphSnapshot | null>(
    session,
    'snapshot',
    null,
  );
  const [steps, setSteps] = useTaskEditValue<TaskGraphStepEdit[]>(
    session,
    'steps',
    [],
    true,
  );
  const [selected, setSelected] = useTaskEditValue(session, 'selected', '');
  const [kind, setKind] = useTaskEditValue(session, 'kind', 'prompt');
  const [loading, setLoading] = useTaskEditValue(session, 'loading', true);
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
    setLoading(true);
    setError('');
    setStale(false);
    setSnapshot(null);
    setSteps([]);
    load(taskId, abort.signal).then(
      (next) => {
        if (abort.signal.aborted || !session.getMeta().active) return;
        setSnapshot(next);
        setSteps(
          next.steps.map(({ id, type, fields }) => ({ id, type, fields })),
        );
        setSelected(next.steps[0]?.id ?? '');
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
    setLoading,
    setError,
    setStale,
    setSnapshot,
    setSteps,
    setSelected,
  ]);

  const index = steps.findIndex((step) => step.id === selected);
  const step = steps[index];
  const retained = snapshot?.steps.find((item) => item.id === selected);
  const disabled = saving || loading || stale || meta.uncertain || !meta.active;

  function change<K extends keyof TaskGraphFields>(
    field: K,
    value: TaskGraphFields[K],
  ) {
    setSteps((current) =>
      current.map((item) =>
        item.id === selected
          ? { ...item, fields: { ...item.fields, [field]: value } }
          : item,
      ),
    );
  }
  function move(offset: number) {
    setSteps((current) => {
      const next = [...current];
      [next[index], next[index + offset]] = [next[index + offset], next[index]];
      return next;
    });
  }
  async function submit() {
    if (
      !snapshot ||
      saving ||
      loading ||
      (stale && !meta.uncertain) ||
      !meta.active ||
      meta.busy ||
      pending.current
    )
      return;
    pending.current = true;
    setSaving(true);
    setError('');
    const ticket = epoch.current;
    try {
      const next = meta.uncertain
        ? await session.retryOperation<TaskGraphSnapshot>()
        : await session.run(() => save(taskId, snapshot.revision, steps));
      if (!session.getMeta().active) return;
      setSnapshot(next);
      setSteps(
        next.steps.map(({ id, type, fields }) => ({ id, type, fields })),
      );
      session.clean('Saved workflow steps. No workflow was run.');
      if (ticket === epoch.current) onSaved(next);
    } catch (cause) {
      if (!session.getMeta().active) return;
      const failure = clientError(cause);
      setError(failure.message);
      setStale(failure.code === 'task_revision_conflict');
    } finally {
      pending.current = false;
      setSaving(false);
    }
  }

  function text(field: keyof TaskGraphFields, label: string, hint?: string) {
    return (
      <Field label={label} hint={hint}>
        <textarea
          className="input"
          rows={3}
          maxLength={16384}
          value={String(step.fields[field] ?? '')}
          onChange={(event) => change(field, event.target.value)}
        />
      </Field>
    );
  }
  function input(field: keyof TaskGraphFields, label: string, hint?: string) {
    return (
      <Field label={label} hint={hint}>
        <Input
          value={String(step.fields[field] ?? '')}
          maxLength={128}
          onChange={(event) => change(field, event.target.value)}
        />
      </Field>
    );
  }
  function number(
    field:
      | 'max_retries'
      | 'retry_delay_seconds'
      | 'timeout_minutes'
      | 'timeout_seconds',
    label: string,
    min: number,
    max: number,
    fallback: number,
  ) {
    return (
      <Field label={label}>
        <Input
          type="number"
          min={min}
          max={max}
          step={1}
          value={step.fields[field] ?? fallback}
          onChange={(event) =>
            change(
              field,
              event.target.value === '' ? null : Number(event.target.value),
            )
          }
        />
      </Field>
    );
  }
  function choice(
    field: keyof TaskGraphFields,
    label: string,
    options: [string, string][],
    fallback = '',
  ) {
    const value = String(step.fields[field] ?? fallback);
    return (
      <Field label={label}>
        <Select
          value={value}
          onChange={(event) => change(field, event.target.value)}
        >
          {!options.some(([key]) => key === value) && (
            <option value={value}>{value || 'Default'}</option>
          )}
          {options.map(([key, title]) => (
            <option key={key} value={key}>
              {title}
            </option>
          ))}
        </Select>
      </Field>
    );
  }
  function branch(field: keyof TaskGraphFields, label: string, fallback = '') {
    return choice(
      field,
      label,
      [
        ['', 'Continue to the next step'],
        ['end', 'End workflow'],
        ...steps
          .filter((item) => item.id !== selected)
          .map((item): [string, string] => [
            item.id,
            `${kinds[item.type as keyof typeof kinds] ?? item.type} · ${item.id}`,
          ]),
      ],
      fallback,
    );
  }

  if (!meta.active)
    return (
      <p role="status">
        Workflow access changed. Reopen the editor in the current session.
      </p>
    );
  return (
    <section
      aria-label="Workflow graph editor"
      className="task-editor task-graph-editor stack capability-section"
    >
      <header className="capability-header">
        <div>
          <h2>Edit workflow steps</h2>
          <p>
            Save changes to the workflow. Use Run separately when you are ready.
          </p>
        </div>
      </header>
      {snapshot?.notify_only && (
        <p role="status">
          This workflow currently sends a notification only. Change that in task
          settings to run these steps.
        </p>
      )}
      {onTaskSettings && (
        <Button onClick={onTaskSettings} disabled={saving}>
          Schedule and task settings
        </Button>
      )}
      {loading && <Skeleton label="Loading workflow steps" />}
      {meta.limit && (
        <p role="alert">
          This retained draft reached its size limit. Shorten a field before
          adding more content.
        </p>
      )}
      {meta.notice && <p role="status">{meta.notice}</p>}
      {meta.uncertain && (
        <p role="status">
          The original graph save is unconfirmed. Your draft is locked until its
          receipt is resolved.
        </p>
      )}
      {error && (
        <div role="alert">
          <p>{error}</p>
          {stale && (
            <p>
              Your draft is retained. Reload the saved graph before making
              further changes.
            </p>
          )}
        </div>
      )}
      {!loading && (
        <Button
          disabled={saving || meta.uncertain}
          onClick={() => setReload((value) => value + 1)}
        >
          Reload saved graph
        </Button>
      )}
      {snapshot && (
        <>
          <ol aria-label="Workflow step order">
            {steps.map((item, position) => (
              <li key={item.id}>
                <Button
                  variant={item.id === selected ? 'primary' : 'secondary'}
                  aria-pressed={item.id === selected}
                  onClick={() => setSelected(item.id)}
                  disabled={saving}
                >
                  {position + 1}.{' '}
                  {kinds[item.type as keyof typeof kinds] ?? item.type} ·{' '}
                  {item.id}
                </Button>
              </li>
            ))}
          </ol>
          <fieldset disabled={disabled} className="stack">
            <legend>Add a workflow step</legend>
            <div className="field-row">
              <Field label="New step type">
                <Select
                  value={kind}
                  onChange={(event) => setKind(event.target.value)}
                >
                  {Object.entries(kinds).map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </Select>
              </Field>
              <Button
                disabled={steps.length >= 100}
                onClick={() => {
                  const id = `draft_${crypto.randomUUID().replaceAll('-', '')}`;
                  setSteps((current) => [
                    ...current,
                    { id, type: kind, fields: initialFields(kind) },
                  ]);
                  setSelected(id);
                }}
              >
                Add step
              </Button>
            </div>
            {steps.length >= 100 && (
              <p>Up to 100 steps can be reviewed here.</p>
            )}
          </fieldset>
          {step && (
            <fieldset disabled={disabled} className="stack">
              <legend>
                {kinds[step.type as keyof typeof kinds] ?? step.type} ·{' '}
                {step.id}
              </legend>
              <div className="actions action-cluster">
                <Button disabled={index === 0} onClick={() => move(-1)}>
                  Move step up
                </Button>
                <Button
                  disabled={index === steps.length - 1}
                  onClick={() => move(1)}
                >
                  Move step down
                </Button>
                <Button
                  variant="danger"
                  disabled={steps.length === 1}
                  onClick={() => {
                    const remaining = steps.filter(
                      (item) => item.id !== selected,
                    );
                    setSteps(remaining);
                    setSelected(
                      remaining[Math.min(index, remaining.length - 1)]?.id ??
                        '',
                    );
                  }}
                >
                  Remove step
                </Button>
              </div>
              {retained?.retained_fields && (
                <p>
                  Additional saved settings are preserved when you edit these
                  fields.
                </p>
              )}
              {retained?.editable === false ? (
                <p>
                  This saved step type is not editable here. Its existing
                  content is preserved.
                </p>
              ) : (
                <>
                  <Field
                    label="Step type"
                    hint="Changing type resets the editable fields for this step."
                  >
                    <Select
                      value={step.type}
                      onChange={(event) =>
                        setSteps((current) =>
                          current.map((item) =>
                            item.id === selected
                              ? {
                                  ...item,
                                  type: event.target.value,
                                  fields: initialFields(event.target.value),
                                }
                              : item,
                          ),
                        )
                      }
                    >
                      {Object.entries(kinds).map(([value, label]) => (
                        <option key={value} value={value}>
                          {label}
                        </option>
                      ))}
                    </Select>
                  </Field>
                  {step.type === 'prompt' && (
                    <>
                      {text('prompt', 'Prompt')}
                      <div className="field-row">
                        {number('max_retries', 'Maximum retries', 1, 10, 2)}
                        {number(
                          'retry_delay_seconds',
                          'Retry delay (seconds)',
                          0,
                          300,
                          5,
                        )}
                      </div>
                    </>
                  )}
                  {step.type === 'condition' && (
                    <>
                      {text(
                        'condition',
                        'Condition expression',
                        'Examples: contains:done, gte:3, json:status:equals:ok, and:[not_empty,contains:done]. LLM conditions are evaluated only when run.',
                      )}
                      {branch('if_true', 'When true')}
                      {branch('if_false', 'When false')}
                    </>
                  )}
                  {step.type === 'approval' && (
                    <>
                      {text('message', 'Approval message')}
                      {number(
                        'timeout_minutes',
                        'Approval timeout (minutes, 0 means no timeout)',
                        0,
                        1440,
                        30,
                      )}
                      {branch('if_approved', 'When approved')}
                      {branch('if_denied', 'When denied', 'end')}
                    </>
                  )}
                  {step.type === 'subtask' && (
                    <>
                      {input('task_id', 'Workflow ID')}
                      <Field label="Pass previous output">
                        <Select
                          value={String(step.fields.pass_output ?? true)}
                          onChange={(event) =>
                            change('pass_output', event.target.value === 'true')
                          }
                        >
                          <option value="true">Pass output</option>
                          <option value="false">Do not pass output</option>
                        </Select>
                      </Field>
                    </>
                  )}
                  {step.type === 'notify' && (
                    <>
                      {text('message', 'Notification message')}
                      {input(
                        'channel',
                        'Notification channel',
                        'Use an existing channel ID, or desktop for an app notification.',
                      )}
                    </>
                  )}
                  {step.type === 'delegate_agent' && (
                    <>
                      {text('objective', 'Agent objective')}
                      {input('profile', 'Agent profile ID')}
                      {input(
                        'developer_workspace_id',
                        'Developer workspace ID',
                      )}
                      {choice(
                        'editing_safety',
                        'Editing safety',
                        [
                          ['profile_default', 'Profile default'],
                          ['read_only', 'Read only'],
                          ['single_writer', 'Single writer'],
                          ['worktree', 'Worktree'],
                        ],
                        'profile_default',
                      )}
                      {choice(
                        'return_mode',
                        'Agent return mode',
                        [
                          ['wait', 'Wait for result'],
                          ['background', 'Continue in background'],
                        ],
                        'wait',
                      )}
                      {text('context', 'Additional context')}
                    </>
                  )}
                  {step.type === 'wait_for_agents' && (
                    <Field
                      label="Agent run IDs"
                      hint="Separate existing run IDs with commas. Leave empty to wait for agents returned by earlier steps."
                    >
                      <Input
                        value={step.fields.run_ids?.join(', ') ?? ''}
                        maxLength={12900}
                        onChange={(event) =>
                          change(
                            'run_ids',
                            event.target.value
                              .split(',')
                              .map((value) => value.trim())
                              .filter(Boolean),
                          )
                        }
                      />
                    </Field>
                  )}
                  {['delegate_agent', 'wait_for_agents'].includes(step.type) &&
                    number(
                      'timeout_seconds',
                      'Timeout (seconds)',
                      1,
                      7200,
                      300,
                    )}
                  {[
                    'prompt',
                    'subtask',
                    'delegate_agent',
                    'wait_for_agents',
                  ].includes(step.type) &&
                    choice(
                      'on_error',
                      'On error',
                      [
                        ['stop', 'Stop workflow'],
                        ['skip', 'Continue'],
                      ],
                      step.type === 'prompt' ? 'skip' : 'stop',
                    )}
                  {!['condition', 'approval'].includes(step.type) &&
                    branch('next', 'Next step')}
                </>
              )}
            </fieldset>
          )}
          <p>
            Removing a referenced step requires updating its branches and named
            output references before saving.
          </p>
        </>
      )}
      <div className="actions action-cluster">
        <Button
          variant="primary"
          disabled={
            !snapshot ||
            saving ||
            loading ||
            (stale && !meta.uncertain) ||
            steps.length === 0
          }
          onClick={() => void submit()}
        >
          {saving
            ? 'Saving graph…'
            : meta.uncertain
              ? 'Retry original graph save'
              : 'Save graph'}
        </Button>
        <Button disabled={saving} onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </section>
  );
}
