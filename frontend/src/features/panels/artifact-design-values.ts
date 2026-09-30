/**
 * Values for the Design inspector's controls (B247). A control shows the
 * element's saved inline style when it has one, else how the element looks
 * on the canvas now (the "look" the preview reports when it is clicked).
 * The look is display only: only a value the person sets is ever saved.
 */

/** How the clicked element looks now: computed CSS, keyed by property. */
export type DesignLook = Record<string, string>;

export type BrandSwatch = { name: string; value: string; hex: string };

/** The computed property each saved style starts from. */
const LOOK_OF: Record<string, string> = {
  'border-radius': 'border-top-left-radius',
  'border-style': 'border-top-style',
  'border-width': 'border-top-width',
  'border-color': 'border-top-color',
  padding: 'padding-top',
  margin: 'margin-top',
  gap: 'row-gap',
};

/** "46px" → 46, "1.5" → 1.5; anything else (auto, 10px 14px) → null. */
export function numberOf(value: string | undefined): number | null {
  const found = /^(-?\d+(?:\.\d+)?)(px)?$/.exec((value ?? '').trim());
  return found ? Number(found[1]) : null;
}

/** #rgb, #rrggbb, rgb() or rgba() as #rrggbb; transparent → null. */
export function hexOf(value: string | undefined): string | null {
  const text = (value ?? '').trim().toLowerCase();
  if (/^#[0-9a-f]{6}$/.test(text)) return text;
  if (/^#[0-9a-f]{3}$/.test(text))
    return `#${[...text.slice(1)].map((c) => c + c).join('')}`;
  const rgb =
    /^rgba?\(\s*(\d{1,3})[\s,]+(\d{1,3})[\s,]+(\d{1,3})(?:\s*[,/]\s*([\d.]+%?))?\s*\)$/.exec(
      text,
    );
  if (!rgb) return null;
  if (rgb[4] !== undefined && parseFloat(rgb[4]) === 0) return null;
  return `#${rgb
    .slice(1, 4)
    .map((part) => Math.min(255, Number(part)).toString(16).padStart(2, '0'))
    .join('')}`;
}

/** The first family of a font stack, unquoted ("Georgia", serif → Georgia). */
export function firstFamily(value: string | undefined): string {
  return (value ?? '')
    .split(',')[0]
    .trim()
    .replace(/^["']|["']$/g, '');
}

/**
 * A style's current value: the saved inline value, else the element's look,
 * in the words the controls use (line height as a ratio, alignment as
 * left/right, colours as hex, font as one family).
 */
export function currentStyle(
  key: string,
  styles: Record<string, string>,
  look: DesignLook | undefined,
): string {
  if (styles[key]) return styles[key];
  const seen = look?.[LOOK_OF[key] ?? key]?.trim() ?? '';
  if (!seen) return '';
  switch (key) {
    case 'line-height': {
      const height = numberOf(seen);
      const size = numberOf(look?.['font-size']);
      if (height === null || !size || !seen.endsWith('px'))
        return seen === 'normal' ? '' : seen;
      return String(Math.round((height / size) * 100) / 100);
    }
    case 'letter-spacing':
      return seen === 'normal' ? '0px' : seen;
    case 'text-align':
      return seen === 'start' ? 'left' : seen === 'end' ? 'right' : seen;
    case 'font-weight':
      return seen === 'normal' ? '400' : seen === 'bold' ? '700' : seen;
    case 'font-family':
      return firstFamily(seen);
    case 'color':
    case 'background-color':
    case 'border-color':
      return hexOf(seen) ?? '';
    default:
      return seen;
  }
}

/** The five brand colours as swatches that follow the brand (var(--primary)…). */
export function brandSwatches(brand: {
  primary_color: string;
  secondary_color: string;
  accent_color: string;
  bg_color: string;
  text_color: string;
}): BrandSwatch[] {
  return (
    [
      ['Primary', 'primary', brand.primary_color],
      ['Secondary', 'secondary', brand.secondary_color],
      ['Accent', 'accent', brand.accent_color],
      ['Background', 'bg', brand.bg_color],
      ['Text', 'text', brand.text_color],
    ] as const
  ).map(([name, token, hex]) => ({
    name,
    value: `var(--${token})`,
    hex: hexOf(hex) ?? hex,
  }));
}

/** Which swatch a colour value is: its brand token, or the same hex. */
export function swatchOf(
  value: string,
  swatches: BrandSwatch[],
): BrandSwatch | undefined {
  const hex = hexOf(value);
  return swatches.find(
    (swatch) => swatch.value === value || (hex !== null && swatch.hex === hex),
  );
}
