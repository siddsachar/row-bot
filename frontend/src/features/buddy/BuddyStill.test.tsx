import { act, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { useBuddyStill } from './BuddyStill';

const mock = vi.hoisted(() => {
  const listeners = new Set<() => void>();
  let state: {
    snapshot: unknown;
    selectedPack: unknown;
    busy: boolean;
    revoked: boolean;
  } = { snapshot: null, selectedPack: null, busy: false, revoked: false };
  return {
    settled: true,
    buddyMedia: vi.fn(),
    session: {
      subscribe: (notify: () => void) => {
        listeners.add(notify);
        return () => listeners.delete(notify);
      },
      getSnapshot: () => state,
      load: vi.fn(async () => undefined),
    },
    set(patch: Partial<typeof state>) {
      state = { ...state, ...patch };
      listeners.forEach((notify) => notify());
    },
  };
});
vi.mock('../../runtime', () => {
  const runtime = {
    controller: { buddyMedia: mock.buddyMedia },
    buddyOwner: { get: () => ({ get: () => mock.session }) },
  };
  return { useRuntime: () => runtime };
});
vi.mock('../../shell-settled', () => ({
  useShellSettled: () => mock.settled,
}));

const pack = (id: string) => ({
  id,
  revision: `${id}-r1`,
  available: true,
  assets: [{ id: 'preview', content_type: 'image/png' }],
});

function Still() {
  return <p>{useBuddyStill('conversation-a') || 'glyph'}</p>;
}

beforeEach(() => {
  let next = 0;
  vi.stubGlobal('URL', {
    createObjectURL: vi.fn(() => `blob:still-${++next}`),
    revokeObjectURL: vi.fn(),
  });
  mock.settled = true;
  mock.session.load.mockClear();
  mock.buddyMedia.mockImplementation(
    async (_conversation: string, packId: string) =>
      new Blob([packId], { type: 'image/png' }),
  );
});
afterEach(() => vi.unstubAllGlobals());

it('shows the selected pack’s still and follows a change of pack (B271)', async () => {
  // The sidebar's Buddy has read the session; its pack is not known yet.
  mock.set({ snapshot: {}, selectedPack: null });
  render(<Still />);
  expect(screen.getByText('glyph')).toBeVisible();
  expect(mock.buddyMedia).not.toHaveBeenCalled();
  await act(async () => mock.set({ selectedPack: pack('tide') }));
  expect(await screen.findByText('blob:still-1')).toBeVisible();
  expect(mock.buddyMedia).toHaveBeenLastCalledWith(
    'conversation-a',
    'tide',
    'preview',
    'tide-r1',
    expect.any(AbortSignal),
  );
  await act(async () => mock.set({ selectedPack: pack('moss') }));
  expect(await screen.findByText('blob:still-2')).toBeVisible();
  expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:still-1');
  // Nothing was read again: the sidebar's Buddy owns the session.
  await act(async () => new Promise((done) => setTimeout(done, 0)));
  expect(mock.session.load).not.toHaveBeenCalled();
});

it('reads Buddy itself once the conversation settles when no Buddy has (phones)', async () => {
  mock.set({ snapshot: null, selectedPack: null, busy: false });
  mock.settled = false;
  const view = render(<Still />);
  await act(async () => new Promise((done) => setTimeout(done, 0)));
  // Not before the open conversation is confirmed (B29).
  expect(mock.session.load).not.toHaveBeenCalled();
  mock.settled = true;
  view.rerender(<Still />);
  await act(async () => new Promise((done) => setTimeout(done, 0)));
  expect(mock.session.load).toHaveBeenCalledOnce();
});

it('leaves a read already under way to the Buddy that started it', async () => {
  mock.set({ snapshot: null, selectedPack: null, busy: true });
  render(<Still />);
  await act(async () => new Promise((done) => setTimeout(done, 0)));
  expect(mock.session.load).not.toHaveBeenCalled();
});
