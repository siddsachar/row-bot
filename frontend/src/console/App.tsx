import { useEffect, useState, type ReactNode } from 'react';
import {
  Navigate,
  NavLink,
  Route,
  Routes,
  useLocation,
} from 'react-router-dom';
import {
  Cpu,
  Inbox,
  Laptop,
  LogIn,
  LogOut,
  RotateCw,
  ScrollText,
  ShieldCheck,
  Users,
} from 'lucide-react';
import {
  Brand,
  Button,
  EmptyState,
  ErrorState,
  IconButton,
  Skeleton,
} from '../ui/primitives';
import { onAccessLost, request, type AccessLost } from './api';
import { DirectoryProvider, useDirectory } from './directory';
import { problem } from './words';
import { useLoad } from './load';
import type { Me, Person } from './types';
import PeoplePage from './pages/PeoplePage';
import DevicesPage from './pages/DevicesPage';
import ModelsPage from './pages/ModelsPage';
import ReviewPage from './pages/ReviewPage';
import PolicyPage from './pages/PolicyPage';
import AuditPage from './pages/AuditPage';

const PAGES = [
  { path: 'people', label: 'People', icon: Users, page: PeoplePage },
  { path: 'devices', label: 'Devices', icon: Laptop, page: DevicesPage },
  { path: 'models', label: 'Models', icon: Cpu, page: ModelsPage },
  { path: 'review', label: 'Review', icon: Inbox, page: ReviewPage },
  { path: 'policy', label: 'Policy', icon: ShieldCheck, page: PolicyPage },
  { path: 'audit', label: 'Audit', icon: ScrollText, page: AuditPage },
];

/** Sign in again and come back to this page (`/console/...`). */
function signInHref(pathname: string) {
  return `/auth/login?next=${encodeURIComponent(`/console${pathname === '/' ? '/' : pathname}`)}`;
}

function StatePage({
  title,
  children,
  action,
}: {
  title: string;
  children: string;
  action?: ReactNode;
}) {
  return (
    <main className="console-state">
      <EmptyState title={title} action={action}>
        {children}
      </EmptyState>
    </main>
  );
}

async function loadSession(): Promise<{ me: Me; people: Person[] }> {
  const me = await request<Me>('/api/me');
  if (!me.admin) return { me, people: [] };
  const { people } = await request<{ people: Person[] }>('/api/admin/people');
  return { me, people };
}

export default function App() {
  const { pathname } = useLocation();
  const [lost, setLost] = useState<AccessLost | 'left' | null>(null);
  useEffect(() => onAccessLost((reason) => setLost(reason)), []);
  const session = useLoad(loadSession);

  if (lost === 'signed_out')
    return (
      <StatePage
        title="Your session ended"
        action={
          <a className="button primary" href={signInHref(pathname)}>
            <LogIn size={16} aria-hidden />
            Sign in again
          </a>
        }
      >
        You were signed out of the company server. Sign in again to keep
        working.
      </StatePage>
    );
  if (lost === 'left')
    return (
      <StatePage
        title="You're signed out"
        action={
          <a className="button primary" href={signInHref(pathname)}>
            <LogIn size={16} aria-hidden />
            Sign in
          </a>
        }
      >
        You signed out of the admin console on this browser.
      </StatePage>
    );
  if (
    lost === 'not_admin' ||
    (session.status === 'ready' && !session.data.me.admin)
  )
    return (
      <StatePage title="You need to be an admin">
        This is your company's Row-Bot admin console. Ask one of its admins to
        make you an admin, then reload this page.
      </StatePage>
    );
  if (session.status === 'loading')
    return (
      <main className="console-state">
        <Skeleton label="Opening the admin console" />
      </main>
    );
  if (session.status === 'error')
    return (
      <main className="console-state">
        <ErrorState
          title="Couldn't open the admin console"
          action={
            <Button onClick={session.reload}>
              <RotateCw size={16} aria-hidden />
              Try again
            </Button>
          }
        >
          {problem(session.error)}
        </ErrorState>
      </main>
    );
  return (
    <DirectoryProvider me={session.data.me} people={session.data.people}>
      <Shell onSignedOut={() => setLost('left')} />
    </DirectoryProvider>
  );
}

function Shell({ onSignedOut }: { onSignedOut: () => void }) {
  const { me } = useDirectory();
  const [leaving, setLeaving] = useState(false);
  async function signOut() {
    setLeaving(true);
    try {
      await request('/auth/logout', { method: 'POST' });
      onSignedOut();
    } catch {
      setLeaving(false);
    }
  }
  return (
    <div className="console-shell">
      <header className="console-nav">
        <div className="console-brand">
          <Brand compact />
          <span className="console-brand-label">Admin console</span>
        </div>
        <nav aria-label="Admin console">
          <ul>
            {PAGES.map(({ path, label, icon: Icon }) => (
              <li key={path}>
                <NavLink to={`/${path}`} className="console-nav-link">
                  <Icon size={18} aria-hidden />
                  <span>{label}</span>
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>
        <div className="console-me">
          <span className="console-me-name" title={me.email ?? undefined}>
            {me.display_name || me.email}
          </span>
          <IconButton
            label="Sign out"
            size="sm"
            disabled={leaving}
            onClick={() => void signOut()}
          >
            <LogOut size={16} aria-hidden />
          </IconButton>
        </div>
      </header>
      <main className="console-main">
        <Routes>
          {PAGES.map(({ path, page: Page }) => (
            <Route key={path} path={`/${path}`} element={<Page />} />
          ))}
          <Route path="*" element={<Navigate to="/people" replace />} />
        </Routes>
      </main>
    </div>
  );
}
