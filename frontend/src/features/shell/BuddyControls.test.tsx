import {
  act,
  fireEvent,
  render as renderUi,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import type { ReactElement } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { OverlayProvider } from '../../ui/overlays';
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
/** Saves are confirmed by the floating notice (B258). */
function render(ui: ReactElement) {
  const view = renderUi(<OverlayProvider>{ui}</OverlayProvider>);
  return {
    ...view,
    rerender: (next: ReactElement) =>
      view.rerender(<OverlayProvider>{next}</OverlayProvider>),
  };
}
function notice(text: string | RegExp) {
  return screen.findByText(text, { selector: '.toast *' });
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

  it('keeps name and compact size in Advanced and shows the looks as a grid of tiles (B258)', () => {
    const input = props();
    render(<BuddyControls {...input} />);

    const advanced = screen.getByText('Advanced').closest('details');
    expect(advanced).not.toHaveAttribute('open');
    // The page status line names the look; each tile says whether it's ready.
    expect(screen.getByText('Shown')).toBeVisible();
    expect(screen.getByText('motion ready')).toBeVisible();
    const looks = screen.getByRole('group', { name: 'Buddy looks' });
    const glyph = within(looks).getByRole('button', {
      name: 'Glyph — 2 clips · Ready',
    });
    expect(glyph).toHaveAttribute('aria-pressed', 'true');
    expect(glyph).toHaveTextContent('GlyphReady');
    expect(screen.queryByText('Buddy Glyph')).not.toBeInTheDocument();
    expect(
      within(screen.getByRole('radiogroup', { name: 'Motion' })).getByRole(
        'radio',
        { name: 'Normal' },
      ),
    ).toHaveAttribute('aria-checked', 'true');

    fireEvent.click(screen.getByText('Advanced'));
    expect(advanced).toHaveAttribute('open');
    expect(screen.getByLabelText('Compact Buddy')).toBeEnabled();
    expect(screen.getByLabelText('Buddy name')).toHaveValue('Buddy');
    expect(screen.getByLabelText('Style notes (optional)')).toHaveValue(
      'Warm and luminous',
    );
  });

  it('ends the looks with a New look tile that opens the generation flow (B258)', () => {
    const input = props();
    const onNewLook = vi.fn();
    const view = render(<BuddyControls {...input} onNewLook={onNewLook} />);
    const looks = screen.getByRole('group', { name: 'Buddy looks' });
    const tiles = within(looks).getAllByRole('button');
    expect(tiles.at(-1)).toHaveTextContent('New look…');
    fireEvent.click(tiles.at(-1)!);
    expect(onNewLook).toHaveBeenCalledOnce();
    view.rerender(
      <BuddyControls
        {...input}
        onNewLook={onNewLook}
        newLookStatus="Making it · 2 of 6 clips"
      />,
    );
    expect(
      within(looks).getByRole('button', { name: /New look/ }),
    ).toHaveTextContent('Making it · 2 of 6 clips');
  });

  it('offers Compact Buddy only for the desktop window, where it is saved', () => {
    const input = props();
    render(
      <BuddyControls
        {...input}
        snapshot={{ ...snapshot, native_placement_retained: false }}
      />,
    );
    fireEvent.click(screen.getByText('Advanced'));
    // A docked Buddy's "collapsed" is always normalized off by the server.
    expect(screen.queryByLabelText('Compact Buddy')).not.toBeInTheDocument();
    expect(screen.getByLabelText('Buddy name')).toBeVisible();
  });

  it('refreshes looks only after an explicit action', async () => {
    const input = props();
    render(<BuddyControls {...input} />);
    expect(input.reload).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Refresh looks' }));
    await notice('Buddy looks refreshed.');
    expect(input.reload).toHaveBeenCalledOnce();
  });

  it('saves a change at once with the captured revision, and Undo puts it back (decision 19)', async () => {
    const input = props();
    input.save = vi.fn(
      async (changes: Partial<BuddySnapshot['preferences']>) => ({
        ...snapshot,
        revision: 'revision-two',
        preferences: { ...snapshot.preferences, ...changes },
      }),
    );
    render(<BuddyControls {...input} />);
    expect(
      screen.queryByRole('button', { name: 'Save Buddy preferences' }),
    ).toBeNull();
    const bubbles = screen.getByRole('radiogroup', { name: 'Bubbles' });
    fireEvent.click(within(bubbles).getByRole('radio', { name: 'Quiet' }));
    const saved = await notice('Bubbles saved');
    expect(input.save).toHaveBeenCalledWith(
      { bubble_verbosity: 'quiet' },
      'revision-one',
    );
    expect(
      within(bubbles).getByRole('radio', { name: 'Quiet' }),
    ).toHaveAttribute('aria-checked', 'true');
    fireEvent.click(
      within(saved.closest('li')!).getByRole('button', { name: 'Undo' }),
    );
    await notice('Bubbles changed back');
    expect(input.save).toHaveBeenLastCalledWith(
      { bubble_verbosity: 'normal' },
      'revision-two',
    );
  });

  it('saves a name when the field is left, not on every key', async () => {
    const input = props();
    input.save = vi.fn(
      async (changes: Partial<BuddySnapshot['preferences']>) => ({
        ...snapshot,
        revision: 'revision-two',
        preferences: { ...snapshot.preferences, ...changes },
      }),
    );
    render(<BuddyControls {...input} />);
    fireEvent.click(screen.getByText('Advanced'));
    const name = screen.getByLabelText('Buddy name');
    fireEvent.change(name, { target: { value: 'Nova' } });
    expect(input.save).not.toHaveBeenCalled();
    fireEvent.keyDown(name, { key: 'Enter' });
    fireEvent.blur(name);
    await notice('Name saved');
    expect(input.save).toHaveBeenCalledExactlyOnceWith(
      { display_name: 'Nova' },
      'revision-one',
    );
  });

  it('keeps typing across a saved revision change and offers a reload', () => {
    const input = props();
    const view = render(<BuddyControls {...input} />);
    fireEvent.click(screen.getByText('Advanced'));
    fireEvent.change(screen.getByLabelText('Buddy name'), {
      target: { value: 'Typed name' },
    });
    view.rerender(
      <BuddyControls
        {...input}
        snapshot={{ ...snapshot, revision: 'revision-two' }}
      />,
    );
    expect(screen.getByLabelText('Buddy name')).toHaveValue('Typed name');
    fireEvent.click(
      screen.getByRole('button', { name: 'Reload saved preferences' }),
    );
    expect(screen.getByLabelText('Buddy name')).toHaveValue('Buddy');
    expect(input.save).not.toHaveBeenCalled();
  });

  it('retains pending save ownership through scope changes and ignores its late result', async () => {
    const input = props();
    const pending = deferred<BuddySnapshot>();
    input.save = vi.fn(() => pending.promise);
    const view = render(<BuddyControls {...input} />);
    fireEvent.change(screen.getByRole('combobox', { name: 'Personality' }), {
      target: { value: 'calm_focus' },
    });
    await waitFor(() => expect(input.save).toHaveBeenCalledOnce());
    view.rerender(
      <BuddyControls
        {...input}
        scopeKey="owner-B"
        snapshot={{ ...snapshot, revision: 'B' }}
      />,
    );
    await act(async () =>
      pending.resolve({
        ...snapshot,
        preferences: { ...snapshot.preferences, personality: 'calm_focus' },
      }),
    );
    expect(screen.getByRole('combobox', { name: 'Personality' })).toHaveValue(
      'warm_mystical',
    );
    expect(screen.queryByText('Personality saved')).toBeNull();
    expect(input.save).toHaveBeenCalledOnce();
  });

  it('removes all content on revoked snapshot and does not apply a late success', async () => {
    const input = props();
    const pending = deferred<BuddySnapshot>();
    input.save = vi.fn(() => pending.promise);
    const view = render(<BuddyControls {...input} />);
    fireEvent.click(screen.getByLabelText('Show Buddy'));
    await waitFor(() => expect(input.save).toHaveBeenCalledOnce());
    view.rerender(<BuddyControls {...input} snapshot={null} />);
    await act(async () => pending.resolve(snapshot));
    expect(screen.queryByRole('complementary')).not.toBeInTheDocument();
    expect(screen.queryByText('Show Buddy saved')).toBeNull();
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
    expect(
      within(
        screen.getByRole('complementary', { name: 'Buddy companion' }),
      ).queryByText('Ready'),
    ).not.toBeInTheDocument();
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
