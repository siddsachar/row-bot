import type { Page, TestInfo } from '@playwright/test';
import { assertNoOverflow, expect, screenshot, test } from './evidence';
import { fixtureHeaders } from './surface-helpers';
import { composer, newConversation, revealContext } from './unified-helpers';

/*
 * F4 · F2 · Row-Bot's browser on apps on this computer, screenshots, page
 * sizes and recordings. The fixture scripts the model; the approval, the
 * conversation's grant (in the real browser session, which never starts a
 * browser here), the recording manager and the cards are real. The screenshot
 * and the recorded video are synthetic pictures of a small local app.
 * (Named to run after bootstrap.spec.ts, which needs a backend with no turns.)
 */

const PHONE = { width: 390, height: 844 };
const DESKTOP = { width: 1280, height: 900 };

async function send(page: Page, text: string) {
  await composer(page).fill(text);
  await page.getByRole('button', { name: 'Send', exact: true }).click();
}

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
  // A resize can mount a panel again under the same name: settle once it holds still.
  await expect(settle).toPass();
  await views(page, info, name);
  await page.setViewportSize(PHONE);
  await expect(settle).toPass();
  await views(page, info, `${name}-phone`);
  await page.setViewportSize(DESKTOP);
}

/** Buddy's status line, or "no-buddy" while Buddy hasn't loaded in the fixture. */
async function buddyStatus(page: Page) {
  const status = page
    .getByRole('complementary', { name: 'Buddy companion' })
    .getByRole('status');
  return (await status.count())
    ? ((await status.first().textContent()) ?? '')
    : 'no-buddy';
}

test.afterEach(async ({ page }) => {
  const response = await page.request.post('/__s3_fixture/reset', {
    headers: fixtureHeaders(),
  });
  expect(response.ok()).toBe(true);
});

test('a local address asks once for this chat, then opens and shows a screenshot', async ({
  page,
}, info) => {
  await page.setViewportSize(DESKTOP);
  await newConversation(page);
  await send(page, 's3 local address fixture');

  const question =
    "Allow Row-Bot's browser to open localhost:5173 in this chat?";
  const card = page.getByRole('complementary', {
    name: `Approval required: ${question}`,
  });
  await expect(card).toContainText(question);
  await expect(card).toContainText('only this address, only in this chat');
  await everywhere(page, info, 'local-address-approval', () =>
    card.scrollIntoViewIfNeeded(),
  );
  await card.getByRole('button', { name: 'Approve', exact: true }).click();

  // Allowed: the page opens and its picture shows in the chat.
  const transcript = page.getByRole('log', { name: 'Conversation' });
  await expect(
    transcript.getByText(
      "localhost:5173 is open in Row-Bot's browser. Here's the sign-up page as it is now.",
    ),
  ).toBeVisible();
  const picture = transcript
    .getByRole('img', { name: 'Page screenshot' })
    .last();
  await expect(picture).toBeVisible();
  await everywhere(page, info, 'screenshot-in-chat', () =>
    picture.scrollIntoViewIfNeeded(),
  );
  // The steps read in words.
  await transcript.getByText('Used 2 tools', { exact: true }).click();
  await expect(transcript.getByText('Opened page')).toBeVisible();
  await expect(transcript.getByText('Took a screenshot')).toBeVisible();

  // The browser panel lists the grant, who allowed it and when it ends, with Remove.
  const context = await revealContext(page);
  await context.getByRole('button', { name: 'Conversation actions' }).click();
  await page.getByRole('menuitem', { name: 'Manage browser' }).click();
  const grants = page.getByRole('region', { name: 'Local addresses' });
  const list = grants.getByRole('list', {
    name: 'Local addresses allowed in this chat',
  });
  await expect(list.getByRole('listitem')).toHaveCount(1);
  await expect(list).toContainText('localhost:5173');
  await expect(list).toContainText(/Allowed by you · (60 min|1 hour) left/);
  await everywhere(page, info, 'local-address-list', () =>
    grants.scrollIntoViewIfNeeded(),
  );

  await grants.getByRole('button', { name: 'Remove localhost:5173' }).click();
  await expect(grants).toContainText(
    'localhost:5173 removed. Row-Bot asks again before opening it.',
  );
  await expect(grants).toContainText(
    "No addresses on this computer are open to Row-Bot's browser in this chat.",
  );
});

test('a page at phone size, as a screenshot in the chat', async ({
  page,
}, info) => {
  await page.setViewportSize(DESKTOP);
  await newConversation(page);
  await send(page, 's3 viewport fixture');

  const transcript = page.getByRole('log', { name: 'Conversation' });
  await expect(
    transcript.getByText(/Here's the sign-up page at phone size/).last(),
  ).toBeVisible();
  const picture = transcript
    .getByRole('img', { name: 'Page screenshot' })
    .last();
  await expect(picture).toBeVisible();
  await everywhere(page, info, 'viewport-phone-screenshot', () =>
    picture.scrollIntoViewIfNeeded(),
  );
});

test('a recording shows live with Stop, then as a video in the chat', async ({
  page,
}, info) => {
  await page.setViewportSize(DESKTOP);
  await newConversation(page);
  await send(page, 's3 recording fixture');

  const transcript = page.getByRole('log', { name: 'Conversation' });
  const live = transcript.getByRole('group', {
    name: /^Browser recording: Recording · 0:\d\d$/,
  });
  await expect(live).toBeVisible();
  await expect(live).toContainText("Recording Row-Bot's browser");
  await expect(live).toContainText('Stops by itself at 5:00');
  // Buddy shows the recording as work while it lasts (when Buddy has loaded).
  await expect
    .poll(() => buddyStatus(page), { timeout: 20_000 })
    .toMatch(/^(no-buddy|Recording)/);
  await everywhere(page, info, 'recording-in-progress', () =>
    live.scrollIntoViewIfNeeded(),
  );

  await live.getByRole('button', { name: 'Stop', exact: true }).click();
  const saved = transcript.getByRole('group', {
    name: /^Browser recording: 0:\d\d · \d+ KB$/,
  });
  await expect(saved).toBeVisible();
  await expect(saved).toContainText('Stopped by you');
  await expect(saved.getByRole('button', { name: 'Stop' })).toHaveCount(0);
  // The video plays where the browser has H.264 (Edge, Chrome); a Chromium
  // build without it shows the preview's download-and-retry fallback instead.
  const player = saved.locator('video');
  const fallback = saved.getByRole('alert');
  await expect
    .poll(
      async () =>
        (await fallback.count())
          ? 'fallback'
          : (await player.count()) === 1 &&
              (await player.evaluate(
                (video: HTMLVideoElement) =>
                  video.readyState >= 1 && video.error === null,
              ))
            ? 'playback'
            : 'pending',
      { timeout: 10_000 },
    )
    .toMatch(/^(fallback|playback)$/);
  // Its first picture, for the evidence.
  const firstPicture = async () => {
    if (!(await player.count())) return;
    await player.evaluate(
      (video: HTMLVideoElement) =>
        new Promise<void>((done) => {
          if (video.readyState >= 2) return done();
          video.addEventListener('loadeddata', () => done(), { once: true });
          video.addEventListener('seeked', () => done(), { once: true });
          setTimeout(done, 5000);
          video.currentTime = 0.05;
        }),
    );
  };
  // Buddy goes back to idle once the recording has ended.
  await expect
    .poll(() => buddyStatus(page), { timeout: 20_000 })
    .not.toMatch(/^Recording/);
  await everywhere(page, info, 'recording-saved', async () => {
    await saved.scrollIntoViewIfNeeded();
    await firstPicture();
  });
});

test('recording the screen without Computer use says what it needs', async ({
  page,
}, info) => {
  await page.setViewportSize(DESKTOP);
  await newConversation(page);
  await send(page, 's3 screen recording fixture');

  const transcript = page.getByRole('log', { name: 'Conversation' });
  const reply = transcript.getByText(
    /I can't record your screen yet: that needs Computer use set up\./,
  );
  await expect(reply).toBeVisible();
  await everywhere(page, info, 'screen-recording-needs-computer-use', () =>
    reply.scrollIntoViewIfNeeded(),
  );
});
