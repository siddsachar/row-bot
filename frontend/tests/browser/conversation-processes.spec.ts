import type { Page, TestInfo } from '@playwright/test';
import { assertNoOverflow, expect, screenshot, test } from './evidence';
import { fixtureHeaders } from './surface-helpers';
import { composer, newConversation } from './unified-helpers';

/*
 * F1 · F6 · background processes. The fixture scripts the model; the real
 * start_process and run_command tools, the process manager and small
 * synthetic programs (a dev server, failing tests, a build) run on the
 * fixture's computer. Start asks first; the conversation follows the process
 * live (View output, Stop); Home › Monitor lists every one with Stop.
 * (Named to run after bootstrap.spec.ts, which needs a backend with no turns.)
 */

const PHONE = { width: 390, height: 844 };
const DESKTOP = { width: 1280, height: 900 };

async function send(page: Page, text: string) {
  await composer(page).fill(text);
  await page.getByRole('button', { name: 'Send', exact: true }).click();
}

/** Evidence in light and dark at the current size. */
async function views(page: Page, info: TestInfo, name: string) {
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    await expect(page.locator('html')).toHaveAttribute(
      'data-theme',
      appearance,
    );
    await assertNoOverflow(page);
    await screenshot(page, info, `${name}-${appearance}`);
  }
  await page.emulateMedia({ colorScheme: 'light' });
}

/** Desktop and phone, light and dark. */
async function everywhere(
  page: Page,
  info: TestInfo,
  name: string,
  settle: () => Promise<void> = async () => {},
) {
  await settle();
  await views(page, info, name);
  await page.setViewportSize(PHONE);
  await settle();
  await views(page, info, `${name}-phone`);
  await page.setViewportSize(DESKTOP);
}

async function stopEverything(page: Page) {
  const response = await page.request.post('/__s2_fixture/processes/stop-all', {
    headers: fixtureHeaders(),
  });
  expect(response.ok()).toBe(true);
}

test.afterEach(async ({ page }) => {
  await stopEverything(page);
});

test('starting a process asks first, saying what runs where', async ({
  page,
}, info) => {
  await page.setViewportSize(DESKTOP);
  await newConversation(page);
  await send(page, 's2 process approval fixture');

  const card = page.getByRole('complementary', {
    name: /Approval required for/,
  });
  await expect(card).toContainText('Start a background process?');
  await expect(card).toContainText('python dev_server.py');
  await expect(card).toContainText('PORT=5199');
  await expect(card).toContainText("until it's stopped or Row-Bot quits");
  await everywhere(page, info, 'process-approval', () =>
    card.scrollIntoViewIfNeeded(),
  );
  await card.getByRole('button', { name: 'Deny', exact: true }).click();
});

test('a process runs in the background: view its output, then stop it', async ({
  page,
}, info) => {
  await page.setViewportSize(DESKTOP);
  const conversation = await newConversation(page);
  await send(page, 's2 background process fixture');

  const transcript = page.getByRole('log', { name: 'Conversation' });
  const card = transcript.getByRole('group', {
    name: /^Background process Dev server: Running/,
  });
  await expect(card).toBeVisible();
  await expect(card).toContainText('localhost:5199');
  await expect(
    transcript.getByText('The dev server is running at http://localhost:5199.'),
  ).toBeVisible();
  await everywhere(page, info, 'process-running', () =>
    card.scrollIntoViewIfNeeded(),
  );

  await card.getByRole('button', { name: 'View output', exact: true }).click();
  const output = page.getByRole('dialog', { name: 'Dev server' });
  const log = output.getByRole('log', { name: 'Output' });
  await expect(log).toContainText('Local:   http://localhost:5199/');
  await expect(log).toContainText('GET /assets/app.js 200');
  await expect(output).toContainText('python dev_server.py');
  await everywhere(page, info, 'process-output');
  await output.getByRole('button', { name: 'Close output' }).click();

  // Every process at once, in Monitor, with its conversation and a Stop.
  await page.goto('/app-v2/?tab=monitor');
  const section = page.getByRole('region', { name: 'Background processes' });
  const running = section.getByRole('list', {
    name: 'Running background processes',
  });
  await expect(running.getByRole('listitem')).toHaveCount(1);
  await expect(running).toContainText('Dev server');
  await expect(running).toContainText('localhost:5199');
  await everywhere(page, info, 'processes-monitor', () =>
    section.scrollIntoViewIfNeeded(),
  );

  await running.getByRole('button', { name: 'Stop Dev server' }).click();
  await expect(section).toContainText('Nothing is running in the background');
  await expect(
    section.locator('summary', { hasText: 'Recently finished' }),
  ).toBeVisible();

  // The conversation's card settles on how it ended.
  await page.goto(`/app-v2/conversations/${conversation}`);
  const stopped = page
    .getByRole('log', { name: 'Conversation' })
    .getByRole('group', {
      name: /^Background process Dev server: Stopped by you after/,
    });
  await expect(stopped).toBeVisible();
  await expect(stopped.getByRole('button', { name: 'Stop' })).toHaveCount(0);
  await everywhere(page, info, 'process-stopped', () =>
    stopped.scrollIntoViewIfNeeded(),
  );
});

test('a failed process and a long command that finishes in the background', async ({
  page,
}, info) => {
  await page.setViewportSize(DESKTOP);
  await newConversation(page);
  const transcript = page.getByRole('log', { name: 'Conversation' });

  await send(page, 's2 failed process fixture');
  const failed = transcript.getByRole('group', {
    name: /^Background process Unit tests: Failed after/,
  });
  await expect(failed).toBeVisible();
  await expect(failed).not.toContainText('exit code');
  await expect(
    transcript.getByText('The tests failed: one assertion in test_login.py.'),
  ).toBeVisible();
  await failed
    .getByRole('button', { name: 'View output', exact: true })
    .click();
  const output = page.getByRole('dialog', { name: 'Unit tests' });
  await expect(output.getByRole('log')).toContainText('1 failed, 23 passed');
  await output.getByText('Details', { exact: true }).click();
  await expect(output).toContainText('Exit code');
  await everywhere(page, info, 'process-failed-output');
  await output.getByRole('button', { name: 'Close output' }).click();

  await send(page, 's2 long command process fixture');
  const build = transcript.getByRole('group', {
    name: /^Background process python build\.py: Running/,
  });
  await expect(build).toBeVisible();
  await expect(
    transcript.getByRole('group', {
      name: /^Background process python build\.py: Finished in/,
    }),
  ).toBeVisible({ timeout: 30_000 });
  await everywhere(page, info, 'process-finished-and-failed', () =>
    failed.scrollIntoViewIfNeeded(),
  );
});
