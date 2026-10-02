import type { Page, Request, TestInfo } from '@playwright/test';
import {
  accessibility,
  assertNoOverflow,
  expect,
  screenshot,
  test,
  writeEvidence,
} from './evidence';
import { blockFixtureServiceWorkers } from './unified-helpers';

// Navigation order: General · Models · Knowledge · Capabilities ·
// Connections · Agents · System.
const settingsRoutes = [
  ['preferences', 'Preferences'],
  ['appearance', 'Appearance'],
  ['buddy', 'Buddy'],
  ['providers', 'Providers'],
  ['models', 'Models'],
  ['voice', 'Voice'],
  ['knowledge', 'Memory'],
  ['documents', 'Documents'],
  ['tracker', 'Tracker'],
  ['tools', 'Tools'],
  ['integrations', 'Integrations'],
  ['accounts', 'Accounts'],
  ['channels', 'Channels'],
  ['system', 'System'],
  ['access', 'Devices & remote access'],
  ['updates', 'Updates'],
  ['data', 'Data'],
] as const;

const firefoxPhoneRoutes = new Set([
  'providers',
  'system',
  'integrations',
  'preferences',
]);

// Legacy ids and moved pages redirect to their new page (and row).
const aliases = [
  ['skills', 'integrations', 'Integrations'],
  ['plugins', 'integrations', 'Integrations'],
  ['mcp', 'integrations', 'Integrations'],
  ['wiki', 'knowledge', 'Memory'],
  ['cloud', 'providers', 'Providers'],
  ['google', 'accounts', 'Accounts'],
  ['gmail', 'accounts', 'Accounts'],
  ['calendar', 'accounts', 'Accounts'],
  ['migration', 'data', 'Data'],
  ['search', 'tools', 'Tools'],
  ['utilities', 'tools', 'Tools'],
] as const;

function settingsPath(id: string): string {
  return `/app-v2/settings/${id}`;
}

// In-app navigation: every page.goto opens a fixture session, and the gate
// shares 256 of them, so repeated redirect checks stay in one page load.
async function navigateInApp(page: Page, path: string): Promise<void> {
  await page.evaluate((target) => {
    history.pushState(history.state, '', target);
    dispatchEvent(new PopStateEvent('popstate', { state: history.state }));
  }, path);
}

async function useLightBlueCompact(page: Page): Promise<void> {
  await page.addInitScript(() => {
    localStorage.setItem(
      'row-bot.appearance.v1',
      JSON.stringify({
        version: 1,
        appearance: 'light',
        accent: 'blue',
        density: 'compact',
        reduce_transparency: false,
      }),
    );
  });
}

async function waitForSettings(page: Page, label: string): Promise<void> {
  await expect(
    page.getByRole('region', { name: 'Settings', exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole('heading', { name: 'Settings', exact: true, level: 1 }),
  ).toBeVisible();
  await expect(
    page.getByRole('heading', { name: label, exact: true, level: 2 }).first(),
  ).toBeVisible();
  await expect(
    page.getByRole('region', { name: label, exact: true }).first(),
  ).toBeVisible();
  await expect
    .poll(() =>
      page.locator('.settings-page-content [aria-busy="true"]').evaluateAll(
        (elements) =>
          elements.filter((element) => {
            const style = getComputedStyle(element);
            return style.display !== 'none' && style.visibility !== 'hidden';
          }).length,
      ),
    )
    .toBe(0);
}

function observeConsequentialRequests(page: Page): {
  requests: { method: string; path: string }[];
  stop: () => void;
} {
  const requests: { method: string; path: string }[] = [];
  const observe = (request: Request) => {
    const url = new URL(request.url());
    if (
      url.pathname.startsWith('/api/v1/') &&
      !['GET', 'HEAD', 'OPTIONS'].includes(request.method()) &&
      !(request.method() === 'POST' && url.pathname === '/api/v1/handshake')
    )
      requests.push({ method: request.method(), path: url.pathname });
  };
  page.on('request', observe);
  return { requests, stop: () => page.off('request', observe) };
}

async function expectCoarseTargets(page: Page, info: TestInfo): Promise<void> {
  const geometry = await page.locator('.settings-shell').evaluate((root) => {
    const seen = new Set<Element>();
    return [
      ...root.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), summary',
      ),
    ]
      .map((element) => {
        const target = element.matches(
          'input[type="checkbox"], input[type="radio"]',
        )
          ? (element.closest('label') ?? element)
          : element;
        if (seen.has(target)) return null;
        seen.add(target);
        const box = target.getBoundingClientRect();
        const style = getComputedStyle(target);
        if (
          box.width <= 0 ||
          box.height <= 0 ||
          style.display === 'none' ||
          style.visibility === 'hidden'
        )
          return null;
        return {
          tag: target.tagName.toLowerCase(),
          name:
            target.getAttribute('aria-label') ||
            target.textContent?.trim().replace(/\s+/g, ' ').slice(0, 100) ||
            target.getAttribute('name') ||
            '',
          width: Math.round(box.width * 10) / 10,
          height: Math.round(box.height * 10) / 10,
        };
      })
      .filter((item): item is NonNullable<typeof item> => item !== null);
  });
  await writeEvidence(info, 'settings-coarse-target-geometry', geometry);
  expect(
    geometry.filter((item) => item.width < 44 || item.height < 44),
    'Visible Settings controls must retain a 44px coarse-pointer target',
  ).toEqual([]);
}

test.use({ serviceWorkers: 'allow' });

test('Settings groups list every page, and search finds pages and rows', async ({
  browserName,
  context,
  page,
}, info) => {
  test.skip(
    browserName !== 'chromium' || info.project.use.viewport!.width !== 1440,
    'One Chromium desktop pass checks the sidebar and compact picker.',
  );
  await blockFixtureServiceWorkers(context);
  await useLightBlueCompact(page);
  await page.goto(settingsPath('documents'));
  await waitForSettings(page, 'Documents');
  const navigation = page.getByRole('navigation', {
    name: 'Settings sections',
    exact: true,
  });
  await expect(
    navigation
      .getByRole('list', { name: 'Knowledge', exact: true })
      .getByRole('link'),
  ).toHaveText(['Memory', 'Documents', 'Tracker']);
  await expect(
    navigation.getByRole('link', { name: 'Documents', exact: true }),
  ).toHaveAttribute('aria-current', 'page');
  await expect(
    navigation.getByRole('link', { name: 'Providers', exact: true }),
  ).toBeVisible();
  await assertNoOverflow(page);
  await screenshot(page, info, 'settings-category-deep-link-wide');

  // "/" focuses search; a page result navigates, a row result jumps to it.
  await page
    .getByRole('heading', { name: 'Documents', exact: true, level: 2 })
    .click();
  await page.keyboard.press('/');
  const search = navigation.getByRole('searchbox', { name: 'Find a setting' });
  await expect(search).toBeFocused();
  await search.fill('gmail');
  const accounts = navigation
    .getByRole('list', { name: 'Matching pages', exact: true })
    .getByRole('link', { name: 'Accounts', exact: true });
  await expect(accounts).toBeVisible();
  await accounts.focus();
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(new RegExp(`${settingsPath('accounts')}$`));
  await waitForSettings(page, 'Accounts');
  await search.fill('dream');
  await navigation
    .getByRole('list', { name: 'Matching settings', exact: true })
    .getByRole('link', { name: 'Dream Cycle', exact: true })
    .click();
  await expect(page).toHaveURL(
    new RegExp(`${settingsPath('preferences')}#dream-cycle$`),
  );
  await waitForSettings(page, 'Preferences');
  await expect(
    page.locator('[data-setting-anchor="dream-cycle"]'),
  ).toBeInViewport();
  await assertNoOverflow(page);
  await screenshot(page, info, 'settings-category-search-wide');

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(settingsPath('providers'));
  await waitForSettings(page, 'Providers');
  // Compact: the picker replaces the page links; the search stays.
  await expect(navigation.getByRole('link')).toHaveCount(0);
  await expect(
    navigation.getByRole('searchbox', { name: 'Find a setting' }),
  ).toBeVisible();
  const picker = page.getByRole('combobox', { name: 'Settings section' });
  await picker.focus();
  await page.keyboard.press('ArrowDown');
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(new RegExp(`${settingsPath('models')}$`));
  await waitForSettings(page, 'Models');
  await assertNoOverflow(page);
  await screenshot(page, info, 'settings-picker-keyboard-narrow');
});

test('Settings shell preserves the exact owner order aliases history and reload', async ({
  browserName,
  context,
  page,
}, info) => {
  test.skip(
    ![1440, 390].includes(info.project.use.viewport!.width),
    'The dedicated Settings lane covers the required desktop and phone sizes.',
  );
  await blockFixtureServiceWorkers(context);
  await useLightBlueCompact(page);
  await page.goto('/app-v2/settings');
  await expect(page).toHaveURL(new RegExp(`${settingsPath('providers')}$`));
  await waitForSettings(page, 'Providers');

  const expectedLabels = settingsRoutes.map(([, label]) => label);
  if (info.project.use.viewport!.width >= 1024) {
    const navigation = page.getByRole('navigation', {
      name: 'Settings sections',
      exact: true,
    });
    await expect(navigation).toBeVisible();
    await expect(navigation.getByRole('heading')).toHaveCount(0);
    const groups = navigation.getByRole('list');
    await expect(groups).toHaveCount(6);
    expect(
      await groups.evaluateAll((lists) =>
        lists.map((list) => list.getAttribute('aria-label')),
      ),
    ).toEqual([
      'General',
      'Models',
      'Knowledge',
      'Capabilities',
      'Connections',
      'System',
    ]);
    await expect(navigation.getByRole('link')).toHaveText(expectedLabels);
  } else {
    const picker = page.getByRole('combobox', {
      name: 'Settings section',
      exact: true,
    });
    await expect(picker).toBeVisible();
    await expect(picker.locator('option')).toHaveText(expectedLabels);
  }

  // The first alias is a fresh deep link; the rest redirect in the app.
  for (const [index, [alias, destination, label]] of aliases.entries()) {
    if (index === 0) await page.goto(settingsPath(alias));
    else await navigateInApp(page, settingsPath(alias));
    await expect
      .poll(() => new URL(page.url()).pathname)
      .toBe(settingsPath(destination));
    await waitForSettings(page, label);
    if (['skills', 'plugins', 'mcp'].includes(alias)) {
      expect(new URL(page.url()).searchParams.get('type')).toBe(
        alias === 'skills' ? 'skill' : alias === 'plugins' ? 'plugin' : 'mcp',
      );
    }
  }
  // Goals belong to one conversation: the old page opens a conversation.
  await navigateInApp(page, settingsPath('goals'));
  await expect
    .poll(() => new URL(page.url()).pathname)
    .not.toContain('/settings');
  // Agent profiles are the sidebar's Agents dialog: old links open it (B260).
  for (const old of ['profiles', 'agent-profiles']) {
    await navigateInApp(page, settingsPath(old));
    const agents = page.getByRole('dialog', { name: 'Agent profiles' });
    await expect(agents).toBeVisible();
    await expect
      .poll(() => new URL(page.url()).pathname)
      .not.toContain('/settings');
    await page.keyboard.press('Escape');
    await expect(agents).toHaveCount(0);
  }

  await page.goto(settingsPath('providers'));
  await page.goto(settingsPath('models'));
  await page.goto(settingsPath('accounts'));
  await page.goBack();
  await expect(page).toHaveURL(new RegExp(`${settingsPath('models')}$`));
  await waitForSettings(page, 'Models');
  await page.goForward();
  await expect(page).toHaveURL(new RegExp(`${settingsPath('accounts')}$`));
  await waitForSettings(page, 'Accounts');
  await page.reload();
  await expect(page).toHaveURL(new RegExp(`${settingsPath('accounts')}$`));
  await waitForSettings(page, 'Accounts');
  await assertNoOverflow(page);
  await accessibility(page, info, 'settings-shell-navigation-axe');
  await screenshot(page, info, 'settings-shell-navigation');
  await writeEvidence(info, 'settings-route-contract', {
    order: settingsRoutes.map(([id]) => id),
    aliases: Object.fromEntries(
      aliases.map(([alias, destination]) => [alias, destination]),
    ),
    history: [
      'providers',
      'models',
      'accounts',
      'back:models',
      'forward:accounts',
    ],
    reload: 'accounts',
    browser: browserName,
  });
});

for (const [id, label] of settingsRoutes) {
  test(`${label} Settings deep parity route is passive accessible and overflow-safe`, async ({
    browserName,
    context,
    page,
  }, info) => {
    test.skip(
      ![1440, 390].includes(info.project.use.viewport!.width),
      'The dedicated Settings lane covers the required desktop and phone sizes.',
    );
    test.skip(
      browserName === 'firefox' && !firefoxPhoneRoutes.has(id),
      'Firefox phone smoke is scoped to the shell, Providers, System, MCP, and Preferences.',
    );
    await blockFixtureServiceWorkers(context);
    await useLightBlueCompact(page);
    const observation = observeConsequentialRequests(page);
    try {
      await page.goto(settingsPath(id));
      await waitForSettings(page, label);
      await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
      await expect(page.locator('html')).toHaveAttribute('data-accent', 'blue');
      await expect(page.locator('html')).toHaveAttribute(
        'data-density',
        'compact',
      );
      await assertNoOverflow(page);
      const close = page.getByRole('link', {
        name: 'Close settings',
        exact: true,
      });
      await close.focus();
      await page.keyboard.press('Tab');
      expect(
        await page.locator('.settings-shell').evaluate((root) => {
          const active = document.activeElement;
          return !!active && active !== document.body && root.contains(active);
        }),
        `${label} must retain keyboard focus inside the Settings shell`,
      ).toBe(true);
      if (info.project.use.viewport!.width === 390) {
        // One header on phones (B119): Settings carries the navigation
        // toggle and commands instead of a second bar above it.
        await expect(page.locator('.compact-controls')).toHaveCount(0);
        await expect(
          page
            .locator('.settings-shell-header')
            .getByRole('button', { name: 'Toggle navigation' }),
        ).toBeVisible();
        await expectCoarseTargets(page, info);
      }
      await accessibility(page, info, `settings-${id}-axe`);
      await screenshot(page, info, `settings-${id}-light-blue-compact`);
    } finally {
      observation.stop();
      await writeEvidence(info, `settings-${id}-request-boundary`, {
        consequential_requests: observation.requests,
        allowed_render_requests: 'GET/HEAD/OPTIONS and session handshake only',
      });
    }
    expect(
      observation.requests,
      `${label} must not issue a consequential API request while rendering`,
    ).toEqual([]);
  });
}

test('Preferences snapshot owner reviews cancels saves receipts and reloads one local setting', async ({
  browserName,
  context,
  page,
}, info) => {
  test.skip(
    browserName !== 'chromium' || info.project.use.viewport!.width !== 1440,
    'One Chromium desktop pass owns the representative snapshot mutation.',
  );
  await blockFixtureServiceWorkers(context);
  await useLightBlueCompact(page);
  await page.goto(settingsPath('preferences'));
  await waitForSettings(page, 'Preferences');

  const name = page.getByRole('textbox', { name: 'Name', exact: true });
  await expect(name).toBeVisible();
  const original = await name.inputValue();
  const replacement =
    original === 'Synthetic Settings Browser'
      ? 'Synthetic Settings Browser Reloaded'
      : 'Synthetic Settings Browser';
  const owner = page.locator('.settings-saved-control').filter({ has: name });
  const commandRequests: string[] = [];
  const recordCommand = (request: Request) => {
    const path = new URL(request.url()).pathname;
    if (
      request.method() === 'POST' &&
      (path === '/api/v1/settings/snapshot/review' ||
        path === '/api/v1/settings/snapshot/commands')
    )
      commandRequests.push(path);
  };
  page.on('request', recordCommand);
  try {
    // Decision 19: no Save or Revert. Escape is the non-mutating path: it
    // puts the saved value back without a review or a command.
    await name.fill(replacement);
    await expect(
      owner.getByRole('button', { name: 'Save', exact: true }),
    ).toHaveCount(0);
    await expect(owner.getByRole('button', { name: /^Revert/ })).toHaveCount(0);
    await name.press('Escape');
    await expect(name).toHaveValue(original);
    expect(commandRequests).toEqual([]);

    await name.fill(replacement);
    await assertNoOverflow(page);
    await accessibility(page, info, 'settings-snapshot-review-axe');
    await screenshot(page, info, 'settings-snapshot-review');

    // Save is reviewed by the server and committed in one step.
    const reviewed = page.waitForResponse(
      (response) =>
        response.request().method() === 'POST' &&
        new URL(response.url()).pathname === '/api/v1/settings/snapshot/review',
    );
    const saved = page.waitForResponse(
      (response) =>
        response.request().method() === 'POST' &&
        new URL(response.url()).pathname ===
          '/api/v1/settings/snapshot/commands',
    );
    // Enter (or leaving the field) saves: reviewed and committed in one step.
    await name.press('Enter');
    expect((await reviewed).ok()).toBe(true);
    const saveResponse = await saved;
    expect(saveResponse.ok()).toBe(true);
    const receipt = (await saveResponse.json()) as {
      command_id: string;
      status: string;
      snapshot?: { preferences?: { identity?: { name?: string } } };
    };
    expect(receipt.status).toBe('completed');
    expect(receipt.snapshot?.preferences?.identity?.name).toBe(replacement);
    // The save is confirmed by the floating notice, with Undo (B258).
    const savedNotice = page
      .locator('.toast')
      .filter({ hasText: 'Name saved' });
    await expect(savedNotice).toBeVisible();
    await expect(
      savedNotice.getByRole('button', { name: 'Undo', exact: true }),
    ).toBeVisible();
    expect(commandRequests).toEqual([
      '/api/v1/settings/snapshot/review',
      '/api/v1/settings/snapshot/commands',
    ]);

    // Read the durable receipt with the already authenticated synthetic
    // browser session. The proof remains local to this closure and is never
    // written to evidence.
    const requestHeaders = await saveResponse.request().allHeaders();
    const receiptResponse = await page.request.get(
      `/api/v1/settings/snapshot/commands/${receipt.command_id}`,
      {
        headers: {
          'X-Client-Session': requestHeaders['x-client-session'],
          'X-CSRF-Token': requestHeaders['x-csrf-token'],
        },
      },
    );
    expect(receiptResponse.ok()).toBe(true);
    const durableReceipt = (await receiptResponse.json()) as {
      command_id: string;
      status: string;
    };
    expect(durableReceipt).toMatchObject({
      command_id: receipt.command_id,
      status: 'completed',
    });

    await page.reload();
    await waitForSettings(page, 'Preferences');
    await expect(
      page.getByRole('textbox', { name: 'Name', exact: true }),
    ).toHaveValue(replacement);
    await writeEvidence(info, 'settings-snapshot-mutation-result', {
      page: 'preferences',
      field: 'identity.name',
      revert_without_request: true,
      command_count: commandRequests.length,
      receipt_status: durableReceipt.status,
      reload_persisted: true,
      secret_material_recorded: false,
    });
  } finally {
    page.off('request', recordCommand);
  }
});

test('Keyboard-only Settings traversal reaches navigation and local controls', async ({
  browserName,
  context,
  page,
}, info) => {
  test.skip(
    browserName !== 'chromium' || info.project.use.viewport!.width !== 1440,
    'One Chromium desktop pass owns the keyboard traversal.',
  );
  await blockFixtureServiceWorkers(context);
  await useLightBlueCompact(page);
  await page.goto(settingsPath('models'));
  await waitForSettings(page, 'Models');

  const close = page.getByRole('link', { name: 'Close settings', exact: true });
  await close.focus();
  await expect(close).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(
    page.getByRole('searchbox', { name: 'Find a setting' }),
  ).toBeFocused();
  // Every page link follows in navigation order.
  const nav = page.getByRole('navigation', {
    name: 'Settings sections',
    exact: true,
  });
  for (const name of ['Preferences', 'Appearance', 'Buddy', 'Providers']) {
    await page.keyboard.press('Tab');
    await expect(nav.getByRole('link', { name, exact: true })).toBeFocused();
  }
  await page.keyboard.press('Tab');
  const models = nav.getByRole('link', { name: 'Models', exact: true });
  await expect(models).toBeFocused();
  await page.keyboard.press('Enter');
  await waitForSettings(page, 'Models');

  // The catalog's job chips work from the keyboard (B229).
  const chips = page.getByRole('group', { name: 'Model category' });
  const vision = chips.getByRole('button', { name: 'Vision', exact: true });
  await vision.focus();
  await expect(vision).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(vision).toHaveAttribute('aria-pressed', 'true');
  await expect(
    chips.getByRole('button', { name: 'Chat', exact: true }),
  ).toHaveAttribute('aria-pressed', 'false');

  await page.goto(settingsPath('appearance'));
  await waitForSettings(page, 'Appearance');
  const appearance = page.getByRole('combobox', {
    name: 'Appearance',
    exact: true,
  });
  await appearance.focus();
  await expect(appearance).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(
    page.getByRole('combobox', { name: 'Colour theme', exact: true }),
  ).toBeFocused();

  const restore = page.getByRole('button', {
    name: 'Reset layout',
    exact: true,
  });
  await restore.focus();
  await expect(restore).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.getByText('Layout reset', { exact: true })).toBeVisible();
  await assertNoOverflow(page);
  await accessibility(page, info, 'settings-keyboard-axe');
  await screenshot(page, info, 'settings-keyboard-focus-restored');
});

test('Leaving Tools takes its sections with it (B183)', async ({
  browserName,
  context,
  page,
}, info) => {
  test.skip(
    browserName !== 'chromium' || info.project.use.viewport!.width < 900,
    'The settings link column exists from 900px.',
  );
  await blockFixtureServiceWorkers(context);
  const duplicateKeys: string[] = [];
  page.on('console', (message) => {
    if (/same key/i.test(message.text())) duplicateKeys.push(message.text());
  });
  await page.goto(settingsPath('tools'));
  await waitForSettings(page, 'Tools');
  await expect(
    page.getByRole('heading', { name: 'Custom tools', exact: true }),
  ).toBeVisible();
  await page
    .getByRole('navigation', { name: 'Settings sections', exact: true })
    .getByRole('link', { name: 'Data', exact: true })
    .click();
  await waitForSettings(page, 'Data');
  await expect(
    page.getByRole('heading', { name: 'Back up and restore', exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole('heading', { name: 'Custom tools', exact: true }),
  ).toHaveCount(0);
  expect(duplicateKeys).toEqual([]);
});
