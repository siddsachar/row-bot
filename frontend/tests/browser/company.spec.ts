import type { Page, Route, TestInfo } from '@playwright/test';
import {
  accessibility,
  assertNoOverflow,
  expect,
  screenshot,
  test,
} from './evidence';

// Settings › Company (Row-Bot for Teams, C1b): Join, the browser sign-in,
// joined, Leave, and the disconnected and unreachable states. The company
// routes are answered here, so no company server and no browser sign-in are
// needed; everything else is the fixture backend.

const COMPANY = 'rowbot.example.test';

type View = Record<string, unknown>;

const base: View = {
  schema_version: 1,
  state: 'not_joined',
  company: null,
  origin: null,
  name: null,
  email: null,
  joined_at: null,
  computer_name: 'Fixture laptop',
  rejoining: false,
  failure: null,
  notice: null,
  on_this_computer: true,
};

const joined: View = {
  ...base,
  state: 'joined',
  company: COMPANY,
  origin: `https://${COMPANY}`,
  name: 'Ada Lovelace',
  email: 'ada@example.test',
  joined_at: '2026-11-23T09:00:00+00:00',
};

/** The company routes, answered from `state.view`; Join and Leave move it on. */
async function companyRoutes(
  page: Page,
  state: { view: View; asked: string[] },
) {
  await page.route('**/api/v1/company**', async (route: Route) => {
    const path = new URL(route.request().url()).pathname.replace(
      '/api/v1/company',
      '',
    );
    state.asked.push(path || '/');
    if (path === '/join')
      state.view = { ...base, state: 'waiting', company: COMPANY };
    if (path === '/cancel') state.view = { ...base, failure: 'cancelled' };
    if (path === '/leave')
      state.view = { ...base, notice: 'left', company: COMPANY };
    await route.fulfill({ status: 200, json: state.view });
  });
}

const phone = (info: TestInfo) =>
  (info.project.use.viewport?.width ?? 1440) < 700;

/** Light and dark, then an accessibility scan of what shows. */
async function shoot(page: Page, info: TestInfo, name: string) {
  const suffix = phone(info) ? '-phone' : '';
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    await assertNoOverflow(page);
    await screenshot(page, info, `${name}${suffix}-${appearance}`);
  }
  await accessibility(page, info, `${name}${suffix}`);
  await page.emulateMedia({ colorScheme: 'light' });
}

test('Settings › Company: join through the browser, joined, and leave', async ({
  page,
}, info) => {
  const state = { view: base, asked: [] as string[] };
  await companyRoutes(page, state);
  await page.goto('/app-v2/settings/company');

  const field = page.getByRole('textbox', {
    name: 'Your company’s Row-Bot server',
  });
  await expect(field).toBeVisible({ timeout: 15_000 });
  await expect(
    page.getByText('Joining lets Row-Bot use your company’s models and rules.'),
  ).toBeVisible();
  // Opening the page only reads the state: nothing is checked while not joined.
  expect(state.asked).toEqual(['/']);
  await shoot(page, info, 'company-not-joined');

  await field.fill(COMPANY);
  await page.getByRole('button', { name: 'Join', exact: true }).click();
  await expect(
    page.getByText('Finish signing in in your browser'),
  ).toBeVisible();
  await shoot(page, info, 'company-waiting');

  // The person pressed Connect in the browser; the page reads the state again.
  state.view = joined;
  await expect(page.getByText(COMPANY, { exact: true })).toBeVisible();
  await expect(page.getByText('Ada Lovelace · ada@example.test')).toBeVisible();
  await shoot(page, info, 'company-joined');

  await page.getByRole('button', { name: `Leave ${COMPANY}` }).click();
  const confirm = page.getByRole('alertdialog', { name: `Leave ${COMPANY}?` });
  await expect(confirm).toContainText(
    'Your own chats and memory stay on this computer.',
  );
  await shoot(page, info, 'company-leave-confirm');
  await confirm.getByRole('button', { name: 'Leave', exact: true }).click();
  await expect(page.getByText(`This Row-Bot left ${COMPANY}.`)).toBeVisible();
  expect(state.asked).toContain('/leave');
  await shoot(page, info, 'company-left');
});

test('Settings › Company: disconnected, unreachable and a refused address', async ({
  page,
}, info) => {
  const state = {
    view: { ...joined, state: 'disconnected' } as View,
    asked: [] as string[],
  };
  await companyRoutes(page, state);
  await page.goto('/app-v2/settings/company');
  await expect(
    page.getByText('Your company disconnected this Row-Bot'),
  ).toBeVisible({ timeout: 15_000 });
  await expect(
    page.getByRole('button', { name: 'Sign in again' }),
  ).toBeEnabled();
  await shoot(page, info, 'company-disconnected');

  state.view = { ...joined, state: 'unreachable' };
  await page.reload();
  await expect(
    page.getByText(`Can’t reach ${COMPANY} right now. Row-Bot stays joined.`),
  ).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole('button', { name: 'Try again' })).toBeVisible();
  await shoot(page, info, 'company-unreachable');

  state.view = { ...base, failure: 'not_company_server' };
  await page.reload();
  await expect(
    page.getByText(
      'That doesn’t look like a Row-Bot company server. Check the address with your IT team.',
    ),
  ).toBeVisible({ timeout: 15_000 });
  await shoot(page, info, 'company-error');
});
