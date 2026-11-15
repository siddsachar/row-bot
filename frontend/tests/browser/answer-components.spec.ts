import type { Page, TestInfo } from '@playwright/test';
import { expect, screenshot, test } from './evidence';
import { openFixture, type FixtureWindow } from './fixture';
import {
  composer,
  dismissNotices,
  newConversation,
  reloadDocument,
} from './unified-helpers';

/*
 * F3 · Answer components. Markdown tables sort and task lists tick in any
 * answer; a reply's rowbot-* fences become a table, chart, checklist,
 * figures, progress and choices through the real server's projection, and a
 * reply button sends its words as the person's own message, nothing else.
 */

type Block = { id: string; type: string; [key: string]: unknown };

/** Shows one assistant answer made of these blocks in the open fixture conversation. */
async function showAnswer(page: Page, blocks: Block[]) {
  await page.evaluate((answer) => {
    const { controller } = (window as FixtureWindow).__ROW_BOT_FIXTURE__;
    const projection = controller.getSnapshot().projection!;
    const base = projection.rows[0];
    const row = {
      ...base,
      id: 'answer-components',
      message_id: 'answer-components',
      role: 'assistant' as const,
      blocks: answer,
      traces: [],
    };
    (controller as unknown as { update(patch: unknown): void }).update({
      history: null,
      projection: { ...projection, rows: [row] },
    });
  }, blocks);
}

/** Desktop and phone, light and dark, of what is on screen; never wider than the screen. */
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

/** The turn has finished: no Stop in the composer, every chart drawn. */
async function settled(page: Page) {
  await expect(
    page.getByRole('button', { name: 'Stop', exact: true }),
  ).toHaveCount(0);
  await expect(
    page.locator('.rich-chart-plot:not([data-status="ready"])'),
  ).toHaveCount(0);
  // The last message is confirmed: the composer (and so a reply button) can send.
  await expect(
    page.getByRole('button', { name: 'Check message', exact: true }),
  ).toHaveCount(0);
}

/** Sends a message from the composer, as the person does. */
async function ask(page: Page, text: string) {
  await composer(page).fill(text);
  await page.getByRole('button', { name: 'Send', exact: true }).click();
}

test('a reply’s components render from the server, and a choice sends the person’s words', async ({
  page,
}, info) => {
  test.skip(
    info.project.name !== 'chromium-desktop',
    'One focused Chromium pass',
  );
  const submitted: string[] = [];
  page.on('request', (request) => {
    if (request.method() !== 'POST' || !request.url().endsWith('/commands'))
      return;
    const body = request.postDataJSON() as {
      type?: string;
      payload?: { text?: string; tapped_choice?: { label?: string } };
    };
    const tap = body.payload?.tapped_choice;
    submitted.push(
      `${body.type}:${body.payload?.text ?? ''}${tap ? ` [tap: ${tap.label}]` : ''}`,
    );
  });
  await newConversation(page);
  await ask(page, 'f3 components fixture');
  const log = page.getByRole('log', { name: 'Conversation' });

  const figures = log.getByRole('region', { name: 'October spend' });
  await expect(figures.getByRole('definition').first()).toHaveText(
    '£2,140-8% vs Sep',
  );
  // The chart is the existing chart card, drawn from the inline data.
  await expect(log.locator('.rich-chart .rich-block-title')).toHaveText(
    'Spend by month',
  );
  const table = log.getByRole('region', { name: 'Biggest costs' });
  await table.getByRole('button', { name: 'Cost' }).click();
  expect(
    await table.locator('tbody tr td:nth-child(2)').allTextContents(),
  ).toEqual(['£186', '£238', '£512', '£1,200']);
  await expect(
    log.getByRole('progressbar', { name: 'Emergency fund' }),
  ).toHaveAttribute('aria-valuenow', '100');
  const checklist = log.getByRole('region', { name: 'This week' });
  await expect(checklist.getByText('1 of 3 done')).toBeVisible();
  await checklist
    .getByRole('checkbox', { name: 'Check the energy bill' })
    .click();
  await expect(checklist.getByText('2 of 3 done')).toBeVisible();
  // Every picture is of the settled turn: no Stop, the chart drawn.
  await settled(page);
  await dismissNotices(page);
  await screens(page, info, 'components-figures-table', () => figures);
  await screens(page, info, 'components-chart', () =>
    log.locator('.rich-chart'),
  );
  await screens(page, info, 'components-progress-checklist', () => checklist);

  const choices = log.getByRole('group', {
    name: 'What should I look at next?',
  });
  await screens(page, info, 'choices-before', () => choices);
  const before = submitted.length;
  // Like the composer's Send, the buttons wait for the turn; once it has
  // settled one tap sends, with no refusal notice.
  await choices.getByRole('button', { name: 'Subscriptions' }).click();
  await expect(
    choices.getByRole('button', { name: /Subscriptions/ }),
  ).toContainText('Sent');
  await expect(page.getByText(/can.t take that reply/)).toHaveCount(0);
  // The words went as the person's own message, and Row-Bot answered them.
  await expect(
    log.getByText('You pay for three: music, cloud storage and a news site.'),
  ).toBeVisible();
  await expect(log.getByText('List my subscriptions')).toBeVisible();
  expect(submitted.slice(before)).toEqual([
    'conversation.submit:List my subscriptions [tap: Subscriptions]',
  ]);
  const picked = choices.getByRole('button', { name: /Subscriptions/ });
  await expect(picked).toHaveAttribute('aria-pressed', 'true');
  await expect(picked).toContainText('Sent');
  await expect(
    choices.getByRole('button', { name: 'Groceries' }),
  ).toBeDisabled();
  await settled(page);
  await dismissNotices(page);
  await screens(page, info, 'choices-after', () => choices);

  // The ticks and the sent choice are kept with the chat: a reload shows them.
  await reloadDocument(page);
  await expect(
    log
      .getByRole('region', { name: 'This week' })
      .getByRole('checkbox', { name: 'Check the energy bill' }),
  ).toBeChecked();
  await expect(log.getByText('2 of 3 done')).toBeVisible();
  await expect(
    log
      .getByRole('group', { name: 'What should I look at next?' })
      .getByRole('button', { name: /Subscriptions/ }),
  ).toContainText('Sent');
});

test('a visual draws in its sandbox with no network, expands, and opens as a page', async ({
  page,
}, info) => {
  test.skip(
    info.project.name !== 'chromium-desktop',
    'One focused Chromium pass',
  );
  // The browser says what it refused the visual's network probe (Chromium's words).
  const probe = 'https://example.com/f3-visual-probe';
  for (const signature of [
    `Connecting to '${probe}' violates the following Content Security Policy directive: "connect-src 'none'". The action has been blocked.`,
    `Fetch API cannot load ${probe}. Refused to connect because it violates the document's Content Security Policy.`,
  ])
    info.annotations.push({
      type: 'expected-console-error',
      description: JSON.stringify({
        owner: 'answer components',
        fixture: 'interactive visual network probe',
        signature,
        count: 1,
        upTo: true,
      }),
    });
  const outside: string[] = [];
  page.on('request', (request) => {
    if (!new URL(request.url()).hostname.match(/^(127\.0\.0\.1|localhost)$/))
      outside.push(request.url());
  });
  await newConversation(page);
  await ask(page, 'f3 visuals fixture');
  const log = page.getByRole('log', { name: 'Conversation' });
  const visuals = log.locator('.answer-visual');
  await expect(visuals).toHaveCount(2);
  const diagram = visuals.first();
  await expect(diagram.locator('.rich-block-title')).toHaveText(
    'The water cycle',
  );
  await expect(diagram.getByText('Made by Row-Bot')).toBeVisible();
  const frames = log.locator('iframe.answer-visual-frame');
  for (const index of [0, 1]) {
    await expect(frames.nth(index)).toHaveAttribute('sandbox', 'allow-scripts');
    // A document Row-Bot serves once, under its own no-network policy.
    await expect(frames.nth(index)).toHaveAttribute(
      'src',
      /^\/page-frames\/[0-9a-f]{32}$/,
    );
  }
  const svg = page.frameLocator('iframe[title="The water cycle"]');
  await expect(svg.getByText('Clouds')).toBeVisible();

  // The interactive one runs its own script; its network probe is refused.
  const slider = page.frameLocator('iframe[title="Compound interest"]');
  await expect(slider.locator('#probe')).toHaveAttribute(
    'data-network',
    'blocked',
  );
  await expect(slider.locator('#total')).toHaveText('£1,629');
  await slider.getByRole('slider', { name: 'Years' }).fill('20');
  await expect(slider.locator('#total')).toHaveText('£2,653');
  // The frame grew to its content, within bounds.
  const height = await frames
    .nth(1)
    .evaluate((element) => element.getBoundingClientRect().height);
  expect(height).toBeGreaterThanOrEqual(120);
  expect(height).toBeLessThanOrEqual(900);
  expect(outside).toEqual([]);

  await dismissNotices(page);
  await screens(page, info, 'visual-diagram', () => diagram);
  await screens(page, info, 'visual-slider', () => visuals.nth(1));

  await diagram.getByRole('button', { name: 'Expand' }).click();
  const dialog = page.getByRole('dialog', { name: 'The water cycle' });
  await expect(dialog.locator('iframe.answer-visual-frame')).toHaveAttribute(
    'sandbox',
    'allow-scripts',
  );
  await screens(page, info, 'visual-expanded', () => dialog);
  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);

  await diagram.getByRole('button', { name: 'Open as page' }).click();
  const panel = page.getByRole('region', { name: 'The water cycle page' });
  await expect(panel).toBeVisible();
  await expect(panel.locator('iframe')).toHaveAttribute(
    'sandbox',
    'allow-scripts',
  );
  await expect(
    page.frameLocator('iframe.page-frame').getByText('Clouds'),
  ).toBeVisible();
  await screenshot(page, info, 'visual-open-as-page');
  expect(outside).toEqual([]);
});

const TABLE = [
  'Here is the budget:',
  '',
  '| Item | Cost | Share | Due |',
  '| --- | ---: | ---: | --- |',
  '| **Rent** | £1,200 | 62% | 1 Dec 2026 |',
  '| Food | £320 | 17% | 12 Nov 2026 |',
  '| Phone | £45 | 2% | 9 Nov 2026 |',
  '| Travel | £380 | 19% | 20 Nov 2026 |',
].join('\n');

test('a Markdown table sorts by pointer and keyboard, and a task list ticks', async ({
  page,
}, info) => {
  test.skip(
    info.project.name !== 'chromium-desktop',
    'One focused Chromium pass',
  );
  await openFixture(page);
  await showAnswer(page, [
    { id: 'table', type: 'markdown', text: TABLE },
    {
      id: 'tasks',
      type: 'markdown',
      text: 'Before you go:\n\n- [x] Book the train\n- [ ] Pack the charger\n- [ ] Tell **Sam**',
    },
  ]);
  const table = page.getByRole('table');
  const costs = () =>
    table.locator('tbody tr td:nth-child(2)').allTextContents();
  await expect(table).toBeVisible();
  expect(await costs()).toEqual(['£1,200', '£320', '£45', '£380']);

  const cost = table.getByRole('button', { name: 'Cost' });
  await cost.click();
  await expect(cost.locator('xpath=..')).toHaveAttribute(
    'aria-sort',
    'ascending',
  );
  expect(await costs()).toEqual(['£45', '£320', '£380', '£1,200']);
  await cost.click();
  await expect(cost.locator('xpath=..')).toHaveAttribute(
    'aria-sort',
    'descending',
  );
  expect(await costs()).toEqual(['£1,200', '£380', '£320', '£45']);
  await screenshot(page, info, 'table-sorted-pointer');

  const due = table.getByRole('button', { name: 'Due' });
  await due.focus();
  await page.keyboard.press('Enter');
  await expect(due.locator('xpath=..')).toHaveAttribute(
    'aria-sort',
    'ascending',
  );
  await expect(cost.locator('xpath=..')).toHaveAttribute('aria-sort', 'none');
  expect(
    await table.locator('tbody tr td:nth-child(4)').allTextContents(),
  ).toEqual(['9 Nov 2026', '12 Nov 2026', '20 Nov 2026', '1 Dec 2026']);
  const align = await table
    .locator('tbody tr td:nth-child(2)')
    .first()
    .evaluate((cell) => getComputedStyle(cell).textAlign);
  expect(align).toBe('end');
  await screens(page, info, 'table-sorted', () => table);

  const pack = page.getByRole('checkbox', { name: 'Pack the charger' });
  await expect(pack).not.toBeChecked();
  await expect(
    page.getByRole('checkbox', { name: 'Book the train' }),
  ).toBeChecked();
  await pack.click();
  await expect(pack).toBeChecked();
  await page.getByRole('checkbox', { name: 'Tell Sam' }).press('Space');
  await expect(page.getByRole('checkbox', { name: 'Tell Sam' })).toBeChecked();
  await screens(page, info, 'task-list', () => page.locator('.task-list'));
});
