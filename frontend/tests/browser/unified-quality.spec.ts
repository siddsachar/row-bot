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
  fixtureState,
  markWorkspaceIdentity,
  newConversation,
  releaseProducer,
  addReviewResourcePair,
  captureActualResourcePanels,
  blockFixtureServiceWorkers,
  assertConversationSummaries,
  assertControlTextUnclipped,
} from './unified-helpers';

test.use({ serviceWorkers: 'allow' });
test.beforeEach(async ({ context }) => blockFixtureServiceWorkers(context));

test('real tools and media keep one composer through every colour theme and system switching', async ({
  page,
}, testInfo) => {
  test.setTimeout(180_000);
  const conversation = await newConversation(page);
  await composer(page).fill('rich fixture');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await expect(
    page.getByText('Synthetic tools and media are ready.', { exact: true }),
  ).toBeVisible();
  const call = (await fixtureState(page)).calls.at(-1)!;
  await page
    .locator('summary')
    .filter({ hasText: /^Activity \(/ })
    .click();
  await expect(
    page.getByRole('img', { name: 'Generated result', exact: true }),
  ).toBeVisible();
  await composer(page).fill('Retained while adjusting appearance');
  await markWorkspaceIdentity(page);
  await addReviewResourcePair(page, 'Theme review Deck');
  await page.getByRole('button', { name: 'Preferences', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Preferences', exact: true });
  await expect(dialog).toBeVisible();
  const observations: { appearance: string; accent: string }[] = [];
  try {
    for (const appearance of ['light', 'dark']) {
      for (const accent of ['blue', 'teal', 'violet', 'amber']) {
        await dialog
          .getByRole('combobox', { name: 'Appearance', exact: true })
          .selectOption(appearance);
        await dialog
          .getByRole('combobox', { name: 'Colour theme', exact: true })
          .selectOption(accent);
        await expect(page.locator('html')).toHaveAttribute(
          'data-theme',
          appearance,
        );
        await expect(page.locator('html')).toHaveAttribute(
          'data-accent',
          accent,
        );
        await assertWorkspaceIdentity(page);
        await expect(composer(page)).toHaveValue(
          'Retained while adjusting appearance',
        );
        await page.keyboard.press('Escape');
        await expect(dialog).toHaveCount(0);
        await captureActualResourcePanels(
          page,
          testInfo,
          'Theme review Deck',
          `real-resources-${appearance}-${accent}`,
        );
        await assertConversationSummaries(page);
        await assertNoOverflow(page);
        await accessibility(
          page,
          testInfo,
          `real-workspace-${appearance}-${accent}-axe`,
          { opaquePreview: true },
        );
        await screenshot(
          page,
          testInfo,
          `real-workspace-${appearance}-${accent}`,
        );
        observations.push({ appearance, accent });
        await page
          .getByRole('button', { name: 'Preferences', exact: true })
          .click();
        await expect(dialog).toBeVisible();
      }
    }
    await dialog
      .getByRole('combobox', { name: 'Appearance', exact: true })
      .selectOption('system');
    for (const scheme of ['dark', 'light', 'dark'] as const) {
      await page.emulateMedia({ colorScheme: scheme });
      await expect(page.locator('html')).toHaveAttribute('data-theme', scheme);
      await assertWorkspaceIdentity(page);
    }
    await page.keyboard.press('Escape');
    await expect(dialog).toHaveCount(0);
    await expect(
      page.getByRole('button', { name: 'Preferences', exact: true }),
    ).toBeFocused();
    await releaseProducer(page, call);
    await expect
      .poll(async () => (await fixtureState(page)).calls.at(-1)?.quiesced)
      .toBe(true);
    await assertWorkspaceIdentity(page);
    await expect(composer(page)).toHaveValue(
      'Retained while adjusting appearance',
    );
    const toolReading = await page
      .getByRole('log', { name: 'Conversation', exact: true })
      .locator('.message-tool .message-text')
      .evaluate((element) => ({
        fontSize: getComputedStyle(element).fontSize,
        lineHeight: getComputedStyle(element).lineHeight,
      }));
    expect(toolReading).toEqual({ fontSize: '16px', lineHeight: '24px' });
    await writeEvidence(testInfo, 'actual-tool-reading-type', toolReading);
    await accessibility(page, testInfo, 'settled-tools-media-axe', {
      opaquePreview: true,
    });
    await screenshot(page, testInfo, 'settled-tools-media');
    await writeEvidence(testInfo, 'appearance-during-generation', {
      conversation,
      generation: call.generation_id,
      themes: observations,
      systemSwitches: ['dark', 'light', 'dark'],
      composerInstances: await composer(page).count(),
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

test('reduced motion and forced colours preserve a usable single conversation', async ({
  page,
  context,
}, testInfo) => {
  test.setTimeout(120_000);
  await page.emulateMedia({
    reducedMotion: 'reduce',
    forcedColors: 'active',
    colorScheme: 'dark',
  });
  await page.addInitScript(() =>
    localStorage.setItem(
      'row-bot.appearance.v1',
      JSON.stringify({
        version: 1,
        appearance: 'dark',
        accent: 'blue',
        reduce_transparency: true,
      }),
    ),
  );
  await newConversation(page);
  const forcedTokens = () =>
    page.evaluate(() => {
      const root = document.documentElement;
      const computed = getComputedStyle(root);
      const names = [
        'accent-solid',
        'accent-on-solid',
        'canvas',
        'text-primary',
      ];
      return {
        active: matchMedia('(forced-colors: active)').matches,
        theme: root.dataset.theme,
        accent: root.dataset.accent,
        computed: Object.fromEntries(
          names.map((name) => [
            name,
            computed.getPropertyValue(`--${name}`).trim().toLowerCase(),
          ]),
        ),
        authored: Object.fromEntries(
          names.map((name) => [
            name,
            root.style.getPropertyValue(`--${name}`).trim().toLowerCase(),
          ]),
        ),
      };
    });
  const activeTokens = await forcedTokens();
  const systemTokens = {
    'accent-solid': 'highlight',
    'accent-on-solid': 'highlighttext',
    canvas: 'canvas',
    'text-primary': 'canvastext',
  };
  expect(activeTokens.active).toBe(true);
  expect(activeTokens.computed).toEqual(systemTokens);
  await page.emulateMedia({ forcedColors: 'none' });
  const restoredTokens = await forcedTokens();
  expect(restoredTokens.active).toBe(false);
  expect(restoredTokens.theme).toBe('dark');
  expect(restoredTokens.accent).toBe('blue');
  expect(restoredTokens.computed).toEqual(restoredTokens.authored);
  expect(restoredTokens.computed).not.toEqual(systemTokens);
  await page.emulateMedia({ forcedColors: 'active' });
  const reactivatedTokens = await forcedTokens();
  expect(reactivatedTokens.active).toBe(true);
  expect(reactivatedTokens.computed).toEqual(systemTokens);
  await writeEvidence(testInfo, 'forced-colour-token-cascade', {
    activeTokens,
    restoredTokens,
    reactivatedTokens,
  });
  await page.evaluate(() => {
    document.documentElement.style.zoom = '2';
  });
  await markWorkspaceIdentity(page);
  await expect(page.locator('html')).toHaveAttribute('data-opaque', 'true');
  await composer(page).fill('stop fixture');
  const send = page.getByRole('button', { name: 'Send', exact: true });
  await expect(send).toBeEnabled();
  const nativeButtonColours = await send.evaluate((element) => {
    const probe = document.createElement('button');
    probe.style.cssText =
      'position:fixed;visibility:hidden;color:CanvasText;background:Canvas;border:1px solid CanvasText';
    document.body.append(probe);
    const actual = getComputedStyle(element);
    const system = getComputedStyle(probe);
    const colours = (style: CSSStyleDeclaration) => ({
      foreground: style.color,
      background: style.backgroundColor,
      border: style.borderTopColor,
    });
    const observation = {
      actualDisabled: (element as HTMLButtonElement).disabled,
      actual: colours(actual),
      system: colours(system),
      forcedColourAdjust: actual.getPropertyValue('forced-color-adjust'),
      forcedColoursActive: matchMedia('(forced-colors: active)').matches,
    };
    probe.remove();
    return observation;
  });
  await writeEvidence(
    testInfo,
    'forced-colour-native-button',
    nativeButtonColours,
  );
  expect(nativeButtonColours.forcedColoursActive).toBe(true);
  expect(nativeButtonColours.actualDisabled).toBe(false);
  expect(nativeButtonColours.actual).toEqual(nativeButtonColours.system);
  expect(nativeButtonColours.forcedColourAdjust).not.toBe('none');
  await assertControlTextUnclipped(send);
  await send.click();
  await expect(
    page.getByText('Synthetic stream is active.', { exact: true }),
  ).toBeVisible();
  const originalViewport = page.viewportSize()!;
  await page.setViewportSize({ width: 360, height: 800 });
  await composer(page).fill('Accessible unsent draft');
  const stop = page.getByRole('button', { name: 'Stop', exact: true });
  await assertControlTextUnclipped(stop);
  await screenshot(page, testInfo, 'combined-stress-stop-control');
  await stop.click();
  await expect
    .poll(async () => (await fixtureState(page)).calls.at(-1)?.quiesced)
    .toBe(true);
  await expect(composer(page)).toHaveValue('Accessible unsent draft');
  await composer(page).fill('approval fixture');
  await assertControlTextUnclipped(send);
  await send.click();
  const review = page.getByRole('button', {
    name: 'Review approval',
    exact: true,
  });
  await assertControlTextUnclipped(review);
  await review.click();
  let dialog = page.getByRole('dialog', {
    name: 'Approval required',
    exact: true,
  });
  await expect(dialog).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);
  await expect(review).toBeFocused();
  const beforeDecision = (await fixtureState(page)).calls.length;
  await review.click();
  dialog = page.getByRole('dialog', { name: 'Approval required', exact: true });
  const reject = dialog.getByRole('button', {
    name: 'Reject action',
    exact: true,
  });
  const approve = dialog.getByRole('button', {
    name: 'Approve action',
    exact: true,
  });
  await assertControlTextUnclipped(approve);
  await assertControlTextUnclipped(reject);
  await screenshot(page, testInfo, 'combined-stress-approval-footer');
  await reject.click();
  await expect(dialog).toHaveCount(0);
  await expect(
    page.getByText('Synthetic approval rejected.', { exact: true }),
  ).toHaveCount(1);
  expect((await fixtureState(page)).calls).toHaveLength(beforeDecision + 1);
  await composer(page).fill('Accessible unsent draft');
  await expect(
    page.getByRole('status').filter({ hasText: /^Draft saved$/ }),
  ).toBeVisible();
  await context.setOffline(true);
  await expect(
    page.getByRole('status').filter({ hasText: /^Connected$/ }),
  ).toHaveCount(0);
  await context.setOffline(false);
  await expect(
    page.getByRole('status').filter({ hasText: /^Connected$/ }),
  ).toBeVisible();
  await assertControlTextUnclipped(send);
  await page.getByRole('button', { name: 'Preferences', exact: true }).click();
  await page.keyboard.press('Escape');
  await assertWorkspaceIdentity(page);
  await expect(composer(page)).toHaveValue('Accessible unsent draft');
  await assertNoOverflow(page);
  await accessibility(page, testInfo, 'forced-colours-reduced-motion-axe');
  await screenshot(page, testInfo, 'forced-colours-reduced-motion');
  await writeEvidence(testInfo, 'actual-combined-preference-stress', {
    originalViewport,
    combinedViewport: { width: 360, height: 800 },
    cssZoom: 2,
    appearance: 'dark',
    opaque: true,
    reducedMotion: true,
    forcedColors: 'emulated',
    actions: [
      'send',
      'stream',
      'resize',
      'stop',
      'approval Escape without decision',
      'approval reject',
      'reconnect',
    ],
    limits:
      'CSS zoom and browser preference emulation; physical keyboard/safe-area, browser-chrome zoom and OS assistive technology are separate.',
  });
});

test('actual state messages and recovery controls remain readable in every theme', async ({
  page,
}, info) => {
  test.skip(
    ![1440, 390].includes(info.project.use.viewport!.width) ||
      info.project.name.endsWith('-dark'),
    'The state matrix internally covers all eight theme pairs on desktop and phone; other viewports and duplicate base-dark projects are covered by the twenty critical flows.',
  );
  test.setTimeout(360_000);
  // The inherited Python fixture uses Path.write_text with native newlines.
  const fixtureText = `Synthetic local fixture${process.platform === 'win32' ? '\r\n' : '\n'}`;
  const observedFailures: { path: string; status: number }[] = [];
  const injectedFailures: { path: string; status: number; code: string }[] = [];
  const consoleSignatures = new Map<string, number>();
  const allowedSignatures = new Set([
    'Failed to load resource: the server responded with a status of 403 (Forbidden)',
    'Failed to load resource: the server responded with a status of 404 (Not Found)',
    'Failed to load resource: the server responded with a status of 409 (Conflict)',
    'Failed to load resource: the server responded with a status of 422 (Unprocessable Entity)',
    'Failed to load resource: the server responded with a status of 503 (Service Unavailable)',
    'Failed to load resource: net::ERR_FAILED',
  ]);
  page.on('response', (response) => {
    if (response.status() >= 400)
      observedFailures.push({
        path: new URL(response.url()).pathname,
        status: response.status(),
      });
  });
  page.on('console', (event) => {
    if (event.type() === 'error' && allowedSignatures.has(event.text()))
      consoleSignatures.set(
        event.text(),
        (consoleSignatures.get(event.text()) ?? 0) + 1,
      );
  });
  const problem = (status: number, code: string) => ({
    type: 'about:blank',
    title: 'Synthetic presentation fault',
    status,
    code,
    request_id: crypto.randomUUID(),
    retryable: false,
    recovery: 'none',
  });
  const injectOnce = async (pattern: string, status: number, code: string) => {
    let fired = false;
    await page.route(pattern, async (route) => {
      if (fired) {
        await route.fallback();
        return;
      }
      fired = true;
      injectedFailures.push({
        path: new URL(route.request().url()).pathname,
        status,
        code,
      });
      await route.fulfill({
        status,
        contentType: 'application/problem+json',
        body: JSON.stringify(problem(status, code)),
      });
    });
    return () =>
      expect(fired, `Named ${code} route must actually execute`).toBe(true);
  };
  const back = async () => {
    const button = page.getByRole('button', {
      name: 'Back to conversation',
      exact: true,
    });
    if (await button.isVisible()) await button.click();
  };
  const evidence: { appearance: string; accent: string; states: string[] }[] =
    [];
  try {
    for (const appearance of ['light', 'dark'])
      for (const accent of ['blue', 'teal', 'violet', 'amber']) {
        let conversation = await newConversation(page);
        await page
          .getByRole('button', { name: 'Preferences', exact: true })
          .click();
        const preferences = page.getByRole('dialog', {
          name: 'Preferences',
          exact: true,
        });
        await preferences
          .getByRole('combobox', { name: 'Appearance', exact: true })
          .selectOption(appearance);
        await preferences
          .getByRole('combobox', { name: 'Colour theme', exact: true })
          .selectOption(accent);
        await page.keyboard.press('Escape');
        const label = `states-${appearance}-${accent}`;
        await screenshot(page, info, `${label}-empty`);
        const unreadyPath = `**/api/v1/conversations/${conversation}/commands`;
        const unready = await injectOnce(
          unreadyPath,
          422,
          'model_configuration_required',
        );
        await composer(page).fill('Synthetic unavailable-model draft');
        await page.getByRole('button', { name: 'Send', exact: true }).click();
        await expect(
          page
            .getByRole('alert')
            .filter({ hasText: 'Choose a configured model' }),
        ).toBeVisible();
        await expect(composer(page)).toHaveValue(
          'Synthetic unavailable-model draft',
        );
        unready();
        await screenshot(page, info, `${label}-model-unavailable`);
        await page.unroute(unreadyPath);
        conversation = await newConversation(page);
        const mediaPath = '**/api/v1/attachments/*';
        const mediaFailure = await injectOnce(
          mediaPath,
          503,
          'media_unavailable',
        );
        await composer(page).fill('rich fixture');
        await page.getByRole('button', { name: 'Send', exact: true }).click();
        await expect(
          page.getByText('Synthetic tools and media are ready.', {
            exact: true,
          }),
        ).toBeVisible();
        const call = (await fixtureState(page)).calls.at(-1)!;
        await page
          .locator('summary')
          .filter({ hasText: /^Activity \(/ })
          .click();
        const retryMedia = page.getByRole('button', {
          name: 'Retry generated result',
          exact: true,
        });
        await expect(retryMedia).toBeVisible();
        mediaFailure();
        await assertControlTextUnclipped(retryMedia);
        await screenshot(page, info, `${label}-media-error`);
        await page.unroute(mediaPath);
        await retryMedia.click();
        await expect(
          page.getByRole('img', { name: 'Generated result', exact: true }),
        ).toBeVisible();
        await composer(page).fill('State review draft');
        await markWorkspaceIdentity(page);
        await addReviewResourcePair(page, `State Deck ${appearance} ${accent}`);
        await page
          .locator('.resource-chips')
          .getByRole('button', {
            name: `State Deck ${appearance} ${accent}`,
            exact: true,
          })
          .click();
        const preview = page.getByRole('region', {
          name: 'Design preview',
          exact: true,
        });
        await expect(preview.locator('iframe')).toBeVisible();
        const previewPath = `**/api/v1/conversations/${conversation}/artifacts/*/preview*`;
        let release!: () => void;
        const held = new Promise<void>((resolve) => {
          release = resolve;
        });
        await page.route(previewPath, async (route) => {
          await held;
          await route.continue();
        });
        await preview
          .getByRole('button', { name: 'Refresh preview', exact: true })
          .click();
        await expect(preview).toHaveAttribute('aria-busy', 'true');
        await screenshot(page, info, `${label}-preview-loading`);
        release();
        await expect(preview).toHaveAttribute('aria-busy', 'false');
        await page.unroute(previewPath);
        for (const [status, code] of [
          [404, 'resource_unavailable'],
          [403, 'resource_binding_revoked'],
        ] as const) {
          const fired = await injectOnce(previewPath, status, code);
          await preview
            .getByRole('button', { name: 'Refresh preview', exact: true })
            .click();
          await expect(
            preview.getByText('Preview unavailable', { exact: true }),
          ).toBeVisible();
          await expect(preview.locator('iframe')).toHaveCount(0);
          fired();
          const retry = preview.getByRole('button', {
            name: 'Reload preview',
            exact: true,
          });
          await assertControlTextUnclipped(retry);
          await screenshot(page, info, `${label}-${code}`);
          await page.unroute(previewPath);
          await retry.click();
          await expect(preview.locator('iframe')).toBeVisible();
        }
        await back();
        await page
          .locator('.resource-chips')
          .getByRole('button', { name: 'Phase 1 workspace', exact: true })
          .click();
        const inspector = page.getByRole('region', {
          name: 'Phase 1 workspace inspector',
          exact: true,
        });
        const fileButton = page.getByRole('button', {
          name: 'Preview file fixture.txt',
          exact: true,
        });
        await expect(inspector).toContainText('Folder is not a Git repository');
        await fileButton.click();
        await expect(
          page.getByLabel('File text', { exact: true }),
        ).toHaveJSProperty('textContent', fixtureText);
        const filePath = `**/api/v1/conversations/${conversation}/workspaces/*/file*`;
        const deniedFile = await injectOnce(
          filePath,
          403,
          'resource_binding_revoked',
        );
        // Reload the same populated file, so this proves cached content clears.
        await fileButton.click();
        await expect(
          page.getByText('Workspace access changed', { exact: true }),
        ).toBeVisible();
        await expect(
          page.getByText(
            'This workspace is unavailable or access changed. Review its binding and permissions, then retry. Your conversation is preserved.',
            { exact: true },
          ),
        ).toBeVisible();
        deniedFile();
        await expect(page.getByLabel('File text', { exact: true })).toHaveCount(
          0,
        );
        await expect(inspector).toHaveCount(0);
        await expect(fileButton).toHaveCount(0);
        const retryInspector = page.getByRole('button', {
          name: 'Retry inspector',
          exact: true,
        });
        await assertControlTextUnclipped(retryInspector);
        await screenshot(page, info, `${label}-inspector-binding-revoked`);
        await page.unroute(filePath);
        await retryInspector.click();
        await expect(inspector).toContainText('Folder is not a Git repository');
        await fileButton.click();
        await expect(
          page.getByLabel('File text', { exact: true }),
        ).toHaveJSProperty('textContent', fixtureText);
        await screenshot(page, info, `${label}-inspector-recovered`);
        await back();
        await assertWorkspaceIdentity(page);
        await expect(composer(page)).toHaveValue('State review draft');
        await releaseProducer(page, call);
        await expect
          .poll(
            async () =>
              (await fixtureState(page)).calls.find(
                (item) => item.generation_id === call.generation_id,
              )?.quiesced,
          )
          .toBe(true);
        await composer(page).fill('approval fixture');
        await page.getByRole('button', { name: 'Send', exact: true }).click();
        const review = page.getByRole('button', {
          name: 'Review approval',
          exact: true,
        });
        await expect(review).toBeVisible();
        const approvalPath = '**/api/v1/approvals/*';
        let releaseApproval!: () => void;
        const heldApproval = new Promise<void>((resolve) => {
          releaseApproval = resolve;
        });
        await page.route(approvalPath, async (route) => {
          await heldApproval;
          await route.continue();
        });
        await review.click();
        let approval = page.getByRole('dialog', {
          name: 'Approval required',
          exact: true,
        });
        await expect(
          approval.getByLabel('Loading current approval', { exact: true }),
        ).toBeVisible();
        await screenshot(page, info, `${label}-approval-loading`);
        releaseApproval();
        await expect(
          approval.getByRole('button', { name: 'Reject action', exact: true }),
        ).toBeVisible();
        await page.unroute(approvalPath);
        await page.keyboard.press('Escape');
        const expired = await injectOnce(approvalPath, 409, 'approval_expired');
        await review.click();
        approval = page.getByRole('dialog', {
          name: 'Approval required',
          exact: true,
        });
        await expect(approval.getByRole('alert')).toContainText(
          'This approval expired',
        );
        await expect(
          approval.getByRole('button', { name: 'Approve action', exact: true }),
        ).toHaveCount(0);
        expired();
        await screenshot(page, info, `${label}-approval-expired`);
        await page.keyboard.press('Escape');
        await page.unroute(approvalPath);
        await review.click();
        approval = page.getByRole('dialog', {
          name: 'Approval required',
          exact: true,
        });
        const reject = approval.getByRole('button', {
          name: 'Reject action',
          exact: true,
        });
        await assertControlTextUnclipped(reject);
        await reject.click();
        await expect(
          page.getByText('Synthetic approval rejected.', { exact: true }),
        ).toHaveCount(1);
        await screenshot(page, info, `${label}-approval-rejected`);
        const commands = `**/api/v1/conversations/${conversation}/commands`;
        let lost = false;
        let lostResponseFinished = false;
        await page.route(commands, async (route) => {
          const body = route.request().postDataJSON();
          if (!lost && body.type === 'conversation.submit') {
            lost = true;
            const response = await route.fetch();
            expect(response.ok()).toBe(true);
            await route.abort('failed');
            lostResponseFinished = true;
          } else await route.continue();
        });
        const count = (await fixtureState(page)).calls.length;
        await composer(page).fill('stop fixture');
        await page.getByRole('button', { name: 'Send', exact: true }).click();
        const receipt = page.getByRole('button', {
          name: 'Check request receipt',
          exact: true,
        });
        await expect.poll(() => lostResponseFinished).toBe(true);
        await expect(receipt).toBeVisible();
        expect(lost).toBe(true);
        await assertControlTextUnclipped(receipt);
        await screenshot(page, info, `${label}-unknown-response`);
        await page.unroute(commands);
        await receipt.click();
        await expect(receipt).toHaveCount(0);
        await expect
          .poll(async () => (await fixtureState(page)).calls.length)
          .toBe(count + 1);
        await page.getByRole('button', { name: 'Stop', exact: true }).click();
        await expect
          .poll(async () => (await fixtureState(page)).calls.at(-1)?.quiesced)
          .toBe(true);
        await expect(composer(page)).toBeEnabled();
        await assertNoOverflow(page);
        await screenshot(page, info, `${label}-receipt-confirmed`);
        await accessibility(page, info, `${label}-axe`, {
          opaquePreview: true,
        });
        evidence.push({
          appearance,
          accent,
          states: [
            'empty',
            'unready retained draft',
            'media error/retry',
            'preview loading',
            'missing resource',
            'revoked binding clears private frame',
            'revoked Inspector clears populated file and all cached sections; explicit retry recovers',
            'approval loading',
            'expired approval',
            'rejected approval',
            'lost accepted response/receipt without duplicate',
          ],
        });
      }
  } finally {
    for (const [signature, count] of consoleSignatures)
      info.annotations.push({
        type: 'expected-console-error',
        description: JSON.stringify({
          signature,
          count,
          owner: 'Phase 3 independent QA',
          fixture:
            'Named bounded public route presentation errors and deliberately lost accepted command response; exact failed HTTP path/status inventory recorded separately',
        }),
      });
    await writeEvidence(info, 'state-matrix-injected-failures', {
      injectedFailures,
      observedFailures,
      consoleSignatures: [...consoleSignatures],
      evidence,
      scope:
        'Presentation faults only. Backend access/approval/receipt safety is independently tested with actual owners. No private headers/bodies or auth proofs recorded.',
    });
  }
  expect(observedFailures).toEqual(
    injectedFailures.map(({ path, status }) => ({ path, status })),
  );
  expect(evidence).toHaveLength(8);
});
