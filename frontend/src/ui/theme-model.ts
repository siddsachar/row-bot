export type Appearance = 'system' | 'light' | 'dark';
export type Accent = 'blue' | 'teal' | 'violet' | 'amber';
export type ThemePreference = {
  version: 1;
  appearance: Appearance;
  accent: Accent;
  density: 'comfortable' | 'compact';
  reduce_transparency: boolean;
};
export const THEME_KEY = 'row-bot.appearance.v1';
export const DEFAULT_THEME: ThemePreference = {
  version: 1,
  appearance: 'dark',
  accent: 'blue',
  density: 'compact',
  reduce_transparency: false,
};
export const TOKENS = {
  light: {
    canvas: '#F3F6F9',
    surface: '#FFFFFF',
    'surface-raised': '#F9FBFD',
    'surface-sunken': '#EAF0F5',
    'surface-soft': '#F0F5FA',
    'surface-hover': '#E5EEF7',
    'surface-pressed': '#D8E6F2',
    'surface-disabled': '#E7EBEF',
    'surface-highlight': '#FFFFFFCC',
    'shadow-color': '#17212B2E',
    'text-primary': '#17212B',
    'text-secondary': '#425466',
    'text-muted': '#526475',
    'text-disabled': '#526475',
    'text-inverse': '#FFFFFF',
    'border-subtle': '#CDD8E3',
    'border-control': '#647586',
    'border-strong': '#415970',
    'code-background': '#EEF2F6',
    'code-text': '#17212B',
    'code-comment': '#526475',
    'syntax-keyword': '#65459B',
    'syntax-string': '#175A3C',
    'syntax-number': '#80550D',
    'status-info-text': '#254E77',
    'status-info-background': '#E8F0F9',
    'status-info-border': '#254E77',
    'status-success-text': '#175A3C',
    'status-success-background': '#E7F4ED',
    'status-success-border': '#175A3C',
    'status-warning-text': '#704709',
    'status-warning-background': '#FFF1CF',
    'status-warning-border': '#704709',
    'status-danger-text': '#8B2735',
    'status-danger-background': '#FBEAEC',
    'status-danger-border': '#8B2735',
    'diff-add-text': '#175A3C',
    'diff-add-background': '#E7F4ED',
    'diff-add-marker': '#175A3C',
    'diff-remove-text': '#8B2735',
    'diff-remove-background': '#FBEAEC',
    'diff-remove-marker': '#8B2735',
    'diff-change-text': '#704709',
    'diff-change-background': '#FFF1CF',
    'diff-change-marker': '#704709',
    'chart-series-1': '#345E87',
    'chart-series-2': '#086A68',
    'chart-series-3': '#65459B',
    'chart-series-4': '#80550D',
    'chart-series-5': '#8B2735',
    'chart-series-6': '#465564',
    'chart-grid': '#647586',
    'chart-axis': '#465564',
    'artifact-canvas-chrome': '#EEF2F6',
    'artifact-page-border': '#647586',
    'overlay-scrim': '#17212B80',
  },
  dark: {
    canvas: '#0F141A',
    surface: '#171D24',
    'surface-raised': '#202832',
    'surface-sunken': '#0C1116',
    'surface-soft': '#182533',
    'surface-hover': '#283440',
    'surface-pressed': '#334252',
    'surface-disabled': '#20262D',
    'surface-highlight': '#FFFFFF12',
    'shadow-color': '#0000008F',
    'text-primary': '#F4F7FA',
    'text-secondary': '#CFD8E1',
    'text-muted': '#A5B3C2',
    'text-disabled': '#B4C0CC',
    'text-inverse': '#0F141A',
    'border-subtle': '#34414F',
    'border-control': '#8CA0B5',
    'border-strong': '#8CA0B5',
    'code-background': '#181818',
    'code-text': '#F1F5F9',
    'code-comment': '#A6B6C7',
    'syntax-keyword': '#BEA7EA',
    'syntax-string': '#A7E3BC',
    'syntax-number': '#E8BE68',
    'status-info-text': '#BAD6F3',
    'status-info-background': '#17324A',
    'status-info-border': '#BAD6F3',
    'status-success-text': '#A7E3BC',
    'status-success-background': '#183E2C',
    'status-success-border': '#A7E3BC',
    'status-warning-text': '#F4D48D',
    'status-warning-background': '#443414',
    'status-warning-border': '#F4D48D',
    'status-danger-text': '#F4AFB8',
    'status-danger-background': '#491F29',
    'status-danger-border': '#F4AFB8',
    'diff-add-text': '#A7E3BC',
    'diff-add-background': '#183E2C',
    'diff-add-marker': '#A7E3BC',
    'diff-remove-text': '#F4AFB8',
    'diff-remove-background': '#491F29',
    'diff-remove-marker': '#F4AFB8',
    'diff-change-text': '#F4D48D',
    'diff-change-background': '#443414',
    'diff-change-marker': '#F4D48D',
    'chart-series-1': '#92B5D8',
    'chart-series-2': '#72C9C2',
    'chart-series-3': '#BEA7EA',
    'chart-series-4': '#E8BE68',
    'chart-series-5': '#F4AFB8',
    'chart-series-6': '#C6D1DC',
    'chart-grid': '#8294A7',
    'chart-axis': '#C6D1DC',
    'artifact-canvas-chrome': '#181818',
    'artifact-page-border': '#8294A7',
    'overlay-scrim': '#000000B3',
  },
  accents: {
    blue: { light: '#285E8A', dark: '#78B8F2' },
    teal: { light: '#086A68', dark: '#72C9C2' },
    violet: { light: '#65459B', dark: '#BEA7EA' },
    amber: { light: '#80550D', dark: '#E8BE68' },
  },
};

/** Self-contained: Vite embeds this same function before the first stylesheet. */
export function bootstrapTheme(
  tokens: typeof TOKENS,
  supplied?: ThemePreference,
): ThemePreference {
  const preference: ThemePreference = {
    version: 1,
    appearance: 'dark',
    accent: 'blue',
    density: 'compact',
    reduce_transparency: false,
  };
  try {
    const saved =
      supplied ??
      JSON.parse(localStorage.getItem('row-bot.appearance.v1') ?? 'null');
    if (saved && (saved.version === 1 || saved.version === 0)) {
      if (['system', 'light', 'dark'].includes(saved.appearance))
        preference.appearance = saved.appearance;
      if (['blue', 'teal', 'violet', 'amber'].includes(saved.accent))
        preference.accent = saved.accent;
      if (['compact', 'comfortable'].includes(saved.density))
        preference.density = saved.density;
      preference.reduce_transparency = saved.reduce_transparency === true;
    }
  } catch {
    /* Private browsing or invalid data uses safe per-device defaults. */
  }
  const mode =
    preference.appearance === 'system'
      ? matchMedia('(prefers-color-scheme: dark)').matches
        ? 'dark'
        : 'light'
      : preference.appearance;
  const root = document.documentElement;
  Object.assign(root.dataset, {
    appearance: preference.appearance,
    theme: mode,
    accent: preference.accent,
    density: preference.density,
    opaque: String(
      preference.reduce_transparency ||
        matchMedia('(prefers-reduced-transparency: reduce)').matches,
    ),
  });
  root.style.colorScheme = mode;
  for (const [name, value] of Object.entries(tokens[mode]))
    root.style.setProperty(`--${name}`, value);
  const accent = tokens.accents[preference.accent][mode];
  for (const name of [
    'accent-solid',
    'focus-ring',
    'text-link',
    'artifact-selection',
    'artifact-handle',
    'selection-background',
  ])
    root.style.setProperty(`--${name}`, accent);
  root.style.setProperty('--accent-on-solid', tokens[mode]['text-inverse']);
  root.style.setProperty('--selection-text', tokens[mode]['text-inverse']);
  root.style.setProperty(
    '--accent-subtle',
    tokens[mode]['status-info-background'],
  );
  return preference;
}
