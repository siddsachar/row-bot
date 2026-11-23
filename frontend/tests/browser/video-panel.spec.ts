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
  const scenes = panel.getByRole('list', { name: 'Storyboard' });
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

  // Its sound (C6): a line given to a character, who joins the voices, and an effect added to the scene.
  await panel.getByLabel('Scene 2: who says its line').fill('Maya');
  const cast = panel.getByRole('group', { name: 'Voices' });
  await expect(cast.getByLabel("Maya's voice")).toHaveValue('');
  await panel.getByRole('button', { name: 'Add a sound to scene 2' }).click();
  await page.getByRole('menuitem', { name: /^Rain/ }).click();
  await panel.getByRole('button', { name: 'Add a sound to scene 2' }).click();
  await page.getByRole('menuitem', { name: /^Whoosh/ }).click();
  const effects = panel.getByRole('list', { name: 'Scene 2 sound effects' });
  await expect(effects.getByRole('listitem')).toHaveCount(2);
  await page.mouse.move(0, 0); // The add button's tooltip closes.
  await cast.scrollIntoViewIfNeeded();
  await views(page, info, 'video-panel-sound', cast);
  await page.setViewportSize(PHONE);
  await views(page, info, 'video-panel-sound-phone', effects);
  await page.setViewportSize(DESKTOP);
  await panel.getByRole('radio', { name: 'Timeline' }).click();
  {
    const timeline = panel.getByRole('application', { name: /^Timeline/ });
    await expect(timeline.getByLabel('Voice')).toContainText('Maya:');
    await expect(timeline.getByLabel('Effects')).toContainText('Rain');
    await views(page, info, 'video-panel-sound-timeline', timeline);
  }
  await panel.getByRole('radio', { name: 'Storyboard' }).click();
  await panel
    .getByRole('group', { name: 'Unsaved changes' })
    .getByRole('button', { name: 'Discard' })
    .click();
  await expect(cast).toHaveCount(0);

  // Edit by text: the line changes, a scene moves; nothing is saved until Save.
  const line = panel.getByLabel('Scene 2 line');
  await line.fill('Then pick the plan that fits your team.');
  {
    const preview = panel.getByLabel('Preview of your edits');
    // A card over a picture sits in the lower third on the left, as the made video has it, not over the action.
    const overPicture = preview.locator('.video-preview-card');
    await expect(overPicture).toHaveText('Sign up');
    const stage = await preview.locator('.video-preview-stage').boundingBox();
    const placed = await overPicture.boundingBox();
    expect(stage && placed).toBeTruthy();
    expect(placed!.x - stage!.x).toBeLessThan(stage!.width * 0.1);
    expect(placed!.y + placed!.height / 2 - stage!.y).toBeGreaterThan(
      stage!.height * 0.6,
    );
    await views(page, info, 'video-panel-preview-card', preview);
  }
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
  await views(page, info, 'video-panel-preview-phone', preview);
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
  const scenes = panel.getByRole('list', { name: 'Storyboard' });
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

test('a new video is a storyboard first: cards to approve and comment on, made on approval, then made again only where it changed', async ({
  page,
}, info) => {
  // Light and dark at desktop and phone width for each step: longer than the default minute.
  test.setTimeout(180_000);
  await page.setViewportSize(DESKTOP);
  await newConversation(page);
  await composer(page).fill('c1 storyboard fixture');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  const log = page.getByRole('log', { name: 'Conversation' });
  const card = log.getByRole('group', { name: 'First snow · video' }).last();
  await expect(card).toContainText('Storyboard');
  await card.getByRole('button', { name: 'Open' }).click();

  const panel = page.getByRole('region', { name: 'First snow video' });
  const bar = panel.getByRole('region', { name: 'Storyboard' });
  await expect(bar).toContainText('4 scenes · about 0:13 · 0 of 4 approved');
  await expect(bar).toContainText('1 scene needs its footage');
  // C6: music made to fit the cut, changing where the cue does.
  await expect(bar).toContainText(
    'Made to fit · calm · changes at scenes 2, 4',
  );
  await expect(panel).toContainText('A fox, a harbour and a small town wake');
  const cards = panel
    .getByRole('list', { name: 'Storyboard' })
    .getByRole('listitem');
  await expect(cards).toHaveCount(4);
  // Mixed footage, each card with its type; the frames are read from the footage as the cards show.
  for (const [index, kind] of [
    [0, 'Title card'],
    [1, 'AI shot'],
    [2, 'Picture'],
    [3, 'Clip'],
  ] as const)
    await expect(
      cards
        .nth(index)
        .getByRole('img', { name: `Scene ${index + 1}: ${kind}` }),
    ).toBeVisible();
  // Each frame loads as its card comes near the screen.
  for (const index of [1, 2, 3]) {
    await cards.nth(index).scrollIntoViewIfNeeded();
    await expect(cards.nth(index).locator('img')).toHaveAttribute(
      'src',
      /^blob:/,
    );
  }
  await expect(cards.nth(1)).toContainText('AI shot to make');
  await expect(cards.nth(1)).toContainText('strings swell');
  // A picture is held for its seconds: no speed or trim.
  await expect(panel.getByLabel('Scene 3 lasts, in seconds')).toHaveValue(
    '3.5',
  );
  await expect(panel.getByLabel('Scene 3 speed')).toHaveCount(0);

  await cards.nth(0).getByRole('button', { name: 'Approve scene 1' }).click();
  await expect(cards.nth(0)).toHaveAttribute('data-approved', 'true');
  await expect(bar).toContainText('1 of 4 approved');
  await views(page, info, 'c1-storyboard', bar);
  await page.setViewportSize(PHONE);
  await expect(panel).toBeVisible();
  await views(page, info, 'c1-storyboard-phone', cards.nth(1));
  await page.setViewportSize(DESKTOP);

  // Make: the AI shot is Row-Bot's to make, so the panel asks in the chat, and follows the video it makes.
  await bar.getByRole('button', { name: 'Make video' }).click();
  await expect(log).toContainText('Make the video "First snow" (vid-');
  await expect(panel.getByLabel(/^First snow, version 2$/)).toHaveAttribute(
    'src',
    /^blob:/,
  );
  await expect(panel.getByRole('region', { name: 'Review' })).toContainText(
    'Ready to share',
  );

  // A comment on one card reaches Row-Bot as the person's words; only that scene is made again.
  await cards
    .nth(1)
    .getByRole('button', { name: 'Comment on scene 2' })
    .click();
  const comment = cards.nth(1).getByLabel('What to change in scene 2');
  await comment.fill('make this one moodier');
  await expect(
    cards.nth(1).getByRole('button', { name: 'Send to Row-Bot' }),
  ).toBeEnabled();
  await expect(
    log.getByRole('group', { name: 'First snow · video' }).last(),
  ).toContainText('Version 2 of 2');
  await views(page, info, 'c1-storyboard-comment', cards.nth(1));
  await page.setViewportSize(PHONE);
  await views(page, info, 'c1-storyboard-comment-phone', cards.nth(1));
  await page.setViewportSize(DESKTOP);
  await comment.press('Enter');
  await expect(log).toContainText('make this one moodier');
  await expect(panel).toContainText(
    'Made again: 1 changed scene drawn, 2 reused.',
  );
  await expect(cards.nth(1)).toContainText('Made again');
  await expect(cards.nth(2)).toContainText('Reused');
  await expect(cards.nth(1)).toContainText('cold blue dusk');
  await views(
    page,
    info,
    'c1-storyboard-partial-make',
    panel.getByText('Made again: 1 changed scene drawn, 2 reused.'),
  );
  await page.setViewportSize(PHONE);
  await views(page, info, 'c1-storyboard-partial-make-phone', cards.nth(1));
});
