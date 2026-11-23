import { expect, test } from 'vitest';
import {
  companyDocument,
  companyValues,
  describe,
  grantText,
  overrideDocument,
  overrideSettings,
} from './policy-model';

const COMPANY = {
  version: 4,
  models: { personal_api_keys: false },
  tools: { disabled_groups: ['shell'] },
  apps: { plugins: 'allow-list', allowed_plugins: ['acme.notes'] },
};

test('the form shows every setting: what the policy sets, else the contract default', () => {
  const values = companyValues(COMPANY);
  expect(values['models.personal_api_keys']).toBe(false);
  expect(values['tools.disabled_groups']).toEqual(['shell']);
  expect(values['models.allowed']).toEqual(['*']);
  expect(values['approvals.max_level']).toBe('auto');
  expect(values['apps.channels']).toBe('allow-list');
  expect(values['offline.max_days']).toBe(7);
});

test('an unchanged form writes back the same document, with the version it replaces', () => {
  expect(companyDocument(companyValues(COMPANY), COMPANY)).toEqual(COMPANY);
});

test('a changed form keeps what the policy set, adds what changed and leaves defaults unset', () => {
  const values = {
    ...companyValues(COMPANY),
    'models.personal_api_keys': true,
    'offline.max_days': 30,
    'tools.disabled_groups': ['shell', ' browser', 'shell'],
    'models.allowed': ['company-chat', '*'],
  };
  expect(companyDocument(values, COMPANY)).toEqual({
    version: 4,
    models: { allowed: ['*'], personal_api_keys: true },
    tools: { disabled_groups: ['browser', 'shell'] },
    apps: { plugins: 'allow-list', allowed_plugins: ['acme.notes'] },
    offline: { max_days: 30 },
  });
  // A first policy replaces version 0 and sets only what differs from the defaults.
  expect(
    companyDocument(
      { ...companyValues({ version: 0 }), 'data.chat_export': false },
      { version: 0 },
    ),
  ).toEqual({ version: 0, data: { chat_export: false } });
});

test('a group override reads and writes the tools it switches back on under its own names', () => {
  const override = {
    version: 2,
    tools: { enabled_groups: ['shell'] },
    approvals: { max_level: 'full' },
  };
  const values = overrideSettings(override);
  expect(values).toEqual({
    'tools.disabled_groups': ['shell'],
    'approvals.max_level': 'full',
  });
  expect(overrideDocument(values, 2)).toEqual(override);
  expect(describe('tools.disabled_groups', ['shell'], true)).toBe(
    'Tool groups switched back on: shell',
  );
  expect(overrideDocument({}, 3)).toEqual({ version: 3 });
});

test('settings read in plain words, also as the server reports what groups still get', () => {
  expect(describe('approvals.max_level', 'full')).toBe(
    'Highest approval level: Hands-off',
  );
  expect(describe('models.allowed', ['*'])).toBe(
    'Models people may use: Every catalog model',
  );
  expect(describe('offline.max_days', 1)).toBe('Offline days: 1 day');
  expect(grantText('models.local_models')).toBe('Local models');
  expect(grantText('tools.enabled_groups: shell')).toBe(
    'Tool groups switched back on: shell',
  );
  expect(grantText('apps.plugins: any')).toBe('Plugins: Any');
});
