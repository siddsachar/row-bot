import type { Page } from '@playwright/test';
import { expect, screenshot, test } from './evidence';
import {
  composer,
  conversationState,
  dismissContext,
  fixtureState,
  newConversation,
  revealContext,
} from './unified-helpers';

/*
 * Phase 11 · do it by conversation: setup cards, Connect cards, paste and
 * drop, slash commands with arguments, goals that run, and Stop keeping the
 * reply so far. Stop and Message for delegated agents are covered by
 * DelegatedActivity.test.tsx and test_delegated_activity.py. The fixture
 * scripts the model; every server owner (goals, approvals, runs, uploads) is
 * real.
 */

async function send(page: Page, text: string) {
  await composer(page).fill(text);
  await page.getByRole('button', { name: 'Send', exact: true }).click();
}

test('a tool the work needs asks to be turned on, in the chat', async ({
  page,
}, testInfo) => {
  await newConversation(page);
  await send(page, 'Search the web for tide tables setup fixture');
  const card = page.getByRole('complementary', {
    name: 'Turn on Web Search',
    exact: true,
  });
  await expect(card).toContainText('Turn on Web Search?');
  await expect(card).toContainText('Row-Bot needs Web Search for this.');
  await expect(
    card.getByRole('button', { name: 'Always allow in this chat' }),
  ).toHaveCount(0);
  await screenshot(page, testInfo, 'setup-card');
  await card.getByRole('button', { name: 'Turn on', exact: true }).click();
  await expect(
    page.getByText('Synthetic approval resumed.', { exact: true }),
  ).toBeVisible();
});

test('a folder the work needs is chosen on a card in the chat (B277)', async ({
  page,
}, testInfo) => {
  await newConversation(page);
  await send(page, 'Work on my tide app folder fixture');
  const card = page.getByRole('complementary', {
    name: 'Use an existing folder',
    exact: true,
  });
  // A browser can't pick a folder on this computer: the card says where to.
  await expect(card).toContainText(
    'Choose the folder in the Row-Bot desktop app, or add it with Add resource, then Continue.',
  );
  await expect(
    card.getByRole('button', { name: 'Choose folder', exact: true }),
  ).toHaveCount(0);
  await screenshot(page, testInfo, 'folder-card');
  await card.getByRole('button', { name: 'Continue', exact: true }).click();
  await expect(
    page.getByText('Synthetic approval resumed.', { exact: true }),
  ).toBeVisible();
});

test('an account the work needs shows a Connect card that opens its connect sheet', async ({
  page,
}, testInfo) => {
  await newConversation(page);
  await send(page, 'Read my calendar connect fixture');
  const card = page.getByRole('group', { name: 'Connect Google', exact: true });
  await expect(card).toContainText('Row-Bot needs Google for this.');
  await screenshot(page, testInfo, 'connect-card');
  await card
    .getByRole('button', { name: 'Connect Google', exact: true })
    .click();
  // The card opens Google's own connect sheet (Phase 15).
  await expect(page).toHaveURL(/\/settings\/accounts#google$/);
  await expect(
    page.getByRole('region', { name: 'Connect Google', exact: true }),
  ).toBeVisible();
});

test('a pasted screenshot and two dropped files attach to the message', async ({
  page,
  browserName,
}, testInfo) => {
  // Firefox drops files from a ClipboardEvent made by a script (a real paste
  // carries them), so this synthetic paste can only run in Chromium and WebKit.
  test.skip(
    browserName === 'firefox',
    'Firefox ignores files in a synthetic paste',
  );
  const conversation = await newConversation(page);
  const field = composer(page);
  // The pasted picture's tile shows the server's small thumbnail (B232).
  const thumbnail = page.waitForResponse((response) =>
    /^\/api\/v1\/attachments\/[^/]+\/thumbnail$/.test(
      new URL(response.url()).pathname,
    ),
  );
  await field.focus();
  await field.evaluate((element) => {
    const pixel = Uint8Array.from(
      atob(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGD4DwABBAEAwS2OUAAAAABJRU5ErkJggg==',
      ),
      (character) => character.charCodeAt(0),
    );
    const data = new DataTransfer();
    data.items.add(new File([pixel], 'image.png', { type: 'image/png' }));
    element.dispatchEvent(
      new ClipboardEvent('paste', {
        clipboardData: data,
        bubbles: true,
        cancelable: true,
      }),
    );
  });
  await expect(
    page.getByRole('button', { name: /^Remove Pasted image .+\.png$/ }),
  ).toBeVisible();
  await page.locator('.composer-field').evaluate((element) => {
    const data = new DataTransfer();
    data.items.add(new File(['first'], 'notes-a.txt', { type: 'text/plain' }));
    data.items.add(new File(['second'], 'notes-b.txt', { type: 'text/plain' }));
    for (const type of ['dragenter', 'dragover', 'drop'])
      element.dispatchEvent(
        new DragEvent(type, {
          dataTransfer: data,
          bubbles: true,
          cancelable: true,
        }),
      );
  });
  for (const name of ['notes-a.txt', 'notes-b.txt'])
    await expect(
      page.getByRole('button', { name: `Remove ${name}`, exact: true }),
    ).toBeVisible();
  await expect
    .poll(
      async () =>
        (await conversationState(page, conversation)).draft.attachments.length,
    )
    .toBe(3);
  const shown = await thumbnail;
  expect(shown.status()).toBe(200);
  expect(shown.headers()['content-type']).toBe('image/png');
  const tiles = page.getByRole('list', { name: 'Attachments', exact: true });
  await expect(
    tiles
      .getByRole('button', { name: /^Preview Pasted image .+\.png$/ })
      .locator('img'),
  ).toHaveAttribute('src', /^blob:/);
  await expect(
    tiles.getByRole('button', { name: 'Preview notes-a.txt', exact: true }),
  ).toContainText('notes-a.txt');
  await screenshot(page, testInfo, 'paste-and-drop');
});

test('slash commands run with their argument instead of reaching the model', async ({
  page,
}, testInfo) => {
  const conversation = await newConversation(page);
  const before = (await fixtureState(page)).calls.length;
  await send(page, '/profile default');
  await expect(
    page.getByText('Agent profile: Default', { exact: true }),
  ).toBeVisible();
  await send(page, '/reasoning default');
  await expect(
    page.getByText('Thinking: Provider default', { exact: true }),
  ).toBeVisible();
  await expect(page.getByRole('article', { name: 'You message' })).toHaveCount(
    0,
  );
  expect((await fixtureState(page)).calls).toHaveLength(before);
  // A goal starts at once and continues until the verifier calls it done.
  await send(page, '/goal Two quick fixture steps');
  const goal = (await revealContext(page)).locator('.context-goal');
  await expect(goal.getByText('Done', { exact: true })).toBeVisible();
  // No turn limit by default (B243).
  await expect(goal.getByText('Turn 2', { exact: true })).toBeVisible();
  await screenshot(page, testInfo, 'goal-done');
  await dismissContext(page);
  await expect(
    page.getByRole('note').filter({ hasText: /^Goal · turn \d$/ }),
  ).toHaveCount(2);
  await expect(composer(page)).toHaveValue('');
  expect(
    (await conversationState(page, conversation)).conversation
      .resource_bindings,
  ).toEqual([]);
});

test('Stop keeps the reply streamed so far', async ({ page }, testInfo) => {
  await newConversation(page);
  await send(page, 'stop fixture');
  await expect(
    page.getByText('Synthetic stream is active.', { exact: true }),
  ).toBeVisible();
  await page.getByRole('button', { name: 'Stop', exact: true }).click();
  const reply = page
    .getByRole('article', { name: 'Row-Bot message' })
    .filter({ hasText: 'Synthetic stream is active.' });
  await expect(reply).toHaveCount(1);
  await expect(reply.locator('.turn-chip')).toHaveText('Stopped');
  await expect(page.getByText('Stopped before a reply')).toHaveCount(0);
  await screenshot(page, testInfo, 'stop-keeps-partial');
});

test('a welcome prompt fills the composer to edit first', async ({ page }) => {
  await newConversation(page);
  await page
    .getByRole('button', { name: 'Plan a weekly brief', exact: true })
    .click();
  await expect(composer(page)).toHaveValue(
    'Create a disabled workflow for a weekly research briefing',
  );
  await expect(page.getByRole('article', { name: 'You message' })).toHaveCount(
    0,
  );
});
