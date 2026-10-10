import { act, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useEffect, useState } from 'react';
import {
  MemoryRouter,
  Route,
  Routes,
  useLocation,
  useParams,
} from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
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

const MORE = 'settings-test:more';

/** A page that keeps loading: more rows arrive above the jumped-to one. */
function LoadingPage() {
  const [more, setMore] = useState(0);
  const [slow, setSlow] = useState(false);
  useEffect(() => {
    const add = () => setMore((value) => value + 1);
    window.addEventListener(MORE, add);
    // A row that only arrives long after the page opens (a slow link).
    const timer = setTimeout(() => setSlow(true), 10000);
    return () => {
      window.removeEventListener(MORE, add);
      clearTimeout(timer);
    };
  }, []);
  return (
    <>
      {Array.from({ length: more }, (_, index) => (
        <p key={index}>Loaded later {index}</p>
      ))}
      <p data-setting-anchor="late-row">Late row</p>
      {slow && (
        <SettingsAdvanced summary="Advanced">
          <p data-setting-anchor="slow-row">Slow row</p>
        </SettingsAdvanced>
      )}
    </>
  );
}

const nextFrame = () =>
  act(async () => {
    await new Promise((resolve) => requestAnimationFrame(resolve));
  });

afterEach(() => {
  vi.useRealTimers();
});

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
      {leaf.id === 'tracker' && <LoadingPage />}
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

it('moves through results with the arrow keys and opens the chosen one with Enter', async () => {
  const user = userEvent.setup();
  show('/settings/providers');
  const search = screen.getByRole('searchbox', { name: 'Find a setting' });
  await user.type(search, 'theme');
  const page = within(
    screen.getByRole('list', { name: 'Matching pages' }),
  ).getByRole('link', { name: /Appearance/ });
  const rows = screen.getByRole('list', { name: 'Matching settings' });
  const lightOrDark = within(rows).getByRole('link', { name: /Light or dark/ });
  const colour = within(rows).getByRole('link', { name: /Colour theme/ });
  // The first result is chosen until the arrows move it, and wraps around.
  expect(search).toHaveAttribute('aria-activedescendant', page.id);
  expect(page).toHaveAttribute('data-active', 'true');
  await user.keyboard('{ArrowUp}');
  expect(search).toHaveAttribute('aria-activedescendant', colour.id);
  await user.keyboard('{ArrowDown}{ArrowDown}');
  expect(search).toHaveAttribute('aria-activedescendant', lightOrDark.id);
  expect(lightOrDark).toHaveAttribute('data-active', 'true');
  expect(page).not.toHaveAttribute('data-active');
  await user.keyboard('{Enter}');
  expect(screen.getByLabelText('Location')).toHaveTextContent(
    '/settings/appearance#theme',
  );
  expect(search).toHaveValue('');

  // Enter alone opens the first result.
  await user.type(search, 'layout{Enter}');
  expect(screen.getByLabelText('Location')).toHaveTextContent(
    '/settings/appearance#layout',
  );
});

it('jumps again when the same result is opened twice, and focus follows the jump', async () => {
  const user = userEvent.setup();
  show('/settings/appearance');
  const search = screen.getByRole('searchbox', { name: 'Find a setting' });
  const open = async () => {
    await user.type(search, 'reset layout');
    await user.click(
      within(screen.getByRole('list', { name: 'Matching settings' })).getByRole(
        'link',
        { name: /Reset layout/ },
      ),
    );
    await nextFrame();
  };
  await open();
  const row = screen.getByText('Reset layout row');
  expect(row).toHaveAttribute('data-search-hit', 'true');
  // The result link closed with the search: focus lands on the setting.
  expect(row).toHaveFocus();
  row.removeAttribute('data-search-hit');
  await open();
  expect(row).toHaveAttribute('data-search-hit', 'true');
});

it('keeps the row in view while the page loads around it, until the person scrolls', async () => {
  const scroll = vi.fn();
  Element.prototype.scrollIntoView = scroll;
  show('/settings/tracker#late-row');
  await nextFrame();
  await nextFrame();
  const landed = scroll.mock.calls.length;
  expect(landed).toBeGreaterThan(0);
  expect(scroll.mock.contexts[0]).toBe(screen.getByText('Late row'));
  // Content above arrives and pushes the row away: the page follows it.
  act(() => {
    window.dispatchEvent(new Event(MORE));
  });
  await nextFrame();
  expect(scroll.mock.calls.length).toBeGreaterThan(landed);
  expect(scroll).toHaveBeenLastCalledWith({
    block: 'center',
    behavior: 'auto',
  });
  // Once the person scrolls, the page stays where they put it.
  fireEvent.wheel(screen.getByText('Late row'));
  const followed = scroll.mock.calls.length;
  act(() => {
    window.dispatchEvent(new Event(MORE));
  });
  await nextFrame();
  expect(scroll).toHaveBeenCalledTimes(followed);
});

it('still lands on a row that renders long after the page opens', async () => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
  show('/settings/tracker#slow-row');
  expect(screen.queryByText('Slow row')).toBeNull();
  await act(async () => {
    vi.advanceTimersByTime(10000);
  });
  // Found after ten seconds: its collapsed section opens around it.
  expect(screen.getByText('Slow row').closest('details')).toHaveAttribute(
    'open',
  );
});

it('has a skip link past the settings list to the open page', async () => {
  const user = userEvent.setup();
  show('/settings/documents');
  screen.getByRole('searchbox', { name: 'Find a setting' }).focus();
  await user.click(screen.getByRole('link', { name: 'Skip to settings' }));
  expect(
    screen.getByRole('heading', { name: 'Documents', level: 2 }),
  ).toHaveFocus();
  expect(screen.getByLabelText('Location')).toHaveTextContent(
    /^\/settings\/documents$/,
  );
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
