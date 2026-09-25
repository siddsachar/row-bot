/** Settings navigation categories; leaf routes retain their existing identities. */
export const settingsGroups = [
  {
    id: 'models',
    label: 'Models and input',
    leaves: ['Providers', 'Models', 'Voice'],
  },
  {
    id: 'knowledge',
    label: 'Knowledge and documents',
    leaves: ['Knowledge', 'Documents'],
  },
  {
    id: 'integrations',
    label: 'Tools and integrations',
    leaves: ['Tools', 'Skills', 'MCP', 'Plugins', 'Accounts', 'Channels'],
  },
  {
    id: 'personal',
    label: 'Personal workspace',
    leaves: ['Buddy', 'Goals', 'Tracker', 'Utilities', 'Preferences'],
  },
  { id: 'system', label: 'System and access', leaves: ['System'] },
] as const;
const settingsOrder = [
  'Providers',
  'Models',
  'Knowledge',
  'Buddy',
  'Goals',
  'Voice',
  'System',
  'Tracker',
  'Documents',
  'Tools',
  'Skills',
  'Accounts',
  'Channels',
  'Utilities',
  'MCP',
  'Plugins',
  'Preferences',
] as const;

export const settingsLeaves = settingsOrder.map((label) => {
  const group = settingsGroups.find((candidate) =>
    candidate.leaves.some((leaf) => leaf === label),
  );
  const id = label.toLowerCase();
  return {
    id,
    label,
    category: group?.label ?? 'Settings',
    href: `/settings/${id}`,
  };
});
const aliases: Record<string, string> = {
  wiki: 'knowledge',
  cloud: 'providers',
  google: 'accounts',
  gmail: 'accounts',
  calendar: 'accounts',
  migration: 'preferences',
  search: 'tools',
  profiles: 'goals',
  'agent-profiles': 'goals',
};
export function resolveSetting(value: string) {
  const key = value.toLowerCase();
  return settingsLeaves.find((leaf) => leaf.id === (aliases[key] ?? key));
}
export function searchSettings(query: string) {
  const term = query.trim().toLowerCase();
  return settingsLeaves.filter((leaf) =>
    `${leaf.label} ${leaf.category} ${Object.entries(aliases)
      .filter(([, id]) => id === leaf.id)
      .map(([alias]) => alias)
      .join(' ')}`
      .toLowerCase()
      .includes(term),
  );
}
