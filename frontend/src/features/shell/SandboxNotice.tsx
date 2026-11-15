import { FlaskConical } from 'lucide-react';
import { useClientSelector } from '../../runtime';
import { Hint } from '../../ui/primitives';

const DETAIL =
  'This is a sandbox: a second Row-Bot on made-up data, with its own chats, settings and sign-ins. It can’t see your own Row-Bot or its files.';

/**
 * A sandbox's mark (5.3.1 B4): every window of a sandbox says so where the
 * sidebar shows what needs the person, so a demo or a trial is never taken
 * for the real thing; the rail shows its icon.
 */
export default function SandboxNotice({
  compact = false,
}: {
  compact?: boolean;
}) {
  const sandbox = useClientSelector(
    (state) => state.handshake?.sandbox === true,
  );
  if (!sandbox) return null;
  return (
    <Hint label={DETAIL}>
      <p
        className={`nav-attention${compact ? ' is-compact button ghost icon-button icon-action icon-action-md' : ''}`}
        data-tone="warning"
        role="status"
        tabIndex={0}
        aria-label={`Sandbox. ${DETAIL}`}
      >
        <FlaskConical size={compact ? 18 : 15} aria-hidden />
        {!compact && <span>Sandbox · made-up data</span>}
      </p>
    </Hint>
  );
}
