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

/** A provider's sheet in Settings › Models (its connection and models), or the page. */
export function providerSheetPath(providerId?: string | null): string {
  return providerId
    ? `/settings/models?provider=${encodeURIComponent(providerId)}`
    : '/settings/models';
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

/**
 * Your starred models first, in their order, then the rest as listed: for
 * lists that can't group, such as a workflow's model.
 */
export function favouritesFirst<T extends Pick<ModelChoice, 'favourite_rank'>>(
  models: readonly T[],
): T[] {
  const favourites = models
    .filter((model) => model.favourite_rank != null)
    .sort((a, b) => (a.favourite_rank ?? 0) - (b.favourite_rank ?? 0));
  return [
    ...favourites,
    ...models.filter((model) => model.favourite_rank == null),
  ];
}

/** One model reached through several providers: the route shown, then the rest. */
export type ModelRoutes<T extends ModelChoice = ModelChoice> = {
  first: T;
  others: T[];
};

/** "anthropic/claude-opus-4.7" → maker "anthropic", name "claude-opus-4-7". */
function routeName(ref: string): { maker: string; name: string } | null {
  if (!ref.startsWith('model:')) return null;
  const id = ref.split(':').slice(2).join(':').toLowerCase();
  const slash = id.lastIndexOf('/');
  const name = id.slice(slash + 1).replace(/[._]/g, '-');
  if (!name) return null;
  return {
    maker: slash > 0 ? id.slice(0, slash).replace(/[^a-z0-9]/g, '') : '',
    name,
  };
}

/** Your plan, this computer, the maker's own API, then a router; unavailable last. */
function routeOrder(model: ModelChoice, maker: string) {
  if (!model.available) return 4;
  if (model.billing === 'subscription') return 0;
  if (model.billing === 'local') return 1;
  return maker ? 3 : 2;
}

/**
 * Folds the same model offered by several providers into one entry (§4.4):
 * "claude-opus-4-7" and "anthropic/claude-opus-4.7" are one model, but two
 * makers' models of one name stay apart. The route shown is the current
 * one, then a starred one, then by `routeOrder`. Display only: each route
 * keeps its own reference, and nothing switches routes by itself.
 */
export function foldRoutes<T extends ModelChoice>(
  models: readonly T[],
  current?: string,
): ModelRoutes<T>[] {
  const named = new Map<string, { model: T; maker: string }[]>();
  const alone: T[] = [];
  for (const model of models) {
    const parsed = routeName(model.model_ref);
    if (!parsed) alone.push(model);
    else
      named
        .set(parsed.name, named.get(parsed.name) ?? [])
        .get(parsed.name)!
        .push({ model, maker: parsed.maker });
  }
  const sets: { model: T; maker: string }[][] = [];
  for (const routes of named.values()) {
    const makers = new Set(routes.map((route) => route.maker).filter(Boolean));
    if (makers.size <= 1) sets.push(routes);
    else
      for (const maker of makers)
        sets.push(routes.filter((route) => route.maker === maker));
    // With two makers, a route that names none could be either: it stays alone.
    if (makers.size > 1)
      alone.push(...routes.filter((route) => !route.maker).map((r) => r.model));
  }
  const rank = (route: { model: T; maker: string }) => [
    route.model.model_ref === current ? 0 : 1,
    route.model.favourite_rank != null && route.model.available
      ? route.model.favourite_rank
      : Infinity,
    routeOrder(route.model, route.maker),
  ];
  const byRank = (
    a: { model: T; maker: string },
    b: { model: T; maker: string },
  ) => {
    const [x, y] = [rank(a), rank(b)];
    return x[0] - y[0] || x[1] - y[1] || x[2] - y[2];
  };
  const folded = new Map<T, ModelRoutes<T>>();
  for (const routes of sets) {
    const [first, ...others] = [...routes].sort(byRank).map((r) => r.model);
    folded.set(routes[0].model, { first, others });
  }
  for (const model of alone) folded.set(model, { first: model, others: [] });
  // In the order the models came, by each one's first appearance.
  return models.flatMap((model) => folded.get(model) ?? []);
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
