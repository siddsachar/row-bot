import type { Page, TestInfo } from '@playwright/test';
import {
  accessibility,
  expect,
  screenshot,
  test,
  writeEvidence,
} from './evidence';
import {
  composer,
  assertControlTextUnclipped,
  advanceOrchestration,
  fixtureState,
  newConversation,
  openConversation,
  releaseProducer,
} from './unified-helpers';

function summarizeFixtureFailure(error: unknown) {
  return {
    name: error instanceof Error ? error.name : typeof error,
    // Preserve the thrown primary error in Playwright's result. This separate
    // diagnostic never serializes request headers or fixture credentials.
    firstLine: (error instanceof Error ? error.message : String(error)).split(
      '\n',
    )[0],
  };
}

async function assertQueuedControlsReachable(
  page: Page,
  testInfo: TestInfo,
  sample: string,
): Promise<void> {
  for (const name of ['Queue message', 'Stop']) {
    const control = page.getByRole('button', { name, exact: true });
    await control.scrollIntoViewIfNeeded();
    const geometry = await control.evaluate((element) => {
      const rect = element.getBoundingClientRect();
      const text = document.createRange();
      text.selectNodeContents(element);
      const notices = Array.from(document.querySelectorAll('.toast'))
        .filter((notice) => {
          const style = getComputedStyle(notice);
          return (
            notice.getClientRects().length > 0 &&
            style.display !== 'none' &&
            style.visibility === 'visible' &&
            Number(style.opacity) > 0
          );
        })
        .map((notice) => notice.getBoundingClientRect().toJSON());
      const points = [
        [rect.left + rect.width / 2, rect.top + rect.height / 2],
        [rect.left + 8, rect.top + 8],
        [rect.right - 8, rect.top + 8],
        [rect.left + 8, rect.bottom - 8],
        [rect.right - 8, rect.bottom - 8],
      ];
      return {
        viewport: { width: innerWidth, height: innerHeight },
        control: rect.toJSON(),
        disabled: element instanceof HTMLButtonElement && element.disabled,
        text: text.getBoundingClientRect().toJSON(),
        notices,
        hits: points.map(([x, y]) => {
          const hit = document.elementFromPoint(x, y);
          return {
            x,
            y,
            owned: !!hit && (hit === element || element.contains(hit)),
            tag: hit?.tagName ?? null,
            className: hit?.getAttribute('class') ?? null,
          };
        }),
        overlapsNotice: notices.some(
          (notice) =>
            Math.min(rect.right, notice.right) >
              Math.max(rect.left, notice.left) &&
            Math.min(rect.bottom, notice.bottom) >
              Math.max(rect.top, notice.top),
        ),
      };
    });
    await writeEvidence(
      testInfo,
      `${sample}-${name === 'Stop' ? 'stop' : 'queue'}-notification-geometry`,
      {
        ...geometry,
        notificationState: geometry.notices.length ? 'visible' : 'not-present',
        method:
          'The first queued sample requires a real visible notification. Identical notifications are deduplicated and may expire after six seconds; later samples retain their actual notice count and action geometry.',
      },
    );
    if (sample.endsWith('-1'))
      expect(
        geometry.notices.length,
        'The first queued sample has a real visible notification',
      ).toBeGreaterThan(0);
    expect(
      geometry.overlapsNotice,
      'Notifications must not cover the action',
    ).toBe(false);
    expect(
      geometry.hits.every((point) => point.owned),
      'The real action must receive pointer hits',
    ).toBe(true);
    await assertControlTextUnclipped(control);
  }
  if (sample.endsWith('-1'))
    await screenshot(page, testInfo, `${sample}-notification-actions`);
}

// Screen-only synthetic evidence; raw protocol traces remain disabled because
// they serialize authentication headers. No fixture credential is rendered.
test.use({ video: 'on' });

test('ordinary queued messages support edit and removal before one accepted dispatch', async ({
  page,
}, testInfo) => {
  const conversation = await newConversation(page);
  await composer(page).fill('Hold the ordinary synthetic response');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await expect(
    page.getByText('Synthetic stream is active.', { exact: true }),
  ).toBeVisible();
  const first = (await fixtureState(page)).calls.at(-1)!;
  let queuedSamples = 0;
  let bodyFailed = false;
  let cleanupFailure: { error: unknown } | undefined;
  try {
    for (const text of ['Retained queue input', 'Remove this queued input']) {
      await composer(page).fill(text);
      await page
        .getByRole('button', { name: 'Queue message', exact: true })
        .click();
      await expect(composer(page)).toHaveValue('');
      await assertQueuedControlsReachable(
        page,
        testInfo,
        `ordinary-${++queuedSamples}`,
      );
    }
    await page
      .locator('summary')
      .filter({ hasText: /^Steering queue$/ })
      .click();
    const queue = page.getByRole('region', {
      name: 'Queued messages',
      exact: true,
    });
    await expect(queue.getByText('Queued', { exact: true })).toHaveCount(2);
    const retained = queue
      .getByRole('listitem')
      .filter({ hasText: 'Retained queue input' });
    await retained
      .getByRole('button', { name: 'Edit message', exact: true })
      .click();
    await retained
      .getByRole('textbox', { name: 'Edit queued message', exact: true })
      .fill('Edited queue input');
    await retained
      .getByRole('button', { name: 'Save queued edit', exact: true })
      .click();
    await expect(
      queue.getByText('Edited queue input', { exact: true }),
    ).toBeVisible();
    const removed = queue
      .getByRole('listitem')
      .filter({ hasText: 'Remove this queued input' });
    await removed
      .getByRole('button', { name: 'Remove message', exact: true })
      .click();
    await page
      .getByRole('button', { name: 'Remove queued message', exact: true })
      .click();
    await expect(queue.getByText('Cancelled', { exact: true })).toHaveCount(1);
    await expect(queue.getByText('Queued', { exact: true })).toHaveCount(1);
    await composer(page).fill('Unsent draft during queued dispatch');
    await releaseProducer(page, first);
    await expect
      .poll(
        async () =>
          (await fixtureState(page)).calls.filter(
            (item) => item.conversation_id === conversation,
          ).length,
      )
      .toBe(2);
    const second = (await fixtureState(page)).calls
      .filter((item) => item.conversation_id === conversation)
      .at(-1)!;
    expect(second.submission_id).not.toBe(first.submission_id);
    await expect(queue.getByText('Consumed', { exact: true })).toHaveCount(1);
    await expect(
      page
        .getByRole('log', { name: 'Conversation', exact: true })
        .getByText('Edited queue input', { exact: true }),
    ).toHaveCount(1);
    await expect(
      page
        .getByRole('log', { name: 'Conversation', exact: true })
        .getByText('Remove this queued input', { exact: true }),
    ).toHaveCount(0);
    await releaseProducer(page, second);
    await expect
      .poll(async () =>
        (await fixtureState(page)).calls
          .filter((item) => item.conversation_id === conversation)
          .every((item) => item.quiesced),
      )
      .toBe(true);
    await expect(composer(page)).toHaveValue(
      'Unsent draft during queued dispatch',
    );
    await writeEvidence(testInfo, 'ordinary-queue-edit-remove', {
      conversation,
      first,
      second,
      actualInvocations: (await fixtureState(page)).calls.filter(
        (item) => item.conversation_id === conversation,
      ).length,
      logicalStates: ['queued', 'edited', 'cancelled', 'consumed'],
    });
    await screenshot(page, testInfo, 'ordinary-queue-after-dispatch');
  } catch (error) {
    bodyFailed = true;
    await writeEvidence(
      testInfo,
      'ordinary-queue-body-failure',
      summarizeFixtureFailure(error),
    );
    throw error;
  } finally {
    try {
      for (const call of (await fixtureState(page)).calls.filter(
        (item) => item.conversation_id === conversation && !item.quiesced,
      ))
        await releaseProducer(page, call);
    } catch (error) {
      cleanupFailure = { error };
      await writeEvidence(testInfo, 'ordinary-queue-cleanup-failure', {
        ...summarizeFixtureFailure(error),
        primaryBodyFailurePreserved: bodyFailed,
      });
    }
  }
  if (cleanupFailure) throw cleanupFailure.error;
});

test('seven steering messages retain duplicate order and acknowledge actual parent batches with child completion', async ({
  page,
}, testInfo) => {
  const conversation = await newConversation(page);
  await composer(page).fill('steering fixture');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await expect(
    page.getByText('Synthetic child is working.', { exact: true }),
  ).toBeVisible();
  const call = (await fixtureState(page)).calls.at(-1)!;
  let queuedSamples = 0;
  let bodyFailed = false;
  let cleanupFailure: { error: unknown } | undefined;
  try {
    const texts = ['A', 'B', 'A', 'four', 'five', 'six', 'seven'];
    for (const text of texts.slice(0, 5)) {
      await composer(page).fill(text);
      await page
        .getByRole('button', { name: 'Queue message', exact: true })
        .click();
      await expect(composer(page)).toHaveValue('');
      await assertQueuedControlsReachable(
        page,
        testInfo,
        `steering-${++queuedSamples}`,
      );
    }
    await page
      .locator('summary')
      .filter({ hasText: /^Steering queue$/ })
      .click();
    const queue = page.getByRole('region', {
      name: 'Steering queue',
      exact: true,
    });
    await expect(queue.getByText('Queued', { exact: true })).toHaveCount(5);
    await advanceOrchestration(page, conversation, 'begin-pass');
    await expect
      .poll(
        async () =>
          (await advanceOrchestration(page, conversation, 'state')).batches,
      )
      .toEqual([texts.slice(0, 5)]);
    for (const text of texts.slice(5)) {
      await composer(page).fill(text);
      await page
        .getByRole('button', { name: 'Queue message', exact: true })
        .click();
      await expect(composer(page)).toHaveValue('');
      await assertQueuedControlsReachable(
        page,
        testInfo,
        `steering-${++queuedSamples}`,
      );
    }
    await expect(queue.getByText('Queued', { exact: true })).toHaveCount(7);
    const queued = await advanceOrchestration(page, conversation, 'state');
    expect(queued.steering.items.map((item) => item.text)).toEqual(texts);
    expect(new Set(queued.steering.items.map((item) => item.id)).size).toBe(7);
    await composer(page).fill('Never consumed unsent draft');
    const first = await advanceOrchestration(
      page,
      conversation,
      'release-pass',
    );
    expect(first.batches).toEqual([texts.slice(0, 5)]);
    await expect(queue.getByText('Consumed', { exact: true })).toHaveCount(5);
    await expect(queue.getByText('Queued', { exact: true })).toHaveCount(2);
    const second = await advanceOrchestration(page, conversation, 'pass');
    expect(second.batches).toEqual([texts.slice(0, 5), texts.slice(5)]);
    await expect(queue.getByText('Consumed', { exact: true })).toHaveCount(7);
    const completed = await advanceOrchestration(
      page,
      conversation,
      'finish-child',
    );
    expect(completed.child_status).toBe('completed');
    await page
      .locator('summary')
      .filter({ hasText: /^Activity \(/ })
      .click();
    await expect(
      page.getByText('Delegated task: completed', { exact: true }),
    ).toBeVisible();
    await releaseProducer(page, call);
    await expect(
      page.getByText(
        'Synthetic child is working. Synthetic joined work complete.',
        { exact: true },
      ),
    ).toHaveCount(1);
    await expect(composer(page)).toHaveValue('Never consumed unsent draft');
    await page
      .getByRole('button', { name: 'Synthetic child', exact: true })
      .click();
    const childDetail = page.getByRole('dialog', {
      name: 'Synthetic child',
      exact: true,
    });
    await expect(
      childDetail.getByText('Synthetic child result', { exact: true }),
    ).toBeVisible();
    await childDetail
      .getByRole('button', { name: 'Open child conversation', exact: true })
      .click();
    await expect(page).toHaveURL(
      new RegExp(`/conversations/${completed.child_conversation_id}$`),
    );
    await expect(
      page.getByText('Synthetic delegated objective', { exact: true }),
    ).toBeVisible();
    await composer(page).fill('Independent child draft');
    await page
      .getByRole('button', { name: 'Back to parent conversation', exact: true })
      .click();
    await expect(page).toHaveURL(new RegExp(`/conversations/${conversation}$`));
    await expect(composer(page)).toHaveValue('Never consumed unsent draft');
    expect(
      (await fixtureState(page)).calls.filter(
        (item) => item.conversation_id === completed.child_conversation_id,
      ),
    ).toHaveLength(0);
    await screenshot(page, testInfo, 'real-steering-batches-and-child');
    await writeEvidence(testInfo, 'parent-consumption', {
      conversation,
      generation: call.generation_id,
      queued,
      first,
      second,
      completed,
    });
  } catch (error) {
    bodyFailed = true;
    await writeEvidence(
      testInfo,
      'seven-steering-body-failure',
      summarizeFixtureFailure(error),
    );
    throw error;
  } finally {
    try {
      await advanceOrchestration(page, conversation, 'release-pass');
      if (
        !(await fixtureState(page)).calls.find(
          (item) => item.barrier_id === call.barrier_id,
        )?.quiesced
      )
        await releaseProducer(page, call);
    } catch (error) {
      cleanupFailure = { error };
      await writeEvidence(testInfo, 'seven-steering-cleanup-failure', {
        ...summarizeFixtureFailure(error),
        primaryBodyFailurePreserved: bodyFailed,
      });
    }
  }
  if (cleanupFailure) throw cleanupFailure.error;
});

test('one hundred sixty token events preserve acknowledgement progress and Stop authority', async ({
  page,
}, testInfo) => {
  const conversation = await newConversation(page);
  await composer(page).fill('burst fixture');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await expect(
    page
      .locator('.message-text')
      .filter({ hasText: 'Burst complete; waiting for Stop.' }),
  ).toBeVisible();
  const call = (await fixtureState(page)).calls.at(-1)!;
  await page.getByRole('button', { name: 'Stop', exact: true }).click();
  await expect
    .poll(
      async () =>
        (await fixtureState(page)).calls.find(
          (item) => item.barrier_id === call.barrier_id,
        )?.quiesced,
    )
    .toBe(true);
  await expect(
    page.getByRole('button', { name: 'Send', exact: true }),
  ).toBeVisible();
  await writeEvidence(testInfo, 'burst-stop-control', {
    conversation,
    generation: call.generation_id,
    tokens: 160,
    quiesced: true,
  });
});

test('ordinary conversation streams, settles once and restores its unsent draft', async ({
  page,
}, testInfo) => {
  const conversation = await newConversation(page);
  const before = await fixtureState(page);
  await composer(page).fill('Please provide the synthetic response');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await expect(
    page.getByText('Synthetic stream is active.', { exact: true }),
  ).toBeVisible();
  const state = await fixtureState(page);
  const call = state.calls.at(-1)!;
  expect(state.calls).toHaveLength(before.calls.length + 1);
  expect(call.conversation_id).toBe(conversation);
  await composer(page).fill('Keep this unsent draft');
  await releaseProducer(page, call);
  await expect(
    page.getByText('Synthetic stream is active. Synthetic stream settled.', {
      exact: true,
    }),
  ).toHaveCount(1);
  await expect(composer(page)).toHaveValue('Keep this unsent draft');
  await page.reload();
  await expect(composer(page)).toHaveValue('Keep this unsent draft');
  await expect(
    page.getByText('Synthetic stream is active. Synthetic stream settled.', {
      exact: true,
    }),
  ).toHaveCount(1);
  const final = await fixtureState(page);
  expect(final.calls).toHaveLength(state.calls.length);
  expect(final.external_calls).toBe(0);
  await writeEvidence(testInfo, 'generation-lifecycle', {
    conversation: call.conversation_id,
    submission: call.submission_id,
    generation: call.generation_id,
    checkpoints: ['submitted', 'streaming', 'settled', 'reloaded'],
    providerInvocations: final.calls.length - before.calls.length,
  });
  await screenshot(page, testInfo, 'settled-conversation-and-draft');
  await accessibility(page, testInfo, 'conversation-axe');
});

test('navigation and a second observer retain one server generation', async ({
  page,
  browser,
}, testInfo) => {
  const conversation = await newConversation(page);
  const before = await fixtureState(page);
  await composer(page).fill('Observe this synthetic response from two clients');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await expect(
    page.getByText('Synthetic stream is active.', { exact: true }),
  ).toBeVisible();
  const call = (await fixtureState(page)).calls.at(-1)!;
  const origin = new URL(page.url()).origin;
  const observerContext = await browser.newContext({
    baseURL: origin,
    viewport: page.viewportSize(),
    serviceWorkers: 'block',
  });
  const observations: {
    errors: string[];
    external: string[];
    responses: { path: string; status: number }[];
  } = {
    errors: [],
    external: [],
    responses: [],
  };
  await observerContext.route(
    (url) =>
      ['http:', 'https:'].includes(url.protocol) && url.origin !== origin,
    async (route) => {
      await route.abort();
    },
  );
  const observer = await observerContext.newPage();
  const safeError = (value: string) =>
    value
      .split(process.env.ROW_BOT_BROWSER_CONTROL_TOKEN!)
      .join('<fixture-control>');
  observer.on('pageerror', (error) =>
    observations.errors.push(safeError(error.message)),
  );
  observer.on('console', (event) => {
    if (event.type() === 'error')
      observations.errors.push(safeError(event.text()));
  });
  observer.on('request', (request) => {
    const url = new URL(request.url());
    if (['http:', 'https:'].includes(url.protocol) && url.origin !== origin)
      observations.external.push(url.pathname);
  });
  observer.on('response', (response) =>
    observations.responses.push({
      path: new URL(response.url()).pathname,
      status: response.status(),
    }),
  );
  try {
    await openConversation(observer, conversation);
    await expect(
      observer.getByText('Synthetic stream is active.', { exact: true }),
    ).toBeVisible();
    await openConversation(page, 'p1-browser-b');
    await composer(page).fill('Independent conversation B draft');
    await releaseProducer(observer, call);
    await expect(
      observer.getByText(
        'Synthetic stream is active. Synthetic stream settled.',
        { exact: true },
      ),
    ).toHaveCount(1);
    await expect(composer(page)).toHaveValue(
      'Independent conversation B draft',
    );
    await expect
      .poll(
        async () =>
          (await fixtureState(observer)).calls.find(
            (item) => item.barrier_id === call.barrier_id,
          )?.quiesced,
      )
      .toBe(true);
    const final = await fixtureState(observer);
    expect(final.calls).toHaveLength(before.calls.length + 1);
    const finalCall = final.calls.find(
      (item) => item.barrier_id === call.barrier_id,
    );
    expect(finalCall?.quiesced).toBe(true);
    await writeEvidence(testInfo, 'two-observers-lifecycle', {
      conversation: call.conversation_id,
      generation: call.generation_id,
      barrierId: call.barrier_id,
      quiesced: finalCall?.quiesced,
      providerInvocations: 1,
      navigatedConversationDraft: 'retained',
    });
  } finally {
    await observerContext.close();
    await writeEvidence(testInfo, 'second-client-observations', observations);
  }
  expect(observations.errors).toEqual([]);
  expect(observations.external).toEqual([]);
});

test('Stop reaches the owned producer and leaves the composer usable', async ({
  page,
}, testInfo) => {
  await newConversation(page);
  const before = await fixtureState(page);
  await composer(page).fill('stop fixture');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await expect(
    page.getByText('Synthetic stream is active.', { exact: true }),
  ).toBeVisible();
  await page.getByRole('button', { name: 'Stop', exact: true }).click();
  await expect
    .poll(async () => (await fixtureState(page)).calls.at(-1)?.quiesced)
    .toBe(true);
  await expect(composer(page)).toBeEnabled();
  expect((await fixtureState(page)).calls).toHaveLength(
    before.calls.length + 1,
  );
  await screenshot(page, testInfo, 'stopped-conversation');
});

test('current approval is reviewed once and resumes its original conversation', async ({
  page,
}, testInfo) => {
  const conversation = await newConversation(page);
  const before = await fixtureState(page);
  await composer(page).fill('approval fixture');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await page
    .getByRole('button', { name: 'Review approval', exact: true })
    .click();
  const dialog = page.getByRole('dialog', {
    name: 'Approval required',
    exact: true,
  });
  await expect(dialog).toBeVisible();
  await expect(
    dialog.getByRole('button', { name: 'Reject action', exact: true }),
  ).toBeEnabled();
  const focusContained: boolean[] = [];
  for (const key of [
    ...Array<string>(12).fill('Tab'),
    ...Array<string>(12).fill('Shift+Tab'),
  ]) {
    await page.keyboard.press(key);
    const contained = await dialog.evaluate((element) =>
      element.contains(document.activeElement),
    );
    focusContained.push(contained);
    expect(contained).toBe(true);
  }
  await writeEvidence(testInfo, 'approval-focus-containment', {
    forwardTabs: 12,
    backwardTabs: 12,
    focusContained,
    scope:
      'Keyboard containment checks; automated axe incomplete rules remain separately reported.',
  });
  await screenshot(page, testInfo, 'approval-consequences');
  await accessibility(page, testInfo, 'approval-dialog-axe');
  await dialog
    .getByRole('button', { name: 'Approve action', exact: true })
    .click();
  await expect(dialog).toHaveCount(0);
  await expect(
    page.getByText('Synthetic approval resumed.', { exact: true }),
  ).toHaveCount(1);
  const after = await fixtureState(page);
  const calls = after.calls.slice(before.calls.length);
  expect(calls.map((call) => call.kind)).toEqual(['submit', 'resume']);
  expect(
    calls.every(
      (call) => call.conversation_id === conversation && call.quiesced,
    ),
  ).toBe(true);
  await expect(composer(page)).toHaveCount(1);
  await screenshot(page, testInfo, 'approval-resumed-once');
  await writeEvidence(testInfo, 'approval-lifecycle', {
    conversation,
    calls,
    decisions: 1,
  });
});

test('tool media appears through the real opaque attachment owner and preserves the final', async ({
  page,
}, testInfo) => {
  await newConversation(page);
  await composer(page).fill('rich fixture');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await expect(
    page.getByText('Synthetic tools and media are ready.', { exact: true }),
  ).toBeVisible();
  const call = (await fixtureState(page)).calls.at(-1)!;
  try {
    await page
      .locator('summary')
      .filter({ hasText: /^Activity \(/ })
      .click();
    const result = page.getByRole('img', {
      name: 'Generated result',
      exact: true,
    });
    await expect(result).toBeVisible();
    await expect
      .poll(() =>
        result.evaluate(
          (element) =>
            element instanceof HTMLImageElement &&
            element.complete &&
            element.naturalWidth > 0,
        ),
      )
      .toBe(true);
    await screenshot(page, testInfo, 'real-tools-and-media');
    await releaseProducer(page, call);
    await expect
      .poll(async () => (await fixtureState(page)).calls.at(-1)?.quiesced)
      .toBe(true);
    await expect(
      page
        .locator('.message-text')
        .filter({ hasText: "print('local fixture')" }),
    ).toHaveCount(1);
    await accessibility(page, testInfo, 'tools-media-final-axe');
  } finally {
    if (
      !(await fixtureState(page)).calls.find(
        (item) => item.barrier_id === call.barrier_id,
      )?.quiesced
    )
      await releaseProducer(page, call);
  }
});
