import {
  test,
  expect,
  accessibility,
  assertNoOverflow,
  screenshot,
  writeEvidence,
} from './evidence';
import {
  assertConversationMarker,
  openFixture,
  stableConversationMarker,
  type FixtureWindow,
} from './fixture';

test('workspace command shortcut focuses search and ignores IME composition', async ({
  page,
}, testInfo) => {
  await openFixture(page);
  await stableConversationMarker(page);
  await page.evaluate(() =>
    window.dispatchEvent(
      new KeyboardEvent('keydown', {
        key: 'k',
        ctrlKey: true,
        isComposing: true,
        bubbles: true,
      }),
    ),
  );
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await page.keyboard.press('Control+k');
  await expect(
    page.getByRole('dialog', { name: 'Workspace commands', exact: true }),
  ).toBeVisible();
  const search = page.getByRole('searchbox', {
    name: 'Find a workspace command',
    exact: true,
  });
  await expect(search).toBeFocused();
  // Preferences moved from a header dialog to the unified settings route.
  await search.fill('preferences');
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/\/app-v2\/settings\/preferences/);
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(
    page.getByRole('heading', { name: 'Preferences', exact: true }),
  ).toBeVisible();
  await assertConversationMarker(page);
  await screenshot(page, testInfo, 'command-to-preferences');
});

test('workspace commands dialog traps focus, locks background scroll and restores its opener', async ({
  page,
}, testInfo) => {
  await openFixture(page);
  await stableConversationMarker(page);
  const opener = page.getByRole('button', {
    name: 'Workspace commands',
    exact: true,
  });
  await opener.click();
  const dialog = page.getByRole('dialog', {
    name: 'Workspace commands',
    exact: true,
  });
  await expect(dialog).toBeVisible();
  await expect(page.getByRole('dialog')).toHaveCount(1);
  for (let index = 0; index < 30; index += 1) {
    await page.keyboard.press(index % 2 ? 'Shift+Tab' : 'Tab');
    expect(
      await dialog.evaluate((element) =>
        element.contains(document.activeElement),
      ),
    ).toBe(true);
  }
  expect(
    await page.evaluate(() => getComputedStyle(document.body).overflow),
  ).toBe('hidden');
  await accessibility(page, testInfo, 'commands-focus-axe');
  await assertNoOverflow(page);
  await screenshot(page, testInfo, 'commands-focus-scope');
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(opener).toBeFocused();
  await assertConversationMarker(page);
  expect(
    await page.evaluate(() => getComputedStyle(document.body).overflow),
  ).not.toBe('hidden');
});

test('confirmation suspension preserves form draft and Cancel never confirms', async ({
  page,
}, testInfo) => {
  // Reset layout now resets immediately with a notice, so the suspension
  // contract is exercised on the shared task dialog with a confirmation.
  await page.goto('/app-v2/primitives?fixture=normal');
  await page.getByRole('button', { name: 'Open dialog', exact: true }).click();
  const task = page.getByRole('dialog', { name: 'Sample dialog', exact: true });
  const draft = task.getByRole('textbox', {
    name: 'Example name',
    exact: true,
  });
  await draft.fill('Suspended local draft');
  const before = await page.evaluate(() => ({ ...localStorage }));
  const discard = page.getByRole('button', {
    name: 'Discard example',
    exact: true,
  });
  await discard.click();
  const confirmation = page.getByRole('alertdialog', {
    name: 'Discard this example?',
    exact: true,
  });
  await expect(confirmation).toBeVisible();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(page.getByRole('alertdialog')).toHaveCount(1);
  await expect(
    confirmation.getByRole('button', { name: 'Cancel', exact: true }),
  ).toBeFocused();
  await screenshot(page, testInfo, 'confirmation-cancel-default');
  await accessibility(page, testInfo, 'confirmation-axe');
  await page.keyboard.press('Escape');
  await expect(page.getByRole('alertdialog')).toHaveCount(0);
  await expect(task).toBeVisible();
  await expect(draft).toHaveValue('Suspended local draft');
  await expect(discard).toBeFocused();
  expect(await page.evaluate(() => ({ ...localStorage }))).toEqual(before);
  await discard.click();
  await expect(confirmation).toBeVisible();
  await page.mouse.click(2, 2);
  await expect(page.getByRole('alertdialog')).toHaveCount(0);
  await expect(task).toBeVisible();
  await expect(draft).toHaveValue('Suspended local draft');
  await expect(
    page.getByText('Example discarded', { exact: true }),
  ).toHaveCount(0);
  expect(await page.evaluate(() => ({ ...localStorage }))).toEqual(before);
  expect(
    await page.evaluate(
      () =>
        (window as FixtureWindow).__ROW_BOT_FIXTURE__.transport.counters
          .commands,
    ),
  ).toBe(0);
  await writeEvidence(testInfo, 'cancel-keeps-task-and-form', {
    unchangedStorage: true,
    retainedDraft: 'Suspended local draft',
    commands: 0,
  });
});

test('settings aliases route to retained unified settings', async ({
  page,
}, testInfo) => {
  await page.goto('/app-v2/settings/preferences?fixture=normal');
  await page
    .getByRole('searchbox', { name: 'Find a setting', exact: true })
    .fill('gmail');
  const accounts = page
    .getByRole('navigation', { name: 'Settings sections', exact: true })
    .getByRole('link', { name: 'Accounts', exact: true });
  await accounts.click();
  await expect(page).toHaveURL(/\/app-v2\/settings\/accounts/);
  await expect(accounts).toHaveAttribute('aria-current', 'page');
  await expect(
    page.getByRole('heading', { name: 'Accounts', exact: true }),
  ).toBeVisible();
  await assertNoOverflow(page);
  await screenshot(page, testInfo, 'accounts-setting-retained');
});
