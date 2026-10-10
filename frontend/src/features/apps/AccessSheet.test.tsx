import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import type { PlanAccess } from '../../api/types';
import AccessSheet from './AccessSheet';

const access: PlanAccess = {
  preset: 'read_only',
  tools_digest: 'd'.repeat(64),
  limited: true,
  note: 'Atlassian was asked for read access only.',
  tools: [
    {
      name: 'createJiraIssue',
      title: 'Create Jira issue',
      description: '',
      effect: 'mutation',
      state: 'off',
      always_asks: false,
      view: false,
      view_only: false,
    },
  ],
};

function sheet(change: boolean) {
  const onAllow = vi.fn();
  render(
    <AccessSheet
      open
      name="Atlassian"
      access={access}
      change={change}
      busy={false}
      onAllow={onAllow}
      onCancel={vi.fn()}
    />,
  );
  return onAllow;
}

it('keeps a sign-in made to look things up reading while connecting, without asking again', () => {
  const onAllow = sheet(false);
  // Chosen when connecting: no second question, and nothing that makes changes can be picked.
  expect(screen.queryByRole('radio')).toBeNull();
  expect(screen.getByText(/looks things up only, as you chose/)).toBeVisible();
  expect(screen.getByText(/asked for read access only/)).toBeVisible();
  expect(screen.getByRole('option', { name: 'Ask first' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Allow' }));
  expect(onAllow).toHaveBeenCalledWith(
    expect.objectContaining({ preset: 'read_only' }),
  );
});

it('asks nothing of an app that only looks things up', () => {
  render(
    <AccessSheet
      open
      name="Context7"
      access={{
        ...access,
        preset: 'ask',
        limited: false,
        note: '',
        tools: [
          {
            ...access.tools[0],
            name: 'resolve',
            title: 'Resolve library',
            effect: 'read_only',
            state: 'use',
          },
        ],
      }}
      change={false}
      busy={false}
      onAllow={vi.fn()}
      onCancel={vi.fn()}
    />,
  );
  expect(screen.queryByRole('radio')).toBeNull();
  expect(screen.getByText(/only looks things up/)).toBeVisible();
  expect(screen.getByRole('button', { name: 'Allow' })).toBeEnabled();
});

it('lets its Access allow changes later, which signs in once more', () => {
  sheet(true);
  expect(
    screen.getByRole('radio', { name: /^Ask before changes/ }),
  ).toBeEnabled();
});
