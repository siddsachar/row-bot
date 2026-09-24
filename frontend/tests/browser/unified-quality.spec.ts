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
  test.setTimeout(600_000);
  const conversation = await newConversation(page);
  await composer(page).fill('rich fixture');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await expect(
    page.getByText('Synthetic tools and media are ready.', { exact: true }),
  ).toBeVisible();
  const call = (await fixtureState(page)).calls.at(-1)!;
  await releaseProducer(page, call);
  await expect
    .poll(
      async () =>
        (await fixtureState(page)).calls.find(
          (item) => item.generation_id === call.generation_id,
        )?.quiesced,
    )
    .toBe(true);
  await page
    .locator('summary')
    .filter({ hasText: /^Activity \(/ })
    .click();
  await expect(
    page.getByRole('img', { name: 'Generated result', exact: true }),
  ).toBeVisible();
  await composer(page).fill('Retained while adjusting appearance');
  await addReviewResourcePair(page, 'Theme review Deck');
  const observations: { appearance: string; accent: string }[] = [];
  try {
    for (const appearance of ['light', 'dark']) {
      for (const accent of ['blue', 'teal', 'violet', 'amber']) {
        await page.goto('/app-v2/settings/preferences');
        await page.getByText('Local client controls', { exact: true }).click();
        await page
          .getByRole('combobox', { name: 'Appearance', exact: true })
          .selectOption(appearance);
        await page
          .getByRole('combobox', { name: 'Colour theme', exact: true })
          .selectOption(accent);
        await page.goto(`/app-v2/conversations/${conversation}`);
        await expect(page.locator('html')).toHaveAttribute(
          'data-theme',
          appearance,
        );
        await expect(page.locator('html')).toHaveAttribute(
          'data-accent',
          accent,
        );
        await expect(composer(page)).toHaveValue(
          'Retained while adjusting appearance',
        );
        await markWorkspaceIdentity(page);
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
      }
    }
    await page.goto('/app-v2/settings/preferences');
    await page.getByText('Local client controls', { exact: true }).click();
    await page
      .getByRole('combobox', { name: 'Appearance', exact: true })
      .selectOption('system');
    await page.goto(`/app-v2/conversations/${conversation}`);
    for (const scheme of ['dark', 'light', 'dark'] as const) {
      await page.emulateMedia({ colorScheme: scheme });
      await expect(page.locator('html')).toHaveAttribute('data-theme', scheme);
      await expect(page).toHaveURL(
        new RegExp(`/conversations/${conversation}$`),
      );
    }
    await expect(page).toHaveURL(new RegExp(`/conversations/${conversation}$`));
    await expect(composer(page)).toHaveValue(
      'Retained while adjusting appearance',
    );
    const responseReading = await page
      .getByRole('log', { name: 'Conversation', exact: true })
      .locator('.message-assistant .message-text')
      .filter({ hasText: 'Synthetic tools and media are ready.' })
      .evaluate((element) => ({
        fontSize: getComputedStyle(element).fontSize,
        lineHeight: getComputedStyle(element).lineHeight,
      }));
    expect(responseReading).toEqual({ fontSize: '16px', lineHeight: '24px' });
    await writeEvidence(testInfo, 'actual-response-reading-type', responseReading);
    await screenshot(page, testInfo, 'settled-tools-media');
    await writeEvidence(testInfo, 'appearance-after-generation', {
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
  const conversation = await newConversation(page);
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
  const approval = page.getByRole('complementary', {
    name: 'Approval required for fixture_action',
    exact: true,
  });
  const details = approval.getByRole('button', {
    name: 'Details',
    exact: true,
  });
  await assertControlTextUnclipped(details);
  await details.click();
  const dialog = page.getByRole('dialog', {
    name: 'Approval details · fixture_action',
    exact: true,
  });
  await expect(dialog).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);
  await expect(details).toBeFocused();
  const beforeDecision = (await fixtureState(page)).calls.length;
  const reject = approval.getByRole('button', {
    name: 'Reject',
    exact: true,
  });
  const approve = approval.getByRole('button', {
    name: 'Approve',
    exact: true,
  });
  await assertControlTextUnclipped(approve);
  await assertControlTextUnclipped(reject);
  await screenshot(page, testInfo, 'combined-stress-approval-footer');
  await reject.click();
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
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await expect(page).toHaveURL(/\/settings\/providers$/);
  await page.goBack();
  await expect(page).toHaveURL(new RegExp(`/conversations/${conversation}$`));
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

test('actual state messages and recovery controls remain readable in light and dark', async ({
  page,
}, info) => {
  test.skip(
    ![1440, 390].includes(info.project.use.viewport!.width) ||
      info.project.name.endsWith('-dark'),
    'The state matrix covers light and dark on desktop and phone; other viewports and accents are covered by visual and theme journeys.',
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
  const openPanel = async (name: string) => {
    const tab = page.getByRole('tab', { name, exact: true });
    if (await tab.isVisible()) {
      await tab.click();
      return;
    }
    await page
      .getByRole('complementary', { name: 'Panel rail' })
      .getByRole('button', { name, exact: true })
      .click();
  };
  const evidence: { appearance: string; accent: string; states: string[] }[] =
    [];
  try {
    await page.goto('/app-v2/');
    for (const appearance of ['light', 'dark'])
      for (const accent of ['blue']) {
        await page.evaluate(
          ({ appearance, accent }) => {
            const key = 'row-bot.appearance.v1';
            const saved = JSON.parse(localStorage.getItem(key) || '{}');
            localStorage.setItem(
              key,
              JSON.stringify({ ...saved, version: 1, appearance, accent }),
            );
          },
          { appearance, accent },
        );
        let conversation = await newConversation(page);
        await expect(page.locator('html')).toHaveAttribute(
          'data-theme',
          appearance,
        );
        await expect(page.locator('html')).toHaveAttribute(
          'data-accent',
          accent,
        );
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
        await composer(page).fill('rich fixture');
        await page.getByRole('button', { name: 'Send', exact: true }).click();
        await expect(
          page.getByText('Synthetic tools and media are ready.', {
            exact: true,
          }),
        ).toBeVisible();
        const call = (await fixtureState(page)).calls.at(-1)!;
        await releaseProducer(page, call);
        await expect
          .poll(
            async () =>
              (await fixtureState(page)).calls.find(
                (item) => item.generation_id === call.generation_id,
              )?.quiesced,
          )
          .toBe(true);
        await page
          .locator('summary')
          .filter({ hasText: /^Activity \(/ })
          .click();
        await expect(
          page.getByRole('img', { name: 'Generated result', exact: true }),
        ).toBeVisible();
        await screenshot(page, info, `${label}-generated-media`);
        await composer(page).fill('State review draft');
        await markWorkspaceIdentity(page);
        await addReviewResourcePair(page, `State Deck ${appearance} ${accent}`);
        await openPanel(`State Deck ${appearance} ${accent}`);
        const preview = page.getByRole('region', {
          name: 'Design preview',
          exact: true,
        });
        await expect(preview.locator('iframe')).toBeVisible();
        const refreshPreview = preview.getByRole('button', {
          name: 'Refresh preview',
          exact: true,
        });
        // Opening a freshly created resource may still be applying its saved
        // revision after the first iframe appears. Intercept only after that
        // owner-driven refresh settles, otherwise this test holds the request
        // that must enable the very button it is about to click.
        await expect(preview).toHaveAttribute('aria-busy', 'false');
        await expect(refreshPreview).toBeEnabled();
        const previewPath = `**/api/v1/conversations/${conversation}/artifacts/*/preview*`;
        let release!: () => void;
        const held = new Promise<void>((resolve) => {
          release = resolve;
        });
        await page.route(previewPath, async (route) => {
          await held;
          await route.continue();
        });
        await refreshPreview.click();
        await expect(preview).toHaveAttribute('aria-busy', 'true');
        const refreshExplanation = preview.getByText(
          'The saved preview is refreshing. Refresh preview is available again once this request settles.',
          { exact: true },
        );
        await expect(refreshExplanation).toBeVisible();
        await expect(refreshExplanation).toHaveAttribute('role', 'status');
        await expect(refreshPreview).toHaveAttribute(
          'aria-describedby',
          'design-preview-refresh-status',
        );
        await screenshot(page, info, `${label}-preview-loading`);
        release();
        await expect(preview).toHaveAttribute('aria-busy', 'false');
        await expect(refreshExplanation).toHaveCount(0);
        await expect(refreshPreview).toBeEnabled();
        await expect(refreshPreview).not.toHaveAttribute('aria-describedby');
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
        await openPanel('Phase 1 workspace');
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
        conversation = await newConversation(page);
        const approvalPath = '**/api/v1/approvals/*';
        let releaseApproval!: () => void;
        const heldApproval = new Promise<void>((resolve) => {
          releaseApproval = resolve;
        });
        await page.route(approvalPath, async (route) => {
          await heldApproval;
          await route.continue();
        });
        await composer(page).fill('approval fixture');
        await page.getByRole('button', { name: 'Send', exact: true }).click();
        let approval = page.locator('.approval-bar');
        await expect(approval).toBeVisible();
        await expect(
          approval.getByLabel('Loading current approval', { exact: true }),
        ).toBeVisible();
        await screenshot(page, info, `${label}-approval-loading`);
        releaseApproval();
        await expect(
          approval.getByRole('button', { name: 'Reject', exact: true }),
        ).toBeVisible();
        await page.unroute(approvalPath);
        conversation = await newConversation(page);
        const expired = await injectOnce(approvalPath, 409, 'approval_expired');
        await composer(page).fill('approval fixture');
        await page.getByRole('button', { name: 'Send', exact: true }).click();
        approval = page.locator('.approval-bar');
        await expect(approval.getByRole('alert')).toContainText(
          'This approval expired',
        );
        await expect(
          approval.getByRole('button', { name: 'Approve', exact: true }),
        ).toHaveCount(0);
        expired();
        await screenshot(page, info, `${label}-approval-expired`);
        await page.unroute(approvalPath);
        conversation = await newConversation(page);
        await composer(page).fill('approval fixture');
        await page.getByRole('button', { name: 'Send', exact: true }).click();
        approval = page.locator('.approval-bar');
        const reject = approval.getByRole('button', {
          name: 'Reject',
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
            'generated media',
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
  expect(evidence).toHaveLength(2);
});
