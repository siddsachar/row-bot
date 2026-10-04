import { useEffect, useState, useSyncExternalStore } from 'react';
import { useRuntime } from '../../runtime';
import type { ProfileSummary } from '../../api/types';

const noSubscription = () => () => undefined;
/** Read existing conversation/profile facts; never select or broaden a profile. */
export default function ProfileContext() {
  const { controller } = useRuntime();
  const workspace = useSyncExternalStore(
    controller.subscribe ?? noSubscription,
    () => {
      const state = controller.getSnapshot?.();
      return state?.workspace?.conversation_id === state?.selectedConversationId
        ? (state?.workspace ?? null)
        : null;
    },
  );
  const profileId = workspace?.controls.profile_id;
  const [loaded, setLoaded] = useState<{
    id: string;
    profile: ProfileSummary | null;
  } | null>(null);
  useEffect(() => {
    if (!profileId) return;
    const abort = new AbortController();
    void controller
      .profile(profileId, abort.signal)
      .then((result) => {
        if (!abort.signal.aborted)
          setLoaded({ id: profileId, profile: result.profile });
      })
      .catch(() => {
        if (!abort.signal.aborted) setLoaded({ id: profileId, profile: null });
      });
    return () => abort.abort();
  }, [controller, profileId]);
  const profile = loaded && loaded.id === profileId ? loaded.profile : null;
  if (!workspace)
    return (
      <p>
        No chat is selected. Availability will be checked against the profile
        and model of the chat you choose.
      </p>
    );
  return (
    <section aria-label="Current chat availability" className="stack">
      <p>
        Current chat profile:{' '}
        {profile?.display_name ||
          workspace.profiles.find((p) => p.id === profileId)?.label ||
          'Not established'}
        .
      </p>
      {workspace.controls.runtime_mode === 'chat_only' && (
        <p>
          Tools are unavailable in Chat only mode. Choose Agent mode in the
          current chat to use tools.
        </p>
      )}
      {workspace.model_status && workspace.model_status.state !== 'ready' && (
        <p>
          Current model is unavailable:{' '}
          {workspace.model_status.reason ||
            'Choose an available model in the chat.'}
        </p>
      )}
      {profile?.enabled === false && (
        <p>This profile is off. Choose an enabled profile before use.</p>
      )}
      {profile?.capability === 'read_only' && (
        <p>
          This profile permits read-only work. Change and high-impact actions
          remain subject to its restrictions.
        </p>
      )}
      {profile && (
        <details>
          <summary>Current profile restrictions</summary>
          <p>
            Tool scope:{' '}
            {profile.allow_tools.length
              ? profile.allow_tools.join(', ')
              : 'Inherits enabled tools'}
            .
          </p>
          <p>
            Selected skills:{' '}
            {profile.skills.length
              ? profile.skills.join(', ')
              : 'Inherits skill defaults'}
            .
          </p>
          <p>
            Approval mode: {profile.approval_mode}. Setup does not change these
            choices; runtime policy remains authoritative.
          </p>
        </details>
      )}
      {profileId && loaded?.id === profileId && !profile && (
        <p>
          Profile restrictions could not be read. Review the profile before use.
        </p>
      )}
      <a href="/app-v2/settings/profiles">Review profile restrictions</a>
    </section>
  );
}
