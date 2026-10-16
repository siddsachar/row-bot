import type { Browser, Page, TestInfo } from '@playwright/test';
import fs from 'node:fs/promises';
import { assertNoOverflow, expect, screenshot, test } from './evidence';
import { fixtureHeaders } from './surface-helpers';
import { composer, newConversation } from './unified-helpers';

/*
 * F7 · Send a file to your devices or a chat app. The fixture scripts the
 * model; send_file, the deliveries, the notices, the downloads and the cards
 * are real. Telegram is the in-memory fake adapter: it records what it would
 * send, and nothing reaches Telegram.
 */

const PHONE = { width: 390, height: 844 };
const DESKTOP = { width: 1280, height: 900 };

async function send(page: Page, text: string) {
  await composer(page).fill(text);
  await page.getByRole('button', { name: 'Send', exact: true }).click();
}

/** Evidence in light and dark at the current size. */
async function views(
  page: Page,
  info: TestInfo,
  name: string,
  settle: () => Promise<void> = async () => {},
) {
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    await expect(page.locator('html')).toHaveAttribute(
      'data-theme',
      appearance,
    );
    await settle();
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

/** The person's phone: another signed-in device, with a chat of its own open. */
async function phoneOf(page: Page, browser: Browser) {
  const origin = new URL(page.url()).origin;
  const context = await browser.newContext({
    baseURL: origin,
    viewport: PHONE,
    hasTouch: true,
    serviceWorkers: 'block',
  });
  await context.route(
    (url) =>
      ['http:', 'https:'].includes(url.protocol) && url.origin !== origin,
    (route) => route.abort(),
  );
  const phone = await context.newPage();
  await newConversation(phone);
  await clearNotices(phone);
  return { phone, context };
}

/** Notices from earlier work (the last ten minutes) show once on a new device; clear them. */
async function clearNotices(page: Page) {
  await page.waitForTimeout(800);
  const dismiss = page.getByRole('button', { name: 'Dismiss notification' });
  while (await dismiss.count()) await dismiss.first().click();
}

test.afterEach(async ({ page }) => {
  const response = await page.request.post('/__s5_fixture/reset', {
    headers: fixtureHeaders(),
  });
  expect(response.ok()).toBe(true);
});

test('sending to your devices shows the download here and on your phone, with a notice', async ({
  page,
  browser,
}, info) => {
  await page.setViewportSize(DESKTOP);
  const conversation = await newConversation(page);
  const { phone, context } = await phoneOf(page, browser);
  try {
    await send(page, 's5 devices fixture');

    const transcript = page.getByRole('log', { name: 'Conversation' });
    const card = transcript.getByRole('group', {
      name: 'Sent to your devices: Ready to download',
    });
    await expect(card).toBeVisible();
    await expect(card).toContainText(
      'On all your signed-in devices · 7 days left',
    );
    await expect(card).toContainText('q3-report.pdf');
    await expect(
      card.getByRole('button', { name: 'Download q3-report.pdf' }),
    ).toBeVisible();
    await expect(
      transcript.getByText(/Sent: q3-report.pdf is on your devices/),
    ).toBeVisible();
    await everywhere(page, info, 'sent-to-devices', () =>
      card.scrollIntoViewIfNeeded(),
    );
    // This device has the chat open: the card is the news, no notice.
    await expect(page.getByText('Row-Bot sent you q3-report.pdf')).toHaveCount(
      0,
    );

    // The phone, in another chat: "Row-Bot sent you …" with Open.
    const notice = phone
      .locator('.toast')
      .filter({ hasText: 'Row-Bot sent you q3-report.pdf' });
    await expect(notice).toBeVisible({ timeout: 15_000 });
    await notice.hover(); // Held while it's looked at.
    await views(phone, info, 'device-notice-phone', () => notice.hover());
    await notice.getByRole('button', { name: 'Open' }).click();
    await expect(phone).toHaveURL(
      new RegExp(`/conversations/${encodeURIComponent(conversation)}$`),
    );

    const onPhone = phone
      .getByRole('log', { name: 'Conversation' })
      .getByRole('group', { name: 'Sent to your devices: Ready to download' });
    await expect(onPhone).toBeVisible();
    await views(phone, info, 'device-download-phone', () =>
      onPhone.scrollIntoViewIfNeeded(),
    );
    const [download] = await Promise.all([
      phone.waitForEvent('download'),
      onPhone.getByRole('button', { name: 'Download q3-report.pdf' }).click(),
    ]);
    expect(download.suggestedFilename()).toBe('q3-report.pdf');
    const saved = await fs.readFile(await download.path());
    expect(saved.subarray(0, 8).toString()).toBe('%PDF-1.4');
    await expect(onPhone.getByRole('alert')).toHaveCount(0);
  } finally {
    await context.close();
  }
});

test('sending to Telegram asks first, then says where it went', async ({
  page,
}, info) => {
  await page.setViewportSize(DESKTOP);
  await newConversation(page);
  await clearNotices(page);
  await send(page, 's5 telegram fixture');

  const question = 'Send q3-report.pdf to your Telegram chat?';
  const approval = page.getByRole('complementary', {
    name: `Approval required: ${question}`,
  });
  await expect(approval).toContainText(question);
  await expect(approval).toContainText(
    "from this chat's files, to your Telegram chat. It leaves Row-Bot and stays in that chat.",
  );
  await everywhere(page, info, 'channel-approval', () =>
    approval.scrollIntoViewIfNeeded(),
  );
  await approval.getByRole('button', { name: 'Approve', exact: true }).click();

  const transcript = page.getByRole('log', { name: 'Conversation' });
  const card = transcript.getByRole('group', {
    name: 'Sent to Telegram: Sent',
  });
  await expect(card).toBeVisible();
  await expect(card).toContainText('Your Telegram chat');
  await expect(card.getByRole('button')).toHaveCount(0);
  await everywhere(page, info, 'sent-to-channel', () =>
    card.scrollIntoViewIfNeeded(),
  );
  const sent = await page.request.get('/__s5_fixture/telegram', {
    headers: fixtureHeaders(),
  });
  expect((await sent.json()).sent).toEqual([
    { target: '4242', name: 'q3-report.pdf' },
  ]);
});

test('a file too big for Telegram says so, and goes to your devices instead in one click', async ({
  page,
}, info) => {
  await page.setViewportSize(DESKTOP);
  await newConversation(page);
  await clearNotices(page);
  await send(page, 's5 too big fixture');

  const transcript = page.getByRole('log', { name: 'Conversation' });
  const card = transcript.getByRole('group', {
    name: 'Not sent to Telegram: Too big for Telegram (50 MB limit)',
  });
  await expect(card).toBeVisible();
  await expect(card).toContainText('demo-recording.mp4');
  await expect(card).toContainText('63.0 MB · MP4 video');
  await everywhere(page, info, 'channel-failure', () =>
    card.scrollIntoViewIfNeeded(),
  );

  await card
    .getByRole('button', { name: 'Send to your devices instead' })
    .click();
  const instead = transcript.getByRole('group', {
    name: 'Sent to your devices: Ready to download',
  });
  await expect(instead).toBeVisible();
  await expect(
    instead.getByRole('button', { name: 'Download demo-recording.mp4' }),
  ).toBeVisible();
  await expect(
    card.getByRole('button', { name: 'Send to your devices instead' }),
  ).toHaveCount(0);
  await everywhere(page, info, 'sent-instead', () =>
    instead.scrollIntoViewIfNeeded(),
  );
  const sent = await page.request.get('/__s5_fixture/telegram', {
    headers: fixtureHeaders(),
  });
  expect((await sent.json()).sent).toEqual([]);
});
