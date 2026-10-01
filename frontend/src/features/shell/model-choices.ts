import type { ModelChoice } from '../../api/types';
import { humanizeToken } from '../../ui/format';

/** Providers whose models run on this machine. */
const LOCAL_PROVIDERS = new Set(['ollama', 'lmstudio', 'lm_studio', 'local']);

export function isLocalProvider(providerId: string) {
  return LOCAL_PROVIDERS.has(providerId.toLowerCase());
}

/** "Claude Opus 4.8 - Claude Subscription" → name and provider label. */
export function splitModelLabel(label: string): {
  name: string;
  provider: string;
} {
  const at = label.lastIndexOf(' - ');
  return at > 0
    ? { name: label.slice(0, at).trim(), provider: label.slice(at + 3).trim() }
    : { name: label.trim(), provider: '' };
}

/** A short display name for a model reference outside the catalog. */
export function modelRefName(ref: string | undefined | null): string {
  if (!ref) return '';
  return ref.startsWith('model:')
    ? ref.split(':').slice(2).join(':') || ref
    : ref;
}

export type ModelGroup = {
  id: string;
  label: string;
  connected: boolean;
  local: boolean;
  models: ModelChoice[];
};

/** Provider groups in catalog order; connected providers first. */
export function groupModels(models: readonly ModelChoice[]): ModelGroup[] {
  const groups = new Map<string, ModelGroup>();
  for (const model of models) {
    let group = groups.get(model.provider_id);
    if (!group) {
      group = {
        id: model.provider_id,
        label:
          splitModelLabel(model.label).provider ||
          humanizeToken(model.provider_id),
        connected: false,
        local: isLocalProvider(model.provider_id),
        models: [],
      };
      groups.set(model.provider_id, group);
    }
    group.models.push(model);
    if (model.available) group.connected = true;
  }
  const ordered = [...groups.values()];
  return [
    ...ordered.filter((group) => group.connected),
    ...ordered.filter((group) => !group.connected),
  ];
}

export function matchesModel(model: ModelChoice, query: string) {
  const terms = query.toLocaleLowerCase().split(/\s+/).filter(Boolean);
  if (!terms.length) return true;
  const haystack =
    `${model.label} ${model.model_ref} ${model.provider_id}`.toLocaleLowerCase();
  return terms.every((term) => haystack.includes(term));
}

const RECENT_KEY = 'row-bot.recent-models.v1';

/** Recently chosen model references on this device (a per-viewer convenience). */
export function readRecentModels(): string[] {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(RECENT_KEY) ?? '[]');
    return Array.isArray(value)
      ? value
          .filter((item): item is string => typeof item === 'string')
          .slice(0, 5)
      : [];
  } catch {
    return [];
  }
}

export function rememberRecentModel(ref: string) {
  try {
    const next = [ref, ...readRecentModels().filter((item) => item !== ref)];
    localStorage.setItem(RECENT_KEY, JSON.stringify(next.slice(0, 5)));
  } catch {
    /* The list is a convenience; private browsing simply has none. */
  }
}
