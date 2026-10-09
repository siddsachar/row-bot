import type { ClientController } from '../../api/controller';
import type { useOverlay } from '../../ui/overlays';
import GoalProfileSettings, {
  type GoalProfileSettingsSession,
  type ProfileSummary,
} from '../settings/GoalProfileSettings';

/** The built-in profile a conversation uses when it names none. */
export const DEFAULT_PROFILE_ID = 'builtin:row_bot_default';

type ProfileChoice = { id: string; label: string; description?: string };

/**
 * Profiles to offer in a chat. Naming no profile means the built-in Default,
 * so Default is listed once: as that profile when the workspace lists it.
 */
export function profileChoices(
  profiles: readonly ProfileChoice[],
): ProfileChoice[] {
  return profiles.some((profile) => profile.id === DEFAULT_PROFILE_ID)
    ? [...profiles]
    : [{ id: '', label: 'Default' }, ...profiles];
}

/** The listed choice for a chat's profile_id; none means the Default. */
export function currentProfileChoice(
  profiles: readonly ProfileChoice[],
  profileId: string | null | undefined,
): string {
  if (profileId) return profileId;
  return profiles.some((profile) => profile.id === DEFAULT_PROFILE_ID)
    ? DEFAULT_PROFILE_ID
    : '';
}

/**
 * Open the Agents library: the reusable profiles a chat can use. Shared by
 * the sidebar entry and the /profiles slash command so both reach the same
 * reviewed owner instead of a text dump (B10).
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
    title: 'Agents',
    description: 'Start a chat with an agent, or make and manage your own.',
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
        loadProfileInstructions={controller.profileInstructions}
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
