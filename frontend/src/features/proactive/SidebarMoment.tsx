import * as Popover from '@radix-ui/react-popover';
import { useInRouterContext, useNavigate } from 'react-router-dom';
import { PHONE_LAYOUT, useMedia } from '../../ui/use-media';
import { FOR_YOU_PATH, useMomentActions } from './moment-actions';
import {
  bubbleLine,
  glanceOf,
  MomentBubble,
  useBuddyMoment,
} from './BuddyMoment';
import {
  useProactive,
  type ProactiveStore,
  type ProactiveView,
} from './proactive-state';

/** Whether Buddy should wear its glance: something waits for the person. */
export function useProactiveGlance(): boolean {
  const { view } = useProactive();
  return glanceOf(view);
}

/** Below this the sidebar is a drawer: the moment strip speaks instead. */
const NARROW = '(max-width: 1023px)';

function Bubble({
  store,
  view,
}: {
  store: ProactiveStore;
  view: ProactiveView;
}) {
  const navigate = useNavigate();
  const bubble = useBuddyMoment(view);
  // Drafts open on Home, where a chat can be started to send one.
  const actions = useMomentActions(store, {
    navigate: (to) => navigate(to),
    draftsInPlace: false,
  });
  const current = bubble.bubble;
  const menu = current
    ? current.kind === 'one'
      ? actions.menu(current.moment, false)
      : [
          {
            label: 'Not now',
            description: 'Keep them in For you.',
            onSelect: () =>
              current.moments.forEach((moment) =>
                actions.feedback(moment, 'not_now'),
              ),
          },
          {
            label: 'Why these?',
            description: 'Each one explains itself in For you.',
            onSelect: () => navigate(FOR_YOU_PATH),
          },
        ]
    : [];
  return (
    <>
      <Popover.Root open={Boolean(current)} modal={false}>
        <Popover.Anchor className="proactive-bubble-anchor" />
        <Popover.Portal>
          <Popover.Content
            className="popover surface-effect proactive-bubble-popover"
            side="right"
            align="end"
            sideOffset={12}
            collisionPadding={12}
            onOpenAutoFocus={(event) => event.preventDefault()}
            onCloseAutoFocus={(event) => event.preventDefault()}
            onInteractOutside={(event) => event.preventDefault()}
            onEscapeKeyDown={() => bubble.close()}
            aria-label={current ? bubbleLine(current) : undefined}
          >
            {current && (
              <MomentBubble
                contained
                bubble={current}
                fading={bubble.fading}
                onHold={bubble.hold}
                onClose={bubble.close}
                menu={[
                  ...menu.map((item) => ({
                    ...item,
                    onSelect: (opener: HTMLButtonElement | null) => {
                      bubble.close();
                      item.onSelect(opener);
                    },
                  })),
                  {
                    label: 'Buddy settings',
                    separatorBefore: true,
                    onSelect: () => {
                      bubble.close();
                      navigate('/settings/buddy');
                    },
                  },
                ]}
                onAction={(moment, action) => {
                  bubble.close();
                  actions.perform(moment, action);
                }}
                onOpenForYou={() => {
                  bubble.close();
                  navigate(FOR_YOU_PATH);
                }}
                onLess={actions.less}
              />
            )}
            <Popover.Arrow className="proactive-bubble-arrow" />
          </Popover.Content>
        </Popover.Portal>
      </Popover.Root>
      {actions.sheets}
    </>
  );
}

/** Buddy's bubble beside the sidebar card (wide windows; nothing outside the app). */
export function SidebarMoment() {
  const { store, view } = useProactive();
  if (!store || view.unavailable) return null;
  return <Placed store={store} view={view} />;
}

function Placed({
  store,
  view,
}: {
  store: ProactiveStore;
  view: ProactiveView;
}) {
  const routed = useInRouterContext();
  const narrow = useMedia(NARROW);
  const phone = useMedia(PHONE_LAYOUT);
  if (!routed || narrow || phone) return null;
  return <Bubble store={store} view={view} />;
}
