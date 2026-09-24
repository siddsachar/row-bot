import { useEffect, useState } from 'react';
import { Power, RefreshCw } from 'lucide-react';
import {
  accessTailscaleClient,
  type AccessTailscaleClient,
  type TailscaleReceipt,
  type TailscaleStatus,
} from '../../api/access';
import { clientError } from '../../api/errors';
import { Button, ErrorState, Skeleton } from '../../ui/primitives';

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

export default function AccessTailscale({
  client = accessTailscaleClient,
}: {
  client?: AccessTailscaleClient;
}) {
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
  const [reload, setReload] = useState(0);

  useEffect(() => {
    const abort = new AbortController();
    void client.status(abort.signal).then(
      (value) => {
        if (abort.signal.aborted) return;
        setStatus(value.status);
        setCanManage(value.can_manage);
      },
      (cause) => {
        if (!abort.signal.aborted) setError(clientError(cause).message);
      },
    );
    return () => abort.abort();
  }, [client, reload]);

  async function check() {
    if (busy) return;
    setBusy(true);
    setError('');
    try {
      setStatus(await client.check());
      setInspected(true);
      setNotice('Tailscale status checked.');
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy(false);
    }
  }

  function accept(receipt: TailscaleReceipt) {
    if (receipt.pending) {
      setNotice('Tailscale action is still running. Check its result again.');
      return;
    }
    setStatus(receipt.status ?? null);
    setNotice(
      receipt.success
        ? receipt.restart_required
          ? 'Tailscale changed. Restart Row-Bot to refresh access routes.'
          : 'Tailscale route updated.'
        : receipt.error || 'Tailscale action did not complete.',
    );
    setPending('');
    setReceiptMissing(false);
    retainCommand('');
    setReload((value) => value + 1);
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

  return (
    <section
      className="settings-snapshot-section stack"
      aria-labelledby="access-tailscale-title"
    >
      <header className="settings-snapshot-heading">
        <div>
          <h3 id="access-tailscale-title">Tailscale Serve</h3>
          <p>
            Private HTTPS access through your tailnet. Checking or changing
            Serve is explicit.
          </p>
          <p className="muted">
            Enabling Serve changes local Tailscale configuration and may contact
            Tailscale. Restart Row-Bot if the route does not appear.
          </p>
        </div>
      </header>
      {error && (
        <ErrorState
          title="Tailscale needs attention"
          action={
            <Button onClick={() => setReload((value) => value + 1)}>
              Retry
            </Button>
          }
        >
          {error}
        </ErrorState>
      )}
      {status === undefined && !error && (
        <Skeleton label="Loading cached Tailscale status" />
      )}
      {status === null && (
        <p>Tailscale has not been checked in this app session.</p>
      )}
      {status && (
        <p role="status">
          {status.state.replaceAll('_', ' ')} · {status.detail}
        </p>
      )}
      {status?.serve_url && (
        <p>
          Serve address: <code>{status.serve_url}</code>
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      {canManage ? (
        <div className="button-row">
          <Button disabled={busy} onClick={() => void check()}>
            <RefreshCw size={18} aria-hidden="true" /> Check status
          </Button>
          {status?.state === 'ready' && !pending && (
            <Button disabled={busy} onClick={() => void run('enable')}>
              <Power size={18} aria-hidden="true" /> Enable Serve
            </Button>
          )}
          {status?.state === 'active_owned' && !pending && (
            <Button disabled={busy} onClick={() => void run('disable')}>
              <Power size={18} aria-hidden="true" /> Disable owned Serve
            </Button>
          )}
          {pending && (
            <Button disabled={busy} onClick={() => void recover()}>
              Check original result
            </Button>
          )}
          {pending && inspected && receiptMissing && (
            <Button
              variant="ghost"
              disabled={busy}
              onClick={() => {
                retainCommand('');
                setPending('');
                setNotice(
                  'Original command record cleared after status inspection.',
                );
              }}
            >
              Clear pending record
            </Button>
          )}
        </div>
      ) : (
        <p>Serve changes are available in the local owner session.</p>
      )}
    </section>
  );
}
