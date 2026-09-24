import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useParams } from 'react-router-dom';
import { expect, it } from 'vitest';
import { resolveSetting, settingsLeaves } from './model';
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

it('shows the current category and keeps all 17 leaves reachable by search', () => {
  show();
  const navigation = screen.getByRole('navigation', {
    name: 'Settings sections',
  });
  expect(navigation.querySelectorAll('a')).toHaveLength(3);
  expect(
    screen.getByRole('button', { name: 'Models and input' }),
  ).toHaveAttribute('aria-pressed', 'true');
  expect(screen.getByRole('link', { name: 'Providers' })).toHaveAttribute(
    'aria-current',
    'page',
  );
  expect(screen.getByRole('heading', { name: 'Settings' })).toBeVisible();
  expect(screen.getByRole('heading', { name: 'Providers' })).toHaveFocus();
  fireEvent.change(screen.getByRole('searchbox', { name: 'Find a setting' }), {
    target: { value: 'access' },
  });
  expect(screen.getByRole('link', { name: 'System' })).toBeVisible();
  fireEvent.change(screen.getByRole('searchbox', { name: 'Find a setting' }), {
    target: { value: '' },
  });
  fireEvent.click(
    screen.getByRole('button', { name: 'Tools and integrations' }),
  );
  expect(
    [...navigation.querySelectorAll('a')].map((link) => link.textContent),
  ).toEqual(['Tools', 'Skills', 'Accounts', 'Channels', 'MCP', 'Plugins']);
  expect(settingsLeaves).toHaveLength(17);
});

it('uses the compact labelled picker to navigate while preserving the shell', () => {
  show();
  fireEvent.change(screen.getByRole('combobox', { name: 'Settings section' }), {
    target: { value: 'documents' },
  });
  expect(screen.getByRole('heading', { name: 'Documents' })).toHaveFocus();
  expect(screen.getByText('Documents owner')).toBeVisible();
  expect(screen.getByRole('link', { name: 'Documents' })).toHaveAttribute(
    'aria-current',
    'page',
  );
});
