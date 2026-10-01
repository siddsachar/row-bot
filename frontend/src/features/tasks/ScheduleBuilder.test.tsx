import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import {
  afterAll,
  afterEach,
  beforeAll,
  describe,
  expect,
  it,
  vi,
} from 'vitest';
import ScheduleBuilder, {
  buildSchedule,
  nextFire,
  parseSchedule,
  type ScheduleDraft,
} from './ScheduleBuilder';
import { clockTime } from '../home/home-format';
import { absoluteTime } from '../../ui/format';

afterEach(cleanup);

const defaults = parseSchedule(null, null);
function draft(patch: Partial<ScheduleDraft>): ScheduleDraft {
  return { ...defaults, ...patch };
}
function repeat(patch: Partial<ScheduleDraft>): ScheduleDraft {
  return draft({ mode: 'repeat', ...patch });
}
function roundTrip(schedule: string | null, at: string | null = null) {
  const parsed = parseSchedule(schedule, at);
  return buildSchedule(parsed);
}

describe('parseSchedule', () => {
  it('reads no schedule as manual with editable defaults', () => {
    expect(parseSchedule(null, null)).toEqual({
      mode: 'manual',
      frequency: 'daily',
      time: '09:00',
      days: ['mon'],
      every: 2,
      cron: '',
      at: '',
    });
    expect(parseSchedule('   ', null).mode).toBe('manual');
  });

  it('reads a one-shot local time, which wins over any recurring value', () => {
    expect(parseSchedule(null, '2027-01-01T10:00:00')).toEqual(
      draft({ mode: 'once', at: '2027-01-01T10:00' }),
    );
    expect(parseSchedule('daily:09:00', '2027-01-01T10:00')).toEqual(
      draft({ mode: 'once', at: '2027-01-01T10:00' }),
    );
    expect(parseSchedule(null, '')).toEqual(draft({ mode: 'once', at: '' }));
  });

  it.each<[string, Partial<ScheduleDraft>]>([
    ['daily:09:00', { frequency: 'daily', time: '09:00' }],
    ['daily:7:05', { frequency: 'daily', time: '07:05' }],
    ['DAILY:23:59', { frequency: 'daily', time: '23:59' }],
    ['weekly:fri:17:30', { frequency: 'days', days: ['fri'], time: '17:30' }],
    [
      'weekly:monday:08:00',
      { frequency: 'days', days: ['mon'], time: '08:00' },
    ],
    ['cron:0 9 * * *', { frequency: 'daily', time: '09:00' }],
    ['cron:15 7 * * mon-fri', { frequency: 'weekdays', time: '07:15' }],
    [
      'cron:0 9 * * mon,tue,wed,thu,fri',
      { frequency: 'weekdays', time: '09:00' },
    ],
    [
      'cron:0 18 * * sat,sun',
      { frequency: 'days', days: ['sat', 'sun'], time: '18:00' },
    ],
    [
      'cron: 30 6 * * WED,Mon',
      { frequency: 'days', days: ['mon', 'wed'], time: '06:30' },
    ],
    ['interval:3', { frequency: 'hours', every: 3 }],
    ['interval:0.5', { frequency: 'minutes', every: 30 }],
    ['interval:1.5', { frequency: 'minutes', every: 90 }],
    ['interval_minutes:45', { frequency: 'minutes', every: 45 }],
    ['cron:*/15 * * * *', { frequency: 'cron', cron: '*/15 * * * *' }],
    ['cron:0 9 1 * *', { frequency: 'cron', cron: '0 9 1 * *' }],
  ])('reads %s into builder fields', (saved, fields) => {
    expect(parseSchedule(saved, null)).toEqual(repeat(fields));
  });

  it.each(['0 9 * * 1', '0 9 * * 0-4', '30 7 * * 1,3', '0 9 * * mon,4'])(
    'keeps numbered weekdays as written (%s): the scheduler counts Monday as 0',
    (expression) => {
      expect(parseSchedule(`cron:${expression}`, null)).toEqual(
        repeat({ frequency: 'cron', cron: expression }),
      );
    },
  );
});

describe('buildSchedule', () => {
  it('writes nothing for a manual schedule and only the local time for once', () => {
    expect(buildSchedule(draft({ mode: 'manual', at: 'ignored' }))).toEqual({
      schedule: null,
      at: null,
    });
    expect(
      buildSchedule(
        draft({ mode: 'once', at: '2027-01-01T10:00', frequency: 'hours' }),
      ),
    ).toEqual({ schedule: null, at: '2027-01-01T10:00' });
  });

  it.each<[Partial<ScheduleDraft>, string]>([
    [{ frequency: 'daily', time: '07:05' }, 'daily:07:05'],
    [{ frequency: 'weekdays', time: '07:05' }, 'cron:5 7 * * mon-fri'],
    [{ frequency: 'days', days: ['fri'], time: '17:30' }, 'weekly:fri:17:30'],
    [
      { frequency: 'days', days: ['sun', 'wed', 'mon'], time: '08:00' },
      'cron:0 8 * * mon,wed,sun',
    ],
    [{ frequency: 'hours', every: 6 }, 'interval:6'],
    [{ frequency: 'hours', every: 0 }, 'interval:1'],
    [{ frequency: 'minutes', every: 45 }, 'interval_minutes:45'],
    [{ frequency: 'minutes', every: -5 }, 'interval_minutes:1'],
    [{ frequency: 'cron', cron: '  0 9 * * 1 ' }, 'cron:0 9 * * 1'],
    [{ frequency: 'cron', cron: 'cron:*/5 * * * *' }, 'cron:*/5 * * * *'],
  ])('writes %o as %s and clears the one-shot time', (fields, schedule) => {
    expect(
      buildSchedule(repeat({ ...fields, at: '2027-01-01T10:00' })),
    ).toEqual({ schedule, at: null });
  });
});

describe('schedule round trips', () => {
  it.each([
    'daily:09:00',
    'daily:23:59',
    'weekly:mon:09:00',
    'weekly:sun:18:30',
    'cron:0 9 * * mon-fri',
    'cron:30 7 * * mon,wed,fri',
    'cron:0 18 * * sat,sun',
    'cron:0 9 * * mon,tue,wed,thu,fri,sat,sun',
    'interval:2',
    'interval:168',
    'interval_minutes:30',
    'interval_minutes:1440',
    'cron:0 9 * * 1',
    'cron:*/15 * * * *',
    'cron:0 9 1 * *',
  ])('rewrites the saved %s unchanged', (saved) => {
    expect(roundTrip(saved)).toEqual({ schedule: saved, at: null });
  });

  it('rewrites a saved one-shot time unchanged', () => {
    expect(roundTrip(null, '2027-01-01T10:00')).toEqual({
      schedule: null,
      at: '2027-01-01T10:00',
    });
  });

  it.each([
    ['daily:9:05', 'daily:09:05'],
    ['weekly:monday:08:00', 'weekly:mon:08:00'],
    ['cron:0 9 * * *', 'daily:09:00'],
    ['cron:0 9 * * mon', 'weekly:mon:09:00'],
    ['cron:0 9 * * mon,tue,wed,thu,fri', 'cron:0 9 * * mon-fri'],
    ['interval:0.5', 'interval_minutes:30'],
    ['interval:1.5', 'interval_minutes:90'],
  ])('normalizes %s to the equivalent %s', (saved, normalized) => {
    expect(roundTrip(saved)).toEqual({ schedule: normalized, at: null });
  });

  it('drops seconds from a saved one-shot time to fit the date field', () => {
    expect(roundTrip(null, '2027-01-01T10:00:30')).toEqual({
      schedule: null,
      at: '2027-01-01T10:00',
    });
  });

  it.each<[string, ScheduleDraft]>([
    ['manual', draft({ mode: 'manual' })],
    ['once', draft({ mode: 'once', at: '2027-01-01T10:00' })],
    ['once without a date yet', draft({ mode: 'once', at: '' })],
    ['every day', repeat({ frequency: 'daily', time: '07:45' })],
    ['weekdays', repeat({ frequency: 'weekdays', time: '08:05' })],
    ['one chosen day', repeat({ frequency: 'days', days: ['tue'] })],
    ['two chosen days', repeat({ frequency: 'days', days: ['tue', 'thu'] })],
    [
      'every day chosen one by one',
      repeat({
        frequency: 'days',
        days: ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'],
      }),
    ],
    ['every few hours', repeat({ frequency: 'hours', every: 3 })],
    ['every few minutes', repeat({ frequency: 'minutes', every: 45 })],
    ['a custom cron', repeat({ frequency: 'cron', cron: '*/15 * * * *' })],
    [
      'a numbered-weekday cron',
      repeat({ frequency: 'cron', cron: '0 9 * * 1' }),
    ],
  ])('reads back a %s draft exactly', (_, value) => {
    const built = buildSchedule(value);
    expect(parseSchedule(built.schedule, built.at)).toEqual(value);
  });

  it('reads chosen Monday-to-Friday back as the equivalent Weekdays choice', () => {
    const built = buildSchedule(
      repeat({ frequency: 'days', days: ['mon', 'tue', 'wed', 'thu', 'fri'] }),
    );
    expect(built.schedule).toBe('cron:0 9 * * mon,tue,wed,thu,fri');
    expect(parseSchedule(built.schedule, built.at)).toEqual(
      repeat({ frequency: 'weekdays' }),
    );
  });

  // Known product bug (reported, not yet fixed): parseSchedule('cron:') falls
  // through to the raw-value branch. Flip to `it` once it reads back empty.
  it('keeps an unfinished custom cron expression empty after a round trip', () => {
    // The builder writes "cron:" while the field is empty; reading that back
    // must not put the prefix into the expression field, or the required
    // field is satisfied and "cron:" is sent to the server.
    const value = repeat({ frequency: 'cron', cron: '' });
    const built = buildSchedule(value);
    expect(parseSchedule(built.schedule, built.at)).toEqual(value);
  });
});

describe('nextFire', () => {
  // Saturday 26 September 2026, local time.
  const saturday = (hour: number, minute = 0) =>
    new Date(2026, 8, 26, hour, minute);

  it('fires later today, or tomorrow once today’s time has passed', () => {
    const daily = repeat({ frequency: 'daily', time: '09:00' });
    expect(nextFire(daily, saturday(8, 59))).toEqual(saturday(9));
    expect(nextFire(daily, saturday(9))).toEqual(new Date(2026, 8, 27, 9));
    expect(nextFire(daily, saturday(10))).toEqual(new Date(2026, 8, 27, 9));
  });

  it('rolls over month, year and leap-day boundaries', () => {
    const daily = repeat({ frequency: 'daily', time: '09:00' });
    expect(nextFire(daily, new Date(2026, 0, 31, 10))).toEqual(
      new Date(2026, 1, 1, 9),
    );
    expect(nextFire(daily, new Date(2026, 11, 31, 23, 30))).toEqual(
      new Date(2027, 0, 1, 9),
    );
    expect(nextFire(daily, new Date(2028, 1, 28, 10))).toEqual(
      new Date(2028, 1, 29, 9),
    );
    expect(nextFire(daily, new Date(2027, 1, 28, 10))).toEqual(
      new Date(2027, 2, 1, 9),
    );
    expect(
      nextFire(
        repeat({ frequency: 'days', days: ['mon'] }),
        new Date(2026, 9, 31, 10),
      ),
    ).toEqual(new Date(2026, 10, 2, 9));
  });

  it('skips weekends for Weekdays', () => {
    const weekdays = repeat({ frequency: 'weekdays', time: '07:30' });
    expect(nextFire(weekdays, saturday(6))).toEqual(
      new Date(2026, 8, 28, 7, 30),
    );
    expect(nextFire(weekdays, new Date(2026, 8, 25, 8))).toEqual(
      new Date(2026, 8, 28, 7, 30),
    );
    expect(nextFire(weekdays, new Date(2026, 8, 24, 7))).toEqual(
      new Date(2026, 8, 24, 7, 30),
    );
  });

  it('maps chosen days with Monday first and Sunday last', () => {
    expect(
      nextFire(repeat({ frequency: 'days', days: ['sun'] }), saturday(10)),
    ).toEqual(new Date(2026, 8, 27, 9));
    expect(
      nextFire(
        repeat({ frequency: 'days', days: ['mon'] }),
        new Date(2026, 8, 27, 10),
      ),
    ).toEqual(new Date(2026, 8, 28, 9));
    expect(
      nextFire(
        repeat({ frequency: 'days', days: ['fri', 'wed'] }),
        new Date(2026, 8, 23, 10),
      ),
    ).toEqual(new Date(2026, 8, 25, 9));
  });

  it('waits a full week when the only chosen day is today and its time has passed', () => {
    expect(
      nextFire(repeat({ frequency: 'days', days: ['sat'] }), saturday(10)),
    ).toEqual(new Date(2026, 9, 3, 9));
  });

  it('returns the one-shot time as local time and nothing for other schedules', () => {
    expect(
      nextFire(draft({ mode: 'once', at: '2027-01-01T10:00' }), saturday(10)),
    ).toEqual(new Date(2027, 0, 1, 10));
    expect(nextFire(draft({ mode: 'once', at: '' }), saturday(10))).toBeNull();
    expect(nextFire(draft({ mode: 'manual' }), saturday(10))).toBeNull();
    for (const frequency of ['hours', 'minutes', 'cron'] as const)
      expect(
        nextFire(repeat({ frequency, cron: '0 9 * * *' }), saturday(8)),
      ).toBeNull();
  });

  describe('across daylight saving changes', () => {
    const original = process.env.TZ;
    const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    beforeAll(() => {
      process.env.TZ = 'America/New_York';
    });
    afterAll(() => {
      // Deleting TZ keeps Node's cached zone, so name the original one.
      process.env.TZ = original ?? zone;
    });

    it('keeps the local wall-clock time when clocks spring forward', () => {
      // Precondition: the zone change applied (EST in winter, EDT after).
      expect(new Date(2026, 2, 7, 12).getTimezoneOffset()).toBe(300);
      expect(new Date(2026, 2, 9, 12).getTimezoneOffset()).toBe(240);
      const now = new Date(2026, 2, 7, 12);
      const next = nextFire(repeat({ frequency: 'daily' }), now);
      expect(next).toEqual(new Date(2026, 2, 8, 9));
      expect(next?.getHours()).toBe(9);
      expect(next!.getTime() - now.getTime()).toBe(20 * 3600_000);
      expect(
        nextFire(repeat({ frequency: 'days', days: ['mon'] }), now),
      ).toEqual(new Date(2026, 2, 9, 9));
    });

    it('moves a time skipped by the change to the same instant the scheduler uses', () => {
      // 02:30 does not exist on 8 March 2026 in New York; APScheduler fires
      // at 02:30 EST, which is 03:30 EDT (07:30 UTC).
      const next = nextFire(
        repeat({ frequency: 'daily', time: '02:30' }),
        new Date(2026, 2, 7, 12),
      );
      expect(next?.toISOString()).toBe('2026-03-08T07:30:00.000Z');
    });

    it('keeps the local wall-clock time when clocks fall back', () => {
      const now = new Date(2026, 9, 31, 12);
      const next = nextFire(repeat({ frequency: 'daily' }), now);
      expect(next).toEqual(new Date(2026, 10, 1, 9));
      expect(next!.getTime() - now.getTime()).toBe(22 * 3600_000);
    });
  });
});

describe('ScheduleBuilder', () => {
  const now = new Date(2026, 8, 26, 8);
  function renderBuilder(
    props: Partial<Parameters<typeof ScheduleBuilder>[0]> = {},
  ) {
    const onChange = vi.fn();
    render(
      <ScheduleBuilder
        schedule={null}
        at={null}
        enabled
        now={now}
        onChange={onChange}
        {...props}
      />,
    );
    return onChange;
  }
  function preview() {
    return document.querySelector('.schedule-preview');
  }

  it('starts manual and offers the three run choices as radios', () => {
    const onChange = renderBuilder();
    const group = screen.getByRole('radiogroup', { name: 'When it runs' });
    expect(group).toBeVisible();
    expect(
      screen.getAllByRole('radio').map((radio) => radio.textContent),
    ).toEqual(['Manually', 'Once', 'Repeats']);
    expect(screen.getByRole('radio', { name: 'Manually' })).toBeChecked();
    expect(screen.queryByLabelText('Date and time')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Repeats')).not.toBeInTheDocument();
    expect(preview()).toHaveTextContent('Runs only when you start it');
    expect(onChange).not.toHaveBeenCalled();
  });

  it('writes a local one-shot date and keeps it separate from repeats', () => {
    const onChange = renderBuilder({ enabled: false });
    fireEvent.click(screen.getByRole('radio', { name: 'Once' }));
    expect(onChange).toHaveBeenLastCalledWith({ schedule: null, at: '' });
    const field = screen.getByLabelText('Date and time');
    expect(field).toHaveAttribute('type', 'datetime-local');
    expect(field).toBeRequired();
    expect(preview()).toHaveTextContent('Choose a date and time');
    fireEvent.change(field, { target: { value: '2027-01-01T10:00' } });
    expect(onChange).toHaveBeenLastCalledWith({
      schedule: null,
      at: '2027-01-01T10:00',
    });
    // The one-off time already names when it runs: no separate "next" run.
    const when = new Date(2027, 0, 1, 10);
    expect(preview()?.textContent).toBe(`Once · ${absoluteTime(when)}`);
    expect(preview()?.querySelector('time')).toBeNull();

    fireEvent.click(screen.getByRole('radio', { name: 'Repeats' }));
    expect(onChange).toHaveBeenLastCalledWith({
      schedule: 'daily:09:00',
      at: null,
    });
    expect(screen.queryByLabelText('Date and time')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('radio', { name: 'Once' }));
    expect(onChange).toHaveBeenLastCalledWith({
      schedule: null,
      at: '2027-01-01T10:00',
    });
    expect(screen.getByLabelText('Date and time')).toHaveValue(
      '2027-01-01T10:00',
    );
    fireEvent.click(screen.getByRole('radio', { name: 'Manually' }));
    expect(onChange).toHaveBeenLastCalledWith({ schedule: null, at: null });
  });

  it('says when a one-off time is not after now', () => {
    // now is 26 September 2026 at 08:00 local time.
    const onChange = renderBuilder({ at: '2026-09-26T07:30' });
    expect(screen.getByRole('radio', { name: 'Once' })).toBeChecked();
    expect(preview()?.textContent).toBe(
      `Once · ${absoluteTime(new Date(2026, 8, 26, 7, 30))} · this time has passed`,
    );
    const field = screen.getByLabelText('Date and time');
    fireEvent.change(field, { target: { value: '2026-09-26T08:00' } });
    expect(preview()).toHaveTextContent('· this time has passed');
    fireEvent.change(field, { target: { value: '2026-09-26T08:01' } });
    expect(preview()?.textContent).toBe(
      `Once · ${absoluteTime(new Date(2026, 8, 26, 8, 1))}`,
    );
    // The warning only describes the draft; the chosen time is still saved.
    expect(onChange).toHaveBeenLastCalledWith({
      schedule: null,
      at: '2026-09-26T08:01',
    });
    fireEvent.change(field, { target: { value: '' } });
    expect(preview()?.textContent).toBe('Choose a date and time');
  });

  it('repeats every day at nine by default and previews the next run', () => {
    const onChange = renderBuilder({ enabled: false });
    fireEvent.click(screen.getByRole('radio', { name: 'Repeats' }));
    expect(screen.getByRole('radio', { name: 'Repeats' })).toBeChecked();
    expect(onChange).toHaveBeenLastCalledWith({
      schedule: 'daily:09:00',
      at: null,
    });
    expect(screen.getByLabelText('Repeats')).toHaveValue('daily');
    expect(
      Array.from(
        (screen.getByLabelText('Repeats') as HTMLSelectElement).options,
      ).map((option) => option.textContent),
    ).toEqual([
      'Every day',
      'Weekdays',
      'On chosen days',
      'Every few hours',
      'Every few minutes',
      'Custom (cron)',
    ]);
    expect(screen.getByLabelText('Time')).toHaveValue('09:00');
    const next = new Date(2026, 8, 26, 9);
    expect(preview()).toHaveTextContent(`Every day at ${clockTime(9, 0)}`);
    expect(preview()).toHaveTextContent(
      `next ${absoluteTime(next)} once enabled`,
    );
    expect(preview()?.querySelector('time')).toHaveAttribute(
      'datetime',
      next.toISOString(),
    );
    fireEvent.change(screen.getByLabelText('Time'), {
      target: { value: '07:30' },
    });
    expect(onChange).toHaveBeenLastCalledWith({
      schedule: 'daily:07:30',
      at: null,
    });
    fireEvent.change(screen.getByLabelText('Time'), { target: { value: '' } });
    expect(onChange).toHaveBeenLastCalledWith({
      schedule: 'daily:09:00',
      at: null,
    });
  });

  it('writes weekdays as a named cron range', () => {
    const onChange = renderBuilder({ schedule: 'daily:07:30' });
    fireEvent.change(screen.getByLabelText('Repeats'), {
      target: { value: 'weekdays' },
    });
    expect(onChange).toHaveBeenLastCalledWith({
      schedule: 'cron:30 7 * * mon-fri',
      at: null,
    });
    expect(preview()).toHaveTextContent(`Weekdays at ${clockTime(7, 30)}`);
    expect(preview()).toHaveTextContent(
      `next ${absoluteTime(new Date(2026, 8, 28, 7, 30))}`,
    );
    expect(preview()).not.toHaveTextContent('once enabled');
  });

  it('toggles chosen days, never leaving none chosen', () => {
    const onChange = renderBuilder({ schedule: 'daily:09:00' });
    fireEvent.change(screen.getByLabelText('Repeats'), {
      target: { value: 'days' },
    });
    expect(onChange).toHaveBeenLastCalledWith({
      schedule: 'weekly:mon:09:00',
      at: null,
    });
    const days = screen.getByRole('group', { name: 'Days of the week' });
    const toggles = Array.from(days.querySelectorAll('button'));
    expect(toggles.map((toggle) => toggle.getAttribute('aria-label'))).toEqual([
      'Monday',
      'Tuesday',
      'Wednesday',
      'Thursday',
      'Friday',
      'Saturday',
      'Sunday',
    ]);
    const monday = screen.getByRole('button', { name: 'Monday' });
    const wednesday = screen.getByRole('button', { name: 'Wednesday' });
    expect(monday).toHaveAttribute('aria-pressed', 'true');
    expect(wednesday).toHaveAttribute('aria-pressed', 'false');
    fireEvent.click(wednesday);
    expect(wednesday).toHaveAttribute('aria-pressed', 'true');
    expect(onChange).toHaveBeenLastCalledWith({
      schedule: 'cron:0 9 * * mon,wed',
      at: null,
    });
    fireEvent.click(monday);
    expect(monday).toHaveAttribute('aria-pressed', 'false');
    expect(onChange).toHaveBeenLastCalledWith({
      schedule: 'weekly:wed:09:00',
      at: null,
    });
    expect(preview()).toHaveTextContent(
      `Every Wednesday at ${clockTime(9, 0)}`,
    );
    expect(preview()).toHaveTextContent(
      `next ${absoluteTime(new Date(2026, 8, 30, 9))}`,
    );
    const calls = onChange.mock.calls.length;
    fireEvent.click(wednesday);
    expect(wednesday).toHaveAttribute('aria-pressed', 'true');
    expect(onChange).toHaveBeenCalledTimes(calls);
  });

  it('counts intervals from when the schedule is saved or enabled', () => {
    const onChange = renderBuilder({ schedule: 'daily:09:00' });
    fireEvent.change(screen.getByLabelText('Repeats'), {
      target: { value: 'hours' },
    });
    expect(onChange).toHaveBeenLastCalledWith({
      schedule: 'interval:2',
      at: null,
    });
    expect(screen.queryByLabelText('Time')).not.toBeInTheDocument();
    const hours = screen.getByLabelText('Every (hours)');
    expect(hours).toHaveAttribute('max', '168');
    fireEvent.change(hours, { target: { value: '6' } });
    expect(onChange).toHaveBeenLastCalledWith({
      schedule: 'interval:6',
      at: null,
    });
    expect(preview()).toHaveTextContent(
      'Every 6 hours · counted from when it is saved',
    );
    expect(preview()?.querySelector('time')).toBeNull();
    fireEvent.change(hours, { target: { value: '0' } });
    expect(onChange).toHaveBeenLastCalledWith({
      schedule: 'interval:1',
      at: null,
    });
    fireEvent.change(screen.getByLabelText('Repeats'), {
      target: { value: 'minutes' },
    });
    const minutes = screen.getByLabelText('Every (minutes)');
    expect(minutes).toHaveAttribute('max', '1440');
    fireEvent.change(minutes, { target: { value: '45' } });
    expect(onChange).toHaveBeenLastCalledWith({
      schedule: 'interval_minutes:45',
      at: null,
    });
    expect(preview()).toHaveTextContent('Every 45 minutes');
  });

  it('says an interval starts counting once enabled while disabled', () => {
    renderBuilder({ schedule: 'interval_minutes:30', enabled: false });
    expect(screen.getByLabelText('Repeats')).toHaveValue('minutes');
    expect(screen.getByLabelText('Every (minutes)')).toHaveValue(30);
    expect(preview()).toHaveTextContent(
      'Every 30 minutes · counted from when it is enabled',
    );
  });

  it('writes a custom cron expression and describes numbered days from Monday = 0', () => {
    const onChange = renderBuilder({ schedule: 'daily:09:00' });
    fireEvent.change(screen.getByLabelText('Repeats'), {
      target: { value: 'cron' },
    });
    const expression = screen.getByLabelText('Cron expression', {
      exact: false,
    });
    expect(expression).toHaveValue('');
    expect(expression).toBeRequired();
    expect(preview()).toHaveTextContent('Enter a cron expression');
    fireEvent.change(expression, { target: { value: '0 9 * * 1' } });
    expect(onChange).toHaveBeenLastCalledWith({
      schedule: 'cron:0 9 * * 1',
      at: null,
    });
    expect(preview()).toHaveTextContent(`Every Tuesday at ${clockTime(9, 0)}`);
    expect(preview()?.querySelector('time')).toBeNull();
    fireEvent.change(expression, { target: { value: '*/10 * * * *' } });
    expect(onChange).toHaveBeenLastCalledWith({
      schedule: 'cron:*/10 * * * *',
      at: null,
    });
    expect(preview()).toHaveTextContent('Custom schedule');
  });

  it.each<[string, string | null, string | null, () => void]>([
    [
      'a one-shot time',
      null,
      '2027-01-01T10:00',
      () => {
        expect(screen.getByRole('radio', { name: 'Once' })).toBeChecked();
        expect(screen.getByLabelText('Date and time')).toHaveValue(
          '2027-01-01T10:00',
        );
      },
    ],
    [
      'a weekly day',
      'weekly:fri:17:00',
      null,
      () => {
        expect(screen.getByLabelText('Repeats')).toHaveValue('days');
        expect(screen.getByRole('button', { name: 'Friday' })).toHaveAttribute(
          'aria-pressed',
          'true',
        );
        expect(screen.getByRole('button', { name: 'Monday' })).toHaveAttribute(
          'aria-pressed',
          'false',
        );
        expect(screen.getByLabelText('Time')).toHaveValue('17:00');
      },
    ],
    [
      'a numbered-weekday cron',
      'cron:0 9 * * 1',
      null,
      () => {
        expect(screen.getByLabelText('Repeats')).toHaveValue('cron');
        expect(
          screen.getByLabelText('Cron expression', { exact: false }),
        ).toHaveValue('0 9 * * 1');
        expect(preview()).toHaveTextContent('Every Tuesday');
      },
    ],
    [
      'weekdays',
      'cron:0 8 * * mon-fri',
      null,
      () => {
        expect(screen.getByLabelText('Repeats')).toHaveValue('weekdays');
        expect(screen.getByLabelText('Time')).toHaveValue('08:00');
      },
    ],
  ])(
    'opens %s in its builder fields without writing',
    (_, schedule, at, check) => {
      const onChange = renderBuilder({ schedule, at });
      check();
      expect(onChange).not.toHaveBeenCalled();
    },
  );

  it('moves between run choices with the arrow keys', () => {
    const onChange = renderBuilder();
    const manual = screen.getByRole('radio', { name: 'Manually' });
    expect(manual).toHaveAttribute('tabindex', '0');
    fireEvent.keyDown(manual, { key: 'ArrowRight' });
    expect(screen.getByRole('radio', { name: 'Once' })).toBeChecked();
    expect(screen.getByRole('radio', { name: 'Once' })).toHaveFocus();
    expect(onChange).toHaveBeenLastCalledWith({ schedule: null, at: '' });
  });
});
