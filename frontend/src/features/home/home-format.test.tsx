import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { absoluteTime } from '../../ui/format';
import {
  FAILED_RUN_STATUSES,
  WAITING_RUN_STATUSES,
  When,
  clockTime,
  cronDays,
  greeting,
  overnightStart,
  plural,
  runStatus,
  scheduleWords,
} from './home-format';

const now = new Date(2026, 8, 26, 9, 30, 0);

describe('When', () => {
  it('shows a relative time against the supplied clock with the full date on hover', () => {
    const value = new Date(2026, 8, 26, 6, 30, 0);
    render(<When value={value.toISOString()} now={now} />);
    const time = screen.getByText('3 hours ago');
    expect(time.tagName).toBe('TIME');
    expect(time).toHaveAttribute('datetime', value.toISOString());
    expect(time).toHaveAttribute('title', absoluteTime(value));
  });

  it('describes future times', () => {
    const value = new Date(2026, 8, 26, 11, 30, 0);
    render(<When value={value.toISOString()} now={now} />);
    expect(screen.getByText('in 2 hours')).toBeInTheDocument();
  });

  it.each([null, undefined, '', 'not a date'])(
    'never shows a raw or invalid value (%s)',
    (value) => {
      const { container, unmount } = render(<When value={value} now={now} />);
      expect(container).toHaveTextContent('Unknown');
      expect(container.querySelector('time')).toBeNull();
      unmount();
      const custom = render(<When value={value} now={now} fallback="" />);
      expect(custom.container).toHaveTextContent('');
    },
  );
});

describe('clockTime', () => {
  it('formats a wall-clock hour and minute without a date', () => {
    expect(clockTime(9, 5)).toMatch(/9.*05/);
    expect(clockTime(21, 30)).toMatch(/(21|9).*30/);
    expect(clockTime(21, 30)).not.toMatch(/2000/);
  });
});

describe('cronDays', () => {
  it('uses the scheduler numbering where Monday is 0', () => {
    expect(cronDays('0')).toEqual(['mon']);
    expect(cronDays('1')).toEqual(['tue']);
    expect(cronDays('6')).toEqual(['sun']);
    expect(cronDays('0-4')).toEqual(['mon', 'tue', 'wed', 'thu', 'fri']);
  });

  it('expands names, lists, and ranges in weekday order', () => {
    expect(cronDays('mon-fri')).toEqual(['mon', 'tue', 'wed', 'thu', 'fri']);
    expect(cronDays('MON,wed')).toEqual(['mon', 'wed']);
    expect(cronDays('sat,sun')).toEqual(['sat', 'sun']);
    expect(cronDays('wed,mon,wed')).toEqual(['mon', 'wed']);
    expect(cronDays('monday')).toEqual(['mon']);
  });

  it('wraps a range that crosses the end of the week', () => {
    expect(cronDays('fri-mon')).toEqual(['mon', 'fri', 'sat', 'sun']);
    expect(cronDays('sun-sun')).toEqual(['sun']);
  });

  it.each(['', '7', 'funday', 'mon-', '1-2-3', '*', '*/2', '0-4/2'])(
    'rejects %j instead of guessing',
    (field) => {
      expect(cronDays(field)).toBeNull();
    },
  );
});

describe('scheduleWords', () => {
  const nine = clockTime(9, 0);

  it.each([
    ['daily:08:00', `Every day at ${clockTime(8, 0)}`],
    ['daily:9:05', `Every day at ${clockTime(9, 5)}`],
    ['DAILY:23:59', `Every day at ${clockTime(23, 59)}`],
    ['weekly:mon:09:00', `Every Monday at ${nine}`],
    ['weekly:sunday:09:00', `Every Sunday at ${nine}`],
    ['interval:1', 'Every hour'],
    ['interval:2', 'Every 2 hours'],
    ['interval:0.5', 'Every 30 minutes'],
    ['interval:0.25', 'Every 15 minutes'],
    ['interval_minutes:1', 'Every minute'],
    ['interval_minutes:30', 'Every 30 minutes'],
    ['cron:0 9 * * *', `Every day at ${nine}`],
    ['cron:0 9 * * mon-fri', `Weekdays at ${nine}`],
    ['cron:0 9 * * 0-4', `Weekdays at ${nine}`],
    ['cron:0 9 * * sat,sun', `Weekends at ${nine}`],
    ['cron:0 9 * * 0-6', `Every day at ${nine}`],
    ['cron:0 9 * * mon', `Every Monday at ${nine}`],
    ['cron:0 9 * * 1', `Every Tuesday at ${nine}`],
    ['cron:0 9 * * mon,wed', `Every Monday, Wednesday at ${nine}`],
    ['cron:5 * * * *', 'Every hour at :05'],
    ['  cron:30 18 * * fri  ', `Every Friday at ${clockTime(18, 30)}`],
  ])('describes %j in words', (schedule, expected) => {
    expect(scheduleWords(schedule, null)).toBe(expected);
  });

  it.each([
    'daily:24:00',
    'daily:09:60',
    'weekly:funday:09:00',
    'weekly:0:09:00',
    'weekly:mon:24:00',
    'interval:0',
    'interval:-1',
    'interval:abc',
    'cron:0 9 1 * *',
    'cron:0 9 * 1 *',
    'cron:0 25 * * *',
    'cron:60 9 * * *',
    'cron:*/5 * * * *',
    'cron:5 * * * mon',
    'cron:0 9 * * 7',
    'cron:0 9 * * 0-4/2',
    'cron:0 9 * *',
    'hourly',
    '<script>alert(1)</script>',
  ])('falls back to a safe label for %j', (schedule) => {
    expect(scheduleWords(schedule, null)).toBe('Custom schedule');
  });

  it('describes a one-time run, an invalid one, and a manual workflow', () => {
    const at = new Date(2026, 9, 1, 9, 0, 0).toISOString();
    expect(scheduleWords(null, at)).toBe(`Once · ${absoluteTime(at)}`);
    expect(scheduleWords('', 'not a date')).toBe('Invalid one-time schedule');
    expect(scheduleWords(null, null)).toBe('Manual');
    expect(scheduleWords('   ', undefined)).toBe('Manual');
  });
});

describe('runStatus', () => {
  it.each([
    ['completed', 'Completed', 'success'],
    ['COMPLETED', 'Completed', 'success'],
    ['completed_delivery_failed', 'Delivery failed', 'warning'],
    ['failed', 'Failed', 'danger'],
    ['blocked', 'Blocked', 'danger'],
    ['timed_out', 'Timed out', 'danger'],
    ['stopped', 'Stopped', 'neutral'],
    ['running', 'Running', 'accent'],
    ['paused', 'Waiting for approval', 'warning'],
    ['waiting_approval', 'Waiting for approval', 'warning'],
    ['approval_pending', 'Waiting for approval', 'warning'],
  ])('describes %s as %s', (status, label, tone) => {
    expect(runStatus(status)).toEqual({ label, tone });
  });

  it('humanizes unknown statuses and names a missing one', () => {
    expect(runStatus('needs_review')).toEqual({
      label: 'Needs review',
      tone: 'neutral',
    });
    expect(runStatus(null)).toEqual({ label: 'Never run', tone: 'neutral' });
    expect(runStatus('  ')).toEqual({ label: 'Never run', tone: 'neutral' });
  });

  it('groups failed and waiting statuses without overlap', () => {
    for (const status of FAILED_RUN_STATUSES)
      expect(WAITING_RUN_STATUSES.has(status)).toBe(false);
    expect(FAILED_RUN_STATUSES.has('completed')).toBe(false);
    expect(WAITING_RUN_STATUSES.has('running')).toBe(false);
  });
});

describe('greeting', () => {
  it.each([
    [0, 'Good evening'],
    [4, 'Good evening'],
    [5, 'Good morning'],
    [11, 'Good morning'],
    [12, 'Good afternoon'],
    [17, 'Good afternoon'],
    [18, 'Good evening'],
    [23, 'Good evening'],
  ])('greets at %i:00 with %s', (hour, expected) => {
    expect(greeting(new Date(2026, 8, 26, hour, 0, 0))).toBe(expected);
  });
});

describe('overnightStart', () => {
  it('starts at 6 PM on the previous local day', () => {
    expect(overnightStart(now)).toEqual(new Date(2026, 8, 25, 18, 0, 0, 0));
    expect(overnightStart(new Date(2026, 8, 26, 23, 59, 59))).toEqual(
      new Date(2026, 8, 25, 18, 0, 0, 0),
    );
  });

  it('rolls back across month and year boundaries without mutating the input', () => {
    const first = new Date(2026, 9, 1, 7, 0, 0);
    expect(overnightStart(first)).toEqual(new Date(2026, 8, 30, 18, 0, 0, 0));
    expect(first).toEqual(new Date(2026, 9, 1, 7, 0, 0));
    expect(overnightStart(new Date(2027, 0, 1, 8, 0, 0))).toEqual(
      new Date(2026, 11, 31, 18, 0, 0, 0),
    );
  });
});

describe('plural', () => {
  it('chooses the singular or plural word with a formatted count', () => {
    expect(plural(1, 'memory', 'memories')).toBe('1 memory');
    expect(plural(0, 'memory', 'memories')).toBe('0 memories');
    expect(plural(2, 'workflow run')).toBe('2 workflow runs');
    expect(plural(1200, 'link')).toBe(`${(1200).toLocaleString()} links`);
  });
});
