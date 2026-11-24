import type { Locator, Page, TestInfo } from '@playwright/test';
import { accessibility, assertNoOverflow, expect, test } from './evidence';
import { fixtureHeaders } from './surface-helpers';

// Learning (preview), C1 Part 4 (O138): any open model. Settings › Starting model has Recommended (the certified
// models), then Other models: any chat model on Hugging Face, offered only after an automatic check on this
// computer, each showing what was checked. The fixture fakes an RTX 5090, Hugging Face's public pages (search,
// cards, files) and the check's steps; nothing reaches huggingface.co, a GPU or a download.

async function fixture(page: Page, path: string) {
  const response = await page.request.post(path, {
    headers: fixtureHeaders(),
  });
  expect(response.ok(), await response.text()).toBe(true);
}

const named =
  (prefix: string) =>
  (page: Page, action: string, name = '') =>
    fixture(page, `${prefix}/${action}${name ? `?name=${name}` : ''}`);
const l1a = named('/__l1a_fixture/learning');
const l2b = named('/__l2b_fixture/learning');
const l2x = named('/__l2x_fixture/learning');
const c1 = named('/__c1_fixture/learning');
const models = named('/__c1m_fixture/learning');

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
  // Close the app first: restoring deletes Learning's data and its stand-ins, and
  // a poll still due would read them half gone.
  await page.close();
  await models(page, 'restore');
  await c1(page, 'restore');
  await l2x(page, 'restore');
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

function notice(page: Page, text: string | RegExp) {
  return page.locator('.toast-message').filter({ hasText: text });
}

/** Learning on, on an RTX 5090, with the fake Hugging Face pages; `engine`: the training engine is here. */
async function onLearning(page: Page, engine = true) {
  await l1a(page, 'stand-ins');
  await l2x(page, 'card', '5090');
  await page.goto('/app-v2/settings/learning');
  await page.getByRole('button', { name: 'Turn on Learning' }).click();
  const sheet = page.getByRole('dialog', { name: 'Turn on Learning' });
  await expect(
    sheet.getByText('NVIDIA GeForce RTX 5090 (32 GB)'),
  ).toBeVisible();
  await sheet.getByRole('button', { name: 'Turn on Learning' }).click();
  await page
    .getByRole('dialog', { name: /^Download the training engine and / })
    .getByRole('button', { name: 'Not now', exact: true })
    .click();
  await expect(notice(page, 'Learning is on.')).toBeVisible();
  if (engine) await l2b(page, 'state', 'installed');
  await models(page, 'models');
}

/** Settings › Learning › Starting model › Change…: the picker. */
async function openPicker(page: Page) {
  await page.goto('/app-v2/settings/learning');
  await page.getByRole('button', { name: 'Change the starting model' }).click();
  const sheet = page.getByRole('dialog', { name: 'Starting model' });
  await expect(
    sheet.getByRole('region', { name: 'Other models' }),
  ).toBeVisible();
  return sheet;
}

async function search(sheet: Locator, text: string) {
  await sheet
    .getByRole('searchbox', { name: 'Search Hugging Face' })
    .fill(text);
  await sheet.getByRole('button', { name: 'Search', exact: true }).click();
  const results = sheet.getByRole('list', { name: 'Search results' });
  await expect(results.getByRole('listitem').first()).toBeVisible();
  return results;
}

function checkedModel(sheet: Locator, name: string) {
  return sheet
    .getByRole('list', { name: 'Checked models' })
    .getByRole('listitem', { name, exact: true });
}

test('Other models: plain words for what can’t be tried, the Check ask with a licence’s terms, a check through to Use for training (O138)', async ({
  page,
}, info) => {
  await onLearning(page);
  const sheet = await openPicker(page);
  await expect(
    sheet.getByRole('radio', { name: /^Qwen3\.5 9B · in use/ }),
  ).toBeChecked();
  await shoot(page, info, 'models-picker');

  const results = await search(sheet, 'llama');
  await expect(
    results.getByRole('listitem', { name: 'Qwen3-30B-A3B' }),
  ).toContainText(
    'Qwen3-30B-A3B is a mixture-of-experts model. Row-Bot’s training doesn’t adapt those yet.',
  );
  await expect(
    results.getByRole('listitem', { name: 'Phi-4-mini-instruct' }),
  ).toContainText('packs its attention layers in a way Row-Bot’s training');
  await expect(
    results.getByRole('listitem', { name: 'Llama-3.3-70B-Instruct' }),
  ).toContainText('Too big to run on this computer afterwards');
  const gated = results
    .getByRole('listitem')
    .filter({ hasText: 'meta-llama/Llama-3.1-8B-Instruct' });
  await expect(
    gated.getByRole('link', { name: 'Request access' }),
  ).toHaveAttribute(
    'href',
    'https://huggingface.co/meta-llama/Llama-3.1-8B-Instruct',
  );
  await expect(results.getByRole('button', { name: 'Check…' })).toHaveCount(2);
  await shoot(page, info, 'models-search');

  await results
    .getByRole('listitem')
    .filter({ hasText: 'unsloth/Llama-3.1-8B-Instruct' })
    .getByRole('button', { name: 'Check…' })
    .click();
  const ask = sheet.getByRole('group', {
    name: 'Check Llama-3.1-8B-Instruct?',
  });
  await expect(ask).toContainText(
    'Llama-3.1-8B-Instruct uses the Llama Community License. Its key terms:',
  );
  await expect(ask).toContainText('Meta’s acceptable use policy applies.');
  const check = ask.getByRole('button', { name: 'Check', exact: true });
  await expect(check).toBeDisabled();
  await shoot(page, info, 'models-ask-terms');

  await ask
    .getByRole('checkbox', { name: /I agree to the Llama Community License/ })
    .check();
  await check.click();
  await expect(
    notice(
      page,
      /^Checking Llama-3\.1-8B-Instruct on this computer\. It downloads first/,
    ),
  ).toBeVisible();
  const llama = checkedModel(sheet, 'Llama-3.1-8B-Instruct');
  await models(page, 'check', 'downloading');
  await expect(llama).toContainText(/Downloading: .+ of .+GB/);
  await expect(
    llama.getByRole('button', { name: 'Stop the check' }),
  ).toBeVisible();
  await shoot(page, info, 'models-downloading');

  await models(page, 'check', 'checking');
  await expect(llama).toContainText(
    'Fits the graphics card: Passed. Trains on this card: peak 21.2 GB',
  );
  await expect(llama).toContainText('Learns: a few training steps: Checking…');
  await shoot(page, info, 'models-checking');

  await models(page, 'check', 'passed');
  await expect(llama.getByText('Passed', { exact: true })).toBeVisible();
  await expect(llama).toContainText(
    'Calls Row-Bot’s tools: Passed. Right 2 of 3 times.',
  );
  await llama.getByText('The tool sample: 2 of 3 tasks right').click();
  await expect(llama).toContainText('Wrong: Use the calculator to add 4821');
  await shoot(page, info, 'models-passed');

  await llama.getByRole('button', { name: 'Use for training…' }).click();
  await expect(
    llama.getByRole('group', { name: 'Use Llama-3.1-8B-Instruct?' }),
  ).toContainText('It isn’t certified: Row-Bot checked it on this computer.');
  await shoot(page, info, 'models-use');

  await llama
    .getByRole('button', { name: 'Use Llama-3.1-8B-Instruct' })
    .click();
  await expect(
    notice(page, /^Switched to Llama-3\.1-8B-Instruct\./),
  ).toBeVisible();
  await expect(llama).toContainText('In use for training');
  await expect(llama.getByRole('button', { name: /^Remove/ })).toHaveCount(0);
  await expect(
    sheet.getByRole('radio', { name: /^Qwen3\.5 9B · recommended/ }),
  ).not.toBeChecked();

  // The switch asks for the starting model's download, which the check already made: on this computer.
  await sheet.getByRole('button', { name: 'Cancel' }).click();
  await l2b(page, 'state', 'installed');
  await page.goto('/app-v2/settings/learning');
  await expect(
    page.getByText(
      /^Llama-3\.1-8B-Instruct · Llama Community License · from Hugging Face/,
    ),
  ).toBeVisible();
  await shoot(page, info, 'models-settings-in-use');

  const again = await openPicker(page);
  await expect(checkedModel(again, 'Llama-3.1-8B-Instruct')).toContainText(
    'In use for training',
  );
  await shoot(page, info, 'models-in-use');
});

test('a check can be stopped, checked again, can fail with its reason, and be removed', async ({
  page,
}, info) => {
  await onLearning(page);
  const sheet = await openPicker(page);
  const results = await search(sheet, 'qwen');

  await results
    .getByRole('listitem', { name: 'Qwen3-14B' })
    .getByRole('button', { name: 'Check…' })
    .click();
  const ask = sheet.getByRole('group', { name: 'Check Qwen3-14B?' });
  await expect(ask.getByRole('checkbox')).toHaveCount(0); // Apache 2.0: shown, nothing to agree
  await shoot(page, info, 'models-ask');
  await ask.getByRole('button', { name: 'Check', exact: true }).click();
  const qwen = checkedModel(sheet, 'Qwen3-14B');
  await expect(qwen).toContainText('Downloading');
  await expect(
    results
      .getByRole('listitem')
      .filter({ hasText: 'unsloth/Llama-3.1-8B-Instruct' })
      .getByRole('button', { name: 'Check…' }),
  ).toBeDisabled(); // one check at a time

  await qwen.getByRole('button', { name: 'Stop the check' }).click();
  await expect(notice(page, /^Stopping the check\./)).toBeVisible();
  await models(page, 'check', 'stopped');
  await expect(qwen.getByText('Stopped', { exact: true })).toBeVisible();
  await shoot(page, info, 'models-stopped');

  await qwen.getByRole('button', { name: 'Check Qwen3-14B again' }).click();
  await expect(qwen).toContainText('Downloading');
  await models(page, 'check', 'failed');
  await expect(qwen.getByText('Didn’t pass', { exact: true })).toBeVisible();
  await expect(qwen).toContainText(
    'Calls Row-Bot’s tools: Didn’t pass. Right 1 of 3 times; it needs at least 2.',
  );
  await expect(
    qwen.getByRole('button', { name: 'Use for training…' }),
  ).toHaveCount(0);
  await shoot(page, info, 'models-failed');

  await qwen.getByRole('button', { name: 'Remove Qwen3-14B' }).click();
  await expect(notice(page, 'Removed from Other models.')).toBeVisible();
  await expect(sheet.getByRole('list', { name: 'Checked models' })).toHaveCount(
    0,
  );
});

test('without the training engine, Other models says to download it first and checks nothing', async ({
  page,
}, info) => {
  await onLearning(page, false);
  const sheet = await openPicker(page);
  const results = await search(sheet, 'qwen');

  await expect(sheet.getByRole('note')).toContainText(
    'To check a model, first download the training engine',
  );
  for (const button of await results
    .getByRole('button', { name: 'Check…' })
    .all())
    await expect(button).toBeDisabled();
  await shoot(page, info, 'models-engine-needed');
});

test('training on Hugging Face: a checked model too big to train here trains there, said to be uncertified', async ({
  page,
}, info) => {
  await onLearning(page);
  await c1(page, 'hugging-face', 'signed_in');
  await page.goto('/app-v2/settings/learning');
  await page
    .getByRole('button', { name: 'Other models on Hugging Face' })
    .click();
  const sheet = page.getByRole('dialog', { name: 'Other models' });
  const results = await search(sheet, 'qwen');
  await results
    .getByRole('listitem', { name: 'Qwen3-14B' })
    .getByRole('button', { name: 'Check…' })
    .click();
  await sheet
    .getByRole('group', { name: 'Check Qwen3-14B?' })
    .getByRole('button', { name: 'Check', exact: true })
    .click();
  await expect(
    notice(page, /^Checking Qwen3-14B on this computer/),
  ).toBeVisible();
  await models(page, 'check', 'too-big');
  const qwen = checkedModel(sheet, 'Qwen3-14B');
  await expect(qwen).toContainText(
    'Trains on Hugging Face: too big to train on this graphics card',
  );
  await expect(qwen).toContainText(
    'it trains on Hugging Face on an Nvidia L40S (48 GB).',
  );

  await qwen.getByRole('button', { name: 'Use on Hugging Face…' }).click();
  await expect(
    qwen.getByRole('group', { name: 'Use Qwen3-14B?' }),
  ).toContainText(
    'its time and price are estimates until a run measures them.',
  );
  await shoot(page, info, 'models-hf-use');
  await qwen.getByRole('button', { name: 'Use Qwen3-14B' }).click();
  await expect(
    notice(page, /^Qwen3-14B will train on Hugging Face\. It isn’t certified/),
  ).toBeVisible();
  await expect(qwen).toContainText('In use for training');
  await sheet.getByRole('button', { name: 'Done' }).click();
  await expect(
    page.getByRole('combobox', { name: 'Model on Hugging Face' }),
  ).toHaveValue('hf:Qwen/Qwen3-14B');
  await shoot(page, info, 'models-hf-settings');

  await page.goto('/app-v2/?tab=learning&view=model');
  const model = page.getByRole('group', { name: 'Model to train' });
  await model.scrollIntoViewIfNeeded(); // on a phone it sits below the sign-in
  await expect(
    model.getByRole('radio', {
      name: /^Qwen3-14B · Apache 2\.0 · Not certified/,
    }),
  ).toBeChecked();
  await expect(model).toContainText(
    'Checked on this computer. Its time and price are estimates.',
  );
  await shoot(page, info, 'models-hf-card');
});
