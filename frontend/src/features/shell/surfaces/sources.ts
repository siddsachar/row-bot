import { useCallback, useEffect, useState } from 'react';
import { useOptionalRuntime } from '../../../runtime';
import { writePath, type DataModel, type SurfaceSpec } from './a2ui';

/** What each source reads, in the person's words. */
const WORDS: Record<string, string> = {
  'rowbot.now': 'The time',
  'rowbot.tasks': 'Your tasks',
  'rowbot.memory': 'Your memory',
  'rowbot.file': 'A file in your workspace',
  'rowbot.files': 'A folder in your workspace',
};

/** What a surface's sources read, each once, for its "on this computer" mark. */
export function sourceWords(spec: SurfaceSpec): string[] {
  const seen: string[] = [];
  for (const call of Object.values(spec.sources ?? {})) {
    const path = call.args?.path;
    const words =
      call['@call'] === 'rowbot.file' && typeof path === 'string' && path
        ? `Your file ${path.split(/[\\/]/).pop()}`
        : WORDS[call['@call']];
    if (words && !seen.includes(words)) seen.push(words);
  }
  return seen;
}

export type SourceState = {
  values: Record<string, unknown>;
  problems: Record<string, string>;
  readAt: string;
  loading: boolean;
  /** Read every source again now. */
  refresh: () => void;
};

/**
 * The values of a surface's sources (private bindings), read by Row-Bot on
 * this computer for this client only: they are never part of the chat, so
 * the model that drew the surface never sees them.
 */
export function useSurfaceSources(
  conversationId: string | undefined,
  messageId: string | undefined,
  spec: SurfaceSpec | null,
  /** Read again this often (ms) while the page is shown; 0 for never. */
  every = 0,
): SourceState | null {
  const controller = useOptionalRuntime()?.controller;
  const has = !!spec && Object.keys(spec.sources ?? {}).length > 0;
  const surfaceId = spec?.surfaceId ?? '';
  const [state, setState] = useState<Omit<SourceState, 'refresh'>>({
    values: {},
    problems: {},
    readAt: '',
    loading: has,
  });
  const [round, setRound] = useState(0);
  const read = useCallback(
    async (refresh: boolean, signal: AbortSignal) => {
      if (!controller || !conversationId || !messageId || !has) return;
      setState((current) => ({ ...current, loading: true }));
      try {
        const data = await controller.surfaceData(
          conversationId,
          { message_id: messageId, surface_id: surfaceId, refresh },
          signal,
        );
        if (signal.aborted || !data) return;
        setState({
          values: data.values,
          problems: data.problems,
          readAt: data.read_at,
          loading: false,
        });
      } catch {
        if (signal.aborted) return;
        setState((current) => ({
          ...current,
          loading: false,
          problems: { '/': 'Row-Bot couldn’t fill this in just now.' },
        }));
      }
    },
    [controller, conversationId, messageId, surfaceId, has],
  );
  useEffect(() => {
    const abort = new AbortController();
    void read(round > 0, abort.signal);
    return () => abort.abort();
  }, [read, round]);
  useEffect(() => {
    if (!every || !has) return;
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible')
        setRound((value) => value + 1);
    }, every);
    return () => window.clearInterval(timer);
  }, [every, has]);
  if (!has) return null;
  return { ...state, refresh: () => setRound((value) => value + 1) };
}

/** The data model with each source's latest value at its path. */
export function withSources(
  model: DataModel,
  values: Record<string, unknown>,
): DataModel {
  let next = model;
  for (const [path, value] of Object.entries(values))
    next = writePath(next, path, value);
  return next;
}

/** The data model without the sources' values: those are read again, never kept beside the chat. */
export function withoutSources(model: DataModel, spec: SurfaceSpec): DataModel {
  let next = model;
  for (const path of Object.keys(spec.sources ?? {}))
    next = writePath(next, path, undefined);
  return JSON.parse(JSON.stringify(next)) as DataModel;
}
