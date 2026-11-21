import { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowDown, ArrowUp, Laptop, Plus, Search, Trash2 } from 'lucide-react';
import type { KnowledgeGraphNode } from './KnowledgeHome';
import { typeSlot } from './knowledge-palette';
import { When, plural } from './home-format';
import { humanizeToken } from '../../ui/format';
import {
  Button,
  IconButton,
  Input,
  Segmented,
  type SegmentedOption,
} from '../../ui/primitives';
import '../../ui/styles/phone-chats.css';
import '../../ui/styles/phone-memory.css';

type SortKey =
  'subject' | 'entity_type' | 'source' | 'relation_count' | 'updated_at';
const ROW_HEIGHT = 36;
const OVERSCAN = 10;

const SOURCES: Record<string, string> = {
  manual: 'Saved by you',
  extraction: 'From conversations',
  document: 'From documents',
  wiki: 'Wiki and Dream Cycle',
  import: 'Imported from other assistants',
  other: 'Other',
};

/** Where a memory came from; an imported one names its app ("Imported from ChatGPT"). */
export function sourceWords(source: string, label = '') {
  if (source === 'import' && label) return `Imported from ${label}`;
  return SOURCES[source] ?? humanizeToken(source);
}

/** A phone's type chips name the kind in the plural ("People"). */
const TYPE_CHIPS: Record<string, string> = {
  person: 'People',
  project: 'Projects',
  preference: 'Preferences',
  fact: 'Facts',
  organisation: 'Organisations',
  organization: 'Organizations',
  place: 'Places',
  location: 'Locations',
  event: 'Events',
  concept: 'Concepts',
  skill: 'Skills',
};

/** "" for every memory, "type:<kind>" for one kind, "source:import" for imported ones. */
type PhoneFilter = string;

/**
 * Memory on a phone (D12 A): a searchable list. Search on top, a labelled
 * chip per kind (and Imported, when there are imported memories), and one
 * row per memory: a tap opens it in the editor, the bin deletes it after the
 * usual confirmation. The graph, import and clean-up stay on the computer.
 */
export function PhoneMemoryList({
  nodes,
  total,
  sources,
  onOpen,
  onDelete,
  onAdd,
  onShowAll,
}: {
  nodes: readonly KnowledgeGraphNode[];
  /** Every saved memory, also those not loaded. */
  total: number;
  /** The source buckets saved memories come from. */
  sources: readonly string[];
  onOpen: (id: string) => void;
  onDelete?: (id: string, subject: string) => void;
  onAdd?: () => void;
  /** Load the memories not loaded yet (only some are). */
  onShowAll?: () => void;
}) {
  const [query, setQuery] = useState('');
  const [chosen, setChosen] = useState<PhoneFilter>('');
  const chips = useMemo<SegmentedOption<PhoneFilter>[]>(() => {
    const counts = new Map<string, number>();
    for (const node of nodes)
      counts.set(node.entity_type, (counts.get(node.entity_type) ?? 0) + 1);
    const kinds = [...counts]
      .map(([kind, count]) => ({
        kind,
        count,
        label: TYPE_CHIPS[kind] ?? humanizeToken(kind),
      }))
      .sort(
        (left, right) =>
          right.count - left.count || left.label.localeCompare(right.label),
      );
    return [
      { value: '', label: 'All' },
      ...kinds.map(({ kind, label }) => ({ value: `type:${kind}`, label })),
      ...(sources.includes('import')
        ? [{ value: 'source:import', label: 'Imported' }]
        : []),
    ];
  }, [nodes, sources]);
  // A chip whose last memory was forgotten falls back to All.
  const filter = chips.some((chip) => chip.value === chosen) ? chosen : '';
  const needle = query.trim().toLocaleLowerCase();
  const rows = useMemo(
    () =>
      nodes
        .filter((node) => {
          if (filter === 'source:import' && node.source !== 'import')
            return false;
          if (
            filter.startsWith('type:') &&
            node.entity_type !== filter.slice(5)
          )
            return false;
          return (
            !needle ||
            [
              node.subject,
              node.description,
              humanizeToken(node.entity_type),
              node.source_label ?? '',
            ]
              .join(' ')
              .toLocaleLowerCase()
              .includes(needle)
          );
        })
        .sort(
          (left, right) =>
            right.updated_at.localeCompare(left.updated_at) ||
            left.subject.localeCompare(right.subject),
        ),
    [filter, needle, nodes],
  );
  if (!total)
    return (
      <div className="phone-memory-empty">
        <strong>No memories yet</strong>
        <p>Row-Bot remembers what it learns about you as you chat.</p>
        {onAdd && <Button onClick={onAdd}>Add memory</Button>}
      </div>
    );
  return (
    <>
      <div className="phone-memory-toolbar">
        <form
          className="library-search phone-memory-search"
          role="search"
          onSubmit={(event) => event.preventDefault()}
        >
          <Search size={18} aria-hidden />
          <Input
            type="search"
            aria-label="Search memories"
            placeholder={`Search ${plural(total, 'memory', 'memories')}`}
            value={query}
            maxLength={200}
            onChange={(event) => setQuery(event.target.value)}
          />
        </form>
        {onAdd && (
          <IconButton
            label="Add memory"
            variant="secondary"
            className="phone-memory-add"
            onClick={onAdd}
          >
            <Plus size={20} aria-hidden />
          </IconButton>
        )}
      </div>
      <Segmented
        size="sm"
        className="phone-chips"
        label="Memory type"
        value={filter}
        onChange={setChosen}
        options={chips}
      />
      <p className="visually-hidden" role="status">
        {needle || filter ? plural(rows.length, 'memory', 'memories') : ''}
      </p>
      {rows.length > 0 ? (
        <ul className="phone-chat-list-rows" aria-label="Memories">
          {rows.map((node) => (
            <li key={node.id} className="phone-row phone-memory-row">
              <button
                type="button"
                className="phone-row-main"
                onClick={() => onOpen(node.id)}
              >
                <span className="phone-row-text">
                  <span className="phone-row-title">{node.subject}</span>
                  <span className="phone-row-meta">
                    {[
                      humanizeToken(node.entity_type),
                      node.status === 'active'
                        ? ''
                        : humanizeToken(node.status),
                      node.source === 'import'
                        ? sourceWords(node.source, node.source_label)
                        : '',
                      node.description,
                    ]
                      .filter(Boolean)
                      .join(' · ')}
                  </span>
                </span>
              </button>
              {onDelete && (
                <IconButton
                  label={`Delete ${node.subject}`}
                  className="phone-memory-delete"
                  onClick={() => onDelete(node.id, node.subject)}
                >
                  <Trash2 size={18} aria-hidden />
                </IconButton>
              )}
            </li>
          ))}
        </ul>
      ) : (
        <p className="phone-memory-none">
          {needle
            ? `No memory matches “${query.trim()}”.`
            : 'No memories of this kind.'}
        </p>
      )}
      {onShowAll && nodes.length < total && (
        <p className="phone-memory-note">
          Showing {nodes.length.toLocaleString()} of {total.toLocaleString()}.{' '}
          <button
            type="button"
            className="knowledge-caption-action"
            onClick={onShowAll}
          >
            Show all
          </button>
        </p>
      )}
      <p className="phone-memory-note">
        <Laptop size={16} aria-hidden />
        <span>The graph, import and clean-up are on your computer.</span>
      </p>
    </>
  );
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
 * With `checked`, each row has a tick box for bulk actions (B264).
 */
export default function KnowledgeList({
  nodes,
  selectedId,
  onSelect,
  checked,
  onCheck,
  checkFull = false,
}: {
  nodes: readonly KnowledgeGraphNode[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  checked?: ReadonlySet<string>;
  onCheck?: (id: string) => void;
  /** The bulk limit is reached: only ticked rows can change. */
  checkFull?: boolean;
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
                <span className="knowledge-list-memory">
                  {checked && onCheck && (
                    // The label is the larger touch target.
                    <label
                      className="knowledge-list-check"
                      onClick={(event) => event.stopPropagation()}
                    >
                      <input
                        type="checkbox"
                        aria-label={`Select ${node.subject}`}
                        checked={checked.has(node.id)}
                        disabled={checkFull && !checked.has(node.id)}
                        onChange={() => onCheck(node.id)}
                      />
                    </label>
                  )}
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
                </span>
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
              <td>{sourceWords(node.source, node.source_label)}</td>
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
