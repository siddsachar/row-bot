import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from 'react';
import { loadLocalRuntimeScript } from '../../ui/local-runtime';
import type { KnowledgeGraphEdge, KnowledgeGraphNode } from './KnowledgeHome';

type VisNetwork = {
  destroy(): void;
  fit(options?: Record<string, unknown>): void;
  getScale(): number;
  moveTo(options: Record<string, unknown>): void;
  on(name: string, callback: (value: { nodes?: string[] }) => void): void;
  redraw(): void;
  selectNodes(ids: string[]): void;
};

declare global {
  interface Window {
    vis?: {
      DataSet: new (items: unknown[]) => unknown;
      Network: new (
        root: HTMLElement,
        data: Record<string, unknown>,
        options: Record<string, unknown>,
      ) => VisNetwork;
    };
  }
}

export type KnowledgeGraphHandle = {
  fit(): void;
  zoom(delta: number): void;
};

const TYPE_COLORS: Record<string, string> = {
  person: '#4fc3f7',
  preference: '#ce93d8',
  fact: '#81c784',
  event: '#ffb74d',
  place: '#4dd0e1',
  project: '#7986cb',
  organization: '#f06292',
  user: '#ffd54f',
};

function hashColor(value: string) {
  let hash = 0;
  for (const character of value)
    hash = (hash * 31 + character.charCodeAt(0)) | 0;
  const palette = [
    '#90caf9',
    '#a5d6a7',
    '#ffcc80',
    '#b39ddb',
    '#80cbc4',
    '#ef9a9a',
  ];
  return palette[Math.abs(hash) % palette.length];
}

function colorForType(type: string) {
  return TYPE_COLORS[type.toLowerCase()] ?? hashColor(type);
}

function shapeFor(node: KnowledgeGraphNode) {
  if (node.is_user) return 'diamond';
  return (
    {
      person: 'dot',
      preference: 'triangle',
      fact: 'ellipse',
      event: 'hexagon',
      place: 'box',
      project: 'star',
      organization: 'database',
    }[node.entity_type.toLowerCase()] ?? 'dot'
  );
}

const KnowledgeGraphCanvas = forwardRef<
  KnowledgeGraphHandle,
  {
    nodes: KnowledgeGraphNode[];
    edges: KnowledgeGraphEdge[];
    centerId: string | null;
    selectedId: string | null;
    onSelect: (id: string) => void;
  }
>(function KnowledgeGraphCanvas(
  { nodes, edges, centerId, selectedId, onSelect },
  forwardedRef,
) {
  const root = useRef<HTMLDivElement>(null);
  const network = useRef<VisNetwork | null>(null);
  const selectCallback = useRef(onSelect);
  const selectedIdRef = useRef(selectedId);
  const [status, setStatus] = useState<'loading' | 'ready' | 'failed'>(
    'loading',
  );
  selectCallback.current = onSelect;
  selectedIdRef.current = selectedId;

  useImperativeHandle(forwardedRef, () => ({
    fit: () => network.current?.fit({ animation: false }),
    zoom: (delta) => {
      const current = network.current;
      if (!current) return;
      const scale = Math.min(2.5, Math.max(0.25, current.getScale() + delta));
      current.moveTo({ scale, animation: false });
    },
  }));

  useEffect(() => {
    let active = true;
    let observer: ResizeObserver | undefined;
    const element = root.current;
    setStatus('loading');
    void loadLocalRuntimeScript('vis-network.min.js', () => Boolean(window.vis))
      .then(() => {
        if (!active || !element || !window.vis) return;
        const reduced = window.matchMedia?.(
          '(prefers-reduced-motion: reduce)',
        ).matches;
        const data = {
          nodes: new window.vis.DataSet(
            nodes.map((node) => ({
              id: node.id,
              label: node.subject,
              title: `${node.subject} · ${node.entity_type} · ${node.source}`,
              shape: shapeFor(node),
              color: {
                background: colorForType(node.entity_type),
                border: node.source.toLowerCase().startsWith('document')
                  ? '#ab47bc'
                  : '#607d8b',
                highlight: { border: '#ffd54f' },
              },
              borderWidth: node.is_user ? 4 : 2,
              value: Math.max(1, node.relation_count + 1),
              ...(reduced
                ? {
                    x:
                      node.id === centerId
                        ? 0
                        : Math.cos(nodes.indexOf(node)) * 220,
                    y:
                      node.id === centerId
                        ? 0
                        : Math.sin(nodes.indexOf(node)) * 150,
                    fixed: true,
                  }
                : {}),
            })),
          ),
          edges: new window.vis.DataSet(
            edges.map((edge) => ({
              id: edge.id,
              from: edge.source_id,
              to: edge.target_id,
              label: edge.relation_type,
              title: edge.relation_type,
              arrows: 'to',
              color: hashColor(edge.relation_type),
            })),
          ),
        };
        const instance = new window.vis.Network(element, data, {
          autoResize: true,
          physics: reduced
            ? false
            : {
                solver: 'forceAtlas2Based',
                stabilization: { iterations: 150, fit: true },
                forceAtlas2Based: {
                  gravitationalConstant: -40,
                  centralGravity: 0.005,
                  springLength: 120,
                  springConstant: 0.06,
                  damping: 0.4,
                },
              },
          interaction: {
            hover: true,
            keyboard: { enabled: true, bindToWindow: false },
            multiselect: false,
            navigationButtons: false,
          },
          nodes: {
            font: { color: '#dce5ee', size: 13 },
            scaling: { min: 14, max: 30 },
          },
          edges: {
            smooth: reduced ? false : { type: 'continuous' },
            font: { color: '#a5b3c2', strokeWidth: 4 },
          },
        });
        network.current = instance;
        instance.on('selectNode', ({ nodes: selected = [] }) => {
          if (selected[0]) selectCallback.current(selected[0]);
        });
        if (selectedIdRef.current)
          instance.selectNodes([selectedIdRef.current]);
        observer =
          typeof ResizeObserver === 'undefined'
            ? undefined
            : new ResizeObserver(() => instance.redraw());
        observer?.observe(element);
        setStatus('ready');
      })
      .catch(() => active && setStatus('failed'));
    return () => {
      active = false;
      observer?.disconnect();
      network.current?.destroy();
      network.current = null;
      element?.replaceChildren();
    };
  }, [centerId, edges, nodes]);

  useEffect(() => {
    if (network.current)
      network.current.selectNodes(selectedId ? [selectedId] : []);
  }, [selectedId]);

  return (
    <div className="knowledge-network-shell" data-renderer-status={status}>
      <div
        ref={root}
        className="knowledge-network-canvas"
        role="img"
        aria-label={`Interactive knowledge graph with ${nodes.length} memories and ${edges.length} connections`}
        aria-hidden={status !== 'ready'}
      />
      {status === 'loading' && <p role="status">Loading graph renderer…</p>}
      {status === 'failed' && (
        <p role="alert">Interactive graph unavailable. Use the list below.</p>
      )}
      <ul
        className={`knowledge-graph-semantic${status === 'ready' ? ' visually-hidden' : ''}`}
        aria-label="Knowledge graph semantic fallback"
      >
        {nodes.map((node) => (
          <li key={node.id}>
            <button
              type="button"
              className={`knowledge-graph-node${node.id === selectedId ? ' selected' : ''}`}
              aria-label={`${node.subject}, ${node.entity_type}, ${node.relation_count} connections`}
              aria-current={node.id === selectedId ? 'true' : undefined}
              onClick={() => onSelect(node.id)}
            >
              {node.subject} · {node.entity_type} · {node.source}
            </button>
          </li>
        ))}
      </ul>
      <div className="visually-hidden" aria-label="Knowledge graph relations">
        {edges.map((edge) => (
          <span className="knowledge-graph-edge" key={edge.id}>
            {edge.source_id} {edge.relation_type} {edge.target_id}
          </span>
        ))}
      </div>
    </div>
  );
});

export default KnowledgeGraphCanvas;
