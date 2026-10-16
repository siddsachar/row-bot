import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, expect, it, vi } from 'vitest';
import { OverlayProvider } from '../../ui/overlays';
import { ThemeProvider } from '../../ui/theme';
import AppearanceSettings from './Appearance';
import Preferences from './Preferences';
import type { SettingsSnapshot } from '../../api/types';
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
  const appearance = screen.getByRole('combobox', { name: /^Appearance/ });
  fireEvent.change(appearance, { target: { value: 'light' } });
  expect(document.documentElement.dataset.theme).toBe('light');
  fireEvent.change(screen.getByRole('combobox', { name: /^Colour theme/ }), {
    target: { value: 'violet' },
  });
  expect(document.documentElement.dataset.accent).toBe('violet');
  expect(
    screen.getByRole('figure', { name: /Light appearance, Violet accent/ }),
  ).toBeVisible();
  fireEvent.change(appearance, { target: { value: 'system' } });
  // System previews both looks side by side.
  expect(document.querySelectorAll('.appearance-mini')).toHaveLength(2);
  fireEvent.click(screen.getByRole('switch', { name: 'Reduce transparency' }));
  expect(document.documentElement.dataset.opaque).toBe('true');
  fireEvent.click(screen.getByRole('button', { name: 'Reset layout' }));
  expect(reset).toHaveBeenCalledOnce();
});

it('chooses a look from its preview card, and says plainly what System does', async () => {
  render(
    <MemoryRouter>
      <ThemeProvider>
        <OverlayProvider>
          <AppearanceSettings onReset={vi.fn()} />
        </OverlayProvider>
      </ThemeProvider>
    </MemoryRouter>,
  );
  const appearance = screen.getByRole('combobox', { name: /^Appearance/ });
  fireEvent.change(appearance, { target: { value: 'system' } });
  // System isn't "light by day": it follows whatever the device uses.
  expect(screen.queryByText(/by day/)).toBeNull();
  // The look the device uses now is marked, but neither is chosen.
  expect(
    await screen.findByRole('button', { name: 'Light, in use now' }),
  ).toHaveAttribute('aria-pressed', 'false');
  fireEvent.click(screen.getByRole('button', { name: 'Dark' }));
  expect(document.documentElement.dataset.theme).toBe('dark');
  expect(appearance).toHaveValue('dark');
  expect(screen.getByRole('button', { name: 'Dark' })).toHaveAttribute(
    'aria-pressed',
    'true',
  );
  fireEvent.click(screen.getByRole('button', { name: 'Light' }));
  expect(appearance).toHaveValue('light');
});

it('moves Learn new skills to Settings › Learning in a Learning preview build (D29)', () => {
  const snapshot = {
    availability: 'available',
    identity: {
      name: 'Row-Bot',
      personality: '',
      personality_max_length: 200,
      self_improvement_enabled: true,
    },
    window_mode: 'ask',
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
  const mutation: SettingsMutationIO = {
    revision: 'b'.repeat(64),
    page: 'preferences',
    review: vi.fn(),
    execute: vi.fn(),
    receipt: vi.fn(),
    drafts: new SettingsDraftOwner(),
    onSnapshot: vi.fn(),
  };
  const view = (skillsMoved: boolean) => (
    <MemoryRouter>
      <ThemeProvider>
        <OverlayProvider>
          <Preferences
            snapshot={snapshot}
            mutation={mutation}
            skillsMoved={skillsMoved}
          />
        </OverlayProvider>
      </ThemeProvider>
    </MemoryRouter>
  );
  const { rerender } = render(view(false));
  expect(
    screen.getByRole('switch', { name: 'Learn new skills' }),
  ).toBeChecked();
  rerender(view(true));
  expect(screen.queryByRole('switch', { name: 'Learn new skills' })).toBeNull();
  expect(screen.getByRole('switch', { name: 'Dream Cycle' })).toBeVisible();
});
