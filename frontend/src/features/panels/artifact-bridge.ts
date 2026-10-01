import type { ArtifactAuthoring } from '../../api/types';
import type { DesignLook } from './artifact-design-values';

export type ArtifactBridgeRect = { x: number; y: number; w: number; h: number };

export type ArtifactBridgeMessage =
  | {
      type: 'select';
      /** Page elements carry their panel id; others select for Ask only. */
      elementId: string | null;
      tag: string;
      text: string;
      rect: ArtifactBridgeRect | null;
      /** How it looks now, for the inspector's controls to start from. */
      look: DesignLook;
    }
  | { type: 'edit'; elementId: string; text: string }
  | { type: 'unavailable' }
  | { type: 'undo' }
  | { type: 'redo' };

/** The computed properties the preview reports (interaction.py's LOOK). */
const LOOK_KEYS = new Set([
  'font-family',
  'font-size',
  'font-weight',
  'line-height',
  'letter-spacing',
  'color',
  'text-align',
  'background-color',
  'border-top-left-radius',
  'border-top-style',
  'border-top-width',
  'border-top-color',
  'opacity',
  'width',
  'height',
  'padding-top',
  'margin-top',
  'row-gap',
  'object-fit',
  'object-position',
]);

/** Only listed properties, as short plain values (display only). */
function lookOf(value: unknown): DesignLook {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {};
  const look: DesignLook = {};
  for (const [key, item] of Object.entries(value))
    if (
      LOOK_KEYS.has(key) &&
      typeof item === 'string' &&
      item.length <= 128 &&
      !/[<>{};\\]/.test(item)
    )
      look[key] = item;
  return look;
}

function rectOf(value: unknown): ArtifactBridgeRect | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const { x, y, w, h } = value as Record<string, unknown>;
  const numbers = [x, y, w, h];
  if (
    !numbers.every(
      (item) =>
        typeof item === 'number' &&
        Number.isFinite(item) &&
        Math.abs(item) < 1_000_000,
    )
  )
    return null;
  return { x: x as number, y: y as number, w: w as number, h: h as number };
}

/** A sandbox message is a proposal, never authority to select another resource. */
export function artifactBridgeMessage(
  event: MessageEvent,
  frame: Window | null,
  identity: ArtifactAuthoring,
  revision: string,
): ArtifactBridgeMessage | null {
  if (!frame || event.source !== frame || event.origin !== 'null') return null;
  const data: unknown = event.data;
  if (!data || typeof data !== 'object' || Array.isArray(data)) return null;
  try {
    if (new TextEncoder().encode(JSON.stringify(data)).length > 16384)
      return null;
  } catch {
    return null;
  }
  const value = data as Record<string, unknown>;
  if (
    Object.keys(value).some(
      (key) =>
        !['previewId', 'revision', 'capability', 'type', 'detail'].includes(
          key,
        ),
    ) ||
    value.previewId !== identity.previewId ||
    value.capability !== identity.capability ||
    value.revision !== revision
  )
    return null;
  // Undo/redo pressed while the canvas has focus; the panel owns both.
  if (
    value.type === 'designer-undo-shortcut' ||
    value.type === 'designer-redo-shortcut'
  )
    return value.detail === undefined
      ? { type: value.type === 'designer-undo-shortcut' ? 'undo' : 'redo' }
      : null;
  const detail = value.detail;
  if (!detail || typeof detail !== 'object' || Array.isArray(detail))
    return null;
  const fields = detail as Record<string, unknown>;
  if (value.type === 'edit-unavailable' && fields.code === 'text_too_large')
    return { type: 'unavailable' };
  const info = value.type === 'text-edit' ? fields.elementInfo : fields;
  if (!info || typeof info !== 'object' || Array.isArray(info)) return null;
  const element = info as Record<string, unknown>;
  const elementId = element.elementId;
  const validId =
    typeof elementId === 'string' && /^[a-f0-9]{64}$/.test(elementId);
  if (value.type === 'element-click') {
    const tag =
      typeof element.tag === 'string' &&
      /^[a-z][a-z0-9-]{0,31}$/.test(element.tag)
        ? element.tag
        : '';
    if (!tag) return null;
    // Only a panel id is kept. Elements without one (inside an SVG, or an
    // agent-marked block's internal id) can still be asked about; they are
    // just never changed from the panel.
    return {
      type: 'select',
      elementId: validId ? (elementId as string) : null,
      tag,
      text:
        typeof element.text === 'string'
          ? element.text.replace(/\s+/g, ' ').trim().slice(0, 200)
          : '',
      rect: rectOf(element.rect),
      look: lookOf(element.style),
    };
  }
  if (
    value.type === 'text-edit' &&
    validId &&
    typeof fields.newText === 'string' &&
    fields.newText.length <= 20000
  )
    return {
      type: 'edit',
      elementId: elementId as string,
      text: fields.newText,
    };
  return null;
}
