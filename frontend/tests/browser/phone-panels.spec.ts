import type { Locator, Page } from '@playwright/test';
import { assertNoOverflow, expect, screenshot, test } from './evidence';
import { asPairedPhone, onPhone, PHONES, tapPath } from './phone-helpers';
import { fixtureHeaders } from './surface-helpers';
import {
  conversationState,
  newConversation,
  openConversation,
  revealContextControl,
} from './unified-helpers';

// Working on, on a phone (B3, D5 A): a design is previewed and paged through,
// a code folder's Changes and Files are read. Edit, zoom, size, Run and Git
// stay on the computer, and a link that leads to one says so.

/** Once per backend: A2's chats bound to the fixture's deck and code folder. */
async function seed(page: Page): Promise<void> {
  const response = await page.request.post('/__a2_fixture/seed', {
    headers: fixtureHeaders(),
  });
  expect(response.ok(), await response.text()).toBe(true);
}

const preview = (page: Page) =>
  page.getByRole('region', { name: 'Design preview', exact: true });
const inspector = (page: Page) =>
  page.getByRole('region', { name: / inspector$/ });
const workingOn = (page: Page) =>
  page.getByRole('button', { name: /^Working on / });

/** A wider screen opens the chat's design from the header's Open panel menu. */
async function openFromHeader(
  page: Page,
  panel: Locator,
  name: RegExp,
): Promise<void> {
  const menu = page.getByRole('button', { name: 'Open panel', exact: true });
  await expect(panel.or(menu).first()).toBeVisible();
  if (await panel.isVisible()) return;
  await menu.click();
  await page.getByRole('menuitem', { name }).click();
}

async function height(target: Locator): Promise<number> {
  return (await target.boundingBox())!.height;
}

/** The design's desktop tools: none of them shows on a phone. */
async function expectNoEditing(panel: Locator): Promise<void> {
  await expect(
    panel.getByRole('radiogroup', { name: 'Design mode' }),
  ).toHaveCount(0);
  await expect(panel.getByRole('radio', { name: 'Edit' })).toHaveCount(0);
  await expect(
    panel.getByRole('combobox', { name: 'Preview zoom' }),
  ).toHaveCount(0);
  await expect(panel.getByRole('button', { name: /^Size: / })).toHaveCount(0);
  for (const name of ['Undo', 'Redo', 'Versions', 'Inspector'])
    await expect(panel.getByRole('button', { name, exact: true })).toHaveCount(
      0,
    );
}

for (const phone of PHONES)
  test.describe(phone.name, () => {
    onPhone(phone);
    const width = phone.use.viewport.width;

    test.beforeEach(async ({ page }) => {
      await seed(page);
      await asPairedPhone(page);
    });

    test('a design opens from its chat and reaches page 2 in two taps, with no Edit, zoom or size and a toolbar that fits', async ({
      page,
    }, info) => {
      await openConversation(page, 'a2-design-chat');
      const panel = preview(page);
      const next = panel.getByRole('button', { name: 'Next slide' });
      expect(await tapPath(page, [workingOn(page)])).toBe(1);
      await expect(panel.locator('iframe')).toBeVisible();
      await expectNoEditing(panel);

      // One row each: the name with Share and ⋯, and ‹ 1 / 2 › (G16).
      const dock = panel.getByRole('toolbar', {
        name: 'Design preview controls',
      });
      const pageMenu = dock.getByRole('button', { name: /^Slide 1 of 2/ });
      await expect(pageMenu).toHaveText(/^1 \/ 2/);
      for (const target of [
        panel.getByRole('button', { name: 'Previous slide' }),
        pageMenu,
        next,
      ])
        expect(await height(target)).toBeGreaterThanOrEqual(44);
      expect(await height(dock)).toBeLessThan(60);
      const name = panel.getByRole('textbox', { name: 'Design name' });
      await expect(name).not.toBeEditable();
      const share = panel.getByRole('button', { name: 'Share or export' });
      const more = panel.getByRole('button', { name: 'More design actions' });
      const row = (await name.boundingBox())!;
      for (const action of [share, more]) {
        const box = (await action.boundingBox())!;
        expect(
          Math.abs(box.y + box.height / 2 - (row.y + row.height / 2)),
        ).toBeLessThan(6);
      }
      await assertNoOverflow(page);
      await screenshot(page, info, `phone-panels-design-${width}`);

      expect(await tapPath(page, [next])).toBe(1);
      await expect(
        dock.getByRole('button', { name: /^Slide 2 of 2/ }),
      ).toHaveText(/^2 \/ 2/);
      await expect(next).toBeDisabled();

      // The page menu pages through; adding and deleting pages stays on the
      // computer, as do the inspector's tools in ⋯.
      await dock.getByRole('button', { name: /^Slide 2 of 2/ }).tap();
      const pages = page.getByRole('menu');
      await expect(pages.getByRole('menuitem')).toHaveCount(2);
      await expect(
        pages.getByRole('menuitem', { name: /^Add a / }),
      ).toHaveCount(0);
      await pages.getByRole('menuitem', { name: /^1\. / }).tap();
      await expect(
        dock.getByRole('button', { name: /^Slide 1 of 2/ }),
      ).toBeVisible();
      await more.tap();
      const menu = page.getByRole('menu');
      await expect(
        menu.getByRole('menuitem', { name: 'Delete design…' }),
      ).toBeVisible();
      for (const hidden of [
        'Versions',
        'Inspector',
        'Library',
        'Import document',
        'Search design tools, pages & assets',
      ])
        await expect(
          menu.getByRole('menuitem', { name: hidden, exact: true }),
        ).toHaveCount(0);
      await page.keyboard.press('Escape');

      // Share stays: it is a phone job.
      await share.tap();
      await expect(
        page.getByRole('menuitem', { name: 'Share…', exact: true }),
      ).toBeVisible();
      await page.keyboard.press('Escape');
    });

    test("a code folder's changes open in two taps; only Changes and Files show, read only", async ({
      page,
    }, info) => {
      await openConversation(page, 'a2-code-chat');
      const panel = inspector(page);
      const tabs = panel.getByRole('tablist', { name: 'Developer inspector' });
      expect(
        await tapPath(page, [
          workingOn(page),
          panel.getByRole('tab', { name: /^Changes/ }),
        ]),
      ).toBe(2);
      await expect(
        panel.getByRole('tab', { name: /^Changes/ }),
      ).toHaveAttribute('aria-selected', 'true');
      await expect(tabs.getByRole('tab')).toHaveText([/^Changes/, 'Files']);
      // It points at Files only: Run is not on the phone.
      await expect(panel).toContainText(
        'not a Git repository, so there is no diff to show. Its files are in Files.',
      );
      await screenshot(page, info, `phone-panels-changes-${width}`);

      await tabs.getByRole('tab', { name: 'Files' }).tap();
      await panel
        .getByRole('button', { name: /fixture\.txt/ })
        .first()
        .tap();
      await expect(
        panel
          .getByRole('heading', { name: /fixture\.txt/ })
          .or(panel.getByText('fixture.txt').nth(1)),
      ).toBeVisible();
      await expect(
        panel.getByRole('button', { name: 'Edit file' }),
      ).toHaveCount(0);
      await assertNoOverflow(page);
      await screenshot(page, info, `phone-panels-files-${width}`);
    });

    test('the branch chip, a link to Git, says to open it on the computer', async ({
      page,
    }, info) => {
      test.setTimeout(120_000);
      await newConversation(page);
      await revealContextControl(page, 'Add resource');
      await page
        .getByRole('button', { name: 'Add resource', exact: true })
        .tap();
      const dialog = page.getByRole('dialog', { name: 'Add resource' });
      await dialog.getByRole('radio', { name: 'Code folder' }).tap();
      await dialog
        .getByRole('combobox', { name: 'Folder setup' })
        .selectOption('draft_folder');
      await dialog
        .getByRole('button', { name: 'Create draft code folder' })
        .tap();
      await expect(dialog.getByText('Code folder ready')).toBeVisible();
      await page.keyboard.press('Escape');
      const conversation = new URL(page.url()).pathname.split('/').at(-1)!;
      const binding = (await conversationState(page, conversation)).conversation
        .resource_bindings[0];
      const repository = await page.request.post(
        `/__p4_fixture/resources/${binding.resource_id}/repository`,
        { headers: fixtureHeaders() },
      );
      expect(repository.ok()).toBe(true);
      const panel = inspector(page);
      await expect(panel).toBeVisible();
      await panel.getByRole('button', { name: 'Refresh inspector' }).tap();
      const branch = panel.getByRole('button', {
        name: 'Branch main. Open Git',
      });
      await expect(
        panel
          .getByRole('tablist', { name: 'Developer inspector' })
          .getByRole('tab'),
      ).toHaveText([/^Changes/, 'Files']);

      expect(await tapPath(page, [branch])).toBe(1);
      const notice = panel.getByRole('region', { name: 'Git', exact: true });
      await expect(
        notice.getByRole('heading', { name: 'Open this on your computer' }),
      ).toBeVisible();
      await expect(notice).toContainText(
        'Branches, commits and pushes need a bigger screen.',
      );
      await expect(
        notice.getByRole('link', { name: 'Go to Home' }),
      ).toBeVisible();
      await expect(
        page.getByRole('region', { name: 'Developer repository controls' }),
      ).toHaveCount(0);
      await assertNoOverflow(page);
      await screenshot(page, info, `phone-panels-git-notice-${width}`);

      // Changes is one tap back.
      await panel.getByRole('tab', { name: /^Changes/ }).tap();
      await expect(notice).toBeHidden();
    });

    test('Edit chosen on a tablet ends at phone width, and its sheet says where editing lives', async ({
      page,
    }, info) => {
      await page.setViewportSize({ width: 800, height: 1000 });
      await openConversation(page, 'a2-design-chat');
      const panel = preview(page);
      await openFromHeader(page, panel, /Community Workshop Deck/i);
      await expect(panel.locator('iframe')).toBeVisible();
      await panel.getByRole('radio', { name: 'Edit' }).tap();
      const sheet = panel.getByRole('complementary', {
        name: 'Design inspector',
      });
      await expect(sheet.getByRole('tab', { name: 'Selection' })).toBeVisible();

      await page.setViewportSize(phone.use.viewport);
      await expectNoEditing(panel);
      await expect(panel).toHaveAttribute('data-mode', 'preview');
      const notice = sheet.getByRole('region', { name: 'Editing a design' });
      await expect(
        notice.getByRole('heading', { name: 'Open this on your computer' }),
      ).toBeVisible();
      await expect(sheet.getByRole('tab', { name: 'Selection' })).toHaveCount(
        0,
      );
      await screenshot(page, info, `phone-panels-edit-notice-${width}`);
      await sheet.getByRole('button', { name: 'Close', exact: true }).tap();
      await expect(sheet).toHaveCount(0);
    });
  });

test.describe('desktop keeps every tool', () => {
  test.use({
    viewport: { width: 1440, height: 900 },
    isMobile: false,
    hasTouch: false,
  });

  test('the design keeps Edit, zoom, size and undo; the code folder keeps Run and Git', async ({
    page,
  }, info) => {
    await seed(page);
    await openConversation(page, 'a2-design-chat');
    const panel = preview(page);
    await openFromHeader(page, panel, /Community Workshop Deck/i);
    await expect(panel.locator('iframe')).toBeVisible();
    await expect(panel.getByRole('radio', { name: 'Edit' })).toBeVisible();
    await expect(
      panel.getByRole('combobox', { name: 'Preview zoom' }),
    ).toBeVisible();
    await expect(panel.getByRole('button', { name: /^Size: / })).toBeVisible();
    for (const name of ['Undo', 'Redo'])
      await expect(
        panel.getByRole('button', { name, exact: true }),
      ).toBeVisible();
    // The panel beside the chat is narrow: Versions and the inspector wait in ⋯.
    await panel
      .getByRole('button', { name: 'More design actions', exact: true })
      .click();
    for (const name of ['Versions', 'Inspector', 'Library'])
      await expect(
        page.getByRole('menuitem', { name, exact: true }),
      ).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(
      panel.getByRole('textbox', { name: 'Design name' }),
    ).toBeEditable();
    await panel.getByRole('button', { name: /^Slide 1 of 2/ }).click();
    await expect(
      page.getByRole('menuitem', { name: 'Add a slide after this one' }),
    ).toBeVisible();
    await page.keyboard.press('Escape');
    await screenshot(page, info, 'phone-panels-desktop-design');

    await openConversation(page, 'a2-code-chat');
    const code = inspector(page);
    await openFromHeader(page, code, /Phase 1 workspace/i);
    await expect(
      code
        .getByRole('tablist', { name: 'Developer inspector' })
        .getByRole('tab'),
    ).toHaveText([/^Changes/, 'Files', 'Run', 'Git']);
    await code.getByRole('tab', { name: 'Run' }).click();
    await expect(code.getByText('Open this on your computer')).toHaveCount(0);
    await screenshot(page, info, 'phone-panels-desktop-code');
  });
});
