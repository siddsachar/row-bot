import {
  accessibility,
  assertNoOverflow,
  expect,
  screenshot,
  test,
  writeEvidence,
} from './evidence';
import {
  blockFixtureServiceWorkers,
  composer,
  conversationState,
  fixtureState,
  newConversation,
  openConversation,
} from './unified-helpers';
import { captureBrowserDownload } from './download-helpers';
import type { Page, TestInfo } from '@playwright/test';
import { createHash } from 'node:crypto';

function fixtureHeaders() {
  const token = process.env.ROW_BOT_BROWSER_CONTROL_TOKEN;
  const base = process.env.ROW_BOT_BROWSER_BASE_URL;
  if (!token || !base) throw new Error('Use the isolated Phase 4 runner');
  return { 'X-Fixture-Token': token, Origin: new URL(base).origin };
}

async function visualCheck(page: Page, info: TestInfo, label: string) {
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    await page.evaluate((nextAppearance) => {
      const key = 'row-bot.appearance.v1';
      const previous = localStorage.getItem(key);
      let saved: Record<string, unknown> = {};
      try {
        saved = JSON.parse(previous ?? '{}');
      } catch {
        /* The theme owner safely fills a malformed or absent preference. */
      }
      const next = JSON.stringify({
        version: 1,
        accent: 'blue',
        density: 'compact',
        reduce_transparency: false,
        ...saved,
        appearance: nextAppearance,
      });
      localStorage.setItem(key, next);
      window.dispatchEvent(
        new StorageEvent('storage', {
          key,
          oldValue: previous,
          newValue: next,
          storageArea: localStorage,
          url: location.href,
        }),
      );
    }, appearance);
    await expect(page.locator('html')).toHaveAttribute(
      'data-theme',
      appearance,
    );
    await assertNoOverflow(page);
    await screenshot(page, info, `${label}-${appearance}`);
    await accessibility(page, info, `${label}-${appearance}`, {
      opaquePreview: true,
    });
  }
}

async function openSettingThroughCommands(
  page: Page,
  route: { label: string; path: string },
) {
  const navigated = page.waitForURL((url) => url.pathname === route.path);
  await page
    .getByRole('button', { name: 'Workspace commands', exact: true })
    .click();
  const commands = page.getByRole('dialog', {
    name: 'Workspace commands',
    exact: true,
  });
  await commands
    .getByRole('searchbox', {
      name: 'Find a workspace command',
      exact: true,
    })
    .fill(`Open ${route.label} settings`);
  await page.keyboard.press('Enter');
  await navigated;
  await expect(
    page.getByRole('heading', { name: route.label, exact: true }),
  ).toBeVisible();
}

async function createResource(
  page: Page,
  kind: 'deck' | 'workspace',
  name: string,
) {
  await page.getByRole('button', { name: 'Add resource', exact: true }).click();
  const dialog = page.getByRole('dialog', {
    name: 'Add resource',
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
    await dialog
      .getByRole('button', { name: 'Create empty workspace', exact: true })
      .click();
  } else {
    await dialog
      .getByRole('textbox', { name: 'Name (optional)', exact: true })
      .fill(name);
    await dialog
      .getByRole('button', { name: 'Create Deck', exact: true })
      .click();
  }
  await expect(
    dialog.getByText('Resource ready', { exact: true }),
  ).toBeVisible();
  await page.keyboard.press('Escape');
  return (
    await conversationState(
      page,
      new URL(page.url()).pathname.split('/').at(-1)!,
    )
  ).conversation.resource_bindings[0];
}

test.beforeEach(async ({ context }) => {
  await blockFixtureServiceWorkers(context);
});

test('Goals and Agent Profiles use reviewed mutations and survive a reload', async ({
  page,
}, info) => {
  test.setTimeout(180_000);
  const conversation = await newConversation(page);
  await composer(page).fill('Retained goal and profile draft');
  await expect(
    page.getByRole('status').filter({ hasText: /^Draft saved$/ }),
  ).toBeVisible();
  await openSettingThroughCommands(page, {
    label: 'Goals',
    path: '/app-v2/settings/goals',
  });

  const owner = page.getByRole('region', {
    name: 'Goals and Agent Profiles',
    exact: true,
  });
  const goals = owner.getByRole('region', { name: 'Goals', exact: true });
  await expect(goals.getByText('0 goals in this conversation.')).toBeVisible();
  await goals
    .getByLabel('Goal objective', { exact: true })
    .fill('Verify the isolated Phase 4 capability surfaces');
  await goals.getByLabel('Maximum turns', { exact: true }).fill('12');
  await goals
    .getByRole('button', { name: 'Review start goal', exact: true })
    .click();
  const review = owner.getByRole('region', {
    name: 'Goal or profile change review',
    exact: true,
  });
  await expect(review).toContainText('Goal action: start.');
  await visualCheck(page, info, 'goal-start-review');
  await review
    .getByRole('button', { name: 'Apply reviewed change', exact: true })
    .click();
  await expect(goals.getByText(/active · 0 of 12 turns/)).toBeVisible();

  await goals
    .getByLabel('Reason for goal status change', { exact: true })
    .fill('Pause after the verified browser checkpoint.');
  await goals
    .getByRole('button', { name: 'Review pause', exact: true })
    .click();
  await expect(review).toContainText('Goal action: pause.');
  await review
    .getByRole('button', { name: 'Apply reviewed change', exact: true })
    .click();
  await expect(goals.getByText(/paused · 0 of 12 turns/)).toBeVisible();

  const avatar = page.locator('.navigation .buddy-avatar').first();
  if (await avatar.count()) {
    await expect
      .poll(() =>
        avatar.evaluate((node) => {
          if (node instanceof HTMLVideoElement)
            return (
              node.src.startsWith('blob:') && node.poster.startsWith('blob:')
            );
          return (
            node instanceof HTMLImageElement &&
            node.src.startsWith('blob:') &&
            node.complete &&
            node.naturalWidth > 0
          );
        }),
      )
      .toBe(true);
  }
  await page.reload();
  await expect(goals.getByText(/paused · 0 of 12 turns/)).toBeVisible();
  await owner.getByRole('tab', { name: 'Agent Profiles', exact: true }).click();
  const profiles = owner.getByRole('region', {
    name: 'Agent Profiles',
    exact: true,
  });
  await expect(profiles.getByText(/reusable profiles/)).toBeVisible();
  await profiles
    .getByRole('button', { name: 'Create profile', exact: true })
    .click();
  const createProfile = owner.getByRole('group', {
    name: 'Create profile',
    exact: true,
  });
  const slug = `phase4_${conversation.replaceAll('-', '_')}`.slice(0, 64);
  const displayName = `Phase 4 browser ${conversation.slice(0, 8)}`;
  await owner.getByLabel('Profile slug', { exact: true }).fill(slug);
  await owner
    .getByLabel('Profile display name', { exact: true })
    .fill(displayName);
  await owner
    .getByLabel('Description', { exact: true })
    .fill('A disposable browser-only profile.');
  await owner
    .getByLabel('When to use', { exact: true })
    .fill('Only while running the isolated Phase 4 browser fixture.');
  await createProfile
    .getByRole('textbox', { name: /^New instructions\b/ })
    .fill(
      'Use deterministic fixture data and do not contact external services.',
    );
  await owner
    .getByRole('button', { name: 'Review create profile', exact: true })
    .click();
  await expect(review).toContainText('Profile action: create.');
  await visualCheck(page, info, 'profile-create-review');
  await review
    .getByRole('button', { name: 'Apply reviewed change', exact: true })
    .click();
  await expect(profiles.getByText(displayName, { exact: true })).toBeVisible();

  expect(
    (await fixtureState(page)).calls.filter(
      (call) => call.conversation_id === conversation,
    ),
  ).toEqual([]);
  await writeEvidence(info, 'goal-profile-result.json', {
    conversation,
    goal: 'paused',
    max_turns: 12,
    profile_slug: slug,
    profile_created: true,
    provider_calls: 0,
  });
});

test('retained settings expose real capability state without leaving the unified client', async ({
  page,
}, info) => {
  test.setTimeout(180_000);
  const conversation = await newConversation(page);
  await composer(page).fill('Retained settings navigation draft');
  await expect(
    page.getByRole('status').filter({ hasText: /^Draft saved$/ }),
  ).toBeVisible();
  await openSettingThroughCommands(page, {
    label: 'Voice',
    path: '/app-v2/settings/voice',
  });
  await expect(
    page.getByText(/Opening this page never starts a microphone/),
  ).toBeVisible();
  await expect(
    page.getByText('This setting is available in the current application.'),
  ).toHaveCount(0);

  await openSettingThroughCommands(page, {
    label: 'Accounts',
    path: '/app-v2/settings/accounts',
  });
  await expect(page.getByText(/reviewed sign-in controls/)).toBeVisible();
  await visualCheck(page, info, 'retained-accounts-settings');

  await openSettingThroughCommands(page, {
    label: 'Tracker',
    path: '/app-v2/settings/tracker',
  });
  await expect(
    page.getByRole('heading', {
      name: 'Tracker tool in conversations',
      exact: true,
    }),
  ).toBeVisible();

  await openSettingThroughCommands(page, {
    label: 'Utilities',
    path: '/app-v2/settings/utilities',
  });
  await expect(
    page.getByRole('link', { name: 'Browse utility tools', exact: true }),
  ).toBeVisible();

  await openSettingThroughCommands(page, {
    label: 'System',
    path: '/app-v2/settings/system',
  });
  await expect(page.getByText(/does not broaden filesystem/)).toBeVisible();
  await visualCheck(page, info, 'retained-system-settings');
  await page.reload();
  await expect(
    page.getByRole('heading', { name: 'System', exact: true }),
  ).toBeVisible();

  for (let remaining = 5; remaining > 0; remaining -= 1)
    await page.goBack({ waitUntil: 'commit' });
  await expect(page).toHaveURL(`/app-v2/conversations/${conversation}`);
  await expect(composer(page)).toHaveValue(
    'Retained settings navigation draft',
  );
  expect(
    (await fixtureState(page)).calls.filter(
      (call) => call.conversation_id === conversation,
    ),
  ).toEqual([]);
  await writeEvidence(info, 'retained-settings-result.json', {
    conversation,
    routes: ['voice', 'accounts', 'tracker', 'utilities', 'system'],
    draft_retained: true,
    provider_calls: 0,
  });
});

test('managed browser uses one reviewed live-control panel with sanitized recovery state', async ({
  page,
}, info) => {
  test.setTimeout(180_000);
  const conversation = await newConversation(page);
  await composer(page).fill('Retained managed browser draft');
  await expect(
    page.getByRole('status').filter({ hasText: /^Draft saved$/ }),
  ).toBeVisible();
  await page
    .getByRole('button', { name: 'Conversation actions', exact: true })
    .click();
  await page
    .getByRole('menuitem', { name: 'Manage browser', exact: true })
    .click();

  let browser = page.getByRole('region', {
    name: 'Managed browser',
    exact: true,
  });
  await expect(browser).toBeVisible();
  await expect(
    browser.getByText('No managed-browser activity for this conversation.'),
  ).toBeVisible();
  await browser
    .getByLabel('Address', { exact: true })
    .fill('https://example.test/start?token=browser-fixture-secret');
  await browser
    .getByRole('button', { name: 'Review address', exact: true })
    .click();
  const review = browser.getByText('Review browser action', { exact: true });
  await expect(review).toBeVisible();
  await expect(browser).toContainText('https://example.test/start');
  await expect(browser).not.toContainText('browser-fixture-secret');
  await visualCheck(page, info, 'managed-browser-review');
  await browser
    .getByRole('button', { name: 'Open reviewed address', exact: true })
    .click();
  await expect(browser.getByText('Open address completed.')).toBeVisible();
  await expect(browser.getByText(/Site:/)).toBeVisible();
  await expect(browser).toContainText('https://example.test/start');
  await expect(browser).not.toContainText('browser-fixture-secret');
  await browser.getByText('Unavailable browser controls').click();
  await expect(browser.getByText(/exact page-target contract/)).toBeVisible();

  await page.reload();
  browser = page.getByRole('region', {
    name: 'Managed browser',
    exact: true,
  });
  await expect(browser).toBeVisible();
  await expect(browser).toContainText('example.test');
  await expect(browser).not.toContainText('browser-fixture-secret');
  if (page.viewportSize()!.width < 1024) {
    await page
      .getByRole('button', { name: 'Back to conversation', exact: true })
      .click();
  }
  await expect(composer(page)).toHaveValue('Retained managed browser draft');
  expect(
    (await fixtureState(page)).calls.filter(
      (call) => call.conversation_id === conversation,
    ),
  ).toEqual([]);
  await writeEvidence(info, 'managed-browser-result.json', {
    conversation,
    site: 'example.test',
    query_redacted: true,
    draft_retained: true,
    provider_calls: 0,
  });
});

test('Developer repository, worktree, and sandbox changes use the bound workspace owner', async ({
  page,
}, info) => {
  test.setTimeout(240_000);
  const conversation = await newConversation(page);
  await composer(page).fill('Retained Developer repository draft');
  const name = `phase4-repository-${conversation}`;
  const binding = await createResource(page, 'workspace', name);
  expect(binding.kind).toBe('workspace');
  const seeded = await page.request.post(
    `/__p4_fixture/resources/${binding.resource_id}/repository`,
    { headers: fixtureHeaders() },
  );
  expect(seeded.ok()).toBe(true);

  const repository = page.getByRole('region', {
    name: 'Developer repository controls',
    exact: true,
  });
  await repository
    .getByRole('button', { name: 'Refresh', exact: true })
    .click();
  await expect(
    repository.getByText('main · Clean', { exact: true }),
  ).toBeVisible();
  await expect(
    repository.getByRole('button', { name: 'Review push', exact: true }),
  ).toBeDisabled();

  const branch = `phase4-${conversation.slice(0, 8)}`;
  await repository.getByLabel('Branch name', { exact: true }).fill(branch);
  await repository
    .getByRole('button', { name: 'Review new branch', exact: true })
    .click();
  const review = repository.getByRole('article', {
    name: 'Reviewed repository change',
    exact: true,
  });
  await expect(review).toContainText('developer.repository.branch.create');
  await review
    .getByRole('button', { name: 'Cancel review', exact: true })
    .click();
  await expect(
    repository.getByText('main · Clean', { exact: true }),
  ).toBeVisible();
  await repository
    .getByRole('button', { name: 'Review new branch', exact: true })
    .click();
  await review
    .getByRole('button', {
      name: 'Apply reviewed repository change',
      exact: true,
    })
    .click();
  await expect(
    repository.getByText(`${branch} · Clean`, { exact: true }),
  ).toBeVisible();

  await repository
    .getByLabel('Worktree objective', { exact: true })
    .fill('Isolated browser worktree');
  await repository
    .getByRole('button', { name: 'Review managed worktree', exact: true })
    .click();
  await expect(review).toContainText('developer.repository.worktree.create');
  await visualCheck(page, info, 'developer-worktree-review');
  await review
    .getByRole('button', {
      name: 'Apply reviewed repository change',
      exact: true,
    })
    .click();
  await expect(repository.getByText(/active · preserve/)).toBeVisible();
  await repository
    .getByLabel('Preservation reason', { exact: true })
    .fill('Keep the disposable verification result.');
  await repository
    .getByRole('button', {
      name: 'Review worktree preservation',
      exact: true,
    })
    .click();
  await expect(review).toContainText('developer.repository.worktree.preserve');
  await review
    .getByRole('button', {
      name: 'Apply reviewed repository change',
      exact: true,
    })
    .click();
  await expect(repository.getByText(/preserved · preserve/)).toBeVisible();

  await repository
    .getByRole('combobox', { name: 'Execution mode', exact: true })
    .selectOption('docker');
  await repository
    .getByRole('combobox', { name: 'Sandbox network', exact: true })
    .selectOption('off');
  await repository
    .getByLabel('Sandbox image', { exact: true })
    .fill('row-bot-fixture:local');
  await repository
    .getByRole('button', { name: 'Review sandbox settings', exact: true })
    .click();
  await expect(review).toContainText('developer.repository.sandbox.configure');
  await review
    .getByRole('button', {
      name: 'Apply reviewed repository change',
      exact: true,
    })
    .click();
  await expect(
    repository.getByRole('button', {
      name: 'Review sandbox rebuild',
      exact: true,
    }),
  ).toBeEnabled();

  await page.reload();
  await expect(
    repository.getByText(`${branch} · Clean`, { exact: true }),
  ).toBeVisible();
  await expect(
    repository.getByRole('combobox', { name: 'Execution mode', exact: true }),
  ).toHaveValue('docker');
  await visualCheck(page, info, 'developer-repository-saved');
  const observed = await page.request
    .get(`/__p4_fixture/resources/${binding.resource_id}/repository`, {
      headers: fixtureHeaders(),
    })
    .then((response) => response.json());
  expect(observed).toMatchObject({
    resource_id: binding.resource_id,
    conversation_id: conversation,
    repository: { is_git: true, branch },
    sandbox: {
      execution_mode: 'docker',
      network: 'off',
      image: 'row-bot-fixture:local',
    },
  });
  expect(observed.worktrees).toHaveLength(1);
  expect(observed.worktrees[0]).toMatchObject({
    owned_by_conversation: true,
    status: 'preserved',
    cleanup_state: 'preserve',
  });
  expect(
    (await fixtureState(page)).calls.filter(
      (call) => call.conversation_id === conversation,
    ),
  ).toEqual([]);
  await writeEvidence(info, 'developer-repository-result.json', {
    resource_id: binding.resource_id,
    branch,
    worktree: observed.worktrees[0],
    sandbox: observed.sandbox,
    remote_actions_available: observed.available['developer.repository.push'],
    provider_calls: 0,
  });
});

test('Design lifecycle opens presentation, export, and sharing inside the unified resource panel', async ({
  page,
}, info) => {
  test.setTimeout(180_000);
  const conversation = await newConversation(page);
  await composer(page).fill('Retained lifecycle conversation draft');
  const name = `phase4-lifecycle-${conversation}`;
  const binding = await createResource(page, 'deck', name);
  expect(binding.kind).toBe('artifact');

  const preview = page.getByRole('region', {
    name: 'Design preview',
    exact: true,
  });
  const lifecycle = preview.getByRole('region', {
    name: 'Design lifecycle',
    exact: true,
  });
  await expect(
    lifecycle.getByRole('heading', {
      name: 'Present, export and share',
      exact: true,
    }),
  ).toBeVisible();
  await expect(lifecycle.getByText(/HTML export:.*Ready\./)).toBeVisible();
  await expect(
    lifecycle.getByText(/Local published link:.*Ready\./),
  ).toBeVisible();
  await expect(
    lifecycle.getByText(/Remote access link:.*Unavailable\./),
  ).toBeVisible();

  await lifecycle.getByRole('button', { name: 'Present', exact: true }).click();
  await expect(
    preview.getByRole('heading', { name: 'Presentation', exact: true }),
  ).toBeVisible();
  await lifecycle.getByRole('button', { name: 'Export', exact: true }).click();
  await expect(
    preview.getByRole('region', { name: 'Design export', exact: true }),
  ).toBeVisible();
  await lifecycle.getByRole('button', { name: 'Share', exact: true }).click();
  await expect(
    preview.getByRole('region', { name: 'Design sharing', exact: true }),
  ).toBeVisible();
  await visualCheck(page, info, 'artifact-lifecycle-sharing');

  if (page.viewportSize()!.width < 1024) {
    await page
      .getByRole('button', { name: 'Back to conversation', exact: true })
      .click();
  }
  await expect(composer(page)).toHaveValue(
    'Retained lifecycle conversation draft',
  );
  expect(
    (await fixtureState(page)).calls.filter(
      (call) => call.conversation_id === conversation,
    ),
  ).toEqual([]);
  await writeEvidence(info, 'artifact-lifecycle-result.json', {
    resource_id: binding.resource_id,
    views_opened: ['presentation', 'export', 'sharing'],
    draft_retained: true,
    provider_calls: 0,
    channel_deliveries: 0,
  });
});

test('Conversation actions rename, pin, and export through one recoverable overlay', async ({
  page,
}, info) => {
  test.setTimeout(180_000);
  const conversation = await newConversation(page);
  await composer(page).fill('Retained conversation actions draft');
  await page
    .getByRole('button', { name: 'Conversation actions', exact: true })
    .click();
  await page
    .getByRole('menuitem', { name: 'Manage conversation', exact: true })
    .click();

  let dialog = page.getByRole('dialog', {
    name: 'Conversation actions',
    exact: true,
  });
  let actions = dialog.getByRole('region', {
    name: 'Conversation actions',
    exact: true,
  });
  await expect(
    actions.getByLabel('Conversation name', { exact: true }),
  ).toBeVisible();
  const renamed = `Phase 4 actions ${conversation.slice(0, 8)}`;
  await actions.getByLabel('Conversation name', { exact: true }).fill(renamed);
  await actions
    .getByRole('button', { name: 'Review rename', exact: true })
    .click();
  const review = actions.getByRole('region', {
    name: 'Conversation action review',
    exact: true,
  });
  await expect(review).toContainText('Rename this conversation');
  await visualCheck(page, info, 'conversation-rename-review');
  await review
    .getByRole('button', { name: 'Apply reviewed action', exact: true })
    .click();
  await expect(
    actions.getByText('Conversation action completed.'),
  ).toBeVisible();

  await actions
    .getByRole('button', { name: 'Review pin', exact: true })
    .click();
  await expect(review).toContainText('Pin this conversation.');
  await review
    .getByRole('button', { name: 'Apply reviewed action', exact: true })
    .click();
  await expect(
    actions.getByRole('button', { name: 'Review unpin', exact: true }),
  ).toBeVisible();
  await expect(actions.getByText(/Archive is unavailable/)).toBeVisible();
  await visualCheck(page, info, 'conversation-actions-pinned');

  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);
  await expect(
    page.getByRole('heading', { name: renamed, exact: true }),
  ).toBeVisible();
  await expect(composer(page)).toHaveValue(
    'Retained conversation actions draft',
  );

  await openConversation(page, conversation);
  await expect(
    page.getByRole('heading', { name: renamed, exact: true }),
  ).toBeVisible();
  await page
    .getByRole('button', { name: 'Conversation actions', exact: true })
    .click();
  await page
    .getByRole('menuitem', { name: 'Manage conversation', exact: true })
    .click();
  dialog = page.getByRole('dialog', {
    name: 'Conversation actions',
    exact: true,
  });
  actions = dialog.getByRole('region', {
    name: 'Conversation actions',
    exact: true,
  });
  await expect(
    actions.getByLabel('Conversation name', { exact: true }),
  ).toHaveValue(renamed);
  await expect(
    actions.getByRole('button', { name: 'Review unpin', exact: true }),
  ).toBeVisible();

  await actions
    .getByRole('button', { name: 'Review export', exact: true })
    .click();
  await expect(review).toContainText('local Markdown copy');
  await review
    .getByRole('button', { name: 'Apply reviewed action', exact: true })
    .click();
  const downloadButton = actions.getByRole('button', {
    name: 'Download conversation export',
    exact: true,
  });
  await expect(downloadButton).toBeVisible();
  const download = await captureBrowserDownload(page, () =>
    downloadButton.click(),
  );
  expect(download.name).toBe('conversation-export.md');
  expect(download.mimeType).toBe('application/octet-stream');
  const bytes = download.bytes;
  const markdown = bytes.toString('utf-8');
  expect(markdown).toContain(`# ${renamed}`);
  expect(markdown).toContain('_Exported from the saved Row-Bot conversation._');
  expect(markdown).not.toContain('system instructions');
  expect(bytes.length).toBeLessThan(1024 * 1024);
  await expect(
    actions.getByText('Conversation export downloaded.'),
  ).toBeVisible();
  await writeEvidence(info, 'conversation-actions-result.json', {
    conversation,
    title: renamed,
    pinned: true,
    export_name: download.name,
    export_bytes: bytes.length,
    export_sha256: createHash('sha256').update(bytes).digest('hex'),
    draft_retained: true,
    provider_calls: 0,
    channel_deliveries: 0,
  });
});
