import type { Locator, Page, TestInfo } from '@playwright/test';
import { assertNoOverflow, expect, screenshot, test } from './evidence';
import { composer, dismissNotices, newConversation } from './unified-helpers';

/*
 * 5.3.1 B3 · the Video panel. The model's video step is scripted; the project,
 * its versions, the card, the panel and its API run through the real server.
 * The made video is the fixture's synthetic clip; nothing is spoken or encoded.
 */

const PHONE = { width: 390, height: 844 };
const DESKTOP = { width: 1280, height: 900 };

async function views(page: Page, info: TestInfo, name: string, focus: Locator) {
  await expect(page.getByText('Buddy is loading…')).toHaveCount(0);
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

test('a video Row-Bot made opens beside the chat as scenes, and an edit there is a new version', async ({
  page,
}, info) => {
  await page.setViewportSize(DESKTOP);
  await newConversation(page);
  await composer(page).fill('b3 video fixture');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  const card = page
    .getByRole('log', { name: 'Conversation' })
    .getByRole('group', { name: 'Launch demo · video' });
  await expect(card).toContainText('Made');
  await expect(card).toContainText('Version 2 of 2');
  await card.getByRole('button', { name: 'Open' }).click();

  const panel = page.getByRole('region', { name: 'Launch demo video' });
  const player = panel.getByLabel('Launch demo, version 2');
  await expect(player).toHaveAttribute('src', /^blob:/);
  const scenes = panel.getByRole('list', { name: 'Scenes' });
  await expect(scenes.getByRole('listitem')).toHaveCount(2);
  // Row-Bot's last change cut the intro: the panel marks that scene.
  await expect(scenes.getByRole('listitem').first()).toHaveAttribute(
    'data-changed',
    'true',
  );
  await expect(scenes.getByText('1.6 s')).toBeVisible();
  await expect(panel.getByLabel('Scene 2 speed')).toHaveValue('2');
  await expect(panel.getByRole('region', { name: 'Review' })).toContainText(
    'Ready to share',
  );
  await views(page, info, 'video-panel', panel);
  // The same version in other shapes, each saying where it fits.
  const shapes = panel.getByRole('list', { name: 'Other shapes' });
  await expect(shapes.getByRole('listitem')).toHaveCount(4);
  await expect(shapes).toContainText('Tall 9:16');
  await expect(shapes).toContainText('Shorts, Reels');
  await views(page, info, 'video-panel-review', shapes);
  // The brand kit: one look for every video, edited here.
  await panel.getByText('Brand kit', { exact: true }).click();
  const kit = panel.getByRole('group', { name: 'Brand kit' });
  await kit.getByLabel('Outro').fill('row-bot.ai');
  await kit.getByLabel('Caption style').selectOption('bold');
  await views(page, info, 'video-panel-brand', kit);
  await kit.getByRole('button', { name: 'Save brand kit' }).click();
  await expect(panel).toContainText('Brand kit saved');

  // Edit by text: the line changes, a scene moves; nothing is saved until Save.
  const line = panel.getByLabel('Scene 2 line');
  await line.fill('Then pick the plan that fits your team.');
  await panel.getByRole('button', { name: 'Move scene 2 up' }).click();
  const save = panel.getByRole('group', { name: 'Unsaved changes' });
  await expect(save).toBeVisible();
  // The player previews the edits live, before anything is made.
  const preview = panel.getByLabel('Preview of your edits');
  await expect(preview).toContainText('Scene 1 of 2');
  await expect(preview.getByLabel('Scene 1')).toHaveAttribute('src', /^blob:/);
  await views(page, info, 'video-panel-preview', preview);
  await views(page, info, 'video-panel-edited', save);

  await page.setViewportSize(PHONE);
  await expect(panel).toBeVisible();
  await views(page, info, 'video-panel-phone', scenes);
  await save.getByRole('button', { name: 'Discard' }).click();
  await expect(save).toHaveCount(0);
  await expect(panel.getByLabel('Scene 1 line')).toHaveValue(
    'First, sign up in a few seconds.',
  );
  await page.setViewportSize(DESKTOP);

  // The timeline is one click away on a desktop: scenes as blocks on tracks.
  await panel.getByRole('radio', { name: 'Timeline' }).click();
  const timeline = panel.getByRole('application', { name: /^Timeline/ });
  await expect(timeline.getByRole('button')).toHaveCount(2);
  await expect(timeline.getByLabel('Voice')).toContainText('First, sign up');
  await expect(timeline.getByLabel('Cards')).toContainText('Sign up');
  await views(page, info, 'video-panel-timeline', timeline);
  // Delete removes the selected scene; Ctrl+Z brings it back.
  await timeline.getByRole('button', { name: /^Scene 2,/ }).click();
  await timeline.focus();
  await page.keyboard.press('Delete');
  await expect(timeline.getByRole('button')).toHaveCount(1);
  await page.keyboard.press('Control+z');
  await expect(timeline.getByRole('button')).toHaveCount(2);
});

test('a title card scene needs no video: it says how long it shows, with no speed or trim', async ({
  page,
}, info) => {
  await page.setViewportSize(DESKTOP);
  await newConversation(page);
  await composer(page).fill('b3 title card fixture');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  const card = page
    .getByRole('log', { name: 'Conversation' })
    .getByRole('group', { name: 'Teaser · video' });
  await card.getByRole('button', { name: 'Open' }).click();

  const panel = page.getByRole('region', { name: 'Teaser video' });
  const scenes = panel.getByRole('list', { name: 'Scenes' });
  await expect(scenes.getByRole('listitem')).toHaveCount(2);
  await expect(panel.getByLabel('Scene 1 lasts, in seconds')).toHaveValue('3');
  await expect(panel.getByLabel('Scene 1 speed')).toHaveCount(0);
  await expect(panel.getByLabel('Scene 1 card')).toHaveValue('Row-Bot 5.3');
  await expect(scenes).toContainText('A title card on your brand colour');
  await expect(panel.getByLabel('Scene 2 speed')).toHaveValue('1');
  await views(page, info, 'video-panel-title-card', scenes);
  await page.setViewportSize(PHONE);
  await views(page, info, 'video-panel-title-card-phone', scenes);
});
