import { isValidElement } from 'react';
import { expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import type { useOverlay } from '../../ui/overlays';
import GoalProfileSettings, {
  type GoalProfileSettingsSession,
} from '../settings/GoalProfileSettings';
import {
  currentProfileChoice,
  DEFAULT_PROFILE_ID,
  openAgentProfiles,
  profileChoices,
} from './agent-profiles';

it('lists the Default profile once and treats no profile as Default', () => {
  const listed = [
    { id: DEFAULT_PROFILE_ID, label: 'Default' },
    { id: 'plan', label: 'Plan' },
  ];
  expect(profileChoices(listed).map((item) => item.label)).toEqual([
    'Default',
    'Plan',
  ]);
  expect(currentProfileChoice(listed, null)).toBe(DEFAULT_PROFILE_ID);
  expect(currentProfileChoice(listed, 'plan')).toBe('plan');
  // Without the built-in in the list, "no profile" stays a choice of its own.
  const custom = [{ id: 'plan', label: 'Plan' }];
  expect(profileChoices(custom)).toEqual([
    { id: '', label: 'Default' },
    { id: 'plan', label: 'Plan' },
  ]);
  expect(currentProfileChoice(custom, '')).toBe('');
});

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
    title: 'Agents',
    className: 'profile-library-dialog',
    returnFocusTo: opener,
  });
  const content = task.content;
  if (!isValidElement<{ profilesOnly?: boolean }>(content))
    throw new Error('Expected a rendered profile library');
  expect(content.type).toBe(GoalProfileSettings);
  expect(content.props).toMatchObject({ profilesOnly: true, session });
});
