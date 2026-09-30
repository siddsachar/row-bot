import { act, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {
  MemoryRouter,
  Route,
  Routes,
  useLocation,
  useParams,
} from 'react-router-dom';
import { expect, it, vi } from 'vitest';
import { resolveSetting, settingsGroups, settingsLeaves } from './model';
import SettingsShell from './SettingsShell';
import {
  SettingsAdvanced,
  SettingsSummary,
  SettingsTabs,
  SummaryChip,
  useTabForAnchor,
} from './anatomy';

function Where() {
  const location = useLocation();
  return (
    <output aria-label="Location">{location.pathname + location.hash}</output>
  );
}

function DiscoverTabs() {
  const [tab, setTab] = useTabForAnchor<'installed' | 'discover'>('installed', {
    'public-skills': 'discover',
  });
  return (
    <SettingsTabs
      label="Skills"
      value={tab}
      onChange={setTab}
      tabs={[
        { id: 'installed', label: 'Installed', content: <p>Installed list</p> },
        {
          id: 'discover',
          label: 'Discover',
          content: <p data-setting-anchor="public-skills">Public hub</p>,
        },
      ]}
    />
  );
}

function Harness() {
  const { setting = 'providers' } = useParams();
  const leaf = resolveSetting(setting)!;
  return (
    <SettingsShell leaf={leaf}>
      <p>{leaf.label} owner</p>
      <SettingsSummary>
        <SummaryChip tone="success">3 ready</SummaryChip>
      </SettingsSummary>
      {leaf.id === 'appearance' && (
        <SettingsAdvanced summary="Advanced">
          <p data-setting-anchor="layout">Reset layout row</p>
        </SettingsAdvanced>
      )}
      {leaf.id === 'skills' && <DiscoverTabs />}
      <Where />
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

it('lists every group with its leaves and marks the open page', () => {
  show('/settings/documents');
  const navigation = screen.getByRole('navigation', {
    name: 'Settings sections',
  });
  expect(within(navigation).queryAllByRole('heading')).toHaveLength(0);
  expect(
    within(navigation)
      .getAllByRole('list')
      .map((list) => list.getAttribute('aria-label')),
  ).toEqual(settingsGroups.map((group) => group.label));
  expect(within(navigation).getAllByRole('link')).toHaveLength(
    settingsLeaves.length,
  );
  for (const group of settingsGroups)
    expect(
      within(
        within(navigation).getByRole('list', { name: group.label }),
      ).getAllByRole('link'),
    ).toHaveLength(group.leaves.length);
  expect(screen.getByRole('link', { name: 'Documents' })).toHaveAttribute(
    'aria-current',
    'page',
  );
  expect(screen.getByRole('heading', { name: 'Documents' })).toHaveFocus();
  expect(
    screen.getByRole('heading', { name: 'Settings', level: 1 }),
  ).toBeVisible();
  // The page's summary renders in its header, beside the title.
  expect(
    screen.getByText('3 ready').closest('.settings-pane-header'),
  ).not.toBeNull();
});

it('finds pages and individual settings, then jumps to the matching row', async () => {
  const user = userEvent.setup();
  show('/settings/providers');
  const search = screen.getByRole('searchbox', { name: 'Find a setting' });
  await user.type(search, 'layout');
  const rows = screen.getByRole('list', { name: 'Matching settings' });
  await user.click(within(rows).getByRole('link', { name: /Reset layout/ }));
  expect(screen.getByLabelText('Location')).toHaveTextContent(
    '/settings/appearance#layout',
  );
  expect(search).toHaveValue('');
  expect(screen.getByRole('heading', { name: 'Appearance' })).toBeVisible();
  const row = screen.getByText('Reset layout row');
  // The collapsed Advanced section opens so the row is on screen.
  expect(row.closest('details')).toHaveAttribute('open');
  await act(async () => {
    await new Promise((resolve) => requestAnimationFrame(resolve));
  });
  expect(row).toHaveAttribute('data-search-hit', 'true');

  await user.type(search, 'no such setting');
  expect(screen.getByText('No settings found.')).toBeVisible();
  await user.keyboard('{Escape}');
  expect(search).toHaveValue('');
});

it('switches to the tab that holds a jumped-to row', async () => {
  show('/settings/skills#public-skills');
  expect(screen.getByRole('tab', { name: 'Discover' })).toHaveAttribute(
    'aria-selected',
    'true',
  );
  expect(screen.getByText('Public hub')).toBeVisible();
  expect(screen.getByText('Installed list')).not.toBeVisible();
});

it('focuses search with the slash key when not typing elsewhere', async () => {
  const user = userEvent.setup();
  show();
  await user.keyboard('/');
  expect(
    screen.getByRole('searchbox', { name: 'Find a setting' }),
  ).toHaveFocus();
});

it('uses the compact grouped picker to navigate while preserving the shell', () => {
  show();
  const picker = screen.getByRole('combobox', { name: 'Settings section' });
  expect(
    within(picker)
      .getAllByRole('group')
      .map((group) => group.getAttribute('label')),
  ).toEqual(settingsGroups.map((group) => group.label));
  fireEvent.change(picker, { target: { value: 'documents' } });
  expect(screen.getByRole('heading', { name: 'Documents' })).toHaveFocus();
  expect(screen.getByText('Documents owner')).toBeVisible();
  expect(screen.getByRole('link', { name: 'Documents' })).toHaveAttribute(
    'aria-current',
    'page',
  );
});

it('keeps the anchor search from scrolling when motion is reduced', () => {
  const scroll = vi.fn();
  Element.prototype.scrollIntoView = scroll;
  show('/settings/appearance#layout');
  expect(
    screen.getByText('Reset layout row').closest('details'),
  ).toHaveAttribute('open');
});

it('has no Agents group; search still finds the Agent profile library, whose link opens the Agents dialog (B260)', async () => {
  const user = userEvent.setup();
  show('/settings/providers');
  const navigation = screen.getByRole('navigation', {
    name: 'Settings sections',
  });
  expect(within(navigation).queryByRole('list', { name: 'Agents' })).toBeNull();
  expect(
    within(navigation).queryByRole('link', { name: 'Agent profiles' }),
  ).toBeNull();
  await user.type(
    screen.getByRole('searchbox', { name: 'Find a setting' }),
    'agent profile',
  );
  const rows = screen.getByRole('list', { name: 'Matching settings' });
  expect(
    within(rows).getByRole('link', { name: /Agent profile library/ }),
  ).toHaveAttribute('href', '/settings/profiles');
});
