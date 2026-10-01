/**
 * Knowledge graph type colours. Nine validated hues (theme tokens graph-1..9)
 * plus a neutral "Other". Colour follows the type, never its rank: common
 * types have fixed slots, and a filter never repaints the survivors. More
 * types than slots share "Other" and are told apart by the legend, labels,
 * the inspector and the List view.
 */
export const GRAPH_SLOTS = 9;

const FIXED: Record<string, number> = {
  concept: 1,
  project: 2,
  preference: 3,
  person: 4,
  organisation: 5,
  organization: 5,
  place: 6,
  location: 6,
  event: 7,
  fact: 8,
  self_knowledge: 9,
};

/** 1–9 for a coloured slot, 0 for Other. */
export function typeSlot(type: string): number {
  return FIXED[type.trim().toLowerCase()] ?? 0;
}

export function typeToken(type: string): string {
  const slot = typeSlot(type);
  return slot ? `--graph-${slot}` : '--graph-other';
}

/** Read the current theme's graph colours from the root style. */
export function readGraphTheme(root: HTMLElement = document.documentElement) {
  const style = getComputedStyle(root);
  const read = (name: string, fallback: string) =>
    style.getPropertyValue(name).trim() || fallback;
  return {
    slots: Array.from({ length: GRAPH_SLOTS + 1 }, (_, index) =>
      index === 0
        ? read('--graph-other', '#8A96A6')
        : read(`--graph-${index}`, '#3987E5'),
    ),
    edge: read('--graph-edge', '#C6D1DC'),
    label: read('--graph-label', '#DCE3EB'),
    canvas: read('--canvas', '#0B0E13'),
    surface: read('--surface-overlay', '#161B23'),
    border: read('--border-hairline', '#FFFFFF1F'),
    accent: read('--accent-solid', '#78B8F2'),
    font: read('--font-sans', 'system-ui, sans-serif'),
  };
}

export type GraphTheme = ReturnType<typeof readGraphTheme>;

function channels(hex: string): [number, number, number, number] {
  const value = hex.replace('#', '');
  const full =
    value.length === 3 || value.length === 4
      ? value
          .split('')
          .map((part) => part + part)
          .join('')
      : value;
  const number = (start: number) =>
    parseInt(full.slice(start, start + 2), 16) || 0;
  return [
    number(0),
    number(2),
    number(4),
    full.length >= 8 ? number(6) / 255 : 1,
  ];
}

/** `color` over `base` at `amount` (0–1) opacity, as a solid hex colour. */
export function mix(color: string, base: string, amount: number): string {
  const [r1, g1, b1] = channels(color);
  const [r2, g2, b2] = channels(base);
  const part = (a: number, b: number) =>
    Math.round(a * amount + b * (1 - amount))
      .toString(16)
      .padStart(2, '0');
  return `#${part(r1, r2)}${part(g1, g2)}${part(b1, b2)}`;
}

/** A hex colour with an alpha channel, as `rgba()` for the 2D canvas. */
export function alpha(color: string, amount: number): string {
  const [r, g, b] = channels(color);
  return `rgba(${r}, ${g}, ${b}, ${amount})`;
}

/**
 * The same colour premultiplied for sigma's WebGL programs, which blend with
 * ONE / ONE_MINUS_SRC_ALPHA: without it a faint edge draws at full strength.
 */
export function glAlpha(color: string, amount: number): string {
  const [r, g, b] = channels(color);
  const part = (value: number) => Math.round(value * amount);
  return `rgba(${part(r)}, ${part(g)}, ${part(b)}, ${amount})`;
}
