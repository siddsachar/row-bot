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

/** "about 40 min left", "about 1 h 40 min left", "less than a minute left". */
export function timeLeftWords(seconds: number): string {
  const minutes = Math.round(Math.max(0, seconds) / 60);
  if (minutes < 1) return 'less than a minute left';
  if (minutes < 60) return `about ${minutes} min left`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `about ${hours} h ${rest} min left` : `about ${hours} h left`;
}

/** "step 340 of 1,000" (or "step 340" while the plan isn't known). */
export function stepWords(step: number, total: number | null | undefined) {
  return total
    ? `step ${step.toLocaleString()} of ${total.toLocaleString()}`
    : `step ${step.toLocaleString()}`;
}

/** Why training stopped, and the way out (designs/learning-ux.md §9). */
export const TRAINING_FAILURES: Record<
  string,
  { text: string; action: string }
> = {
  out_of_gpu_memory: {
    text: 'Training stopped: not enough GPU memory. A smaller starting model or shorter tasks fit. Earlier versions are kept.',
    action: 'Use recommended settings',
  },
  out_of_disk: {
    text: 'Training stopped: there isn’t enough free disk space to save its progress. Free some space, then try again.',
    action: 'Try again',
  },
  data_unreadable: {
    text: 'Training stopped: Row-Bot couldn’t read the examples it prepared. Trying again prepares them afresh.',
    action: 'Try again',
  },
  cannot_resume: {
    text: 'Training stopped: it couldn’t continue from its saved steps. Trying again starts it afresh.',
    action: 'Try again',
  },
  engine_error: {
    text: 'Training stopped: something went wrong in the training engine. Earlier versions are kept.',
    action: 'Try again',
  },
};

/** Where the personal model's download comes from, and what is switched off (E14, AGENTS.md). */
export const ENGINE_SOURCES = 'From PyPI, PyTorch and Hugging Face.';
export const ENGINE_TELEMETRY =
  'The training engine’s own usage reporting is switched off.';

/** "3.1 of 6.8 GB": both in the total's unit, so the two numbers compare at a glance. */
export function progressWords(done: number, total: number): string {
  const whole = Math.max(0, total);
  const part = Math.min(Math.max(0, done), whole);
  const unit = whole >= 1_000_000_000 ? 1_000_000_000 : 1_000_000;
  const name = unit === 1_000_000_000 ? 'GB' : 'MB';
  const show = (value: number) =>
    unit === 1_000_000_000
      ? (value / unit).toFixed(1).replace(/\.0$/, '')
      : String(Math.round(value / unit));
  return `${show(part)} of ${show(whole)}\u00a0${name}`;
}

/** "45%": whole percent, never 100% before the last byte. */
export function percentWords(done: number, total: number): string {
  if (!total) return '0%';
  const share = Math.floor((Math.max(0, done) / total) * 100);
  return `${Math.min(done >= total ? 100 : 99, share)}%`;
}

/** Why the personal model's download stopped (designs/learning-ux.md §9). */
export const DOWNLOAD_FAILURES: Record<string, string> = {
  network:
    'The download stopped: Row-Bot couldn’t reach the download server. Check the internet connection, then try again.',
  mismatch:
    'The download stopped: a file didn’t match the one Row-Bot expects, so it wasn’t used. Try again.',
  refused:
    'The download stopped: the download server refused it. Try again later.',
  install_failed: 'The training engine couldn’t be installed. Try again.',
  check_failed:
    'The training engine was installed but didn’t start. Try again; if it happens again, remove it in Learning settings and download it afresh.',
  no_cuda:
    'The training engine couldn’t use the graphics card. Update your NVIDIA driver, then try again.',
};
