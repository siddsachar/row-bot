import { useEffect, useId, useState } from 'react';
import { Laptop, LogOut, Smartphone } from 'lucide-react';
import {
  accessClient,
  type AccessClient,
  type AccessDevice,
} from '../../api/access';
import { clientError } from '../../api/errors';
import { useOverlay } from '../../ui/overlays';
import {
  Button,
  EntityList,
  EntityRow,
  ErrorState,
  InlineEmpty,
  Input,
  Skeleton,
} from '../../ui/primitives';
import { When } from '../../ui/When';

function ipv4(address: string): number[] | null {
  const parts = address.split('.').map(Number);
  return parts.length === 4 && parts.every((part) => part >= 0 && part < 256)
    ? parts
    : null;
}

/** Where a device was last seen from, in words (parity row 48). */
export function addressInWords(address: string | null | undefined): string {
  if (!address) return '';
  const lower = address.toLowerCase();
  const v4 = ipv4(address);
  if (lower === '::1' || (v4 && v4[0] === 127)) return 'via this computer';
  if (
    (v4 && v4[0] === 100 && v4[1] >= 64 && v4[1] <= 127) ||
    lower.startsWith('fd7a:115c:a1e0:')
  )
    return `over Tailscale (${address})`;
  if (
    (v4 &&
      (v4[0] === 10 ||
        (v4[0] === 172 && v4[1] >= 16 && v4[1] <= 31) ||
        (v4[0] === 192 && v4[1] === 168))) ||
    /^f[cd]|^fe80:/.test(lower)
  )
    return `on your network (${address})`;
  return `from the internet (${address})`;
}

function isPhone(device: AccessDevice) {
  return /iPhone|iPad|iPod|Android|Mobile/.test(device.user_agent ?? '');
}

/**
 * Settings › Devices & remote access › Your devices (U59, B141, parity row
 * 48). Each phone or computer signed in by invitation: its name, "This
 * device" for the one making the request (the server marks it), when and
 * where it was last seen in words, Sign out after a confirmation, and Rename.
 * Signing out ends every session of that device at once; it needs a new code
 * to come back. Signed-out devices are not listed.
 */
export default function AccessDevices({
  client = accessClient,
  reloadKey = 0,
}: {
  client?: Pick<AccessClient, 'devices' | 'revokeDevice' | 'rename'>;
  /** Changes when a device connected elsewhere on the page. */
  reloadKey?: number;
}) {
  const overlay = useOverlay();
  const nameId = useId();
  const [devices, setDevices] = useState<AccessDevice[] | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState('');
  const [renaming, setRenaming] = useState<AccessDevice | null>(null);
  const [name, setName] = useState('');
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
  }, [client, reload, reloadKey]);

  async function perform(key: string, action: () => Promise<void>) {
    if (busy) return;
    setBusy(key);
    setError('');
    try {
      await action();
      setReload((value) => value + 1);
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy('');
    }
  }

  function signOut(device: AccessDevice, opener: HTMLElement) {
    overlay.open({
      kind: 'alert',
      returnFocusTo: opener,
      title: `Sign out ${device.display_name}?`,
      description: device.current
        ? 'This device signs out now. To use Row-Bot here again, connect it with a new code from the computer running Row-Bot.'
        : 'It can’t open Row-Bot again until you connect it with a new code.',
      confirmLabel: 'Sign out',
      onConfirm: () => {
        overlay.close();
        void perform(`sign-out:${device.id}`, async () => {
          await client.revokeDevice(device.id);
          if (device.current) location.assign('/connect');
        });
      },
    });
  }

  const listed = devices?.filter((device) => !device.revoked_at) ?? [];
  return (
    <section
      className="settings-section-flat access-devices"
      aria-labelledby="access-devices-title"
      data-setting-anchor="devices"
    >
      <header className="settings-section-head">
        <span className="settings-section-icon" aria-hidden>
          <Laptop size={16} aria-hidden />
        </span>
        <div className="settings-section-text">
          <h3 id="access-devices-title">Your devices</h3>
          <p>
            Phones and computers signed in to Row-Bot. Sign one out to cut it
            off at once.
          </p>
        </div>
      </header>
      {error && (
        <ErrorState
          title="Devices unavailable"
          action={
            <Button onClick={() => setReload((value) => value + 1)}>
              Retry
            </Button>
          }
        >
          {error}
        </ErrorState>
      )}
      {!devices && !error ? <Skeleton label="Loading devices" /> : null}
      {devices && listed.length === 0 && (
        <InlineEmpty>No devices yet. Connect one above.</InlineEmpty>
      )}
      {listed.length > 0 && (
        <EntityList label="Your devices">
          {listed.map((device) => {
            const where = addressInWords(device.last_address);
            return (
              <EntityRow
                key={device.id}
                title={device.display_name}
                icon={
                  isPhone(device) ? (
                    <Smartphone size={16} aria-hidden />
                  ) : (
                    <Laptop size={16} aria-hidden />
                  )
                }
                status={
                  device.current
                    ? { tone: 'accent', label: 'This device' }
                    : undefined
                }
                meta={
                  device.last_seen_at ? (
                    <>
                      Last seen <When value={device.last_seen_at} />
                      {where ? ` · ${where}` : ''}
                    </>
                  ) : (
                    'Not seen yet'
                  )
                }
                action={
                  <Button
                    variant="secondary"
                    disabled={Boolean(busy)}
                    aria-label={`Sign out ${device.display_name}`}
                    onClick={(event) => signOut(device, event.currentTarget)}
                  >
                    <LogOut size={16} aria-hidden />
                    {busy === `sign-out:${device.id}`
                      ? 'Signing out…'
                      : 'Sign out'}
                  </Button>
                }
                menu={[
                  {
                    label: 'Rename',
                    onSelect: () => {
                      setRenaming(device);
                      setName(device.display_name);
                    },
                    // The menu's focus trap would pull focus back (B74).
                    afterClose: () => document.getElementById(nameId)?.focus(),
                  },
                ]}
              />
            );
          })}
        </EntityList>
      )}
      {renaming && (
        <form
          className="access-rename"
          onSubmit={(event) => {
            event.preventDefault();
            const target = renaming;
            void perform(`rename:${target.id}`, async () => {
              await client.rename(target.id, name.trim());
              setRenaming(null);
            });
          }}
        >
          <label htmlFor={nameId}>Rename {renaming.display_name}</label>
          <Input
            id={nameId}
            value={name}
            maxLength={80}
            onChange={(event) => setName(event.target.value)}
          />
          <Button
            type="submit"
            variant="primary"
            disabled={Boolean(busy) || !name.trim()}
          >
            Save
          </Button>
          <Button variant="ghost" onClick={() => setRenaming(null)}>
            Cancel
          </Button>
        </form>
      )}
    </section>
  );
}
