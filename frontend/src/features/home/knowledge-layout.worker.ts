// Lays the knowledge graph out off the main thread (B250). A same-origin
// module worker: graphology-layout-forceatlas2's own supervisor builds a
// blob: worker, which the CSP (worker-src 'self') refuses.
import {
  settle,
  type LayoutReply,
  type LayoutRequest,
} from './knowledge-layout';

let edges = new Float32Array(0);

self.onmessage = ({ data }: MessageEvent<LayoutRequest>) => {
  if (data.edges) edges = new Float32Array(data.edges);
  const result = settle(
    data.settings,
    new Float32Array(data.nodes),
    edges,
    data.iterations,
  );
  const reply: LayoutReply = { nodes: data.nodes, ...result };
  self.postMessage(reply, { transfer: [data.nodes] });
};
