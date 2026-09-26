import { describe, expect, it } from 'vitest';
import {
  resolveSetting,
  searchSettings,
  searchSettingsRows,
  settingsGroups,
  settingsHref,
  settingsLeaves,
  settingsRedirects,
  settingsRowHref,
  settingsRows,
} from './model';

describe('settings navigation metadata', () => {
  it('groups every leaf once, in visit order, with stable deep links', () => {
    expect(settingsGroups.map((group) => group.label)).toEqual([
      'General',
      'Models',
      'Knowledge',
      'Capabilities',
      'Connections',
      'Agents',
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
      'Plugins',
      'MCP',
      'Accounts',
      'Channels',
      'Agent profiles',
      'Goals',
      'System',
      'Access',
      'Updates',
      'Data',
    ]);
    // Every leaf id that existed before the regroup still resolves.
    for (const id of [
      'providers',
      'models',
      'knowledge',
      'buddy',
      'goals',
      'voice',
      'system',
      'tracker',
      'documents',
      'tools',
      'skills',
      'accounts',
      'channels',
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
      ['Google', 'accounts'],
      ['Gmail', 'accounts'],
      ['Calendar', 'accounts'],
      ['Wiki', 'knowledge'],
      ['Migration', 'data'],
      ['Search', 'tools'],
      ['utilities', 'tools'],
      ['agent-profiles', 'profiles'],
      ['profiles', 'profiles'],
    ])
      expect(resolveSetting(alias)?.id).toBe(target);
    expect(settingsHref('utilities')).toBe('/settings/tools#built-in-tools');
    expect(settingsHref('migration')).toBe('/settings/data#migration');
    expect(settingsHref('wiki')).toBe('/settings/knowledge#wiki-vault');
    expect(settingsHref('providers')).toBe('/settings/providers');
    expect(resolveSetting('unknown')).toBeUndefined();
    for (const redirect of Object.values(settingsRedirects))
      expect(resolveSetting(redirect.leaf)?.id).toBe(redirect.leaf);
  });

  it('searches pages by name, group, keyword and alias', () => {
    expect(searchSettings('gmail').map((leaf) => leaf.id)).toEqual([
      'accounts',
    ]);
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
    for (const row of settingsRows)
      expect(resolveSetting(row.leaf)?.id).toBe(row.leaf);
  });
});
