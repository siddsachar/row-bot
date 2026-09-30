import {
  createContext,
  useContext,
  useEffect,
  useEffectEvent,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import * as Dialog from '@radix-ui/react-dialog';
import * as Toast from '@radix-ui/react-toast';
import { PanelLeftClose, X } from 'lucide-react';
import { Button, IconButton } from './primitives';

type Overlay = {
  key?: string;
  className?: string;
  /** Usually text; a node can follow live state, e.g. a renamed item. */
  title: ReactNode;
  description: string;
  content?: ReactNode;
  /** A palette has no header or footer chrome; Escape closes it. */
  kind?: 'dialog' | 'sheet' | 'drawer' | 'alert' | 'palette';
  confirmLabel?: string;
  onConfirm?: () => void;
  returnFocusTo?: HTMLElement | null;
};
type Task = Overlay & { opener: HTMLElement | null };
export type NoticeTone = 'warning' | 'danger';
/** One action on a notice, e.g. Undo after an easy-to-regret removal. */
export type NoticeAction = { label: string; onAction: () => void };
type Notice = {
  id: number;
  message: string;
  tone?: NoticeTone;
  action?: NoticeAction;
};
let historyOwner = 0;

/** Same-URL history entries let platform Back dismiss modal work first. */
function useOverlayHistoryLevel(level: number, onBack: () => void) {
  const [owner] = useState(() => `overlay-${++historyOwner}`);
  const armed = useRef(0);
  const programmatic = useRef(0);
  const handleBack = useEffectEvent(onBack);
  useEffect(() => {
    const popstate = () => {
      if (programmatic.current > 0) {
        programmatic.current -= 1;
        return;
      }
      if (armed.current === 0) return;
      armed.current -= 1;
      handleBack();
    };
    window.addEventListener('popstate', popstate);
    return () => window.removeEventListener('popstate', popstate);
  }, []);
  useEffect(() => {
    while (armed.current < level) {
      armed.current += 1;
      const existing = window.history.state;
      const state =
        existing && typeof existing === 'object' && !Array.isArray(existing)
          ? { ...existing }
          : {};
      window.history.pushState(
        {
          ...state,
          __row_bot_overlay: { owner, depth: armed.current },
        },
        '',
        window.location.href,
      );
    }
    while (armed.current > level) {
      const marker = window.history.state?.__row_bot_overlay;
      armed.current -= 1;
      if (!marker || marker.owner !== owner) {
        armed.current = level;
        break;
      }
      programmatic.current += 1;
      window.history.back();
    }
  }, [level, owner]);
  return () => {
    if (armed.current === 0) return;
    const marker = window.history.state?.__row_bot_overlay;
    armed.current -= 1;
    if (!marker || marker.owner !== owner) return;
    programmatic.current += 1;
    window.history.back();
  };
}

/** How long a notice stays (Radix holds it while hovered or focused). */
export const NOTICE_MS = 5000;
export const TONED_NOTICE_MS = 8000;
export const ACTION_NOTICE_MS = 12000;

const OverlayContext = createContext<{
  open: (overlay: Overlay) => void;
  close: (returnFocusTo?: HTMLElement | null) => void;
  dismiss: (key: string) => void;
  /**
   * A short notice that goes away by itself (5 s; warnings and errors 8 s
   * and announced; with an action such as Undo 12 s, run at most once).
   * Hovering or focusing one holds it.
   */
  notify: (message: string, tone?: NoticeTone, action?: NoticeAction) => void;
} | null>(null);

/** A single Radix modal focus/scroll scope; confirmation suspends a mounted task. */
export function OverlayProvider({ children }: { children: ReactNode }) {
  const [task, setTask] = useState<Task | null>(null);
  const [confirmation, setConfirmation] = useState<Task | null>(null);
  const [notices, setNotices] = useState<Notice[]>([]);
  const nextNotice = useRef(1);
  const returningTo = useRef<HTMLElement | null>(null);
  const resumeFocus = useRef<HTMLElement | null>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const current = confirmation ?? task;
  // Handlers inside an overlay's content were created when it opened; closing
  // and dismissing act on the overlays shown now, not on that render's state.
  const shown = useRef({ task, confirmation });
  useLayoutEffect(() => {
    shown.current = { task, confirmation };
  });
  const shownClass = `dialog ${confirmation ? 'alert-dialog' : task?.kind === 'sheet' ? 'sheet' : task?.kind === 'drawer' ? 'drawer' : task?.kind === 'palette' ? 'palette' : ''} ${current?.className ?? ''}`;
  // A closing surface keeps its presentation. Dropping the kind class would
  // switch it to the base dialog animation, which Radix treats as an exit
  // animation: an empty card would fade in for a moment and its outside-
  // dismiss would swallow the next tap (reopening the drawer at once).
  const [closingClass, setClosingClass] = useState(shownClass);
  if (current && closingClass !== shownClass) setClosingClass(shownClass);
  const palette = !confirmation && task?.kind === 'palette';
  const level = confirmation ? 2 : task ? 1 : 0;
  const activeElement = () =>
    document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
  function open(overlay: Overlay) {
    if (overlay.kind === 'alert')
      setConfirmation({
        ...overlay,
        opener: overlay.returnFocusTo ?? activeElement(),
      });
    else {
      setConfirmation(null);
      setTask({
        ...overlay,
        opener: overlay.returnFocusTo ?? task?.opener ?? activeElement(),
      });
    }
  }
  function closeInternal(returnFocusTo?: HTMLElement | null) {
    const { task, confirmation } = shown.current;
    if (confirmation) {
      if (task) resumeFocus.current = confirmation.opener;
      else returningTo.current = confirmation.opener;
      setConfirmation(null);
    } else {
      returningTo.current = returnFocusTo ?? task?.opener ?? null;
      setTask(null);
    }
  }
  const releaseHistory = useOverlayHistoryLevel(level, () => closeInternal());
  function close(returnFocusTo?: HTMLElement | null) {
    releaseHistory();
    closeInternal(returnFocusTo);
  }
  useEffect(() => {
    if (confirmation) cancelRef.current?.focus();
    else if (resumeFocus.current?.isConnected) {
      resumeFocus.current.focus();
      resumeFocus.current = null;
    }
  }, [confirmation]);
  const notify = (message: string, tone?: NoticeTone, action?: NoticeAction) =>
    setNotices((previous) =>
      previous.some((notice) => notice.message === message)
        ? previous
        : [
            ...previous,
            { id: nextNotice.current++, message, tone, action },
          ].slice(-3),
    );
  return (
    <OverlayContext.Provider
      value={{
        open,
        close,
        dismiss: (key) => {
          if (shown.current.task?.key === key) close();
        },
        notify,
      }}
    >
      <Toast.Provider duration={NOTICE_MS} swipeDirection="right">
        <div className="overlay-layout">
          <div className="overlay-content">{children}</div>
          <Dialog.Root
            open={Boolean(current)}
            onOpenChange={(value) => {
              if (!value) close();
            }}
          >
            <Dialog.Portal>
              <Dialog.Overlay className="overlay-backdrop" />
              <Dialog.Content
                aria-modal="true"
                role={confirmation ? 'alertdialog' : 'dialog'}
                className={current ? shownClass : closingClass}
                onOpenAutoFocus={(event) => {
                  const search = document.querySelector<HTMLElement>(
                    '[role="dialog"] [data-initial-focus]',
                  );
                  if (confirmation) {
                    event.preventDefault();
                    cancelRef.current?.focus();
                  } else if (search) {
                    event.preventDefault();
                    search.focus();
                  }
                }}
                onCloseAutoFocus={(event) => {
                  event.preventDefault();
                  if (returningTo.current?.isConnected)
                    returningTo.current.focus();
                }}
              >
                <header
                  className={`dialog-header ${palette ? 'visually-hidden' : ''}`}
                >
                  <div>
                    <Dialog.Title className="dialog-title">
                      {current?.title}
                    </Dialog.Title>
                    <Dialog.Description className="dialog-description">
                      {current?.description}
                    </Dialog.Description>
                  </div>
                  {!confirmation && !palette && (
                    <Button
                      iconOnly
                      variant="ghost"
                      aria-label={
                        task?.kind === 'drawer'
                          ? 'Back to conversation'
                          : 'Close dialog'
                      }
                      onClick={() => close()}
                    >
                      {task?.kind === 'drawer' ? (
                        <PanelLeftClose size={20} aria-hidden />
                      ) : (
                        <X size={20} aria-hidden />
                      )}
                    </Button>
                  )}
                </header>
                <div
                  className="dialog-body"
                  hidden={Boolean(confirmation)}
                  inert={Boolean(confirmation)}
                >
                  {task?.content}
                </div>
                {confirmation && (
                  <div className="dialog-body">{confirmation.content}</div>
                )}
                {(confirmation || (task?.kind !== 'sheet' && !palette)) && (
                  <footer className="dialog-footer">
                    {confirmation ? (
                      <>
                        <Button ref={cancelRef} onClick={() => close()}>
                          Cancel
                        </Button>
                        <Button
                          variant="danger"
                          onClick={() => {
                            confirmation.onConfirm?.();
                            close();
                          }}
                        >
                          {confirmation.confirmLabel ?? 'Confirm'}
                        </Button>
                      </>
                    ) : (
                      <Button onClick={() => close()}>Close</Button>
                    )}
                  </footer>
                )}
              </Dialog.Content>
            </Dialog.Portal>
          </Dialog.Root>
          {/* Floats over the page below the top bar, never over the
              composer, and takes no room in the layout. */}
          <div
            className="notification-layer"
            hidden={Boolean(current) || notices.length === 0}
          >
            <Toast.Viewport className="toast-viewport" label="Notifications" />
          </div>
          {!current &&
            notices.map((notice) => (
              <Toast.Root
                className="toast"
                key={notice.id}
                data-tone={notice.tone}
                type={
                  notice.tone || notice.action ? 'foreground' : 'background'
                }
                duration={
                  notice.action
                    ? ACTION_NOTICE_MS
                    : notice.tone
                      ? TONED_NOTICE_MS
                      : NOTICE_MS
                }
                onOpenChange={(value) => {
                  if (!value)
                    setNotices((values) =>
                      values.filter((item) => item.id !== notice.id),
                    );
                }}
              >
                <Toast.Description>{notice.message}</Toast.Description>
                {notice.action && (
                  <Toast.Action altText={notice.action.label} asChild>
                    <Button
                      variant="ghost"
                      className="small"
                      onClick={() => {
                        const run = notice.action!.onAction;
                        setNotices((values) =>
                          values.filter((item) => item.id !== notice.id),
                        );
                        run();
                      }}
                    >
                      {notice.action.label}
                    </Button>
                  </Toast.Action>
                )}
                <Toast.Close asChild>
                  <Button
                    iconOnly
                    variant="ghost"
                    aria-label="Dismiss notification"
                  >
                    <X size={16} aria-hidden />
                  </Button>
                </Toast.Close>
              </Toast.Root>
            ))}
        </div>
      </Toast.Provider>
    </OverlayContext.Provider>
  );
}

type ModalTaskProps = {
  open: boolean;
  title: string;
  description: string;
  children: ReactNode;
  onOpenChange: (open: boolean) => void;
  ariaLabel?: string;
  kind?: 'dialog' | 'sheet';
  dismissible?: boolean;
  returnFocusTo?: HTMLElement | null;
  fallbackFocusTo?: HTMLElement | null;
  /** Extra class on the dialog, e.g. a wider task such as the workflow builder. */
  className?: string;
};

/** Declarative settings/setup task using the same Radix/back/focus contract. */
export function ModalTask({
  open,
  title,
  description,
  children,
  onOpenChange,
  ariaLabel,
  kind = 'dialog',
  dismissible = true,
  returnFocusTo,
  fallbackFocusTo,
  className = '',
}: ModalTaskProps) {
  const opener = useRef<HTMLElement | null>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const wasOpen = useRef(open);
  const focusTarget = () =>
    [returnFocusTo, opener.current, fallbackFocusTo].find(
      (target) => target?.isConnected,
    );
  const restoreFocus = () => {
    queueMicrotask(() => {
      focusTarget()?.focus();
    });
  };
  const restoreAfterClose = useEffectEvent(restoreFocus);
  useEffect(() => {
    if (wasOpen.current && !open) restoreAfterClose();
    wasOpen.current = open;
  }, [open]);
  const releaseHistory = useOverlayHistoryLevel(open ? 1 : 0, () => {
    if (dismissible) onOpenChange(false);
  });
  const close = () => {
    if (!dismissible) return;
    releaseHistory();
    onOpenChange(false);
  };
  return (
    <Dialog.Root
      open={open}
      onOpenChange={(value) => {
        if (!value) close();
      }}
    >
      <Dialog.Portal>
        <Dialog.Overlay className="overlay-backdrop" />
        <Dialog.Content
          ref={contentRef}
          className={`dialog shared-dialog-task ${kind === 'sheet' ? 'sheet' : ''} ${className}`}
          aria-label={ariaLabel}
          aria-modal="true"
          data-testid="shared-dialog-task"
          data-overlay-kind={kind}
          onOpenAutoFocus={(event) => {
            const active =
              document.activeElement instanceof HTMLElement
                ? document.activeElement
                : null;
            if (active && !contentRef.current?.contains(active))
              opener.current = active;
            const initial = contentRef.current?.querySelector<HTMLElement>(
              '[data-initial-focus]',
            );
            if (initial) {
              event.preventDefault();
              initial.focus();
            }
          }}
          onCloseAutoFocus={(event) => {
            const target = focusTarget();
            if (!target) return;
            event.preventDefault();
            target.focus();
          }}
        >
          <header className="dialog-header">
            <div>
              <Dialog.Title className="dialog-title">{title}</Dialog.Title>
              <Dialog.Description className="dialog-description">
                {description}
              </Dialog.Description>
            </div>
            <Button
              iconOnly
              variant="ghost"
              aria-label="Close dialog"
              disabled={!dismissible}
              onClick={close}
            >
              <X size={20} aria-hidden />
            </Button>
          </header>
          <div className="dialog-body">{children}</div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
type DrawerProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: string;
  children: ReactNode;
  /** Extra header icon actions shown before Close. */
  actions?: ReactNode;
  side?: 'right' | 'left';
  /**
   * Inspectors default to non-modal: the canvas stays interactive, focus moves
   * to the drawer heading and Escape or Close dismisses it.
   */
  modal?: boolean;
  /** Render inside a positioned container instead of the viewport edge. */
  container?: HTMLElement | null;
  closeLabel?: string;
  className?: string;
};

/** Side inspector for details (knowledge node, tool step, workflow run). */
export function Drawer({
  open,
  onOpenChange,
  title,
  description,
  children,
  actions,
  side = 'right',
  modal = false,
  container,
  closeLabel,
  className = '',
}: DrawerProps) {
  const heading = useRef<HTMLHeadingElement>(null);
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange} modal={modal}>
      <Dialog.Portal container={container ?? undefined}>
        {modal && <Dialog.Overlay className="overlay-backdrop" />}
        <Dialog.Content
          className={`drawer-panel ${className}`}
          data-side={side}
          data-contained={container ? 'true' : undefined}
          // Radix expects an explicit opt-out when no description renders.
          {...(description ? {} : { 'aria-describedby': undefined })}
          onOpenAutoFocus={(event) => {
            if (modal) return;
            event.preventDefault();
            heading.current?.focus({ preventScroll: true });
          }}
          onInteractOutside={(event) => {
            if (!modal) event.preventDefault();
          }}
        >
          <header className="drawer-header">
            <div className="drawer-heading">
              <Dialog.Title
                ref={heading}
                tabIndex={-1}
                className="drawer-title"
              >
                {title}
              </Dialog.Title>
              {description && (
                <Dialog.Description className="drawer-description">
                  {description}
                </Dialog.Description>
              )}
            </div>
            <div className="drawer-actions">
              {actions}
              <Dialog.Close asChild>
                <IconButton
                  size="sm"
                  label={closeLabel ?? `Close ${title}`}
                  shortcut="Escape"
                >
                  <X size={16} aria-hidden />
                </IconButton>
              </Dialog.Close>
            </div>
          </header>
          <div className="drawer-body">{children}</div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

const noNotify = () => {};

/** notify() where an OverlayProvider may be missing (then it does nothing). */
export function useNotify(): (
  message: string,
  tone?: NoticeTone,
  action?: NoticeAction,
) => void {
  return useContext(OverlayContext)?.notify ?? noNotify;
}

export function useOverlay() {
  const context = useContext(OverlayContext);
  if (!context) throw new Error('OverlayProvider is required');
  return context;
}
