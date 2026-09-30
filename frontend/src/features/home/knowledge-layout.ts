import type { ForceAtlas2Settings } from 'graphology-layout-forceatlas2';
import iterate from 'graphology-layout-forceatlas2/iterate.js';

export type LayoutSettings = Required<ForceAtlas2Settings>;

/** Sent to the layout worker; only the first request carries the edges. */
export type LayoutRequest = {
  nodes: ArrayBuffer;
  edges?: ArrayBuffer;
  settings: LayoutSettings;
  iterations: number;
};

export type LayoutReply = {
  nodes: ArrayBuffer;
  iterations: number;
  converged: boolean;
};

// ForceAtlas2's node matrix: x and y lead each node's ten values.
const NODE_VALUES = 10;
/** An iteration that moves memories less than this share of the picture's
 * width, on average, has settled. */
const SETTLED_MOVEMENT = 5e-4;

/**
 * Runs up to `iterations` ForceAtlas2 iterations in place and stops early
 * once the layout settles. Every run of the same graph ends in the same
 * picture, however the iterations are batched.
 */
export function settle(
  settings: LayoutSettings,
  nodes: Float32Array,
  edges: Float32Array,
  iterations: number,
): { iterations: number; converged: boolean } {
  const order = nodes.length / NODE_VALUES;
  const before = new Float32Array(order * 2);
  for (let done = 1; done <= iterations; done += 1) {
    for (let index = 0; index < order; index += 1) {
      before[index * 2] = nodes[index * NODE_VALUES];
      before[index * 2 + 1] = nodes[index * NODE_VALUES + 1];
    }
    iterate(settings, nodes, edges);
    let moved = 0;
    let left = Infinity;
    let right = -Infinity;
    let top = Infinity;
    let bottom = -Infinity;
    for (let index = 0; index < order; index += 1) {
      const x = nodes[index * NODE_VALUES];
      const y = nodes[index * NODE_VALUES + 1];
      moved +=
        Math.abs(x - before[index * 2]) + Math.abs(y - before[index * 2 + 1]);
      left = Math.min(left, x);
      right = Math.max(right, x);
      top = Math.min(top, y);
      bottom = Math.max(bottom, y);
    }
    const extent = Math.max(right - left, bottom - top);
    if (moved / order <= extent * SETTLED_MOVEMENT)
      return { iterations: done, converged: true };
  }
  return { iterations, converged: false };
}
