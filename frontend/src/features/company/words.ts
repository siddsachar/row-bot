import type { CompanyView } from '../../api/types';

/** What joining does, in one line (Settings › Company, not joined). */
export const JOIN_LINE =
  'Joining lets Row-Bot use your company’s models and rules. Your own chats and memory stay on this computer.';

/** Why the last Join or Sign in again didn't join, in plain words. */
export function failureWords(
  failure: NonNullable<CompanyView['failure']>,
  company: string,
): string {
  switch (failure) {
    case 'not_https':
      return 'Use an https:// address. Your company’s server needs a secure connection.';
    case 'invalid_address':
      return 'That isn’t a server address. Check it and try again, for example rowbot.yourcompany.com.';
    case 'not_company_server':
      return 'That doesn’t look like a Row-Bot company server. Check the address with your IT team.';
    case 'unreachable':
      return 'Row-Bot couldn’t reach that address. Check it and your connection, then try again.';
    case 'busy':
      return `${company} is busy. Try again in a minute.`;
    case 'browser':
      return 'Row-Bot couldn’t open your browser. Try again.';
    case 'cancelled':
      return 'Sign-in cancelled.';
    case 'denied':
      return 'The sign-in was cancelled on your company’s page.';
    case 'timed_out':
      return 'The sign-in timed out. Try again when you’re ready.';
    case 'failed':
      return 'Your company’s server didn’t accept the sign-in. Try again.';
    case 'storage_unavailable':
      return 'Row-Bot can’t keep your company’s sign-in: this computer’s keychain is locked or unavailable. Unlock it (on a Mac, in Keychain Access), then try again.';
  }
}

/** After Leave. */
export function noticeWords(
  notice: NonNullable<CompanyView['notice']>,
  company: string,
): string {
  return notice === 'left'
    ? `This Row-Bot left ${company}.`
    : 'This Row-Bot left. Your company may still list it until an admin removes it.';
}

/** "Joined 23 Nov 2026" in this device's way of writing dates. */
export function joinedWords(joinedAt: string | null): string {
  const date = joinedAt ? new Date(joinedAt) : null;
  if (!date || Number.isNaN(date.getTime())) return 'Joined';
  return `Joined ${new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' }).format(date)}`;
}

/** Who signed in: "Ada Lovelace · ada@example.com". */
export function personWords(view: CompanyView): string {
  return (
    [view.name, view.email].filter(Boolean).join(' · ') ||
    'Signed in with your company account'
  );
}

/** The confirm before Leave: what happens, and what stays. */
export function leaveWords(company: string): string {
  return `This Row-Bot stops using ${company}’s models and rules. Your own chats and memory stay on this computer.`;
}

/** The provider id of the company's models (`model:company:<id>`). */
export const COMPANY_PROVIDER = 'company';

/** Settings › Company's line on the company's models (C2). */
export function modelsWords(count: number, company: string): string {
  if (count === 0) return `No models from ${company} yet`;
  return `${count} model${count === 1 ? '' : 's'} from ${company}`;
}

/**
 * The lockdown line (C3a): Settings › Company and the model picker, while
 * Row-Bot uses only what's safe on this computer.
 */
export function lockdownWords(view: CompanyView): string {
  const company = view.company ?? 'your company';
  return view.policy_reason === 'disconnected'
    ? `${company} disconnected this Row-Bot, so it’s using only what’s safe on this computer until you sign in again.`
    : `Row-Bot can’t reach ${company}, so it’s using only what’s safe on this computer until it can.`;
}

/** The tooltip of "Set by your company": the setting and its value. */
export function lockDetail(lock: { title: string; value: string }): string {
  return `${lock.title}: ${lock.value}`;
}

/** Why Join is off on a phone or another computer. */
export function elsewhereWords(computer: string): string {
  return `Join from Row-Bot on ${computer}: the sign-in opens in that computer’s browser.`;
}
