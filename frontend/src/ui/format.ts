/** Presentation helpers that turn system values into human text. */

const RELATIVE_STEPS: [Intl.RelativeTimeFormatUnit, number][] = [
  ['second', 60],
  ['minute', 60],
  ['hour', 24],
  ['day', 7],
  ['week', 4.34524],
  ['month', 12],
  ['year', Number.POSITIVE_INFINITY],
];

/**
 * Parse an ISO-8601 timestamp or epoch seconds. Offset-less date-times are
 * local wall-clock values (the backend writes `datetime.now().isoformat()`),
 * which is how JavaScript parses them. Fractions are trimmed to milliseconds
 * because some engines reject microsecond precision.
 */
export function parseTimestamp(value: string | number | null | undefined) {
  if (value == null || value === '') return null;
  if (typeof value === 'number')
    return Number.isFinite(value) ? new Date(value * 1000) : null;
  const text = value.trim().replace(/(\.\d{3})\d+/, '$1');
  const parsed = new Date(text);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

/** "just now", "5 min ago", "yesterday", "3 weeks ago", "in 2 hours". */
export function relativeTime(
  value: string | number | Date | null | undefined,
  now: Date = new Date(),
  locale?: string,
): string {
  const date = value instanceof Date ? value : parseTimestamp(value);
  if (!date) return 'Unknown';
  let delta = (date.getTime() - now.getTime()) / 1000;
  if (Math.abs(delta) < 45) return 'just now';
  const format = new Intl.RelativeTimeFormat(locale, {
    numeric: 'auto',
    style: 'long',
  });
  for (const [unit, size] of RELATIVE_STEPS) {
    if (Math.abs(delta) < size) return format.format(Math.round(delta), unit);
    delta /= size;
  }
  return format.format(Math.round(delta), 'year');
}

/** Full local date and time for tooltips and `<time title>`. */
export function absoluteTime(
  value: string | number | Date | null | undefined,
  locale?: string,
): string {
  const date = value instanceof Date ? value : parseTimestamp(value);
  if (!date) return '';
  return new Intl.DateTimeFormat(locale, {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(date);
}

const TOKEN_LABELS: Record<string, string> = {
  api_key: 'API key',
  oauth: 'OAuth',
  mcp: 'MCP',
  url: 'URL',
  http: 'HTTP',
  https: 'HTTPS',
  streamable_http: 'HTTP',
  sse: 'SSE',
  stdio: 'Local process',
  third_party_router: 'Router',
  local_private: 'Private · on device',
  cloud_provider: 'Cloud',
  subscription: 'Subscription',
  ui: 'UI',
  id: 'ID',
  ai: 'AI',
  llm: 'LLM',
  openai: 'OpenAI',
  codex: 'ChatGPT / Codex',
  ollama: 'Ollama',
};

/**
 * "OpenAI API key", not "OpenAI API API key": a provider named after its API
 * already says "API" (B116).
 */
export function apiKeyLabel(provider: string | null | undefined): string {
  const name = String(provider ?? '').trim() || 'Provider';
  return /\bAPI$/i.test(name) ? `${name} key` : `${name} API key`;
}

/** Translate an internal enum or identifier into sentence-case words. */
export function humanizeToken(value: string | null | undefined): string {
  const raw = String(value ?? '').trim();
  if (!raw) return '';
  const known = TOKEN_LABELS[raw.toLowerCase()];
  if (known) return known;
  const words = raw
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .split(/[\s_\-.:/]+/)
    .filter(Boolean)
    .map((word) => TOKEN_LABELS[word.toLowerCase()] ?? word.toLowerCase());
  if (!words.length) return '';
  const [first, ...rest] = words;
  return [first.charAt(0).toUpperCase() + first.slice(1), ...rest].join(' ');
}

export type ShortcutPlatform = 'mac' | 'other';

export function detectShortcutPlatform(): ShortcutPlatform {
  if (typeof navigator === 'undefined') return 'other';
  const source =
    (navigator as Navigator & { userAgentData?: { platform?: string } })
      .userAgentData?.platform ||
    navigator.platform ||
    navigator.userAgent;
  return /mac|iphone|ipad|ipod/i.test(source) ? 'mac' : 'other';
}

const KEY_SYMBOLS: Record<ShortcutPlatform, Record<string, string>> = {
  mac: {
    mod: '⌘',
    meta: '⌘',
    ctrl: '⌃',
    control: '⌃',
    alt: '⌥',
    option: '⌥',
    shift: '⇧',
  },
  other: {
    mod: 'Ctrl',
    meta: 'Win',
    ctrl: 'Ctrl',
    control: 'Ctrl',
    alt: 'Alt',
    option: 'Alt',
    shift: 'Shift',
  },
};
const SHARED_KEYS: Record<string, string> = {
  enter: '↵',
  return: '↵',
  escape: 'Esc',
  esc: 'Esc',
  arrowup: '↑',
  arrowdown: '↓',
  arrowleft: '←',
  arrowright: '→',
  backspace: '⌫',
  delete: 'Del',
  space: 'Space',
  tab: 'Tab',
  period: '.',
  comma: ',',
  slash: '/',
};

/** "Mod+K" → ["⌘", "K"] on macOS and ["Ctrl", "K"] elsewhere. */
export function shortcutKeys(
  shortcut: string,
  platform: ShortcutPlatform = detectShortcutPlatform(),
): string[] {
  return shortcut
    .split('+')
    .map((part) => part.trim())
    .filter(Boolean)
    .map((part) => {
      const key = part.toLowerCase();
      return (
        KEY_SYMBOLS[platform][key] ??
        SHARED_KEYS[key] ??
        (part.length === 1 ? part.toUpperCase() : part)
      );
    });
}

/** Value for `aria-keyshortcuts`, e.g. "Meta+K" or "Control+K". */
export function ariaKeyShortcut(
  shortcut: string,
  platform: ShortcutPlatform = detectShortcutPlatform(),
): string {
  return shortcut
    .split('+')
    .map((part) => part.trim())
    .filter(Boolean)
    .map((part) => {
      const key = part.toLowerCase();
      if (key === 'mod') return platform === 'mac' ? 'Meta' : 'Control';
      if (key === 'ctrl') return 'Control';
      if (key === 'esc') return 'Escape';
      if (key === 'period') return 'Period';
      return part.length === 1 ? part.toUpperCase() : part;
    })
    .join('+');
}

/** "****4c99" → "····4c99": the saved secret's last characters, never more. */
export function maskedTail(fingerprint: string | null | undefined): string {
  const text = String(fingerprint ?? '').trim();
  if (!text) return '';
  const tail = text.replace(/^[*•·]+/, '');
  return tail ? `····${tail}` : '····';
}

const CREDENTIAL_SOURCES: Record<string, string> = {
  keyring: 'in keychain',
  encrypted_file: 'encrypted on this device',
  environment: 'from environment',
  secret_file: 'server secret file',
  session: 'this session only',
  legacy_plaintext: 'plain file (legacy)',
};

/** Where a saved secret lives, in words ("in keychain"). */
export function credentialSourceLabel(source: string | null | undefined) {
  const key = String(source ?? '').trim();
  if (!key || key === 'none') return '';
  return CREDENTIAL_SOURCES[key] ?? humanizeToken(key).toLowerCase();
}
