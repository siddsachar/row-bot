import { isValidElement } from 'react';
import { expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import type { useOverlay } from '../../ui/overlays';
import GoalProfileSettings, {
  type GoalProfileSettingsSession,
} from '../settings/GoalProfileSettings';
import { openAgentProfiles } from './agent-profiles';

it('opens the reviewed profile library rather than a text summary (B10)', () => {
  const open = vi.fn<ReturnType<typeof useOverlay>['open']>();
  const overlay = {
    open,
    close: vi.fn(),
    dismiss: vi.fn(),
    notify: vi.fn(),
  };
  const session = {} as GoalProfileSettingsSession;
  const opener = document.createElement('textarea');
  const onStartProfileChat = vi.fn();
  openAgentProfiles({
    overlay,
    controller: {} as ClientController,
    session,
    returnFocusTo: opener,
    onStartProfileChat,
  });
  expect(open).toHaveBeenCalledTimes(1);
  const task = open.mock.calls[0][0];
  expect(task).toMatchObject({
    title: 'Agent profiles',
    className: 'profile-library-dialog',
    returnFocusTo: opener,
  });
  const content = task.content;
  if (!isValidElement<{ profilesOnly?: boolean }>(content))
    throw new Error('Expected a rendered profile library');
  expect(content.type).toBe(GoalProfileSettings);
  expect(content.props).toMatchObject({ profilesOnly: true, session });
});
