import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, expect, it, vi } from 'vitest';
import { setPlace } from '../../api/place';
import { OverlayProvider } from '../../ui/overlays';
import { ThemeProvider } from '../../ui/theme';
import AppearanceSettings from './Appearance';
import Preferences from './Preferences';
import type { SettingsSnapshot } from '../../api/types';
import type { MemoryReviewIO } from '../knowledge/memory-review-io';
import {
  SettingsDraftOwner,
  type SettingsMutationIO,
} from './SettingsSnapshotPanels';

beforeEach(() => {
  localStorage.clear();
  vi.stubGlobal(
    'matchMedia',
    vi.fn(() => ({
      matches: false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  );
});

it('keeps device-local appearance off the Preferences page', () => {
  render(
    <ThemeProvider>
      <OverlayProvider>
        <Preferences snapshotState={<p>Saved preferences</p>} />
      </OverlayProvider>
    </ThemeProvider>,
  );
  expect(screen.getByText('Saved preferences')).toBeVisible();
  expect(screen.queryByLabelText('Appearance')).toBeNull();
  expect(screen.queryByText('Local client controls')).toBeNull();
});

it('applies appearance choices immediately and previews them', () => {
  const reset = vi.fn();
  render(
    <MemoryRouter>
      <ThemeProvider>
        <OverlayProvider>
          <AppearanceSettings onReset={reset} />
        </OverlayProvider>
      </ThemeProvider>
    </MemoryRouter>,
  );
  // The look is chosen from its card alone: no second control for it (U37).
  expect(screen.queryByRole('combobox', { name: /^Appearance/ })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Light' }));
  expect(document.documentElement.dataset.theme).toBe('light');
  fireEvent.change(screen.getByRole('combobox', { name: /^Colour theme/ }), {
    target: { value: 'violet' },
  });
  expect(document.documentElement.dataset.accent).toBe('violet');
  expect(
    screen.getByRole('figure', { name: /Light appearance, Violet accent/ }),
  ).toBeVisible();
  // System, Light and Dark each preview their look.
  expect(document.querySelectorAll('.appearance-mini')).toHaveLength(3);
  fireEvent.click(screen.getByRole('switch', { name: 'Reduce transparency' }));
  expect(document.documentElement.dataset.opaque).toBe('true');
  fireEvent.click(screen.getByRole('button', { name: 'Reset layout' }));
  expect(reset).toHaveBeenCalledOnce();
});

it('chooses a look from its card, and says plainly what System does', () => {
  render(
    <MemoryRouter>
      <ThemeProvider>
        <OverlayProvider>
          <AppearanceSettings onReset={vi.fn()} />
        </OverlayProvider>
      </ThemeProvider>
    </MemoryRouter>,
  );
  const system = screen.getByRole('button', {
    name: /^System, follows your device/,
  });
  fireEvent.click(system);
  expect(system).toHaveAttribute('aria-pressed', 'true');
  // System isn't "light by day": it follows whatever the device uses.
  expect(screen.queryByText(/by day/)).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Dark' }));
  expect(document.documentElement.dataset.theme).toBe('dark');
  expect(screen.getByRole('button', { name: 'Dark' })).toHaveAttribute(
    'aria-pressed',
    'true',
  );
  expect(system).toHaveAttribute('aria-pressed', 'false');
});

const SNAPSHOT = {
  availability: 'available',
  identity: {
    name: 'Row-Bot',
    personality: '',
    personality_max_length: 200,
    self_improvement_enabled: true,
  },
  window_mode: 'native',
  desktop_notifications: true,
  dream_cycle: {
    enabled: true,
    window_start: 1,
    window_end: 5,
    last_run: null,
    last_summary: '',
  },
  updates: {
    current_version: '5.4.0',
    channel: 'stable',
    last_check: null,
    last_success: null,
    runtime_state: 'cached',
  },
  migration: { available: false, sources: [] },
} satisfies SettingsSnapshot['preferences'];

function savingMutation(): SettingsMutationIO {
  return {
    revision: 'b'.repeat(64),
    page: 'preferences',
    review: vi.fn(async (request) => ({
      schema_version: 1 as const,
      operation: 'settings.update' as const,
      settings_revision: request.settings_revision,
      page: request.page,
      field: request.field,
      value_summary: String(request.value),
      secret: false,
      action_digest: 'a'.repeat(64),
      review_id: 'review-a',
    })),
    execute: vi.fn(async (request, _review, commandId) => ({
      command_id: commandId,
      status: 'completed' as const,
      settings_revision: 'c'.repeat(64),
      snapshot: null,
    })),
    receipt: vi.fn(),
    drafts: new SettingsDraftOwner(),
    onSnapshot: vi.fn(),
  };
}

it('keeps every notification setting in one Notifications group', async () => {
  const mutation = savingMutation();
  render(
    <MemoryRouter>
      <ThemeProvider>
        <OverlayProvider>
          <Preferences snapshot={SNAPSHOT} mutation={mutation} />
        </OverlayProvider>
      </ThemeProvider>
    </MemoryRouter>,
  );
  const group = screen.getByRole('region', { name: 'Notifications' });
  const desktop = within(group).getByRole('switch', {
    name: 'Desktop notifications',
  });
  expect(desktop).toBeChecked();
  expect(
    within(group).getByText(/A notice and a sound on this computer/),
  ).toBeVisible();
  // The phone and Proactive settings stay with their owners: rows lead there.
  expect(
    within(group).getByRole('link', { name: 'Open Devices & remote access' }),
  ).toHaveAttribute('href', '/settings/access#notifications');
  expect(
    within(group).getByRole('link', { name: 'Open Proactive settings' }),
  ).toHaveAttribute('href', '/settings/proactive');
  expect(
    within(group).queryByRole('switch', {
      name: 'Notifications on this device',
    }),
  ).toBeNull();

  fireEvent.click(desktop);
  await waitFor(() => expect(mutation.execute).toHaveBeenCalledTimes(1));
  expect(mutation.review).toHaveBeenCalledWith(
    expect.objectContaining({
      page: 'preferences',
      field: 'desktop_notifications',
      value: false,
    }),
    expect.any(AbortSignal),
  );
});

it('leaves out the desktop switch on a hosted Row-Bot, which has no desktop', () => {
  setPlace({ kind: 'hosted', host_name: 'PikaPods' });
  try {
    render(
      <MemoryRouter>
        <ThemeProvider>
          <OverlayProvider>
            <Preferences snapshot={SNAPSHOT} mutation={savingMutation()} />
          </OverlayProvider>
        </ThemeProvider>
      </MemoryRouter>,
    );
    const group = screen.getByRole('region', { name: 'Notifications' });
    expect(
      within(group).queryByRole('switch', { name: 'Desktop notifications' }),
    ).toBeNull();
    expect(within(group).getByText('Phone notifications')).toBeVisible();
  } finally {
    setPlace(null);
  }
});

/** The phone layout (C3): below 768px. */
function onPhoneWidth() {
  vi.stubGlobal(
    'matchMedia',
    vi.fn((query: string) => ({
      matches: query === '(max-width: 767px)',
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  );
}

it('on a phone shows the theme first, as one System, Light and Dark control (C3)', () => {
  onPhoneWidth();
  render(
    <MemoryRouter>
      <ThemeProvider>
        <OverlayProvider>
          <AppearanceSettings onReset={vi.fn()} />
        </OverlayProvider>
      </ThemeProvider>
    </MemoryRouter>,
  );
  const theme = screen.getByRole('radiogroup', { name: 'Theme' });
  // First on the page, and the only control for it (U37): no preview cards.
  expect(
    theme.compareDocumentPosition(
      screen.getByRole('combobox', { name: /^Colour theme/ }),
    ) & Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
  expect(document.querySelectorAll('.appearance-mini')).toHaveLength(0);
  expect(screen.getByText(/^Follows your device/)).toBeVisible();
  fireEvent.click(within(theme).getByRole('radio', { name: 'Dark' }));
  expect(document.documentElement.dataset.theme).toBe('dark');
  expect(within(theme).getByRole('radio', { name: 'Dark' })).toHaveAttribute(
    'aria-checked',
    'true',
  );
});

it('offers Review memory beside the Dream Cycle on a computer, not on a phone, where clean-up is on the computer (B4)', () => {
  const view = () =>
    render(
      <MemoryRouter>
        <ThemeProvider>
          <OverlayProvider>
            <Preferences
              snapshot={SNAPSHOT}
              mutation={savingMutation()}
              reviewIO={{} as MemoryReviewIO}
            />
          </OverlayProvider>
        </ThemeProvider>
      </MemoryRouter>,
    );
  const desktop = view();
  expect(screen.getByRole('button', { name: 'Review memory' })).toBeVisible();
  desktop.unmount();
  onPhoneWidth();
  view();
  expect(screen.getByRole('switch', { name: /Dream Cycle/ })).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Review memory' })).toBeNull();
});
