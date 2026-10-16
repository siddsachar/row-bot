import { useEffect, useState } from 'react';

const PHONE = '(max-width: 767px)';

/** A phone-width window: short asks become bottom sheets there. */
export function usePhone(): boolean {
  const [phone, setPhone] = useState(() =>
    typeof window !== 'undefined' && typeof window.matchMedia === 'function'
      ? window.matchMedia(PHONE).matches
      : false,
  );
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return;
    const media = window.matchMedia(PHONE);
    const change = () => setPhone(media.matches);
    media.addEventListener?.('change', change);
    return () => media.removeEventListener?.('change', change);
  }, []);
  return phone;
}
