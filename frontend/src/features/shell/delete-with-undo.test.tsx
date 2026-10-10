import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import {
  ACTION_NOTICE_MS,
  OverlayProvider,
  useOverlay,
} from '../../ui/overlays';
import { deleteWithUndo, useDeletingConversations } from './delete-with-undo';

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

function owner(
  command: (target: string) => Promise<unknown> = async () => ({
    status: 'DeleteCompleted',
  }),
) {
  return {
    getSnapshot: () =>
      ({ handshake: { client_session_id: 'session-a' } }) as never,
    command: vi.fn(command) as never as ReturnType<typeof vi.fn>,
    forgetConversation: vi.fn(),
    loadMoreConversations: vi.fn(async () => {}),
  };
}

const TIDES = { id: 'conversation-a', title: 'Tides', revision: '3' };
const KELP = { id: 'conversation-b', title: 'Kelp', revision: '5' };

function Harness({ controller }: { controller: ReturnType<typeof owner> }) {
  const { notify } = useOverlay();
  const deleting = useDeletingConversations();
  return (
    <>
      <output aria-label="Leaving the lists">{[...deleting].join(' ')}</output>
      {[TIDES, KELP].map((row) => (
        <button
          key={row.id}
          onClick={() => deleteWithUndo(controller as never, notify, row)}
        >
          {`Delete ${row.title}`}
        </button>
      ))}
    </>
  );
}

function setup(controller = owner()) {
  render(
    <OverlayProvider>
      <Harness controller={controller} />
    </OverlayProvider>,
  );
  return controller;
}

const leaving = () => screen.getByRole('status', { name: 'Leaving the lists' });
const notice = (text: string) =>
  within(screen.getByText(text).closest<HTMLElement>('.toast')!);
/** Let the sent delete's promise chain finish. */
const settle = () =>
  act(async () => {
    for (let tick = 0; tick < 10; tick += 1) await Promise.resolve();
  });

it('hides a confirmed conversation at once and deletes it only when its notice ends', async () => {
  const controller = setup();
  fireEvent.click(screen.getByRole('button', { name: 'Delete Tides' }));
  expect(leaving()).toHaveTextContent('conversation-a');
  expect(controller.forgetConversation).toHaveBeenCalledWith('conversation-a');
  expect(
    notice("Deleted 'Tides'.").getByRole('button', { name: 'Undo' }),
  ).toBeVisible();
  await act(async () => {
    vi.advanceTimersByTime(ACTION_NOTICE_MS - 100);
  });
  expect(controller.command).not.toHaveBeenCalled();
  await act(async () => {
    vi.advanceTimersByTime(200);
  });
  await settle();
  // Its own id and the revision the person confirmed, once.
  expect(controller.command).toHaveBeenCalledTimes(1);
  expect(controller.command).toHaveBeenCalledWith(
    'conversation-a',
    expect.objectContaining({
      type: 'conversation.delete',
      expected_revision: '3',
    }),
    expect.any(String),
  );
  expect(leaving()).toHaveTextContent('');
  expect(controller.loadMoreConversations).toHaveBeenCalledWith(true);
  expect(screen.queryByText("Deleted 'Tides'.")).toBeNull();
});

it('Undo brings the conversation back and nothing is ever deleted', async () => {
  const controller = setup();
  fireEvent.click(screen.getByRole('button', { name: 'Delete Tides' }));
  await act(async () =>
    fireEvent.click(
      notice("Deleted 'Tides'.").getByRole('button', { name: 'Undo' }),
    ),
  );
  expect(leaving()).toHaveTextContent('');
  expect(controller.loadMoreConversations).toHaveBeenCalledWith(true);
  await act(async () => {
    vi.advanceTimersByTime(ACTION_NOTICE_MS * 3);
  });
  await settle();
  expect(controller.command).not.toHaveBeenCalled();
});

it('handles a second delete while the first waits: each is sent on its own', async () => {
  const controller = setup();
  fireEvent.click(screen.getByRole('button', { name: 'Delete Tides' }));
  await act(async () => {
    vi.advanceTimersByTime(1000);
  });
  fireEvent.click(screen.getByRole('button', { name: 'Delete Kelp' }));
  expect(leaving()).toHaveTextContent('conversation-a conversation-b');
  // Dismissing Kelp's notice ends it: only Kelp is deleted now.
  await act(async () =>
    fireEvent.click(
      notice("Deleted 'Kelp'.").getByRole('button', {
        name: 'Dismiss notification',
      }),
    ),
  );
  await settle();
  expect(controller.command).toHaveBeenCalledTimes(1);
  expect(controller.command).toHaveBeenLastCalledWith(
    'conversation-b',
    expect.objectContaining({ expected_revision: '5' }),
    expect.any(String),
  );
  expect(leaving()).toHaveTextContent('conversation-a');
  await act(async () => {
    vi.advanceTimersByTime(ACTION_NOTICE_MS);
  });
  await settle();
  expect(controller.command).toHaveBeenCalledTimes(2);
  expect(controller.command).toHaveBeenLastCalledWith(
    'conversation-a',
    expect.objectContaining({ expected_revision: '3' }),
    expect.any(String),
  );
  expect(leaving()).toHaveTextContent('');
});

it('shows the conversation again and says why when the delete is refused', async () => {
  const controller = setup(
    owner(async () => {
      throw { code: 'revision_conflict' };
    }),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Delete Tides' }));
  await act(async () =>
    fireEvent.click(
      notice("Deleted 'Tides'.").getByRole('button', {
        name: 'Dismiss notification',
      }),
    ),
  );
  await settle();
  expect(controller.command).toHaveBeenCalledTimes(1);
  expect(leaving()).toHaveTextContent('');
  expect(controller.loadMoreConversations).toHaveBeenCalledWith(true);
  expect(
    screen.getByText(
      'This changed somewhere else. Load it again, then make your change.',
    ),
  ).toBeInTheDocument();
});
