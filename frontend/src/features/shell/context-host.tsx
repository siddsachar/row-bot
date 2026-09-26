import {
  createContext,
  useContext,
  useLayoutEffect,
  useRef,
  useState,
} from 'react';

/**
 * Context stays mounted while the right region changes (B18). The rail
 * renders once, through a portal, into one detached host element; slots in
 * the chat column, the right region's Context tab or a sheet adopt that
 * element while they are active. Moving a DOM node does not remount React
 * content, so nothing re-reads when a panel opens or closes.
 */
export type ContextHost = {
  element: HTMLDivElement;
  /** Return the host to the hidden parking slot. */
  park: () => void;
};

export const ContextHostContext = createContext<ContextHost | null>(null);

export function useContextHost(): ContextHost | null {
  return useContext(ContextHostContext);
}

/** Owns the host element and a hidden parking slot for it. */
export function useContextHostOwner() {
  const [element] = useState(() => {
    const host = document.createElement('div');
    host.className = 'context-host';
    return host;
  });
  const parking = useRef<HTMLDivElement>(null);
  const [host] = useState<ContextHost>(() => ({
    element,
    park: () => {
      const target = parking.current;
      if (target && element.parentNode !== target) target.appendChild(element);
    },
  }));
  useLayoutEffect(() => {
    if (!element.parentNode) host.park();
  }, [element, host]);
  return { host, parking };
}

/** A place the Context host occupies while active. */
export function ContextSlot({
  active,
  className = 'context-slot',
  host: explicitHost,
}: {
  active: boolean;
  className?: string;
  /** Overlay content renders outside the provider; pass the host directly. */
  host?: ContextHost | null;
}) {
  const provided = useContextHost();
  const host = explicitHost ?? provided;
  const slot = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const target = slot.current;
    if (!active || !target || !host) return;
    target.appendChild(host.element);
    return () => {
      if (host.element.parentNode === target) host.park();
    };
  }, [active, host]);
  return <div ref={slot} className={className} hidden={!active} />;
}
