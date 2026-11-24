import type { Locator, Page, TestInfo } from '@playwright/test';
import { assertNoOverflow, expect, screenshot, test } from './evidence';
import { fixtureHeaders } from './surface-helpers';
import { composer, dismissNotices, headerAction } from './unified-helpers';

/*
 * The outputs list (W3): one list behind Library › Media, the phone's Files
 * and a chat's Outputs pane. A fixture chat made a sign-up film in three
 * versions (the second presented), two pictures, and on the way a recording,
 * a voice line and a review sheet; the person added a brief. Finished items
 * show by default; Uploads and Working files on their chips or toggle.
 * Nothing leaves the machine.
 */

const PHONE = { width: 390, height: 844 };
const DESKTOP = { width: 1280, height: 900 };
const CHAT = 'media-fixture-launch';

async function views(page: Page, info: TestInfo, name: string, focus: Locator) {
  await dismissNotices(page);
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    await expect(page.locator('html')).toHaveAttribute(
      'data-theme',
      appearance,
    );
    await focus.scrollIntoViewIfNeeded();
    await assertNoOverflow(page);
    await screenshot(page, info, `${name}-${appearance}`);
  }
  await page.emulateMedia({ colorScheme: 'light' });
}

async function seed(page: Page) {
  const seeded = await page.request.post('/__media_fixture/seed', {
    headers: fixtureHeaders(),
  });
  expect(seeded.ok(), await seeded.text()).toBe(true);
}

test('Library › Media shows what was made once each, uploads and working files on their chips, and tags one', async ({
  page,
}, info) => {
  await page.setViewportSize(DESKTOP);
  await seed(page);
  await page.goto('/app-v2/library?show=media');
  const list = page.getByRole('list', { name: 'Media items' });
  // Other journeys leave their own outputs here: this one looks at its chat's.
  const search = page.getByRole('searchbox', { name: 'Search media' });
  await search.fill('Launch assets');
  const chips = page.getByRole('radiogroup', { name: 'Show' });
  await expect(chips.getByRole('radio', { name: 'Made' })).toBeChecked();
  // The film once, at its presented version, with its versions counted.
  await expect(list.getByRole('listitem')).toHaveCount(3);
  for (const name of ['Launch card', 'Pricing step', 'Sign-up walkthrough'])
    await expect(
      list.getByRole('link', { name: `${name}, in Launch assets` }),
    ).toBeVisible();
  await expect(
    list.getByRole('link', { name: 'Sign-up walkthrough, in Launch assets' }),
  ).toContainText('Video · v2 of 3');
  await expect(list.locator('img.media-library-thumb').first()).toBeVisible();
  await views(page, info, 'library-media-made', list);

  await chips.getByRole('radio', { name: 'Uploads' }).click();
  await expect(
    list.getByRole('link', { name: 'launch-brief.pdf, in Launch assets' }),
  ).toBeVisible();
  await expect(list.getByRole('listitem')).toHaveCount(1);
  await views(page, info, 'library-media-uploads', list);

  await chips.getByRole('radio', { name: 'Working files' }).click();
  await expect(list.getByRole('listitem')).toHaveCount(3);
  for (const name of [
    'Recording',
    'Voice line 01',
    'Sign-up walkthrough review',
  ])
    await expect(
      list.getByRole('link', { name: `${name}, in Launch assets` }),
    ).toBeVisible();
  await views(page, info, 'library-media-working', list);

  await chips.getByRole('radio', { name: 'Made' }).click();
  const kinds = page.getByRole('radiogroup', { name: 'Kind of media' });
  await kinds.getByRole('radio', { name: 'Videos', exact: true }).click();
  await expect(list.getByRole('listitem')).toHaveCount(1);
  await expect(list.getByText('Sign-up walkthrough')).toBeVisible();

  const tag = list.getByRole('button', {
    name: /^(Add tags to|Edit tags for) Sign-up walkthrough$/,
  });
  await tag.click();
  const tags = list.getByRole('textbox', {
    name: 'Tags for Sign-up walkthrough',
  });
  await tags.fill('Demo, x post');
  await tags.press('Enter');
  await expect(list.getByText('x post', { exact: true })).toBeVisible();

  await kinds.getByRole('radio', { name: 'Every kind', exact: true }).click();
  await search.fill('x post');
  await expect(list.getByRole('listitem')).toHaveCount(1);
  await expect(list.getByText('Sign-up walkthrough')).toBeVisible();
  await search.fill('Launch assets');
  await expect(list.getByRole('listitem')).toHaveCount(3);

  // A phone shows Library › Media as Chats › Files with its Media chip on (D10),
  // from the same list: the film once, never its drafts or working files.
  await page.setViewportSize(PHONE);
  const files = page.getByRole('list', { name: 'Files' });
  await expect(page.getByRole('radio', { name: 'Media' })).toHaveAttribute(
    'aria-checked',
    'true',
  );
  await expect(
    files.getByRole('link', { name: /^Sign-up walkthrough.*v2 of 3/ }),
  ).toHaveCount(1);
  await expect(files.getByRole('link', { name: /^Recording/ })).toHaveCount(0);
  await views(page, info, 'phone-files-media', files);
  await page.getByRole('radio', { name: 'Working files' }).click();
  await expect(
    files.getByRole('link', { name: /^Voice line 01/ }).first(),
  ).toBeVisible();
  await views(page, info, 'phone-files-working', files);
  await page.setViewportSize(DESKTOP);
});

test('the Outputs pane shows a tile per output with its versions inside, and working files behind a toggle', async ({
  page,
}, info) => {
  await page.setViewportSize(DESKTOP);
  await seed(page);
  await page.goto(`/app-v2/conversations/${CHAT}`);
  const context = page.getByRole('complementary', {
    name: 'Conversation details',
  });
  // The details may open by themselves on a wide window: reveal them only when hidden.
  await expect(composer(page)).toBeVisible();
  await expect(
    page.getByText('Here are the launch card', { exact: false }),
  ).toBeVisible();
  if (!(await context.isVisible()))
    await headerAction(page, 'Conversation details');
  await expect(context).toBeVisible();
  const outputs = context.getByRole('region', { name: 'Outputs' });
  const made = outputs.getByRole('list', { name: 'Outputs' });
  await expect(made.getByRole('listitem')).toHaveCount(3);
  await expect(
    made.getByRole('button', { name: 'Open Sign-up walkthrough' }),
  ).toBeVisible();
  // Its versions are in its menu, newest first, the presented one marked.
  await made
    .getByRole('button', { name: 'Actions for Sign-up walkthrough' })
    .click();
  const menu = page.getByRole('menu');
  for (const version of ['Version 3', 'Version 2', 'Version 1'])
    await expect(
      menu.getByRole('menuitem', { name: version, exact: true }),
    ).toBeVisible();
  await expect(
    menu.getByRole('menuitem', { name: 'Version 2', exact: true }),
  ).toHaveAttribute('aria-current', 'true');
  await views(page, info, 'outputs-pane-versions', menu);
  await page.keyboard.press('Escape');

  const toggle = outputs.getByRole('switch', { name: 'Show working files' });
  await expect(toggle).not.toBeChecked();
  await expect(
    outputs.getByRole('list', { name: 'Working files' }),
  ).toHaveCount(0);
  await toggle.click();
  const working = outputs.getByRole('list', { name: 'Working files' });
  await expect(working.getByRole('listitem')).toHaveCount(3);
  await views(page, info, 'outputs-pane-working', outputs);

  // Save to workspace keeps the file's own name, in the folder it names.
  await made
    .getByRole('button', { name: 'Actions for Sign-up walkthrough' })
    .click();
  await page.getByRole('menuitem', { name: 'Save to workspace' }).click();
  await expect(outputs.getByRole('status')).toHaveText(
    'Saved to your workspace as Saved outputs/Sign-up walkthrough.mp4',
  );

  await page.setViewportSize(PHONE);
  await page.goto(`/app-v2/conversations/${CHAT}`);
  await expect(composer(page)).toBeVisible();
  await headerAction(page, 'Conversation details');
  const sheet = page
    .getByRole('dialog', { name: 'Conversation details' })
    .or(context)
    .first();
  const phoneOutputs = sheet.getByRole('region', { name: 'Outputs' });
  await expect(
    phoneOutputs.getByRole('button', { name: 'Open Sign-up walkthrough' }),
  ).toBeVisible();
  await phoneOutputs
    .getByRole('switch', { name: 'Show working files' })
    .click();
  await expect(
    phoneOutputs
      .getByRole('list', { name: 'Working files' })
      .getByRole('listitem'),
  ).toHaveCount(3);
  await views(page, info, 'outputs-pane-phone', phoneOutputs);
  await page.setViewportSize(DESKTOP);
});
