import type { ArtifactAuthoring } from '../../api/types';

export type ArtifactBridgeMessage =
  | { type: 'select'; elementId: string }
  | { type: 'edit'; elementId: string; text: string }
  | { type: 'unavailable' };

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
  const detail = value.detail;
  if (!detail || typeof detail !== 'object' || Array.isArray(detail))
    return null;
  const fields = detail as Record<string, unknown>;
  if (value.type === 'edit-unavailable' && fields.code === 'text_too_large')
    return { type: 'unavailable' };
  const info = value.type === 'text-edit' ? fields.elementInfo : fields;
  if (!info || typeof info !== 'object' || Array.isArray(info)) return null;
  const elementId = (info as Record<string, unknown>).elementId;
  if (typeof elementId !== 'string' || !/^[a-f0-9]{64}$/.test(elementId))
    return null;
  if (value.type === 'element-click') return { type: 'select', elementId };
  if (
    value.type === 'text-edit' &&
    typeof fields.newText === 'string' &&
    fields.newText.length <= 20000
  )
    return { type: 'edit', elementId, text: fields.newText };
  return null;
}
