import {
  accessibility,
  assertNoOverflow,
  expect,
  screenshot,
  test,
} from './evidence';

test('Providers show live NiceGUI connection cards and row actions', async ({
  page,
}, info) => {
  const token = process.env.ROW_BOT_BROWSER_CONTROL_TOKEN;
  const base = process.env.ROW_BOT_BROWSER_BASE_URL;
  if (!token || !base) throw new Error('Use the isolated browser runner');
  const headers = { 'X-Fixture-Token': token, Origin: new URL(base).origin };
  expect(
    (
      await page.request.post('/__p4_fixture/provider-credentials', { headers })
    ).ok(),
  ).toBe(true);
  await page.goto('/app-v2/settings/providers');
  await expect(
    page.getByRole('heading', { name: 'Connection Status' }),
  ).toBeVisible();
  const row = page
    .getByRole('listitem')
    .filter({ hasText: 'OpenAI API' })
    .first();
  await expect(row).toContainText('Connected');
  await expect(row).toContainText('Saved in keyring');
  await expect(
    row.getByRole('button', { name: 'Manage OpenAI API API key' }),
  ).toBeVisible();
  await expect(
    row.getByRole('button', {
      name: 'Refresh OpenAI API provider status and catalog',
    }),
  ).toBeVisible();
  await expect(page.getByText(/connection readiness not checked/i)).toHaveCount(
    0,
  );
  await expect(
    page.getByRole('button', { name: 'Reload saved status' }),
  ).toHaveCount(0);
  await expect(page.getByText('Subscription checks')).toHaveCount(0);
  await assertNoOverflow(page);
  await screenshot(page, info, 'providers-live-parity');
  await accessibility(page, info, 'providers-live-parity');
});

test('Provider row refresh requests its own catalog and reports the outcome', async ({
  page,
}) => {
  await page.goto('/app-v2/settings/providers');
  const row = page
    .getByRole('listitem')
    .filter({ hasText: 'Ollama Local' })
    .first();
  const requested = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname ===
        '/api/v1/settings/providers/live/ollama/refresh' &&
      response.request().method() === 'POST',
  );
  await row
    .getByRole('button', {
      name: 'Refresh Ollama Local provider status and catalog',
    })
    .click();
  await expect(page.locator('.settings-provider-notice')).toContainText(
    'Refreshing Ollama Local...',
  );
  expect((await requested).ok()).toBe(true);
  await expect(page.locator('.settings-provider-notice')).not.toContainText(
    'Refreshing Ollama Local...',
    { timeout: 20_000 },
  );
});

test('Provider API key can be replaced through the compact row dialog', async ({
  page,
}) => {
  const token = process.env.ROW_BOT_BROWSER_CONTROL_TOKEN;
  const base = process.env.ROW_BOT_BROWSER_BASE_URL;
  if (!token || !base) throw new Error('Use the isolated browser runner');
  const headers = { 'X-Fixture-Token': token, Origin: new URL(base).origin };
  expect(
    (
      await page.request.post('/__p4_fixture/provider-credentials', { headers })
    ).ok(),
  ).toBe(true);
  await page.goto('/app-v2/settings/providers');
  const row = page
    .getByRole('listitem')
    .filter({ hasText: 'OpenAI API' })
    .first();
  await row.getByRole('button', { name: 'Manage OpenAI API API key' }).click();
  const dialog = page.getByRole('dialog', { name: 'Manage API key' });
  await expect(
    dialog.getByRole('heading', { name: 'OpenAI API API key' }),
  ).toBeVisible();
  await dialog.getByLabel('API key').fill('synthetic-browser-replacement');
  const applied = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname ===
        '/api/v1/settings/providers/commands' &&
      response.request().method() === 'POST',
  );
  await dialog.getByRole('button', { name: 'Replace key' }).click();
  expect((await applied).ok()).toBe(true);
  await expect(dialog).toHaveCount(0);
  await expect(
    row.getByRole('button', {
      name: 'Refresh OpenAI API provider status and catalog',
    }),
  ).toBeVisible();
});

test('A keyed custom endpoint opens its credential dialog after Save', async ({
  page,
}, info) => {
  const token = process.env.ROW_BOT_BROWSER_CONTROL_TOKEN;
  const base = process.env.ROW_BOT_BROWSER_BASE_URL;
  if (!token || !base) throw new Error('Use the isolated browser runner');
  const headers = { 'X-Fixture-Token': token, Origin: new URL(base).origin };
  expect(
    (
      await page.request.post('/__p4_fixture/provider-credentials', { headers })
    ).ok(),
  ).toBe(true);
  await page.goto('/app-v2/settings/providers');
  await page.getByRole('button', { name: 'Add custom endpoint' }).click();
  const endpoint = page.getByRole('dialog', { name: 'Add custom endpoint' });
  await endpoint
    .getByLabel('Endpoint id')
    .fill(`browser-keyed-${info.project.name}`);
  await endpoint.getByLabel('Display name').fill('Browser keyed endpoint');
  await endpoint.getByLabel('Base URL').fill('http://127.0.0.1:9/v1');
  await endpoint.getByLabel('API key required').check();
  const created = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname ===
        '/api/v1/settings/providers/commands' &&
      response.request().method() === 'POST',
  );
  await endpoint.getByRole('button', { name: 'Save' }).click();
  expect((await created).ok()).toBe(true);
  await expect(
    page.getByRole('dialog', { name: 'Custom endpoint API key' }),
  ).toBeVisible();
  await expect(
    page.getByRole('heading', { name: 'Browser keyed endpoint API key' }),
  ).toBeVisible();
});

test('ChatGPT sign-in runs from its row and Check login advances the flow', async ({
  page,
}) => {
  const token = process.env.ROW_BOT_BROWSER_CONTROL_TOKEN;
  const base = process.env.ROW_BOT_BROWSER_BASE_URL;
  if (!token || !base) throw new Error('Use the isolated browser runner');
  const headers = { 'X-Fixture-Token': token, Origin: new URL(base).origin };
  expect(
    (await page.request.post('/__p4_fixture/subscriptions', { headers })).ok(),
  ).toBe(true);
  await page.goto('/app-v2/settings/providers');
  const row = page
    .getByRole('listitem')
    .filter({ hasText: 'ChatGPT / Codex' })
    .first();
  await row.getByRole('button', { name: 'Connect ChatGPT / Codex' }).click();
  const dialog = page.getByRole('dialog', {
    name: 'Manage subscription account',
  });
  await dialog.getByRole('button', { name: 'Connect' }).click();
  await expect(dialog.getByLabel('Device code')).toHaveValue('SYNTHETIC');
  await expect(dialog.getByRole('button', { name: 'Check login' })).toHaveCount(
    1,
  );
  await dialog.getByRole('button', { name: 'Check login' }).click();
  await expect(dialog.getByText('Connected')).toBeVisible();
});

test('xAI OAuth client options save and reset from the provider row', async ({
  page,
}) => {
  const token = process.env.ROW_BOT_BROWSER_CONTROL_TOKEN;
  const base = process.env.ROW_BOT_BROWSER_BASE_URL;
  if (!token || !base) throw new Error('Use the isolated browser runner');
  const headers = { 'X-Fixture-Token': token, Origin: new URL(base).origin };
  expect(
    (
      await page.request.post('/__p4_fixture/subscription-options', { headers })
    ).ok(),
  ).toBe(true);
  await page.goto('/app-v2/settings/providers');
  const row = page
    .getByRole('listitem')
    .filter({ hasText: 'xAI Grok' })
    .first();
  await row
    .getByRole('button', { name: 'Configure xAI OAuth client ID' })
    .click();
  const dialog = page.getByRole('dialog', { name: 'Account options' });
  await dialog
    .getByLabel('OAuth client ID override')
    .fill('synthetic-browser-client');
  await dialog.getByRole('button', { name: 'Save override' }).click();
  await expect(dialog.getByText('OAuth client ID saved.')).toBeVisible();
  await dialog.getByRole('button', { name: 'Reset to default' }).click();
  await expect(
    dialog.getByText('Using the default OAuth client ID.'),
  ).toBeVisible();
});
