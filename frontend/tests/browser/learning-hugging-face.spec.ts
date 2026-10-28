import type { Page, TestInfo } from '@playwright/test';
import { accessibility, assertNoOverflow, expect, test } from './evidence';
import { fixtureHeaders } from './surface-helpers';

// Learning (preview), C1: training on Hugging Face for people new to it. The fixture fakes an RTX 5090 (it runs
// the 27B after), a fake Hub and sign-in in memory, and a Hugging Face run at each of its states; nothing
// reaches huggingface.co, a GPU or a provider. Every paid run shows its quote first, on the device it starts
// from; contact lost reads "Checking whether Hugging Face is still training…", never Failed.

async function fixture(page: Page, path: string) {
  const response = await page.request.post(path, { headers: fixtureHeaders() });
  expect(response.ok(), await response.text()).toBe(true);
}

const l1a = (page: Page, action: string) =>
  fixture(page, `/__l1a_fixture/learning/${action}`);
const l2x = (page: Page, action: string, name = '') =>
  fixture(
    page,
    `/__l2x_fixture/learning/${action}${name ? `?name=${name}` : ''}`,
  );
const c1 = (page: Page, action: string, name = '') =>
  fixture(
    page,
    `/__c1_fixture/learning/${action}${name ? `?name=${name}` : ''}`,
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

/** Learning on (the download not asked), on an RTX 5090, training on Hugging Face with the fake Hub. */
async function onHuggingFace(page: Page, signIn = 'signed_out') {
  await l1a(page, 'preview');
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
  await c1(page, 'hugging-face', signIn);
}

async function openModel(page: Page) {
  await page.goto('/app-v2/?tab=learning&view=model');
  await expect(
    page.getByRole('heading', { name: 'Learning', exact: true }),
  ).toBeVisible();
}

function card(page: Page) {
  return page.getByRole('region', { name: 'Hugging Face' });
}

function training(page: Page) {
  return page.getByRole('region', { name: 'Training' });
}

test('someone new to Hugging Face is guided: what it is, what Jobs need, a typical price, then Sign in', async ({
  page,
}, info) => {
  await onHuggingFace(page);
  await openModel(page);

  await expect(card(page)).toContainText(
    'Hugging Face is where open models like Qwen are published',
  );
  await expect(card(page)).toContainText(
    'You need a Hugging Face account with credit for Jobs',
  );
  await expect(card(page)).toContainText(
    /A typical run of Qwen3\.8 27B costs about \$\d+\.\d\d/,
  );
  await expect(
    card(page).getByRole('link', { name: 'Create an account' }),
  ).toHaveAttribute('href', 'https://huggingface.co/join');
  await expect(
    card(page).getByRole('button', { name: 'Sign in with Hugging Face' }),
  ).toBeVisible();
  await shoot(page, info, 'hf-guided-start');

  await c1(page, 'hugging-face', 'signing_in');
  await openModel(page);
  await expect(card(page)).toContainText(
    'If it asks for a code, enter ABCD-EFGH.',
  );
  await expect(
    card(page).getByRole('link', { name: 'Open Hugging Face' }),
  ).toHaveAttribute(
    'href',
    /^https:\/\/huggingface\.co\/oauth\/device\?user_code=ABCD-EFGH$/,
  );
  await shoot(page, info, 'hf-signing-in');
});

test('signed in: the models this computer can run after, the strongest first', async ({
  page,
}, info) => {
  await onHuggingFace(page, 'signed_in');
  await openModel(page);

  await expect(card(page)).toContainText('Signed in to Hugging Face as ada.');
  const models = card(page).getByRole('group', { name: 'Model to train' });
  await expect(
    models.getByRole('radio', {
      name: /^Qwen3\.8 27B · Apache 2\.0 · Recommended/,
    }),
  ).toBeChecked();
  await expect(
    models.getByRole('radio', { name: /^Qwen3\.5 9B · Apache 2\.0/ }),
  ).toBeVisible();
  await shoot(page, info, 'hf-signed-in');

  await models.getByRole('radio', { name: /^Qwen3\.5 9B/ }).click();
  await expect(
    notice(page, 'Qwen3.5 9B will train on Hugging Face.'),
  ).toBeVisible();

  await c1(page, 'hugging-face', 'small');
  await openModel(page);
  await expect(card(page).getByRole('radio')).toHaveCount(1); // a 12 GB card runs the 9B, not the 27B
  await shoot(page, info, 'hf-small-card');
});

test('the first run shows its quote, with the monthly cap, before anything is uploaded', async ({
  page,
}, info) => {
  await onHuggingFace(page, 'signed_in');
  await c1(page, 'run', 'first');
  await openModel(page);

  await expect(training(page)).toContainText(
    'on Hugging Face is ready to price.',
  );
  await training(page)
    .getByRole('button', { name: 'Review and start…' })
    .click();
  const sheet = page.getByRole('dialog', {
    name: /^Train v\d+ on Hugging Face\?$/,
  });
  await expect(sheet).toContainText('Nvidia A100 (80 GB) · $2.50 an hour');
  await expect(sheet).toContainText(
    /about \$\d+\.\d\d · at most \$\d+\.\d\d \(Hugging Face stops the run there\)/,
  );
  await expect(sheet).toContainText('412 examples');
  await expect(sheet).toContainText(
    'to a private dataset in your account. Deleted once your model is downloaded and checked.',
  );
  await expect(
    sheet.getByRole('combobox', { name: 'Spend at most' }),
  ).toBeVisible();
  await shoot(page, info, 'hf-quote');

  await sheet.getByRole('button', { name: /^Start · about \$/ }).click();
  await expect(
    notice(
      page,
      /^Uploading 412 examples to your private Hugging Face dataset/,
    ),
  ).toBeVisible();
});

test('a run on Hugging Face: its stage, steps, cost so far and Cancel; lost contact is checking, never failed', async ({
  page,
}, info) => {
  await onHuggingFace(page, 'signed_in');

  for (const [state, words] of [
    ['uploading', 'Uploading your examples to Hugging Face'],
    [
      'queued',
      'Hugging Face is waiting for a free Nvidia A100. No charge while it waits.',
    ],
    ['setting-up', 'Setting up training on Hugging Face…'],
    ['training', /on Hugging Face · step 118 of \d+/],
    ['exporting', 'Building the model file on Hugging Face…'],
    ['downloading', /results from Hugging Face…/],
  ] as const) {
    await c1(page, 'run', state);
    await openModel(page);
    await expect(training(page)).toContainText(words);
    await shoot(page, info, `hf-run-${state}`);
    await c1(page, 'hugging-face', 'signed_in');
  }

  await c1(page, 'run', 'lost');
  await openModel(page);
  await expect(training(page)).toContainText(
    'Checking whether Hugging Face is still training…',
  );
  await expect(training(page)).not.toContainText('Failed');
  await expect(
    training(page).getByRole('button', { name: 'Check now' }),
  ).toBeVisible();
  await expect(
    training(page).getByRole('button', { name: 'Cancel the job' }),
  ).toBeVisible();
  await shoot(page, info, 'hf-run-lost');
});

test('a run that needs credit says so; a finished one says its uploads were deleted', async ({
  page,
}, info) => {
  await onHuggingFace(page, 'signed_in');
  await c1(page, 'run', 'no-credit');
  await openModel(page);
  await expect(training(page)).toContainText(
    'Hugging Face needs credit to run training.',
  );
  await shoot(page, info, 'hf-run-no-credit');

  await c1(page, 'hugging-face', 'signed_in');
  await c1(page, 'run', 'done');
  await openModel(page);
  await expect(card(page)).toContainText(
    'its uploaded examples and files are deleted from Hugging Face',
  );
  await shoot(page, info, 'hf-run-done');
});

test('the monthly cap holds a run back in plain words', async ({
  page,
}, info) => {
  await onHuggingFace(page, 'signed_in');
  await c1(page, 'run', 'over-cap');
  await openModel(page);
  await expect(training(page)).toContainText(
    /waits: this month’s Hugging Face spending \(\$18\.40 of \$20\.00\)/,
  );
  await shoot(page, info, 'hf-over-cap');
});

test('Settings › Learning shows the account, the model and the cap', async ({
  page,
}, info) => {
  await onHuggingFace(page, 'signed_in');
  await c1(page, 'run', 'planned');
  await page.goto('/app-v2/settings/learning');
  await expect(
    page.getByText('Signed in to Hugging Face as ada.'),
  ).toBeVisible();
  await expect(
    page.getByRole('combobox', { name: 'Model on Hugging Face' }),
  ).toBeVisible();
  await expect(
    page.getByRole('combobox', { name: 'Monthly spending cap' }),
  ).toHaveValue('20');
  await shoot(page, info, 'hf-settings');
});

test('Monitor shows a run on Hugging Face with its cost so far; its Cancel asks first', async ({
  page,
}, info) => {
  await onHuggingFace(page, 'signed_in');
  await c1(page, 'run', 'training');
  await page.goto('/app-v2/?tab=monitor');
  const row = page
    .getByRole('listitem')
    .filter({ hasText: /^Personal model v\d+/ });
  await expect(
    row.getByText(/^Training on Hugging Face · step 118 of \d+/),
  ).toBeVisible();
  await expect(row.getByText(/^\$\d+\.\d\d so far$/)).toBeVisible();
  await row.evaluate((element) => element.scrollIntoView({ block: 'center' }));
  await shoot(page, info, 'hf-monitor');
  await row
    .getByRole('button', {
      name: /^Cancel the Hugging Face run of personal model v\d+$/,
    })
    .click();
  const ask = page.getByRole('alertdialog', {
    name: 'Cancel this run on Hugging Face?',
  });
  await expect(ask).toContainText(
    /Billing stops now \(\$\d+\.\d\d so far\)\. What it trained so far is lost, and the uploaded examples are deleted from Hugging Face\. Earlier versions are kept\./,
  );
  await shoot(page, info, 'hf-monitor-cancel');
  await ask.getByRole('button', { name: 'Keep running' }).click();
  await expect(ask).toHaveCount(0);
});
