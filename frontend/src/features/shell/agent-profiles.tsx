import type { ClientController } from '../../api/controller';
import type { useOverlay } from '../../ui/overlays';
import GoalProfileSettings, {
  type GoalProfileSettingsSession,
  type ProfileSummary,
} from '../settings/GoalProfileSettings';

/**
 * Open the reusable Agent profiles library. Shared by the sidebar entry and
 * the /profiles slash command so both reach the same reviewed owner instead
 * of a text dump (B10).
 */
export function openAgentProfiles({
  overlay,
  controller,
  session,
  returnFocusTo,
  onStartProfileChat,
}: {
  overlay: ReturnType<typeof useOverlay>;
  controller: ClientController;
  session: GoalProfileSettingsSession;
  returnFocusTo: HTMLElement | null;
  onStartProfileChat?: (profile: ProfileSummary) => void;
}) {
  overlay.open({
    title: 'Agent profiles',
    description: 'Browse and manage reusable profiles.',
    className: 'profile-library-dialog',
    returnFocusTo,
    content: (
      <GoalProfileSettings
        profilesOnly
        session={session}
        loadProfiles={({ query, scope, cursor }, signal) =>
          controller.profiles(query, scope, cursor, signal)
        }
        loadProfile={controller.profile}
        reviewProfile={controller.reviewProfile}
        executeProfile={(command, review) =>
          controller.executeProfile({
            ...command,
            payload: { ...command.payload, review_id: review.review_id },
          })
        }
        onStartProfileChat={(profile) => {
          let started = false;
          let fallback = 0;
          const start = () => {
            if (started) return;
            started = true;
            window.removeEventListener('popstate', afterClose);
            window.clearTimeout(fallback);
            onStartProfileChat?.(profile);
          };
          const afterClose = () => window.requestAnimationFrame(start);
          window.addEventListener('popstate', afterClose, { once: true });
          overlay.close();
          fallback = window.setTimeout(start, 500);
        }}
      />
    ),
  });
}
