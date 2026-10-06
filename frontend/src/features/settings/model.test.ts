import { describe, expect, it } from 'vitest';
import {
  AGENT_PROFILE_SETTINGS,
  legacyIntegrationHref,
  resolveSetting,
  searchSettings,
  searchSettingsRows,
  settingsGroups,
  settingsHref,
  settingsLeaves,
  settingsRedirects,
  settingsRowHref,
  settingsRows,
  THREAD_SETTINGS,
} from './model';

describe('settings navigation metadata', () => {
  it('groups every leaf once, in visit order, with stable deep links', () => {
    expect(settingsGroups.map((group) => group.label)).toEqual([
      'General',
      'Models',
      'Knowledge',
      'Capabilities',
      'Connections',
      'System',
    ]);
    expect(new Set(settingsLeaves.map((leaf) => leaf.id)).size).toBe(
      settingsLeaves.length,
    );
    for (const leaf of settingsLeaves)
      expect(resolveSetting(leaf.id)?.href).toBe(leaf.href);
    expect(settingsLeaves.map((leaf) => leaf.label)).toEqual([
      'Preferences',
      'Appearance',
      'Buddy',
      'Providers',
      'Models',
      'Voice',
      'Memory',
      'Documents',
      'Tracker',
      'Tools',
      'Skills',
      'Apps',
      'System',
      'Devices & remote access',
      'Updates',
      'Data',
    ]);
    // Every leaf id that existed before the regroup still resolves, except
    // goals, which belong to one conversation and open its Context instead.
    expect(resolveSetting('goals')).toBeUndefined();
    expect(THREAD_SETTINGS.has('goals')).toBe(true);
    // Agent profiles are the sidebar's Agents dialog now; their old links
    // open it (B260).
    for (const id of ['profiles', 'agent-profiles']) {
      expect(resolveSetting(id)).toBeUndefined();
      expect(AGENT_PROFILE_SETTINGS.has(id)).toBe(true);
    }
    for (const id of [
      'providers',
      'models',
      'knowledge',
      'buddy',
      'voice',
      'system',
      'tracker',
      'documents',
      'tools',
      'skills',
      'utilities',
      'mcp',
      'plugins',
      'preferences',
    ])
      expect(resolveSetting(id)).toBeDefined();
  });

  it('redirects legacy ids and moved pages to their new home and row', () => {
    for (const [alias, target] of [
      ['Cloud', 'providers'],
      ['Wiki', 'knowledge'],
      ['Migration', 'data'],
      ['Search', 'tools'],
      ['utilities', 'tools'],
    ])
      expect(resolveSetting(alias)?.id).toBe(target);
    expect(settingsHref('utilities')).toBe('/settings/tools#built-in-tools');
    expect(settingsHref('migration')).toBe('/settings/data#migration');
    expect(settingsHref('wiki')).toBe('/settings/knowledge#wiki-vault');
    expect(settingsHref('skills', 'public-skills')).toBe(
      '/settings/skills#public-skills',
    );
    expect(settingsHref('plugins')).toBe('/settings/apps');
    expect(settingsHref('mcp', 'mcp-runtimes')).toBe(
      '/settings/apps?view=advanced',
    );
    expect(
      legacyIntegrationHref(
        new URLSearchParams('tab=my&type=skill&selected=skill:pdf'),
      ),
    ).toBe('/settings/skills/pdf');
    expect(
      legacyIntegrationHref(new URLSearchParams('type=mcp&selected=mcp:abc')),
    ).toBe('/settings/apps/item?id=mcp%3Aabc');
    expect(legacyIntegrationHref(new URLSearchParams('type=skill'))).toBe(
      '/settings/skills',
    );
    expect(
      legacyIntegrationHref(new URLSearchParams('type=mcp&view=catalogs')),
    ).toBe('/settings/apps?view=advanced');
    expect(settingsHref('providers')).toBe('/settings/providers');
    // Accounts and channels are apps: their old pages and rows open the app.
    expect(settingsHref('accounts', 'google')).toBe('/settings/apps/google');
    expect(settingsHref('gmail')).toBe('/settings/apps/google');
    expect(settingsHref('channels', '#telegram')).toBe(
      '/settings/apps/telegram',
    );
    expect(settingsHref('accounts')).toBe('/settings/apps');
    expect(settingsHref('channels')).toBe(
      '/settings/apps?category=communication',
    );
    expect(resolveSetting('unknown')).toBeUndefined();
    for (const redirect of Object.values(settingsRedirects))
      expect(resolveSetting(redirect.leaf)?.id).toBe(redirect.leaf);
  });

  it('searches pages by name, group, keyword and alias', () => {
    expect(searchSettings('gmail').map((leaf) => leaf.id)).toEqual(['apps']);
    expect(searchSettings('theme').map((leaf) => leaf.id)).toEqual([
      'appearance',
    ]);
    expect(searchSettings('system access').map((leaf) => leaf.id)).toEqual([
      'access',
    ]);
    expect(searchSettings('built-in').map((leaf) => leaf.id)).toContain(
      'tools',
    );
  });

  it('searches rows that link to an anchor on their page', () => {
    const rows = searchSettingsRows('dark');
    expect(rows.map((row) => settingsRowHref(row))).toEqual([
      '/settings/appearance#theme',
    ]);
    expect(
      searchSettingsRows('default model').map((row) => row.anchor),
    ).toEqual(['default-model']);
    expect(searchSettingsRows('')).toEqual([]);
    // A page name narrows a row search but never lists a whole page.
    expect(searchSettingsRows('runtimes').map(settingsRowHref)).toEqual([
      '/settings/apps?view=advanced#chats',
    ]);
    expect(searchSettingsRows('find skills').map(settingsRowHref)).toEqual([
      '/settings/skills#public-skills',
    ]);
    expect(searchSettingsRows('logging').map(settingsRowHref)).toEqual([
      '/settings/system#logging.level',
    ]);
    for (const row of settingsRows)
      expect(resolveSetting(row.leaf)?.id).toBe(row.leaf);
  });
});
