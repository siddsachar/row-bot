import { useEffect, useState, type ReactNode } from 'react';
import { Building2, User } from 'lucide-react';
import type { CompanyView } from '../../api/types';
import { useOptionalRuntime } from '../../runtime';
import { Segmented, type SegmentedOption } from '../../ui/primitives';
import CompanyGraph from './CompanyGraph';
import './company.css';

export type KnowledgeScope = 'mine' | 'company';

/** The last scope chosen, remembered on this device only. */
const SCOPE_KEY = 'row-bot:knowledge:scope';
/** The company's knowledge can be shown (or why not, in its own words). */
const JOINED = new Set<CompanyView['state']>([
  'joined',
  'unreachable',
  'disconnected',
]);

function rememberedScope(): KnowledgeScope {
  try {
    return window.localStorage.getItem(SCOPE_KEY) === 'company'
      ? 'company'
      : 'mine';
  } catch {
    return 'mine';
  }
}

function rememberScope(scope: KnowledgeScope) {
  try {
    window.localStorage.setItem(SCOPE_KEY, scope);
  } catch {
    // Storage refused: the switch still works for this visit.
  }
}

const OPTIONS: SegmentedOption<KnowledgeScope>[] = [
  { value: 'mine', label: 'Mine', icon: <User size={14} aria-hidden /> },
  {
    value: 'company',
    label: 'Company',
    icon: <Building2 size={14} aria-hidden />,
  },
];

/**
 * Knowledge home's scope (Row-Bot for Teams, B8): the person's own graph
 * (`children`, as it is today), or their company's. The switch shows only
 * while this Row-Bot is joined to a company; with none, only `children`
 * renders, and reading the company's state contacts nothing.
 */
export default function CompanyScope({ children }: { children: ReactNode }) {
  const controller = useOptionalRuntime()?.controller;
  const [view, setView] = useState<CompanyView | null>(null);
  const [read, setRead] = useState(false);
  const [scope, setScope] = useState<KnowledgeScope>(rememberedScope);

  useEffect(() => {
    if (!controller) return;
    let current = true;
    const answered = (next: CompanyView | null) => {
      if (!current) return;
      setView(next);
      setRead(true);
    };
    try {
      controller.company('status').then(answered, () => answered(null));
    } catch {
      answered(null);
    }
    return () => {
      current = false;
    };
  }, [controller]);

  const joined = view !== null && JOINED.has(view.state);
  // Someone who chose Company last time sees nothing drawn until the state
  // is read, rather than their own graph for a moment.
  const waiting = Boolean(controller) && !read && scope === 'company';
  const shown: KnowledgeScope = joined ? scope : 'mine';
  return (
    <>
      {joined && (
        <div className="company-scope-bar">
          <Segmented
            label="Knowledge scope"
            size="sm"
            value={shown}
            onChange={(next) => {
              setScope(next);
              rememberScope(next);
            }}
            options={OPTIONS}
          />
        </div>
      )}
      {waiting ? null : shown === 'company' && controller && view ? (
        <CompanyGraph controller={controller} company={view} />
      ) : (
        children
      )}
    </>
  );
}
