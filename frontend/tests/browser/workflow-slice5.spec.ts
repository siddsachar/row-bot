import { assertNoOverflow, expect, screenshot, test } from './evidence';
import { blockFixtureServiceWorkers } from './unified-helpers';

test.use({ serviceWorkers: 'allow', nativeNetwork: true });
test.beforeEach(async ({ context }) => {
  await blockFixtureServiceWorkers(context);
});

test('workflow cards and create dialog fit, retain drafts, and restore focus', async ({
  page,
}, info) => {
  const token = process.env.ROW_BOT_BROWSER_CONTROL_TOKEN;
  const base = process.env.ROW_BOT_BROWSER_BASE_URL;
  if (!token || !base) throw new Error('Use the isolated Phase 4 runner');
  const seeded = await page.request.post('/__p4_fixture/tasks/populated', {
    headers: { 'X-Fixture-Token': token, Origin: new URL(base).origin },
  });
  expect(seeded.ok(), await seeded.text()).toBe(true);

  await page.goto('/app-v2/');
  await page
    .getByRole('searchbox', { name: 'Search workflows' })
    .fill('Phase 4 saved task 104');
  await page
    .getByRole('button', { name: 'Search workflows', exact: true })
    .click();
  const card = page.locator('.workflow-grid > li').first();
  await expect(card).toBeVisible();
  await expect(card.locator('.workflow-metadata')).toHaveText(
    /Reminder.*Never run.*Run manually/,
  );
  await assertNoOverflow(page);
  await screenshot(page, info, 'slice5-card');

  const create = page.getByRole('button', {
    name: 'New workflow',
    exact: true,
  });
  await create.click();
  const dialog = page.getByRole('dialog', { name: 'New task/workflow' });
  const editor = dialog.getByRole('form', { name: 'Create task' });
  await expect(editor).toBeVisible();
  await expect(card).toHaveCount(1);
  const bounds = (await dialog.boundingBox())!;
  const viewport = page.viewportSize()!;
  expect(bounds.x).toBeGreaterThanOrEqual(0);
  expect(bounds.x + bounds.width).toBeLessThanOrEqual(viewport.width);
  expect(bounds.y).toBeGreaterThanOrEqual(0);
  expect(bounds.y + bounds.height).toBeLessThanOrEqual(viewport.height);
  await editor
    .getByRole('textbox', { name: 'Name', exact: true })
    .fill(`Slice 5 workflow ${info.project.name}`);
  await editor
    .getByRole('textbox', { name: 'Prompt 1', exact: true })
    .fill('Summarize synthetic sample only');
  await screenshot(page, info, 'slice5-create-dialog');
  await assertNoOverflow(page);
  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);
  await expect(create).toBeFocused();
  const recovery = page.getByRole('region', {
    name: 'Continue editing workflows',
  });
  await expect(recovery).toBeVisible();
  await recovery.getByRole('button', { name: 'Continue editing' }).click();
  await expect(
    editor.getByRole('textbox', { name: 'Name', exact: true }),
  ).toHaveValue(`Slice 5 workflow ${info.project.name}`);
  await editor.getByRole('button', { name: 'Save task' }).click();
  await expect(dialog).toHaveCount(0);
  await expect(create).toBeFocused();
  await page
    .getByRole('searchbox', { name: 'Search workflows' })
    .fill(`Slice 5 workflow ${info.project.name}`);
  await page
    .getByRole('button', { name: 'Search workflows', exact: true })
    .click();
  await expect(page.locator('.workflow-grid > li')).toHaveCount(1);
  await expect(page.locator('.workflow-metadata')).toContainText('1 step');
  await assertNoOverflow(page);
});
