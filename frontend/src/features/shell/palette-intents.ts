/**
 * What people mean in ⌘K (Phase 18, U10): intents named the way people ask
 * ("connect a model", "phone", "connect telegram"), each going to the exact
 * place it is done. Matching is by words, in any order, so "model connect"
 * and "connect my model" find the same thing; a word still being typed
 * matches the start of one.
 */

export type PaletteIntent = {
  id: string;
  label: string;
  /** Where it opens, in words. */
  detail: string;
  /** An in-app path, a Settings row where possible. */
  href: string;
  phrases: readonly string[];
};

const channel = (id: string, name: string, more: string[] = []) => ({
  id: `connect-${id}`,
  label: `Connect ${name}`,
  detail: 'Channels settings',
  href: `/settings/channels#${id}`,
  phrases: [id, `connect ${id}`, `${id} bot`, ...more],
});

export const PALETTE_INTENTS: readonly PaletteIntent[] = [
  {
    id: 'connect-model',
    label: 'Connect a model provider',
    detail: 'Providers settings',
    href: '/settings/providers',
    phrases: [
      'connect a model',
      'model provider',
      'api key',
      'provider key',
      'local model',
      'connect openai',
      'connect anthropic',
      'connect ollama',
      'connect chatgpt',
      'connect claude',
    ],
  },
  {
    id: 'choose-model',
    label: 'Choose your default model',
    detail: 'Models settings',
    href: '/settings/models#default-model',
    phrases: ['choose model', 'default model', 'brain model', 'which model'],
  },
  {
    id: 'connect-phone',
    label: 'Connect a phone or computer',
    detail: 'Devices & remote access',
    href: '/settings/access#connect',
    phrases: [
      'phone',
      'connect phone',
      'phone access',
      'connect device',
      'another computer',
      'laptop',
      'qr code',
    ],
  },
  channel('telegram', 'Telegram'),
  channel('slack', 'Slack'),
  channel('discord', 'Discord'),
  channel('whatsapp', 'WhatsApp'),
  channel('sms', 'SMS', ['text message', 'connect twilio']),
  {
    id: 'connect-google',
    label: 'Connect Google',
    detail: 'Gmail and Calendar · Accounts settings',
    href: '/settings/accounts#google',
    phrases: ['google', 'connect google', 'google calendar'],
  },
  {
    id: 'connect-github',
    label: 'Connect GitHub',
    detail: 'Accounts settings',
    href: '/settings/accounts#github',
    phrases: ['github', 'connect github'],
  },
  {
    id: 'connect-x',
    label: 'Connect X',
    detail: 'Accounts settings',
    href: '/settings/accounts#x',
    phrases: ['twitter', 'connect twitter', 'connect x'],
  },
  {
    id: 'add-mcp',
    label: 'Add an MCP server',
    detail: 'MCP settings',
    href: '/settings/mcp#mcp-servers',
    phrases: ['connect mcp server', 'mcp server', 'model context protocol'],
  },
  {
    id: 'install-skill',
    label: 'Find and install skills',
    detail: 'Skills settings',
    href: '/settings/skills#public-skills',
    phrases: ['install skill', 'find skill', 'connect skill', 'skill hub'],
  },
  {
    id: 'install-plugin',
    label: 'Find and install plugins',
    detail: 'Plugins settings',
    href: '/settings/plugins#plugin-marketplace',
    phrases: ['install plugin', 'find plugin', 'connect plugin', 'extension'],
  },
  {
    id: 'backup',
    label: 'Back up or restore your data',
    detail: 'Data settings',
    href: '/settings/data#backup',
    phrases: ['back up', 'backup', 'restore', 'move to new computer'],
  },
];

/** Words that carry no meaning in a request. */
const STOP = new Set([
  'a',
  'an',
  'the',
  'my',
  'me',
  'to',
  'for',
  'of',
  'i',
  'want',
  'please',
  'how',
  'do',
  'can',
  'up',
  'with',
  'new',
  'some',
]);

/** One verb for the many ways people say it. */
const SAME = new Map<string, string>([
  ...['set', 'setup', 'add', 'link', 'pair', 'attach', 'hook', 'configure'].map(
    (word) => [word, 'connect'] as const,
  ),
  ...['change', 'switch', 'pick', 'select', 'swap'].map(
    (word) => [word, 'choose'] as const,
  ),
  ...['mobile', 'tablet', 'iphone', 'android', 'cellphone'].map(
    (word) => [word, 'phone'] as const,
  ),
  ['gmail', 'google'],
]);

/** Meaningful words, lower case, singular, with one name per verb. */
export function words(text: string): string[] {
  return text
    .toLowerCase()
    .split(/[^a-z0-9]+/)
    .filter((word) => word && !STOP.has(word))
    .map((word) =>
      word.length > 3 && word.endsWith('s') && !word.endsWith('ss')
        ? word.slice(0, -1)
        : word,
    )
    .map((word) => SAME.get(word) ?? word);
}

/** A whole phrase named: above every literal match. */
export const INTENT_FULL = 2000;
/** Several words, all in the phrase: above literal matches too. */
const INTENT_PARTIAL = 1500;
/** One word from a longer phrase: about a literal keyword match. */
const INTENT_WORD = 1000;

const names = (word: string, query: string) =>
  word === query || (query.length >= 3 && word.startsWith(query));

/**
 * How well a query names one of the phrases, or null. Every query word must
 * name a word of the phrase; naming all of them exactly ranks first, while a
 * word still being typed ranks as a partial match.
 */
export function intentScore(
  query: string,
  phrases: readonly string[],
): number | null {
  const asked = words(query);
  if (!asked.length) return null;
  let best: number | null = null;
  for (const phrase of phrases) {
    const said = words(phrase);
    if (!said.length || !asked.every((q) => said.some((w) => names(w, q))))
      continue;
    const covered =
      said.filter((w) => asked.some((q) => names(w, q))).length / said.length;
    const score = said.every((w) => asked.includes(w))
      ? INTENT_FULL + Math.min(asked.length, 4) * 10
      : asked.length > 1
        ? INTENT_PARTIAL + covered * 200
        : INTENT_WORD + covered * 100;
    if (best === null || score > best) best = score;
  }
  return best;
}

/**
 * "turn on X", "enable X", "X off": the state asked for and what it names.
 * `want` is null when no state is asked for.
 */
export function switchRequest(query: string): {
  want: boolean | null;
  rest: string;
} {
  const text = query.trim().toLowerCase();
  const verb = /^(?:turn|switch|toggle|set)\s+(on|off)\s+(.+)$/.exec(text);
  if (verb) return { want: verb[1] === 'on', rest: verb[2] };
  const single = /^(enable|activate|show|disable|deactivate|hide)\s+(.+)$/.exec(
    text,
  );
  if (single)
    return {
      want: ['enable', 'activate', 'show'].includes(single[1]),
      rest: single[2],
    };
  const after = /^(.+?)\s+(on|off)$/.exec(text);
  if (after) return { want: after[2] === 'on', rest: after[1] };
  return { want: null, rest: text };
}
