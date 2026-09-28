import type { Page } from '@playwright/test';
import {
  accessibility,
  expect,
  screenshot,
  test,
  writeEvidence,
} from './evidence';
import {
  composer,
  fixtureState,
  newConversation,
  releaseProducer,
  type FixtureCall,
} from './unified-helpers';

// Stop with a follow-up waiting used to wedge a conversation: the message
// vanished from the page, every later send was refused, and Send again left
// a check loop (B107). These run the real queue on the fixture server.

async function callsFor(page: Page, conversation: string) {
  return (await fixtureState(page)).calls.filter(
    (item) => item.conversation_id === conversation,
  );
}

async function holdThenQueue(page: Page, conversation: string, text: string) {
  await composer(page).fill('Hold the ordinary synthetic response');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await expect(
    page.getByText('Synthetic stream is active.', { exact: true }),
  ).toBeVisible();
  const first = (await callsFor(page, conversation)).at(-1)!;
  await composer(page).fill(text);
  await page
    .getByRole('button', { name: 'Queue message', exact: true })
    .click();
  await expect(composer(page)).toHaveValue('');
  const waiting = page.getByRole('region', {
    name: 'Waiting messages',
    exact: true,
  });
  await expect(waiting).toContainText(
    '1 message waiting · sends when Row-Bot finishes',
  );
  await page.getByRole('button', { name: 'Stop', exact: true }).click();
  await releaseProducer(page, first);
  await expect
    .poll(async () =>
      (await callsFor(page, conversation)).every((item) => item.quiesced),
    )
    .toBe(true);
  return { first, waiting };
}

async function releaseAll(page: Page, conversation: string) {
  for (const call of (await callsFor(page, conversation)).filter(
    (item: FixtureCall) => !item.quiesced,
  ))
    await releaseProducer(page, call);
}

test('a message waiting at Stop stays listed and goes with Send now', async ({
  page,
}, testInfo) => {
  testInfo.annotations.push({
    type: 'expected-console-error',
    description: JSON.stringify({
      signature:
        'Failed to load resource: the server responded with a status of 409 (Conflict)',
      count: 1,
      owner: 'Phase 9 waiting messages',
      fixture:
        'A new message is sent while a stopped conversation still has one waiting (queue_pending)',
    }),
  });
  const started = Date.now();
  const marks: Record<string, number> = {};
  const mark = (name: string) => (marks[name] = Date.now() - started);
  const conversation = await newConversation(page);
  try {
    const { waiting } = await holdThenQueue(
      page,
      conversation,
      'Waiting follow-up message',
    );
    // Still listed after Stop, not sent by itself, and nothing offers a
    // Send again the server would refuse.
    await expect(waiting).toContainText('1 message waiting');
    await expect(waiting).not.toContainText('sends when');
    await expect(waiting).toContainText('Waiting follow-up message');
    await expect(
      page.getByRole('button', { name: 'Send again', exact: true }),
    ).toHaveCount(0);
    expect(await callsFor(page, conversation)).toHaveLength(1);
    mark('stopped');
    await screenshot(page, testInfo, 'waiting-after-stop');
    mark('screenshot');
    await accessibility(page, testInfo, 'waiting-after-stop');
    mark('axe');

    // A newer message is refused in plain words with the fix; nothing is
    // left pending, the draft is kept.
    await composer(page).fill('A newer message');
    await page.getByRole('button', { name: 'Send', exact: true }).click();
    const alert = page
      .getByRole('alert')
      .filter({ hasText: 'A message is waiting to be sent.' });
    await expect(alert).toBeVisible();
    await expect(
      page.getByRole('button', { name: 'Check message', exact: true }),
    ).toHaveCount(0);
    await expect(composer(page)).toHaveValue('A newer message');
    mark('refused');
    await screenshot(page, testInfo, 'waiting-refused-send');

    await alert.getByRole('button', { name: 'Send now', exact: true }).click();
    await expect
      .poll(async () => (await callsFor(page, conversation)).length)
      .toBe(2);
    mark('dispatched');
    const second = (await callsFor(page, conversation)).at(-1)!;
    await releaseProducer(page, second);
    await expect(waiting).toHaveCount(0);
    const log = page.getByRole('log', { name: 'Conversation', exact: true });
    await expect(
      log.getByText('Waiting follow-up message', { exact: true }),
    ).toHaveCount(1);

    mark('sent-now');
    // The conversation is not wedged: the newer message now sends.
    await page.getByRole('button', { name: 'Send', exact: true }).click();
    await expect
      .poll(async () => (await callsFor(page, conversation)).length)
      .toBe(3);
    mark('third-call');
    await releaseProducer(page, (await callsFor(page, conversation)).at(-1)!);
    mark('released');
    await expect(log.getByText('A newer message', { exact: true })).toHaveCount(
      1,
    );
    mark('shown');
    await writeEvidence(testInfo, 'waiting-send-now', {
      conversation,
      invocations: (await callsFor(page, conversation)).length,
      marks,
    });
  } finally {
    await releaseAll(page, conversation);
    mark('cleaned');
    await writeEvidence(testInfo, 'waiting-send-now-timing', marks);
  }
});

test('a waiting message can be discarded and the chat goes on', async ({
  page,
}, testInfo) => {
  testInfo.annotations.push({
    type: 'expected-console-error',
    description: JSON.stringify({
      signature:
        'Failed to load resource: the server responded with a status of 409 (Conflict)',
      count: 1,
      upTo: true,
      owner: 'Phase 9 waiting messages',
      fixture:
        'Discard can land before the list re-reads the revision Stop moved; it retries once on the current revision',
    }),
  });
  const conversation = await newConversation(page);
  try {
    const { waiting } = await holdThenQueue(page, conversation, 'Discard me');
    await waiting.getByRole('button', { name: 'Discard', exact: true }).click();
    const confirm = page.getByRole('alertdialog', {
      name: 'Discard this message?',
    });
    await expect(confirm).toBeVisible();
    await confirm
      .getByRole('button', { name: 'Discard message', exact: true })
      .click();
    await expect(waiting).toHaveCount(0);
    await composer(page).fill('After the discard');
    await page.getByRole('button', { name: 'Send', exact: true }).click();
    await expect
      .poll(async () => (await callsFor(page, conversation)).length)
      .toBe(2);
    await releaseProducer(page, (await callsFor(page, conversation)).at(-1)!);
    const log = page.getByRole('log', { name: 'Conversation', exact: true });
    await expect(
      log.getByText('After the discard', { exact: true }),
    ).toHaveCount(1);
    await expect(log.getByText('Discard me', { exact: true })).toHaveCount(0);
    await screenshot(page, testInfo, 'waiting-discarded');
  } finally {
    await releaseAll(page, conversation);
  }
});
