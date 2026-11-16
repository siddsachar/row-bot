import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import type {
  EditorConfigPreview,
  EditorView,
  EditorsClient,
} from '../../api/editors';
import { OverlayProvider } from '../../ui/overlays';
import EditorConnections from './EditorConnections';

const editors: EditorView[] = [
  {
    id: 'claude-code',
    name: 'Claude Code',
    writes_config: true,
    connected: false,
    connected_at: null,
    last_used_at: null,
  },
  {
    id: 'cursor',
    name: 'Cursor',
    writes_config: true,
    connected: true,
    connected_at: '2026-11-14T09:00:00Z',
    last_used_at: '2026-11-15T08:00:00Z',
  },
  {
    id: 'codex',
    name: 'Codex',
    writes_config: true,
    connected: false,
    connected_at: null,
    last_used_at: null,
  },
  {
    id: 'zed',
    name: 'Zed',
    writes_config: false,
    connected: false,
    connected_at: null,
    last_used_at: null,
  },
];
const snippet = {
  path: '/home/person/.claude.json',
  format: 'json' as const,
  text: '{\n  "mcpServers": {\n    "row-bot": {}\n  }\n}\n',
};
const preview: EditorConfigPreview = {
  path: '/home/person/.claude.json',
  exists: true,
  fingerprint: 'f'.repeat(64),
  format: 'json',
  action: 'add',
  problem: null,
  before: null,
  after: '{\n  "row-bot": {}\n}',
};

function fixture(list = editors): EditorsClient {
  return {
    list: vi.fn().mockResolvedValue(list),
    connect: vi.fn(async (id) => ({
      editor: { ...list.find((item) => item.id === id)!, connected: true },
      snippet,
    })),
    snippet: vi.fn().mockResolvedValue(snippet),
    preview: vi.fn().mockResolvedValue(preview),
    write: vi.fn().mockResolvedValue({
      path: preview.path,
      backup_path: '/home/person/.claude.json.row-bot-backup-20261115-090000',
      changed: true,
    }),
    revoke: vi.fn().mockResolvedValue(undefined),
  };
}

function show(client = fixture()) {
  const writeClipboard = vi.fn().mockResolvedValue({ status: 'ok' });
  render(
    <OverlayProvider>
      <EditorConnections client={client} writeClipboard={writeClipboard} />
    </OverlayProvider>,
  );
  return { client, writeClipboard };
}

it('lists connected editors with their last use, and icon actions with names', async () => {
  show();
  const list = await screen.findByRole('list', { name: 'Connected editors' });
  const [row] = within(list).getAllByRole('listitem');
  expect(row).toHaveTextContent('Cursor');
  expect(row).toHaveTextContent('Last used');
  expect(
    within(row).getByRole('button', { name: 'Settings for Cursor' }),
  ).toBeVisible();
  expect(
    within(row).getByRole('button', { name: 'Revoke Cursor' }),
  ).toBeVisible();
  expect(
    screen.getByRole('button', { name: 'Connect an editor' }),
  ).toBeVisible();
});

it('offers to connect one when none is connected', async () => {
  show(fixture(editors.map((editor) => ({ ...editor, connected: false }))));
  expect(await screen.findByText('No editors connected.')).toBeVisible();
  expect(
    screen.getByRole('button', { name: 'Connect an editor' }),
  ).toBeVisible();
});

it('connects an editor, shows its settings and writes them only after the change is shown', async () => {
  const { client, writeClipboard } = show();
  fireEvent.click(
    await screen.findByRole('button', { name: 'Connect an editor' }),
  );
  const sheet = await screen.findByRole('dialog', {
    name: 'Connect an editor',
  });
  const connect = within(sheet).getByRole('button', { name: 'Connect' });
  expect(connect).toBeDisabled();
  fireEvent.click(within(sheet).getByRole('radio', { name: /Claude Code/ }));
  fireEvent.click(connect);

  const settings = await screen.findByRole('dialog', { name: 'Claude Code' });
  expect(client.connect).toHaveBeenCalledWith('claude-code');
  expect(settings).toHaveTextContent('/home/person/.claude.json');
  expect(settings).toHaveTextContent('these settings don’t contain it');
  fireEvent.click(
    within(settings).getByRole('button', { name: 'Copy settings' }),
  );
  await waitFor(() =>
    expect(writeClipboard).toHaveBeenCalledWith(snippet.text),
  );

  fireEvent.click(
    within(settings).getByRole('button', { name: /Add it for me/ }),
  );
  const review = await screen.findByRole('dialog', {
    name: 'Add Row-Bot to Claude Code',
  });
  expect(
    await within(review).findByText(/adds its entry to this file/),
  ).toBeVisible();
  expect(review).toHaveTextContent(
    'A copy of the file is kept beside it first.',
  );
  expect(client.write).not.toHaveBeenCalled();
  fireEvent.click(
    within(review).getByRole('button', { name: 'Change the file' }),
  );
  await waitFor(() =>
    expect(client.write).toHaveBeenCalledWith(
      'claude-code',
      preview.fingerprint,
    ),
  );
  expect(
    await screen.findByText(/Restart Claude Code to use Row-Bot/),
  ).toBeVisible();
  expect(
    screen.getByText(
      '/home/person/.claude.json.row-bot-backup-20261115-090000',
    ),
  ).toBeVisible();
});

it('says why a file Row-Bot will not change is left alone, and offers no write', async () => {
  const client = fixture();
  client.preview = vi.fn().mockResolvedValue({
    ...preview,
    action: 'unavailable',
    problem: 'config_not_plain',
    after: null,
  });
  show(client);
  fireEvent.click(
    await screen.findByRole('button', { name: 'Settings for Cursor' }),
  );
  const settings = await screen.findByRole('dialog', { name: 'Cursor' });
  fireEvent.click(
    await within(settings).findByRole('button', { name: /Add it for me/ }),
  );
  const review = await screen.findByRole('dialog', {
    name: 'Add Row-Bot to Cursor',
  });
  expect(await within(review).findByRole('alert')).toHaveTextContent(
    "won't change this file",
  );
  expect(
    within(review).queryByRole('button', { name: 'Change the file' }),
  ).toBeNull();
});

it('revokes an editor after a confirmation', async () => {
  const { client } = show();
  fireEvent.click(await screen.findByRole('button', { name: 'Revoke Cursor' }));
  const alert = await screen.findByRole('alertdialog', {
    name: 'Revoke Cursor?',
  });
  expect(client.revoke).not.toHaveBeenCalled();
  fireEvent.click(within(alert).getByRole('button', { name: 'Revoke' }));
  await waitFor(() => expect(client.revoke).toHaveBeenCalledWith('cursor'));
});

it('shows Zed its settings to paste, with no offer to write them', async () => {
  const client = fixture(
    editors.map((editor) =>
      editor.id === 'zed' ? { ...editor, connected: true } : editor,
    ),
  );
  show(client);
  fireEvent.click(
    await screen.findByRole('button', { name: 'Settings for Zed' }),
  );
  const settings = await screen.findByRole('dialog', { name: 'Zed' });
  expect(await within(settings).findByText(/Copy this into/)).toBeVisible();
  expect(
    within(settings).queryByRole('button', { name: /Add it for me/ }),
  ).toBeNull();
});
