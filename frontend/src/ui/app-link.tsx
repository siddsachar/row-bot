import type { AnchorHTMLAttributes } from 'react';
import { Link, useInRouterContext } from 'react-router-dom';

/** The client's router basename; in-app paths never repeat it. */
export const APP_BASE = '/app-v2';

/**
 * An in-app link to a router path such as `/settings/system`. Inside the
 * router it navigates in place; rendered on its own (an isolated owner or a
 * test) it falls back to the full `/app-v2/…` address. Never pass a path that
 * already starts with the basename (B31).
 */
export function AppLink({
  to,
  ...props
}: Omit<AnchorHTMLAttributes<HTMLAnchorElement>, 'href'> & { to: string }) {
  const routed = useInRouterContext();
  return routed ? (
    <Link to={to} {...props} />
  ) : (
    <a href={`${APP_BASE}${to}`} {...props} />
  );
}
