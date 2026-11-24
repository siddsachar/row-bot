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
import CompanyShares from './CompanyShares';
import {
  JOIN_LINE,
  PIN_INVALID,
  auditFields,
  auditWords,
  disconnectedWords,
  elsewhereWords,
  failureWords,
  hostOf,
  joinedWords,
  leaveWords,
  lockdownWords,
  modelsWords,
  movedWords,
  noticeWords,
  personWords,
  pinnedWords,
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
  // The company server this computer's IT department set (C5): Join goes
  // only there and Leave isn't offered, unless Row-Bot is joined to another
  // server (IT moved it), which it may then leave.
  const pinned = view.pinned_server ?? null;
  const moved =
    pinned !== null &&
    view.origin !== null &&
    view.origin !== pinned &&
    (view.state === 'joined' ||
      view.state === 'unreachable' ||
      view.state === 'disconnected');
  const canLeave = pinned === null || moved;

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

  const leaveButton = canLeave ? (
    <IconButton
      label={`Leave ${company}`}
      disabled={busy}
      onClick={confirmLeave}
    >
      <LogOut size={16} aria-hidden />
    </IconButton>
  ) : null;

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

  if (moved && pinned !== null) {
    const target = hostOf(pinned);
    return (
      <SettingsGroup label="Company">
        <SettingsItem
          label={`Your IT department moved Row-Bot to ${target}`}
          help={movedWords(target, company)}
          icon={<Building2 size={16} aria-hidden />}
          tone="accent"
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
                onClick={() =>
                  void act('join', {
                    address: pinned,
                    device_name: view.computer_name,
                  })
                }
              >
                Sign in to {target}
              </Button>
              {leaveButton}
            </>
          }
        />
      </SettingsGroup>
    );
  }

  if (view.state === 'joined' || view.state === 'unreachable')
    return (
      <>
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
        <CompanyShares
          controller={controller}
          lock={view.locked?.find(
            (lock) => lock.setting === 'data.share_to_company',
          )}
        />
      </>
    );

  if (view.state === 'disconnected')
    return (
      <SettingsGroup label="Company">
        <SettingsItem
          label="Your company disconnected this Row-Bot"
          help={disconnectedWords(company)}
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
    const target = pinned ?? address.trim();
    if (!target || !here) return;
    void act('join', {
      address: target,
      device_name: (deviceName ?? view.computer_name).trim(),
    });
  };
  const note = problem ? (
    <StatusLine tone="danger">{problem}</StatusLine>
  ) : view.notice ? (
    <StatusLine>{noticeWords(view.notice, company)}</StatusLine>
  ) : view.pin_invalid ? (
    <StatusLine tone="warning">{PIN_INVALID}</StatusLine>
  ) : !here ? (
    <StatusLine>{elsewhereWords(view.computer_name)}</StatusLine>
  ) : undefined;
  const computer = here && (
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
  );
  // IT set the company server: its name and Join, with no address to type.
  if (pinned !== null)
    return (
      <form onSubmit={submit} noValidate>
        <SettingsGroup label="Company">
          <SettingsItem
            label={hostOf(pinned)}
            help={pinnedWords(hostOf(pinned))}
            icon={<Building2 size={16} aria-hidden />}
            tone="accent"
            bind={false}
            status={note}
            control={
              <Button type="submit" variant="primary" disabled={busy || !here}>
                Join
              </Button>
            }
          >
            {computer}
          </SettingsItem>
        </SettingsGroup>
      </form>
    );
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
          {computer}
        </SettingsItem>
      </SettingsGroup>
    </form>
  );
}
