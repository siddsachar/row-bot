import { readLayout } from './panel-helpers';
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
  composer,
  conversationState,
  fixtureState,
  fixtureResources,
  markWorkspaceIdentity,
  newConversation,
  openConversation,
  releaseProducer,
  captureActualResourcePanels,
  assertConversationSummaries,
} from './unified-helpers';

// Playwright's built-in blocker reads a forbidden getter in opaque srcdoc.
// Retain registration blocking without generating an instrumentation exception.
test.use({ serviceWorkers: 'allow' });
test.beforeEach(async ({ context }) => {
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
});

test('two Decks require an explicit captured write target independent of panel focus', async ({
  page,
}, info) => {
  const conversation = await newConversation(page);
  for (const [index, name] of [
    'First target Deck',
    'Second target Deck',
  ].entries()) {
    await page
      .getByRole('button', { name: 'Add resource', exact: true })
      .click();
    const setup = page.getByRole('dialog', {
      name: 'Add resource',
      exact: true,
    });
    if (index)
      await setup
        .getByRole('button', { name: 'Start another resource', exact: true })
        .click();
    await setup
      .getByRole('textbox', { name: 'Name (optional)', exact: true })
      .fill(name);
    await setup
      .getByRole('button', { name: 'Create Deck', exact: true })
      .click();
    await expect
      .poll(
        async () =>
          (await conversationState(page, conversation)).conversation
            .resource_bindings.length,
      )
      .toBe(index + 1);
    await expect(
      setup.getByText('Resource ready', { exact: true }),
    ).toBeVisible();
    await page.keyboard.press('Escape');
    await page
      .getByRole('button', { name: 'Close all panels', exact: true })
      .click();
  }
  const targets = (await conversationState(page, conversation)).conversation
    .resource_bindings;
  const first = targets[0].binding_id;
  await expect(
    page.getByRole('button', { name: 'Deck target', exact: true }),
  ).toContainText('Deck · None');
  await page.getByRole('button', { name: 'Deck target', exact: true }).click();
  await page
    .getByRole('menuitem', { name: 'First target Deck', exact: true })
    .click();
  await composer(page).fill('target fixture');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  const confirmation = page.getByRole('alertdialog', {
    name: 'Confirm resource targets',
    exact: true,
  });
  await expect(confirmation).toContainText('First target Deck');
  await confirmation
    .getByRole('button', { name: 'Send with these targets', exact: true })
    .click();
  await expect(
    page.getByText('Captured target is active.', { exact: true }),
  ).toBeVisible();
  const call = (await fixtureState(page)).calls
    .filter((item) => item.conversation_id === conversation)
    .at(-1)!;
  try {
    expect(call.accepted_binding_ids).toEqual([first]);
    await page
      .locator('.resource-chips')
      .getByRole('button', { name: 'Second target Deck', exact: true })
      .click();
    await expect(
      page.getByRole('region', { name: 'Design preview', exact: true }),
    ).toBeVisible();
    await releaseProducer(page, call);
    await expect
      .poll(
        async () =>
          (await fixtureState(page)).calls.find(
            (item) => item.generation_id === call.generation_id,
          )?.final_binding_ids,
      )
      .toEqual([first]);
    await page
      .getByRole('button', { name: 'Close all panels', exact: true })
      .click();
    await expect(
      page.getByRole('button', { name: 'Deck target', exact: true }),
    ).toContainText('First target Deck');
    await writeEvidence(info, 'two-deck-target-capture', {
      conversation,
      targets,
      acceptedBinding: first,
      focusedResource: targets[1].resource_id,
      finalBindings: [first],
    });
    await screenshot(page, info, 'explicit-target-after-other-panel-focus');
  } finally {
    if (
      !(await fixtureState(page)).calls.find(
        (item) => item.generation_id === call.generation_id,
      )?.quiesced
    )
      await releaseProducer(page, call);
  }
});

test('optional first-draft provider failure preserves the confirmed Deck and never automatically resubmits', async ({
  page,
}, info) => {
  const conversation = await newConversation(page);
  await composer(page).fill('Unsent chat draft survives first-draft failure');
  await page.getByRole('button', { name: 'Add resource', exact: true }).click();
  const setup = page.getByRole('dialog', { name: 'Add resource', exact: true });
  await setup
    .getByRole('textbox', { name: 'Name (optional)', exact: true })
    .fill('Failure retained Deck');
  await setup
    .getByRole('textbox', { name: 'Brief (optional)', exact: true })
    .fill('fail first draft');
  await setup
    .getByRole('checkbox', {
      name: /Review first draft generation after creation/,
    })
    .check();
  await setup
    .getByRole('button', { name: 'Create and review first draft', exact: true })
    .click();
  await expect(
    setup.getByRole('region', { name: 'First draft generation', exact: true }),
  ).toBeVisible();
  const before = await conversationState(page, conversation);
  expect(before.conversation.resource_bindings).toHaveLength(1);
  expect(
    (await fixtureState(page)).calls.filter(
      (item) => item.conversation_id === conversation,
    ),
  ).toHaveLength(0);
  await setup
    .getByRole('button', { name: 'Review generation controls', exact: true })
    .click();
  await setup
    .getByRole('button', { name: 'Generate first draft', exact: true })
    .click();
  await expect
    .poll(
      async () =>
        (await fixtureState(page)).calls
          .filter((item) => item.conversation_id === conversation)
          .at(-1)?.quiesced,
    )
    .toBe(true);
  await setup
    .getByRole('button', { name: 'Check generation receipt', exact: true })
    .click();
  await expect(
    setup.getByText('Generation request accepted.', { exact: true }),
  ).toBeVisible();
  await page.keyboard.press('Escape');
  await page
    .getByRole('button', { name: 'Close all panels', exact: true })
    .click();
  await expect(
    page.getByRole('status').filter({ hasText: /^Work interrupted\./ }),
  ).toBeVisible();
  await expect(composer(page)).toHaveValue(
    'Unsent chat draft survives first-draft failure',
  );
  await page.reload();
  await expect(composer(page)).toHaveValue(
    'Unsent chat draft survives first-draft failure',
  );
  await page.getByRole('button', { name: 'Add resource', exact: true }).click();
  await setup
    .getByRole('button', { name: 'Check generation receipt', exact: true })
    .click();
  await expect(
    setup.getByText('Generation request accepted.', { exact: true }),
  ).toBeVisible();
  const after = await conversationState(page, conversation);
  expect(after.conversation.resource_bindings).toEqual(
    before.conversation.resource_bindings,
  );
  const calls = (await fixtureState(page)).calls.filter(
    (item) => item.conversation_id === conversation,
  );
  expect(calls).toHaveLength(1);
  expect(calls[0].accepted_binding_ids).toEqual([
    before.conversation.resource_bindings[0].binding_id,
  ]);
  await writeEvidence(info, 'first-draft-partial-failure', {
    conversation,
    bindings: after.conversation.resource_bindings,
    generation: calls[0].generation_id,
    providerInvocations: 1,
    draft: 'retained',
    receipt: 'accepted despite later provider failure',
  });
  await screenshot(page, info, 'confirmed-deck-after-generation-failure');
});

test('global Deck creation and repeated Open return to one original conversation', async ({
  page,
}, testInfo) => {
  const original = await newConversation(page);
  const deckName = `Global origin Deck ${testInfo.project.name}`;
  await composer(page).fill(
    'Original chat draft survives global resource setup',
  );
  const before = await fixtureState(page);
  await page.getByRole('button', { name: 'New resource', exact: true }).click();
  let dialog = page.getByRole('dialog', {
    name: 'New or open resource',
    exact: true,
  });
  await dialog
    .getByRole('textbox', { name: 'Name (optional)', exact: true })
    .fill(deckName);
  await dialog
    .getByRole('button', { name: 'Create Deck', exact: true })
    .click();
  await expect(page).not.toHaveURL(new RegExp(`/conversations/${original}$`));
  await expect(page).toHaveURL(/\/app-v2\/conversations\/[^/?]+/);
  const origin = new URL(page.url()).pathname.split('/').at(-1)!;
  await page.keyboard.press('Escape');
  const created = await conversationState(page, origin);
  expect(created.conversation.resource_bindings).toHaveLength(1);
  for (let attempt = 0; attempt < 2; attempt++) {
    await openConversation(page, original);
    await expect(composer(page)).toHaveValue(
      'Original chat draft survives global resource setup',
    );
    await page
      .getByRole('button', { name: 'New resource', exact: true })
      .click();
    dialog = page.getByRole('dialog', {
      name: 'New or open resource',
      exact: true,
    });
    await dialog
      .getByRole('button', { name: 'Start another resource', exact: true })
      .click();
    await dialog
      .getByRole('combobox', { name: 'Choose resource', exact: true })
      .selectOption('existing');
    await dialog
      .getByRole('button', {
        name: `${deckName} Resource ID: ${created.conversation.resource_bindings[0].resource_id}`,
        exact: true,
      })
      .click();
    await dialog
      .getByRole('button', { name: 'Open resource', exact: true })
      .click();
    await expect(page).toHaveURL(new RegExp(`/conversations/${origin}$`));
    await page.keyboard.press('Escape');
    expect(
      (await conversationState(page, origin)).conversation.resource_bindings,
    ).toEqual(created.conversation.resource_bindings);
  }
  expect((await fixtureState(page)).calls).toHaveLength(before.calls.length);
  await writeEvidence(testInfo, 'global-resource-origin', {
    original,
    origin,
    bindings: created.conversation.resource_bindings,
    repeatedOpen: 2,
    providerCalls: 0,
  });
});

test('explicit existing-folder picker registers its real saved identity without editing files or Git', async ({
  page,
}, testInfo) => {
  const conversation = await newConversation(page);
  const before = await fixtureResources(page);
  const callsBefore = (await fixtureState(page)).calls.length;
  const returnFromCompactResource = async () => {
    if ((page.viewportSize()?.width ?? 1440) >= 1024) return;
    await expect(
      page.getByRole('region', { name: 'Compact panel', exact: true }),
    ).toBeVisible();
    await page
      .getByRole('button', { name: 'Back to conversation', exact: true })
      .click();
    await expect(composer(page)).toBeVisible();
  };
  await composer(page).fill('Folder registration draft');
  await page.getByRole('button', { name: 'Add resource', exact: true }).click();
  const dialog = page.getByRole('dialog', {
    name: 'Add resource',
    exact: true,
  });
  await dialog
    .getByRole('combobox', { name: 'Resource type', exact: true })
    .selectOption('workspace');
  await expect(
    dialog.getByRole('button', { name: 'Register folder', exact: true }),
  ).toBeDisabled();
  await dialog
    .getByRole('button', { name: 'Choose existing folder', exact: true })
    .click();
  await expect(
    dialog.getByText('Selected: fixture-workspace', { exact: true }),
  ).toBeVisible();
  await dialog
    .getByRole('button', { name: 'Register folder', exact: true })
    .click();
  await expect
    .poll(
      async () =>
        (await conversationState(page, conversation)).conversation
          .resource_bindings.length,
    )
    .toBe(1);
  await page.keyboard.press('Escape');
  const bound = (await conversationState(page, conversation)).conversation
    .resource_bindings;
  expect(bound[0].resource_id).toBe(before.workspace_id);
  expect(await fixtureResources(page)).toEqual(before);
  expect(before.git_present).toBe(false);
  await expect(composer(page)).toHaveValue('Folder registration draft');
  await screenshot(page, testInfo, 'authorized-existing-folder');
  const chooseGlobalWorkspace = async (hasPriorOutcome: boolean) => {
    await page
      .getByRole('button', { name: 'New resource', exact: true })
      .click();
    const global = page.getByRole('dialog', {
      name: 'New or open resource',
      exact: true,
    });
    if (hasPriorOutcome)
      await global
        .getByRole('button', { name: 'Start another resource', exact: true })
        .click();
    await global
      .getByRole('combobox', { name: 'Resource type', exact: true })
      .selectOption('workspace');
    await global
      .getByRole('combobox', { name: 'Choose resource', exact: true })
      .selectOption('existing');
    await expect(
      global.getByRole('button', {
        name: 'New conversation with this workspace',
        exact: true,
      }),
    ).toBeDisabled();
    await global
      .getByRole('button', {
        name: `Phase 1 workspace Resource ID: ${before.workspace_id}`,
        exact: true,
      })
      .click();
    return global;
  };
  let global = await chooseGlobalWorkspace(false);
  await global
    .getByRole('button', { name: 'Open resource', exact: true })
    .click();
  await expect(
    global.getByText('Resource ready', { exact: true }),
  ).toBeVisible();
  const originalWorkspaceConversation = new URL(page.url()).pathname
    .split('/')
    .at(-1)!;
  const originalWorkspaceState = await conversationState(
    page,
    originalWorkspaceConversation,
  );
  await page.keyboard.press('Escape');
  await returnFromCompactResource();
  await openConversation(page, conversation);
  await expect(composer(page)).toHaveValue('Folder registration draft');
  global = await chooseGlobalWorkspace(true);
  await global
    .getByRole('button', {
      name: 'New conversation with this workspace',
      exact: true,
    })
    .click();
  await expect(
    global.getByText('Resource ready', { exact: true }),
  ).toBeVisible();
  await expect(page).not.toHaveURL(
    new RegExp(`/conversations/${originalWorkspaceConversation}$`),
  );
  const separateWorkspaceConversation = new URL(page.url()).pathname
    .split('/')
    .at(-1)!;
  expect(separateWorkspaceConversation).not.toBe(conversation);
  const separateState = await conversationState(
    page,
    separateWorkspaceConversation,
  );
  expect(separateState.conversation.resource_bindings).toHaveLength(1);
  const separateBinding = separateState.conversation.resource_bindings[0];
  expect(separateBinding.resource_id).toBe(before.workspace_id);
  expect(separateBinding.binding_id).not.toBe(bound[0].binding_id);
  for (const originalBinding of originalWorkspaceState.conversation
    .resource_bindings)
    expect(separateBinding.binding_id).not.toBe(originalBinding.binding_id);
  await page.keyboard.press('Escape');
  await returnFromCompactResource();
  await openConversation(page, separateWorkspaceConversation);
  await composer(page).fill('Separate workspace conversation draft');
  await expect(
    page.getByRole('status').filter({ hasText: /^Draft saved$/ }),
  ).toBeVisible();
  global = await chooseGlobalWorkspace(true);
  await global
    .getByRole('button', { name: 'Open resource', exact: true })
    .click();
  await expect(
    global.getByText('Resource ready', { exact: true }),
  ).toBeVisible();
  await expect(page).toHaveURL(
    new RegExp(`/conversations/${originalWorkspaceConversation}$`),
  );
  expect(
    (await conversationState(page, originalWorkspaceConversation)).conversation,
  ).toEqual(originalWorkspaceState.conversation);
  await page.keyboard.press('Escape');
  await returnFromCompactResource();
  await openConversation(page, separateWorkspaceConversation);
  await expect(composer(page)).toHaveValue(
    'Separate workspace conversation draft',
  );
  await openConversation(page, conversation);
  await expect(composer(page)).toHaveValue('Folder registration draft');
  expect(await fixtureResources(page)).toEqual(before);
  expect((await fixtureState(page)).calls).toHaveLength(callsBefore);
  await writeEvidence(testInfo, 'explicit-global-workspace-conversation', {
    resourceId: before.workspace_id,
    originalWorkspaceConversation,
    separateWorkspaceConversation,
    separateBinding,
    originalBindings: originalWorkspaceState.conversation.resource_bindings,
    originalConversationUnchanged: true,
    globalOpenReturnsOriginal: true,
    providerInvocations: 0,
    fileChecksumUnchanged: before.fixture_file_sha256,
    gitCreated: false,
  });
  await writeEvidence(testInfo, 'existing-folder-integrity', {
    conversation,
    binding: bound[0],
    checksum: before.fixture_file_sha256,
    gitCreated: false,
  });
});

test('adding a real Deck and saved workspace preserves the live conversation, settings and attachment draft', async ({
  page,
}, testInfo) => {
  const conversation = await newConversation(page);
  const initial = await conversationState(page, conversation);
  await composer(page).fill('Keep working while I add resources');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await expect(
    page.getByText('Synthetic stream is active.', { exact: true }),
  ).toBeVisible();
  const call = (await fixtureState(page)).calls.at(-1)!;
  try {
    await composer(page).fill('Unsent multi-resource draft');
    const chooser = page.waitForEvent('filechooser');
    await page
      .getByRole('button', { name: 'Attach file', exact: true })
      .click();
    await (
      await chooser
    ).setFiles({
      name: 'synthetic-note.txt',
      mimeType: 'text/plain',
      buffer: Buffer.from('Synthetic attachment content'),
    });
    await expect(
      page.getByRole('button', {
        name: 'Remove synthetic-note.txt',
        exact: true,
      }),
    ).toBeVisible();
    await markWorkspaceIdentity(page);
    await page
      .getByRole('button', { name: 'Add resource', exact: true })
      .click();
    let dialog = page.getByRole('dialog', {
      name: 'Add resource',
      exact: true,
    });
    await dialog
      .getByRole('textbox', { name: 'Name (optional)', exact: true })
      .fill('Phase 3 review Deck');
    await dialog
      .getByRole('button', { name: 'Create Deck', exact: true })
      .click();
    await expect
      .poll(
        async () =>
          (await conversationState(page, conversation)).conversation
            .resource_bindings.length,
      )
      .toBe(1);
    await expect(
      dialog.getByText('Resource ready', { exact: true }),
    ).toBeVisible();
    await page.keyboard.press('Escape');
    if (page.viewportSize()!.width < 1024)
      await page
        .getByRole('button', { name: 'Back to conversation', exact: true })
        .click();
    await assertWorkspaceIdentity(page);
    await expect(composer(page)).toHaveValue('Unsent multi-resource draft');

    await page
      .getByRole('button', { name: 'Add resource', exact: true })
      .click();
    dialog = page.getByRole('dialog', { name: 'Add resource', exact: true });
    await dialog
      .getByRole('button', { name: 'Start another resource', exact: true })
      .click();
    await dialog
      .getByRole('combobox', { name: 'Resource type', exact: true })
      .selectOption('workspace');
    await dialog
      .getByRole('combobox', { name: 'Choose resource', exact: true })
      .selectOption('existing');
    await dialog
      .getByRole('button', {
        name: `Phase 1 workspace Resource ID: ${(await fixtureResources(page)).workspace_id}`,
        exact: true,
      })
      .click();
    await dialog
      .getByRole('button', { name: 'Add to this conversation', exact: true })
      .click();
    await expect
      .poll(
        async () =>
          (await conversationState(page, conversation)).conversation
            .resource_bindings.length,
      )
      .toBe(2);
    await expect(
      dialog.getByText('Resource ready', { exact: true }),
    ).toBeVisible();
    await page.keyboard.press('Escape');
    if (page.viewportSize()!.width < 1024)
      await page
        .getByRole('button', { name: 'Back to conversation', exact: true })
        .click();
    await assertWorkspaceIdentity(page);
    await expect(page).toHaveURL(
      new RegExp(`/app-v2/conversations/${conversation}$`),
    );
    await expect(composer(page)).toHaveValue('Unsent multi-resource draft');
    await expect(
      page.getByRole('button', {
        name: 'Remove synthetic-note.txt',
        exact: true,
      }),
    ).toBeVisible();
    const bound = await conversationState(page, conversation);
    expect(bound.workspace.controls).toEqual(initial.workspace.controls);
    expect(
      bound.conversation.resource_bindings.map((item) => item.kind).sort(),
    ).toEqual(['artifact', 'workspace']);
    expect((await fixtureState(page)).calls.at(-1)?.generation_id).toBe(
      call.generation_id,
    );
    const originalViewport = page.viewportSize()!;
    const registered = (await readLayout(page)).panels;
    for (const viewport of [
      originalViewport.width >= 1024
        ? { width: 390, height: 844 }
        : { width: 1440, height: 900 },
      originalViewport,
    ]) {
      const activeBeforeResize = (await readLayout(page)).activePanelId;
      await page.setViewportSize(viewport);
      const back = page.getByRole('button', {
        name: 'Back to conversation',
        exact: true,
      });
      if (viewport.width < 1024 && activeBeforeResize !== null) {
        await expect(
          page.getByRole('region', { name: 'Compact panel', exact: true }),
        ).toBeVisible();
        await back.click();
      }
      await expect(
        page.getByRole('region', { name: 'Compact panel', exact: true }),
      ).toHaveCount(0);
      await assertWorkspaceIdentity(page);
      await expect(composer(page)).toHaveValue('Unsent multi-resource draft');
      await expect(
        page.getByRole('button', {
          name: 'Remove synthetic-note.txt',
          exact: true,
        }),
      ).toBeVisible();
      expect((await readLayout(page)).panels.map((p) => p.instance_id)).toEqual(
        registered.map((p) => p.instance_id),
      );
      expect(
        (await conversationState(page, conversation)).conversation
          .resource_bindings,
      ).toEqual(bound.conversation.resource_bindings);
    }
    const persisted = await readLayout(page);
    await page.reload();
    await expect(composer(page)).toHaveValue('Unsent multi-resource draft');
    expect((await readLayout(page)).panels).toEqual(persisted.panels);
    await page.evaluate(() => {
      const size =
        innerWidth >= 1024 ? 'desktop' : innerWidth >= 768 ? 'tablet' : 'phone';
      const id = decodeURIComponent(
        location.pathname.split('/conversations/')[1],
      );
      const key = Object.keys(localStorage).find(
        (value) =>
          value.startsWith('row-bot:layout:v2:') &&
          value.endsWith(`:${encodeURIComponent(id)}:${size}`),
      )!;
      const layout = JSON.parse(localStorage.getItem(key)!);
      localStorage.setItem(
        key,
        JSON.stringify({
          ...layout,
          version: 0,
          navigation: 278,
          side: 99999,
          bottom: 1,
          activePanelId: null,
        }),
      );
    });
    await page.reload();
    await expect(composer(page)).toHaveValue('Unsent multi-resource draft');
    const migrated = await readLayout(page);
    expect(migrated.version).toBe(2);
    expect(migrated.panels).toEqual(persisted.panels);
    await markWorkspaceIdentity(page);
    await page
      .getByRole('button', { name: 'Preferences', exact: true })
      .click();
    await page
      .getByRole('button', { name: 'Reset layout', exact: true })
      .click();
    const reset = page.getByRole('alertdialog', {
      name: 'Reset layout?',
      exact: true,
    });
    await reset.getByRole('button', { name: 'Cancel', exact: true }).click();
    expect((await readLayout(page)).panels).toEqual(migrated.panels);
    await page
      .getByRole('button', { name: 'Reset layout', exact: true })
      .click();
    await reset
      .getByRole('button', { name: 'Reset layout', exact: true })
      .click();
    await expect
      .poll(async () => (await readLayout(page)).panels.length)
      .toBe(0);
    await page.keyboard.press('Escape');
    await assertWorkspaceIdentity(page);
    await expect(composer(page)).toHaveValue('Unsent multi-resource draft');
    await expect(
      page.getByRole('button', {
        name: 'Remove synthetic-note.txt',
        exact: true,
      }),
    ).toBeVisible();
    const afterLayout = await conversationState(page, conversation);
    expect(afterLayout.conversation.resource_bindings).toEqual(
      bound.conversation.resource_bindings,
    );
    expect(afterLayout.workspace.controls).toEqual(bound.workspace.controls);
    expect(
      (await fixtureState(page)).calls.find(
        (item) => item.generation_id === call.generation_id,
      )?.quiesced,
    ).toBe(false);
    await writeEvidence(testInfo, 'real-resource-layout-lifecycle', {
      persisted,
      migrated,
      reset: await readLayout(page),
      generation: call.generation_id,
      reloads: 2,
      draft: 'retained',
      attachment: 'retained',
      bindings: 'unchanged',
    });
    await releaseProducer(page, call);
    await expect(
      page.getByText('Synthetic stream is active. Synthetic stream settled.', {
        exact: true,
      }),
    ).toHaveCount(1);
    await captureActualResourcePanels(
      page,
      testInfo,
      'Phase 3 review Deck',
      'live-multi-resource',
    );
    await assertConversationSummaries(page);
    await assertNoOverflow(page);
    await screenshot(page, testInfo, 'conversation-with-real-resources');
    await accessibility(page, testInfo, 'multi-resource-workspace-axe', {
      opaquePreview: true,
    });
    await page.reload();
    await expect(composer(page)).toHaveValue('Unsent multi-resource draft');
    await expect(
      page.getByRole('button', {
        name: 'Remove synthetic-note.txt',
        exact: true,
      }),
    ).toBeVisible();
    expect(
      (await conversationState(page, conversation)).conversation
        .resource_bindings,
    ).toEqual(bound.conversation.resource_bindings);
    await writeEvidence(testInfo, 'additive-resource-identities', {
      conversation,
      generation: call.generation_id,
      bindings: bound.conversation.resource_bindings,
      draft: 'retained',
      attachment: 'retained',
      controls: 'unchanged',
    });
  } finally {
    if (
      !(await fixtureState(page)).calls.find(
        (item) => item.barrier_id === call.barrier_id,
      )?.quiesced
    )
      await releaseProducer(page, call);
  }
});

test('cancel before creating a resource keeps the chat draft and creates no binding', async ({
  page,
}, testInfo) => {
  const conversation = await newConversation(page);
  await composer(page).fill('Keep this draft after cancellation');
  const initial = await conversationState(page, conversation);
  await markWorkspaceIdentity(page);
  await page.getByRole('button', { name: 'Add resource', exact: true }).click();
  const dialog = page.getByRole('dialog', {
    name: 'Add resource',
    exact: true,
  });
  await dialog
    .getByRole('textbox', { name: 'Name (optional)', exact: true })
    .fill('Never created');
  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);
  await expect(
    page.getByRole('button', { name: 'Add resource', exact: true }),
  ).toBeFocused();
  await assertWorkspaceIdentity(page);
  await expect(composer(page)).toHaveValue(
    'Keep this draft after cancellation',
  );
  expect(
    (await conversationState(page, conversation)).conversation
      .resource_bindings,
  ).toEqual(initial.conversation.resource_bindings);
  await screenshot(page, testInfo, 'cancelled-setup-draft');
});

test('a late resource setup receipt retains its original A binding after navigation through B to C', async ({
  page,
}, testInfo) => {
  const createThroughNavigation = async () => {
    const navigation = page.getByRole('navigation', {
      name: 'Workspace navigation',
      exact: true,
    });
    if (!(await navigation.isVisible()))
      await page
        .getByRole('button', { name: 'Toggle navigation', exact: true })
        .click();
    await navigation
      .getByRole('button', { name: 'New chat', exact: true })
      .click();
  };
  const a = await newConversation(page);
  await composer(page).fill('Conversation A retained draft');
  let resolveEntered!: () => void;
  const entered = new Promise<void>((resolve) => {
    resolveEntered = resolve;
  });
  let release!: () => void;
  const barrier = new Promise<void>((resolve) => {
    release = resolve;
  });
  let releaseOpen = () => {};
  const endpoint = `/api/v1/conversations/${a}/commands`;
  await page.route(`**${endpoint}`, async (route) => {
    const response = await route.fetch();
    resolveEntered();
    await barrier;
    await route.fulfill({ response });
  });
  await page.getByRole('button', { name: 'Add resource', exact: true }).click();
  const dialog = page.getByRole('dialog', {
    name: 'Add resource',
    exact: true,
  });
  await dialog
    .getByRole('textbox', { name: 'Name (optional)', exact: true })
    .fill('Late A Deck');
  await dialog
    .getByRole('button', { name: 'Create Deck', exact: true })
    .click();
  try {
    await entered;
    await page.keyboard.press('Escape');
    await createThroughNavigation();
    await expect(page).not.toHaveURL(new RegExp(`/conversations/${a}$`));
    const b = new URL(page.url()).pathname.split('/').at(-1)!;
    await composer(page).fill('Conversation B retained draft');
    let openEntered!: () => void;
    const enteredOpen = new Promise<void>((resolve) => {
      openEntered = resolve;
    });
    const heldOpen = new Promise<void>((resolve) => {
      releaseOpen = resolve;
    });
    const openPattern = '**/api/v1/conversations/*/open';
    await page.route(openPattern, async (route) => {
      const response = await route.fetch();
      openEntered();
      await heldOpen;
      await route.fulfill({ response });
    });
    await createThroughNavigation();
    await expect(page).not.toHaveURL(new RegExp(`/conversations/${b}$`));
    const c = new URL(page.url()).pathname.split('/').at(-1)!;
    await enteredOpen;
    await composer(page).fill('Conversation C stays in focus');
    await expect(composer(page)).toHaveValue('Conversation C stays in focus');
    const openedC = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === `/api/v1/conversations/${c}/open`,
    );
    releaseOpen();
    await openedC;
    await page.unroute(openPattern);
    await expect(
      page.getByRole('status').filter({ hasText: /^Draft saved$/ }),
    ).toBeVisible();
    const arrived = page.waitForResponse(
      (response) => new URL(response.url()).pathname === endpoint,
    );
    release();
    await arrived;
    await expect(page).toHaveURL(new RegExp(`/conversations/${c}$`));
    await expect(composer(page)).toHaveValue('Conversation C stays in focus');
    expect(
      (await conversationState(page, a)).conversation.resource_bindings,
    ).toHaveLength(1);
    expect(
      (await conversationState(page, b)).conversation.resource_bindings,
    ).toHaveLength(0);
    expect(
      (await conversationState(page, c)).conversation.resource_bindings,
    ).toHaveLength(0);
    await expect(
      page.getByRole('tab', { name: 'Late A Deck', exact: true }),
    ).toHaveCount(0);
    await openConversation(page, b);
    await expect(composer(page)).toHaveValue('Conversation B retained draft');
    await openConversation(page, c);
    await expect(composer(page)).toHaveValue('Conversation C stays in focus');
    await writeEvidence(testInfo, 'late-setup-original-owner', {
      a,
      b,
      c,
      finalFocus: c,
      bindingOwner: a,
      earlyTypedCWhileOpenPending: true,
      distinctBDraftAndCDraftPersisted: true,
    });
  } finally {
    releaseOpen();
    release();
  }
});
