import { useId, useMemo, useState } from 'react';
import { CalendarClock } from 'lucide-react';
import {
  Combobox,
  Input,
  Segmented,
  Select,
  type ComboboxOption,
} from '../../ui/primitives';
import { cronDays, ordinal, scheduleWords } from '../home/home-format';
import {
  defaultZone,
  wallAt,
  zonedInstant,
  zoneNames,
  zoneTime,
} from './schedule-zones';

type Mode = 'manual' | 'once' | 'repeat';
type Frequency =
  'daily' | 'weekdays' | 'days' | 'monthly' | 'hours' | 'minutes' | 'cron';
export type ScheduleDraft = {
  mode: Mode;
  frequency: Frequency;
  time: string;
  days: string[];
  /** Day of the month, 1–31, for Monthly. */
  date: number;
  every: number;
  cron: string;
  at: string;
};

const DAYS: { key: string; short: string; name: string }[] = [
  { key: 'mon', short: 'M', name: 'Monday' },
  { key: 'tue', short: 'T', name: 'Tuesday' },
  { key: 'wed', short: 'W', name: 'Wednesday' },
  { key: 'thu', short: 'T', name: 'Thursday' },
  { key: 'fri', short: 'F', name: 'Friday' },
  { key: 'sat', short: 'S', name: 'Saturday' },
  { key: 'sun', short: 'S', name: 'Sunday' },
];
const DAY_ORDER = DAYS.map((day) => day.key);

const pad = (value: number) => String(value).padStart(2, '0');
function clock(hour: number, minute: number) {
  return `${pad(hour)}:${pad(minute)}`;
}

/** Read a saved schedule into builder fields. Unknown shapes stay custom. */
export function parseSchedule(
  schedule: string | null,
  at: string | null,
): ScheduleDraft {
  const draft: ScheduleDraft = {
    mode: 'manual',
    frequency: 'daily',
    time: '09:00',
    days: ['mon'],
    date: 1,
    every: 2,
    cron: '',
    at: '',
  };
  if (at !== null) return { ...draft, mode: 'once', at: at.slice(0, 16) };
  const value = schedule?.trim();
  if (!value) return draft;
  const repeat = { ...draft, mode: 'repeat' as const };
  const daily = /^daily:(\d{1,2}):(\d{2})$/i.exec(value);
  if (daily)
    return { ...repeat, time: clock(Number(daily[1]), Number(daily[2])) };
  const weekly = /^weekly:([a-z]+):(\d{1,2}):(\d{2})$/i.exec(value);
  const day = weekly && cronDays(weekly[1]);
  if (weekly && day?.length === 1)
    return {
      ...repeat,
      frequency: 'days',
      days: day,
      time: clock(Number(weekly[2]), Number(weekly[3])),
    };
  const interval = /^interval(_minutes)?:([0-9]+(?:\.[0-9]+)?)$/i.exec(value);
  if (interval) {
    const count = Number(interval[2]);
    if (interval[1]) return { ...repeat, frequency: 'minutes', every: count };
    return Number.isInteger(count)
      ? { ...repeat, frequency: 'hours', every: count }
      : { ...repeat, frequency: 'minutes', every: Math.round(count * 60) };
  }
  const cron = /^cron:\s*(.*)$/i.exec(value);
  // An unfinished custom expression stays empty so the editor still asks.
  if (cron && !cron[1].trim())
    return { ...repeat, frequency: 'cron', cron: '' };
  if (cron) {
    const fields = cron[1].trim().split(/\s+/);
    const [minute, hour, dom, month, dow] = fields;
    const timed =
      fields.length === 5 &&
      /^\d{1,2}$/.test(minute) &&
      /^\d{1,2}$/.test(hour) &&
      month === '*' &&
      Number(minute) < 60 &&
      Number(hour) < 24;
    // One day of every month (B345): "0 8 1 * *" is Monthly on the 1st.
    if (
      timed &&
      dow === '*' &&
      /^\d{1,2}$/.test(dom) &&
      Number(dom) >= 1 &&
      Number(dom) <= 31
    )
      return {
        ...repeat,
        frequency: 'monthly',
        date: Number(dom),
        time: clock(Number(hour), Number(minute)),
      };
    const simple = timed && dom === '*';
    const days = simple && dow !== '*' ? cronDays(dow) : null;
    // Numbered weekdays are left as written: the scheduler counts Monday as 0.
    if (simple && !/\d/.test(dow)) {
      const time = clock(Number(hour), Number(minute));
      if (dow === '*') return { ...repeat, time };
      if (days?.join() === 'mon,tue,wed,thu,fri')
        return { ...repeat, frequency: 'weekdays', time };
      if (days?.length) return { ...repeat, frequency: 'days', days, time };
    }
    return { ...repeat, frequency: 'cron', cron: cron[1].trim() };
  }
  return { ...repeat, frequency: 'cron', cron: value };
}

/** Builder fields back to the saved `schedule` / `at` pair. */
export function buildSchedule(draft: ScheduleDraft): {
  schedule: string | null;
  at: string | null;
} {
  if (draft.mode === 'manual') return { schedule: null, at: null };
  if (draft.mode === 'once') return { schedule: null, at: draft.at };
  const [hour, minute] = draft.time.split(':').map(Number);
  const time = clock(hour || 0, minute || 0);
  switch (draft.frequency) {
    case 'daily':
      return { schedule: `daily:${time}`, at: null };
    case 'weekdays':
      return {
        schedule: `cron:${minute || 0} ${hour || 0} * * mon-fri`,
        at: null,
      };
    case 'days': {
      const days = DAY_ORDER.filter((day) => draft.days.includes(day));
      if (days.length === 1)
        return { schedule: `weekly:${days[0]}:${time}`, at: null };
      return {
        schedule: `cron:${minute || 0} ${hour || 0} * * ${days.join(',')}`,
        at: null,
      };
    }
    case 'monthly':
      return {
        schedule: `cron:${minute || 0} ${hour || 0} ${draft.date} * *`,
        at: null,
      };
    case 'hours':
      return { schedule: `interval:${Math.max(1, draft.every)}`, at: null };
    case 'minutes':
      return {
        schedule: `interval_minutes:${Math.max(1, draft.every)}`,
        at: null,
      };
    default: {
      const expression = draft.cron.trim();
      return {
        schedule: expression.startsWith('cron:')
          ? expression
          : `cron:${expression}`,
        at: null,
      };
    }
  }
}

const localZone = () =>
  Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';

/** A one-off's wall-clock date and time ("2027-01-01T10:00") as an instant in its zone. */
export function onceAt(at: string, zone: string): Date | null {
  const found = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(at);
  if (!found) return null;
  const [year, month, day, hour, minute] = found.slice(1).map(Number);
  return zonedInstant(Date.UTC(year, month - 1, day, hour, minute), zone);
}

/**
 * The next time a daily or day-of-week schedule fires, from the builder: at
 * its wall-clock time in its zone, as the scheduler places it (N9).
 */
export function nextFire(
  draft: ScheduleDraft,
  now: Date,
  zone: string = localZone(),
): Date | null {
  if (draft.mode === 'once') return onceAt(draft.at, zone);
  if (
    draft.mode !== 'repeat' ||
    !['daily', 'weekdays', 'days', 'monthly'].includes(draft.frequency)
  )
    return null;
  const [hour, minute] = draft.time.split(':').map(Number);
  const today = new Date(wallAt(now.getTime(), zone));
  const wall = (months: number, date: number) =>
    Date.UTC(
      today.getUTCFullYear(),
      today.getUTCMonth() + months,
      date,
      hour || 0,
      minute || 0,
    );
  if (draft.frequency === 'monthly') {
    // A month without that day (the 31st in April) is skipped.
    for (let offset = 0; offset < 13; offset += 1) {
      const candidate = wall(offset, draft.date);
      if (new Date(candidate).getUTCDate() !== draft.date) continue;
      const fire = zonedInstant(candidate, zone);
      if (fire > now) return fire;
    }
    return null;
  }
  const allowed =
    draft.frequency === 'daily'
      ? DAY_ORDER
      : draft.frequency === 'weekdays'
        ? DAY_ORDER.slice(0, 5)
        : draft.days;
  for (let offset = 0; offset < 8; offset += 1) {
    const candidate = wall(0, today.getUTCDate() + offset);
    const key = DAY_ORDER[(new Date(candidate).getUTCDay() + 6) % 7];
    const fire = zonedInstant(candidate, zone);
    if (fire > now && allowed.includes(key)) return fire;
  }
  return null;
}

export type ScheduleValue = {
  schedule: string | null;
  at: string | null;
  /** The IANA zone the schedule keeps its times in. */
  timezone: string;
};

export default function ScheduleBuilder({
  schedule,
  at,
  timezone = null,
  enabled,
  onChange,
  now = new Date(),
}: {
  schedule: string | null;
  at: string | null;
  /** The saved zone; a new schedule starts with this device's. */
  timezone?: string | null;
  enabled: boolean;
  onChange: (value: ScheduleValue) => void;
  now?: Date;
}) {
  const id = useId();
  const [draft, setDraft] = useState(() => parseSchedule(schedule, at));
  const [zone, setZone] = useState(() => timezone || defaultZone());
  const zoneOptions = useMemo<ComboboxOption[]>(
    () =>
      zoneNames(zone).map((name) => ({
        value: name,
        label: name.replaceAll('_', ' '),
        keywords: [name],
      })),
    [zone],
  );
  const update = (patch: Partial<ScheduleDraft>, nextZone = zone) => {
    const next = { ...draft, ...patch };
    setDraft(next);
    setZone(nextZone);
    onChange({ ...buildSchedule(next), timezone: nextZone });
  };
  const built = buildSchedule(draft);
  const words =
    draft.mode === 'manual'
      ? 'Runs only when you start it'
      : draft.mode === 'once' && !draft.at
        ? 'Choose a date and time'
        : draft.mode === 'repeat' &&
            draft.frequency === 'cron' &&
            !draft.cron.trim()
          ? 'Enter a cron expression'
          : draft.mode === 'once'
            ? `Once · ${zoneTime(onceAt(draft.at, zone) ?? now, zone)}`
            : scheduleWords(built.schedule, built.at);
  // A one-off run already names its time; say so only when it has passed.
  const upcoming = draft.mode === 'once' ? null : nextFire(draft, now, zone);
  const passed =
    draft.mode === 'once' &&
    (onceAt(draft.at, zone)?.getTime() ?? Infinity) <= now.getTime();
  const interval =
    draft.mode === 'repeat' &&
    (draft.frequency === 'hours' || draft.frequency === 'minutes');
  // Times of day keep their place in a zone; an interval counts on whatever the zone.
  const zoned =
    (draft.mode === 'once' && !!draft.at) ||
    (draft.mode === 'repeat' && !interval);
  return (
    <div className="schedule-builder">
      <Segmented
        label="When it runs"
        size="sm"
        value={draft.mode}
        onChange={(mode) =>
          update({
            mode,
            at: mode === 'once' && !draft.at ? '' : draft.at,
          })
        }
        options={[
          { value: 'manual', label: 'Manually' },
          { value: 'once', label: 'Once' },
          { value: 'repeat', label: 'Repeats' },
        ]}
      />
      {draft.mode === 'once' && (
        <label className="schedule-field" htmlFor={`${id}-at`}>
          <span>Date and time</span>
          <Input
            id={`${id}-at`}
            type="datetime-local"
            aria-label="Date and time"
            required
            value={draft.at}
            onChange={(event) => update({ at: event.target.value })}
          />
        </label>
      )}
      {draft.mode === 'repeat' && (
        <>
          <label className="schedule-field" htmlFor={`${id}-frequency`}>
            <span>How often</span>
            <Select
              id={`${id}-frequency`}
              value={draft.frequency}
              onChange={(event) =>
                update({ frequency: event.target.value as Frequency })
              }
            >
              <option value="daily">Every day</option>
              <option value="weekdays">Weekdays</option>
              <option value="days">On chosen days</option>
              <option value="monthly">Monthly</option>
              <option value="hours">Every few hours</option>
              <option value="minutes">Every few minutes</option>
              <option value="cron">Custom (cron)</option>
            </Select>
          </label>
          {draft.frequency === 'days' && (
            <div
              className="schedule-days"
              role="group"
              aria-label="Days of the week"
            >
              {DAYS.map((day) => {
                const on = draft.days.includes(day.key);
                return (
                  <button
                    key={day.key}
                    type="button"
                    className="schedule-day"
                    aria-label={day.name}
                    aria-pressed={on}
                    title={day.name}
                    onClick={() => {
                      const days = on
                        ? draft.days.filter((item) => item !== day.key)
                        : [...draft.days, day.key];
                      if (days.length) update({ days });
                    }}
                  >
                    {day.short}
                  </button>
                );
              })}
            </div>
          )}
          {draft.frequency === 'monthly' && (
            <label className="schedule-field" htmlFor={`${id}-date`}>
              <span>Day of the month</span>
              <Select
                id={`${id}-date`}
                value={String(draft.date)}
                onChange={(event) =>
                  update({ date: Number(event.target.value) })
                }
              >
                {Array.from({ length: 31 }, (_, index) => (
                  <option key={index + 1} value={index + 1}>
                    {ordinal(index + 1)}
                  </option>
                ))}
              </Select>
              {draft.date > 28 && (
                <small>Months without this day are skipped.</small>
              )}
            </label>
          )}
          {['daily', 'weekdays', 'days', 'monthly'].includes(
            draft.frequency,
          ) && (
            <label className="schedule-field" htmlFor={`${id}-time`}>
              <span>Time</span>
              <Input
                id={`${id}-time`}
                type="time"
                required
                value={draft.time}
                onChange={(event) =>
                  update({ time: event.target.value || '09:00' })
                }
              />
            </label>
          )}
          {interval && (
            <label className="schedule-field" htmlFor={`${id}-every`}>
              <span>
                Every ({draft.frequency === 'hours' ? 'hours' : 'minutes'})
              </span>
              <Input
                id={`${id}-every`}
                type="number"
                min={1}
                max={draft.frequency === 'hours' ? 168 : 1440}
                step={1}
                required
                value={draft.every}
                onChange={(event) =>
                  update({
                    every: Math.max(1, Number(event.target.value) || 1),
                  })
                }
              />
            </label>
          )}
          {draft.frequency === 'cron' && (
            <label className="schedule-field" htmlFor={`${id}-cron`}>
              <span>Cron expression</span>
              <Input
                id={`${id}-cron`}
                required
                maxLength={250}
                spellCheck={false}
                placeholder="0 9 * * mon-fri"
                value={draft.cron}
                onChange={(event) => update({ cron: event.target.value })}
              />
              <small>
                minute hour day month weekday. Name the days (mon–sun): numbered
                days start from Monday = 0.
              </small>
            </label>
          )}
        </>
      )}
      {(draft.mode === 'once' || (draft.mode === 'repeat' && !interval)) && (
        <div className="schedule-field">
          <span id={`${id}-zone`}>Timezone</span>
          <Combobox
            label="Timezone"
            value={zone}
            options={zoneOptions}
            placeholder="Search timezones"
            onChange={(value) => value && update({}, value)}
          />
        </div>
      )}
      <p className="schedule-preview" aria-live="polite">
        <CalendarClock size={14} aria-hidden />
        <span>
          <strong>
            {words}
            {zoned && ` · ${zone}`}
          </strong>
          {upcoming && (
            <span>
              {' '}
              · next{' '}
              <time dateTime={upcoming.toISOString()}>
                {zoneTime(upcoming, zone)}
              </time>
              {!enabled && ' once enabled'}
            </span>
          )}
          {passed && <span> · this time has passed</span>}
          {interval && (
            <span>
              {' '}
              · counted from when it is {enabled ? 'saved' : 'enabled'}
            </span>
          )}
        </span>
      </p>
    </div>
  );
}
