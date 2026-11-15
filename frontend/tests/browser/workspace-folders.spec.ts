import type { Locator, Page, TestInfo } from '@playwright/test';
import { assertNoOverflow, expect, screenshot, test } from './evidence';
import { fixtureHeaders } from './surface-helpers';
import {
  composer,
  headerAction,
  newConversation,
  openAddResource,
} from './unified-helpers';

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
  await expect(settle).toPass();
  await views(page, info, name);
  await page.setViewportSize(PHONE);
  await expect(settle).toPass();
  await views(page, info, `${name}-phone`);
  await page.setViewportSize(DESKTOP);
  await expect(settle).toPass();
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
  // A new conversation starts on Auto, which clones without asking; under Ask the download is confirmed.
  await page.getByRole('button', { name: /^Approvals: / }).click();
  await page.getByRole('menuitem', { name: /^Ask/ }).click();
  await expect(
    page.getByRole('button', { name: 'Approvals: Ask' }),
  ).toBeVisible();
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
  // Choosing Ask is remembered for new conversations: later specs start on Auto again.
  const reset = await page.request.post('/__a0_fixture/full-autonomy/reset', {
    headers: fixtureHeaders(),
  });
  expect(reset.ok()).toBe(true);
});

/** The details card on screen: the rail on a desktop, its sheet on a phone. */
async function detailsCard(page: Page) {
  const rail = page.getByRole('complementary', {
    name: 'Conversation details',
  });
  const sheet = page.getByRole('dialog', { name: 'Conversation details' });
  const card = rail.or(sheet).filter({ visible: true }).first();
  // A phone shows the code folder's panel over the chat: go back to it first.
  const back = page.getByRole('button', {
    name: 'Back to conversation',
    exact: true,
  });
  await expect(async () => {
    if (!(await rail.isVisible()) && !(await sheet.isVisible())) {
      if (await back.isVisible()) await back.click();
      await headerAction(page, 'Conversation details');
      await expect(card).toBeVisible({ timeout: 10_000 });
    }
    await expect(card.getByText(/^Project notes:/)).toBeVisible({
      timeout: 5_000,
    });
  }).toPass({ timeout: 45_000 });
  return card;
}

/** The project notes popover, opened from the card on screen. */
async function notesPopover(page: Page) {
  const popover = page.getByRole('dialog', { name: 'Project notes' });
  if (!(await popover.isVisible())) {
    const card = await detailsCard(page);
    await card.getByRole('button', { name: 'About project notes' }).click();
  }
  await expect(popover).toBeVisible();
  return popover;
}

/** The card's line, then its popover, on a desktop and a phone, light and dark. */
async function notesViews(
  page: Page,
  info: TestInfo,
  name: string,
  check: (popover: Locator) => Promise<void>,
) {
  const popover = page.getByRole('dialog', { name: 'Project notes' });
  for (const [size, suffix] of [
    [DESKTOP, ''],
    [PHONE, '-phone'],
  ] as const) {
    await page.setViewportSize(size);
    if (await popover.isVisible()) await page.keyboard.press('Escape');
    await expect(popover).toBeHidden();
    const card = await detailsCard(page);
    await card.getByText(/^Project notes:/).scrollIntoViewIfNeeded();
    await views(page, info, `${name}-card${suffix}`);
    await expect(async () => check(await notesPopover(page))).toPass();
    await views(page, info, `${name}${suffix}`);
  }
  await page.keyboard.press('Escape');
  await page.setViewportSize(DESKTOP);
}

test('the details card names the folder’s project notes, what was cut and what was left out', async ({
  page,
}, info) => {
  test.setTimeout(180_000);
  const conversation = await newConversation(page);
  const dialog = await codeFolderSetup(page, 'draft_folder');
  await dialog
    .getByRole('textbox', { name: 'Name (optional)', exact: true })
    .fill('tide-notes');
  await dialog
    .getByRole('button', { name: 'Create draft code folder', exact: true })
    .click();
  await expect(dialog.getByText('Code folder ready')).toBeVisible();
  await page.keyboard.press('Escape');

  const notes = async (leftOut: boolean) => {
    const written = await page.request.post('/__s6_fixture/project-notes', {
      headers: fixtureHeaders(),
      data: { conversation_id: conversation, left_out: leftOut },
    });
    expect(written.ok()).toBe(true);
    await page.reload();
    await expect(composer(page)).toBeVisible();
  };

  await notes(false);
  await expect(await detailsCard(page)).toContainText(
    'Project notes: AGENTS.md, .cursorrules',
  );
  await notesViews(page, info, 'project-notes-used', async (popover) => {
    await expect(popover).toContainText(
      /AGENTS\.mdUsed in part: the first [\d,]+ of [\d,]+ characters/,
    );
    await expect(popover).toContainText('.cursorrulesUsed');
  });

  await notes(true);
  await expect(await detailsCard(page)).toContainText(
    'Project notes: AGENTS.md, .cursorrules · CLAUDE.md left out',
  );
  await notesViews(page, info, 'project-notes-left-out', async (popover) => {
    await expect(popover).toContainText(
      'CLAUDE.mdNot used: it tries to give the agent new instructions',
    );
  });

  // Open file shows it in the code folder's Files tab.
  const popover = await notesPopover(page);
  await popover.getByRole('button', { name: 'Open AGENTS.md' }).click();
  const file = page.getByRole('region', { name: 'File text' });
  await expect(file).toContainText(
    'Rule 1: run `make test` before handing off.',
  );
});
