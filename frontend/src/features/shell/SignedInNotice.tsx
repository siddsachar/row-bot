import { useEffect } from 'react';
import { useNotify } from '../../ui/overlays';

/** Set by the claim page (/setup) just before it opens the client (H1, U1). */
export const SIGNED_IN_KEY = 'row-bot:signed-in';

function tabStorage(): Storage | null {
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

/** Once a setup code signed this browser in, the client says so, once. */
export default function SignedInNotice({
  storage = tabStorage(),
}: {
  storage?: Pick<Storage, 'getItem' | 'removeItem'> | null;
}) {
  const notify = useNotify();
  useEffect(() => {
    try {
      if (storage?.getItem(SIGNED_IN_KEY) !== '1') return;
      storage.removeItem(SIGNED_IN_KEY);
    } catch {
      return;
    }
    notify('This browser is signed in.');
  }, [notify, storage]);
  return null;
}
