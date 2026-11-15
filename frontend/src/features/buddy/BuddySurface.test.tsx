import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import BuddySurface, {
  BuddyAvatar,
  BuddyPortrait,
  rememberBuddyMedia,
  type BuddyMediaLoader,
} from './BuddySurface';
import type { BuddyPack, BuddySnapshot } from '../shell/BuddyControls';

const snapshot: BuddySnapshot = {
  schema_version: 1,
  revision: 'buddy-revision',
  placement: 'docked',
  native_placement_retained: true,
  preferences: {
    visible: true,
    collapsed: false,
    display_name: 'Buddy',
    personality: 'warm_mystical',
    personality_description: 'Warm and luminous',
    bubble_verbosity: 'normal',
    animation_intensity: 'normal',
    pack_id: 'luminous',
  },
  status: {
    mood: 'curious',
    animation: 'idle',
    energy: 64,
    focus: 42,
    alert: 7,
    event_id: 12,
    label: 'Ready',
  },
};

const surface = vi.hoisted(() => {
  const buddyPlacement = vi.fn();
  const globalBuddy = vi.fn();
  const globalBuddyPack = vi.fn();
  const globalBuddyMedia = vi.fn();
  return {
    state: { selectedConversationId: null as string | null },
    buddyOwner: null as null | { get: () => { get: (id: string) => unknown } },
    navigate: vi.fn(),
    globalBuddy,
    globalBuddyPack,
    globalBuddyMedia,
    buddyPlacement,
    platform: { buddyPlacement },
    controller: {
      globalBuddy,
      globalBuddyPack,
      globalBuddyMedia,
      buddyMedia: vi.fn(),
    },
  };
});

vi.mock('../../runtime', () => ({
  useClientState: () => surface.state,
  useRuntime: () => ({
    controller: surface.controller,
    buddyOwner: surface.buddyOwner,
    platform: surface.platform,
  }),
}));

vi.mock('react-router-dom', () => ({
  useNavigate: () => surface.navigate,
  useSearchParams: () => [new URLSearchParams(), vi.fn()],
}));

const pack: BuddyPack = {
  id: 'luminous',
  name: 'Luminous',
  revision: 'pack-revision',
  runtime: 'living',
  generated: true,
  available: true,
  assets: [
    { id: 'art', content_type: 'image/png' },
    { id: 'preview', content_type: 'image/png' },
    { id: 'rig', content_type: 'application/json' },
  ],
  animation_map: {},
};

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
  surface.buddyOwner = null;
});

beforeEach(() => {
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(
    () => undefined,
  );
  vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(
    () => undefined,
  );
  surface.buddyPlacement.mockResolvedValue({
    status: 'unavailable',
    reason: 'browser',
  });
});

it('renders passive global Buddy state without selecting a conversation', async () => {
  surface.state.selectedConversationId = null;
  surface.globalBuddy.mockResolvedValue({
    ...snapshot,
    conversation_id: null,
    activity: 'idle',
  });
  surface.globalBuddyPack.mockResolvedValue({ ...pack, available: false });

  const view = render(<BuddySurface />);

  await waitFor(() =>
    expect(view.getByLabelText('Buddy settings')).toBeInTheDocument(),
  );
  expect(surface.globalBuddy).toHaveBeenCalled();
  expect(surface.globalBuddyPack).toHaveBeenCalledWith(
    'luminous',
    expect.any(AbortSignal),
  );
  expect(view.getByRole('status')).toHaveTextContent('Ready');
});

it('shows the sidebar Buddy with a quiet Ready, its name in the tooltip and peek, and opens a peek with its actions', async () => {
  surface.state.selectedConversationId = null;
  surface.globalBuddy.mockResolvedValue({
    ...snapshot,
    preferences: { ...snapshot.preferences, display_name: 'Pip' },
    conversation_id: null,
    activity: 'idle',
  });
  surface.globalBuddyPack.mockResolvedValue(pack);
  const user = userEvent.setup();
  render(<BuddySurface />);
  const avatar = await screen.findByRole('button', { name: 'Open Pip' });
  const card = screen.getByRole('complementary', { name: 'Buddy companion' });
  // What it is doing beside the avatar, never "Idle"; the name stays for screen readers.
  expect(within(card).getByText('Pip')).toHaveClass('visually-hidden');
  expect(within(card).getByRole('status')).toHaveTextContent('Ready');
  expect(card).not.toHaveTextContent('Idle');
  // Keyboard focus and hover show both in the avatar's tooltip.
  act(() => avatar.focus());
  expect(await screen.findByRole('tooltip')).toHaveTextContent('Pip · Ready');
  act(() => avatar.blur());
  await waitFor(() => expect(screen.queryByRole('tooltip')).toBeNull());
  await user.hover(avatar);
  expect(await screen.findByRole('tooltip')).toHaveTextContent('Pip · Ready');
  // Settings sits on the card's edge, and in the peek the avatar opens.
  expect(
    within(card).getByRole('button', { name: 'Buddy settings' }),
  ).toBeInTheDocument();
  await user.click(avatar);
  const peek = await screen.findByRole('dialog', { name: 'Pip' });
  expect(peek).toHaveTextContent('PipReady');
  expect(
    within(peek).getByRole('button', { name: 'Settings' }),
  ).toBeInTheDocument();
});

it('hides the duplicate docked Buddy while native overlay is out and restores it on Dock', async () => {
  surface.state.selectedConversationId = null;
  surface.globalBuddy.mockResolvedValue({
    ...snapshot,
    conversation_id: null,
    activity: 'idle',
  });
  surface.globalBuddyPack.mockResolvedValue(pack);
  surface.buddyPlacement
    .mockResolvedValueOnce({
      status: 'ok',
      value: { placement: 'desktop', visible: true },
    })
    .mockResolvedValueOnce({
      status: 'ok',
      value: { placement: 'docked', visible: true },
    });
  render(<BuddySurface />);
  await waitFor(() =>
    expect(screen.getByRole('button', { name: 'Dock Buddy' })).toBeVisible(),
  );
  expect(screen.queryByLabelText('Buddy settings')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Dock Buddy' }));
  await waitFor(() =>
    expect(screen.getByLabelText('Buddy settings')).toBeVisible(),
  );
  expect(surface.buddyPlacement).toHaveBeenCalledWith('dock');
});

it('offers native Undock through the placement bridge', async () => {
  surface.state.selectedConversationId = null;
  surface.globalBuddy.mockResolvedValue({
    ...snapshot,
    conversation_id: null,
    activity: 'idle',
  });
  surface.globalBuddyPack.mockResolvedValue(pack);
  surface.buddyPlacement
    .mockResolvedValueOnce({
      status: 'ok',
      value: { placement: 'docked', visible: true },
    })
    .mockResolvedValueOnce({
      status: 'ok',
      value: { placement: 'desktop', visible: true },
    });
  render(<BuddySurface />);
  const undock = await screen.findByRole('button', { name: 'Pop out Buddy' });
  fireEvent.click(undock);
  await waitFor(() =>
    expect(screen.getByRole('button', { name: 'Dock Buddy' })).toBeVisible(),
  );
  expect(surface.buddyPlacement).toHaveBeenCalledWith('tear_off', {
    x: expect.any(Number),
    y: expect.any(Number),
  });
});

describe('Buddy avatar lifecycle', () => {
  it('shows Buddy’s state and loads its still for the conversation, revoking it on change', async () => {
    let nextUrl = 0;
    const create = vi.fn(() => `blob:buddy-${++nextUrl}`);
    // A URL is revoked only once no committed image still uses it.
    const stillReferenced: string[] = [];
    const revoke = vi.fn((url: string) => {
      if (document.body.innerHTML.includes(url)) stillReferenced.push(url);
    });
    vi.stubGlobal('URL', { createObjectURL: create, revokeObjectURL: revoke });
    const signals: AbortSignal[] = [];
    const loadMedia = vi.fn(async (...args: Parameters<BuddyMediaLoader>) => {
      signals.push(args[4]);
      return new Blob([args[2]]);
    });
    const view = render(
      <BuddyAvatar
        conversation="conversation-one"
        pack={pack}
        snapshot={snapshot}
        loadMedia={loadMedia}
      />,
    );
    const frame = view.container.querySelector('.buddy-avatar-frame');
    expect(frame).toHaveAttribute('data-state', 'idle');
    expect(frame).toHaveAttribute('data-buddy-mood', 'curious');
    expect(frame).toHaveAttribute('data-media', 'living');
    await waitFor(() =>
      expect(view.container.querySelector('img.buddy-avatar')).toHaveAttribute(
        'src',
        'blob:buddy-1',
      ),
    );
    expect(loadMedia).toHaveBeenCalledWith(
      'conversation-one',
      'luminous',
      'art',
      'pack-revision',
      expect.any(AbortSignal),
    );

    view.rerender(
      <BuddyAvatar
        conversation="conversation-two"
        pack={pack}
        snapshot={snapshot}
        loadMedia={loadMedia}
      />,
    );
    expect(signals[0].aborted).toBe(true);
    expect(revoke).not.toHaveBeenCalled();
    await waitFor(() => expect(revoke).toHaveBeenCalledWith('blob:buddy-1'), {
      timeout: 3000,
    });
    expect(stillReferenced).toEqual([]);
    expect(loadMedia).toHaveBeenCalledWith(
      'conversation-two',
      'luminous',
      'art',
      'pack-revision',
      expect.any(AbortSignal),
    );
  });

  it('does not reload unchanged media when the canonical pack is reprojected', async () => {
    vi.stubGlobal('URL', {
      createObjectURL: () => 'blob:stable-pack',
      revokeObjectURL: vi.fn(),
    });
    const loadMedia = vi.fn<BuddyMediaLoader>(async () => new Blob(['media']));
    const view = render(
      <BuddyAvatar
        conversation="conversation"
        pack={pack}
        snapshot={snapshot}
        loadMedia={loadMedia}
        preview
      />,
    );
    await waitFor(() => expect(loadMedia).toHaveBeenCalledTimes(1));

    view.rerender(
      <BuddyAvatar
        conversation="conversation"
        pack={{ ...pack, assets: pack.assets.map((asset) => ({ ...asset })) }}
        snapshot={{ ...snapshot, status: { ...snapshot.status } }}
        loadMedia={loadMedia}
        preview
      />,
    );

    expect(loadMedia).toHaveBeenCalledTimes(1);
  });

  it('keeps showing the still where a living Buddy cannot draw', async () => {
    vi.stubGlobal('URL', {
      createObjectURL: () => 'blob:still',
      revokeObjectURL: vi.fn(),
    });
    // jsdom has no WebGL2: the living renderer must leave the still in place.
    const loadMedia = vi.fn<BuddyMediaLoader>(async () => new Blob(['media']));
    const view = render(
      <BuddyAvatar
        conversation={null}
        pack={pack}
        snapshot={snapshot}
        loadMedia={loadMedia}
      />,
    );
    await waitFor(() =>
      expect(view.container.querySelector('.living-buddy')).toHaveAttribute(
        'data-runtime',
        'still',
      ),
    );
    expect(view.container.querySelector('img.buddy-avatar')).toHaveAttribute(
      'src',
      'blob:still',
    );
  });

  it('shows pack previews as stills, never a living renderer', async () => {
    vi.stubGlobal('URL', {
      createObjectURL: () => 'blob:preview',
      revokeObjectURL: vi.fn(),
    });
    const loadMedia = vi.fn<BuddyMediaLoader>(async () => new Blob(['media']));
    const view = render(
      <BuddyAvatar
        conversation={null}
        pack={pack}
        snapshot={snapshot}
        loadMedia={loadMedia}
        preview
      />,
    );
    await waitFor(() =>
      expect(
        view.container.querySelector('.buddy-avatar-frame'),
      ).toHaveAttribute('data-media', 'still'),
    );
    expect(view.container.querySelector('canvas')).not.toBeInTheDocument();
    expect(loadMedia).toHaveBeenCalledTimes(1);
  });

  it('starts no blob: image while the page unloads, and shows the still again when it returns', async () => {
    vi.stubGlobal('URL', {
      createObjectURL: () => 'blob:still',
      revokeObjectURL: vi.fn(),
    });
    let arrive: (blob: Blob) => void = () => undefined;
    const loadMedia = vi.fn<BuddyMediaLoader>(
      () => new Promise((resolve) => (arrive = resolve)),
    );
    const view = render(
      <BuddyAvatar
        conversation={null}
        pack={pack}
        snapshot={snapshot}
        loadMedia={loadMedia}
        preview
      />,
    );
    const image = () => view.container.querySelector('img.buddy-avatar');
    // The still arrives as the page unloads: the browser would load a blob:
    // image started now as a ping, which the page's connect-src refuses.
    act(() => {
      window.dispatchEvent(new Event('pagehide'));
    });
    await act(async () => arrive(new Blob(['still'])));
    expect(image()).toHaveAttribute('data-glyph', 'true');
    expect(image()?.getAttribute('src')).not.toMatch(/^blob:/);

    // Back from the back/forward cache, Buddy shows its still.
    act(() => {
      window.dispatchEvent(new Event('pageshow'));
    });
    expect(image()).toHaveAttribute('src', 'blob:still');
  });

  it('shows the still again when the person stays after beforeunload', async () => {
    vi.stubGlobal('URL', {
      createObjectURL: () => 'blob:still',
      revokeObjectURL: vi.fn(),
    });
    const loadMedia = vi.fn<BuddyMediaLoader>(async () => new Blob(['media']));
    const props = { conversation: null, pack, loadMedia, preview: true };
    const view = render(<BuddyAvatar {...props} snapshot={snapshot} />);
    const image = () => view.container.querySelector('img.buddy-avatar');
    await waitFor(() => expect(image()).toHaveAttribute('src', 'blob:still'));

    // A render while beforeunload runs (an unsaved draft asks to stay)...
    act(() => {
      window.dispatchEvent(new Event('beforeunload'));
      view.rerender(
        <BuddyAvatar
          {...props}
          snapshot={{ ...snapshot, activity: 'thinking' }}
        />,
      );
    });
    expect(image()).toHaveAttribute('data-glyph', 'true');
    // ...and the page carries on.
    await waitFor(() => expect(image()).toHaveAttribute('src', 'blob:still'));
  });

  it('shows an explicit glyph fallback for an unavailable selected pack', () => {
    const loadMedia = vi.fn<BuddyMediaLoader>();
    const view = render(
      <BuddyAvatar
        conversation="conversation"
        pack={{ ...pack, available: false }}
        snapshot={snapshot}
        loadMedia={loadMedia}
      />,
    );
    expect(loadMedia).not.toHaveBeenCalled();
    expect(view.container.querySelector('.buddy-avatar-frame')).toHaveAttribute(
      'data-media',
      'unavailable',
    );
    expect(view.getByText('Pack unavailable')).toBeInTheDocument();
  });
});

it('reuses pack media across conversations for the same pack revision', async () => {
  const load = vi.fn<BuddyMediaLoader>(
    async (_c, _p, asset) => new Blob([asset]),
  );
  const owner = {};
  const remembered = rememberBuddyMedia(owner, load);
  const signal = new AbortController().signal;
  await remembered('conversation-a', 'glyph', 'idle', 'r1', signal);
  // Switching conversations shows the same pack: no second download.
  await remembered('conversation-b', 'glyph', 'idle', 'r1', signal);
  expect(load).toHaveBeenCalledTimes(1);
  // A new pack revision or another asset downloads again.
  await remembered('conversation-b', 'glyph', 'idle', 'r2', signal);
  await remembered('conversation-b', 'glyph', 'preview', 'r2', signal);
  expect(load).toHaveBeenCalledTimes(3);
  // Another runtime owner keeps its own media.
  await rememberBuddyMedia({}, load)(
    'conversation-a',
    'glyph',
    'idle',
    'r1',
    signal,
  );
  expect(load).toHaveBeenCalledTimes(4);
});

describe('BuddyPortrait: Buddy beside the Overview greeting (B269)', () => {
  it('shows the global Buddy live, and nothing while Buddy is hidden', async () => {
    surface.state.selectedConversationId = null;
    surface.globalBuddy.mockResolvedValueOnce({
      ...snapshot,
      conversation_id: null,
      activity: 'idle',
    });
    surface.globalBuddyPack.mockResolvedValue(pack);
    const { container, unmount } = render(<BuddyPortrait />);
    await waitFor(() =>
      expect(
        container.querySelector('.buddy-avatar-frame[data-buddy-mood]'),
      ).toHaveAttribute('data-buddy-mood', 'curious'),
    );
    expect(surface.globalBuddyPack).toHaveBeenCalledWith(
      'luminous',
      expect.any(AbortSignal),
    );
    unmount();

    surface.globalBuddy.mockResolvedValueOnce({
      ...snapshot,
      preferences: { ...snapshot.preferences, visible: false },
      conversation_id: null,
      activity: 'idle',
    });
    const hidden = render(<BuddyPortrait />);
    await waitFor(() => expect(hidden.container).toBeEmptyDOMElement());
  });

  it('follows the open conversation’s Buddy session without reading it again', () => {
    surface.state.selectedConversationId = 'conversation-a';
    const view = {
      snapshot: { ...snapshot, activity: 'thinking' },
      selectedPack: pack,
      busy: false,
      revoked: false,
    };
    const session = {
      subscribe: () => () => undefined,
      getSnapshot: () => view,
      observe: vi.fn(() => () => undefined),
      load: vi.fn(),
    };
    surface.buddyOwner = { get: () => ({ get: () => session }) };
    const { container } = render(<BuddyPortrait />);
    expect(container.querySelector('.buddy-avatar-frame')).toHaveAttribute(
      'data-state',
      'thinking',
    );
    expect(surface.globalBuddy).not.toHaveBeenCalled();
    expect(session.load).not.toHaveBeenCalled();
  });
});

it('shows Learning’s quiet line while a personal model trains', async () => {
  surface.state.selectedConversationId = null;
  surface.globalBuddy.mockResolvedValue({
    ...snapshot,
    status: {
      ...snapshot.status,
      mood: 'focused',
      animation: 'step_check',
      label: 'Learning: training your personal model',
    },
    conversation_id: null,
    activity: 'idle',
  });
  surface.globalBuddyPack.mockResolvedValue({ ...pack, available: false });

  const view = render(<BuddySurface />);

  await waitFor(() =>
    expect(view.getByRole('status')).toHaveTextContent(
      'Learning: training your personal model',
    ),
  );
});
