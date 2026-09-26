import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from 'react';
import type { KnowledgeGraphEdge, KnowledgeGraphNode } from './KnowledgeHome';
import { plural } from './home-format';
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

  useImperativeHandle(forwardedRef, () => ({
    fit: () => {
      const camera = sigma.current?.getCamera();
      if (!camera) return;
      if (reducedMotion()) camera.setState({ x: 0.5, y: 0.5, ratio: 1 });
      else void camera.animatedReset({ duration: 320 });
    },
    zoom: (delta) => {
      const camera = sigma.current?.getCamera();
      if (!camera) return;
      const { ratio } = camera.getState();
      moveTo({
        ratio: Math.min(4, Math.max(0.06, ratio / (1 + delta * 2.5))),
      });
    },
    focus: (id) => {
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
    const element = root.current;
    const report = (next: GraphStatus) => {
      if (!active) return;
      setStatus(next);
      statusCallback.current?.(next);
    };
    report('loading');
    if (!element || !webglAvailable()) {
      report('failed');
      return () => {
        active = false;
      };
    }
    void Promise.all([
      import('graphology'),
      import('sigma'),
      import('graphology-layout-forceatlas2'),
    ])
      .then(([graphology, sigmaModule, layout]) => {
        if (!active) return;
        const Graph = graphology.default;
        const Sigma = sigmaModule.default;
        const forceAtlas2 = layout.default;
        const graph = new Graph({ multi: true, allowSelfLoops: false });
        for (const node of nodes) {
          const value = seed(node.id);
          const angle = ((value % 3600) / 3600) * Math.PI * 2;
          const radius = Math.sqrt(((value >>> 12) % 1000) / 1000) * 100;
          graph.addNode(node.id, {
            x: Math.cos(angle) * radius,
            y: Math.sin(angle) * radius,
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
        if (graph.order > 1) {
          const settings = forceAtlas2.inferSettings(graph);
          forceAtlas2.assign(graph, {
            iterations: graph.order > 400 ? 140 : 220,
            settings: {
              ...settings,
              gravity: 1.2,
              strongGravityMode: true,
              scalingRatio: 6,
              slowDown: 3,
              barnesHutOptimize: graph.order > 200,
            },
          });
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
      })
      .catch(() => report('failed'));
    return () => {
      active = false;
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
    <div className="knowledge-network-shell" data-renderer-status={status}>
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
