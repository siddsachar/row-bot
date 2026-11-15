import type { BrowserContext, Page, Route, TestInfo } from '@playwright/test';
import {
  accessibility,
  assertNoOverflow,
  expect,
  screenshot,
  test,
} from './evidence';
import { blockFixtureServiceWorkers, dismissNotices } from './unified-helpers';

// N2: Home › Overview is one calm column. Each state is staged by answering
// Home's reads here (what needs you, approvals, workflows, checks, memory,
// setup and the conversation list); nothing else is changed or sent.

const at = (minutes: number) =>
  new Date(Date.now() + minutes * 60_000).toISOString();

type Staged = {
  conversations?: object[];
  approvals?: object[];
  problems?: object[];
  tasks?: object[];
  checks?: object[];
  saved?: number;
  needsModel?: boolean;
  setupReminder?: boolean;
  unreadable?: boolean;
};

const unavailable = (route: Route) =>
  route.fulfill({
    status: 503,
    contentType: 'application/json',
    body: JSON.stringify({
      type: 'about:blank',
      title: 'Unavailable',
      status: 503,
      code: 'dependency_unavailable',
      request_id: 'home-overview',
    }),
  });

/** A read's own answer as an object, so a state changes only its fields. */
type Answer = Record<string, unknown> & {
  steps: { id: string }[];
  completed_steps: string[];
  skipped_steps: string[];
};

async function patched(route: Route, change: (body: Answer) => object) {
  const response = await route.fetch();
  await route.fulfill({ response, json: change(await response.json()) });
}

async function stage(context: BrowserContext, page: Page, staged: Staged) {
  // Home's reads come from the page, never a cached worker answer.
  await blockFixtureServiceWorkers(context);
  const get = (handler: (route: Route) => Promise<void>) => (route: Route) =>
    route.request().method() === 'GET' ? handler(route) : route.fallback();
  await page.route(
    '**/api/v1/monitor/attention',
    get((route) =>
      route.fulfill({
        json: {
          schema_version: 1,
          problems: staged.problems ?? [],
          update: null,
        },
      }),
    ),
  );
  await page.route(
    '**/api/v1/monitor/approvals',
    get((route) =>
      route.fulfill({
        json: {
          schema_version: 1,
          items: staged.approvals ?? [],
          total: staged.approvals?.length ?? 0,
        },
      }),
    ),
  );
  await page.route(
    /\/api\/v1\/tasks(\?.*)?$/,
    get((route) =>
      staged.unreadable
        ? unavailable(route)
        : patched(route, (body) => ({
            ...body,
            items: staged.tasks ?? [],
            total: staged.tasks?.length ?? 0,
            next_cursor: null,
          })),
    ),
  );
  await page.route(
    '**/api/v1/monitor/diagnosis',
    get((route) =>
      staged.unreadable
        ? unavailable(route)
        : route.fulfill({
            json: {
              schema_version: 1,
              hourly_network_checks: true,
              checks: staged.checks ?? [],
            },
          }),
    ),
  );
  await page.route(
    /\/api\/v1\/monitor$/,
    get((route) =>
      patched(route, (body) => ({
        ...body,
        extraction_journal: staged.saved
          ? [
              {
                timestamp: at(-26 * 60),
                summary: '',
                contradictions_blocked: 0,
                low_confidence_skipped: 0,
                islands_repaired: 0,
                threads: [
                  {
                    label: 'Autumn menu ideas',
                    extracted: 8,
                    saved: staged.saved,
                  },
                ],
                errors: [],
              },
            ]
          : [],
        extraction_journal_availability: 'available',
      })),
    ),
  );
  await page.route(
    '**/api/v1/setup/onboarding',
    get((route) =>
      patched(route, (body) => ({
        ...body,
        needs_model: Boolean(staged.needsModel),
        dismissed_home_card: !staged.setupReminder,
        completed_steps: staged.setupReminder
          ? body.steps.slice(0, 3).map((step) => step.id)
          : body.completed_steps,
        skipped_steps: staged.setupReminder ? [] : body.skipped_steps,
      })),
    ),
  );
  const conversations = staged.conversations ?? [];
  await page.route(
    /\/api\/v1\/conversations(\?.*)?$/,
    get((route) =>
      route.fulfill({
        json: { items: conversations, has_more: false, next_cursor: null },
      }),
    ),
  );
  await page.route(
    /\/api\/v1\/conversations\/c-[a-z]+$/,
    get((route) =>
      route.fulfill({
        json: conversations.find((row) =>
          route
            .request()
            .url()
            .endsWith(`/${(row as { id: string }).id}`),
        ),
      }),
    ),
  );
  // Home waits for a chosen model before it opens, as "Set up later" lets it.
  await page.addInitScript(() =>
    sessionStorage.setItem('row-bot:setup-later:v1', '1'),
  );
}

const chat = (id: string, title: string, minutes: number, extra = {}) => ({
  id,
  revision: '3',
  title,
  pinned: false,
  updated_at: at(-minutes),
  ...extra,
});

const busy: Staged = {
  conversations: [
    chat('c-research', 'Research sprint: oat suppliers', 4, {
      activity_state: 'active',
    }),
    chat('c-menu', 'Autumn menu ideas', 130, { category: 'designer' }),
    chat('c-supplier', 'Supplier price check', 60),
    chat('c-rota', 'Shop rota', 26 * 60),
  ],
  approvals: [
    {
      id: 'approval-mail',
      source: 'conversation',
      title: 'Send an email to Riverside Flour',
      what: 'Reorder for next week: 40 kg rye',
      requested_at: at(-12),
      expires_at: null,
      conversation_id: 'c-supplier',
      task_id: null,
    },
  ],
  problems: [
    {
      id: 'workflow:t-stock',
      title: 'Stock count failed',
      detail: 'Run it again, or open it to see why.',
      place: 'workflows',
      fix: {
        kind: 'open',
        href: '/?tab=workflows&workflow=t-stock',
        target: null,
        name: 'Stock count',
      },
    },
    {
      id: 'app:mcp:notion',
      title: 'Sign in to Notion',
      detail: 'The saved sign-in no longer works. Sign in again.',
      place: 'mcp',
      fix: {
        kind: 'open',
        href: '/settings/apps/item?id=mcp:notion&fix=1',
        target: null,
        name: 'Notion',
      },
    },
  ],
  tasks: [
    {
      id: 't-report',
      name: 'Morning shop report',
      description: '',
      icon: '',
      enabled: true,
      notify_only: false,
      step_count: 3,
      schedule: 'daily:07:00',
      at: null,
      last_run: at(-7 * 60),
      last_status: 'completed',
      conversation_id: null,
      agent_profile_id: 'builtin:row_bot_default',
      approval_mode: 'block',
      next_run: at(17 * 60),
    },
  ],
  checks: [
    {
      id: 'documents',
      name: 'Documents',
      status: 'warn',
      detail: 'Rebuild recommended',
      checked_at: Date.now() / 1000 - 600,
      settings_tab: 'Documents',
      network: false,
      stale: false,
      fix: {
        kind: 'open',
        href: '/settings/documents#embedding',
        target: null,
        name: 'Documents',
      },
    },
    // Kept before WhatsApp was turned off: not current, so not shown (B384).
    {
      id: 'channel:whatsapp',
      name: 'WhatsApp',
      status: 'warn',
      detail: 'Stopped',
      checked_at: Date.now() / 1000 - 3 * 86_400,
      settings_tab: 'Channels',
      network: false,
      stale: true,
      fix: null,
    },
  ],
  saved: 12,
  setupReminder: true,
};

const phone = (info: TestInfo) =>
  (info.project.use.viewport?.width ?? 1440) < 700;

/**
 * Light and dark, then an accessibility scan of what shows. Home scrolls in
 * its own pane, so the window grows to show the whole column, without the
 * passing new-approval notice over it.
 */
async function shoot(page: Page, info: TestInfo, name: string) {
  const suffix = phone(info) ? '-phone' : '';
  await dismissNotices(page);
  const viewport = page.viewportSize()!;
  const column = await page
    .locator('.overview-home')
    .evaluate((home) => home.getBoundingClientRect().bottom);
  await page.setViewportSize({
    width: viewport.width,
    height: Math.max(viewport.height, Math.ceil(column) + 120),
  });
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    // The whole page has taken the appearance before it is pictured.
    await expect
      .poll(() =>
        page.evaluate(() => {
          const [r, g, b] = getComputedStyle(document.body)
            .backgroundColor.match(/\d+/g)!
            .map(Number);
          return r + g + b < 384 ? 'dark' : 'light';
        }),
      )
      .toBe(appearance);
    await page.evaluate(
      () =>
        new Promise((done) =>
          requestAnimationFrame(() => requestAnimationFrame(done)),
        ),
    );
    await assertNoOverflow(page);
    await screenshot(page, info, `${name}${suffix}-${appearance}`);
  }
  await accessibility(page, info, `${name}${suffix}`);
  await page.emulateMedia({ colorScheme: 'light' });
}

/** Home's column (named by its greeting) and its one-line summary. */
async function openHome(page: Page) {
  await page.goto('/app-v2/');
  const home = page.getByRole('region', {
    name: /^Good (morning|afternoon|evening)$/,
  });
  await expect(home).toBeVisible();
  const summary = home
    .getByRole('heading', { level: 2 })
    .locator('xpath=following-sibling::p');
  return { home, summary };
}

test('first run: only "Choose a model" under the greeting', async ({
  context,
  page,
}, info) => {
  await stage(context, page, { needsModel: true });
  const { home } = await openHome(page);
  await expect(
    home.getByRole('link', { name: 'Choose a model' }),
  ).toBeVisible();
  await expect(home.getByRole('textbox', { name: 'Ask Row-Bot' })).toHaveCount(
    0,
  );
  await expect(home.getByRole('list')).toHaveCount(0);
  await shoot(page, info, 'first-run');
});

test('empty profile: the greeting, "All quiet", the Ask box and quick starts', async ({
  context,
  page,
}, info) => {
  await stage(context, page, {});
  const { home, summary } = await openHome(page);
  await expect(summary).toHaveText('All quiet.');
  await expect(
    home.getByRole('textbox', { name: 'Ask Row-Bot' }),
  ).toBeVisible();
  await expect(
    home
      .getByRole('group', { name: 'Quick starts' })
      .getByRole('button', { name: 'New workflow' }),
  ).toBeVisible();
  await expect(home.getByRole('list')).toHaveCount(0);
  await shoot(page, info, 'empty');
});

test('busy profile: Needs you under the Ask box, then Recent and Today', async ({
  context,
  page,
}, info) => {
  await stage(context, page, busy);
  const { home, summary } = await openHome(page);
  await expect(summary).toHaveText('3 things need you.');
  const needs = home.getByRole('list', { name: 'Needs you' });
  await expect(
    needs.getByText('Send an email to Riverside Flour'),
  ).toBeVisible();
  await expect(needs.getByText('Stock count failed')).toBeVisible();
  await expect(
    home.getByRole('region', { name: 'More you can set up' }),
  ).toBeVisible();
  // The sidebar (shown beside Home on a desktop) counts the same three.
  if (!phone(info))
    await expect(
      page.getByRole('button', { name: '3 things need you. Review' }),
    ).toBeVisible();
  const today = home.getByRole('list', { name: 'Today' });
  await expect(
    today.getByText('Morning shop report runs in 17 hours'),
  ).toBeVisible();
  await expect(today.getByText('Documents: Rebuild recommended')).toBeVisible();
  await expect(today.getByText(/WhatsApp/)).toHaveCount(0);
  await expect(
    home.getByRole('list', { name: 'Recent' }).getByRole('button'),
  ).toHaveCount(3);
  await shoot(page, info, 'busy');
});

test('all quiet: recent chats and what was learned, nothing waiting', async ({
  context,
  page,
}, info) => {
  await stage(context, page, {
    conversations: busy.conversations!.slice(1),
    saved: 12,
  });
  const { home, summary } = await openHome(page);
  await expect(summary).toHaveText('All quiet.');
  await expect(home.getByRole('list', { name: 'Needs you' })).toHaveCount(0);
  await expect(
    home
      .getByRole('list', { name: 'Today' })
      .getByText('12 memories saved this week'),
  ).toBeVisible();
  await shoot(page, info, 'all-quiet');
});

test('error: an unreadable source is one quiet line', async ({
  context,
  page,
}, info) => {
  for (const path of ['tasks', 'monitor/diagnosis'])
    test.info().annotations.push({
      type: 'expected-console-error',
      description: JSON.stringify({
        owner: 'home-overview',
        fixture: `${path} unavailable`,
        signature:
          'Failed to load resource: the server responded with a status of 503 (Service Unavailable)',
        count: 4,
        upTo: true,
      }),
    });
  await stage(context, page, {
    conversations: busy.conversations!.slice(1),
    unreadable: true,
  });
  const { home } = await openHome(page);
  await expect(
    home.getByRole('region', { name: 'Today' }).getByRole('status'),
  ).toHaveText(
    'Couldn’t read workflows and health checks just now. Their tabs may say more.',
  );
  await shoot(page, info, 'error');
});
