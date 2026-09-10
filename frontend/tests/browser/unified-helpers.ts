import type { BrowserContext, Locator, Page, TestInfo } from '@playwright/test';
import { expect, screenshot, writeEvidence } from './evidence';

export type FixtureCall = {
  kind: string;
  conversation_id: string;
  case: string;
  generation_id: string;
  submission_id: string;
  barrier_id: string;
  quiesced: boolean;
  accepted_binding_ids?: string[];
  final_binding_ids?: string[];
  emitted_tokens?: number;
};

function fixtureHeaders(): Record<string, string> {
  const token = process.env.ROW_BOT_BROWSER_CONTROL_TOKEN;
  const base = process.env.ROW_BOT_BROWSER_BASE_URL;
  if (!token || !base)
    throw new Error('Use the isolated Phase 3 browser runner');
  return { 'X-Fixture-Token': token, Origin: new URL(base).origin };
}

export async function blockFixtureServiceWorkers(
  context: BrowserContext,
): Promise<void> {
  await context.addInitScript(() => {
    let workers: ServiceWorkerContainer | undefined;
    try {
      workers = navigator.serviceWorker;
    } catch (error) {
      if (!(error instanceof DOMException && error.name === 'SecurityError'))
        throw error;
    }
    if (workers)
      workers.register = async () => {
        throw new DOMException(
          'Fixture blocks service workers',
          'NotAllowedError',
        );
      };
  });
}

export async function fixtureState(page: Page): Promise<{
  calls: FixtureCall[];
  external_calls: number;
}> {
  const response = await page.request.get('/__p1_fixture/state', {
    headers: fixtureHeaders(),
  });
  expect(response.ok()).toBe(true);
  return response.json();
}

export async function releaseProducer(
  page: Page,
  call: FixtureCall,
): Promise<void> {
  const response = await page.request.post(
    `/__p1_fixture/release/${call.barrier_id}`,
    {
      headers: fixtureHeaders(),
    },
  );
  expect(response.ok()).toBe(true);
}

export async function startExpiryProducer(
  page: Page,
  call: FixtureCall,
): Promise<void> {
  const response = await page.request.post(
    `/__p3_fixture/expiry/${call.barrier_id}/start`,
    { headers: fixtureHeaders() },
  );
  expect(response.ok()).toBe(true);
}

export async function installResetProbe(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const audit = window as unknown as { __QA_RESET_COUNT__: number };
    audit.__QA_RESET_COUNT__ = 0;
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
        const text =
          state.tail + state.decoder.decode(result.value, { stream: true });
        if (text.includes('event: snapshot_required'))
          audit.__QA_RESET_COUNT__++;
        state.tail = text.slice(-64);
      }
      return result;
    };
  });
}

export async function advanceCadence(
  page: Page,
  barrier: string,
  index: number,
): Promise<void> {
  const response = await page.request.post(
    `/__p3_fixture/cadence/${barrier}/${index}`,
    {
      headers: fixtureHeaders(),
    },
  );
  expect(response.ok()).toBe(true);
}

export async function fixtureResources(page: Page): Promise<{
  workspace_id: string;
  artifact_id: string;
  fixture_file_sha256: string;
  git_present: boolean;
}> {
  const response = await page.request.get('/__p3_fixture/resources', {
    headers: fixtureHeaders(),
  });
  expect(response.ok()).toBe(true);
  return response.json();
}

export async function advanceOrchestration(
  page: Page,
  conversation: string,
  action: 'state' | 'pass' | 'begin-pass' | 'release-pass' | 'finish-child',
): Promise<{
  child_id: string;
  child_conversation_id: string;
  child_status: string;
  parent_state: string;
  batches: string[][];
  steering: { items: { id: string; text: string; state: string }[] };
}> {
  const response = await page.request.post(
    `/__p3_fixture/orchestration/${conversation}/${action}`,
    {
      headers: fixtureHeaders(),
    },
  );
  expect(response.ok()).toBe(true);
  return response.json();
}

export async function seedLargeLibrary(page: Page): Promise<{
  conversation_count: number;
  message_count: number;
  conversation_id: string;
  message_id: string;
  short_conversation_id: string;
  short_last_message_id: string;
  query: string;
  oversized_conversation_id: string;
  oversized_message_id: string;
  oversized_public_sha256: string;
  oversized_public_characters: number;
}> {
  const response = await page.request.post('/__p3_fixture/large-library', {
    headers: fixtureHeaders(),
    timeout: 120_000,
  });
  expect(response.ok()).toBe(true);
  return response.json();
}

export async function conversationState(
  page: Page,
  id: string,
): Promise<{
  conversation: {
    id: string;
    resource_bindings: {
      binding_id: string;
      resource_id: string;
      kind: string;
    }[];
  };
  workspace: {
    controls: unknown;
    resources: { resource_revision: string; title: string }[];
  };
}> {
  const response = await page.request.get(`/__p3_fixture/conversation/${id}`, {
    headers: fixtureHeaders(),
  });
  expect(response.ok()).toBe(true);
  return response.json();
}

export function composer(page: Page) {
  return page.getByRole('textbox', {
    name: 'Message',
    exact: true,
    includeHidden: true,
  });
}

export async function openConversation(
  page: Page,
  id = 'p1-browser-a',
): Promise<void> {
  const opened = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === `/api/v1/conversations/${id}/open` &&
      response.ok(),
  );
  await page.goto(`/app-v2/conversations/${id}`);
  await opened;
  await expect(
    page.getByLabel('Opening conversation', { exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole('button', { name: 'Browse history', exact: true }),
  ).toBeEnabled();
  await expect(
    page.getByRole('status').filter({ hasText: /^Connected$/ }),
  ).toBeVisible();
  await expect(composer(page)).toBeVisible();
  await expect(composer(page)).toHaveCount(1);
}

export async function newConversation(page: Page): Promise<string> {
  await page.goto('/app-v2/');
  await expect(
    page.getByRole('status').filter({ hasText: /^Connected$/ }),
  ).toBeVisible();
  await page
    .locator('.home-view')
    .getByRole('button', { name: 'New chat', exact: true })
    .click();
  await expect(page).toHaveURL(/\/app-v2\/conversations\/[^/?]+/);
  await expect(composer(page)).toBeVisible();
  await expect(composer(page)).toHaveCount(1);
  const id = new URL(page.url()).pathname.split('/').at(-1);
  if (!id)
    throw new Error('New chat did not select its confirmed conversation');
  return id;
}

export async function markWorkspaceIdentity(page: Page): Promise<void> {
  await composer(page).evaluate((element) => {
    element.setAttribute('data-qa-composer-identity', 'retained');
  });
  await page
    .getByRole('log', { name: 'Conversation', includeHidden: true })
    .evaluate((element) => {
      element.setAttribute('data-qa-transcript-identity', 'retained');
    });
}

export async function assertWorkspaceIdentity(page: Page): Promise<void> {
  await expect(composer(page)).toHaveAttribute(
    'data-qa-composer-identity',
    'retained',
  );
  await expect(
    page.getByRole('log', { name: 'Conversation', includeHidden: true }),
  ).toHaveAttribute('data-qa-transcript-identity', 'retained');
}

export async function assertControlTextUnclipped(
  control: Locator,
): Promise<void> {
  await control.scrollIntoViewIfNeeded();
  // The visual-alignment contract intentionally compacts fine-pointer actions.
  // Touch and comfortable/compact-viewport controls retain the literal 44px
  // minimum; text clipping and actual hit testing below remain unchanged.
  const minimumHeight = await control.evaluate(() => {
    const controlSize = Number.parseFloat(
      getComputedStyle(document.documentElement).getPropertyValue(
        '--control-size',
      ),
    );
    return matchMedia('(pointer: coarse)').matches || controlSize >= 44
      ? 44
      : 34;
  });
  await expect
    .poll(() =>
      control.evaluate((element) => element.getBoundingClientRect().height),
    )
    .toBeGreaterThanOrEqual(minimumHeight);
  await expect
    .poll(
      () =>
        control.evaluate((element) => {
          const range = document.createRange();
          range.selectNodeContents(element);
          const text = range.getBoundingClientRect();
          let top = 0,
            left = 0,
            bottom = innerHeight,
            right = innerWidth;
          for (
            let parent = element.parentElement;
            parent;
            parent = parent.parentElement
          ) {
            const style = getComputedStyle(parent),
              box = parent.getBoundingClientRect(),
              scaleX = parent.offsetWidth ? box.width / parent.offsetWidth : 1,
              scaleY = parent.offsetHeight
                ? box.height / parent.offsetHeight
                : 1;
            if (/(auto|scroll|hidden|clip)/.test(style.overflowY)) {
              top = Math.max(top, box.top + parent.clientTop * scaleY);
              bottom = Math.min(
                bottom,
                box.top + (parent.clientTop + parent.clientHeight) * scaleY,
              );
            }
            if (/(auto|scroll|hidden|clip)/.test(style.overflowX)) {
              left = Math.max(left, box.left + parent.clientLeft * scaleX);
              right = Math.min(
                right,
                box.left + (parent.clientLeft + parent.clientWidth) * scaleX,
              );
            }
          }
          return (
            text.height > 0 &&
            text.top >= top - 1 &&
            text.bottom <= bottom + 1 &&
            text.left >= left - 1 &&
            text.right <= right + 1
          );
        }),
      { message: 'Control text must fit its clipping ancestors and viewport' },
    )
    .toBe(true);
}

export async function assertConversationSummaries(page: Page): Promise<void> {
  for (const label of [/^Steering queue$/, /^Activity \(/]) {
    const summary = page
      .locator('.chat-workspace > details.activity > summary')
      .filter({ hasText: label });
    if (await summary.count()) await assertControlTextUnclipped(summary);
  }
}

export async function addReviewResourcePair(
  page: Page,
  deck: string,
): Promise<void> {
  for (const kind of ['artifact', 'workspace']) {
    await page
      .getByRole('button', { name: 'Add resource', exact: true })
      .click();
    const setup = page.getByRole('dialog', {
      name: 'Add resource',
      exact: true,
    });
    if (kind === 'workspace') {
      await setup
        .getByRole('button', { name: 'Start another resource', exact: true })
        .click();
      await setup
        .getByRole('combobox', { name: 'Resource type', exact: true })
        .selectOption('workspace');
      await setup
        .getByRole('combobox', { name: 'Choose resource', exact: true })
        .selectOption('existing');
      await setup
        .getByRole('button', {
          name: `Phase 1 workspace Resource ID: ${(await fixtureResources(page)).workspace_id}`,
          exact: true,
        })
        .click();
      await setup
        .getByRole('button', { name: 'Add to this conversation', exact: true })
        .click();
    } else {
      await setup
        .getByRole('textbox', { name: 'Name (optional)', exact: true })
        .fill(deck);
      await setup
        .getByRole('button', { name: 'Create Deck', exact: true })
        .click();
    }
    await expect(
      setup.getByText('Resource ready', { exact: true }),
    ).toBeVisible();
    await page.keyboard.press('Escape');
    if (page.viewportSize()!.width < 1024)
      await page
        .getByRole('button', { name: 'Back to conversation', exact: true })
        .click();
  }
}

export async function captureActualResourcePanels(
  page: Page,
  info: TestInfo,
  deck: string,
  label: string,
): Promise<void> {
  const desktop = page.viewportSize()!.width >= 1024;
  await page
    .locator('.resource-chips')
    .getByRole('button', { name: deck, exact: true })
    .click();
  const preview = page.getByRole('region', {
    name: 'Design preview',
    exact: true,
  });
  await expect(preview).toBeVisible();
  await expect(preview.locator('iframe')).toBeVisible();
  const assertSlideVisible = async () => {
    const frame = preview.locator('iframe');
    await frame.scrollIntoViewIfNeeded();
    await expect
      .poll(
        () =>
          frame.evaluate((element) => {
            const box = element.getBoundingClientRect();
            let left = Math.max(0, box.left),
              right = Math.min(innerWidth, box.right);
            let top = Math.max(0, box.top),
              bottom = Math.min(innerHeight, box.bottom);
            for (
              let parent = element.parentElement;
              parent;
              parent = parent.parentElement
            ) {
              const style = getComputedStyle(parent),
                clip = parent.getBoundingClientRect(),
                scaleX = parent.offsetWidth
                  ? clip.width / parent.offsetWidth
                  : 1,
                scaleY = parent.offsetHeight
                  ? clip.height / parent.offsetHeight
                  : 1;
              if (/(auto|scroll|hidden|clip)/.test(style.overflowX)) {
                left = Math.max(left, clip.left + parent.clientLeft * scaleX);
                right = Math.min(
                  right,
                  clip.left + (parent.clientLeft + parent.clientWidth) * scaleX,
                );
              }
              if (/(auto|scroll|hidden|clip)/.test(style.overflowY)) {
                top = Math.max(top, clip.top + parent.clientTop * scaleY);
                bottom = Math.min(
                  bottom,
                  clip.top + (parent.clientTop + parent.clientHeight) * scaleY,
                );
              }
            }
            return (
              right - left >= 100 &&
              bottom - top >= 64 &&
              document.elementFromPoint(
                box.left + box.width / 2,
                box.top + box.height / 2,
              ) === element
            );
          }),
        {
          message:
            'Actual slide centre must be visible and hit-testable inside its dock',
        },
      )
      .toBe(true);
  };
  if (desktop) {
    const bottom = page.getByRole('region', {
      name: 'Bottom panels',
      exact: true,
    });
    if (
      !(await bottom
        .getByRole('region', { name: 'Design preview', exact: true })
        .count())
    ) {
      await page
        .getByRole('region', { name: 'Side panels', exact: true })
        .getByRole('button', { name: 'Panel actions', exact: true })
        .click();
      await page
        .getByRole('menuitem', { name: 'Move to bottom', exact: true })
        .click();
    }
    await expect(
      bottom.getByRole('region', { name: 'Design preview', exact: true }),
    ).toBeVisible();
  } else {
    await assertSlideVisible();
    await screenshot(page, info, `${label}-deck-compact`);
    await page
      .getByRole('button', { name: 'Back to conversation', exact: true })
      .click();
  }
  await page
    .locator('.resource-chips')
    .getByRole('button', { name: 'Phase 1 workspace', exact: true })
    .click();
  const inspector = page.getByRole('region', {
    name: 'Phase 1 workspace inspector',
    exact: true,
  });
  await expect(inspector).toBeVisible();
  await expect(inspector).toContainText('Folder is not a Git repository');
  if (desktop) {
    await expect(
      page
        .getByRole('region', { name: 'Side panels', exact: true })
        .getByRole('region', {
          name: 'Phase 1 workspace inspector',
          exact: true,
        }),
    ).toBeVisible();
    await expect(preview).toBeVisible();
    await assertSlideVisible();
    await expect(composer(page)).toBeVisible();
    await assertConversationSummaries(page);
    await screenshot(page, info, `${label}-both-desktop-panels`);
  } else {
    await screenshot(page, info, `${label}-inspector-compact`);
    await page
      .getByRole('button', { name: 'Back to conversation', exact: true })
      .click();
    await expect(composer(page)).toBeVisible();
    await assertConversationSummaries(page);
  }
  await assertWorkspaceIdentity(page);
  await writeEvidence(info, `${label}-actual-panel-presence`, {
    desktop,
    deck,
    inspector: 'Phase 1 workspace inspector',
    composerCount: await composer(page).count(),
  });
}
