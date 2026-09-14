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

it('keeps all 18 categories in one shell with a single current desktop link', () => {
  show();
  const navigation = screen.getByRole('navigation', {
    name: 'Settings sections',
  });
  expect(navigation.querySelectorAll('a')).toHaveLength(18);
  expect(screen.getByRole('link', { name: 'Providers' })).toHaveAttribute(
    'aria-current',
    'page',
  );
  expect(
    [...navigation.querySelectorAll('a')].map((link) => link.textContent),
  ).toEqual(settingsLeaves.map((leaf) => leaf.label));
  expect(screen.getByRole('heading', { name: 'Providers' })).toHaveFocus();
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
