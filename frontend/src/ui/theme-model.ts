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
  // Follow the operating system until the person chooses; dark stays the
  // showcase appearance for captures and marketing.
  appearance: 'system',
  accent: 'blue',
  density: 'compact',
  reduce_transparency: false,
};
export const TOKENS = {
  light: {
    canvas: '#F3F5F7',
    surface: '#FFFFFF',
    'surface-raised': '#F9FAFB',
    'surface-overlay': '#FFFFFF',
    'surface-sunken': '#ECEFF2',
    'surface-soft': '#EFF3F8',
    'surface-hover': '#ECEFF3',
    'surface-pressed': '#E1E6EC',
    'surface-disabled': '#E8EBEF',
    'surface-highlight': '#FFFFFFCC',
    'shadow-color': '#0F172A24',
    'text-primary': '#141922',
    'text-secondary': '#3A4351',
    'text-muted': '#535D6C',
    'text-disabled': '#535D6C',
    'text-inverse': '#FFFFFF',
    'border-subtle': '#DDE2E8',
    'border-hairline': '#0F172A14',
    'border-control': '#6B7686',
    'border-strong': '#4A5566',
    'code-background': '#F1F3F6',
    'code-text': '#141922',
    'code-comment': '#535D6C',
    'syntax-keyword': '#65459B',
    'syntax-string': '#175A3C',
    'syntax-number': '#80550D',
    'syntax-function': '#285E8A',
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
    // Knowledge graph types: 9 hues + Other, validated for CVD separation and
    // the normal-vision floor on the canvas (see CLIENT_PLATFORM_PRODUCT_SYSTEM).
    'graph-1': '#2A78D6',
    'graph-2': '#EB6834',
    'graph-3': '#1BAF7A',
    'graph-4': '#EDA100',
    'graph-5': '#E87BA4',
    'graph-6': '#008300',
    'graph-7': '#4A3AA7',
    'graph-8': '#E34948',
    'graph-9': '#0E8FB0',
    'graph-other': '#6B7686',
    'graph-edge': '#1E293B',
    'graph-label': '#141922',
    'artifact-canvas-chrome': '#EEF1F4',
    'artifact-page-border': '#6B7686',
    'overlay-scrim': '#0F172A66',
  },
  dark: {
    canvas: '#0B0E13',
    surface: '#11151C',
    'surface-raised': '#171C25',
    'surface-overlay': '#1C222C',
    'surface-sunken': '#080A0E',
    'surface-soft': '#121925',
    'surface-hover': '#212834',
    'surface-pressed': '#2A3240',
    'surface-disabled': '#181D25',
    'surface-highlight': '#FFFFFF0D',
    'shadow-color': '#000000A6',
    'text-primary': '#E8EBF0',
    'text-secondary': '#BCC4CF',
    'text-muted': '#98A2B1',
    'text-disabled': '#A4AEBC',
    'text-inverse': '#0B0E13',
    'border-subtle': '#252C37',
    'border-hairline': '#FFFFFF12',
    'border-control': '#7D8899',
    'border-strong': '#9AA5B5',
    'code-background': '#131820',
    'code-text': '#F1F5F9',
    'code-comment': '#A6B6C7',
    'syntax-keyword': '#BEA7EA',
    'syntax-string': '#A7E3BC',
    'syntax-number': '#E8BE68',
    'syntax-function': '#8CC3F5',
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
    'graph-1': '#3987E5',
    'graph-2': '#D95926',
    'graph-3': '#199E70',
    'graph-4': '#C98500',
    'graph-5': '#D55181',
    'graph-6': '#008300',
    'graph-7': '#9085E9',
    'graph-8': '#E66767',
    'graph-9': '#1FA3C4',
    'graph-other': '#8A96A6',
    'graph-edge': '#C6D1DC',
    'graph-label': '#DCE3EB',
    'artifact-canvas-chrome': '#0E1218',
    'artifact-page-border': '#7D8899',
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
    appearance: 'system',
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
