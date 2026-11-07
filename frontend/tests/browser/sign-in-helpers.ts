import type { BrowserContext, Page } from '@playwright/test';

/**
 * The synthetic provider's sign-in page, in its own tab. A browser opens it by
 * itself only while the Connect click still counts as the person's gesture
 * (Chromium: about five seconds); when the sign-in starts later, as on a busy
 * CI runner, the page offers "Open the sign-in page again", and so does this.
 */
export async function signInPage(
  context: BrowserContext,
  page: Page,
  start: () => Promise<unknown>,
): Promise<Page> {
  const opened = context
    .waitForEvent('page', { timeout: 10_000 })
    .catch(() => null);
  await start();
  const tab = await opened;
  if (tab) return tab;
  const again = context.waitForEvent('page');
  await page
    .getByRole('button', { name: 'Open the sign-in page again' })
    .click();
  return again;
}
