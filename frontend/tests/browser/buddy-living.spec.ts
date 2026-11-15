import type { Locator, Page } from '@playwright/test';
import { expect, screenshot, test } from './evidence';
import { retireDocument } from './unified-helpers';

// 5.3.4: Buddy is a living illustration. It draws itself (WebGL2) with real
// transparency: no frame, no disc, whatever is behind it showing through
// around it. Runs against the fixture backend, which serves the built-in
// looks.

type Rgb = number[];

async function sample(page: Page, buddy: Locator) {
  const shot = await buddy.screenshot();
  return page.evaluate(async (data) => {
    // The page's CSP allows data: images but not fetching them.
    const image = new Image();
    image.src = `data:image/png;base64,${data}`;
    await image.decode();
    const canvas = new OffscreenCanvas(image.width, image.height);
    const context = canvas.getContext('2d')!;
    context.drawImage(image, 0, 0);
    const at = (x: number, y: number) =>
      Array.from(context.getImageData(x, y, 1, 1).data.slice(0, 3));
    return {
      corners: [
        at(2, 2),
        at(image.width - 3, 2),
        at(2, image.height - 3),
        at(image.width - 3, image.height - 3),
      ],
      middle: at(Math.floor(image.width / 2), Math.floor(image.height / 2)),
    };
  }, shot.toString('base64'));
}

const near = (a: Rgb, b: Rgb, within: number) =>
  a.every((channel, index) => Math.abs(channel - b[index]) <= within);

/** Buddy is alive, paints no box of its own, and what is behind it shows
 * through at its corners while its body covers the middle. */
async function standsFree(page: Page, buddy: Locator) {
  await expect(buddy).toBeVisible();
  await expect(buddy).toHaveAttribute('data-media', 'living');
  await expect(buddy.locator('.living-buddy')).toHaveAttribute(
    'data-runtime',
    'live',
    { timeout: 15_000 },
  );
  expect(
    await buddy.evaluate(
      (element) => getComputedStyle(element).backgroundColor,
    ),
  ).toBe('rgba(0, 0, 0, 0)');
  for (const colour of [
    [255, 0, 0],
    [0, 160, 255],
  ]) {
    await buddy.evaluate(
      (element, [r, g, b]) =>
        ((element as HTMLElement).style.background = `rgb(${r}, ${g}, ${b})`),
      colour,
    );
    const { corners, middle } = await sample(page, buddy);
    for (const corner of corners) expect(near(corner, colour, 6)).toBe(true);
    expect(near(middle, colour, 24)).toBe(false);
  }
  await buddy.evaluate((element) =>
    (element as HTMLElement).style.removeProperty('background'),
  );
}

for (const appearance of ['light', 'dark'] as const) {
  test(`Buddy comes alive and stands free on the page in ${appearance}`, async ({
    page,
  }, info) => {
    await page.emulateMedia({ colorScheme: appearance });
    await retireDocument(page);
    await page.goto('/app-v2/');
    await expect(page.locator('.connection-status.connected')).toHaveText(
      'Connected',
    );
    // Overview's greeting, at every width.
    await standsFree(
      page,
      page.locator('.overview-buddy > .buddy-avatar-frame'),
    );
    // The expanded sidebar's portrait, on wide screens.
    if (page.viewportSize()!.width >= 1024)
      await standsFree(
        page,
        page.locator('.navigation .buddy-avatar-frame').first(),
      );
    await page.waitForTimeout(800);
    await screenshot(page, info, `buddy-living-${appearance}`);
  });
}
