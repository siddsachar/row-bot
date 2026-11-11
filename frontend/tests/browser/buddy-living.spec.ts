import { expect, screenshot, test } from './evidence';
import { retireDocument } from './unified-helpers';

// 5.3.4: Buddy is a living illustration. In the sidebar it draws itself
// (WebGL2) with real transparency: no frame, no dark disc, the page showing
// through around it in light and dark. Runs against the fixture backend,
// which serves the built-in looks.

for (const appearance of ['light', 'dark'] as const) {
  test(`the sidebar Buddy comes alive and stands on the page in ${appearance}`, async ({
    page,
  }, info) => {
    test.skip(
      page.viewportSize()!.width < 1024,
      'the expanded sidebar shows the portrait',
    );
    await page.emulateMedia({ colorScheme: appearance });
    await retireDocument(page);
    await page.goto('/app-v2/');
    await expect(page.locator('.connection-status.connected')).toHaveText(
      'Connected',
    );
    const buddy = page.locator('.navigation .buddy-avatar-frame').first();
    await expect(buddy).toBeVisible();
    await expect(buddy).toHaveAttribute('data-media', 'living');
    await expect(buddy.locator('.living-buddy')).toHaveAttribute(
      'data-runtime',
      'live',
      { timeout: 15_000 },
    );
    await page.waitForTimeout(800);

    // The corners of Buddy's box are the sidebar itself: real alpha.
    const shot = await buddy.screenshot();
    const sample = await page.evaluate(async (data) => {
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
    const sidebar = await page
      .locator('.navigation')
      .evaluate((element) => getComputedStyle(element).backgroundColor);
    const rgb = sidebar.match(/\d+/g)!.slice(0, 3).map(Number);
    for (const corner of sample.corners)
      corner.forEach((channel, index) =>
        expect(Math.abs(channel - rgb[index])).toBeLessThanOrEqual(6),
      );
    // …and Buddy itself is drawn in the middle.
    expect(
      sample.middle.some(
        (channel, index) => Math.abs(channel - rgb[index]) > 24,
      ),
    ).toBe(true);
    await screenshot(page, info, `buddy-living-sidebar-${appearance}`);
  });
}
