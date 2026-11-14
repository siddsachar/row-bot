import { useMemo, type ReactNode } from 'react';
import type { ClientController } from '../../api/controller';
import type { ClientPlatform } from '../../platform';
import { MomentBubble, useBuddyMoment } from './BuddyMoment';
import {
  momentMenu,
  performMoment,
  useLessLikeThis,
  type MomentGo,
} from './moment-actions';
import { proactiveIO, useProactive } from './proactive-state';

/**
 * The desktop pet's Proactive: Buddy's glance and its smaller moment
 * bubble. Every action brings the main window forward (at the chat, for
 * Continue in chat); the rest of the moment waits in For you there.
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
  const go: MomentGo = {
    conversation: (id) => main(id),
    path: () => main(),
    draft: () => main(),
    why: () => main(),
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
                  main();
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
          main();
        }}
        onLess={less}
      />
    ) : null;
  return { glance: bubble.glance, moment };
}
