import type { Page, TestInfo } from '@playwright/test';
import { accessibility, assertNoOverflow, expect, test } from './evidence';
import { fixtureHeaders } from './surface-helpers';
import { clickNewChat, openConversation } from './unified-helpers';

// Learning (preview), L2c2: comparing the personal model, trying it and using it. The fixture
// trains v1 as L2c's does, makes it ready, then writes what a comparison leaves (v1 against the
// starting model and the main model, counts with their denominators), a trial that's due to be
// kept, a chat where v1 answered and got stuck once, v1 in the composer's picker and a deleted
// chat v1 was trained on. Nothing reaches the network, a GPU, a runtime or a provider. Each test
// puts the profile back.

async function fixture(page: Page, path: string) {
  const response = await page.request.post(path, {
    headers: fixtureHeaders(),
  });
  expect(response.ok(), await response.text()).toBe(true);
}

const l1a = (page: Page, action: string) =>
  fixture(page, `/__l1a_fixture/learning/${action}`);
const l2c = (page: Page, action: string, name = '') =>
  fixture(
    page,
    `/__l2c_fixture/learning/${action}${name ? `?name=${name}` : ''}`,
  );
const l2c2 = (page: Page, action: string, name = '') =>
  fixture(
    page,
    `/__l2c2_fixture/learning/${action}${name ? `?name=${name}` : ''}`,
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
  await l2c2(page, 'restore');
  await fixture(page, '/__s8_fixture/models/restore');
  await l2c(page, 'restore');
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

const GPU = 'NVIDIA GeForce RTX 5070 Laptop (8 GB)';

async function turnOn(page: Page) {
  await l1a(page, 'preview');
  await l1a(page, 'gpu');
  await page.goto('/app-v2/settings/learning');
  await page.getByRole('button', { name: 'Turn on Learning' }).click();
  const sheet = page.getByRole('dialog', { name: 'Turn on Learning' });
  await expect(sheet.getByText(GPU)).toBeVisible();
  await sheet.getByRole('button', { name: 'Turn on Learning' }).click();
  await page
    .getByRole('dialog', {
      name: 'Download the training engine and Qwen3.5 2B?',
    })
    .getByRole('button', { name: 'Not now', exact: true })
    .click();
  await expect(notice(page, 'Learning is on.')).toBeVisible();
}

/** Learning on, v1 trained by L2c's fixture and ready in Ollama, with its files. */
async function readyV1(page: Page) {
  await turnOn(page);
  await l2c(page, 'setup');
  await l2c(page, 'train');
  await l2c(page, 'finish');
  await l2c(page, 'version', 'ready');
  await l2c2(page, 'files');
}

async function openModel(page: Page) {
  await page.goto('/app-v2/?tab=learning&view=model');
  await expect(
    page.getByRole('heading', { name: 'Learning', exact: true }),
  ).toBeVisible();
  await expect(page.getByText(GPU).first()).toBeVisible();
}

async function openOverview(page: Page) {
  await page.goto('/app-v2/?tab=learning');
  await expect(
    page.getByRole('heading', { name: 'Learning', exact: true }),
  ).toBeVisible();
}

const versions = (page: Page) => page.getByRole('region', { name: 'Versions' });
const uses = (page: Page) => page.getByRole('region', { name: 'In use for' });
const needs = (page: Page) => page.getByRole('region', { name: 'Needs you' });

test('compare v1, review it with its experiment details, try it for document lookups, keep it and undo it', async ({
  page,
}, info) => {
  test.setTimeout(240_000);
  await readyV1(page);

  // Ready: compared before it is ever used (D24); Compare now doesn't wait for a free computer.
  await openModel(page);
  const card = page.getByRole('region', { name: 'Personal model v1' });
  await expect(card.getByText('v1 is ready to compare')).toBeVisible();
  await card.getByRole('button', { name: 'Compare now' }).click();
  await expect(
    notice(page, 'Comparing v1 with your main model.'),
  ).toBeVisible();
  await l2c2(page, 'progress', '12');
  await openModel(page);
  await expect(
    versions(page).getByText(/^Comparing v1 with .* · 12 of 96 tasks$/),
  ).toBeVisible();
  await expect(
    versions(page).getByRole('progressbar', { name: 'Comparing v1' }),
  ).toHaveAttribute('max', '96');
  await shoot(page, info, 'compare-running');

  // The result: the versions table, every count with its denominator, said plainly.
  await l2c2(page, 'compared', 'better');
  await openModel(page);
  if (phone(info)) {
    await expect(versions(page).getByRole('table')).toHaveCount(0);
    await expect(
      versions(page).getByText(
        /^.+: 11 passed · Starting model: 6 passed · v1 \(new\): 10 passed$/,
      ),
    ).toBeVisible();
  } else {
    const table = versions(page).getByRole('table');
    await expect(table.getByRole('columnheader')).toHaveText([
      'Kind of work',
      /.+/, // the main model, by name
      'Starting model',
      'v1 (new)',
    ]);
    await expect(
      table.getByRole('rowheader', { name: /^Document lookups \(12\)/ }),
    ).toContainText('Better');
  }
  await expect(
    versions(page).getByText(
      'Better at document lookups than the starting model: 10 of 12 passed, against 6.',
    ),
  ).toBeVisible();
  await expect(
    versions(page).getByText(
      'Coding tasks need Docker or Podman, so they were left out.',
    ),
  ).toBeVisible();
  // Main screens never name the file (O54).
  await expect(page.getByText('row-bot-personal-v1-Q8_0.gguf')).toHaveCount(0);
  await shoot(page, info, 'versions-compared');

  // Overview › Needs you › Review: the shared review sheet with Experiment details.
  await openOverview(page);
  await expect(
    needs(page).getByText('Personal model v1 is ready to compare'),
  ).toBeVisible();
  await shoot(page, info, 'overview-ready-to-compare');
  await needs(page).getByRole('button', { name: 'Review' }).click();
  const sheet = page.getByRole('dialog', { name: 'Personal model v1' });
  await expect(
    sheet.getByRole('button', { name: 'Use v1 for document lookups' }),
  ).toBeVisible();
  await shoot(page, info, 'review');
  await sheet
    .getByRole('button', { name: 'More for Personal model v1' })
    .click();
  await page.getByRole('menuitem', { name: 'Experiment details' }).click();
  const details = sheet.getByRole('region', { name: 'Experiment details' });
  await expect(details.getByText(/^154 examples of your work/)).toBeVisible();
  await expect(
    details.getByText('row-bot-personal-v1-Q8_0.gguf', { exact: false }),
  ).toBeVisible();
  await shoot(page, info, 'review-experiment-details');

  // Use v1 for document lookups: a trial, at once, with Undo on its notice.
  await sheet
    .getByRole('button', { name: 'Use v1 for document lookups' })
    .click();
  await expect(
    notice(page, /^v1 now answers document lookups, on trial/),
  ).toBeVisible();
  await openModel(page);
  await expect(uses(page).getByText('Document lookups · v1')).toBeVisible();
  await expect(
    uses(page).getByText(/^On trial since .* · No requests yet$/),
  ).toBeVisible();
  await shoot(page, info, 'trial');

  // After a few days and enough requests, Overview asks to keep it.
  await l2c2(page, 'restore');
  await l2c2(page, 'keep-due');
  await openOverview(page);
  const keep = needs(page)
    .getByRole('listitem')
    .filter({ hasText: 'Keep v1 for document lookups?' });
  await expect(
    keep.getByText(/^18 requests: 17 answered, 1 finished by .* on trial\.$/),
  ).toBeVisible();
  await shoot(page, info, 'keep-ask');
  await keep.getByRole('button', { name: 'Keep', exact: true }).click();
  await expect(
    notice(page, 'v1 is in use for document lookups.'),
  ).toBeVisible();
  await openModel(page);
  await expect(uses(page).getByText(/^In use since/)).toBeVisible();
  await shoot(page, info, 'in-use');

  // Undo, at once: the main model answers document lookups again.
  await uses(page)
    .getByRole('button', { name: 'Undo v1 for document lookups' })
    .click();
  await expect(notice(page, /^Document lookups go back to/)).toBeVisible();
  await expect(uses(page)).toHaveCount(0);
});

test('a result with no clear difference is said plainly, and nothing is used', async ({
  page,
}, info) => {
  await readyV1(page);
  await l2c2(page, 'compared', 'no_difference');
  await openModel(page);
  await expect(
    versions(page).getByText(
      'No clear difference from the starting model. Not used.',
    ),
  ).toBeVisible();
  await expect(
    versions(page).getByRole('button', { name: /^Use v1 for/ }),
  ).toHaveCount(0);
  await expect(uses(page)).toHaveCount(0);
  await shoot(page, info, 'no-clear-difference');
});

test('Export… lists the files it saves, and Advanced keeps the recommended values with Reset', async ({
  page,
}, info) => {
  await readyV1(page);
  await l2c2(page, 'compared', 'better');
  await openModel(page);
  await versions(page).getByRole('button', { name: 'More for v1' }).click();
  await page.getByRole('menuitem', { name: 'Export…' }).click();
  const dialog = page.getByRole('dialog', { name: 'Export v1' });
  await expect(dialog.getByRole('textbox', { name: 'Folder' })).toHaveValue(
    /Row-Bot personal models$/,
  );
  await expect(dialog.getByText('MODEL_CARD.md')).toBeVisible();
  await expect(dialog.getByText(/^Files \(2\sGB in all\)$/)).toBeVisible();
  await shoot(page, info, 'export');
  await dialog.getByRole('button', { name: 'Cancel' }).click();

  // Advanced ›: closed until asked for, with "Recommended" beside it.
  const advanced = page.locator('.learning-advanced > summary');
  await expect(advanced).toHaveText(/^Advanced\s*Recommended$/);
  await advanced.click();
  const rank = page.getByRole('combobox', { name: 'LoRA rank' });
  await expect(rank).toHaveValue('16');
  await rank.selectOption('32');
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  await expect(
    notice(page, /The next training run uses these settings\.$/),
  ).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Reset to recommended' }),
  ).toBeEnabled();
  // The notice would cover Reset on a phone: it is read, then dismissed.
  await page
    .getByRole('button', { name: 'Dismiss notification', exact: true })
    .click();
  await expect(
    notice(page, /The next training run uses these settings\.$/),
  ).toHaveCount(0);
  await shoot(page, info, 'advanced');
  await page.getByRole('button', { name: 'Reset to recommended' }).click();
  await expect(notice(page, 'Back to the recommended settings.')).toBeVisible();
  await expect(rank).toHaveValue('16');
});

test('a deleted chat v1 was trained on asks to retrain without it', async ({
  page,
}, info) => {
  await readyV1(page);
  await l2c2(page, 'compared', 'better');
  await l2c2(page, 'retrain');
  await openOverview(page);
  const item = needs(page)
    .getByRole('listitem')
    .filter({ hasText: 'A deleted chat was used to train v1.' });
  await expect(
    item.getByText(
      'Deleting a chat doesn’t remove what a model already learned from it.',
    ),
  ).toBeVisible();
  await shoot(page, info, 'retrain-without');
  await item.getByRole('button', { name: 'Retrain without it' }).click();
  await expect(notice(page, /without that chat\.$/)).toBeVisible();
});

test('the chat says which replies the personal model gave, and when the main model finished one', async ({
  page,
}, info) => {
  await readyV1(page);
  await l2c2(page, 'chat');
  await openConversation(page, 'l2c2-travel-policy');
  // Only the answer it gave: never the step it got stuck on.
  await expect(page.getByText('Answered by your personal model')).toHaveCount(
    1,
  );
  await expect(
    page.getByRole('note').filter({
      hasText: /^Your personal model got stuck; .+ finished this\.$/,
    }),
  ).toBeVisible();
  // The hand-over is never shown as the person's words.
  await expect(page.getByText(/couldn.t finish it/)).toHaveCount(0);
  await shoot(page, info, 'chat-lines');
  // Putting the profile back deletes this chat: close it first, as a person would leave it.
  await page.close();
});

test('the composer’s picker offers a ready version as Personal model v1', async ({
  page,
}, info) => {
  await readyV1(page);
  await fixture(page, '/__s8_fixture/models/seed');
  await l2c2(page, 'picker');
  await page.goto('/app-v2/');
  await clickNewChat(page);
  const pill = page.getByRole('button', { name: 'Model', exact: true });
  const plus = page.getByRole('button', { name: 'Add files and more' });
  await expect(pill.or(plus).first()).toBeVisible();
  if (await pill.isVisible()) await pill.click();
  else {
    await plus.click();
    await page.getByRole('menuitem', { name: /^Model/ }).click();
  }
  const list = page.getByRole('listbox', { name: 'Models' });
  const row = list.getByRole('option', { name: /^Personal model v1/ });
  await expect(row).toContainText('Personal');
  // Its tag in Ollama is machinery: never shown (O54).
  await expect(list.getByText('row-bot-personal-v1')).toHaveCount(0);
  await shoot(page, info, 'picker');
});
