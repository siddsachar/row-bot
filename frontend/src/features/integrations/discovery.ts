import type {
  IntegrationSearchRequest,
  IntegrationItem,
} from '../../api/types';
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
export type Source = NonNullable<IntegrationSearchRequest['sources']>[number];
// Identifiers only. Eligibility and access policy always come from the server.
export const catalogs: Record<Kind, Source[]> = {
  mcp: [
    'recommended',
    'official',
    'hermes_mcp',
    'glama',
    'pulsemcp',
    'smithery',
  ],
  skill: ['clawhub', 'github', 'skills_sh', 'browse_sh', 'lobehub'],
  plugin: ['hermes', 'native', 'clawhub_plugins'],
};
export const sourceNames: Partial<Record<Source, string>> = {
  recommended: 'Vendor recommendations',
  official: 'Official MCP Registry',
  hermes_mcp: 'Hermes MCP recipes',
  hermes: 'Hermes',
  native: 'Row-Bot marketplace',
  clawhub: 'ClawHub',
  github: 'GitHub',
  skills_sh: 'skills.sh',
  browse_sh: 'browse.sh',
  lobehub: 'LobeHub',
  glama: 'Glama',
  pulsemcp: 'PulseMCP',
  smithery: 'Smithery',
  clawhub_plugins: 'ClawHub plugins',
};
export const sourceName = (source: string) =>
  sourceNames[source as Source] ?? source;
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
        ? saved.disabled.filter((s: Source) =>
            Object.values(catalogs).flat().includes(s),
          )
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
