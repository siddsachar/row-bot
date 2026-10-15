import type { Page, TestInfo } from '@playwright/test';
import { assertNoOverflow, expect, screenshot, test } from './evidence';
import { fixtureHeaders } from './surface-helpers';
import { composer, newConversation } from './unified-helpers';

/*
 * F3 · F5 · Video editing and many images at once. The fixture scripts the
 * model; edit_video and analyze_image, the edit jobs, the process manager, the
 * files and the cards are real. ffmpeg is a small synthetic program (it
 * reports progress and "makes" a synthetic H.264 clip), its first download is
 * a fake that reports progress, and the vision model's answer is scripted.
 */

const PHONE = { width: 390, height: 844 };
const DESKTOP = { width: 1280, height: 900 };
const SUMMARY = 'Trimmed, sped up 2× and captioned';

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
  await settle();
  await views(page, info, name);
  await page.setViewportSize(PHONE);
  await settle();
  await views(page, info, `${name}-phone`);
  await page.setViewportSize(DESKTOP);
}

test.afterEach(async ({ page }) => {
  const response = await page.request.post('/__s4_fixture/reset', {
    headers: fixtureHeaders(),
  });
  expect(response.ok()).toBe(true);
});

test('the first edit asks once to download ffmpeg, then shows the download with Stop', async ({
  page,
}, info) => {
  await page.setViewportSize(DESKTOP);
  await newConversation(page);
  await send(page, 's4 consent fixture');

  const question =
    'Video editing needs ffmpeg (about 30 MB) from PyPI. Download it now?';
  const card = page.getByRole('complementary', {
    name: `Approval required: ${question}`,
  });
  await expect(card).toContainText(question);
  await expect(card).toContainText('keep only its ffmpeg program');
  await everywhere(page, info, 'ffmpeg-consent', () =>
    card.scrollIntoViewIfNeeded(),
  );
  await card.getByRole('button', { name: 'Approve', exact: true }).click();

  const transcript = page.getByRole('log', { name: 'Conversation' });
  const download = transcript.getByRole('group', {
    name: /^Getting ffmpeg for video editing: Downloading · \d+ MB of 30 MB$/,
  });
  await expect(download).toBeVisible();
  await expect(download).toContainText('From PyPI, once');
  await expect(
    download.getByRole('progressbar', { name: 'ffmpeg download' }),
  ).toBeVisible();
  await expect
    .poll(async () => (await download.getAttribute('aria-label')) ?? '')
    .toMatch(/Downloading · (1[0-9]|[2-9]\d) MB/);
  await everywhere(page, info, 'ffmpeg-download', () =>
    download.scrollIntoViewIfNeeded(),
  );

  await download.getByRole('button', { name: 'Stop', exact: true }).click();
  await expect(
    transcript.getByRole('group', { name: `${SUMMARY}: Stopped by you` }),
  ).toBeVisible();
});

test('a long edit shows its progress with Stop', async ({ page }, info) => {
  await page.setViewportSize(DESKTOP);
  await newConversation(page);
  await send(page, 's4 long edit fixture');

  const transcript = page.getByRole('log', { name: 'Conversation' });
  const live = transcript.getByRole('group', {
    name: new RegExp(`^${SUMMARY}: Editing · \\d+%$`),
  });
  await expect(live).toBeVisible();
  await expect(
    live.getByRole('progressbar', { name: 'Edit progress' }),
  ).toBeVisible();
  await expect
    .poll(async () => (await live.getAttribute('aria-label')) ?? '', {
      timeout: 15_000,
    })
    .toMatch(/Editing · ([2-9]\d)%$/);
  await everywhere(page, info, 'edit-in-progress', () =>
    live.scrollIntoViewIfNeeded(),
  );

  await live.getByRole('button', { name: 'Stop', exact: true }).click();
  const stopped = transcript.getByRole('group', {
    name: `${SUMMARY}: Stopped by you`,
  });
  await expect(stopped).toBeVisible();
  await expect(stopped.getByRole('button', { name: 'Stop' })).toHaveCount(0);
});

test('the edited video shows in the chat with a player', async ({
  page,
}, info) => {
  await page.setViewportSize(DESKTOP);
  await newConversation(page);
  await send(page, 's4 edit fixture');

  const transcript = page.getByRole('log', { name: 'Conversation' });
  const saved = transcript.getByRole('group', {
    name: new RegExp(`^${SUMMARY}: 0:\\d\\d(\\.\\d)? · \\d+ KB$`),
  });
  await expect(saved).toBeVisible({ timeout: 20_000 });
  await expect(saved).toContainText('Saved to this chat');
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
  await saved.getByText('Details').click();
  await expect(saved).toContainText(
    'ffmpeg 7.1 (GPL version 3 or later), downloaded from PyPI',
  );
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
  await everywhere(page, info, 'edited-video', async () => {
    await saved.scrollIntoViewIfNeeded();
    await firstPicture();
  });
  await transcript.getByText('Used 1 tool', { exact: true }).click();
  await expect(transcript.getByText('Edited a video')).toBeVisible();
});

test('a contact sheet of many screenshots shows in the chat', async ({
  page,
}, info) => {
  await page.setViewportSize(DESKTOP);
  await newConversation(page);
  await send(page, 's4 contact sheet fixture');

  const transcript = page.getByRole('log', { name: 'Conversation' });
  await expect(transcript.getByText(/Step 9 goes wrong/).last()).toBeVisible();
  const sheet = transcript.getByRole('img', { name: 'Contact sheet' }).last();
  await expect(sheet).toBeVisible();
  await everywhere(page, info, 'contact-sheet', () =>
    sheet.scrollIntoViewIfNeeded(),
  );
});

test('when the model can’t see images it says so plainly', async ({
  page,
}, info) => {
  await page.setViewportSize(DESKTOP);
  await newConversation(page);
  await send(page, 's4 no vision fixture');

  const transcript = page.getByRole('log', { name: 'Conversation' });
  const reply = transcript.getByText(
    /I can't look at these screenshots with this chat's model/,
  );
  await expect(reply).toBeVisible();
  await expect(
    transcript.getByRole('img', { name: 'Contact sheet' }),
  ).toHaveCount(0);
  await transcript.getByText('Used 1 tool', { exact: true }).click();
  await expect(transcript.getByText('Looked at an image')).toBeVisible();
  await everywhere(page, info, 'no-vision-fallback', () =>
    reply.scrollIntoViewIfNeeded(),
  );
});
