import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, test, vi } from 'vitest';
import {
  ATLAS,
  ATLAS_APP,
  COMPANY_SPACE,
  PLATFORM,
  SALES_SPACE,
} from '../../browser/fake-brain';
import { renderConsole, sent } from '../../testing';

afterEach(() => vi.unstubAllGlobals());

const PAGE = `/knowledge?space=${COMPANY_SPACE}&tab=graph`;
const ME = '11111111-1111-4111-8111-111111111111';
const LIN = '44444444-4444-4444-8444-444444444444';

const entries = () => screen.getByRole('list', { name: 'Company knowledge' });
const titles = () =>
  within(entries())
    .getAllByRole('listitem')
    .map((item) => item.querySelector('.entity-row-title')?.textContent);

function entry(name: string) {
  return within(entries())
    .getByText(name, { selector: '.entity-row-title' })
    .closest('li') as HTMLElement;
}

test('entries list their type and connections, and open on their mentions as citations', async () => {
  renderConsole(PAGE);
  await screen.findByRole('list', { name: 'Company knowledge' });
  expect(titles()).toEqual([
    'Atlas',
    'Expenses policy',
    'Platform team',
    'Atlas app',
    'Hot desk',
  ]);
  expect(entry('Atlas')).toHaveTextContent('Project · 2 connections');
  expect(entry('Atlas app')).toHaveTextContent('Product · 0 connections');
  expect(entry('Hot desk')).toHaveTextContent('Retired');
  expect(
    within(entry('Hot desk')).getByRole('button', {
      name: 'Restore Hot desk',
    }),
  ).toBeInTheDocument();

  await userEvent.click(
    screen.getByRole('button', { name: 'Show details for Atlas' }),
  );
  expect(await screen.findByText('The new expenses app.')).toBeInTheDocument();
  const connections = screen.getByRole('region', { name: 'Connections' });
  expect(connections).toHaveTextContent('Platform team works on Atlas');
  expect(connections).toHaveTextContent('Expenses policy applies to Atlas');
  const mentions = screen.getByRole('region', { name: 'Mentioned in' });
  expect(mentions).toHaveTextContent(
    'Employee handbook… the Platform team builds Atlas, so ask them first …',
  );
  expect(mentions).toHaveTextContent(
    'A fact from Expenses policyExpense claims are due by the 5th of each month.',
  );
});

test('merging picks another entry of the space and folds this one into it', async () => {
  const api = renderConsole(PAGE);
  await userEvent.click(
    await screen.findByRole('button', {
      name: 'Merge Atlas app into another entry',
    }),
  );
  const dialog = screen.getByRole('dialog');
  expect(dialog).toHaveTextContent('A merge can’t be undone.');
  const into = await within(dialog).findByLabelText('Into');
  expect(
    within(into)
      .getAllByRole('option')
      .map((option) => option.textContent),
  ).toEqual([
    'Choose an entry',
    'Atlas (Project)',
    'Expenses policy (Policy)',
    'Platform team (Team)',
  ]);
  await userEvent.selectOptions(into, 'Atlas (Project)');
  await userEvent.click(within(dialog).getByRole('button', { name: 'Merge' }));

  expect(
    await screen.findByText('Atlas app is merged into Atlas'),
  ).toBeInTheDocument();
  expect(sent(api, 'POST', `/api/brain/entities/${ATLAS_APP}/merge`)).toEqual({
    into: ATLAS,
  });
  expect(titles()).not.toContain('Atlas app');
  expect(entry('Atlas')).toHaveTextContent('Also called Atlas app');
});

test('renaming sends only the new name, and keeps the old one as another name', async () => {
  const api = renderConsole(PAGE);
  await userEvent.click(
    await screen.findByRole('button', { name: 'Edit Platform team' }),
  );
  const dialog = screen.getByRole('dialog');
  const name = within(dialog).getByLabelText(/^Name/);
  await userEvent.clear(name);
  await userEvent.type(name, 'Platform group');
  await userEvent.click(within(dialog).getByRole('button', { name: 'Save' }));

  expect(
    await screen.findByText('Platform group is saved'),
  ).toBeInTheDocument();
  expect(sent(api, 'PATCH', `/api/brain/entities/${PLATFORM}`)).toEqual({
    subject: 'Platform group',
  });
  expect(entry('Platform group')).toHaveTextContent(
    'Team · 1 connection · Also called Platform team',
  );
});

test('a name and type another entry has is refused with what to do instead', async () => {
  renderConsole(PAGE);
  await userEvent.click(
    await screen.findByRole('button', { name: 'Edit Atlas app' }),
  );
  const dialog = screen.getByRole('dialog');
  const name = within(dialog).getByLabelText(/^Name/);
  await userEvent.clear(name);
  await userEvent.type(name, 'Atlas');
  await userEvent.selectOptions(
    within(dialog).getByLabelText(/^Type/),
    'Project',
  );
  await userEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
  expect(await within(dialog).findByRole('alert')).toHaveTextContent(
    'Another entry in this space already has that name and type. Merge the two instead.',
  );
});

test('retiring and restoring an entry need no confirm and show at once', async () => {
  const api = renderConsole(PAGE);
  await userEvent.click(
    await screen.findByRole('button', { name: 'Retire Atlas' }),
  );
  expect(await screen.findByText('Atlas is retired')).toBeInTheDocument();
  expect(entry('Atlas')).toHaveTextContent('Retired');
  await userEvent.click(screen.getByRole('button', { name: 'Restore Atlas' }));
  expect(await screen.findByText('Atlas is restored')).toBeInTheDocument();
  expect(entry('Atlas')).not.toHaveTextContent('Retired');
  const posted = api.calls.filter((call) => call.method === 'POST');
  expect(posted.map((call) => call.path)).toEqual([
    `/api/brain/entities/${ATLAS}/retire`,
    `/api/brain/entities/${ATLAS}/restore`,
  ]);
});

test('searching asks the server for names and other names that match', async () => {
  const api = renderConsole(PAGE);
  await screen.findByRole('list', { name: 'Company knowledge' });
  await userEvent.type(
    screen.getByRole('searchbox', { name: 'Search knowledge' }),
    'atl',
  );
  await waitFor(() => expect(titles()).toEqual(['Atlas', 'Atlas app']));
  expect(api.calls.some((call) => call.path.includes('q=atl'))).toBe(true);
  await userEvent.clear(
    screen.getByRole('searchbox', { name: 'Search knowledge' }),
  );
  await userEvent.type(
    screen.getByRole('searchbox', { name: 'Search knowledge' }),
    'nobody',
  );
  expect(
    await screen.findByText('Nothing matches “nobody”.'),
  ).toBeInTheDocument();
});

test('an admin who does not read a space is offered to own it, then sees its knowledge', async () => {
  const api = renderConsole(`/knowledge?space=${SALES_SPACE}&tab=graph`);
  expect(
    await screen.findByRole('heading', {
      name: 'Only its readers see its knowledge',
    }),
  ).toBeInTheDocument();
  await userEvent.click(
    screen.getByRole('button', { name: 'Make me an owner' }),
  );
  expect(
    await screen.findByRole('heading', { name: 'No knowledge yet' }),
  ).toBeInTheDocument();
  expect(sent(api, 'PATCH', `/api/admin/brain/spaces/${SALES_SPACE}`)).toEqual({
    owner_principals: [LIN, ME],
  });
});
