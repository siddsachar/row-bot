import { useEffect, useState } from 'react';
import type { IntegrationItem, IntegrationSourceView } from '../../api/types';
import { clientError } from '../../api/errors';
import { useRuntime } from '../../runtime';
export type Kind = 'skill' | 'mcp' | 'plugin';
export const categories = [
  {
    kind: 'mcp',
    label: 'Apps & tools',
    purpose: 'Connect services and give Row-Bot tools.',
    note: 'MCP',
  },
  {
    kind: 'skill',
    label: 'Skills',
    purpose: 'Teach Row-Bot a workflow or specialist method.',
    note: 'Instructions and resources',
  },
  {
    kind: 'plugin',
    label: 'Plugins',
    purpose:
      'Add a package containing tools, skills or other supported capabilities.',
    note: 'Packages',
  },
] as const;
export type Source = string;
// Ids, labels and eligibility come from the server; nothing here decides access.
let known: IntegrationSourceView[] = [];
export const sourceName = (source: string) =>
  known.find((item) => item.id === source)?.label ?? source;
export const sourcesFor = (
  kind: Kind | 'all',
  list: IntegrationSourceView[] = known,
) =>
  list
    .filter((item) => kind === 'all' || item.kinds.includes(kind))
    .map((item) => item.id);
export function useSources(): {
  sources: IntegrationSourceView[];
  error: string;
} {
  const { controller } = useRuntime();
  const [sources, setSources] = useState(known);
  const [error, setError] = useState('');
  useEffect(() => {
    if (known.length) return;
    const abort = new AbortController();
    void controller
      .integrationSources(abort.signal)
      .then((list) => {
        known = list?.items ?? [];
        if (!abort.signal.aborted) setSources(known);
      })
      .catch((e) => {
        if (!abort.signal.aborted) setError(clientError(e).message);
      });
    return () => abort.abort();
  }, [controller]);
  return { sources, error };
}
const key = 'row-bot.integrations.preferences.v1';
export type Preferences = { category: Kind | ''; disabled: Source[] };
export function readPreferences(): Preferences {
  try {
    const saved = JSON.parse(localStorage.getItem(key) ?? '{}');
    return {
      category: categories.some((c) => c.kind === saved.category)
        ? saved.category
        : '',
      disabled: Array.isArray(saved.disabled)
        ? saved.disabled.filter((s: unknown) => typeof s === 'string')
        : [],
    };
  } catch {
    return { category: '', disabled: [] };
  }
}
export function savePreferences(value: Preferences): boolean {
  try {
    localStorage.setItem(key, JSON.stringify(value));
    return true;
  } catch {
    return false;
  }
}
export function setupLabel(item: IntegrationItem): string {
  if (item.compatibility === 'unsupported') return 'Unsupported';
  if (item.kind === 'skill') return 'Review instructions and files';
  if (item.auth_requirement === 'required')
    return 'Sign-in or credentials required';
  if (item.auth_requirement === 'none')
    return 'No account required; review setup';
  return 'Setup and account requirements not yet verified';
}
