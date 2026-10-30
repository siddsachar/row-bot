import {
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from 'react';
import { X } from 'lucide-react';
import { clientError } from '../../api/errors';
import type { ClaudePlanStatus, ClaudeSignInOutput } from '../../api/types';
import { useRuntime } from '../../runtime';
import { IconButton } from '../../ui/primitives';
import { TerminalSession, type TerminalLink } from '../panels/terminal-session';
import '../panels/NativeTerminal.css';

/** The one line above Claude's sign-in (U11), where the person's browser is somewhere else. */
export const SIGN_IN_GUIDE =
  'This is Claude’s own sign-in. Open the link it shows, sign in to Claude, then paste the code Claude gives you here.';
/** On the computer Claude runs on, Claude opens the browser itself and usually needs no code. */
export const SIGN_IN_GUIDE_HERE =
  'Finish signing in in the browser window Claude opened.';

/** How Claude's sign-in ended: closed or exited, and what `claude auth status` said after. */
export type SignInEnd = {
  ended: NonNullable<ClaudeSignInOutput['ended']> | 'failed';
  plan: ClaudePlanStatus | null;
  error: string;
};

/**
 * Claude's own sign-in (H15): `claude auth login` running on the computer
 * Row-Bot runs on, shown in a small terminal. Row-Bot carries keys in and
 * output out and never looks at either; Claude's prompts, link and messages
 * are Claude's. It ends when Claude Code exits or the panel closes.
 */
export default function ClaudeSignInPanel({
  onEnd,
}: {
  onEnd: (end: SignInEnd) => void;
}) {
  const { controller, platform } = useRuntime();
  const [browserOpens, setBrowserOpens] = useState<boolean | null>(null);
  const last = useRef<ClaudeSignInOutput | null>(null);
  const finished = useRef(false);
  const ending = useRef(onEnd);
  ending.current = onEnd;
  const host = useRef<HTMLDivElement>(null);

  const session = useMemo(() => {
    const link: TerminalLink = {
      open: async (_conversation, cols, rows) => {
        const view = await controller.openClaudeSignIn(cols, rows);
        setBrowserOpens(view.browser_opens);
        return { status: 'ok', lease: view.sign_in_id };
      },
      read: async (lease, cursor, signal) => {
        try {
          const value = await controller.readClaudeSignIn(
            lease,
            cursor,
            signal,
          );
          last.current = value;
          return { ...value, ended: value.status === 'ended' };
        } catch (cause) {
          // Closed elsewhere (a newer sign-in replaced it): nothing more comes.
          if (clientError(cause).code !== 'not_found') throw cause;
          last.current = null;
          return {
            cursor,
            latest: cursor,
            truncated: false,
            frames: [],
            ended: true,
          };
        }
      },
      input: (lease, data) => controller.writeClaudeSignIn(lease, data),
      resize: (lease, cols, rows) =>
        controller.resizeClaudeSignIn(lease, cols, rows),
      close: (lease) => controller.closeClaudeSignIn(lease),
      openLink: (uri) => void platform.openExternal(uri),
      textLinks: true,
    };
    return new TerminalSession(link);
  }, [controller, platform]);
  const state = useSyncExternalStore(session.subscribe, session.snapshot);

  function finish(end: SignInEnd) {
    if (finished.current) return;
    finished.current = true;
    ending.current(end);
  }

  useLayoutEffect(() => {
    const element = host.current!;
    const detach = session.attach(element);
    const observer = new ResizeObserver(() => session.layout());
    observer.observe(element);
    return () => {
      observer.disconnect();
      detach();
    };
  }, [session]);

  useEffect(() => {
    void session.connect(null).then(() => session.focus());
    session.applyTheme();
    const observer = new MutationObserver(() => session.applyTheme());
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['data-theme', 'data-accent'],
    });
    return () => {
      observer.disconnect();
      // Closing the panel ends Claude's sign-in and drops what it showed.
      void session.close().catch(() => {});
      session.terminal.dispose();
    };
  }, [session]);

  useEffect(() => {
    if (state.status === 'ended')
      finish({
        ended: last.current?.ended ?? 'replaced',
        plan: last.current?.plan ?? null,
        error: '',
      });
    else if (state.status === 'failed')
      finish({ ended: 'failed', plan: null, error: state.error });
  }, [state.status, state.error]);

  return (
    <section className="claude-sign-in" aria-label="Claude’s sign-in">
      <header className="claude-sign-in-header">
        <p className="claude-sign-in-guide" role="status">
          {browserOpens === null
            ? 'Starting Claude’s sign-in…'
            : browserOpens
              ? SIGN_IN_GUIDE_HERE
              : SIGN_IN_GUIDE}
        </p>
        <IconButton
          size="sm"
          label="Close Claude’s sign-in"
          onClick={() => {
            void session.close().catch(() => {});
            finish({ ended: 'closed', plan: null, error: '' });
          }}
        >
          <X size={14} aria-hidden />
        </IconButton>
      </header>
      <div
        ref={host}
        className="native-terminal-screen claude-sign-in-screen"
        data-testid="claude-sign-in-terminal"
      />
    </section>
  );
}
