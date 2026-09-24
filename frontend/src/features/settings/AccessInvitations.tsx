import { useEffect, useState } from 'react';
import { ClipboardCopy, Link2, Plus, RefreshCw, X } from 'lucide-react';
import {
  accessInvitationClient,
  type AccessInvitation,
  type AccessInvitationClient,
  type AccessRoute,
  type AccessRouteSettings,
} from '../../api/access';
import { clientError } from '../../api/errors';
import type { ClientPlatform } from '../../platform';
import { writeClipboardText } from '../../platform/clipboard';
import { Button, ErrorState, Skeleton, Toggle } from '../../ui/primitives';

export default function AccessInvitations({
  client = accessInvitationClient,
  writeClipboard,
}: {
  client?: AccessInvitationClient;
  writeClipboard?: ClientPlatform['writeClipboard'];
}) {
  const [routes, setRoutes] = useState<AccessRoute[] | null>(null);
  const [settings, setSettings] = useState<AccessRouteSettings | null>(null);
  const [newOrigin, setNewOrigin] = useState('');
  const [routeNotice, setRouteNotice] = useState('');
  const [invitations, setInvitations] = useState<AccessInvitation[] | null>(
    null,
  );
  const [selected, setSelected] = useState('');
  const [lifetime, setLifetime] = useState<'trusted' | 'temporary'>('trusted');
  const [issuedUrl, setIssuedUrl] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);

  useEffect(() => {
    const abort = new AbortController();
    setError('');
    void Promise.all([
      client.routes(abort.signal),
      client.routeSettings(abort.signal),
      client.invitations(abort.signal),
    ]).then(
      ([newRoutes, newSettings, newInvitations]) => {
        if (abort.signal.aborted) return;
        setRoutes(newRoutes);
        setSettings(newSettings);
        setInvitations(newInvitations);
        setSelected((old) =>
          newRoutes.some(
            (route) => route.id === old && route.available && route.eligible,
          )
            ? old
            : (newRoutes.find((route) => route.available && route.eligible)
                ?.id ?? ''),
        );
      },
      (cause) => {
        if (!abort.signal.aborted) setError(clientError(cause).message);
      },
    );
    return () => abort.abort();
  }, [client, reload]);

  async function perform(action: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    setError('');
    try {
      await action();
      setReload((value) => value + 1);
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy(false);
    }
  }

  const active =
    invitations?.filter(
      (invitation) =>
        !invitation.claimed_at &&
        !invitation.cancelled_at &&
        Date.parse(invitation.expires_at) > Date.now(),
    ) ?? [];
  return (
    <section
      className="settings-snapshot-section stack"
      aria-labelledby="access-invitations-title"
    >
      <header className="settings-snapshot-heading">
        <div>
          <h3 id="access-invitations-title">Connect another device</h3>
          <p>
            Create a one-time link for a route this server currently offers. The
            receiving browser gets full owner access.
          </p>
        </div>
        <Button
          iconOnly
          aria-label="Refresh connection routes"
          title="Refresh connection routes"
          onClick={() => setReload((value) => value + 1)}
          disabled={busy}
        >
          <RefreshCw size={18} aria-hidden="true" />
        </Button>
      </header>
      {error && (
        <ErrorState
          title="Connection links unavailable"
          action={
            <Button onClick={() => setReload((value) => value + 1)}>
              Retry
            </Button>
          }
        >
          {error}
        </ErrorState>
      )}
      {!routes && !error ? (
        <Skeleton label="Loading connection routes" />
      ) : null}
      {routes && (
        <>
          {settings?.can_manage_routes && (
            <div className="stack">
              <label className="button-row">
                <span>Allow local network connections</span>
                <Toggle
                  label="Allow local network connections"
                  checked={settings.listen_mode === 'local_network'}
                  disabled={busy}
                  onChange={(event) =>
                    void perform(async () => {
                      const result = await client.setListenMode(
                        settings.listen_mode,
                        event.target.checked ? 'local_network' : 'local_only',
                      );
                      setRouteNotice(
                        result.restart_required
                          ? 'Saved. Restart Row-Bot to apply the listen change.'
                          : 'Listen mode saved.',
                      );
                    })
                  }
                />
              </label>
              {!settings.managed_externally && (
                <>
                  <label htmlFor="access-trusted-origin">
                    Additional trusted address
                  </label>
                  <div className="button-row">
                    <input
                      id="access-trusted-origin"
                      type="url"
                      value={newOrigin}
                      disabled={busy}
                      placeholder="https://row-bot.example.com"
                      onChange={(event) => setNewOrigin(event.target.value)}
                    />
                    <Button
                      iconOnly
                      aria-label="Add trusted address"
                      title="Add trusted address"
                      disabled={!newOrigin.trim() || busy}
                      onClick={() =>
                        void perform(async () => {
                          await client.changeOrigin(
                            'add',
                            newOrigin,
                            settings.configured_origins,
                          );
                          setNewOrigin('');
                          setRouteNotice(
                            'Trusted address saved. Check DNS, TLS, and reachability separately.',
                          );
                        })
                      }
                    >
                      <Plus size={18} aria-hidden="true" />
                    </Button>
                  </div>
                  {settings.configured_origins.map((origin) => (
                    <div className="button-row" key={origin}>
                      <span>{origin}</span>
                      <Button
                        iconOnly
                        variant="ghost"
                        aria-label={`Remove trusted address ${origin}`}
                        title="Remove trusted address"
                        disabled={busy}
                        onClick={() =>
                          void perform(() =>
                            client.changeOrigin(
                              'remove',
                              origin,
                              settings.configured_origins,
                            ),
                          )
                        }
                      >
                        <X size={18} aria-hidden="true" />
                      </Button>
                    </div>
                  ))}
                </>
              )}
              {settings.managed_externally && (
                <p className="muted">
                  Trusted addresses are managed by deployment configuration.
                </p>
              )}
            </div>
          )}
          {routeNotice && <p role="status">{routeNotice}</p>}
          <label htmlFor="access-route">Connection route</label>
          <select
            id="access-route"
            value={selected}
            onChange={(event) => setSelected(event.target.value)}
            disabled={busy}
          >
            {!selected && <option value="">Choose a route</option>}
            {routes
              .filter((route) => route.available && route.eligible)
              .map((route) => (
                <option value={route.id} key={route.id}>
                  {route.label}
                </option>
              ))}
          </select>
          {selected && (
            <p className="muted">
              {routes.find((route) => route.id === selected)?.detail}
            </p>
          )}
          {selected &&
            routes.find((route) => route.id === selected)?.warning && (
              <p role="note">
                {routes.find((route) => route.id === selected)?.warning}
              </p>
            )}
          <label htmlFor="access-lifetime">Session duration</label>
          <select
            id="access-lifetime"
            value={lifetime}
            onChange={(event) =>
              setLifetime(event.target.value as 'trusted' | 'temporary')
            }
            disabled={busy}
          >
            <option value="trusted">Trusted · 30 days</option>
            <option value="temporary">Temporary · 12 hours</option>
          </select>
          <div className="button-row">
            <Button
              disabled={!selected || busy}
              onClick={() =>
                void perform(async () => {
                  setIssuedUrl('');
                  const result = await client.create(selected, lifetime);
                  setIssuedUrl(result.url);
                })
              }
            >
              <Link2 size={18} aria-hidden="true" /> Create connection link
            </Button>
          </div>
        </>
      )}
      {issuedUrl && (
        <div className="card stack">
          <label htmlFor="access-issued-url">
            One-time link · expires in 10 minutes
          </label>
          <input
            id="access-issued-url"
            value={issuedUrl}
            readOnly
            onFocus={(event) => event.currentTarget.select()}
          />
          <div className="button-row">
            <Button
              aria-label="Copy one-time link"
              onClick={() =>
                void writeClipboardText(issuedUrl, writeClipboard).then(
                  (copied) => {
                    if (!copied)
                      setError('Copy failed. Select and copy the link above.');
                  },
                )
              }
            >
              <ClipboardCopy size={18} aria-hidden="true" /> Copy link
            </Button>
            <Button variant="ghost" onClick={() => setIssuedUrl('')}>
              Hide link
            </Button>
          </div>
        </div>
      )}
      {active.length > 0 && (
        <div className="stack">
          <h4>Open invitations</h4>
          {active.map((invitation) => (
            <div className="button-row" key={invitation.id}>
              <span>
                {invitation.intended_origin} · {invitation.session_lifetime} ·
                expires {invitation.expires_at}
              </span>
              <Button
                iconOnly
                variant="ghost"
                aria-label={`Cancel invitation for ${invitation.intended_origin}`}
                title="Cancel invitation"
                disabled={busy}
                onClick={() =>
                  void perform(async () => {
                    await client.cancel(invitation.id);
                    setIssuedUrl('');
                  })
                }
              >
                <X size={18} aria-hidden="true" />
              </Button>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
