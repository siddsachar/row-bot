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
import { bounds, Glide } from './knowledge-motion';
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
type Matrices = ReturnType<LayoutHelpers['graphToByteArrays']>;

/** While the graph settles in view, a layout batch runs for about this long,
 * so the layout streams in as fast as the worker can go. */
const BATCH_MS = 24;
/** Memories without a place start this much nearer the middle, so the graph
 * blooms out into place. */
const BLOOM = 0.25;
/** The settle ends once nothing is further than this share of the picture
 * from its place: about a pixel. */
const AT_REST = 1 / 800;

/**
 * Where each memory came to rest this session: coming back to Knowledge
 * shows the same picture at once instead of settling it again.
 */
const restingPlaces = new Map<string, { x: number; y: number }>();

type BBox = { x: [number, number]; y: [number, number] };

type SigmaLike = {
  kill(): void;
  refresh(options?: {
    partialGraph?: { nodes?: string[] };
    skipIndexation?: boolean;
    schedule?: boolean;
  }): void;
  resize(): void;
  on(event: string, handler: (payload: { node?: string }) => void): void;
  setSetting(key: string, value: unknown): void;
  setCustomBBox(box: BBox | null): void;
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

// ForceAtlas2's node matrix: x and y lead each node's ten values.
const NODE_VALUES = 10;
// A graph this small labels every memory; a larger one labels by room and on focus (F9).
const LABEL_EVERY = 25;

/** The x, y pairs of a ForceAtlas2 node matrix. */
function positions(matrix: Float32Array) {
  const pairs = new Float64Array((matrix.length / NODE_VALUES) * 2);
  for (let index = 0; index < pairs.length / 2; index += 1) {
    pairs[index * 2] = matrix[index * NODE_VALUES];
    pairs[index * 2 + 1] = matrix[index * NODE_VALUES + 1];
  }
  return pairs;
}

/**
 * Runs ForceAtlas2 in a worker (B250) from the positions in `matrices`.
 * `onBatch` sees the node matrix after each batch: the whole layout at once,
 * or, when `streamed`, batches of about BATCH_MS run back to back. The
 * iteration count is fixed, so the same graph always ends in the same
 * picture. `onDone` says whether the layout ran. Returns a stop.
 */
function runLayout(
  matrices: Matrices,
  streamed: boolean,
  onBatch: (nodes: Float32Array) => void,
  onDone: (laidOut: boolean) => void,
): () => void {
  const order = matrices.nodes.length / NODE_VALUES;
  const total = order > 400 ? 140 : 220;
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
    barnesHutOptimize: order > 200,
  };
  const worker = new Worker(
    new URL('./knowledge-layout.worker.ts', import.meta.url),
    { type: 'module' },
  );
  let done = 0;
  let stopped = false;
  const stop = () => {
    stopped = true;
    worker.terminate();
  };
  const request = (nodes: ArrayBuffer, edges?: ArrayBuffer) => {
    const message: LayoutRequest = {
      nodes,
      settings,
      iterations: total - done,
    };
    if (streamed) message.budget = BATCH_MS;
    if (edges) message.edges = edges;
    worker.postMessage(message, edges ? [nodes, edges] : [nodes]);
  };
  worker.onmessage = ({ data }: MessageEvent<LayoutReply>) => {
    if (stopped) return;
    done += data.iterations;
    onBatch(new Float32Array(data.nodes));
    if (!data.converged && done < total) {
      request(data.nodes);
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
  request(matrices.nodes.buffer, matrices.edges.buffer);
  return stop;
}

type Settle = {
  /** Holds the picture's frame still while the user looks around. */
  hold(): void;
  /** Lets a held frame take in the whole layout again. */
  follow(): void;
  /** Where a memory is headed, in camera coordinates. */
  destination(id: string): { x: number; y: number } | undefined;
  stop(): void;
};

/**
 * Settles the graph in view (B250). Every frame eases each memory toward
 * the layout's latest positions, and the picture's frame (sigma's bounding
 * box) toward the box around them, so the drawn graph starts from rest,
 * glides and slows into place however the worker's batches arrive. Once the
 * layout is done and nothing is visibly moving, the memories land exactly on
 * it, `onRest` runs and the frame loop stops. While the user looks around,
 * the frame holds still and the memories keep settling.
 */
function settleInView(
  graph: Graph,
  renderer: SigmaLike,
  matrices: Matrices,
  onRest: (laidOut: boolean) => void,
): Settle {
  const drawn: number[] = [];
  graph.forEachNode((_key, { x, y }) => drawn.push(x, y));
  const start = positions(matrices.nodes);
  const memories = new Glide(drawn, start);
  const frame = new Glide(bounds(start));
  let laidOut: boolean | null = null;
  let rested = false;
  let held = false;
  let finished = false;
  let request = 0;
  let last: number | null = null;
  const showFrame = () => {
    const [left, top, right, bottom] = frame.at;
    renderer.setCustomBBox({ x: [left, right], y: [top, bottom] });
  };
  // Positions are written in place: sigma reads them afresh whenever it
  // processes the graph, while graph events would also rerun every reducer.
  const draw = () => {
    let index = 0;
    graph.forEachNode((_key, attributes) => {
      attributes.x = memories.at[index++];
      attributes.y = memories.at[index++];
    });
  };
  const repaint = (schedule = true) =>
    renderer.refresh({
      partialGraph: { nodes: [] },
      skipIndexation: false,
      schedule,
    });
  const tick = (now: number) => {
    request = 0;
    const elapsed = last === null ? 0 : now - last;
    last = now;
    const [left, top, right, bottom] = frame.at;
    // Closer than this is about a pixel at the current zoom.
    const near =
      Math.max(right - left, bottom - top) *
      AT_REST *
      Math.min(1, renderer.getCamera().getState().ratio);
    if (!rested) {
      memories.step(elapsed);
      const landing = laidOut !== null && memories.gap() <= near;
      if (landing) memories.land();
      draw();
      if (landing) {
        rested = true;
        onRest(laidOut === true);
      }
    }
    if (!held) {
      frame.step(elapsed);
      if (rested && frame.gap() <= near) frame.land();
      if (rested && frame.gap() === 0) {
        // The frame is now the box sigma draws anyway: hand it back.
        renderer.setCustomBBox(null);
        finished = true;
        repaint();
        return;
      }
      showFrame();
    }
    repaint();
    if (!rested || !held) request = requestAnimationFrame(tick);
  };
  const wake = () => {
    if (request) return;
    last = null;
    request = requestAnimationFrame(tick);
  };
  const stopLayout = runLayout(
    matrices,
    true,
    (nodes) => {
      const target = positions(nodes);
      memories.aim(target);
      frame.aim(bounds(target));
    },
    (ran) => {
      laidOut = ran;
    },
  );
  // Framed before the first paint: sigma drew its own box on creation.
  showFrame();
  repaint(false);
  wake();
  let order: Map<string, number> | undefined;
  return {
    hold: () => {
      if (!finished) held = true;
    },
    follow: () => {
      if (finished || !held) return;
      held = false;
      wake();
    },
    destination: (id) => {
      if (finished) return undefined;
      order ??= new Map(graph.nodes().map((key, index) => [key, index]));
      const index = order.get(id);
      if (index === undefined) return undefined;
      const [left, top, right, bottom] = frame.at;
      const size = Math.max(right - left, bottom - top) || 1;
      return {
        x: 0.5 + (memories.target[index * 2] - (left + right) / 2) / size,
        y: 0.5 + (memories.target[index * 2 + 1] - (top + bottom) / 2) / size,
      };
    },
    stop: () => {
      cancelAnimationFrame(request);
      request = 0;
      stopLayout();
    },
  };
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
  /** The graph settling in view (inert once it is done). */
  const settle = useRef<Settle | null>(null);
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

  // Navigating while the graph settles holds its frame still, so the view
  // never shifts under the user; Fit takes in the whole layout again.
  useImperativeHandle(forwardedRef, () => ({
    fit: () => {
      settle.current?.follow();
      const camera = sigma.current?.getCamera();
      if (!camera) return;
      if (reducedMotion()) camera.setState({ x: 0.5, y: 0.5, ratio: 1 });
      else void camera.animatedReset({ duration: 320 });
    },
    zoom: (delta) => {
      settle.current?.hold();
      const camera = sigma.current?.getCamera();
      if (!camera) return;
      const { ratio } = camera.getState();
      moveTo({
        ratio: Math.min(4, Math.max(0.06, ratio / (1 + delta * 2.5))),
      });
    },
    focus: (id) => {
      settle.current?.hold();
      const data =
        settle.current?.destination(id) ??
        sigma.current?.getNodeDisplayData(id);
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
    let settling: Settle | undefined;
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
        const rest = (laidOut: boolean) => {
          if (laidOut)
            graph.forEachNode((key, { x, y }) =>
              restingPlaces.set(key, { x, y }),
            );
          if (active) setLayout('settled');
        };
        // The graph is drawn at once and settles in view; with reduced
        // motion the layout is placed before the first frame.
        const unplaced =
          graph.order > 1 && nodes.some((node) => !restingPlaces.has(node.id));
        const animate = unplaced && !reducedMotion();
        const matrices = unplaced
          ? helpers.graphToByteArrays(graph, () => 1)
          : undefined;
        if (!matrices) setLayout('settled');
        else if (!animate) {
          const laidOut = await new Promise<boolean>((resolve) => {
            stopLayout = runLayout(
              matrices,
              false,
              (laid) => helpers.assignLayoutChanges(graph, laid, null),
              resolve,
            );
          });
          if (!active) return;
          rest(laidOut);
        } else
          graph.updateEachNodeAttributes(
            (key, attributes) =>
              restingPlaces.has(key)
                ? attributes
                : {
                    ...attributes,
                    x: attributes.x * BLOOM,
                    y: attributes.y * BLOOM,
                  },
            { attributes: ['x', 'y'] },
          );
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
                forceLabel:
                  Boolean(data.user) || visibleRef.current.size <= LABEL_EVERY,
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
        // A press (drag, click, pinch) or the wheel holds the frame still.
        for (const event of [
          'downNode',
          'downStage',
          'wheelNode',
          'wheelStage',
        ])
          instance.on(event, () => settle.current?.hold());
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
        if (animate && matrices) {
          settling = settleInView(graph, instance, matrices, rest);
          settle.current = settling;
        }
      })
      .catch(() => report('failed'));
    return () => {
      active = false;
      // Leaving mid-settle keeps nothing: next time it settles again.
      stopLayout?.();
      settling?.stop();
      if (settle.current === settling) settle.current = null;
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
