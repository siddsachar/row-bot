import { useEffect, useRef, useState, type ReactNode } from 'react';
import { ChevronDown, PanelRight, Pencil, Search, Share } from 'lucide-react';
import { Hint, IconButton } from '../../ui/primitives';

/**
 * The conversation header: the title (click to rename), the current model as
 * a chip, and icon actions for find, share/export and the context panel.
 */
export default function ConversationHeader({
  title,
  canRename,
  onRename,
  model,
  onModel,
  onFind,
  onShare,
  onContext,
  contextDisabled = false,
  children,
}: {
  title: string;
  canRename: boolean;
  onRename: (title: string) => Promise<void>;
  model?: string;
  onModel?: () => void;
  onFind?: () => void;
  onShare?: () => void;
  /** Present when the context panel is a sheet (compact layouts). */
  onContext?: () => void;
  contextDisabled?: boolean;
  /** Rare, urgent actions such as checking a pending receipt. */
  children?: ReactNode;
}) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(title);
  const [saving, setSaving] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (!editing) setValue(title);
  }, [editing, title]);
  useEffect(() => {
    if (editing) input.current?.select();
  }, [editing]);
  async function commit() {
    const next = value.trim().slice(0, 160);
    if (!next || next === title) {
      setEditing(false);
      return;
    }
    setSaving(true);
    try {
      await onRename(next);
      setEditing(false);
    } finally {
      setSaving(false);
    }
  }
  return (
    <header className="conversation-heading">
      <div className="conversation-title-block">
        {editing ? (
          <input
            ref={input}
            className="conversation-title-input"
            aria-label="Conversation title"
            value={value}
            maxLength={160}
            disabled={saving}
            onChange={(event) => setValue(event.target.value)}
            onBlur={() => void commit()}
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                event.preventDefault();
                void commit();
              } else if (event.key === 'Escape') {
                event.preventDefault();
                setValue(title);
                setEditing(false);
              }
            }}
          />
        ) : (
          <h1
            title={title}
            onClick={() => canRename && setEditing(true)}
            data-renamable={canRename ? 'true' : undefined}
          >
            {title}
          </h1>
        )}
        {canRename && !editing && (
          <IconButton
            size="sm"
            label="Rename conversation"
            className="conversation-rename"
            onClick={() => setEditing(true)}
          >
            <Pencil size={14} aria-hidden />
          </IconButton>
        )}
        {model && onModel && (
          <Hint label="Change model">
            <button
              type="button"
              className="conversation-model-chip"
              aria-label={`Model: ${model}. Change model`}
              onClick={onModel}
            >
              <span>{model}</span>
              <ChevronDown size={12} aria-hidden />
            </button>
          </Hint>
        )}
      </div>
      <div
        className="conversation-actions"
        role="group"
        aria-label="Conversation actions"
      >
        {children}
        {onFind && (
          <IconButton label="Find" onClick={onFind}>
            <Search size={16} aria-hidden />
          </IconButton>
        )}
        {onShare && (
          <IconButton label="Share or export" onClick={onShare}>
            <Share size={16} aria-hidden />
          </IconButton>
        )}
        {onContext && (
          <IconButton
            label="Context"
            disabled={contextDisabled}
            onClick={onContext}
          >
            <PanelRight size={16} aria-hidden />
          </IconButton>
        )}
      </div>
    </header>
  );
}
