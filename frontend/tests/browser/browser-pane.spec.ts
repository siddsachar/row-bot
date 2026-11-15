import type { Page, TestInfo } from '@playwright/test';
import { assertNoOverflow, expect, screenshot, test } from './evidence';
import { fixtureHeaders } from './surface-helpers';
import { composer, newConversation, revealContext } from './unified-helpers';
import { SIGNED_IN_STATE } from './signed-in';

/*
 * N12-A · Row-Bot's browser in the app. The Browser pane, its live stream,
 * the person's controls, the chat's live step, the payment approval and
 * Settings › Browser are real; the page is a picture the spec renders from
 * the task suite's fixture pages, at the size the engine gives the page (the
 * pane's), and hands the synthetic engine (no Chromium starts in the
 * fixture). Every state at desktop and phone, light and dark.
 */

const PHONE = { width: 390, height: 844 };
const DESKTOP = { width: 1280, height: 900 };
// A resize to a phone can put the pane over the chat just after a settle saw
// the chat: an action then waits for a step the pane hides. Bounded, it fails
// and the settle's retry goes back to the conversation; unbounded, it waited
// out the test (seen on CI).
test.use({ actionTimeout: 15_000 });
type Still = 'account' | 'claim' | 'sign-in' | 'checkout';

const RUNNING = {
  status: 'running',
  url: 'https://homecover.example/claim/1',
  title: 'Make a claim · 1 of 5',
  site: 'homecover.example',
  loading: false,
  sign_in: '',
  acting: '',
  waiting: '',
  tabs: [
    {
      id: 't1',
      title: 'Make a claim · 1 of 5',
      url: 'https://homecover.example/claim/1',
      active: true,
    },
  ],
};

type Fit = { width: number; height: number; scale: number };
const DESKTOP_PAGE: Fit = { width: 1280, height: 800, scale: 1 };

/** The fixture page as Row-Bot's browser shows it: a JPEG of the page at its size and pixel ratio. */
async function still(page: Page, name: Still, fit: Fit): Promise<string> {
  const browser = page.context().browser();
  if (!browser)
    throw new Error('The spec needs its own browser to picture pages');
  const context = await browser.newContext({
    storageState: SIGNED_IN_STATE,
    viewport: { width: fit.width, height: fit.height },
    deviceScaleFactor: fit.scale,
  });
  const tab = await context.newPage();
  await tab.goto(new URL(`/__n12_fixture/site/${name}`, page.url()).href);
  const jpeg = await tab.screenshot({ type: 'jpeg', quality: 75 });
  await context.close();
  return jpeg.toString('base64');
}

async function show(
  page: Page,
  conversation: string,
  state: Record<string, unknown>,
  frame?: { image: string; fit: Fit },
) {
  const response = await page.request.post('/__n12_fixture/browser', {
    headers: fixtureHeaders(),
    data: {
      conversation_id: conversation,
      state,
      frame: frame?.image,
      width: frame?.fit.width,
      height: frame?.fit.height,
    },
  });
  expect(response.ok()).toBe(true);
}

function pane(page: Page) {
  return page.getByRole('region', { name: 'Browser' });
}

/** The page's size in the engine: the pane's while a pane shows it. */
async function pageFit(page: Page, conversation: string): Promise<Fit> {
  const response = await page.request.get(
    `/__n12_fixture/browser/size?conversation_id=${encodeURIComponent(conversation)}`,
    { headers: fixtureHeaders() },
  );
  expect(response.ok()).toBe(true);
  return (await response.json()) as Fit;
}

/**
 * Once a resize settles the page has the pane's size (a phone's width at
 * least: narrower, the picture scales down), and a desktop window's when no
 * pane shows it.
 */
async function settledFit(page: Page, conversation: string): Promise<Fit> {
  let fit = DESKTOP_PAGE;
  await expect(async () => {
    fit = await pageFit(page, conversation);
    const area = pane(page).locator('.browser-viewport');
    if (!(await area.isVisible())) {
      expect(fit).toEqual(DESKTOP_PAGE);
      return;
    }
    const box = await area.boundingBox();
    const width = Math.round(box?.width ?? 0);
    const height = Math.round(box?.height ?? 0);
    expect(fit.width).toBe(Math.max(360, width));
    expect(fit.height).toBe(
      width < 360 ? Math.round((height * 360) / width) : height,
    );
  }).toPass();
  return fit;
}

/** Show the page: the fixture page pictured at the size the engine gives it now. */
async function paint(
  page: Page,
  conversation: string,
  state: Record<string, unknown>,
  name: Still,
) {
  const fit = await settledFit(page, conversation);
  await show(page, conversation, state, {
    image: await still(page, name, fit),
    fit,
  });
}

/** Pane size → page size → picture: the live page fills the pane 1:1. */
async function expectOneToOne(page: Page, conversation: string) {
  const fit = await settledFit(page, conversation);
  const area = await pane(page).locator('.browser-viewport').boundingBox();
  await expect(async () => {
    const [natural, shown] = await pane(page)
      .locator('.browser-picture')
      .evaluate((img: HTMLImageElement) => [
        img.naturalWidth,
        img.getBoundingClientRect().width,
      ]);
    expect(natural).toBe(Math.round(fit.width * fit.scale));
    expect(Math.abs(shown - (area?.width ?? 0))).toBeLessThan(1);
  }).toPass();
}

async function openPane(page: Page) {
  const context = await revealContext(page);
  await context.getByRole('button', { name: 'Conversation actions' }).click();
  await page.getByRole('menuitem', { name: 'Open browser' }).click();
  await expect(pane(page)).toBeVisible();
}

/** On a phone the pane fills the screen: open it from the chat if the chat shows. */
async function paneInView(page: Page) {
  if (await pane(page).isVisible()) return;
  const open = page
    .getByRole('log', { name: 'Conversation' })
    .getByRole('button', { name: 'Open browser' })
    .last();
  if (await open.isVisible()) await open.click();
  else
    await page
      .getByRole('tab', { name: /Browser/ })
      .first()
      .click();
  await expect(pane(page)).toBeVisible();
}

/** On a phone the pane covers the chat: go back to the conversation. */
async function chatInView(page: Page) {
  const back = page.getByRole('button', { name: 'Back to conversation' });
  if (await back.isVisible()) await back.click();
  await expect(page.getByRole('log', { name: 'Conversation' })).toBeVisible();
}

async function views(page: Page, info: TestInfo, name: string) {
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

async function everywhere(
  page: Page,
  info: TestInfo,
  name: string,
  settle: () => Promise<void> = async () => {},
  repaint: () => Promise<void> = async () => {},
) {
  await expect(settle).toPass();
  await repaint();
  await views(page, info, name);
  await page.setViewportSize(PHONE);
  await expect(settle).toPass();
  await repaint();
  await views(page, info, `${name}-phone`);
  await page.setViewportSize(DESKTOP);
}

test('the Browser pane in every state, from nothing open to a crash', async ({
  page,
}, info) => {
  await page.setViewportSize(DESKTOP);
  const conversation = await newConversation(page);
  await openPane(page);

  await expect(pane(page)).toContainText('Nothing open yet');
  await expect(
    pane(page).getByRole('textbox', { name: 'Address' }),
  ).toBeVisible();
  await everywhere(page, info, 'pane-empty', () => paneInView(page));

  await show(page, conversation, { status: 'starting' });
  await expect(pane(page)).toContainText("Starting Row-Bot's browser");
  await everywhere(page, info, 'pane-starting', () => paneInView(page));

  const loading = { ...RUNNING, loading: true };
  await paint(page, conversation, loading, 'claim');
  await expect(
    pane(page).getByRole('img', { name: 'Live page: Make a claim · 1 of 5' }),
  ).toBeVisible();
  await expect(
    pane(page).getByRole('progressbar', { name: 'Loading the page' }),
  ).toBeVisible();
  await expect(
    pane(page).getByRole('button', { name: 'Stop loading' }),
  ).toBeVisible();
  // The page lays out at the docked pane's size and fills it 1:1; so on a phone.
  await expectOneToOne(page, conversation);
  await everywhere(
    page,
    info,
    'pane-loading',
    () => paneInView(page),
    async () => {
      await paint(page, conversation, loading, 'claim');
      await expectOneToOne(page, conversation);
    },
  );

  const signIn = {
    ...RUNNING,
    url: 'https://homecover.example/login',
    title: 'Sign in',
    sign_in: 'password',
  };
  await paint(page, conversation, signIn, 'sign-in');
  await expect(pane(page)).toContainText(
    "Sign in here. Row-Bot doesn't type passwords: click the page and sign in.",
  );
  // One shared page: nothing to take over or give back.
  await expect(
    pane(page).getByRole('button', { name: /Take control|Give back/ }),
  ).toHaveCount(0);
  await everywhere(
    page,
    info,
    'pane-sign-in',
    () => paneInView(page),
    () => paint(page, conversation, signIn, 'sign-in'),
  );

  // A code to paste: the menu pastes the clipboard as the page's own paste, beside the keyboard's.
  const pasteItem = page.getByRole('menuitem', { name: 'Paste into the page' });
  await everywhere(page, info, 'pane-paste-menu', async () => {
    await paneInView(page);
    if (!(await pasteItem.isVisible()))
      await pane(page)
        .getByRole('button', { name: 'More browser actions' })
        .click();
    await expect(pasteItem).toBeVisible();
  });
  await page.keyboard.press('Escape');
  await expect(pasteItem).toHaveCount(0);

  // Signing in: the person just uses the page, and moving the mouse there takes nothing over.
  await pane(page).locator('.browser-viewport').hover();
  await page.mouse.move(40, 40);
  await expect(pane(page)).not.toContainText('You have control');

  await show(page, conversation, { status: 'crashed', tabs: [] });
  const crash = pane(page).getByRole('alert');
  await expect(crash).toContainText("Row-Bot's browser stopped. Restart it?");
  await everywhere(page, info, 'pane-crash', () => paneInView(page));
  await crash.getByRole('button', { name: 'Restart' }).click();
  await expect(pane(page).getByRole('alert')).toHaveCount(0);
});

test('when Row-Bot asks for something on the page, the question and its answer show above it', async ({
  page,
}, info) => {
  await page.setViewportSize(DESKTOP);
  const conversation = await newConversation(page);
  await openPane(page);
  const signIn = {
    ...RUNNING,
    url: 'https://homecover.example/login',
    title: 'Sign in',
    sign_in: 'password',
  };
  await paint(page, conversation, signIn, 'sign-in');
  try {
    await composer(page).fill('n4 question fixture');
    await page.getByRole('button', { name: 'Send', exact: true }).click();
    await expect(pane(page)).toContainText(
      'Row-Bot is waiting for you. When should the beta go out?',
    );
    const answer = pane(page).getByRole('button', { name: /Beta\.1 now/ });
    await expect(answer).toBeEnabled();
    await everywhere(
      page,
      info,
      'pane-asks-you',
      () => paneInView(page),
      () => paint(page, conversation, signIn, 'sign-in'),
    );
    await answer.click();
    await expect(
      page
        .getByRole('log', { name: 'Conversation' })
        .getByText("Thanks. I'll plan the beta that way.", { exact: true }),
    ).toBeVisible();
    await expect(pane(page)).not.toContainText('Row-Bot is waiting for you.');
  } finally {
    await page.request.post('/__n4_fixture/reset', {
      headers: fixtureHeaders(),
    });
  }
});

test('a turn using the browser shows live in the chat, then asks before paying', async ({
  page,
}, info) => {
  await page.setViewportSize(DESKTOP);
  const conversation = await newConversation(page);
  await openPane(page);
  await composer(page).fill('n12 claim fixture');
  await page.getByRole('button', { name: 'Send', exact: true }).click();

  const working = {
    ...RUNNING,
    url: 'https://homecover.example/account',
    title: 'Your cover',
    acting: 'Clicking link "Make a claim"',
  };
  await paint(page, conversation, working, 'account');
  const transcript = page.getByRole('log', { name: 'Conversation' });
  const step = transcript.locator('.browser-step');
  await expect(step).toContainText('Using the browser · homecover.example');
  await expect(step).toContainText('Clicking "Make a claim"');
  await expect(pane(page)).toContainText('Row-Bot: Clicking "Make a claim"');
  await expect(
    pane(page).getByRole('button', { name: 'Take control' }),
  ).toHaveCount(0);
  // With the pane hidden on a phone the page is a desktop window again: the step shows it so.
  await everywhere(
    page,
    info,
    'agent-working',
    async () => {
      await chatInView(page);
      await step.scrollIntoViewIfNeeded();
    },
    () => paint(page, conversation, working, 'account'),
  );
  await everywhere(
    page,
    info,
    'agent-working-pane',
    () => paneInView(page),
    () => paint(page, conversation, working, 'account'),
  );

  await paint(
    page,
    conversation,
    {
      ...RUNNING,
      url: 'https://homecover.example/claim/5',
      title: 'Make a claim · 5 of 5',
      waiting: 'Waiting for your OK in the chat',
    },
    'checkout',
  );
  const released = await page.request.post('/__n12_fixture/release', {
    headers: fixtureHeaders(),
    data: { conversation_id: conversation },
  });
  expect(released.ok()).toBe(true);
  const card = page.getByRole('complementary', {
    name: 'Approval required: Pay on homecover.example?',
  });
  await expect(card).toContainText(
    'Row-Bot will click "Confirm and pay £60" on homecover.example. This can charge you.',
  );
  await expect(
    card.getByRole('button', { name: 'Open the browser: homecover.example' }),
  ).toBeVisible();
  await expect(pane(page)).toContainText('Waiting for your OK in the chat');
  await everywhere(page, info, 'payment-approval', async () => {
    await chatInView(page);
    await card.scrollIntoViewIfNeeded();
  });
  await card.getByRole('button', { name: 'Approve', exact: true }).click();
  await expect(
    transcript.getByText(/Booked: a plumber comes tomorrow/),
  ).toBeVisible();
});

test('Settings › Browser lists the signed-in sites with Sign out', async ({
  page,
}, info) => {
  await page.setViewportSize(DESKTOP);
  await page.goto('/app-v2/settings/system');
  const item = page.locator('[data-setting-anchor="browser-computer-use"]');
  await item.getByRole('button', { name: 'Set up…' }).click();
  const dialog = page.getByRole('dialog', { name: 'Browser and computer use' });
  await expect(dialog).toContainText('Signed-in sites');
  await expect(dialog.getByText('homecover.example')).toBeVisible();
  await everywhere(page, info, 'settings-browser', () =>
    dialog.getByText('Signed-in sites').scrollIntoViewIfNeeded(),
  );
  await dialog.getByRole('button', { name: 'Sign out of github.com' }).click();
  await expect(dialog.getByText('github.com')).toHaveCount(0);
});

test('popped out: only this conversation’s browser, full size', async ({
  page,
}, info) => {
  await page.setViewportSize(DESKTOP);
  const conversation = await newConversation(page);
  await paint(page, conversation, RUNNING, 'claim');
  await page.goto(`/app-v2/browser/${encodeURIComponent(conversation)}`);
  await expect(
    pane(page).getByRole('img', { name: 'Live page: Make a claim · 1 of 5' }),
  ).toBeVisible();
  // Its own window: the page takes the window's size.
  await paint(page, conversation, RUNNING, 'claim');
  await expectOneToOne(page, conversation);
  await expect(pane(page).getByRole('button', { name: 'Pop out' })).toHaveCount(
    0,
  );
  await views(page, info, 'pop-out');
});
