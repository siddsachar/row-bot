import type { ReactNode } from 'react';
import { AlertTriangle, Info } from 'lucide-react';

/** A console page: its title, one line on what it is for, and its primary action. */
export function Page({
  title,
  description,
  action,
  children,
}: {
  title: string;
  description: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="console-page">
      <header className="console-page-header">
        <div>
          <h1>{title}</h1>
          <p>{description}</p>
        </div>
        {action && <div className="console-page-action">{action}</div>}
      </header>
      {children}
    </div>
  );
}

/** A calm notice in the page: information, or a warning that needs a look. */
export function Notice({
  tone = 'info',
  title,
  children,
  action,
}: {
  tone?: 'info' | 'warning';
  title: string;
  children?: ReactNode;
  /** One small control at its end, such as Dismiss. */
  action?: ReactNode;
}) {
  const Icon = tone === 'warning' ? AlertTriangle : Info;
  return (
    <div className="console-notice" data-tone={tone} role="status">
      <Icon size={18} aria-hidden />
      <div>
        <strong>{title}</strong>
        {children && <div className="console-notice-body">{children}</div>}
      </div>
      {action && <div className="console-notice-action">{action}</div>}
    </div>
  );
}
