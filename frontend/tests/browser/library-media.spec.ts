import type { Locator, Page, TestInfo } from '@playwright/test';
import { assertNoOverflow, expect, screenshot, test } from './evidence';
import { fixtureHeaders } from './surface-helpers';
import { dismissNotices } from './unified-helpers';

/*
 * Library › Media (5.3.1 B3): every picture, video and sound from the chats,
 * newest first, with search, a kind filter and tags. A fixture chat holds a
 * recording, two pictures and a voice line; nothing leaves the machine.
 */

const PHONE = { width: 390, height: 844 };
const DESKTOP = { width: 1280, height: 900 };

async function views(page: Page, info: TestInfo, name: string, focus: Locator) {
  await dismissNotices(page);
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    await expect(page.locator('html')).toHaveAttribute(
      'data-theme',
      appearance,
    );
    await focus.scrollIntoViewIfNeeded();
    await assertNoOverflow(page);
    await screenshot(page, info, `${name}-${appearance}`);
  }
  await page.emulateMedia({ colorScheme: 'light' });
}

test('Library › Media lists every picture, video and sound with its chat, filters by kind, and tags one', async ({
  page,
}, info) => {
  await page.setViewportSize(DESKTOP);
  const seeded = await page.request.post('/__media_fixture/seed', {
    headers: fixtureHeaders(),
  });
  expect(seeded.ok(), await seeded.text()).toBe(true);
  await page.goto('/app-v2/library?show=media');
  const list = page.getByRole('list', { name: 'Media items' });
  // Other journeys leave their own recordings and edits here: this one looks at its chat's.
  const search = page.getByRole('searchbox', { name: 'Search media' });
  await search.fill('Launch assets');
  await expect(list.getByRole('listitem')).toHaveCount(4);
  for (const name of [
    'launch-card.png',
    'pricing-step.png',
    'signup-walkthrough.mp4',
    'voice-line-01.wav',
  ])
    await expect(
      list.getByRole('link', { name: `${name}, in Launch assets` }),
    ).toBeVisible();
  await expect(list.locator('img.media-library-thumb').first()).toBeVisible();
  await views(page, info, 'library-media', list);

  const kinds = page.getByRole('radiogroup', { name: 'Kind of media' });
  await kinds.getByRole('radio', { name: 'Videos', exact: true }).click();
  await expect(list.getByRole('listitem')).toHaveCount(1);
  await expect(list.getByText('signup-walkthrough.mp4')).toBeVisible();

  await list
    .getByRole('button', { name: 'Add tags to signup-walkthrough.mp4' })
    .click();
  const tags = list.getByRole('textbox', {
    name: 'Tags for signup-walkthrough.mp4',
  });
  await tags.fill('Demo, x post');
  await tags.press('Enter');
  await expect(list.getByText('x post', { exact: true })).toBeVisible();

  await kinds.getByRole('radio', { name: 'All', exact: true }).click();
  await search.fill('x post');
  await expect(list.getByRole('listitem')).toHaveCount(1);
  await expect(list.getByText('signup-walkthrough.mp4')).toBeVisible();
  await search.fill('Launch assets');
  await expect(list.getByRole('listitem')).toHaveCount(4);
  await page.setViewportSize(PHONE);
  await views(page, info, 'library-media-phone', list);
  await page.setViewportSize(DESKTOP);
});
