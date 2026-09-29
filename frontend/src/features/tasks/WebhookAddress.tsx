import { useEffect, useState } from 'react';
import { clientError } from '../../api/errors';
import { useOverlay } from '../../ui/overlays';
import { Button } from '../../ui/primitives';

export type WebhookReach = {
  /** The public tunnel address, while the app is reachable from the internet. */
  publicBase: string | null;
  /** Only the local owner on this computer can start or stop the tunnel. */
  canControl: boolean;
};

/** The webhook's private configuration, read only when something is copied. */
export type WebhookConfiguration = {
  /** `/api/webhook/<task>`, without the secret. */
  path: string;
  /** The older address form with `?secret=`, for services without headers. */
  pathWithSecret: string;
  /** The header that carries the secret (B132). */
  header: string;
  secret: string;
};

export const WEBHOOK_SECRET_HEADER = 'X-Row-Bot-Webhook-Secret';

export interface WebhookAddressProps {
  taskId: string;
  /** This app's own address, e.g. http://127.0.0.1:8080. */
  localBase: string;
  /** The webhook's address and secret; read only when copying. */
  readAddress: () => Promise<WebhookConfiguration>;
  writeClipboard: (text: string) => Promise<boolean>;
  loadTunnel: (signal?: AbortSignal) => Promise<WebhookReach>;
  /** Start (true) or stop (false) the app tunnel; explicit actions only. */
  setPublic: (
    on: boolean,
  ) => Promise<{ tunnel: WebhookReach; message: string }>;
}

/**
 * The saved webhook's address with Copy (parity row 20) and whether it is
 * reachable from the internet (parity row 52). The secret travels in a
 * header, out of the address that proxies and logs keep (B132); a service
 * that can only take an address gets the older form by choice. The secret
 * never enters the page: Copy reads it and writes it to the clipboard.
 * Nothing becomes public without "Make reachable from the internet" and its
 * confirmation.
 */
export default function WebhookAddress({
  taskId,
  localBase,
  readAddress,
  writeClipboard,
  loadTunnel,
  setPublic,
}: WebhookAddressProps) {
  const overlay = useOverlay();
  const [reach, setReach] = useState<WebhookReach | null>(null);
  const [copied, setCopied] = useState('');
  const [inAddress, setInAddress] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  useEffect(() => {
    const abort = new AbortController();
    loadTunnel(abort.signal).then(
      (next) => {
        if (!abort.signal.aborted) setReach(next);
      },
      () => {
        if (!abort.signal.aborted)
          setReach({ publicBase: null, canControl: false });
      },
    );
    return () => abort.abort();
  }, [loadTunnel]);

  async function copy(base: string | null, what: string) {
    setCopied('');
    setError('');
    try {
      const configuration = await readAddress();
      const text =
        base === null
          ? configuration.secret
          : `${base}${inAddress ? configuration.pathWithSecret : configuration.path}`;
      setCopied(
        (await writeClipboard(text))
          ? 'Copied.'
          : `Row-Bot couldn't copy the ${what}. Try again.`,
      );
    } catch (cause) {
      setError(clientError(cause).message);
    }
  }

  async function change(on: boolean) {
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const result = await setPublic(on);
      setReach(result.tunnel);
      setNotice(result.message);
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy(false);
    }
  }

  const publicHost = reach?.publicBase
    ? reach.publicBase.replace(/^https?:\/\//, '').replace(/\/+$/, '')
    : '';
  return (
    <div className="task-webhook-address stack">
      <div role="group" aria-label="Webhook address">
        <code className="settings-break-word">
          {`POST ${localBase}/api/webhook/${taskId}${inAddress ? '?secret=••••' : ''}`}
        </code>
        {!inAddress && (
          <code className="settings-break-word">
            {`${WEBHOOK_SECRET_HEADER}: ••••`}
          </code>
        )}
        <span className="task-webhook-actions">
          <Button
            className="small"
            onClick={() => void copy(localBase, 'address')}
          >
            Copy address
          </Button>
          {!inAddress && (
            <Button className="small" onClick={() => void copy(null, 'secret')}>
              Copy secret
            </Button>
          )}
          {copied && (
            <span role="status" className="home-caption">
              {copied}
            </span>
          )}
        </span>
        <label className="task-webhook-option">
          <input
            type="checkbox"
            checked={inAddress}
            onChange={(event) => {
              setInAddress(event.target.checked);
              setCopied('');
            }}
          />{' '}
          Put the secret in the address (for a service that can&apos;t send
          headers; addresses end up in logs)
        </label>
      </div>
      {reach &&
        (reach.publicBase ? (
          <p className="home-caption">
            Reachable from the internet at {publicHost}. Anyone with this
            webhook address can run the workflow.{' '}
            <Button
              className="small"
              disabled={busy}
              onClick={() =>
                void copy(reach.publicBase!.replace(/\/+$/, ''), 'address')
              }
            >
              Copy public address
            </Button>{' '}
            {reach.canControl && (
              <Button
                className="small"
                disabled={busy}
                onClick={() => void change(false)}
              >
                Stop public access
              </Button>
            )}
          </p>
        ) : (
          <p className="home-caption">
            Not reachable from the internet.{' '}
            {reach.canControl ? (
              <Button
                className="small"
                disabled={busy}
                onClick={(event) =>
                  overlay.open({
                    kind: 'alert',
                    returnFocusTo: event.currentTarget,
                    title: 'Make Row-Bot reachable from the internet?',
                    description:
                      'This starts your tunnel so services on the internet can call this webhook. Anyone with this webhook address can run the workflow. Row-Bot itself also opens at the tunnel address, where it still asks for sign-in. Stop it here or in Settings › Devices & remote access.',
                    confirmLabel: 'Make reachable',
                    onConfirm: () => {
                      overlay.close();
                      void change(true);
                    },
                  })
                }
              >
                Make reachable from the internet
              </Button>
            ) : (
              "Only Row-Bot's owner on this computer can make it reachable."
            )}
          </p>
        ))}
      {notice && (
        <p role="status" className="home-caption">
          {notice}
        </p>
      )}
      {error && (
        <p role="alert" className="task-builder-alert">
          {error}
        </p>
      )}
    </div>
  );
}
