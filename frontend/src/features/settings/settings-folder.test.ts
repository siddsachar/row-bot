import { expect, it, vi } from 'vitest';
import { createFakePlatform } from '../../platform/fake';
import { pickSettingsFolder } from './settings-folder';

function picked(
  result: Awaited<
    ReturnType<ReturnType<typeof createFakePlatform>['selectFolder']>
  >,
) {
  const platform = createFakePlatform();
  const selectFolder = vi.fn(async () => result);
  return { platform: { ...platform, selectFolder }, selectFolder };
}

it('picks a setting folder in the desktop window and claims it for this session', async () => {
  const { platform, selectFolder } = picked({
    status: 'ok',
    value: { kind: 'folder', reference: 'one-use-reference' },
  });
  const controller = {
    claimFolder: vi.fn(async () => ({
      status: 'selected' as const,
      grant_id: 'session-grant',
      name: 'Vault',
    })),
  };
  await expect(pickSettingsFolder(platform, controller)).resolves.toEqual({
    status: 'selected',
    grant_id: 'session-grant',
    name: 'Vault',
  });
  expect(selectFolder).toHaveBeenCalledWith(undefined, {
    intentId: expect.any(String),
    intent: 'settings_folder',
    conversationId: null,
    destination: 'settings',
  });
  expect(controller.claimFolder).toHaveBeenCalledWith(
    'one-use-reference',
    undefined,
  );
});

it('a cancelled pick claims nothing', async () => {
  const { platform } = picked({ status: 'cancelled' });
  const controller = { claimFolder: vi.fn() };
  await expect(pickSettingsFolder(platform, controller)).resolves.toEqual({
    status: 'cancelled',
  });
  expect(controller.claimFolder).not.toHaveBeenCalled();
});

it('a browser says to use the desktop app; a reconnecting window says so', async () => {
  const controller = { claimFolder: vi.fn() };
  await expect(
    pickSettingsFolder(
      picked({ status: 'unavailable', reason: 'desktop_required' }).platform,
      controller,
    ),
  ).rejects.toMatchObject({
    message: 'Choose the folder in the Row-Bot desktop app.',
  });
  await expect(
    pickSettingsFolder(
      picked({ status: 'unavailable', reason: 'native_reconnecting' }).platform,
      controller,
    ),
  ).rejects.toMatchObject({
    message: 'Desktop features are reconnecting. Try again in a moment.',
  });
  // A browser's own folder input gives files, never a reference to claim.
  await expect(
    pickSettingsFolder(
      picked({ status: 'ok', value: { kind: 'folder', files: [] } }).platform,
      controller,
    ),
  ).rejects.toMatchObject({
    message: 'Choose the folder in the Row-Bot desktop app.',
  });
  expect(controller.claimFolder).not.toHaveBeenCalled();
});
