import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, test, vi } from 'vitest';
import { renderConsole, sent } from '../testing';

afterEach(() => vi.unstubAllGlobals());

test('the company policy reads as plain settings, and a change is saved as the server document', async () => {
  const api = renderConsole('/policy');
  const keys = await screen.findByRole('switch', { name: 'Personal API keys' });
  expect(keys).not.toBeChecked();
  expect(
    screen.getByRole('textbox', { name: 'Tools switched off' }),
  ).toHaveValue('computer_use, shell');
  expect(screen.getByRole('radio', { name: 'Auto' })).toHaveAttribute(
    'aria-checked',
    'true',
  );
  expect(screen.queryByText(/unsaved change/)).not.toBeInTheDocument();

  await userEvent.click(keys);
  await userEvent.click(
    within(
      screen.getByRole('radiogroup', { name: 'Models people may use' }),
    ).getByRole('radio', { name: 'Only these' }),
  );
  const allowed = screen.getByRole('list', { name: 'Allowed models' });
  await userEvent.click(
    within(allowed).getByRole('switch', { name: 'company-embed' }),
  );
  expect(screen.getByText('2 unsaved changes')).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: 'Save policy' }));

  expect(
    await screen.findByText(/The company policy is saved/),
  ).toBeInTheDocument();
  expect(sent(api, 'PUT', '/api/admin/policy')).toEqual({
    version: 3,
    models: {
      allowed: ['claude-sonnet', 'company-chat'],
      personal_api_keys: true,
      local_models: true,
    },
    tools: { disabled_groups: ['computer_use', 'shell'] },
    approvals: { max_level: 'auto' },
    apps: { mcp_servers: 'allow-list', allowed_mcp_servers: ['jira'] },
  });
  // And back: the saved version is what the form now shows, with nothing unsaved.
  expect(
    screen.getByRole('switch', { name: 'Personal API keys' }),
  ).toBeChecked();
  expect(screen.queryByText(/unsaved change/)).not.toBeInTheDocument();
});

test('a tightening a group still gets back is shown calmly, and unknown models are warned about', async () => {
  renderConsole('/policy', 'populated', (url, method) =>
    method === 'PUT'
      ? {
          status: 200,
          body: {
            policy: { version: 4, models: { allowed: ['ghost'] } },
            unknown_models: ['ghost'],
            still_granted_by: {
              Engineering: [
                'models.personal_api_keys',
                'tools.enabled_groups: shell',
              ],
            },
          },
        }
      : undefined,
  );
  await userEvent.click(
    await screen.findByRole('switch', { name: 'Local models' }),
  );
  await userEvent.click(screen.getByRole('button', { name: 'Save policy' }));
  const notices = await screen.findAllByRole('status');
  const granted = notices.find((notice) =>
    notice.textContent?.includes('Some groups still get'),
  );
  expect(granted).toHaveTextContent(
    'Engineering: Personal API keys; Tool groups switched back on: shell',
  );
  expect(
    screen.getByText("Some models aren't in the catalog yet"),
  ).toBeInTheDocument();
  expect(
    screen.getByText(/ghost: people can use them once you add them/),
  ).toBeInTheDocument();
});

test('discarding puts the saved policy back', async () => {
  renderConsole('/policy');
  await userEvent.click(
    await screen.findByRole('switch', { name: 'Chat export' }),
  );
  await userEvent.click(screen.getByRole('button', { name: 'Discard' }));
  expect(screen.getByRole('switch', { name: 'Chat export' })).toBeChecked();
  expect(screen.queryByText(/unsaved change/)).not.toBeInTheDocument();
});

test('a group override shows only what it adds, and edits only add', async () => {
  const api = renderConsole('/policy?tab=groups');
  expect(
    await screen.findByText('What Engineering gets on top'),
  ).toBeInTheDocument();
  expect(
    screen.getByText(
      'An override can only add to the company policy. Restrictions belong in the company policy.',
    ),
  ).toBeInTheDocument();
  expect(screen.getByText('Personal API keys: Allowed')).toBeInTheDocument();
  expect(
    screen.getByText('Tool groups switched back on: shell'),
  ).toBeInTheDocument();
  expect(screen.getByText('Offline days: 30 days')).toBeInTheDocument();

  await userEvent.click(
    screen.getByRole('button', { name: 'Change what Engineering gets' }),
  );
  const dialog = screen.getByRole('dialog');
  // Only what a group can be given beyond the company policy: local models are already allowed.
  expect(
    within(dialog).queryByRole('switch', { name: 'Local models' }),
  ).not.toBeInTheDocument();
  await userEvent.click(
    within(dialog).getByRole('switch', { name: 'computer_use' }),
  );
  await userEvent.click(
    within(dialog).getByRole('switch', { name: 'Personal API keys' }),
  );
  await userEvent.click(within(dialog).getByRole('button', { name: 'Save' }));

  expect(
    await screen.findByText(
      'Tool groups switched back on: computer_use, shell',
    ),
  ).toBeInTheDocument();
  expect(sent(api, 'PUT', '/api/admin/policy/groups/Engineering')).toEqual({
    version: 2,
    tools: { enabled_groups: ['computer_use', 'shell'] },
    offline: { max_days: 30 },
  });

  await userEvent.selectOptions(
    screen.getByRole('combobox', { name: 'Group' }),
    'Sales',
  );
  expect(
    screen.getByText('Nothing: Sales gets the company policy.'),
  ).toBeInTheDocument();
});

test('the history names who changed what and when', async () => {
  renderConsole('/policy?tab=history');
  const list = await screen.findByRole('list', { name: 'Policy history' });
  const rows = within(list).getAllByRole('listitem');
  expect(rows[0]).toHaveTextContent('Company policy');
  expect(rows[0]).toHaveTextContent('Version 3 · Sam Rivera');
  expect(rows[1]).toHaveTextContent("Engineering's override");
  await userEvent.click(
    within(rows[2]).getByRole('button', { name: /Show details/ }),
  );
  expect(
    within(rows[2]).getByText('Personal API keys: Not allowed'),
  ).toBeVisible();
});

test('a policy that cannot load says so, with a retry', async () => {
  renderConsole('/policy', 'error');
  expect(
    await screen.findByText("Couldn't load the policy"),
  ).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
});

test('an empty server shows the default policy and no history', async () => {
  renderConsole('/policy?tab=history', 'empty');
  expect(
    await screen.findByRole('heading', { name: 'No changes yet' }),
  ).toBeInTheDocument();
});
