import { useId, useState } from 'react';
import { CalendarClock } from 'lucide-react';
import { Input, Segmented, Select } from '../../ui/primitives';
import { absoluteTime, parseTimestamp } from '../../ui/format';
import { cronDays, scheduleWords } from '../home/home-format';

type Mode = 'manual' | 'once' | 'repeat';
type Frequency = 'daily' | 'weekdays' | 'days' | 'hours' | 'minutes' | 'cron';
export type ScheduleDraft = {
  mode: Mode;
  frequency: Frequency;
  time: string;
  days: string[];
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
    const simple =
      fields.length === 5 &&
      /^\d{1,2}$/.test(minute) &&
      /^\d{1,2}$/.test(hour) &&
      dom === '*' &&
      month === '*' &&
      Number(minute) < 60 &&
      Number(hour) < 24;
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

/** The next time a daily or day-of-week schedule fires, from the builder. */
export function nextFire(draft: ScheduleDraft, now: Date): Date | null {
  if (draft.mode === 'once') return parseTimestamp(draft.at);
  if (
    draft.mode !== 'repeat' ||
    !['daily', 'weekdays', 'days'].includes(draft.frequency)
  )
    return null;
  const [hour, minute] = draft.time.split(':').map(Number);
  const allowed =
    draft.frequency === 'daily'
      ? DAY_ORDER
      : draft.frequency === 'weekdays'
        ? DAY_ORDER.slice(0, 5)
        : draft.days;
  for (let offset = 0; offset < 8; offset += 1) {
    const candidate = new Date(now);
    candidate.setDate(now.getDate() + offset);
    candidate.setHours(hour || 0, minute || 0, 0, 0);
    const key = DAY_ORDER[(candidate.getDay() + 6) % 7];
    if (candidate > now && allowed.includes(key)) return candidate;
  }
  return null;
}

export default function ScheduleBuilder({
  schedule,
  at,
  enabled,
  onChange,
  now = new Date(),
}: {
  schedule: string | null;
  at: string | null;
  enabled: boolean;
  onChange: (value: { schedule: string | null; at: string | null }) => void;
  now?: Date;
}) {
  const id = useId();
  const [draft, setDraft] = useState(() => parseSchedule(schedule, at));
  const update = (patch: Partial<ScheduleDraft>) => {
    const next = { ...draft, ...patch };
    setDraft(next);
    onChange(buildSchedule(next));
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
          : scheduleWords(built.schedule, built.at);
  // A one-off run already names its time; say so only when it has passed.
  const upcoming = draft.mode === 'once' ? null : nextFire(draft, now);
  const passed =
    draft.mode === 'once' &&
    (parseTimestamp(draft.at)?.getTime() ?? Infinity) <= now.getTime();
  const interval =
    draft.mode === 'repeat' &&
    (draft.frequency === 'hours' || draft.frequency === 'minutes');
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
            required
            value={draft.at}
            onChange={(event) => update({ at: event.target.value })}
          />
        </label>
      )}
      {draft.mode === 'repeat' && (
        <>
          <label className="schedule-field" htmlFor={`${id}-frequency`}>
            <span>Repeats</span>
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
          {['daily', 'weekdays', 'days'].includes(draft.frequency) && (
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
      <p className="schedule-preview" aria-live="polite">
        <CalendarClock size={14} aria-hidden />
        <span>
          <strong>{words}</strong>
          {upcoming && (
            <span>
              {' '}
              · next{' '}
              <time dateTime={upcoming.toISOString()}>
                {absoluteTime(upcoming)}
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
