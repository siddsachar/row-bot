/**
 * Where to get each provider's API key and what one looks like. The hint is
 * only a hint: formats change, so a key that doesn't match still goes to the
 * provider for the real check.
 */
export type ProviderKeyInfo = {
  /** The provider's own page for creating a key. */
  url: string;
  /** The usual beginning of a key, when it has one. */
  prefix?: string;
};

export const PROVIDER_KEYS: Readonly<Record<string, ProviderKeyInfo>> = {
  openai: { url: 'https://platform.openai.com/api-keys', prefix: 'sk-' },
  anthropic: {
    url: 'https://console.anthropic.com/settings/keys',
    prefix: 'sk-ant-',
  },
  google: { url: 'https://aistudio.google.com/apikey', prefix: 'AIza' },
  openrouter: { url: 'https://openrouter.ai/settings/keys', prefix: 'sk-or-' },
  xai: { url: 'https://console.x.ai', prefix: 'xai-' },
  minimax: { url: 'https://platform.minimax.io' },
  requesty: { url: 'https://router.requesty.ai' },
  opencode_zen: { url: 'https://opencode.ai' },
  opencode_go: { url: 'https://opencode.ai' },
  atlascloud: { url: 'https://atlascloud.ai' },
  ollama_cloud: { url: 'https://ollama.com' },
};

/** Recommended first in Setup; the rest sit under "More providers". */
export const RECOMMENDED_KEY_PROVIDERS = [
  'openai',
  'anthropic',
  'google',
  'openrouter',
] as const;

export const MORE_KEY_PROVIDERS = [
  'xai',
  'minimax',
  'requesty',
  'opencode_zen',
  'opencode_go',
  'atlascloud',
  'ollama_cloud',
] as const;

export function keyFormatHint(providerId: string): string {
  const prefix = PROVIDER_KEYS[providerId]?.prefix;
  return prefix ? `Starts with ${prefix}` : 'Paste the whole key';
}

/** A gentle warning when a key does not look like this provider's. */
export function keyFormatWarning(providerId: string, value: string): string {
  const key = value.trim();
  if (!key) return '';
  if (/\s/.test(key))
    return 'Keys have no spaces. Check that you copied only the key.';
  const prefix = PROVIDER_KEYS[providerId]?.prefix;
  if (prefix && !key.startsWith(prefix))
    return `This provider's keys usually start with ${prefix}. Check that you copied the right key.`;
  if (key.length < 16)
    return 'This looks too short for a key. Check that you copied all of it.';
  return '';
}
