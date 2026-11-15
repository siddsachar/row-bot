import { useCallback, useEffect, useState } from 'react';
import { Hourglass, X } from 'lucide-react';
import type { WaitView } from '../../api/types';
import { useLiveUpdate } from '../../live';
import { useRuntime } from '../../runtime';
import { IconButton } from '../../ui/primitives';

/**
 * What conversations wait for (5.3.1): a process to end, a check to show
 * something, a time, another conversation. Read again when the server says
 * conversations changed, which is where a wait's changes are published.
 */
export function useWaits(): {
  waits: WaitView[];
  stop: (waitId: string) => Promise<void>;
} {
  const { controller } = useRuntime();
  const [waits, setWaits] = useState<WaitView[]>([]);
  const [readAt, setReadAt] = useState(0);
  useLiveUpdate(['conversations'], () => setReadAt(Date.now()));

  useEffect(() => {
    const abort = new AbortController();
    controller.waits(abort.signal).then(
      (page) => {
        if (!abort.signal.aborted && page) setWaits(page.items);
      },
      () => undefined,
    );
    return () => abort.abort();
  }, [controller, readAt]);

  const stop = useCallback(
    async (waitId: string) => {
      const page = await controller.stopWait(waitId).catch(() => null);
      if (page) setWaits(page.items);
      else setWaits((current) => current.filter((w) => w.wait_id !== waitId));
    },
    [controller],
  );
  return { waits, stop };
}

/**
 * Above the composer, one quiet line per wait: "Waiting for CI on PR 590 is
 * green · checks every 2 min" with a Stop. Nothing runs while it waits; when
 * it happens the conversation carries on by itself.
 */
export function WaitsStrip({ conversationId }: { conversationId: string }) {
  const { waits, stop } = useWaits();
  const mine = waits.filter((wait) => wait.conversation_id === conversationId);
  if (!conversationId || mine.length === 0) return null;
  return (
    <section className="waits-strip" aria-label="Waiting">
      <ul>
        {mine.map((wait) => (
          <li key={wait.wait_id} className="waits-item">
            <Hourglass size={14} aria-hidden />
            <span className="waits-label">
              Waiting for <strong>{wait.label}</strong>
            </span>
            <span className="waits-how">{wait.how}</span>
            <IconButton
              size="sm"
              label="Stop waiting"
              onClick={() => void stop(wait.wait_id)}
            >
              <X size={14} aria-hidden />
            </IconButton>
          </li>
        ))}
      </ul>
    </section>
  );
}
