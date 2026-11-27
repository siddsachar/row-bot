import type { Page } from '@playwright/test';
import {
  accessibility,
  assertNoOverflow,
  expect,
  screenshot,
  test,
} from './evidence';
import { asPairedPhone, onPhone, PHONES, tapPath } from './phone-helpers';
import { fixtureHeaders } from './surface-helpers';
import { composer, openConversation } from './unified-helpers';

// Home on a phone (C1, D2–D5, D13): no sub-tabs; Needs you with Approve and
// Deny on an approval's card (answered through the server's own gate, as its
// card answers), Now and Recent, over the Ask box docked above the tab bar.
// Earlier specs in the same backend leave chats and approvals behind, so
// nothing here counts them all: each test finds its own by name.

/** A chat named for this test, with one message sent from its composer. */
async function chatWith(page: Page, title: string, text: string) {
  const response = await page.request.post(
    `/__p3_fixture/named-conversation/${encodeURIComponent(title)}`,
    { headers: fixtureHeaders() },
  );
  expect(response.ok(), await response.text()).toBe(true);
  const { conversation_id: id } = (await response.json()) as {
    conversation_id: string;
  };
  await openConversation(page, id);
  await composer(page).fill(text);
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  return id;
}

/** A chat waiting for approval to commit its changes. */
async function waitingCommit(page: Page, title: string) {
  const id = await chatWith(page, title, 'c1 commit fixture');
  await expect(page.getByText('Commit changes?').first()).toBeVisible();
  return id;
}

async function openHome(page: Page) {
  await page.goto('/app-v2/');
  await expect(
    page.getByRole('heading', { level: 2, name: /^Good / }),
  ).toBeVisible();
}

const needs = (page: Page) => page.getByRole('list', { name: 'Needs you' });
/** The approval card on Home for the chat with this title. */
const card = (page: Page, title: string) =>
  needs(page).getByRole('listitem').filter({ hasText: title });

async function release(page: Page) {
  await page.request.post('/__phone_c1_fixture/release', {
    headers: fixtureHeaders(),
  });
}

/** A silent microphone: a real audio stream that never speaks, for Row-Bot Voice's capture. */
async function syntheticMicrophone(page: Page) {
  await page.addInitScript(() => {
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: {
        getUserMedia: async () => {
          const context = new AudioContext();
          return context.createMediaStreamDestination().stream;
        },
      },
    });
  });
}

test.afterEach(async ({ page }) => {
  await release(page);
});

for (const phone of PHONES)
  test.describe(phone.name, () => {
    onPhone(phone);
    const width = phone.use.viewport.width;

    test.beforeEach(async ({ page }) => {
      await asPairedPhone(page);
    });

    test('approve from Home in one tap and deny in one tap, through the server’s gate', async ({
      page,
    }, info) => {
      const approved = `Release notes ${width}`;
      const denied = `Changelog ${width}`;
      const approvedId = await waitingCommit(page, approved);
      const deniedId = await waitingCommit(page, denied);
      await openHome(page);

      // The card says the action and the chat; no sub-tabs above Home.
      await expect(
        page.getByRole('tablist', { name: 'Home capabilities' }),
      ).toBeHidden();
      await expect(card(page, approved)).toContainText('Commit changes?');
      await expect(card(page, approved)).toContainText(
        'Commit 3 changed files: Tidy the release notes',
      );
      for (const choice of [/Always allow/, /Allow in this chat/, /Auto/])
        await expect(
          card(page, approved).getByRole('button', { name: choice }),
        ).toHaveCount(0);
      await assertNoOverflow(page);
      await screenshot(page, info, `phone-home-needs-you-${width}`);
      await accessibility(page, info, `phone-home-needs-you-${width}`);

      expect(
        await tapPath(page, [
          card(page, approved).getByRole('button', {
            name: 'Approve: Commit changes?',
          }),
        ]),
      ).toBe(1);
      await expect(card(page, approved)).toHaveCount(0);
      expect(
        await tapPath(page, [
          card(page, denied).getByRole('button', {
            name: 'Deny: Commit changes?',
          }),
        ]),
      ).toBe(1);
      await expect(card(page, denied)).toHaveCount(0);

      // Each chat carried on as its card was answered.
      await openConversation(page, approvedId);
      await expect(page.getByText('Synthetic approval resumed.')).toBeVisible();
      await openConversation(page, deniedId);
      await expect(
        page.getByText('You said no, so Row-Bot stopped there.'),
      ).toBeVisible();
    });

    test('a tap on an approval card opens its sheet with the other choices', async ({
      page,
    }, info) => {
      const title = `Site copy ${width}`;
      await waitingCommit(page, title);
      await openHome(page);
      expect(
        await tapPath(page, [
          card(page, title).getByRole('button', {
            name: `Open Commit changes?, ${title}`,
          }),
        ]),
      ).toBe(1);
      const sheet = page.getByRole('dialog', { name: 'Review and approve' });
      await expect(sheet).toContainText(title);
      await expect(
        sheet.getByRole('button', { name: 'More choices' }),
      ).toBeVisible();
      await expect(
        sheet.getByRole('button', { name: 'Details' }),
      ).toBeVisible();
      await screenshot(page, info, `phone-home-approval-sheet-${width}`);
      await sheet.getByRole('button', { name: 'Deny', exact: true }).tap();
      await expect(sheet).toBeHidden();
      await expect(card(page, title)).toHaveCount(0);
    });

    test('Now lists a chat at work and opens it in one tap; Recent opens a chat in one tap', async ({
      page,
    }, info) => {
      const title = `Launch checklist ${width}`;
      const id = await chatWith(page, title, 'c1 working fixture');
      await expect(
        page.getByText('Working through the launch checklist.').first(),
      ).toBeVisible();
      await openHome(page);
      const now = page.getByRole('list', { name: 'Now' });
      await expect(
        now.getByRole('button', { name: `Open ${title}` }),
      ).toBeVisible();
      await expect(now).toContainText('Writing a reply');
      await screenshot(page, info, `phone-home-now-${width}`);
      expect(
        await tapPath(page, [
          now.getByRole('button', { name: `Open ${title}` }),
        ]),
      ).toBe(1);
      await expect(page).toHaveURL(new RegExp(`/conversations/${id}$`));

      await release(page);
      await openHome(page);
      const recent = page
        .getByRole('list', { name: 'Recent' })
        .getByRole('button', { name: `Open ${title}` });
      await recent.scrollIntoViewIfNeeded();
      expect(await tapPath(page, [recent])).toBe(1);
      await expect(page).toHaveURL(new RegExp(`/conversations/${id}$`));
    });

    test('Talk on the docked Ask box starts voice in a new chat in one tap', async ({
      page,
    }, info) => {
      await syntheticMicrophone(page);
      await openHome(page);
      const ask = page.getByRole('textbox', { name: 'Ask Row-Bot' });
      await expect(ask).toBeInViewport();
      await expect(
        page.getByRole('button', { name: 'Add photos or files in a new chat' }),
      ).toBeVisible();
      await expect(
        page.getByRole('button', { name: 'Dictate in a new chat' }),
      ).toBeVisible();
      // The Ask box sits above the tab bar.
      const dock = await ask.boundingBox();
      const tabs = await page
        .getByRole('navigation', { name: 'Main' })
        .boundingBox();
      expect(dock!.y + dock!.height).toBeLessThanOrEqual(tabs!.y);
      await screenshot(page, info, `phone-home-ask-${width}`);

      await expect(
        page.getByRole('button', { name: 'Talk', exact: true }),
      ).toBeEnabled();
      expect(await tapPath(page, ['Talk'])).toBe(1);
      await expect(page).toHaveURL(/\/conversations\/[^/?]+$/);
      const bar = page.getByRole('region', { name: 'Voice conversation' });
      await expect(bar).toHaveAttribute('data-state', 'listening');
      await screenshot(page, info, `phone-home-talk-${width}`);
      await bar.getByRole('button', { name: 'End voice' }).tap();
      await expect(bar).toBeHidden();
    });

    test('the notifications card comes once something has waited, and Not now hides it', async ({
      page,
    }, info) => {
      // A phone's browser that hasn't been asked yet, over HTTPS (staged here).
      await page.addInitScript(() =>
        Object.defineProperty(Notification, 'permission', {
          get: () => 'default',
        }),
      );
      await page.route('**/api/access/devices/push', (route) =>
        route.request().method() === 'GET'
          ? route.fulfill({
              json: {
                available: true,
                public_key: 'BFixtureKey',
                subscribed: false,
                show_details: true,
              },
            })
          : route.fallback(),
      );
      const title = `Notes ${width}`;
      await waitingCommit(page, title);
      await openHome(page);
      const offer = page.getByRole('region', { name: 'Phone notifications' });
      await expect(offer).toContainText('Notify me when needed');
      await expect(
        offer.getByRole('button', { name: 'Turn on' }),
      ).toBeVisible();
      await assertNoOverflow(page);
      await screenshot(page, info, `phone-home-notifications-${width}`);
      expect(
        await tapPath(page, [offer.getByRole('button', { name: 'Not now' })]),
      ).toBe(1);
      await expect(offer).toBeHidden();
      await page.reload();
      await expect(card(page, title)).toBeVisible();
      await expect(offer).toHaveCount(0);
      await card(page, title)
        .getByRole('button', { name: 'Deny: Commit changes?' })
        .tap();
    });

    test('deep links: ?tab=knowledge lists memories; ?tab=monitor and ?tab=insights say to open them on the computer', async ({
      page,
    }, info) => {
      await page.goto('/app-v2/?tab=knowledge');
      await expect(
        page.getByRole('region', { name: 'Knowledge' }),
      ).toBeVisible();
      await expect(page.locator('.knowledge-network-shell')).toHaveCount(0);
      await expect(
        page.getByRole('tablist', { name: 'Home capabilities' }),
      ).toBeHidden();
      await screenshot(page, info, `phone-home-knowledge-${width}`);
      for (const [tab, what] of [
        ['monitor', 'System Monitor'],
        ['insights', 'Insights'],
      ]) {
        await page.goto(`/app-v2/?tab=${tab}`);
        const notice = page.getByRole('region', { name: what });
        await expect(notice).toContainText('Open this on your computer');
        await expect(notice).toContainText(`${what} needs a bigger screen.`);
        await assertNoOverflow(page);
        await screenshot(page, info, `phone-home-${tab}-${width}`);
      }
      expect(await tapPath(page, ['Go to Home'])).toBe(1);
      await expect(
        page.getByRole('heading', { level: 2, name: /^Good / }),
      ).toBeVisible();
    });
  });

test.describe('desktop', () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test('keeps its Home: its tabs, the Ask box and quick starts in the greeting, Review on an approval', async ({
    page,
  }, info) => {
    await waitingCommit(page, 'Desktop release notes');
    await openHome(page);
    // Learning's tab while Learning is on, which it is from the start (O195).
    await expect(
      page.getByRole('tablist', { name: 'Home capabilities' }).getByRole('tab'),
    ).toHaveText([
      'Overview',
      'Workflows',
      'Knowledge',
      'Monitor',
      'Insights',
      'Learning',
    ]);
    await expect(
      page.getByRole('group', { name: 'Quick starts' }),
    ).toBeVisible();
    await expect(page.locator('.phone-home-dock')).toHaveCount(0);
    await expect(page.getByRole('list', { name: 'Now' })).toHaveCount(0);
    const row = needs(page)
      .getByRole('listitem')
      .filter({ hasText: 'Desktop release notes' });
    await expect(
      row.getByRole('button', { name: 'Review: Desktop release notes' }),
    ).toBeVisible();
    await expect(row.getByRole('button', { name: /^Approve/ })).toHaveCount(0);
    await screenshot(page, info, 'phone-home-desktop');
    await row
      .getByRole('button', { name: 'Review: Desktop release notes' })
      .click();
    await page
      .getByRole('dialog', { name: 'Review and approve' })
      .getByRole('button', { name: 'Deny', exact: true })
      .click();
    await expect(row).toHaveCount(0);
  });
});
