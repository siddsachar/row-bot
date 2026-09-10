import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { bootstrapTheme, THEME_KEY, TOKENS } from './theme-model';

beforeEach(() => {
  localStorage.clear();
  document.documentElement.removeAttribute('style');
  for (const key of Object.keys(document.documentElement.dataset))
    delete document.documentElement.dataset[key];
});
afterEach(() => vi.restoreAllMocks());

it.each([false, true])(
  'starts a fresh device dark/blue/compact regardless of system dark=%s',
  (systemDark) => {
    vi.spyOn(window, 'matchMedia').mockImplementation(
      (query) =>
        ({ matches: query.includes('dark') && systemDark }) as MediaQueryList,
    );
    const write = vi.spyOn(Storage.prototype, 'setItem');
    expect(bootstrapTheme(TOKENS)).toEqual({
      version: 1,
      appearance: 'dark',
      accent: 'blue',
      density: 'compact',
      reduce_transparency: false,
    });
    expect(document.documentElement.dataset).toMatchObject({
      appearance: 'dark',
      theme: 'dark',
      accent: 'blue',
      density: 'compact',
    });
    expect(document.documentElement.style.getPropertyValue('--canvas')).toBe(
      TOKENS.dark.canvas,
    );
    expect(write).not.toHaveBeenCalled();
  },
);

it.each(['system', 'light', 'dark'] as const)(
  'preserves a saved explicit %s/comfortable preference and accent unchanged',
  (appearance) => {
    const saved = {
      version: 1,
      appearance,
      accent: 'violet',
      density: 'comfortable',
      reduce_transparency: true,
    };
    const raw = JSON.stringify(saved);
    localStorage.setItem(THEME_KEY, raw);
    const write = vi.spyOn(Storage.prototype, 'setItem');
    expect(bootstrapTheme(TOKENS)).toEqual(saved);
    expect(localStorage.getItem(THEME_KEY)).toBe(raw);
    expect(write).not.toHaveBeenCalled();
    expect(document.documentElement.dataset).toMatchObject({
      appearance,
      density: 'comfortable',
      accent: 'violet',
      opaque: 'true',
    });
  },
);

it('resolves a saved System preference using the current media value without replacing it', () => {
  localStorage.setItem(
    THEME_KEY,
    JSON.stringify({
      version: 1,
      appearance: 'system',
      density: 'comfortable',
      accent: 'teal',
    }),
  );
  let systemDark = false;
  vi.spyOn(window, 'matchMedia').mockImplementation(
    (query) =>
      ({ matches: query.includes('dark') && systemDark }) as MediaQueryList,
  );
  expect(bootstrapTheme(TOKENS).appearance).toBe('system');
  expect(document.documentElement.dataset.theme).toBe('light');
  systemDark = true;
  expect(bootstrapTheme(TOKENS).appearance).toBe('system');
  expect(document.documentElement.dataset.theme).toBe('dark');
  expect(document.documentElement.dataset.density).toBe('comfortable');
});

it('preserves explicit old-version preferences while filling only absent fields from the new default', () => {
  localStorage.setItem(
    THEME_KEY,
    JSON.stringify({
      version: 0,
      appearance: 'system',
      density: 'comfortable',
    }),
  );
  expect(bootstrapTheme(TOKENS)).toEqual({
    version: 1,
    appearance: 'system',
    accent: 'blue',
    density: 'comfortable',
    reduce_transparency: false,
  });
});
