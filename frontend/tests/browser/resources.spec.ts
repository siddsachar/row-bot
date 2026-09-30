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
} from './unified-helpers';
import type { Locator, Page } from '@playwright/test';
import { installDesktopFolderBridge } from './surface-helpers';

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
  await designAction(page, preview, 'Share');
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
    .getByRole('button', { name: 'Prepare channel send', exact: true })
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
    .getByRole('button', { name: 'Confirm send to channel', exact: true })
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
    // The canvas frame; page-strip thumbnails are separate static frames.
    const frame = region.locator('iframe[title*=" preview: "]');
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
      info.annotations.push({
        type: 'expected-console-error',
        description: JSON.stringify({
          signature:
            'Failed to load resource: the server responded with a status of 409 (Conflict)',
          count: 1,
          upTo: true,
          owner: 'resources interaction-page fixture',
          fixture:
            'The fixture saves interaction pages behind the panel: a lifecycle read for the revision before answers 409 and the panel reads the new one',
        }),
      });
      const seeded = await page.request.post(
        `/__p4_fixture/artifacts/${state.conversation.resource_bindings[0].resource_id}/interaction-pages`,
        {
          headers: fixtureHeaders(),
        },
      );
      expect(seeded.ok()).toBe(true);
      // No manual refresh outside an error: reopening the canvas reads it.
      await region.getByRole('radio', { name: 'Edit', exact: true }).click();
      await region.getByRole('radio', { name: 'Preview', exact: true }).click();
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
    const pageLabel = mode === 'deck' ? 'Slide' : 'Page';
    const pageMenu = region.getByRole('button', {
      name: new RegExp(`^${pageLabel} 1 of \\d+`),
    });
    if (!/ 1 of 1:/.test((await pageMenu.getAttribute('aria-label')) ?? '')) {
      await pageMenu.click();
      await page.getByRole('menuitem').nth(1).click();
      await expect(
        region.getByRole('button', {
          name: new RegExp(`^${pageLabel} 2 of`),
        }),
      ).toBeVisible();
    }
    await designAction(page, region, 'Inspector');
    const inspector = region.getByRole('complementary', {
      name: 'Design inspector',
      exact: true,
    });
    await expect(inspector).toBeVisible();
    await expect(
      inspector.getByRole('tab', { name: 'Selection', exact: true }),
    ).toHaveAttribute('aria-selected', 'true');
    // The name is edited in place in the top bar; Enter saves it.
    const renamed = `Edited ${label} ${conversation}`;
    const nameBox = region.getByRole('textbox', {
      name: 'Design name',
      exact: true,
    });
    await nameBox.fill(renamed);
    await nameBox.press('Enter');
    await expect(region.getByText('Renamed.', { exact: true })).toBeVisible();
    await expect(nameBox).toHaveValue(renamed);
    await screenshot(page, info, `${mode}-saved-properties`);
    await accessibility(page, info, `${mode}-saved-properties`, {
      opaquePreview: true,
    });
    await region
      .getByRole('button', { name: 'Close inspector', exact: true })
      .click();
    await designAction(page, region, 'Export');
    const exporting = region.getByRole('region', {
      name: 'Design export',
      exact: true,
    });
    // One click: this computer's owner gets a copy in the workspace's
    // Exports folder with Open / Show in folder (never clicked here: they
    // open the person's own apps).
    await exporting
      .getByRole('button', { name: 'Export as HTML', exact: true })
      .click();
    await expect(exporting.getByRole('status')).toContainText(
      `Saved · ${renamed}.html in `,
    );
    await expect(exporting.getByRole('status')).toContainText('› Exports');
    await expect(
      exporting.getByRole('button', { name: 'Show in folder', exact: true }),
    ).toBeVisible();
    await expect(
      exporting.getByRole('button', { name: 'Open', exact: true }),
    ).toBeVisible();
    await screenshot(page, info, `${mode}-html-export`);
    await accessibility(page, info, `${mode}-html-export`, {
      opaquePreview: true,
    });
    await region
      .getByRole('button', { name: 'Close export', exact: true })
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
    // Brand colours live in the inspector's Brand tab and save on their own.
    await designAction(page, region, 'Inspector');
    await inspector.getByRole('tab', { name: 'Brand', exact: true }).click();
    const designControls = region.getByRole('region', {
      name: 'Design brand',
      exact: true,
    });
    const primary = designControls.getByRole('textbox', {
      name: 'Primary colour',
      exact: true,
    });
    await primary.fill('#654321');
    await primary.blur();
    await expect(designControls.getByRole('status')).toHaveText('Saved.');
    await expect(primary).toHaveValue('#654321');
    await screenshot(page, info, `${mode}-advanced-brand`);
    await region
      .getByRole('button', { name: 'Close inspector', exact: true })
      .click();
    // Present starts at once from the current page.
    await designAction(page, region, 'Present');
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

/**
 * A Design toolbar action (B247): its own button on a wide panel; on a
 * phone-width panel Present, Share and Export sit in "Share or export" and
 * the inspector and versions in ⋯.
 */
async function designAction(
  page: Page,
  region: Locator,
  name: 'Inspector' | 'Present' | 'Share' | 'Export',
) {
  const button = region.getByRole('button', { name, exact: true });
  if (await button.isVisible()) return button.click();
  const share = name !== 'Inspector';
  await region
    .getByRole('button', {
      name: share ? 'Share or export' : 'More design actions',
      exact: true,
    })
    .click();
  await page
    .getByRole('menuitem', {
      name: name === 'Share' || name === 'Export' ? `${name}…` : name,
      exact: true,
    })
    .click();
}

/** The canvas has shown the saved design and stopped reloading. */
async function settledCanvas(region: Locator, frame: Locator) {
  let seen: string | null = null;
  await expect
    .poll(
      async () => {
        const now = await frame.getAttribute('srcdoc');
        const updating = await region
          .getByText('Updating preview…', { exact: true })
          .count();
        const settled = now !== null && now === seen && updating === 0;
        seen = now;
        return settled;
      },
      { intervals: [300] },
    )
    .toBe(true);
}

// A 1×1 PNG, the logo uploaded below.
const LOGO_PNG =
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==';

test('Design inline edits in a row keep the selection, and colour, font and logo choices save (B245, B246)', async ({
  page,
}, info) => {
  await newConversation(page);
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
  const region = page.getByRole('region', {
    name: 'Design preview',
    exact: true,
  });
  const frame = region.locator('iframe[title*=" preview: "]');
  await region.getByRole('radio', { name: 'Edit', exact: true }).click();
  await expect(frame).toHaveAttribute('sandbox', 'allow-scripts');
  const artwork = frame.contentFrame();
  // Several inline edits in a row on the same text, each saved before the next.
  for (const text of ['Bake sale one', 'Bake sale two', 'Bake sale three']) {
    await settledCanvas(region, frame);
    // Every element can be selected; text the panel saves is marked editable.
    const target = artwork.locator('[data-row-bot-text]').first();
    await target.dispatchEvent('dblclick');
    await target.fill(text);
    await target.press('Enter');
    await expect(frame).toHaveAttribute('srcdoc', new RegExp(text));
  }
  await settledCanvas(region, frame);
  // Edit mode opens the inspector (a half-height sheet on a phone).
  const inspector = region.getByRole('complementary', {
    name: 'Design inspector',
    exact: true,
  });
  if (!(await inspector.isVisible()))
    await designAction(page, region, 'Inspector');
  // The edited text is still the selection, and nothing reports an error.
  await expect(
    inspector.getByRole('textbox', { name: 'Element text', exact: true }),
  ).toHaveValue('Bake sale three');
  await expect(inspector.getByRole('alert')).toHaveCount(0);
  const selection = inspector.getByRole('region', {
    name: 'Design controls',
    exact: true,
  });
  // Text gets text controls (B247): a size stepper, weights, colour swatches.
  await expect(
    selection.getByRole('radiogroup', { name: 'Weight', exact: true }),
  ).toBeVisible();
  await selection
    .getByLabel('Colour: another colour', { exact: true })
    .fill('#aa3300');
  await expect(frame).toHaveAttribute('srcdoc', /#aa3300/i);
  // The controls and the selected element each keep a status line (B247).
  await expect(
    selection.getByRole('status').filter({ hasText: /^Saved\.$/ }),
  ).toBeVisible();
  await expect(inspector.getByRole('alert')).toHaveCount(0);
  await screenshot(page, info, 'design-text-controls');
  // Brand settings are one tab: the heading font comes from a searchable
  // list, not a text box.
  await inspector.getByRole('tab', { name: 'Brand', exact: true }).click();
  const controls = inspector.getByRole('region', {
    name: 'Design brand',
    exact: true,
  });
  await controls
    .getByRole('button', { name: 'Heading font', exact: true })
    .click();
  await page
    .getByRole('combobox', { name: 'Search heading font', exact: true })
    .fill('Lora');
  await page
    .getByRole('listbox', { name: 'Heading font', exact: true })
    .getByRole('option', { name: 'Lora', exact: true })
    .click();
  await expect(
    controls.getByRole('button', { name: 'Heading font', exact: true }),
  ).toHaveAccessibleDescription('Lora');
  await expect(frame).toHaveAttribute('srcdoc', /Lora/);
  // The logo is a picture: upload one, it becomes the logo, then place it.
  const logo = controls.getByRole('radiogroup', { name: 'Logo', exact: true });
  await expect(
    logo.getByRole('radio', { name: 'No logo', exact: true }),
  ).toHaveAttribute('aria-checked', 'true');
  await expect(
    controls.getByRole('button', { name: 'Upload logo…', exact: true }),
  ).toBeVisible();
  await controls.getByLabel('Logo file', { exact: true }).setInputFiles({
    name: 'bake-sale-logo.png',
    mimeType: 'image/png',
    buffer: Buffer.from(LOGO_PNG, 'base64'),
  });
  const uploaded = logo.getByRole('radio', {
    name: 'bake-sale-logo.png',
    exact: true,
  });
  await expect(uploaded).toHaveAttribute('aria-checked', 'true');
  await expect(uploaded.locator('img')).toHaveAttribute('src', /^blob:/);
  await controls
    .getByRole('radio', { name: 'Bottom left', exact: true })
    .click();
  await expect(
    controls.getByRole('radio', { name: 'Bottom left', exact: true }),
  ).toHaveAttribute('aria-checked', 'true');
  await screenshot(page, info, 'design-pickers');
  await logo.getByRole('radio', { name: 'No logo', exact: true }).click();
  await expect(
    controls.getByRole('radiogroup', { name: 'Logo placement', exact: true }),
  ).toHaveCount(0);
  await expect(inspector.getByRole('alert')).toHaveCount(0);
});

test('Phase 4 sandbox import and Undo retain reviews and restore exact original files', async ({
  page,
}, info) => {
  await installDesktopFolderBridge(page);
  const conversation = await newConversation(page);
  await composer(page).fill('Retained sandbox import draft');
  await page.getByRole('button', { name: 'Add resource', exact: true }).click();
  const setup = page.getByRole('dialog', { name: 'Add resource', exact: true });
  await setup.getByRole('radio', { name: 'Code folder', exact: true }).click();
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
  // Sandbox imports sit under the agent changes in the Changes tab.
  const inspector = page.getByRole('region', { name: / inspector$/ });
  await inspector.getByRole('button', { name: 'Refresh inspector' }).click();
  await inspector.getByRole('tab', { name: /^Changes/ }).click();
  const sandboxChanges = inspector
    .locator('summary')
    .filter({ hasText: 'Sandbox changes' });
  const imports = page.getByRole('region', {
    name: 'Sandbox imports',
    exact: true,
  });
  // The section opens by itself once it counts a waiting import; opening it by
  // hand before that could race the automatic open and close it again.
  await expect(sandboxChanges).toContainText('waiting');
  await expect(async () => {
    const details = sandboxChanges.locator('xpath=..');
    if ((await details.getAttribute('open')) === null)
      await sandboxChanges.click();
    await expect(imports).toBeVisible({ timeout: 1_000 });
  }).toPass();
  await imports.getByRole('button', { name: /^Pending · 2 files/ }).click();
  await expect(
    imports.getByText('Synthetic imported', { exact: false }),
  ).toBeVisible();
  // Selecting a saved change shows its patch; nothing is imported until the
  // one reviewed Import step.
  await expect(
    imports.getByRole('textbox', { name: 'Saved patch', exact: true }),
  ).toHaveValue(/new\/nested\/empty\.txt/);
  const before = await page.request.get(
    `/__p4_fixture/resources/${resource}/import-probe`,
    { headers: fixtureHeaders() },
  );
  expect(await before.json()).toMatchObject({
    content: 'Synthetic original\n',
    empty_created: false,
    imported: false,
  });
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    await assertNoOverflow(page);
    await screenshot(page, info, `sandbox-import-review-${appearance}`);
    await accessibility(page, info, `sandbox-import-review-${appearance}`);
  }
  await imports
    .getByRole('button', { name: 'Import selected changes', exact: true })
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
  // The import is an agent change the panel can undo, after a confirmation.
  const openUndo = async () => {
    await inspector
      .getByRole('button', {
        name: 'More actions for Import sandbox changes',
        exact: true,
      })
      .click();
    await page
      .getByRole('menuitem', {
        name: 'Undo Import sandbox changes',
        exact: true,
      })
      .click();
  };
  await openUndo();
  const undo = page.getByRole('region', {
    name: 'Undo workspace changes',
    exact: true,
  });
  await expect(undo).toContainText('Undo “Import sandbox changes”?');
  await undo.getByRole('button', { name: 'Keep changes', exact: true }).click();
  await expect(undo).toHaveCount(0);
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
  await openUndo();
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
  await undo.getByRole('button', { name: 'Undo change', exact: true }).click();
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

test('Phase 4 one-shot empty workspace save failure requires renewed parent and resumes exact identity', async ({
  page,
}, info) => {
  await installDesktopFolderBridge(page);
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
  await dialog.getByRole('radio', { name: 'Code folder', exact: true }).click();
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
      dialog.getByRole('button', { name: 'Check setup', exact: true }),
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
