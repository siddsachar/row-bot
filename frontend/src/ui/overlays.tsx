import {
  createContext,
  useContext,
  useEffect,
  useEffectEvent,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import * as Dialog from '@radix-ui/react-dialog';
import * as Toast from '@radix-ui/react-toast';
import { X } from 'lucide-react';
import { Button } from './primitives';

type Overlay = {
  key?: string;
  title: string;
  description: string;
  content?: ReactNode;
  kind?: 'dialog' | 'sheet' | 'drawer' | 'alert';
  confirmLabel?: string;
  onConfirm?: () => void;
  returnFocusTo?: HTMLElement | null;
};
type Task = Overlay & { opener: HTMLElement | null };
type Notice = { id: number; message: string };
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

const OverlayContext = createContext<{
  open: (overlay: Overlay) => void;
  close: (returnFocusTo?: HTMLElement | null) => void;
  dismiss: (key: string) => void;
  notify: (message: string) => void;
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
  const notify = (message: string) =>
    setNotices((previous) =>
      previous.some((notice) => notice.message === message)
        ? previous
        : [...previous, { id: nextNotice.current++, message }].slice(-3),
    );
  return (
    <OverlayContext.Provider
      value={{
        open,
        close,
        dismiss: (key) => {
          if (task?.key === key) close();
        },
        notify,
      }}
    >
      <Toast.Provider duration={6000} swipeDirection="right">
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
                className={`dialog ${confirmation ? 'alert-dialog' : task?.kind === 'sheet' ? 'sheet' : task?.kind === 'drawer' ? 'drawer' : ''}`}
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
                <header className="dialog-header">
                  <div>
                    <Dialog.Title className="dialog-title">
                      {current?.title}
                    </Dialog.Title>
                    <Dialog.Description className="dialog-description">
                      {current?.description}
                    </Dialog.Description>
                  </div>
                  {!confirmation && (
                    <Button
                      iconOnly={task?.kind !== 'drawer'}
                      variant="ghost"
                      aria-label={
                        task?.kind === 'drawer'
                          ? 'Back to conversation'
                          : 'Close dialog'
                      }
                      onClick={() => close()}
                    >
                      {task?.kind === 'drawer' ? (
                        'Back'
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
                {(confirmation || task?.kind !== 'sheet') && (
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
          <div
            className="notification-footer"
            hidden={Boolean(current) || notices.length === 0}
          >
            <Toast.Viewport className="toast-viewport" label="Notifications" />
          </div>
          {!current &&
            notices.map((notice) => (
              <Toast.Root
                className="toast"
                key={notice.id}
                onOpenChange={(value) => {
                  if (!value)
                    setNotices((values) =>
                      values.filter((item) => item.id !== notice.id),
                    );
                }}
              >
                <Toast.Description>{notice.message}</Toast.Description>
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
}: ModalTaskProps) {
  const opener = useRef<HTMLElement | null>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const wasOpen = useRef(open);
  const restoreFocus = () => {
    const target = returnFocusTo ?? opener.current;
    queueMicrotask(() => {
      if (target?.isConnected) target.focus();
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
          className={`dialog shared-dialog-task ${kind === 'sheet' ? 'sheet' : ''}`}
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
            const target = returnFocusTo ?? opener.current;
            if (!target?.isConnected) return;
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
export function useOverlay() {
  const context = useContext(OverlayContext);
  if (!context) throw new Error('OverlayProvider is required');
  return context;
}
