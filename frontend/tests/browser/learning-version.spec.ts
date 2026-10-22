import type { Page, TestInfo } from '@playwright/test';
import { accessibility, assertNoOverflow, expect, test } from './evidence';
import { fixtureHeaders } from './surface-helpers';

// Learning (preview), L2c: the personal model from its examples to a version made ready. The
// fixture flips the start-up flag, fakes the hardware check, stands in for the download, the
// dataset's count and the release maker, and trains with the fake trainer at the 5070's own
// numbers (154 examples, 39 steps of about 28 s). Nothing reaches the network, a GPU, a runtime or
// a provider. Needs more examples, training, preparing, making ready, ready (waiting for its
// comparison), Runs in, couldn't start, waiting for the person with a command, couldn't be
// prepared, a graphics card another app holds, the Docker image, and O49's download title and
// Monitor's Stop notice. Each test puts the profile back.

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
const l2c = (page: Page, action: string, name = '') =>
  fixture(
    page,
    `/__l2c_fixture/learning/${action}${name ? `?name=${name}` : ''}`,
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

async function openModel(page: Page) {
  await page.goto('/app-v2/?tab=learning&view=model');
  await expect(
    page.getByRole('heading', { name: 'Learning', exact: true }),
  ).toBeVisible();
  await expect(page.getByText(GPU).first()).toBeVisible();
}

/** The newest version's card. */
function version(page: Page) {
  return page.getByRole('region', { name: 'Personal model v1' });
}

function training(page: Page) {
  return page.getByRole('region', { name: 'Training' });
}

function facts(page: Page) {
  return page.getByLabel('Personal model', { exact: true });
}

test('the personal model: needs more examples, training, preparing, making ready, ready to compare, Runs in, and the ways out', async ({
  page,
}, info) => {
  test.setTimeout(240_000);
  await turnOn(page);
  await l2c(page, 'setup');

  // Needs more examples: the count so far and how many training needs.
  await l2c(page, 'examples', '37');
  await openModel(page);
  await expect(
    training(page).getByText(/^Needs more examples · 37 of 100/),
  ).toBeVisible();
  await shoot(page, info, 'model-needs-examples');

  // Training with the 5070's numbers: the step, the time left, the pause promise and Stop.
  await l2c(page, 'train');
  await openModel(page);
  await expect(
    training(page).getByText(
      'Training v1 · step 17 of 39 · about 10 min left · pauses when you use Row-Bot',
    ),
  ).toBeVisible();
  await expect(
    training(page).getByRole('progressbar', { name: 'Training progress' }),
  ).toHaveAttribute('max', '39');
  await shoot(page, info, 'model-training');

  // Trained: v1 is prepared to run (the export's steps), then made ready in Ollama.
  await l2c(page, 'finish');
  await l2c(page, 'version', 'exporting');
  await openModel(page);
  await expect(
    version(page).getByText('Preparing v1 to run on this computer…'),
  ).toBeVisible();
  await expect(version(page).getByText(/\(step 2 of 3\)/)).toBeVisible();
  await expect(training(page)).toHaveCount(0); // the version's card says it all
  await shoot(page, info, 'model-exporting');

  await l2c(page, 'version', 'making-ready');
  await openModel(page);
  await expect(
    version(page).getByText('Making v1 ready in Ollama…'),
  ).toBeVisible();
  await shoot(page, info, 'model-making-ready');

  // Ready, and waiting for L2c2's comparison: said plainly; the main model still answers.
  await l2c(page, 'version', 'ready');
  await openModel(page);
  await expect(version(page).getByText('v1 is ready to compare')).toBeVisible();
  await expect(
    version(page).getByText(
      'Trained on 154 examples of your work · runs in Ollama.',
    ),
  ).toBeVisible();
  await expect(
    version(page).getByText(
      'Row-Bot compares it with your main model before asking you to use it. Until then your main model answers everything.',
    ),
  ).toBeVisible();
  await expect(facts(page)).toContainText('Ollama ✓');
  await expect(facts(page)).toContainText('v1 · 2 GB');
  await shoot(page, info, 'model-ready');

  // Runs in: where personal models run; moving v1 makes it ready there.
  await page
    .getByRole('button', { name: 'Change where your personal model runs' })
    .click();
  const sheet = page.getByRole('dialog', {
    name: 'Where your personal model runs',
  });
  // L2x (O75): Row-Bot's own runner is the recommendation on a computer with an NVIDIA card;
  // this profile chose Ollama, which stays a choice.
  await expect(sheet.getByRole('radio', { name: /^Ollama/ })).toBeChecked();
  await expect(
    sheet.getByRole('radio', { name: /^Row-Bot · recommended/ }),
  ).toBeEnabled();
  await shoot(page, info, 'runs-in');
  await sheet.getByRole('radio', { name: /LM Studio/ }).check();
  await sheet.getByRole('button', { name: 'Use LM Studio' }).click();
  await expect(
    notice(page, 'Personal models run in LM Studio. Making v1 ready there.'),
  ).toBeVisible();
  await expect(
    version(page).getByText('Making v1 ready in LM Studio…'),
  ).toBeVisible();

  // Couldn't start in a runtime: the main model is still in use, with a way out.
  await l2c(page, 'version', 'runtime-failed');
  await openModel(page);
  await expect(
    version(page).getByText(
      'v1 couldn’t start in LM Studio. Your main model is still in use.',
    ),
  ).toBeVisible();
  await expect(
    version(page).getByRole('button', { name: 'Try Ollama' }),
  ).toBeVisible();
  await expect(
    version(page).getByRole('button', { name: 'Try again' }),
  ).toBeVisible();
  await shoot(page, info, 'model-runtime-failed');
  await version(page).getByRole('button', { name: 'Try again' }).click();
  await expect(notice(page, 'Making v1 ready in LM Studio.')).toBeVisible();

  // A runtime the person starts: the one command, with Copy and Check again.
  await l2c(page, 'version', 'waiting');
  await openModel(page);
  await expect(
    version(page).getByText('Load v1 in llama.cpp server'),
  ).toBeVisible();
  await expect(
    version(page).getByText(/--jinja --offline --no-webui --reasoning off/),
  ).toBeVisible();
  await expect(
    version(page).getByRole('button', { name: 'Copy command' }),
  ).toBeVisible();
  await shoot(page, info, 'model-waiting-for-you');

  // Couldn't be prepared: what it needs, and Try again.
  await l2c(page, 'version', 'export-failed');
  await openModel(page);
  await expect(
    version(page).getByText(/^v1 couldn’t be prepared to run\./),
  ).toBeVisible();
  await expect(
    version(page).getByText(
      'It needs 9.4 GB free; 6 GB free. What it learned is kept.',
    ),
  ).toBeVisible();
  await shoot(page, info, 'model-export-failed');
});

test('training waits while another app holds the graphics card', async ({
  page,
}, info) => {
  await turnOn(page);
  await l2c(page, 'setup');
  await l2c(page, 'gpu-busy');
  await openModel(page);
  await expect(
    training(page).getByText(
      'Training v1 starts when the graphics card is free.',
    ),
  ).toBeVisible();
  await expect(
    training(page).getByText(
      /^Another app is using the graphics card’s memory/,
    ),
  ).toBeVisible();
  await shoot(page, info, 'model-gpu-busy');
});

test('training waits while other apps hold the computer’s memory (O105)', async ({
  page,
}, info) => {
  await turnOn(page);
  await l2c(page, 'setup');
  await l2c(page, 'memory-busy');
  await openModel(page);
  await expect(
    training(page).getByText(
      'Training v1 starts when this computer has the memory free.',
    ),
  ).toBeVisible();
  await expect(
    training(page).getByText(
      /^Other apps are using this computer’s memory \(RAM and virtual memory\)\. Closing some frees it\./,
    ),
  ).toBeVisible();
  await shoot(page, info, 'model-memory-busy');
});

test('the Docker image says training is unavailable', async ({
  page,
}, info) => {
  await turnOn(page);
  await l2c(page, 'container');
  await page.goto('/app-v2/?tab=learning&view=model');
  await expect(
    page.getByRole('heading', { name: 'Training isn’t available here' }),
  ).toBeVisible();
  await shoot(page, info, 'model-docker');
});

test('O49: the download names both parts with its bytes, and Monitor’s Stop says where it continues', async ({
  page,
}, info) => {
  await turnOn(page);
  await l2b(page, 'state', 'downloading');
  await openModel(page);
  const card = page.getByRole('region', { name: 'Download' });
  await expect(
    card.getByText(
      'Downloading the training engine and Qwen3.5 2B · 1.2 of 6.8 GB',
    ),
  ).toBeVisible();
  await expect(card.getByText('17% · now the training engine')).toBeVisible();
  await shoot(page, info, 'download-title');

  await page.goto('/app-v2/?tab=monitor');
  const row = page.getByRole('listitem').filter({ hasText: 'Training engine' });
  await row
    .getByRole('button', { name: 'Stop downloading Training engine' })
    .click();
  await expect(
    notice(page, /Resume it in Learning › Personal model\.$/),
  ).toBeVisible();
  await shoot(page, info, 'monitor-stop-notice');
});
