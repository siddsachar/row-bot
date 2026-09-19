import {
  accessibility,
  assertNoOverflow,
  expect,
  screenshot,
  test,
  writeEvidence,
} from './evidence';
import {
  assertWorkspaceIdentity,
  blockFixtureServiceWorkers,
  composer,
  conversationState,
  fixtureState,
  markWorkspaceIdentity,
  newConversation,
  openConversation,
} from './unified-helpers';
import { captureBrowserDownload } from './download-helpers';
import type { Locator, Page, Request } from '@playwright/test';

function fixtureHeaders() {
  const token = process.env.ROW_BOT_BROWSER_CONTROL_TOKEN;
  const base = process.env.ROW_BOT_BROWSER_BASE_URL;
  if (!token || !base) throw new Error('Use the isolated Phase 4 runner');
  return { 'X-Fixture-Token': token, Origin: new URL(base).origin };
}

async function resourceState(page: Page, resource: string) {
  const response = await page.request.get(
    `/__p4_fixture/resources/${resource}/state`,
    {
      headers: fixtureHeaders(),
    },
  );
  expect(response.ok()).toBe(true);
  return response.json() as Promise<{
    resource_id: string;
    registered: boolean;
    exists: boolean;
    origin_id: string | null;
    directory_identity: string | null;
    children: string[];
    has_more: boolean;
    git_present: boolean;
  }>;
}

async function returnToChat(page: Page) {
  const back = page.getByRole('button', {
    name: 'Back to conversation',
    exact: true,
  });
  if (await back.isVisible()) await back.click();
  await expect(composer(page)).toBeVisible();
}

async function selectSaved(dialog: Locator, name: string, resource: string) {
  await dialog
    .getByRole('combobox', { name: 'Choose resource', exact: true })
    .selectOption('existing');
  const choice = dialog.getByRole('button', {
    name: `${name} Resource ID: ${resource}`,
    exact: true,
  });
  const more = dialog.getByRole('button', {
    name: 'More saved resources',
    exact: true,
  });
  await expect(dialog.getByLabel('Loading saved resources')).toHaveCount(0);
  while (!(await choice.count()) && (await more.isVisible())) {
    const count = await dialog.locator('[aria-pressed]').count();
    await more.click();
    await expect
      .poll(() => dialog.locator('[aria-pressed]').count())
      .toBeGreaterThan(count);
  }
  await expect(choice).toBeVisible();
  await choice.click();
}

async function restartSetup(dialog: Locator) {
  await expect(dialog).toBeVisible();
  const restart = dialog.getByRole('button', {
    name: 'Start another resource',
    exact: true,
  });
  await expect
    .poll(
      async () =>
        (await restart.isVisible()) ||
        (await dialog
          .getByRole('combobox', { name: 'Resource type', exact: true })
          .isVisible()),
    )
    .toBe(true);
  if (await restart.isVisible()) await restart.click();
}

function trackResourceViewRequests(page: Page) {
  const pending = new Set<Request>();
  const isFiniteResourceViewRequest = (request: Request) => {
    const path = new URL(request.url()).pathname;
    return (
      /^\/api\/v1\/conversations\/[^/]+\/subscriptions$/.test(path) ||
      /^\/api\/v1\/conversations\/[^/]+\/artifacts\/[^/]+\/(?:preview|lifecycle)$/.test(
        path,
      ) ||
      /^\/api\/v1\/conversations\/[^/]+\/buddy\/packs(?:\/[^/]+\/media\/[^/]+)?$/.test(
        path,
      )
    );
  };
  const started = (request: Request) => {
    if (isFiniteResourceViewRequest(request)) pending.add(request);
  };
  const settled = (request: Request) => pending.delete(request);
  page.on('request', started);
  page.on('requestfinished', settled);
  page.on('requestfailed', settled);

  return async (kind: 'artifact' | 'workspace') => {
    await expect(
      page
        .getByRole('status', { includeHidden: true })
        .filter({ hasText: /^Connected$/ }),
    ).toHaveText('Connected');
    if (kind === 'artifact') {
      const preview = page.getByRole('region', {
        name: 'Design preview',
        exact: true,
      });
      await expect(preview).toBeVisible();
      await expect(
        preview.getByLabel('Loading design preview', { exact: true }),
      ).toHaveCount(0);
      await expect(
        preview.getByLabel('Loading design lifecycle', { exact: true }),
      ).toHaveCount(0);
      await expect(
        preview.getByRole('toolbar', {
          name: 'Design lifecycle views',
          exact: true,
        }),
      ).toBeVisible();
      await expect(preview.locator('iframe')).toHaveCount(1);
    } else {
      const inspector = page.getByRole('region', {
        name: / inspector$/,
      });
      await expect(inspector).toBeVisible();
      await expect(
        inspector.getByLabel('Loading workspace inspector', { exact: true }),
      ).toHaveCount(0);
    }
    const companion = page.getByRole('complementary', {
      name: 'Buddy companion',
      exact: true,
    });
    if (await companion.isVisible())
      await expect(
        companion.locator('.buddy-avatar[src^="blob:"]'),
      ).toBeVisible();
    await expect
      .poll(() =>
        [...pending].map((request) => new URL(request.url()).pathname).sort(),
      )
      .toEqual([]);
  };
}

test.use({
  serviceWorkers: 'allow',
  // Match lifecycle coverage: WebKit navigation uses native cancellation with
  // the real local-only CSP verified by the evidence fixture before page load.
  nativeNetwork: async ({ browserName }, provide) =>
    provide(browserName === 'webkit'),
});
test.beforeEach(async ({ context, page }) => {
  await blockFixtureServiceWorkers(context);
  await page.addInitScript(() => {
    if (window !== window.top) return;
    localStorage.setItem(
      'row-bot.appearance.v1',
      JSON.stringify({ version: 1, appearance: 'system', accent: 'blue' }),
    );
  });
});

test('Phase 4 reviewed sharing submits once to an isolated fake channel and preserves chat', async ({
  page,
}, info) => {
  const seeded = await page.request.post('/__p4_fixture/sharing', {
    headers: fixtureHeaders(),
  });
  expect(seeded.ok()).toBe(true);
  const beforeCount = (await seeded.json()).count as number;
  const conversation = await newConversation(page);
  await composer(page).fill('Retained sharing draft');
  await markWorkspaceIdentity(page);
  await page.getByRole('button', { name: 'Add resource', exact: true }).click();
  const setup = page.getByRole('dialog', { name: 'Add resource', exact: true });
  await setup
    .getByRole('combobox', { name: 'Design type', exact: true })
    .selectOption('deck');
  await setup.getByRole('button', { name: 'Create Deck', exact: true }).click();
  await expect(
    setup.getByText('Resource ready', { exact: true }),
  ).toBeVisible();
  await page.keyboard.press('Escape');
  const preview = page.getByRole('region', {
    name: 'Design preview',
    exact: true,
  });
  await preview.getByRole('button', { name: 'Share', exact: true }).click();
  const sharing = page.getByRole('region', {
    name: 'Design sharing',
    exact: true,
  });
  await sharing
    .getByRole('combobox', { name: 'Share action', exact: true })
    .selectOption('channel');
  await sharing
    .getByRole('combobox', { name: 'Channel', exact: true })
    .selectOption('p4_fake_share');
  await sharing
    .getByRole('combobox', { name: 'Delivery', exact: true })
    .selectOption('html');
  await sharing
    .getByRole('button', { name: 'Review sharing', exact: true })
    .click();
  await expect(
    sharing.getByText('Recipient: synthetic-recipient', { exact: true }),
  ).toBeVisible();
  expect(
    (
      await (
        await page.request.get('/__p4_fixture/sharing', {
          headers: fixtureHeaders(),
        })
      ).json()
    ).count,
  ).toBe(beforeCount);
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    await assertNoOverflow(page);
    await screenshot(page, info, `sharing-review-${appearance}`);
    await accessibility(page, info, `sharing-review-${appearance}`, {
      opaquePreview: true,
    });
  }
  await sharing
    .getByRole('button', { name: 'Send to channel', exact: true })
    .click();
  await expect(
    sharing.getByText('Submitted 1 of 1 items to the destination adapter.', {
      exact: true,
    }),
  ).toBeVisible();
  const delivered = await (
    await page.request.get('/__p4_fixture/sharing', {
      headers: fixtureHeaders(),
    })
  ).json();
  expect(delivered.count).toBe(beforeCount + 1);
  expect(delivered.last).toMatchObject({
    target: 'synthetic-recipient',
    html: true,
  });
  await assertWorkspaceIdentity(page);
  await expect(composer(page)).toHaveValue('Retained sharing draft');
  expect(
    (await fixtureState(page)).calls.filter(
      (call) => call.conversation_id === conversation,
    ),
  ).toEqual([]);
});

for (const [mode, label] of [
  ['deck', 'Deck'],
  ['document', 'Document'],
  ['landing', 'Landing page'],
  ['app_mockup', 'App mockup'],
  ['storyboard', 'Storyboard'],
] as const) {
  test(`Phase 4 ${label} creation retains one chat and an isolated working preview`, async ({
    page,
  }, info) => {
    const conversation = await newConversation(page);
    const before = await conversationState(page, conversation);
    const savedDraft = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname ===
          `/api/v1/conversations/${conversation}/draft` &&
        response.request().method() === 'PUT' &&
        response.ok(),
    );
    const retainedDraft = `Retained ${label} conversation draft`;
    await composer(page).click();
    await composer(page).pressSequentially(retainedDraft);
    await savedDraft;
    await expect(composer(page)).toHaveValue(retainedDraft);
    await markWorkspaceIdentity(page);
    await page
      .getByRole('button', { name: 'Add resource', exact: true })
      .click();
    const setup = page.getByRole('dialog', {
      name: 'Add resource',
      exact: true,
    });
    await expect(setup).toHaveAccessibleName('Add resource');
    await writeEvidence(
      info,
      'setup-dialog-label',
      await setup.evaluate((element) => ({
        labelledBy: element.getAttribute('aria-labelledby'),
        title: Array.from(element.querySelectorAll('h2')).map((heading) => ({
          id: heading.id,
          text: heading.textContent,
        })),
      })),
    );
    await setup
      .getByRole('combobox', { name: 'Design type', exact: true })
      .selectOption(mode);
    await expect(
      setup.getByRole('button', { name: `Create ${label}`, exact: true }),
    ).toBeEnabled();
    await setup
      .getByRole('textbox', { name: 'Name (optional)', exact: true })
      .fill(`phase4-${mode}-${conversation}`);
    await page.keyboard.press('Escape');
    await expect(setup).toHaveCount(0);
    await expect(
      page.getByRole('button', { name: 'Add resource', exact: true }),
    ).toBeFocused();
    expect(
      (await conversationState(page, conversation)).conversation
        .resource_bindings,
    ).toEqual(before.conversation.resource_bindings);
    await assertWorkspaceIdentity(page);
    await page
      .getByRole('button', { name: 'Add resource', exact: true })
      .press('Enter');
    await expect(
      setup.getByRole('textbox', { name: 'Name (optional)', exact: true }),
    ).toHaveValue(`phase4-${mode}-${conversation}`);
    await screenshot(page, info, `${mode}-setup`);
    await setup
      .getByRole('button', { name: `Create ${label}`, exact: true })
      .click();
    await expect(
      setup.getByText('Resource ready', { exact: true }),
    ).toBeVisible();
    const state = await conversationState(page, conversation);
    expect(state.conversation.resource_bindings).toHaveLength(1);
    await page.keyboard.press('Escape');
    await expect(page).toHaveURL(new RegExp(`/conversations/${conversation}$`));
    const region = page.getByRole('region', {
      name: 'Design preview',
      exact: true,
    });
    await expect(region).toBeVisible();
    const frame = region.locator('iframe');
    await expect(frame).toHaveCount(1);
    const interactive = ['landing', 'app_mockup', 'storyboard'].includes(mode);
    await expect(frame).toHaveAttribute(
      'sandbox',
      interactive ? 'allow-scripts' : '',
    );
    await expect(frame.contentFrame().locator('body')).not.toBeEmpty();
    await screenshot(page, info, `${mode}-actual-created-preview`);
    if (interactive) {
      await expect(frame.contentFrame().locator('html')).toHaveAttribute(
        'data-row-bot-active-route',
        /.+/,
      );
      await expect(
        frame.contentFrame().locator('[data-row-bot-route-active]'),
      ).toHaveCount(1);
      const seeded = await page.request.post(
        `/__p4_fixture/artifacts/${state.conversation.resource_bindings[0].resource_id}/interaction-pages`,
        {
          headers: fixtureHeaders(),
        },
      );
      expect(seeded.ok()).toBe(true);
      await region
        .getByRole('button', { name: 'Refresh preview', exact: true })
        .click();
      const artwork = frame.contentFrame();
      await region
        .getByRole('combobox', { name: 'Preview zoom', exact: true })
        .selectOption('actual');
      await expect(
        artwork.getByRole('heading', { name: 'Fixture first route' }),
      ).toBeVisible();
      await frame.scrollIntoViewIfNeeded();
      const details = artwork.getByRole('button', {
        name: 'Toggle details',
        exact: true,
      });
      await details.scrollIntoViewIfNeeded();
      await details.click();
      await expect(details).toHaveAttribute('aria-pressed', 'true');
      const secondRoute = artwork.getByRole('button', {
        name: 'Go to second route',
        exact: true,
      });
      await secondRoute.scrollIntoViewIfNeeded();
      await secondRoute.click();
      await expect(artwork.locator('html')).toHaveAttribute(
        'data-row-bot-active-route',
        'second',
      );
      const firstRoute = artwork.getByRole('button', {
        name: 'Return to first route',
        exact: true,
      });
      await firstRoute.scrollIntoViewIfNeeded();
      await firstRoute.click();
      await expect(artwork.locator('html')).toHaveAttribute(
        'data-row-bot-active-route',
        'first',
      );
      await writeEvidence(
        info,
        `${mode}-synthetic-interaction-fixture`,
        await seeded.json(),
      );
      await region
        .getByRole('combobox', { name: 'Preview zoom', exact: true })
        .selectOption('fit');
    }
    const isolation = await frame
      .contentFrame()
      .locator('html')
      .evaluate(() => {
        let parentDenied = false;
        try {
          void window.parent.document.documentElement;
        } catch (error) {
          parentDenied =
            error instanceof DOMException && error.name === 'SecurityError';
        }
        return {
          parentDenied,
          authoredScriptExecuted:
            (window as unknown as { __QA_AUTHORED_SCRIPT_EXECUTED__?: boolean })
              .__QA_AUTHORED_SCRIPT_EXECUTED__ === true,
        };
      });
    expect(isolation).toEqual({
      parentDenied: true,
      authoredScriptExecuted: false,
    });
    const pages = region.getByRole('combobox', {
      name: mode === 'deck' ? 'Slide' : 'Page',
      exact: true,
    });
    const options = await pages.locator('option').all();
    if (options.length > 1) {
      await pages.selectOption({ index: 1 });
      await expect(region.getByText(/(?:Slide|Page) 2 of/)).toBeVisible();
    }
    await region
      .getByRole('button', { name: 'Design properties', exact: true })
      .click();
    const editing = region.getByRole('region', {
      name: 'Design editing',
      exact: true,
    });
    await expect(editing).toBeVisible();
    await expect(
      editing.getByRole('tab', { name: 'Properties', exact: true }),
    ).toHaveAttribute('aria-selected', 'true');
    const renamed = `Edited ${label} ${conversation}`;
    await editing
      .getByRole('textbox', { name: 'Design name', exact: true })
      .fill(renamed);
    await editing
      .getByRole('button', { name: 'Save design name', exact: true })
      .click();
    await expect(
      editing.getByRole('textbox', { name: 'Design name', exact: true }),
    ).toHaveValue(renamed);
    await expect(
      editing.getByText('Changes saved.', { exact: true }),
    ).toBeVisible();
    await editing
      .getByRole('button', { name: 'Refresh properties', exact: true })
      .click();
    await expect(editing.getByLabel('Loading design properties')).toHaveCount(
      0,
    );
    await expect(
      editing.getByRole('textbox', { name: 'Design name', exact: true }),
    ).toHaveValue(renamed);
    await screenshot(page, info, `${mode}-saved-properties`);
    await accessibility(page, info, `${mode}-saved-properties`, {
      opaquePreview: true,
    });
    await region
      .getByRole('button', { name: 'Design properties', exact: true })
      .click();
    await region.getByRole('button', { name: 'Export', exact: true }).click();
    const exporting = region.getByRole('region', {
      name: 'Design export',
      exact: true,
    });
    await exporting
      .getByRole('combobox', { name: 'Export format', exact: true })
      .selectOption('html');
    await exporting
      .getByRole('button', { name: 'Export design', exact: true })
      .click();
    const downloadButton = exporting.getByRole('button', {
      name: 'Download HTML',
      exact: true,
    });
    await expect(downloadButton).toBeVisible();
    const download = await captureBrowserDownload(page, () =>
      downloadButton.click(),
    );
    expect(download.name).toBe(`${renamed}.html`);
    expect(download.mimeType).toBe('text/html');
    const html = download.bytes.toString('utf8');
    expect(html.toLowerCase()).toContain('<html');
    expect(html).toContain(renamed);
    await screenshot(page, info, `${mode}-html-export`);
    await accessibility(page, info, `${mode}-html-export`, {
      opaquePreview: true,
    });
    await region
      .getByRole('button', {
        name: 'Export',
        exact: true,
        pressed: true,
      })
      .click();
    await assertWorkspaceIdentity(page);
    // Compact panels may cover the composer; the same node and draft remain owned by the chat.
    await expect(composer(page)).toHaveValue(
      `Retained ${label} conversation draft`,
    );
    const calls = (await fixtureState(page)).calls.filter(
      (call) => call.conversation_id === conversation,
    );
    expect(calls).toEqual([]);
    expect(
      (await conversationState(page, conversation)).workspace.controls,
    ).toEqual(before.workspace.controls);
    await region
      .getByRole('button', { name: 'Design controls', exact: true })
      .click();
    const designControls = region.getByRole('region', {
      name: 'Design controls',
      exact: true,
    });
    await designControls.getByText('Brand and fonts', { exact: true }).click();
    await designControls
      .getByRole('textbox', { name: 'primary color', exact: true })
      .fill('#654321');
    await designControls
      .getByRole('button', { name: 'Apply brand', exact: true })
      .click();
    await expect(
      designControls.getByRole('textbox', {
        name: 'primary color',
        exact: true,
      }),
    ).toHaveValue('#654321');
    await expect(
      region.getByText('Changes saved.', { exact: true }),
    ).toBeVisible();
    await screenshot(page, info, `${mode}-advanced-brand`);
    await region
      .getByRole('button', { name: 'Design controls', exact: true })
      .click();
    await region.getByRole('button', { name: 'Present', exact: true }).click();
    await region
      .getByRole('button', { name: 'Start presentation', exact: true })
      .click();
    const slide = region.locator('iframe[title^="Presentation:"]');
    await expect(slide).toHaveCount(1);
    await expect(slide).toHaveAttribute('sandbox', '');
    await expect(slide.contentFrame().locator('body')).toBeVisible();
    const audienceOpened = page.waitForEvent('popup');
    await region
      .getByRole('button', { name: 'Open audience window', exact: true })
      .click();
    const audience = await audienceOpened;
    const audienceSlide = audience.locator('iframe');
    await expect(audienceSlide).toHaveAttribute('sandbox', '');
    await expect(audienceSlide).toHaveAttribute(
      'title',
      (await slide.getAttribute('title')) ?? '',
    );
    await expect(audience.getByRole('button')).toHaveCount(0);
    await expect(
      audience.getByRole('complementary', { name: 'Speaker notes' }),
    ).toHaveCount(0);
    await expect(audienceSlide.contentFrame().locator('body')).toBeVisible();
    await expect
      .poll(async () => (await audienceSlide.boundingBox())?.width ?? 0)
      .toBeGreaterThan(100);
    await expect
      .poll(async () => (await slide.boundingBox())?.width ?? 0)
      .toBeGreaterThan(100);
    await screenshot(audience, info, `${mode}-audience-slide`);
    for (const appearance of ['light', 'dark'] as const) {
      await page.emulateMedia({ colorScheme: appearance });
      await expect(page.locator('html')).toHaveAttribute(
        'data-theme',
        appearance,
      );
      await assertNoOverflow(page);
      await screenshot(page, info, `${mode}-${appearance}-preview`);
      await accessibility(page, info, `${mode}-${appearance}-preview`, {
        opaquePreview: true,
      });
    }
    await region
      .getByRole('button', { name: 'End presentation', exact: true })
      .click();
    await expect.poll(() => audience.isClosed()).toBe(true);
    await returnToChat(page);
    await assertWorkspaceIdentity(page);
    await expect(composer(page)).toHaveValue(
      `Retained ${label} conversation draft`,
    );
    await writeEvidence(info, `${mode}-resource`, {
      conversation,
      bindings: state.conversation.resource_bindings,
      mode,
      isolation,
    });
  });
}

test('Phase 4 sandbox import and Undo retain reviews and restore exact original files', async ({
  page,
}, info) => {
  const conversation = await newConversation(page);
  await composer(page).fill('Retained sandbox import draft');
  await page.getByRole('button', { name: 'Add resource', exact: true }).click();
  const setup = page.getByRole('dialog', { name: 'Add resource', exact: true });
  await setup
    .getByRole('combobox', { name: 'Resource type', exact: true })
    .selectOption('workspace');
  await setup
    .getByRole('combobox', { name: 'Folder setup', exact: true })
    .selectOption('empty_folder');
  await setup
    .getByRole('textbox', { name: 'New folder name', exact: true })
    .fill(`phase4-import-${conversation}`);
  await setup
    .getByRole('button', { name: 'Choose parent folder', exact: true })
    .click();
  await setup
    .getByRole('button', { name: 'Create empty workspace', exact: true })
    .click();
  await expect(
    setup.getByText('Resource ready', { exact: true }),
  ).toBeVisible();
  await page.keyboard.press('Escape');
  const bindings = (await conversationState(page, conversation)).conversation
    .resource_bindings;
  const resource = bindings[0].resource_id;
  const seed = await page.request.post(
    `/__p4_fixture/resources/${resource}/import-probe`,
    { headers: fixtureHeaders() },
  );
  expect(seed.ok()).toBe(true);
  await page
    .getByRole('button', { name: 'Sandbox changes', exact: true })
    .click();
  const imports = page.getByRole('region', {
    name: 'Sandbox imports',
    exact: true,
  });
  await imports.getByRole('button', { name: /^Pending · 2 files/ }).click();
  await expect(
    imports.getByText('Synthetic imported', { exact: false }),
  ).toBeVisible();
  await imports
    .getByRole('button', { name: 'Review import', exact: true })
    .click();
  const folders = imports.getByRole('list', {
    name: 'Reviewed new folders',
    exact: true,
  });
  await expect(folders.getByRole('listitem')).toHaveText(['new', 'new/nested']);
  await imports
    .getByRole('button', { name: 'Cancel review', exact: true })
    .click();
  const before = await page.request.get(
    `/__p4_fixture/resources/${resource}/import-probe`,
    { headers: fixtureHeaders() },
  );
  expect(await before.json()).toMatchObject({
    content: 'Synthetic original\n',
    empty_created: false,
    imported: false,
  });
  await imports
    .getByRole('button', { name: 'Review import', exact: true })
    .click();
  await page
    .getByRole('button', { name: 'Hide sandbox changes', exact: true })
    .click();
  await page
    .getByRole('button', { name: 'Sandbox changes', exact: true })
    .click();
  await expect(folders.getByRole('listitem')).toHaveText(['new', 'new/nested']);
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    await assertNoOverflow(page);
    await screenshot(page, info, `sandbox-import-review-${appearance}`);
    await accessibility(page, info, `sandbox-import-review-${appearance}`);
  }
  await imports
    .getByRole('button', { name: 'Confirm import', exact: true })
    .click();
  await expect(
    imports.getByText(
      'Sandbox changes imported. Original files and change history are retained.',
      { exact: true },
    ),
  ).toBeVisible();
  const after = await page.request.get(
    `/__p4_fixture/resources/${resource}/import-probe`,
    { headers: fixtureHeaders() },
  );
  expect(await after.json()).toMatchObject({
    content: 'Synthetic imported\n',
    empty_created: true,
    imported: true,
    change_sets: 1,
    retained_original: true,
  });
  await page
    .getByRole('button', { name: 'Load agent changes', exact: true })
    .click();
  await page
    .getByRole('button', {
      name: 'Review Undo Import sandbox changes',
      exact: true,
    })
    .click();
  const undo = page.getByRole('region', {
    name: 'Undo workspace changes',
    exact: true,
  });
  await undo.getByRole('button', { name: 'Review Undo', exact: true }).click();
  await expect(
    undo.getByText('These created folders will remain:', { exact: true }),
  ).toBeVisible();
  await undo
    .getByRole('button', { name: 'Cancel review', exact: true })
    .click();
  expect(
    await (
      await page.request.get(
        `/__p4_fixture/resources/${resource}/import-probe`,
        { headers: fixtureHeaders() },
      )
    ).json(),
  ).toMatchObject({
    content: 'Synthetic imported\n',
    empty_created: true,
    reverted: false,
  });
  await undo.getByRole('button', { name: 'Review Undo', exact: true }).click();
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    await assertNoOverflow(page);
    await screenshot(page, info, `workspace-undo-review-${appearance}`);
    await accessibility(page, info, `workspace-undo-review-${appearance}`);
  }
  const undoneResponse = page.waitForResponse(
    (response) =>
      response.url().endsWith('/undo/commands') &&
      response.request().method() === 'POST',
  );
  await undo
    .getByRole('button', { name: 'Undo these changes', exact: true })
    .click();
  const undone = await undoneResponse;
  expect(undone.ok()).toBe(true);
  expect(await undone.json()).toMatchObject({
    status: 'undone',
    reverted: true,
    ledger_saved: true,
  });
  await expect(undo.getByRole('status')).toHaveText(
    'Original files restored. Created directories remain.',
  );
  expect(
    await (
      await page.request.get(
        `/__p4_fixture/resources/${resource}/import-probe`,
        { headers: fixtureHeaders() },
      )
    ).json(),
  ).toMatchObject({
    content: 'Synthetic original\n',
    empty_created: false,
    directories_retained: true,
    reverted: true,
    change_sets: 1,
    retained_original: true,
  });
  await returnToChat(page);
  await expect(composer(page)).toHaveValue('Retained sandbox import draft');
  expect(
    (await fixtureState(page)).calls.filter(
      (call) => call.conversation_id === conversation,
    ),
  ).toEqual([]);
});

test('Phase 4 empty workspace requires a named parent-scoped action and retains the chat', async ({
  page,
}, info) => {
  const conversation = await newConversation(page);
  const before = await conversationState(page, conversation);
  await composer(page).fill('Retained empty-workspace draft');
  await markWorkspaceIdentity(page);
  await page.getByRole('button', { name: 'Add resource', exact: true }).click();
  const setup = page.getByRole('dialog', { name: 'Add resource', exact: true });
  await expect(setup).toHaveAccessibleName('Add resource');
  await setup
    .getByRole('combobox', { name: 'Resource type', exact: true })
    .selectOption('workspace');
  await setup
    .getByRole('combobox', { name: 'Folder setup', exact: true })
    .selectOption('empty_folder');
  const create = setup.getByRole('button', {
    name: 'Create empty workspace',
    exact: true,
  });
  await expect(create).toBeDisabled();
  await setup
    .getByRole('textbox', { name: 'New folder name', exact: true })
    .fill(`phase4-${conversation}`);
  await expect(create).toBeDisabled();
  await setup
    .getByRole('button', { name: 'Choose parent folder', exact: true })
    .click();
  await expect(create).toBeEnabled();
  await screenshot(page, info, 'empty-workspace-setup');
  await create.click();
  await expect(
    setup.getByText('Resource ready', { exact: true }),
  ).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page).toHaveURL(new RegExp(`/conversations/${conversation}$`));
  await assertWorkspaceIdentity(page);
  const bindings = (await conversationState(page, conversation)).conversation
    .resource_bindings;
  expect(bindings).toHaveLength(1);
  expect(bindings[0].kind).toBe('workspace');
  const disk = await resourceState(page, bindings[0].resource_id);
  expect(disk).toMatchObject({
    registered: true,
    exists: true,
    origin_id: conversation,
    children: [],
    has_more: false,
    git_present: false,
  });
  expect(
    (await conversationState(page, conversation)).workspace.controls,
  ).toEqual(before.workspace.controls);
  expect(
    (await fixtureState(page)).calls.filter(
      (call) => call.conversation_id === conversation,
    ),
  ).toEqual([]);
  await screenshot(page, info, 'empty-workspace-inspector');
  await assertNoOverflow(page);
  await accessibility(page, info, 'empty-workspace-inspector');
  const seed = await page.request.post(
    `/__p4_fixture/resources/${bindings[0].resource_id}/edit-file`,
    { headers: fixtureHeaders() },
  );
  expect(seed.ok()).toBe(true);
  await page
    .getByRole('button', { name: 'Refresh inspector', exact: true })
    .click();
  await page
    .getByRole('button', { name: 'Workspace root', exact: true })
    .click();
  await page
    .getByRole('button', { name: 'Preview file sample.txt', exact: true })
    .click();
  await page.getByRole('button', { name: 'Edit file', exact: true }).click();
  const editor = page.getByRole('region', {
    name: 'Workspace file editor',
    exact: true,
  });
  const contents = editor.getByRole('textbox', {
    name: 'File contents',
    exact: true,
  });
  await expect(contents).toHaveValue('Synthetic original\n');
  await contents.fill('Synthetic edited\n');
  await editor
    .getByRole('button', { name: 'Close editor and keep draft', exact: true })
    .click();
  await page
    .getByRole('button', { name: 'Resume edit: sample.txt', exact: true })
    .click();
  await expect(contents).toHaveValue('Synthetic edited\n');
  await page
    .getByRole('button', { name: 'Close all panels', exact: true })
    .click();
  await expect(editor).not.toBeVisible();
  await page
    .getByRole('group', { name: 'Bound resources', exact: true })
    .getByRole('button', { name: `phase4-${conversation}`, exact: true })
    .click();
  await expect(contents).toHaveValue('Synthetic edited\n');
  await editor.getByRole('button', { name: 'Save file', exact: true }).click();
  await expect(
    editor.getByText(
      'File saved. Original bytes remain available in edit recovery.',
      { exact: true },
    ),
  ).toBeVisible();
  const stored = await page.request.get(
    `/__p4_fixture/resources/${bindings[0].resource_id}/edit-file`,
    { headers: fixtureHeaders() },
  );
  expect(await stored.json()).toMatchObject({
    content: 'Synthetic edited\n',
    retained_original: true,
  });
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    await assertNoOverflow(page);
    await screenshot(page, info, `workspace-editor-${appearance}`);
    await accessibility(page, info, `workspace-editor-${appearance}`);
  }
  const processProbe = await page.request.post(
    `/__p4_fixture/resources/${bindings[0].resource_id}/process-probe`,
    { headers: fixtureHeaders() },
  );
  expect(processProbe.ok()).toBe(true);
  await page.getByRole('button', { name: 'Processes', exact: true }).click();
  const processes = page.getByRole('region', {
    name: 'Workspace processes',
    exact: true,
  });
  await expect(
    processes.getByText('No owned processes reported.', { exact: true }),
  ).toBeVisible();
  await processes
    .getByRole('textbox', { name: 'Process command', exact: true })
    .fill((await processProbe.json()).command);
  const startProcess = processes.getByRole('button', {
    name: 'Start reviewed command',
    exact: true,
  });
  await expect(startProcess).toBeDisabled();
  await processes
    .getByRole('button', { name: 'Review command', exact: true })
    .click();
  await expect(startProcess).toBeEnabled();
  await expect(processes.getByRole('button', { name: /^Stop / })).toHaveCount(
    0,
  );
  await startProcess.click();
  await expect(
    processes.getByText('Workspace writer held until cleanup completes.', {
      exact: true,
    }),
  ).toBeVisible();
  await expect
    .poll(async () => {
      await processes
        .getByRole('button', { name: 'View output', exact: true })
        .click();
      return processes
        .getByLabel('Process output', { exact: true })
        .textContent();
    })
    .toContain('Synthetic process ready');
  await page
    .getByRole('button', { name: 'Close all panels', exact: true })
    .click();
  await page
    .getByRole('group', { name: 'Bound resources', exact: true })
    .getByRole('button', { name: `phase4-${conversation}`, exact: true })
    .click();
  await page.getByRole('button', { name: 'Processes', exact: true }).click();
  await expect(
    processes.getByRole('textbox', { name: 'Process command', exact: true }),
  ).toHaveValue('python -I -S process_probe.py');
  await processes.getByRole('button', { name: /^Stop / }).click();
  await expect(
    processes.getByRole('button', { name: /^Stop / }),
  ).toBeDisabled();
  await expect(
    processes.getByText('Workspace writer held until cleanup completes.', {
      exact: true,
    }),
  ).toHaveCount(0);
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    await assertNoOverflow(page);
    await screenshot(page, info, `workspace-process-${appearance}`);
    await accessibility(page, info, `workspace-process-${appearance}`);
  }
  await returnToChat(page);
  await expect(composer(page)).toHaveValue('Retained empty-workspace draft');
  await writeEvidence(info, 'empty-workspace-filesystem', disk);
});

for (const [kind, mode, label] of [
  ['artifact', 'deck', 'Deck'],
  ['artifact', 'document', 'Document'],
  ['artifact', 'landing', 'Landing page'],
  ['artifact', 'app_mockup', 'App mockup'],
  ['artifact', 'storyboard', 'Storyboard'],
  ['workspace', 'empty_folder', 'empty workspace'],
] as const) {
  test(`Phase 4 global ${label} Open reuses origin after additive in-chat reuse`, async ({
    page,
  }, info) => {
    const settleResourceView = trackResourceViewRequests(page);
    const original = await newConversation(page);
    const initial = await conversationState(page, original);
    const name = `phase4-${mode.replaceAll('_', '-')}-${original}`;
    await composer(page).fill(`Retained original ${mode} draft`);
    await page
      .getByRole('button', { name: 'New resource', exact: true })
      .click();
    let dialog = page.getByRole('dialog', {
      name: 'New or open resource',
      exact: true,
    });
    if (kind === 'workspace') {
      await dialog
        .getByRole('combobox', { name: 'Resource type', exact: true })
        .selectOption('workspace');
      await dialog
        .getByRole('combobox', { name: 'Folder setup', exact: true })
        .selectOption('empty_folder');
      await dialog
        .getByRole('textbox', { name: 'New folder name', exact: true })
        .fill(name);
      await dialog
        .getByRole('button', { name: 'Choose parent folder', exact: true })
        .click();
    } else {
      await dialog
        .getByRole('combobox', { name: 'Design type', exact: true })
        .selectOption(mode);
      await dialog
        .getByRole('textbox', { name: 'Name (optional)', exact: true })
        .fill(name);
    }
    await dialog
      .getByRole('button', { name: `Create ${label}`, exact: true })
      .click();
    await expect(
      dialog.getByText('Resource ready', { exact: true }),
    ).toBeVisible();
    await expect(page).not.toHaveURL(new RegExp(`/conversations/${original}$`));
    const origin = new URL(page.url()).pathname.split('/').at(-1)!;
    await page.keyboard.press('Escape');
    await settleResourceView(kind);
    const originState = await conversationState(page, origin);
    expect(originState.conversation.resource_bindings).toHaveLength(1);
    const resource = originState.conversation.resource_bindings[0].resource_id;
    for (let attempt = 0; attempt < 2; attempt++) {
      await openConversation(page, original);
      await expect(composer(page)).toHaveValue(
        `Retained original ${mode} draft`,
      );
      await page
        .getByRole('button', { name: 'New resource', exact: true })
        .click();
      dialog = page.getByRole('dialog', {
        name: 'New or open resource',
        exact: true,
      });
      await restartSetup(dialog);
      await dialog
        .getByRole('combobox', { name: 'Resource type', exact: true })
        .selectOption(kind);
      await selectSaved(dialog, name, resource);
      await dialog
        .getByRole('button', { name: 'Open resource', exact: true })
        .click();
      await expect(
        dialog.getByText('Resource ready', { exact: true }),
      ).toBeVisible();
      await expect(page).toHaveURL(new RegExp(`/conversations/${origin}$`));
      await page.keyboard.press('Escape');
      await settleResourceView(kind);
      expect(
        (await conversationState(page, origin)).conversation.resource_bindings,
      ).toEqual(originState.conversation.resource_bindings);
    }
    await openConversation(page, original);
    await markWorkspaceIdentity(page);
    await page
      .getByRole('button', { name: 'Add resource', exact: true })
      .click();
    const add = page.getByRole('dialog', { name: 'Add resource', exact: true });
    await add
      .getByRole('combobox', { name: 'Resource type', exact: true })
      .selectOption(kind);
    await selectSaved(add, name, resource);
    await add
      .getByRole('button', { name: 'Add to this conversation', exact: true })
      .click();
    await expect(
      add.getByText('Resource ready', { exact: true }),
    ).toBeVisible();
    await page.keyboard.press('Escape');
    await settleResourceView(kind);
    await expect(page).toHaveURL(new RegExp(`/conversations/${original}$`));
    await assertWorkspaceIdentity(page);
    const added = await conversationState(page, original);
    expect(added.conversation.resource_bindings).toHaveLength(1);
    expect(added.conversation.resource_bindings[0].resource_id).toBe(resource);
    expect(added.conversation.resource_bindings[0].binding_id).not.toBe(
      originState.conversation.resource_bindings[0].binding_id,
    );
    expect(added.workspace.controls).toEqual(initial.workspace.controls);
    await returnToChat(page);
    await expect(composer(page)).toHaveValue(`Retained original ${mode} draft`);
    await page
      .getByRole('button', { name: 'New resource', exact: true })
      .click();
    dialog = page.getByRole('dialog', {
      name: 'New or open resource',
      exact: true,
    });
    await restartSetup(dialog);
    await dialog
      .getByRole('combobox', { name: 'Resource type', exact: true })
      .selectOption(kind);
    await selectSaved(dialog, name, resource);
    await dialog
      .getByRole('button', { name: 'Open resource', exact: true })
      .click();
    await expect(page).toHaveURL(new RegExp(`/conversations/${origin}$`));
    await page.keyboard.press('Escape');
    await settleResourceView(kind);
    expect(
      (await conversationState(page, origin)).conversation.resource_bindings,
    ).toEqual(originState.conversation.resource_bindings);
    expect(
      (await fixtureState(page)).calls.filter((call) =>
        [original, origin].includes(call.conversation_id),
      ),
    ).toEqual([]);
    await assertNoOverflow(page);
    await screenshot(page, info, `${mode}-canonical-origin`);
    await writeEvidence(info, `${mode}-global-and-additive-reuse`, {
      original,
      origin,
      resource,
      repeatedOpen: 3,
      additiveBinding: added.conversation.resource_bindings[0],
      originBindings: originState.conversation.resource_bindings,
    });
  });
}

test('Phase 4 one-shot empty workspace save failure requires renewed parent and resumes exact identity', async ({
  page,
}, info) => {
  const conversation = await newConversation(page);
  const name = `phase4-recover-${conversation}`;
  const armed = await page.request.post(
    `/__p4_fixture/workspace-save-failure/${name}`,
    { headers: fixtureHeaders() },
  );
  expect(armed.ok()).toBe(true);
  const resource = (await armed.json()).resource_id as string;
  const before = await conversationState(page, conversation);
  await composer(page).fill('Retained recovery draft');
  await markWorkspaceIdentity(page);
  await page.getByRole('button', { name: 'Add resource', exact: true }).click();
  const dialog = page.getByRole('dialog', {
    name: 'Add resource',
    exact: true,
  });
  await dialog
    .getByRole('combobox', { name: 'Resource type', exact: true })
    .selectOption('workspace');
  await dialog
    .getByRole('combobox', { name: 'Folder setup', exact: true })
    .selectOption('empty_folder');
  await dialog
    .getByRole('textbox', { name: 'New folder name', exact: true })
    .fill(name);
  await dialog
    .getByRole('button', { name: 'Choose parent folder', exact: true })
    .click();
  await dialog
    .getByRole('button', { name: 'Create empty workspace', exact: true })
    .click();
  await expect(
    dialog.getByText('Setup partially completed', { exact: true }),
  ).toBeVisible();
  const partial = await resourceState(page, resource);
  expect(partial).toMatchObject({
    exists: true,
    registered: false,
    children: [],
    has_more: false,
    git_present: false,
  });
  expect(
    (await conversationState(page, conversation)).conversation
      .resource_bindings,
  ).toEqual(before.conversation.resource_bindings);
  await screenshot(page, info, 'empty-workspace-partial');
  await page.keyboard.press('Escape');
  await assertWorkspaceIdentity(page);
  await page.getByRole('button', { name: 'Add resource', exact: true }).click();
  await expect(
    dialog.getByText('Setup partially completed', { exact: true }),
  ).toBeVisible();
  await expect(
    dialog.getByRole('button', { name: 'Continue setup', exact: true }),
  ).toBeDisabled();
  await dialog
    .getByRole('button', { name: 'Choose parent folder again', exact: true })
    .click();
  await dialog
    .getByRole('button', { name: 'Continue setup', exact: true })
    .click();
  await expect(
    dialog.getByText('Resource ready', { exact: true }),
  ).toBeVisible();
  const completed = await resourceState(page, resource);
  expect(completed).toMatchObject({
    registered: true,
    exists: true,
    origin_id: conversation,
    children: [],
    has_more: false,
    git_present: false,
  });
  expect(completed.directory_identity).toBe(partial.directory_identity);
  await page.keyboard.press('Escape');
  await assertWorkspaceIdentity(page);
  await returnToChat(page);
  await expect(composer(page)).toHaveValue('Retained recovery draft');
  const result = await conversationState(page, conversation);
  expect(result.conversation.resource_bindings).toHaveLength(1);
  expect(result.conversation.resource_bindings[0].resource_id).toBe(resource);
  expect(result.workspace.controls).toEqual(before.workspace.controls);
  expect(
    (await fixtureState(page)).calls.filter(
      (call) => call.conversation_id === conversation,
    ),
  ).toEqual([]);
  await writeEvidence(info, 'same-directory-recovery', {
    partial,
    completed,
    binding: result.conversation.resource_bindings[0],
  });
});

test('Phase 4 double-submit and hidden response recover the original durable receipt', async ({
  page,
}, info) => {
  const conversation = await newConversation(page);
  await composer(page).fill('Retained uncertain-outcome draft');
  await markWorkspaceIdentity(page);
  let entered!: () => void, release!: () => void;
  const arrived = new Promise<void>((resolve) => {
    entered = resolve;
  });
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  const commands: string[] = [];
  const endpoint = `/api/v1/conversations/${conversation}/commands`;
  await page.route(`**${endpoint}`, async (route) => {
    const request = route.request().postDataJSON() as {
      type: string;
      command_id: string;
    };
    if (request.type !== 'resource.setup') {
      await route.continue();
      return;
    }
    commands.push(request.command_id);
    const response = await route.fetch();
    entered();
    await held;
    await route.fulfill({ response });
  });
  try {
    await page
      .getByRole('button', { name: 'Add resource', exact: true })
      .click();
    const dialog = page.getByRole('dialog', {
      name: 'Add resource',
      exact: true,
    });
    await dialog
      .getByRole('textbox', { name: 'Name (optional)', exact: true })
      .fill(`phase4-delayed-${conversation}`);
    await dialog
      .getByRole('button', { name: 'Create Deck', exact: true })
      .dblclick();
    await arrived;
    await expect(
      dialog.getByRole('button', { name: 'Check setup receipt', exact: true }),
    ).toBeDisabled();
    await expect(
      dialog.getByRole('button', { name: 'Create Deck', exact: true }),
    ).toHaveCount(0);
    await page.keyboard.press('Escape');
    await page
      .getByRole('button', { name: 'Add resource', exact: true })
      .click();
    await expect(
      dialog.getByText('Resource ready', { exact: true }),
    ).toBeVisible();
    const confirmed = await conversationState(page, conversation);
    expect(confirmed.conversation.resource_bindings).toHaveLength(1);
    expect(commands).toHaveLength(1);
    const received = page.waitForResponse(
      (response) => new URL(response.url()).pathname === endpoint,
    );
    release();
    await received;
    await page.keyboard.press('Escape');
    await assertWorkspaceIdentity(page);
    await returnToChat(page);
    await expect(composer(page)).toHaveValue(
      'Retained uncertain-outcome draft',
    );
    expect(
      (await conversationState(page, conversation)).conversation
        .resource_bindings,
    ).toEqual(confirmed.conversation.resource_bindings);
    expect(commands).toHaveLength(1);
    await writeEvidence(info, 'durable-receipt-reconciliation', {
      commandIds: commands,
      bindings: confirmed.conversation.resource_bindings,
      reopenedBeforeResponseReleased: true,
    });
  } finally {
    release();
  }
});
