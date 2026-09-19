import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { OverlayProvider } from '../../ui/overlays';
import { ThemeProvider } from '../../ui/theme';
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

it('keeps React-only local controls behind one closed supplemental disclosure', () => {
  render(
    <ThemeProvider>
      <OverlayProvider>
        <Preferences snapshotState={<p>Saved preferences</p>} />
      </OverlayProvider>
    </ThemeProvider>,
  );

  const summary = screen.getByText('Local client controls');
  expect(summary.closest('details')).not.toHaveAttribute('open');
  expect(screen.getByLabelText('Appearance')).not.toBeVisible();
  expect(screen.getByText('Local client workspace layout')).not.toBeVisible();

  fireEvent.click(summary);
  expect(screen.getByLabelText('Appearance')).toBeVisible();
  expect(screen.getByText('Local client workspace layout')).toBeVisible();
});
