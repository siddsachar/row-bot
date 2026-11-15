import { useEffect, useState } from 'react';
import { Check, Circle, ExternalLink } from 'lucide-react';
import type { OnboardingSnapshot } from '../../api/types';
import { useRuntime } from '../../runtime';
import { IconButton } from '../../ui/primitives';

export const SPENDING_LIMIT_DONE = 'Done. Don’t show this again';

/**
 * U7 (A3): the host card's one step once Row-Bot has answered for the first
 * time, "Set a spending limit in Railway", linking the workspace's Usage page.
 * The first answer is the one the phone card follows (U10), kept on the
 * server. Row-Bot can't see the host's billing, so a ✓ hides the line for
 * good, kept on the server as the phone card's dismissal is.
 */
export default function SpendingLimitStep({
  label,
  url,
}: {
  /** The host's own words: "Set a spending limit in Railway". */
  label: string;
  /** The host's usage page, where the limit is set. */
  url: string;
}) {
  const { controller } = useRuntime();
  const [setup, setSetup] = useState<OnboardingSnapshot | null>(null);
  const [hidden, setHidden] = useState(false);
  useEffect(() => {
    let live = true;
    Promise.resolve(controller.onboarding())
      .then((value) => {
        if (live) setSetup(value ?? null);
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [controller]);

  if (
    hidden ||
    !setup ||
    !setup.phone_card_conversation ||
    setup.dismissed_spending_limit
  )
    return null;

  async function done() {
    setHidden(true);
    const send = (known: OnboardingSnapshot) =>
      controller.onboardingCommand({
        command_id: crypto.randomUUID(),
        expected_revision: known.revision,
        action: 'dismiss_spending_limit',
        profile: [],
        step: '',
      });
    try {
      // The state this line read; changed meanwhile, read it and send again.
      await send(setup!).catch(async () => {
        const latest = await controller.onboarding();
        if (latest && !latest.dismissed_spending_limit) await send(latest);
      });
    } catch {
      /* Hidden here; it may show once more after a reload. */
    }
  }

  return (
    <p className="hosted-by-step">
      <Circle size={14} aria-hidden />
      <a href={url} target="_blank" rel="noopener noreferrer">
        {label}
        <ExternalLink size={12} aria-hidden />
      </a>
      <IconButton
        size="sm"
        label={SPENDING_LIMIT_DONE}
        onClick={() => void done()}
      >
        <Check size={15} aria-hidden />
      </IconButton>
    </p>
  );
}
