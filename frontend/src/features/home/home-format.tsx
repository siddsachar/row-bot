import type { Tone } from '../../ui/primitives';
import { absoluteTime, parseTimestamp } from '../../ui/format';

// The shared relative time lives in ui/ so Settings can use it too (U59).
export { When } from '../../ui/When';

const WEEKDAYS: Record<string, string> = {
  mon: 'Monday',
  tue: 'Tuesday',
  wed: 'Wednesday',
  thu: 'Thursday',
  fri: 'Friday',
  sat: 'Saturday',
  sun: 'Sunday',
};
// The scheduler (APScheduler) numbers weekdays from Monday = 0, unlike
// standard cron. Numeric days are described as they actually fire.
const SCHEDULER_DAYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'];

export function clockTime(hour: number, minute: number) {
  return new Intl.DateTimeFormat(undefined, {
    hour: 'numeric',
    minute: '2-digit',
  }).format(new Date(2000, 0, 1, hour, minute));
}

function dayKey(value: string) {
  const key = value.trim().toLowerCase();
  if (/^[0-6]$/.test(key)) return SCHEDULER_DAYS[Number(key)];
  const short = key.slice(0, 3);
  return SCHEDULER_DAYS.includes(short) ? short : null;
}

function weekday(value: string) {
  const key = dayKey(value);
  return key ? WEEKDAYS[key] : undefined;
}

/** Expand a day-of-week field ("mon-fri", "mon,wed", "0-4") to day keys. */
export function cronDays(field: string): string[] | null {
  const days = new Set<string>();
  for (const part of field.toLowerCase().split(',')) {
    const range = part.split('-');
    if (range.length === 1) {
      const key = dayKey(part);
      if (!key) return null;
      days.add(key);
    } else if (range.length === 2) {
      const start = dayKey(range[0]);
      const end = dayKey(range[1]);
      if (!start || !end) return null;
      let index = SCHEDULER_DAYS.indexOf(start);
      const stop = SCHEDULER_DAYS.indexOf(end);
      for (let guard = 0; guard < 7; guard += 1) {
        days.add(SCHEDULER_DAYS[index]);
        if (index === stop) break;
        index = (index + 1) % 7;
      }
    } else return null;
  }
  return SCHEDULER_DAYS.filter((day) => days.has(day));
}

/** Describe the common 5-field cron shapes; null when it is not one. */
function cronWords(expression: string): string | null {
  const fields = expression.trim().split(/\s+/);
  if (fields.length !== 5) return null;
  const [minute, hour, dom, month, dow] = fields;
  if (!/^\d{1,2}$/.test(minute) || Number(minute) > 59) return null;
  if (dom !== '*' || month !== '*') return null;
  if (hour === '*') {
    return dow === '*' ? `Every hour at :${minute.padStart(2, '0')}` : null;
  }
  if (!/^\d{1,2}$/.test(hour) || Number(hour) > 23) return null;
  const at = clockTime(Number(hour), Number(minute));
  if (dow === '*') return `Every day at ${at}`;
  const days = cronDays(dow);
  if (!days?.length) return null;
  if (days.length === 7) return `Every day at ${at}`;
  if (days.join() === 'mon,tue,wed,thu,fri') return `Weekdays at ${at}`;
  if (days.join() === 'sat,sun') return `Weekends at ${at}`;
  return `Every ${days.map((day) => WEEKDAYS[day]).join(', ')} at ${at}`;
}

/** A saved schedule ("daily:09:00", "cron:0 9 * * 1", …) in words. */
export function scheduleWords(
  schedule: string | null | undefined,
  at: string | null | undefined,
): string {
  const value = schedule?.trim();
  if (value) {
    const daily = /^daily:(\d{1,2}):(\d{2})$/i.exec(value);
    if (daily && Number(daily[1]) < 24 && Number(daily[2]) < 60)
      return `Every day at ${clockTime(Number(daily[1]), Number(daily[2]))}`;
    const weekly = /^weekly:([a-z]+):(\d{1,2}):(\d{2})$/i.exec(value);
    if (weekly && weekday(weekly[1]) && Number(weekly[2]) < 24)
      return `Every ${weekday(weekly[1])} at ${clockTime(Number(weekly[2]), Number(weekly[3]))}`;
    const interval = /^interval(_minutes)?:([0-9]+(?:\.[0-9]+)?)$/i.exec(value);
    if (interval && Number(interval[2]) > 0) {
      const count = Number(interval[2]);
      if (!interval[1] && count < 1) {
        const minutes = Math.round(count * 60);
        return `Every ${minutes} minute${minutes === 1 ? '' : 's'}`;
      }
      const unit = interval[1] ? 'minute' : 'hour';
      return count === 1 ? `Every ${unit}` : `Every ${count} ${unit}s`;
    }
    const cron = /^cron:(.+)$/i.exec(value);
    if (cron) return cronWords(cron[1]) ?? 'Custom schedule';
    return 'Custom schedule';
  }
  if (at) {
    const date = parseTimestamp(at);
    return date ? `Once · ${absoluteTime(date)}` : 'Invalid one-time schedule';
  }
  return 'Manual';
}

export type RunStatusView = { label: string; tone: Tone };

const RUN_STATUS: Record<string, RunStatusView> = {
  completed: { label: 'Completed', tone: 'success' },
  completed_delivery_failed: { label: 'Delivery failed', tone: 'warning' },
  failed: { label: 'Failed', tone: 'danger' },
  blocked: { label: 'Blocked', tone: 'danger' },
  timed_out: { label: 'Timed out', tone: 'danger' },
  stopped: { label: 'Stopped', tone: 'neutral' },
  cancelled: { label: 'Cancelled', tone: 'neutral' },
  starting: { label: 'Starting', tone: 'accent' },
  running: { label: 'Running', tone: 'accent' },
  resuming: { label: 'Resuming', tone: 'accent' },
  stopping: { label: 'Stopping', tone: 'neutral' },
  paused: { label: 'Waiting for approval', tone: 'warning' },
  waiting_approval: { label: 'Waiting for approval', tone: 'warning' },
  approval_pending: { label: 'Waiting for approval', tone: 'warning' },
};

/** A saved run status in words with its tone. */
export function runStatus(status: string | null | undefined): RunStatusView {
  const key = String(status ?? '')
    .trim()
    .toLowerCase();
  if (!key) return { label: 'Never run', tone: 'neutral' };
  return (
    RUN_STATUS[key] ?? {
      label: key.charAt(0).toUpperCase() + key.slice(1).replaceAll('_', ' '),
      tone: 'neutral',
    }
  );
}

export const FAILED_RUN_STATUSES = new Set([
  'failed',
  'blocked',
  'timed_out',
  'completed_delivery_failed',
]);

export const WAITING_RUN_STATUSES = new Set([
  'paused',
  'waiting_approval',
  'approval_pending',
]);

export function plural(count: number, one: string, many = `${one}s`) {
  return `${count.toLocaleString()} ${count === 1 ? one : many}`;
}

/** "Good morning" / "Good afternoon" / "Good evening" by local hour. */
export function greeting(now: Date) {
  const hour = now.getHours();
  if (hour < 5) return 'Good evening';
  if (hour < 12) return 'Good morning';
  if (hour < 18) return 'Good afternoon';
  return 'Good evening';
}

/** Start of "overnight": 6 PM yesterday, local time. */
export function overnightStart(now: Date) {
  const start = new Date(now);
  start.setDate(start.getDate() - 1);
  start.setHours(18, 0, 0, 0);
  return start;
}
