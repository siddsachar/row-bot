import { useEffect, useId, useMemo, useState, type FormEvent } from 'react';
import { createPortal } from 'react-dom';
import { useNavigate } from 'react-router-dom';
import { SendHorizontal } from 'lucide-react';
import type { ClientController } from '../../api/controller';
import type { CompanySharesView } from '../../api/types';
import { clientError } from '../../api/errors';
import { useDialogFooter } from '../../ui/dialog-footer';
import { useOptionalOverlay } from '../../ui/overlays';
import { Button, Field, Input, Select, Skeleton } from '../../ui/primitives';
import {
  chatNoteWords,
  disconnectedWords,
  lockdownWords,
  shareReviewWords,
  shareSentWords,
  unreachableWords,
} from './words';
import './company.css';

export type ShareController = Pick<
  ClientController,
  'company' | 'companyShares' | 'shareToCompany'
>;

/** B4a's limits on a share. */
export const MAX_TITLE = 200;
export const MAX_TEXT = 8000;
export const MAX_NOTE = 500;

/** How long a read of whether sharing is on is reused by every message. */
const FRESH_MS = 30_000;

type Seen = { at: number; allowed: boolean; reading?: Promise<boolean> };
const seen = new WeakMap<object, Seen>();

/** Whether this Row-Bot is joined and its company lets the person share (reading the state contacts nothing). */
async function readAllowed(controller: ShareController): Promise<boolean> {
  try {
    const view = await controller.company('status');
    return (
      (view.state === 'joined' || view.state === 'unreachable') &&
      view.limits?.share_to_company === true
    );
  } catch {
    return false;
  }
}

/** One read for every message on screen, again after a while. */
function allowedNow(controller: ShareController): Promise<boolean> {
  const known = seen.get(controller);
  if (known?.reading) return known.reading;
  if (known && Date.now() - known.at < FRESH_MS)
    return Promise.resolve(known.allowed);
  const reading = readAllowed(controller).then((allowed) => {
    seen.set(controller, { at: Date.now(), allowed });
    return allowed;
  });
  seen.set(controller, {
    at: known?.at ?? 0,
    allowed: known?.allowed ?? false,
    reading,
  });
  return reading;
}

/** A share's first title: the text's first line, without Markdown's marks. */
export function titleFrom(text: string): string {
  const line =
    text
      .split('\n')
      .map((part) => part.trim())
      .find(Boolean) ?? '';
  const plain = line
    .replace(/^(#+|>|[-*+]|\d+[.)])\s+/, '')
    .replace(/[*_`]/g, '')
    .replace(/\s+/g, ' ')
    .trim();
  return Array.from(plain).slice(0, 80).join('').trim();
}

function length(value: string): number {
  return Array.from(value.trim()).length;
}

/**
 * Share to company (Row-Bot for Teams, B4b): the message action's opener,
 * or null when it isn't offered: no company joined, the company turned
 * sharing off (or Row-Bot is locked down), or outside the app.
 */
export function useShareToCompany(
  controller: ShareController | null | undefined,
): ((text: string) => void) | null {
  const overlay = useOptionalOverlay();
  const usable =
    controller && typeof controller.company === 'function' ? controller : null;
  const [allowed, setAllowed] = useState(
    () => (usable && seen.get(usable)?.allowed) ?? false,
  );
  useEffect(() => {
    if (!usable) return;
    let current = true;
    void allowedNow(usable).then((value) => {
      if (current) setAllowed(value);
    });
    return () => {
      current = false;
    };
  }, [usable]);
  return useMemo(() => {
    if (!allowed || !overlay || !usable) return null;
    return (text: string) =>
      overlay.open({
        kind: 'dialog',
        className: 'company-share-dialog',
        title: 'Share to company',
        description: 'Send this to your company’s brain.',
        content: (
          <ShareDialog
            controller={usable}
            text={text}
            onClose={() => overlay.close()}
          />
        ),
      });
  }, [allowed, overlay, usable]);
}

/**
 * The dialog: who sees it first, then the space (the ones the person may
 * read), a title, the text (editable, up to 8,000 characters) and a note.
 * Send for review puts it in the space's review queue.
 */
export function ShareDialog({
  controller,
  text,
  onClose,
  now,
}: {
  controller: ShareController;
  text: string;
  onClose: () => void;
  /** The day the note names; today when not given. */
  now?: Date;
}) {
  const navigate = useNavigate();
  const formId = useId();
  const notify = useOptionalOverlay()?.notify;
  const [view, setView] = useState<CompanySharesView | null>(null);
  const [loadError, setLoadError] = useState('');
  const [space, setSpace] = useState('');
  const [title, setTitle] = useState(() => titleFrom(text));
  const [body, setBody] = useState(text);
  const [note, setNote] = useState(() => chatNoteWords(now ?? new Date()));
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState('');

  useEffect(() => {
    let current = true;
    const read = async () => {
      try {
        const next = await controller.companyShares('pending');
        if (!current) return;
        setView(next);
        setSpace((chosen) => chosen || next.spaces[0]?.id || '');
      } catch (cause) {
        if (current) setLoadError(clientError(cause).message);
      }
    };
    void read();
    return () => {
      current = false;
    };
  }, [controller]);

  const ready = view?.state === 'ready' && view.allowed && view.spaces.length;
  const footer = useDialogFooter(Boolean(ready));
  const company = view?.company ?? 'your company';
  const spaceName =
    view?.spaces.find((item) => item.id === space)?.name ?? 'this space';
  const count = length(body);
  const over = count - MAX_TEXT;
  const canSend =
    !busy &&
    Boolean(space) &&
    Boolean(title.trim()) &&
    count > 0 &&
    over <= 0 &&
    Array.from(note.trim()).length <= MAX_NOTE;

  async function send(event?: FormEvent) {
    event?.preventDefault();
    if (!canSend) return;
    setBusy(true);
    setProblem('');
    const name = spaceName;
    try {
      const next = await controller.shareToCompany(space, {
        title: title.trim(),
        text: body.trim(),
        source_note: note.trim(),
      });
      if (next.state === 'ready' && next.outcome === 'sent') {
        onClose();
        notify?.(shareSentWords(name), undefined, {
          label: 'Your shares',
          onAction: () => navigate('/settings/company#your-shares'),
        });
        return;
      }
      setProblem(
        next.state === 'ready'
          ? (next.words ?? 'It wasn’t sent. Try again.')
          : stateWords(next),
      );
    } catch (cause) {
      setProblem(clientError(cause).message);
    } finally {
      setBusy(false);
    }
  }

  if (!view)
    return loadError ? (
      <p role="alert" className="company-share-state">
        {loadError}
      </p>
    ) : (
      <Skeleton label="Loading your company’s spaces" />
    );
  if (view.state !== 'ready')
    return <p className="company-share-state">{stateWords(view)}</p>;
  if (!view.allowed)
    return (
      <p className="company-share-state">
        Your company turned off Share to company.
      </p>
    );
  if (!view.spaces.length)
    return (
      <p className="company-share-state">
        {`You don’t read any of ${company}’s spaces yet, so there’s nowhere to share this.`}
      </p>
    );

  const actions = (
    <>
      <Button onClick={onClose}>Cancel</Button>
      <Button type="submit" form={formId} variant="primary" disabled={!canSend}>
        <SendHorizontal size={14} aria-hidden /> Send for review
      </Button>
    </>
  );
  return (
    <form
      id={formId}
      className="company-share-form"
      onSubmit={(event) => void send(event)}
      noValidate
    >
      <p className="company-share-review">{shareReviewWords(spaceName)}</p>
      <Field label="Space">
        <Select
          value={space}
          disabled={busy}
          onChange={(event) => setSpace(event.currentTarget.value)}
        >
          {view.spaces.map((item) => (
            <option key={item.id} value={item.id}>
              {item.name}
            </option>
          ))}
        </Select>
      </Field>
      <Field label="Title">
        <Input
          value={title}
          maxLength={MAX_TITLE}
          disabled={busy}
          data-initial-focus
          onChange={(event) => setTitle(event.target.value)}
        />
      </Field>
      <Field
        label="Text"
        hint={
          over > 0
            ? `Shorten this by ${over.toLocaleString()} character${over === 1 ? '' : 's'} to send it.`
            : `${count.toLocaleString()} of ${MAX_TEXT.toLocaleString()} characters`
        }
      >
        <textarea
          className="input company-share-text"
          rows={8}
          value={body}
          disabled={busy}
          aria-invalid={over > 0 ? true : undefined}
          onChange={(event) => setBody(event.target.value)}
        />
      </Field>
      <Field label="Note" hint="Optional: where it came from.">
        <Input
          value={note}
          maxLength={MAX_NOTE}
          disabled={busy}
          onChange={(event) => setNote(event.target.value)}
        />
      </Field>
      {problem && (
        <p role="alert" className="company-share-problem">
          {problem}
        </p>
      )}
      {footer ? (
        createPortal(actions, footer)
      ) : (
        <div className="button-row">{actions}</div>
      )}
    </form>
  );
}

/** Why the company can't be asked now (B8's states). */
function stateWords(view: CompanySharesView): string {
  const company = view.company ?? 'your company';
  if (view.state === 'disconnected') return disconnectedWords(company);
  if (view.state === 'locked_down')
    return lockdownWords({ company, policy_reason: null });
  return unreachableWords(company, view.reason);
}
