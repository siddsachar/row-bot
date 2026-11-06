import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import { setPlace } from '../../api/place';
import type { IntegrationEntry, IntegrationEntryPage } from '../../api/types';
import type { ClientPlatform } from '../../platform';
import { RuntimeContext } from '../../runtime';
import { WorkspaceActionsContext } from '../shell/workspace-actions';
import AppsArea from './AppsArea';

const revision = 'a'.repeat(64);
const now = Math.floor(Date.now() / 1000);

function entry(fields: Partial<IntegrationEntry> = {}): IntegrationEntry {
  return {
    id: 'mcp:official:io.github.fan/tool@1.0.0',
    kind: 'mcp',
    parent_id: null,
    name: 'tool',
    description: 'A community server.',
    app: null,
    icon: 'letter:T',
    verified: false,
    label: 'community',
    match: 'name',
    source: 'official',
    method: 'local',
    publisher: 'fan on GitHub',
    version: '1.0.0',
    installed: false,
    enabled: false,
    account_label: '',
    compatibility: 'not_inspected',
    evidence: 'listed',
    tested_with_row_bot: false,
    lifecycle: 'available',
    readiness: null,
    blockers: [],
    next_action: { kind: 'connect', label: 'Connect' },
    attributions: [],
    children: [],
    ...fields,
  };
}

const notion = entry({
  id: 'mcp:curated:makenotion-notion-mcp-server',
  name: 'Notion MCP',
  verified: true,
  label: 'official',
  match: 'exact',
  method: 'hosted_sign_in',
  publisher: 'Notion',
  icon: 'letter:N',
  signals: { stars: 1234, last_activity_at: now - 86400 },
  app: {
    id: 'notion',
    name: 'Notion',
    publisher: 'Notion Labs',
    category: 'productivity',
    icon: 'letter:N',
    verified: true,
    featured_rank: 4,
  },
});

function community(n: number, fields: Partial<IntegrationEntry> = {}) {
  return entry({
    id: `mcp:official:io.github.fan${n}/notion-${n}@1.0.0`,
    name: `notion-${n}`,
    publisher: `fan${n} on GitHub`,
    signals: { stars: 900 },
    ...fields,
  });
}

function page(
  items: IntegrationEntry[],
  fields: Partial<IntegrationEntryPage> = {},
): IntegrationEntryPage {
  const counts = { official: 0, reviewed: 0, built_in: 0, community: 0 };
  for (const item of items) counts[item.label ?? 'community'] += 1;
  return {
    schema_version: 1,
    revision,
    items,
    total: items.length,
    next_cursor: null,
    sources: [],
    hidden: 0,
    best_match_id: null,
    label_counts: counts,
    ...fields,
  };
}

function Where() {
  const location = useLocation();
  return (
    <output aria-label="Location">{location.pathname + location.search}</output>
  );
}

function Area() {
  const location = useLocation();
  const [, , leaf] = location.pathname.split('/');
  return (
    <AppsArea
      kind={leaf === 'skills' ? 'skill' : 'app'}
      item=""
      editor={() => null}
    />
  );
}

function show(
  path: string,
  catalog: (options: Record<string, string>) => IntegrationEntryPage,
) {
  const integrationItems = vi.fn(async (options: Record<string, string>) =>
    options.scope === 'installed' ? page([]) : catalog(options),
  );
  const controller = {
    integrationItems,
    integrationIcons: vi.fn(async () => ({ schema_version: 1, items: [] })),
    integrationApps: vi.fn(async () => ({ schema_version: 1, items: [] })),
  } as unknown as ClientController;
  render(
    <RuntimeContext.Provider
      value={{ controller, platform: {} as unknown as ClientPlatform }}
    >
      <WorkspaceActionsContext.Provider
        value={{ resetLayout: vi.fn(), newChat: vi.fn() }}
      >
        <MemoryRouter initialEntries={[path]}>
          <Routes>
            <Route
              path="settings/:setting/*"
              element={
                <>
                  <Area />
                  <Where />
                </>
              }
            />
          </Routes>
        </MemoryRouter>
      </WorkspaceActionsContext.Provider>
    </RuntimeContext.Provider>,
  );
  return integrationItems;
}

afterEach(() => {
  window.localStorage.clear();
  setPlace(null);
});

it('leads with the best match and four more, and folds the rest by who stands behind them', async () => {
  const reviewed = entry({
    id: 'skill:featured:notion-to-markdown',
    kind: 'skill',
    name: 'Notion to Markdown',
    label: 'reviewed',
    match: 'description',
    method: '',
    source: 'featured_skills',
    publisher: 'Row-Bot',
  });
  const described = community(9, { match: 'description' });
  const items = show('/settings/apps?q=notion', ({ all }) =>
    page(
      [
        notion,
        ...[1, 2, 3, 4, 5].map((n) => community(n)),
        reviewed,
        described,
      ],
      { best_match_id: notion.id, hidden: all ? 0 : 3 },
    ),
  );
  const best = await screen.findByRole('region', { name: 'Best match' });
  const hero = within(best).getByRole('link', { name: /^Notion/ });
  expect(hero).toHaveTextContent('Official');
  expect(hero).toHaveTextContent('by Notion Labs');
  expect(hero).toHaveTextContent('★ 1.2k · active');
  const also = screen.getByRole('region', { name: 'Also good' });
  const rows = within(also).getAllByRole('link');
  expect(rows.map((row) => row.textContent)).toEqual([
    expect.stringMatching(/notion-1Community · fan1 on GitHub/),
    expect.stringMatching(/notion-2/),
    expect.stringMatching(/notion-3/),
    expect.stringMatching(/notion-4/),
  ]);
  // Stars show on the best match only.
  expect(also).not.toHaveTextContent('★');
  const more = screen
    .getByText('More results (3)')
    .closest('details') as HTMLDetailsElement;
  expect(more.open).toBe(false);
  expect(more).toHaveTextContent('3 copies and placeholders hidden');
  expect(
    within(more).getByRole('heading', { name: /Reviewed by Row-Bot/ }),
  ).toBeTruthy();
  expect(within(more).getByRole('heading', { name: /Community/ })).toBeTruthy();
  expect(
    within(more)
      .getAllByRole('link')
      .map((link) => link.textContent),
  ).toEqual([
    expect.stringMatching(/Notion to MarkdownReviewed/),
    expect.stringMatching(/notion-5/),
    expect.stringMatching(/notion-9/),
  ]);
  fireEvent.click(
    within(more).getByRole('button', { name: 'Show all results' }),
  );
  await waitFor(() =>
    expect(items).toHaveBeenCalledWith(
      expect.objectContaining({
        scope: 'catalog',
        query: 'notion',
        all: 'true',
      }),
      expect.anything(),
    ),
  );
});

it('says where a local result runs when Row-Bot runs on a server (U7)', async () => {
  setPlace({ kind: 'hosted', host_name: 'PikaPods' });
  const files = entry({
    id: 'mcp:curated:filesystem',
    name: 'Files on this computer',
    label: 'reviewed',
    match: 'exact',
    publisher: 'Row-Bot',
  });
  show('/settings/apps?q=files', () =>
    page([files, community(1, { name: 'files-1' })], {
      best_match_id: files.id,
    }),
  );
  const best = await screen.findByRole('region', { name: 'Best match' });
  expect(best).toHaveTextContent('Files on your Row-Bot server');
  expect(best).toHaveTextContent('Runs on your Row-Bot server');
  expect(screen.getByRole('region', { name: 'Also good' })).toHaveTextContent(
    'Runs on your Row-Bot server',
  );
  expect(
    within(screen.getByRole('group', { name: 'Filters' })).getByRole('option', {
      name: 'On your Row-Bot server',
    }),
  ).toBeInTheDocument();
  expect(document.body).not.toHaveTextContent(/this computer/);
});

it('says so when only the community makes what was searched for, with no best match', async () => {
  show('/settings/apps?q=gizmo', () =>
    page([1, 2].map((n) => community(n, { name: `gizmo-${n}` }))),
  );
  expect(
    await screen.findByText(/No official or reviewed app matches “gizmo”/),
  ).toBeVisible();
  expect(screen.queryByRole('region', { name: 'Best match' })).toBeNull();
  const top = screen.getByRole('region', { name: 'Top results' });
  expect(within(top).getAllByRole('link')).toHaveLength(2);
  expect(screen.queryByText(/^More results/)).toBeNull();
});

it('filters on the server and remembers only "Official & reviewed only"', async () => {
  const items = show('/settings/apps?q=notion', () =>
    page([notion], { best_match_id: notion.id }),
  );
  const filters = await screen.findByRole('group', { name: 'Filters' });
  fireEvent.click(
    within(filters).getByRole('button', { name: 'Official & reviewed only' }),
  );
  await waitFor(() =>
    expect(items).toHaveBeenLastCalledWith(
      expect.objectContaining({
        labels: 'official,reviewed,built_in',
        kind: 'app',
      }),
      expect.anything(),
    ),
  );
  fireEvent.change(
    within(filters).getByRole('combobox', { name: 'Where it runs' }),
    {
      target: { value: 'hosted' },
    },
  );
  fireEvent.click(within(filters).getByRole('button', { name: 'No sign-in' }));
  fireEvent.click(within(filters).getByRole('button', { name: 'Skills' }));
  await waitFor(() =>
    expect(items).toHaveBeenLastCalledWith(
      expect.objectContaining({
        kind: 'skill',
        labels: 'official,reviewed,built_in',
        runs: 'hosted',
        no_sign_in: 'true',
      }),
      expect.anything(),
    ),
  );
  expect(window.localStorage.getItem('row-bot:apps:backed-only')).toBe('true');
});

it('keeps the filters in one sheet on a phone, with how many are on', async () => {
  window.localStorage.setItem('row-bot:apps:backed-only', 'true');
  const items = show('/settings/apps?q=notion', () =>
    page([notion], { best_match_id: notion.id }),
  );
  const open = await screen.findByRole('button', { name: 'Filters · 1' });
  await waitFor(() =>
    expect(items).toHaveBeenCalledWith(
      expect.objectContaining({ labels: 'official,reviewed,built_in' }),
      expect.anything(),
    ),
  );
  fireEvent.click(open);
  const sheet = await screen.findByRole('dialog', { name: 'Filters' });
  fireEvent.click(
    within(sheet).getByRole('switch', { name: 'No sign-in needed' }),
  );
  await waitFor(() =>
    expect(items).toHaveBeenLastCalledWith(
      expect.objectContaining({ no_sign_in: 'true' }),
      expect.anything(),
    ),
  );
  fireEvent.click(within(sheet).getByRole('button', { name: 'Show 1 result' }));
  expect(
    await screen.findByRole('button', { name: 'Filters · 2' }),
  ).toBeVisible();
});

it('browsing without a search shows no filters and keeps the page to its own kind', async () => {
  const items = show('/settings/apps', () => page([notion]));
  await screen.findAllByRole('link', { name: /^Notion/ });
  expect(screen.queryByRole('group', { name: 'Filters' })).toBeNull();
  expect(items).toHaveBeenCalledWith(
    { scope: 'catalog', kind: 'app', query: '' },
    expect.anything(),
  );
});
