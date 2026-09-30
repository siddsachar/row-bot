import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from 'react';
import type Graph from 'graphology';
import type { KnowledgeGraphEdge, KnowledgeGraphNode } from './KnowledgeHome';
import { plural } from './home-format';
import type {
  LayoutReply,
  LayoutRequest,
  LayoutSettings,
} from './knowledge-layout';
import {
  alpha,
  glAlpha,
  mix,
  readGraphTheme,
  typeSlot,
  type GraphTheme,
} from './knowledge-palette';

export type KnowledgeGraphHandle = {
  fit(): void;
  zoom(delta: number): void;
  /** Move the camera to a memory (eased unless motion is reduced). */
  focus(id: string): void;
};

type GraphStatus = 'loading' | 'ready' | 'failed';
type LayoutState = 'settling' | 'settled';
type LayoutHelpers = typeof import('graphology-layout-forceatlas2/helpers.js');

/** How long the graph visibly settles when motion is allowed. */
const SETTLE_MS = 2500;

/**
 * Where each memory came to rest this session: coming back to Knowledge
 * shows the same picture at once instead of settling it again.
 */
const restingPlaces = new Map<string, { x: number; y: number }>();

type SigmaLike = {
  kill(): void;
  refresh(): void;
  resize(): void;
  on(event: string, handler: (payload: { node?: string }) => void): void;
  setSetting(key: string, value: unknown): void;
  getNodeDisplayData(
    key: string,
  ): { x: number; y: number; size: number } | undefined;
  getCamera(): {
    animate(
      state: { x?: number; y?: number; ratio?: number },
      options?: { duration?: number; easing?: string },
    ): Promise<void>;
    setState(state: { x?: number; y?: number; ratio?: number }): void;
    getState(): { x: number; y: number; ratio: number; angle: number };
    animatedReset(options?: { duration?: number }): Promise<void>;
  };
};

/** WebGL is required; jsdom and locked-down hosts fall back to the list. */
export function webglAvailable(): boolean {
  if (typeof window === 'undefined') return false;
  if (typeof window.WebGLRenderingContext === 'undefined') return false;
  try {
    const canvas = document.createElement('canvas');
    return Boolean(canvas.getContext('webgl2') ?? canvas.getContext('webgl'));
  } catch {
    return false;
  }
}

function reducedMotion() {
  return Boolean(
    window.matchMedia?.('(prefers-reduced-motion: reduce)').matches,
  );
}

/** Stable pseudo-random seed from a memory id (FNV-1a). */
function seed(id: string) {
  let hash = 2166136261;
  for (let index = 0; index < id.length; index += 1)
    hash = Math.imul(hash ^ id.charCodeAt(index), 16777619);
  return hash >>> 0;
}

export function nodeSize(node: KnowledgeGraphNode) {
  const size = 2.6 + 1.9 * Math.sqrt(Math.max(0, node.relation_count));
  return Math.min(16, node.is_user ? Math.max(size, 11) : size);
}

/**
 * Runs ForceAtlas2 in a worker (B250). When animating, each batch is placed
 * on the canvas as it arrives and the batches are paced to finish in about
 * SETTLE_MS; otherwise the whole layout is placed once. `onDone` says
 * whether the layout ran. Returns a stop that leaves the graph where it is.
 */
function runLayout(
  graph: Graph,
  helpers: LayoutHelpers,
  animate: boolean,
  onDone: (laidOut: boolean) => void,
): () => void {
  const matrices = helpers.graphToByteArrays(graph, () => 1);
  const total = graph.order > 400 ? 140 : 220;
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
    barnesHutOptimize: graph.order > 200,
  };
  const worker = new Worker(
    new URL('./knowledge-layout.worker.ts', import.meta.url),
    { type: 'module' },
  );
  const started = performance.now();
  let done = 0;
  let requests = 0;
  let frame = 0;
  let stopped = false;
  const stop = () => {
    stopped = true;
    cancelAnimationFrame(frame);
    worker.terminate();
  };
  const request = (nodes: ArrayBuffer) => {
    const remaining = total - done;
    let iterations = remaining;
    if (animate) {
      // What is left, spread over the frames left at the pace seen so far.
      const elapsed = performance.now() - started;
      const pace = requests ? elapsed / requests : 1000 / 60;
      const frames = Math.max(1, (SETTLE_MS - elapsed) / pace);
      iterations = Math.ceil(remaining / frames);
    }
    const message: LayoutRequest = { nodes, settings, iterations };
    if (requests === 0) message.edges = matrices.edges.buffer;
    worker.postMessage(
      message,
      message.edges ? [nodes, message.edges] : [nodes],
    );
    requests += 1;
  };
  worker.onmessage = ({ data }: MessageEvent<LayoutReply>) => {
    if (stopped) return;
    done += data.iterations;
    const settled = data.converged || done >= total;
    if (animate || settled)
      helpers.assignLayoutChanges(graph, new Float32Array(data.nodes), null);
    if (!settled) {
      frame = requestAnimationFrame(() => request(data.nodes));
      return;
    }
    stop();
    onDone(true);
  };
  // A worker that cannot run leaves the memories where they started.
  worker.onerror = () => {
    stop();
    onDone(false);
  };
  request(matrices.nodes.buffer);
  return stop;
}

function roundRect(
  context: CanvasRenderingContext2D,
  x: number,
  y: number,
  width: number,
  height: number,
  radius: number,
) {
  context.beginPath();
  context.moveTo(x + radius, y);
  context.arcTo(x + width, y, x + width, y + height, radius);
  context.arcTo(x + width, y + height, x, y + height, radius);
  context.arcTo(x, y + height, x, y, radius);
  context.arcTo(x, y, x + width, y, radius);
  context.closePath();
}

const KnowledgeGraphCanvas = forwardRef<
  KnowledgeGraphHandle,
  {
    nodes: KnowledgeGraphNode[];
    edges: KnowledgeGraphEdge[];
    /** Memories that pass the filters; others are hidden in place. */
    visible: ReadonlySet<string>;
    selectedId: string | null;
    onSelect: (id: string | null) => void;
    onStatus?: (status: GraphStatus) => void;
  }
>(function KnowledgeGraphCanvas(
  { nodes, edges, visible, selectedId, onSelect, onStatus },
  forwardedRef,
) {
  const root = useRef<HTMLDivElement>(null);
  const sigma = useRef<SigmaLike | null>(null);
  const neighbours = useRef(new Map<string, Set<string>>());
  const visibleRef = useRef(visible);
  const selectedRef = useRef(selectedId);
  const hoveredRef = useRef<string | null>(null);
  const themeRef = useRef<GraphTheme | null>(null);
  const selectCallback = useRef(onSelect);
  const statusCallback = useRef(onStatus);
  const [status, setStatus] = useState<GraphStatus>('loading');
  const [layout, setLayout] = useState<LayoutState>('settling');
  /** Stops a settling layout where it is; set only while one runs. */
  const interruptLayout = useRef<(() => void) | null>(null);
  visibleRef.current = visible;
  selectedRef.current = selectedId;
  selectCallback.current = onSelect;
  statusCallback.current = onStatus;

  const moveTo = (state: { x?: number; y?: number; ratio?: number }) => {
    const camera = sigma.current?.getCamera();
    if (!camera) return;
    if (reducedMotion()) camera.setState(state);
    else
      void camera.animate(state, { duration: 420, easing: 'quadraticInOut' });
  };

  // Navigating stops a settling layout, so the view holds still under it.
  useImperativeHandle(forwardedRef, () => ({
    fit: () => {
      interruptLayout.current?.();
      const camera = sigma.current?.getCamera();
      if (!camera) return;
      if (reducedMotion()) camera.setState({ x: 0.5, y: 0.5, ratio: 1 });
      else void camera.animatedReset({ duration: 320 });
    },
    zoom: (delta) => {
      interruptLayout.current?.();
      const camera = sigma.current?.getCamera();
      if (!camera) return;
      const { ratio } = camera.getState();
      moveTo({
        ratio: Math.min(4, Math.max(0.06, ratio / (1 + delta * 2.5))),
      });
    },
    focus: (id) => {
      interruptLayout.current?.();
      const data = sigma.current?.getNodeDisplayData(id);
      if (!data) return;
      const { ratio } = sigma.current!.getCamera().getState();
      moveTo({ x: data.x, y: data.y, ratio: Math.min(ratio, 0.45) });
    },
  }));

  useEffect(() => {
    let active = true;
    let instance: SigmaLike | null = null;
    let observer: ResizeObserver | undefined;
    let themeObserver: MutationObserver | undefined;
    let stopLayout: (() => void) | undefined;
    const element = root.current;
    const report = (next: GraphStatus) => {
      if (!active) return;
      setStatus(next);
      statusCallback.current?.(next);
    };
    report('loading');
    setLayout('settling');
    if (!element || !webglAvailable()) {
      report('failed');
      return () => {
        active = false;
      };
    }
    void Promise.all([
      import('graphology'),
      import('sigma'),
      import('graphology-layout-forceatlas2/helpers.js'),
    ])
      .then(async ([graphology, sigmaModule, helpers]) => {
        if (!active) return;
        const Graph = graphology.default;
        const Sigma = sigmaModule.default;
        const graph = new Graph({ multi: true, allowSelfLoops: false });
        for (const node of nodes) {
          const value = seed(node.id);
          const angle = ((value % 3600) / 3600) * Math.PI * 2;
          const radius = Math.sqrt(((value >>> 12) % 1000) / 1000) * 100;
          const rested = restingPlaces.get(node.id);
          graph.addNode(node.id, {
            x: rested?.x ?? Math.cos(angle) * radius,
            y: rested?.y ?? Math.sin(angle) * radius,
            size: nodeSize(node),
            label: node.subject,
            slot: typeSlot(node.entity_type),
            user: node.is_user,
          });
        }
        const links = new Map<string, Set<string>>();
        const link = (from: string, to: string) => {
          if (!links.has(from)) links.set(from, new Set());
          links.get(from)!.add(to);
        };
        for (const edge of edges) {
          if (
            edge.source_id === edge.target_id ||
            !graph.hasNode(edge.source_id) ||
            !graph.hasNode(edge.target_id)
          )
            continue;
          graph.addEdgeWithKey(edge.id, edge.source_id, edge.target_id, {
            size: 0.6,
          });
          link(edge.source_id, edge.target_id);
          link(edge.target_id, edge.source_id);
        }
        neighbours.current = links;
        const rest = (laidOut = true) => {
          interruptLayout.current = null;
          if (laidOut)
            graph.forEachNode((key, { x, y }) =>
              restingPlaces.set(key, { x, y }),
            );
          if (active) setLayout('settled');
        };
        // The seeded start is drawn at once and settles in view; with
        // reduced motion the layout is placed before the first frame.
        const settling =
          graph.order > 1 && nodes.some((node) => !restingPlaces.has(node.id));
        const animate = settling && !reducedMotion();
        if (!settling) setLayout('settled');
        else if (!animate) {
          const laidOut = await new Promise<boolean>((resolve) => {
            stopLayout = runLayout(graph, helpers, false, resolve);
          });
          if (!active) return;
          rest(laidOut);
        }
        themeRef.current = readGraphTheme();
        const theme = () => themeRef.current ?? readGraphTheme();
        instance = new Sigma(graph, element, {
          allowInvalidContainer: true,
          renderEdgeLabels: false,
          defaultEdgeType: 'line',
          defaultEdgeColor: glAlpha(theme().edge, 0.2),
          labelColor: { color: theme().label },
          labelFont: theme().font,
          labelSize: 12,
          labelWeight: '500',
          labelDensity: 0.4,
          labelGridCellSize: 110,
          labelRenderedSizeThreshold: 9,
          zIndex: true,
          minCameraRatio: 0.06,
          maxCameraRatio: 4,
          stagePadding: 36,
          // Labels sit on a canvas-coloured halo so crossing edges never
          // run through the text.
          defaultDrawNodeLabel: (context, data, settings) => {
            if (!data.label) return;
            const colors = theme();
            const size = settings.labelSize;
            context.font = `${settings.labelWeight} ${size}px ${settings.labelFont}`;
            const x = data.x + data.size + 4;
            const y = data.y + size / 3;
            context.lineJoin = 'round';
            context.lineWidth = 4;
            context.strokeStyle = colors.canvas;
            context.strokeText(data.label, x, y);
            context.fillStyle = colors.label;
            context.fillText(data.label, x, y);
          },
          defaultDrawNodeHover: (context, data, settings) => {
            const colors = theme();
            const label = data.label ?? '';
            context.beginPath();
            context.arc(data.x, data.y, data.size + 2.5, 0, Math.PI * 2);
            context.strokeStyle = colors.accent;
            context.lineWidth = 1.5;
            context.stroke();
            if (!label) return;
            const size = settings.labelSize;
            context.font = `${settings.labelWeight} ${size}px ${settings.labelFont}`;
            const width = context.measureText(label).width + 14;
            const height = size + 10;
            const x = data.x + data.size + 5;
            const y = data.y - height / 2;
            roundRect(context, x, y, width, height, 6);
            context.fillStyle = colors.surface;
            context.fill();
            context.strokeStyle = alpha(colors.label, 0.14);
            context.lineWidth = 1;
            context.stroke();
            context.fillStyle = colors.label;
            context.fillText(label, x + 7, data.y + size / 3);
          },
          nodeReducer: (node, data) => {
            const colors = theme();
            const color = colors.slots[(data.slot as number) ?? 0];
            if (!visibleRef.current.has(node)) return { ...data, hidden: true };
            const focus = selectedRef.current ?? hoveredRef.current;
            if (!focus || !visibleRef.current.has(focus))
              return {
                ...data,
                color,
                forceLabel: Boolean(data.user),
              };
            const near = neighbours.current.get(focus);
            if (node === focus)
              return {
                ...data,
                color,
                highlighted: node === selectedRef.current,
                forceLabel: true,
                zIndex: 3,
              };
            if (near?.has(node))
              return {
                ...data,
                color,
                forceLabel: (near.size ?? 0) <= 24,
                zIndex: 2,
              };
            return {
              ...data,
              color: mix(color, colors.canvas, 0.15),
              label: '',
              zIndex: 0,
            };
          },
          edgeReducer: (edge, data) => {
            const graphEdges = graph.extremities(edge);
            const [from, to] = graphEdges;
            if (!visibleRef.current.has(from) || !visibleRef.current.has(to))
              return { ...data, hidden: true };
            const colors = theme();
            const focus = selectedRef.current ?? hoveredRef.current;
            if (!focus || !visibleRef.current.has(focus))
              return { ...data, color: glAlpha(colors.edge, 0.2) };
            if (from === focus || to === focus)
              return {
                ...data,
                color: glAlpha(colors.accent, 0.6),
                size: 1,
                zIndex: 1,
              };
            return { ...data, color: glAlpha(colors.edge, 0.05) };
          },
        } as ConstructorParameters<typeof Sigma>[2]) as unknown as SigmaLike;
        sigma.current = instance;
        instance.on('clickNode', ({ node }) => {
          if (node) selectCallback.current(node);
        });
        instance.on('clickStage', () => selectCallback.current(null));
        instance.on('enterNode', ({ node }) => {
          hoveredRef.current = node ?? null;
          element.style.cursor = 'pointer';
          instance?.refresh();
        });
        instance.on('leaveNode', () => {
          hoveredRef.current = null;
          element.style.cursor = '';
          instance?.refresh();
        });
        // A press (drag, click, pinch) or the wheel stops the settling.
        for (const event of [
          'downNode',
          'downStage',
          'wheelNode',
          'wheelStage',
        ])
          instance.on(event, () => interruptLayout.current?.());
        let size = { width: element.clientWidth, height: element.clientHeight };
        observer =
          typeof ResizeObserver === 'undefined'
            ? undefined
            : new ResizeObserver(() => {
                const next = {
                  width: element.clientWidth,
                  height: element.clientHeight,
                };
                if (next.width === size.width && next.height === size.height)
                  return;
                size = next;
                instance?.resize();
                instance?.refresh();
              });
        observer?.observe(element);
        themeObserver = new MutationObserver(() => {
          themeRef.current = readGraphTheme();
          const colors = themeRef.current;
          instance?.setSetting('labelColor', { color: colors.label });
          instance?.setSetting('defaultEdgeColor', glAlpha(colors.edge, 0.2));
          instance?.refresh();
        });
        themeObserver.observe(document.documentElement, {
          attributes: true,
          attributeFilter: ['data-theme', 'data-accent', 'style'],
        });
        report('ready');
        if (animate) {
          stopLayout = runLayout(graph, helpers, true, rest);
          interruptLayout.current = () => {
            stopLayout?.();
            rest();
          };
        }
      })
      .catch(() => report('failed'));
    return () => {
      active = false;
      // Leaving mid-settle keeps nothing: next time it settles again.
      stopLayout?.();
      interruptLayout.current = null;
      observer?.disconnect();
      themeObserver?.disconnect();
      instance?.kill();
      sigma.current = null;
      element?.replaceChildren();
    };
  }, [edges, nodes]);

  // Filters and focus change what shows, never where it sits.
  useEffect(() => {
    sigma.current?.refresh();
  }, [visible, selectedId]);

  const shownEdges = useMemo(
    () =>
      edges.filter(
        (edge) => visible.has(edge.source_id) && visible.has(edge.target_id),
      ).length,
    [edges, visible],
  );

  return (
    <div
      className="knowledge-network-shell"
      data-renderer-status={status}
      data-layout={layout}
    >
      <div
        ref={root}
        className="knowledge-network-canvas"
        role="img"
        aria-label={`Interactive knowledge graph with ${plural(visible.size, 'memory', 'memories')} and ${plural(shownEdges, 'connection')}`}
        aria-hidden={status !== 'ready'}
      />
      {status === 'loading' && (
        <p className="knowledge-graph-status" role="status">
          Drawing the graph…
        </p>
      )}
    </div>
  );
});

export default KnowledgeGraphCanvas;
