import { useEffect, useState, type MouseEvent } from 'react';
import { Link } from 'react-router-dom';
import * as Popover from '@radix-ui/react-popover';
import { ArrowUpCircle, ShieldAlert, TriangleAlert } from 'lucide-react';
import type { AttentionSnapshot, PendingApprovalPage } from '../../api/types';
import { Hint } from '../../ui/primitives';
import { PendingApprovalList } from './InPlaceApproval';
import { usePendingApprovals } from './pending-approvals';

const REMINDER_KEY = 'row-bot.update-reminder.v1';
const REMINDER_EVENT = 'row-bot:update-reminder';
const REMIND_AFTER_MS = 24 * 60 * 60 * 1000;
const READ_EVERY_MS = 60_000;

/**
 * "Remind me later" for one update version on this device: the indicator
 * leaves it out for a day (Settings › Updates still shows it).
 */
export function remindLaterAbout(version: string, now = Date.now()) {
  try {
    localStorage.setItem(
      REMINDER_KEY,
      JSON.stringify({ version, until: now + REMIND_AFTER_MS }),
    );
  } catch {
    // Private windows may refuse storage; the reminder then lasts this page.
  }
  window.dispatchEvent(new Event(REMINDER_EVENT));
}

export function updateReminderPending(version: string, now = Date.now()) {
  try {
    const saved = JSON.parse(localStorage.getItem(REMINDER_KEY) ?? 'null') as {
      version?: unknown;
      until?: unknown;
    } | null;
    return (
      saved?.version === version &&
      typeof saved.until === 'number' &&
      saved.until > now
    );
  } catch {
    return false;
  }
}

/**
 * One sidebar-footer indicator (NiceGUI parity rows 12 and 13). It appears
 * only when something needs attention: waiting approvals open a small list
 * with Approve / Deny in place (B255), problems open Monitor, an update
 * opens Updates. Quiet when everything is healthy; no permanent health dot.
 */
export default function AttentionIndicator({
  load,
  loadApprovals,
  compact = false,
  onNavigate,
}: {
  load?: (signal?: AbortSignal) => Promise<AttentionSnapshot>;
  loadApprovals?: (signal?: AbortSignal) => Promise<PendingApprovalPage>;
  /** The collapsed rail: an icon with its words as the name. */
  compact?: boolean;
  onNavigate?: (to: string) => (event: MouseEvent<HTMLAnchorElement>) => void;
}) {
  const [snapshot, setSnapshot] = useState<AttentionSnapshot | null>(null);
  const approvals = usePendingApprovals(loadApprovals);
  const [listOpen, setListOpen] = useState(false);
  const [, setReminded] = useState(0);
  useEffect(() => {
    if (!load) return;
    let abort = new AbortController();
    const read = () => {
      if (document.visibilityState === 'hidden') return;
      abort.abort();
      abort = new AbortController();
      const current = abort;
      load(current.signal).then(
        (value) => {
          if (!current.signal.aborted) setSnapshot(value);
        },
        () => undefined,
      );
    };
    read();
    const timer = window.setInterval(read, READ_EVERY_MS);
    document.addEventListener('visibilitychange', read);
    return () => {
      abort.abort();
      window.clearInterval(timer);
      document.removeEventListener('visibilitychange', read);
    };
  }, [load]);
  useEffect(() => {
    const changed = () => setReminded((value) => value + 1);
    window.addEventListener(REMINDER_EVENT, changed);
    window.addEventListener('storage', changed);
    return () => {
      window.removeEventListener(REMINDER_EVENT, changed);
      window.removeEventListener('storage', changed);
    };
  }, []);
  const problems = snapshot?.problems ?? [];
  const update =
    snapshot?.update && !updateReminderPending(snapshot.update.version)
      ? snapshot.update
      : null;
  const waiting = approvals.page?.items ?? [];
  const waitingTotal = approvals.page?.total ?? 0;
  // The list closed with its last approval; the next one doesn't reopen it.
  if (!waitingTotal && listOpen) setListOpen(false);
  if (waitingTotal) {
    const words = `${waitingTotal} ${waitingTotal === 1 ? 'approval is' : 'approvals are'} waiting`;
    const problemWords = `${problems.length} ${problems.length === 1 ? 'thing needs' : 'things need'} attention`;
    const close = (to: string) => (event: MouseEvent<HTMLAnchorElement>) => {
      setListOpen(false);
      onNavigate?.(to)(event);
    };
    return (
      <Popover.Root open={listOpen} onOpenChange={setListOpen}>
        <Hint label={waiting.map((item) => item.title).join(' · ')}>
          <Popover.Trigger asChild>
            <button
              type="button"
              className={`nav-attention${compact ? ' is-compact button ghost icon-button icon-action icon-action-md' : ''}`}
              data-tone="warning"
              aria-label={`${words}. Review`}
            >
              <ShieldAlert size={compact ? 18 : 15} aria-hidden />
              {compact ? (
                <span className="nav-attention-count" aria-hidden>
                  {waitingTotal}
                </span>
              ) : (
                <span>{words}</span>
              )}
            </button>
          </Popover.Trigger>
        </Hint>
        <Popover.Portal>
          <Popover.Content
            className="popover surface-effect pending-approvals-popover"
            side="top"
            align="start"
            sideOffset={8}
            collisionPadding={12}
            aria-label="Waiting for your approval"
          >
            <strong>Waiting for your approval</strong>
            <PendingApprovalList
              items={waiting}
              onChanged={approvals.refresh}
              onNavigate={close}
            />
            {waitingTotal > waiting.length && (
              <small>
                The {waiting.length} newest of {waitingTotal} are shown.
              </small>
            )}
            {problems.length > 0 && (
              <Link
                className="button ghost small"
                to="/?tab=monitor"
                onClick={close('/?tab=monitor')}
              >
                <TriangleAlert size={15} aria-hidden />
                {problemWords}
              </Link>
            )}
          </Popover.Content>
        </Popover.Portal>
      </Popover.Root>
    );
  }
  if (!problems.length && !update) return null;
  const to = problems.length ? '/?tab=monitor' : '/settings/updates';
  const words = problems.length
    ? `${problems.length} ${problems.length === 1 ? 'thing needs' : 'things need'} attention`
    : `Update to ${update!.version} available`;
  const detail = problems.length
    ? problems.map((problem) => problem.title).join(' · ')
    : 'Open Updates to install it or be reminded later.';
  return (
    <Hint label={detail}>
      <Link
        className={`nav-attention${compact ? ' is-compact button ghost icon-button icon-action icon-action-md' : ''}`}
        data-tone={problems.length ? 'warning' : 'info'}
        to={to}
        aria-label={`${words}. ${problems.length ? 'Open Monitor' : 'Open Updates'}`}
        onClick={onNavigate?.(to)}
      >
        {problems.length ? (
          <TriangleAlert size={compact ? 18 : 15} aria-hidden />
        ) : (
          <ArrowUpCircle size={compact ? 18 : 15} aria-hidden />
        )}
        {!compact && <span>{words}</span>}
      </Link>
    </Hint>
  );
}
