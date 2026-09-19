import { Link } from 'react-router-dom';
import {
  BarChart3,
  BookOpen,
  Bot,
  AppWindow,
  Calculator,
  CalendarClock,
  ChevronDown,
  CloudSun,
  FileKey,
  FileText,
  GitBranch,
  Globe2,
  HardDrive,
  Hammer,
  Import,
  ListChecks,
  Mic,
  MonitorCog,
  Moon,
  Network,
  Radio,
  RefreshCw,
  Search,
  ShieldCheck,
  SlidersHorizontal,
  SquareTerminal,
  Trash2,
  Volume2,
  Wrench,
} from 'lucide-react';
import {
  useEffect,
  useRef,
  useState,
  type ComponentType,
  type ReactNode,
} from 'react';
import { clientError } from '../../api/errors';
import type {
  SettingsMutationReceipt,
  SettingsMutationRequest,
  SettingsMutationReview,
  SettingsSnapshot,
} from '../../api/types';
import { Button, Field, Input, Select } from '../../ui/primitives';

type Icon = ComponentType<{ size?: number; 'aria-hidden'?: boolean }>;
export type SettingsPage = SettingsMutationRequest['page'];
export type SettingsValue = SettingsMutationRequest['value'];
export class SettingsDraftOwner {
  private readonly values = new Map<string, SettingsValue>();

  has(page: SettingsPage, field: string) {
    return this.values.has(`${page}:${field}`);
  }

  read(page: SettingsPage, field: string) {
    const value = this.values.get(`${page}:${field}`);
    return Array.isArray(value) ? [...value] : value;
  }

  write(page: SettingsPage, field: string, value: SettingsValue) {
    this.values.set(
      `${page}:${field}`,
      Array.isArray(value) ? [...value] : value,
    );
  }

  discard(page: SettingsPage, field: string) {
    this.values.delete(`${page}:${field}`);
  }

  clear() {
    this.values.clear();
  }
}
export type SettingsMutationIO = {
  revision: string;
  page: SettingsPage;
  review: (
    request: SettingsMutationRequest,
    signal?: AbortSignal,
  ) => Promise<SettingsMutationReview>;
  execute: (
    request: SettingsMutationRequest,
    review: SettingsMutationReview,
    commandId: string,
    signal?: AbortSignal,
  ) => Promise<SettingsMutationReceipt>;
  receipt: (
    commandId: string,
    signal?: AbortSignal,
  ) => Promise<SettingsMutationReceipt>;
  drafts: SettingsDraftOwner;
  onSnapshot: (snapshot: SettingsSnapshot) => void;
};
export type SettingsFolderGrant = {
  status: 'selected' | 'cancelled' | 'unavailable';
  grant_id?: string | null;
  name?: string | null;
};
export type SettingsFolderPicker = (
  signal?: AbortSignal,
) => Promise<SettingsFolderGrant>;

function StateChip({
  active,
  children,
  warning = false,
}: {
  active?: boolean | null;
  children: ReactNode;
  warning?: boolean;
}) {
  return (
    <span
      className={`status-chip ${active ? 'success' : warning ? 'warning' : ''}`}
    >
      {children}
    </span>
  );
}

function Section({
  title,
  description,
  icon: Icon,
  tone = '',
  children,
}: {
  title: string;
  description: string;
  icon: Icon;
  tone?: 'warning' | 'danger' | '';
  children: ReactNode;
}) {
  const id = `settings-snapshot-${title.toLowerCase().replace(/[^a-z0-9]+/g, '-')}`;
  return (
    <section
      className={`settings-snapshot-section stack ${tone ? `is-${tone}` : ''}`}
      aria-labelledby={id}
    >
      <header className="settings-snapshot-heading">
        <Icon size={18} aria-hidden />
        <div>
          <h3 id={id}>{title}</h3>
          <p>{description}</p>
        </div>
      </header>
      {children}
    </section>
  );
}

function Fact({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="settings-fact">
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}
function Facts({ children }: { children: ReactNode }) {
  return <dl className="settings-facts">{children}</dl>;
}
function VoiceModelRow({
  title,
  description,
  provider,
  status,
  ready,
}: {
  title: string;
  description: string;
  provider: string;
  status: string;
  ready?: boolean;
}) {
  return (
    <li>
      <Mic size={17} aria-hidden />
      <div>
        <strong>{title}</strong>
        <small>{description}</small>
      </div>
      <StateChip>{provider}</StateChip>
      <StateChip active={ready} warning={ready === false}>
        {status}
      </StateChip>
    </li>
  );
}
function enabledLabel(value: boolean | null | undefined) {
  return value == null ? 'Status unavailable' : value ? 'Enabled' : 'Disabled';
}
function configuredLabel(value: boolean | null | undefined) {
  return value == null
    ? 'Saved state unavailable'
    : value
      ? 'Configured'
      : 'Not configured';
}
function savedStateLabel(value: string | null | undefined) {
  if (!value || value === 'cached_unknown' || value === 'not_checked')
    return 'Not checked';
  return value
    .replaceAll('_', ' ')
    .replace(/(^|\s)\S/g, (letter) => letter.toUpperCase());
}
function accountStateLabel(value: string) {
  return (
    {
      not_configured: 'Not configured',
      not_authenticated: 'Not authenticated',
      configured_unchecked: 'Configured · not checked',
      saved_unchecked: 'Saved · not checked',
      expired: 'Expired',
      unavailable: 'Unavailable',
    }[value] ?? savedStateLabel(value)
  );
}
function dateOnly(value: string | null) {
  if (!value) return 'never';
  const date = value.match(/^\d{4}-\d{2}-\d{2}/)?.[0];
  return date ?? value;
}
function formattedDateTime(value: string | null) {
  if (!value) return 'Never';
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return value;
  return new Intl.DateTimeFormat('en-US', {
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date);
}
function versionLabel(value: string) {
  return value.toLowerCase().startsWith('v') ? value : `v${value}`;
}
const operationLabels: Record<string, string> = {
  read_file: 'Read files',
  list_directory: 'List folders',
  file_search: 'Search files',
  write_file: 'Write files',
  copy_file: 'Copy files',
  export_to_pdf: 'Export to PDF',
  move_file: 'Move files',
  file_delete: 'Delete files',
  search_gmail: 'Search email',
  get_gmail_message: 'Read message',
  get_gmail_thread: 'Read thread',
  create_gmail_draft: 'Create draft',
  send_gmail_message: 'Send email',
  get_current_datetime: 'Current date and time',
  search_events: 'Search events',
  create_calendar_event: 'Create event',
  create_calendar_events: 'Create multiple events',
  update_calendar_event: 'Update event',
  move_calendar_event: 'Move event',
  delete_calendar_event: 'Delete event',
  x_search: 'Search posts',
  x_read_tweet: 'Read post',
  x_timeline: 'Home timeline',
  x_mentions: 'Mentions',
  x_user_info: 'User information',
  x_post_tweet: 'Create post',
  x_reply: 'Reply',
  x_quote: 'Quote post',
  x_delete_tweet: 'Delete post',
  x_like: 'Like',
  x_unlike: 'Remove like',
  x_repost: 'Repost',
  x_unrepost: 'Undo repost',
  x_bookmark: 'Bookmark',
  x_unbookmark: 'Remove bookmark',
};
const utilityPresentation: Record<
  string,
  { label: string; description: string }
> = {
  task: {
    label: 'Tasks',
    description: 'Create and manage scheduled or one-off tasks.',
  },
  url_reader: {
    label: 'URL Reader',
    description: 'Read explicitly requested web pages.',
  },
  calculator: {
    label: 'Calculator',
    description: 'Evaluate calculations locally.',
  },
  weather: {
    label: 'Weather',
    description: 'Look up weather when explicitly requested.',
  },
  chart: { label: 'Charts', description: 'Create charts from supplied data.' },
  system_info: {
    label: 'System Info',
    description: 'Read bounded host information.',
  },
  conversation_search: {
    label: 'Conversation Search',
    description: 'Search saved conversations.',
  },
  custom_tool_builder: {
    label: 'Custom Tool Builder',
    description: 'Build reviewed local tools.',
  },
};
function operationLabel(value: string) {
  return operationLabels[value] ?? savedStateLabel(value);
}
function sameValue(left: SettingsValue, right: SettingsValue) {
  return JSON.stringify(left) === JSON.stringify(right);
}

function SavedSetting({
  mutation,
  field,
  label,
  value,
  hint,
  validate,
  group = false,
  onDraftChange,
  children,
}: {
  mutation: SettingsMutationIO;
  field: string;
  label: string;
  value: SettingsValue;
  hint?: string;
  group?: boolean;
  validate?: (value: SettingsValue) => string;
  onDraftChange?: (value: SettingsValue) => void;
  children: (state: {
    value: SettingsValue;
    setValue: (value: SettingsValue) => void;
    disabled: boolean;
  }) => ReactNode;
}) {
  const [draft, setDraft] = useState<SettingsValue>(() =>
    mutation.drafts.has(mutation.page, field)
      ? (mutation.drafts.read(mutation.page, field) as SettingsValue)
      : value,
  );
  const [review, setReview] = useState<SettingsMutationReview | null>(null);
  const [reviewedRequest, setReviewedRequest] =
    useState<SettingsMutationRequest | null>(null);
  const [busy, setBusy] = useState<'review' | 'save' | ''>('');
  const [pendingCommand, setPendingCommand] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const abort = useRef<AbortController | null>(null);
  const dirty = !sameValue(draft, value);

  useEffect(() => {
    if (!dirty && !busy) {
      mutation.drafts.discard(mutation.page, field);
      setDraft(value);
    }
  }, [busy, dirty, field, mutation.drafts, mutation.page, value]);
  useEffect(() => {
    if (review && review.settings_revision !== mutation.revision) {
      setReview(null);
      setReviewedRequest(null);
      setNotice('Saved Settings changed. Review this draft again.');
    }
  }, [mutation.revision, review]);
  useEffect(() => () => abort.current?.abort(), []);
  function change(next: SettingsValue) {
    setDraft(next);
    onDraftChange?.(next);
    if (sameValue(next, value)) mutation.drafts.discard(mutation.page, field);
    else mutation.drafts.write(mutation.page, field, next);
    setReview(null);
    setReviewedRequest(null);
    setError('');
    setNotice('');
  }
  async function reviewChange() {
    if (!dirty || busy) return;
    const validation = validate?.(draft) ?? '';
    if (validation) {
      setError(validation);
      return;
    }
    const request: SettingsMutationRequest = {
      settings_revision: mutation.revision,
      page: mutation.page,
      field,
      value: Array.isArray(draft) ? [...draft] : draft,
    };
    abort.current?.abort();
    abort.current = new AbortController();
    setBusy('review');
    setError('');
    setNotice('');
    try {
      const result = await mutation.review(request, abort.current.signal);
      if (
        result.operation !== 'settings.update' ||
        result.settings_revision !== request.settings_revision ||
        result.page !== request.page ||
        result.field !== request.field
      )
        throw { code: 'revision_conflict' };
      setReview(result);
      setReviewedRequest(request);
      setNotice(`Review ready: ${result.value_summary}`);
    } catch (cause) {
      if (!abort.current.signal.aborted) setError(clientError(cause).message);
    } finally {
      setBusy('');
    }
  }
  async function save() {
    if (!review || !reviewedRequest || busy) return;
    setBusy('save');
    setError('');
    const commandId = crypto.randomUUID();
    setPendingCommand(commandId);
    try {
      const receipt = await mutation.execute(
        reviewedRequest,
        review,
        commandId,
      );
      setReview(null);
      setReviewedRequest(null);
      if (receipt.status === 'completed' && receipt.snapshot) {
        setPendingCommand('');
        mutation.drafts.discard(mutation.page, field);
        mutation.onSnapshot(receipt.snapshot);
        setNotice(`${label} saved.`);
      } else if (receipt.status === 'partial')
        setNotice(
          'The outcome is unconfirmed. Reload the saved snapshot before trying again.',
        );
      else {
        setPendingCommand('');
        setError(
          'The reviewed change was rejected. Reload and review it again.',
        );
      }
    } catch (cause) {
      setError(clientError(cause).message);
      setNotice(
        'The outcome is unconfirmed. This change was not automatically repeated.',
      );
    } finally {
      setBusy('');
    }
  }
  async function checkReceipt() {
    if (!pendingCommand || busy) return;
    abort.current?.abort();
    abort.current = new AbortController();
    setBusy('save');
    setError('');
    try {
      const receipt = await mutation.receipt(
        pendingCommand,
        abort.current.signal,
      );
      if (receipt.status === 'partial') {
        setNotice('The original outcome is still unconfirmed.');
        return;
      }
      setPendingCommand('');
      if (receipt.status === 'completed' && receipt.snapshot) {
        mutation.drafts.discard(mutation.page, field);
        mutation.onSnapshot(receipt.snapshot);
        setNotice(`${label} save confirmed.`);
      } else {
        setError('The original reviewed change was rejected.');
      }
    } catch (cause) {
      if (!abort.current.signal.aborted) setError(clientError(cause).message);
    } finally {
      setBusy('');
    }
  }
  return (
    <div className="settings-saved-control" aria-busy={!!busy}>
      {group ? (
        <fieldset className="field settings-choice-field">
          <legend>{label}</legend>
          {children({
            value: draft,
            setValue: change,
            disabled: !!busy || !!pendingCommand,
          })}
          {hint && <small>{hint}</small>}
        </fieldset>
      ) : (
        <Field label={label} hint={hint}>
          {children({
            value: draft,
            setValue: change,
            disabled: !!busy || !!pendingCommand,
          })}
        </Field>
      )}
      <div className="settings-control-actions">
        {dirty && !review && !pendingCommand && (
          <Button onClick={() => void reviewChange()} disabled={!!busy}>
            {busy === 'review' ? 'Reviewing…' : 'Review change'}
          </Button>
        )}
        {review && (
          <>
            <Button
              variant="primary"
              onClick={() => void save()}
              disabled={!!busy}
            >
              {busy === 'save' ? 'Saving…' : 'Save reviewed change'}
            </Button>
            <Button
              variant="ghost"
              onClick={() => {
                setReview(null);
                setReviewedRequest(null);
                setNotice('');
              }}
              disabled={!!busy}
            >
              Edit
            </Button>
          </>
        )}
        {pendingCommand && (
          <Button onClick={() => void checkReceipt()} disabled={!!busy}>
            {busy === 'save' ? 'Checking…' : 'Check original receipt'}
          </Button>
        )}
        {dirty && !pendingCommand && (
          <Button
            variant="ghost"
            onClick={() => change(value)}
            disabled={!!busy}
          >
            Revert
          </Button>
        )}
      </div>
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
    </div>
  );
}

function ReviewedSettingsAction({
  mutation,
  field,
  label,
  description,
  variant,
}: {
  mutation: SettingsMutationIO;
  field: string;
  label: string;
  description: string;
  variant?: 'danger' | 'primary' | 'ghost';
}) {
  const [review, setReview] = useState<SettingsMutationReview | null>(null);
  const [request, setRequest] = useState<SettingsMutationRequest | null>(null);
  const [pendingCommand, setPendingCommand] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const abort = useRef<AbortController | null>(null);
  useEffect(() => () => abort.current?.abort(), []);
  useEffect(() => {
    if (review && review.settings_revision !== mutation.revision) {
      setReview(null);
      setRequest(null);
      setMessage('Saved Settings changed. Review this action again.');
    }
  }, [mutation.revision, review]);

  async function reviewAction() {
    if (busy || pendingCommand) return;
    const next: SettingsMutationRequest = {
      settings_revision: mutation.revision,
      page: mutation.page,
      field,
      value: true,
    };
    abort.current?.abort();
    abort.current = new AbortController();
    setBusy(true);
    setError('');
    setMessage('');
    try {
      const result = await mutation.review(next, abort.current.signal);
      if (
        result.settings_revision !== next.settings_revision ||
        result.page !== next.page ||
        result.field !== next.field
      )
        throw { code: 'revision_conflict' };
      setReview(result);
      setRequest(next);
      setMessage(`Review ready: ${result.value_summary}`);
    } catch (cause) {
      if (!abort.current.signal.aborted) setError(clientError(cause).message);
    } finally {
      setBusy(false);
    }
  }

  async function executeAction() {
    if (!review || !request || busy) return;
    const commandId = crypto.randomUUID();
    setBusy(true);
    setPendingCommand(commandId);
    setError('');
    try {
      const receipt = await mutation.execute(request, review, commandId);
      setReview(null);
      setRequest(null);
      if (receipt.status === 'completed' && receipt.snapshot) {
        setPendingCommand('');
        mutation.onSnapshot(receipt.snapshot);
        setMessage(`${label} completed.`);
      } else if (receipt.status === 'partial') {
        setMessage('The outcome is unconfirmed. Check the original receipt.');
      } else {
        setPendingCommand('');
        setError('The reviewed action was rejected.');
      }
    } catch (cause) {
      setError(clientError(cause).message);
      setMessage('The outcome is unconfirmed. This action was not repeated.');
    } finally {
      setBusy(false);
    }
  }

  async function checkReceipt() {
    if (!pendingCommand || busy) return;
    setBusy(true);
    setError('');
    try {
      const receipt = await mutation.receipt(pendingCommand);
      if (receipt.status === 'partial') {
        setMessage('The original outcome is still unconfirmed.');
      } else {
        setPendingCommand('');
        if (receipt.status === 'completed' && receipt.snapshot) {
          mutation.onSnapshot(receipt.snapshot);
          setMessage(`${label} confirmed.`);
        } else setError('The original reviewed action was rejected.');
      }
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="settings-reviewed-action" aria-busy={busy}>
      <p className="settings-help">{description}</p>
      {!review && !pendingCommand && (
        <Button
          variant={variant}
          disabled={busy}
          onClick={() => void reviewAction()}
        >
          Review {label}
        </Button>
      )}
      {review && (
        <div className="settings-control-actions">
          <Button
            variant="primary"
            disabled={busy}
            onClick={() => void executeAction()}
          >
            {busy ? 'Working…' : `Run reviewed ${label}`}
          </Button>
          <Button
            variant="ghost"
            disabled={busy}
            onClick={() => {
              setReview(null);
              setRequest(null);
              setMessage('');
            }}
          >
            Cancel
          </Button>
        </div>
      )}
      {pendingCommand && (
        <Button disabled={busy} onClick={() => void checkReceipt()}>
          {busy ? 'Checking…' : 'Check original receipt'}
        </Button>
      )}
      {error && <p role="alert">{error}</p>}
      {message && <p role="status">{message}</p>}
    </div>
  );
}

function TextSetting({
  mutation,
  field,
  label,
  value,
  hint,
  maxLength,
  multiline = false,
}: {
  mutation: SettingsMutationIO;
  field: string;
  label: string;
  value: string;
  hint?: string;
  maxLength?: number;
  multiline?: boolean;
}) {
  return (
    <SavedSetting
      mutation={mutation}
      field={field}
      label={label}
      value={value}
      hint={hint}
    >
      {({ value: draft, setValue, disabled }) =>
        multiline ? (
          <textarea
            className="input settings-textarea"
            value={String(draft)}
            maxLength={maxLength}
            disabled={disabled}
            onChange={(event) => setValue(event.target.value)}
          />
        ) : (
          <Input
            value={String(draft)}
            maxLength={maxLength}
            disabled={disabled}
            onChange={(event) => setValue(event.target.value)}
          />
        )
      }
    </SavedSetting>
  );
}

function SelectSetting({
  mutation,
  field,
  label,
  value,
  options,
  hint,
  onDraftChange,
}: {
  mutation: SettingsMutationIO;
  field: string;
  label: string;
  value: string;
  options: { value: string; label: string }[];
  hint?: string;
  onDraftChange?: (value: string) => void;
}) {
  const available = options.some((option) => option.value === value)
    ? options
    : [{ value, label: value || 'Not selected' }, ...options];
  return (
    <SavedSetting
      mutation={mutation}
      field={field}
      label={label}
      value={value}
      hint={hint}
      onDraftChange={(next) => onDraftChange?.(String(next))}
    >
      {({ value: draft, setValue, disabled }) => (
        <Select
          value={String(draft)}
          disabled={disabled}
          onChange={(event) => setValue(event.target.value)}
        >
          {available.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </Select>
      )}
    </SavedSetting>
  );
}

function RadioSetting({
  mutation,
  field,
  label,
  value,
  options,
}: {
  mutation: SettingsMutationIO;
  field: string;
  label: string;
  value: string;
  options: { value: string; label: string }[];
}) {
  return (
    <SavedSetting
      mutation={mutation}
      field={field}
      label={label}
      value={value}
      group
    >
      {({ value: draft, setValue, disabled }) => (
        <div className="settings-choice-grid">
          {options.map((option) => (
            <label className="check-field" key={option.value}>
              <input
                type="radio"
                name={`${mutation.page}-${field}`}
                value={option.value}
                checked={draft === option.value}
                disabled={disabled}
                onChange={(event) => setValue(event.target.value)}
              />
              {option.label}
            </label>
          ))}
        </div>
      )}
    </SavedSetting>
  );
}

function SwitchSetting({
  mutation,
  field,
  label,
  value,
  hint,
}: {
  mutation: SettingsMutationIO;
  field: string;
  label: string;
  value: boolean;
  hint?: string;
}) {
  return (
    <SavedSetting
      mutation={mutation}
      field={field}
      label={label}
      value={value}
      hint={hint}
    >
      {({ value: draft, setValue, disabled }) => (
        <span className="check-field settings-switch-field">
          <input
            aria-label={label}
            type="checkbox"
            checked={Boolean(draft)}
            disabled={disabled}
            onChange={(event) => setValue(event.target.checked)}
          />
          {draft ? 'Enabled' : 'Disabled'}
        </span>
      )}
    </SavedSetting>
  );
}

function NumberSetting({
  mutation,
  field,
  label,
  value,
  min,
  max,
  step = 1,
  optional = false,
}: {
  mutation: SettingsMutationIO;
  field: string;
  label: string;
  value: number | null;
  min: number;
  max: number;
  step?: number;
  optional?: boolean;
}) {
  return (
    <SavedSetting
      mutation={mutation}
      field={field}
      label={label}
      value={value}
      validate={(draft) =>
        draft == null ||
        (typeof draft === 'number' && draft >= min && draft <= max)
          ? ''
          : `Enter a value from ${min} to ${max}.`
      }
    >
      {({ value: draft, setValue, disabled }) => (
        <Input
          type="number"
          value={draft == null ? '' : String(draft)}
          min={min}
          max={max}
          step={step}
          disabled={disabled}
          placeholder={optional ? 'Automatic' : undefined}
          onChange={(event) =>
            setValue(
              optional && event.target.value === ''
                ? null
                : event.target.valueAsNumber,
            )
          }
        />
      )}
    </SavedSetting>
  );
}

function ChoiceListSetting({
  mutation,
  field,
  label,
  value,
  options,
}: {
  mutation: SettingsMutationIO;
  field: string;
  label: string;
  value: string[];
  options: string[];
}) {
  return (
    <SavedSetting
      mutation={mutation}
      field={field}
      label={label}
      value={value}
      group
    >
      {({ value: draft, setValue, disabled }) => {
        const selected = draft as string[];
        return (
          <div className="settings-choice-grid">
            {options.map((option) => (
              <label className="check-field" key={option}>
                <input
                  type="checkbox"
                  checked={selected.includes(option)}
                  disabled={disabled}
                  onChange={(event) =>
                    setValue(
                      event.target.checked
                        ? [...selected, option]
                        : selected.filter((item) => item !== option),
                    )
                  }
                />
                {operationLabel(option)}
              </label>
            ))}
          </div>
        );
      }}
    </SavedSetting>
  );
}

function GroupedOperationSetting({
  mutation,
  field,
  label,
  value,
  options,
}: {
  mutation: SettingsMutationIO;
  field: string;
  label: string;
  value: string[];
  options: string[];
}) {
  const known = new Set([
    'read_file',
    'list_directory',
    'file_search',
    'write_file',
    'copy_file',
    'export_to_pdf',
    'move_file',
    'file_delete',
  ]);
  const groups = [
    {
      label: 'Read-only',
      options: options.filter((option) =>
        ['read_file', 'list_directory', 'file_search'].includes(option),
      ),
    },
    {
      label: 'Write',
      options: options.filter((option) =>
        ['write_file', 'copy_file', 'export_to_pdf'].includes(option),
      ),
    },
    {
      label: 'Destructive',
      options: options.filter((option) =>
        ['move_file', 'file_delete'].includes(option),
      ),
    },
    {
      label: 'Other',
      options: options.filter((option) => !known.has(option)),
    },
  ].filter((group) => group.options.length);
  return (
    <SavedSetting
      mutation={mutation}
      field={field}
      label={label}
      value={value}
      group
    >
      {({ value: draft, setValue, disabled }) => {
        const selected = draft as string[];
        return (
          <div className="settings-operation-groups">
            {groups.map((group) => (
              <div key={group.label}>
                <strong>{group.label}</strong>
                {group.options.map((option) => (
                  <label className="check-field" key={option}>
                    <input
                      type="checkbox"
                      checked={selected.includes(option)}
                      disabled={disabled}
                      onChange={(event) =>
                        setValue(
                          event.target.checked
                            ? [...selected, option]
                            : selected.filter((item) => item !== option),
                        )
                      }
                    />
                    {operationLabel(option)}
                  </label>
                ))}
              </div>
            ))}
          </div>
        );
      }}
    </SavedSetting>
  );
}

function DelimitedListSetting({
  mutation,
  field,
  label,
  value,
  hint,
}: {
  mutation: SettingsMutationIO;
  field: string;
  label: string;
  value: string[];
  hint?: string;
}) {
  return (
    <SavedSetting
      mutation={mutation}
      field={field}
      label={label}
      value={value}
      hint={hint}
    >
      {({ value: draft, setValue, disabled }) => (
        <Input
          value={(draft as string[]).join(', ')}
          disabled={disabled}
          onChange={(event) =>
            setValue(
              event.target.value
                .split(',')
                .map((item) => item.trim())
                .filter(Boolean),
            )
          }
        />
      )}
    </SavedSetting>
  );
}

function SecretSetting({
  mutation,
  field,
  label,
  configured,
  source,
}: {
  mutation: SettingsMutationIO;
  field: string;
  label: string;
  configured: boolean;
  source?: string | null;
}) {
  const [editing, setEditing] = useState(() =>
    mutation.drafts.has(mutation.page, field),
  );
  return (
    <div className="settings-secret-control">
      <div className="settings-secret-summary">
        <div className="settings-summary-strip">
          <StateChip active={configured} warning={!configured}>
            {configured ? 'Saved · masked' : 'Not configured'}
          </StateChip>
          {source && <StateChip>{source}</StateChip>}
        </div>
        {!editing && (
          <Button variant="ghost" onClick={() => setEditing(true)}>
            {configured ? `Replace or remove ${label}` : `Add ${label}`}
          </Button>
        )}
      </div>
      {editing && (
        <SavedSetting
          mutation={mutation}
          field={field}
          label={label}
          value=""
          hint="Write-only. The saved value is never returned to this client."
          group
        >
          {({ value, setValue, disabled }) => (
            <div className="settings-secret-input-row">
              <Input
                aria-label={label}
                type="password"
                value={value == null ? '' : String(value)}
                disabled={disabled}
                autoComplete="new-password"
                placeholder={
                  configured ? 'Enter a replacement' : 'Enter a value'
                }
                onChange={(event) => setValue(event.target.value)}
              />
              {configured && value !== null && (
                <Button
                  variant="danger"
                  disabled={disabled}
                  onClick={() => setValue(null)}
                >
                  Remove saved credential
                </Button>
              )}
              <Button
                variant="ghost"
                disabled={disabled}
                onClick={() => {
                  mutation.drafts.discard(mutation.page, field);
                  setEditing(false);
                }}
              >
                Cancel credential edit
              </Button>
            </div>
          )}
        </SavedSetting>
      )}
    </div>
  );
}

export function VoiceSnapshotPanel({
  snapshot,
  conversationId,
  mutation,
}: {
  snapshot: SettingsSnapshot['voice'];
  conversationId: string | null;
  mutation: SettingsMutationIO;
}) {
  const [talkProvider, setTalkProvider] = useState(() =>
    mutation.drafts.has(mutation.page, 'runtime.talk_provider')
      ? String(mutation.drafts.read(mutation.page, 'runtime.talk_provider'))
      : snapshot.runtime.talk_provider,
  );
  useEffect(() => {
    if (!mutation.drafts.has(mutation.page, 'runtime.talk_provider'))
      setTalkProvider(snapshot.runtime.talk_provider);
  }, [mutation.drafts, mutation.page, snapshot.runtime.talk_provider]);
  return (
    <div className="stack settings-snapshot-page settings-voice-page">
      <Section
        title="Talk"
        description="Continuous voice conversation through the normal chat and approval path."
        icon={Mic}
      >
        <div className="settings-control-grid">
          <SelectSetting
            mutation={mutation}
            field="runtime.talk_provider"
            label="Talk provider"
            value={snapshot.runtime.talk_provider}
            options={snapshot.talk_providers}
            onDraftChange={setTalkProvider}
          />
          <TextSetting
            mutation={mutation}
            field="runtime.talk_model"
            label="Talk model"
            value={snapshot.runtime.talk_model}
          />
          <SwitchSetting
            mutation={mutation}
            field="runtime.captions_enabled"
            label={
              talkProvider === 'local' ? 'Local captions' : 'Realtime captions'
            }
            value={snapshot.runtime.captions_enabled}
          />
          {talkProvider === 'openai_realtime' && (
            <SwitchSetting
              mutation={mutation}
              field="runtime.realtime_fallback_to_local"
              label="Fallback to local Talk if Realtime is unavailable"
              value={snapshot.runtime.realtime_fallback_to_local}
            />
          )}
        </div>
        <p className="settings-help">
          {talkProvider === 'local'
            ? 'Local Talk keeps microphone transcription on this machine, then sends finished text through normal chat.'
            : 'Realtime Talk sends live microphone audio only while an explicitly started session is active.'}
        </p>
        <Link
          className="button"
          to={conversationId ? `/conversations/${conversationId}` : '/'}
        >
          {conversationId ? 'Open conversation voice' : 'Open a conversation'}
        </Link>
      </Section>
      {talkProvider === 'openai_realtime' && (
        <Section
          title="Realtime Talk Voice"
          description="Controls the voice used by the active OpenAI Realtime Talk session."
          icon={Radio}
        >
          <SelectSetting
            mutation={mutation}
            field="runtime.realtime_voice"
            label="Realtime voice"
            value={snapshot.runtime.realtime_voice}
            options={snapshot.realtime_voice_options}
          />
          <p className="settings-help">
            This voice applies to Realtime Talk only, not local read-aloud.
          </p>
        </Section>
      )}
      <Section
        title="Dictation"
        description="Speech-to-text only; text stays in the composer until Send."
        icon={Mic}
      >
        <div className="settings-control-grid">
          <SelectSetting
            mutation={mutation}
            field="runtime.dictation_provider"
            label="Dictation provider"
            value={snapshot.runtime.dictation_provider}
            options={snapshot.dictation_providers}
          />
          <TextSetting
            mutation={mutation}
            field="runtime.dictation_model"
            label="Dictation model"
            value={snapshot.runtime.dictation_model}
          />
          <SelectSetting
            mutation={mutation}
            field="local.whisper_model"
            label="Whisper model size"
            value={snapshot.local.whisper_model}
            options={snapshot.whisper_options}
          />
        </div>
      </Section>
      <Section
        title="Normal Read-Aloud"
        description="Hear non-Realtime responses using the configured speech output."
        icon={Volume2}
      >
        <div className="settings-control-grid">
          <SelectSetting
            mutation={mutation}
            field="runtime.speech_output_provider"
            label="Read-aloud provider"
            value={snapshot.runtime.speech_output_provider}
            options={snapshot.speech_output_providers}
          />
          <TextSetting
            mutation={mutation}
            field="runtime.speech_output_model"
            label="Read-aloud model"
            value={snapshot.runtime.speech_output_model}
          />
          {snapshot.tts.installed ? (
            <>
              <SwitchSetting
                mutation={mutation}
                field="tts.enabled"
                label="Enable text-to-speech"
                value={snapshot.tts.enabled}
              />
              <SelectSetting
                mutation={mutation}
                field="tts.voice"
                label="Voice"
                value={snapshot.tts.voice}
                options={snapshot.tts_voice_options}
              />
              <NumberSetting
                mutation={mutation}
                field="tts.speed"
                label="Speech speed"
                value={snapshot.tts.speed}
                min={0.5}
                max={2}
                step={0.1}
              />
              <SwitchSetting
                mutation={mutation}
                field="tts.auto_speak"
                label="Auto-speak voice responses"
                value={snapshot.tts.auto_speak}
              />
            </>
          ) : (
            <>
              <StateChip warning>Kokoro not installed</StateChip>
              <ReviewedSettingsAction
                mutation={mutation}
                field="tts.install"
                label="Install Kokoro TTS"
                description="Downloads the Kokoro model and voices, then keeps speech generation local. Network access occurs only after review."
              />
            </>
          )}
        </div>
        {snapshot.tts.installed && (
          <ReviewedSettingsAction
            mutation={mutation}
            field="tts.test"
            label="Test voice"
            description="Plays one fixed local phrase through the selected output device."
          />
        )}
      </Section>
      <details className="settings-snapshot-disclosure">
        <summary>Models &amp; setup</summary>
        <Section
          title="Voice Models"
          description="Saved runtime choices and masked provider configuration."
          icon={Radio}
        >
          <h4>Runtime Voice Models</h4>
          <ul className="settings-voice-model-list">
            <VoiceModelRow
              title={`Whisper ${snapshot.local.whisper_model}`}
              description="Dictation and local Talk transcription"
              provider="Local"
              status={savedStateLabel(snapshot.local.runtime_state)}
            />
            <VoiceModelRow
              title="SenseVoice"
              description="Talk and Dictation multilingual transcription"
              provider="Local"
              status={
                snapshot.local.sensevoice_path_configured
                  ? 'Configured'
                  : 'Setup needed'
              }
              ready={snapshot.local.sensevoice_path_configured}
            />
            <VoiceModelRow
              title="Kokoro"
              description={`Speech output · ${snapshot.tts.voice}`}
              provider="Local"
              status={snapshot.tts.installed ? 'Installed' : 'Not installed'}
              ready={snapshot.tts.installed}
            />
            <VoiceModelRow
              title="OpenAI Realtime Talk"
              description={`Realtime voice-agent · ${snapshot.runtime.realtime_voice}`}
              provider="OpenAI"
              status={
                snapshot.openai_realtime_credential.configured
                  ? 'Credential saved · not checked'
                  : 'Setup needed'
              }
              ready={
                snapshot.openai_realtime_credential.configured
                  ? undefined
                  : false
              }
            />
          </ul>
          {!snapshot.local.sensevoice_path_configured && (
            <>
              <a
                href="https://modelscope.cn/models/iic/SenseVoiceSmall"
                target="_blank"
                rel="noreferrer"
              >
                SenseVoice Small model and Apache-2.0 license
              </a>
              <ReviewedSettingsAction
                mutation={mutation}
                field="sensevoice.install"
                label="Install SenseVoice Small"
                description="Downloads the Apache-2.0 SenseVoice Small model from ModelScope. No audio, prompts, or usage data are sent."
              />
            </>
          )}
          <Link className="button" to="/settings/providers">
            Open Providers
          </Link>
        </Section>
      </details>
      <details className="settings-snapshot-disclosure">
        <summary>Diagnostics</summary>
        <Section
          title="Diagnostics"
          description="Cached audio configuration; no device or provider was probed."
          icon={ShieldCheck}
        >
          <Facts>
            <Fact label="Settings availability" value={snapshot.availability} />
            <Fact label="Microphone/provider readiness" value="Not checked" />
          </Facts>
          <p className="settings-help">
            Talk can call models and tools. Realtime sessions can incur provider
            cost while active.
          </p>
        </Section>
      </details>
    </div>
  );
}

export function SystemSnapshotPanel({
  snapshot,
  mutation,
  pickFolder,
}: {
  snapshot: SettingsSnapshot['system'];
  mutation: SettingsMutationIO;
  pickFolder?: SettingsFolderPicker;
}) {
  return (
    <div className="stack settings-snapshot-page settings-system-page">
      <Section
        title="Workspace Folder"
        description="The filesystem tool is sandboxed to this folder."
        icon={HardDrive}
      >
        <WorkspaceFolderSetting
          key={mutation.revision}
          mutation={mutation}
          configured={snapshot.workspace.configured}
          currentName={snapshot.workspace.label}
          pickFolder={pickFolder}
        />
        <StateChip
          active={snapshot.workspace.exists}
          warning={!snapshot.workspace.exists}
        >
          {snapshot.workspace.exists ? 'Folder available' : 'Folder not found'}
        </StateChip>
      </Section>
      <Section
        title="Shell Access"
        description="Shell commands run directly on the host inside saved boundaries."
        icon={SquareTerminal}
        tone="warning"
      >
        {snapshot.shell.available && snapshot.shell.enabled != null ? (
          <>
            <SwitchSetting
              mutation={mutation}
              field="shell.enabled"
              label="Enable Shell tool"
              value={snapshot.shell.enabled}
            />
            <TextSetting
              mutation={mutation}
              field="shell.blocked_patterns"
              label="Additional blocked patterns (comma-separated)"
              value={snapshot.shell.blocked_patterns}
            />
          </>
        ) : (
          <StateChip warning>Shell tool not found</StateChip>
        )}
      </Section>
      <Section
        title="Browser & Computer Use"
        description="Web and native-app automation keep separate setup and authority."
        icon={AppWindow}
      >
        <div className="settings-system-runtime-list">
          {snapshot.browser.available && snapshot.browser.enabled != null ? (
            <div className="settings-system-runtime-row">
              <div>
                <strong>Browser automation</strong>
                <small>
                  Runtime: {savedStateLabel(snapshot.browser.runtime_state)}
                </small>
              </div>
              <SwitchSetting
                mutation={mutation}
                field="browser.enabled"
                label="Enable Browser tool"
                value={snapshot.browser.enabled}
              />
              <ReviewedSettingsAction
                mutation={mutation}
                field="browser.install"
                label="Install browser runtime"
                description="Downloads Row-Bot's managed Playwright Chromium only after review. Installed Chrome or Edge remains preferred."
              />
            </div>
          ) : (
            <StateChip warning>Browser tool not found</StateChip>
          )}
          {snapshot.computer_use.available &&
          snapshot.computer_use.enabled != null ? (
            <div className="settings-system-runtime-row">
              <div>
                <strong>Computer Use (Beta)</strong>
                <small>
                  Runtime:{' '}
                  {savedStateLabel(snapshot.computer_use.runtime_state)}
                </small>
              </div>
              <SwitchSetting
                mutation={mutation}
                field="computer_use.enabled"
                label="Computer Use (Beta)"
                value={snapshot.computer_use.enabled}
              />
            </div>
          ) : (
            <StateChip warning>Computer Use unavailable</StateChip>
          )}
        </div>
        {snapshot.computer_use.available && (
          <details className="settings-system-detail">
            <summary>Computer Use setup details</summary>
            <Facts>
              <Fact
                label="Disclosure"
                value={
                  snapshot.computer_use.disclosure_acknowledged
                    ? 'Acknowledged'
                    : 'Not acknowledged'
                }
              />
              <Fact
                label="System Cua executable"
                value={configuredLabel(
                  snapshot.computer_use.system_binary_configured,
                )}
              />
            </Facts>
          </details>
        )}
        {snapshot.computer_use.available &&
          snapshot.computer_use.disclosure_acknowledged && (
            <ReviewedSettingsAction
              mutation={mutation}
              field="computer_use.install"
              label="Install Computer Use runtime"
              description="Downloads the reviewed, pinned Cua Driver artifact for this platform after review."
            />
          )}
      </Section>
      <Section
        title="File Operations"
        description="Saved read, write, and destructive filesystem operations."
        icon={FileText}
      >
        {snapshot.file_operations.available &&
        snapshot.file_operations.enabled != null ? (
          <>
            <SwitchSetting
              mutation={mutation}
              field="file_operations.enabled"
              label="Enable Filesystem tool"
              value={snapshot.file_operations.enabled}
            />
            <GroupedOperationSetting
              mutation={mutation}
              field="file_operations.selected"
              label="Allowed operations"
              value={snapshot.file_operations.selected}
              options={snapshot.file_operations.options}
            />
          </>
        ) : (
          <StateChip warning>Filesystem tool unavailable</StateChip>
        )}
      </Section>
      <Section
        title="Tunnel Settings"
        description="Credentials remain masked; tunnel checks require an explicit action."
        icon={Network}
      >
        <SelectSetting
          mutation={mutation}
          field="tunnel.provider"
          label="Tunnel provider"
          value={snapshot.tunnel.provider}
          options={[{ value: 'ngrok', label: 'ngrok' }]}
        />
        <SecretSetting
          mutation={mutation}
          field="tunnel.credential"
          label="Tunnel credential"
          configured={snapshot.tunnel.credential.configured}
          source={snapshot.tunnel.credential.source}
        />
        <p className="settings-help">
          Runtime status: {savedStateLabel(snapshot.tunnel.runtime_state)}.{' '}
          {snapshot.tunnel.active_count == null
            ? 'Active tunnel count not checked.'
            : `${snapshot.tunnel.active_count} active tunnels.`}{' '}
          Opening Settings never starts or exposes a tunnel.
        </p>
        <div className="settings-action-grid">
          <ReviewedSettingsAction
            mutation={mutation}
            field="tunnel.check"
            label="Check tunnel setup"
            description="Checks saved local configuration without opening a tunnel."
          />
          <ReviewedSettingsAction
            mutation={mutation}
            field="tunnel.start_main"
            label="Start app tunnel"
            description="Exposes the local app and task webhook endpoint to the internet through ngrok."
          />
          <ReviewedSettingsAction
            mutation={mutation}
            field="tunnel.stop_main"
            label="Stop app tunnel"
            description="Stops only the Row-Bot app tunnel managed by this process."
          />
        </div>
      </Section>
      <Section
        title="Remote Access"
        description="Every non-local device needs a revocable authenticated session."
        icon={ShieldCheck}
        tone="warning"
      >
        <SelectSetting
          mutation={mutation}
          field="remote_access.listen_mode"
          label="Listen mode"
          value={snapshot.remote_access.listen_mode}
          options={[
            { value: 'local_only', label: 'This device only' },
            { value: 'local_network', label: 'Local network' },
          ]}
        />
        <DelimitedListSetting
          mutation={mutation}
          field="remote_access.configured_origins"
          label="Allowed origins"
          value={snapshot.remote_access.configured_origins}
          hint="Comma-separated exact origins."
        />
        <p className="settings-help">
          Tailscale and host admission: not checked.
        </p>
        <Facts>
          <Fact
            label="Remote access availability"
            value={savedStateLabel(snapshot.mobile_access.availability)}
          />
          <Fact
            label="Connected devices"
            value={snapshot.mobile_access.active_devices}
          />
          <Fact
            label="Authenticated sessions"
            value={snapshot.mobile_access.active_sessions}
          />
        </Facts>
      </Section>
      <Section
        title="Logging & Diagnostics"
        description="Local diagnostic level and output directory."
        icon={ListChecks}
      >
        <SelectSetting
          mutation={mutation}
          field="logging.level"
          label="File log level"
          value={snapshot.logging.level}
          options={[
            { value: 'DEBUG', label: 'Debug' },
            { value: 'INFO', label: 'Info' },
            { value: 'WARNING', label: 'Warning' },
            { value: 'ERROR', label: 'Error' },
          ]}
        />
        <Facts>
          <Fact
            label="Log directory"
            value={
              snapshot.logging.directory_available
                ? 'Available locally'
                : 'Created when logging starts'
            }
          />
        </Facts>
        <ReviewedSettingsAction
          mutation={mutation}
          field="logging.open"
          label="Open Log Folder"
          description="Opens Row-Bot's fixed local log directory; the renderer never receives its path."
        />
      </Section>
    </div>
  );
}

function WorkspaceFolderSetting({
  mutation,
  configured,
  currentName,
  pickFolder,
}: {
  mutation: SettingsMutationIO;
  configured: boolean;
  currentName: string;
  pickFolder?: SettingsFolderPicker;
}) {
  const [selectedName, setSelectedName] = useState('');
  const [pickError, setPickError] = useState('');
  const [picking, setPicking] = useState(false);
  const abort = useRef<AbortController | null>(null);
  useEffect(() => () => abort.current?.abort(), []);
  return (
    <SavedSetting
      mutation={mutation}
      field="workspace.folder_grant"
      label="Workspace folder grant"
      value=""
      group
    >
      {({ setValue, disabled }) => (
        <div className="stack">
          <p className="settings-help">
            {configured
              ? `Current folder: ${currentName || 'Selected local folder'}.`
              : 'No workspace folder selected.'}{' '}
            The full local path is never sent to the renderer.
          </p>
          <Button
            disabled={disabled || picking || !pickFolder}
            onClick={() => {
              if (!pickFolder || picking) return;
              abort.current?.abort();
              abort.current = new AbortController();
              setPicking(true);
              setPickError('');
              void pickFolder(abort.current.signal)
                .then((result) => {
                  if (result.status === 'unavailable') {
                    setPickError('The local folder picker is unavailable.');
                    return;
                  }
                  if (result.status !== 'selected' || !result.grant_id) return;
                  setSelectedName(result.name || 'Selected local folder');
                  setValue(result.grant_id);
                })
                .catch((cause) => {
                  if (!abort.current?.signal.aborted)
                    setPickError(clientError(cause).message);
                })
                .finally(() => setPicking(false));
            }}
          >
            {picking ? 'Choosing folder…' : 'Choose workspace folder'}
          </Button>
          {selectedName && <p role="status">Selected: {selectedName}</p>}
          {pickError && <p role="alert">{pickError}</p>}
        </div>
      )}
    </SavedSetting>
  );
}

export function TrackerSnapshotPanel({
  snapshot,
  mutation,
}: {
  snapshot: SettingsSnapshot['tracker'];
  mutation: SettingsMutationIO;
}) {
  return (
    <div className="stack settings-snapshot-page">
      <Section
        title="Tracker Tool"
        description="Enable the tool and review locally stored tracker data."
        icon={ListChecks}
      >
        {snapshot.tool_available && snapshot.enabled != null ? (
          <SwitchSetting
            mutation={mutation}
            field="enabled"
            label="Enable Habit Tracker"
            value={snapshot.enabled}
          />
        ) : (
          <StateChip warning>Tracker tool not found</StateChip>
        )}
      </Section>
      <div
        className="settings-summary-strip"
        role="group"
        aria-label="Tracker totals"
      >
        <StateChip>{snapshot.items.length} active trackers</StateChip>
        <StateChip>{snapshot.total_entries} entries</StateChip>
      </div>
      {snapshot.items.length ? (
        <>
          <ul className="settings-compact-list" aria-label="Saved trackers">
            {snapshot.items.map((tracker) => (
              <li key={tracker.tracker_id}>
                <strong>
                  {tracker.icon} {tracker.name}
                </strong>
                <span>
                  {tracker.kind}
                  {tracker.unit ? ` · ${tracker.unit}` : ''}
                  {tracker.last_event_at
                    ? ` · Last ${dateOnly(tracker.last_event_at)}`
                    : ''}
                </span>
                <StateChip>{tracker.entry_count} entries</StateChip>
              </li>
            ))}
          </ul>
          <div className="settings-tracker-danger">
            <Section
              title="Danger Zone"
              description="Delete all habit and health tracker rows."
              icon={Trash2}
              tone="danger"
            >
              <TrackerDeleteAll mutation={mutation} />
            </Section>
          </div>
        </>
      ) : (
        <p className="muted">No trackers yet.</p>
      )}
    </div>
  );
}

function TrackerDeleteAll({ mutation }: { mutation: SettingsMutationIO }) {
  const [review, setReview] = useState<SettingsMutationReview | null>(null);
  const [request, setRequest] = useState<SettingsMutationRequest | null>(null);
  const [pendingCommand, setPendingCommand] = useState('');
  const [busy, setBusy] = useState<'review' | 'delete' | ''>('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const abort = useRef<AbortController | null>(null);

  useEffect(() => {
    if (review && review.settings_revision !== mutation.revision) {
      setReview(null);
      setRequest(null);
      setNotice('Tracker data changed. Review the deletion again.');
    }
  }, [mutation.revision, review]);
  useEffect(() => () => abort.current?.abort(), []);

  async function reviewDeletion() {
    if (busy || pendingCommand) return;
    const next: SettingsMutationRequest = {
      settings_revision: mutation.revision,
      page: 'tracker',
      field: 'delete_all',
      value: true,
    };
    abort.current?.abort();
    abort.current = new AbortController();
    setBusy('review');
    setError('');
    setNotice('');
    try {
      const result = await mutation.review(next, abort.current.signal);
      if (
        result.operation !== 'settings.update' ||
        result.settings_revision !== next.settings_revision ||
        result.page !== next.page ||
        result.field !== next.field
      )
        throw { code: 'settings_review_changed' };
      setRequest(next);
      setReview(result);
    } catch (cause) {
      if (!abort.current.signal.aborted) setError(clientError(cause).message);
    } finally {
      setBusy('');
    }
  }

  async function deleteAll() {
    if (!review || !request || busy) return;
    const commandId = crypto.randomUUID();
    const approvedReview = review;
    const approvedRequest = request;
    setPendingCommand(commandId);
    setReview(null);
    setRequest(null);
    setBusy('delete');
    setError('');
    setNotice('');
    try {
      const receipt = await mutation.execute(
        approvedRequest,
        approvedReview,
        commandId,
      );
      if (receipt.status === 'completed' && receipt.snapshot) {
        setPendingCommand('');
        mutation.onSnapshot(receipt.snapshot);
        setNotice('All tracker data deleted.');
      } else if (receipt.status === 'partial') {
        setNotice(
          'The deletion outcome is unconfirmed. Check the original receipt before trying again.',
        );
      } else {
        setPendingCommand('');
        setError('The reviewed deletion was rejected. Review it again.');
      }
    } catch (cause) {
      setError(clientError(cause).message);
      setNotice(
        'The deletion outcome is unconfirmed. It was not automatically repeated.',
      );
    } finally {
      setBusy('');
    }
  }

  async function checkReceipt() {
    if (!pendingCommand || busy) return;
    abort.current?.abort();
    abort.current = new AbortController();
    setBusy('delete');
    setError('');
    try {
      const receipt = await mutation.receipt(
        pendingCommand,
        abort.current.signal,
      );
      if (receipt.status === 'partial') {
        setNotice('The original deletion outcome is still unconfirmed.');
        return;
      }
      setPendingCommand('');
      if (receipt.status === 'completed' && receipt.snapshot) {
        mutation.onSnapshot(receipt.snapshot);
        setNotice('All tracker data deletion confirmed.');
      } else {
        setError('The original reviewed deletion was rejected.');
      }
    } catch (cause) {
      if (!abort.current.signal.aborted) setError(clientError(cause).message);
    } finally {
      setBusy('');
    }
  }

  return (
    <div className="settings-saved-control" aria-busy={!!busy}>
      {!review && !pendingCommand && (
        <Button
          variant="danger"
          disabled={!!busy}
          onClick={() => void reviewDeletion()}
        >
          {busy === 'review'
            ? 'Reviewing deletion…'
            : 'Delete All Tracker Data'}
        </Button>
      )}
      {review && (
        <>
          <p role="status">{review.value_summary}</p>
          <div className="settings-control-actions">
            <Button
              variant="danger"
              disabled={!!busy}
              onClick={() => void deleteAll()}
            >
              Confirm Delete All Tracker Data
            </Button>
            <Button
              variant="ghost"
              disabled={!!busy}
              onClick={() => {
                setReview(null);
                setRequest(null);
                setNotice('Deletion cancelled. No tracker data was changed.');
              }}
            >
              Cancel
            </Button>
          </div>
        </>
      )}
      {pendingCommand && (
        <Button disabled={!!busy} onClick={() => void checkReceipt()}>
          {busy === 'delete' ? 'Checking…' : 'Check original receipt'}
        </Button>
      )}
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
    </div>
  );
}

function AccountPanel({
  label,
  icon: Icon,
  account,
  mutation,
  prefix,
}: {
  label: string;
  icon: Icon;
  account: SettingsSnapshot['accounts']['github'];
  mutation: SettingsMutationIO;
  prefix: 'github' | 'x';
}) {
  const status =
    prefix === 'github' && account.authentication_state === 'not_configured'
      ? 'Not connected'
      : accountStateLabel(account.authentication_state);
  return (
    <details className="settings-account-panel">
      <summary>
        <Icon size={18} aria-hidden />
        <strong>{label}</strong>
        <span>{status}</span>
        <ChevronDown
          className="settings-disclosure-chevron"
          size={17}
          aria-hidden
        />
      </summary>
      <div className="stack settings-account-content">
        <Facts>
          <Fact
            label="Configuration"
            value={configuredLabel(account.configured)}
          />
          <Fact
            label="Credential source"
            value={
              account.credential?.source
                ? savedStateLabel(account.credential.source)
                : 'Not saved'
            }
          />
        </Facts>
        {prefix === 'x' && account.enabled != null && (
          <SwitchSetting
            mutation={mutation}
            field={`${prefix}.enabled`}
            label={`Enable ${label}`}
            value={account.enabled}
          />
        )}
        {prefix === 'github' && (
          <SecretSetting
            mutation={mutation}
            field="github.credential"
            label="GitHub token"
            configured={account.credential?.configured ?? false}
            source={account.credential?.source}
          />
        )}
        {prefix === 'x' && (
          <>
            <SecretSetting
              mutation={mutation}
              field="x.client_id"
              label="X client ID"
              configured={account.configured}
              source={account.credential?.source}
            />
            <SecretSetting
              mutation={mutation}
              field="x.client_secret"
              label="X client secret"
              configured={account.credential?.configured ?? false}
              source={account.credential?.source}
            />
            <ChoiceListSetting
              mutation={mutation}
              field="x.read_operations"
              label="Read operations"
              value={account.read_operations}
              options={[
                'x_search',
                'x_read_tweet',
                'x_timeline',
                'x_mentions',
                'x_user_info',
              ]}
            />
            <ChoiceListSetting
              mutation={mutation}
              field="x.post_operations"
              label="Post operations"
              value={account.post_operations}
              options={['x_post_tweet', 'x_reply', 'x_quote', 'x_delete_tweet']}
            />
            <ChoiceListSetting
              mutation={mutation}
              field="x.engage_operations"
              label="Engage operations"
              value={account.engage_operations}
              options={[
                'x_like',
                'x_unlike',
                'x_repost',
                'x_unrepost',
                'x_bookmark',
                'x_unbookmark',
              ]}
            />
          </>
        )}
        <p className="settings-help">
          Authentication is started only by an explicit account action. Opening
          this panel never contacts the account provider.
        </p>
      </div>
    </details>
  );
}

function GoogleAccountPanel({
  gmail,
  calendar,
  mutation,
}: {
  gmail: SettingsSnapshot['accounts']['gmail'];
  calendar: SettingsSnapshot['accounts']['calendar'];
  mutation: SettingsMutationIO;
}) {
  const status =
    gmail.authentication_state === calendar.authentication_state
      ? gmail.authentication_state === 'expired'
        ? 'Token issue'
        : accountStateLabel(gmail.authentication_state)
      : `Gmail: ${accountStateLabel(gmail.authentication_state)} · Calendar: ${accountStateLabel(calendar.authentication_state)}`;
  return (
    <details className="settings-account-panel">
      <summary>
        <FileKey size={18} aria-hidden />
        <strong>Google (Gmail &amp; Calendar)</strong>
        <span>{status}</span>
        <ChevronDown
          className="settings-disclosure-chevron"
          size={17}
          aria-hidden
        />
      </summary>
      <div className="stack settings-account-content">
        <div className="settings-control-grid">
          {gmail.enabled != null && (
            <SwitchSetting
              mutation={mutation}
              field="gmail.enabled"
              label="Gmail"
              value={gmail.enabled}
            />
          )}
          {calendar.enabled != null && (
            <SwitchSetting
              mutation={mutation}
              field="calendar.enabled"
              label="Calendar"
              value={calendar.enabled}
            />
          )}
        </div>
        <ChoiceListSetting
          mutation={mutation}
          field="gmail.operations"
          label="Gmail operations"
          value={gmail.operations}
          options={[
            'search_gmail',
            'get_gmail_message',
            'get_gmail_thread',
            'create_gmail_draft',
            'send_gmail_message',
          ]}
        />
        <ChoiceListSetting
          mutation={mutation}
          field="calendar.operations"
          label="Calendar operations"
          value={calendar.operations}
          options={[
            'get_current_datetime',
            'search_events',
            'create_calendar_event',
            'create_calendar_events',
            'update_calendar_event',
            'move_calendar_event',
            'delete_calendar_event',
          ]}
        />
        <Facts>
          <Fact
            label="Credentials file"
            value={
              gmail.configured || calendar.configured
                ? 'Configured locally'
                : 'Not configured'
            }
          />
          <Fact
            label="Gmail configuration"
            value={configuredLabel(gmail.configured)}
          />
          <Fact
            label="Calendar configuration"
            value={configuredLabel(calendar.configured)}
          />
        </Facts>
        <p className="settings-help">
          Credential locations stay local and are never returned to this page.
          Native credential selection and authentication start only through an
          explicit reviewed account action.
        </p>
      </div>
    </details>
  );
}
export function AccountsSnapshotPanel({
  snapshot,
  mutation,
}: {
  snapshot: SettingsSnapshot['accounts'];
  mutation: SettingsMutationIO;
}) {
  if (snapshot.availability !== 'available')
    return (
      <Section
        title="Accounts"
        description="Saved account settings are unavailable."
        icon={FileKey}
      >
        <StateChip warning>Account settings unavailable</StateChip>
      </Section>
    );
  return (
    <div className="stack settings-snapshot-page">
      <AccountPanel
        label="GitHub"
        icon={GitBranch}
        account={snapshot.github}
        mutation={mutation}
        prefix="github"
      />
      <GoogleAccountPanel
        gmail={snapshot.gmail}
        calendar={snapshot.calendar}
        mutation={mutation}
      />
      <AccountPanel
        label="X (Twitter)"
        icon={Network}
        account={snapshot.x}
        mutation={mutation}
        prefix="x"
      />
    </div>
  );
}

export function UtilitiesSnapshotPanel({
  snapshot,
  mutation,
}: {
  snapshot: SettingsSnapshot['utilities'];
  mutation: SettingsMutationIO;
}) {
  const availableUtilities = snapshot.items.filter(
    (utility) => utility.available,
  );
  const utilities = availableUtilities.filter(
    (utility) => utility.enabled != null,
  );
  const icons: Record<string, Icon> = {
    task: CalendarClock,
    url_reader: Globe2,
    calculator: Calculator,
    weather: CloudSun,
    chart: BarChart3,
    system_info: MonitorCog,
    conversation_search: Search,
    custom_tool_builder: Hammer,
  };
  if (snapshot.availability !== 'available')
    return (
      <Section
        title="Utility Tools"
        description="Saved utility settings are unavailable."
        icon={Wrench}
      >
        <StateChip warning>Utility settings unavailable</StateChip>
      </Section>
    );
  return (
    <div className="stack settings-snapshot-page">
      <div
        className="settings-summary-strip"
        role="group"
        aria-label="Utility totals"
      >
        <StateChip>
          {utilities.filter((item) => item.enabled).length} enabled
        </StateChip>
        <StateChip>{availableUtilities.length} available</StateChip>
      </div>
      <Section
        title="Utility Tools"
        description="Toggle small tools used for everyday tasks."
        icon={Wrench}
      >
        <ul className="settings-toggle-list settings-utility-list">
          {utilities.map((utility) => {
            const UtilityIcon = icons[utility.utility_id] ?? Wrench;
            const presentation = utilityPresentation[utility.utility_id] ?? {
              label: utility.label,
              description: utility.description,
            };
            return (
              <li key={utility.utility_id}>
                <div className="settings-utility-toggle">
                  <SwitchSetting
                    mutation={mutation}
                    field={`${utility.utility_id}.enabled`}
                    label={`Enable ${presentation.label}`}
                    value={Boolean(utility.enabled)}
                  />
                </div>
                <UtilityIcon size={18} aria-hidden />
                <div>
                  <strong>{presentation.label}</strong>
                  <small>{presentation.description}</small>
                </div>
              </li>
            );
          })}
        </ul>
        {!utilities.length && (
          <p className="settings-help">No utility tools are available.</p>
        )}
      </Section>
    </div>
  );
}

export function DocumentEmbeddingSnapshot({
  snapshot,
  mutation,
}: {
  snapshot: SettingsSnapshot['documents'];
  mutation: SettingsMutationIO;
}) {
  const embedding = snapshot.embedding;
  const [provider, setProvider] = useState(() =>
    mutation.drafts.has(mutation.page, 'embedding.provider')
      ? String(mutation.drafts.read(mutation.page, 'embedding.provider'))
      : embedding.provider,
  );
  useEffect(() => {
    if (!mutation.drafts.has(mutation.page, 'embedding.provider'))
      setProvider(embedding.provider);
  }, [embedding.provider, mutation.drafts, mutation.page]);
  if (snapshot.availability !== 'available')
    return (
      <Section
        title="Embedding Engine"
        description="Local models stay private; cloud models send admitted text to the provider."
        icon={Bot}
      >
        <StateChip warning>Embedding settings unavailable</StateChip>
      </Section>
    );
  const activeEmbedding =
    snapshot.active_embedding ||
    (embedding.provider === 'local'
      ? `${embedding.local_model} (local)`
      : `${embedding.cloud_model} (cloud)`);
  const vectors = snapshot.document_vectors ?? {
    state: 'unavailable',
    detail: 'Saved document vector health is unavailable.',
  };
  const localRuntime = snapshot.local_runtime ?? {
    state: 'unavailable',
    detail: 'Saved local model runtime state is unavailable.',
  };
  const memoryIndex = snapshot.memory_index ?? {
    state: 'unavailable',
    detail: 'Saved memory index state is unavailable.',
  };
  return (
    <div className="stack settings-document-embedding-owner">
      <div
        className="settings-summary-strip settings-document-status-strip"
        role="group"
        aria-label="Document index status"
      >
        <StateChip>
          {snapshot.indexed_documents == null
            ? 'Indexed count unavailable'
            : `${snapshot.indexed_documents.toLocaleString()} indexed`}
        </StateChip>
        <StateChip>{activeEmbedding}</StateChip>
        <StateChip
          active={vectors.state === 'current'}
          warning={vectors.state !== 'current'}
        >
          Vectors {vectors.state}
        </StateChip>
      </div>
      <Section
        title="Embedding Engine"
        description="Local models stay private; cloud models send admitted text to the provider."
        icon={Network}
      >
        <div className="settings-control-grid">
          <SelectSetting
            mutation={mutation}
            field="embedding.provider"
            label="Provider"
            value={embedding.provider}
            onDraftChange={setProvider}
            options={[
              { value: 'local', label: 'Local runtime model' },
              { value: 'cloud', label: 'Cloud embedding model' },
            ]}
          />
          {provider === 'local' ? (
            <SelectSetting
              key="local-embedding-model"
              mutation={mutation}
              field="embedding.local_model"
              label="Local model"
              value={embedding.local_model}
              options={embedding.local_options}
            />
          ) : (
            <SelectSetting
              key="cloud-embedding-model"
              mutation={mutation}
              field="embedding.cloud_model"
              label="Cloud model"
              value={embedding.cloud_model}
              options={embedding.cloud_options}
            />
          )}
          <NumberSetting
            mutation={mutation}
            field="embedding.dimension"
            label="Dimension override"
            value={embedding.dimension}
            min={1}
            max={65536}
            optional
          />
          <SwitchSetting
            mutation={mutation}
            field="embedding.auto_unload"
            label="Auto-unload local resources"
            value={embedding.auto_unload}
          />
        </div>
        <div className="settings-document-runtime" role="status">
          {provider === 'local' && (
            <p>
              <strong>Local model: {localRuntime.state}</strong> —{' '}
              {localRuntime.detail}
            </p>
          )}
          {provider === 'local' && (
            <p>
              <strong>Memory index: {memoryIndex.state}</strong> —{' '}
              {memoryIndex.detail}
            </p>
          )}
          <p>
            <strong>Document vectors: {vectors.state}</strong> —{' '}
            {vectors.detail}
          </p>
          <p className="settings-help">
            Normal recall never downloads a model. Saving a field does not
            rebuild either index.
          </p>
        </div>
        <details className="settings-snapshot-disclosure">
          <summary>Index &amp; model maintenance</summary>
          <div
            className="settings-document-maintenance"
            role="group"
            aria-label="Document index maintenance"
          >
            <ReviewedSettingsAction
              mutation={mutation}
              field="vectors.rebuild"
              label="rebuild document vectors"
              description="Recreate document search vectors from the admitted local document vault."
            />
            <ReviewedSettingsAction
              mutation={mutation}
              field="memory_index.rebuild"
              label="rebuild memory index"
              description="Recreate the local memory vector index with the saved embedding configuration."
            />
            {provider === 'local' && (
              <>
                <ReviewedSettingsAction
                  mutation={mutation}
                  field="local_model.retry"
                  label="retry local load"
                  description="Retry loading the selected model from the existing on-device cache."
                />
                <ReviewedSettingsAction
                  mutation={mutation}
                  field="local_model.download"
                  label="download local model"
                  description="Download the selected embedding model only after review. This requires network access."
                />
                <ReviewedSettingsAction
                  mutation={mutation}
                  field="local_model.repair"
                  label="repair local model"
                  description="Replace the selected model's cached files only after review. This requires network access."
                  variant="danger"
                />
              </>
            )}
          </div>
        </details>
      </Section>
    </div>
  );
}

export function ToolConfigurationSnapshot({
  snapshot,
  mutation,
}: {
  snapshot: SettingsSnapshot['tools'];
  mutation: SettingsMutationIO;
}) {
  const toolPresentation: Record<
    string,
    { label: string; order: number; description: string; setupUrl?: string }
  > = {
    arxiv: {
      label: 'arXiv',
      order: 0,
      description: 'Search research papers and preprints.',
    },
    duckduckgo: {
      label: 'DuckDuckGo',
      order: 1,
      description: 'Search the current web without an API key.',
    },
    web_search: {
      label: 'Web Search',
      order: 2,
      description: 'Search the live web with Tavily.',
      setupUrl: 'https://app.tavily.com/',
    },
    wikipedia: {
      label: 'Wikipedia',
      order: 3,
      description: 'Look up encyclopedia articles and summaries.',
    },
    wolfram_alpha: {
      label: 'Wolfram Alpha',
      order: 4,
      description: 'Run advanced computation and scientific queries.',
      setupUrl: 'https://developer.wolframalpha.com/',
    },
    youtube: {
      label: 'YouTube',
      order: 5,
      description: 'Search videos and retrieve available transcripts.',
    },
  };
  const [toolQuery, setToolQuery] = useState('');
  const normalizedQuery = toolQuery.trim().toLocaleLowerCase();
  const tools = snapshot.items
    .map((tool) => ({
      ...tool,
      displayLabel: toolPresentation[tool.tool_id]?.label ?? tool.label,
      description:
        toolPresentation[tool.tool_id]?.description ??
        'Saved research capability.',
      setupUrl: toolPresentation[tool.tool_id]?.setupUrl,
    }))
    .filter(
      (tool) =>
        !normalizedQuery ||
        `${tool.displayLabel} ${tool.description}`
          .toLocaleLowerCase()
          .includes(normalizedQuery),
    )
    .sort(
      (left, right) =>
        (toolPresentation[left.tool_id]?.order ?? Number.MAX_SAFE_INTEGER) -
          (toolPresentation[right.tool_id]?.order ?? Number.MAX_SAFE_INTEGER) ||
        left.displayLabel.localeCompare(right.displayLabel),
    );
  if (snapshot.availability !== 'available')
    return (
      <Section
        title="Search & Knowledge Tools"
        description="Saved research-tool settings are unavailable."
        icon={BookOpen}
      >
        <StateChip warning>Tool settings unavailable</StateChip>
      </Section>
    );
  return (
    <div className="stack settings-snapshot-page">
      <Section
        title="Capability loading"
        description="Choose how enabled external capabilities are exposed to the model."
        icon={SlidersHorizontal}
      >
        <RadioSetting
          mutation={mutation}
          field="external_loading_mode"
          label="External tool loading"
          value={snapshot.external_loading_mode}
          options={[
            {
              value: 'auto',
              label: 'Auto-select external tools (recommended)',
            },
            { value: 'eager', label: 'Load all external tools' },
          ]}
        />
        <p className="settings-help">
          Core tools stay available. Enabled external tools are selected when
          needed unless compatibility mode loads them all.
        </p>
      </Section>
      <Section
        title="Retrieval Compression"
        description="Controls how search results are filtered before reaching the model."
        icon={Search}
      >
        <SelectSetting
          mutation={mutation}
          field="compression_mode"
          label="Compression mode"
          value={snapshot.compression_mode}
          options={[
            { value: 'off', label: 'Off (default)' },
            { value: 'deep', label: 'Deep' },
          ]}
        />
      </Section>
      <Section
        title="Search & Knowledge Tools"
        description="Saved enablement and masked configuration for research tools."
        icon={BookOpen}
      >
        <Field label="Search research tools">
          <Input
            type="search"
            value={toolQuery}
            onChange={(event) => setToolQuery(event.target.value)}
            placeholder="Name or purpose"
          />
        </Field>
        <ul className="settings-toggle-list">
          {tools.map((tool) => (
            <li key={tool.tool_id}>
              <div>
                <strong>{tool.displayLabel}</strong>
                <small>{tool.description}</small>
                <small>
                  {tool.available ? 'Available' : 'Unavailable'}
                  {tool.configured_fields.length
                    ? ` · ${tool.configured_fields.length} configured fields`
                    : ''}
                </small>
              </div>
              {tool.available && tool.enabled != null ? (
                <SwitchSetting
                  mutation={mutation}
                  field={`${tool.tool_id}.enabled`}
                  label={`Enable ${tool.displayLabel}`}
                  value={tool.enabled}
                />
              ) : (
                <StateChip warning>Unavailable</StateChip>
              )}
              {(tool.credentials.length > 0 || tool.setupUrl) && (
                <details className="settings-snapshot-disclosure settings-tool-detail">
                  <summary>Credentials &amp; setup</summary>
                  {tool.setupUrl && (
                    <p className="settings-help">
                      Create the provider credential at{' '}
                      <a href={tool.setupUrl} target="_blank" rel="noreferrer">
                        {new URL(tool.setupUrl).hostname}
                      </a>
                      , then save it below. Credentials remain write-only and
                      masked.
                    </p>
                  )}
                  {tool.credentials.map((credential) =>
                    tool.tool_id === 'web_search' ||
                    tool.tool_id === 'wolfram_alpha' ? (
                      <SecretSetting
                        key={credential.name}
                        mutation={mutation}
                        field={`${tool.tool_id}.credential`}
                        label={credential.label}
                        configured={credential.configured}
                        source={credential.source}
                      />
                    ) : (
                      <div
                        className="settings-summary-strip"
                        key={credential.name}
                      >
                        <strong>{credential.label}</strong>
                        <StateChip
                          active={credential.configured}
                          warning={!credential.configured}
                        >
                          {credential.configured
                            ? 'Saved · masked'
                            : 'Not configured'}
                        </StateChip>
                      </div>
                    ),
                  )}
                </details>
              )}
            </li>
          ))}
        </ul>
        {!tools.length && (
          <p className="settings-help">No research tools match this search.</p>
        )}
      </Section>
    </div>
  );
}

export function PreferencesSnapshotPanel({
  snapshot,
  mutation,
}: {
  snapshot: SettingsSnapshot['preferences'];
  mutation: SettingsMutationIO;
}) {
  return (
    <div className="stack settings-snapshot-page">
      <section
        className="settings-preferences-identity stack"
        aria-label="Assistant identity"
      >
        <div className="settings-preferences-block">
          <h3>Assistant name</h3>
          <TextSetting
            mutation={mutation}
            field="identity.name"
            label="Name"
            value={snapshot.identity.name}
            maxLength={80}
          />
        </div>
        <div className="settings-preferences-block">
          <h3>Personality</h3>
          <p className="settings-help">
            Optional behaviour guidance, up to{' '}
            {snapshot.identity.personality_max_length} characters.
          </p>
          <TextSetting
            mutation={mutation}
            field="identity.personality"
            label="Personality"
            value={snapshot.identity.personality}
            maxLength={snapshot.identity.personality_max_length}
            multiline
          />
          <p className="settings-help">
            {snapshot.identity.personality.length} /{' '}
            {snapshot.identity.personality_max_length}
          </p>
        </div>
        <div className="settings-preferences-block">
          <h3>Preview</h3>
          <p className="settings-help settings-preferences-preview">
            You are {snapshot.identity.name}, a knowledgeable personal assistant
            with access to tools.
            {snapshot.identity.personality
              ? ` ${snapshot.identity.personality}`
              : ''}
          </p>
        </div>
        <div className="settings-preferences-block">
          <h3>Self-Improvement</h3>
          <p className="settings-help">
            Allows the assistant to create and improve skills over time.
          </p>
          <SwitchSetting
            mutation={mutation}
            field="identity.self_improvement_enabled"
            label="Enable self-improvement"
            value={snapshot.identity.self_improvement_enabled}
          />
        </div>
      </section>
      <Section
        title="Window Mode"
        description="Controls how Row-Bot opens on the next launch."
        icon={AppWindow}
      >
        <SelectSetting
          mutation={mutation}
          field="window_mode"
          label="Window mode"
          value={snapshot.window_mode}
          options={[
            { value: 'ask', label: 'Ask on Launch' },
            { value: 'native', label: 'Native Window' },
            { value: 'browser', label: 'System Browser' },
          ]}
        />
        <p className="settings-help">
          Native Window gives Row-Bot its own app window. System Browser uses
          your default browser.
        </p>
      </Section>
      <Section
        title="Dream Cycle"
        description="Idle background cleanup for memory and sparse knowledge."
        icon={Moon}
      >
        <div className="settings-summary-strip">
          <StateChip active={snapshot.dream_cycle.enabled}>
            {enabledLabel(snapshot.dream_cycle.enabled)}
          </StateChip>
          <StateChip>
            {String(snapshot.dream_cycle.window_start).padStart(2, '0')}:00–
            {String(snapshot.dream_cycle.window_end).padStart(2, '0')}:00 idle
            window
          </StateChip>
          <StateChip>
            {formattedDateTime(snapshot.dream_cycle.last_run)} last run
          </StateChip>
        </div>
        <div className="settings-control-grid">
          <SwitchSetting
            mutation={mutation}
            field="dream_cycle.enabled"
            label="Enable Dream Cycle"
            value={snapshot.dream_cycle.enabled}
          />
          <NumberSetting
            mutation={mutation}
            field="dream_cycle.window_start"
            label="Start hour"
            value={snapshot.dream_cycle.window_start}
            min={0}
            max={23}
          />
          <NumberSetting
            mutation={mutation}
            field="dream_cycle.window_end"
            label="End hour"
            value={snapshot.dream_cycle.window_end}
            min={0}
            max={23}
          />
        </div>
        {snapshot.dream_cycle.last_summary && (
          <Facts>
            <Fact
              label="Last summary"
              value={snapshot.dream_cycle.last_summary}
            />
          </Facts>
        )}
      </Section>
      <Section
        title="Updates"
        description="Cached release state; no update check runs when Settings opens."
        icon={RefreshCw}
      >
        <div className="settings-control-grid settings-update-primary">
          <SelectSetting
            mutation={mutation}
            field="updates.channel"
            label="Update channel"
            value={snapshot.updates.channel}
            options={[
              { value: 'stable', label: 'Stable' },
              { value: 'beta', label: 'Beta' },
            ]}
          />
          <Facts>
            <Fact
              label="Current version"
              value={versionLabel(snapshot.updates.current_version)}
            />
          </Facts>
        </div>
        <details className="settings-update-details">
          <summary>Cached update details</summary>
          <Facts>
            <Fact
              label="Last check"
              value={formattedDateTime(snapshot.updates.last_check)}
            />
            <Fact
              label="Last success"
              value={formattedDateTime(snapshot.updates.last_success)}
            />
            <Fact
              label="Skipped versions"
              value={
                snapshot.updates.skipped_versions
                  .map(versionLabel)
                  .join(', ') || 'None'
              }
            />
          </Facts>
        </details>
        <p className="settings-help">
          Update status is cached. Checking or installing an update requires a
          separate reviewed action.
        </p>
      </Section>
      <Section
        title="Migration"
        description="Import selected data only through the reviewed migration wizard."
        icon={Import}
      >
        <Facts>
          <Fact
            label="Available"
            value={enabledLabel(snapshot.migration.available)}
          />
          <Fact
            label="Available sources"
            value={snapshot.migration.sources.join(', ') || 'None detected'}
          />
        </Facts>
      </Section>
    </div>
  );
}
