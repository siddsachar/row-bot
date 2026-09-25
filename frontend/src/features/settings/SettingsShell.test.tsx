import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useParams } from 'react-router-dom';
import { expect, it } from 'vitest';
import { resolveSetting, settingsGroups, settingsLeaves } from './model';
import SettingsShell from './SettingsShell';

function Harness() {
  const { setting = 'providers' } = useParams();
  const leaf = resolveSetting(setting)!;
  return (
    <SettingsShell leaf={leaf}>
      <p>{leaf.label} owner</p>
    </SettingsShell>
  );
}

function show(route = '/settings/providers') {
  return render(
    <MemoryRouter initialEntries={[route]}>
      <Routes>
        <Route path="settings/:setting" element={<Harness />} />
      </Routes>
    </MemoryRouter>,
  );
}

it('nests each leaf list beneath its category and expands the deep-linked category', () => {
  show('/settings/documents');
  const navigation = screen.getByRole('navigation', {
    name: 'Settings sections',
  });
  const categories = within(navigation).getAllByRole('button');
  expect(categories.map((button) => button.textContent)).toEqual(
    settingsGroups.map((group) => group.label),
  );
  for (const group of settingsGroups) {
    expect(
      within(navigation).getByRole('heading', { name: group.label }),
    ).toBeVisible();
    const button = within(navigation).getByRole('button', {
      name: group.label,
    });
    const list = navigation.querySelector(`#settings-group-${group.id}`)!;
    expect(button).toHaveAttribute('aria-controls', list.id);
    expect(button.parentElement?.nextElementSibling).toBe(list);
    expect(
      within(list as HTMLElement).getAllByRole('link', { hidden: true }),
    ).toHaveLength(group.leaves.length);
    expect(button).toHaveAttribute(
      'aria-expanded',
      group.id === 'knowledge' ? 'true' : 'false',
    );
  }
  expect(within(navigation).getAllByRole('link')).toHaveLength(2);
  expect(screen.getByRole('link', { name: 'Documents' })).toHaveAttribute(
    'aria-current',
    'page',
  );
  expect(screen.getByRole('heading', { name: 'Documents' })).toHaveFocus();
  expect(settingsLeaves).toHaveLength(17);
});

it('opens and closes category disclosures with the keyboard and keeps focus', async () => {
  const user = userEvent.setup();
  show();
  const models = screen.getByRole('button', { name: 'Models and input' });
  const integrations = screen.getByRole('button', {
    name: 'Tools and integrations',
  });
  models.focus();
  await user.keyboard('{Enter}');
  expect(models).toHaveFocus();
  expect(models).toHaveAttribute('aria-expanded', 'false');
  expect(screen.queryByRole('link', { name: 'Providers' })).toBeNull();
  await user.keyboard('{Tab}');
  await user.keyboard('{Tab}');
  expect(integrations).toHaveFocus();
  await user.keyboard(' ');
  expect(integrations).toHaveFocus();
  expect(integrations).toHaveAttribute('aria-expanded', 'true');
  expect(screen.getByRole('link', { name: 'Tools' })).toBeVisible();
  expect(screen.queryByRole('link', { name: 'Providers' })).toBeNull();
});

it('reveals matching links inside their categories and restores the active category after search', () => {
  show('/settings/providers');
  const search = screen.getByRole('searchbox', { name: 'Find a setting' });
  search.focus();
  fireEvent.change(search, { target: { value: 'access' } });
  expect(search).toHaveFocus();
  expect(
    screen.getByRole('button', { name: 'System and access' }),
  ).toHaveAttribute('aria-expanded', 'true');
  expect(screen.getByRole('link', { name: 'System' })).toBeVisible();
  expect(screen.queryByRole('link', { name: 'Providers' })).toBeNull();
  fireEvent.change(search, { target: { value: 'and' } });
  for (const group of settingsGroups) {
    expect(screen.getByRole('button', { name: group.label })).toHaveAttribute(
      'aria-expanded',
      group.id === 'personal' ? 'false' : 'true',
    );
  }
  fireEvent.change(search, { target: { value: 'no such setting' } });
  expect(screen.getByText('No settings found.')).toBeVisible();
  fireEvent.change(search, { target: { value: '' } });
  expect(
    screen.getByRole('button', { name: 'Models and input' }),
  ).toHaveAttribute('aria-expanded', 'true');
  expect(screen.getByRole('link', { name: 'Providers' })).toHaveAttribute(
    'aria-current',
    'page',
  );
});

it('navigates from a searched result and expands its category after clearing search', async () => {
  const user = userEvent.setup();
  show();
  const search = screen.getByRole('searchbox', { name: 'Find a setting' });
  await user.type(search, 'gmail');
  expect(screen.getByRole('link', { name: 'Accounts' })).toBeVisible();
  await user.click(screen.getByRole('link', { name: 'Accounts' }));
  expect(screen.getByRole('heading', { name: 'Accounts' })).toHaveFocus();
  expect(screen.getByText('Accounts owner')).toBeVisible();
  await user.clear(search);
  expect(
    screen.getByRole('button', { name: 'Tools and integrations' }),
  ).toHaveAttribute('aria-expanded', 'true');
  expect(screen.getByRole('link', { name: 'Accounts' })).toHaveAttribute(
    'aria-current',
    'page',
  );
});

it('uses the compact labelled picker to navigate while preserving the shell', () => {
  show();
  fireEvent.click(screen.getByRole('button', { name: 'Models and input' }));
  expect(
    screen.getByRole('button', { name: 'Models and input' }),
  ).toHaveAttribute('aria-expanded', 'false');
  fireEvent.change(screen.getByRole('combobox', { name: 'Settings section' }), {
    target: { value: 'documents' },
  });
  expect(screen.getByRole('heading', { name: 'Documents' })).toHaveFocus();
  expect(screen.getByText('Documents owner')).toBeVisible();
  expect(screen.getByRole('link', { name: 'Documents' })).toHaveAttribute(
    'aria-current',
    'page',
  );
  expect(
    screen.getByRole('button', { name: 'Knowledge and documents' }),
  ).toHaveAttribute('aria-expanded', 'true');
});
