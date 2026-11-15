import {
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import {
  ArrowRightLeft,
  Check,
  CheckCheck,
  FileUp,
  GitMerge,
  Pencil,
  Plus,
  Undo2,
  X,
} from 'lucide-react';
import type {
  MemoryImportCandidate,
  MemoryImportPreviewItem,
  MemoryImportReceipt,
  MemoryImportSource,
} from '../../api/types';
import { copyText } from '../../platform/clipboard';
import {
  CopyGlyph,
  IconButton,
  Input,
  Select,
  useCopyFeedback,
} from '../../ui/primitives';
import { typeSlot } from '../home/knowledge-palette';
import {
  CATEGORIES,
  categoryWords,
  formatSize,
  receiptWords,
  shortDate,
  type Category,
} from './memory-import-io';

/** Only the rows in view are drawn; spacers keep the scroll height and positions honest. */
export function VirtualList<T>({
  items,
  rowHeight,
  label,
  render,
  rowKey,
  className = '',
}: {
  items: readonly T[];
  rowHeight: number;
  label: string;
  render: (item: T) => ReactNode;
  rowKey: (item: T) => string;
  className?: string;
}) {
  const scroller = useRef<HTMLDivElement>(null);
  const [view, setView] = useState({ top: 0, height: 480 });
  useEffect(() => {
    const element = scroller.current;
    if (!element) return;
    const measure = () =>
      setView({ top: element.scrollTop, height: element.clientHeight || 480 });
    measure();
    if (typeof ResizeObserver !== 'function') return;
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  const overscan = 6;
  const first = Math.max(0, Math.floor(view.top / rowHeight) - overscan);
  const last = Math.min(
    items.length,
    Math.ceil((view.top + view.height) / rowHeight) + overscan,
  );
  return (
    <div
      ref={scroller}
      className={`memory-import-scroller ${className}`}
      tabIndex={0}
      aria-label={label}
      role="region"
      onScroll={(event) =>
        setView({
          top: event.currentTarget.scrollTop,
          height: event.currentTarget.clientHeight || 480,
        })
      }
    >
      <ul
        className="memory-import-rows"
        aria-label={label}
        style={{ height: items.length * rowHeight }}
      >
        {items.slice(first, last).map((item, offset) => (
          <li
            key={rowKey(item)}
            className="memory-import-row"
            aria-setsize={items.length}
            aria-posinset={first + offset + 1}
            style={{ top: (first + offset) * rowHeight, height: rowHeight }}
          >
            {render(item)}
          </li>
        ))}
      </ul>
    </div>
  );
}

export function CategoryChip({ category }: { category: string }) {
  return (
    <span className="memory-import-chip">
      <span
        className="knowledge-type-dot"
        data-slot={typeSlot(category)}
        aria-hidden
      />
      {categoryWords(category)}
    </span>
  );
}

/** The sources as a grid of choices; each says in one line how to get its file. */
export function SourceGrid({
  sources,
  value,
  onChange,
}: {
  sources: readonly MemoryImportSource[];
  value: string;
  onChange: (id: string) => void;
}) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const current = Math.max(
    0,
    sources.findIndex((source) => source.id === value),
  );
  const move = (index: number) => {
    const next = (index + sources.length) % sources.length;
    refs.current[next]?.focus();
    onChange(sources[next].id);
  };
  return (
    <div
      className="memory-import-sources"
      role="radiogroup"
      aria-label="Where from"
    >
      {sources.map((source, index) => (
        <button
          key={source.id}
          ref={(element) => {
            refs.current[index] = element;
          }}
          type="button"
          role="radio"
          aria-checked={source.id === value}
          tabIndex={index === current ? 0 : -1}
          className="memory-import-source"
          onClick={() => onChange(source.id)}
          onKeyDown={(event) => {
            const step =
              event.key === 'ArrowRight' || event.key === 'ArrowDown'
                ? 1
                : event.key === 'ArrowLeft' || event.key === 'ArrowUp'
                  ? -1
                  : 0;
            if (!step) return;
            event.preventDefault();
            move(index + step);
          }}
        >
          <span className="memory-import-source-name">
            {sourceName(source)}
          </span>
          <span className="memory-import-source-kind">
            {source.export === 'memories'
              ? 'Export file'
              : source.export === 'history'
                ? source.prompt_route
                  ? 'Prompt and chats'
                  : 'Chats export'
                : 'Prompt'}
          </span>
        </button>
      ))}
    </div>
  );
}

export function sourceName(source: MemoryImportSource) {
  return source.id === 'other' ? 'Another app' : source.name;
}

/** The prompt to paste into the old assistant, with a copy icon. */
export function PromptCard({
  prompt,
  source,
}: {
  prompt: string;
  source: string;
}) {
  const [copied, setCopied] = useCopyFeedback();
  const [failed, setFailed] = useState(false);
  return (
    <div className="memory-import-prompt">
      <div className="memory-import-prompt-head">
        <strong>1. Paste this prompt into {source}</strong>
        <IconButton
          size="sm"
          label={copied ? 'Copied' : 'Copy prompt'}
          onClick={() =>
            void copyText(prompt).then((done) => {
              setFailed(!done);
              if (done) setCopied(true);
            })
          }
        >
          <CopyGlyph copied={copied} />
        </IconButton>
      </div>
      <pre className="memory-import-prompt-text" tabIndex={0}>
        {prompt}
      </pre>
      {failed && (
        <small role="alert">
          Copying isn&apos;t available here. Select the prompt and copy it.
        </small>
      )}
    </div>
  );
}

export type PickedFile = {
  key: string;
  file: File;
  sent: number;
  state: 'waiting' | 'sending' | 'sent' | 'failed';
  /** The server's id once it has the whole file. */
  fileId?: string;
};

/** A drop zone and file picker for export files (copied from Documents' drop zone). */
export function FileDrop({
  files,
  disabled,
  label,
  maxBytes,
  onAdd,
  onRemove,
}: {
  files: readonly PickedFile[];
  disabled: boolean;
  label: string;
  maxBytes: number;
  onAdd: (files: File[]) => void;
  onRemove: (key: string) => void;
}) {
  const inputId = useId();
  const [dragging, setDragging] = useState(false);
  return (
    <div className="memory-import-files">
      <div
        className="memory-import-drop"
        data-dragging={dragging ? 'true' : undefined}
        data-disabled={disabled ? 'true' : undefined}
        onDragEnter={(event) => {
          if (disabled || !event.dataTransfer.types.includes('Files')) return;
          event.preventDefault();
          setDragging(true);
        }}
        onDragOver={(event) => {
          if (disabled || !event.dataTransfer.types.includes('Files')) return;
          event.preventDefault();
          event.dataTransfer.dropEffect = 'copy';
        }}
        onDragLeave={(event) => {
          if (!event.currentTarget.contains(event.relatedTarget as Node))
            setDragging(false);
        }}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          if (!disabled) onAdd(Array.from(event.dataTransfer.files));
        }}
      >
        <FileUp size={18} aria-hidden className="memory-import-drop-icon" />
        <div className="memory-import-drop-text">
          <strong>{label}</strong>
          <small>
            Zip, JSON, CSV or text · every part of a split export · up to{' '}
            {formatSize(maxBytes)} each
          </small>
        </div>
        <label htmlFor={inputId} className="button secondary">
          Choose files…
        </label>
        <input
          id={inputId}
          aria-label="Choose export files"
          className="memory-import-drop-input"
          type="file"
          multiple
          accept=".zip,.json,.jsonl,.csv,.txt,.md,.af,.gz"
          disabled={disabled}
          onChange={(event) => {
            onAdd(Array.from(event.target.files ?? []));
            event.target.value = '';
          }}
        />
      </div>
      {files.length > 0 && (
        <ul className="memory-import-file-list" aria-label="Files to import">
          {files.map((item) => (
            <li key={item.key}>
              <span className="memory-import-file-name">{item.file.name}</span>
              <span className="memory-import-file-meta">
                {item.state === 'sending'
                  ? `${formatSize(item.sent)} of ${formatSize(item.file.size)}`
                  : item.state === 'failed'
                    ? 'Not sent'
                    : formatSize(item.file.size)}
              </span>
              <IconButton
                size="sm"
                label={`Remove ${item.file.name}`}
                disabled={disabled}
                onClick={() => onRemove(item.key)}
              >
                <X size={14} aria-hidden />
              </IconButton>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export type Edit = { text?: string; category?: Category };

/** One found memory: tick, type, text (editable), and where it came from. */
export function CandidateRow({
  item,
  edit,
  checked,
  onCheck,
  onEdit,
}: {
  item: MemoryImportCandidate;
  edit: Edit | undefined;
  checked: boolean;
  onCheck: (checked: boolean) => void;
  onEdit: (edit: Edit | undefined) => void;
}) {
  const [editing, setEditing] = useState(false);
  const text = edit?.text ?? item.text;
  const category = edit?.category ?? item.category;
  const [draft, setDraft] = useState(text);
  const [draftCategory, setDraftCategory] = useState<string>(category);
  const textId = useId();
  const save = () => {
    const clean = draft.replace(/\s+/g, ' ').trim();
    if (!clean) return;
    const next: Edit = {};
    if (clean !== item.text) next.text = clean;
    if (draftCategory !== item.category)
      next.category = draftCategory as Category;
    onEdit(next.text || next.category ? next : undefined);
    setEditing(false);
  };
  const meta = [
    item.source_label,
    shortDate(item.date),
    edit ? 'Edited' : '',
  ].filter(Boolean);
  if (editing)
    return (
      <form
        className="memory-import-candidate is-editing"
        onSubmit={(event) => {
          event.preventDefault();
          save();
        }}
        onKeyDown={(event) => {
          if (event.key === 'Escape') {
            event.preventDefault();
            event.stopPropagation();
            setEditing(false);
          }
        }}
      >
        <Select
          aria-label="Type"
          value={draftCategory}
          onChange={(event) => setDraftCategory(event.target.value)}
        >
          {CATEGORIES.map((value) => (
            <option key={value} value={value}>
              {categoryWords(value)}
            </option>
          ))}
        </Select>
        <Input
          aria-label="Memory"
          value={draft}
          maxLength={2000}
          autoFocus
          onChange={(event) => setDraft(event.target.value)}
        />
        <IconButton size="sm" label="Save edit" type="submit">
          <Check size={15} aria-hidden />
        </IconButton>
        <IconButton
          size="sm"
          label="Cancel edit"
          onClick={() => setEditing(false)}
        >
          <X size={15} aria-hidden />
        </IconButton>
      </form>
    );
  return (
    <div className="memory-import-candidate" data-checked={checked}>
      <label className="memory-import-check">
        <input
          type="checkbox"
          checked={checked}
          aria-labelledby={textId}
          onChange={(event) => onCheck(event.target.checked)}
        />
      </label>
      <div className="memory-import-candidate-body">
        <span id={textId} className="memory-import-candidate-text">
          {text}
        </span>
        <span className="memory-import-candidate-meta">
          <CategoryChip category={category} />
          {meta.map((part) => (
            <span key={part}>{part}</span>
          ))}
        </span>
      </div>
      <IconButton
        size="sm"
        label="Edit memory"
        onClick={() => {
          setDraft(text);
          setDraftCategory(category);
          setEditing(true);
        }}
      >
        <Pencil size={14} aria-hidden />
      </IconButton>
    </div>
  );
}

const ACTIONS: Record<
  MemoryImportPreviewItem['action'],
  {
    icon: ReactNode;
    tone: string;
    words: (item: MemoryImportPreviewItem) => string;
  }
> = {
  add: {
    icon: <Plus size={13} aria-hidden />,
    tone: 'add',
    words: () => 'New',
  },
  update: {
    icon: <GitMerge size={13} aria-hidden />,
    tone: 'update',
    words: (item) => `Adds to “${item.saved_subject ?? item.subject}”`,
  },
  supersede: {
    icon: <ArrowRightLeft size={13} aria-hidden />,
    tone: 'supersede',
    words: (item) => `Replaces “${item.saved_subject ?? ''}”`,
  },
  skip: {
    icon: <CheckCheck size={13} aria-hidden />,
    tone: 'skip',
    words: () => 'Already saved',
  },
};

export function actionWords(item: MemoryImportPreviewItem) {
  return ACTIONS[item.action].words(item);
}

/** One previewed memory: what saving it does, with a tick to leave it out. */
export function PreviewRow({
  item,
  checked,
  onCheck,
}: {
  item: MemoryImportPreviewItem;
  checked: boolean;
  onCheck: (checked: boolean) => void;
}) {
  const textId = useId();
  const action = ACTIONS[item.action];
  const skip = item.action === 'skip';
  const detail =
    item.action === 'supersede'
      ? `Kept as history: ${item.saved_text ?? ''}`
      : item.action === 'update'
        ? `Will read: ${item.new_text ?? item.text}`
        : item.action === 'skip'
          ? `Saved: ${item.saved_text ?? ''}`
          : item.source_label;
  return (
    <div className="memory-import-candidate" data-checked={checked && !skip}>
      <label className="memory-import-check">
        <input
          type="checkbox"
          checked={checked && !skip}
          disabled={skip}
          aria-labelledby={textId}
          onChange={(event) => onCheck(event.target.checked)}
        />
      </label>
      <div className="memory-import-candidate-body">
        <span id={textId} className="memory-import-candidate-text">
          {item.text}
        </span>
        <span className="memory-import-candidate-meta">
          <span className="memory-import-action" data-tone={action.tone}>
            {action.icon}
            {action.words(item)}
          </span>
          <span className="memory-import-detail" title={detail}>
            {detail}
          </span>
        </span>
      </div>
    </div>
  );
}

/** Imports from the last 7 days, each with Undo. */
export function RecentImports({
  items,
  busy,
  onUndo,
}: {
  items: readonly MemoryImportReceipt[];
  busy: string | null;
  onUndo: (receipt: MemoryImportReceipt) => void;
}) {
  const shown = useMemo(
    () => items.filter((item) => item.status === 'applied').slice(0, 3),
    [items],
  );
  if (!shown.length) return null;
  return (
    <ul className="memory-import-recent" aria-label="Recent imports">
      {shown.map((receipt) => (
        <li key={receipt.batch_id}>
          <span>
            <strong>
              From {receipt.sources.join(', ') || 'another assistant'}
            </strong>{' '}
            · {shortDate(receipt.applied_at)} · {receiptWords(receipt)}
          </span>
          <IconButton
            size="sm"
            label="Undo this import"
            disabled={busy !== null}
            onClick={() => onUndo(receipt)}
          >
            <Undo2 size={14} aria-hidden />
          </IconButton>
        </li>
      ))}
    </ul>
  );
}
