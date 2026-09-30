import type { Page } from '@playwright/test';
import { expect, screenshot, test } from './evidence';
import {
  advanceOrchestration,
  composer,
  conversationState,
  dismissContext,
  fixtureState,
  newConversation,
  releaseProducer,
  reloadDocument,
  revealContext,
} from './unified-helpers';

/*
 * Phase 11 · do it by conversation: setup cards, Connect cards, paste and
 * drop, slash commands with arguments, goals that run, Stop and Message for
 * delegated agents, and Stop keeping the reply so far. The fixture scripts
 * the model; every server owner (goals, approvals, runs, uploads) is real.
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
}, testInfo) => {
  const conversation = await newConversation(page);
  const field = composer(page);
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

test('a delegated agent can be messaged and stopped from Agents', async ({
  page,
}, testInfo) => {
  const conversation = await newConversation(page);
  await send(page, 'steering fixture');
  await expect(
    page.getByText('Synthetic child is working.', { exact: true }),
  ).toBeVisible();
  const call = (await fixtureState(page)).calls.at(-1)!;
  try {
    // The fixture writes the child run without a run event; a fresh read
    // lists it, as the runner's own events do in the app.
    await reloadDocument(page);
    await expect(composer(page)).toBeVisible();
    // A live agent opens the Agents section by itself (B30).
    const child = (await revealContext(page)).getByRole('button', {
      name: 'Synthetic child',
      exact: true,
    });
    await expect(child).toBeVisible();
    await child.click();
    const detail = page.getByRole('dialog', { name: 'Synthetic child' });
    await expect(detail.getByRole('status').first()).toHaveText(
      /Status\s+Working/,
    );
    await detail.getByRole('button', { name: 'Message', exact: true }).click();
    await detail
      .getByRole('textbox', { name: 'Message to Synthetic child' })
      .fill('Also cover the evening tides.');
    await detail
      .getByRole('button', { name: 'Send to agent', exact: true })
      .click();
    await expect(
      detail.getByText('Message sent. The agent reads it at its next step.'),
    ).toBeVisible();
    await screenshot(page, testInfo, 'agent-message-sent');
    await detail.getByRole('button', { name: 'Stop', exact: true }).click();
    await expect
      .poll(
        async () =>
          (await advanceOrchestration(page, conversation, 'state'))
            .child_status,
      )
      .toMatch(/^stop/);
  } finally {
    await advanceOrchestration(page, conversation, 'release-pass').catch(
      () => undefined,
    );
    if (
      !(await fixtureState(page)).calls.find(
        (item) => item.barrier_id === call.barrier_id,
      )?.quiesced
    )
      await releaseProducer(page, call).catch(() => undefined);
  }
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
