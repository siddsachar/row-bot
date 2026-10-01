import { useEffect, useState } from 'react';
import { Button, Combobox, type ComboboxOption } from '../../ui/primitives';
import type { DesignControlItem } from './ArtifactDesignControls';
import type { DesignCatalog } from './artifact-design-catalog';

const RECENT_KEY = 'row-bot.design-fonts.recent.v1';
const GENERIC = new Set(['serif', 'sans-serif', 'monospace', 'system-ui']);

/** Fonts chosen lately on this device (a per-viewer convenience). */
function readRecentFonts(): string[] {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(RECENT_KEY) ?? '[]');
    return Array.isArray(value)
      ? value
          .filter((item): item is string => typeof item === 'string')
          .slice(0, 5)
      : [];
  } catch {
    return [];
  }
}

function rememberRecentFont(name: string) {
  try {
    const next = [name, ...readRecentFonts().filter((item) => item !== name)];
    localStorage.setItem(RECENT_KEY, JSON.stringify(next.slice(0, 5)));
  } catch {
    /* The list is a convenience; private browsing simply has none. */
  }
}

/** The family a bundled font is shown in here (never one of the app's own). */
function faceName(family: string) {
  return `Row-Bot sample ${family}`;
}

const registered = new Set<string>();
/**
 * Bundled fonts ship with Row-Bot (under /static/fonts, as designer.fonts
 * serves them). Registering one fetches nothing until its name is shown.
 */
function useBundledFaces(fonts: DesignControlItem[] | null) {
  useEffect(() => {
    for (const font of fonts ?? []) {
      if (font.kind !== 'bundled' || registered.has(font.id)) continue;
      registered.add(font.id);
      const slug = font.id.toLowerCase().replace(/ /g, '-');
      document.fonts.add(
        new FontFace(
          faceName(font.id),
          `url("/static/fonts/${slug}/${slug}-400.woff2") format("woff2")`,
        ),
      );
    }
  }, [fonts]);
}

function fontStack(font: DesignControlItem) {
  if (GENERIC.has(font.id)) return font.id;
  const own =
    font.kind === 'bundled'
      ? `"${faceName(font.id)}", "${font.id}"`
      : `"${font.id}"`;
  return `${own}, system-ui, sans-serif`;
}

/**
 * A searchable font list with each name in its own face: recently used
 * first, then the fonts bundled with Row-Bot, then those on this computer.
 * Only fonts that need no download are listed (the fonts catalog).
 */
export default function ArtifactFontPicker({
  label,
  value,
  catalog,
  disabled,
  onChange,
}: {
  label: string;
  value: string;
  catalog: DesignCatalog;
  disabled: boolean;
  onChange: (font: string) => void;
}) {
  const { items: fonts, failed, retry } = catalog;
  const [recent] = useState(readRecentFonts);
  useBundledFaces(fonts);
  const listed = fonts ?? [];
  const recentFonts = recent
    .map((name) => listed.find((font) => font.id === name))
    .filter((font): font is DesignControlItem => Boolean(font));
  const option = (font: DesignControlItem, group: string): ComboboxOption => ({
    value: font.id,
    label: font.label,
    group,
    labelStyle: { fontFamily: fontStack(font) },
  });
  const rest = listed.filter((font) => !recentFonts.includes(font));
  const options = [
    ...recentFonts.map((font) => option(font, 'Recently used')),
    ...rest
      .filter((font) => font.kind === 'bundled')
      .map((font) => option(font, 'Bundled')),
    ...rest
      .filter((font) => font.kind !== 'bundled')
      .map((font) => option(font, 'On this computer')),
  ];
  return (
    <Combobox
      label={label}
      className="design-font-picker"
      value={value}
      placeholder={value || 'Choose a font'}
      options={options}
      disabled={disabled}
      emptyText={
        failed
          ? "The font list couldn't be read."
          : fonts
            ? 'No fonts match.'
            : 'Reading fonts…'
      }
      footer={
        failed ? (
          <Button variant="ghost" onClick={retry}>
            Try again
          </Button>
        ) : (
          <small className="muted">
            Only fonts already on this computer are listed. Nothing is
            downloaded.
          </small>
        )
      }
      onChange={(font) => {
        rememberRecentFont(font);
        onChange(font);
      }}
    />
  );
}
