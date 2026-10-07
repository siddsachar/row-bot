import { useEffect, useId, useRef, useSyncExternalStore } from 'react';
import {
  useTaskEditSession,
  useTaskEditValue,
  type TaskEditSession,
} from './task-edit-sessions';
import type { TaskSettingsFields, TaskSettingsSnapshot } from '../../api/types';
import { clientError } from '../../api/errors';
import {
  Button,
  Combobox,
  Input,
  Select,
  SettingRow,
  Skeleton,
  Toggle,
  type ComboboxOption,
} from '../../ui/primitives';
import { DangerAction, SettingsDangerZone } from '../settings/anatomy';
import WebhookAddress, {
  type WebhookAddressProps,
  type WebhookConfiguration,
} from './WebhookAddress';

export interface TaskSettingsEditorProps {
  session?: TaskEditSession;
  taskId: string;
  load: (taskId: string, signal?: AbortSignal) => Promise<TaskSettingsSnapshot>;
  review: (
    taskId: string,
    fields: TaskSettingsFields,
    signal?: AbortSignal,
  ) => Promise<TaskSettingsSnapshot>;
  save: (
    taskId: string,
    revision: string,
    profileRevision: string,
    fields: TaskSettingsFields,
  ) => Promise<TaskSettingsSnapshot>;
  rotate: (taskId: string, revision: string) => Promise<TaskSettingsSnapshot>;
  download: (taskId: string, revision: string) => Promise<void>;
  onSaved: (snapshot: TaskSettingsSnapshot) => void;
  onCancel: () => void;
  profileOptions?: ReadonlyArray<{ id: string; label: string }>;
  modelOptions?: ReadonlyArray<{ id: string; label: string }>;
  /** Shown in the page's one-line description. */
  taskName?: string;
  /** The saved webhook's address and reachability (parity rows 20, 52). */
  webhook?: Omit<WebhookAddressProps, 'taskId' | 'readAddress'> & {
    readAddress: (
      taskId: string,
      revision: string,
    ) => Promise<WebhookConfiguration>;
  };
}

export default function TaskSettingsEditor({
  taskId,
  load,
  review,
  save,
  rotate,
  download,
  onSaved,
  onCancel,
  profileOptions = [],
  modelOptions = [],
  taskName = '',
  webhook,
  session: injectedSession,
}: TaskSettingsEditorProps) {
  const session = useTaskEditSession(injectedSession, 'settings', taskId);
  // Pick from lists (U41). A saved value that is not offered any more stays
  // visible, so it is never swapped silently.
  const profileChoices = (current: string) =>
    profileOptions.some((option) => option.id === current) || !current
      ? profileOptions
      : [
          ...profileOptions,
          { id: current, label: `${current} (not available)` },
        ];
  const modelChoicesFor = (current: string | null): ComboboxOption[] => [
    { value: '', label: 'Default model' },
    ...modelOptions.map((option) => ({
      value: option.id,
      label: option.label,
    })),
    ...(current && !modelOptions.some((option) => option.id === current)
      ? [{ value: current, label: `${current} (not available)` }]
      : []),
  ];
  const meta = useSyncExternalStore(session.subscribe, session.getMeta);
  const [snapshot, setSnapshot] = useTaskEditValue<TaskSettingsSnapshot | null>(
    session,
    'snapshot',
    null,
  );
  const [fields, setFields] = useTaskEditValue<TaskSettingsFields | null>(
    session,
    'fields',
    null,
    true,
  );
  const [reviewed, setReviewed] = useTaskEditValue<TaskSettingsSnapshot | null>(
    session,
    'reviewed',
    null,
  );
  const [loading, setLoading] = useTaskEditValue(session, 'loading', true);
  const [busy, setBusy] = useTaskEditValue(session, 'busy', '');
  const [error, setError] = useTaskEditValue(session, 'error', '');
  const [notice, setNotice] = useTaskEditValue(session, 'notice', '');
  const [stale, setStale] = useTaskEditValue(session, 'stale', false);
  const [rotationAccepted, setRotationAccepted] = useTaskEditValue(
    session,
    'rotationAccepted',
    false,
    true,
  );
  const [reload, setReload] = useTaskEditValue(session, 'reload', 0);
  const [pendingKind, setPendingKind] = useTaskEditValue<'save' | 'rotate'>(
    session,
    'pendingKind',
    'save',
  );
  const epoch = useRef(0);
  const reviewEpoch = useRef(0);
  const reviewAbort = useRef<AbortController | null>(null);
  const effectPending = useRef(false);
  const inputId = useId();

  useEffect(() => {
    const abort = session.beginRead(reload);
    if (!abort)
      return () => {
        epoch.current += 1;
        reviewEpoch.current += 1;
        reviewAbort.current?.abort();
        if (session.get<string>('busy', '') === 'review') setBusy('');
      };
    ++epoch.current;
    setLoading(true);
    setSnapshot(null);
    setFields(null);
    setReviewed(null);
    setError('');
    setNotice('');
    setStale(false);
    setRotationAccepted(false);
    setBusy('');
    load(taskId, abort.signal).then(
      (next) => {
        if (abort.signal.aborted || !session.getMeta().active) return;
        setSnapshot(next);
        setFields(next.fields);
        setReviewed(next);
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
      reviewAbort.current?.abort();
      if (session.get<string>('busy', '') === 'review') setBusy('');
      epoch.current += 1;
      reviewEpoch.current += 1;
    };
  }, [
    taskId,
    load,
    reload,
    session,
    injectedSession,
    setLoading,
    setSnapshot,
    setFields,
    setReviewed,
    setError,
    setNotice,
    setStale,
    setRotationAccepted,
    setBusy,
  ]);

  function change<K extends keyof TaskSettingsFields>(
    key: K,
    value: TaskSettingsFields[K],
  ) {
    reviewAbort.current?.abort();
    reviewEpoch.current += 1;
    setBusy('');
    setReviewed(null);
    setNotice('');
    setFields(
      (current) =>
        current && {
          ...current,
          [key]: value,
          ...(key === 'trigger_type' && value !== 'task_complete'
            ? { trigger_task_id: null }
            : {}),
        },
    );
  }

  async function reviewFields() {
    if (
      !fields ||
      effectPending.current ||
      stale ||
      meta.uncertain ||
      !meta.active
    )
      return;
    reviewAbort.current?.abort();
    const abort = new AbortController();
    reviewAbort.current = abort;
    const ticket = ++reviewEpoch.current;
    const generation = epoch.current;
    setBusy('review');
    setError('');
    setReviewed(null);
    let approved: TaskSettingsSnapshot | null = null;
    try {
      const result = await review(taskId, fields, abort.signal);
      if (
        abort.signal.aborted ||
        ticket !== reviewEpoch.current ||
        generation !== epoch.current
      )
        return;
      if (snapshot && result.revision !== snapshot.revision) {
        setStale(true);
        setError(
          'The saved workflow changed. Reload before reviewing these settings.',
        );
        return;
      }
      setReviewed(result);
      setFields(result.fields);
      if (result.profile_available && result.profile_revision)
        approved = result;
    } catch (cause) {
      if (
        abort.signal.aborted ||
        ticket !== reviewEpoch.current ||
        generation !== epoch.current
      )
        return;
      const failure = clientError(cause);
      setError(failure.message);
      if (failure.code === 'task_revision_conflict') setStale(true);
    } finally {
      if (
        !abort.signal.aborted &&
        ticket === reviewEpoch.current &&
        generation === epoch.current
      )
        setBusy('');
    }
    if (approved) await effect('save', approved);
  }

  async function effect(
    kind: 'save' | 'rotate' | 'download',
    approved?: TaskSettingsSnapshot,
  ) {
    const selected = approved ?? reviewed;
    const selectedFields = approved?.fields ?? fields;
    if (
      !snapshot ||
      !selectedFields ||
      effectPending.current ||
      busy ||
      (stale && !meta.uncertain) ||
      meta.busy ||
      !meta.active
    )
      return;
    if (
      !meta.uncertain &&
      kind === 'save' &&
      (!selected?.profile_revision || !selected.profile_available)
    )
      return;
    if (
      !meta.uncertain &&
      kind !== 'save' &&
      JSON.stringify(selectedFields) !== JSON.stringify(snapshot.fields)
    )
      return;
    if (!meta.uncertain && kind === 'rotate' && !rotationAccepted) return;
    effectPending.current = true;
    setBusy(kind);
    setError('');
    setNotice('');
    const generation = epoch.current;
    try {
      if (kind === 'download') {
        await session.run(() => download(taskId, snapshot.revision), true);
        if (generation === epoch.current)
          setNotice(
            'The private webhook configuration was downloaded. Keep it private.',
          );
      } else {
        setPendingKind(kind);
        const result = meta.uncertain
          ? await session.retryOperation<TaskSettingsSnapshot>()
          : await session.run(() =>
              kind === 'save'
                ? save(
                    taskId,
                    snapshot.revision,
                    selected!.profile_revision!,
                    selectedFields,
                  )
                : rotate(taskId, snapshot.revision),
            );
        if (!session.getMeta().active) return;
        setSnapshot(result);
        setFields(result.fields);
        setReviewed(result);
        setRotationAccepted(false);
        session.clean();
        if (kind === 'save') {
          setNotice('Saved workflow settings. No workflow was run.');
          if (generation === epoch.current) onSaved(result);
        } else {
          setNotice(
            'Webhook secret rotated. Download the new configuration and update existing callers.',
          );
        }
      }
    } catch (cause) {
      if (!session.getMeta().active) return;
      const failure = clientError(cause);
      setError(failure.message);
      if (
        ['task_revision_conflict', 'task_settings_profile_conflict'].includes(
          failure.code,
        )
      ) {
        setStale(true);
        setReviewed(null);
      }
    } finally {
      effectPending.current = false;
      setBusy('');
    }
  }

  const mutating = ['save', 'rotate', 'download'].includes(busy);
  const dirty = reviewed === null;
  const unsaved = JSON.stringify(fields) !== JSON.stringify(snapshot?.fields);
  if (!meta.active)
    return (
      <p role="status">
        Workflow access changed. Reopen the editor in the current session.
      </p>
    );
  const locked = mutating || stale || meta.uncertain;
  return (
    <section className="task-settings" aria-label="Workflow settings editor">
      <h2 className="visually-hidden">Workflow settings</h2>
      <div className="task-graph-bar">
        <p className="home-caption">
          {taskName ? `${taskName} · ` : ''}How future runs choose their model,
          approvals, conversation and trigger. Saving never runs it.
        </p>
        <span className="home-page-header-spacer" />
        {!loading && (
          <Button
            variant="ghost"
            className="small"
            disabled={mutating || meta.uncertain}
            onClick={() => setReload((value) => value + 1)}
          >
            Reload saved settings
          </Button>
        )}
      </div>
      {loading && <Skeleton label="Loading workflow settings" />}
      {error && (
        <div className="task-builder-alert" role="alert">
          <p>{error}</p>
          {stale && (
            <p>
              Your draft is retained. Reload the saved settings before
              continuing.
            </p>
          )}
        </div>
      )}
      {notice && <p role="status">{notice}</p>}
      {meta.limit && (
        <p className="task-builder-alert" role="alert">
          This retained draft reached its size limit. Shorten a field before
          adding more content.
        </p>
      )}
      {meta.uncertain && (
        <div className="task-builder-note">
          <p role="status">
            The original settings change is unconfirmed. Your draft is locked
            until its receipt is resolved.
          </p>
          <Button
            className="small"
            disabled={!!busy}
            onClick={() => void effect(pendingKind)}
          >
            Retry original settings change
          </Button>
        </div>
      )}
      {fields && (
        <>
          <fieldset disabled={locked} className="task-settings-group">
            <legend>Model and approvals</legend>
            <SettingRow
              label="Agent profile"
              htmlFor={`${inputId}-profile`}
              description="Its tools and limits apply to every run."
            >
              <Select
                id={`${inputId}-profile`}
                aria-label="Agent profile"
                value={fields.agent_profile_id}
                onChange={(event) =>
                  change('agent_profile_id', event.target.value)
                }
              >
                {profileChoices(fields.agent_profile_id).map((option) => (
                  <option key={option.id} value={option.id}>
                    {option.label}
                  </option>
                ))}
              </Select>
            </SettingRow>
            <SettingRow
              label="Approvals"
              htmlFor={`${inputId}-approval`}
              description={
                fields.approval_mode === 'allow_all'
                  ? 'Auto permits actions without asking when the selected profile allows them; apps still ask when their access says to. Existing sandbox limits still apply.'
                  : 'What happens when a run wants to act.'
              }
            >
              <Select
                id={`${inputId}-approval`}
                aria-label="Approval policy"
                value={fields.approval_mode}
                onChange={(event) =>
                  change(
                    'approval_mode',
                    event.target.value as TaskSettingsFields['approval_mode'],
                  )
                }
              >
                <option value="block">Block actions</option>
                <option value="approve">Ask before actions</option>
                <option value="allow_all">
                  Auto — allow actions within the profile policy
                </option>
              </Select>
            </SettingRow>
            <SettingRow
              label="Model"
              description="Readiness is checked when it runs."
            >
              <Combobox
                label="Model"
                value={fields.model_override ?? ''}
                options={modelChoicesFor(fields.model_override)}
                onChange={(value) => change('model_override', value || null)}
              />
            </SettingRow>
          </fieldset>
          <fieldset disabled={locked} className="task-settings-group">
            <legend>Runs</legend>
            <SettingRow
              label="Reuse a conversation across runs"
              description="Turning this off keeps existing conversations and their content."
            >
              <Toggle
                label="Reuse a conversation across runs"
                checked={fields.persistent_enabled}
                onChange={(event) =>
                  change('persistent_enabled', event.target.checked)
                }
              />
            </SettingRow>
            <SettingRow
              label="Concurrency group"
              htmlFor={`${inputId}-group`}
              description="Runs in the same group wait for each other. Empty keeps the automatic behaviour."
            >
              <Input
                id={`${inputId}-group`}
                aria-label="Concurrency group"
                maxLength={128}
                value={fields.concurrency_group ?? ''}
                onChange={(event) =>
                  change('concurrency_group', event.target.value || null)
                }
              />
            </SettingRow>
            <SettingRow
              label="Trigger"
              htmlFor={`${inputId}-trigger`}
              description={
                fields.trigger_type === 'webhook'
                  ? 'A private secret is created when you save a new webhook trigger. Download its configuration after saving. No webhook request is sent here.'
                  : 'Besides its schedule and manual runs.'
              }
            >
              <Select
                id={`${inputId}-trigger`}
                value={fields.trigger_type}
                onChange={(event) =>
                  change(
                    'trigger_type',
                    event.target.value as TaskSettingsFields['trigger_type'],
                  )
                }
              >
                <option value="none">Manual and scheduled runs only</option>
                <option value="task_complete">
                  When another workflow completes
                </option>
                <option value="webhook">Webhook (HTTP POST)</option>
              </Select>
            </SettingRow>
            {fields.trigger_type === 'task_complete' && (
              <SettingRow
                label="Source workflow ID"
                htmlFor={`${inputId}-source`}
                description="This workflow runs after that one completes."
              >
                <Input
                  id={`${inputId}-source`}
                  maxLength={128}
                  value={fields.trigger_task_id ?? ''}
                  onChange={(event) =>
                    change('trigger_task_id', event.target.value || null)
                  }
                />
              </SettingRow>
            )}
          </fieldset>
          {reviewed && (
            <p role="status" className="home-caption">
              {reviewed.profile_available
                ? `Reviewed effective approval policy: ${reviewed.effective_approval_mode === 'block' ? 'Block' : reviewed.effective_approval_mode === 'approve' ? 'Ask' : 'Auto'}.`
                : 'The saved profile is unavailable. Choose an enabled profile and review again.'}
            </p>
          )}
          {snapshot?.fields.trigger_type === 'webhook' && (
            <fieldset
              disabled={!!busy || stale || dirty || unsaved || meta.uncertain}
              className="task-settings-group"
            >
              <legend>Saved webhook</legend>
              {webhook && snapshot.webhook_configured && (
                <WebhookAddress
                  taskId={taskId}
                  localBase={webhook.localBase}
                  readAddress={() =>
                    webhook.readAddress(taskId, snapshot.revision)
                  }
                  writeClipboard={webhook.writeClipboard}
                  loadTunnel={webhook.loadTunnel}
                  setPublic={webhook.setPublic}
                />
              )}
              <SettingRow
                label="Configuration"
                description={
                  snapshot.webhook_configured
                    ? 'A private webhook secret is configured. Keep the file private.'
                    : 'This webhook has no private secret yet. Rotate it to create one.'
                }
              >
                <Button
                  className="small"
                  disabled={!snapshot.webhook_configured}
                  onClick={() => void effect('download')}
                >
                  Download private webhook configuration
                </Button>
              </SettingRow>
              <SettingsDangerZone anchor="webhook-danger">
                <DangerAction
                  title="Rotate webhook secret"
                  description="Existing callers stop working until they use the new configuration."
                >
                  <label className="task-settings-ack">
                    <input
                      type="checkbox"
                      checked={rotationAccepted}
                      onChange={(event) =>
                        setRotationAccepted(event.target.checked)
                      }
                    />
                    I will update existing callers after rotating the secret
                  </label>
                  <Button
                    variant="danger"
                    className="small"
                    disabled={!rotationAccepted}
                    onClick={() => void effect('rotate')}
                  >
                    Rotate webhook secret
                  </Button>
                </DangerAction>
              </SettingsDangerZone>
            </fieldset>
          )}
        </>
      )}
      <footer className="task-builder-actions">
        <Button
          className="small"
          disabled={mutating}
          onClick={() => {
            reviewAbort.current?.abort();
            onCancel();
          }}
        >
          Cancel
        </Button>
        {fields && (
          <Button
            variant="primary"
            className="small"
            disabled={!!busy || stale || meta.uncertain}
            onClick={() => void reviewFields()}
          >
            {busy === 'save' || busy === 'review'
              ? 'Saving settings…'
              : 'Save settings'}
          </Button>
        )}
      </footer>
    </section>
  );
}
