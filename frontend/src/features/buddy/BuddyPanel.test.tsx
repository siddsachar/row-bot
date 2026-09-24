import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import BuddyPanel, {
  createBuddyPanelSession,
  type BuddyPanelTransport,
  type BuddyReceipt,
} from './BuddyPanel';
import type { BuddySnapshot, BuddyPack } from '../shell/BuddyControls';
import type { HatchRequest, HatchResult } from '../shell/BuddyHatch';

it('retains unsent preference and Hatch drafts across panel remount and purges them on auth loss', async () => {
  const session = createBuddyPanelSession(transport(), () => {});
  const props = {
    session,
    scopeKey: 'same-conversation',
    settingsOpen: true,
    onSettings: vi.fn(),
  };
  const first = render(<BuddyPanel {...props} />);
  await screen.findByRole('button', { name: 'Save Buddy preferences' });
  fireEvent.change(screen.getByLabelText('Bubble style'), {
    target: { value: 'chatty' },
  });
  fireEvent.change(screen.getByLabelText('Describe your Buddy'), {
    target: { value: 'Retained private look' },
  });
  expect(session.hasRetained()).toBe(true);
  first.unmount();
  render(<BuddyPanel {...props} />);
  expect(screen.getByLabelText('Bubble style')).toHaveValue('chatty');
  expect(screen.getByLabelText('Describe your Buddy')).toHaveValue(
    'Retained private look',
  );
  act(() => session.purge());
  expect(
    screen.queryByLabelText('Describe your Buddy'),
  ).not.toBeInTheDocument();
  expect(session.hasRetained()).toBe(false);
  expect(session.hatchEditor.get('prompt', '')).toBe('');
});

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
    animation_intensity: 'quiet',
    pack_id: 'glyph',
  },
  status: {
    mood: 'curious',
    animation: 'idle',
    energy: 50,
    focus: 0,
    alert: 0,
    event_id: 1,
    label: 'Ready',
  },
};
const pack: BuddyPack = {
  id: 'glyph',
  name: 'Glyph',
  revision: 'pack-one',
  runtime: 'rive',
  generated: false,
  available: true,
  assets: [],
  animation_map: {},
};
const request: HatchRequest = {
  action: 'full',
  prompt: 'Synthetic private Buddy',
  config_revision: 'revision-one',
};
function result(id: string, status = 'running'): HatchResult {
  return {
    schema_version: 1,
    command_id: id,
    job_id: 'buddy-hatch-' + id,
    status,
    stage: 'motion_provider_started',
    pack_id: null,
    selected: false,
    completed_clips: 2,
    total_clips: 6,
    code: '',
    retained_copy: true,
    has_still: true,
  };
}
function transport(): BuddyPanelTransport {
  return {
    snapshot: vi.fn(async () => snapshot),
    packs: vi.fn(async () => ({
      revision: 'catalog',
      total: 1,
      packs: [pack],
      next_cursor: null,
    })),
    pack: vi.fn(async () => pack),
    review: vi.fn(async (value) => ({
      review_id: 'review-one',
      action: value.action,
      config_revision: value.config_revision,
      image_model: 'fixture/image',
      video_model: 'fixture/video',
      provider_calls: 7,
    })),
    execute: vi.fn(async (command) => ({
      command_id: command.command_id,
      status: 'accepted',
      hatch: result(command.command_id),
    })),
    receipt: vi.fn(async (id) => ({
      command_id: id,
      status: 'completed',
      hatch: result(id, 'completed'),
    })),
  };
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

describe('controller-lifetime Buddy commands', () => {
  it('shares status reads across mounted views and aborts the final observer without changing drafts', async () => {
    vi.useFakeTimers();
    try {
      const api = transport();
      const session = createBuddyPanelSession(api, () => {});
      await session.load();
      session.preferencesEditor.get('draft', snapshot.preferences);
      session.preferencesEditor.set('draft', {
        ...snapshot.preferences,
        display_name: 'Unsent name',
      });
      session.preferencesEditor.get('dirty', false);
      session.preferencesEditor.set('dirty', true);
      const pending = deferred<BuddySnapshot>();
      let signal: AbortSignal | undefined;
      api.snapshot = vi.fn((value) => {
        signal = value;
        return pending.promise;
      });
      const first = session.observe(),
        second = session.observe();
      await vi.advanceTimersByTimeAsync(2000);
      expect(api.snapshot).toHaveBeenCalledTimes(1);
      first();
      expect(signal?.aborted).toBe(false);
      await vi.advanceTimersByTimeAsync(4000);
      expect(api.snapshot).toHaveBeenCalledTimes(1);
      second();
      expect(signal?.aborted).toBe(true);
      pending.resolve({
        ...snapshot,
        status: { ...snapshot.status, label: 'Late private status' },
      });
      await vi.advanceTimersByTimeAsync(4000);
      expect(session.getSnapshot().snapshot?.status.label).toBe('Ready');
      expect(
        session.preferencesEditor.get('draft', snapshot.preferences)
          .display_name,
      ).toBe('Unsent name');
      expect(api.execute).not.toHaveBeenCalled();
      session.purge();
    } finally {
      vi.useRealTimers();
    }
  });

  it('refreshes current Buddy status without overwriting a retained preference draft', async () => {
    vi.useFakeTimers();
    try {
      const api = transport();
      const session = createBuddyPanelSession(api, () => {});
      await session.load();
      session.preferencesEditor.get('draft', snapshot.preferences);
      session.preferencesEditor.set('draft', {
        ...snapshot.preferences,
        display_name: 'Retained',
      });
      api.snapshot = vi.fn(async () => ({
        ...snapshot,
        status: { ...snapshot.status, label: 'Working', event_id: 2 },
      }));
      const stop = session.observe();
      await vi.advanceTimersByTimeAsync(2000);
      expect(session.getSnapshot().snapshot?.status.label).toBe('Working');
      expect(
        session.preferencesEditor.get('draft', snapshot.preferences)
          .display_name,
      ).toBe('Retained');
      expect(api.pack).toHaveBeenCalledTimes(1);
      stop();
      session.purge();
    } finally {
      vi.useRealTimers();
    }
  });
  it('offers an explicit saved-view retry after a passive load failure', async () => {
    const api = transport();
    api.snapshot = vi
      .fn()
      .mockRejectedValueOnce(new Error('unavailable'))
      .mockResolvedValue(snapshot);
    const session = createBuddyPanelSession(api, () => {});
    render(
      <BuddyPanel
        scopeKey="auth-one"
        session={session}
        settingsOpen
        onSettings={() => {}}
      />,
    );
    await waitFor(() =>
      expect(
        screen.getByText(
          'Buddy settings could not be read. Retry the saved view.',
        ),
      ).toBeVisible(),
    );
    fireEvent.click(
      screen.getByRole('button', { name: 'Retry Buddy settings' }),
    );
    await waitFor(() =>
      expect(session.getSnapshot().snapshot?.revision).toBe(snapshot.revision),
    );
    expect(api.execute).not.toHaveBeenCalled();
  });
  it('loads passively, retaining the selected pack even outside the catalog page', async () => {
    const api = transport();
    api.packs = vi.fn(async () => ({
      revision: 'catalog',
      total: 2,
      packs: [],
      next_cursor: 'next',
    }));
    const session = createBuddyPanelSession(api, () => {});
    await session.load();
    expect(session.getSnapshot().selectedPack?.id).toBe('glyph');
    expect(api.review).not.toHaveBeenCalled();
    expect(api.execute).not.toHaveBeenCalled();
    await session.loadPacks('next');
    expect(api.packs).toHaveBeenLastCalledWith('next');
  });
  it('retains the original review and command after lost acknowledgement and only reads its receipt', async () => {
    const api = transport();
    api.execute = vi.fn(async () => {
      throw new Error('lost acknowledgement');
    });
    const session = createBuddyPanelSession(
      api,
      () => {},
      () => 'original-command',
    );
    await session.review(request);
    await expect(session.confirm('review-one')).rejects.toThrow();
    expect(session.getSnapshot().pending).toBe(true);
    await expect(
      session.review({ ...request, prompt: 'Changed' }),
    ).rejects.toThrow('buddy_outcome_uncertain');
    const recovered = await session.confirm('review-one');
    expect('command_id' in recovered && recovered.command_id).toBe(
      'original-command',
    );
    expect(api.execute).toHaveBeenCalledTimes(1);
    expect(api.receipt).toHaveBeenCalledWith('original-command');
    expect(vi.mocked(api.execute).mock.calls[0][0].payload).toEqual({
      request,
      review_id: 'review-one',
    });
  });
  it('preserves an unresolved command across unmount and supplies recovery on reopening', async () => {
    const api = transport();
    api.execute = vi.fn(async () => {
      throw new Error('lost');
    });
    const session = createBuddyPanelSession(
      api,
      () => {},
      () => 'original-command',
    );
    await session.load();
    await session.review(request);
    await expect(session.confirm('review-one')).rejects.toThrow();
    const props = {
      scopeKey: 'auth-one',
      session,
      settingsOpen: true,
      onSettings: vi.fn(),
    };
    const first = render(<BuddyPanel {...props} />);
    expect(
      screen.getByRole('button', { name: 'Refresh original Buddy command' }),
    ).toBeVisible();
    first.unmount();
    render(<BuddyPanel {...props} />);
    fireEvent.click(
      screen.getByRole('button', { name: 'Refresh original Buddy command' }),
    );
    await waitFor(() =>
      expect(screen.getByText('Buddy is ready.')).toBeVisible(),
    );
    expect(api.execute).toHaveBeenCalledTimes(1);
  });
  it('keeps single admission through panel changes and purges late responses on revocation', async () => {
    const api = transport();
    const pending = deferred<BuddyReceipt>();
    api.execute = vi.fn(() => pending.promise);
    const session = createBuddyPanelSession(
      api,
      () => {},
      () => 'original-command',
    );
    await session.review(request);
    const sending = session.confirm('review-one');
    await Promise.resolve();
    await expect(session.confirm('review-one')).rejects.toThrow('buddy_busy');
    session.purge();
    pending.resolve({
      command_id: 'original-command',
      status: 'completed',
      hatch: result('original-command'),
    });
    await expect(sending).rejects.toThrow('authentication_required');
    expect(session.getSnapshot()).toMatchObject({
      revoked: true,
      result: null,
      snapshot: null,
      pending: false,
    });
    expect(api.execute).toHaveBeenCalledTimes(1);
  });
  it('rejects mismatched receipt identity and retains recovery ownership', async () => {
    const api = transport();
    api.execute = vi.fn(async () => ({
      command_id: 'foreign',
      status: 'completed',
    }));
    const session = createBuddyPanelSession(
      api,
      () => {},
      () => 'original',
    );
    await session.review(request);
    await expect(session.confirm('review-one')).rejects.toThrow(
      'buddy_receipt_changed',
    );
    expect(session.getSnapshot().pending).toBe(true);
    expect(session.getSnapshot().result).toBeNull();
  });
  it('refreshes lost settings acknowledgement without saving again', async () => {
    const api = transport();
    api.execute = vi.fn(async () => {
      throw new Error('lost');
    });
    api.receipt = vi.fn(async (id) => ({
      command_id: id,
      status: 'completed',
      buddy_revision: 'saved',
    }));
    const session = createBuddyPanelSession(
      api,
      () => {},
      () => 'settings-command',
    );
    await expect(
      session.save({ visible: false }, snapshot.revision),
    ).rejects.toThrow();
    await session.recover();
    expect(api.execute).toHaveBeenCalledTimes(1);
    expect(api.receipt).toHaveBeenCalledWith('settings-command');
    expect(session.getSnapshot().pending).toBe(false);
  });
  it('preserves generation and exact Stop command identities after a lost Stop acknowledgement', async () => {
    const api = transport();
    let sequence = 0;
    const original = api.execute;
    api.execute = vi.fn(async (command) => {
      if (command.type === 'buddy.cancel') throw new Error('lost stop');
      return original(command);
    });
    api.receipt = vi.fn(async (id) => ({
      command_id: id,
      status: 'completed',
      cancel_requested: true,
    }));
    const session = createBuddyPanelSession(
      api,
      () => {},
      () => 'command-' + ++sequence,
    );
    await session.review(request);
    await session.confirm('review-one');
    await expect(session.cancel('buddy-hatch-command-1')).rejects.toThrow();
    await session.cancel('buddy-hatch-command-1');
    expect(api.execute).toHaveBeenCalledTimes(2);
    expect(api.receipt).toHaveBeenCalledWith('command-2');
    expect(session.getSnapshot().result?.command_id).toBe('command-1');
    expect(session.getSnapshot().pending).toBe(true);
  });
  it('uses the canonical avatar without duplicating the composer Stop action', async () => {
    const api = transport();
    const session = createBuddyPanelSession(api, () => {});
    await session.load();
    render(
      <BuddyPanel
        scopeKey="auth-one"
        session={session}
        settingsOpen={false}
        onSettings={() => {}}
        renderAvatar={() => <span>Canonical avatar</span>}
      />,
    );
    expect(screen.getByText('Canonical avatar')).toBeVisible();
    expect(
      screen.queryByRole('button', { name: /stop current run/i }),
    ).not.toBeInTheDocument();
    expect(api.execute).not.toHaveBeenCalled();
  });
});
