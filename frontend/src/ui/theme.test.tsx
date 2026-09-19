import { act, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { THEME_KEY } from './theme-model';
import { ThemeProvider, useTheme } from './theme';

function Preference() {
  const { preference } = useTheme();
  return <p>{preference.appearance}</p>;
}

beforeEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
  vi.stubGlobal(
    'matchMedia',
    vi.fn(() => ({
      matches: false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  );
});

it('keeps one storage listener through rapid consecutive appearance changes', async () => {
  const add = vi.spyOn(window, 'addEventListener');
  render(
    <ThemeProvider>
      <Preference />
    </ThemeProvider>,
  );

  const setAppearance = async (appearance: 'light' | 'dark') => {
    const previous = localStorage.getItem(THEME_KEY);
    const next = JSON.stringify({
      version: 1,
      appearance,
      accent: 'blue',
      density: 'compact',
      reduce_transparency: false,
    });
    localStorage.setItem(THEME_KEY, next);
    act(() => {
      window.dispatchEvent(
        new StorageEvent('storage', {
          key: THEME_KEY,
          oldValue: previous,
          newValue: next,
          storageArea: localStorage,
          url: location.href,
        }),
      );
    });
    await waitFor(() => expect(screen.getByText(appearance)).toBeVisible());
  };

  await setAppearance('light');
  await setAppearance('dark');
  await setAppearance('light');

  expect(add.mock.calls.filter(([event]) => event === 'storage')).toHaveLength(
    1,
  );
  expect(document.documentElement.dataset.theme).toBe('light');
});

it('does not let a delayed system-scheme event overwrite a stored appearance', async () => {
  const schemeListeners = new Set<EventListener>();
  vi.stubGlobal(
    'matchMedia',
    vi.fn((query: string) => ({
      matches: false,
      addEventListener: vi.fn((event: string, listener: EventListener) => {
        if (event === 'change' && query.includes('color-scheme'))
          schemeListeners.add(listener);
      }),
      removeEventListener: vi.fn(),
    })),
  );
  localStorage.setItem(
    THEME_KEY,
    JSON.stringify({
      version: 1,
      appearance: 'light',
      accent: 'blue',
      density: 'compact',
      reduce_transparency: false,
    }),
  );
  render(
    <ThemeProvider>
      <Preference />
    </ThemeProvider>,
  );
  await waitFor(() => expect(screen.getByText('light')).toBeVisible());

  const previous = localStorage.getItem(THEME_KEY);
  const next = JSON.stringify({
    version: 1,
    appearance: 'dark',
    accent: 'blue',
    density: 'compact',
    reduce_transparency: false,
  });
  act(() => {
    localStorage.setItem(THEME_KEY, next);
    window.dispatchEvent(
      new StorageEvent('storage', {
        key: THEME_KEY,
        oldValue: previous,
        newValue: next,
        storageArea: localStorage,
        url: location.href,
      }),
    );
    for (const listener of schemeListeners) listener(new Event('change'));
  });

  await waitFor(() => expect(screen.getByText('dark')).toBeVisible());
  expect(document.documentElement.dataset.theme).toBe('dark');
});
