import {
  ShieldAlert,
  ShieldBan,
  ShieldCheck,
  ShieldQuestion,
  type LucideIcon,
} from 'lucide-react';
import { useOverlay } from '../../ui/overlays';

/**
 * The four approval levels (A0), in the words people see everywhere: Ask
 * (the default), Auto (a reviewer approves what matches the request), Full
 * autonomy (nothing asks; chosen on purpose, one dialog the first time) and
 * Block. Never the stored values.
 */
export type ApprovalLevel = 'approve' | 'auto' | 'full' | 'block';

/** The order every control offers them in. */
export const APPROVAL_LEVELS: readonly ApprovalLevel[] = [
  'approve',
  'auto',
  'full',
  'block',
];

export const APPROVAL_LABELS: Record<ApprovalLevel, string> = {
  approve: 'Ask',
  auto: 'Auto',
  full: 'Hands-off',
  block: 'Block',
};

/** One line under each level in the menus that choose it. */
export const APPROVAL_DESCRIPTIONS: Record<ApprovalLevel, string> = {
  approve: 'Asks before anything that makes changes',
  auto: 'A reviewer approves what you asked for; the rest asks',
  full: 'Nothing asks; every action runs and is recorded',
  block: 'Nothing that makes changes runs',
};

export const APPROVAL_ICONS: Record<ApprovalLevel, LucideIcon> = {
  approve: ShieldQuestion,
  auto: ShieldCheck,
  full: ShieldAlert,
  block: ShieldBan,
};

/** A stored or older value in today's words ("allow_all" was the old Auto). */
export function approvalLevel(value: string | null | undefined): ApprovalLevel {
  if (value === 'block' || value === 'auto' || value === 'full') return value;
  if (value === 'allow_all' || value === 'agent_run' || value === 'auto_edit')
    return 'auto';
  return 'approve';
}

/** A workflow's or run's level as a short tag ("Hands-off" carries the warning tone). */
export function approvalTag(value: string | null | undefined): string {
  const level = approvalLevel(value);
  return {
    approve: 'Asks before actions',
    auto: 'Auto approvals',
    full: 'Hands-off',
    block: 'Blocks actions',
  }[level];
}

export const SHARED_CONVERSATION_NOTE =
  'Other people share this conversation, so it runs on Auto at most.';

/** What Hands-off skips and what still holds: the one-time dialog's body. */
export function FullAutonomyNotice() {
  return (
    <div className="full-autonomy-notice stack">
      <p>What it skips:</p>
      <ul>
        <li>
          Every approval card: commands, file changes, messages and emails,
          purchases and app actions
        </li>
        <li>
          Actions an app&rsquo;s access says to ask about, even high-impact ones
        </li>
        <li>
          Screen and camera captures, and reading Row-Bot&rsquo;s own data when
          that setting is on
        </li>
        <li>Pauses in workflows and goals that run while you&rsquo;re away</li>
      </ul>
      <p>What stays in place:</p>
      <ul>
        <li>
          Blocked commands, settings you&rsquo;ve switched off and apps you
          turned off in a chat
        </li>
        <li>A record of every action in the conversation&rsquo;s activity</li>
        <li>Conversations shared with other people stay on Auto</li>
      </ul>
      <p>
        You won&rsquo;t see this again. Switch back to Ask or Auto any time.
      </p>
    </div>
  );
}

/** The overlay layer, or null where a part is rendered without one. */
function useOverlayIfAny() {
  try {
    return useOverlay();
  } catch {
    return null;
  }
}

/**
 * Asks the person to turn Hands-off on (the first time on this install):
 * a warning dialog with Cancel and Turn on. `onConfirm` runs on Turn on.
 */
export function useConfirmFullAutonomy() {
  const overlay = useOverlayIfAny();
  // Never turned on without the dialog: with no overlay layer, nothing happens.
  return (onConfirm: () => void, returnFocusTo?: HTMLElement | null) =>
    overlay?.open({
      kind: 'alert',
      key: 'full-autonomy',
      returnFocusTo,
      title: 'Turn on Hands-off?',
      description:
        'With Hands-off on, every action runs without asking you. Only choose it for work you trust Row-Bot to do unattended.',
      content: <FullAutonomyNotice />,
      confirmLabel: 'Turn on',
      onConfirm,
    });
}
