import type { Page, TestInfo } from '@playwright/test';
import { assertNoOverflow, expect, screenshot, test } from './evidence';
import { fixtureHeaders } from './surface-helpers';
import { composer, headerAction, newConversation } from './unified-helpers';

/*
 * Finals first (W1, W2): a turn that makes a birthday montage the way a
 * person would ask for any film. The fixture scripts the model; the tools are
 * real: an AI clip (generate_video's capture), a recording of Row-Bot's
 * browser, an edit (a synthetic ffmpeg), a contact sheet (a scripted vision
 * model) and `present`. Only the AI clip and the presented film show; the
 * recording, the edit and the contact sheet fold into "3 working files".
 *
 * End to end (W8): a whole video made in a chat over two turns. Row-Bot
 * generates an AI shot, records its browser with beats, makes a video
 * project of the shot and the beats (create, make, its check and review
 * sheet) and asks for a review of the draft; the person asks for a shorter
 * intro, and the change makes version 2, which `present` shows as the final.
 * Every tool is real; ffmpeg, the voice and the vision model are synthetic.
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

/** The brightest channel in a strip of the page, read from its pixels. */
async function brightest(page: Page, clip: Rect) {
  const shot = await page.screenshot({ clip });
  return page.evaluate(async (data) => {
    // The page's CSP allows data: images but not fetching them.
    const image = new Image();
    image.src = `data:image/png;base64,${data}`;
    await image.decode();
    const canvas = new OffscreenCanvas(image.width, image.height);
    const context = canvas.getContext('2d')!;
    context.drawImage(image, 0, 0);
    const { data: pixels } = context.getImageData(
      0,
      0,
      image.width,
      image.height,
    );
    let most = 0;
    for (let at = 0; at < pixels.length; at += 4)
      most = Math.max(most, pixels[at], pixels[at + 1], pixels[at + 2]);
    return most;
  }, shot.toString('base64'));
}

type Rect = { x: number; y: number; width: number; height: number };

/**
 * A floating card stays frosted, not see-through: over a white backdrop its
 * dark card stays dark, and over a black one its light card stays light, so
 * a bright video under it never shows as a haze.
 */
async function staysFrosted(page: Page, card: Rect) {
  const strip = {
    x: card.x + 24,
    y: card.y + card.height - 8,
    width: card.width - 48,
    height: 4,
  };
  for (const [appearance, backdrop] of [
    ['dark', '#fff'],
    ['light', '#000'],
  ] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    await expect(page.locator('html')).toHaveAttribute(
      'data-theme',
      appearance,
    );
    const under = await page.evaluate(
      ({ rect, colour }) => {
        const element = document.createElement('div');
        Object.assign(element.style, {
          position: 'fixed',
          left: `${rect.x - 80}px`,
          top: `${rect.y - 80}px`,
          width: `${rect.width + 160}px`,
          height: `${rect.height + 160}px`,
          background: colour,
          // Under the card (z-index 30), over everything else.
          zIndex: '29',
          pointerEvents: 'none',
        });
        element.dataset.backdrop = 'probe';
        document.querySelector('.chat-workspace')!.append(element);
        return true;
      },
      { rect: card, colour: backdrop },
    );
    expect(under).toBe(true);
    const most = await brightest(page, strip);
    if (appearance === 'dark') expect(most).toBeLessThan(90);
    else expect(most).toBeGreaterThan(200);
    await page.evaluate(() =>
      document.querySelector('[data-backdrop="probe"]')?.remove(),
    );
  }
  await page.emulateMedia({ colorScheme: 'light' });
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
  // Named by its title, as its Outputs tile is, not by its file.
  const final = transcript.getByRole('figure', {
    name: 'Final: Birthday montage',
  });
  await expect(final).toBeVisible();
  await expect(final).not.toContainText('.mp4');
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

test('a video made end to end: its draft shows for review, the change is presented as the final, and Outputs holds one tile per output', async ({
  page,
}, info) => {
  test.setTimeout(180_000);
  await page.setViewportSize(DESKTOP);
  const chat = await newConversation(page);
  const transcript = page.getByRole('log', { name: 'Conversation' });
  const film = 'Sign-up in 30 seconds';
  const draft = transcript.getByRole('group', { name: `${film} · video` });
  const shot = transcript.getByRole('group', { name: 'Generated result' });
  const recording = transcript.getByRole('group', {
    name: /^Browser recording: 0:04 · \d+ KB$/,
  });

  // Generate, record with beats, make: the draft shows for review, beside the AI shot.
  await send(page, 'w8 video turn fixture');
  await expect(
    transcript.getByText("Here's the first cut", { exact: false }),
  ).toBeVisible({ timeout: 90_000 });
  await closeBrowserPane(page);
  await expect(draft).toContainText('Made');
  await expect(draft).toContainText('Version 1 of 1');
  await playable(draft);
  await expect(shot).toHaveCount(1);
  await playable(shot);
  // The recording is a working file: folded under the steps.
  await expect(
    transcript.getByText('1 working file', { exact: true }),
  ).toHaveCount(1);
  await expect(recording).toHaveCount(0);
  await everywhere(page, info, 'w8-draft-review', () =>
    draft.scrollIntoViewIfNeeded(),
  );
  // On a phone too, Open stays on the card's row, beside its title and state.
  await page.setViewportSize(PHONE);
  await draft.scrollIntoViewIfNeeded();
  const open = await draft
    .getByRole('button', { name: 'Open', exact: true })
    .boundingBox();
  const heading = await draft.getByText(film, { exact: true }).boundingBox();
  const state = await draft
    .getByText('Version 1 of 1', { exact: false })
    .boundingBox();
  expect(open && heading && state).toBeTruthy();
  expect(open!.y).toBeLessThan(state!.y + state!.height);
  expect(open!.x).toBeGreaterThan(heading!.x + heading!.width);
  await page.setViewportSize(DESKTOP);

  // A shorter intro: version 2 is made and presented, large, as the final.
  await send(page, 'w8 shorter intro fixture');
  await expect(
    transcript.getByText('with the shorter intro', { exact: false }),
  ).toBeVisible({ timeout: 90_000 });
  const final = transcript.getByRole('figure', { name: `Final: ${film}` });
  await expect(final).toContainText('Final');
  await expect(final).not.toContainText('.mp4');
  await expect(final).toContainText('Signing up, start to finish');
  await playable(final);
  // The draft folds away (version 1's card is gone, version 2's is a working
  // file of the reply that presented it); the AI shot stays.
  await expect(draft).toHaveCount(0);
  await expect(shot).toHaveCount(1);
  const folds = transcript.getByText('1 working file', { exact: true });
  await expect(folds).toHaveCount(2);
  await everywhere(page, info, 'w8-final', () =>
    final.scrollIntoViewIfNeeded(),
  );
  // Opened on demand: the recording, and the draft at version 2.
  await folds.nth(0).click();
  await expect(recording).toBeVisible();
  await folds.nth(1).click();
  await expect(draft).toContainText('Version 2 of 2');
  await everywhere(page, info, 'w8-working-files-open', () =>
    draft.scrollIntoViewIfNeeded(),
  );

  // The Outputs pane: the film and the AI shot, one tile each; the film's
  // versions in its menu, the presented one marked; working files on a toggle.
  const context = page.getByRole('complementary', {
    name: 'Conversation details',
  });
  if (!(await context.isVisible()))
    await headerAction(page, 'Conversation details');
  const outputs = context.getByRole('region', { name: 'Outputs' });
  const made = outputs.getByRole('list', { name: 'Outputs' });
  await expect(made.getByRole('listitem')).toHaveCount(2);
  await expect(
    made.getByRole('button', { name: `Open ${film}` }),
  ).toBeVisible();
  await expect(
    made.getByRole('button', { name: 'Open A phone lighting up on a desk' }),
  ).toBeVisible();
  await made.getByRole('button', { name: `Actions for ${film}` }).click();
  const menu = page.getByRole('menu');
  await expect(
    menu.getByRole('menuitem', { name: 'Version 2', exact: true }),
  ).toHaveAttribute('aria-current', 'true');
  await expect(
    menu.getByRole('menuitem', { name: 'Version 1', exact: true }),
  ).toBeVisible();
  await views(page, info, 'w8-outputs-versions');
  await page.keyboard.press('Escape');
  const toggle = outputs.getByRole('switch', { name: 'Show working files' });
  await expect(toggle).not.toBeChecked();
  await expect(
    outputs.getByRole('list', { name: 'Working files' }),
  ).toHaveCount(0);
  await toggle.click();
  // The recording, the review sheet (one tile for both versions') and the
  // voice of each scene that says a line, each told apart by its number.
  const working = outputs.getByRole('list', { name: 'Working files' });
  await expect(working.getByRole('listitem')).toHaveCount(5);
  for (const name of [
    'Recording',
    `${film} review`,
    `Voice 1 · ${film}`,
    `Voice 2 · ${film}`,
    `Voice 3 · ${film}`,
  ])
    await expect(
      working.getByRole('button', { name: `Open ${name}`, exact: true }),
    ).toBeVisible();
  await views(page, info, 'w8-outputs-working');
  // Frosted over whatever is under it (a bright video in a dark theme).
  const card = await context.boundingBox();
  expect(card).toBeTruthy();
  await staysFrosted(page, card!);

  await page.setViewportSize(PHONE);
  await page.goto(`/app-v2/conversations/${chat}`);
  await expect(composer(page)).toBeVisible();
  await headerAction(page, 'Conversation details');
  const sheet = page
    .getByRole('dialog', { name: 'Conversation details' })
    .or(context)
    .first();
  const phoneOutputs = sheet.getByRole('region', { name: 'Outputs' });
  await expect(
    phoneOutputs.getByRole('list', { name: 'Outputs' }).getByRole('listitem'),
  ).toHaveCount(2);
  await phoneOutputs
    .getByRole('switch', { name: 'Show working files' })
    .click();
  await expect(
    phoneOutputs
      .getByRole('list', { name: 'Working files' })
      .getByRole('listitem'),
  ).toHaveCount(5);
  await views(page, info, 'w8-outputs-phone');
  await page.setViewportSize(DESKTOP);
});
