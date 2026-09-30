import Graph from 'graphology';
import { graphToByteArrays } from 'graphology-layout-forceatlas2/helpers.js';
import { describe, expect, it } from 'vitest';
import { settle, type LayoutSettings } from './knowledge-layout';

const settings: LayoutSettings = {
  linLogMode: false,
  outboundAttractionDistribution: false,
  adjustSizes: false,
  edgeWeightInfluence: 1,
  barnesHutTheta: 0.5,
  gravity: 1.2,
  strongGravityMode: true,
  scalingRatio: 6,
  slowDown: 3,
  barnesHutOptimize: false,
};

/** A hub with `count` spokes, the spokes starting on a small spiral. */
function matrices(count: number) {
  const graph = new Graph();
  graph.addNode('hub', { x: 0, y: 0, size: 11 });
  for (let index = 0; index < count; index += 1) {
    graph.addNode(`spoke-${index}`, {
      x: Math.cos(index) * (1 + index),
      y: Math.sin(index) * (1 + index),
      size: 3,
    });
    graph.addEdge('hub', `spoke-${index}`);
    if (index % 3 === 0 && index > 0)
      graph.addEdge(`spoke-${index}`, `spoke-${index - 1}`);
  }
  return graphToByteArrays(graph, () => 1);
}

describe('settle', () => {
  it('ends in the same picture however the iterations are batched', () => {
    const whole = matrices(40);
    expect(settle(settings, whole.nodes, whole.edges, 30)).toEqual({
      iterations: 30,
      converged: false,
    });
    const batched = matrices(40);
    let done = 0;
    for (const batch of [1, 2, 4, 9, 14])
      done += settle(settings, batched.nodes, batched.edges, batch).iterations;
    expect(done).toBe(30);
    expect(Array.from(batched.nodes)).toEqual(Array.from(whole.nodes));
  });

  it('stops early once a whole iteration barely moves anything', () => {
    const graph = matrices(4);
    const result = settle(settings, graph.nodes, graph.edges, 1000);
    expect(result.converged).toBe(true);
    expect(result.iterations).toBeLessThan(1000);
    expect(graph.nodes.every(Number.isFinite)).toBe(true);
  });
});
