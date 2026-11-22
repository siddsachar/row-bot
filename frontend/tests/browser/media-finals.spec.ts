import type { Page, TestInfo } from '@playwright/test';
import { assertNoOverflow, expect, screenshot, test } from './evidence';
import { fixtureHeaders } from './surface-helpers';
import { composer, newConversation } from './unified-helpers';

/*
 * Finals first (W1, W2): a turn that makes a birthday montage the way a
 * person would ask for any film. The fixture scripts the model; the tools are
 * real: an AI clip (generate_video's capture), a recording of Row-Bot's
 * browser, an edit (a synthetic ffmpeg), a contact sheet (a scripted vision
 * model) and `present`. Only the AI clip and the presented film show; the
 * recording, the edit and the contact sheet fold into "3 working files".
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
  settle: () => Promise<void>,
) {
  await expect(settle).toPass();
  await views(page, info, name);
  await page.setViewportSize(PHONE);
  await expect(settle).toPass();
  await views(page, info, `${name}-phone`);
  await page.setViewportSize(DESKTOP);
}

/** A video loaded far enough to show its first picture, or the fallback. */
async function playable(scope: ReturnType<Page['locator']>) {
  const player = scope.locator('video').first();
  const fallback = scope.getByRole('alert');
  await expect
    .poll(
      async () =>
        (await fallback.count())
          ? 'fallback'
          : (await player.count()) &&
              (await player.evaluate(
                (video: HTMLVideoElement) =>
                  video.readyState >= 1 && video.error === null,
              ))
            ? 'playback'
            : 'pending',
      { timeout: 15_000 },
    )
    .toMatch(/^(fallback|playback)$/);
}

/** The browser pane the recording opened: the views are of the chat itself. */
async function closeBrowserPane(page: Page) {
  const close = page.getByRole('button', {
    name: 'Close all panels',
    exact: true,
  });
  if (await close.isVisible()) await close.click();
  await expect(page.getByRole('region', { name: 'Browser' })).toHaveCount(0);
}

test.afterEach(async ({ page }) => {
  for (const reset of ['/__s4_fixture/reset', '/__s3_fixture/reset']) {
    const response = await page.request.post(reset, {
      headers: fixtureHeaders(),
    });
    expect(response.ok()).toBe(true);
  }
});

test('a film made with working files shows only its final and the AI clip; the rest folds', async ({
  page,
}, info) => {
  test.setTimeout(180_000);
  await page.setViewportSize(DESKTOP);
  await newConversation(page);
  await send(page, 's4 finals fixture');

  const transcript = page.getByRole('log', { name: 'Conversation' });
  await expect(
    transcript.getByText("Here's Sam's birthday montage", { exact: false }),
  ).toBeVisible({ timeout: 30_000 });

  // The final, large, with its name, its Final mark and the caption it was given.
  const final = transcript.getByRole('figure', {
    name: 'Final: Birthday montage.mp4',
  });
  await expect(final).toBeVisible();
  await expect(final).toContainText('Final');
  await expect(final).toContainText("Sam's birthday, 1 minute");
  await playable(final);
  // The AI clip shows too, by itself.
  await expect(
    transcript.getByRole('group', { name: 'Generated result' }),
  ).toHaveCount(1);
  // The workshop folds: no recording, edit or contact sheet inline.
  const fold = transcript.getByText('3 working files', { exact: true });
  await expect(fold).toBeVisible();
  await expect(
    transcript.getByRole('group', { name: /^Browser recording:/ }),
  ).toHaveCount(0);
  await expect(
    transcript.getByRole('img', { name: 'Contact sheet' }),
  ).toHaveCount(0);
  await closeBrowserPane(page);
  await everywhere(page, info, 'finals-first', () =>
    final.scrollIntoViewIfNeeded(),
  );

  // Opened on demand: the recording, the edit and the contact sheet.
  await fold.click();
  const recording = transcript.getByRole('group', {
    name: /^Browser recording: 0:\d\d · \d+ KB$/,
  });
  await expect(recording).toBeVisible();
  await expect(
    transcript.getByRole('img', { name: 'Contact sheet' }),
  ).toBeVisible();
  await expect(
    transcript.getByRole('group', {
      name: /^Trimmed, sped up 2× and captioned: /,
    }),
  ).toBeVisible();
  await everywhere(page, info, 'finals-working-files-open', async () => {
    await fold.scrollIntoViewIfNeeded();
  });
});
