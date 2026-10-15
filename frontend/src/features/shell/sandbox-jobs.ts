import { useCallback, useEffect, useRef, useState } from 'react';
import type { SandboxJobView } from '../../api/types';
import { clientError } from '../../api/errors';
import { parseTimestamp } from '../../ui/format';
import type { Tone } from '../../ui/primitives';
import { useRuntime } from '../../runtime';

/**
 * Prepare sandbox (F18): the Docker sandbox gets the project's test tools,
 * with its network on only while they install, then off again. A job is
 * followed by its id, every second while it runs.
 */

const LIVE_POLL_MS = 1000;

export function jobLive(view: SandboxJobView | null | undefined) {
  return (
    !!view &&
    (view.status === 'pulling' ||
      view.status === 'installing' ||
      view.status === 'finishing')
  );
}

export function jobTitle(view: SandboxJobView) {
  if (view.kind === 'pull')
    return {
      pulling: 'Downloading the sandbox image',
      installing: 'Downloading the sandbox image',
      finishing: 'Downloading the sandbox image',
      ready: 'Sandbox image downloaded',
      failed: "Couldn't download the sandbox image",
      stopped: 'Download stopped',
    }[view.status];
  return {
    pulling: 'Getting the sandbox image first',
    installing: 'Preparing the sandbox',
    finishing: 'Turning the network off',
    ready: 'Sandbox ready for tests',
    failed: "Couldn't prepare the sandbox",
    stopped: 'Prepare stopped',
  }[view.status];
}

export function jobStatus(view: SandboxJobView): {
  tone: Tone;
  label: string;
  pulse?: boolean;
} {
  switch (view.status) {
    case 'pulling':
      return { tone: 'accent', label: 'Downloading', pulse: true };
    case 'installing':
      return { tone: 'accent', label: 'Installing', pulse: true };
    case 'finishing':
      return { tone: 'accent', label: 'Finishing', pulse: true };
    case 'ready':
      return {
        tone: 'success',
        label: view.kind === 'pull' ? 'Done' : 'Ready',
      };
    case 'stopped':
      return { tone: 'neutral', label: 'Stopped' };
    default:
      return { tone: 'danger', label: 'Failed' };
  }
}

/** "12:04" in this device's time. */
export function clockTime(value: string | null | undefined) {
  const date = parseTimestamp(value);
  return date
    ? new Intl.DateTimeFormat(undefined, { timeStyle: 'short' }).format(date)
    : '';
}

/** The sandbox network for this job, in words, with its tone. */
export function networkWords(view: SandboxJobView): {
  text: string;
  warning: boolean;
} {
  if (view.network === 'on')
    return { text: 'Network on for this install', warning: true };
  if (!view.network_on_at)
    return {
      text:
        view.kind === 'pull'
          ? 'Docker downloads it; the sandbox network stays off'
          : 'Network stayed off',
      warning: false,
    };
  if (!view.network_off_confirmed)
    return {
      text: "Couldn't confirm the network is off",
      warning: true,
    };
  return { text: 'Network off again', warning: false };
}

export function problemWords(view: SandboxJobView) {
  if (view.status === 'stopped')
    return view.stopped_by === 'quit'
      ? 'Row-Bot closed before it finished. Nothing more was installed.'
      : 'Stopped. Nothing more was installed.';
  if (view.status !== 'failed') return '';
  return view.detail || 'It did not finish.';
}

/** One job, read live while it runs; `merge` takes a fresher view (from Stop). */
export function useSandboxJob(jobId: string | null | undefined) {
  const { controller } = useRuntime();
  const [view, setView] = useState<SandboxJobView | null>(null);
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => {
    if (!jobId) return;
    const abort = new AbortController();
    let alive = true;
    const read = async () => {
      try {
        const next = await controller.sandboxJob(jobId, abort.signal);
        if (!alive) return;
        setView(next);
        setError('');
        if (jobLive(next)) timer.current = setTimeout(read, LIVE_POLL_MS);
      } catch (cause) {
        if (alive && !abort.signal.aborted)
          setError(clientError(cause).message);
      }
    };
    void read();
    return () => {
      alive = false;
      abort.abort();
      clearTimeout(timer.current);
    };
  }, [controller, jobId, attempt]);
  const refresh = useCallback(() => setAttempt((value) => value + 1), []);
  const merge = useCallback(
    (next: SandboxJobView) => {
      setView(next);
      if (jobLive(next)) refresh();
    },
    [refresh],
  );
  return { view, error, refresh, merge };
}
