import {
  accessibility,
  assertNoOverflow,
  expect,
  screenshot,
  test,
} from './evidence';

async function seed(page: import('@playwright/test').Page) {
  const token = process.env.ROW_BOT_BROWSER_CONTROL_TOKEN;
  const base = process.env.ROW_BOT_BROWSER_BASE_URL;
  if (!token || !base) throw new Error('Use the isolated Phase 4 runner');
  const headers = { 'X-Fixture-Token': token, Origin: new URL(base).origin };
  expect(
    (
      await page.request.post('/__p4_fixture/provider-credentials', { headers })
    ).ok(),
  ).toBe(true);
  expect(
    (
      await page.request.post('/__p4_fixture/catalog/populated', { headers })
    ).ok(),
  ).toBe(true);
}

test('Providers and Models share the contained shell and Models opens without provider refresh', async ({
  page,
}, info) => {
  await seed(page);
  const refreshes: string[] = [];
  page.on('request', (request) => {
    if (
      request.method() === 'POST' &&
      /\/settings\/(models\/refresh|providers\/live\/[^/]+\/refresh)$/.test(
        new URL(request.url()).pathname,
      )
    )
      refreshes.push(new URL(request.url()).pathname);
  });
  await page.goto('/app-v2/settings/providers');
  await expect(
    page.getByRole('heading', { name: 'Connection Status' }),
  ).toBeVisible();
  const providers = page.locator('.settings-shell');
  await expect(providers).toBeVisible();
  expect((await providers.boundingBox())!.width).toBeLessThan(1200);
  await page.goto('/app-v2/settings/models');
  const models = page.locator('[aria-label="Models settings"]');
  await expect(
    models.getByRole('combobox', { name: 'Default model' }),
  ).toBeVisible();
  await expect(
    models.getByRole('combobox', { name: 'Vision model' }),
  ).toBeVisible();
  await expect(
    models.getByRole('combobox', { name: 'Image model' }),
  ).toBeVisible();
  await expect(
    models.getByRole('combobox', { name: 'Video model' }),
  ).toBeVisible();
  await expect(
    models.getByRole('button', { name: 'Model Catalog' }),
  ).toHaveAttribute('aria-expanded', 'false');
  await expect(
    models.getByText(
      /Brain draft|Readiness not checked|Runtime not checked|installation state unknown/i,
    ),
  ).toHaveCount(0);
  expect(
    (await page.locator('.settings-shell').boundingBox())!.width,
  ).toBeLessThan(1200);
  expect(refreshes).toEqual([]);
  await assertNoOverflow(page);
  await screenshot(page, info, 'models-contained-defaults');
  await accessibility(page, info, 'models-contained-defaults');
});

test('Model catalog rows remain lazy and bounded', async ({ page }, info) => {
  await seed(page);
  await page.goto('/app-v2/settings/models');
  const models = page.locator('[aria-label="Models settings"]');
  await expect(
    models.getByRole('combobox', { name: 'Default model' }),
  ).toBeVisible();
  await expect(
    models.getByRole('listitem').filter({ hasText: 'Saved example 000' }),
  ).toHaveCount(0);
  await models.getByRole('button', { name: 'Model Catalog' }).click();
  await expect(models.getByRole('tab', { name: 'CHAT' })).toBeVisible();
  await expect(
    models.getByRole('heading', { name: 'Providers' }),
  ).toBeVisible();
  await expect(
    models.getByRole('listitem').filter({ hasText: 'Saved example 000' }),
  ).toHaveCount(0);
  await models
    .locator('.settings-model-provider-summaries')
    .scrollIntoViewIfNeeded();
  await screenshot(page, info, 'models-catalog-provider-summaries');
  await models
    .getByRole('button', { name: 'Open' })
    .filter({ hasText: 'Open' })
    .first()
    .click();
  await expect(models.getByText('Showing 80 of 105 models')).toBeVisible();
  await expect(models.locator('.settings-model-row-list > li')).toHaveCount(80);
  await models
    .locator('.settings-model-row-list > li')
    .first()
    .scrollIntoViewIfNeeded();
  await screenshot(page, info, 'models-catalog-provider-rows');
  await accessibility(page, info, 'models-catalog-provider-rows');
  await models.getByRole('button', { name: 'Show more models' }).click();
  await expect(models.locator('.settings-model-row-list > li')).toHaveCount(
    105,
  );
  await models.getByRole('tab', { name: 'VISION' }).click();
  await expect(
    models.getByRole('heading', { name: 'Providers' }),
  ).toBeVisible();
  await models.getByRole('tab', { name: 'IMAGE' }).click();
  await models.getByRole('tab', { name: 'VIDEO' }).click();
  await models.getByRole('tab', { name: 'VOICE' }).click();
  await assertNoOverflow(page);
});

test('Vision, media, context, and delegation controls write local settings', async ({
  page,
}) => {
  await seed(page);
  await page.goto('/app-v2/settings/models');
  const models = page.locator('[aria-label="Models settings"]');
  await expect(
    models.getByRole('combobox', { name: 'Default model' }),
  ).toBeVisible();
  const vision = models.locator('[aria-label="vision"]');
  await vision.getByRole('checkbox', { name: 'Enabled' }).uncheck();
  await expect(
    vision.getByRole('checkbox', { name: 'Enabled' }),
  ).not.toBeChecked();
  await vision.getByRole('button', { name: 'Refresh camera list' }).click();
  await expect(
    vision.getByText(/No cameras detected|camera\(s\) detected/),
  ).toBeVisible();
  const image = models.locator('[aria-label="image"]');
  await image.getByRole('checkbox', { name: 'Enabled' }).check();
  await expect(image.getByRole('checkbox', { name: 'Enabled' })).toBeChecked();
  const video = models.locator('[aria-label="video"]');
  await video.getByRole('checkbox', { name: 'Enabled' }).check();
  await expect(video.getByRole('checkbox', { name: 'Enabled' })).toBeChecked();
  await models
    .locator('summary')
    .filter({ hasText: 'Advanced context' })
    .click();
  const context = models.getByRole('combobox', {
    name: /model context|context cap/i,
  });
  await context.selectOption('32768');
  await expect(models.getByRole('status')).toContainText(
    'Context setting saved',
  );
  await context.selectOption('custom');
  await models
    .getByRole('spinbutton', { name: 'Custom context tokens' })
    .fill('60000');
  await models.getByRole('button', { name: 'Save cap' }).click();
  await expect(models.getByRole('status')).toContainText(
    'Context setting saved',
  );
  await models
    .getByRole('spinbutton', { name: 'Maximum work rounds' })
    .fill('91');
  await models.getByRole('button', { name: 'Save', exact: true }).click();
  await expect(models.getByRole('status')).toContainText('Agent limits saved');
  await models
    .getByRole('button', { name: 'Restore recommended defaults' })
    .click();
  await expect(
    models.getByRole('spinbutton', { name: 'Maximum work rounds' }),
  ).toHaveValue('90');
  await assertNoOverflow(page);
});

test('Catalog pin and default actions update the picker through reviewed commands', async ({
  page,
}, info) => {
  await seed(page);
  const index = info.project.name.includes('phone') ? '001' : '000';
  const label = `Saved example ${index}`;
  await page.goto('/app-v2/settings/models');
  const models = page.locator('[aria-label="Models settings"]');
  await expect(
    models.getByRole('combobox', { name: 'Default model' }),
  ).toBeVisible();
  await models.getByRole('button', { name: 'Model Catalog' }).click();
  await models.getByRole('button', { name: 'Open' }).first().click();
  const row = models
    .locator('.settings-model-row-list > li')
    .filter({ hasText: label });
  await expect(row).toBeVisible();
  await row.getByRole('button', { name: `Pin ${label} for chat` }).click();
  await expect(
    row.getByRole('button', { name: `Unpin ${label} for chat` }),
  ).toBeVisible();
  await row
    .getByRole('button', { name: `Set ${label} as chat default` })
    .click();
  await expect(
    models.getByRole('combobox', { name: 'Default model' }),
  ).toHaveValue(`model:openai:phase4-${index}`);
  await expect(row.getByText('default', { exact: true })).toBeVisible();
  await assertNoOverflow(page);
});

test('Catalog refresh starts only from its explicit Models action', async ({
  page,
}) => {
  await seed(page);
  const refreshes: string[] = [];
  page.on('request', (request) => {
    if (
      request.method() === 'POST' &&
      new URL(request.url()).pathname === '/api/v1/settings/models/refresh'
    )
      refreshes.push(request.url());
  });
  await page.goto('/app-v2/settings/models');
  const models = page.locator('[aria-label="Models settings"]');
  await expect(
    models.getByRole('combobox', { name: 'Default model' }),
  ).toBeVisible();
  expect(refreshes).toHaveLength(0);
  const requested = page.waitForRequest(
    (request) =>
      new URL(request.url()).pathname === '/api/v1/settings/models/refresh' &&
      request.method() === 'POST',
  );
  await models.getByRole('button', { name: 'Refresh catalog' }).click();
  await requested;
  expect(refreshes).toHaveLength(1);
  await expect(models.getByRole('status')).toContainText('Model catalog', {
    timeout: 30_000,
  });
});
