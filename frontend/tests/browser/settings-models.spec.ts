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
    page.getByRole('region', { name: 'Provider connections' }),
  ).toBeVisible();
  const providers = page.locator('.settings-shell');
  await expect(providers).toBeVisible();
  expect((await providers.boundingBox())!.width).toBeLessThan(1200);
  await page.goto('/app-v2/settings/models');
  const models = page.locator('[aria-label="Models settings"]');
  const brain = models.getByRole('button', { name: 'Brain model' });
  await expect(brain).toBeVisible();
  // The four jobs share one picker at one height and width (B227, B229).
  const brainBox = (await brain.boundingBox())!;
  for (const name of ['Vision model', 'Image model', 'Video model']) {
    const picker = models.getByRole('button', { name });
    await expect(picker).toBeVisible();
    const box = (await picker.boundingBox())!;
    expect(box.height).toBeCloseTo(brainBox.height, 0);
    expect(box.width).toBeCloseTo(brainBox.width, 0);
  }
  // One status line, no chip repeating the Brain model (B229).
  await expect(page.locator('.settings-pane-status')).toContainText(
    /providers? connected/,
  );
  await expect(page.locator('.settings-summary-chip')).toHaveCount(0);
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
    models.getByRole('button', { name: 'Brain model' }),
  ).toBeVisible();
  // The catalog shows its providers; rows wait for a provider or a search.
  const chips = models.getByRole('group', { name: 'Model category' });
  await expect(
    chips.getByRole('button', { name: 'Chat', exact: true }),
  ).toHaveAttribute('aria-pressed', 'true');
  await expect(
    models.getByRole('button', { name: /^Open / }).first(),
  ).toBeVisible();
  await expect(
    models.getByRole('listitem').filter({ hasText: 'Saved example 000' }),
  ).toHaveCount(0);
  await models
    .locator('.settings-model-provider-summaries')
    .scrollIntoViewIfNeeded();
  await screenshot(page, info, 'models-catalog-provider-summaries');
  await models
    .getByRole('button', { name: /^Open / })
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
  await chips.getByRole('button', { name: 'Vision', exact: true }).click();
  // The fixture saved chat models only: another job says it has none yet.
  await expect(models.getByText(/^No vision models saved yet/)).toBeVisible();
  for (const name of ['Image', 'Video', 'Voice'])
    await chips.getByRole('button', { name, exact: true }).click();
  await assertNoOverflow(page);
});

test('Vision, media, context, and delegation controls write local settings', async ({
  page,
}) => {
  await seed(page);
  await page.goto('/app-v2/settings/models');
  const models = page.locator('[aria-label="Models settings"]');
  await expect(
    models.getByRole('button', { name: 'Brain model' }),
  ).toBeVisible();
  const notices = page.locator('.toast');
  // A job that is off keeps its picker in view, but closed (B229).
  const vision = models.getByRole('switch', { name: 'Enable vision' });
  if (!(await vision.isChecked())) await vision.check();
  await expect(vision).toBeChecked();
  // Vision's searchable list starts with following the Brain (B227).
  const visionPicker = models.getByRole('button', { name: 'Vision model' });
  await visionPicker.click();
  const visionList = page.getByRole('dialog', {
    name: 'Choose the vision model',
  });
  await expect(visionList.getByRole('option').first()).toContainText(
    'Same as Brain',
  );
  await visionList.getByRole('option').first().click();
  await expect(visionList).toBeHidden();
  await expect(visionPicker).toContainText('Same as Brain');
  // The camera list loads when its select is opened (B229).
  const camera = models.getByRole('combobox', { name: 'Camera' });
  await camera.focus();
  await expect(camera.locator('option').first()).toHaveText(/^Camera \d+$/);
  // Camera sits under Vision only while Vision is on.
  await vision.uncheck();
  await expect(vision).not.toBeChecked();
  await expect(models.getByRole('combobox', { name: 'Camera' })).toHaveCount(0);
  await expect(visionPicker).toBeDisabled();
  await models.getByRole('switch', { name: 'Enable image' }).check();
  await expect(
    models.getByRole('switch', { name: 'Enable image' }),
  ).toBeChecked();
  await models.getByRole('switch', { name: 'Enable video' }).check();
  await expect(
    models.getByRole('switch', { name: 'Enable video' }),
  ).toBeChecked();
  await models
    .locator('summary')
    .filter({ hasText: 'Advanced context' })
    .click();
  // Automatic or a limit, in plain words (B229).
  await models.getByRole('radio', { name: 'Limit…' }).click();
  const limit = models.getByRole('spinbutton', { name: 'Limit in tokens' });
  await limit.fill('60000');
  // Enter saves the limit (decision 19); the notice offers Undo.
  await limit.press('Enter');
  const saved = notices.filter({ hasText: 'Reading limit: 60,000 tokens' });
  await expect(saved).toBeVisible();
  await expect(
    saved.getByRole('button', { name: 'Undo', exact: true }),
  ).toBeVisible();
  // Agent limits are advanced: open their disclosure first.
  await models
    .locator('summary')
    .filter({ hasText: 'Limits for long work' })
    .click();
  const rounds = models.getByRole('spinbutton', { name: 'Steps per run' });
  await rounds.fill('91');
  await rounds.press('Enter');
  await expect(
    notices.filter({ hasText: 'New runs use these limits' }),
  ).toBeVisible();
  await models
    .getByRole('button', { name: 'Restore recommended defaults' })
    .click();
  await expect(
    models.getByRole('spinbutton', { name: 'Steps per run' }),
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
    models.getByRole('button', { name: 'Brain model' }),
  ).toBeVisible();
  await models
    .getByRole('button', { name: /^Open / })
    .first()
    .click();
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
  // The default is a searchable picker (U12): its button names the model.
  await expect(
    models.getByRole('button', { name: 'Brain model' }),
  ).toContainText(label);
  await expect(row.getByText('Default', { exact: true })).toBeVisible();
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
    models.getByRole('button', { name: 'Brain model' }),
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
  await expect(
    page.locator('.toast').filter({ hasText: 'Model catalog' }),
  ).toBeVisible({ timeout: 30_000 });
});
