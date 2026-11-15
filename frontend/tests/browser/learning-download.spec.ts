import type { Page, TestInfo } from '@playwright/test';
import { accessibility, assertNoOverflow, expect, test } from './evidence';
import { fixtureHeaders } from './surface-helpers';

// Learning (preview), L2b: the personal model's download (the training engine and the
// starting model). The fixture flips the start-up flag, fakes the hardware check and stands
// in for the download with states it sets; nothing reaches the network, a GPU or a provider.
// The ask (with where it comes from and usage reporting off), downloading, Monitor's row,
// paused, not enough disk, failed, Ready to download, the engine and model in Settings with
// Remove and Delete, and an NVIDIA driver too old to train. Each test puts the profile back.

async function fixture(page: Page, path: string) {
  const response = await page.request.post(path, {
    headers: fixtureHeaders(),
  });
  expect(response.ok(), await response.text()).toBe(true);
}

const l1a = (page: Page, action: string) =>
  fixture(page, `/__l1a_fixture/learning/${action}`);
const l2b = (page: Page, action: string, name = '') =>
  fixture(
    page,
    `/__l2b_fixture/learning/${action}${name ? `?name=${name}` : ''}`,
  );

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    if (window !== window.top) return;
    localStorage.setItem(
      'row-bot.appearance.v1',
      JSON.stringify({
        version: 1,
        appearance: 'system',
        accent: 'blue',
        density: 'compact',
        reduce_transparency: false,
      }),
    );
  });
});

test.afterEach(async ({ page }) => {
  // Close the app first: restoring turns Learning's start-up flag off, and a
  // poll still due (download, training) would get a 404 a real app never sees.
  await page.close();
  await l1a(page, 'restore');
});

const phone = (info: TestInfo) =>
  (info.project.use.viewport?.width ?? 1440) < 700;

/** `<surface>-<state>-{desktop,phone}-{light,dark}.png`, each with an axe scan. */
async function shoot(page: Page, info: TestInfo, name: string) {
  const size = phone(info) ? 'phone' : 'desktop';
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    await assertNoOverflow(page);
    const file = info.outputPath(`${name}-${size}-${appearance}.png`);
    await page.screenshot({
      path: file,
      fullPage: true,
      animations: 'disabled',
    });
    await info.attach(`${name}-${size}-${appearance}`, {
      path: file,
      contentType: 'image/png',
    });
    await accessibility(page, info, `${name}-${size}-${appearance}`);
  }
  await page.emulateMedia({ colorScheme: 'light' });
}

/** A notice by its words (not Radix's announcement copy of them). */
function notice(page: Page, text: string | RegExp) {
  return page.locator('.toast-message').filter({ hasText: text });
}

async function turnOn(page: Page, label: string, ask = true) {
  await page.goto('/app-v2/settings/learning');
  await page.getByRole('button', { name: 'Turn on Learning' }).click();
  const sheet = page.getByRole('dialog', { name: 'Turn on Learning' });
  await expect(sheet.getByText(label)).toBeVisible();
  await sheet.getByRole('button', { name: 'Turn on Learning' }).click();
  if (ask)
    await page
      .getByRole('dialog', {
        name: 'Download the training engine and Qwen3.5 9B?',
      })
      .getByRole('button', { name: 'Not now', exact: true })
      .click();
  await expect(notice(page, 'Learning is on.')).toBeVisible();
}

async function openModel(page: Page, label: string) {
  await page.goto('/app-v2/?tab=learning&view=model');
  await expect(
    page.getByRole('heading', { name: 'Learning', exact: true }),
  ).toBeVisible();
  await expect(page.getByText(label).first()).toBeVisible();
}

/** The download card's own words (the status region repeats some for screen readers). */
function card(page: Page) {
  return page.getByRole('region', { name: 'Download' });
}

const GPU = 'NVIDIA GeForce RTX 5080 Laptop (16 GB)';

test('the download: the ask, downloading, Monitor, paused, not enough disk, failed, ready, and the engine and model in Settings', async ({
  page,
}, info) => {
  test.setTimeout(180_000);
  await l1a(page, 'preview');
  await l1a(page, 'gpu');
  await turnOn(page, GPU);

  // The ask: sizes, free space, where it comes from, and the engine's usage reporting off.
  await openModel(page, GPU);
  await expect(
    page.getByRole('heading', { name: 'No personal model yet' }),
  ).toBeVisible();
  await page.getByRole('button', { name: 'Download…' }).click();
  const ask = page.getByRole('dialog', {
    name: 'Download the training engine and Qwen3.5 9B?',
  });
  await expect(
    ask.getByText(
      'From PyPI, PyTorch, Hugging Face and GitHub (llama.cpp). The training engine’s own usage reporting is switched off.',
    ),
  ).toBeVisible();
  await shoot(page, info, 'download-ask');
  await ask.getByRole('button', { name: 'Download', exact: true }).click();
  await expect(
    notice(page, 'Downloading the training engine and Qwen3.5 9B.'),
  ).toBeVisible();

  // Downloading: bytes and percent of the whole download, a real bar, Stop.
  await expect(
    card(page).getByText(
      'Downloading the training engine and Qwen3.5 9B · 1.2 of 21.5 GB',
    ),
  ).toBeVisible();
  await expect(
    card(page).getByText('5% · now the training engine'),
  ).toBeVisible();
  await expect(
    card(page).getByRole('progressbar', { name: 'Download progress' }),
  ).toHaveAttribute('max', '21549991432');
  await shoot(page, info, 'model-downloading');

  // Monitor: one row for the download, with Stop.
  await page.goto('/app-v2/?tab=monitor');
  const row = page.getByRole('listitem').filter({ hasText: 'Training engine' });
  await expect(row.getByText('Downloading · 5%')).toBeVisible();
  await expect(
    row.getByRole('button', { name: 'Stop downloading Training engine' }),
  ).toBeVisible();
  await row.evaluate((element) => element.scrollIntoView({ block: 'center' }));
  await shoot(page, info, 'monitor-download');
  await row
    .getByRole('button', { name: 'Stop downloading Training engine' })
    .click();
  // Stop pauses it: the row goes (Resume is on the Personal model view).
  await expect(row).toHaveCount(0);

  // Paused (UX §9's words), with Resume and Cancel.
  await l2b(page, 'state', 'paused');
  await openModel(page, GPU);
  await expect(
    card(page).getByText('Download paused at 3.1 of 21.5 GB.'),
  ).toBeVisible();
  await expect(
    card(page).getByRole('button', { name: 'Resume' }),
  ).toBeVisible();
  await shoot(page, info, 'model-paused');
  await card(page).getByRole('button', { name: 'Cancel' }).click();
  const cancel = page.getByRole('dialog', { name: 'Cancel the download?' });
  await expect(cancel).toContainText(
    'What it downloaded so far (3.1 GB) is deleted.',
  );
  await shoot(page, info, 'model-cancel-confirm');
  await cancel.getByRole('button', { name: 'Keep it' }).click();

  // Not enough disk: what it needs, and what is free.
  await l2b(page, 'state', 'disk');
  await openModel(page, GPU);
  await expect(
    card(page).getByText('Needs 24.2 GB free; 6 GB free.'),
  ).toBeVisible();
  await shoot(page, info, 'model-not-enough-disk');

  // Failed, with the reason and Try again.
  await l2b(page, 'state', 'failed');
  await openModel(page, GPU);
  await expect(
    card(page).getByText(/^The download stopped: Row-Bot couldn’t reach/),
  ).toBeVisible();
  await expect(
    card(page).getByRole('button', { name: 'Try again' }),
  ).toBeVisible();
  await shoot(page, info, 'model-failed');

  // Ready to download (an earlier Download answer, O36): the sizes and free space again.
  await l2b(page, 'state', 'ready');
  await openModel(page, GPU);
  await expect(card(page).getByText('Ready to download')).toBeVisible();
  await expect(
    card(page).getByText(
      'The training engine (2.2 GB) and Qwen3.5 9B (19.3 GB). 512 GB free on this computer.',
    ),
  ).toBeVisible();
  await shoot(page, info, 'model-ready');

  // Installed: Settings › Learning shows the engine and starting model with their sizes.
  await l2b(page, 'state', 'installed');
  await page.goto('/app-v2/settings/learning');
  const engine = page
    .locator('.settings-row')
    .filter({ hasText: 'Training engine' });
  await expect(engine.getByText('Installed, 4 GB')).toBeVisible();
  const starting = page
    .locator('.settings-row')
    .filter({ hasText: 'Starting model' });
  await expect(starting.getByText('On this computer, 19.3 GB')).toBeVisible();
  await engine.evaluate((element) =>
    element.scrollIntoView({ block: 'center' }),
  );
  await shoot(page, info, 'settings-installed');
  await engine.getByRole('button', { name: 'Remove' }).click();
  const remove = page.getByRole('alertdialog', {
    name: 'Remove the training engine?',
  });
  await expect(remove).toContainText('It frees 4 GB.');
  await shoot(page, info, 'settings-remove-confirm');
  await remove.getByRole('button', { name: 'Cancel' }).click();
  await starting.getByRole('button', { name: 'Delete' }).click();
  const remove2 = page.getByRole('alertdialog', { name: 'Delete Qwen3.5 9B?' });
  await expect(remove2).toContainText('It frees 19.3 GB.');
  await shoot(page, info, 'settings-delete-confirm');
  await remove2.getByRole('button', { name: 'Cancel' }).click();

  // The Personal model view reflects them.
  await openModel(page, GPU);
  const facts = page.locator('dl[aria-label="Personal model"]');
  await expect(facts).toContainText('On this computer (19.3 GB)');
  await expect(facts).toContainText('Installed (4 GB)');
  await shoot(page, info, 'model-installed');
});

test('an NVIDIA driver older than 570 says to update it, in the setup sheet and the Personal model view', async ({
  page,
}, info) => {
  await l1a(page, 'preview');
  await l1a(page, 'gpu');
  await l2b(page, 'old-driver');
  const label = 'NVIDIA GeForce RTX 4090 Laptop (16 GB)';
  await page.goto('/app-v2/settings/learning');
  await page.getByRole('button', { name: 'Turn on Learning' }).click();
  const sheet = page.getByRole('dialog', { name: 'Turn on Learning' });
  await expect(
    sheet.getByText('Update your NVIDIA driver to train here.'),
  ).toBeVisible();
  await shoot(page, info, 'setup-old-driver');
  await sheet.getByRole('button', { name: 'Turn on Learning' }).click();
  await expect(notice(page, 'Learning is on.')).toBeVisible();

  await openModel(page, label);
  await expect(
    page.getByRole('heading', {
      name: 'Update your NVIDIA driver to train here',
    }),
  ).toBeVisible();
  await shoot(page, info, 'model-old-driver');
});
