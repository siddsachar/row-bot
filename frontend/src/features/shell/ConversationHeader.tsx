import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Cpu, PanelRight, Pencil, Search, Share } from 'lucide-react';
import { IconButton } from '../../ui/primitives';

/**
 * The conversation header: the title (click to rename), the current model as
 * a chip, and icon actions for find, share/export and the context panel.
 */
export default function ConversationHeader({
  title,
  canRename,
  onRename,
  model,
  onFind,
  onShare,
  onContext,
  contextPressed,
  contextDisabled = false,
  actions,
  children,
}: {
  title: string;
  canRename: boolean;
  onRename: (title: string) => Promise<void>;
  model?: string;
  onFind?: () => void;
  onShare?: () => void;
  /** Shows Context: toggles the right region, or opens a sheet when narrow. */
  onContext?: () => void;
  /** Whether Context is on screen (desktop); undefined for a sheet. */
  contextPressed?: boolean;
  contextDisabled?: boolean;
  /** Workspace-owned icon actions (Open panel). */
  actions?: ReactNode;
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
        {model && (
          // The composer's model pill is the control; this chip only says
          // which model the conversation uses.
          <span className="conversation-model-chip" title={`Model: ${model}`}>
            <Cpu size={12} aria-hidden />
            <span>{model}</span>
          </span>
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
        {actions}
        {onContext && (
          <IconButton
            label="Context"
            shortcut={contextPressed === undefined ? undefined : 'Mod+.'}
            pressed={contextPressed}
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
