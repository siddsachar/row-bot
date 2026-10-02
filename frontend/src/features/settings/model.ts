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
    leaves: ['tools', 'integrations'],
  },
  { id: 'connections', label: 'Connections', leaves: ['accounts', 'channels'] },
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
  integrations: 'Integrations',
  accounts: 'Accounts',
  channels: 'Channels',
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
  integrations:
    'skills plugins mcp hub extensions servers marketplace discover install connectors hermes clawhub runtimes',
  accounts: 'github google gmail calendar x twitter oauth',
  channels: 'telegram discord slack sms whatsapp messaging',
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
  skills: { leaf: 'integrations' },
  plugins: { leaf: 'integrations' },
  mcp: { leaf: 'integrations' },
  wiki: { leaf: 'knowledge', anchor: 'wiki-vault' },
  memory: { leaf: 'knowledge' },
  cloud: { leaf: 'providers' },
  google: { leaf: 'accounts', anchor: 'google' },
  gmail: { leaf: 'accounts', anchor: 'google' },
  calendar: { leaf: 'accounts', anchor: 'google' },
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

/** The canonical href for a leaf id, legacy id or moved page. */
export function settingsHref(value: string, anchor = '') {
  const key = value.toLowerCase();
  if (['skills', 'plugins', 'mcp'].includes(key)) {
    const type =
      key === 'skills' ? 'skill' : key === 'plugins' ? 'plugin' : 'mcp';
    const tab = [
      'public-skills',
      'plugin-marketplace',
      'mcp-marketplace',
    ].includes(anchor.replace(/^#/, ''))
      ? 'discover'
      : 'my';
    const source =
      tab === 'discover'
        ? key === 'skills'
          ? '&source=clawhub'
          : key === 'plugins'
            ? '&source=native'
            : '&source=official'
        : '';
    return `/settings/integrations?tab=${tab}&type=${type}${source}${anchor ? '#' + anchor.replace(/^#/, '') : ''}`;
  }
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
  context?: 'skills' | 'plugins' | 'mcp';
  anchor: string;
  label: string;
  keywords?: string;
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
  {
    leaf: 'integrations',
    context: 'skills',
    anchor: 'skill-library',
    label: 'Installed skills',
  },
  {
    leaf: 'integrations',
    context: 'skills',
    anchor: 'public-skills',
    label: 'Discover public skills',
    keywords: 'hub browse',
  },
  {
    leaf: 'integrations',
    context: 'plugins',
    anchor: 'installed-plugins',
    label: 'Installed plugins',
  },
  {
    leaf: 'integrations',
    context: 'plugins',
    anchor: 'plugin-marketplace',
    label: 'Plugin marketplace',
    keywords: 'discover browse',
  },
  {
    leaf: 'integrations',
    context: 'mcp',
    anchor: 'mcp-servers',
    label: 'MCP servers',
    keywords: 'add server import config',
  },
  {
    leaf: 'integrations',
    context: 'mcp',
    anchor: 'mcp-runtimes',
    label: 'Runtimes (Node.js, uv)',
    keywords: 'node python uv',
  },
  { leaf: 'accounts', anchor: 'github', label: 'GitHub account' },
  {
    leaf: 'accounts',
    anchor: 'google',
    label: 'Google account',
    keywords: 'gmail calendar',
  },
  { leaf: 'accounts', anchor: 'x', label: 'X account', keywords: 'twitter' },
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
    const text =
      `${own} ${row.context ?? ''} ${leafLabels[row.leaf]}`.toLowerCase();
    return (
      words.every((word) => text.includes(word)) &&
      words.some((word) => own.includes(word))
    );
  });
}

export function settingsRowHref(row: SettingsRow) {
  return (
    settingsHref(row.context ?? row.leaf, row.anchor) ??
    `/settings/${row.leaf}#${row.anchor}`
  );
}

/** Whether every word of a search names the Agent profile library. */
export function searchFindsAgentProfiles(query: string) {
  const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const text =
    `${agentProfileLibrary.label} ${agentProfileLibrary.keywords}`.toLowerCase();
  return words.length > 0 && words.every((word) => text.includes(word));
}
