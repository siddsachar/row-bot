import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from 'react';
import { Link } from 'react-router-dom';
import { Building2, Cpu, Globe, LogOut, RefreshCw, Unplug } from 'lucide-react';
import type { ClientController } from '../../api/controller';
import type {
  CompanyAction,
  CompanyJoinRequest,
  CompanyView,
} from '../../api/types';
import { clientError } from '../../api/errors';
import { useOverlay } from '../../ui/overlays';
import {
  Button,
  Disclosure,
  ErrorState,
  Field,
  IconButton,
  Input,
  Skeleton,
} from '../../ui/primitives';
import { SettingsGroup, SettingsItem, StatusLine } from '../settings/anatomy';
import {
  JOIN_LINE,
  auditFields,
  auditWords,
  elsewhereWords,
  failureWords,
  joinedWords,
  leaveWords,
  lockdownWords,
  modelsWords,
  noticeWords,
  personWords,
} from './words';

/** How often the state is read while the browser sign-in is open. */
const WAITING_MS = 1000;

/**
 * Settings › Company (Row-Bot for Teams, C1b): join this Row-Bot to a
 * company's server, see who and which company, sign in again, leave.
 * Opening the page reads the state, which contacts nothing; on a joined
 * Row-Bot it then makes one light check that the company still accepts it.
 */
export default function CompanySettings({
  controller,
}: {
  controller: ClientController;
}) {
  const overlay = useOverlay();
  const [view, setView] = useState<CompanyView | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [address, setAddress] = useState('');
  const [deviceName, setDeviceName] = useState<string | null>(null);
  const stopped = useRef(false);

  const show = useCallback((next: CompanyView) => {
    if (!stopped.current) setView(next);
  }, []);

  const load = useCallback(async () => {
    setLoadError(null);
    try {
      const first = await controller.company('status');
      show(first);
      if (first.state === 'joined' || first.state === 'unreachable')
        show(await controller.company('check'));
    } catch (cause) {
      if (!stopped.current) setLoadError(clientError(cause).message);
    }
  }, [controller, show]);

  useEffect(() => {
    stopped.current = false;
    void load();
    return () => {
      stopped.current = true;
    };
  }, [load]);

  // While the browser sign-in is open, read the state again every second.
  useEffect(() => {
    if (view?.state !== 'waiting') return;
    const timer = window.setTimeout(async () => {
      try {
        show(await controller.company('status'));
      } catch {
        show({ ...view }); // Best effort: read again shortly.
      }
    }, WAITING_MS);
    return () => window.clearTimeout(timer);
  }, [controller, show, view]);

  async function act(action: CompanyAction, body?: CompanyJoinRequest) {
    setBusy(true);
    setError(null);
    try {
      show(await controller.company(action, body));
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy(false);
    }
  }

  if (!view)
    return loadError ? (
      <ErrorState
        title="Company settings didn’t load"
        action={<Button onClick={() => void load()}>Try again</Button>}
      >
        {loadError}
      </ErrorState>
    ) : (
      <Skeleton label="Loading company" />
    );

  const company = view.company ?? 'your company';
  const here = view.on_this_computer;
  const problem =
    error ?? (view.failure && failureWords(view.failure, company));

  function confirmLeave() {
    overlay.open({
      kind: 'alert',
      title: `Leave ${company}?`,
      description: leaveWords(company),
      confirmLabel: 'Leave',
      cancelLabel: 'Stay joined',
      onConfirm: () => void act('leave'),
    });
  }

  const lockedDown = view.policy_state === 'locked_down';
  // Every setting the company's policy sets, in plain words (C3a); while
  // locked down, the safe settings in force.
  const ruleList = view.rules ?? [];
  const rules = ruleList.length ? (
    <Disclosure
      summary="Your company’s rules"
      meta={lockedDown ? 'Locked down' : undefined}
    >
      {ruleList.map((rule) => (
        <SettingsItem
          key={rule.setting}
          label={rule.title}
          help={rule.help}
          bind={false}
          status={<StatusLine>{rule.value}</StatusLine>}
        />
      ))}
    </Disclosure>
  ) : null;

  // What Row-Bot tells the company (C4), and behind it each field in plain words.
  const told = (
    <Disclosure summary={auditWords(company)}>
      {auditFields(company).map((field) => (
        <SettingsItem
          key={field.label}
          label={field.label}
          help={field.help}
          bind={false}
        />
      ))}
    </Disclosure>
  );

  const leaveButton = (
    <IconButton
      label={`Leave ${company}`}
      disabled={busy}
      onClick={confirmLeave}
    >
      <LogOut size={16} aria-hidden />
    </IconButton>
  );

  if (view.state === 'waiting')
    return (
      <SettingsGroup label="Company">
        <SettingsItem
          label="Finish signing in in your browser"
          help={
            view.rejoining
              ? `Your browser opened ${company}’s sign-in. Press Connect there. If it says the link can’t connect Row-Bot, cancel here and sign in again.`
              : `Your browser opened ${company}’s sign-in. Press Connect there.`
          }
          icon={<Globe size={16} aria-hidden />}
          tone="accent"
          bind={false}
          status={
            <StatusLine tone="accent" pulse>
              Waiting for your browser
            </StatusLine>
          }
          control={
            <Button disabled={busy} onClick={() => void act('cancel')}>
              Cancel
            </Button>
          }
        />
      </SettingsGroup>
    );

  if (view.state === 'joined' || view.state === 'unreachable')
    return (
      <SettingsGroup label="Company">
        <SettingsItem
          label={company}
          help={personWords(view)}
          icon={<Building2 size={16} aria-hidden />}
          tone="accent"
          bind={false}
          status={
            lockedDown ? (
              <StatusLine>{lockdownWords(view)}</StatusLine>
            ) : view.state === 'joined' ? (
              <StatusLine tone="success">
                {joinedWords(view.joined_at)}
              </StatusLine>
            ) : (
              <StatusLine tone="warning">
                {view.failure === 'busy'
                  ? `${company} is busy. Row-Bot stays joined; try again in a minute.`
                  : view.failure
                    ? failureWords(view.failure, company)
                    : `Can’t reach ${company} right now. Row-Bot stays joined.`}
              </StatusLine>
            )
          }
          control={
            <>
              {(view.state === 'unreachable' || lockedDown) && (
                <IconButton
                  label={lockedDown ? 'Retry' : 'Try again'}
                  disabled={busy}
                  onClick={() => void act('check')}
                >
                  <RefreshCw size={16} aria-hidden />
                </IconButton>
              )}
              {leaveButton}
            </>
          }
        >
          {error && <StatusLine tone="danger">{error}</StatusLine>}
        </SettingsItem>
        {view.model_count != null && (
          <SettingsItem
            label={modelsWords(view.model_count, company)}
            icon={<Cpu size={16} aria-hidden />}
            tone="neutral"
            bind={false}
            control={
              <Link
                className="button ghost small"
                to="/settings/models#company-models"
                aria-label={`See ${company}’s models in Models`}
              >
                Models
              </Link>
            }
          />
        )}
        {rules}
        {told}
      </SettingsGroup>
    );

  if (view.state === 'disconnected')
    return (
      <SettingsGroup label="Company">
        <SettingsItem
          label="Your company disconnected this Row-Bot"
          help={`${company} no longer accepts this Row-Bot. That happens when an admin removes it, your account is paused, or it’s been a month since you last signed in. Your own chats and memory are still here.`}
          icon={<Unplug size={16} aria-hidden />}
          tone="neutral"
          bind={false}
          status={
            problem ? (
              <StatusLine tone="danger">{problem}</StatusLine>
            ) : !here ? (
              <StatusLine>{elsewhereWords(view.computer_name)}</StatusLine>
            ) : undefined
          }
          control={
            <>
              <Button
                variant="primary"
                disabled={busy || !here}
                onClick={() => void act('sign_in_again')}
              >
                Sign in again
              </Button>
              {leaveButton}
            </>
          }
        />
        {rules}
      </SettingsGroup>
    );

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!address.trim() || !here) return;
    void act('join', {
      address: address.trim(),
      device_name: (deviceName ?? view.computer_name).trim(),
    });
  };
  const note = problem ? (
    <StatusLine tone="danger">{problem}</StatusLine>
  ) : view.notice ? (
    <StatusLine>{noticeWords(view.notice, company)}</StatusLine>
  ) : !here ? (
    <StatusLine>{elsewhereWords(view.computer_name)}</StatusLine>
  ) : undefined;
  return (
    <form onSubmit={submit} noValidate>
      <SettingsGroup label="Company">
        <SettingsItem
          label="Your company’s Row-Bot server"
          help={JOIN_LINE}
          layout="stacked"
          status={note}
          control={
            <Input
              value={address}
              onChange={(event) => setAddress(event.target.value)}
              placeholder="rowbot.yourcompany.com"
              autoComplete="url"
              inputMode="url"
              spellCheck={false}
              disabled={!here}
            />
          }
          trailing={
            <Button
              type="submit"
              variant="primary"
              disabled={busy || !here || !address.trim()}
            >
              Join
            </Button>
          }
        >
          {here && (
            <Disclosure
              summary="This computer’s name"
              meta={deviceName ?? view.computer_name}
            >
              <Field
                label="Name your company sees"
                hint="Shown to your company’s admins next to this Row-Bot."
              >
                <Input
                  value={deviceName ?? view.computer_name}
                  maxLength={200}
                  onChange={(event) => setDeviceName(event.target.value)}
                />
              </Field>
            </Disclosure>
          )}
        </SettingsItem>
      </SettingsGroup>
    </form>
  );
}
