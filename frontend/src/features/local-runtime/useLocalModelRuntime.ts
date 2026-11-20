import { useCallback, useEffect, useRef, useState } from 'react';
import type { ClientController } from '../../api/controller';
import type { LocalModelRuntimeView } from '../../api/types';
import { useRuntime } from '../../runtime';

/** The app's controller where a runtime provides one (Setup); Settings pages pass their own. */
export function useOptionalController(): ClientController | undefined {
  try {
    return useRuntime().controller;
  } catch {
    return undefined;
  }
}

/** How often the state is read while a download runs, and otherwise (0: once). */
const DOWNLOADING_MS = 1000;

/**
 * Run on this computer's state (E1), read now and again every second while a
 * download runs (and every `idleMs` otherwise, when given), with the speed seen
 * so far for "about 4 min". Reading it never downloads anything.
 */
export function useLocalModelRuntime(
  enabled: boolean,
  idleMs = 0,
  given?: ClientController,
) {
  const fromRuntime = useOptionalController();
  const controller = given ?? fromRuntime;
  const [view, setView] = useState<LocalModelRuntimeView | null>(null);
  const [speed, setSpeed] = useState<number | null>(null);
  const last = useRef<{ at: number; done: number } | null>(null);
  const timer = useRef(0);
  const stopped = useRef(false);

  const accept = useCallback(
    (next: LocalModelRuntimeView | null | undefined) => {
      if (!next || stopped.current) return;
      const download = next.state === 'downloading' ? next.download : null;
      if (download) {
        const now = Date.now();
        const before = last.current;
        if (before && now > before.at && download.done_bytes >= before.done) {
          const instant =
            ((download.done_bytes - before.done) * 1000) / (now - before.at);
          setSpeed((old) =>
            old == null ? instant : old * 0.7 + instant * 0.3,
          );
        }
        last.current = { at: now, done: download.done_bytes };
      } else {
        last.current = null;
        setSpeed(null);
      }
      setView(next);
    },
    [],
  );

  const read = useCallback(async () => {
    window.clearTimeout(timer.current);
    if (stopped.current) return;
    let next: LocalModelRuntimeView | null | undefined = null;
    if (document.visibilityState !== 'hidden') {
      try {
        next = await controller?.localModelRuntime();
        accept(next);
      } catch {
        /* Best effort: the next read tries again. */
      }
    }
    if (stopped.current) return;
    const wait = next?.state === 'downloading' ? DOWNLOADING_MS : idleMs;
    if (wait) timer.current = window.setTimeout(() => void read(), wait);
  }, [accept, controller, idleMs]);

  useEffect(() => {
    if (!enabled) return;
    stopped.current = false;
    void read();
    return () => {
      stopped.current = true;
      window.clearTimeout(timer.current);
    };
  }, [enabled, read]);

  /** Show an action's answer at once and keep reading while it downloads. */
  const update = useCallback(
    (next: LocalModelRuntimeView | null | undefined) => {
      accept(next);
      if (next?.state === 'downloading') {
        window.clearTimeout(timer.current);
        timer.current = window.setTimeout(() => void read(), DOWNLOADING_MS);
      }
    },
    [accept, read],
  );

  return { view, speed, update, reload: read };
}
