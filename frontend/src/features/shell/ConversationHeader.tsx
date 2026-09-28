import { useEffect, useRef, useState, type ReactNode } from 'react';
import {
  Cpu,
  MoreHorizontal,
  PanelRight,
  Pencil,
  Share,
  TextSearch,
} from 'lucide-react';
import { IconButton, Menu, type MenuAction } from '../../ui/primitives';

/** The server keeps at most 120 characters of a title (B135). */
export const TITLE_LIMIT = 120;

/**
 * The conversation header: the title (click to rename), the current model as
 * a chip, and icon actions for find, share/export and the context panel. On
 * phones it is one 48px row: `leading` (back to the conversation list), the
 * title and a ⋯ menu that holds every other action (`menuActions` first).
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
  leading,
  menuActions,
  children,
}: {
  title: string;
  canRename: boolean;
  onRename: (title: string) => Promise<void>;
  model?: string;
  onFind?: () => void;
  onShare?: () => void;
  /** Shows or hides the Context card, or opens its sheet on compact layouts. */
  onContext?: () => void;
  /** Whether Context is on screen (desktop); undefined for a sheet. */
  contextPressed?: boolean;
  contextDisabled?: boolean;
  /** Workspace-owned icon actions (Open panel). */
  actions?: ReactNode;
  /** Shown before the title on compact layouts (the navigation button). */
  leading?: ReactNode;
  /**
   * Phone layout: fold the actions into one ⋯ menu. The workspace's own
   * entries (search, panels) come first.
   */
  menuActions?: MenuAction[];
  /** Rare, urgent actions such as checking a pending receipt. */
  children?: ReactNode;
}) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(title);
  const [saving, setSaving] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  // Enter commits and disables the input, and disabling a focused input
  // blurs it: without this guard the blur renamed a second time with the
  // same revision and the server answered 409.
  const committing = useRef(false);
  useEffect(() => {
    if (!editing) setValue(title);
  }, [editing, title]);
  useEffect(() => {
    if (!editing) return;
    input.current?.focus();
    input.current?.select();
  }, [editing]);
  async function commit() {
    if (committing.current) return;
    const next = value.trim().slice(0, TITLE_LIMIT);
    if (!next || next === title) {
      setEditing(false);
      return;
    }
    committing.current = true;
    setSaving(true);
    try {
      await onRename(next);
      setEditing(false);
    } finally {
      committing.current = false;
      setSaving(false);
    }
  }
  const phone = menuActions !== undefined;
  const menu: MenuAction[] = phone
    ? [
        ...menuActions,
        ...(onFind
          ? [
              {
                label: 'Find in conversation',
                icon: <TextSearch size={16} />,
                separatorBefore: menuActions.length > 0,
                onSelect: onFind,
              },
            ]
          : []),
        ...(onContext
          ? [
              {
                label: 'Context',
                icon: <PanelRight size={16} />,
                disabled: contextDisabled,
                onSelect: onContext,
              },
            ]
          : []),
        ...(onShare
          ? [
              {
                label: 'Share or export',
                icon: <Share size={16} />,
                onSelect: onShare,
              },
            ]
          : []),
        ...(canRename
          ? [
              {
                label: 'Rename conversation',
                icon: <Pencil size={16} />,
                onSelect: () => undefined,
                // After the menu lets go of focus, so the field keeps it.
                afterClose: () => setEditing(true),
              },
            ]
          : []),
      ]
    : [];
  return (
    <header
      className="conversation-heading"
      data-layout={phone ? 'phone' : leading ? 'compact' : undefined}
    >
      {leading}
      <div className="conversation-title-block">
        {editing ? (
          <input
            ref={input}
            className="conversation-title-input"
            aria-label="Conversation title"
            value={value}
            maxLength={TITLE_LIMIT}
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
        {canRename && !editing && !phone && (
          <IconButton
            size="sm"
            label="Rename conversation"
            className="conversation-rename"
            onClick={() => setEditing(true)}
          >
            <Pencil size={14} aria-hidden />
          </IconButton>
        )}
        {model && !phone && (
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
        {phone && menu.length > 0 && (
          <Menu
            label="Conversation menu"
            iconOnly
            variant="ghost"
            className="conversation-menu"
            actions={menu}
          >
            <MoreHorizontal size={18} aria-hidden />
          </Menu>
        )}
        {!phone && onFind && (
          <IconButton label="Find" onClick={onFind}>
            <TextSearch size={16} aria-hidden />
          </IconButton>
        )}
        {!phone && onShare && (
          <IconButton label="Share or export" onClick={onShare}>
            <Share size={16} aria-hidden />
          </IconButton>
        )}
        {!phone && actions}
        {!phone && onContext && (
          <IconButton
            label="Context"
            data-context-toggle=""
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
