import type { ReactNode } from 'react';
import {
  ArrowUpRight,
  CalendarClock,
  CalendarHeart,
  FileText,
  Mail,
  MessageSquare,
  PenLine,
  RefreshCw,
  Sparkles,
  Sunrise,
  Workflow,
  Wrench,
} from 'lucide-react';
import type {
  ProactiveEvidence,
  ProactiveMomentView,
  ProactiveSettingsView,
} from '../../api/types';
import { parseTimestamp } from '../../ui/format';

export type MomentKind = ProactiveMomentView['kind'];
export type ProactiveLevel = ProactiveSettingsView['level'];

/** The kinds in the person's words (the server's KIND_WORDS). */
export const KIND_WORDS: Record<MomentKind, string> = {
  follow_up: 'Follow-ups',
  meeting_prep: 'Meeting prep',
  brief: 'Morning brief',
  date: 'Dates that matter',
  change: 'Changes you care about',
  workflow_offer: 'Make it a workflow',
};

/** One of a kind, for a rule's words ("at most one follow-up a day"). */
export const KIND_ONE: Record<MomentKind, string> = {
  follow_up: 'follow-up',
  meeting_prep: 'meeting brief',
  brief: 'morning brief',
  date: 'date reminder',
  change: 'change',
  workflow_offer: 'workflow offer',
};

/** Each kind's tile: an icon and a tone from the chart series. */
export const KIND_TILES: Record<
  MomentKind,
  { tone: '2' | '3' | '4' | '5' | 'accent' | 'warning'; icon: ReactNode }
> = {
  follow_up: { tone: 'accent', icon: <Mail size={16} aria-hidden /> },
  meeting_prep: { tone: '4', icon: <CalendarClock size={16} aria-hidden /> },
  brief: { tone: '5', icon: <Sunrise size={16} aria-hidden /> },
  date: { tone: '2', icon: <CalendarHeart size={16} aria-hidden /> },
  change: { tone: 'warning', icon: <RefreshCw size={16} aria-hidden /> },
  workflow_offer: { tone: '3', icon: <Workflow size={16} aria-hidden /> },
};

/** The icon for an action the server offers (its label is the tooltip). */
export function actionIcon(id: string, size = 16): ReactNode {
  switch (id) {
    case 'open_draft':
      return <PenLine size={size} aria-hidden />;
    case 'open_brief':
      return <FileText size={size} aria-hidden />;
    case 'continue_in_chat':
      return <MessageSquare size={size} aria-hidden />;
    case 'make_workflow':
      return <Workflow size={size} aria-hidden />;
    case 'open_fix':
      return <Wrench size={size} aria-hidden />;
    case 'open_for_you':
      return <Sparkles size={size} aria-hidden />;
    default:
      return <ArrowUpRight size={size} aria-hidden />;
  }
}

/** What Row-Bot already prepared, in a few words, or nothing. */
export function preparedWords(moment: ProactiveMomentView): string {
  if (!moment.prepared_ref) return '';
  if (moment.actions.some((action) => action.id === 'open_draft'))
    return 'Reply drafted, not sent';
  if (moment.kind === 'brief') return '';
  return 'Brief ready';
}

/** "a meeting in 10 minutes": what's next in Now, for Home's lead line. */
const SOON_WORDS: Partial<Record<MomentKind, string>> = {
  meeting_prep: 'meeting',
  change: 'change',
};

/**
 * Home's one sentence when moments wait: "2 things for you · meeting in 10
 * minutes." Counts the live moments (the brief carries its own), and names
 * the soonest thing in Now when it has a time. "" when nothing waits.
 */
export function forYouLead(
  moments: readonly ProactiveMomentView[],
  now: Date,
  relative: (date: Date, now: Date) => string,
): string {
  const live = moments.filter(
    (moment) => moment.kind !== 'brief' && isLive(moment, now),
  );
  if (!live.length)
    return currentBrief(moments, now) ? 'Your morning brief is ready.' : '';
  const soon = live
    .filter((moment) => moment.group === 'now' && SOON_WORDS[moment.kind])
    .map((moment) => ({ moment, at: parseTimestamp(moment.starts_at ?? '') }))
    .filter(
      (item): item is { moment: ProactiveMomentView; at: Date } =>
        Boolean(item.at) && item.at!.getTime() > now.getTime(),
    )
    .sort((left, right) => left.at.getTime() - right.at.getTime())[0];
  const count = `${live.length} ${live.length === 1 ? 'thing' : 'things'} for you`;
  return soon
    ? `${count} · ${SOON_WORDS[soon.moment.kind]} ${relative(soon.at, now)}.`
    : `${count}.`;
}

/** Where a piece of evidence came from, in words. */
export const SOURCE_WORDS: Record<string, string> = {
  gmail: 'Gmail',
  outlook: 'Outlook',
  google_calendar: 'Google Calendar',
  outlook_calendar: 'Outlook Calendar',
  chats: 'Your chats',
  memory: 'Memory',
  workflows: 'Workflows',
  monitor: 'Monitor',
};

export const EVIDENCE_WORDS: Record<ProactiveEvidence['type'], string> = {
  event: 'Event',
  message: 'Email',
  thread: 'Email thread',
  memory: 'Memory',
  conversation: 'Chat',
  workflow_run: 'Workflow run',
  check: 'Check',
  ask: 'Repeated ask',
};

/** A moment still worth showing: open and not past its expiry. */
export function isLive(moment: ProactiveMomentView, now: Date): boolean {
  if (moment.state !== 'pending' && moment.state !== 'shown') return false;
  const expires = parseTimestamp(moment.expires_at);
  return !expires || expires.getTime() > now.getTime();
}

export type MomentGroup = {
  id: 'now' | 'later' | 'ready';
  title: string;
  moments: ProactiveMomentView[];
};

/** Within this of its time (or past it), a moment is "Now". */
const NOW_WINDOW_MS = 60 * 60_000;

const SERVER_GROUPS: Record<
  NonNullable<ProactiveMomentView['group']>,
  MomentGroup['id']
> = { now: 'now', later_today: 'later', ready: 'ready' };

/** When the thing a moment is about happens (a meeting's start), else its due time. */
export function momentTime(moment: ProactiveMomentView): Date | null {
  return parseTimestamp(moment.starts_at || moment.due_at);
}

/**
 * For you's groups, in order: Now (urgent, due within the hour or past
 * due), Later today (due later today), Ready when you are (no time, or another
 * day). The server decides each moment's group (so the morning brief's
 * carried items never jump); an older server's moments are grouped here. The
 * morning brief is its own card, outside the groups.
 */
export function groupMoments(
  moments: readonly ProactiveMomentView[],
  now: Date,
): MomentGroup[] {
  const groups: MomentGroup[] = [
    { id: 'now', title: 'Now', moments: [] },
    { id: 'later', title: 'Later today', moments: [] },
    { id: 'ready', title: 'Ready when you are', moments: [] },
  ];
  const [present, later, ready] = groups;
  const midnight = new Date(now);
  midnight.setHours(24, 0, 0, 0);
  for (const moment of moments) {
    if (moment.kind === 'brief' || !isLive(moment, now)) continue;
    if (moment.group) {
      groups
        .find((group) => group.id === SERVER_GROUPS[moment.group!])!
        .moments.push(moment);
      continue;
    }
    // A moment due when it was made has no time of its own (a follow-up).
    const made = parseTimestamp(moment.created_at)?.getTime() ?? 0;
    const dueAt = parseTimestamp(moment.due_at);
    const due = dueAt && dueAt.getTime() - made > 60_000 ? dueAt : null;
    if (
      moment.urgency === 'urgent' ||
      (due && due.getTime() - now.getTime() <= NOW_WINDOW_MS)
    )
      present.moments.push(moment);
    else if (due && due.getTime() < midnight.getTime())
      later.moments.push(moment);
    else ready.moments.push(moment);
  }
  const at = (moment: ProactiveMomentView) =>
    momentTime(moment)?.getTime() ?? Infinity;
  present.moments.sort(
    (left, right) =>
      Number(right.urgency === 'urgent') - Number(left.urgency === 'urgent') ||
      at(left) - at(right),
  );
  later.moments.sort((left, right) => at(left) - at(right));
  const arrived = (moment: ProactiveMomentView) =>
    parseTimestamp(moment.created_at)?.getTime() ?? 0;
  ready.moments.sort((left, right) => arrived(right) - arrived(left));
  return groups.filter((group) => group.moments.length > 0);
}

/** The newest live morning brief, if any. */
export function currentBrief(
  moments: readonly ProactiveMomentView[],
  now: Date,
): ProactiveMomentView | null {
  return (
    moments
      .filter((moment) => moment.kind === 'brief' && isLive(moment, now))
      .sort(
        (left, right) =>
          (parseTimestamp(right.created_at)?.getTime() ?? 0) -
          (parseTimestamp(left.created_at)?.getTime() ?? 0),
      )[0] ?? null
  );
}

/** The brief's "- item" lines, without its closing "That's everything" line. */
export function briefLines(body: string): string[] {
  return body
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line.startsWith('- '))
    .map((line) => line.slice(2).trim())
    .filter(Boolean);
}

/** "around 2 PM": an hour in the person's clock. */
export function hourWords(hour: number): string {
  return new Intl.DateTimeFormat(undefined, { hour: 'numeric' }).format(
    new Date(2000, 0, 1, hour, 0),
  );
}

/** "Quiet hours 22:00–07:30" in this device's clock. */
export function clockWords(value: string): string {
  const match = /^(\d{2}):(\d{2})$/.exec(value);
  if (!match) return value;
  return new Intl.DateTimeFormat(undefined, {
    hour: 'numeric',
    minute: '2-digit',
  }).format(new Date(2000, 0, 1, Number(match[1]), Number(match[2])));
}

/** Each level in one line, and its daily number (the copy deck). */
export const LEVEL_LINES: Record<
  ProactiveLevel,
  { line: string; daily: string }
> = {
  off: {
    line: 'Row-Bot doesn’t check anything or speak first. Home shows Today as before.',
    daily: '',
  },
  quiet: {
    line: 'Things wait on Home. Buddy speaks only for what’s about to happen, and once in the morning.',
    daily: 'Up to 3 a day',
  },
  balanced: {
    line: 'Buddy speaks at good moments. Your phone hears only when you’re away.',
    daily: 'Up to 6 a day',
  },
  eager: {
    line: 'Row-Bot brings up more, and earlier.',
    daily: 'Up to 12 a day',
  },
};

/** An example day at each level (shown until a day of real checks exists). */
export const EXAMPLE_DAY: Record<
  ProactiveLevel,
  { time: string; line: string; where: 'bubble' | 'home' | 'phone' }[]
> = {
  off: [],
  quiet: [
    { time: '08:41', line: 'Morning brief', where: 'bubble' },
    { time: '09:50', line: 'Design review brief', where: 'bubble' },
    { time: '', line: 'Priya hasn’t replied (4 days)', where: 'home' },
    { time: '', line: 'Dad’s birthday is Sunday', where: 'home' },
  ],
  balanced: [
    { time: '08:41', line: 'Morning brief', where: 'bubble' },
    { time: '09:50', line: 'Design review brief', where: 'bubble' },
    { time: '11:02', line: 'Priya hasn’t replied (4 days)', where: 'bubble' },
    { time: '15:10', line: 'Your 1:1 with Ana moved', where: 'phone' },
    { time: '', line: 'Dad’s birthday is Sunday', where: 'home' },
  ],
  eager: [
    { time: '08:41', line: 'Morning brief', where: 'bubble' },
    { time: '09:50', line: 'Design review brief', where: 'bubble' },
    { time: '11:02', line: 'Priya hasn’t replied (4 days)', where: 'bubble' },
    { time: '13:30', line: 'Dad’s birthday is Sunday', where: 'bubble' },
    { time: '15:10', line: 'Your 1:1 with Ana moved', where: 'phone' },
    {
      time: '16:00',
      line: 'Make the sales summary a workflow?',
      where: 'bubble',
    },
  ],
};

/** The one plain privacy line of the introduction and the page. */
export const PRIVACY_LINE =
  'Watching for changes happens on this computer; to decide what’s worth your attention, short summaries go to your main model.';
