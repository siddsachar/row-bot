/**
 * Learning's words and numbers, one name per thing (designs/learning-ux.md, "Words").
 * Never shown: self-improvement, RSI, candidate, adapter, LoRA, eval, audit, cron.
 */
import type { LearningCommand } from '../../api/types';

export type TeachCategory = NonNullable<LearningCommand['category']>;

export const TEACH_CATEGORIES: { value: TeachCategory; label: string }[] = [
  { value: 'wrong_answer', label: 'Wrong answer' },
  { value: 'wrong_tool_or_step', label: 'Wrong tool or step' },
  { value: 'ignored_instructions', label: 'Ignored my instructions' },
  { value: 'other', label: 'Other' },
];

/** 2,000,000 → "2M", 240,000 → "240K", 1,100,000 → "1.1M". */
export function tokenWords(count: number): string {
  const value = Math.max(0, Math.round(count));
  const short = (amount: number, unit: string) =>
    `${Number.isInteger(amount) ? amount : amount.toFixed(1).replace(/\.0$/, '')}${unit}`;
  if (value >= 1_000_000) return short(Math.round(value / 100_000) / 10, 'M');
  if (value >= 1_000) return short(Math.round(value / 1_000), 'K');
  return String(value);
}

/** 4,570,000,000 → "4.6 GB", 43,700,000 → "44 MB" (number and unit kept together). */
export function sizeWords(bytes: number): string {
  const value = Math.max(0, bytes);
  if (value >= 1_000_000_000) {
    const amount = value / 1_000_000_000;
    return `${amount >= 100 ? Math.round(amount) : amount.toFixed(1).replace(/\.0$/, '')}\u00a0GB`;
  }
  if (value >= 1_000_000)
    return `${Math.max(1, Math.round(value / 1_000_000))}\u00a0MB`;
  if (value >= 1_000)
    return `${Math.max(1, Math.round(value / 1_000))}\u00a0KB`;
  return value === 1 ? '1\u00a0byte' : `${value}\u00a0bytes`;
}

/** The monthly limit's choices; the saved value shows even when it isn't one of them. */
export const LIMIT_CHOICES = [
  500_000, 1_000_000, 2_000_000, 5_000_000, 10_000_000,
];

export function limitOptions(saved: number) {
  const values = LIMIT_CHOICES.includes(saved)
    ? LIMIT_CHOICES
    : [...LIMIT_CHOICES, saved].sort((left, right) => left - right);
  return [
    ...values.map((value) => ({
      value: String(value),
      label: value === 0 ? 'No model calls' : `${tokenWords(value)} tokens`,
    })),
    ...(values.includes(0) ? [] : [{ value: '0', label: 'No model calls' }]),
  ];
}

/** "About 20 tests of a change" (a change's test takes about 100K tokens). */
export function limitMeaning(limit: number): string {
  if (limit <= 0)
    return 'Learning makes no model calls. Local models still work.';
  const tests = Math.max(1, Math.round(limit / 100_000));
  return `About ${tests} ${tests === 1 ? 'test' : 'tests'} of a change. Local models don't count.`;
}

/** "1:00 AM" (the reader's own clock), as Dream Cycle's hours show. */
export function hourWords(hour: number): string {
  return new Intl.DateTimeFormat(undefined, {
    hour: 'numeric',
    minute: '2-digit',
  }).format(new Date(2000, 0, 1, hour));
}

export function quietHoursWords(start: number, end: number): string {
  return `${hourWords(start)}–${hourWords(end)}`;
}

/** "1 Nov" for a local ISO date. */
export function dayWords(isoDate: string): string {
  const [year, month, day] = isoDate.split('-').map(Number);
  if (!year || !month || !day) return isoDate;
  return new Intl.DateTimeFormat(undefined, {
    day: 'numeric',
    month: 'short',
  }).format(new Date(year, month - 1, day));
}

export function plural(count: number, one: string, many = `${one}s`) {
  return `${count.toLocaleString()} ${count === 1 ? one : many}`;
}
