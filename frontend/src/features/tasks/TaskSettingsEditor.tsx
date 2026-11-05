import {
  useEffect,
  useId,
  useRef,
  useState,
  useSyncExternalStore,
} from 'react';
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
  InlineEmpty,
  Input,
  Select,
  SettingRow,
  Skeleton,
  Toggle,
  type ComboboxOption,
} from '../../ui/primitives';
import { AppIcon } from '../apps/parts';
export type StepApp = { id: string; name: string; icon: string };
import { DangerAction, SettingsDangerZone } from '../settings/anatomy';
import {
  approvalLevel,
  useConfirmFullAutonomy,
  type ApprovalLevel,
} from '../shell/approval-levels';
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
    /** The person turned Hands-off on in its one-time dialog just now. */
    confirmFullAutonomy?: boolean,
  ) => Promise<TaskSettingsSnapshot>;
  rotate: (taskId: string, revision: string) => Promise<TaskSettingsSnapshot>;
  /** Save the webhook's private file; true once it is saved (false: cancelled). */
  download: (taskId: string, revision: string) => Promise<boolean>;
  onSaved: (snapshot: TaskSettingsSnapshot) => void;
  onCancel: () => void;
  profileOptions?: ReadonlyArray<{ id: string; label: string }>;
  /** Favourites first; hidden models carry the group "More models" (F10). */
  modelOptions?: ReadonlyArray<{ id: string; label: string; group?: string }>;
  /** Shown in the page's one-line description. */
  taskName?: string;
  /** The saved webhook's address and reachability (parity rows 20, 52). */
  webhook?: Omit<WebhookAddressProps, 'taskId' | 'readAddress'> & {
    readAddress: (
      taskId: string,
      revision: string,
    ) => Promise<WebhookConfiguration>;
  };
  /** Your code folders, for the workflow's own folder (N9). */
  loadFolders?: (
    signal?: AbortSignal,
  ) => Promise<ReadonlyArray<{ id: string; name: string }>>;
  /** The apps set up here, for "Always ask me" (N9). */
  loadApps?: (signal?: AbortSignal) => Promise<readonly StepApp[]>;
  /** Opened to fix one thing: the Code folder a removed folder left (N9). */
  focus?: 'workspace_id';
}

/** Load a list once for the editor; null until it is known. */
function useLoaded<T>(load?: (signal?: AbortSignal) => Promise<T>): T | null {
  const [value, setValue] = useState<T | null>(null);
  useEffect(() => {
    if (!load) return;
    const abort = new AbortController();
    load(abort.signal).then(
      (found) => !abort.signal.aborted && setValue(found),
      () => undefined,
    );
    return () => abort.abort();
  }, [load]);
  return value;
}

/** The apps whose actions always wait for the person's own approval. */
function AlwaysAskApps({
  apps,
  value,
  onChange,
}: {
  apps: readonly StepApp[] | null;
  value: readonly string[];
  onChange: (next: string[]) => void;
}) {
  if (apps === null) return <Skeleton label="Loading apps" />;
  const chosen = new Set(value);
  const known = new Set(apps.map((app) => app.id));
  // An app that is gone stays listed while it is chosen, so saving never drops it silently.
  const gone = value.filter((id) => !known.has(id));
  if (!apps.length && !gone.length)
    return (
      <InlineEmpty>
        No apps are set up yet. Once one is, you can choose to approve its
        actions yourself on every run.
      </InlineEmpty>
    );
  const set = (id: string, on: boolean) =>
    onChange(
      on ? [...value, id].slice(0, 16) : value.filter((item) => item !== id),
    );
  return (
    <ul className="step-apps">
      {apps.map((app) => (
        <li key={app.id}>
          <AppIcon icon={app.icon} size={16} />
          <Toggle
            label={`Always ask me before ${app.name} acts`}
            checked={chosen.has(app.id)}
            disabled={!chosen.has(app.id) && chosen.size >= 16}
            onChange={(event) => set(app.id, event.target.checked)}
          />
          <span>{app.name}</span>
        </li>
      ))}
      {gone.map((id) => (
        <li key={id}>
          <Toggle
            label="Always ask me before an app that was removed acts"
            checked
            onChange={() => set(id, false)}
          />
          <span>An app that was removed</span>
        </li>
      ))}
    </ul>
  );
}

/** What the saved approvals let this workflow do, in words. */
function approvalWords(mode: string | null) {
  return {
    block:
      'Approvals: Block. This workflow can only read; anything that would change something is refused.',
    approve:
      'Approvals: Ask. This workflow asks you before anything that changes something.',
    auto: "Approvals: Auto. A reviewer approves what matches this workflow's messages; anything else pauses the run for you.",
    full: 'Approvals: Hands-off. This workflow makes every change without asking; each one is recorded.',
  }[approvalLevel(mode)];
}

/** What each level means for a workflow that runs while you're away. */
const LEVEL_NOTES: Record<ApprovalLevel, string> = {
  approve:
    'The run pauses and asks you before anything that changes something.',
  auto: "A reviewer approves what matches the workflow's messages; anything else pauses the run for you.",
  full: 'Nothing pauses for approval; every action runs and is recorded. The profile and sandbox limits still apply.',
  block: 'Anything that would change something is refused.',
};

/** What reaches the workflow's channels under each choice (G8). */
const SEND_RESULTS: Record<
  NonNullable<TaskSettingsFields['send_results']>,
  string
> = {
  every_run: 'Each run sends what it did first, then its reply.',
  needs_me:
    "Only a run that didn't finish, or isn't sure something went through. Approvals still reach you.",
  digest: 'One message from 6 PM with a line for each run that day.',
};

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
  loadFolders,
  loadApps,
  focus,
  session: injectedSession,
}: TaskSettingsEditorProps) {
  const session = useTaskEditSession(injectedSession, 'settings', taskId);
  const folders = useLoaded(loadFolders);
  const apps = useLoaded(loadApps);
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
      ...(option.group ? { group: option.group } : {}),
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
  // Hands-off was turned on in its dialog during this edit: the save says so once.
  const [fullConfirmedNow, setFullConfirmedNow] = useTaskEditValue(
    session,
    'fullConfirmedNow',
    false,
  );
  const confirmFullAutonomy = useConfirmFullAutonomy();
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
  const ready = fields !== null;
  // Opened from a run its removed folder stopped: the Code folder is the one thing to change.
  useEffect(() => {
    if (focus !== 'workspace_id' || !ready) return;
    const field = document.getElementById(`${inputId}-folder`);
    field?.scrollIntoView?.({ block: 'center' });
    field?.focus();
  }, [focus, ready, inputId]);

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
        const saved = await session.run(
          () => download(taskId, snapshot.revision),
          true,
        );
        if (saved && generation === epoch.current)
          setNotice('Keep the webhook file private: it holds its secret.');
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
                    fullConfirmedNow && selectedFields.approval_mode === 'full',
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
          approvals and trigger. Saving never runs it.
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
              description="Its instructions apply to every run. It has the tools a chat has."
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
              description={LEVEL_NOTES[approvalLevel(fields.approval_mode)]}
            >
              <Select
                id={`${inputId}-approval`}
                aria-label="Approval policy"
                value={fields.approval_mode}
                data-level={fields.approval_mode}
                onChange={(event) => {
                  const level = event.target.value as ApprovalLevel;
                  const opener = event.currentTarget;
                  if (
                    level !== 'full' ||
                    snapshot?.full_autonomy_confirmed ||
                    fullConfirmedNow
                  ) {
                    change('approval_mode', level);
                    return;
                  }
                  // The first time on this install: say what it skips, then turn it on.
                  confirmFullAutonomy(() => {
                    setFullConfirmedNow(true);
                    change('approval_mode', 'full');
                  }, opener);
                }}
              >
                <option value="approve">Ask before actions</option>
                <option value="auto">Auto, reviewed</option>
                <option value="full">Hands-off</option>
                <option value="block">Block actions</option>
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
              label="Code folder"
              htmlFor={`${inputId}-folder`}
              description={
                fields.workspace_id &&
                fields.workspace_id === snapshot?.fields.workspace_id &&
                snapshot?.workspace_available === false
                  ? `${snapshot.workspace_name ?? 'This folder'} isn't a code folder any more. Choose another, or None.`
                  : 'Its conversation works in it. A helper agent can choose its own.'
              }
            >
              <Select
                id={`${inputId}-folder`}
                aria-label="Code folder"
                value={fields.workspace_id ?? ''}
                data-unavailable={
                  fields.workspace_id &&
                  snapshot?.workspace_available === false &&
                  fields.workspace_id === snapshot.fields.workspace_id
                    ? 'true'
                    : undefined
                }
                onChange={(event) =>
                  change('workspace_id', event.target.value || null)
                }
              >
                <option value="">None: the conversation's folder</option>
                {(folders ?? []).map((folder) => (
                  <option key={folder.id} value={folder.id}>
                    {folder.name}
                  </option>
                ))}
                {fields.workspace_id &&
                  !(folders ?? []).some(
                    (folder) => folder.id === fields.workspace_id,
                  ) && (
                    <option value={fields.workspace_id}>
                      {snapshot?.workspace_available === false
                        ? `${snapshot.workspace_name ?? 'Saved folder'} (removed)`
                        : (snapshot?.workspace_name ?? 'Saved folder')}
                    </option>
                  )}
              </Select>
            </SettingRow>
            <SettingRow
              label="If Row-Bot is closed at a scheduled time"
              htmlFor={`${inputId}-missed`}
              description={
                fields.missed_runs === 'run_once'
                  ? 'Only the latest missed time runs, once, and says it ran late.'
                  : 'The missed time is skipped; the next one runs as usual.'
              }
            >
              <Select
                id={`${inputId}-missed`}
                aria-label="If Row-Bot is closed at a scheduled time"
                value={fields.missed_runs ?? 'skip'}
                onChange={(event) =>
                  change(
                    'missed_runs',
                    event.target.value as TaskSettingsFields['missed_runs'],
                  )
                }
              >
                <option value="skip">Skip it</option>
                <option value="run_once">Run once when Row-Bot starts</option>
              </Select>
            </SettingRow>
            <SettingRow
              label="Send results"
              htmlFor={`${inputId}-send`}
              description={SEND_RESULTS[fields.send_results ?? 'every_run']}
            >
              <Select
                id={`${inputId}-send`}
                aria-label="Send results"
                value={fields.send_results ?? 'every_run'}
                onChange={(event) =>
                  change(
                    'send_results',
                    event.target.value as TaskSettingsFields['send_results'],
                  )
                }
              >
                <option value="every_run">After every run</option>
                <option value="needs_me">Only when it needs me</option>
                <option value="digest">In a daily digest</option>
              </Select>
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
          <fieldset disabled={locked} className="task-settings-group">
            <legend>Always ask me</legend>
            <p className="home-caption">
              Actions in these apps wait for your own approval on every run,
              even under Auto or Hands-off. The card shows exactly what will
              happen.
            </p>
            <AlwaysAskApps
              apps={loadApps ? apps : []}
              value={fields.always_ask ?? []}
              onChange={(next) => change('always_ask', next)}
            />
          </fieldset>
          {reviewed && (
            <p role="status" className="home-caption">
              {reviewed.profile_available
                ? approvalWords(reviewed.effective_approval_mode)
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
