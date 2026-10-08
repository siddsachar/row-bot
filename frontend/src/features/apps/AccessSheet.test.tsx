import { render, screen } from '@testing-library/react';
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
  render(
    <AccessSheet
      open
      name="Atlassian"
      access={access}
      change={change}
      busy={false}
      onAllow={vi.fn()}
      onCancel={vi.fn()}
    />,
  );
}

it('keeps a sign-in made to look things up reading while connecting', () => {
  sheet(false);
  expect(screen.getByRole('radio', { name: /^Read only/ })).toBeEnabled();
  expect(
    screen.getByRole('radio', { name: /^Ask before changes/ }),
  ).toBeDisabled();
  expect(screen.getByRole('radio', { name: /^Full access/ })).toBeDisabled();
  expect(screen.getByText(/asked for read access only/)).toBeVisible();
});

it('lets its Access allow changes later, which signs in once more', () => {
  sheet(true);
  expect(
    screen.getByRole('radio', { name: /^Ask before changes/ }),
  ).toBeEnabled();
});
