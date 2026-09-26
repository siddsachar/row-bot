import { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowDown, ArrowUp } from 'lucide-react';
import type { KnowledgeGraphNode } from './KnowledgeHome';
import { typeSlot } from './knowledge-palette';
import { When } from './home-format';
import { humanizeToken } from '../../ui/format';

type SortKey =
  'subject' | 'entity_type' | 'source' | 'relation_count' | 'updated_at';
const ROW_HEIGHT = 36;
const OVERSCAN = 10;

const SOURCES: Record<string, string> = {
  manual: 'Saved by you',
  extraction: 'From conversations',
  document: 'From documents',
  wiki: 'Wiki and Dream Cycle',
  other: 'Other',
};

export function sourceWords(source: string) {
  return SOURCES[source] ?? humanizeToken(source);
}

const COLUMNS: { key: SortKey; label: string; numeric?: boolean }[] = [
  { key: 'subject', label: 'Memory' },
  { key: 'entity_type', label: 'Type' },
  { key: 'source', label: 'Source' },
  { key: 'relation_count', label: 'Links', numeric: true },
  { key: 'updated_at', label: 'Updated' },
];

/**
 * Every visible memory as a dense, sortable table. Only the rows in view are
 * rendered; the spacer rows keep the scroll height and row positions honest.
 */
export default function KnowledgeList({
  nodes,
  selectedId,
  onSelect,
}: {
  nodes: readonly KnowledgeGraphNode[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  const [sort, setSort] = useState<{ key: SortKey; direction: 1 | -1 }>({
    key: 'relation_count',
    direction: -1,
  });
  const scroller = useRef<HTMLDivElement>(null);
  const [view, setView] = useState({ top: 0, height: 600 });
  const rows = useMemo(() => {
    const sorted = [...nodes];
    sorted.sort((left, right) => {
      const a = left[sort.key];
      const b = right[sort.key];
      const order =
        typeof a === 'number' && typeof b === 'number'
          ? a - b
          : String(a).localeCompare(String(b), undefined, {
              sensitivity: 'base',
            });
      return order * sort.direction || left.id.localeCompare(right.id);
    });
    return sorted;
  }, [nodes, sort]);

  useEffect(() => {
    const element = scroller.current;
    if (!element) return;
    const measure = () =>
      setView({ top: element.scrollTop, height: element.clientHeight || 600 });
    measure();
    const observer =
      typeof ResizeObserver === 'undefined'
        ? undefined
        : new ResizeObserver(measure);
    observer?.observe(element);
    return () => observer?.disconnect();
  }, []);

  // Bring a memory chosen elsewhere (search, inspector links) into view.
  useEffect(() => {
    const element = scroller.current;
    if (!element || !selectedId) return;
    const index = rows.findIndex((row) => row.id === selectedId);
    if (index < 0) return;
    const top = index * ROW_HEIGHT;
    if (
      top < element.scrollTop ||
      top > element.scrollTop + element.clientHeight - ROW_HEIGHT * 2
    )
      element.scrollTop = Math.max(0, top - element.clientHeight / 3);
  }, [rows, selectedId]);

  const first = Math.max(0, Math.floor(view.top / ROW_HEIGHT) - OVERSCAN);
  const last = Math.min(
    rows.length,
    Math.ceil((view.top + view.height) / ROW_HEIGHT) + OVERSCAN,
  );
  const slice = rows.slice(first, last);
  return (
    <div
      ref={scroller}
      className="knowledge-list"
      tabIndex={0}
      aria-label="Memory list"
      onScroll={(event) =>
        setView({
          top: event.currentTarget.scrollTop,
          height: event.currentTarget.clientHeight || 600,
        })
      }
    >
      <table aria-label="Knowledge entities" aria-rowcount={rows.length + 1}>
        <thead>
          <tr aria-rowindex={1}>
            {COLUMNS.map((column) => {
              const active = sort.key === column.key;
              return (
                <th
                  key={column.key}
                  scope="col"
                  data-numeric={column.numeric ? 'true' : undefined}
                  aria-sort={
                    active
                      ? sort.direction === 1
                        ? 'ascending'
                        : 'descending'
                      : 'none'
                  }
                >
                  <button
                    type="button"
                    onClick={() =>
                      setSort((current) => ({
                        key: column.key,
                        direction:
                          current.key === column.key
                            ? (-current.direction as 1 | -1)
                            : column.numeric || column.key === 'updated_at'
                              ? -1
                              : 1,
                      }))
                    }
                  >
                    {column.label}
                    {active &&
                      (sort.direction === 1 ? (
                        <ArrowUp size={12} aria-hidden />
                      ) : (
                        <ArrowDown size={12} aria-hidden />
                      ))}
                  </button>
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {first > 0 && (
            <tr aria-hidden className="knowledge-list-spacer">
              <td colSpan={5} style={{ height: first * ROW_HEIGHT }} />
            </tr>
          )}
          {slice.map((node, index) => (
            <tr
              key={node.id}
              aria-rowindex={first + index + 2}
              data-selected={node.id === selectedId ? 'true' : undefined}
              onClick={() => onSelect(node.id)}
            >
              <td>
                <button
                  type="button"
                  className="knowledge-list-open"
                  aria-current={node.id === selectedId ? 'true' : undefined}
                  onClick={(event) => {
                    event.stopPropagation();
                    onSelect(node.id);
                  }}
                >
                  {node.subject}
                </button>
              </td>
              <td>
                <span className="knowledge-type">
                  <span
                    className="knowledge-type-dot"
                    data-slot={typeSlot(node.entity_type)}
                    aria-hidden
                  />
                  {humanizeToken(node.entity_type)}
                </span>
              </td>
              <td>{sourceWords(node.source)}</td>
              <td data-numeric="true">{node.relation_count}</td>
              <td>
                <When value={node.updated_at} fallback="" />
              </td>
            </tr>
          ))}
          {last < rows.length && (
            <tr aria-hidden className="knowledge-list-spacer">
              <td
                colSpan={5}
                style={{ height: (rows.length - last) * ROW_HEIGHT }}
              />
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
