import { expect, screenshot, test } from './evidence';
import { homeOpened } from './phone-helpers';
import { openFixture } from './fixture';
import { openPanel } from './panel-helpers';
import { commandsDialog } from './unified-helpers';

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
    // Phones fold the panels into the header's menu (Search lives in the
    // Chats tab, D15), and go Home by ‹ and the tab bar: no drawer (D1).
    await page
      .getByRole('button', { name: 'Conversation menu', exact: true })
      .click();
    await expect(page.getByRole('menuitem', { name: 'Search' })).toHaveCount(0);
    await expect(
      page.getByRole('menuitem', { name: 'Open Workspace notes' }),
    ).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(
      page.getByRole('button', { name: 'Toggle navigation' }),
    ).toHaveCount(0);
    await screenshot(page, info, 'slice1-compact-conversation');
    await page.getByRole('button', { name: 'Back', exact: true }).click();
    await page
      .getByRole('navigation', { name: 'Main' })
      .getByRole('link', { name: 'Home' })
      .click();
  } else {
    await expect(
      page.getByRole('button', { name: 'Open panel' }),
    ).toBeVisible();
    await expect(
      page.getByRole('button', { name: 'Workspace commands' }),
    ).toBeVisible();
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
    await expect(
      nav.getByRole('button', { name: 'New chat', exact: true }),
    ).toBeVisible();
    await screenshot(page, info, 'slice1-conversation-shell');
    await nav.getByRole('link', { name: 'Home' }).click();
  }
  await expect(page).toHaveURL(/\/app-v2\/?$/);
  await expect(homeOpened(page)).toBeVisible();
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
  await expect(commandsDialog(page).dialog).toBeVisible();
});

test('the sidebar comes back as it was after a phone-width window (B340)', async ({
  page,
}, info) => {
  test.skip(
    page.viewportSize()!.width < 1024,
    'Starts and ends at a desktop width.',
  );
  const desktop = page.viewportSize()!;
  await openFixture(page);
  const pane = page.locator('.navigation-pane');
  const expanded = page.getByRole('navigation', {
    name: 'Workspace navigation',
  });
  const rail = page.getByRole('navigation', { name: 'Collapsed navigation' });
  await expect(expanded).toBeVisible();
  const width = async () => Math.round((await pane.boundingBox())?.width ?? 0);
  const before = await width();
  expect(before).toBeGreaterThanOrEqual(200);

  // Snapped narrow and back: the full sidebar, at the width it had.
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(expanded).toBeHidden();
  await page.setViewportSize(desktop);
  await expect(expanded).toBeVisible();
  await expect.poll(width).toBe(before);
  await expect(rail).toHaveCount(0);
  await screenshot(page, info, 'b340-desktop-after-phone');

  // Collapsed by the person, it comes back as the icon rail.
  await page.getByRole('button', { name: 'Toggle navigation' }).click();
  await expect(rail).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(rail).toBeHidden();
  await page.setViewportSize(desktop);
  await expect(rail).toBeVisible();
  await expect(expanded).toHaveCount(0);
  await expect.poll(width).toBe(48);
  await screenshot(page, info, 'b340-rail-after-phone');
  await page.getByRole('button', { name: 'Expand navigation' }).click();
  await expect(expanded).toBeVisible();
  await expect.poll(width).toBe(before);

  // An open side panel comes back open too.
  await openPanel(page);
  const side = page.locator('#side-pane');
  const sideWidth = async () =>
    Math.round((await side.boundingBox())?.width ?? 0);
  const sideBefore = await sideWidth();
  expect(sideBefore).toBeGreaterThanOrEqual(320);
  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(sideWidth).toBe(0);
  await page.setViewportSize(desktop);
  await expect.poll(sideWidth).toBe(sideBefore);
  await expect.poll(width).toBe(before);
});
