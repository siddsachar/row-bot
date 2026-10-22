import { useEffect, useRef, type ReactNode } from 'react';
import { MoreHorizontal } from 'lucide-react';
import { Button, Menu, type MenuAction } from '../../ui/primitives';
import { Drawer } from '../../ui/overlays';
import './learning.css';

/**
 * One change to review (designs/learning-ux.md §4). L3b fills it from tested changes;
 * a personal-model version (L2c2) brings its own results table. Counts always come
 * with their denominator.
 */
export type ReviewChange = {
  title: string;
  /** "Lesson for document lookups". */
  kind: string;
  /** Why it was proposed, in plain words. */
  why: string;
  /** The text the change adds (a lesson) or the setting it changes ("" when nothing is quoted). */
  change: string;
  /** The text was edited since it was tested: test again before using it. */
  edited?: boolean;
  results: {
    rows: { label: string; total: number; without: number; with: number }[];
    /** "Testing used 85K tokens · Small test, so expect some variation." */
    note: string;
  };
};

export default function ReviewSheet({
  open,
  onOpenChange,
  change,
  onUse,
  onTestAgain,
  onNotNow,
  onEdit,
  onSeeChats,
  onDontSuggest,
  onDetails,
  results,
  primary,
  notNowLabel = 'Not now',
  menuActions = [],
  details,
  detailsOpen = false,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  change: ReviewChange;
  onUse: () => void;
  onTestAgain: () => void;
  onNotNow: () => void;
  onEdit?: () => void;
  onSeeChats?: () => void;
  onDontSuggest?: () => void;
  onDetails: () => void;
  /** A results view of its own (a version's comparison table) instead of Without/With. */
  results?: ReactNode;
  /** The primary action, when it isn't "Use this change" (none: nothing to use). */
  primary?: { label: string; onSelect: () => void; disabled?: boolean };
  notNowLabel?: string;
  /** More actions in the … menu, before Experiment details. */
  menuActions?: MenuAction[];
  /** Experiment details, shown in the sheet while ``detailsOpen``. */
  details?: ReactNode;
  detailsOpen?: boolean;
}) {
  const custom = results !== undefined;
  const shown = useRef<HTMLElement>(null);
  useEffect(() => {
    // Opened from the … menu below: bring the details into view.
    if (detailsOpen) shown.current?.scrollIntoView?.({ block: 'start' });
  }, [detailsOpen]);
  return (
    <Drawer
      open={open}
      onOpenChange={onOpenChange}
      modal
      title={change.title}
      description={change.kind}
      className="learning-review"
      closeLabel="Close review"
    >
      {/* It scrolls when the details are open: keyboard users can scroll it too. */}
      <div
        className="learning-review-body"
        role="group"
        aria-label="Review"
        tabIndex={0}
      >
        <section aria-label="Why">
          <h3>Why</h3>
          <p>{change.why}</p>
          {onSeeChats && (
            <div>
              <Button variant="ghost" className="small" onClick={onSeeChats}>
                See the chats
              </Button>
            </div>
          )}
        </section>
        {change.change && (
          <section aria-label="What changes">
            <h3>What changes</h3>
            <blockquote className="learning-review-quote">
              {change.change}
            </blockquote>
            {change.edited && (
              <p role="status">Edited: test again before using.</p>
            )}
            {onEdit && (
              <div>
                <Button variant="ghost" className="small" onClick={onEdit}>
                  Edit
                </Button>
              </div>
            )}
          </section>
        )}
        <section aria-label="Test result">
          <h3>Test result</h3>
          {custom ? (
            results
          ) : (
            <>
              <table className="learning-results">
                <caption className="visually-hidden">
                  Tasks passed without and with this change
                </caption>
                <thead>
                  <tr>
                    <th scope="col">Tasks</th>
                    <th scope="col">Without</th>
                    <th scope="col">With</th>
                  </tr>
                </thead>
                <tbody>
                  {change.results.rows.map((row) => (
                    <tr key={row.label}>
                      <th scope="row">
                        {row.label} ({row.total})
                      </th>
                      <td data-label="Without">{row.without}</td>
                      <td data-label="With">{row.with} passed</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="learning-muted">{change.results.note}</p>
            </>
          )}
        </section>
        {details && detailsOpen && (
          <section
            ref={shown}
            aria-label="Experiment details"
            className="learning-review-details"
          >
            <h3>Experiment details</h3>
            {details}
          </section>
        )}
      </div>
      <div className="learning-review-actions">
        {custom ? (
          primary && (
            <Button
              variant="primary"
              disabled={primary.disabled}
              onClick={primary.onSelect}
            >
              {primary.label}
            </Button>
          )
        ) : change.edited ? (
          <Button variant="primary" onClick={onTestAgain}>
            Test again
          </Button>
        ) : (
          <Button variant="primary" onClick={onUse}>
            Use this change
          </Button>
        )}
        <Button
          variant={custom && !primary ? undefined : 'ghost'}
          onClick={onNotNow}
        >
          {notNowLabel}
        </Button>
        <span className="learning-review-more">
          <Menu
            label={custom ? `More for ${change.title}` : 'More for this change'}
            iconOnly
            variant="ghost"
            className="icon-action"
            actions={[
              ...menuActions,
              ...(onDontSuggest
                ? [
                    {
                      label: 'Don’t suggest this again',
                      onSelect: onDontSuggest,
                    },
                  ]
                : []),
              {
                label: detailsOpen
                  ? 'Hide experiment details'
                  : 'Experiment details',
                onSelect: onDetails,
              },
            ]}
          >
            <MoreHorizontal size={16} aria-hidden />
          </Menu>
        </span>
      </div>
    </Drawer>
  );
}
