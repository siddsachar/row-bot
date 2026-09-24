import {
  accessibility,
  assertNoOverflow,
  expect,
  screenshot,
  test,
} from './evidence';
import {
  blockFixtureServiceWorkers,
  composer,
  newConversation,
} from './unified-helpers';

test.use({ serviceWorkers: 'allow' });
test.beforeEach(async ({ context, page }) => {
  await blockFixtureServiceWorkers(context);
  await page.addInitScript(() => {
    localStorage.setItem(
      'row-bot.appearance.v1',
      JSON.stringify({ version: 1, appearance: 'system' }),
    );
  });
});

const viewports = [
  { name: 'wide', width: 1440, height: 900 },
  { name: 'laptop', width: 1280, height: 720 },
  { name: 'tablet', width: 820, height: 1180 },
  { name: 'phone', width: 390, height: 844 },
  { name: 'small-phone', width: 360, height: 800 },
] as const;

test('conversation and Home keep their primary actions readable at every target size and theme', async ({
  page,
}, info) => {
  await newConversation(page);
  await expect(page.locator('.transcript .empty-state')).toBeVisible();
  const notice = page.getByRole('button', { name: 'Dismiss', exact: true });
  if (await notice.isVisible()) await notice.click();
  for (const viewport of viewports) {
    await page.setViewportSize(viewport);
    for (const theme of ['light', 'dark'] as const) {
      await page.emulateMedia({ colorScheme: theme, reducedMotion: 'reduce' });
      await expect(composer(page)).toBeVisible();
      await assertNoOverflow(page);
      await screenshot(page, info, `conversation-${viewport.name}-${theme}`);
    }
  }
  await page.setViewportSize(viewports[0]);
  await page.goto('/app-v2/');
  await expect(page.getByRole('tab', { name: 'Workflows' })).toBeVisible();
  for (const viewport of [viewports[0], viewports[3]]) {
    await page.setViewportSize(viewport);
    for (const theme of ['light', 'dark'] as const) {
      await page.emulateMedia({ colorScheme: theme, reducedMotion: 'reduce' });
      for (const tab of ['Workflows', 'Knowledge', 'Monitor', 'Insights']) {
        await page.getByRole('tab', { name: tab }).click();
        await page.locator('.home-view').evaluate((element) => {
          element.scrollTop = 0;
        });
        await assertNoOverflow(page);
        await screenshot(
          page,
          info,
          `home-${tab.toLowerCase()}-${viewport.name}-${theme}`,
        );
      }
    }
  }
});

test('workflow editor and Settings reflow at narrow and zoom-equivalent widths', async ({
  page,
}, info) => {
  const token = process.env.ROW_BOT_BROWSER_CONTROL_TOKEN;
  const base = process.env.ROW_BOT_BROWSER_BASE_URL;
  if (!token || !base) throw new Error('Use the isolated browser fixture');
  const seed = await page.request.post('/__p4_fixture/tasks/empty', {
    headers: { 'X-Fixture-Token': token, Origin: new URL(base).origin },
  });
  expect(seed.ok(), await seed.text()).toBe(true);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/app-v2/');
  await page.getByRole('button', { name: 'New workflow', exact: true }).click();
  const editor = page.getByRole('form', { name: 'Create task', exact: true });
  await expect(editor).toBeVisible();
  await expect(
    page.getByRole('heading', { name: 'New workflow' }),
  ).toBeVisible();
  await assertNoOverflow(page);
  await screenshot(page, info, 'workflow-editor-phone');
  await page.setViewportSize({ width: 720, height: 900 });
  await page
    .getByRole('heading', { name: 'New workflow' })
    .scrollIntoViewIfNeeded();
  await assertNoOverflow(page);
  await screenshot(page, info, 'workflow-editor-200-percent-reflow');
  await page.goto('/app-v2/settings/providers');
  await expect(
    page.getByRole('heading', { name: 'Providers' }).last(),
  ).toBeVisible();
  await assertNoOverflow(page);
  await screenshot(page, info, 'settings-providers-200-percent-reflow');
  await page.setViewportSize({ width: 1152, height: 900 });
  await page.emulateMedia({ forcedColors: 'active', reducedMotion: 'reduce' });
  await assertNoOverflow(page);
  await screenshot(
    page,
    info,
    'settings-providers-high-contrast-125-percent-reflow',
  );
});

test('all Settings leaves remain routed and reflow at desktop and phone sizes', async ({
  page,
}, info) => {
  test.setTimeout(180_000);
  const leaves = [
    'providers',
    'models',
    'knowledge',
    'buddy',
    'goals',
    'voice',
    'system',
    'tracker',
    'documents',
    'tools',
    'skills',
    'accounts',
    'channels',
    'utilities',
    'mcp',
    'plugins',
    'preferences',
  ];
  for (const viewport of [viewports[0], viewports[3]]) {
    await page.setViewportSize(viewport);
    for (const theme of ['light', 'dark'] as const) {
      await page.emulateMedia({ colorScheme: theme, reducedMotion: 'reduce' });
      for (const leaf of leaves) {
        await page.goto(`/app-v2/settings/${leaf}`);
        await expect(
          page
            .getByRole('heading', {
              name: leaf === 'mcp' ? 'MCP' : new RegExp(`^${leaf}$`, 'i'),
            })
            .last(),
        ).toBeVisible();
        await assertNoOverflow(page);
        await screenshot(
          page,
          info,
          `settings-${leaf}-${viewport.name}-${theme}`,
        );
      }
    }
  }
});

test('Setup Center remains readable and reachable across themes and viewports', async ({
  page,
}, info) => {
  for (const viewport of viewports) {
    await page.setViewportSize(viewport);
    for (const theme of ['light', 'dark'] as const) {
      await page.emulateMedia({ colorScheme: theme, reducedMotion: 'reduce' });
      await page.goto('/app-v2/setup');
      await expect(
        page.getByRole('heading', { name: 'Setup Center', exact: true }),
      ).toBeVisible();
      await assertNoOverflow(page);
      await accessibility(page, info, `setup-${viewport.name}-${theme}`);
      await screenshot(page, info, `setup-${viewport.name}-${theme}`);
    }
  }
});
