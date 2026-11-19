import type { Page, TestInfo } from '@playwright/test';
import { assertNoOverflow, expect, screenshot, test } from './evidence';
import { fixtureHeaders } from './surface-helpers';
import { composer, newConversation, revealContext } from './unified-helpers';
import { SIGNED_IN_STATE } from './signed-in';

/*
 * C3 · Show Row-Bot how. In the Browser pane the person starts a demo (the
 * toolbar's button on a desktop, "More browser actions" on a phone), the bar
 * shows it recording with Done, the steps so far list what was kept (a
 * sign-in step keeps nothing), Done asks what it's for, the chat model's
 * scripted draft opens in C1's review sheet with "What you showed", and after
 * Approve, Try it opens a new chat with the request ready and unsent. The
 * page is a picture (no Chromium starts in the fixture), so the recorder's
 * steps come from the fixture as it would describe them. No model is called.
 */

test.use({ actionTimeout: 15_000 });

const SIZES = {
  desktop: { width: 1280, height: 900 },
  phone: { width: 375, height: 812 },
} as const;
const ACCOUNT = 'https://homecover.example/account?session=abc';
const RUNNING = {
  status: 'running',
  url: ACCOUNT,
  title: 'Your account',
  site: 'homecover.example',
  loading: false,
  sign_in: '',
  acting: '',
  waiting: '',
  tabs: [{ id: 't1', title: 'Your account', url: ACCOUNT, active: true }],
};

// The person clicks Statements, types the month, then signs in on a sign-in page.
const STEPS = [
  {
    kind: 'click',
    role: 'link',
    name: 'Statements',
    context: 'Account',
    url: ACCOUNT,
  },
  {
    kind: 'type',
    role: 'textbox',
    name: 'Month',
    value: 'October 2026',
    url: 'https://homecover.example/statements',
  },
  {
    kind: 'type',
    role: 'textbox',
    name: 'Password',
    type: 'password',
    url: 'https://homecover.example/sign-in?next=/statements',
    secret: 'password',
  },
  {
    kind: 'click',
    role: 'button',
    name: 'Download statement',
    url: 'https://homecover.example/statements',
  },
];

async function post(page: Page, path: string, data: unknown = {}) {
  const response = await page.request.post(path, {
    headers: fixtureHeaders(),
    data,
  });
  expect(response.ok(), await response.text()).toBe(true);
  return response.json();
}

function pane(page: Page) {
  return page.getByRole('region', { name: 'Browser' });
}

/** The fixture's account page, pictured at a desktop window's size. */
async function picture(page: Page): Promise<string> {
  const browser = page.context().browser();
  if (!browser) throw new Error('The spec needs its own browser');
  const context = await browser.newContext({
    storageState: SIGNED_IN_STATE,
    viewport: { width: 1280, height: 800 },
  });
  const tab = await context.newPage();
  await tab.goto(new URL('/__n12_fixture/site/account', page.url()).href);
  const jpeg = await tab.screenshot({ type: 'jpeg', quality: 75 });
  await context.close();
  return jpeg.toString('base64');
}

async function views(page: Page, info: TestInfo, name: string) {
  await assertNoOverflow(page);
  await screenshot(page, info, name);
}

for (const size of ['desktop', 'phone'] as const) {
  for (const appearance of ['light', 'dark'] as const) {
    test(`show Row-Bot how: record, review the draft and try it (${size}, ${appearance})`, async ({
      page,
    }, info) => {
      const shot = (name: string) =>
        views(page, info, `${name}-${size}-${appearance}`);
      await page.emulateMedia({ colorScheme: appearance });
      await page.setViewportSize(SIZES.desktop);
      await post(page, '/__c3_fixture/seed');
      const conversation = await newConversation(page);
      const context = await revealContext(page);
      await context
        .getByRole('button', { name: 'Conversation actions' })
        .click();
      await page.getByRole('menuitem', { name: 'Open browser' }).click();
      await expect(pane(page)).toBeVisible();
      await post(page, '/__n12_fixture/browser', {
        conversation_id: conversation,
        state: RUNNING,
        frame: await picture(page),
        width: 1280,
        height: 800,
      });
      await expect(
        pane(page).getByRole('img', { name: 'Live page: Your account' }),
      ).toBeVisible();
      await page.setViewportSize(SIZES[size]);
      if (!(await pane(page).isVisible()))
        await page
          .getByRole('log', { name: 'Conversation' })
          .getByRole('button', { name: 'Open browser' })
          .last()
          .click();
      await expect(pane(page)).toBeVisible();

      // The entry: an icon button with its tooltip, or the phone's menu item.
      if (size === 'desktop') {
        const start = pane(page).getByRole('button', {
          name: /^Show Row-Bot how/,
        });
        await start.hover();
        await expect(page.getByRole('tooltip')).toContainText(
          'Show Row-Bot how',
        );
        await shot('entry');
        await start.click();
      } else {
        await pane(page)
          .getByRole('button', { name: 'More browser actions' })
          .click();
        const item = page.getByRole('menuitem', { name: /Show Row-Bot how/ });
        await expect(item).toBeVisible();
        await shot('entry');
        await item.click();
      }
      const bar = pane(page).getByRole('status', { name: 'Show Row-Bot how' });
      await expect(bar).toContainText('Showing Row-Bot how 1 step');

      await post(page, '/__c3_fixture/steps', {
        conversation_id: conversation,
        steps: STEPS,
      });
      await expect(bar).toContainText('5 steps');
      await expect(
        bar.getByRole('button', { name: /speech model|Narrat/ }),
      ).toBeVisible();
      await shot('recording');

      await bar.getByRole('button', { name: 'Steps so far' }).click();
      const steps = pane(page).getByRole('region', { name: 'Steps so far' });
      await expect(steps.getByRole('listitem')).toHaveCount(5);
      await expect(steps).toContainText('Not kept (a password)');
      await expect(steps).toContainText('October 2026');
      await expect(steps).not.toContainText('session=abc');
      await shot('steps');
      await steps.getByRole('button', { name: 'Hide the steps' }).click();

      await bar.getByRole('button', { name: 'Done' }).click();
      const finish = page.getByRole('dialog', { name: 'What is this for?' });
      await expect(finish).toBeVisible();
      await finish
        .getByRole('textbox', { name: /What is this for/ })
        .fill('Download my monthly statement');
      await shot('finish');

      await finish.getByRole('button', { name: 'Draft skill' }).click();
      const review = page.getByRole('dialog', {
        name: '🏦 Download a statement',
      });
      await expect(review).toBeVisible();
      await review.getByText(/What you showed/).click();
      await expect(review).toContainText(
        'Typed into the “Month” field on homecover.example · an input',
      );
      await expect(review).toContainText("you'll do this part yourself");
      await expect(review).not.toContainText('October 2026');
      await shot('draft');

      await review.getByRole('button', { name: 'Approve' }).click();
      const tryIt = review.getByRole('button', { name: /Try it/ });
      await expect(tryIt).toBeVisible();
      await shot('try-it');

      await tryIt.click();
      await expect(composer(page)).toHaveValue(
        'Do “Download a statement” with:\n- the month: ',
      );
      await shot('try-it-chat');
    });
  }
}

test('a skill drafted by Show Row-Bot how offers Try it when approved later from Settings › Skills (W17)', async ({
  page,
}, info) => {
  await page.setViewportSize(SIZES.desktop);
  await post(page, '/__c3_fixture/seed');
  const conversation = await newConversation(page);
  const context = await revealContext(page);
  await context.getByRole('button', { name: 'Conversation actions' }).click();
  await page.getByRole('menuitem', { name: 'Open browser' }).click();
  await post(page, '/__n12_fixture/browser', {
    conversation_id: conversation,
    state: RUNNING,
    frame: await picture(page),
    width: 1280,
    height: 800,
  });
  await pane(page)
    .getByRole('button', { name: /^Show Row-Bot how/ })
    .click();
  const bar = pane(page).getByRole('status', { name: 'Show Row-Bot how' });
  await expect(bar).toContainText('Showing Row-Bot how 1 step');
  await post(page, '/__c3_fixture/steps', {
    conversation_id: conversation,
    steps: STEPS,
  });
  await expect(bar).toContainText('5 steps');
  await bar.getByRole('button', { name: 'Done' }).click();
  const finish = page.getByRole('dialog', { name: 'What is this for?' });
  await finish
    .getByRole('textbox', { name: /What is this for/ })
    .fill('Download my monthly statement');
  await finish.getByRole('button', { name: 'Draft skill' }).click();
  const review = page.getByRole('dialog', { name: '🏦 Download a statement' });
  await expect(review).toBeVisible();
  // Closed without deciding: the draft waits in Settings › Skills.
  await review.getByRole('button', { name: 'Close skill draft' }).click();
  await expect(review).toHaveCount(0);

  await page.goto('/app-v2/settings/skills');
  const waiting = page.getByRole('region', { name: 'Waiting for your review' });
  await waiting
    .getByRole('button', { name: 'Review Download a statement' })
    .click();
  await review.getByRole('button', { name: 'Approve' }).click();
  const tryIt = review.getByRole('button', { name: /Try it/ });
  await expect(tryIt).toBeVisible();
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    await views(page, info, `try-it-from-settings-desktop-${appearance}`);
  }
  await page.emulateMedia({ colorScheme: 'light' });

  await tryIt.click();
  await expect(composer(page)).toHaveValue(
    'Do “Download a statement” with:\n- the month: ',
  );
});
