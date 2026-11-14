import { useMemo, type ReactNode } from 'react';
import type { ClientController } from '../../api/controller';
import type { ClientPlatform, MainWindowTarget } from '../../platform';
import { MomentBubble, useBuddyMoment } from './BuddyMoment';
import {
  FOR_YOU_PATH,
  momentMenu,
  performMoment,
  useLessLikeThis,
  type MomentGo,
} from './moment-actions';
import { proactiveIO, useProactive } from './proactive-state';

/**
 * The desktop pet's Proactive: Buddy's glance and its smaller moment
 * bubble. Every action brings the main window forward on its place: the
 * chat (Continue in chat), the draft or brief, For you, or the moment's Why
 * this; the rest of the moment waits in For you there.
 */
export function usePetMoment(
  controller: ClientController,
  platform: ClientPlatform,
  enabled: boolean,
): { glance: boolean; moment: ReactNode } {
  const io = useMemo(
    () =>
      typeof controller.proactiveMoments === 'function'
        ? proactiveIO(controller)
        : null,
    [controller],
  );
  const { store, view } = useProactive(io);
  const bubble = useBuddyMoment(enabled ? view : { ...view, moments: [] });
  const less = useLessLikeThis(store);
  const main = (conversation: string | null = null) =>
    void platform.showMainWindow(conversation);
  // The main window comes forward on the place itself: For you, a draft, or
  // the moment's Why this (anywhere else, on Home).
  const open = (target: MainWindowTarget) =>
    void platform.showMainWindow(null, target);
  const go: MomentGo = {
    conversation: (id) => main(id),
    path: (to) => (to === FOR_YOU_PATH ? open('for-you') : main()),
    draft: (id) => open(`draft:${id}`),
    why: (item) => open(`why:${item.moment_id}`),
  };
  const current = bubble.bubble;
  const moment =
    store && current ? (
      <MomentBubble
        bubble={current}
        fading={bubble.fading}
        onHold={bubble.hold}
        onClose={bubble.close}
        menu={
          current.kind === 'one'
            ? momentMenu({
                notNow: () => {
                  bubble.close();
                  void store
                    .feedback(current.moment.moment_id, { action: 'not_now' })
                    .catch(() => undefined);
                },
                less: () => undefined,
                why: () => {
                  bubble.close();
                  open(`why:${current.moment.moment_id}`);
                },
              })
            : [
                {
                  label: 'Not now',
                  onSelect: () => {
                    bubble.close();
                    current.moments.forEach(
                      (item) =>
                        void store
                          .feedback(item.moment_id, { action: 'not_now' })
                          .catch(() => undefined),
                    );
                  },
                },
              ]
        }
        onAction={(item, action) => {
          bubble.close();
          void performMoment(store, item, action, go).catch(() => undefined);
        }}
        onOpenForYou={() => {
          bubble.close();
          open('for-you');
        }}
        onLess={less}
      />
    ) : null;
  return { glance: bubble.glance, moment };
}
