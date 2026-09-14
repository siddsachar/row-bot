import { Link } from 'react-router-dom';
import {
  BookOpen,
  Bot,
  AppWindow,
  CalendarDays,
  FileKey,
  FileText,
  GitBranch,
  HardDrive,
  Import,
  ListChecks,
  Mic,
  Moon,
  Network,
  Radio,
  RefreshCw,
  Search,
  ShieldCheck,
  SlidersHorizontal,
  Smartphone,
  SquareTerminal,
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
  children,
}: {
  mutation: SettingsMutationIO;
  field: string;
  label: string;
  value: SettingsValue;
  hint?: string;
  group?: boolean;
  validate?: (value: SettingsValue) => string;
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
}: {
  mutation: SettingsMutationIO;
  field: string;
  label: string;
  value: string;
  options: { value: string; label: string }[];
  hint?: string;
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
                {option.replaceAll('_', ' ')}
              </label>
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
  return (
    <div className="settings-secret-control">
      <div className="settings-summary-strip">
        <StateChip active={configured} warning={!configured}>
          {configured ? 'Saved · masked' : 'Not configured'}
        </StateChip>
        {source && <StateChip>{source}</StateChip>}
      </div>
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
              placeholder={configured ? 'Enter a replacement' : 'Enter a value'}
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
          </div>
        )}
      </SavedSetting>
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
  return (
    <div className="stack settings-snapshot-page">
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
          />
          <TextSetting
            mutation={mutation}
            field="runtime.talk_model"
            label="Talk model"
            value={snapshot.runtime.talk_model}
          />
          <SelectSetting
            mutation={mutation}
            field="runtime.realtime_voice"
            label="Realtime voice"
            value={snapshot.runtime.realtime_voice}
            options={snapshot.realtime_voice_options}
          />
          <SwitchSetting
            mutation={mutation}
            field="runtime.captions_enabled"
            label="Local captions"
            value={snapshot.runtime.captions_enabled}
          />
          <SwitchSetting
            mutation={mutation}
            field="runtime.talk_auto_start"
            label="Start automatically"
            value={snapshot.runtime.talk_auto_start}
          />
          <SwitchSetting
            mutation={mutation}
            field="runtime.realtime_fallback_to_local"
            label="Fallback to local Talk"
            value={snapshot.runtime.realtime_fallback_to_local}
          />
        </div>
        <p className="settings-help">
          Local Talk keeps transcription on this machine. Realtime Talk sends
          live microphone audio only while an explicitly started session is
          active.
        </p>
        <Link
          className="button"
          to={conversationId ? `/conversations/${conversationId}` : '/'}
        >
          {conversationId ? 'Open conversation voice' : 'Open a conversation'}
        </Link>
      </Section>
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
          <TextSetting
            mutation={mutation}
            field="runtime.speech_output_voice"
            label="Provider voice"
            value={snapshot.runtime.speech_output_voice}
          />
          <SwitchSetting
            mutation={mutation}
            field="tts.enabled"
            label="Local TTS"
            value={snapshot.tts.enabled}
          />
          <SelectSetting
            mutation={mutation}
            field="tts.voice"
            label="Local voice"
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
            label="Automatically read responses"
            value={snapshot.tts.auto_speak}
          />
        </div>
      </Section>
      <Section
        title="Voice Models"
        description="Saved runtime choices and masked provider configuration."
        icon={Radio}
      >
        <Facts>
          <Fact
            label="SenseVoice path"
            value={configuredLabel(snapshot.local.sensevoice_path_configured)}
          />
          <Fact label="Local runtime" value={snapshot.local.runtime_state} />
          <Fact
            label="Local TTS package"
            value={snapshot.tts.installed ? 'Installed' : 'Not installed'}
          />
        </Facts>
        <SecretSetting
          mutation={mutation}
          field="openai_realtime_credential"
          label="OpenAI Realtime API key"
          configured={snapshot.openai_realtime_credential.configured}
          source={snapshot.openai_realtime_credential.source}
        />
        <Link className="button" to="/settings/providers">
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
      </Section>
    </div>
  );
}

export function SystemSnapshotPanel({
  snapshot,
  mutation,
}: {
  snapshot: SettingsSnapshot['system'];
  mutation: SettingsMutationIO;
}) {
  return (
    <div className="stack settings-snapshot-page">
      <Section
        title="Workspace Folder"
        description="The filesystem tool is sandboxed to this folder."
        icon={HardDrive}
      >
        <TextSetting
          mutation={mutation}
          field="workspace.path"
          label="Workspace folder"
          value={snapshot.workspace.path}
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
        {snapshot.shell.enabled != null ? (
          <SwitchSetting
            mutation={mutation}
            field="shell.enabled"
            label="Enable shell tool"
            value={snapshot.shell.enabled}
          />
        ) : (
          <StateChip warning>Shell unavailable</StateChip>
        )}
        <TextSetting
          mutation={mutation}
          field="shell.blocked_patterns"
          label="Additional blocked patterns"
          value={snapshot.shell.blocked_patterns}
          hint="One saved pattern expression."
        />
      </Section>
      <Section
        title="Browser & Computer Use"
        description="Web and native-app automation keep separate setup and authority."
        icon={AppWindow}
      >
        <div className="settings-control-grid">
          {snapshot.browser.enabled != null && (
            <SwitchSetting
              mutation={mutation}
              field="browser.enabled"
              label="Enable browser tool"
              value={snapshot.browser.enabled}
            />
          )}
          {snapshot.computer_use.enabled != null && (
            <SwitchSetting
              mutation={mutation}
              field="computer_use.enabled"
              label="Computer Use (Beta)"
              value={snapshot.computer_use.enabled}
            />
          )}
        </div>
        <Facts>
          <Fact
            label="Browser runtime"
            value={snapshot.browser.runtime_state}
          />
          <Fact
            label="Native runtime"
            value={snapshot.computer_use.runtime_state}
          />
          <Fact
            label="Disclosure"
            value={
              snapshot.computer_use.disclosure_acknowledged
                ? 'Acknowledged'
                : 'Not acknowledged'
            }
          />
        </Facts>
      </Section>
      <Section
        title="File Operations"
        description="Saved read, write, and destructive filesystem operations."
        icon={FileText}
      >
        {snapshot.file_operations.enabled != null && (
          <SwitchSetting
            mutation={mutation}
            field="file_operations.enabled"
            label="Enable file operations"
            value={snapshot.file_operations.enabled}
          />
        )}
        <ChoiceListSetting
          mutation={mutation}
          field="file_operations.selected"
          label="Allowed operations"
          value={snapshot.file_operations.selected}
          options={snapshot.file_operations.options}
        />
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
          Runtime status: Not checked. Opening Settings never starts or exposes
          a tunnel.
        </p>
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
      </Section>
      <Section
        title="Mobile Access"
        description="Active paired devices and local client sessions."
        icon={Smartphone}
      >
        <Facts>
          <Fact
            label="Availability"
            value={snapshot.mobile_access.availability}
          />
          <Fact
            label="Active devices"
            value={snapshot.mobile_access.active_devices}
          />
          <Fact
            label="Active sessions"
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
          label="Log level"
          value={snapshot.logging.level}
          options={[
            { value: 'DEBUG', label: 'Debug' },
            { value: 'INFO', label: 'Info' },
            { value: 'WARNING', label: 'Warning' },
            { value: 'ERROR', label: 'Error' },
          ]}
        />
        <Facts>
          <Fact label="Log directory" value={snapshot.logging.directory} />
        </Facts>
      </Section>
    </div>
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
        {snapshot.enabled != null ? (
          <SwitchSetting
            mutation={mutation}
            field="enabled"
            label="Enable Tracker"
            value={snapshot.enabled}
          />
        ) : (
          <StateChip warning>Tracker tool unavailable</StateChip>
        )}
        <div className="settings-summary-strip">
          <StateChip>{snapshot.items.length} trackers</StateChip>
          <StateChip>{snapshot.total_entries} entries</StateChip>
        </div>
      </Section>
      {snapshot.items.length ? (
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
                  ? ` · Last ${tracker.last_event_at}`
                  : ''}
              </span>
              <StateChip>{tracker.entry_count} entries</StateChip>
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">No trackers yet.</p>
      )}
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
  prefix: 'github' | 'gmail' | 'calendar' | 'x';
}) {
  return (
    <details className="settings-account-panel">
      <summary>
        <Icon size={18} aria-hidden />
        <strong>{label}</strong>
        <span>{account.authentication_state.replaceAll('_', ' ')}</span>
      </summary>
      <div className="stack settings-account-content">
        <Facts>
          <Fact
            label="Configuration"
            value={configuredLabel(account.configured)}
          />
          <Fact
            label="Credential source"
            value={account.credential?.source || 'Not saved'}
          />
        </Facts>
        {prefix !== 'github' && account.enabled != null && (
          <SwitchSetting
            mutation={mutation}
            field={`${prefix}.enabled`}
            label={`Enable ${label}`}
            value={account.enabled}
          />
        )}
        {(prefix === 'gmail' || prefix === 'calendar') && (
          <TextSetting
            mutation={mutation}
            field={`${prefix}.credentials_path`}
            label="Credentials file"
            value={account.credentials_path || ''}
          />
        )}
        {(prefix === 'gmail' || prefix === 'calendar') && (
          <ChoiceListSetting
            mutation={mutation}
            field={`${prefix}.operations`}
            label="Allowed operations"
            value={account.operations}
            options={
              prefix === 'gmail'
                ? [
                    'search_gmail',
                    'get_gmail_message',
                    'get_gmail_thread',
                    'create_gmail_draft',
                    'send_gmail_message',
                  ]
                : [
                    'get_current_datetime',
                    'search_events',
                    'create_calendar_event',
                    'create_calendar_events',
                    'update_calendar_event',
                    'move_calendar_event',
                    'delete_calendar_event',
                  ]
            }
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
export function AccountsSnapshotPanel({
  snapshot,
  mutation,
}: {
  snapshot: SettingsSnapshot['accounts'];
  mutation: SettingsMutationIO;
}) {
  return (
    <div className="stack settings-snapshot-page">
      <AccountPanel
        label="GitHub"
        icon={GitBranch}
        account={snapshot.github}
        mutation={mutation}
        prefix="github"
      />
      <AccountPanel
        label="Google · Gmail"
        icon={FileKey}
        account={snapshot.gmail}
        mutation={mutation}
        prefix="gmail"
      />
      <AccountPanel
        label="Google · Calendar"
        icon={CalendarDays}
        account={snapshot.calendar}
        mutation={mutation}
        prefix="calendar"
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
  return (
    <Section
      title="Utility Tools"
      description="Small saved tools for everyday tasks."
      icon={Wrench}
    >
      <div className="settings-summary-strip">
        <StateChip>
          {snapshot.items.filter((item) => item.enabled).length} enabled
        </StateChip>
        <StateChip>{snapshot.items.length} available</StateChip>
      </div>
      <ul className="settings-toggle-list">
        {snapshot.items.map((utility) => (
          <li key={utility.utility_id}>
            <div>
              <strong>{utility.label}</strong>
              <small>{utility.description}</small>
            </div>
            {utility.available && utility.enabled != null ? (
              <SwitchSetting
                mutation={mutation}
                field={`${utility.utility_id}.enabled`}
                label={`Enable ${utility.label}`}
                value={utility.enabled}
              />
            ) : (
              <StateChip warning>Unavailable</StateChip>
            )}
          </li>
        ))}
      </ul>
    </Section>
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
  return (
    <Section
      title="Embedding Engine"
      description="Local models stay private; cloud models send admitted text to the provider."
      icon={Bot}
    >
      <div className="settings-control-grid">
        <SelectSetting
          mutation={mutation}
          field="embedding.provider"
          label="Provider"
          value={embedding.provider}
          options={[
            { value: 'local', label: 'Local' },
            { value: 'cloud', label: 'Cloud provider' },
          ]}
        />
        <SelectSetting
          mutation={mutation}
          field="embedding.local_model"
          label="Local model"
          value={embedding.local_model}
          options={embedding.local_options}
        />
        <SelectSetting
          mutation={mutation}
          field="embedding.cloud_model"
          label="Cloud model"
          value={embedding.cloud_model}
          options={embedding.cloud_options}
        />
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
      <p className="settings-help">
        Cached readiness: {embedding.runtime_state}. Saving does not download a
        model or rebuild an index.
      </p>
    </Section>
  );
}

export function ToolConfigurationSnapshot({
  snapshot,
  mutation,
}: {
  snapshot: SettingsSnapshot['tools'];
  mutation: SettingsMutationIO;
}) {
  return (
    <div className="stack settings-snapshot-page">
      <Section
        title="Capability loading"
        description="Choose how enabled external capabilities are exposed to the model."
        icon={SlidersHorizontal}
      >
        <SelectSetting
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
        <ul className="settings-toggle-list">
          {snapshot.items.map((tool) => (
            <li key={tool.tool_id}>
              <div>
                <strong>{tool.label}</strong>
                <small>
                  {tool.configured_fields.length
                    ? `${tool.configured_fields.length} configured fields`
                    : 'No extra configuration'}
                </small>
              </div>
              {tool.enabled != null ? (
                <SwitchSetting
                  mutation={mutation}
                  field={`${tool.tool_id}.enabled`}
                  label={`Enable ${tool.label}`}
                  value={tool.enabled}
                />
              ) : (
                <StateChip warning>Unavailable</StateChip>
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
                  <div className="settings-summary-strip" key={credential.name}>
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
            </li>
          ))}
        </ul>
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
      <Section
        title="Assistant name"
        description="The name used throughout the local application."
        icon={Bot}
      >
        <TextSetting
          mutation={mutation}
          field="identity.name"
          label="Name"
          value={snapshot.identity.name}
          maxLength={80}
        />
      </Section>
      <Section
        title="Personality"
        description={`Optional behaviour guidance, up to ${snapshot.identity.personality_max_length} characters.`}
        icon={SlidersHorizontal}
      >
        <TextSetting
          mutation={mutation}
          field="identity.personality"
          label="Personality instructions"
          value={snapshot.identity.personality}
          maxLength={snapshot.identity.personality_max_length}
          multiline
        />
        <p className="settings-help">
          {snapshot.identity.personality.length} /{' '}
          {snapshot.identity.personality_max_length} · Preview: You are{' '}
          {snapshot.identity.name}, a knowledgeable personal assistant with
          access to tools.
        </p>
      </Section>
      <Section
        title="Self-Improvement"
        description="Allows the assistant to create and improve skills over time."
        icon={Wrench}
      >
        <SwitchSetting
          mutation={mutation}
          field="identity.self_improvement_enabled"
          label="Enable self-improvement"
          value={snapshot.identity.self_improvement_enabled}
        />
      </Section>
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
            { value: 'ask', label: 'Ask on launch' },
            { value: 'native', label: 'Native window' },
            { value: 'browser', label: 'Browser' },
          ]}
        />
      </Section>
      <Section
        title="Dream Cycle"
        description="Idle background cleanup for memory and sparse knowledge."
        icon={Moon}
      >
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
        <Facts>
          <Fact
            label="Last run"
            value={snapshot.dream_cycle.last_run || 'Never'}
          />
          <Fact
            label="Last summary"
            value={snapshot.dream_cycle.last_summary || 'No run summary saved'}
          />
        </Facts>
      </Section>
      <Section
        title="Updates"
        description="Cached release state; no update check runs when Settings opens."
        icon={RefreshCw}
      >
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
            value={snapshot.updates.current_version}
          />
          <Fact
            label="Last check"
            value={snapshot.updates.last_check || 'Never'}
          />
          <Fact
            label="Last success"
            value={snapshot.updates.last_success || 'Never'}
          />
          <Fact
            label="Skipped versions"
            value={snapshot.updates.skipped_versions.join(', ') || 'None'}
          />
        </Facts>
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
