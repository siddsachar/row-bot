import { clientError } from '../../api/errors';

type Listener = (source: unknown) => void;
const listeners = new Set<Listener>();

/**
 * One MCP configuration revision for every Settings › MCP panel (B262).
 *
 * Each panel reads the saved configuration through its own page, so a save
 * in one panel left the others holding a stale revision and their next
 * review failed. After every save the saving panel calls `saved`, and every
 * other panel reads again. A review that still meets a conflict (another
 * window saved) reads again and retries once (`reviewFresh`).
 */
export const mcpRevision = {
  subscribe(notify: Listener) {
    listeners.add(notify);
    return () => {
      listeners.delete(notify);
    };
  },
  /** The saved configuration changed; `source` is the panel that saved it. */
  saved(source?: unknown) {
    listeners.forEach((notify) => notify(source));
  },
};

/**
 * Reviews against `revision`; on a revision conflict reads the current
 * revision with `reread` and retries once.
 */
export async function reviewFresh<T>(
  review: (revision: string) => Promise<T>,
  revision: string,
  reread: () => Promise<string | null>,
): Promise<T> {
  try {
    return await review(revision);
  } catch (cause) {
    if (clientError(cause).code !== 'revision_conflict') throw cause;
    const fresh = await reread();
    if (!fresh) throw cause;
    return review(fresh);
  }
}
