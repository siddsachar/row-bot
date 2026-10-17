import type { Locator, Page, TestInfo } from '@playwright/test';
import { assertNoOverflow, expect, screenshot, test } from './evidence';
import { fixtureHeaders } from './surface-helpers';
import { newConversation, openAddResource } from './unified-helpers';

/*
 * F19 · Attach a code folder without the native picker. Plain Chromium has
 * no folder picker for the computer Row-Bot runs on, so Add resource offers
 * Row-Bot's workspace folder, folder by folder. The workspace folder is a
 * disposable one; a clone is a fake Git that makes the folder (nothing
 * reaches the network). Every listing and choice is checked by the server.
 */

const PHONE = { width: 390, height: 844 };
const DESKTOP = { width: 1280, height: 900 };

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
  await settle();
}

/**
 * One way out (the sheet's ×, no footer Close or extra Cancel beside it)
 * and the primary action in view, in the footer, at any width.
 */
async function oneWayOut(dialog: Locator, primary: string) {
  await expect(
    dialog.getByRole('button', { name: 'Close', exact: true }),
  ).toHaveCount(0);
  await expect(
    dialog.getByRole('button', { name: 'Close dialog', exact: true }),
  ).toBeVisible();
  const action = dialog
    .locator('.dialog-footer')
    .getByRole('button', { name: primary, exact: true });
  await expect(action).toBeInViewport({ ratio: 1 });
}

async function codeFolderSetup(page: Page, mode: string) {
  await openAddResource(page);
  const dialog = page.getByRole('dialog', {
    name: 'Add resource',
    exact: true,
  });
  await dialog.getByRole('radio', { name: 'Code folder', exact: true }).click();
  await dialog
    .getByRole('combobox', { name: 'Folder setup', exact: true })
    .selectOption(mode);
  return dialog;
}

test.beforeEach(async ({ page }) => {
  await page.setViewportSize(DESKTOP);
  const response = await page.request.post('/__s6_fixture/workspace', {
    headers: fixtureHeaders(),
  });
  expect(response.ok()).toBe(true);
});

test.afterEach(async ({ page }) => {
  const response = await page.request.post('/__s6_fixture/reset', {
    headers: fixtureHeaders(),
  });
  expect(response.ok()).toBe(true);
});

test('a plain browser registers a folder chosen in Row-Bot’s workspace folder', async ({
  page,
}, info) => {
  // The server refuses the folder that became a link out: one 403, on purpose.
  test.info().annotations.push({
    type: 'expected-console-error',
    description: JSON.stringify({
      owner: 'workspace-folders',
      fixture: 'Projects/notes swapped for a link out of the workspace folder',
      signature:
        'Failed to load resource: the server responded with a status of 403 (Forbidden)',
      count: 1,
    }),
  });
  await newConversation(page);
  const dialog = await codeFolderSetup(page, 'existing_folder');
  await expect(dialog).toContainText(
    'Choose a folder in Row-Bot’s workspace folder, on the computer Row-Bot runs on.',
  );
  await dialog
    .getByRole('button', { name: 'Choose existing folder', exact: true })
    .click();
  const browser = dialog.getByRole('region', { name: 'Choose a folder' });
  await expect(
    browser.getByRole('list', { name: 'Folders in Row-Bot' }),
  ).toBeVisible();
  // Folders only, and no path to type.
  await expect(browser.getByRole('textbox')).toHaveCount(0);
  await expect(
    dialog.getByRole('button', { name: 'Use Row-Bot', exact: true }),
  ).toBeDisabled();
  await browser.getByRole('button', { name: 'Projects', exact: true }).click();
  await expect(
    browser.getByRole('button', {
      name: 'tide-app, Git repository',
      exact: true,
    }),
  ).toBeVisible();
  await everywhere(page, info, 'folder-browser', async () => {
    await browser.scrollIntoViewIfNeeded();
    await oneWayOut(dialog, 'Use Projects');
  });

  // An empty folder says so, and can still be used.
  await browser.getByRole('button', { name: 'notes', exact: true }).click();
  await expect(browser).toContainText(
    'No folders inside notes. Use it, or go back.',
  );
  await everywhere(page, info, 'folder-browser-empty', async () => {
    await browser.scrollIntoViewIfNeeded();
    await oneWayOut(dialog, 'Use notes');
  });

  // A folder swapped for a link out of the workspace folder is refused.
  await browser
    .getByRole('navigation', { name: 'Folder path' })
    .getByRole('button', { name: 'Projects', exact: true })
    .click();
  // Listed while it was a folder; then it becomes a link out.
  await expect(
    browser.getByRole('button', { name: 'notes', exact: true }),
  ).toBeVisible();
  const linked = await page.request.post('/__s6_fixture/link-out', {
    headers: fixtureHeaders(),
  });
  expect(linked.ok()).toBe(true);
  await browser.getByRole('button', { name: 'notes', exact: true }).click();
  await expect(browser.getByRole('alert')).toContainText(
    "That folder is outside Row-Bot's workspace folder, so it can't be used.",
  );
  await everywhere(page, info, 'folder-browser-refused', async () => {
    await expect(
      dialog.getByRole('button', { name: 'Close', exact: true }),
    ).toHaveCount(0);
    await browser.getByRole('alert').scrollIntoViewIfNeeded();
  });
  await browser
    .getByRole('button', { name: 'Back to Row-Bot', exact: true })
    .click();

  await browser.getByRole('button', { name: 'Projects', exact: true }).click();
  await browser
    .getByRole('button', { name: 'tide-app, Git repository', exact: true })
    .click();
  await dialog
    .getByRole('button', { name: 'Use tide-app', exact: true })
    .click();
  // Chosen: the footer's Close is back, and the form's own submit.
  await expect(
    dialog.getByText('Selected: Row-Bot › Projects › tide-app'),
  ).toBeVisible();
  await dialog
    .getByRole('button', { name: 'Register folder', exact: true })
    .click();
  await expect(dialog.getByText('Code folder ready')).toBeVisible();
});

test('Clone from URL clones into Row-Bot’s workspace folder after the download is confirmed', async ({
  page,
}, info) => {
  await newConversation(page);
  const dialog = await codeFolderSetup(page, 'clone_repository');
  await dialog
    .getByRole('textbox', { name: 'Repository URL', exact: true })
    .fill('https://example.test/team/demo.git');
  await dialog
    .getByRole('button', { name: 'Choose parent folder', exact: true })
    .click();
  await dialog
    .getByRole('button', { name: 'Use Row-Bot', exact: true })
    .click();
  await expect(dialog.getByText('Selected: Row-Bot')).toBeVisible();
  await dialog
    .getByRole('button', { name: 'Clone repository', exact: true })
    .click();
  const confirm = dialog.getByRole('group', { name: 'Confirm the download' });
  await expect(confirm).toContainText(
    "Clone demo? Row-Bot downloads it from example.test into Row-Bot, with this computer's own Git sign-in.",
  );
  await everywhere(page, info, 'clone-from-url', async () => {
    await oneWayOut(dialog, 'Clone');
    await confirm.scrollIntoViewIfNeeded();
  });
  await dialog
    .locator('.dialog-footer')
    .getByRole('button', { name: 'Clone', exact: true })
    .click();
  await expect(dialog.getByText('Code folder ready')).toBeVisible();
  const state = await page.request.get('/__s6_fixture/workspace', {
    headers: fixtureHeaders(),
  });
  expect((await state.json()).folders).toContain('demo');
});
