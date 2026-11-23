import type { Locator, Page, Route, TestInfo } from '@playwright/test';
import { assertNoOverflow, expect, screenshot, test } from './evidence';
import { asPairedPhone, onPhone, PHONES } from './phone-helpers';
import { fixtureHeaders } from './surface-helpers';
import { dismissNotices } from './unified-helpers';

/*
 * The Conversation library and the phone's Chats tab while they load: rows in
 * the list's own shape, then the rows in their place with nothing moving
 * (PR "Library and Chats open fast"). The reads are held so the loading state
 * can be seen; nothing leaves the machine.
 */

/**
 * Holds the requests `matches` picks until the returned function lets them
 * go: on to the backend, or answered with `answer`.
 */
async function hold(
  page: Page,
  matches: (url: URL) => boolean,
  answer?: unknown,
): Promise<() => void> {
  let release!: () => void;
  const released = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route(
    (url) => matches(url),
    async (route: Route) => {
      await released;
      await (
        answer === undefined
          ? route.continue()
          : route.fulfill({ json: answer })
      ).catch(() => undefined);
    },
  );
  return release;
}
/** The Library's own read: its largest pages, not the sidebar's first. */
const libraryRead = (url: URL) =>
  url.pathname.endsWith('/api/v1/conversations') &&
  url.searchParams.get('limit') === '200';

async function seed(page: Page, info: TestInfo, who: string): Promise<void> {
  for (const title of ['Loading spec trip plans', 'Loading spec budget'])
    expect(
      (
        await page.request.post(
          `/__p3_fixture/named-conversation/${encodeURIComponent(`${title} ${who} ${info.project.name} ${info.retry}`)}`,
          { headers: fixtureHeaders() },
        )
      ).ok(),
    ).toBe(true);
}
/**
 * A sound for Files to list, answered in place of the backend's outputs list
 * so that other specs' media counts stay their own.
 */
const recording = (who: string) => {
  const at = new Date().toISOString().replace(/\.\d+Z$/, 'Z');
  const version = {
    ref: 'loading-spec:00000000-0000-4000-8000-000000000001',
    version: 1,
    role: 'final',
    file_name: `Loading spec ${who}.wav`,
    mime_type: 'audio/wav',
    size: 16_000,
    created_at: at,
  };
  return {
    items: [
      {
        ...version,
        id: '0'.repeat(24),
        conversation_id: 'loading-spec',
        conversation_title: 'Loading spec recordings',
        name: `Loading spec ${who}`,
        kind: 'audio',
        versions: [version],
        version_count: 1,
        working_count: 0,
        updated_at: at,
        tags: [],
      },
    ],
  };
};

/** Light and dark screenshots of what is on screen now. */
async function views(page: Page, info: TestInfo, name: string) {
  await dismissNotices(page);
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

/** The loading rows, shown once a read takes longer than a moment. */
async function loadingRows(page: Page, label: string): Promise<Locator> {
  const loading = page.getByRole('status', { name: label });
  await expect(loading).toHaveAttribute('aria-busy', 'true');
  await expect(loading.locator('.skeleton-row').first()).toBeVisible();
  return loading;
}

const top = async (locator: Locator) => (await locator.boundingBox())!.y;

test('the Conversation library shows rows in its own shape while it loads, then its rows in their place', async ({
  page,
}, info) => {
  await seed(page, info, 'desktop');
  const release = await hold(page, libraryRead);
  await page.goto('/app-v2/library');
  const loading = await loadingRows(page, 'Loading all conversations');
  await expect(page.getByText('Loading conversations…')).toBeVisible();
  const placeholder = await top(loading.locator('.skeleton-row').first());
  await views(page, info, 'library-loading');

  release();
  const library = page.getByRole('region', {
    name: 'Conversation library',
    exact: true,
  });
  await expect(
    library.getByText(/ conversations · newest first$/),
  ).toBeVisible();
  await expect(loading).toHaveCount(0);
  // The first row takes the first placeholder's place.
  const first = await top(library.locator('.library-row').first());
  expect(Math.abs(first - placeholder)).toBeLessThanOrEqual(2);
  await views(page, info, 'library-loaded');
});

for (const phone of PHONES.slice(1))
  test.describe(phone.name, () => {
    onPhone(phone);

    test('Chats and Files show rows in their own shape while they load, then their rows in place', async ({
      page,
    }, info) => {
      const who = phone.name.replace(' ', '-');
      await seed(page, info, who);
      await asPairedPhone(page);
      const chats = await hold(page, libraryRead);
      await page.goto('/app-v2/library');
      const loading = await loadingRows(page, 'Loading all conversations');
      const placeholder = await top(loading.locator('.skeleton-row').first());
      await views(page, info, 'phone-chats-loading');

      chats();
      const library = page.getByRole('region', {
        name: 'Conversation library',
        exact: true,
      });
      await expect(library.locator('.phone-row').first()).toBeVisible();
      await expect(loading).toHaveCount(0);
      const first = await top(library.locator('.phone-row').first());
      expect(Math.abs(first - placeholder)).toBeLessThanOrEqual(2);
      await views(page, info, 'phone-chats-loaded');

      const media = await hold(
        page,
        (url) => url.pathname.endsWith('/api/v1/outputs'),
        recording(who),
      );
      await page.getByRole('radio', { name: 'Files' }).click();
      const files = await loadingRows(page, 'Loading files');
      const filePlaceholder = (await files
        .locator('.skeleton-row')
        .first()
        .boundingBox())!;
      await views(page, info, 'phone-files-loading');
      media();
      const firstFile = page
        .getByRole('list', { name: 'Files' })
        .locator('.phone-row')
        .first();
      await expect(firstFile).toBeVisible();
      await expect(files).toHaveCount(0);
      // The first file takes the first placeholder's place, at its height.
      const fileRow = (await firstFile.boundingBox())!;
      expect(Math.abs(fileRow.y - filePlaceholder.y)).toBeLessThanOrEqual(2);
      expect(
        Math.abs(fileRow.height - filePlaceholder.height),
      ).toBeLessThanOrEqual(1);
      await views(page, info, 'phone-files-loaded');
    });
  });
