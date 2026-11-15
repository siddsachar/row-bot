import { useEffect, useState } from 'react';

/** The phone layout (below 768px), where pages lay out for one hand. */
export const PHONE_LAYOUT = '(max-width: 767px)';

/** Whether a media query matches now; follows it as the window changes. */
export function useMedia(query: string): boolean {
  const read = () =>
    typeof matchMedia === 'function' ? matchMedia(query).matches : false;
  const [matches, setMatches] = useState(read);
  useEffect(() => {
    if (typeof matchMedia !== 'function') return;
    const media = matchMedia(query);
    const changed = () => setMatches(media.matches);
    media.addEventListener('change', changed);
    changed();
    return () => media.removeEventListener('change', changed);
  }, [query]);
  return matches;
}
