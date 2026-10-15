import {
  accessibility,
  assertNoOverflow,
  expect,
  screenshot,
  test,
} from './evidence';
import { blockFixtureServiceWorkers } from './unified-helpers';
import type { Locator, Page, TestInfo } from '@playwright/test';

// F23: "Edit workflow steps" draws the steps as a flowchart. The fixture seeds
// switched-off workflows that branch, loop, break and leave a step unreached;
// nothing is scheduled or run.

async function seedGraphs(page: Page) {
  const token = process.env.ROW_BOT_BROWSER_CONTROL_TOKEN;
  const base = process.env.ROW_BOT_BROWSER_BASE_URL;
  if (!token || !base) throw new Error('Use the isolated Phase 4 runner');
  const response = await page.request.post('/__p4_fixture/workflow-graphs', {
    headers: { 'X-Fixture-Token': token, Origin: new URL(base).origin },
  });
  expect(response.ok(), await response.text()).toBe(true);
}

const phone = (info: TestInfo) =>
  (info.project.use.viewport?.width ?? 1440) < 700;

async function openSteps(page: Page, name: string) {
  await page.goto('/app-v2/?tab=workflows');
  await page.getByRole('searchbox', { name: 'Search workflows' }).fill(name);
  await page
    .getByRole('button', { name: 'Search workflows', exact: true })
    .click();
  const card = page.locator('.workflow-grid > li').filter({ hasText: name });
  await expect(card).toHaveCount(1);
  await card
    .getByRole('button', { name: `More actions for ${name}`, exact: true })
    .click();
  await page
    .getByRole('menuitem', { name: 'Edit workflow steps', exact: true })
    .click();
  await expect(page.getByRole('button', { name: 'Save graph' })).toBeVisible();
}

function viewChoice(page: Page, name: 'Flowchart' | 'List') {
  return page
    .getByRole('radiogroup', { name: 'Show steps as' })
    .getByRole('radio', { name, exact: true });
}

/** On a phone the list comes first; the flowchart is one tap away. */
async function flowchart(page: Page, info: TestInfo) {
  if (phone(info)) {
    await expect(viewChoice(page, 'List')).toBeChecked();
    await viewChoice(page, 'Flowchart').click();
  }
  await expect(viewChoice(page, 'Flowchart')).toBeChecked();
  const chart = page.getByRole('group', { name: 'Workflow flowchart' });
  await expect(chart).toBeVisible();
  return chart;
}

function cards(chart: Locator) {
  return chart.getByRole('button');
}

/** Cards never overlap and never leave the canvas. */
async function expectTidy(chart: Locator) {
  const canvas = (await chart.boundingBox())!;
  const boxes = await cards(chart).evaluateAll((elements) =>
    elements.map((element) => {
      const box = element.getBoundingClientRect();
      return { x: box.x, y: box.y, width: box.width, height: box.height };
    }),
  );
  for (const box of boxes) {
    expect(box.x).toBeGreaterThanOrEqual(canvas.x);
    expect(box.x + box.width).toBeLessThanOrEqual(canvas.x + canvas.width);
  }
  for (let a = 0; a < boxes.length; a += 1)
    for (let b = a + 1; b < boxes.length; b += 1) {
      const [one, two] = [boxes[a], boxes[b]];
      expect(
        one.x < two.x + two.width &&
          two.x < one.x + one.width &&
          one.y < two.y + two.height &&
          two.y < one.y + one.height,
      ).toBe(false);
    }
  return boxes;
}

async function shoot(page: Page, info: TestInfo, name: string) {
  const suffix = phone(info) ? '-phone' : '';
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    await assertNoOverflow(page);
    await screenshot(page, info, `${name}${suffix}-${appearance}`);
  }
  await accessibility(page, info, `${name}${suffix}`);
  await page.emulateMedia({ colorScheme: 'light' });
}

test.use({ serviceWorkers: 'allow', nativeNetwork: true });
test.beforeEach(async ({ context, page }) => {
  await blockFixtureServiceWorkers(context);
  await page.addInitScript(() => {
    if (window !== window.top) return;
    localStorage.setItem(
      'row-bot.appearance.v1',
      JSON.stringify({
        version: 1,
        appearance: 'system',
        accent: 'blue',
        density: 'compact',
        reduce_transparency: false,
      }),
    );
  });
});

test('the step editor draws branches as a flowchart and a card opens its step to edit', async ({
  page,
}, info) => {
  await seedGraphs(page);
  await openSteps(page, 'Flowchart: weekly report review');
  if (phone(info)) await shoot(page, info, 'flowchart-phone-fallback');
  const chart = await flowchart(page, info);
  await expect(cards(chart)).toHaveCount(5);
  expect(
    await cards(chart).evaluateAll((elements) =>
      elements.map((element) => element.getAttribute('aria-label')),
    ),
  ).toEqual([
    'Step 1 · Prompt',
    'Step 2 · Condition',
    'Step 3 · Prompt',
    'Step 4 · Approval',
    'Step 5 · Notification',
  ]);
  for (const label of ['yes', 'no', 'approved', 'denied'])
    await expect(chart.getByText(label, { exact: true })).toBeVisible();
  await expect(chart.getByText('Start', { exact: true })).toBeVisible();
  await expect(chart.getByText('End', { exact: true })).toHaveCount(2);
  // The main line runs straight down; the optional step sits to the left.
  const [draft, check, fix, review, publish] = await expectTidy(chart);
  const centre = (box: { x: number; width: number }) => box.x + box.width / 2;
  for (const box of [check, review, publish])
    expect(Math.abs(centre(box) - centre(draft))).toBeLessThan(1);
  expect(centre(fix)).toBeLessThan(draft.x);
  expect(review.y).toBeGreaterThan(fix.y + fix.height);
  await expect(
    page.getByText(/^(?:draft|check|fix|review|publish)$/),
  ).toHaveCount(0);
  await shoot(page, info, 'flowchart-review');
  if (!phone(info)) {
    // A 1440 × 900 window at 200% zoom: the form moves under the chart.
    await page.setViewportSize({ width: 720, height: 450 });
    await expect(chart).toBeVisible();
    await shoot(page, info, 'flowchart-zoom-200');
    await page.setViewportSize({ width: 1440, height: 900 });
  }

  // Clicking a card opens that step's form and highlights the card.
  const card = chart.getByRole('button', { name: 'Step 3 · Prompt' });
  await card.click();
  await expect(card).toHaveAttribute('aria-pressed', 'true');
  const form = page.getByRole('group', { name: 'Step 3 · Prompt' });
  await expect(form).toBeVisible();
  await expect(form.getByRole('textbox', { name: 'Prompt' })).toHaveValue(
    'Finish every TODO left in the report',
  );
  await form
    .getByRole('textbox', { name: 'Prompt' })
    .fill('Finish every TODO, then read the report once more');
  await expect(card).toContainText('Finish every TODO, then read the report');
  await shoot(page, info, 'flowchart-selected');

  await page.getByRole('button', { name: 'Save graph', exact: true }).click();
  await expect(
    page.getByRole('heading', { name: 'Workflows', exact: true }),
  ).toBeVisible();
  await openSteps(page, 'Flowchart: weekly report review');
  // The view choice is remembered: a phone now opens on the flowchart too.
  await expect(viewChoice(page, 'Flowchart')).toBeChecked();
  const saved = page
    .getByRole('group', { name: 'Workflow flowchart' })
    .getByRole('button', { name: 'Step 3 · Prompt' });
  await expect(saved).toContainText('Finish every TODO, then read the report');
  await saved.click();
  await expect(page.getByRole('textbox', { name: 'Prompt' })).toHaveValue(
    'Finish every TODO, then read the report once more',
  );
});

test('a simple chain, a loop and the list view read clearly', async ({
  page,
}, info) => {
  await seedGraphs(page);
  await openSteps(page, 'Flowchart: morning summary');
  let chart = await flowchart(page, info);
  const chain = await expectTidy(chart);
  expect(new Set(chain.map((box) => Math.round(box.x))).size).toBe(1);
  await expect(chart.getByText(/^(?:yes|no|approved|denied)$/)).toHaveCount(0);
  await shoot(page, info, 'flowchart-chain');

  await openSteps(page, 'Flowchart: draft until approved');
  chart = page.getByRole('group', { name: 'Workflow flowchart' });
  await expect(chart).toBeVisible();
  await expectTidy(chart);
  await expect(chart.getByText('denied', { exact: true })).toBeVisible();
  await expect(chart.getByText('approved', { exact: true })).toBeVisible();
  await expect(
    chart.getByRole('button', { name: 'Step 2 · Approval' }),
  ).toHaveAccessibleDescription(
    'Approve the announcement? When approved, goes to step 3 · Notification. When denied, goes to step 1 · Prompt.',
  );
  await shoot(page, info, 'flowchart-loop');

  // The list stays, one tap away, and is remembered.
  await viewChoice(page, 'List').click();
  const list = page.getByRole('list', { name: 'Workflow step order' });
  await expect(list.getByRole('listitem')).toHaveCount(3);
  await shoot(page, info, 'flowchart-toggle-list');
  await openSteps(page, 'Flowchart: draft until approved');
  await expect(viewChoice(page, 'List')).toBeChecked();
  await expect(list).toBeVisible();
  await viewChoice(page, 'Flowchart').click();
  await expect(chart).toBeVisible();
});

test('a broken branch shows on its card in plain words until it is fixed', async ({
  page,
}, info) => {
  await seedGraphs(page);
  await openSteps(page, 'Flowchart: release checklist');
  const chart = await flowchart(page, info);
  const problem = 'When false goes to a step that was removed. Choose another.';
  await expect(page.getByText('1 step needs a fix')).toBeVisible();
  const gate = chart.getByRole('button', { name: 'Step 2 · Condition' });
  await expect(gate).toContainText('Goes to a removed step');
  await expect(gate).toHaveAccessibleDescription(
    /Problem: When false goes to a step that was removed/,
  );
  await expect(chart.getByText('Not reached', { exact: true })).toBeVisible();
  await expect(
    chart.getByRole('button', { name: 'Step 4 · Prompt' }),
  ).toHaveAccessibleDescription(/Not reached: no step leads here/);
  await expect(page.getByText('notes_9')).toHaveCount(0);
  await expectTidy(chart);
  await gate.click();
  const form = page.getByRole('group', { name: 'Step 2 · Condition' });
  await expect(form.getByText(problem)).toBeVisible();
  await expect(form.getByRole('combobox', { name: 'When false' })).toHaveValue(
    'notes_9',
  );
  await shoot(page, info, 'flowchart-problem');
  await form
    .getByRole('combobox', { name: 'When false' })
    .selectOption({ label: 'End workflow' });
  await expect(page.getByText('1 step needs a fix')).toHaveCount(0);
  await expect(gate).not.toContainText('Goes to a removed step');
  await expect(chart.getByText('no', { exact: true })).toBeVisible();
});
