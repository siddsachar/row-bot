import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { useEffect } from 'react';
import { afterEach, expect, it, vi } from 'vitest';
import {
  ACTION_NOTICE_MS,
  OverlayProvider,
  useOverlay,
  type NoticeAction,
} from './overlays';

afterEach(() => vi.useRealTimers());

function Notify({ onUndo }: { onUndo: () => void }) {
  const overlay = useOverlay();
  useEffect(() => {
    overlay.notify('Removed Tides deck from this conversation.', undefined, {
      label: 'Undo',
      onAction: onUndo,
    });
    // Once, on mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return null;
}

it('shows an Undo toast for an easy-to-regret removal and runs it once (decision 19)', async () => {
  const onUndo = vi.fn();
  render(
    <OverlayProvider>
      <Notify onUndo={onUndo} />
    </OverlayProvider>,
  );
  expect(
    await screen.findByText('Removed Tides deck from this conversation.'),
  ).toBeInTheDocument();
  const undo = screen.getByRole('button', { name: 'Undo' });
  await act(async () => fireEvent.click(undo));
  expect(onUndo).toHaveBeenCalledTimes(1);
  expect(
    screen.queryByText('Removed Tides deck from this conversation.'),
  ).toBeNull();
});

/** Each click raises one notice with Undo and an end callback. */
function Notices({ actions }: { actions: Record<string, () => NoticeAction> }) {
  const { notify } = useOverlay();
  return (
    <>
      {Object.entries(actions).map(([message, action]) => (
        <button
          key={message}
          onClick={() => notify(message, undefined, action())}
        >
          {`Raise ${message}`}
        </button>
      ))}
    </>
  );
}

function action() {
  const value = { label: 'Undo', onAction: vi.fn(), onEnd: vi.fn() };
  return value;
}

it('ends a notice with Undo once when it times out or is dismissed, never after Undo', async () => {
  vi.useFakeTimers();
  const timedOut = action();
  const dismissed = action();
  const undone = action();
  render(
    <OverlayProvider>
      <Notices
        actions={{
          'Deleted one': () => timedOut,
          'Deleted two': () => dismissed,
          'Deleted three': () => undone,
        }}
      />
    </OverlayProvider>,
  );
  for (const message of ['Deleted one', 'Deleted two', 'Deleted three'])
    fireEvent.click(screen.getByRole('button', { name: `Raise ${message}` }));
  const toast = (text: string) =>
    within(screen.getByText(text).closest<HTMLElement>('.toast')!);
  await act(async () =>
    fireEvent.click(
      toast('Deleted three').getByRole('button', { name: 'Undo' }),
    ),
  );
  expect(undone.onAction).toHaveBeenCalledTimes(1);
  await act(async () =>
    fireEvent.click(
      toast('Deleted two').getByRole('button', {
        name: 'Dismiss notification',
      }),
    ),
  );
  expect(dismissed.onEnd).toHaveBeenCalledTimes(1);
  expect(timedOut.onEnd).not.toHaveBeenCalled();
  await act(async () => {
    vi.advanceTimersByTime(ACTION_NOTICE_MS + 100);
  });
  expect(timedOut.onEnd).toHaveBeenCalledTimes(1);
  await act(async () => {
    vi.advanceTimersByTime(ACTION_NOTICE_MS * 2);
  });
  expect(timedOut.onEnd).toHaveBeenCalledTimes(1);
  expect(dismissed.onEnd).toHaveBeenCalledTimes(1);
  expect(undone.onEnd).not.toHaveBeenCalled();
  expect(timedOut.onAction).not.toHaveBeenCalled();
  expect(dismissed.onAction).not.toHaveBeenCalled();
});

it('never merges notices with work waiting on them, and ends one pushed out by newer notices', () => {
  const first = action();
  const second = action();
  const later = [action(), action(), action()];
  let next = 0;
  render(
    <OverlayProvider>
      <Notices
        actions={{
          "Deleted 'New chat'.": () => (next++ === 0 ? first : second),
          Later: () => later.shift()!,
        }}
      />
    </OverlayProvider>,
  );
  const raise = (message: string) =>
    fireEvent.click(screen.getByRole('button', { name: `Raise ${message}` }));
  raise("Deleted 'New chat'.");
  raise("Deleted 'New chat'.");
  // Two deletions of chats with the same name: two notices, two Undos.
  expect(screen.getAllByText("Deleted 'New chat'.")).toHaveLength(2);
  raise('Later');
  expect(first.onEnd).not.toHaveBeenCalled();
  // A fourth notice pushes the oldest out: that one has ended.
  raise('Later');
  expect(first.onEnd).toHaveBeenCalledTimes(1);
  expect(second.onEnd).not.toHaveBeenCalled();
  expect(screen.getAllByText("Deleted 'New chat'.")).toHaveLength(1);
});
