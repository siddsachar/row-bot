import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, test, vi } from 'vitest';
import { COMPANY_SPACE, HANDBOOK, SALES_SPACE } from '../browser/fake-brain';
import { renderConsole, sent } from '../testing';

afterEach(() => vi.unstubAllGlobals());

const ADA = '22222222-2222-4222-8222-222222222222';
const GRACE = '33333333-3333-4333-8333-333333333333';

test('spaces say who reads and owns them and how many documents they hold', async () => {
  renderConsole('/knowledge');
  const list = await screen.findByRole('list', { name: 'Spaces' });
  const rows = within(list).getAllByRole('listitem');
  expect(
    rows.map((row) => row.querySelector('.entity-row-title')?.textContent),
  ).toEqual(['Company', 'Engineering', 'Sales team']);
  expect(rows[0]).toHaveTextContent(
    'Company space · Read by everyone signed in · Owned by it-admins · 4 documents',
  );
  expect(rows[1]).toHaveTextContent(
    'Group space · Read by Engineering · Owned by it-admins, Ada Lovelace · 1 document',
  );
  expect(rows[2]).toHaveTextContent(
    'Group space · Read by Sales · Owned by Lin Okafor · 1 document',
  );
  // A company with its models set up is told nothing about models.
  expect(screen.queryByText('No embeddings model yet')).not.toBeInTheDocument();
});

test('adding a space picks its reader groups and owners and sends them', async () => {
  const api = renderConsole('/knowledge');
  await userEvent.click(
    await screen.findByRole('button', { name: 'Add space' }),
  );
  const dialog = screen.getByRole('dialog');
  await userEvent.type(within(dialog).getByLabelText(/^Name/), 'Support');
  const readers = within(dialog).getByLabelText('Add a group to readers');
  // Enter adds the group; it doesn't send the form.
  await userEvent.type(readers, 'Support{Enter}');
  await userEvent.type(readers, 'Sales{Enter}');
  await userEvent.click(
    within(dialog).getByRole('button', { name: 'Remove Sales from readers' }),
  );
  await userEvent.selectOptions(
    within(dialog).getByLabelText('Add a person to owners'),
    'Ada Lovelace',
  );
  expect(
    within(dialog).getByRole('list', { name: 'Readers' }),
  ).toHaveTextContent('Support');
  expect(
    within(dialog).getByRole('list', { name: 'Owners' }),
  ).toHaveTextContent('Ada Lovelace');
  expect(api.calls.some((call) => call.method === 'POST')).toBe(false);

  await userEvent.click(
    within(dialog).getByRole('button', { name: 'Add space' }),
  );
  expect(await screen.findByText('Support is saved')).toBeInTheDocument();
  expect(sent(api, 'POST', '/api/admin/brain/spaces')).toEqual({
    name: 'Support',
    reader_groups: ['Support'],
    owner_groups: [],
    owner_principals: [ADA],
  });
  const row = within(screen.getByRole('list', { name: 'Spaces' }))
    .getByText('Support', { selector: '.entity-row-title' })
    .closest('li') as HTMLElement;
  expect(row).toHaveTextContent(
    'Group space · Read by Support · Owned by Ada Lovelace · No documents',
  );
});

test('the company space is read by everyone: editing it changes only its owners', async () => {
  const api = renderConsole('/knowledge');
  await userEvent.click(
    await screen.findByRole('button', { name: 'Edit Company' }),
  );
  const dialog = screen.getByRole('dialog');
  expect(
    within(dialog).queryByLabelText('Add a group to readers'),
  ).not.toBeInTheDocument();
  await userEvent.click(
    within(dialog).getByRole('button', {
      name: 'Remove it-admins from owner groups',
    }),
  );
  await userEvent.selectOptions(
    within(dialog).getByLabelText('Add a person to owners'),
    'Grace Hopper',
  );
  await userEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
  expect(await screen.findByText('Company is saved')).toBeInTheDocument();
  expect(
    sent(api, 'PATCH', `/api/admin/brain/spaces/${COMPANY_SPACE}`),
  ).toEqual({ owner_groups: [], owner_principals: [GRACE] });
});

test('deleting a space asks first and says what is lost; the company space stays', async () => {
  const api = renderConsole('/knowledge');
  const company = await screen.findByRole('button', {
    name: "The company space can't be deleted",
  });
  expect(company).toHaveAttribute('aria-disabled', 'true');
  await userEvent.click(company);
  expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();

  await userEvent.click(
    screen.getByRole('button', { name: 'Delete Sales team' }),
  );
  const confirm = screen.getByRole('alertdialog');
  expect(confirm).toHaveTextContent('Delete Sales team?');
  expect(confirm).toHaveTextContent(
    "Its 1 document, its facts and its knowledge are deleted for good, for everyone. This can't be undone.",
  );
  await userEvent.click(
    within(confirm).getByRole('button', { name: 'Cancel' }),
  );
  expect(api.calls.some((call) => call.method === 'DELETE')).toBe(false);

  await userEvent.click(
    screen.getByRole('button', { name: 'Delete Sales team' }),
  );
  await userEvent.click(
    within(screen.getByRole('alertdialog')).getByRole('button', {
      name: 'Delete',
    }),
  );
  expect(await screen.findByText('Sales team is deleted')).toBeInTheDocument();
  const spaces = screen.getByRole('list', { name: 'Spaces' });
  expect(within(spaces).queryByText('Sales team')).not.toBeInTheDocument();
  expect(api.calls.map((call) => `${call.method} ${call.path}`)).toContain(
    `DELETE /api/admin/brain/spaces/${SALES_SPACE}`,
  );
});

test('without an embeddings or a chat model the page says so and links to Models', async () => {
  renderConsole('/knowledge', 'empty');
  expect(
    await screen.findByText('No embeddings model yet'),
  ).toBeInTheDocument();
  expect(
    screen.getByRole('link', { name: 'Add an embeddings model in Models' }),
  ).toHaveAttribute('href', '/models?tab=catalog');
  expect(
    screen.getByRole('link', { name: 'Add a chat model in Models' }),
  ).toHaveAttribute('href', '/models?tab=catalog');
  const list = await screen.findByRole('list', { name: 'Spaces' });
  expect(list).toHaveTextContent('No documents');
});

test('opening a space shows its documents, and All spaces goes back', async () => {
  renderConsole('/knowledge');
  await userEvent.click(
    await screen.findByRole('button', { name: 'Open Engineering' }),
  );
  expect(
    await screen.findByRole('heading', { name: 'Engineering' }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole('tab', { name: 'Documents', selected: true }),
  ).toBeInTheDocument();
  expect(
    await screen.findByRole('list', { name: 'Documents' }),
  ).toHaveTextContent('Release checklist');
  await userEvent.click(screen.getByRole('link', { name: 'All spaces' }));
  expect(
    await screen.findByRole('list', { name: 'Spaces' }),
  ).toBeInTheDocument();
});

test('facts: add one with its source, edit one, and retire one after a confirm', async () => {
  const api = renderConsole(`/knowledge?space=${COMPANY_SPACE}&tab=facts`);
  const facts = () => screen.getByRole('list', { name: 'Facts' });
  await screen.findByRole('list', { name: 'Facts' });
  expect(within(facts()).getAllByRole('listitem')).toHaveLength(2);
  expect(facts()).toHaveTextContent(
    'Expense claims are due by the 5th of each month.',
  );
  expect(facts()).toHaveTextContent('· From Expenses policy');
  await userEvent.click(screen.getByRole('radio', { name: 'Retired' }));
  expect(facts()).toHaveTextContent('Parking is free on Fridays.');
  expect(facts()).toHaveTextContent('Retired');
  await userEvent.click(screen.getByRole('radio', { name: 'In force' }));

  await userEvent.click(screen.getByRole('button', { name: 'Add fact' }));
  let dialog = screen.getByRole('dialog');
  await userEvent.type(
    within(dialog).getByLabelText(/^Fact/),
    'Visitors sign in at reception.',
  );
  await userEvent.selectOptions(
    within(dialog).getByLabelText(/^Source/),
    'Employee handbook',
  );
  await userEvent.click(
    within(dialog).getByRole('button', { name: 'Add fact' }),
  );
  expect(await screen.findByText('The fact is saved')).toBeInTheDocument();
  expect(facts()).toHaveTextContent('Visitors sign in at reception.');
  expect(
    sent(api, 'POST', `/api/brain/spaces/${COMPANY_SPACE}/facts`),
  ).toEqual({
    text: 'Visitors sign in at reception.',
    source_document_id: HANDBOOK,
  });

  await userEvent.click(
    screen.getByRole('button', {
      name: 'Edit “The office is closed on public holidays.”',
    }),
  );
  dialog = screen.getByRole('dialog');
  const text = within(dialog).getByLabelText(/^Fact/);
  await userEvent.clear(text);
  await userEvent.type(
    text,
    'The office is closed on public holidays and on 24 December.',
  );
  await userEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
  await waitFor(() =>
    expect(
      api.calls.filter((call) => call.method === 'PATCH').at(-1)?.body,
    ).toEqual({
      text: 'The office is closed on public holidays and on 24 December.',
    }),
  );

  await userEvent.click(
    screen.getByRole('button', {
      name: 'Retire “Visitors sign in at reception.”',
    }),
  );
  const confirm = screen.getByRole('alertdialog');
  expect(confirm).toHaveTextContent('Retire this fact?');
  expect(confirm).toHaveTextContent('it can’t be brought back');
  await userEvent.click(
    within(confirm).getByRole('button', { name: 'Retire' }),
  );
  await waitFor(() =>
    expect(facts()).not.toHaveTextContent('Visitors sign in at reception.'),
  );
  expect(
    api.calls.some(
      (call) => call.method === 'POST' && call.path.endsWith('/retire'),
    ),
  ).toBe(true);
});
