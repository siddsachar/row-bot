import type { Page, TestInfo } from '@playwright/test';
import { expect, screenshot, test } from './evidence';
import { fixtureHeaders } from './surface-helpers';
import {
  composer,
  dismissNotices,
  newConversation,
  reloadDocument,
} from './unified-helpers';

/*
 * Generative UI (G1). A reply draws an interface (A2UI 1.0 in a rowbot-ui
 * fence) through the real server's projection; the client lays it out from
 * Row-Bot's own controls. Edits stay on the surface and survive a reload; a
 * press goes to Row-Bot as the surface's action, shown as the button's words.
 */

async function screens(
  page: Page,
  info: TestInfo,
  name: string,
  target: () => ReturnType<Page['locator']>,
) {
  for (const [width, height, size] of [
    [1440, 900, 'desktop'],
    [375, 812, 'phone'],
  ] as const) {
    await page.setViewportSize({ width, height });
    for (const scheme of ['light', 'dark'] as const) {
      await page.emulateMedia({ colorScheme: scheme });
      await target().scrollIntoViewIfNeeded();
      const layout = await page.evaluate(() => ({
        document: document.documentElement.scrollWidth,
        viewport: document.documentElement.clientWidth,
      }));
      expect(layout.document).toBeLessThanOrEqual(layout.viewport + 1);
      await screenshot(page, info, `${name}-${size}-${scheme}`);
    }
  }
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.emulateMedia({ colorScheme: 'light' });
}

async function settled(page: Page) {
  await expect(
    page.getByRole('button', { name: 'Stop', exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole('button', { name: 'Check message', exact: true }),
  ).toHaveCount(0);
}

test.afterEach(async ({ page }) => {
  const reset = await page.request.post('/__f3_fixture/reset', {
    headers: fixtureHeaders(),
  });
  expect(reset.ok(), await reset.text()).toBe(true);
});

test('a reply draws an interface the person edits and presses', async ({
  page,
}, info) => {
  test.skip(
    info.project.name !== 'chromium-desktop',
    'One focused Chromium pass',
  );
  const pressed: unknown[] = [];
  page.on('request', (request) => {
    if (request.method() !== 'POST' || !request.url().endsWith('/commands'))
      return;
    const body = request.postDataJSON() as {
      payload?: { surface_action?: unknown };
    };
    if (body.payload?.surface_action) pressed.push(body.payload.surface_action);
  });
  await newConversation(page);
  await composer(page).fill('genui trip fixture');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  const log = page.getByRole('log', { name: 'Conversation' });
  const trip = log.getByRole('region', { name: 'Lisbon trip' });

  await expect(
    trip.getByRole('heading', { name: 'Lisbon · 12–15 March' }),
  ).toBeVisible();
  // A template: one card per day in the data model.
  await expect(trip.getByText('Thursday')).toBeVisible();
  await expect(trip.getByText('Pena Palace', { exact: false })).toBeVisible();
  await settled(page);
  await dismissNotices(page);
  await screens(page, info, 'genui-days', () => trip);

  // Edits change every part bound to them at once, and send nothing.
  await trip.getByRole('tab', { name: 'Budget' }).click();
  await trip.getByRole('slider', { name: 'Budget for the stay' }).fill('1800');
  await trip.getByRole('button', { name: 'Baixa' }).click();
  await expect(
    trip.getByText('Up to £1,800 for three nights in Baixa'),
  ).toBeVisible();
  expect(pressed).toEqual([]);
  await screens(page, info, 'genui-budget', () => trip);

  // A press goes as the surface's action, with the values it names.
  await trip.getByRole('button', { name: 'Book the hotel' }).click();
  await expect(
    log.getByText('Booked a hotel in Baixa for three nights, within £1,800.'),
  ).toBeVisible();
  expect(pressed).toEqual([
    {
      message_id: expect.any(String),
      surface_id: 'lisbon_trip',
      name: 'book_hotel',
      source_component_id: 'book',
      context: { budget: 1800, area: ['Baixa'] },
    },
  ]);
  // The chat shows the press in the button's words, marked as one.
  await expect(log.getByText('Pressed in an interface')).toBeVisible();
  await expect(log.getByText('Book the hotel · Lisbon trip')).toBeVisible();
  // Its values read as words, never as data.
  await expect(log.getByText('Area: Baixa')).toBeVisible();
  await expect(log.getByText('Budget: 1,800')).toBeVisible();
  await settled(page);
  await dismissNotices(page);
  await screens(page, info, 'genui-pressed', () =>
    log.getByText('Pressed in an interface'),
  );

  // What the person entered is kept with the chat: a reload shows it.
  await reloadDocument(page);
  const again = log.getByRole('region', { name: 'Lisbon trip' });
  await again.getByRole('tab', { name: 'Budget' }).click();
  await expect(
    again.getByRole('slider', { name: 'Budget for the stay' }),
  ).toHaveValue('1800');
  await expect(
    again.getByText('Up to £1,800 for three nights in Baixa'),
  ).toBeVisible();
});
