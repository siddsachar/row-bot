import { useState, type ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import * as Popover from '@radix-ui/react-popover';
import { EyeOff, PictureInPicture2, Settings } from 'lucide-react';
import { useClientState } from '../../runtime';
import { Button, Hint, IconButton, StatusDot } from '../../ui/primitives';
import type { BuddySnapshot } from '../shell/BuddyControls';
import { SidebarMoment, useProactiveGlance } from '../proactive/SidebarMoment';

type Tone = 'neutral' | 'accent' | 'warning' | 'danger' | 'success';

/**
 * What Buddy is doing, in words, with its tone: "Ready" at rest (never
 * "Idle"), else the status Row-Bot gives ("Needs approval", "Workflow
 * running"). Working pulses; the rest hold still.
 */
export function buddyStatus(snapshot: BuddySnapshot): {
  text: string;
  tone: Tone;
  pulse: boolean;
} {
  const activity = snapshot.activity ?? 'idle';
  const label = snapshot.status.label.trim();
  // After a finished turn the label goes back to Idle while the activity still says completed.
  const quiet = !label || /^(idle|ready)$/i.test(label);
  const resting = activity === 'idle' && quiet;
  const text = quiet ? 'Ready' : label;
  if (['thinking', 'streaming', 'tool'].includes(activity))
    return { text, tone: 'accent', pulse: true };
  if (activity === 'approval' || activity === 'stopping')
    return { text, tone: 'warning', pulse: false };
  if (activity === 'error' || activity === 'disconnected')
    return { text, tone: 'danger', pulse: false };
  if (activity === 'completed') return { text, tone: 'success', pulse: false };
  // A status beside a conversation's own (a workflow, a command, Learning) is live work too.
  return resting
    ? { text, tone: 'neutral', pulse: false }
    : { text, tone: 'accent', pulse: false };
}

/** The conversation Buddy's status is about, when it isn't the one open. */
function useElsewhere(conversationId: string) {
  const state = useClientState();
  if (conversationId === state.selectedConversationId) return null;
  const row = state.conversations.find((item) => item.id === conversationId);
  return row ? row.title || 'Untitled' : null;
}

/**
 * Buddy's status, opening the conversation it is about when that isn't the
 * open one ("Needs approval" in another chat). Rendered only while Buddy is
 * busy with a conversation, so the card itself needs no router.
 */
function StatusElsewhere({
  conversationId,
  status,
  peek = false,
  onOpen,
}: {
  conversationId: string;
  status: ReactNode;
  peek?: boolean;
  onOpen(): void;
}) {
  const navigate = useNavigate();
  const title = useElsewhere(conversationId);
  const open = () => {
    onOpen();
    navigate(`/conversations/${encodeURIComponent(conversationId)}`);
  };
  if (peek)
    return title ? (
      <button type="button" className="buddy-peek-link" onClick={open}>
        In {title} →
      </button>
    ) : null;
  return title ? (
    <button
      type="button"
      className="buddy-card-status-link"
      title={`Open ${title}`}
      onClick={open}
    >
      <span role="status">{status}</span>
    </button>
  ) : (
    <span role="status">{status}</span>
  );
}

/**
 * Buddy in the sidebar: its avatar with an activity ring and what it is
 * doing (its name is in the tooltip and the peek); Pop out and Settings show at the right edge on hover. The
 * avatar opens a peek card: Buddy larger, where its status is from, and its
 * actions.
 */
export function BuddyCard({
  snapshot,
  avatar,
  portrait,
  busy = false,
  showStatus = true,
  onSettings,
  onUndock,
  onHide,
  children,
}: {
  snapshot: BuddySnapshot;
  /** The sidebar avatar (it may be dragged out to undock). */
  avatar: ReactNode;
  /** Buddy larger, for the peek card. */
  portrait: ReactNode;
  busy?: boolean;
  showStatus?: boolean;
  onSettings(): void;
  onUndock?(): void;
  onHide?(): void;
  children?: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const narrow =
    typeof window !== 'undefined' &&
    window.matchMedia?.('(max-width: 640px)').matches === true;
  const name = snapshot.preferences.display_name || 'Buddy';
  const status = buddyStatus(snapshot);
  // Buddy's face says it; a screen reader hears it with Buddy's name.
  // When Buddy is busy, its status words win.
  const glancing = useProactiveGlance() && status.tone === 'neutral';
  // The conversation its work is in, while it works.
  const about =
    status.tone !== 'neutral' ? (snapshot.conversation_id ?? '') : '';
  const statusLine = (
    <StatusDot
      tone={status.tone}
      pulse={status.pulse}
      label={status.text}
      showLabel
      className="buddy-card-status"
    />
  );
  return (
    <aside
      className="buddy-companion buddy-card"
      aria-label="Buddy companion"
      aria-busy={busy}
    >
      <Popover.Root open={open} onOpenChange={setOpen}>
        <Hint
          label={`${name} · ${glancing ? 'Something for you' : status.text}`}
        >
          <Popover.Trigger asChild>
            <Button
              variant="ghost"
              className="buddy-avatar-button"
              aria-label={`Open ${name}`}
              aria-description={glancing ? 'Something for you' : undefined}
              data-proactive={glancing ? 'moment' : undefined}
            >
              {avatar}
            </Button>
          </Popover.Trigger>
        </Hint>
        <Popover.Portal>
          <Popover.Content
            className="popover surface-effect buddy-peek"
            aria-label={name}
            tabIndex={-1}
            // Beside the sidebar on a wide window; above the avatar in a phone's drawer.
            side={narrow ? 'top' : 'right'}
            align={narrow ? 'start' : 'end'}
            // The card takes focus, not its first action: no ring on a link nobody chose.
            onOpenAutoFocus={(event) => {
              event.preventDefault();
              (event.currentTarget as HTMLElement | null)?.focus();
            }}
            sideOffset={12}
            collisionPadding={12}
          >
            <div className="buddy-peek-portrait">{portrait}</div>
            <p className="buddy-peek-name">{name}</p>
            <div role="status" className="buddy-peek-status">
              {statusLine}
            </div>
            {about && (
              <StatusElsewhere
                conversationId={about}
                status={statusLine}
                peek
                onOpen={() => setOpen(false)}
              />
            )}
            <div className="buddy-peek-actions">
              {onUndock && (
                <Button
                  variant="ghost"
                  onClick={() => {
                    setOpen(false);
                    onUndock();
                  }}
                >
                  <PictureInPicture2 size={16} aria-hidden /> Pop out
                </Button>
              )}
              <Button
                variant="ghost"
                onClick={() => {
                  setOpen(false);
                  onSettings();
                }}
              >
                <Settings size={16} aria-hidden /> Settings
              </Button>
              {onHide && (
                <Button
                  variant="ghost"
                  onClick={() => {
                    setOpen(false);
                    onHide();
                  }}
                >
                  <EyeOff size={16} aria-hidden /> Hide
                </Button>
              )}
            </div>
          </Popover.Content>
        </Popover.Portal>
      </Popover.Root>
      <span className="buddy-companion-text">
        {/* The name shows in the peek and the tooltip; here it is for screen readers. */}
        <span className="buddy-companion-name visually-hidden">{name}</span>
        {showStatus &&
          (about ? (
            <StatusElsewhere
              conversationId={about}
              status={statusLine}
              onOpen={() => setOpen(false)}
            />
          ) : (
            <span role="status">{statusLine}</span>
          ))}
      </span>
      <span className="buddy-card-actions">
        {onUndock && (
          <IconButton size="sm" label="Pop out Buddy" onClick={onUndock}>
            <PictureInPicture2 size={15} aria-hidden />
          </IconButton>
        )}
        <IconButton size="sm" label="Buddy settings" onClick={onSettings}>
          <Settings size={15} aria-hidden />
        </IconButton>
      </span>
      {children}
      {/* Proactive: Buddy's bubble for a moment it was asked to deliver. */}
      <SidebarMoment />
    </aside>
  );
}
