import {
  accessibility,
  assertNoOverflow,
  expect,
  screenshot,
  test,
} from './evidence';
import { blockFixtureServiceWorkers } from './unified-helpers';
import type { Page } from '@playwright/test';

function headers() {
  return {
    'X-Fixture-Token': process.env.ROW_BOT_BROWSER_CONTROL_TOKEN!,
    Origin: new URL(process.env.ROW_BOT_BROWSER_BASE_URL!).origin,
  };
}
async function confirm(page: Page) {
  await page
    .getByRole('dialog')
    .getByRole('button', { name: 'Confirm', exact: true })
    .click();
}

test('portable package installs off, manages its children and removes only owned data', async ({
  page,
  context,
}, info) => {
  await blockFixtureServiceWorkers(context);
  await page.goto('/app-v2/settings/integrations?tab=discover');
  await page
    .getByRole('button', { name: 'Local text tools', exact: true })
    .click();
  await page
    .getByRole('button', { name: 'Inspect integration', exact: true })
    .click();
  await expect(
    page.getByText('Skill: text-review', { exact: true }),
  ).toBeVisible();
  await page.getByRole('button', { name: 'Add package', exact: true }).click();
  await confirm(page);
  await expect(page).toHaveURL(/selected=plugin%3A/);
  await expect(
    page.getByRole('heading', { name: 'Included with local-text-tools' }),
  ).toBeVisible();
  await page
    .getByRole('button', { name: 'Run local test', exact: true })
    .click();
  const enabled = page.getByRole('switch', {
    name: 'Plugin enabled',
    exact: true,
  });
  await expect(enabled).toBeEnabled();
  await expect(enabled).not.toBeChecked();
  await enabled.click();
  await expect(enabled).toBeChecked();
  await expect(
    page.getByText('Setup needed · Row-Bot', { exact: true }),
  ).toBeVisible();
  await enabled.click();
  await expect(enabled).not.toBeChecked();
  await expect(page.getByText('Off · Row-Bot', { exact: true })).toBeVisible();
  await page.reload();
  await expect(
    page.getByRole('heading', { name: 'Included with local-text-tools' }),
  ).toBeVisible();
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    await assertNoOverflow(page);
    await screenshot(page, info, `integrations-package-${appearance}`);
    await accessibility(page, info, `integrations-package-${appearance}`);
  }
  await page
    .getByRole('button', { name: 'Remove package', exact: true })
    .click();
  await confirm(page);
  await expect(
    page.getByRole('button', { name: 'Delete saved data', exact: true }),
  ).toBeEnabled();
  await page.reload();
  await expect(
    page.getByText('Saved data retained · portable', { exact: true }),
  ).toBeVisible();
  await page
    .getByRole('button', { name: 'Delete saved data', exact: true })
    .click();
  await confirm(page);
  await expect(page).not.toHaveURL(/selected=/);
  await expect(
    page.getByRole('button', { name: 'local-text-tools', exact: true }),
  ).toHaveCount(0);
});

test('MCP setup tests once, accepts its tools and connects through existing authority', async ({
  page,
  context,
}, info) => {
  await blockFixtureServiceWorkers(context);
  expect(
    (
      await page.request.post('/__p4_fixture/mcp-catalog', {
        headers: headers(),
      })
    ).ok(),
  ).toBe(true);
  await page.goto('/app-v2/settings/integrations?type=mcp');
  await page
    .getByRole('button', { name: 'Synthetic lifecycle', exact: true })
    .click();
  await page
    .getByRole('button', { name: 'Test connection', exact: true })
    .click();
  await confirm(page);
  const catalog = page.getByRole('region', {
    name: 'Accept tested MCP tools',
    exact: true,
  });
  await expect(
    catalog.getByText('3 tools found.', { exact: true }),
  ).toBeVisible();
  await page.reload();
  await catalog
    .getByRole('button', { name: 'Accept tools', exact: true })
    .click();
  await expect(catalog.getByText(/Tools accepted/)).toBeVisible();
  expect(
    (
      await (
        await page.request.get('/__p4_fixture/mcp-catalog', {
          headers: headers(),
        })
      ).json()
    ).calls,
  ).toEqual(['connect', 'list_tools']);
  await page
    .getByRole('button', { name: 'Turn on and connect', exact: true })
    .click();
  await confirm(page);
  await expect(page.getByText('Connected.', { exact: true })).toBeVisible();
  await expect(page.getByText('Ready · custom', { exact: true })).toBeVisible();
  await assertNoOverflow(page);
  await screenshot(page, info, 'integrations-mcp-connected');
  await page
    .getByRole('button', { name: 'Remove connection', exact: true })
    .click();
  await confirm(page);
  await expect(
    page.getByRole('button', { name: 'Synthetic lifecycle', exact: true }),
  ).toHaveCount(0);
});

test('public skill imports its resources then remains manageable after reload', async ({
  page,
  context,
}, info) => {
  await blockFixtureServiceWorkers(context);
  expect(
    (
      await page.request.post('/__p4_fixture/integration-skills', {
        headers: headers(),
      })
    ).ok(),
  ).toBe(true);
  await page.goto(
    '/app-v2/settings/integrations?tab=discover&type=skill&source=clawhub',
  );
  await page
    .getByRole('button', { name: 'Search public source', exact: true })
    .click();
  await page
    .getByRole('button', { name: 'Browser writing', exact: true })
    .click();
  await page
    .getByRole('button', { name: 'Inspect integration', exact: true })
    .click();
  await page.getByText('2 included files and checks', { exact: true }).click();
  await expect(
    page.getByText('references/checklist.txt', { exact: true }),
  ).toBeVisible();
  await page.getByRole('button', { name: 'Add skill', exact: true }).click();
  await confirm(page);
  await expect(page).toHaveURL(/tab=my.*selected=skill%3Abrowser_writing/);
  await page.reload();
  await expect(
    page.getByRole('switch', { name: 'Available in chats', exact: true }),
  ).not.toBeChecked();
  await expect(
    page.getByRole('button', { name: 'Pin for new work', exact: true }),
  ).toBeVisible();
  await page
    .getByRole('switch', { name: 'Available in chats', exact: true })
    .click();
  await expect(
    page.getByText('Ready · clawhub', { exact: true }),
  ).toBeVisible();
  await assertNoOverflow(page);
  await screenshot(page, info, 'integrations-skill-installed');
});
