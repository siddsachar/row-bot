import type { Page, TestInfo } from '@playwright/test';
import {
  accessibility,
  assertNoOverflow,
  expect,
  screenshot,
  test,
} from './evidence';
import { fixtureHeaders } from './surface-helpers';
import { composer, newConversation } from './unified-helpers';

// D1 Always allow: a card offers a rule, its sheet shows what the rule would
// have covered in the last 30 days, Save makes it and approves; the next time
// the rule allows the command (through the real approval gate), and Settings ›
// System › Activity lists, switches and narrows it. The fixture scripts the
// model and seeds the month of activity (/__d1_fixture/rules/seed).

async function rules(page: Page, state: 'seed' | 'clear') {
  const response = await page.request.post(`/__d1_fixture/rules/${state}`, {
    headers: fixtureHeaders(),
  });
  expect(response.ok(), await response.text()).toBe(true);
}

test.afterEach(async ({ page }) => {
  await rules(page, 'clear');
});

const SIZES = {
  desktop: { width: 1280, height: 900 },
  phone: { width: 375, height: 812 },
} as const;

/** Light and dark pictures of what shows. */
async function pictures(page: Page, info: TestInfo, name: string) {
  const suffix = (page.viewportSize()?.width ?? 1280) < 700 ? '-phone' : '';
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    await expect(page.locator('html')).toHaveAttribute(
      'data-theme',
      appearance,
    );
    await assertNoOverflow(page);
    await screenshot(page, info, `${name}${suffix}-${appearance}`);
  }
  await page.emulateMedia({ colorScheme: 'light' });
}

/** Light and dark, then an accessibility scan of what shows. */
async function shoot(page: Page, info: TestInfo, name: string) {
  const suffix = (page.viewportSize()?.width ?? 1280) < 700 ? '-phone' : '';
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    await expect(page.locator('html')).toHaveAttribute(
      'data-theme',
      appearance,
    );
    await assertNoOverflow(page);
    await screenshot(page, info, `${name}${suffix}-${appearance}`);
  }
  await accessibility(page, info, `${name}${suffix}`);
  await page.emulateMedia({ colorScheme: 'light' });
}

async function send(page: Page, text: string) {
  await composer(page).fill(text);
  await page.getByRole('button', { name: 'Send', exact: true }).click();
}

for (const size of ['desktop', 'phone'] as const) {
  test.describe(size, () => {
    test.use({ viewport: SIZES[size] });

    test(`a card's Always allow shows its dry run, saves the rule and the rule allows it next time (${size})`, async ({
      page,
    }, info) => {
      await rules(page, 'seed');
      await newConversation(page);
      await send(page, 'Run the tests always allow fixture');
      const card = page.getByRole('complementary', {
        name: /^Approval required/,
      });
      await expect(card).toContainText('npm test', { timeout: 15_000 });

      if (size === 'desktop') {
        const always = card.getByRole('button', { name: 'Always allow…' });
        await always.hover();
        await expect(
          page.getByRole('tooltip').filter({ hasText: 'from now on' }),
        ).toBeVisible();
        await shoot(page, info, 'always-allow-card');
        await always.click();
      } else {
        await shoot(page, info, 'always-allow-card');
        await card
          .getByRole('button', { name: 'More choices', exact: true })
          .click();
        const item = page.getByRole('menuitem', { name: /Always allow…/ });
        await expect(item).toBeVisible();
        // An open menu hides the rest of the page from assistive technology: pictures only.
        await pictures(page, info, 'always-allow-card-menu');
        await item.click();
      }

      const sheet = page.getByRole('dialog', { name: 'Always allow this?' });
      await expect(sheet).toContainText('Always allow npm test in row-bot');
      await expect(sheet).toContainText(
        'In the last 30 days this rule would have allowed 5 actions without asking: you approved 3, the reviewer approved 1 and you said no to 1.',
      );
      await expect(sheet).toContainText(
        'You said no to one of these. With this rule, it would have run.',
      );
      await shoot(page, info, 'always-allow-sheet');
      await page
        .getByRole('button', { name: 'Save and approve', exact: true })
        .click();
      const log = page.getByRole('log', { name: 'Conversation' });
      await expect(
        log.getByText('The tests passed: 12 of 12.', { exact: true }),
      ).toBeVisible({ timeout: 15_000 });

      // The next time, the rule allows it: no card, and the step says whose rule.
      await send(page, 'Run the tests again always allow fixture');
      await expect(
        log.getByText('The tests passed: 12 of 12.', { exact: true }),
      ).toHaveCount(2, { timeout: 15_000 });
      await expect(card).toHaveCount(0);
      const steps = log.getByText('Used 1 tool', { exact: true }).last();
      const ruled = log.getByText('Your rule', { exact: true });
      // The live steps settle into the saved ones as the turn ends (collapsed again): open them.
      await expect(async () => {
        if (!(await ruled.isVisible())) await steps.click();
        await expect(ruled).toBeVisible({ timeout: 1_000 });
      }).toPass({ timeout: 15_000 });
      await ruled.click();
      await expect(
        log.getByText(
          'Allowed by your rule: Always allow `npm test` in row-bot',
        ),
      ).toBeVisible();
      await shoot(page, info, 'always-allow-step');

      // Settings lists it with the seeded ones: one switched off.
      await page.goto('/app-v2/settings/activity');
      const list = page.getByRole('region', {
        name: 'Always allow',
        exact: true,
      });
      const npm = list.getByRole('listitem').filter({ hasText: 'npm test' });
      await expect(npm).toContainText('Used once · last', { timeout: 15_000 });
      await expect(
        list.getByRole('listitem').filter({ hasText: 'moving and renaming' }),
      ).toContainText('Off ·');
      const notion = list
        .getByRole('listitem')
        .filter({ hasText: 'Notion › Create a page' });
      await expect(notion).toContainText('Used 4 times');
      await notion.getByRole('switch', { name: 'Rule on' }).click();
      await expect(notion).toContainText('Off ·');
      await notion.getByRole('switch', { name: 'Rule off' }).click();
      await expect(notion).not.toContainText('Off ·');
      await shoot(page, info, 'always-allow-rules');

      // Narrowing a command is checked like a new rule: the server refuses an install's dry run (422).
      // Its reason phrase depends on the server's Python ("Unprocessable Content" from 3.13).
      for (const phrase of ['Unprocessable Content', 'Unprocessable Entity'])
        test.info().annotations.push({
          type: 'expected-console-error',
          description: JSON.stringify({
            owner: 'always-allow',
            fixture: 'a command rule narrowed to an install',
            signature: `Failed to load resource: the server responded with a status of 422 (${phrase})`,
            count: 2,
            upTo: true,
          }),
        });
      await npm.getByRole('button', { name: 'Edit rule' }).click();
      const edit = page.getByRole('dialog', { name: 'Edit rule' });
      const field = edit.getByRole('textbox', { name: /Exact command/ });
      await field.fill('npm install');
      await expect(edit).toContainText(
        'A rule can only cover one plain command',
      );
      await expect(edit.getByRole('button', { name: 'Save' })).toBeDisabled();
      await field.fill('npm test -- --run');
      await expect(edit).toContainText('Would this have asked?');
      await expect(edit).toContainText('Nothing in the last 30 days');
      await shoot(page, info, 'always-allow-narrow');
      await edit.getByRole('button', { name: 'Save', exact: true }).click();
      await expect(npm).toContainText('npm test -- --run');

      // The action the rule allowed, in the log.
      const activity = page.getByRole('region', {
        name: 'Every action',
        exact: true,
      });
      const allowed = activity
        .getByRole('button')
        .filter({ hasText: 'Allowed by your rule' });
      await expect(allowed).toHaveCount(1);
      await allowed.click();
      await expect(
        activity.getByRole('button', { name: 'See the rule' }),
      ).toBeVisible();
      await allowed.scrollIntoViewIfNeeded();
      await shoot(page, info, 'always-allow-activity-row');
    });
  });
}
