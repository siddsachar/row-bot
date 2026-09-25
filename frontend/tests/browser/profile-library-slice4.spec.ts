import { expect, screenshot, test, assertNoOverflow } from './evidence';
import { openFixture } from './fixture';

test('profile library manages profiles and starts a selected chat', async ({
  page,
}, info) => {
  await openFixture(page);
  if (page.viewportSize()!.width < 1024)
    await page.getByRole('button', { name: 'Toggle navigation' }).click();
  const navigation = page.getByRole('navigation', {
    name: 'Workspace navigation',
  });
  const entry = navigation.getByRole('button', {
    name: /Agent profiles.*1 built-in.*0 custom/,
  });
  await expect(entry).toBeVisible();
  await entry.click();
  const dialog = page.getByRole('dialog', { name: 'Agent profiles' });
  await expect(
    dialog.locator('summary').filter({ hasText: 'Everyday' }),
  ).toBeVisible();
  await expect(dialog.getByText('General Assistant')).toBeVisible();
  await screenshot(page, info, 'slice4-profile-library');
  await assertNoOverflow(page);

  await dialog.getByRole('button', { name: 'View General Assistant' }).click();
  await expect(
    dialog.getByRole('region', { name: 'Profile details' }),
  ).toBeVisible();
  await dialog.getByRole('button', { name: 'Close details' }).click();
  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);
  if (page.viewportSize()!.width < 1024) {
    const toggle = page.getByRole('button', { name: 'Toggle navigation' });
    await expect(toggle).toBeFocused();
    await toggle.click();
  } else await expect(entry).toBeFocused();
  await entry.click();
  await dialog
    .getByRole('button', { name: 'Duplicate General Assistant' })
    .click();
  await expect(
    dialog.getByRole('group', { name: 'Duplicate profile' }),
  ).toBeVisible();
  await dialog
    .getByRole('button', { name: 'Duplicate profile', exact: true })
    .click();
  await dialog.locator('summary').filter({ hasText: 'My Profiles' }).click();
  await expect(
    dialog.getByRole('button', { name: 'View General Assistant Copy' }),
  ).toBeVisible();

  await dialog
    .getByRole('button', { name: 'Create profile', exact: true })
    .click();
  await dialog.getByLabel('Profile slug').fill('fixture_researcher');
  await dialog.getByLabel('Profile display name').fill('Fixture Researcher');
  await dialog.getByLabel('Description').fill('A local browser fixture.');
  await dialog
    .getByRole('group', { name: 'Create profile' })
    .getByRole('button', { name: 'Create profile' })
    .click();
  await expect(
    dialog.getByRole('button', { name: 'View Fixture Researcher' }),
  ).toBeVisible();
  await screenshot(page, info, 'slice4-profile-created');
  await assertNoOverflow(page);
  await dialog
    .getByRole('button', { name: 'Start chat with Fixture Researcher' })
    .click();
  await expect(dialog).toHaveCount(0);
  await expect(page).toHaveURL(/\/conversations\/fixture-/);
});
