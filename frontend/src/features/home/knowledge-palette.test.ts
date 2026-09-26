import { afterEach, describe, expect, it } from 'vitest';
import {
  GRAPH_SLOTS,
  alpha,
  glAlpha,
  mix,
  readGraphTheme,
  typeSlot,
  typeToken,
} from './knowledge-palette';

describe('typeSlot and typeToken', () => {
  it('gives common memory types fixed slots within the nine graph colours', () => {
    const fixed = {
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
    for (const [type, slot] of Object.entries(fixed)) {
      expect(typeSlot(type)).toBe(slot);
      expect(typeToken(type)).toBe(`--graph-${slot}`);
      expect(slot).toBeGreaterThanOrEqual(1);
      expect(slot).toBeLessThanOrEqual(GRAPH_SLOTS);
    }
  });

  it('follows the type regardless of case or padding', () => {
    expect(typeSlot('  Person ')).toBe(typeSlot('person'));
    expect(typeSlot('FACT')).toBe(8);
  });

  it('sends unknown types to the neutral Other colour', () => {
    expect(typeSlot('recipe')).toBe(0);
    expect(typeSlot('')).toBe(0);
    expect(typeToken('recipe')).toBe('--graph-other');
  });
});

describe('readGraphTheme', () => {
  const root = document.documentElement;
  const names = [
    '--graph-other',
    '--graph-1',
    '--graph-9',
    '--graph-edge',
    '--graph-label',
    '--canvas',
    '--accent-solid',
    '--font-sans',
  ];

  afterEach(() => {
    for (const name of names) root.style.removeProperty(name);
  });

  it('falls back to built-in colours when the theme defines none', () => {
    const theme = readGraphTheme();
    expect(theme.slots).toHaveLength(GRAPH_SLOTS + 1);
    expect(theme.slots[0]).toBe('#8A96A6');
    expect(theme.slots.slice(1).every((color) => color === '#3987E5')).toBe(
      true,
    );
    expect(theme).toMatchObject({
      edge: '#C6D1DC',
      label: '#DCE3EB',
      canvas: '#0B0E13',
      surface: '#161B23',
      border: '#FFFFFF1F',
      accent: '#78B8F2',
      font: 'system-ui, sans-serif',
    });
  });

  it('reads and trims the current theme tokens, slot by slot', () => {
    root.style.setProperty('--graph-other', ' #111111 ');
    root.style.setProperty('--graph-1', '#222222');
    root.style.setProperty('--graph-9', '#999999');
    root.style.setProperty('--graph-edge', '#333333');
    root.style.setProperty('--graph-label', '#444444');
    root.style.setProperty('--canvas', '#555555');
    root.style.setProperty('--accent-solid', '#666666');
    root.style.setProperty('--font-sans', 'Inter, sans-serif');
    const theme = readGraphTheme();
    expect(theme.slots[0]).toBe('#111111');
    expect(theme.slots[1]).toBe('#222222');
    expect(theme.slots[2]).toBe('#3987E5');
    expect(theme.slots[9]).toBe('#999999');
    expect(theme).toMatchObject({
      edge: '#333333',
      label: '#444444',
      canvas: '#555555',
      accent: '#666666',
      font: 'Inter, sans-serif',
    });
  });

  it('reads from a given root element', () => {
    const element = document.createElement('div');
    element.style.setProperty('--graph-edge', '#010203');
    document.body.append(element);
    try {
      expect(readGraphTheme(element).edge).toBe('#010203');
      expect(readGraphTheme().edge).toBe('#C6D1DC');
    } finally {
      element.remove();
    }
  });
});

describe('colour helpers', () => {
  it('mixes a colour over a base as a solid hex colour', () => {
    expect(mix('#ffffff', '#000000', 0.5)).toBe('#808080');
    expect(mix('#3987E5', '#0B0E13', 1)).toBe('#3987e5');
    expect(mix('#3987E5', '#0B0E13', 0)).toBe('#0b0e13');
    // Shorthand expands and any alpha channel is ignored.
    expect(mix('#f00', '#000000', 1)).toBe('#ff0000');
    expect(mix('#FFFFFF80', '#000', 0.25)).toBe('#404040');
  });

  it('adds an alpha channel for the 2D canvas', () => {
    expect(alpha('#3987E5', 0.2)).toBe('rgba(57, 135, 229, 0.2)');
    expect(alpha('#fff', 0.14)).toBe('rgba(255, 255, 255, 0.14)');
  });

  it('premultiplies alpha for sigma WebGL blending', () => {
    expect(glAlpha('#C6D1DC', 0.2)).toBe('rgba(40, 42, 44, 0.2)');
    expect(glAlpha('#3987E5', 1)).toBe(alpha('#3987E5', 1));
    expect(glAlpha('#ffffff', 0)).toBe('rgba(0, 0, 0, 0)');
  });

  it('treats unreadable channels as zero instead of NaN', () => {
    expect(mix('zzzzzz', '#ffffff', 1)).toBe('#000000');
    expect(alpha('', 0.5)).toBe('rgba(0, 0, 0, 0.5)');
  });
});
