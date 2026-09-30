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
    buddyOwner: null,
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
  runtime: 'video',
  generated: true,
  available: true,
  assets: [
    { id: 'preview', content_type: 'image/png' },
    { id: 'idle-loop', content_type: 'video/mp4' },
  ],
  animation_map: { idle: 'idle-loop' },
};

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
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

it('names the sidebar Buddy in its avatar tooltip, keeping the name for assistive tech (B225)', async () => {
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
  const avatar = await screen.findByRole('button', { name: 'Buddy settings' });
  expect(screen.queryByRole('tooltip')).toBeNull();
  await user.hover(avatar.querySelector('.buddy-avatar-frame')!);
  expect(await screen.findByRole('tooltip')).toHaveTextContent('Pip');
  // The companion still carries the custom name for screen readers.
  expect(
    within(
      screen.getByRole('complementary', { name: 'Buddy companion' }),
    ).getByText('Pip'),
  ).toBeInTheDocument();
  expect(screen.getByRole('status')).toHaveTextContent('Ready');
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
  const undock = await screen.findByRole('button', { name: 'Undock Buddy' });
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
  it('uses canonical status hooks and revokes scoped media on conversation change', async () => {
    let nextUrl = 0;
    const create = vi.fn(() => `blob:buddy-${++nextUrl}`);
    // A URL is revoked only once no committed image or video still uses it.
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
    expect(frame).toHaveAttribute('data-energy', '64');
    await waitFor(() =>
      expect(view.container.querySelector('video')).toHaveAttribute(
        'src',
        expect.stringMatching(/^blob:buddy-/),
      ),
    );
    expect(loadMedia).toHaveBeenCalledWith(
      'conversation-one',
      'luminous',
      'idle-loop',
      'pack-revision',
      expect.any(AbortSignal),
    );
    const video = view.container.querySelector('video')!;

    view.rerender(
      <BuddyAvatar
        conversation="conversation-two"
        pack={pack}
        snapshot={snapshot}
        loadMedia={loadMedia}
      />,
    );
    expect(signals[0].aborted).toBe(true);
    // The dropped video stops loading before its source can be revoked.
    expect(video).not.toHaveAttribute('src');
    expect(video).not.toHaveAttribute('poster');
    expect(HTMLMediaElement.prototype.load).toHaveBeenCalled();
    expect(revoke).not.toHaveBeenCalled();
    await waitFor(() => expect(revoke).toHaveBeenCalledTimes(2));
    expect(stillReferenced).toEqual([]);
    await waitFor(() =>
      expect(loadMedia).toHaveBeenCalledWith(
        'conversation-two',
        'luminous',
        'preview',
        'pack-revision',
        expect.any(AbortSignal),
      ),
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
      />,
    );
    await waitFor(() => expect(loadMedia).toHaveBeenCalledTimes(2));

    view.rerender(
      <BuddyAvatar
        conversation="conversation"
        pack={{
          ...pack,
          assets: pack.assets.map((asset) => ({ ...asset })),
          animation_map: { ...pack.animation_map },
        }}
        snapshot={{ ...snapshot, status: { ...snapshot.status } }}
        loadMedia={loadMedia}
      />,
    );

    expect(loadMedia).toHaveBeenCalledTimes(2);
  });

  it('reacts to reduced motion and falls back from video to the selected still', async () => {
    let reduced = false;
    let changed: (() => void) | undefined;
    vi.stubGlobal('matchMedia', () => ({
      get matches() {
        return reduced;
      },
      media: '(prefers-reduced-motion: reduce)',
      addEventListener: (_name: string, listener: () => void) => {
        changed = listener;
      },
      removeEventListener: vi.fn(),
    }));
    let nextUrl = 0;
    const revoke = vi.fn();
    vi.stubGlobal('URL', {
      createObjectURL: () => `blob:motion-${++nextUrl}`,
      revokeObjectURL: revoke,
    });
    const loadMedia = vi.fn<BuddyMediaLoader>(async () => new Blob(['media']));
    const view = render(
      <BuddyAvatar
        conversation="conversation"
        pack={pack}
        snapshot={snapshot}
        loadMedia={loadMedia}
      />,
    );
    await waitFor(() =>
      expect(view.container.querySelector('video')).toBeInTheDocument(),
    );
    reduced = true;
    act(() => changed?.());
    await waitFor(() => {
      expect(view.container.querySelector('video')).not.toBeInTheDocument();
      expect(
        view.container.querySelector('.buddy-avatar-frame'),
      ).toHaveAttribute('data-reduced-motion', 'true');
    });
    await waitFor(() => expect(revoke).toHaveBeenCalled());
    expect(
      loadMedia.mock.calls.filter((call) => call[2] === 'idle-loop'),
    ).toHaveLength(1);
  });

  it('shows an explicit glyph fallback for an unavailable selected pack', () => {
    const loadMedia = vi.fn<BuddyMediaLoader>();
    const view = render(
      <BuddyAvatar
        conversation="conversation"
        pack={{ ...pack, available: false }}
        snapshot={{
          ...snapshot,
          preferences: { ...snapshot.preferences, collapsed: true },
        }}
        loadMedia={loadMedia}
      />,
    );
    expect(view.container.querySelector('.buddy-avatar-frame')).toHaveAttribute(
      'data-media',
      'unavailable',
    );
    expect(view.container.querySelector('.buddy-avatar-frame')).toHaveAttribute(
      'data-collapsed',
      'true',
    );
    expect(view.getByText('Pack unavailable')).toBeInTheDocument();
    expect(loadMedia).not.toHaveBeenCalled();
  });

  it('keeps the still when a motion element fails', async () => {
    let nextUrl = 0;
    vi.stubGlobal('URL', {
      createObjectURL: () => `blob:fallback-${++nextUrl}`,
      revokeObjectURL: vi.fn(),
    });
    const view = render(
      <BuddyAvatar
        conversation="conversation"
        pack={pack}
        snapshot={snapshot}
        loadMedia={async () => new Blob(['media'])}
      />,
    );
    const video = await waitFor(() => {
      const element = view.container.querySelector('video');
      expect(element).toBeInTheDocument();
      return element!;
    });
    fireEvent.error(video);
    expect(view.container.querySelector('video')).not.toBeInTheDocument();
    expect(view.container.querySelector('img')).toHaveAttribute(
      'src',
      expect.stringMatching(/^blob:fallback-/),
    );
    expect(view.container.querySelector('.buddy-avatar-frame')).toHaveAttribute(
      'data-media',
      'still',
    );
  });

  it('settles bundled idle media after two passes and restarts for activity', async () => {
    let nextUrl = 0;
    vi.stubGlobal('URL', {
      createObjectURL: () => `blob:bundled-${++nextUrl}`,
      revokeObjectURL: vi.fn(),
    });
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined);
    const loadMedia = async () => new Blob(['bundled media']);
    const view = render(
      <BuddyAvatar
        conversation="conversation"
        pack={pack}
        snapshot={snapshot}
        loadMedia={loadMedia}
      />,
    );
    const video = await waitFor(() => {
      const element = view.container.querySelector('video');
      expect(element).toBeInTheDocument();
      return element!;
    });
    expect(video.muted).toBe(true);
    expect(video).not.toHaveAttribute('loop');
    fireEvent.ended(video);
    expect(view.container.querySelector('video')).toBeInTheDocument();
    fireEvent.ended(video);
    expect(view.container.querySelector('video')).not.toBeInTheDocument();
    expect(view.container.querySelector('img')).toHaveAttribute(
      'src',
      expect.stringMatching(/^blob:bundled-/),
    );
    view.rerender(
      <BuddyAvatar
        conversation="conversation"
        pack={pack}
        snapshot={{
          ...snapshot,
          status: { ...snapshot.status, label: 'Still ready' },
        }}
        loadMedia={loadMedia}
      />,
    );
    expect(view.container.querySelector('video')).not.toBeInTheDocument();
    view.rerender(
      <BuddyAvatar
        conversation="conversation"
        pack={{
          ...pack,
          animation_map: { ...pack.animation_map, thinking: 'idle-loop' },
        }}
        snapshot={{
          ...snapshot,
          activity: 'thinking',
          status: { ...snapshot.status, event_id: 14 },
        }}
        loadMedia={loadMedia}
      />,
    );
    await waitFor(() =>
      expect(view.container.querySelector('video')).toBeInTheDocument(),
    );
  });

  it('settles a finished run after two passes but loops while work is live (B29)', async () => {
    let nextUrl = 0;
    vi.stubGlobal('URL', {
      createObjectURL: () => `blob:finished-${++nextUrl}`,
      revokeObjectURL: vi.fn(),
    });
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined);
    const loadMedia = async () => new Blob(['clip']);
    const celebrating = {
      ...pack,
      animation_map: { ...pack.animation_map, celebrate: 'idle-loop' },
    };
    const view = render(
      <BuddyAvatar
        conversation="conversation"
        pack={celebrating}
        snapshot={{
          ...snapshot,
          activity: 'completed',
          status: { ...snapshot.status, event_id: 21 },
        }}
        loadMedia={loadMedia}
      />,
    );
    const video = await waitFor(() => {
      const element = view.container.querySelector('video');
      expect(element).toBeInTheDocument();
      return element!;
    });
    fireEvent.ended(video);
    expect(view.container.querySelector('video')).toBeInTheDocument();
    fireEvent.ended(video);
    expect(view.container.querySelector('video')).not.toBeInTheDocument();
    view.rerender(
      <BuddyAvatar
        conversation="conversation"
        pack={{
          ...celebrating,
          animation_map: { ...celebrating.animation_map, working: 'idle-loop' },
        }}
        snapshot={{
          ...snapshot,
          activity: 'tool',
          status: { ...snapshot.status, event_id: 22 },
        }}
        loadMedia={loadMedia}
      />,
    );
    const live = await waitFor(() => {
      const element = view.container.querySelector('video');
      expect(element).toBeInTheDocument();
      return element!;
    });
    for (let pass = 0; pass < 4; pass += 1) fireEvent.ended(live);
    expect(view.container.querySelector('video')).toBeInTheDocument();
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
