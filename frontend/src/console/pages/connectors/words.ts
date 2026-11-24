import type { Tone } from '../../../ui/primitives';
import { ApiError } from '../../api';
import { count, problem } from '../../words';
import type { Connector, ConnectorKind } from './types';

/** The server README's connector setup (Entra and Google). */
export const SETUP_GUIDE =
  'https://github.com/siddsachar/row-bot/blob/main/deploy/org/README.md#connectors';

/** A Microsoft tenant or application id, as an input's pattern. */
export const GUID =
  '[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}';

export const KINDS: Record<ConnectorKind, string> = {
  microsoft: 'SharePoint or OneDrive',
  google: 'Google Drive',
};

/** Where a synced file's own permissions live, for "… can open it in SharePoint". */
export const SOURCE: Record<ConnectorKind, string> = {
  microsoft: 'SharePoint',
  google: 'Google Drive',
};

export const STATES: Record<Connector['state'], { tone: Tone; label: string }> =
  {
    active: { tone: 'success', label: 'Connected' },
    paused: { tone: 'neutral', label: 'Paused' },
    error: { tone: 'danger', label: 'Stopped' },
  };

/** Said wherever a change makes the saver the admin a connector syncs as. */
export const SYNCS_AS_YOU =
  'Saving makes you the admin it syncs as: the files it reads from now on are added in your name, and their embeddings count as your model use.';

/** "1 file", "12 files". */
export function plural(value: number, one: string, many = `${one}s`) {
  return `${count(value)} ${value === 1 ? one : many}`;
}

/** Whether a connector needs a look: someone unmatched, files failing, or a limit reached. */
export function needsALook(connector: Connector) {
  return (
    connector.unmapped_files > 0 ||
    connector.failing > 0 ||
    connector.limited > 0
  );
}

/** Who couldn't be matched, by the kind of key the server gives them. */
const KEY_KINDS: [string, string][] = [
  ['user:', 'Person with no email'],
  ['microsoft-group:', 'Microsoft group'],
  ['google-group:', 'Google group'],
  ['sharepoint-group:', 'SharePoint site group'],
  ['link:', 'Sharing link'],
  ['google-domain:', 'Everyone in a domain'],
  ['google-anyone', 'Sharing link'],
  ['too-many-readers', 'Shared too widely'],
  ['permission:', 'Other sharing'],
];

export function keyKind(key: string) {
  return (
    KEY_KINDS.find(([prefix]) => key.startsWith(prefix))?.[1] ?? 'Other sharing'
  );
}

const LINKS: Record<string, string> = {
  'link:organization': 'Anyone in the organisation with the link',
  'link:anonymous': 'Anyone with the link',
  'link:users': 'Specific people with the link',
  'google-anyone': 'Anyone with the link',
  'too-many-readers': 'More than 1,000 people and groups',
};

/** A principal map key's name when the server gave none (a key mapped before). */
export function keyName(key: string) {
  if (LINKS[key]) return LINKS[key];
  const prefix = KEY_KINDS.find(([start]) => key.startsWith(start))?.[0];
  return prefix ? `${keyKind(key)} ${key.slice(prefix.length)}` : key;
}

/** The permissions note without its last sentence, which the details show on their own line. */
export function noteWithoutChecked(note: string) {
  return note.replace(/\s*Permissions last checked at .*$/, '');
}

const FIELDS: Record<string, string> = {
  name: 'Check the name: 1 to 160 characters.',
  tenant_id:
    'Check the tenant id: it is a GUID, as the app registration’s Overview shows it.',
  client_id:
    'Check the application (client) id: it is a GUID, as the app registration’s Overview shows it.',
  client_secret:
    'Check the client secret: paste its value exactly as Entra showed it.',
  drive_id: 'Check the drive id: copy it exactly, with no spaces.',
  folder_id: 'Check the folder id: copy it exactly, with no spaces.',
  subject: 'Check the user it acts as: it must be an email address.',
  service_account:
    'That key file can’t be used. Download a new JSON key for the service account and add it again.',
  principal_map:
    'Check the group names: each is a Row-Bot group, with no space at either end.',
};

/** What went wrong saving a connector, naming the field the server refused. */
export function connectorProblem(error: unknown, kind?: ConnectorKind): string {
  if (error instanceof ApiError && error.code === 'space_not_found')
    return 'That space is no longer on the server. Choose another one.';
  if (error instanceof ApiError && error.status === 422) {
    const detail = Array.isArray(error.body.detail) ? error.body.detail : [];
    const loc = (detail[0] as { loc?: unknown[] } | undefined)?.loc ?? [];
    const field = typeof loc[1] === 'string' ? loc[1] : '';
    if (FIELDS[field]) return FIELDS[field];
    // A whole-body refusal is the key file's, once the form checked the rest.
    if (detail.length && kind === 'google') return FIELDS.service_account;
  }
  return problem(error);
}
