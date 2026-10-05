/**
 * Settings navigation: six groups ordered by how often people visit them.
 * Leaf ids are stable deep links; legacy ids and pages that moved redirect to
 * their new home, optionally to one row on that page (`#anchor`). Goals
 * belong to one conversation, so `goals` leaves Settings for the thread's
 * Context (see THREAD_SETTINGS).
 */
export const settingsGroups = [
  {
    id: 'general',
    label: 'General',
    leaves: ['preferences', 'appearance', 'buddy'],
  },
  { id: 'models', label: 'Models', leaves: ['providers', 'models', 'voice'] },
  {
    id: 'knowledge',
    label: 'Knowledge',
    leaves: ['knowledge', 'documents', 'tracker'],
  },
  {
    id: 'capabilities',
    label: 'Capabilities',
    leaves: ['tools', 'skills'],
  },
  {
    id: 'connections',
    label: 'Connections',
    // Accounts and channels are apps too (Apps › Google, Apps › Telegram).
    leaves: ['apps'],
  },
  {
    id: 'system',
    label: 'System',
    leaves: ['system', 'access', 'updates', 'data'],
  },
] as const;

export type SettingsGroupId = (typeof settingsGroups)[number]['id'];
export type SettingsLeafId = (typeof settingsGroups)[number]['leaves'][number];

const leafLabels: Record<SettingsLeafId, string> = {
  preferences: 'Preferences',
  appearance: 'Appearance',
  buddy: 'Buddy',
  providers: 'Providers',
  models: 'Models',
  voice: 'Voice',
  knowledge: 'Memory',
  documents: 'Documents',
  tracker: 'Tracker',
  tools: 'Tools',
  skills: 'Skills',
  apps: 'Apps',
  system: 'System',
  access: 'Devices & remote access',
  updates: 'Updates',
  data: 'Data',
};

/** Words people use for a page that are not in its name. */
export const settingsKeywords: Record<SettingsLeafId, string> = {
  preferences: 'identity name personality launch window dream cycle',
  appearance: 'theme dark light accent colour color density transparency',
  buddy: 'companion avatar pack motion desktop',
  providers: 'api key credentials ollama openai anthropic cloud connect',
  models: 'default model thinking reasoning catalog pin vision image',
  voice: 'dictation talk speech microphone tts read aloud',
  knowledge: 'knowledge memories wiki graph vault',
  documents: 'files upload pdf library embedding index',
  tracker: 'habits tracking health',
  tools: 'utilities built-in search web research compression custom tools',
  skills: 'skill library slash commands instructions clawhub create import',
  apps: 'integrations mcp plugins packages connectors servers marketplace discover connect catalogs runtimes accounts github google gmail calendar x twitter oauth channels telegram discord slack sms whatsapp messaging',
  system: 'shell browser computer use workspace folder logging files',
  access:
    'access remote tunnel invitations sessions tailscale mobile phone qr pair wifi public',
  updates: 'version upgrade release channel beta',
  data: 'migration import hermes openclaw danger delete',
};

export type SettingsLeaf = {
  id: SettingsLeafId;
  label: string;
  group: SettingsGroupId;
  category: string;
  href: string;
};

export const settingsLeaves: SettingsLeaf[] = settingsGroups.flatMap((group) =>
  group.leaves.map((id) => ({
    id,
    label: leafLabels[id],
    group: group.id,
    category: group.label,
    href: `/settings/${id}`,
  })),
);

/** Legacy ids and moved pages: where they live now. */
export const settingsRedirects: Record<
  string,
  { leaf: SettingsLeafId; anchor?: string }
> = {
  integrations: { leaf: 'apps' },
  plugins: { leaf: 'apps' },
  mcp: { leaf: 'apps' },
  wiki: { leaf: 'knowledge', anchor: 'wiki-vault' },
  memory: { leaf: 'knowledge' },
  cloud: { leaf: 'providers' },
  migration: { leaf: 'data', anchor: 'migration' },
  backup: { leaf: 'data', anchor: 'backup' },
  search: { leaf: 'tools', anchor: 'search-tools' },
  utilities: { leaf: 'tools', anchor: 'built-in-tools' },
  theme: { leaf: 'appearance' },
  update: { leaf: 'updates' },
  'remote-access': { leaf: 'access' },
  sessions: { leaf: 'access', anchor: 'devices' },
  devices: { leaf: 'access', anchor: 'devices' },
};

/** Former Settings pages that now live in a conversation's Context card. */
export const THREAD_SETTINGS = new Set(['goals']);

/**
 * Settings › Agent profiles duplicated the sidebar's Agents dialog (B260):
 * its old links, and search for it, open that dialog instead.
 */
export const AGENT_PROFILE_SETTINGS = new Set(['profiles', 'agent-profiles']);
export const agentProfileLibrary = {
  label: 'Agent profile library',
  keywords: 'agents profiles personas delegation',
  href: '/settings/profiles',
};

export function resolveSetting(value: string) {
  const key = value.toLowerCase();
  const target = settingsRedirects[key]?.leaf ?? key;
  return settingsLeaves.find((leaf) => leaf.id === target);
}

/**
 * Where an old Integrations, MCP, Plugins or Skills link lands now: Apps,
 * Skills, one item, or Apps › Advanced (catalogs and runtimes).
 */
export function legacyIntegrationHref(
  key: string,
  search: URLSearchParams,
  anchor = '',
) {
  const hash = anchor.replace(/^#/, '');
  const selected = search.get('selected') ?? '';
  if (selected.startsWith('skill:'))
    return `/settings/skills/${encodeURIComponent(selected.slice(6))}`;
  if (selected)
    return `/settings/apps/item?${new URLSearchParams({ id: selected })}`;
  if (search.get('view') === 'catalogs' || hash === 'mcp-runtimes')
    return '/settings/apps?view=advanced';
  if (search.get('type') === 'skill' || (key === 'skills' && !search.size))
    return '/settings/skills';
  return '/settings/apps';
}

/**
 * Settings › Accounts and › Channels joined Apps: an account or channel is an
 * app's built-in way to connect, so its old link opens that app's page.
 */
const CONNECTION_APPS: Record<string, string> = {
  google: 'google',
  gmail: 'google',
  calendar: 'google',
  github: 'github',
  x: 'x',
  telegram: 'telegram',
  whatsapp: 'whatsapp',
  discord: 'discord',
  slack: 'slack',
  sms: 'sms',
};
export const CONNECTION_PAGES = new Set([
  'accounts',
  'channels',
  'google',
  'gmail',
  'calendar',
]);

export function connectionHref(key: string, anchor = '') {
  const page = key.toLowerCase();
  const app =
    CONNECTION_APPS[anchor.replace(/^#/, '').split('.')[0]] ??
    CONNECTION_APPS[page];
  if (app) return `/settings/apps/${app}`;
  return page === 'channels'
    ? '/settings/apps?category=communication'
    : '/settings/apps';
}

/** The canonical href for a leaf id, legacy id or moved page. */
export function settingsHref(value: string, anchor = '') {
  const key = value.toLowerCase();
  if (['integrations', 'plugins', 'mcp'].includes(key))
    return legacyIntegrationHref(key, new URLSearchParams(), anchor);
  if (CONNECTION_PAGES.has(key)) return connectionHref(key, anchor);
  const redirect = settingsRedirects[key];
  const leaf = resolveSetting(key);
  if (!leaf) return undefined;
  const section = anchor || redirect?.anchor;
  return section ? `${leaf.href}#${section.replace(/^#/, '')}` : leaf.href;
}

function leafText(leaf: SettingsLeaf) {
  return `${leaf.label} ${leaf.category} ${settingsKeywords[leaf.id]} ${Object.entries(
    settingsRedirects,
  )
    .filter(([, target]) => target.leaf === leaf.id)
    .map(([alias]) => alias)
    .join(' ')}`.toLowerCase();
}

export function searchSettings(query: string) {
  const term = query.trim().toLowerCase();
  return settingsLeaves.filter((leaf) =>
    term
      .split(/\s+/)
      .filter(Boolean)
      .every((word) => leafText(leaf).includes(word)),
  );
}

/**
 * Rows that search can jump to. `anchor` matches a `data-setting-anchor` on
 * the page; the shell opens any collapsed section around it, scrolls to it and
 * highlights it.
 */
export type SettingsRow = {
  leaf: SettingsLeafId;
  anchor: string;
  label: string;
  keywords?: string;
  /** A row that is its own page (an app). */
  href?: string;
};

export const settingsRows: SettingsRow[] = [
  { leaf: 'preferences', anchor: 'identity.name', label: 'Assistant name' },
  {
    leaf: 'preferences',
    anchor: 'identity.personality',
    label: 'Personality',
    keywords: 'behaviour tone',
  },
  {
    leaf: 'preferences',
    anchor: 'identity.self_improvement_enabled',
    label: 'Self-improvement',
    keywords: 'skills learn',
  },
  {
    leaf: 'preferences',
    anchor: 'window_mode',
    label: 'Window mode',
    keywords: 'native browser launch',
  },
  {
    leaf: 'preferences',
    anchor: 'dream-cycle',
    label: 'Dream Cycle',
    keywords: 'overnight background consolidation',
  },
  {
    leaf: 'appearance',
    anchor: 'theme',
    label: 'Theme',
    keywords: 'dark light system mode',
  },
  {
    leaf: 'appearance',
    anchor: 'accent',
    label: 'Accent colour',
    keywords: 'color blue teal violet amber',
  },
  {
    leaf: 'appearance',
    anchor: 'density',
    label: 'Density',
    keywords: 'compact comfortable',
  },
  {
    leaf: 'appearance',
    anchor: 'transparency',
    label: 'Reduce transparency',
    keywords: 'blur glass',
  },
  {
    leaf: 'appearance',
    anchor: 'layout',
    label: 'Reset layout',
    keywords: 'panel sizes',
  },
  { leaf: 'buddy', anchor: 'buddy-visibility', label: 'Show Buddy' },
  {
    leaf: 'buddy',
    anchor: 'buddy-look',
    label: 'Buddy look and motion',
    keywords: 'avatar pack',
  },
  {
    leaf: 'providers',
    anchor: 'custom-endpoints',
    label: 'Custom endpoints',
    keywords: 'base url openai compatible',
  },
  {
    leaf: 'models',
    anchor: 'default-model',
    label: 'Default model',
    keywords: 'brain chat',
  },
  { leaf: 'models', anchor: 'vision-model', label: 'Vision model' },
  {
    leaf: 'models',
    anchor: 'image-model',
    label: 'Image model',
    keywords: 'generation',
  },
  {
    leaf: 'models',
    anchor: 'model-catalog',
    label: 'Model catalog',
    keywords: 'pin models',
  },
  { leaf: 'voice', anchor: 'talk', label: 'Talk', keywords: 'realtime' },
  { leaf: 'voice', anchor: 'dictation', label: 'Dictation' },
  {
    leaf: 'voice',
    anchor: 'local.whisper_model',
    label: 'Whisper model size',
  },
  {
    leaf: 'voice',
    anchor: 'read-aloud',
    label: 'Read aloud',
    keywords: 'text to speech tts kokoro',
  },
  {
    leaf: 'knowledge',
    anchor: 'memory-graph',
    label: 'Memory graph',
    keywords: 'entities relations',
  },
  {
    leaf: 'knowledge',
    anchor: 'wiki-vault',
    label: 'Wiki vault',
    keywords: 'obsidian markdown',
  },
  {
    leaf: 'documents',
    anchor: 'embedding',
    label: 'Embedding engine',
    keywords: 'vectors model',
  },
  {
    leaf: 'documents',
    anchor: 'document-upload',
    label: 'Upload documents',
    keywords: 'add files drop',
  },
  {
    leaf: 'tracker',
    anchor: 'tracker.enabled',
    label: 'Habit tracker',
  },
  {
    leaf: 'tools',
    anchor: 'capability-loading',
    label: 'Capability loading',
    keywords: 'external tools',
  },
  {
    leaf: 'tools',
    anchor: 'retrieval-compression',
    label: 'Retrieval compression',
  },
  {
    leaf: 'tools',
    anchor: 'search-tools',
    label: 'Search and knowledge tools',
    keywords: 'web search tavily arxiv duckduckgo wolfram',
  },
  {
    leaf: 'tools',
    anchor: 'built-in-tools',
    label: 'Built-in tools',
    keywords: 'utilities calculator weather charts url reader',
  },
  {
    leaf: 'tools',
    anchor: 'custom-tools',
    label: 'Custom tools',
    keywords: 'own tools scripts repository folder commands builder',
  },
  { leaf: 'skills', anchor: 'skill-library', label: 'Your skills' },
  {
    leaf: 'skills',
    anchor: 'public-skills',
    label: 'Find skills',
    keywords: 'hub browse discover public',
  },
  {
    leaf: 'apps',
    anchor: 'catalogs',
    label: 'App catalogs',
    keywords: 'update registry hermes mcp',
  },
  {
    leaf: 'apps',
    anchor: 'chats',
    label: 'Runtimes (Node.js, uv)',
    keywords: 'node python uv mcp',
  },
  {
    leaf: 'apps',
    anchor: '',
    label: 'GitHub account',
    href: '/settings/apps/github',
  },
  {
    leaf: 'apps',
    anchor: '',
    label: 'Google account',
    keywords: 'gmail calendar drive',
    href: '/settings/apps/google',
  },
  {
    leaf: 'apps',
    anchor: '',
    label: 'X account',
    keywords: 'twitter',
    href: '/settings/apps/x',
  },
  ...(
    [
      ['telegram', 'Telegram'],
      ['whatsapp', 'WhatsApp'],
      ['discord', 'Discord'],
      ['slack', 'Slack'],
      ['sms', 'Text messages'],
    ] as const
  ).map(([id, label]) => ({
    leaf: 'apps' as const,
    anchor: '',
    label: `${label} channel`,
    keywords: 'messaging channel sms',
    href: `/settings/apps/${id}`,
  })),
  {
    leaf: 'system',
    anchor: 'workspace-folder',
    label: 'Workspace folder',
  },
  {
    leaf: 'system',
    anchor: 'shell.enabled',
    label: 'Shell access',
    keywords: 'terminal commands',
  },
  {
    leaf: 'system',
    anchor: 'browser-computer-use',
    label: 'Browser and Computer Use',
  },
  { leaf: 'system', anchor: 'file-operations', label: 'File operations' },
  {
    leaf: 'system',
    anchor: 'logging.level',
    label: 'Log level',
    keywords: 'logging logs diagnostics debug',
  },
  {
    leaf: 'access',
    anchor: 'connect',
    label: 'Connect a phone or computer',
    keywords: 'phone pair qr code invitation tablet laptop',
  },
  {
    leaf: 'access',
    anchor: 'devices',
    label: 'Your devices',
    keywords: 'sessions signed in sign out phones computers',
  },
  {
    leaf: 'access',
    anchor: 'remote-access',
    label: 'Network access',
    keywords: 'listen wifi lan allowed addresses origins',
  },
  {
    leaf: 'access',
    anchor: 'tunnel',
    label: 'Public link',
    keywords: 'ngrok tunnel internet',
  },
  {
    leaf: 'updates',
    anchor: 'updates.channel',
    label: 'Update channel',
    keywords: 'beta stable',
  },
  {
    leaf: 'data',
    anchor: 'backup',
    label: 'Back up and restore',
    keywords: 'backup restore copy archive move computer',
  },
  {
    leaf: 'data',
    anchor: 'migration',
    label: 'Import from another assistant',
    keywords: 'hermes openclaw migration',
  },
];

export function searchSettingsRows(query: string) {
  const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return [];
  return settingsRows.filter((row) => {
    const own = `${row.label} ${row.keywords ?? ''}`.toLowerCase();
    // The page name narrows ("mcp runtime" → MCP › Runtimes), but
    // one word must name the row itself, so "system" lists the page only.
    const text = `${own} ${leafLabels[row.leaf]}`.toLowerCase();
    return (
      words.every((word) => text.includes(word)) &&
      words.some((word) => own.includes(word))
    );
  });
}

export function settingsRowHref(row: SettingsRow) {
  if (row.href) return row.href;
  if (row.leaf === 'apps') return `/settings/apps?view=advanced#${row.anchor}`;
  return (
    settingsHref(row.leaf, row.anchor) ?? `/settings/${row.leaf}#${row.anchor}`
  );
}

/** Whether every word of a search names the Agent profile library. */
export function searchFindsAgentProfiles(query: string) {
  const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const text =
    `${agentProfileLibrary.label} ${agentProfileLibrary.keywords}`.toLowerCase();
  return words.length > 0 && words.every((word) => text.includes(word));
}
