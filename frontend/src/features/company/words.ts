import type {
  CompanyDocument,
  CompanyDocumentsView,
  CompanyView,
} from '../../api/types';
import type { Tone } from '../../ui/primitives';

/** What joining does, in one line (Settings › Company, not joined). */
export const JOIN_LINE =
  'Joining lets Row-Bot use your company’s models and rules. Your own chats and memory stay on this computer.';

/** A company server's host, as Row-Bot names a company (`https://rowbot.acme.com` → `rowbot.acme.com`). */
export function hostOf(origin: string): string {
  try {
    return new URL(origin).hostname || origin;
  } catch {
    return origin;
  }
}

/** Settings › Company when IT set the company server (C5), not joined yet. */
export function pinnedWords(company: string): string {
  return `Your IT department set up this Row-Bot for ${company}. ${JOIN_LINE}`;
}

/** IT set a company server, but Row-Bot is joined to another one (C5). */
export function movedWords(pinned: string, company: string): string {
  return `Sign in to ${pinned} to keep using your company’s models and rules. Row-Bot leaves ${company} first. Your own chats and memory stay on this computer.`;
}

/** IT set a company server address Row-Bot can't use (C5); it is ignored. */
export const PIN_INVALID =
  'Your IT department set a company server address that isn’t valid';

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
export function lockdownWords(
  view: Pick<CompanyView, 'company' | 'policy_reason'>,
): string {
  const company = view.company ?? 'your company';
  return view.policy_reason === 'disconnected'
    ? `${company} disconnected this Row-Bot, so it’s using only what’s safe on this computer until you sign in again.`
    : `Row-Bot can’t reach ${company}, so it’s using only what’s safe on this computer until it can.`;
}

/** Why a disconnected Row-Bot can't use its company, and what stays (C1b). */
export function disconnectedWords(company: string): string {
  return `${company} no longer accepts this Row-Bot. That happens when an admin removes it, your account is paused, or it’s been a month since you last signed in. Your own chats and memory are still here.`;
}

/** Joined, but the company's server can't be used now (C1b's words). */
export function unreachableWords(
  company: string,
  reason: string | null,
): string {
  if (reason === 'busy')
    return `${company} is busy. Row-Bot stays joined; try again in a minute.`;
  if (reason === 'storage_unavailable')
    return failureWords('storage_unavailable', company);
  return `Can’t reach ${company} right now. Row-Bot stays joined.`;
}

/** Knowledge home's Company scope (B8): a graph with nothing in it yet. */
export const NO_DOCUMENTS = 'Your company hasn’t added documents yet';

/** The Company scope's filters found nothing. */
export const NO_MATCH = 'Nothing matches';

/** The quiet line an owner of a space sees on its entities (they curate it there). */
export const EDITED_IN_CONSOLE = 'Edited in the admin console';

/** Where the company's knowledge mentions an entity: "From Sales: Accounts 2026". */
export function citationWords(citation: {
  space: string;
  document: string | null;
}): string {
  return citation.document
    ? `From ${citation.space}: ${citation.document}`
    : `From ${citation.space}`;
}

/** The note on a graph larger than what is drawn: "Showing 300 of 1,204". */
export function showingWords(shown: number, total: number): string {
  return `Showing ${shown.toLocaleString()} of ${total.toLocaleString()}`;
}

/** The tooltip of "Set by your company": the setting and its value. */
export function lockDetail(lock: { title: string; value: string }): string {
  return `${lock.title}: ${lock.value}`;
}

/** Why Join is off on a phone or another computer. */
export function elsewhereWords(computer: string): string {
  return `Join from Row-Bot on ${computer}: the sign-in opens in that computer’s browser.`;
}

/** What a joined Row-Bot tells its company (C4), in one line. */
export function auditWords(company: string): string {
  return `Row-Bot tells ${company} which risky actions it takes, never what’s in your chats.`;
}

/** The fields it sends, in plain words, behind the line. */
export function auditFields(
  company: string,
): { label: string; help: string }[] {
  return [
    {
      label: 'Actions that needed approval',
      help: 'The tool’s name and app, the approval level, how risky it was, where it ran (a chat, workflow, agent or channel), who decided and how it ended.',
    },
    {
      label: 'Exports',
      help: 'That you exported a chat or backed up your data, and how many chats.',
    },
    {
      label: 'Lockdowns',
      help: `That Row-Bot couldn’t reach ${company} for longer than its rules allow, and for how many days.`,
    },
    {
      label: 'Never',
      help: 'What you or Row-Bot wrote, your files, what a tool was asked to do or what it found.',
    },
  ];
}

/** A file's size in words: "24 KB", "1.5 MB". */
export function sizeWords(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.ceil(bytes / 1024)} KB`;
  const megabytes = bytes / (1024 * 1024);
  return `${Number.isInteger(megabytes) ? megabytes : megabytes.toFixed(1)} MB`;
}

/** The types a company's server reads: ".docx, .md or .txt". */
export function typesWords(types: readonly string[]): string {
  return types.length > 1
    ? `${types.slice(0, -1).join(', ')} or ${types[types.length - 1]}`
    : (types[0] ?? '');
}

// The server's reasons for an admin, said to a space owner.
const OWNER_REASONS: Record<string, string> = {
  'Add an embeddings model in Models':
    'Found by its words for now: your company’s admin hasn’t added a search model yet.',
  'Add a chat model in Models':
    'Its knowledge waits for your company’s admin to add a chat model.',
};

function reasonWords(reason: string): string {
  return OWNER_REASONS[reason] ?? reason;
}

/**
 * A company document's status (B3): its word and tone, and why it waits or
 * failed, from the server's status and reason (B1a) and how far its
 * knowledge got (B7).
 */
export function documentStatus(document: CompanyDocument): {
  tone: Tone;
  label: string;
  detail: string | null;
} {
  const reason = document.status_reason;
  switch (document.status) {
    case 'queued':
      return reason
        ? { tone: 'warning', label: 'Waiting', detail: reasonWords(reason) }
        : { tone: 'info', label: 'Waiting to be read', detail: null };
    case 'processing':
      return { tone: 'info', label: 'Reading', detail: null };
    case 'failed':
      return {
        tone: 'danger',
        label: 'Couldn’t be read',
        detail: reason ?? 'Replace it with a file that can be read.',
      };
    case 'ready':
      break;
  }
  const graph = document.graph_reason;
  switch (document.graph_status) {
    case 'pending':
    case 'extracting':
      return { tone: 'info', label: 'Finding knowledge', detail: null };
    case 'waiting':
      return {
        tone: 'warning',
        label: 'Searchable',
        detail: graph ? reasonWords(graph) : null,
      };
    case 'failed':
      return {
        tone: 'warning',
        label: 'Searchable',
        detail: graph
          ? `Its knowledge couldn’t be read: ${graph}`
          : 'Its knowledge couldn’t be read.',
      };
    default:
      return { tone: 'success', label: 'Ready', detail: null };
  }
}

/** Whether the company's server is still working on a document (the list reads itself again). */
export function documentWorking(document: CompanyDocument): boolean {
  return (
    (document.status === 'queued' && !document.status_reason) ||
    document.status === 'processing' ||
    (document.status === 'ready' &&
      (document.graph_status === 'pending' ||
        document.graph_status === 'extracting'))
  );
}

/** How an upload, replace or delete went, for the file named. */
export function outcomeWords(
  outcome: NonNullable<CompanyDocumentsView['outcome']>,
  name: string,
  view: Pick<CompanyDocumentsView, 'max_upload_bytes' | 'supported_types'>,
): string {
  switch (outcome) {
    case 'uploaded':
      return `${name} was added. Your company’s server reads it next.`;
    case 'replaced':
      return `${name} replaced the earlier file. Your company’s server reads it next.`;
    case 'removed':
      return `${name} was deleted.`;
    case 'too_large':
      return `${name} is larger than the ${sizeWords(view.max_upload_bytes)} your company allows.`;
    case 'unsupported_type':
      return `${name} can’t be added. Add ${typesWords(view.supported_types)} files.`;
    case 'invalid_file':
      return `${name} can’t be added: your company’s server doesn’t accept its name. Rename it, then try again.`;
    case 'not_owner':
      return 'You no longer own this space, so its documents can’t be changed here.';
    case 'not_found':
      return 'That document or space isn’t there any more.';
  }
}
