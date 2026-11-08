import type { Page, TestInfo } from '@playwright/test';
import { assertNoOverflow, expect, screenshot, test } from './evidence';
import {
  composer,
  fixtureState,
  newConversation,
  releaseProducer,
  type FixtureCall,
} from './unified-helpers';

/*
 * 5.3.3 · A turn of several steps reads in the order it happened. Each model
 * step says a line, then takes a tool step that makes a video or a picture:
 * its words, its step and what it made come together, and the newest step is
 * at the bottom, where the chat follows it, while the turn runs, once it ends
 * and after a reload. The fixture scripts the model; the platform's live
 * rows, steps, media and saved turn are real.
 */

const PHONE = { width: 390, height: 844 };
const DESKTOP = { width: 1280, height: 860 };
const STEP_1 = 'First, a short video of the sign-up flow.';
const STEP_2 = 'Now a still of the finished form, to check its layout.';
const STEP_4 = 'Next, a shorter cut with captions.';
const FINAL =
  'Here are the three videos and the still. The shorter cut with captions is the one to share.';

let held: FixtureCall | undefined;
test.afterEach(async ({ page }) => {
  // A failed check never leaves the turn waiting behind its barrier.
  if (held) await releaseProducer(page, held).catch(() => undefined);
  held = undefined;
});

const transcript = (page: Page) =>
  page.getByRole('log', { name: 'Conversation' });

/**
 * The reply as a reader meets it, top to bottom: each line of narration, each
 * activity line and each picture or video.
 */
async function reading(page: Page): Promise<string[]> {
  return transcript(page).evaluate((log) =>
    [
      ...log.querySelectorAll(
        '.message-assistant .message-text, .message-assistant .activity-row, .message-assistant [data-media-ref]',
      ),
    ].map((element) =>
      element.classList.contains('message-text')
        ? `text: ${element.textContent?.trim()}`
        : element.classList.contains('activity-row')
          ? 'activity'
          : `media: ${element.getAttribute('data-kind')}`,
    ),
  );
}

/** Desktop and phone, light and dark, at the newest step. */
async function everywhere(page: Page, info: TestInfo, name: string) {
  for (const size of [DESKTOP, PHONE]) {
    await page.setViewportSize(size);
    for (const appearance of ['light', 'dark'] as const) {
      await page.emulateMedia({ colorScheme: appearance });
      await expect(page.locator('html')).toHaveAttribute(
        'data-theme',
        appearance,
      );
      await assertNoOverflow(page);
      await screenshot(
        page,
        info,
        `${name}${size === PHONE ? '-phone' : ''}-${appearance}`,
      );
    }
  }
  await page.emulateMedia({ colorScheme: 'light' });
  await page.setViewportSize(DESKTOP);
}

const FINISHED = [
  `text: ${STEP_1}`,
  'activity',
  'media: video',
  `text: ${STEP_2}`,
  'activity',
  'media: image',
  'activity',
  'media: video',
  `text: ${STEP_4}`,
  'activity',
  'media: video',
  `text: ${FINAL}`,
];

test('a turn of several steps shows each step’s words, activity and media in order', async ({
  page,
}, info) => {
  await page.setViewportSize(DESKTOP);
  await newConversation(page);
  await composer(page).fill('turn steps fixture');
  await page.getByRole('button', { name: 'Send', exact: true }).click();

  // Mid-stream: the last step runs, its words below the videos made before it.
  const log = transcript(page);
  const newest = log.getByText(STEP_4, { exact: true });
  await expect(newest).toBeVisible();
  await expect(log.locator('video')).toHaveCount(2);
  await expect(log.locator('[data-media-ref][data-kind="image"]')).toHaveCount(
    1,
  );
  held = (await fixtureState(page)).calls
    .filter((call) => call.case === 'turn-steps')
    .at(-1);
  expect(held).toBeDefined();
  await expect
    .poll(() => reading(page))
    .toEqual([
      `text: ${STEP_1}`,
      'activity',
      'media: video',
      `text: ${STEP_2}`,
      'activity',
      'media: image',
      'activity',
      'media: video',
      `text: ${STEP_4}`,
      // The step in progress, on the turn's live line.
      'activity',
    ]);
  const below = (await newest.boundingBox())!.y;
  for (const video of await log.locator('video').all())
    expect((await video.boundingBox())!.y).toBeLessThan(below);
  await expect(log.locator('.message-live')).toContainText(
    'Generating a video',
  );
  await everywhere(page, info, 'turn-steps-mid-stream');

  // Finished: the last video sits under its step, the answer after it.
  await releaseProducer(page, held!);
  held = undefined;
  await expect(log.getByText(FINAL, { exact: true })).toBeVisible();
  await expect(log.locator('.message-live')).toHaveCount(0);
  await expect.poll(() => reading(page)).toEqual(FINISHED);
  // Each picture or video once (B22).
  const references = await log
    .locator('[data-media-ref]')
    .evaluateAll((elements) =>
      elements.map((element) => element.getAttribute('data-media-ref')),
    );
  expect(references).toHaveLength(4);
  expect(new Set(references).size).toBe(4);
  // One action bar for the turn, at its end.
  await expect(
    log.getByRole('toolbar', { name: 'Row-Bot message actions' }),
  ).toHaveCount(1);
  await everywhere(page, info, 'turn-steps-finished');

  // After a reload the turn reads the same.
  await page.reload();
  await expect(log.getByText(FINAL, { exact: true })).toBeVisible();
  await expect.poll(() => reading(page)).toEqual(FINISHED);
  await everywhere(page, info, 'turn-steps-reloaded');
});
