import { useCallback, useEffect, useState } from 'react';
import { Laptop } from 'lucide-react';
import type { LocalAddressGrantView, LocalAddressPage } from '../../api/types';
import { clientError } from '../../api/errors';
import { parseTimestamp } from '../../ui/format';
import {
  Button,
  EntityList,
  EntityRow,
  InlineEmpty,
  Skeleton,
} from '../../ui/primitives';

/** How often the list is read again while it shows (grants come and go as the agent works). */
const REFRESH_MS = 10_000;

const SOURCE: Record<LocalAddressGrantView['source'], string> = {
  you: 'Allowed by you',
  reviewer: 'Allowed by the reviewer',
  hands_off: 'Allowed by Hands-off',
  row_bot_server: 'Row-Bot started this server',
};

export type LocalAddressesProps = {
  conversationId: string;
  load: (
    conversationId: string,
    signal: AbortSignal,
  ) => Promise<LocalAddressPage>;
  remove: (
    conversationId: string,
    grantId: string,
  ) => Promise<LocalAddressPage>;
};

/** "42 min left", "1 hour left": how long the address stays allowed. */
export function timeLeft(expiresAt: string, now = Date.now()) {
  const ends = parseTimestamp(expiresAt)?.getTime();
  if (ends === undefined) return '';
  const minutes = Math.max(1, Math.ceil((ends - now) / 60_000));
  return minutes >= 60 ? '1 hour left' : `${minutes} min left`;
}

function visible() {
  return (
    typeof document === 'undefined' || document.visibilityState !== 'hidden'
  );
}

/**
 * The addresses on this computer (localhost:5173) Row-Bot's browser may open
 * in this conversation, who allowed each, when it runs out, and Remove (F4).
 */
export function LocalAddresses({
  conversationId,
  load,
  remove,
}: LocalAddressesProps) {
  const [items, setItems] = useState<LocalAddressGrantView[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [removing, setRemoving] = useState('');
  const [message, setMessage] = useState('');
  const [attempt, setAttempt] = useState(0);

  const read = useCallback(
    (signal: AbortSignal) =>
      load(conversationId, signal)
        .then((page) => {
          if (signal.aborted || page.conversation_id !== conversationId) return;
          setItems(page.items);
          setFailed(false);
        })
        .catch(() => {
          if (!signal.aborted) setFailed(true);
        }),
    [conversationId, load],
  );

  useEffect(() => {
    const abort = new AbortController();
    setItems(null);
    setFailed(false);
    setMessage('');
    void read(abort.signal);
    const timer = setInterval(() => {
      if (visible()) void read(abort.signal);
    }, REFRESH_MS);
    return () => {
      abort.abort();
      clearInterval(timer);
    };
  }, [read, attempt]);

  async function removeOne(item: LocalAddressGrantView) {
    if (removing) return;
    setRemoving(item.grant_id);
    setMessage('');
    try {
      const page = await remove(conversationId, item.grant_id);
      setItems(page.items);
      setMessage(
        `${item.address} removed. Row-Bot asks again before opening it.`,
      );
    } catch (cause) {
      setMessage(clientError(cause).message);
    } finally {
      setRemoving('');
    }
  }

  return (
    <section
      className="capability-section stack browser-local-addresses"
      aria-label="Local addresses"
    >
      <div className="section-heading">
        <h4>Local addresses</h4>
      </div>
      {items === null && !failed ? (
        <Skeleton label="Checking local addresses" />
      ) : failed && items === null ? (
        <div className="browser-local-addresses-error" role="alert">
          <span>Local addresses couldn&apos;t be checked just now.</span>
          <Button
            className="small"
            onClick={() => setAttempt((value) => value + 1)}
          >
            Try again
          </Button>
        </div>
      ) : !items?.length ? (
        <InlineEmpty icon={<Laptop size={16} />}>
          No addresses on this computer are open to Row-Bot&apos;s browser in
          this chat. It asks before opening one, like localhost:5173.
        </InlineEmpty>
      ) : (
        <>
          <EntityList label="Local addresses allowed in this chat">
            {items.map((item) => (
              <EntityRow
                key={item.grant_id}
                title={item.address}
                icon={<Laptop size={16} />}
                meta={`${SOURCE[item.source]} · ${timeLeft(item.expires_at)}`}
                action={
                  <Button
                    variant="ghost"
                    className="small"
                    aria-label={`Remove ${item.address}`}
                    disabled={Boolean(removing)}
                    onClick={() => void removeOne(item)}
                  >
                    Remove
                  </Button>
                }
              />
            ))}
          </EntityList>
          <p className="browser-local-addresses-note">
            Each one is for this chat only, for up to an hour. Other computers
            and devices on your network never open here.
          </p>
        </>
      )}
      {message && <p role="status">{message}</p>}
    </section>
  );
}
