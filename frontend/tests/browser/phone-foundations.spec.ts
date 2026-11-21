import { expect, screenshot, test } from './evidence';
import { asPairedPhone, onPhone, PHONES, tapPath } from './phone-helpers';

// The phone helpers every phone spec builds on: a phone's size, touch and
// user agent; the handshake a paired phone gets, not the desktop's own; and a
// tap path counted from Home.
for (const phone of PHONES)
  test.describe(phone.name, () => {
    onPhone(phone);

    test('a paired phone reaches Settings › Documents in three taps and is told Row-Bot runs on a server', async ({
      page,
    }, testInfo) => {
      await asPairedPhone(page);
      await page.goto('/app-v2/');
      const main = page.getByRole('navigation', { name: 'Main' });

      // Documents is in More settings on a phone (C3).
      const taps = await tapPath(page, [
        main.getByRole('link', { name: 'Settings' }),
        page.getByRole('link', { name: 'More settings' }),
        page.getByRole('link', { name: /^Documents/ }),
      ]);

      expect(taps).toBe(3);
      await expect(page).toHaveURL(/\/app-v2\/settings\/documents$/);
      const searchRuns = page.getByRole('radiogroup', { name: 'Search runs' });
      await expect(
        searchRuns.getByRole('radio', { name: 'Your Row-Bot server' }),
      ).toBeVisible();
      await expect(
        searchRuns.getByRole('radio', { name: 'This computer' }),
      ).toHaveCount(0);
      await screenshot(
        page,
        testInfo,
        `phone-foundations-documents-${phone.use.viewport.width}`,
      );
    });
  });
