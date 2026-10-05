import type { Page } from '@playwright/test';
import {
  accessibility,
  assertNoOverflow,
  expect,
  screenshot,
  test,
} from './evidence';
import { clickNewChat } from './unified-helpers';

// Phase 10 (decisions 9 and 10): until a default model exists, Row-Bot opens
// one question, "How should Row-Bot think?". The fixture makes the profile
// fresh (no model, setup not finished) and fakes everything Setup touches:
// the local runtime, the one-message test, key checks and provider catalogs.
// Nothing reaches a runtime, a provider or the network.

async function fixture(page: Page, path: string) {
  const token = process.env.ROW_BOT_BROWSER_CONTROL_TOKEN;
  const base = process.env.ROW_BOT_BROWSER_BASE_URL;
  if (!token || !base) throw new Error('Use the isolated Phase 4 runner');
  const response = await page.request.post(path, {
    headers: { 'X-Fixture-Token': token, Origin: new URL(base).origin },
  });
  expect(response.ok(), await response.text()).toBe(true);
}

async function openFresh(page: Page, runtime = 'not_installed') {
  await fixture(page, `/__p10_fixture/first-run/fresh?runtime=${runtime}`);
  await page.goto('/app-v2/');
  await expect(page).toHaveURL(/\/app-v2\/setup$/);
  await expect(
    page.getByRole('heading', { name: 'How should Row-Bot think?' }),
  ).toBeVisible();
}

async function expectHome(page: Page) {
  await expect(
    page.getByRole('tablist', { name: 'Home capabilities' }),
  ).toBeVisible({ timeout: 15_000 });
  await expect(page).toHaveURL(/\/app-v2\/?$/);
}

test.afterEach(async ({ page }) => {
  await fixture(page, '/__p10_fixture/first-run/restore');
});

test('no runtime yet: Setup opens, Ollama appears by itself, the pick becomes the default', async ({
  page,
  isMobile,
}, testInfo) => {
  await openFresh(page);
  await screenshot(page, testInfo, 'first-screen');
  await accessibility(page, testInfo, 'first-screen-axe');
  await assertNoOverflow(page);
  const choices = page.getByRole('group', {
    name: 'How should Row-Bot think?',
  });
  await expect(choices).toContainText("Ollama isn't installed yet");
  await choices.getByRole('button', { name: /On this computer/ }).click();
  await expect(
    page.getByRole('heading', { name: 'Install Ollama' }),
  ).toBeVisible();
  // The steps follow the server's system (the fixture server runs where the
  // browser does): a command to copy on Linux, a download elsewhere.
  if (process.platform === 'linux')
    await expect(
      page.getByRole('button', {
        name: 'Copy curl -fsSL https://ollama.com/install.sh | sh',
      }),
    ).toBeVisible();
  else
    await expect(
      page.getByRole('link', { name: 'Download Ollama' }),
    ).toHaveAttribute('href', 'https://ollama.com/download');
  await screenshot(page, testInfo, 'not-installed');
  await assertNoOverflow(page);

  // Ollama is installed and started outside Row-Bot: Setup notices by itself.
  const origin = await page.evaluate(() => performance.timeOrigin);
  await fixture(
    page,
    '/__p10_fixture/first-run/runtime?runtime=running&models=fixture-local:8b,fixture-local:27b',
  );
  const models = page.getByRole('list', { name: 'Models on this computer' });
  await expect(models.getByRole('button')).toHaveCount(2, { timeout: 10_000 });
  expect(await page.evaluate(() => performance.timeOrigin)).toBe(origin);
  await expect(choices).toContainText('Ollama is running · 2 models');
  await screenshot(page, testInfo, 'detected');
  await models.getByRole('button', { name: /fixture-local:27b/ }).click();
  await expectHome(page);
  await screenshot(page, testInfo, 'home');

  // The pick is the default: Setup no longer opens, and a new chat uses it.
  await page.goto('/app-v2/');
  await expectHome(page);
  await clickNewChat(page);
  if (!isMobile) {
    const pill = page.getByRole('button', { name: 'Model', exact: true });
    await expect(pill).toContainText('fixture-local:27b');
    await expect(
      pill.getByRole('img', { name: 'Runs on this device' }),
    ).toBeVisible();
  }
});

test('API key: recommended first, a key link, checked before it is saved', async ({
  page,
}, testInfo) => {
  await fixture(page, '/__p4_fixture/provider-credentials');
  await openFresh(page);
  await page.getByRole('button', { name: /With an API key/ }).click();
  const providers = page.getByRole('list', { name: 'Providers' });
  await expect(providers.getByRole('button')).toHaveText([
    /^OpenAI/,
    /^Anthropic/,
    /^Google Gemini/,
    /^OpenRouter/,
  ]);
  await expect(providers).toContainText('Pay per use');
  await expect(providers).toContainText('Credits');
  await expect(page.getByText('More providers')).toBeVisible();
  await providers.getByRole('button', { name: /^Anthropic/ }).click();
  await expect(page.getByRole('link', { name: 'Get a key' })).toHaveAttribute(
    'href',
    'https://console.anthropic.com/settings/keys',
  );
  const key = page.getByRole('textbox', { name: 'API key' });
  await expect(key).toHaveAccessibleDescription('Starts with sk-ant-');
  await expect(
    page.getByText(/Saving sends the key to .+ to check it/),
  ).toBeVisible();
  await key.fill('sk-ant-synthetic-refused');
  await page.getByRole('button', { name: 'Save key' }).click();
  await expect(page.getByRole('alert')).toContainText("didn't accept this key");
  // The refused key was never saved: it is still here to correct.
  await expect(key).toHaveValue('sk-ant-synthetic-refused');
  await screenshot(page, testInfo, 'key-refused');
  await assertNoOverflow(page);
  await key.fill('sk-ant-synthetic-fixture-good');
  await page.getByRole('button', { name: 'Save key' }).click();
  const models = page.getByRole('list', { name: 'Anthropic models' });
  await expect(models.getByRole('button').first()).toBeVisible({
    timeout: 20_000,
  });
  await screenshot(page, testInfo, 'key-models');
  await models.getByRole('button').first().click();
  await expectHome(page);
});

test('custom endpoint and Set up later: nothing traps the person', async ({
  page,
  browserName,
}, testInfo) => {
  // WebKit reports the access-session check that page.goto cancels as a page
  // error, though the client handles it; Chromium and Firefox stay quiet.
  test.skip(
    browserName === 'webkit',
    'WebKit reports a fetch cancelled by navigation as a page error',
  );
  await openFresh(page);
  await page.getByRole('link', { name: 'Other (custom endpoint)' }).click();
  await expect(
    page.getByRole('dialog', { name: /Add custom endpoint/ }),
  ).toBeVisible();
  await screenshot(page, testInfo, 'custom-endpoint');
  await page.getByRole('button', { name: 'Cancel' }).click();
  await expect(page).toHaveURL(/\/app-v2\/settings\/providers/);
  // A deep link is never redirected; the plain address still opens Setup.
  await page.goto('/app-v2/settings/models');
  await expect(page).toHaveURL(/\/app-v2\/settings\/models$/);
  await page.goto('/app-v2/');
  await expect(page).toHaveURL(/\/app-v2\/setup$/);
  await page.getByRole('button', { name: 'Set up later' }).click();
  await expectHome(page);
  const card = page.getByRole('region', { name: 'Continue setup' });
  await expect(card).toContainText('Choose how Row-Bot thinks');
  await expect(
    card.getByRole('link', { name: 'Choose a model' }),
  ).toHaveAttribute('href', '/app-v2/setup');
  await screenshot(page, testInfo, 'set-up-later');
});
