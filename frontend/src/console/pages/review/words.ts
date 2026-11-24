import type { Tone } from '../../../ui/primitives';
import type { ReviewSpace, Shown, SubmissionStatus } from './types';

/** What a fact holds: a longer share is approved only once shortened. */
export const MAX_FACT = 2000;
export const MAX_TITLE = 200;
export const MAX_REASON = 500;

export const SHOWN: { value: Shown; label: string }[] = [
  { value: 'pending', label: 'Waiting' },
  { value: 'approved', label: 'Approved' },
  { value: 'rejected', label: 'Rejected' },
  { value: 'all', label: 'All' },
];

export const STATUS: Record<SubmissionStatus, { tone: Tone; label: string }> = {
  pending: { tone: 'warning', label: 'Waiting' },
  approved: { tone: 'success', label: 'Approved' },
  rejected: { tone: 'danger', label: 'Rejected' },
};

const COUNT = new Intl.NumberFormat();

/** "3 shares are waiting for review." */
export function waiting(count: number) {
  if (count === 0) return 'Nothing is waiting for review.';
  return count === 1
    ? '1 share is waiting for review.'
    : `${COUNT.format(count)} shares are waiting for review.`;
}

/** A share's first words, to name its actions. */
export const short = (text: string) =>
  text.length > 48 ? `${text.slice(0, 47).trimEnd()}…` : text;

/** What approving does, said before it is done. */
export function becomes(space: ReviewSpace | undefined) {
  if (!space) return 'This becomes a fact in its space.';
  return space.kind === 'company'
    ? `This becomes a fact in ${space.name} that everyone in the company can see.`
    : `This becomes a fact in ${space.name} that everyone in ${space.name} can see.`;
}

/** How far over a fact's length a text is, in a sentence; null when it fits. */
export function tooLong(text: string) {
  const over = text.trim().length - MAX_FACT;
  if (over <= 0) return null;
  return `A fact holds up to ${COUNT.format(MAX_FACT)} characters. Shorten this by ${COUNT.format(over)} ${over === 1 ? 'character' : 'characters'} to approve it.`;
}

export const characters = (text: string, most: number) =>
  `${COUNT.format(text.trim().length)} of ${COUNT.format(most)} characters`;
