import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { RotateCw } from 'lucide-react';
import { Button, ErrorState, ListSkeleton } from '../ui/primitives';
import { problem } from './words';

export type Load<T> =
  | { status: 'loading'; data?: undefined; error?: undefined }
  | { status: 'ready'; data: T; error?: undefined }
  | { status: 'error'; data?: undefined; error: unknown };

/** Loads `fetcher()` now and again on `reload()` or when `key` changes. */
export function useLoad<T>(fetcher: () => Promise<T>, key = '') {
  const [state, setState] = useState<Load<T>>({ status: 'loading' });
  const latest = useRef(fetcher);
  latest.current = fetcher;
  const run = useRef(0);
  const reload = useCallback(() => {
    const id = ++run.current;
    setState({ status: 'loading' });
    latest.current().then(
      (data) => {
        if (id === run.current) setState({ status: 'ready', data });
      },
      (error: unknown) => {
        if (id === run.current) setState({ status: 'error', error });
      },
    );
  }, []);
  useEffect(() => reload(), [reload, key]);
  /** Replace the loaded data after a change the server confirmed. */
  const update = useCallback((change: (data: T) => T) => {
    setState((current) =>
      current.status === 'ready'
        ? { status: 'ready', data: change(current.data) }
        : current,
    );
  }, []);
  return { ...state, reload, update };
}

/** The loading and error states of a load; its data once there. */
export function Loaded<T>({
  load,
  label,
  what,
  children,
}: {
  load: Load<T> & { reload: () => void };
  /** The loading state's name ("Loading people"). */
  label: string;
  /** What couldn't load ("people"). */
  what: string;
  children: (data: T) => ReactNode;
}) {
  if (load.status === 'loading')
    return <ListSkeleton label={label} rows={4} className="console-skeleton" />;
  if (load.status === 'error')
    return (
      <ErrorState
        title={`Couldn't load ${what}`}
        action={
          <Button onClick={load.reload}>
            <RotateCw size={16} aria-hidden />
            Try again
          </Button>
        }
      >
        {problem(load.error)}
      </ErrorState>
    );
  return <>{children(load.data)}</>;
}
