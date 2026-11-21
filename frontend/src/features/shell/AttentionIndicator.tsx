import { useEffect, useState, type MouseEvent } from 'react';
import { Link } from 'react-router-dom';
import * as Popover from '@radix-ui/react-popover';
import {
  ArrowUpCircle,
  RefreshCw,
  ShieldAlert,
  TriangleAlert,
} from 'lucide-react';
import type {
  AttentionSnapshot,
  ConversationView,
  PendingApprovalPage,
} from '../../api/types';
import { Hint } from '../../ui/primitives';
import { PendingApprovalList } from './InPlaceApproval';
import { conversationsWaiting, needsCount, useAttention } from './needs';
import { usePendingApprovals } from './pending-approvals';

const REMINDER_KEY = 'row-bot.update-reminder.v1';
const REMINDER_EVENT = 'row-bot:update-reminder';
const REMIND_AFTER_MS = 24 * 60 * 60 * 1000;

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
 * One sidebar indicator (NiceGUI parity rows 12 and 13). It appears only
 * when something needs the person, with the same number Home's Needs you
 * shows (B383): waiting approvals open a small list with Approve / Deny in
 * place (B255), anything else opens Home, where each has its fix; an update
 * opens Updates ("Restart to update" once it is downloaded and ready). Quiet
 * when everything is healthy; no permanent health dot.
 */
export default function AttentionIndicator({
  load,
  loadApprovals,
  conversations = [],
  compact = false,
  onNavigate,
}: {
  load?: (signal?: AbortSignal) => Promise<AttentionSnapshot>;
  loadApprovals?: (signal?: AbortSignal) => Promise<PendingApprovalPage>;
  /** The listed conversations: one waiting on the person counts too. */
  conversations?: readonly ConversationView[];
  /** The collapsed rail: an icon with its words as the name. */
  compact?: boolean;
  onNavigate?: (to: string) => (event: MouseEvent<HTMLAnchorElement>) => void;
}) {
  const snapshot = useAttention(load).value;
  const approvals = usePendingApprovals(loadApprovals);
  const [listOpen, setListOpen] = useState(false);
  const [, setReminded] = useState(0);
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
  const total = needsCount({
    approvals: approvals.page,
    problems,
    conversations,
  });
  const others = total - waitingTotal;
  const otherTitles = [
    ...problems.map((problem) => problem.title),
    ...conversationsWaiting(conversations, waiting).map(
      (row) => row.title || 'Untitled conversation',
    ),
  ];
  // The list closed with its last approval; the next one doesn't reopen it.
  if (!waitingTotal && listOpen) setListOpen(false);
  // An update keeps its own link beside approvals and problems (B364): "Restart to update" never hides.
  const updateLink = update ? (
    <UpdateLink update={update} compact={compact} onNavigate={onNavigate} />
  ) : null;
  // Row-Bot's questions (N4) count as things that need the person, in their own words.
  const asking = waiting.filter((item) => item.kind === 'question').length;
  const waitingWords = !asking
    ? `${waitingTotal} ${waitingTotal === 1 ? 'approval is' : 'approvals are'} waiting`
    : asking === waitingTotal
      ? waitingTotal === 1
        ? 'Row-Bot has a question'
        : `${waitingTotal} questions are waiting`
      : `${waitingTotal} things need you`;
  const words = others
    ? `${total} ${total === 1 ? 'thing needs' : 'things need'} you`
    : waitingWords;
  const listTitle = asking ? 'Waiting for you' : 'Waiting for your approval';
  if (waitingTotal) {
    const otherWords = `${others} more ${others === 1 ? 'needs' : 'need'} you`;
    const close = (to: string) => (event: MouseEvent<HTMLAnchorElement>) => {
      setListOpen(false);
      onNavigate?.(to)(event);
    };
    return (
      <>
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
                    {total}
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
              aria-label={listTitle}
            >
              <strong>{listTitle}</strong>
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
              {others > 0 && (
                <Link
                  className="button ghost small"
                  to="/"
                  onClick={close('/')}
                >
                  <TriangleAlert size={15} aria-hidden />
                  {otherWords}
                </Link>
              )}
            </Popover.Content>
          </Popover.Portal>
        </Popover.Root>
        {updateLink}
      </>
    );
  }
  if (!total) return updateLink;
  return (
    <>
      <Hint label={otherTitles.join(' · ')}>
        <Link
          className={`nav-attention${compact ? ' is-compact button ghost icon-button icon-action icon-action-md' : ''}`}
          data-tone="warning"
          to="/"
          aria-label={`${words}. Open Home`}
          onClick={onNavigate?.('/')}
        >
          <TriangleAlert size={compact ? 18 : 15} aria-hidden />
          {compact ? (
            <span className="nav-attention-count" aria-hidden>
              {total}
            </span>
          ) : (
            <span>{words}</span>
          )}
        </Link>
      </Hint>
      {updateLink}
    </>
  );
}

/** The update, downloaded and ready ("Restart to update") or available; opens Updates. */
export function UpdateLink({
  update,
  compact,
  onNavigate,
}: {
  update: NonNullable<AttentionSnapshot['update']>;
  compact: boolean;
  onNavigate?: (to: string) => (event: MouseEvent<HTMLAnchorElement>) => void;
}) {
  const to = '/settings/updates';
  const words = update.ready
    ? 'Restart to update'
    : `Update to ${update.version} available`;
  const detail = update.ready
    ? `Version ${update.version} is ready. Restart to install it, or it installs when you quit.`
    : 'Open Updates to install it or be reminded later.';
  return (
    <Hint label={detail}>
      <Link
        className={`nav-attention${compact ? ' is-compact button ghost icon-button icon-action icon-action-md' : ''}`}
        data-tone="info"
        to={to}
        aria-label={`${words}${update.ready ? ` to ${update.version}` : ''}. Open Updates`}
        onClick={onNavigate?.(to)}
      >
        {update.ready ? (
          <RefreshCw size={compact ? 18 : 15} aria-hidden />
        ) : (
          <ArrowUpCircle size={compact ? 18 : 15} aria-hidden />
        )}
        {!compact && <span>{words}</span>}
      </Link>
    </Hint>
  );
}
