import type { Page, TestInfo } from '@playwright/test';
import {
  accessibility,
  assertNoOverflow,
  expect,
  screenshot,
  test,
} from './evidence';

// The backup model (#140): its row in Settings › Models › Defaults and the
// chat's one line when it took over. Synthetic settings and messages only;
// nothing calls a model.

async function fixture(page: Page, path: string) {
  const token = process.env.ROW_BOT_BROWSER_CONTROL_TOKEN;
  const base = process.env.ROW_BOT_BROWSER_BASE_URL;
  if (!token || !base) throw new Error('Use the isolated browser runner');
  const response = await page.request.post(path, {
    headers: { 'X-Fixture-Token': token, Origin: new URL(base).origin },
  });
  expect(response.ok(), await response.text()).toBe(true);
  return response.json();
}

test.afterEach(async ({ page }) => {
  await fixture(page, '/__backup_fixture/restore');
  await fixture(page, '/__s8_fixture/models/restore');
});

const phone = (info: TestInfo) =>
  (info.project.use.viewport?.width ?? 1440) < 700;

/** Light and dark, then an accessibility scan of what shows. */
async function shoot(page: Page, info: TestInfo, name: string) {
  const suffix = phone(info) ? '-phone' : '';
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    await assertNoOverflow(page);
    await screenshot(page, info, `${name}${suffix}-${appearance}`);
  }
  await accessibility(page, info, `${name}${suffix}`);
  await page.emulateMedia({ colorScheme: 'light' });
}

test('the backup model is off until chosen, and says where a local model’s messages go', async ({
  page,
}, info) => {
  await fixture(page, '/__p4_fixture/provider-credentials');
  await fixture(page, '/__s8_fixture/models/seed');
  await page.goto('/app-v2/settings/models');
  const models = page.locator('[aria-label="Models settings"]');
  const toggle = models.getByRole('switch', { name: 'Enable backup model' });
  await expect(toggle).not.toBeChecked();
  await expect(
    models.getByRole('button', { name: 'Backup model', exact: true }),
  ).toBeDisabled();

  await fixture(page, '/__backup_fixture/seed');
  await page.reload();
  await expect(toggle).toBeChecked();
  const picker = models.getByRole('button', {
    name: 'Backup model',
    exact: true,
  });
  await expect(picker).toBeEnabled();
  await expect(picker).toContainText('GPT-5 mini');
  await expect(
    models.getByText(/can.t answer, your message goes to .+ Incognito chats/),
  ).toBeVisible();
  // The same picker as the main model's, at the same size (B227).
  const main = models.getByRole('button', { name: 'Main model', exact: true });
  const [mainBox, pickerBox] = [
    (await main.boundingBox())!,
    (await picker.boundingBox())!,
  ];
  expect(pickerBox.height).toBeCloseTo(mainBox.height, 0);
  expect(pickerBox.width).toBeCloseTo(mainBox.width, 0);
  await page
    .locator('[data-setting-anchor="default-model"]')
    .scrollIntoViewIfNeeded();
  await shoot(page, info, 'backup-model-row');
});

test('the chat says in one line which model answered', async ({
  page,
}, info) => {
  const { conversation_id } = await fixture(page, '/__backup_fixture/chat');
  await page.goto(`/app-v2/conversations/${conversation_id}`);
  const line = page
    .getByRole('note')
    .filter({ hasText: 'so GPT-5 mini took over.' });
  await expect(line).toHaveText(
    "qwen3:8b didn't answer (it couldn't be reached), so GPT-5 mini took over.",
  );
  await expect(page.getByText(/My main model couldn/)).toHaveCount(0);
  await expect(
    page.getByRole('listitem').filter({ hasText: 'Dentist' }),
  ).toBeVisible();
  await shoot(page, info, 'backup-model-note');
});

test('a message still goes when the main model is down and the backup answers', async ({
  page,
}, info) => {
  await fixture(page, '/__p4_fixture/provider-credentials');
  await fixture(page, '/__s8_fixture/models/seed');
  await fixture(page, '/__backup_fixture/seed');
  await fixture(page, '/__backup_fixture/outage');
  const { conversation_id } = await fixture(page, '/__backup_fixture/chat');
  await page.goto(`/app-v2/conversations/${conversation_id}`);
  const composer = page.getByRole('textbox', { name: 'Message' });
  await composer.fill('What should I prepare for tomorrow?');
  await expect(
    page.getByText("qwen3:8b isn't available, so GPT-5 mini will answer."),
  ).toBeVisible();
  await expect(page.getByRole('button', { name: 'Reconnect' })).toHaveCount(0);
  await expect(
    page.getByRole('button', { name: 'Send', exact: true }),
  ).toBeEnabled();
  await shoot(page, info, 'backup-model-composer');
});
