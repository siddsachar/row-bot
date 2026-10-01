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
it('selects an element with its box, tag and short text for the anchored prompt', () => {
  const select = (detail: Record<string, unknown>) =>
    artifactBridgeMessage(
      event({
        data: {
          ...identity,
          revision: 'revision',
          type: 'element-click',
          detail,
        },
      }),
      frame,
      identity,
      'revision',
    );
  expect(
    select({
      elementId,
      tag: 'h1',
      text: '  Launch   day  ',
      rect: { x: 10, y: 20, w: 300, h: 40 },
    }),
  ).toEqual({
    type: 'select',
    elementId,
    tag: 'h1',
    text: 'Launch day',
    rect: { x: 10, y: 20, w: 300, h: 40 },
    look: {},
  });
  // Elements without an editable id can still be asked about, never edited.
  expect(select({ elementId: '', tag: 'img', text: '' })).toMatchObject({
    elementId: null,
    tag: 'img',
    rect: null,
  });
  // An agent-marked ancestor's internal id is not editable: still selectable.
  expect(select({ elementId: 'el-81befbe4', tag: 'h1' })).toMatchObject({
    type: 'select',
    elementId: null,
    tag: 'h1',
  });
  expect(select({ elementId: { forged: true }, tag: 'p' })).toMatchObject({
    elementId: null,
  });
  expect(select({ elementId, tag: '<script>' })).toBeNull();
  expect(
    select({ elementId, tag: 'p', rect: { x: 'a', y: 0, w: 1, h: 1 } }),
  ).toMatchObject({ rect: null });
});
it('carries how a selected element looks now, only as short plain listed values', () => {
  const message = artifactBridgeMessage(
    event({
      data: {
        ...identity,
        revision: 'revision',
        type: 'element-click',
        detail: {
          elementId,
          tag: 'h1',
          style: {
            'font-size': '46px',
            color: 'rgb(20, 25, 34)',
            'font-family': '"Georgia", serif',
            'background-image': 'url(https://example.invalid/x.png)',
            'text-align': 'left; color: red',
            width: 'x'.repeat(200),
            opacity: 1,
          },
        },
      },
    }),
    frame,
    identity,
    'revision',
  );
  expect(message).toEqual(
    expect.objectContaining({
      type: 'select',
      look: {
        'font-size': '46px',
        color: 'rgb(20, 25, 34)',
        'font-family': '"Georgia", serif',
      },
    }),
  );
});
it('turns canvas undo and redo shortcuts into panel actions', () => {
  const shortcut = (type: string, extra = {}) =>
    artifactBridgeMessage(
      event({ data: { ...identity, revision: 'revision', type, ...extra } }),
      frame,
      identity,
      'revision',
    );
  expect(shortcut('designer-undo-shortcut')).toEqual({ type: 'undo' });
  expect(shortcut('designer-redo-shortcut')).toEqual({ type: 'redo' });
  expect(shortcut('designer-undo-shortcut', { detail: {} })).toBeNull();
});
