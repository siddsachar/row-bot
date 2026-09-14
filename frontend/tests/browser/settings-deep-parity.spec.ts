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

const settingsRoutes = [
  ['providers', 'Providers'],
  ['models', 'Models'],
  ['knowledge', 'Knowledge'],
  ['wiki', 'Wiki'],
  ['buddy', 'Buddy'],
  ['goals', 'Goals'],
  ['voice', 'Voice'],
  ['system', 'System'],
  ['tracker', 'Tracker'],
  ['documents', 'Documents'],
  ['tools', 'Tools'],
  ['skills', 'Skills'],
  ['accounts', 'Accounts'],
  ['channels', 'Channels'],
  ['utilities', 'Utilities'],
  ['mcp', 'MCP'],
  ['plugins', 'Plugins'],
  ['preferences', 'Preferences'],
] as const;

const firefoxPhoneRoutes = new Set([
  'providers',
  'system',
  'mcp',
  'preferences',
]);

const aliases = [
  ['cloud', 'providers', 'Providers'],
  ['google', 'accounts', 'Accounts'],
  ['gmail', 'accounts', 'Accounts'],
  ['calendar', 'accounts', 'Accounts'],
  ['migration', 'preferences', 'Preferences'],
  ['search', 'tools', 'Tools'],
  ['profiles', 'goals', 'Goals'],
  ['agent-profiles', 'goals', 'Goals'],
] as const;

function settingsPath(id: string): string {
  return `/app-v2/settings/${id}`;
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
    await expect(navigation.getByRole('link')).toHaveText(expectedLabels);
  } else {
    const picker = page.getByRole('combobox', {
      name: 'Settings section',
      exact: true,
    });
    await expect(picker).toBeVisible();
    await expect(picker.locator('option')).toHaveText(expectedLabels);
  }

  for (const [alias, destination, label] of aliases) {
    await page.goto(settingsPath(alias));
    await expect
      .poll(() => new URL(page.url()).pathname)
      .toBe(settingsPath(destination));
    await waitForSettings(page, label);
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
      await expect(
        page.getByText(
          'This setting is available in the current application.',
          {
            exact: true,
          },
        ),
      ).toHaveCount(0);
      await assertNoOverflow(page);
      if (info.project.use.viewport!.width === 390)
        await expectCoarseTargets(page, info);
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
      path === '/api/v1/settings/snapshot/commands'
    )
      commandRequests.push(path);
  };
  page.on('request', recordCommand);
  try {
    await name.fill(replacement);
    const firstReview = page.waitForResponse(
      (response) =>
        response.request().method() === 'POST' &&
        new URL(response.url()).pathname === '/api/v1/settings/snapshot/review',
    );
    await owner
      .getByRole('button', { name: 'Review change', exact: true })
      .click();
    expect((await firstReview).ok()).toBe(true);
    await expect(owner.getByRole('status')).toContainText('Review ready:');
    await expect(
      owner.getByRole('button', { name: 'Save reviewed change', exact: true }),
    ).toBeVisible();

    // Edit is the non-mutating cancellation path: it retains the draft and
    // returns to the review boundary without issuing a command.
    await owner.getByRole('button', { name: 'Edit', exact: true }).click();
    await expect(name).toHaveValue(replacement);
    await expect(
      owner.getByRole('button', { name: 'Review change', exact: true }),
    ).toBeVisible();
    expect(commandRequests).toEqual([]);

    await owner
      .getByRole('button', { name: 'Review change', exact: true })
      .click();
    await expect(
      owner.getByRole('button', { name: 'Save reviewed change', exact: true }),
    ).toBeVisible();
    await assertNoOverflow(page);
    await accessibility(page, info, 'settings-snapshot-review-axe');
    await screenshot(page, info, 'settings-snapshot-review');

    const saved = page.waitForResponse(
      (response) =>
        response.request().method() === 'POST' &&
        new URL(response.url()).pathname ===
          '/api/v1/settings/snapshot/commands',
    );
    await owner
      .getByRole('button', { name: 'Save reviewed change', exact: true })
      .click();
    const saveResponse = await saved;
    expect(saveResponse.ok()).toBe(true);
    const receipt = (await saveResponse.json()) as {
      command_id: string;
      status: string;
      snapshot?: { preferences?: { identity?: { name?: string } } };
    };
    expect(receipt.status).toBe('completed');
    expect(receipt.snapshot?.preferences?.identity?.name).toBe(replacement);
    await expect(owner.getByRole('status')).toHaveText('Name saved.');
    expect(commandRequests).toEqual(['/api/v1/settings/snapshot/commands']);

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
      review_cancelled_without_command: true,
      command_count: commandRequests.length,
      receipt_status: durableReceipt.status,
      reload_persisted: true,
      secret_material_recorded: false,
    });
  } finally {
    page.off('request', recordCommand);
  }
});

test('Keyboard-only Settings traversal reaches navigation controls disclosure and restores dialog focus', async ({
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
  const providers = page
    .getByRole('navigation', { name: 'Settings sections', exact: true })
    .getByRole('link', { name: 'Providers', exact: true });
  await expect(providers).toBeFocused();
  await page.keyboard.press('Tab');
  const models = page
    .getByRole('navigation', { name: 'Settings sections', exact: true })
    .getByRole('link', { name: 'Models', exact: true });
  await expect(models).toBeFocused();
  await page.keyboard.press('Enter');
  await waitForSettings(page, 'Models');

  const disclosure = page.getByRole('button', {
    name: /Model Catalog/,
    exact: false,
  });
  await disclosure.focus();
  await expect(disclosure).toBeFocused();
  const expanded = await disclosure.getAttribute('aria-expanded');
  await page.keyboard.press('Enter');
  await expect(disclosure).toHaveAttribute(
    'aria-expanded',
    expanded === 'true' ? 'false' : 'true',
  );

  await page.goto(settingsPath('preferences'));
  await waitForSettings(page, 'Preferences');
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
    name: 'Review layout reset',
    exact: true,
  });
  await restore.focus();
  await page.keyboard.press('Enter');
  const dialog = page.getByRole('alertdialog', {
    name: 'Reset layout?',
    exact: true,
  });
  await expect(dialog).toBeVisible();
  await expect(
    dialog.getByRole('button', { name: 'Cancel', exact: true }),
  ).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);
  await expect(restore).toBeFocused();
  await assertNoOverflow(page);
  await accessibility(page, info, 'settings-keyboard-axe');
  await screenshot(page, info, 'settings-keyboard-focus-restored');
});
