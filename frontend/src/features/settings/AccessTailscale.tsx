import { useEffect, useRef, useState, type ReactNode } from 'react';
import { ExternalLink, Power, RefreshCw } from 'lucide-react';
import {
  accessTailscaleClient,
  type AccessTailscaleClient,
  type TailscaleReceipt,
  type TailscaleStatus,
} from '../../api/access';
import { clientError } from '../../api/errors';
import type { ClientPlatform } from '../../platform';
import { writeClipboardText } from '../../platform/clipboard';
import { useOverlay } from '../../ui/overlays';
import { Button, Skeleton, type Tone } from '../../ui/primitives';

const pendingKey = 'row-bot-tailscale-command';

function retainedCommand(): string {
  try {
    return sessionStorage.getItem(pendingKey) ?? '';
  } catch {
    return '';
  }
}
function retainCommand(id: string) {
  try {
    if (id) sessionStorage.setItem(pendingKey, id);
    else sessionStorage.removeItem(pendingKey);
  } catch {
    /* storage may be unavailable */
  }
}

function host(url: string) {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}

/** A Tailscale state in words, with the next step when there is one (B138). */
export function tailscaleWords(status: TailscaleStatus): {
  line: string;
  tone: Tone;
  next?: ReactNode;
} {
  switch (status.state) {
    case 'cli_not_found':
      return {
        line: 'Tailscale isn’t installed on this computer.',
        tone: 'neutral',
        next: (
          <a
            href="https://tailscale.com/download"
            target="_blank"
            rel="noopener noreferrer"
          >
            Get Tailscale <ExternalLink size={12} aria-hidden />
          </a>
        ),
      };
    case 'signed_out':
      return {
        line: 'Tailscale is installed but signed out. Sign in to Tailscale on this computer, then check again.',
        tone: 'warning',
      };
    case 'daemon_unavailable':
      return {
        line: 'Tailscale isn’t running on this computer. Start it, then check again.',
        tone: 'warning',
      };
    case 'ready':
      return {
        line: 'Ready to share Row-Bot privately with the devices on your tailnet.',
        tone: 'neutral',
      };
    case 'consent_required':
      return {
        line: 'Tailscale needs your OK to use HTTPS on your tailnet. Allow it on Tailscale’s page, then check again.',
        tone: 'warning',
      };
    case 'route_conflict':
      return {
        line: 'Tailscale already shares something else from this computer, and Row-Bot won’t change it.',
        tone: 'warning',
        next: (
          <ol className="access-next-steps">
            <li>
              In a terminal, run <code>tailscale serve status</code> to see what
              is shared.
            </li>
            <li>
              Remove what you no longer need (<code>tailscale serve reset</code>{' '}
              removes all of it).
            </li>
            <li>Check again here, or choose Same Wi-Fi or Internet.</li>
          </ol>
        ),
      };
    case 'active_owned':
      return {
        line: `Shared privately at ${host(status.serve_url)}.`,
        tone: 'success',
      };
    case 'active_unowned':
      return {
        line: `Shared at ${host(status.serve_url)} by your own Tailscale setting. Row-Bot uses it and won’t change it.`,
        tone: 'success',
      };
    case 'funnel_active':
      return {
        line: 'Tailscale Funnel makes this computer public, so Row-Bot won’t use it for a private link. Turn Funnel off to share privately.',
        tone: 'warning',
      };
    case 'unsupported_cli':
      return {
        line: 'This Tailscale is too old to share Row-Bot. Update Tailscale, then check again.',
        tone: 'warning',
      };
    case 'outcome_unverified':
      return {
        line: 'Row-Bot couldn’t confirm the last Tailscale change. Check again before trying anything else.',
        tone: 'warning',
      };
    default:
      return {
        line: status.detail || 'Tailscale reported a problem. Check again.',
        tone: 'danger',
      };
  }
}

/**
 * Tailscale for Devices & remote access. In the connect flow (`option`) it
 * checks this computer's Tailscale once when opened (the local CLI; nothing
 * changes), says the state in words with its next step, shares Row-Bot
 * privately after one confirmation and links Tailscale's own consent page
 * when it asks. In Advanced (`line`) it only shows a share Row-Bot made, with
 * Copy and Stop sharing, and never probes.
 */
export default function AccessTailscale({
  client = accessTailscaleClient,
  variant = 'option',
  onChanged,
  writeClipboard,
}: {
  client?: AccessTailscaleClient;
  variant?: 'option' | 'line';
  /** Called after Tailscale changed, so routes can be read again. */
  onChanged?: () => void;
  writeClipboard?: ClientPlatform['writeClipboard'];
}) {
  const overlay = useOverlay();
  const [status, setStatus] = useState<TailscaleStatus | null | undefined>(
    undefined,
  );
  const [canManage, setCanManage] = useState(false);
  const [pending, setPending] = useState(retainedCommand);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [inspected, setInspected] = useState(false);
  const [receiptMissing, setReceiptMissing] = useState(false);
  const probed = useRef(false);

  useEffect(() => {
    const abort = new AbortController();
    void client.status(abort.signal).then(
      (value) => {
        if (abort.signal.aborted) return;
        setStatus(value.status);
        setCanManage(value.can_manage);
        // Opening the connect flow is the explicit step: look once.
        if (
          variant === 'option' &&
          value.can_manage &&
          !value.status &&
          !probed.current
        ) {
          probed.current = true;
          void check();
        }
      },
      (cause) => {
        if (!abort.signal.aborted) setError(clientError(cause).message);
      },
    );
    return () => abort.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client]);

  async function check() {
    setBusy(true);
    setError('');
    try {
      setStatus(await client.check());
      setInspected(true);
      onChanged?.();
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy(false);
    }
  }

  function accept(receipt: TailscaleReceipt) {
    if (receipt.pending) {
      setNotice('The Tailscale change is still running. Check it again.');
      return;
    }
    setStatus(receipt.status ?? null);
    setNotice(
      receipt.success
        ? receipt.restart_required
          ? 'Done. Restart Row-Bot to use the new address.'
          : 'Done. Row-Bot is restarting to use the new address.'
        : receipt.error || 'Tailscale didn’t make the change.',
    );
    setPending('');
    setReceiptMissing(false);
    retainCommand('');
    onChanged?.();
  }

  async function run(action: 'enable' | 'disable') {
    if (busy || pending) return;
    const id = crypto.randomUUID();
    setPending(id);
    retainCommand(id);
    setBusy(true);
    setError('');
    setNotice('');
    try {
      accept(await client.action(action, id));
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy(false);
    }
  }

  async function recover() {
    if (busy || !pending) return;
    setBusy(true);
    setError('');
    try {
      accept(await client.receipt(pending));
    } catch (cause) {
      const failure = clientError(cause);
      setError(failure.message);
      setReceiptMissing(failure.code === 'receipt_missing');
    } finally {
      setBusy(false);
    }
  }

  const words = status ? tailscaleWords(status) : null;
  const recovery = pending && (
    <>
      <Button disabled={busy} onClick={() => void recover()}>
        Check the earlier change
      </Button>
      {inspected && receiptMissing && (
        <Button
          variant="ghost"
          disabled={busy}
          onClick={() => {
            retainCommand('');
            setPending('');
            setNotice('Cleared. The status above is current.');
          }}
        >
          Forget it
        </Button>
      )}
    </>
  );

  if (variant === 'line') {
    if (status?.state !== 'active_owned' && !pending) return null;
    return (
      <div className="access-status-line" data-tone="success">
        <p>
          <strong>Private</strong>{' '}
          {status ? tailscaleWords(status).line : 'A Tailscale change is open.'}
        </p>
        <div className="button-row">
          {status?.serve_url && (
            <Button
              variant="ghost"
              onClick={() =>
                void writeClipboardText(status.serve_url, writeClipboard).then(
                  (copied) =>
                    setNotice(copied ? 'Copied.' : 'Row-Bot couldn’t copy it.'),
                )
              }
            >
              Copy address
            </Button>
          )}
          {canManage && !pending && (
            <Button disabled={busy} onClick={() => void run('disable')}>
              <Power size={16} aria-hidden /> Stop sharing
            </Button>
          )}
          {canManage && recovery}
        </div>
        {notice && <p role="status">{notice}</p>}
        {error && <p role="alert">{error}</p>}
      </div>
    );
  }

  return (
    <div className="access-option-detail" aria-busy={busy || undefined}>
      {status === undefined && !error && <Skeleton label="Reading Tailscale" />}
      {status === null && !busy && (
        <p className="settings-help">
          {canManage
            ? 'Tailscale hasn’t been checked yet.'
            : 'Tailscale is set up from the computer running Row-Bot.'}
        </p>
      )}
      {busy && !status && <p className="settings-help">Checking Tailscale…</p>}
      {words && (
        <p className="settings-help" data-tone={words.tone}>
          {words.line}
        </p>
      )}
      {words?.next}
      {notice && <p role="status">{notice}</p>}
      {error && <p role="alert">{error}</p>}
      {canManage && (
        <div className="button-row">
          {status?.state === 'consent_required' && status.consent_url && (
            <a
              className="button primary"
              href={status.consent_url}
              target="_blank"
              rel="noopener noreferrer"
            >
              Open Tailscale’s consent page{' '}
              <ExternalLink size={14} aria-hidden />
            </a>
          )}
          {status?.state === 'ready' && !pending && (
            <Button
              variant="primary"
              disabled={busy}
              onClick={(event) =>
                overlay.open({
                  kind: 'alert',
                  returnFocusTo: event.currentTarget,
                  title: 'Share Row-Bot privately on your tailnet?',
                  description:
                    'Devices signed in to your tailnet can open Row-Bot at this computer’s tailnet address, and each still needs a code from this page to sign in. This changes Tailscale’s settings on this computer; Tailscale may ask you to allow HTTPS once. Row-Bot restarts to use the new address.',
                  confirmLabel: 'Share privately',
                  onConfirm: () => {
                    overlay.close();
                    void run('enable');
                  },
                })
              }
            >
              Share privately…
            </Button>
          )}
          {status?.state !== 'active_owned' &&
            status?.state !== 'active_unowned' &&
            status?.state !== 'ready' && (
              <Button disabled={busy} onClick={() => void check()}>
                <RefreshCw size={16} aria-hidden /> Check again
              </Button>
            )}
          {recovery}
        </div>
      )}
    </div>
  );
}
