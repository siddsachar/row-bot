import { Laptop } from 'lucide-react';
import type { ReactNode } from 'react';
import { AppLink } from './app-link';
import { PHONE_LAYOUT, useMedia } from './use-media';
import './styles/on-your-computer.css';

/**
 * Whether the phone layout applies (below 768px). It follows the width, never
 * the user agent, so the native shells' WebViews get it too.
 */
export function usePhone(): boolean {
  return useMedia(PHONE_LAYOUT);
}

/**
 * What a phone shows in place of a desktop-only tool (D5): open it on the
 * computer, and a way back to Home. `children` says why, in place of
 * "<what> needs a bigger screen."
 */
export function OnYourComputer({
  what,
  children,
}: {
  what: string;
  children?: ReactNode;
}) {
  return (
    <section className="empty-state on-your-computer" aria-label={what}>
      <span className="on-your-computer-icon" aria-hidden>
        <Laptop size={24} />
      </span>
      <h2>Open this on your computer</h2>
      <p>
        {children ?? `${what} needs a bigger screen.`} Anything that needs you
        still shows on Home.
      </p>
      <AppLink to="/" className="button primary">
        Go to Home
      </AppLink>
    </section>
  );
}
