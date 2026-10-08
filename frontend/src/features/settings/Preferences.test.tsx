import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, expect, it, vi } from 'vitest';
import { OverlayProvider } from '../../ui/overlays';
import { ThemeProvider } from '../../ui/theme';
import AppearanceSettings from './Appearance';
import Preferences from './Preferences';

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
