import { Link } from 'react-router-dom';
import {
  Activity,
  UserRound,
  BarChart3,
  BookOpen,
  Bot,
  AppWindow,
  Calculator,
  CalendarClock,
  ChevronDown,
  Download,
  CloudSun,
  FileKey,
  FileText,
  GitBranch,
  Globe2,
  HardDrive,
  Code2,
  Hammer,
  Import,
  ListChecks,
  Mic,
  MonitorCog,
  Moon,
  Network,
  Radio,
  RefreshCw,
  RotateCcw,
  Search,
  ShieldCheck,
  SlidersHorizontal,
  SquareTerminal,
  Square,
  Play,
  Volume2,
  Wrench,
} from 'lucide-react';
import {
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
  type ComponentType,
  type ReactNode,
} from 'react';
import { clientError } from '../../api/errors';
import { appPwaClient } from '../../pwa/client';
import type { ClientPlatform } from '../../platform';
import { writeClipboardText } from '../../platform/clipboard';
import { ModalTask } from '../../ui/overlays';
import ConnectedUpdateControls from './UpdateControls';
import ConnectedMigrationControls from './MigrationControls';
import ConnectedDataBackup from './DataBackup';
import ConnectedGitHubAccessControls from './GitHubAccessControls';
import ConnectedAccountAuthControls from './AccountAuthControls';
import { ConnectSheet } from './ConnectSheet';
import { ACCOUNT_LINKS } from './connect-guides';
import type {
  SettingsMutationReceipt,
  SettingsMutationRequest,
  SettingsMutationReview,
  SettingsSnapshot,
} from '../../api/types';
import {
  Button,
  Field,
  IconButton,
  Input,
  Select,
  Toggle,
} from '../../ui/primitives';
import {
  absoluteTime,
  credentialSourceLabel,
  humanizeToken,
  maskedTail,
  relativeTime,
} from '../../ui/format';
import {
  DangerAction,
  SettingsAdvanced,
  SettingsDangerZone,
  SettingsSummary,
  SummaryChip,
} from './anatomy';

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
  sessionId?: string;
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
  refreshSnapshot?: () => Promise<SettingsSnapshot>;
  drafts: SettingsDraftOwner;
  onSnapshot: (snapshot: SettingsSnapshot) => void;
  /** Saved defaults for this page's fields, when the server reports them. */
  defaults?: Partial<Record<string, SettingsValue>>;
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
  anchor,
  children,
}: {
  title: string;
  description: string;
  icon: Icon;
  tone?: 'warning' | 'danger' | '';
  /** Row-search target; defaults to the title as a slug. */
  anchor?: string;
  children: ReactNode;
}) {
  const slug = title.toLowerCase().replace(/[^a-z0-9]+/g, '-');
  const id = `settings-snapshot-${slug}`;
  return (
    <section
      className={`settings-snapshot-section stack ${tone ? `is-${tone}` : ''}`}
      aria-labelledby={id}
      data-setting-anchor={anchor ?? slug}
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
      connected: 'Connected',
      invalid: 'Reconnect needed',
      configured_unchecked: 'Configured · not checked',
      saved_unchecked: 'Saved · not checked',
      expired: 'Expired',
      unavailable: 'Unavailable',
    }[value] ?? savedStateLabel(value)
  );
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
    description: 'Build local tools.',
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
  confirm,
  group = false,
  commit = 'change',
  layout = 'row',
  onDraftChange,
  children,
}: {
  mutation: SettingsMutationIO;
  field: string;
  label: string;
  value: SettingsValue;
  hint?: string;
  group?: boolean;
  /**
   * When a change saves (decision 19): "change" at once (choices, switches),
   * "blur" when the field is left or Enter is pressed (text, numbers),
   * "explicit" with its own Save (write-only secrets).
   */
  commit?: 'change' | 'blur' | 'explicit';
  /**
   * "row" puts the control beside its label, "stack" below it; "bare" shows
   * only the control (its own accessible name) inside a row that names it.
   */
  layout?: 'row' | 'stack' | 'bare';
  validate?: (value: SettingsValue) => string;
  /** An outward choice asks first (decision 19): the question, or ''. */
  confirm?: (value: SettingsValue) => string;
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
  const [busy, setBusy] = useState<'review' | 'save' | ''>('');
  const [pendingCommand, setPendingCommand] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  // The value before the last save, while "Saved · Undo" shows.
  const [undo, setUndo] = useState<{ value: SettingsValue } | null>(null);
  const [asking, setAsking] = useState<{
    value: SettingsValue;
    question: string;
  } | null>(null);
  const abort = useRef<AbortController | null>(null);
  const running = useRef(false);
  const dirty = !sameValue(draft, value);
  // Actions dressed as fields (verify a path, grant a folder) and secrets
  // have nothing to go back to.
  const undoable =
    commit !== 'explicit' &&
    !field.endsWith('_verify') &&
    field !== 'workspace.folder_grant';
  const defaultValue = mutation.defaults?.[field];
  const modified =
    defaultValue !== undefined && !sameValue(value, defaultValue);

  useEffect(() => {
    if (!dirty && !busy) {
      mutation.drafts.discard(mutation.page, field);
      setDraft(value);
    }
  }, [busy, dirty, field, mutation.drafts, mutation.page, value]);
  useEffect(() => () => abort.current?.abort(), []);
  function change(next: SettingsValue) {
    setDraft(next);
    onDraftChange?.(next);
    if (sameValue(next, value)) mutation.drafts.discard(mutation.page, field);
    else mutation.drafts.write(mutation.page, field, next);
    setError('');
    setNotice('');
    setUndo(null);
    setAsking(null);
    if (commit !== 'change' || sameValue(next, value)) return;
    const question = confirm?.(next) ?? '';
    if (question) setAsking({ value: next, question });
    else void saveChange(next);
  }
  async function saveChange(nextValue: SettingsValue = draft, undoing = false) {
    // An Undo always sends: the page may not show the saved value yet.
    if (
      (!undoing && sameValue(nextValue, value)) ||
      running.current ||
      pendingCommand
    )
      return;
    const validation = validate?.(nextValue) ?? '';
    if (validation) {
      setError(validation);
      return;
    }
    const request: SettingsMutationRequest = {
      settings_revision: mutation.revision,
      page: mutation.page,
      field,
      value: Array.isArray(nextValue) ? [...nextValue] : nextValue,
    };
    abort.current?.abort();
    abort.current = new AbortController();
    running.current = true;
    setBusy('review');
    setError('');
    setNotice('');
    setUndo(null);
    const before = value;
    let submitted = false;
    try {
      const result = await mutation.review(request, abort.current.signal);
      if (
        result.operation !== 'settings.update' ||
        result.settings_revision !== request.settings_revision ||
        result.page !== request.page ||
        result.field !== request.field
      )
        throw { code: 'revision_conflict' };
      if (abort.current.signal.aborted) return;
      setBusy('save');
      const commandId = crypto.randomUUID();
      setPendingCommand(commandId);
      submitted = true;
      const receipt = await mutation.execute(request, result, commandId);
      if (receipt.status === 'completed' && receipt.snapshot) {
        setPendingCommand('');
        mutation.drafts.discard(mutation.page, field);
        if (field === 'computer_use.system_binary_verify') setDraft('');
        mutation.onSnapshot(receipt.snapshot);
        setNotice(
          receipt.action_result
            ? `${receipt.action_result.message} ${receipt.action_result.remediation}`.trim()
            : undoing
              ? 'Undone.'
              : 'Saved',
        );
        if (undoable && !undoing && !receipt.action_result)
          setUndo({ value: before });
      } else if (receipt.status === 'partial') {
        setNotice("Row-Bot couldn't confirm that. Check again.");
      } else {
        setPendingCommand('');
        setError('The change was rejected. Refresh and try again.');
      }
    } catch (cause) {
      if (!abort.current.signal.aborted) {
        setError(clientError(cause).message);
        if (submitted) setNotice("Row-Bot couldn't confirm that. Check again.");
      }
    } finally {
      running.current = false;
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
        if (field === 'computer_use.system_binary_verify') setDraft('');
        mutation.onSnapshot(receipt.snapshot);
        setNotice(
          receipt.action_result
            ? `${receipt.action_result.message} ${receipt.action_result.remediation}`.trim()
            : `${label} save confirmed.`,
        );
      } else {
        setError('The original change was rejected.');
      }
    } catch (cause) {
      if (!abort.current.signal.aborted) setError(clientError(cause).message);
    } finally {
      setBusy('');
    }
  }
  const modifiedDot = modified ? (
    <span
      className="settings-modified-dot"
      aria-hidden
      title="Changed from default"
    />
  ) : null;
  return (
    <div
      className="settings-saved-control"
      aria-busy={!!busy}
      data-setting-anchor={field}
      data-modified={modified ? 'true' : undefined}
      onBlur={(event) => {
        if (
          commit !== 'blur' ||
          event.currentTarget.contains(event.relatedTarget as Node | null)
        )
          return;
        void saveChange();
      }}
      onKeyDown={(event) => {
        if (commit !== 'blur') return;
        const target = event.target as HTMLElement;
        if (event.key === 'Enter' && target.tagName === 'INPUT') {
          event.preventDefault();
          void saveChange();
        } else if (event.key === 'Escape' && dirty) {
          // Leave the field as it was saved; nothing is sent.
          event.preventDefault();
          event.stopPropagation();
          change(value);
        }
      }}
    >
      {group ? (
        <fieldset className="field settings-choice-field">
          <legend>
            {label}
            {modifiedDot}
          </legend>
          {children({
            value: draft,
            setValue: change,
            disabled: !!busy || !!pendingCommand,
          })}
          {hint && <small>{hint}</small>}
        </fieldset>
      ) : layout === 'bare' ? (
        children({
          value: draft,
          setValue: change,
          disabled: !!busy || !!pendingCommand,
        })
      ) : (
        <Field
          label={label}
          hint={hint}
          layout={layout}
          labelAddon={modifiedDot}
        >
          {children({
            value: draft,
            setValue: change,
            disabled: !!busy || !!pendingCommand,
          })}
        </Field>
      )}
      <div className="settings-control-actions">
        {modified && !dirty && !pendingCommand && (
          <IconButton
            size="sm"
            label={`Reset ${label} to default`}
            disabled={!!busy}
            onClick={() => {
              setDraft(defaultValue as SettingsValue);
              void saveChange(defaultValue as SettingsValue);
            }}
          >
            <RotateCcw size={14} aria-hidden />
          </IconButton>
        )}
        {dirty && commit !== 'explicit' && error && !pendingCommand && (
          <Button onClick={() => void saveChange()} disabled={!!busy}>
            Retry
          </Button>
        )}
        {dirty && commit === 'explicit' && !pendingCommand && (
          <Button
            variant="primary"
            onClick={() => void saveChange()}
            disabled={!!busy}
          >
            {busy ? 'Saving…' : 'Save'}
          </Button>
        )}
        {pendingCommand && (
          <Button onClick={() => void checkReceipt()} disabled={!!busy}>
            {busy === 'save' ? 'Checking…' : 'Check again'}
          </Button>
        )}
      </div>
      {asking && (
        <div
          role="group"
          aria-label={`Confirm ${label}`}
          className="settings-confirm-change"
        >
          <p>{asking.question}</p>
          <div className="action-cluster">
            <Button
              variant="primary"
              className="small"
              disabled={!!busy}
              onClick={() => {
                const next = asking.value;
                setAsking(null);
                void saveChange(next);
              }}
            >
              Change it
            </Button>
            <Button className="small" onClick={() => change(value)}>
              Cancel
            </Button>
          </div>
        </div>
      )}
      {error && <p role="alert">{error}</p>}
      {notice && (
        <p role="status" className="settings-saved-note">
          {notice}
          {undo && (
            <>
              {' · '}
              <Button
                variant="ghost"
                className="small"
                aria-label={`Undo ${label}`}
                disabled={!!busy || !!pendingCommand}
                onClick={() => {
                  const previous = undo.value;
                  setDraft(previous);
                  void saveChange(previous, true);
                }}
              >
                Undo
              </Button>
            </>
          )}
        </p>
      )}
    </div>
  );
}

function ReviewedSettingsAction({
  mutation,
  field,
  label,
  description,
  variant,
  confirm,
}: {
  mutation: SettingsMutationIO;
  field: string;
  label: string;
  description: string;
  variant?: 'danger' | 'primary' | 'ghost';
  confirm?: string;
}) {
  const tunnelRecovery =
    field === 'tunnel.start_main' || field === 'tunnel.stop_main';
  const computerRecovery = field.startsWith('computer_use.');
  const recoveryKey =
    (tunnelRecovery || computerRecovery) && mutation.sessionId
      ? `${tunnelRecovery ? 'row-bot.settings-tunnel.pending.v1' : 'row-bot.settings-computer.pending.v1'}:${mutation.sessionId}:${field}`
      : '';
  function readPending() {
    if (!recoveryKey) return '';
    try {
      const value = sessionStorage.getItem(recoveryKey) || '';
      return /^[0-9a-f]{8}-[0-9a-f-]{27,}$/.test(value) ? value : '';
    } catch {
      return '';
    }
  }
  function storePending(value: string) {
    if (!recoveryKey) return;
    try {
      if (value) sessionStorage.setItem(recoveryKey, value);
      else sessionStorage.removeItem(recoveryKey);
    } catch {
      // The original server receipt remains authoritative if tab storage fails.
    }
  }
  const [pendingCommand, setPendingCommand] = useState(readPending);
  const [terminalPartial, setTerminalPartial] = useState(false);
  const [inspectedPartial, setInspectedPartial] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [confirmOpen, setConfirmOpen] = useState(false);
  const abort = useRef<AbortController | null>(null);
  const running = useRef(false);
  useEffect(() => () => abort.current?.abort(), []);

  async function runAction() {
    if (running.current || pendingCommand) return;
    const next: SettingsMutationRequest = {
      settings_revision: mutation.revision,
      page: mutation.page,
      field,
      value: true,
    };
    abort.current?.abort();
    abort.current = new AbortController();
    running.current = true;
    setBusy(true);
    setError('');
    setMessage('');
    let submitted = false;
    try {
      const result = await mutation.review(next, abort.current.signal);
      if (
        result.settings_revision !== next.settings_revision ||
        result.page !== next.page ||
        result.field !== next.field
      )
        throw { code: 'revision_conflict' };
      if (abort.current.signal.aborted) return;
      const commandId = crypto.randomUUID();
      storePending(commandId);
      setPendingCommand(commandId);
      submitted = true;
      const receipt = await mutation.execute(next, result, commandId);
      if (receipt.status === 'completed' && receipt.snapshot) {
        storePending('');
        setPendingCommand('');
        mutation.onSnapshot(receipt.snapshot);
        setMessage(
          receipt.action_result
            ? `${receipt.action_result.message} ${receipt.action_result.remediation}`.trim()
            : `${label} completed.`,
        );
      } else if (receipt.status === 'partial') {
        setMessage("Row-Bot couldn't confirm that. Check again.");
      } else {
        storePending('');
        setPendingCommand('');
        setError('The action was rejected. Refresh and try again.');
      }
    } catch (cause) {
      if (!abort.current.signal.aborted) {
        setError(clientError(cause).message);
        if (submitted)
          setMessage("Row-Bot couldn't confirm that. Check again.");
      }
    } finally {
      running.current = false;
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
        setTerminalPartial(true);
        setMessage('The original outcome is still unconfirmed.');
      } else {
        storePending('');
        setPendingCommand('');
        if (receipt.status === 'completed' && receipt.snapshot) {
          mutation.onSnapshot(receipt.snapshot);
          setMessage(
            receipt.action_result
              ? `${receipt.action_result.message} ${receipt.action_result.remediation}`.trim()
              : `${label} confirmed.`,
          );
        } else setError('The original action was rejected.');
      }
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy(false);
    }
  }

  async function inspectPartial() {
    if (!mutation.refreshSnapshot || busy) return;
    setBusy(true);
    setError('');
    try {
      const snapshot = await mutation.refreshSnapshot();
      mutation.onSnapshot(snapshot);
      const tunnel = snapshot.system.tunnel;
      if (computerRecovery) {
        const computer = snapshot.system.computer_use;
        setMessage(
          `Computer Use: ${computer.status_message} ${computer.remediation} ` +
            'You can start a new action after inspecting this state.',
        );
      } else {
        setMessage(
          `Current saved restart choice: ${tunnel.main_app_enabled ? 'enabled' : 'disabled'}; ` +
            `app tunnel: ${tunnel.main_app_url ? 'active' : 'not active'}. ` +
            'You can start a new action after inspecting this state.',
        );
      }
      setInspectedPartial(true);
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="settings-reviewed-action" aria-busy={busy}>
      <p className="settings-help">{description}</p>
      {!pendingCommand && (
        <Button
          variant={variant}
          disabled={busy}
          onClick={() => (confirm ? setConfirmOpen(true) : void runAction())}
        >
          {field.endsWith('.install') ? (
            <Download size={16} aria-hidden />
          ) : field.includes('stop') ? (
            <Square size={16} aria-hidden />
          ) : field.includes('check') || field.includes('rebuild') ? (
            <RefreshCw size={16} aria-hidden />
          ) : (
            <Play size={16} aria-hidden />
          )}
          {busy ? 'Working…' : label}
        </Button>
      )}
      {pendingCommand && (
        <Button disabled={busy} onClick={() => void checkReceipt()}>
          {busy ? 'Checking…' : 'Check again'}
        </Button>
      )}
      {pendingCommand &&
        terminalPartial &&
        (tunnelRecovery || computerRecovery) &&
        mutation.refreshSnapshot && (
          <Button disabled={busy} onClick={() => void inspectPartial()}>
            Inspect current {computerRecovery ? 'Computer Use' : 'tunnel'} state
          </Button>
        )}
      {pendingCommand && inspectedPartial && (
        <Button
          disabled={busy}
          onClick={() => {
            storePending('');
            setPendingCommand('');
            setTerminalPartial(false);
            setInspectedPartial(false);
            setMessage('The next action will use the refreshed saved state.');
          }}
        >
          {tunnelRecovery
            ? 'Try another tunnel action'
            : 'Try another Computer Use action'}
        </Button>
      )}
      {error && <p role="alert">{error}</p>}
      {message && <p role="status">{message}</p>}
      <ModalTask
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title={label}
        description={confirm || ''}
      >
        <div className="button-row">
          <Button onClick={() => setConfirmOpen(false)}>Cancel</Button>
          <Button
            variant="danger"
            onClick={() => {
              setConfirmOpen(false);
              void runAction();
            }}
          >
            {label}
          </Button>
        </div>
      </ModalTask>
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
      layout={multiline ? 'stack' : 'row'}
      commit="blur"
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
  confirm,
}: {
  mutation: SettingsMutationIO;
  field: string;
  label: string;
  value: string;
  options: { value: string; label: string }[];
  hint?: string;
  onDraftChange?: (value: string) => void;
  confirm?: (value: string) => string;
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
      confirm={confirm && ((next) => confirm(String(next)))}
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
  bare = false,
}: {
  mutation: SettingsMutationIO;
  field: string;
  label: string;
  value: boolean;
  hint?: string;
  /** Only the switch, for rows that already show the name beside it. */
  bare?: boolean;
}) {
  return (
    <SavedSetting
      mutation={mutation}
      field={field}
      label={label}
      value={value}
      hint={hint}
      layout={bare ? 'bare' : 'row'}
    >
      {({ value: draft, setValue, disabled }) => (
        <Toggle
          label={label}
          checked={Boolean(draft)}
          disabled={disabled}
          onChange={(event) => setValue(event.target.checked)}
        />
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
  hint,
}: {
  mutation: SettingsMutationIO;
  field: string;
  label: string;
  value: number | null;
  min: number;
  max: number;
  step?: number;
  optional?: boolean;
  hint?: string;
}) {
  return (
    <SavedSetting
      mutation={mutation}
      field={field}
      label={label}
      value={value}
      hint={hint}
      validate={(draft) =>
        draft == null ||
        (typeof draft === 'number' && draft >= min && draft <= max)
          ? ''
          : `Enter a value from ${min} to ${max}.`
      }
      commit="blur"
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

function SecretSetting({
  mutation,
  field,
  label,
  configured,
  source,
  fingerprint,
}: {
  mutation: SettingsMutationIO;
  field: string;
  label: string;
  configured: boolean;
  source?: string | null;
  fingerprint?: string | null;
}) {
  const [editing, setEditing] = useState(() =>
    mutation.drafts.has(mutation.page, field),
  );
  const tail = maskedTail(fingerprint);
  const where = credentialSourceLabel(source);
  const saved = /key|token|credential/i.test(label) ? 'Key saved' : 'Saved';
  return (
    <div className="settings-secret-control" data-setting-anchor={field}>
      <div className="settings-secret-summary">
        <div className="settings-secret-text">
          <span className="settings-secret-label">{label}</span>
          <span className="settings-secret-state">
            {configured ? (
              <>
                {saved}
                {tail && (
                  <>
                    {' · '}
                    <span className="settings-secret-tail">{tail}</span>
                  </>
                )}
                {where && ` · ${where}`}
              </>
            ) : (
              'Not set'
            )}
          </span>
        </div>
        {!editing && (
          <Button
            variant={configured ? 'ghost' : 'secondary'}
            aria-label={
              configured ? `Replace or remove ${label}` : `Add ${label}`
            }
            onClick={() => setEditing(true)}
          >
            {configured ? 'Replace' : 'Add'}
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
          commit="explicit"
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

/** "Small (~244 MB)" from the size option "Small (~244 MB, accurate)". */
function whisperSize(
  options: readonly { value: string; label: string }[],
  value: string,
) {
  const label =
    options.find((option) => option.value === value)?.label ?? value;
  return label.replace(/,\s*[^)]*\)/, ')');
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
  const readAloudOn = snapshot.tts.installed && snapshot.tts.enabled;
  return (
    <div className="stack settings-snapshot-page settings-voice-page">
      <SettingsSummary>
        <SummaryChip>
          {talkProvider === 'openai_realtime' ? 'Realtime Talk' : 'Local Talk'}
        </SummaryChip>
        <SummaryChip tone={readAloudOn ? 'success' : undefined}>
          {readAloudOn ? 'Read aloud on' : 'Read aloud off'}
        </SummaryChip>
      </SettingsSummary>
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
        <div className="settings-inline-row">
          <p className="settings-help">
            {talkProvider === 'local'
              ? 'Local Talk keeps microphone transcription on this machine, then sends finished text through normal chat.'
              : 'Realtime Talk sends live microphone audio only while an explicitly started session is active.'}
          </p>
          <Link
            className="button ghost"
            to={conversationId ? `/conversations/${conversationId}` : '/'}
          >
            {conversationId ? 'Open conversation voice' : 'Open a conversation'}
          </Link>
        </div>
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
        {/* Dictate needs the chosen Whisper model on this computer (B140). */}
        {snapshot.local.whisper_installed ? (
          <StateChip active>
            Whisper{' '}
            {whisperSize(
              snapshot.whisper_options,
              snapshot.local.whisper_model,
            )}{' '}
            installed
          </StateChip>
        ) : (
          <>
            <StateChip warning>Whisper not installed</StateChip>
            <ReviewedSettingsAction
              mutation={mutation}
              field="whisper.install"
              label={`Install Whisper ${whisperSize(snapshot.whisper_options, snapshot.local.whisper_model)}`}
              description="Downloads the speech recognition model from Hugging Face (Systran) once and keeps it on this computer; Dictate then works offline. This action uses network access."
            />
          </>
        )}
      </Section>
      <Section
        title="Read aloud"
        description="Hear responses spoken with the configured speech output."
        icon={Volume2}
        anchor="read-aloud"
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
                description="Downloads the Kokoro model and voices, then keeps speech generation local. This action uses network access."
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
      <SettingsAdvanced meta="Voice models, setup and diagnostics">
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
              description={`Speech output · ${
                snapshot.tts_voice_options.find(
                  (option) => option.value === snapshot.tts.voice,
                )?.label ?? humanizeToken(snapshot.tts.voice)
              }`}
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
          <Link className="button ghost" to="/settings/providers">
            Open Providers
          </Link>
        </Section>
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
      </SettingsAdvanced>
    </div>
  );
}

function publicHost(url: string) {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}

/**
 * The public link in one line (parity row 51, B106): whether it is on, the
 * address with Copy and Stop, or why it is off. Starting it asks first, since
 * the whole app becomes reachable from the internet.
 */
function PublicLinkStatus({
  tunnel,
  mutation,
  writeClipboard,
}: {
  tunnel: SettingsSnapshot['system']['tunnel'];
  mutation: SettingsMutationIO;
  writeClipboard?: ClientPlatform['writeClipboard'];
}) {
  const [copied, setCopied] = useState('');
  const url = tunnel.main_app_url;
  const count = tunnel.active_count ?? 1;
  const control = tunnel.local_owner_control_available;
  const line =
    tunnel.runtime_state === 'active'
      ? url
        ? `On at ${publicHost(url)}. Anyone can reach Row-Bot’s sign-in page there; only devices you connect get in.`
        : `On: ${count} public ${count === 1 ? 'address' : 'addresses'}.`
      : tunnel.runtime_state === 'failed'
        ? `Off. It didn’t start: ${tunnel.last_error ?? 'no reason given'}`
        : tunnel.runtime_state === 'not_configured' ||
            !tunnel.credential.configured
          ? 'Not set up: add your ngrok token below.'
          : tunnel.runtime_state === 'idle'
            ? 'Off. Set up and ready to start.'
            : 'Off.';
  return (
    <div
      className="access-status-line"
      data-tone={
        tunnel.runtime_state === 'active'
          ? 'warning'
          : tunnel.runtime_state === 'failed'
            ? 'danger'
            : undefined
      }
    >
      <p role={tunnel.runtime_state === 'failed' ? 'alert' : undefined}>
        <strong>Public</strong> {line}
      </p>
      {control ? (
        <div className="settings-action-grid">
          {url && (
            <Button
              variant="secondary"
              onClick={() =>
                void writeClipboardText(url, writeClipboard).then((done) =>
                  setCopied(done ? 'Copied.' : 'Row-Bot couldn’t copy it.'),
                )
              }
            >
              Copy address
            </Button>
          )}
          {tunnel.runtime_state === 'active' ? (
            <ReviewedSettingsAction
              mutation={mutation}
              field="tunnel.stop_main"
              label="Stop public link"
              description=""
            />
          ) : tunnel.credential.configured ? (
            <>
              <StartPublicLink mutation={mutation} />
              <ReviewedSettingsAction
                mutation={mutation}
                field="tunnel.check"
                label="Check setup"
                description=""
                variant="ghost"
              />
            </>
          ) : null}
          {copied && <span role="status">{copied}</span>}
        </div>
      ) : (
        <p className="settings-help">
          Only Row-Bot’s owner on the computer running it can start or stop the
          public link.
        </p>
      )}
    </div>
  );
}

/** Start the saved public link, after saying what that means. */
export function StartPublicLink({
  mutation,
  description = '',
}: {
  mutation: SettingsMutationIO;
  description?: string;
}) {
  return (
    <ReviewedSettingsAction
      mutation={mutation}
      field="tunnel.start_main"
      label="Start public link"
      description={description}
      variant="primary"
      confirm="Row-Bot becomes reachable from the internet at a public ngrok address until you stop it, and starts it again after a restart. Anyone can reach its sign-in page there; only a device with a code from this page gets in."
    />
  );
}

export function SystemSnapshotPanel({
  snapshot,
  mutation,
  pickFolder,
  writeClipboard,
  part = 'system',
  network,
}: {
  snapshot: SettingsSnapshot['system'];
  mutation: SettingsMutationIO;
  pickFolder?: SettingsFolderPicker;
  writeClipboard?: ClientPlatform['writeClipboard'];
  /** System: this machine's capabilities. Access: reaching it from elsewhere. */
  part?: 'system' | 'access';
  /** Access › Advanced › Network: listen mode, addresses, Tailscale. */
  network?: ReactNode;
}) {
  const appAvailability = useSyncExternalStore(
    appPwaClient.subscribe,
    appPwaClient.getSnapshot,
  );
  if (part === 'access')
    return (
      <div className="stack settings-snapshot-page settings-access-page">
        <SettingsSummary>
          <SummaryChip
            tone={
              snapshot.remote_access.listen_mode === 'local_only'
                ? 'success'
                : 'warning'
            }
          >
            {snapshot.remote_access.listen_mode === 'local_only'
              ? 'This computer only'
              : 'Your network too'}
          </SummaryChip>
          <SummaryChip>
            {snapshot.mobile_access.active_devices === 1
              ? '1 device'
              : `${snapshot.mobile_access.active_devices} devices`}
          </SummaryChip>
          {snapshot.tunnel.runtime_state === 'active' && (
            <SummaryChip tone="warning">Public link on</SummaryChip>
          )}
        </SettingsSummary>
        <SettingsAdvanced
          anchor="advanced"
          meta="Network, allowed addresses and the public link"
        >
          <Section
            title="Network"
            description="Where Row-Bot listens and the addresses it accepts."
            icon={ShieldCheck}
            anchor="network"
          >
            {network}
          </Section>
          <Section
            title="Public link"
            description="Reach Row-Bot from anywhere through ngrok. It opens only when you start it."
            icon={Network}
            anchor="tunnel"
          >
            <PublicLinkStatus
              tunnel={snapshot.tunnel}
              mutation={mutation}
              writeClipboard={writeClipboard}
            />
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
              fingerprint={snapshot.tunnel.credential.fingerprint}
            />
            <details>
              <summary>Tunnel setup</summary>
              <p className="settings-help">
                Create an ngrok account at{' '}
                <a
                  href="https://ngrok.com/"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  ngrok.com
                </a>
                , then copy your authtoken from the{' '}
                <a
                  href="https://dashboard.ngrok.com/get-started/your-authtoken"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  ngrok dashboard
                </a>{' '}
                and save it above. Opening Settings never starts the public
                link.
              </p>
            </details>
          </Section>
        </SettingsAdvanced>
      </div>
    );
  const cu = snapshot.computer_use;
  return (
    <div className="stack settings-snapshot-page settings-system-page">
      <SettingsSummary>
        <SummaryChip tone={snapshot.workspace.exists ? 'success' : 'warning'}>
          {snapshot.workspace.exists
            ? 'Workspace folder ready'
            : 'Workspace folder missing'}
        </SummaryChip>
        <SummaryChip
          tone={
            appAvailability.phase === 'ready'
              ? 'success'
              : appAvailability.phase === 'error'
                ? 'danger'
                : undefined
          }
          title="Install and offline support for this browser"
        >
          {appAvailability.phase === 'offline'
            ? 'Offline'
            : appAvailability.phase === 'ready'
              ? 'Offline ready'
              : appAvailability.phase === 'unsupported'
                ? 'No offline mode'
                : appAvailability.phase === 'error'
                  ? 'Offline unavailable'
                  : 'Checking offline'}
        </SummaryChip>
      </SettingsSummary>
      <Section
        title="Workspace folder"
        description="The filesystem tool is sandboxed to this folder."
        icon={HardDrive}
        anchor="workspace-folder"
      >
        <WorkspaceFolderSetting
          key={mutation.revision}
          mutation={mutation}
          configured={snapshot.workspace.configured}
          currentName={snapshot.workspace.label}
          exists={snapshot.workspace.exists}
          pickFolder={pickFolder}
        />
      </Section>
      <Section
        title="Shell access"
        description="Shell commands run directly on the host inside saved boundaries."
        icon={SquareTerminal}
        anchor="shell"
      >
        {snapshot.shell.available && snapshot.shell.enabled != null ? (
          <SwitchSetting
            mutation={mutation}
            field="shell.enabled"
            label="Enable Shell tool"
            value={snapshot.shell.enabled}
          />
        ) : (
          <StateChip warning>Shell tool not found</StateChip>
        )}
      </Section>
      <Section
        title="Browser & Computer Use"
        description="Web and native-app automation keep separate setup and authority."
        icon={AppWindow}
        anchor="browser-computer-use"
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
                description="Downloads Row-Bot's managed Playwright Chromium. Installed Chrome or Edge remains preferred."
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
                <small>{snapshot.computer_use.status_message}</small>
              </div>
              {snapshot.computer_use.local_owner_control_available &&
              snapshot.computer_use.disclosure_acknowledged ? (
                <SwitchSetting
                  mutation={mutation}
                  field="computer_use.enabled"
                  label="Computer Use (Beta)"
                  value={snapshot.computer_use.enabled}
                />
              ) : !snapshot.computer_use.disclosure_acknowledged ? (
                <StateChip warning>Telemetry notice required</StateChip>
              ) : (
                <StateChip>Host setup is local only</StateChip>
              )}
            </div>
          ) : (
            <StateChip warning>Computer Use unavailable</StateChip>
          )}
        </div>
        {snapshot.computer_use.available && (
          <>
            {snapshot.computer_use.local_owner_control_available &&
              !snapshot.computer_use.disclosure_acknowledged && (
                <div className="settings-system-detail">
                  <p className="settings-help">
                    {snapshot.computer_use.disclosure_text}
                  </p>
                  <SwitchSetting
                    mutation={mutation}
                    field="computer_use.disclosure_acknowledged"
                    label="Accept Cua Driver telemetry notice"
                    value={false}
                  />
                </div>
              )}
            {snapshot.computer_use.local_owner_control_available &&
              snapshot.computer_use.disclosure_acknowledged && (
                <div className="settings-system-detail">
                  <p className="settings-help">
                    {snapshot.computer_use.remediation}
                  </p>
                  <ReviewedSettingsAction
                    mutation={mutation}
                    field="computer_use.check"
                    label="Check Computer Use setup"
                    description="Run the reviewed local driver diagnostics and report missing access."
                  />
                  {snapshot.computer_use.platform === 'macos' && (
                    <details>
                      <summary>macOS permission recovery</summary>
                      <p className="settings-help">
                        Switch on Row-Bot in Accessibility and Screen Recording,
                        then check setup again. macOS may ask you to reopen
                        Row-Bot.
                      </p>
                      <ReviewedSettingsAction
                        mutation={mutation}
                        field="computer_use.open_accessibility"
                        label="Open Accessibility settings"
                        description="Open the local macOS Privacy & Security pane."
                      />
                      <ReviewedSettingsAction
                        mutation={mutation}
                        field="computer_use.open_screen_recording"
                        label="Open Screen Recording settings"
                        description="Open the local macOS Privacy & Security pane."
                      />
                    </details>
                  )}
                  {snapshot.computer_use.runtime_state === 'ready' && (
                    <ReviewedSettingsAction
                      mutation={mutation}
                      field="computer_use.test"
                      label="Test with Calculator"
                      description="Open Calculator briefly and verify a local target window."
                    />
                  )}
                  <details>
                    <summary>Advanced system Cua executable</summary>
                    <p className="settings-help">
                      Verifying starts the executable you select on this host.
                      Use only a separately reviewed Cua Driver binary.
                    </p>
                    <TextSetting
                      mutation={mutation}
                      field="computer_use.system_binary_verify"
                      label="Verify system Cua executable"
                      value=""
                      maxLength={4096}
                    />
                    {snapshot.computer_use.system_binary_configured && (
                      <ReviewedSettingsAction
                        mutation={mutation}
                        field="computer_use.use_managed_runtime"
                        label="Use managed Cua runtime"
                        description="Return to Row-Bot's reviewed managed component."
                      />
                    )}
                  </details>
                </div>
              )}
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
          </>
        )}
        {snapshot.computer_use.available &&
          snapshot.computer_use.local_owner_control_available &&
          snapshot.computer_use.disclosure_acknowledged && (
            <ReviewedSettingsAction
              mutation={mutation}
              field="computer_use.install"
              label="Install Computer Use runtime"
              description="Downloads the pinned Cua Driver artifact for this platform."
            />
          )}
      </Section>
      <Section
        title="File operations"
        description="Which read, write and destructive filesystem operations are allowed."
        icon={FileText}
        anchor="file-operations"
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
        title="Logging"
        description="Local diagnostic level and output folder."
        icon={ListChecks}
        anchor="logging"
      >
        <SelectSetting
          mutation={mutation}
          field="logging.level"
          label="File log level"
          hint={
            snapshot.logging.directory_available
              ? 'Logs are written to a local folder.'
              : 'The log folder is created when logging starts.'
          }
          value={snapshot.logging.level}
          options={[
            { value: 'DEBUG', label: 'Debug' },
            { value: 'INFO', label: 'Info' },
            { value: 'WARNING', label: 'Warning' },
            { value: 'ERROR', label: 'Error' },
          ]}
        />
        <ReviewedSettingsAction
          mutation={mutation}
          field="logging.open"
          label="Open log folder"
          description="Opens Row-Bot's fixed local log directory; the renderer never receives its path."
        />
      </Section>
      <SettingsAdvanced meta="Blocked commands, offline support">
        {snapshot.shell.available && snapshot.shell.enabled != null && (
          <TextSetting
            mutation={mutation}
            field="shell.blocked_patterns"
            label="Additional blocked patterns (comma-separated)"
            value={snapshot.shell.blocked_patterns}
          />
        )}
        <div className="settings-inline-row">
          <div>
            <strong>App availability</strong>
            <p role="status">
              {appAvailability.phase === 'offline'
                ? 'Offline. Unsent drafts remain on this device.'
                : appAvailability.phase === 'error'
                  ? 'Install and offline support are unavailable. Check browser storage and service worker permissions.'
                  : appAvailability.phase === 'unsupported'
                    ? 'This browser does not support install and offline mode.'
                    : appAvailability.updateAvailable
                      ? 'An update is ready to apply from the app notice.'
                      : appAvailability.phase === 'ready'
                        ? 'Install and offline support are ready.'
                        : 'Checking install and offline support.'}
            </p>
          </div>
        </div>
      </SettingsAdvanced>
      {cu.available &&
        cu.local_owner_control_available &&
        cu.disclosure_acknowledged && (
          <SettingsDangerZone>
            <DangerAction
              title="Remove the managed Computer Use runtime"
              description="Deletes Row-Bot's managed Cua Driver and turns Computer Use off. You can install it again later."
            >
              <ReviewedSettingsAction
                mutation={mutation}
                field="computer_use.remove"
                label="Remove managed Cua runtime"
                description=""
                variant="danger"
                confirm="This deletes the managed runtime files. You can install the reviewed runtime again later."
              />
            </DangerAction>
          </SettingsDangerZone>
        )}
    </div>
  );
}

function WorkspaceFolderSetting({
  mutation,
  configured,
  currentName,
  exists,
  pickFolder,
}: {
  mutation: SettingsMutationIO;
  configured: boolean;
  currentName: string;
  exists?: boolean;
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
        <div className="settings-inline-row settings-folder-row">
          <div>
            <strong>
              {configured
                ? `Current folder: ${currentName || 'Selected local folder'}`
                : 'No workspace folder selected'}
            </strong>
            <p>
              {exists === false && configured ? 'Folder not found. ' : ''}
              The full local path is never sent to the renderer.
            </p>
          </div>
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

const trackerKinds: Record<string, string> = {
  boolean: 'Yes or no',
  duration: 'Duration',
  count: 'Count',
  numeric: 'Number',
  number: 'Number',
  scale: 'Scale',
  text: 'Note',
};

export function TrackerSnapshotPanel({
  snapshot,
  mutation,
  showDanger = true,
}: {
  snapshot: SettingsSnapshot['tracker'];
  mutation: SettingsMutationIO;
  showDanger?: boolean;
}) {
  return (
    <div className="stack settings-snapshot-page">
      <SettingsSummary>
        <SummaryChip>
          {snapshot.items.length === 1
            ? '1 tracker'
            : `${snapshot.items.length} trackers`}
        </SummaryChip>
        <SummaryChip>
          {snapshot.total_entries === 1
            ? '1 entry'
            : `${snapshot.total_entries.toLocaleString()} entries`}
        </SummaryChip>
      </SettingsSummary>
      <Section
        title="Tracker Tool"
        description="Lets the assistant log and review habits, symptoms and health events."
        icon={ListChecks}
        anchor="tracker.enabled"
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
      <Section
        title="Trackers"
        description="Stored on this device."
        icon={CalendarClock}
        anchor="trackers"
      >
        {snapshot.items.length ? (
          <ul className="settings-row-list" aria-label="Saved trackers">
            {snapshot.items.map((tracker) => (
              <li key={tracker.tracker_id}>
                <span className="settings-row-list-icon" aria-hidden>
                  {tracker.icon || <Activity size={15} aria-hidden />}
                </span>
                <div className="settings-row-list-text">
                  <strong>{tracker.name}</strong>
                  <small>
                    {trackerKinds[tracker.kind] ?? humanizeToken(tracker.kind)}
                    {tracker.unit ? ` · ${tracker.unit}` : ''}
                    {tracker.last_event_at ? (
                      <>
                        {' · Last '}
                        <time
                          dateTime={tracker.last_event_at}
                          title={absoluteTime(tracker.last_event_at)}
                        >
                          {relativeTime(tracker.last_event_at)}
                        </time>
                      </>
                    ) : (
                      ' · No entries yet'
                    )}
                  </small>
                </div>
                <span className="settings-row-list-meta">
                  {tracker.entry_count === 1
                    ? '1 entry'
                    : `${tracker.entry_count} entries`}
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted">No trackers yet.</p>
        )}
      </Section>
      {showDanger && snapshot.items.length > 0 && (
        <SettingsDangerZone>
          <TrackerDangerAction mutation={mutation} />
        </SettingsDangerZone>
      )}
    </div>
  );
}

/** Delete every tracker row: a reviewed deletion with its own confirmation. */
export function TrackerDangerAction({
  mutation,
}: {
  mutation: SettingsMutationIO;
}) {
  return (
    <DangerAction
      title="Delete all tracker data"
      description="Removes every habit and health tracker and all their entries. This cannot be undone."
    >
      <TrackerDeleteAll mutation={mutation} />
    </DangerAction>
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
          "Row-Bot couldn't confirm the deletion. Check again before retrying.",
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
          {busy === 'delete' ? 'Checking…' : 'Check again'}
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
  showActions = false,
}: {
  label: string;
  icon: Icon;
  account: SettingsSnapshot['accounts']['github'];
  mutation: SettingsMutationIO;
  prefix: 'github' | 'x';
  showActions?: boolean;
}) {
  const status =
    prefix === 'github' && account.authentication_state === 'not_configured'
      ? 'Not connected'
      : accountStateLabel(account.authentication_state);
  return (
    <details className="settings-account-panel" data-setting-anchor={prefix}>
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
        {prefix === 'github' ? (
          // GitHub's connect sheet (parity row 45).
          <ConnectSheet
            title="Connect GitHub"
            steps={[
              {
                id: 'token',
                text: 'Sign in with the GitHub CLI (gh auth login) on this computer, or create a fine-grained token for the repositories Row-Bot should use.',
                link: {
                  href: ACCOUNT_LINKS.githubToken,
                  label: 'Create a token',
                },
              },
              {
                id: 'paste',
                text: 'If you made a token, paste it here. It stays in your keychain.',
                done: account.credential?.configured === true,
                children: (
                  <SecretSetting
                    mutation={mutation}
                    field="github.credential"
                    label="GitHub token"
                    configured={account.credential?.configured ?? false}
                    source={account.credential?.source}
                    fingerprint={account.credential?.fingerprint}
                  />
                ),
              },
              {
                id: 'check',
                text: 'Check that Row-Bot can reach GitHub with it.',
                done: account.authentication_state === 'connected',
                children: showActions ? (
                  <ConnectedGitHubAccessControls />
                ) : null,
              },
            ]}
          />
        ) : (
          // X's connect sheet, with the callback address to register.
          <ConnectSheet
            title="Connect X"
            steps={[
              {
                id: 'app',
                text: 'In the X developer portal, create a project and an app, and turn on OAuth 2.0 with read and write access.',
                link: {
                  href: ACCOUNT_LINKS.xPortal,
                  label: 'Open the developer portal',
                },
              },
              ...(account.callback_url
                ? [
                    {
                      id: 'callback',
                      text: 'Add this callback address to the app’s OAuth settings:',
                      copy: {
                        value: account.callback_url,
                        label: 'X callback address',
                      },
                    },
                  ]
                : []),
              {
                id: 'keys',
                text: 'Paste its client ID and client secret.',
                done: account.configured,
                children: (
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
                      fingerprint={account.credential?.fingerprint}
                    />
                  </>
                ),
              },
              {
                id: 'authenticate',
                text: 'Authenticate X in your browser.',
                done: account.authentication_state === 'saved_unchecked',
                children: showActions ? (
                  <ConnectedAccountAuthControls account="x" />
                ) : null,
              },
            ]}
          />
        )}
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
        {prefix === 'x' && (
          <>
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
  showActions = false,
}: {
  gmail: SettingsSnapshot['accounts']['gmail'];
  calendar: SettingsSnapshot['accounts']['calendar'];
  mutation: SettingsMutationIO;
  showActions?: boolean;
}) {
  const status =
    gmail.authentication_state === calendar.authentication_state
      ? gmail.authentication_state === 'expired'
        ? 'Token issue'
        : accountStateLabel(gmail.authentication_state)
      : `Gmail: ${accountStateLabel(gmail.authentication_state)} · Calendar: ${accountStateLabel(calendar.authentication_state)}`;
  return (
    <details className="settings-account-panel" data-setting-anchor="google">
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
        <ConnectSheet
          title="Connect Google"
          steps={[
            {
              id: 'apis',
              text: 'In Google Cloud, create a project and turn on the Gmail API and the Google Calendar API.',
              link: {
                href: ACCOUNT_LINKS.googleLibrary,
                label: 'Open the API library',
              },
            },
            {
              id: 'consent',
              text: 'Set up the OAuth consent screen and add yourself as a test user.',
              link: {
                href: ACCOUNT_LINKS.googleConsent,
                label: 'Open the consent screen',
              },
            },
            {
              id: 'client',
              text: 'Create an OAuth client ID of type Desktop app and download its JSON file.',
              link: {
                href: ACCOUNT_LINKS.googleCredentials,
                label: 'Open credentials',
              },
              done: gmail.configured || calendar.configured,
            },
            {
              id: 'authenticate',
              text: 'Choose that file here, then authenticate Google in your browser.',
              done:
                gmail.authentication_state === 'saved_unchecked' ||
                calendar.authentication_state === 'saved_unchecked',
              children: showActions ? (
                <ConnectedAccountAuthControls account="google" />
              ) : null,
            },
          ]}
        />
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
          Choose a client file or start authentication on the local owner
          device.
        </p>
      </div>
    </details>
  );
}
export function AccountsSnapshotPanel({
  snapshot,
  mutation,
  showActions = false,
}: {
  snapshot: SettingsSnapshot['accounts'];
  mutation: SettingsMutationIO;
  showActions?: boolean;
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
  const connected = [
    snapshot.github,
    snapshot.gmail,
    snapshot.calendar,
    snapshot.x,
  ].filter((account) => account.configured).length;
  return (
    <div className="stack settings-snapshot-page settings-accounts-page">
      <SettingsSummary>
        <SummaryChip tone={connected ? 'success' : undefined}>
          {connected} configured
        </SummaryChip>
      </SettingsSummary>
      <AccountPanel
        label="GitHub"
        icon={GitBranch}
        account={snapshot.github}
        mutation={mutation}
        prefix="github"
        showActions={showActions}
      />
      <GoogleAccountPanel
        gmail={snapshot.gmail}
        calendar={snapshot.calendar}
        mutation={mutation}
        showActions={showActions}
      />
      <AccountPanel
        label="X (Twitter)"
        icon={Network}
        account={snapshot.x}
        mutation={mutation}
        prefix="x"
        showActions={showActions}
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
    developer: Code2,
  };
  if (snapshot.availability !== 'available')
    return (
      <Section
        title="Built-in tools"
        description="Saved built-in tool settings are unavailable."
        icon={Wrench}
        anchor="built-in-tools"
      >
        <StateChip warning>Built-in tool settings unavailable</StateChip>
      </Section>
    );
  return (
    <Section
      title="Built-in tools"
      description={`Small tools for everyday tasks · ${utilities.filter((item) => item.enabled).length} of ${availableUtilities.length} on.`}
      icon={Wrench}
      anchor="built-in-tools"
    >
      <ul className="settings-row-list settings-utility-list">
        {utilities.map((utility) => {
          const UtilityIcon = icons[utility.utility_id] ?? Wrench;
          const presentation = utilityPresentation[utility.utility_id] ?? {
            label: utility.label,
            description: utility.description,
          };
          return (
            <li key={utility.utility_id}>
              <span className="settings-row-list-icon" aria-hidden>
                <UtilityIcon size={16} aria-hidden />
              </span>
              <div className="settings-row-list-text">
                <strong>{presentation.label}</strong>
                <small>{presentation.description}</small>
              </div>
              <div className="settings-utility-toggle">
                <SwitchSetting
                  mutation={mutation}
                  field={`${utility.utility_id}.enabled`}
                  label={`Enable ${presentation.label}`}
                  value={Boolean(utility.enabled)}
                  bare
                />
              </div>
            </li>
          );
        })}
      </ul>
      {!utilities.length && (
        <p className="settings-help">No built-in tools are available.</p>
      )}
    </Section>
  );
}

/** U45: the model that reads documents, chosen next to the queue. */
export function DocumentModelSetting({
  snapshot,
  mutation,
  models,
}: {
  snapshot: SettingsSnapshot['documents'];
  mutation: SettingsMutationIO;
  models: { model_ref: string; label: string; available: boolean }[];
}) {
  const current = snapshot.processing_model ?? '';
  return (
    <SelectSetting
      mutation={mutation}
      field="processing_model"
      label="Model for documents"
      hint="Reads each document and saves what it says to knowledge. The conversation's model is used when none is picked."
      value={current}
      options={[
        { value: '', label: "Conversation's model" },
        ...models
          .filter((model) => model.available || model.model_ref === current)
          .map((model) => ({
            value: model.model_ref,
            label: model.available
              ? model.label
              : `${model.label} (not available)`,
          })),
      ]}
    />
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
        description="Local models stay private; cloud models send the document text to the provider."
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
      <SettingsSummary>
        <SummaryChip>
          {snapshot.indexed_documents == null
            ? 'Indexed count unavailable'
            : `${snapshot.indexed_documents.toLocaleString()} indexed`}
        </SummaryChip>
        <SummaryChip
          tone={vectors.state === 'current' ? 'success' : 'warning'}
          title={vectors.detail}
        >
          {vectors.state === 'current'
            ? 'Vectors current'
            : `Vectors ${humanizeToken(vectors.state).toLowerCase()}`}
        </SummaryChip>
      </SettingsSummary>
      <Section
        title="Embedding Engine"
        description={`Local models stay private; cloud models send the document text to the provider. Active: ${activeEmbedding}.`}
        icon={Network}
        anchor="embedding"
      >
        <div className="settings-control-grid">
          <SelectSetting
            mutation={mutation}
            field="embedding.provider"
            label="Provider"
            value={embedding.provider}
            onDraftChange={setProvider}
            confirm={(next) =>
              next === 'cloud'
                ? 'Documents and memories will be sent to the cloud embedding provider to be indexed. Change it?'
                : ''
            }
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
      </Section>
      <SettingsAdvanced meta="Index health and maintenance">
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
        <div className="settings-document-maintenance-wrap">
          <div
            className="settings-document-maintenance"
            role="group"
            aria-label="Document index maintenance"
          >
            <ReviewedSettingsAction
              mutation={mutation}
              field="vectors.rebuild"
              label="rebuild document vectors"
              description="Recreate document search vectors from the documents already added."
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
                  description="Download the selected embedding model. This requires network access."
                />
                <ReviewedSettingsAction
                  mutation={mutation}
                  field="local_model.repair"
                  label="repair local model"
                  description="Replace the selected model's cached files. This requires network access."
                  variant="danger"
                />
              </>
            )}
          </div>
        </div>
      </SettingsAdvanced>
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
      <SettingsSummary>
        <SummaryChip>
          {
            snapshot.items.filter((tool) => tool.available && tool.enabled)
              .length
          }{' '}
          research tools on
        </SummaryChip>
      </SettingsSummary>
      <Section
        title="Capability loading"
        description="Choose how enabled external capabilities are exposed to the model."
        icon={SlidersHorizontal}
        anchor="capability-loading"
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
        title="Retrieval compression"
        description="Controls how search results are filtered before reaching the model."
        icon={Search}
        anchor="retrieval-compression"
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
        description="Research tools the assistant can use, with masked credentials."
        icon={BookOpen}
        anchor="search-tools"
      >
        <label className="settings-inline-search">
          <span className="visually-hidden">Search research tools</span>
          <Search size={14} aria-hidden />
          <Input
            type="search"
            value={toolQuery}
            onChange={(event) => setToolQuery(event.target.value)}
            placeholder="Filter by name or purpose"
          />
        </label>
        <ul className="settings-toggle-list settings-row-list">
          {tools.map((tool) => {
            // A tool that needs a key says so before it is turned on (U50).
            const missingKey =
              tool.credentials.length > 0 &&
              tool.credentials.some((credential) => !credential.configured);
            return (
              <li key={tool.tool_id}>
                <div className="settings-row-list-text">
                  <strong>{tool.displayLabel}</strong>
                  <small>
                    {tool.description}
                    {!tool.available ? ' · Unavailable' : ''}
                    {tool.configured_fields.length
                      ? ` · ${tool.configured_fields.length} configured fields`
                      : ''}
                  </small>
                  {missingKey && (
                    <small className="settings-tool-needs-key">
                      {tool.enabled
                        ? 'On, but it has no key yet: it fails until you add one below.'
                        : 'Needs its key first: add it under Credentials & setup.'}
                    </small>
                  )}
                </div>
                {tool.available && tool.enabled != null ? (
                  <SwitchSetting
                    mutation={mutation}
                    field={`${tool.tool_id}.enabled`}
                    label={`Enable ${tool.displayLabel}`}
                    value={tool.enabled}
                    bare
                  />
                ) : (
                  <StateChip warning>Unavailable</StateChip>
                )}
                {(tool.credentials.length > 0 || tool.setupUrl) && (
                  <details
                    className="settings-snapshot-disclosure settings-tool-detail"
                    open={missingKey || undefined}
                  >
                    <summary>Credentials &amp; setup</summary>
                    {tool.setupUrl && (
                      <p className="settings-help">
                        Create the provider credential at{' '}
                        <a
                          href={tool.setupUrl}
                          target="_blank"
                          rel="noreferrer"
                        >
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
                          fingerprint={credential.fingerprint}
                        />
                      ) : (
                        <div
                          className="settings-secret-summary"
                          key={credential.name}
                        >
                          <div className="settings-secret-text">
                            <span className="settings-secret-label">
                              {credential.label}
                            </span>
                            <span className="settings-secret-state">
                              {credential.configured
                                ? `Saved${credential.fingerprint ? ` · ${maskedTail(credential.fingerprint)}` : ''}`
                                : 'Not set'}
                            </span>
                          </div>
                        </div>
                      ),
                    )}
                  </details>
                )}
              </li>
            );
          })}
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
  showUpdateControls = false,
  part = 'preferences',
}: {
  snapshot: SettingsSnapshot['preferences'];
  mutation: SettingsMutationIO;
  showUpdateControls?: boolean;
  /** Preferences, or the Updates / Data pages that share its snapshot. */
  part?: 'preferences' | 'updates' | 'data';
}) {
  if (part === 'updates')
    return (
      <div className="stack settings-snapshot-page settings-updates-page">
        <SettingsSummary>
          <SummaryChip tone="success">
            {versionLabel(snapshot.updates.current_version)}
          </SummaryChip>
          <SummaryChip>
            {snapshot.updates.channel === 'beta' ? 'Beta channel' : 'Stable'}
          </SummaryChip>
        </SettingsSummary>
        <Section
          title="Updates"
          description="Cached release state; no update check runs when Settings opens."
          icon={RefreshCw}
          anchor="updates"
        >
          <SelectSetting
            mutation={mutation}
            field="updates.channel"
            label="Update channel"
            hint="Beta gets new features first and may be less stable."
            value={snapshot.updates.channel}
            options={[
              { value: 'stable', label: 'Stable' },
              { value: 'beta', label: 'Beta' },
            ]}
          />
          <div className="settings-inline-row">
            <div>
              <strong>Last check</strong>
              <p>
                {snapshot.updates.last_check ? (
                  <time
                    dateTime={snapshot.updates.last_check}
                    title={absoluteTime(snapshot.updates.last_check)}
                  >
                    {relativeTime(snapshot.updates.last_check)}
                  </time>
                ) : (
                  'Never checked'
                )}
              </p>
            </div>
          </div>
          {showUpdateControls && <ConnectedUpdateControls />}
        </Section>
        <SettingsAdvanced meta="Cached update details">
          <Facts>
            <Fact
              label="Current version"
              value={versionLabel(snapshot.updates.current_version)}
            />
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
        </SettingsAdvanced>
      </div>
    );
  if (part === 'data')
    return (
      <div className="stack settings-snapshot-page settings-data-page">
        {showUpdateControls && (
          <Section
            title="Back up and restore"
            description="A copy of your Row-Bot on this computer, without passwords or sign-ins."
            icon={HardDrive}
            anchor="backup"
          >
            <ConnectedDataBackup />
          </Section>
        )}
        <Section
          title="Import from another assistant"
          description="Scan and select data from Hermes Agent or OpenClaw. Nothing is written until you confirm."
          icon={Import}
          anchor="migration"
        >
          <div className="settings-inline-row">
            <div>
              <strong>Works with</strong>
              <p>
                {snapshot.migration.available
                  ? snapshot.migration.sources.join(' and ')
                  : 'Migration is unavailable on this installation.'}
              </p>
            </div>
          </div>
          {showUpdateControls && <ConnectedMigrationControls />}
        </Section>
      </div>
    );
  return (
    <div className="stack settings-snapshot-page">
      <SettingsSummary>
        <SummaryChip>{snapshot.identity.name}</SummaryChip>
        <SummaryChip
          tone={snapshot.dream_cycle.enabled ? 'success' : undefined}
        >
          {snapshot.dream_cycle.enabled ? 'Dream Cycle on' : 'Dream Cycle off'}
        </SummaryChip>
      </SettingsSummary>
      <section
        className="settings-preferences-identity settings-snapshot-section stack"
        aria-label="Assistant identity"
        data-setting-anchor="identity"
      >
        <header className="settings-snapshot-heading">
          <UserRound size={18} aria-hidden />
          <div>
            <h3>Identity</h3>
            <p>How the assistant introduces itself and behaves.</p>
          </div>
        </header>
        <TextSetting
          mutation={mutation}
          field="identity.name"
          label="Name"
          hint="Shown in chat and used in the system prompt."
          value={snapshot.identity.name}
          maxLength={80}
        />
        <div className="settings-preferences-personality">
          <TextSetting
            mutation={mutation}
            field="identity.personality"
            label="Personality"
            value={snapshot.identity.personality}
            maxLength={snapshot.identity.personality_max_length}
            multiline
          />
          <p className="settings-help">
            Optional behaviour guidance, up to{' '}
            {snapshot.identity.personality_max_length} characters ·{' '}
            {snapshot.identity.personality.length} /{' '}
            {snapshot.identity.personality_max_length}
          </p>
          <p className="settings-preferences-preview">
            <span>Preview</span>
            You are {snapshot.identity.name}, a knowledgeable personal assistant
            with access to tools.
            {snapshot.identity.personality
              ? ` ${snapshot.identity.personality}`
              : ''}
          </p>
        </div>
        <SwitchSetting
          mutation={mutation}
          field="identity.self_improvement_enabled"
          label="Enable self-improvement"
          hint="Lets the assistant create and improve skills over time."
          value={snapshot.identity.self_improvement_enabled}
        />
      </section>
      <Section
        title="Launch"
        description="How Row-Bot opens on the next launch."
        icon={AppWindow}
        anchor="window-mode"
      >
        <SelectSetting
          mutation={mutation}
          field="window_mode"
          label="Window mode"
          hint="Native Window gives Row-Bot its own app window. System Browser uses your default browser."
          value={snapshot.window_mode}
          options={[
            { value: 'ask', label: 'Ask on Launch' },
            { value: 'native', label: 'Native Window' },
            { value: 'browser', label: 'System Browser' },
          ]}
        />
      </Section>
      <Section
        title="Dream Cycle"
        description="Idle background clean-up for memory and sparse knowledge."
        icon={Moon}
        anchor="dream-cycle"
      >
        <SwitchSetting
          mutation={mutation}
          field="dream_cycle.enabled"
          label="Enable Dream Cycle"
          hint={
            snapshot.dream_cycle.last_run
              ? `Last run ${relativeTime(snapshot.dream_cycle.last_run)}.`
              : 'Has not run yet.'
          }
          value={snapshot.dream_cycle.enabled}
        />
        <NumberSetting
          mutation={mutation}
          field="dream_cycle.window_start"
          label="Start hour"
          hint="Local time, 0–23. Runs only while Row-Bot is idle."
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
        {snapshot.dream_cycle.last_summary && (
          <div className="settings-inline-row">
            <div>
              <strong>Last summary</strong>
              <p>{snapshot.dream_cycle.last_summary}</p>
            </div>
          </div>
        )}
      </Section>
    </div>
  );
}
