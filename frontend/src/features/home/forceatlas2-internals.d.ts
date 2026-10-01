// graphology-layout-forceatlas2 0.10.1 ships no types for the two modules its
// own supervisor is built from; the knowledge graph runs them in a same-origin
// worker instead (the supervisor's blob: worker is refused by the CSP).
declare module 'graphology-layout-forceatlas2/iterate.js' {
  import type { ForceAtlas2Settings } from 'graphology-layout-forceatlas2';

  /** One ForceAtlas2 iteration over the node and edge matrices, in place. */
  export default function iterate(
    settings: Required<ForceAtlas2Settings>,
    nodes: Float32Array,
    edges: Float32Array,
  ): void;
}

declare module 'graphology-layout-forceatlas2/helpers.js' {
  import type Graph from 'graphology-types';

  export function graphToByteArrays(
    graph: Graph,
    getEdgeWeight: () => number,
  ): { nodes: Float32Array<ArrayBuffer>; edges: Float32Array<ArrayBuffer> };
  export function assignLayoutChanges(
    graph: Graph,
    nodes: Float32Array,
    outputReducer: null,
  ): void;
}
