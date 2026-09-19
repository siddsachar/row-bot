import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import type { WikiSettingsSnapshot } from '../../api/types';
import WikiSettings, {
  WikiSettingsSession,
  type WikiReview,
  type WikiSettingsIO,
} from './WikiSettings';

const revision = 'a'.repeat(64);
const articleId = 'b'.repeat(64);

function setup(available = true) {
  const scope = { selected: available };
  const review: WikiReview = {
    schema_version: 1,
    action: 'wiki.sync',
    scope_id: 'scope',
    revision,
    source_revision: null,
    vault_revision: 'c'.repeat(64),
    articles: [
      {
        article_id: articleId,
        entity_id: 'entity',
        title: 'Reviewed article',
        vault_text: 'Vault version',
        vault_hash: 'd'.repeat(64),
        database_text: 'Database version',
        db_revision: 'e'.repeat(64),
      },
    ],
    enabled: null,
    action_digest: 'f'.repeat(64),
    review_id: 'review-proof',
  };
  const io: WikiSettingsIO = {
    status: vi.fn(async () => ({
      schema_version: 1,
      revision,
      enabled: true,
      availability: scope.selected
        ? ('available' as const)
        : ('scope_required' as const),
      scope_id: scope.selected ? 'scope' : null,
      articles: scope.selected ? 1 : null,
      edited: scope.selected ? 1 : null,
      conflicts: scope.selected ? 0 : null,
    })),
    articles: vi.fn(async () => ({
      schema_version: 1,
      revision: '1'.repeat(64),
      scope_id: 'scope',
      items: [
        {
          article_id: articleId,
          entity_id: 'entity',
          title: 'Reviewed article',
          status: 'edited' as const,
          vault_hash: 'd'.repeat(64),
          db_revision: 'e'.repeat(64),
        },
      ],
      total: 1,
      next_cursor: null,
    })),
    article: vi.fn(async () => review.articles[0]),
    review: vi.fn(async (action) => ({ ...review, action })),
    execute: vi.fn(async (action, _payload, _review, commandId) => ({
      command_id: commandId,
      status: 'completed' as const,
      action,
      count: 1,
      conflicts: 0,
      code: null,
    })),
    receipt: vi.fn(async () => null),
    chooseVault: vi.fn(async () => {
      scope.selected = true;
      return 'Authorized vault';
    }),
  };
  return { io, session: new WikiSettingsSession(io), review };
}

it('loads status and articles passively, then opens without importing', async () => {
  const { io, session } = setup();
  render(<WikiSettings session={session} />);
  await screen.findByText('Reviewed article');
  expect(io.status).toHaveBeenCalledTimes(1);
  expect(io.articles).toHaveBeenCalledTimes(1);
  expect(io.review).not.toHaveBeenCalled();
  expect(io.execute).not.toHaveBeenCalled();
  fireEvent.click(
    screen.getByRole('button', { name: 'Open Reviewed article' }),
  );
  await screen.findByDisplayValue('Vault version');
  expect(io.article).toHaveBeenCalledWith(articleId, expect.any(AbortSignal));
  expect(io.execute).not.toHaveBeenCalled();
});

it('selects an authorized vault explicitly before exposing article controls', async () => {
  const { io, session } = setup(false);
  render(<WikiSettings session={session} />);
  await screen.findByText(/Select an authorized vault/);
  expect(io.articles).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Browse' }));
  await screen.findByText('Reviewed article');
  expect(io.chooseVault).toHaveBeenCalledTimes(1);
  expect(io.articles).toHaveBeenCalledTimes(1);
});

it('shows an editable display path but requires Browse authority before Apply', async () => {
  const { io, session } = setup();
  const snapshot: WikiSettingsSnapshot = {
    availability: 'available',
    enabled: true,
    vault_path: 'C:/Synthetic/Vault',
    path_state: 'available',
    articles: 1,
    conversations: 2,
  };
  render(<WikiSettings compact session={session} snapshot={snapshot} />);
  await screen.findByText('Reviewed article');
  const path = screen.getByRole('textbox', { name: 'Vault path' });
  fireEvent.change(path, { target: { value: 'C:/Untrusted/Typed' } });
  expect(screen.getByText(/Browse to authorize this folder/)).toBeVisible();
  expect(screen.getByRole('button', { name: 'Apply' })).toBeDisabled();
  expect(io.review).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Browse' }));
  await waitFor(() => expect(path).toHaveValue('Authorized: Authorized vault'));
  expect(screen.getByRole('button', { name: 'Apply' })).toBeEnabled();
});

it('retains the exact reviewed versions through navigation and executes once', async () => {
  const { io, session } = setup();
  const first = render(<WikiSettings session={session} />);
  await screen.findByText('Reviewed article');
  fireEvent.click(screen.getByLabelText('Select edit: Reviewed article'));
  fireEvent.click(
    screen.getByRole('button', { name: 'Review 1 selected edits' }),
  );
  await screen.findByRole('region', { name: 'Reviewed wiki action' });
  const retained = session.getSnapshot().review;
  first.unmount();
  render(<WikiSettings session={session} />);
  expect(screen.getByDisplayValue('Vault version')).toBeVisible();
  fireEvent.click(
    screen.getByRole('button', { name: 'Sync reviewed vault edits' }),
  );
  await screen.findByText(/Finished: 1 completed/);
  expect(io.execute).toHaveBeenCalledTimes(1);
  expect(vi.mocked(io.execute).mock.calls[0][2]).toEqual(retained?.value);
  expect(session.hasRetained()).toBe(false);
});

it('never retries an uncertain command and reconciles only its receipt', async () => {
  const { io, session } = setup();
  vi.mocked(io.execute).mockRejectedValueOnce(new Error('lost response'));
  vi.mocked(io.receipt).mockImplementationOnce(async (commandId) => ({
    command_id: commandId,
    status: 'completed',
    action: 'wiki.rebuild',
    count: 2,
    conflicts: 0,
    code: null,
  }));
  render(<WikiSettings session={session} />);
  await screen.findByText('Reviewed article');
  fireEvent.click(screen.getByRole('button', { name: 'Review rebuild' }));
  await screen.findByRole('region', { name: 'Reviewed wiki action' });
  fireEvent.click(
    screen.getByRole('button', { name: 'Rebuild managed wiki files' }),
  );
  await screen.findByText(/outcome is unconfirmed/);
  const commandId = session.getSnapshot().pending?.commandId;
  fireEvent.click(
    screen.getByRole('button', { name: 'Check original receipt' }),
  );
  await screen.findByText(/Finished: 2 completed/);
  expect(io.execute).toHaveBeenCalledTimes(1);
  expect(io.receipt).toHaveBeenCalledWith(commandId, expect.any(AbortSignal));
});

it('purges retained content and ignores late passive reads after disposal', async () => {
  let finish!: (value: Awaited<ReturnType<WikiSettingsIO['status']>>) => void;
  const { io, session } = setup();
  vi.mocked(io.status).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  const pending = session.load();
  await waitFor(() => expect(session.getSnapshot().busy).toBe(true));
  session.dispose();
  finish({
    schema_version: 1,
    revision,
    enabled: true,
    availability: 'available',
    scope_id: 'scope',
    articles: 0,
    edited: 0,
    conflicts: 0,
  });
  await pending;
  expect(session.getSnapshot().status).toBeNull();
  expect(session.hasRetained()).toBe(false);
});
