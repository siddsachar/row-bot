import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import type {
  InstallCountDeletion,
  InstallCountSnapshot,
} from '../../api/types';
import { clientError } from '../../api/errors';
import { useRuntime } from '../../runtime';
import { Button, Disclosure, Toggle } from '../../ui/primitives';
import { useNotify, useOverlay } from '../../ui/overlays';
import { SettingsGroup, SettingsItem, StatusLine } from './anatomy';

const SYSTEMS: Record<InstallCountSnapshot['os'], string> = {
  windows: 'Windows',
  macos: 'macOS',
  linux: 'Linux',
  docker: 'Docker',
};

/**
 * Install stats (N6): what is sent, when, where, for how long and how to turn
 * it off, under Settings › System › Privacy › Details.
 */
export function InstallCountFacts({
  snapshot,
}: {
  snapshot: InstallCountSnapshot;
}) {
  return (
    <dl className="install-count-facts">
      <div>
        <dt>What is sent</dt>
        <dd>
          A random ID made just for this, the Row-Bot version (
          {snapshot.version}) and the system ({SYSTEMS[snapshot.os]}). Nothing
          else: no name, account, location, conversations or files, and nothing
          about what you do in Row-Bot.
        </dd>
      </div>
      <div>
        <dt>When</dt>
        <dd>Once a day while Row-Bot is running.</dd>
      </div>
      <div>
        <dt>Where</dt>
        <dd>
          Row-Bot’s stats service on Cloudflare:{' '}
          <span className="install-count-service">{snapshot.service}</span>.
        </dd>
      </div>
      <div>
        <dt>How long it is kept</dt>
        <dd>
          Each day’s record is deleted after 35 days. Only totals without IDs
          are kept.
        </dd>
      </div>
      <div>
        <dt>Your IP address</dt>
        <dd>
          Cloudflare sees it, as it does for any connection. Row-Bot doesn’t
          store it; the stats service only adds one to its country’s count for
          the day, never next to the ID.
        </dd>
      </div>
      <div>
        <dt>Turning it off</dt>
        <dd>
          The switch above, at any time, or ROW_BOT_INSTALL_COUNT=0 on a server.
        </dd>
      </div>
    </dl>
  );
}

function sentOn(day: string) {
  const date = new Date(`${day}T12:00:00Z`);
  if (date.toDateString() === new Date().toDateString())
    return 'last sent today';
  return `last sent on ${date.toLocaleDateString(undefined, { day: 'numeric', month: 'short' })}`;
}

export type PrivacyIo = {
  load: (signal: AbortSignal) => Promise<InstallCountSnapshot>;
  choose: (enabled: boolean) => Promise<InstallCountSnapshot>;
  remove: () => Promise<InstallCountDeletion>;
};

/**
 * Settings › System › Privacy: one row for install stats (the switch, its
 * status and its details, with "Delete what was sent…" inside them), then what
 * else Row-Bot contacts, closed until asked for.
 */
export function Privacy({ io }: { io: PrivacyIo }) {
  const overlay = useOverlay();
  const notify = useNotify();
  const [snapshot, setSnapshot] = useState<InstallCountSnapshot | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    const abort = new AbortController();
    io.load(abort.signal).then(setSnapshot, (cause: unknown) => {
      if (!abort.signal.aborted) setError(clientError(cause).message);
    });
    return () => abort.abort();
  }, [io]);

  async function choose(enabled: boolean) {
    setBusy(true);
    setError('');
    try {
      setSnapshot(await io.choose(enabled));
      notify(enabled ? 'Install stats turned on' : 'Install stats turned off');
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    setBusy(true);
    setError('');
    try {
      const result = await io.remove();
      setSnapshot(result.snapshot);
      if (result.outcome === 'deleted')
        notify('Deleted what was sent. Install stats are off.');
      else
        setError(
          'Install stats are off, but Row-Bot couldn’t reach its stats service to delete what was sent. Try again later.',
        );
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy(false);
    }
  }

  function confirmDelete(opener: HTMLElement) {
    overlay.open({
      kind: 'alert',
      returnFocusTo: opener,
      title: 'Delete what was sent?',
      description:
        'Row-Bot asks its stats service to delete everything it holds for this installation, and turns install stats off. Daily totals without IDs stay.',
      confirmLabel: 'Delete',
      onConfirm: () => {
        overlay.close();
        void remove();
      },
    });
  }

  const shown =
    snapshot !== null && (snapshot.available || snapshot.turned_off_here);
  return (
    <SettingsGroup title="Privacy" anchor="privacy">
      {snapshot && shown && (
        <SettingsItem
          anchor="install-stats"
          label="Install stats"
          help="A random ID, the version and the system, once a day."
          layout="inline"
          status={
            snapshot.turned_off_here ? (
              <StatusLine tone="neutral">
                Off on this server (ROW_BOT_INSTALL_COUNT=0)
              </StatusLine>
            ) : snapshot.enabled ? (
              <StatusLine
                tone="success"
                more={
                  snapshot.last_sent_on ? [sentOn(snapshot.last_sent_on)] : []
                }
              >
                On
              </StatusLine>
            ) : (
              <StatusLine tone="neutral">Off</StatusLine>
            )
          }
          control={
            snapshot.turned_off_here ? undefined : (
              <Toggle
                label="Share install stats"
                checked={snapshot.enabled}
                disabled={busy}
                onChange={(event) => void choose(event.currentTarget.checked)}
              />
            )
          }
        >
          <Disclosure summary="Details" className="install-count-details">
            <InstallCountFacts snapshot={snapshot} />
            {snapshot.can_delete && (
              <Button
                className="small install-count-delete"
                disabled={busy}
                onClick={(event) => confirmDelete(event.currentTarget)}
              >
                Delete what was sent…
              </Button>
            )}
          </Disclosure>
        </SettingsItem>
      )}
      {error && (
        <p className="settings-help" role="alert">
          {error}
        </p>
      )}
      <div className="privacy-contacts-row" data-setting-anchor="contacts">
        <Disclosure
          summary="What else Row-Bot contacts"
          className="settings-group-disclosure settings-divided"
        >
          <ul className="privacy-contacts">
            <li>
              <strong>Update checks</strong> GitHub, about once a day, for new
              versions (and their download, unless you turn that off) ·{' '}
              <Link to="/settings/updates">Settings › Updates</Link>
            </li>
            <li>
              <strong>Connection checks</strong> every hour: the internet
              (1.1.1.1), Ollama and your accounts ·{' '}
              <Link to="/?tab=monitor">Monitor</Link>
            </li>
            <li>
              <strong>Catalogs</strong> model lists (with OpenRouter’s public
              list and models.dev’s, at most hourly) and Apps & Skills, when you
              connect, refresh or update ·{' '}
              <Link to="/settings/apps?view=advanced#catalogs">
                Apps › Advanced
              </Link>
            </li>
          </ul>
          <p className="settings-help">
            The models, tools, apps and channels you use contact their own
            services.
          </p>
        </Disclosure>
      </div>
    </SettingsGroup>
  );
}

export default function ConnectedPrivacy() {
  const { controller } = useRuntime();
  const io = useRef<PrivacyIo>({
    load: (signal) => controller.installCount(signal),
    choose: (enabled) => controller.chooseInstallCount(enabled),
    remove: () => controller.deleteCountedData(),
  });
  return <Privacy io={io.current} />;
}
