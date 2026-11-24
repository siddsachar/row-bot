import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, test, vi } from 'vitest';
import {
  ENGINEERING_WIKI,
  HR_POLICIES,
  SALES_DECKS,
} from '../browser/fake-connectors';
import { renderConsole, sent } from '../testing';

afterEach(() => vi.unstubAllGlobals());

const HR = `/api/admin/connectors/${HR_POLICIES}`;
const KEY = JSON.stringify({
  type: 'service_account',
  client_email: 'new-sync@example-project.iam.gserviceaccount.com',
  private_key: 'test-only-placeholder',
});

async function rows() {
  const list = await screen.findByRole('list', { name: 'Connectors' });
  return within(list).getAllByRole('listitem');
}

async function details(name: string) {
  await userEvent.click(
    await screen.findByRole('button', { name: `Show details for ${name}` }),
  );
  const row = (await rows()).find((item) =>
    within(item).queryByText(name, { exact: true }),
  );
  return row as HTMLElement;
}

async function choose(connector: string, item: string) {
  await userEvent.click(
    await screen.findByRole('button', { name: `More for ${connector}` }),
  );
  await userEvent.click(await screen.findByRole('menuitem', { name: item }));
}

test('connectors list their kind, space, state with its reason, last sync, counts and a badge when they need a look', async () => {
  renderConsole('/connectors');
  const [wiki, hr, decks] = await rows();
  expect(wiki).toHaveTextContent('Engineering wiki');
  expect(wiki).toHaveTextContent('Stopped');
  expect(wiki).toHaveTextContent(
    'SharePoint or OneDrive · Engineering · Synced',
  );
  expect(wiki).toHaveTextContent(
    "Microsoft refused the connector's sign-in. Check its credentials and their consent.",
  );
  expect(wiki).not.toHaveTextContent('Needs a look');

  expect(hr).toHaveTextContent('HR policies');
  expect(hr).toHaveTextContent('Connected');
  expect(hr).toHaveTextContent('124 files · 98 documents');
  expect(hr).toHaveTextContent('Needs a look');

  expect(decks).toHaveTextContent('Paused');
  expect(decks).toHaveTextContent('Google Drive · Sales team');
  expect(decks).toHaveTextContent('Needs a look');
  expect(
    screen.getByRole('button', { name: 'Add connector' }),
  ).toBeInTheDocument();
});

test('details show each warning, when permissions were last checked, and who finds the files', async () => {
  renderConsole('/connectors');
  const hr = await details('HR policies');
  expect(hr).toHaveTextContent('4 files only admins can read');
  expect(hr).toHaveTextContent('2 files couldn’t be fetched');
  expect(hr).toHaveTextContent('22 files skipped');
  expect(hr).toHaveTextContent('Permissions last checked');
  expect(hr).toHaveTextContent(
    'Only the people who can open a file in SharePoint can find it in Row-Bot.',
  );
  expect(hr).toHaveTextContent(
    'Drive b!hr-policies-drive, folder 01HRPOLICIESFOLDER',
  );
  expect(hr).toHaveTextContent('Secret saved');
  expect(hr).not.toHaveTextContent('Some files were left out');
  const unmatched = within(hr).getByRole('list', {
    name: 'Who HR policies can’t match',
  });
  expect(within(unmatched).getAllByRole('listitem')[0]).toHaveTextContent(
    'HR Members',
  );
  expect(within(unmatched).getAllByRole('listitem')[0]).toHaveTextContent(
    'SharePoint site group · 3 files',
  );
  expect(hr).toHaveTextContent('SharePoint site group 7 → it-admins');

  const decks = await details('Sales decks');
  expect(decks).toHaveTextContent('Some files were left out');
  expect(decks).toHaveTextContent(
    '3 files were left out: this connector reached its limit of files or space.',
  );
  expect(decks).toHaveTextContent(
    'Paused: its files keep their last permissions.',
  );
  expect(decks).not.toHaveTextContent('2026-11-21');
  expect(decks).toHaveTextContent(
    'Only the people who can open a file in Google Drive can find it in Row-Bot.',
  );
  expect(decks).toHaveTextContent('Key file saved');

  const wiki = await details('Engineering wiki');
  expect(wiki).toHaveTextContent(
    'This connector stopped: its files keep their last permissions for 24 hours, then only admins can read them.',
  );
});

test('the principal map editor maps someone unmatched and clears a mapping', async () => {
  const api = renderConsole('/connectors');
  await details('HR policies');
  await userEvent.click(
    screen.getByRole('button', { name: 'Map HR Members to a Row-Bot group' }),
  );
  const dialog = screen.getByRole('dialog', {
    name: 'Map HR policies’s people and groups',
  });
  expect(dialog).toHaveTextContent(
    'Row-Bot can’t tell who these are. Give each one the Row-Bot group',
  );
  await userEvent.type(within(dialog).getByLabelText(/^HR Members/), 'Sales');
  const before = within(dialog).getByLabelText(/^SharePoint site group 7/);
  expect(before).toHaveValue('it-admins');
  await userEvent.clear(before);
  await userEvent.click(
    within(dialog).getByRole('button', { name: 'Save the map' }),
  );
  expect(
    await screen.findByText(
      'The map is saved. Row-Bot reads HR policies’s permissions again.',
    ),
  ).toBeInTheDocument();
  expect(sent(api, 'PATCH', HR)).toEqual({
    principal_map: { 'sharepoint-group:5': 'Sales' },
  });
  expect(
    screen.getByText(
      'SharePoint site group · Mapped to Sales: applies when its files are read again',
    ),
  ).toBeInTheDocument();
});

test('pausing and resuming ask first and say what happens', async () => {
  const api = renderConsole('/connectors');
  await userEvent.click(
    await screen.findByRole('button', { name: 'Pause HR policies' }),
  );
  let confirm = screen.getByRole('alertdialog');
  expect(confirm).toHaveTextContent('Pause HR policies?');
  expect(confirm).toHaveTextContent('Nothing syncs until you resume it.');
  await userEvent.click(within(confirm).getByRole('button', { name: 'Pause' }));
  expect(await screen.findByText('HR policies is paused')).toBeInTheDocument();
  expect((await rows())[1]).toHaveTextContent('Paused');

  await userEvent.click(
    screen.getByRole('button', { name: 'Resume HR policies' }),
  );
  confirm = screen.getByRole('alertdialog');
  expect(confirm).toHaveTextContent(
    'It syncs again now, as you: the files it reads are added in your name',
  );
  await userEvent.click(
    within(confirm).getByRole('button', { name: 'Resume' }),
  );
  expect(
    await screen.findByText('HR policies is syncing again'),
  ).toBeInTheDocument();
  expect((await rows())[1]).toHaveTextContent('Connected');
  const paths = api.calls.map((call) => `${call.method} ${call.path}`);
  expect(paths).toContain(`POST ${HR}/pause`);
  expect(paths).toContain(`POST ${HR}/resume`);
});

test('sync now runs at once, and a paused connector says why it can’t', async () => {
  const api = renderConsole('/connectors');
  await userEvent.click(
    await screen.findByRole('button', { name: 'Sync HR policies now' }),
  );
  expect(
    await screen.findByText('HR policies is syncing now'),
  ).toBeInTheDocument();
  const paused = screen.getByRole('button', {
    name: 'Sales decks is paused: resume it to sync',
  });
  expect(paused).toHaveAttribute('aria-disabled', 'true');
  await userEvent.click(paused);
  const paths = api.calls.map((call) => `${call.method} ${call.path}`);
  expect(paths).toContain(`POST ${HR}/sync`);
  expect(paths).not.toContain(`POST /api/admin/connectors/${SALES_DECKS}/sync`);
});

test('deleting asks first, says how many documents go, and Cancel keeps it', async () => {
  const api = renderConsole('/connectors');
  await choose('HR policies', 'Delete');
  let confirm = await screen.findByRole('alertdialog');
  expect(confirm).toHaveTextContent('Delete HR policies?');
  expect(confirm).toHaveTextContent(
    'Removes its 98 documents from Company. The files stay in SharePoint. This can’t be undone.',
  );
  await userEvent.click(
    within(confirm).getByRole('button', { name: 'Cancel' }),
  );
  expect(api.calls.some((call) => call.method === 'DELETE')).toBe(false);

  await choose('HR policies', 'Delete');
  confirm = await screen.findByRole('alertdialog');
  await userEvent.click(
    within(confirm).getByRole('button', { name: 'Delete' }),
  );
  expect(await screen.findByText('HR policies is deleted')).toBeInTheDocument();
  expect(await rows()).toHaveLength(2);
  expect(
    api.calls.some((call) => call.method === 'DELETE' && call.path === HR),
  ).toBe(true);
});

test('a connection test shows its result in plain words in the details', async () => {
  renderConsole('/connectors');
  await choose('Engineering wiki', 'Test connection');
  const failed = await screen.findByText('The connection test failed');
  expect(failed.closest('[role="status"]')).toHaveTextContent(
    "Microsoft refused the connector's sign-in. Check its credentials and their consent.",
  );

  await choose('HR policies', 'Test connection');
  expect(await screen.findByText('The connection works')).toBeInTheDocument();
});

test('replacing Microsoft credentials never shows the saved secret, sends only what changed and tests again', async () => {
  const api = renderConsole('/connectors');
  await choose('Engineering wiki', 'Replace credentials');
  const dialog = await screen.findByRole('dialog', {
    name: 'Replace Engineering wiki’s credentials',
  });
  const secret = within(dialog).getByLabelText(/^Client secret/);
  expect(secret).toHaveAttribute('type', 'password');
  expect(secret).toHaveValue('');
  expect(secret).toHaveAttribute('placeholder', 'Secret saved');
  expect(dialog).toHaveTextContent('Secret saved. Enter the new secret');
  expect(within(dialog).getByLabelText(/^Tenant id/)).toHaveValue(
    '5f1c2a9e-3b4d-4e6f-8a7b-1c2d3e4f5a6b',
  );
  await userEvent.type(secret, 'new-test-secret');
  await userEvent.click(
    within(dialog).getByRole('button', { name: 'Save credentials' }),
  );
  expect(
    await screen.findByText('New credentials for Engineering wiki are saved'),
  ).toBeInTheDocument();
  expect(
    sent(api, 'PATCH', `/api/admin/connectors/${ENGINEERING_WIKI}`),
  ).toEqual({ client_secret: 'new-test-secret' });
  // The fake starts a stopped connector again on new credentials, and the test passes.
  expect(await screen.findByText('The connection works')).toBeInTheDocument();
  expect((await rows())[0]).toHaveTextContent('Connected');
  expect(screen.queryByDisplayValue('new-test-secret')).not.toBeInTheDocument();
  expect(document.body).not.toHaveTextContent('new-test-secret');
});

test('replacing a Google key file shows only the service account, never the key', async () => {
  const api = renderConsole('/connectors');
  await choose('Sales decks', 'Replace credentials');
  const dialog = await screen.findByRole('dialog', {
    name: 'Replace Sales decks’s credentials',
  });
  expect(dialog).toHaveTextContent(
    'Key file saved for row-bot-sync@example-project.iam.gserviceaccount.com',
  );
  await userEvent.click(within(dialog).getByLabelText(/^Service account key/));
  await userEvent.paste(KEY);
  expect(dialog).toHaveTextContent(
    'new-sync@example-project.iam.gserviceaccount.com · pasted key',
  );
  expect(dialog).not.toHaveTextContent('test-only-placeholder');
  await userEvent.click(
    within(dialog).getByRole('button', { name: 'Save credentials' }),
  );
  expect(
    await screen.findByText('New credentials for Sales decks are saved'),
  ).toBeInTheDocument();
  expect(sent(api, 'PATCH', `/api/admin/connectors/${SALES_DECKS}`)).toEqual({
    service_account: KEY,
  });
  expect(document.body).not.toHaveTextContent('test-only-placeholder');
});

test('renaming sends only the new name', async () => {
  const api = renderConsole('/connectors');
  await choose('HR policies', 'Rename');
  const dialog = await screen.findByRole('dialog', {
    name: 'Rename HR policies',
  });
  const name = within(dialog).getByLabelText(/^Name/);
  await userEvent.clear(name);
  await userEvent.type(name, 'People policies');
  await userEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
  expect(
    await screen.findByText('People policies is saved'),
  ).toBeInTheDocument();
  expect(sent(api, 'PATCH', HR)).toEqual({ name: 'People policies' });
});

test('no connectors yet says what to connect, with the one action', async () => {
  renderConsole('/connectors', 'empty');
  expect(
    await screen.findByRole('heading', { name: 'No connectors yet' }),
  ).toBeInTheDocument();
  expect(
    screen.getByText(/^Connect SharePoint or Google Drive\./),
  ).toBeInTheDocument();
  expect(screen.getAllByRole('button', { name: 'Add connector' })).toHaveLength(
    1,
  );
});

test('connectors that cannot load offer to try again', async () => {
  let fail = true;
  renderConsole('/connectors', 'populated', (url) =>
    fail && url.pathname === '/api/admin/connectors'
      ? { status: 500, body: { error: 'internal' } }
      : undefined,
  );
  const error = await screen.findByRole('alert');
  expect(error).toHaveTextContent("Couldn't load connectors");
  expect(error).toHaveTextContent('The company server had a problem');
  fail = false;
  await userEvent.click(
    within(error).getByRole('button', { name: 'Try again' }),
  );
  expect(await rows()).toHaveLength(3);
});

test('connectors show a loading state while they load', async () => {
  renderConsole('/connectors');
  const answer = globalThis.fetch;
  vi.stubGlobal('fetch', (input: string, init?: RequestInit) =>
    input.startsWith('/api/admin/connectors')
      ? new Promise<Response>(() => undefined)
      : answer(input, init),
  );
  expect(
    await screen.findByRole('status', { name: 'Loading connectors' }),
  ).toBeInTheDocument();
});
