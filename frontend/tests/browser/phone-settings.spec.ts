import type { Page, Route } from '@playwright/test';
import {
  accessibility,
  assertNoOverflow,
  expect,
  screenshot,
  test,
} from './evidence';
import { asPairedPhone, onPhone, PHONES, tapPath } from './phone-helpers';

// Settings on a phone (C3, D6 A, D7 A, D13 A): one grouped list (This phone
// with the notifications switch and the theme, Row-Bot's six pages, and More
// settings with every other page), each page under one header row
// "‹ Models" with no second search, Appearance's theme first, Devices'
// Connect under Advanced, and deep links that land with ‹ back to their list.

/**
 * Push over HTTPS, as a paired phone has it (staged: the fixture runs on
 * loopback, the computer's own address). Turning it on is remembered.
 */
async function stagePush(page: Page) {
  // A phone's browser that hasn't been asked yet.
  await page.addInitScript(() =>
    Object.defineProperty(Notification, 'permission', {
      get: () => 'default',
    }),
  );
  let subscribed = false;
  await page.route('**/api/access/devices/push', (route: Route) => {
    const method = route.request().method();
    if (method === 'GET')
      return route.fulfill({
        json: {
          available: true,
          public_key: 'BFixtureKey',
          subscribed,
          show_details: true,
        },
      });
    return route.fallback();
  });
  // Turning on and off records this phone's encrypted address (staged).
  await page.route('**/api/access/devices/push/subscribe', (route: Route) => {
    subscribed = true;
    return route.fulfill({ json: { subscribed: true } });
  });
  await page.route('**/api/access/devices/push/unsubscribe', (route: Route) => {
    subscribed = false;
    return route.fulfill({ json: { subscribed: false } });
  });
}

/** The browser grants push without a real push service (staged). */
async function grantPush(page: Page) {
  await page.addInitScript(() => {
    Notification.requestPermission = async () => 'granted';
    Object.defineProperty(ServiceWorkerContainer.prototype, 'ready', {
      get: () =>
        Promise.resolve({
          pushManager: {
            subscribe: async () => ({
              toJSON: () => ({
                endpoint: 'https://push.invalid/c3-fixture',
                keys: { p256dh: 'BKey', auth: 'auth' },
              }),
            }),
            getSubscription: async () => null,
          },
        }),
    });
  });
}

const tabBar = (page: Page) => page.getByRole('navigation', { name: 'Main' });
const settingsTab = (page: Page) =>
  tabBar(page).getByRole('link', { name: 'Settings' });
const title = (page: Page) => page.getByRole('heading', { level: 1 });
const thisPhone = (page: Page) =>
  page.getByRole('list', { name: 'This phone' });
const rowBot = (page: Page) => page.getByRole('list', { name: 'Row-Bot' });
const notifications = (page: Page) =>
  thisPhone(page).getByRole('switch', { name: 'Notifications' });

const ROW_BOT = [
  'Models',
  'Voice',
  'Memory',
  'Apps',
  'Activity',
  'Buddy & Proactive',
];

async function openHome(page: Page) {
  await page.goto('/app-v2/');
  await expect(
    page.getByRole('heading', { level: 2, name: /^Good / }),
  ).toBeVisible();
}

/**
 * One header row: the page's name and ‹, and no settings search or second
 * title (a page's own search, as Apps has, stays).
 */
async function expectOneHeaderRow(page: Page, name: string, back: string) {
  await expect(title(page)).toHaveText(name);
  await expect(page.getByRole('link', { name: back })).toBeInViewport();
  await expect(
    page.getByRole('searchbox', { name: 'Find a setting' }),
  ).toHaveCount(0);
  await expect(
    page
      .locator('.settings-pane-header')
      .getByRole('heading', { name, exact: true }),
  ).toHaveCount(0);
}

for (const phone of PHONES)
  test.describe(phone.name, () => {
    onPhone(phone);
    const width = phone.use.viewport.width;

    test.beforeEach(async ({ page }) => {
      await asPairedPhone(page);
      await stagePush(page);
    });

    test('one list: This phone’s switch and theme, Row-Bot’s six pages, More settings; Dark is two taps', async ({
      page,
    }, info) => {
      await openHome(page);
      expect(await tapPath(page, [settingsTab(page)])).toBe(1);
      await expect(page).toHaveURL(/\/app-v2\/settings$/);
      await expect(title(page)).toHaveText('Settings');
      await expect(notifications(page)).toBeEnabled();
      await expect(notifications(page)).toHaveAccessibleDescription(
        'When Row-Bot needs you',
      );
      await expect(rowBot(page).getByRole('link')).toHaveText(ROW_BOT);
      await expect(
        page.getByRole('link', { name: 'More settings' }),
      ).toBeInViewport();
      // The app header's Search finds settings: no second field here.
      await expect(page.getByRole('searchbox')).toHaveCount(0);
      await assertNoOverflow(page);
      await screenshot(page, info, `phone-settings-list-${width}`);
      await accessibility(page, info, `phone-settings-list-${width}`);

      // Theme: Settings → Dark (two taps, where it was three and a scroll).
      const theme = thisPhone(page).getByRole('radiogroup', { name: 'Theme' });
      expect(
        await tapPath(page, [theme.getByRole('radio', { name: 'Dark' })]),
      ).toBe(1);
      await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
      await expect(theme.getByRole('radio', { name: 'Dark' })).toHaveAttribute(
        'aria-checked',
        'true',
      );
      await screenshot(page, info, `phone-settings-list-dark-${width}`);
    });

    test('notifications: Settings → the switch turns them on through the browser’s own prompt', async ({
      page,
    }) => {
      await grantPush(page);
      await page.goto('/app-v2/settings');
      await expect(notifications(page)).toBeEnabled();
      expect(await tapPath(page, [notifications(page)])).toBe(1);
      await expect(notifications(page)).toBeChecked();
      await page.reload();
      await expect(notifications(page)).toBeChecked();
    });

    test('each Row-Bot page is one header row “‹ Name” with no search, and ‹ goes back to the list', async ({
      page,
    }, info) => {
      await page.goto('/app-v2/settings');
      for (const name of ROW_BOT) {
        expect(
          await tapPath(page, [rowBot(page).getByRole('link', { name })]),
        ).toBe(1);
        await expectOneHeaderRow(page, name, 'Back to Settings');
        if (name === 'Models')
          await screenshot(page, info, `phone-settings-models-${width}`);
        if (name === 'Buddy & Proactive') {
          // Buddy's status, which a computer shows in the sidebar's card.
          await expect(
            page.getByRole('region', { name: / now$/ }).getByRole('status'),
          ).toBeVisible();
          await screenshot(page, info, `phone-settings-buddy-${width}`);
          expect(
            await tapPath(page, [
              page
                .getByRole('list', { name: 'Proactive' })
                .getByRole('link', { name: 'Proactive' }),
            ]),
          ).toBe(1);
          await expectOneHeaderRow(
            page,
            'Proactive',
            'Back to Buddy & Proactive',
          );
          await page
            .getByRole('link', { name: 'Back to Buddy & Proactive' })
            .tap();
          await expect(title(page)).toHaveText('Buddy & Proactive');
        }
        await assertNoOverflow(page);
        await page.getByRole('link', { name: 'Back to Settings' }).tap();
        await expect(title(page)).toHaveText('Settings');
      }
    });

    test('More settings reaches every other page; Updates is three taps and goes back to More settings', async ({
      page,
    }, info) => {
      await openHome(page);
      expect(
        await tapPath(page, [settingsTab(page), 'More settings', 'Updates']),
      ).toBe(3);
      await expectOneHeaderRow(page, 'Updates', 'Back to More settings');
      await page.getByRole('link', { name: 'Back to More settings' }).tap();
      await expect(title(page)).toHaveText('More settings');
      const more = page.locator('.phone-settings');
      for (const name of [
        'Preferences',
        'Appearance',
        'Documents',
        'Tracker',
        'Tools',
        'Skills',
        'System',
        'Devices & remote access',
        'Updates',
        'Data',
      ])
        await expect(
          more.getByRole('link', { name, exact: true }),
        ).toBeAttached();
      await assertNoOverflow(page);
      await screenshot(page, info, `phone-settings-more-${width}`);
      await page.getByRole('link', { name: 'Back to Settings' }).tap();
      await expect(thisPhone(page)).toBeVisible();
    });

    test('Appearance shows the theme first; Devices keeps Connect under Advanced', async ({
      page,
    }, info) => {
      await page.goto('/app-v2/settings/appearance');
      await expectOneHeaderRow(page, 'Appearance', 'Back to More settings');
      const theme = page.getByRole('radiogroup', { name: 'Theme' });
      await expect(theme).toBeInViewport();
      const accent = page.getByRole('combobox', { name: /^Colour theme/ });
      expect((await theme.boundingBox())!.y).toBeLessThan(
        (await accent.boundingBox())!.y,
      );
      await expect(page.locator('.appearance-mini')).toHaveCount(0);
      await screenshot(page, info, `phone-settings-appearance-${width}`);

      await page.goto('/app-v2/settings/access');
      await expectOneHeaderRow(
        page,
        'Devices & remote access',
        'Back to More settings',
      );
      const advanced = page.locator('.settings-advanced').filter({
        has: page.locator('summary', { hasText: 'Advanced' }),
      });
      const connect = advanced.getByRole('heading', {
        name: 'Connect a phone or computer',
      });
      await expect(connect).toBeHidden();
      await advanced.locator('summary').first().tap();
      await expect(connect).toBeVisible();
      await expect(
        page.getByRole('heading', { name: 'Connect a phone or computer' }),
      ).toHaveCount(1);
      await connect.scrollIntoViewIfNeeded();
      await assertNoOverflow(page);
      await screenshot(page, info, `phone-settings-devices-${width}`);
    });

    test('deep links open their page with ‹ back to its list, and Home’s card leads to This phone', async ({
      page,
    }) => {
      await page.goto('/app-v2/settings/knowledge');
      await expectOneHeaderRow(page, 'Memory', 'Back to Settings');
      await page.goto('/app-v2/settings/access#notifications');
      await expectOneHeaderRow(
        page,
        'Devices & remote access',
        'Back to More settings',
      );
      await expect(
        page.getByRole('switch', { name: 'Notifications on this device' }),
      ).toBeInViewport();
      await page.getByRole('link', { name: 'Back to More settings' }).tap();
      await expect(title(page)).toHaveText('More settings');
      // An old link to a moved page still lands, with its way back.
      await page.goto('/app-v2/settings/theme');
      await expectOneHeaderRow(page, 'Appearance', 'Back to More settings');

      // Home's card, once notifications are on, says where to change them.
      await grantPush(page);
      await page.addInitScript(() =>
        localStorage.setItem('row-bot:phone-notifications-waited', 'yes'),
      );
      await openHome(page);
      const offer = page.getByRole('region', { name: 'Phone notifications' });
      await offer.getByRole('button', { name: 'Turn on' }).tap();
      await expect(offer).toContainText('Notifications are on for this phone');
      await offer.getByRole('link', { name: 'Settings' }).tap();
      await expect(page).toHaveURL(/\/app-v2\/settings#notifications$/);
      await expect(notifications(page)).toBeInViewport();
      await expect(notifications(page)).toBeChecked();
    });
  });

test.describe('desktop', () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test('keeps the side list with its search, each page’s own title, the theme previews and Connect on top', async ({
    page,
  }, info) => {
    await page.goto('/app-v2/settings');
    await expect(page).toHaveURL(/\/app-v2\/settings\/models$/);
    const navigation = page.getByRole('navigation', {
      name: 'Settings sections',
    });
    await expect(
      navigation.getByRole('searchbox', { name: 'Find a setting' }),
    ).toBeVisible();
    await expect(
      navigation.getByRole('link', { name: 'Updates' }),
    ).toBeVisible();
    await expect(page.getByRole('heading', { level: 1 })).toHaveText(
      'Settings',
    );
    await expect(
      page.getByRole('heading', { level: 2, name: 'Models' }),
    ).toBeVisible();
    await expect(page.getByRole('list', { name: 'This phone' })).toHaveCount(0);
    await expect(
      page.getByRole('link', { name: 'Close settings' }),
    ).toBeVisible();
    await screenshot(page, info, 'phone-settings-desktop-models');

    await navigation.getByRole('link', { name: 'Appearance' }).click();
    await expect(page.locator('.appearance-mini')).toHaveCount(3);
    await expect(page.getByRole('radiogroup', { name: 'Theme' })).toHaveCount(
      0,
    );

    await navigation
      .getByRole('link', { name: 'Devices & remote access' })
      .click();
    await expect(
      page.getByRole('button', { name: 'Connect a phone or computer' }),
    ).toBeVisible();
  });
});
