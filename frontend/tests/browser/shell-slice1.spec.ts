import { expect, screenshot, test } from './evidence';
import { openFixture } from './fixture';

test('simplified shell keeps controls and Home reachable', async ({
  page,
}, info) => {
  await openFixture(page);
  const compact = page.viewportSize()!.width < 1024;
  const phone = page.viewportSize()!.width < 768;
  await expect(page.locator('.app-header')).toHaveCount(0);
  // A conversation is one header row at every width: no separate bar.
  await expect(page.locator('.compact-controls')).toHaveCount(0);
  if (phone) {
    // Phones fold search and the panels into the header's menu.
    await page
      .getByRole('button', { name: 'Conversation menu', exact: true })
      .click();
    await expect(
      page.getByRole('menuitem', { name: 'Workspace commands' }),
    ).toBeVisible();
    await expect(
      page.getByRole('menuitem', { name: 'Open Workspace notes' }),
    ).toBeVisible();
    await page.keyboard.press('Escape');
  } else {
    await expect(
      page.getByRole('button', { name: 'Open panel' }),
    ).toBeVisible();
    await expect(
      page.getByRole('button', { name: 'Workspace commands' }),
    ).toBeVisible();
  }

  if (compact) {
    await screenshot(page, info, 'slice1-compact-conversation');
    await page.getByRole('button', { name: 'Toggle navigation' }).click();
  } else {
    await page.getByRole('button', { name: 'Toggle navigation' }).click();
    await expect(
      page.getByRole('button', { name: 'Open panel' }),
    ).toBeVisible();
    await page.getByRole('button', { name: 'Expand navigation' }).click();
  }
  const nav = page.getByRole('navigation', { name: 'Workspace navigation' });
  await expect(nav).toBeVisible();
  await expect(
    nav.getByRole('combobox', { name: 'Conversation group' }),
  ).toHaveCount(0);
  await expect(
    nav.getByRole('list', { name: 'Recent conversations' }),
  ).toBeVisible();
  await expect(nav.getByRole('button', { name: 'New chat' })).toBeVisible();
  await screenshot(page, info, 'slice1-conversation-shell');

  await nav.getByRole('link', { name: 'Home' }).click();
  await expect(page).toHaveURL(/\/app-v2\/?$/);
  await expect(
    page.getByRole('tablist', { name: 'Home capabilities' }),
  ).toBeVisible();
  await expect(page.getByRole('region', { name: 'Start working' })).toHaveCount(
    0,
  );
  await expect(
    page.getByRole('region', { name: 'Recent conversations' }),
  ).toHaveCount(0);
  await expect(
    page.getByRole('region', { name: 'Start with an example' }),
  ).toHaveCount(0);
  await page.mouse.move(
    page.viewportSize()!.width - 4,
    page.viewportSize()!.height - 4,
  );
  await page.evaluate(() => (document.activeElement as HTMLElement)?.blur());
  await screenshot(page, info, 'slice1-home');

  await page.keyboard.press('Control+k');
  await expect(
    page.getByRole('dialog', { name: 'Workspace commands' }),
  ).toBeVisible();
});
