import { Home, MessagesSquare, Settings, Workflow } from 'lucide-react';
import type { ReactNode } from 'react';
import { useClientState, useRuntime } from '../../runtime';
import { AppLink } from '../../ui/app-link';
import { needsCount, useAttention } from './needs';
import { usePendingApprovals } from './pending-approvals';

export type PhoneTab = 'home' | 'chats' | 'workflows' | 'settings';

/** Which tab a route belongs to; none for other routed pages. */
export function phoneTabFor(pathname: string, search: string): PhoneTab | null {
  if (pathname === '/')
    return new URLSearchParams(search).get('tab') === 'workflows'
      ? 'workflows'
      : 'home';
  if (pathname.startsWith('/library')) return 'chats';
  if (pathname.startsWith('/settings')) return 'settings';
  return null;
}

const TABS: { id: PhoneTab; label: string; to: string; icon: ReactNode }[] = [
  { id: 'home', label: 'Home', to: '/', icon: <Home size={20} /> },
  {
    id: 'chats',
    label: 'Chats',
    to: '/library',
    icon: <MessagesSquare size={20} />,
  },
  {
    id: 'workflows',
    label: 'Workflows',
    to: '/?tab=workflows',
    icon: <Workflow size={20} />,
  },
  {
    id: 'settings',
    label: 'Settings',
    to: '/settings',
    icon: <Settings size={20} />,
  },
];

/**
 * A phone's four places, one tap each (D1): Home, with a count of what needs
 * the person (the sidebar's count); Chats; Workflows; Settings. A
 * conversation and a panel sheet take the whole height instead.
 */
export default function PhoneTabBar({ current }: { current: PhoneTab }) {
  const { controller } = useRuntime();
  const state = useClientState();
  const attention = useAttention(controller.attention).value;
  const approvals = usePendingApprovals(controller.pendingApprovals).page;
  const needs = needsCount({
    approvals,
    problems: attention?.problems ?? [],
    conversations: state.conversations,
  });
  return (
    <nav className="phone-tabs" aria-label="Main">
      {TABS.map((tab) => (
        <AppLink
          key={tab.id}
          to={tab.to}
          className="phone-tab"
          aria-current={tab.id === current ? 'page' : undefined}
          aria-description={
            tab.id === 'home' && needs
              ? `${needs} ${needs === 1 ? 'thing needs' : 'things need'} you`
              : undefined
          }
        >
          <span className="phone-tab-icon" aria-hidden>
            {tab.icon}
            {tab.id === 'home' && needs > 0 && (
              <span className="phone-tab-badge">
                {needs > 9 ? '9+' : needs}
              </span>
            )}
          </span>
          <span className="phone-tab-label">{tab.label}</span>
        </AppLink>
      ))}
    </nav>
  );
}
