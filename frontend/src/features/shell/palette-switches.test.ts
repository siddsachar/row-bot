import { expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import type { SettingsSnapshot } from '../../api/types';
import { saveSwitch, settingsSwitches } from './palette-switches';

const snapshot = {
  revision: 'a'.repeat(64),
  utilities: {
    availability: 'available',
    items: [
      {
        utility_id: 'developer',
        label: 'Developer',
        description: '',
        available: true,
        enabled: false,
      },
    ],
  },
  system: {
    availability: 'available',
    shell: { available: true, enabled: true },
    browser: { available: true, enabled: null },
  },
  preferences: { availability: 'unavailable' },
  tracker: { availability: 'available', tool_available: false, enabled: true },
} as unknown as SettingsSnapshot;

function controllerWith(status: 'completed' | 'partial' | 'rejected') {
  const controller = {
    settingsSnapshot: vi.fn(async () => snapshot),
    reviewSettingsMutation: vi.fn(
      async (request: { page: string; field: string }) => ({
        schema_version: 1,
        operation: 'settings.update',
        settings_revision: 'a'.repeat(64),
        page: request.page,
        field: request.field,
        value_summary: 'On',
        secret: false,
        action_digest: 'd'.repeat(64),
        review_id: 'r'.repeat(32),
      }),
    ),
    executeSettingsMutation: vi.fn(async () => ({
      command_id: 'command',
      status,
      code: status === 'rejected' ? 'settings_changed' : null,
    })),
  };
  return { controller, client: controller as unknown as ClientController };
}

it('reads only the switches the snapshot can show, with their state', () => {
  const set = vi.fn();
  const switches = settingsSwitches(snapshot, set);
  // No state, no tool or no readable page: not offered.
  expect(switches.map((item) => [item.id, item.on])).toEqual([
    ['developer', false],
    ['shell', true],
  ]);
  switches[0].set(true);
  expect(set).toHaveBeenCalledWith(
    expect.objectContaining({ id: 'developer', on: false }),
    true,
  );
});

it('saves a switch with the reviewed change its Settings page sends', async () => {
  const { controller, client } = controllerWith('completed');
  const [developer] = settingsSwitches(snapshot, vi.fn());
  await expect(saveSwitch(client, developer, true)).resolves.toBe('completed');
  const request = {
    settings_revision: 'a'.repeat(64),
    page: 'utilities',
    field: 'developer.enabled',
    value: true,
  };
  expect(controller.reviewSettingsMutation).toHaveBeenCalledWith(request);
  expect(controller.executeSettingsMutation).toHaveBeenCalledWith(
    request,
    expect.objectContaining({ review_id: 'r'.repeat(32) }),
    expect.any(String),
  );
});

it('refuses a review for another field and reports a refusal or doubt', async () => {
  const [developer] = settingsSwitches(snapshot, vi.fn());
  const mismatch = controllerWith('completed');
  mismatch.controller.reviewSettingsMutation.mockResolvedValueOnce({
    ...(await mismatch.controller.reviewSettingsMutation(developer)),
    field: 'shell.enabled',
  });
  await expect(saveSwitch(mismatch.client, developer, true)).rejects.toEqual({
    code: 'revision_conflict',
  });
  expect(mismatch.controller.executeSettingsMutation).not.toHaveBeenCalled();

  const refused = controllerWith('rejected');
  await expect(saveSwitch(refused.client, developer, true)).rejects.toEqual({
    code: 'settings_changed',
  });
  const unsure = controllerWith('partial');
  await expect(saveSwitch(unsure.client, developer, true)).resolves.toBe(
    'partial',
  );
});
