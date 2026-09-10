import type { Browser, Page } from '@playwright/test';
import {
  expect,
  screenshot,
  test,
  writeEvidence,
  assertNoOverflow,
} from './evidence';
import {
  assertWorkspaceIdentity,
  blockFixtureServiceWorkers,
  composer,
  conversationState,
  fixtureResources,
  fixtureState,
  markWorkspaceIdentity,
  newConversation,
  openConversation,
  releaseProducer,
} from './unified-helpers';

test.use({ serviceWorkers: 'allow' });
test.beforeEach(async ({ context }) => blockFixtureServiceWorkers(context));

type Layout = {
  panels: {
    instance_id: string;
    descriptor: { title: string };
    placement: string;
  }[];
  activePanelId: string | null;
  presentation: { dismissed: string[]; lastExplicitKey: string | null };
};

async function layoutFor(page: Page, conversation: string): Promise<Layout> {
  return page.evaluate((id) => {
    const size =
      innerWidth >= 1024 ? 'desktop' : innerWidth >= 768 ? 'tablet' : 'phone';
    const key = Object.keys(localStorage).find(
      (value) =>
        value.startsWith('row-bot:layout:v2:') &&
        value.endsWith(`:${encodeURIComponent(id)}:${size}`),
    );
    return key ? JSON.parse(localStorage.getItem(key)!) : null;
  }, conversation);
}

async function home(page: Page): Promise<void> {
  const link = page.getByRole('link', { name: 'Home', exact: true });
  if (!(await link.isVisible()))
    await page
      .getByRole('button', { name: 'Toggle navigation', exact: true })
      .click();
  await link.click();
  await expect(page).toHaveURL(/\/app-v2\/?$/);
  await expect(
    page.getByRole('heading', { name: 'Home', exact: true }),
  ).toBeVisible();
}

async function addDeck(page: Page, name: string): Promise<void> {
  await page.getByRole('button', { name: 'Add resource', exact: true }).click();
  const setup = page.getByRole('dialog', { name: 'Add resource', exact: true });
  const another = setup.getByRole('button', {
    name: 'Start another resource',
    exact: true,
  });
  if (await another.isVisible()) await another.click();
  await setup
    .getByRole('textbox', { name: 'Name (optional)', exact: true })
    .fill(name);
  await setup.getByRole('button', { name: 'Create Deck', exact: true }).click();
  await expect(
    setup.getByText('Resource ready', { exact: true }),
  ).toBeVisible();
  await page.keyboard.press('Escape');
}

async function independentPeer(browser: Browser, page: Page) {
  const origin = new URL(page.url()).origin;
  const context = await browser.newContext({
    baseURL: origin,
    viewport: page.viewportSize(),
    serviceWorkers: 'allow',
  });
  await blockFixtureServiceWorkers(context);
  await context.route(
    (url) =>
      ['http:', 'https:'].includes(url.protocol) && url.origin !== origin,
    (route) => route.abort(),
  );
  const peer = await context.newPage();
  const errors: string[] = [];
  const safe = (value: string) =>
    value
      .split(process.env.ROW_BOT_BROWSER_CONTROL_TOKEN ?? 'no-fixture-token')
      .join('<fixture-control>');
  peer.on('pageerror', (error) => errors.push(safe(error.message)));
  peer.on('console', (event) => {
    if (event.type() === 'error') errors.push(safe(event.text()));
  });
  return {
    page: peer,
    close: async () => {
      await context.close();
      expect(errors, 'Independent peer has no unexplained errors').toEqual([]);
    },
  };
}

test('Home is a real bounded library and New chat creates exactly once without setup', async ({
  page,
}, info) => {
  const creates: string[] = [];
  page.on('request', (request) => {
    if (
      request.method() !== 'POST' ||
      !new URL(request.url()).pathname.endsWith('/commands')
    )
      return;
    const payload = request.postDataJSON();
    if (payload?.type === 'conversation.create')
      creates.push(payload.command_id);
  });
  await page.goto('/app-v2/');
  await expect(
    page.getByRole('heading', { name: 'Home', exact: true }),
  ).toBeVisible();
  for (const name of [
    'Recent conversations',
    'Designer library',
    'Developer library',
  ])
    await expect(page.getByRole('region', { name, exact: true })).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Create Deck', exact: true }),
  ).toBeEnabled();
  await expect(
    page.getByRole('button', { name: 'Open folder', exact: true }),
  ).toBeEnabled();
  expect(creates).toEqual([]);
  await screenshot(page, info, 'home-real-libraries');
  // The top sidebar action is also present in the compact navigation drawer.
  if (
    !(await page
      .getByRole('navigation', { name: 'Workspace navigation' })
      .isVisible())
  )
    await page
      .getByRole('button', { name: 'Toggle navigation', exact: true })
      .click();
  await page
    .getByRole('navigation', { name: 'Workspace navigation' })
    .getByRole('button', { name: 'New chat', exact: true })
    .click();
  await expect(page).toHaveURL(/\/app-v2\/conversations\/[^/?]+/);
  await expect(composer(page)).toBeVisible();
  await expect(composer(page)).toHaveCount(1);
  await expect(page.getByRole('dialog')).toHaveCount(0);
  expect(creates).toHaveLength(1);
  await home(page);
  await page.reload();
  await expect(
    page.getByRole('heading', { name: 'Home', exact: true }),
  ).toBeVisible();
  expect(creates).toHaveLength(1);
  await writeEvidence(info, 'home-navigation-creations', {
    creates,
    noForm: true,
  });
});

test('Home and Back preserve the same running producer, mounted composer and unsent draft', async ({
  page,
}, info) => {
  const id = await newConversation(page);
  await composer(page).fill('Hold the ordinary synthetic response');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await expect
    .poll(
      async () =>
        (await fixtureState(page)).calls.filter(
          (value) => value.conversation_id === id,
        ).length,
    )
    .toBe(1);
  const call = (await fixtureState(page)).calls.find(
    (value) => value.conversation_id === id,
  )!;
  try {
    await composer(page).fill('Unsent Home navigation draft');
    await markWorkspaceIdentity(page);
    await home(page);
    expect(
      (await fixtureState(page)).calls.find(
        (value) => value.generation_id === call.generation_id,
      )?.quiesced,
    ).toBe(false);
    await page.goBack();
    await expect(composer(page)).toBeVisible();
    await expect(composer(page)).toHaveValue('Unsent Home navigation draft');
    await assertWorkspaceIdentity(page);
    expect(
      (await fixtureState(page)).calls.filter(
        (value) => value.conversation_id === id,
      ),
    ).toHaveLength(1);
    await screenshot(page, info, 'home-back-running-draft');
  } finally {
    await releaseProducer(page, call);
  }
});

test('explicit Deck opens automatically, persists close through revisit/reload, and reopens the same binding', async ({
  page,
}, info) => {
  const id = await newConversation(page);
  await composer(page).fill('Draft with a Deck');
  await markWorkspaceIdentity(page);
  await addDeck(page, 'Automatic presentation Deck');
  await expect(
    page.getByRole('region', { name: 'Design preview', exact: true }),
  ).toBeVisible();
  await expect
    .poll(async () => (await layoutFor(page, id))?.panels.length)
    .toBe(1);
  const binding = (await conversationState(page, id)).conversation
    .resource_bindings[0];
  await screenshot(page, info, 'deck-open-after-confirmed-binding');
  await page
    .getByRole('button', { name: 'Close all panels', exact: true })
    .click();
  await expect(composer(page)).toBeVisible();
  await assertWorkspaceIdentity(page);
  await expect
    .poll(async () => (await layoutFor(page, id)).presentation.dismissed.length)
    .toBe(1);
  await home(page);
  await page.goBack();
  await expect(composer(page)).toBeVisible();
  expect((await layoutFor(page, id)).panels).toEqual([]);
  await page.reload();
  await expect(composer(page)).toBeVisible();
  expect((await layoutFor(page, id)).panels).toEqual([]);
  await page
    .locator('.resource-chips')
    .getByRole('button', { name: 'Automatic presentation Deck', exact: true })
    .click();
  await expect(
    page.getByRole('region', { name: 'Design preview', exact: true }),
  ).toBeVisible();
  expect(
    (await conversationState(page, id)).conversation.resource_bindings,
  ).toEqual([binding]);
  await expect
    .poll(async () => (await layoutFor(page, id)).presentation.dismissed.length)
    .toBe(0);
  await writeEvidence(info, 'close-revisit-reload-explicit-reopen', {
    binding,
    layout: await layoutFor(page, id),
  });
});

for (const zoom of [1, 2])
  test(`background binding registers once without stealing typing or synthetic IME at labelled CSS zoom ${zoom}`, async ({
    page,
    browser,
  }, info) => {
    const id = await newConversation(page);
    const observer = await independentPeer(browser, page);
    const peer = observer.page;
    await openConversation(peer, id);
    await page.evaluate((value) => {
      document.documentElement.style.zoom = String(value);
    }, zoom);
    await composer(page).fill('Typing selection remains intact');
    await composer(page).focus();
    await composer(page).evaluate((element: HTMLTextAreaElement) => {
      element.setSelectionRange(2, 9);
      element.dispatchEvent(
        new CompositionEvent('compositionstart', {
          bubbles: true,
          data: 'synthetic',
        }),
      );
    });
    await markWorkspaceIdentity(page);
    try {
      await addDeck(peer, `Background available Deck ${zoom}`);
      await expect
        .poll(async () => (await layoutFor(page, id))?.panels.length)
        .toBe(1);
      await expect(composer(page)).toBeVisible();
      await expect(composer(page)).toBeFocused();
      await expect(composer(page)).toHaveValue(
        'Typing selection remains intact',
      );
      expect(
        await composer(page).evaluate((element: HTMLTextAreaElement) => [
          element.selectionStart,
          element.selectionEnd,
        ]),
      ).toEqual([2, 9]);
      await assertWorkspaceIdentity(page);
      if (page.viewportSize()!.width < 1024) {
        expect((await layoutFor(page, id)).activePanelId).toBeNull();
        await expect(page.getByRole('dialog')).toHaveCount(0);
        await expect(
          page.locator('.panel-rail').getByRole('button', {
            name: `Background available Deck ${zoom}`,
            exact: true,
          }),
        ).toBeVisible();
      }
      await assertNoOverflow(page);
      await screenshot(
        page,
        info,
        `background-panel-composer-css-zoom-${zoom}`,
      );
      await writeEvidence(info, 'automatic-panel-keeps-typing', {
        zoom,
        method:
          'CSS zoom; synthetic composition event, not physical IME/device evidence',
        layout: await layoutFor(page, id),
      });
    } finally {
      await composer(page).evaluate((element) =>
        element.dispatchEvent(
          new CompositionEvent('compositionend', { bubbles: true, data: '' }),
        ),
      );
      await observer.close();
    }
  });

test('last explicitly opened Deck retains priority when another client adds a workspace', async ({
  page,
  browser,
}, info) => {
  const id = await newConversation(page);
  await addDeck(page, 'Priority Deck');
  const before = await layoutFor(page, id);
  const observer = await independentPeer(browser, page);
  const peer = observer.page;
  await peer.goto(`/app-v2/conversations/${id}`);
  const back = peer.getByRole('button', {
    name: 'Back to conversation',
    exact: true,
  });
  if (await back.isVisible()) await back.click();
  await expect(composer(peer)).toBeVisible();
  try {
    await peer
      .getByRole('button', { name: 'Add resource', exact: true })
      .click();
    const setup = peer.getByRole('dialog', {
      name: 'Add resource',
      exact: true,
    });
    await setup
      .getByRole('combobox', { name: 'Resource type', exact: true })
      .selectOption('workspace');
    await setup
      .getByRole('combobox', { name: 'Choose resource', exact: true })
      .selectOption('existing');
    await setup
      .getByRole('button', {
        name: `Phase 1 workspace Resource ID: ${(await fixtureResources(peer)).workspace_id}`,
        exact: true,
      })
      .click();
    await setup
      .getByRole('button', { name: 'Add to this conversation', exact: true })
      .click();
    await expect(
      setup.getByText('Resource ready', { exact: true }),
    ).toBeVisible();
    await expect
      .poll(async () => (await layoutFor(page, id)).panels.length)
      .toBe(2);
    const after = await layoutFor(page, id);
    expect(after.activePanelId).toBe(before.activePanelId);
    expect(after.presentation.lastExplicitKey).toBe(
      before.presentation.lastExplicitKey,
    );
    await expect(
      page.getByRole('region', { name: 'Design preview', exact: true }),
    ).toBeVisible();
    expect(
      (await conversationState(page, id)).conversation.resource_bindings
        .map((value) => value.kind)
        .sort(),
    ).toEqual(['artifact', 'workspace']);
    await writeEvidence(info, 'background-workspace-preserves-explicit-deck', {
      before,
      after,
    });
    await screenshot(page, info, 'both-resources-explicit-priority');
  } finally {
    await observer.close();
  }
});

test('Thinking persists its exact-model choice and changes the admitted fake request', async ({
  page,
}, info) => {
  const id = await newConversation(page);
  await page.getByRole('button', { name: 'Model', exact: true }).click();
  await screenshot(page, info, 'compact-model-menu');
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: 'Thinking', exact: true }).click();
  const thinking = page.getByRole('dialog', { name: 'Thinking', exact: true });
  await expect(
    thinking.getByRole('button', { name: 'Provider default', exact: true }),
  ).toBeVisible();
  await expect(
    thinking.getByRole('button', { name: 'Low', exact: true }),
  ).toBeVisible();
  await expect(
    thinking.getByRole('button', { name: 'High', exact: true }),
  ).toBeVisible();
  await screenshot(page, info, 'compact-thinking-menu');
  await thinking.getByRole('button', { name: 'High', exact: true }).click();
  await expect(
    page.getByRole('button', { name: 'Thinking', exact: true }),
  ).toContainText('High');
  await page.reload();
  await expect(
    page.getByRole('button', { name: 'Thinking', exact: true }),
  ).toContainText('High');
  await composer(page).fill('Thinking request uses admitted configuration');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await expect
    .poll(
      async () =>
        (await fixtureState(page)).calls.filter(
          (value) => value.conversation_id === id,
        ).length,
    )
    .toBe(1);
  const call = (await fixtureState(page)).calls.find(
    (value) => value.conversation_id === id,
  )!;
  try {
    const captured = call as typeof call & {
      reasoning_selection: { kind: string; effort?: string };
      effective_reasoning_kwargs: Record<string, unknown>;
    };
    expect(captured.reasoning_selection).toEqual({
      kind: 'effort',
      effort: 'high',
    });
    expect(Object.values(captured.effective_reasoning_kwargs)).toContain(
      'high',
    );
    await writeEvidence(info, 'thinking-admitted-request', {
      selection: captured.reasoning_selection,
      effective: captured.effective_reasoning_kwargs,
    });
    await screenshot(page, info, 'compact-controls-active-request');
  } finally {
    await releaseProducer(page, call);
  }
});
