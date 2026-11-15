import { LayoutList, X } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { Button, IconButton } from '../../ui/primitives';
import { useMedia } from '../../ui/use-media';
import { FOR_YOU_PATH, useMomentActions } from './moment-actions';
import { bubbleLine, useBuddyMoment } from './BuddyMoment';
import { useProactive } from './proactive-state';
import { KIND_TILES, actionIcon } from './words';
import './proactive.css';

const NARROW = '(max-width: 1023px)';

/**
 * Phones and narrow windows: the sidebar's Buddy is in a drawer, so a
 * moment Buddy delivers shows as a strip above the tab bar instead. Never
 * inside a conversation (it would cover the composer); one action and Not
 * now; it fades like the bubble.
 */
export function MomentStrip({ hidden = false }: { hidden?: boolean }) {
  const narrow = useMedia(NARROW);
  const { store, view } = useProactive();
  const navigate = useNavigate();
  const strip = useBuddyMoment(
    narrow && !hidden ? view : { ...view, moments: [] },
  );
  const actions = useMomentActions(store, {
    navigate: (to) => navigate(to),
    draftsInPlace: false,
  });
  const current = strip.bubble;
  if (!narrow || hidden || !store || !current) return <>{actions.sheets}</>;
  const one = current.kind === 'one' ? current.moment : null;
  const primary = one?.actions[0];
  const line = bubbleLine(current);
  return (
    <>
      <div
        className="proactive-strip"
        role="dialog"
        aria-modal="false"
        aria-label={line}
        data-fading={strip.fading ? 'true' : undefined}
        onPointerEnter={() => strip.hold(true)}
        onPointerLeave={() => strip.hold(false)}
        onFocus={() => strip.hold(true)}
        onKeyDown={(event) => {
          if (event.key === 'Escape') strip.close();
        }}
      >
        <p className="proactive-bubble-line">
          <span
            className="overview-tile"
            data-tone={one ? KIND_TILES[one.kind].tone : 'accent'}
            aria-hidden
          >
            {one ? KIND_TILES[one.kind].icon : <LayoutList size={16} />}
          </span>
          <span className="proactive-bubble-title">{line}</span>
        </p>
        <div className="proactive-strip-actions">
          {one && primary ? (
            <Button
              variant="primary"
              onClick={() => {
                strip.close();
                actions.perform(one, primary);
              }}
            >
              {actionIcon(primary.id, 15)} {primary.label}
            </Button>
          ) : (
            <Button
              variant="primary"
              onClick={() => {
                strip.close();
                navigate(FOR_YOU_PATH);
              }}
            >
              <LayoutList size={15} aria-hidden /> Open For you
            </Button>
          )}
          <IconButton
            label="Not now"
            onClick={() => {
              strip.close();
              (current.kind === 'one'
                ? [current.moment]
                : current.moments
              ).forEach((moment) => actions.feedback(moment, 'not_now'));
            }}
          >
            <X size={16} aria-hidden />
          </IconButton>
        </div>
        <p className="visually-hidden" role="status" aria-live="polite">
          Buddy: {line}
        </p>
      </div>
      {actions.sheets}
    </>
  );
}
