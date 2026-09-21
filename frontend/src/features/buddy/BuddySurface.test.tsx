import { act, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { BuddyAvatar, type BuddyMediaLoader } from './BuddySurface';
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
  vi.unstubAllGlobals();
});

describe('Buddy avatar lifecycle', () => {
  it('uses canonical status hooks and revokes scoped media on conversation change', async () => {
    let nextUrl = 0;
    const create = vi.fn(() => `blob:buddy-${++nextUrl}`);
    const revoke = vi.fn();
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

    view.rerender(
      <BuddyAvatar
        conversation="conversation-two"
        pack={pack}
        snapshot={snapshot}
        loadMedia={loadMedia}
      />,
    );
    expect(signals[0].aborted).toBe(true);
    expect(revoke).toHaveBeenCalledTimes(2);
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
    expect(revoke).toHaveBeenCalled();
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
});
