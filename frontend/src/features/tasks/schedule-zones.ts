import { serverTimezone } from '../../ui/format';

/**
 * A schedule's own timezone (N9). Wall-clock times are carried as UTC epoch
 * milliseconds of the same fields ("wall" values), so day arithmetic never meets
 * daylight saving; `zonedInstant` places one in its zone by the same rules as
 * the scheduler: a time that doesn't exist that day (the clocks go forward over
 * it) is the next valid minute, and a time that happens twice is the first.
 */

const MINUTE = 60_000;
const DAY = 86_400_000;
const formatters = new Map<string, Intl.DateTimeFormat>();

function parts(zone: string) {
  let format = formatters.get(zone);
  if (!format) {
    format = new Intl.DateTimeFormat('en-US', {
      timeZone: zone,
      hourCycle: 'h23',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
    formatters.set(zone, format);
  }
  return format;
}

/** The wall-clock time of an instant in a zone, as a wall value. */
export function wallAt(instant: number, zone: string): number {
  const found: Record<string, number> = {};
  for (const part of parts(zone).formatToParts(new Date(instant)))
    if (part.type !== 'literal') found[part.type] = Number(part.value);
  return Date.UTC(
    found.year,
    found.month - 1,
    found.day,
    found.hour % 24,
    found.minute,
    found.second,
  );
}

/** The instant a wall value means in a zone (see above). */
export function zonedInstant(wall: number, zone: string): Date {
  for (let probe = wall, step = 0; step <= 24 * 60; step += 1) {
    const offsets = new Set(
      [probe - DAY, probe, probe + DAY].map(
        (near) => wallAt(near, zone) - near,
      ),
    );
    const valid = [...offsets]
      .map((offset) => probe - offset)
      .filter((instant) => wallAt(instant, zone) === probe)
      .sort((a, b) => a - b);
    if (valid.length) return new Date(valid[0]);
    probe = Math.floor(probe / MINUTE) * MINUTE + MINUTE;
  }
  return new Date(wall);
}

/** Whether this browser knows the zone. */
export function isZone(zone: string | null | undefined): zone is string {
  if (!zone) return false;
  try {
    parts(zone);
    return true;
  } catch {
    return false;
  }
}

/**
 * The zone a new schedule starts with: this device's, where the person is, else
 * the Row-Bot computer's. A hosted Row-Bot's server runs on Etc/UTC, so "Once at
 * 9" made from a phone must not mean 9 UTC.
 */
export function defaultZone(): string {
  const device = Intl.DateTimeFormat().resolvedOptions().timeZone;
  if (isZone(device)) return device;
  const server = serverTimezone();
  return isZone(server) ? server : 'UTC';
}

/** Every zone this browser knows, with *current* first kept even if it isn't listed. */
export function zoneNames(current: string): string[] {
  let names: string[];
  try {
    names = (
      Intl as unknown as { supportedValuesOf(key: string): string[] }
    ).supportedValuesOf('timeZone');
  } catch {
    names = [];
  }
  return names.includes(current) ? names : [current, ...names];
}

/** "23 Oct 2026, 09:00" in the zone's own wall-clock time. */
export function zoneTime(instant: Date, zone: string, locale?: string) {
  return new Intl.DateTimeFormat(locale, {
    timeZone: zone,
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(instant);
}

/** "09:00" of an ISO time with an offset, as it read where it was written. */
export function wallTimeOf(iso: string | null | undefined): string {
  return /T(\d{2}:\d{2})/.exec(iso ?? '')?.[1] ?? '';
}
