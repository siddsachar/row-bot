import type { Page } from '@playwright/test';
import {
  accessibility,
  assertNoOverflow,
  expect,
  screenshot,
  test,
} from './evidence';

async function fixture(page: Page, name: string) {
  const token = process.env.ROW_BOT_BROWSER_CONTROL_TOKEN;
  const base = process.env.ROW_BOT_BROWSER_BASE_URL;
  if (!token || !base) throw new Error('Use the isolated browser runner');
  const headers = { 'X-Fixture-Token': token, Origin: new URL(base).origin };
  expect(
    (await page.request.post(`/__p4_fixture/${name}`, { headers })).ok(),
  ).toBe(true);
}

/** A provider's sheet: from its tile when connected, else from Add a provider. */
async function openProvider(page: Page, name: string) {
  const connected = page.getByRole('region', { name: 'Connected' });
  await expect(connected.getByRole('list').first()).toBeVisible();
  const tile = connected.locator('.settings-provider-tile', { hasText: name });
  if (await tile.count()) await tile.first().click();
  else {
    await connected.getByRole('button', { name: 'Add a provider' }).click();
    const gallery = page.getByRole('dialog', { name: 'Add a provider' });
    for (const tab of await gallery.getByRole('tab').all()) {
      await tab.click();
      const option = gallery.getByRole('button', { name: new RegExp(name) });
      if (await option.count()) {
        await option.first().click();
        break;
      }
    }
  }
  const sheet = page.getByRole('dialog', { name });
  await expect(sheet).toBeVisible();
  return sheet;
}

test('Providers live on Models: a tile per connection, each with its sheet', async ({
  page,
}, info) => {
  await fixture(page, 'provider-credentials');
  // The old page's links land on Models' connections.
  await page.goto('/app-v2/settings/providers');
  await expect(page).toHaveURL(/\/settings\/models#connections$/);
  const connected = page.getByRole('region', { name: 'Connected' });
  const tile = connected.locator('.settings-provider-tile', {
    hasText: 'OpenAI API',
  });
  await expect(tile).toContainText('Connected');
  await expect(page.getByText(/connection readiness not checked/i)).toHaveCount(
    0,
  );
  await expect(page.getByText('Subscription checks')).toHaveCount(0);
  await assertNoOverflow(page);
  await screenshot(page, info, 'providers-connected');
  await accessibility(page, info, 'providers-connected');
  await tile.click();
  const sheet = page.getByRole('dialog', { name: 'OpenAI API' });
  await expect(sheet.locator('summary', { hasText: 'API key' })).toBeVisible();
  await expect(
    sheet.getByRole('button', { name: 'Refresh OpenAI API model list' }),
  ).toBeVisible();
  await expect(sheet.getByRole('search')).toBeVisible();
  await screenshot(page, info, 'providers-sheet');
  await accessibility(page, info, 'providers-sheet');
});

test('A provider sheet refreshes its own catalog and reports the outcome', async ({
  page,
}) => {
  await page.goto('/app-v2/settings/models');
  const sheet = await openProvider(page, 'Ollama Local');
  const requested = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname ===
        '/api/v1/settings/providers/live/ollama/refresh' &&
      response.request().method() === 'POST',
  );
  await sheet
    .getByRole('button', { name: 'Refresh Ollama Local model list' })
    .click();
  await expect(sheet.locator('.settings-provider-notice')).toContainText(
    'Refreshing Ollama Local...',
  );
  expect((await requested).ok()).toBe(true);
  await expect(sheet.locator('.settings-provider-notice')).not.toContainText(
    'Refreshing Ollama Local...',
    { timeout: 20_000 },
  );
});

test('A provider’s API key is replaced in its sheet', async ({ page }) => {
  await fixture(page, 'provider-credentials');
  await page.goto('/app-v2/settings/models');
  const sheet = await openProvider(page, 'OpenAI API');
  await sheet.locator('summary', { hasText: 'API key' }).click();
  await expect(
    sheet.getByRole('heading', { name: 'OpenAI API key' }),
  ).toBeVisible();
  await sheet.getByLabel('API key').fill('synthetic-browser-replacement');
  const applied = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname ===
        '/api/v1/settings/providers/commands' &&
      response.request().method() === 'POST',
  );
  await sheet.getByRole('button', { name: 'Replace key' }).click();
  expect((await applied).ok()).toBe(true);
  // Saved: the sheet stays, re-reading the provider's models to star.
  await expect(sheet.locator('.settings-provider-notice')).toContainText(
    /OpenAI API/,
  );
});

test('A keyed custom endpoint opens its credential dialog after Save', async ({
  page,
}, info) => {
  await fixture(page, 'provider-credentials');
  await page.goto('/app-v2/settings/models');
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

test('Your own server is added from Add a provider, or from an old link', async ({
  page,
}) => {
  await page.goto('/app-v2/settings/models');
  await page
    .getByRole('region', { name: 'Connected' })
    .getByRole('button', { name: 'Add a provider' })
    .click();
  const gallery = page.getByRole('dialog', { name: 'Add a provider' });
  await gallery.getByRole('tab', { name: 'Your server' }).click();
  await gallery
    .getByRole('button', { name: /OpenAI-compatible server/ })
    .click();
  await expect(
    page.getByRole('dialog', { name: 'Add custom endpoint' }),
  ).toBeVisible();
  // First run and other pages link to the old page with ?add=custom-endpoint.
  await page.goto('/app-v2/settings/providers?add=custom-endpoint');
  await expect(page).toHaveURL(/\/settings\/models\?add=custom-endpoint/);
  await expect(
    page.getByRole('dialog', { name: 'Add custom endpoint' }),
  ).toBeVisible();
});

test('ChatGPT sign-in runs from its sheet and connects by itself, with nothing to press', async ({
  page,
}) => {
  await fixture(page, 'subscriptions');
  await page.goto('/app-v2/settings/models');
  const sheet = await openProvider(page, 'ChatGPT / Codex');
  await sheet.getByRole('button', { name: 'Connect ChatGPT / Codex' }).click();
  const dialog = page.getByRole('dialog', {
    name: 'Manage subscription account',
  });
  await dialog.getByRole('button', { name: 'Connect' }).click();
  // The code is shown large with a Copy button; the sign-in is checked by
  // itself, so there is no Check now and one status line at a time (U4, U7).
  await expect(dialog.getByText('SYNTHETIC', { exact: true })).toBeVisible();
  await expect(
    dialog.getByRole('button', { name: 'Copy device code' }),
  ).toBeVisible();
  await expect(dialog.getByRole('button', { name: 'Check now' })).toHaveCount(
    0,
  );
  await expect(dialog.getByText('Not connected')).toHaveCount(0);
  await expect(dialog.getByText('Connected')).toBeVisible({ timeout: 15_000 });
});

test('xAI OAuth client options save and reset from the provider sheet', async ({
  page,
}) => {
  await fixture(page, 'subscription-options');
  await page.goto('/app-v2/settings/models');
  const sheet = await openProvider(page, 'xAI Grok');
  await sheet.getByRole('button', { name: 'Set up xAI sign-in' }).click();
  const dialog = page.getByRole('dialog', { name: 'Account options' });
  const override = dialog.getByLabel('OAuth client ID override');
  await override.fill('synthetic-browser-client');
  // Saves on Enter or when the field is left (decision 19).
  await override.press('Enter');
  await expect(dialog.getByText('OAuth client ID saved.')).toBeVisible();
  await dialog.getByRole('button', { name: 'Reset to default' }).click();
  await expect(
    dialog.getByText('Using the default OAuth client ID.'),
  ).toBeVisible();
});
