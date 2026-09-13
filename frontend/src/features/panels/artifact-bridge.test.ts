import { expect, it } from 'vitest';
import { artifactBridgeMessage } from './artifact-bridge';

const frame = {} as Window;
const identity = {
  previewId: 'synthetic-preview',
  capability: 'synthetic-capability',
};
const elementId = 'a'.repeat(64);
function event(extra = {}) {
  return {
    source: frame,
    origin: 'null',
    data: {
      ...identity,
      revision: 'revision',
      type: 'text-edit',
      detail: {
        elementInfo: { elementId },
        newText: '<img onerror=synthetic>',
      },
    },
    ...extra,
  } as MessageEvent;
}

it('accepts only a current exact-frame plain text proposal', () => {
  expect(artifactBridgeMessage(event(), frame, identity, 'revision')).toEqual({
    type: 'edit',
    elementId,
    text: '<img onerror=synthetic>',
  });
});
it.each([
  { source: {} },
  { origin: 'https://foreign.invalid' },
  { data: { ...event().data, capability: 'old' } },
  { data: { ...event().data, revision: 'old' } },
  { data: { ...event().data, resource_id: 'another-resource' } },
  {
    data: {
      ...event().data,
      detail: { elementInfo: { elementId }, newText: 'a'.repeat(17000) },
    },
  },
])('rejects foreign/stale/oversized messages %#', (extra) => {
  expect(
    artifactBridgeMessage(event(extra), frame, identity, 'revision'),
  ).toBeNull();
});
it('exposes an explicit oversize edit outcome without a truncated write', () => {
  expect(
    artifactBridgeMessage(
      event({
        data: {
          ...identity,
          revision: 'revision',
          type: 'edit-unavailable',
          detail: { code: 'text_too_large' },
        },
      }),
      frame,
      identity,
      'revision',
    ),
  ).toEqual({ type: 'unavailable' });
});
