import type { Page, TestInfo } from '@playwright/test';
import { assertNoOverflow, expect, screenshot, test } from './evidence';
import { fixtureHeaders } from './surface-helpers';
import { composer, newConversation, openAddResource } from './unified-helpers';

/*
 * F18 · Prepare sandbox. The conversation's code folder is a Python project
 * in the Docker sandbox; the fixture scripts a test run there that couldn't
 * start because pytest isn't installed. The offer, the person's Prepare, its
 * job and card, the network's record and the sandbox controls are real;
 * Docker is a fake whose install takes until the fixture releases it, so
 * nothing is pulled or installed.
 */

const PHONE = { width: 390, height: 844 };
const DESKTOP = { width: 1280, height: 900 };

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
  await settle();
}

/** A new chat with a new draft code folder, made a Docker-sandbox Python project by the fixture. */
async function sandboxChat(page: Page, imagePresent = true) {
  const conversation = await newConversation(page);
  await openAddResource(page);
  const dialog = page.getByRole('dialog', {
    name: 'Add resource',
    exact: true,
  });
  await dialog.getByRole('radio', { name: 'Code folder', exact: true }).click();
  await dialog
    .getByRole('combobox', { name: 'Folder setup', exact: true })
    .selectOption('draft_folder');
  await dialog
    .getByRole('textbox', { name: 'Name (optional)', exact: true })
    .fill('tide-app');
  await dialog
    .getByRole('button', { name: 'Create draft code folder', exact: true })
    .click();
  await expect(dialog.getByText('Code folder ready')).toBeVisible();
  await page.keyboard.press('Escape');
  const prepared = await page.request.post('/__s6_fixture/sandbox', {
    headers: fixtureHeaders(),
    data: { conversation_id: conversation, image_present: imagePresent },
  });
  expect(prepared.ok()).toBe(true);
  // The fixture changed the code folder's settings: read them again.
  await page.reload();
  await expect(composer(page)).toBeVisible();
  return conversation;
}

test.beforeEach(async ({ page }) => {
  await page.setViewportSize(DESKTOP);
});

test.afterEach(async ({ page }) => {
  const response = await page.request.post('/__s6_fixture/reset', {
    headers: fixtureHeaders(),
  });
  expect(response.ok()).toBe(true);
});

test('a test run that lacked pytest offers Prepare sandbox, which installs with the network on only for that', async ({
  page,
}, info) => {
  test.setTimeout(180_000);
  await sandboxChat(page);
  // The chat, without the code folder's panel beside it.
  const close = page.getByRole('button', { name: 'Close panel', exact: true });
  if (await close.first().isVisible()) await close.first().click();
  await composer(page).fill('s6 run the tests fixture');
  await page.getByRole('button', { name: 'Send', exact: true }).click();

  const transcript = page.getByRole('log', { name: 'Conversation' });
  const offer = transcript.getByRole('group', { name: "Tests couldn't run" });
  await expect(offer).toContainText(
    "Tests couldn't run: pytest isn't installed in the sandbox",
  );
  await expect(offer).toContainText(
    "The sandbox's network is off; it's on only while they install, then off again.",
  );
  // A phone shows the chat, not the code folder's panel over it.
  const chat = async () => {
    const back = page.getByRole('button', {
      name: 'Back to conversation',
      exact: true,
    });
    if (await back.isVisible()) await back.click();
  };
  await everywhere(page, info, 'prepare-offer', async () => {
    await chat();
    await offer.scrollIntoViewIfNeeded();
  });

  await offer
    .getByRole('button', { name: 'Prepare sandbox', exact: true })
    .click();
  const confirm = offer.getByRole('group', { name: 'Confirm Prepare sandbox' });
  await expect(confirm).toContainText(
    'Prepare the sandbox? Row-Bot turns the sandbox network on for the install only, installs pytest and the project with its test extra (from pyproject.toml), then turns the network off again.',
  );
  await everywhere(page, info, 'prepare-confirm', async () => {
    await chat();
    await confirm.scrollIntoViewIfNeeded();
  });
  await confirm
    .getByRole('button', { name: 'Prepare sandbox', exact: true })
    .click();

  const running = transcript.getByRole('group', {
    name: 'Preparing the sandbox: Installing',
  });
  await expect(running).toContainText('Network on for this install');
  // The fake install waits on this line until the fixture releases it.
  await expect(running).toContainText(
    'Downloading pytest-8.3.3-py3-none-any.whl (342 kB)',
  );
  await expect(running.getByRole('button', { name: 'Stop' })).toBeVisible();
  await everywhere(page, info, 'prepare-in-progress', async () => {
    await chat();
    await running.scrollIntoViewIfNeeded();
  });

  const released = await page.request.post('/__s6_fixture/sandbox/release', {
    headers: fixtureHeaders(),
  });
  expect(released.ok()).toBe(true);
  const ready = transcript.getByRole('group', {
    name: 'Sandbox ready for tests: Ready',
  });
  await expect(ready).toContainText('Network off again', { timeout: 20_000 });
  await expect(ready).toContainText(
    'Installed pytest and the project with its test extra. The tests can run now.',
  );
  await ready.locator('summary').filter({ hasText: 'Details' }).click();
  await expect(ready).toContainText(
    /Network\s*On at .*, off again at .* \(for the install only\)/,
  );
  await everywhere(page, info, 'network-back-off', async () => {
    await chat();
    await ready.scrollIntoViewIfNeeded();
  });
});

test('Docker mode offers to download the image, and Safety boundaries explains the network', async ({
  page,
}, info) => {
  test.setTimeout(180_000);
  await sandboxChat(page, false);
  const inspector = page.getByRole('region', { name: / inspector$/ });
  await expect(inspector).toBeVisible();
  await inspector
    .getByRole('button', { name: 'Refresh inspector', exact: true })
    .click();
  await expect(inspector).toContainText('Docker sandbox · network off');

  const safety = async () => {
    await page
      .getByRole('region', { name: / inspector$/ })
      .getByRole('button', { name: 'Safety boundaries', exact: true })
      .click();
    const popover = page.getByRole('dialog', { name: 'Safety boundaries' });
    await expect(popover).toContainText(
      'The sandbox’s network is off, as it is by default: commands there can’t reach the internet.',
    );
    await expect(popover).toContainText(
      'Prepare sandbox turns it on only while it installs the project’s test tools',
    );
  };
  await safety();
  await views(page, info, 'safety-boundaries');
  await page.keyboard.press('Escape');

  // The Sandbox section is in Git › Advanced; a narrow layout opens the panel afresh.
  const sandboxSection = async () => {
    const panel = page.getByRole('region', { name: / inspector$/ });
    await panel.getByRole('tab', { name: /^Git/ }).click();
    const controls = page.getByRole('region', {
      name: 'Developer repository controls',
      exact: true,
    });
    await expect(controls).toBeVisible();
    const advanced = controls
      .locator('summary')
      .filter({ hasText: 'Advanced' });
    if ((await advanced.locator('xpath=..').getAttribute('open')) === null)
      await advanced.click();
    return controls;
  };
  const controls = await sandboxSection();
  const offer = controls.getByRole('group', {
    name: 'Sandbox image',
    exact: true,
  });
  await expect(offer).toContainText('Download the sandbox image?');
  await expect(offer).toContainText('about 570 MB from Docker Hub');
  await everywhere(page, info, 'image-pull-offer', async () => {
    await sandboxSection();
    await page
      .getByRole('group', { name: 'Sandbox image', exact: true })
      .scrollIntoViewIfNeeded();
  });
  await offer
    .getByRole('button', { name: 'Download image', exact: true })
    .click();
  const downloaded = controls.getByRole('group', {
    name: 'Sandbox image downloaded: Done',
  });
  await expect(downloaded).toBeVisible({ timeout: 20_000 });
  await expect(
    controls.getByRole('group', { name: 'Sandbox image', exact: true }),
  ).toHaveCount(0);

  await page.setViewportSize(PHONE);
  await expect(page.getByRole('region', { name: / inspector$/ })).toBeVisible();
  await safety();
  await views(page, info, 'safety-boundaries-phone');
  await page.keyboard.press('Escape');
  await page.setViewportSize(DESKTOP);
});
