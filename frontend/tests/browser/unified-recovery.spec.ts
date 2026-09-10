import { test, expect, screenshot, writeEvidence } from './evidence';
import {
  composer,
  fixtureState,
  newConversation,
  openConversation,
  releaseProducer,
} from './unified-helpers';

test('a delayed real observer exhausts history without delaying the producer or another client', async ({
  page,
  browser,
}, info) => {
  test.setTimeout(180_000);
  const conversation = await newConversation(page);
  const origin = new URL(page.url()).origin;
  const slowContext = await browser.newContext({
    baseURL: origin,
    viewport: page.viewportSize(),
    serviceWorkers: 'block',
  });
  await slowContext.route(
    (url) =>
      ['http:', 'https:'].includes(url.protocol) && url.origin !== origin,
    (route) => route.abort(),
  );
  let releaseRequest!: () => void;
  let entered!: () => void;
  const held = new Promise<void>((resolve) => {
    entered = resolve;
  });
  const release = new Promise<void>((resolve) => {
    releaseRequest = resolve;
  });
  let first = true;
  await slowContext.route('**/api/v1/events?*', async (route) => {
    if (first) {
      first = false;
      entered();
      await release;
    }
    await route.continue();
  });
  const slow = await slowContext.newPage();
  await slow.addInitScript(() => {
    const audit = window as unknown as { __ROW_BOT_QA_RESET_SEEN__: boolean };
    audit.__ROW_BOT_QA_RESET_SEEN__ = false;
    const original = ReadableStreamDefaultReader.prototype.read;
    const decoders = new WeakMap<
      object,
      { decoder: TextDecoder; tail: string }
    >();
    ReadableStreamDefaultReader.prototype.read = async function (...args) {
      const result = await original.apply(this, args);
      if (result.value instanceof Uint8Array) {
        let state = decoders.get(this);
        if (!state) {
          state = { decoder: new TextDecoder(), tail: '' };
          decoders.set(this, state);
        }
        const received =
          state.tail + state.decoder.decode(result.value, { stream: true });
        if (received.includes('event: snapshot_required'))
          audit.__ROW_BOT_QA_RESET_SEEN__ = true;
        state.tail = received.slice(-64);
      }
      return result;
    };
  });
  const errors: string[] = [];
  const external: string[] = [];
  slow.on('pageerror', (error) => errors.push(error.name));
  slow.on('console', (event) => {
    if (event.type() === 'error') errors.push(event.text());
  });
  slow.on('request', (request) => {
    const url = new URL(request.url());
    if (['http:', 'https:'].includes(url.protocol) && url.origin !== origin)
      external.push(url.pathname);
  });
  let call;
  try {
    await openConversation(slow, conversation);
    await held;
    await composer(slow).fill('Slow observer unsent draft');
    await expect(
      slow.getByRole('status').filter({ hasText: /^Draft saved$/ }),
    ).toBeVisible();
    if (info.project.use.browserName !== 'firefox')
      info.annotations.push({
        type: 'expected-console-error',
        description: JSON.stringify({
          signature:
            'Failed to load resource: the server responded with a status of 409 (Conflict)',
          count: 1,
          owner: 'Phase 3 independent QA',
          fixture:
            'Two real clients deliberately edit the same saved draft revision',
        }),
      });
    let draftConflictResponses = 0;
    page.on('response', (response) => {
      if (
        new URL(response.url()).pathname ===
          `/api/v1/conversations/${conversation}/draft` &&
        response.status() === 409
      )
        draftConflictResponses++;
    });
    const draftConflict = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname ===
          `/api/v1/conversations/${conversation}/draft` &&
        response.status() === 409,
    );
    await composer(page).fill('exhaustion fixture');
    const conflictResponse = await draftConflict;
    expect((await conflictResponse.json()).code).toBe(
      'draft_revision_conflict',
    );
    await page
      .getByRole('button', { name: 'Review draft conflict', exact: true })
      .click();
    const conflict = page.getByRole('dialog', {
      name: 'Review draft conflict',
      exact: true,
    });
    await expect(conflict).toContainText('Slow observer unsent draft');
    await expect(conflict).toContainText('exhaustion fixture');
    await conflict
      .getByRole('button', {
        name: 'Replace saved draft with mine',
        exact: true,
      })
      .click();
    await expect(conflict).toHaveCount(0);
    await expect(
      page.getByRole('status').filter({ hasText: /^Draft saved$/ }),
    ).toBeVisible();
    await page.getByRole('button', { name: 'Send', exact: true }).click();
    await expect
      .poll(
        async () =>
          (await fixtureState(page)).calls
            .filter((item) => item.conversation_id === conversation)
            .at(-1)?.emitted_tokens,
        { timeout: 60_000 },
      )
      .toBe(5000);
    call = (await fixtureState(page)).calls
      .filter((item) => item.conversation_id === conversation)
      .at(-1)!;
    await expect(
      page.getByRole('log', { name: 'Conversation', exact: true }),
    ).toContainText('Tick 4999.');
    await releaseProducer(page, call);
    await expect
      .poll(
        async () =>
          (await fixtureState(page)).calls.find(
            (item) => item.generation_id === call!.generation_id,
          )?.quiesced,
      )
      .toBe(true);
    await expect(
      page
        .getByRole('log', { name: 'Conversation', exact: true })
        .locator('.message-text')
        .filter({ hasText: 'Exhaustion settled.' }),
    ).toHaveCount(1);
    releaseRequest();
    await expect
      .poll(() =>
        slow.evaluate(
          () =>
            (window as unknown as { __ROW_BOT_QA_RESET_SEEN__: boolean })
              .__ROW_BOT_QA_RESET_SEEN__,
        ),
      )
      .toBe(true);
    await expect(
      slow
        .getByRole('log', { name: 'Conversation', exact: true })
        .locator('.message-text')
        .filter({ hasText: 'Exhaustion settled.' }),
    ).toHaveCount(1);
    await expect(composer(slow)).toHaveValue('Slow observer unsent draft');
    const calls = (await fixtureState(page)).calls.filter(
      (item) => item.conversation_id === conversation,
    );
    expect(calls).toHaveLength(1);
    expect(draftConflictResponses).toBe(1);
    await writeEvidence(info, 'actual-exhaustion-and-slow-observer', {
      conversation,
      emittedTokens: 5000,
      providerInvocations: calls.length,
      producerQuiescedBeforeSlowRelease: true,
      actualServerReset: true,
      draftConflictHttpStatus: conflictResponse.status(),
      draftConflictResponses,
      expectedMainPageConflictConsoleErrors:
        info.project.use.browserName === 'firefox' ? 0 : 1,
      finalRowsPerClient: 1,
      errors,
      external,
    });
    await screenshot(slow, info, 'slow-observer-restored-after-exhaustion');
  } finally {
    releaseRequest();
    if (
      call &&
      !(await fixtureState(page)).calls.find(
        (item) => item.generation_id === call!.generation_id,
      )?.quiesced
    )
      await releaseProducer(page, call);
    await slowContext.close();
  }
  expect(errors).toEqual([]);
  expect(external).toEqual([]);
});
