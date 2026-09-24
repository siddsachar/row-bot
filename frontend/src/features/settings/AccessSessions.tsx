import { useEffect, useState } from 'react';
import {
  accessClient,
  type AccessClient,
  type AccessDevice,
} from '../../api/access';
import { clientError } from '../../api/errors';
import { Button, ErrorState, Skeleton } from '../../ui/primitives';

export default function AccessSessions({
  currentDeviceId,
  currentSessionId,
  client = accessClient,
}: {
  currentDeviceId?: string | null;
  currentSessionId?: string | null;
  client?: AccessClient;
}) {
  const [devices, setDevices] = useState<AccessDevice[] | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState('');
  const [reload, setReload] = useState(0);

  useEffect(() => {
    const abort = new AbortController();
    setError('');
    void client.devices(abort.signal).then(
      (value) => {
        if (!abort.signal.aborted) setDevices(value);
      },
      (cause) => {
        if (!abort.signal.aborted) setError(clientError(cause).message);
      },
    );
    return () => abort.abort();
  }, [client, reload]);

  async function perform(
    key: string,
    action: (signal: AbortSignal) => Promise<void>,
  ) {
    if (busy) return;
    const abort = new AbortController();
    setBusy(key);
    setError('');
    try {
      await action(abort.signal);
      setReload((value) => value + 1);
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy('');
    }
  }

  return (
    <section
      className="settings-snapshot-section stack"
      aria-labelledby="access-sessions-title"
    >
      <header className="settings-snapshot-heading">
        <div>
          <h3 id="access-sessions-title">Connected devices and sessions</h3>
          <p>
            Review and revoke paired owner sessions. Revocation also stops their
            active API and event authority.
          </p>
        </div>
      </header>
      {error && (
        <ErrorState
          title="Access sessions unavailable"
          action={
            <Button onClick={() => setReload((value) => value + 1)}>
              Retry
            </Button>
          }
        >
          {error}
        </ErrorState>
      )}
      {!devices && !error ? (
        <Skeleton label="Loading connected devices" />
      ) : null}
      {devices?.map((device) => (
        <article className="card stack" key={device.id}>
          <div>
            <strong>{device.display_name}</strong>{' '}
            {device.id === currentDeviceId ? (
              <small>(this device)</small>
            ) : null}
            <p className="muted">
              {device.revoked_at
                ? 'Revoked'
                : device.last_seen_at
                  ? `Last seen ${device.last_seen_at}`
                  : 'Not yet seen'}
            </p>
          </div>
          {device.sessions.map((session) => (
            <div className="button-row" key={session.id}>
              <span>
                {session.lifetime} session · expires {session.expires_at}
                {session.id === currentSessionId ? ' · current' : ''}
                {session.revoked_at ? ' · revoked' : ''}
              </span>
              {!session.revoked_at ? (
                <Button
                  variant="secondary"
                  disabled={Boolean(busy)}
                  onClick={() =>
                    void perform(`session:${session.id}`, (signal) =>
                      client.revokeSession(session.id, signal),
                    )
                  }
                >
                  {busy === `session:${session.id}`
                    ? 'Revoking…'
                    : 'Revoke session'}
                </Button>
              ) : null}
            </div>
          ))}
          {!device.revoked_at ? (
            <div className="button-row">
              <Button
                variant="secondary"
                disabled={Boolean(busy)}
                onClick={() =>
                  void perform(`device:${device.id}`, (signal) =>
                    client.revokeDevice(device.id, signal),
                  )
                }
              >
                {busy === `device:${device.id}` ? 'Revoking…' : 'Revoke device'}
              </Button>
            </div>
          ) : null}
        </article>
      ))}
      {currentSessionId ? (
        <div className="button-row">
          <Button
            variant="secondary"
            disabled={Boolean(busy)}
            onClick={() =>
              void perform('refresh', async (signal) => {
                await client.refresh(signal);
              })
            }
          >
            {busy === 'refresh' ? 'Refreshing…' : 'Refresh this session'}
          </Button>
          <Button
            variant="secondary"
            disabled={Boolean(busy)}
            onClick={() =>
              void perform('logout', async (signal) => {
                await client.logout(signal);
                location.assign('/connect?next=/app-v2/');
              })
            }
          >
            {busy === 'logout' ? 'Signing out…' : 'Sign out this device'}
          </Button>
        </div>
      ) : null}
    </section>
  );
}
