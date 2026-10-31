import { useEffect, useId, useState } from 'react';
import { Plus, X } from 'lucide-react';
import {
  accessInvitationClient,
  type AccessInvitationClient,
  type AccessRouteSettings,
} from '../../api/access';
import { clientError } from '../../api/errors';
import { usePlace, type PlaceWords } from '../../api/place';
import { useOverlay } from '../../ui/overlays';
import { Button, Field, Input, Select, Skeleton } from '../../ui/primitives';

const listenLabels = (
  place: PlaceWords,
): Record<AccessRouteSettings['listen_mode'], string> => ({
  local_only: `${place.label} only`,
  local_network: `${place.label} and my network`,
});

/**
 * Advanced › Network: where Row-Bot listens and the extra addresses it
 * accepts. One control per job, applied through the live access routes (the
 * listen change restarts Row-Bot, so it asks first). Only the owner on this
 * computer can change them; other devices read them.
 */
export default function AccessNetwork({
  client = accessInvitationClient,
}: {
  client?: AccessInvitationClient;
}) {
  const place = usePlace();
  const overlay = useOverlay();
  const addressId = useId();
  const [settings, setSettings] = useState<AccessRouteSettings | null>(null);
  const [address, setAddress] = useState('');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);

  useEffect(() => {
    const abort = new AbortController();
    void client.routeSettings(abort.signal).then(
      (value) => {
        if (!abort.signal.aborted) setSettings(value);
      },
      (cause) => {
        if (!abort.signal.aborted) setError(clientError(cause).message);
      },
    );
    return () => abort.abort();
  }, [client, reload]);

  async function perform(action: () => Promise<string | void>) {
    if (busy) return;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const message = await action();
      if (message) setNotice(message);
      setReload((value) => value + 1);
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy(false);
    }
  }

  if (!settings)
    return error ? (
      <p role="alert">{error}</p>
    ) : (
      <Skeleton label="Loading network settings" />
    );
  return (
    <div className="access-network stack" data-setting-anchor="remote-access">
      <Field
        label="Listen on"
        layout="row"
        hint="“My network” lets phones and computers on the same Wi-Fi reach Row-Bot; each still needs a code to sign in. Changing it restarts Row-Bot."
      >
        <Select
          value={settings.listen_mode}
          disabled={busy}
          onChange={(event) => {
            const next = event.target
              .value as AccessRouteSettings['listen_mode'];
            const previous = settings.listen_mode;
            if (next === previous) return;
            overlay.open({
              kind: 'alert',
              returnFocusTo: event.currentTarget,
              title:
                next === 'local_network'
                  ? 'Let devices on your network reach Row-Bot?'
                  : 'Stop listening on your network?',
              description:
                next === 'local_network'
                  ? 'Row-Bot restarts and listens on your network. Anyone there can reach its sign-in page; only a device with a code from this page gets in.'
                  : `Row-Bot restarts and listens ${place.on} only. Devices that reach it over your network can’t connect until you turn it back on.`,
              confirmLabel: 'Restart and apply',
              onConfirm: () => {
                overlay.close();
                void perform(async () => {
                  const result = await client.setListenMode(previous, next);
                  return result.restart_required
                    ? 'Saved. Restart Row-Bot to apply it.'
                    : 'Saved. Row-Bot is restarting to apply it.';
                });
              },
            });
          }}
        >
          {Object.entries(listenLabels(place)).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </Select>
      </Field>
      <div className="access-addresses">
        <p className="settings-help">
          <strong>Allowed addresses.</strong> Exact addresses that reach this
          computer through your own proxy or domain, such as
          https://row-bot.example.com. Row-Bot doesn’t check DNS, HTTPS or
          whether they are reachable.
        </p>
        {settings.configured_origins.length > 0 ? (
          <ul className="access-address-list" aria-label="Allowed addresses">
            {settings.configured_origins.map((origin) => (
              <li key={origin}>
                <span className="settings-break-word">{origin}</span>
                {!settings.managed_externally && (
                  <Button
                    iconOnly
                    variant="ghost"
                    aria-label={`Remove ${origin}`}
                    title="Remove"
                    disabled={busy}
                    onClick={(event) => {
                      const remove = () =>
                        void perform(() =>
                          client.changeOrigin(
                            'remove',
                            origin,
                            settings.configured_origins,
                          ),
                        );
                      // Any signed-in device may change these; one that uses
                      // this address would cut itself off, so it asks first.
                      if (origin !== window.location.origin) return remove();
                      overlay.open({
                        kind: 'alert',
                        returnFocusTo: event.currentTarget,
                        title: 'Remove the address this device uses?',
                        description: `This device reaches Row-Bot at ${origin}. Once it’s removed, this device can’t connect there until the address is added again.`,
                        confirmLabel: 'Remove',
                        onConfirm: () => {
                          overlay.close();
                          remove();
                        },
                      });
                    }}
                  >
                    <X size={16} aria-hidden />
                  </Button>
                )}
              </li>
            ))}
          </ul>
        ) : (
          <p className="settings-help">None.</p>
        )}
        {settings.managed_externally ? (
          <p className="settings-help">
            Set by this server’s deployment configuration.
          </p>
        ) : (
          <form
            className="access-address-add"
            onSubmit={(event) => {
              event.preventDefault();
              if (!address.trim()) return;
              void perform(async () => {
                await client.changeOrigin(
                  'add',
                  address.trim(),
                  settings.configured_origins,
                );
                setAddress('');
                return 'Address added.';
              });
            }}
          >
            <label className="visually-hidden" htmlFor={addressId}>
              Add an allowed address
            </label>
            <Input
              id={addressId}
              type="url"
              value={address}
              disabled={busy}
              placeholder="https://row-bot.example.com"
              onChange={(event) => setAddress(event.target.value)}
            />
            <Button type="submit" disabled={!address.trim() || busy}>
              <Plus size={16} aria-hidden /> Add
            </Button>
          </form>
        )}
      </div>
      {notice && <p role="status">{notice}</p>}
      {error && <p role="alert">{error}</p>}
    </div>
  );
}
