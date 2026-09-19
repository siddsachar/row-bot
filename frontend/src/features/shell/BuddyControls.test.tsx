import { act, fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import BuddyControls, {
  type BuddyControlsProps,
  type BuddySnapshot,
  type BuddyPackPage,
} from './BuddyControls';

const snapshot: BuddySnapshot = {
  schema_version: 1,
  revision: 'revision-one',
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
    pack_id: 'glyph',
  },
  status: {
    mood: 'curious',
    animation: 'idle_breathe',
    energy: 68,
    focus: 20,
    alert: 0,
    event_id: 1,
    label: 'Ready',
  },
};
const page: BuddyPackPage = {
  revision: 'packs-one',
  total: 2,
  next_cursor: 'next',
  packs: [
    {
      id: 'glyph',
      name: 'Buddy Glyph',
      revision: 'pack-one',
      runtime: 'generated_still',
      generated: false,
      available: true,
      assets: [
        { id: 'preview', content_type: 'image/png' },
        { id: 'idle', content_type: 'video/mp4' },
        { id: 'wave', content_type: 'video/webm' },
      ],
      animation_map: {},
    },
  ],
};
function props(): BuddyControlsProps {
  return {
    scopeKey: 'owner-A',
    snapshot,
    settingsOpen: true,
    packs: page,
    currentRunId: 'run-one',
    onSettings: vi.fn(),
    save: vi.fn(async () => snapshot),
    loadPacks: vi.fn(async () => page),
    reload: vi.fn(async () => snapshot),
    stop: vi.fn(async () => {}),
  };
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

describe('Buddy shared companion and preferences', () => {
  it('is passive, uses the injected avatar, and keeps native placement explicit', () => {
    const input = props();
    render(
      <BuddyControls
        {...input}
        renderAvatar={() => <span>Owned avatar</span>}
      />,
    );
    expect(screen.getByText('Owned avatar')).toBeVisible();
    expect(
      screen.getByText(
        'Your saved desktop placement is retained for the native app.',
      ),
    ).toBeVisible();
    expect(input.save).not.toHaveBeenCalled();
    expect(input.loadPacks).not.toHaveBeenCalled();
    expect(input.stop).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Buddy settings' }));
    expect(input.onSettings).toHaveBeenCalledOnce();
  });

  it('keeps secondary companion settings closed and presents pack readiness before the grid', () => {
    const input = props();
    render(<BuddyControls {...input} />);

    const advanced = screen.getByText('Advanced companion').closest('details');
    expect(advanced).not.toHaveAttribute('open');
    expect(
      screen.getByText('Selected: Glyph. Motion pack ready.'),
    ).toBeVisible();
    expect(screen.getByText('2 clips · Ready')).toBeVisible();
    expect(screen.queryByText('Buddy Glyph')).not.toBeInTheDocument();
    expect(
      screen.getByText('Selected: Glyph. Motion pack ready.')
        .nextElementSibling,
    ).toBe(screen.getByRole('group', { name: 'Buddy looks' }));

    fireEvent.click(screen.getByText('Advanced companion'));
    expect(advanced).toHaveAttribute('open');
    expect(screen.getByLabelText('Compact Buddy')).toBeEnabled();
    expect(screen.getByLabelText('Buddy name')).toHaveValue('Buddy');
    expect(screen.getByLabelText('Animation intensity')).toHaveValue('normal');
    expect(screen.getByLabelText('Style notes (optional)')).toHaveValue(
      'Warm and luminous',
    );
  });

  it('refreshes looks only after an explicit action', async () => {
    const input = props();
    render(<BuddyControls {...input} />);
    expect(input.reload).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Refresh looks' }));
    await screen.findByText('Buddy looks refreshed.');
    expect(input.reload).toHaveBeenCalledOnce();
  });

  it('saves only explicit changes with the captured revision', async () => {
    const input = props();
    input.save = vi.fn(async (): Promise<BuddySnapshot> => ({
      ...snapshot,
      revision: 'revision-two',
      preferences: { ...snapshot.preferences, bubble_verbosity: 'quiet' },
    }));
    render(<BuddyControls {...input} />);
    fireEvent.change(screen.getByLabelText('Bubble style'), {
      target: { value: 'quiet' },
    });
    fireEvent.click(
      screen.getByRole('button', { name: 'Save Buddy preferences' }),
    );
    await screen.findByText('Buddy preferences saved.');
    expect(input.save).toHaveBeenCalledWith(
      { bubble_verbosity: 'quiet' },
      'revision-one',
    );
    expect(screen.getByLabelText('Bubble style')).toHaveValue('quiet');
  });

  it('preserves a dirty draft across a saved revision change and requires reload', () => {
    const input = props();
    const view = render(<BuddyControls {...input} />);
    fireEvent.change(screen.getByLabelText('Companion personality'), {
      target: { value: 'calm_focus' },
    });
    view.rerender(
      <BuddyControls
        {...input}
        snapshot={{ ...snapshot, revision: 'revision-two' }}
      />,
    );
    expect(screen.getByLabelText('Companion personality')).toHaveValue(
      'calm_focus',
    );
    expect(
      screen.getByRole('button', { name: 'Save Buddy preferences' }),
    ).toBeDisabled();
    fireEvent.click(
      screen.getByRole('button', { name: 'Reload saved preferences' }),
    );
    expect(screen.getByLabelText('Companion personality')).toHaveValue(
      'warm_mystical',
    );
  });

  it('retains pending save ownership through scope changes and ignores its late result', async () => {
    const input = props();
    const pending = deferred<BuddySnapshot>();
    input.save = vi.fn(() => pending.promise);
    const view = render(<BuddyControls {...input} />);
    fireEvent.change(screen.getByLabelText('Companion personality'), {
      target: { value: 'calm_focus' },
    });
    fireEvent.click(
      screen.getByRole('button', { name: 'Save Buddy preferences' }),
    );
    view.rerender(
      <BuddyControls
        {...input}
        scopeKey="owner-B"
        snapshot={{ ...snapshot, revision: 'B' }}
      />,
    );
    expect(
      screen.getByRole('button', { name: 'Save Buddy preferences' }),
    ).toBeDisabled();
    await act(async () =>
      pending.resolve({
        ...snapshot,
        preferences: { ...snapshot.preferences, personality: 'calm_focus' },
      }),
    );
    expect(screen.getByLabelText('Companion personality')).toHaveValue(
      'warm_mystical',
    );
    expect(
      screen.queryByText('Buddy preferences saved.'),
    ).not.toBeInTheDocument();
    expect(input.save).toHaveBeenCalledOnce();
  });

  it('removes all content on revoked snapshot and does not apply a late success', async () => {
    const input = props();
    const pending = deferred<BuddySnapshot>();
    input.save = vi.fn(() => pending.promise);
    const view = render(<BuddyControls {...input} />);
    fireEvent.click(screen.getByLabelText('Show Buddy'));
    fireEvent.click(
      screen.getByRole('button', { name: 'Save Buddy preferences' }),
    );
    view.rerender(<BuddyControls {...input} snapshot={null} />);
    await act(async () => pending.resolve(snapshot));
    expect(screen.queryByRole('complementary')).not.toBeInTheDocument();
    expect(
      screen.queryByText('Buddy preferences saved.'),
    ).not.toBeInTheDocument();
  });

  it('uses real next cursor and keeps unavailable looks disabled', async () => {
    const input = props();
    input.loadPacks = vi.fn(async () => ({
      ...page,
      next_cursor: null,
      packs: [
        { ...page.packs[0], id: 'missing', name: 'Missing', available: false },
      ],
    }));
    render(<BuddyControls {...input} />);
    fireEvent.click(screen.getByRole('button', { name: 'More Buddy looks' }));
    expect(
      await screen.findByRole('button', {
        name: 'Missing — 2 clips · Unavailable',
      }),
    ).toBeDisabled();
    expect(input.loadPacks).toHaveBeenCalledWith('next');
    expect(
      screen.queryByRole('button', { name: 'More Buddy looks' }),
    ).not.toBeInTheDocument();
  });

  it('targets Stop only at the explicit current run and shows safe failure text', async () => {
    const input = props();
    input.stop = vi.fn(async () => {
      throw new Error('private server detail');
    });
    render(<BuddyControls {...input} />);
    fireEvent.click(screen.getByRole('button', { name: 'Stop current run' }));
    await screen.findByText('Buddy needs attention');
    expect(input.stop).toHaveBeenCalledWith('run-one');
    expect(screen.queryByText('private server detail')).not.toBeInTheDocument();
  });

  it('honors saved quiet bubbles and hidden companion without starting media', () => {
    const input = props();
    const view = render(
      <BuddyControls
        {...input}
        snapshot={{
          ...snapshot,
          preferences: { ...snapshot.preferences, bubble_verbosity: 'quiet' },
        }}
      />,
    );
    expect(screen.queryByText('Ready')).not.toBeInTheDocument();
    view.rerender(
      <BuddyControls
        {...input}
        snapshot={{
          ...snapshot,
          preferences: { ...snapshot.preferences, visible: false },
        }}
      />,
    );
    expect(screen.queryByRole('complementary')).not.toBeInTheDocument();
    expect(
      screen.getByRole('region', { name: 'Buddy preferences' }),
    ).toBeVisible();
  });
});
